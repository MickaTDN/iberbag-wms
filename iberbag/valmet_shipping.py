"""Office fulfilment. Every mutation uses the existing authenticated transaction."""
import json
import hashlib
from collections import defaultdict
from datetime import date,datetime
from sqlalchemy import select
from . import valmet as v,valmet_planning as p,valmet_balances as b
from .database import (ValmetPreparation as Prep,ValmetPlanRevision as Rev,ValmetReservation as Hold,
    ValmetUnit,ValmetPosition,ValmetShipment as Shipment,ValmetShipmentLine as Line,
    ValmetOccurrence as Occurrence,ValmetRelocation as Relocation,ValmetDocument as Document,now_utc)
from .receiving import ReceivingError,required,quantity,request_key

def shipments(db,ident):return db.scalars(select(Shipment).where(Shipment.preparation_id==ident).order_by(Shipment.id)).all()

def sent_totals(db,ident):
    counts=defaultdict(int)
    for load in shipments(db,ident):
        for s in json.loads(load.snapshot)['selections']:
            for lot,q in s['materials'].items():counts[(s['unit']['kind'],lot)]+=q
    return dict(counts)

def summary(db,record,additional=()):
    first=db.scalar(select(Rev).where(Rev.preparation_id==record.id,Rev.action=='draft').order_by(Rev.id).limit(1))
    original=json.loads(first.snapshot);asked={(l['kind'],l['lot']):l['quantity'] for l in original['lines']}
    rows=[s for load in shipments(db,record.id) for s in json.loads(load.snapshot)['selections']]+list(additional)
    sent=defaultdict(int);byunit=defaultdict(int);physical=0
    for s in rows:
        physical+=s['out_physical']
        for lot,q in s['materials'].items():
            sent[(s['unit']['kind'],lot)]+=q;byunit[(s['unit']['kind'],lot,s['unit']['origin_id'],s['unit']['number'])]+=q
    budget=dict(asked);extra_units=[]
    for (kind,lot,uid,number),q in byunit.items():
        used=min(q,budget.get((kind,lot),0));budget[(kind,lot)]=budget.get((kind,lot),0)-used
        if q>used:extra_units.append(dict(kind=kind,lot=lot,unit=uid,number=number,quantity=q-used))
    return dict(original=original,physical=physical,sent=[dict(kind=k,lot=l,quantity=q) for (k,l),q in sent.items()],
        shortages=[dict(kind=k,lot=l,quantity=q-sent[(k,l)]) for (k,l),q in asked.items() if sent[(k,l)]<q],
        extras=[dict(kind=k,lot=l,quantity=q-asked.get((k,l),0)) for (k,l),q in sent.items() if q>asked.get((k,l),0)],extra_units=extra_units)

def current(db,record):
    if not record.active_revision:raise ReceivingError('Confirmar a preparação antes de recolher/carregar.')
    revision=db.get(Rev,record.active_revision)
    if revision.version!=record.version:raise ReceivingError('Existe alteração por confirmar; rever e confirmar as reservas.')
    selections=[s for s in b.allocation_rows(db) if s['revision_id']==revision.id]
    available={u['id']:u for u in p.source(db,record.id)}
    for s in selections:
        fresh=available.get(s['unit']['id'])
        if not fresh:raise ReceivingError('Unidade bloqueada, sem localização ou N.º Armazém: corrigir/rever antes de carregar.')
        for lot,q in s['materials'].items():
            cap=fresh['available'].get(lot,0)
            if cap is not None and q>cap:raise ReceivingError('Reserva excede o material remanescente; rever.')
        s['unit']=fresh
    return selections

@v.command
def ready(db,actor,ident,version):
    record=db.get(Prep,ident);p.access(record,actor)
    if record.version!=version:raise ReceivingError('Versão desatualizada.')
    rows=current(db,record)
    if not rows:raise ReceivingError('Não existe material selecionado para carregar.')
    unresolved=db.scalars(select(Occurrence).where(Occurrence.preparation_id==ident)).all()
    resolved={o.resolves_id for o in unresolved if o.resolves_id}
    if any(o.kind!='resolved' and o.id not in resolved and o.version>=version for o in unresolved):
        raise ReceivingError('Corrigir/rever ou resolver as exceções antes de colocar pronta.')
    if record.status!='ready':
        p.revision(db,record,actor,'ready',dict(reservation_revision=record.active_revision))
        record.status='ready'
    return ident

def permission(record,actor):
    if not record:raise ReceivingError('Preparação inexistente.',404)
    if actor.role!='admin' and actor.id!=record.created_by:raise ReceivingError('Sem permissão para alterar esta preparação.',403)

@v.command
def report(db,actor,ident,version,key,kind,note,lot='',amount=None,observed='',request_id=None):
    record=db.get(Prep,ident);p.access(record,actor)
    if record.version!=version:raise ReceivingError('Versão desatualizada.')
    if kind not in ('missing','location','substitution','quantity','not_loaded'):raise ReceivingError('Tipo de exceção inválido.')
    raw=dict(version=version,key=key,kind=kind,note=note,lot=lot,amount=amount,observed=observed)
    token=request_key(request_id or hashlib.sha256(p.dump(dict(preparation=ident,**raw)).encode()).hexdigest()[:32])
    existing=db.scalar(select(Occurrence).where(Occurrence.request_key==token))
    if existing:
        if existing.preparation_id!=ident or json.loads(existing.details).get('request')!=raw:raise ReceivingError('Chave de ocorrência repetida com dados diferentes.')
        return existing.id
    rows=[s for s in b.allocation_rows(db) if s['preparation_id']==ident]
    selected=next((s for s in rows if s['unit']['id']==key),None)
    if not selected:raise ReceivingError('Escolher uma unidade desta preparação.')
    if lot and lot not in selected['materials']:raise ReceivingError('Lote fora da seleção.')
    amount=quantity(amount) if amount not in ('',None) else None
    if amount is not None and lot and amount>selected['materials'][lot]:raise ReceivingError('Quantidade da ocorrência excede a selecionada.')
    if observed:
        from .database import Location
        location=db.scalar(select(Location).where(func_lower(Location.codigo)==observed.strip().lower(),Location.active.is_(True)))
        if not location:raise ReceivingError('Localização constatada fora do catálogo/contexto.')
        observed=location.codigo
    occurrence=Occurrence(preparation_id=ident,unit_id=selected['unit']['origin_id'],version=version,kind=kind,request_key=token,
        details=p.dump(dict(note=required(note,'Observação / motivo'),lot=lot,quantity=amount,observed_local=v.optional(observed),selection=selected,request=raw)),actor=actor.username)
    db.add(occurrence);db.flush()
    record.status='reserved'
    p.revision(db,record,actor,'exception',dict(kind=kind,unit=selected['unit']['origin_id'],note=note))
    return occurrence.id

@v.command
def resolve(db,actor,occurrence_id,note):
    original=db.get(Occurrence,occurrence_id)
    if not original or original.kind=='resolved':raise ReceivingError('Ocorrência inexistente.')
    record=db.get(Prep,original.preparation_id);permission(record,actor)
    if original.kind=='missing' and actor.role!='admin':raise ReceivingError('Só Administrador resolve material não encontrado.',403)
    if db.scalar(select(Occurrence.id).where(Occurrence.resolves_id==original.id)):return original.id
    db.add(Occurrence(preparation_id=record.id,unit_id=original.unit_id,version=record.version,kind='resolved',resolves_id=original.id,
        details=p.dump(dict(proof=required(note,'Comprovação da resolução'))),actor=actor.username))
    p.revision(db,record,actor,'resolve',dict(occurrence=original.id,proof=note));return original.id

def rebind(db,record,actor,selections,status='reserved'):
    """Replace current reservations once, without silently optimising new units."""
    record.version+=1;record.active_revision=None;record.status='draft';record.review=None;db.flush();db.expire_all()
    fresh={u['id']:u for u in p.source(db,record.id)};selected={}
    for s in selections:
        u=fresh.get(s['unit']['id'])
        if not u:
            pos=db.get(ValmetPosition,s['unit']['position_id'])
            if pos and pos.uncertain and s['unit']['kind']=='lab':continue
            raise ReceivingError('Não foi possível conservar as reservas remanescentes; nada foi alterado.')
        for lot,q in s['materials'].items():
            cap=u['available'].get(lot,0)
            if cap is not None and q>cap:raise ReceivingError('Conflito nas reservas remanescentes.')
        selected[u['id']]=dict(s,unit=u)
    if selected:
        plan=p.summarize(db,record,selected);revision=p.revision(db,record,actor,'reserve',plan)
        p.reserve_rows(db,revision,plan['selections']);record.active_revision=revision.id;record.status=status
    return bool(selected)

@v.command
def relocate(db,actor,ident,version,key,target,amount,note,lot='',request_id=None):
    record=db.get(Prep,ident);permission(record,actor)
    raw=p.dump(dict(preparation=ident,version=version,key=key,target=target,amount=str(amount),note=note,lot=lot))
    token=request_key(request_id or hashlib.sha256(raw.encode()).hexdigest()[:32])
    existing=db.scalar(select(Relocation).where(Relocation.request_key==token))
    if existing:
        if existing.preparation_id!=ident or existing.payload!=raw:raise ReceivingError('Chave de localização repetida com dados diferentes.')
        return existing.id
    if record.version!=version:raise ReceivingError('Versão desatualizada.')
    pos=b.position_for(db,key)
    if not pos or pos.physical_quantity<=0:raise ReceivingError('Posição de origem inexistente.')
    unit=db.get(ValmetUnit,pos.unit_id);amount=quantity(amount);note=required(note,'Comprovação da localização efetiva')
    contents=json.loads(pos.materials)
    if unit.kind in ('rolos','lab','caixas','pecas','vazias') or (unit.kind=='paletes' and unit.physical_quantity==1):
        if amount!=pos.physical_quantity:raise ReceivingError('A localização da unidade física é indivisível.')
        moving=contents
    else:
        lot=required(lot,'Lote da quantidade fisicamente conferida')
        if lot not in contents or amount>contents[lot]:raise ReceivingError('Quantidade/localização não comprovada no stock.')
        moving={lot:amount}
    before=[s for s in b.allocation_rows(db) if s['preparation_id']==ident];previous_status=record.status
    if record.active_revision:record.active_revision=None;record.status='draft';db.flush()
    from .database import Location
    location=db.scalar(select(Location).where(func_lower(Location.codigo)==target.strip().lower()))
    if not location or not location.active:raise ReceivingError('Localização de destino desconhecida/inativa.')
    event=Relocation(preparation_id=ident,position_id=pos.id,request_key=token,payload=raw,target_local=location.codigo,physical_quantity=amount,materials=p.dump(moving),note=note,actor=actor.username)
    db.add(event);db.flush();db.expire_all()
    dest=db.scalar(select(ValmetPosition).where(ValmetPosition.unit_id==unit.id,ValmetPosition.local==location.codigo))
    updated=[]
    for s in before:
        if s['unit']['position_id']!=pos.id:updated.append(s);continue
        # Move only the part of this preparation actually covered by the proof;
        # other reservations remain at their own proven positions.
        moved={k:min(q,moving.get(k,0) or 0) for k,q in s['materials'].items() if moving.get(k) is not None}
        moved={k:q for k,q in moved.items() if q}
        left={k:q-moved.get(k,0) for k,q in s['materials'].items() if q>moved.get(k,0)}
        if unit.kind=='lab' and any(q is None for q in moving.values()):moved=dict(s['materials']);left={}
        for materials,destination in ((left,pos),(moved,dest)):
            if not materials:continue
            u=dict(s['unit'],id=b.source_key(destination),position_id=destination.id,local=destination.local)
            physical=1 if unit.kind in ('rolos','lab','caixas','pecas','vazias') or (unit.kind=='paletes' and unit.physical_quantity==1) else sum(materials.values())
            updated.append(dict(s,unit=u,materials=materials,physical=physical))
    if before:rebind(db,record,actor,updated,status='ready' if previous_status=='ready' and not any(x['unit']['origin_id']==unit.id for x in before) else 'reserved')
    elif record.status not in ('cancelled','shipped'):record.status='draft'
    p.revision(db,record,actor,'location',dict(event=event.id,proof=note,from_position=pos.id,to_position=dest.id))
    return event.id

def func_lower(column):
    from sqlalchemy import func
    return func.lower(column)

def references(values):
    clean=[]
    for value in values:
        value=required(value,'GT/CMR')
        kind,sep,number=value.partition(' ')
        if kind not in ('GT','CMR') or not sep or not number.strip():raise ReceivingError('Indicar GT número ou CMR número, uma referência por linha.')
        if value in clean:raise ReceivingError('GT/CMR repetida.')
        clean.append(value)
    if not 1<=len(clean)<=30:raise ReceivingError('Indicar 1–30 referências GT/CMR.')
    return clean

@v.command
def ship(db,actor,ident,version,key,day,refs,lines=None,close=False,extra_ack=False,unknown_ack=False,lab_pallets=()):
    record=db.get(Prep,ident);permission(record,actor);key=request_key(key)
    try:loaded=date.fromisoformat(day)
    except (ValueError,TypeError):raise ReceivingError('Data efetiva inválida.')
    refs=references(refs)
    raw=p.dump(dict(version=version,day=day,refs=refs,lines=lines,close=bool(close),extra_ack=bool(extra_ack),unknown_ack=bool(unknown_ack),lab_pallets=list(lab_pallets)))
    existing=db.scalar(select(Shipment).where(Shipment.request_key==key))
    if existing:
        if existing.preparation_id!=ident or existing.payload!=raw:raise ReceivingError('Chave repetida com dados diferentes.')
        return existing.id
    if record.status!='ready' or record.version!=version:raise ReceivingError('Colocar a versão atual pronta para carregamento.')
    reserved=current(db,record);selected={s['unit']['id']:s for s in reserved}
    amounts=defaultdict(dict)
    if lines is None:
        for k,s in selected.items():amounts[k]=dict(s['materials'])
    else:
        if len(lines)>200:raise ReceivingError('Demasiadas linhas de carregamento.')
        for k,lot,q in lines:
            if k not in selected or lot not in selected[k]['materials']:raise ReceivingError('Material fora da reserva.')
            q=quantity(q)
            amounts[k][lot]=amounts[k].get(lot,0)+q
    outputs=[];remaining=[]
    for k,s in selected.items():
        chosen=amounts.get(k,{})
        if not chosen:remaining.append(s);continue
        if any(q>s['materials'][lot] for lot,q in chosen.items()):raise ReceivingError('Quantidade excede a reserva.')
        u=s['unit'];whole=u['kind'] in ('caixas','pecas','vazias') or (u['kind']=='paletes' and u['original_physical']==1) or (u['kind']=='rolos' and u['separation']!='yes')
        if whole and chosen!=s['materials']:raise ReceivingError('A unidade indivisível tem de sair completa, com todos os lotes.')
        after={lot:(None if q is None else q-chosen.get(lot,0)) for lot,q in u['materials'].items()}
        after={lot:q for lot,q in after.items() if q is None or q>0}
        uncontrolled=u['kind']=='lab' and any(q is None for q in u['materials'].values())
        if uncontrolled and not unknown_ack:raise ReceivingError('Confirmar explicitamente LAB sem saldo controlado; o remanescente ficará bloqueado.')
        if whole:physical=u['physical']
        elif u['kind']=='rolos':physical=u['physical'] if not after else 0
        elif u['kind']=='lab':physical=u['physical'] if k in lab_pallets and not after else 0
        else:physical=sum(chosen.values())
        observed=None
        for occurrence in db.scalars(select(Occurrence).where(Occurrence.preparation_id==ident,Occurrence.unit_id==u['origin_id'],Occurrence.kind=='location').order_by(Occurrence.id.desc())):
            proof=json.loads(occurrence.details)
            if proof.get('observed_local'):
                observed=proof['observed_local']
                if observed!=u['local'] and physical!=u['physical'] and proof.get('quantity')!=physical:
                    raise ReceivingError('Corrigir a localização efetiva do remanescente antes de expedir parcialmente esta unidade.')
                break
        outputs.append(dict(s,materials=chosen,out_physical=physical,observed_local=observed or u['local']))
        left={lot:q-chosen.get(lot,0) for lot,q in s['materials'].items() if q>chosen.get(lot,0)}
        if left and not uncontrolled:remaining.append(dict(s,materials=left,physical=1 if u['kind'] in ('rolos','lab') else sum(left.values())))
    if not outputs:raise ReceivingError('Carregamento sem material; nenhuma saída foi criada.')
    actual=summary(db,record,outputs)
    if actual['extras'] and not extra_ack:raise ReceivingError('Confirmar Material enviado a mais e lotes adicionais efetivamente carregados.')
    header=Shipment(preparation_id=ident,revision_id=record.active_revision,request_key=key,payload=raw,
        loaded_on=datetime.combine(loaded,datetime.min.time()),references=p.dump(refs),snapshot=p.dump(dict(selections=outputs,actual=actual,reference=json.loads(record.payload)['reference'])),actor=actor.username)
    db.add(header);db.flush()
    holds={h.unit_id:h for h in db.scalars(select(Hold).where(Hold.revision_id==record.active_revision))}
    for s in outputs:db.add(Line(shipment_id=header.id,reservation_id=holds[s['unit']['origin_id']].id,position_id=s['unit']['position_id'],
        physical_quantity=s['out_physical'],materials=p.dump(s['materials']),unknown_ack=unknown_ack))
    db.flush()
    record.active_revision=None;record.status='draft';db.flush()
    if not close and remaining:
        kept=rebind(db,record,actor,remaining,status='ready')
    else:record.version+=1;kept=False
    if not kept:record.status='shipped'
    p.revision(db,record,actor,'shipment',dict(shipment=header.id,actual=actual,remaining_reserved=kept,closed=bool(close)))
    return header.id

@v.command
def issue_document(db,actor,ident,version):
    record=db.get(Prep,ident);permission(record,actor)
    if record.version!=version:raise ReceivingError('Versão desatualizada.')
    if not record.active_revision:raise ReceivingError('Não existe seleção confirmada atual.')
    revision=db.get(Rev,record.active_revision)
    if revision.version!=version:raise ReceivingError('Confirmar as alterações antes de emitir nova versão.')
    existing=db.scalar(select(Document).where(Document.revision_id==revision.id))
    if existing:return existing.id
    plan=json.loads(revision.snapshot)
    if any(l['kind']=='vasilhame' for l in plan['payload']['lines']):raise ReceivingError('Vasilhame não tem folha de recolha.')
    for selection in plan['selections']:
        u=db.get(ValmetUnit,selection['unit'].get('origin_id',selection['unit']['id']))
        if u.kind=='vazias' and (u.legacy_unnumbered or not u.number):raise ReceivingError('Caixas Vazias exigem N.º Armazém individual antes de emitir folha/expedir.')
    if not plan['selections']:raise ReceivingError('Não existe material selecionado para imprimir.')
    created=now_utc()
    from .valmet_pdf import render_pdf
    pdf=render_pdf(record.id,revision.version,plan,created)
    document=Document(preparation_id=ident,revision_id=revision.id,pdf=pdf,digest=hashlib.sha256(pdf).hexdigest(),actor=actor.username,created_at=created)
    db.add(document);db.flush();p.revision(db,record,actor,'document',dict(document=document.id,digest=document.digest,reservation_revision=revision.id))
    return document.id
