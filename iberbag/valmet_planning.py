"""Office planning only. Immutable revisions, current pointers, no physical debit."""
import json
import hashlib
from datetime import date
from collections import defaultdict
from sqlalchemy import select
from . import valmet as v
from .database import (ValmetPreparation as Prep,ValmetPlanRevision as Rev,ValmetReservation as Hold,
    ValmetHandling as Policy,ValmetUnit,ValmetIdentification,ValmetItem,Audit,Location,ValmetPosition)
from . import valmet_balances as balances
from .receiving import ReceivingError,required,quantity,request_key

def dump(data):return json.dumps(data,sort_keys=True,ensure_ascii=False)

def review_key(record):return hashlib.sha256((record.review or '').encode()).hexdigest()

def active(db,exclude=None):
    query=select(Hold).join(Rev,Hold.revision_id==Rev.id).join(Prep,Rev.preparation_id==Prep.id).where(
        Prep.status.in_(('reserved','ready')),Prep.active_revision==Rev.id)
    if exclude is not None:query=query.where(Prep.id!=exclude)
    return db.scalars(query).all()

def policy(db,uid):return db.scalar(select(Policy).where(Policy.unit_id==uid).order_by(Policy.id.desc()).limit(1))

def materials_for(unit,items):
    materials=defaultdict(int)
    for item in items:
        if not item.lot:continue
        if unit.kind in ('caixas','vazias') or (unit.kind=='paletes' and unit.physical_quantity==1):materials[item.lot]=unit.physical_quantity
        elif item.quantity is not None:
            if materials[item.lot] is not None:materials[item.lot]+=item.quantity
        elif unit.kind=='lab':materials[item.lot]=None
    return {unit.brand:unit.physical_quantity} if unit.kind=='vasilhame' else dict(materials)

def stock_details(db,rows):
    holds=balances.allocation_rows(db);result={}
    candidates={u['id']:u for u in source(db,unit_ids=[getattr(row[0],'origin_id',row[0].id) for row in rows])}
    for unit,ident,items,_ in rows:
        held=[h for h in holds if h['unit']['id']==unit.id];reserved=defaultdict(int)
        for h in held:
            for lot,q in h['materials'].items():reserved[lot]+=q
        result[unit.id]=dict(reserved=min(unit.physical_quantity,sum(h['physical'] for h in held)),
            materials=unit.materials,material_reserved=dict(reserved),
            available=candidates.get(unit.id,{}).get('available',{}),policy=policy(db,unit.origin_id))
    return result

@v.command
def configure(db,actor,uid,separation,reconciled=False,unlocated=False,note=''):
    if actor.role!='admin':raise ReceivingError('Só Administrador configura a separação/reconciliação.',403)
    unit=db.get(ValmetUnit,uid)
    if not unit or separation not in ('yes','no','unknown'):raise ReceivingError('Unidade ou regra inválida.')
    if separation!='unknown' and unit.kind!='rolos':raise ReceivingError('Separação de lotes aplica-se apenas a ROLOS.')
    if any(r.unit_id==uid for r in active(db)):raise ReceivingError('Unidade reservada; cancelar/rever antes de mudar regras.')
    if reconciled and not (unit.kind=='vazias' and unit.legacy_unnumbered):raise ReceivingError('Reconciliação apenas para grupos históricos de Caixas Vazias.')
    if reconciled:
        lots=set(db.scalars(select(ValmetItem.lot).where(ValmetItem.unit_id==uid,ValmetItem.identification_id.is_not(None))))
        if len(lots)!=1 or not next(iter(lots)):
            raise ReceivingError('Grupo histórico exige um lote comprovado; distribuição física multilote requer reconciliação própria.')
    if unlocated and unit.kind!='vasilhame':raise ReceivingError('Exceção de localização apenas para vasilhame.')
    record=Policy(unit_id=uid,separation=separation,reconciled=bool(reconciled),unlocated=bool(unlocated),note=required(note,'Comprovação'),actor=actor.username)
    db.add(record);db.add(Audit(username=actor.username,action='VALMET_REGRA_MANUSEAMENTO',details=f'Unidade {uid}; {separation}; reconciliada={reconciled}; histórico sem localização={unlocated}; {note}'))

def source(db,exclude=None,unit_ids=None):
    holds=defaultdict(list)
    for h in balances.allocation_rows(db,exclude):holds[h['unit']['position_id']].append(h)
    result=[]
    query=select(ValmetUnit,ValmetPosition).join(ValmetPosition,ValmetPosition.unit_id==ValmetUnit.id).where(ValmetPosition.physical_quantity>0).order_by(ValmetUnit.id,ValmetPosition.id).limit(10001)
    if unit_ids is not None:query=query.where(ValmetUnit.id.in_(unit_ids))
    units=db.execute(query).all()
    if len(units)>10000:raise ReceivingError('Mais de 10000 unidades; reconciliação administrativa necessária para planear com segurança.')
    locations=set(db.scalars(select(Location.codigo).where(Location.active.is_(True))))
    blocked_ids=balances.blocked_units(db)
    for u,pos in units:
        ident=db.scalar(select(ValmetIdentification).where(ValmetIdentification.unit_id==u.id))
        p=policy(db,u.id)
        if not ident or u.legacy_unnumbered or u.id in blocked_ids or pos.uncertain:continue
        if u.kind=='vazias' and not u.number:continue
        unlocated=not pos.local
        if unlocated and not (u.kind=='vasilhame' and p and p.unlocated):continue
        if not unlocated and pos.local not in locations:continue
        items=db.scalars(select(ValmetItem).where(ValmetItem.identification_id==ident.id).order_by(ValmetItem.id)).all()
        materials=json.loads(pos.materials);articles=defaultdict(set)
        for item in items:
            if not item.lot:continue
            if item.article:articles[item.lot].add(item.article)
        if not materials:continue
        used=defaultdict(int);blocked=False;phys=0
        for h in holds[pos.id]:
            blocked|=h['exclusive']
            phys+=h['physical']
            for lot,q in h['materials'].items():used[lot]+=q
        if blocked:continue
        available={lot:(None if q is None else max(0,q-used[lot])) for lot,q in materials.items()}
        if u.kind in ('paletes','fio','prateleiras','vasilhame','vazias'):
            available={lot:min(q,max(0,pos.physical_quantity-phys)) for lot,q in available.items()}
        result.append(dict(id=balances.source_key(pos),origin_id=u.id,position_id=pos.id,original_physical=u.physical_quantity,entry_id=u.entry_id,kind=u.kind,number=u.number,local=pos.local,physical=pos.physical_quantity,
            materials=dict(materials),available=available,articles={k:sorted(a) for k,a in articles.items()},
            composition=[dict(article=' / '.join(sorted(articles[lot])) or None,lot=lot,quantity=q) for lot,q in materials.items()],
            separation=p.separation if p else 'unknown',policy=p.id if p else None,unlocated=unlocated,held=bool(holds[pos.id])))
        result[-1]['free_physical']=max(0,pos.physical_quantity-phys)
    return result

def payload(reference,day,destination,lines):
    try:date.fromisoformat(day)
    except (ValueError,TypeError):raise ReceivingError('Data da preparação inválida.',400)
    if not 1<=len(lines)<=30:raise ReceivingError('Indicar 1–30 linhas.',400)
    clean=[];seen=set()
    for kind,lot,q in lines:
        if kind not in v.KINDS:raise ReceivingError('Categoria inválida.',400)
        lot=required(lot,'Lote / referência');q=quantity(q)
        if kind=='vasilhame' and lot not in v.BRANDS:raise ReceivingError('Referência de vasilhame inválida.')
        if (kind,lot) in seen:raise ReceivingError('Agrupar pedidos do mesmo lote/categoria numa linha.')
        seen.add((kind,lot));clean.append(dict(kind=kind,lot=lot,quantity=q))
    vas=any(l['kind']=='vasilhame' for l in clean)
    if vas and any(l['kind']!='vasilhame' for l in clean):raise ReceivingError('Vasilhame exige preparação exclusiva.')
    return dict(reference=required(reference,'Encomenda'),day=day,destination='' if vas else required(destination,'Destino'),lines=clean)

def access(record,actor):
    if not record:raise ReceivingError('Preparação inexistente.',404)
    if actor.role!='admin' and record.created_by!=actor.id:raise ReceivingError('Sem permissão para alterar esta preparação.',403)
    if record.status in ('cancelled','shipped'):raise ReceivingError('Preparação cancelada ou expedida.')

def revision(db,record,actor,action,snapshot):
    r=Rev(preparation_id=record.id,version=record.version,action=action,snapshot=dump(snapshot),actor=actor.username)
    db.add(r);db.flush();db.add(Audit(username=actor.username,action='VALMET_PREPARACAO_'+action.upper(),details=f'Preparação {record.id}; versão {record.version}; revisão {r.id}'))
    return r

@v.command
def save(db,actor,key,reference,day,destination,lines,ident=None,version=None):
    key=request_key(key);data=dump(payload(reference,day,destination,lines))
    record=db.get(Prep,ident) if ident else db.scalar(select(Prep).where(Prep.request_key==key))
    if record:
        access(record,actor)
        if not ident:
            if record.payload!=data:raise ReceivingError('Chave repetida com dados diferentes.')
            return record.id
        if record.version!=version or record.request_key!=key:raise ReceivingError('Revisão desatualizada.')
        record.payload=data;record.version+=1;record.review=None
        if record.status=='ready':record.status='reserved'
    else:
        if ident:raise ReceivingError('Preparação inexistente.',404)
        record=Prep(request_key=key,payload=data,created_by=actor.id);db.add(record);db.flush()
    revision(db,record,actor,'draft',json.loads(data));return record.id

def boxes(candidates,lot,needed):
    # Exact bounded subset optimisation: excess, number of boxes, stable IDs.
    # Never silently replace optimisation by a greedy commercial rule.
    if len(candidates)>128:raise ReceivingError('Mais de 128 caixas candidatas; restringir a seleção manualmente.')
    states={0:()}
    for u in candidates:
        q=u['available'][lot]
        for total,ids in list(states.items()):
            new=total+q;choice=ids+(u['id'],)
            if new not in states or (len(choice),choice)<(len(states[new]),states[new]):states[new]=choice
        if len(states)>50000:raise ReceivingError('Otimização excede o limite seguro; indicar caixas manualmente para revisão.')
    over=[s for s in states if s>=needed]
    total=min(over) if over else max(states)
    return states[total]

def calculate(db,record,manual=None):
    data=pending_payload(db,record);units=source(db,record.id);byid={u['id']:u for u in units}
    selected={}
    def add(u,lot,q):
        single=u.get('original_physical',u['physical'])==1
        whole=(u['kind'] in ('caixas','pecas') or
            (u['kind']=='rolos' and u['separation']!='yes') or
            (u['kind'] in ('paletes','vazias') and single) or
            (u['kind']=='lab' and u['available'].get(lot) is None))
        if whole:
            if u['held']:raise ReceivingError('Unidade indivisível já parcialmente reservada.')
            if u['id'] in selected:return
            materials=dict(u['materials'])
            if u['kind']=='lab':materials={lot:q}
            selected[u['id']]=dict(unit=u,physical=u['physical'],materials=materials,exclusive=True)
        else:
            old=selected.setdefault(u['id'],dict(unit=u,physical=0,materials={},exclusive=False))
            if q>u['available'].get(lot,0)-old['materials'].get(lot,0):raise ReceivingError('Quantidade selecionada excede o disponível.')
            if u['kind'] not in ('rolos','lab') and old['physical']+q>u['free_physical']:raise ReceivingError('Quantidade física já reservada.')
            old['materials'][lot]=old['materials'].get(lot,0)+q
            old['physical']=1 if u['kind'] in ('rolos','lab') else sum(old['materials'].values())
    if manual is not None:
        requested={(l['kind'],l['lot']) for l in data['lines']}
        for uid,lot,q in manual:
            u=byid.get(uid)
            if not u or (u['kind'],lot) not in requested or lot not in u['available']:raise ReceivingError('Seleção manual inválida ou indisponível.')
            amount=quantity(q)
            if u['available'][lot] is not None and amount>u['available'][lot]:raise ReceivingError('Seleção manual excede o stock.')
            add(u,lot,amount)
    else:
        for line in data['lines']:
            lot=line['lot'];kind=line['kind']
            supplied=sum(s['materials'].get(lot,0) or 0 for s in selected.values() if s['unit']['kind']==kind)
            need=max(0,line['quantity']-supplied)
            candidates=[u for u in units if u['kind']==kind and lot in u['available'] and (u['available'][lot] is None or u['available'][lot]>0) and (u['id'] not in selected or not selected[u['id']]['exclusive'])]
            if kind=='pecas':
                ids=boxes(candidates,lot,need) if need else ()
                candidates=[byid[i] for i in ids]
            for u in candidates:
                if need<=0:break
                already=selected.get(u['id'],{}).get('materials',{}).get(lot,0)
                q=need if u['available'][lot] is None else min(need,max(0,u['available'][lot]-already))
                if u['kind'] in ('paletes','fio','prateleiras','vazias','vasilhame') and u['physical']>1:
                    q=min(q,u['free_physical']-selected.get(u['id'],{}).get('physical',0))
                if q<=0:continue
                add(u,lot,q)
                need=max(0,line['quantity']-sum(s['materials'].get(lot,0) or 0 for s in selected.values() if s['unit']['kind']==kind))
    return summarize(db,record,selected)

def pending_payload(db,record):
    from .valmet_shipping import sent_totals
    data=json.loads(record.payload);sent=sent_totals(db,record.id)
    for line in data["lines"]:line["quantity"]=max(0,line["quantity"]-sent.get((line["kind"],line["lot"]),0))
    return data

def summarize(db,record,selected):
    data=pending_payload(db,record)
    supplied=defaultdict(int)
    for s in selected.values():
        for lot,q in s['materials'].items():supplied[(s['unit']['kind'],lot)]+=q or 0
    asked={(l['kind'],l['lot']):l['quantity'] for l in data['lines']}
    shortages=[dict(kind=k,lot=l,quantity=q-supplied[(k,l)]) for (k,l),q in asked.items() if supplied[(k,l)]<q]
    extras=[dict(kind=k,lot=l,quantity=q-asked.get((k,l),0)) for (k,l),q in supplied.items() if q>asked.get((k,l),0)]
    remaining=dict(asked);extra_sources=[]
    for s in selected.values():
        for lot,q in s['materials'].items():
            key=(s['unit']['kind'],lot);used=min(q or 0,remaining.get(key,0))
            remaining[key]=remaining.get(key,0)-used
            if (q or 0)>used:extra_sources.append(dict(kind=key[0],lot=lot,unit=s['unit']['id'],number=s['unit']['number'],quantity=q-used))
    result=dict(payload=data,selections=list(selected.values()),shortages=shortages,extras=extras,extra_sources=extra_sources,
        physical=sum(s['physical'] for s in selected.values()),unlocated=any(s['unit']['unlocated'] for s in selected.values()),
        uncontrolled=any(s['unit']['kind']=='lab' and any(q is None for q in s['unit']['materials'].values()) for s in selected.values()))
    return result

@v.command
def review(db,actor,ident,version,manual=None):
    record=db.get(Prep,ident);access(record,actor)
    if record.version!=version:raise ReceivingError('Revisão desatualizada.')
    if record.active_revision and db.get(Rev,record.active_revision).version==version:
        raise ReceivingError('Alterar o pedido antes de recalcular as reservas confirmadas.')
    result=calculate(db,record,manual);record.review=dump(result)
    revision(db,record,actor,'review',result)
    return result

@v.command
def confirm(db,actor,ident,version,accept_extra=False,accept_unlocated=False,accept_uncontrolled=False,expected_review=None):
    record=db.get(Prep,ident);access(record,actor)
    if record.version!=version:raise ReceivingError('Revisão desatualizada.')
    if record.active_revision:
        r=db.get(Rev,record.active_revision)
        if r.version==version:return ident
    if not record.review:raise ReceivingError('Rever antes de confirmar.')
    if expected_review is not None and expected_review!=review_key(record):raise ReceivingError('A seleção revista mudou; abrir novamente a revisão.')
    previous=json.loads(record.review)
    manual=[(s['unit']['id'],lot,q) for s in previous['selections'] for lot,q in s['materials'].items()
        if (s['unit']['kind'],lot) in {(l['kind'],l['lot']) for l in previous['payload']['lines']}]
    current=calculate(db,record,manual)
    if dump(current)!=record.review:raise ReceivingError('Stock/regras mudaram; rever novamente, sem substituir a seleção silenciosamente.')
    if current['extras'] and not accept_extra:raise ReceivingError('Confirmar explicitamente Material enviado a mais e lotes adicionais.')
    if current['unlocated'] and not accept_unlocated:raise ReceivingError('Validar explicitamente vasilhame Por localizar.')
    if current['uncontrolled'] and not accept_uncontrolled:raise ReceivingError('Validar bobines LAB sem contagem remanescente controlada.')
    r=revision(db,record,actor,'reserve',current)
    reserve_rows(db,r,current['selections'])
    record.active_revision=r.id;record.status='reserved';return ident

def reserve_rows(db,revision,selections):
    grouped={}
    for s in selections:
        uid=s['unit'].get('origin_id',s['unit']['id'])
        h=grouped.setdefault(uid,dict(physical=0,materials=defaultdict(int),exclusive=False))
        h['physical']+=s['physical'];h['exclusive']|=s['exclusive']
        if s['unit']['kind'] in ('rolos','lab'):h['physical']=1
        for lot,q in s['materials'].items():h['materials'][lot]+=q
    for uid,h in grouped.items():db.add(Hold(revision_id=revision.id,unit_id=uid,physical=h['physical'],materials=dump(dict(h['materials'])),exclusive=h['exclusive']))

@v.command
def cancel(db,actor,ident,version,note):
    record=db.get(Prep,ident)
    if not record:raise ReceivingError('Preparação inexistente.',404)
    if actor.role!='admin' and record.created_by!=actor.id:raise ReceivingError('Sem permissão.',403)
    if record.status=='cancelled':return ident
    if record.status=='shipped':raise ReceivingError('Expedição confirmada não é cancelável.')
    if record.version!=version:raise ReceivingError('Revisão desatualizada.')
    reason=required(note,'Motivo');previous=record.active_revision
    # Flush the coherent cancelled state before appending the revision: ready
    # records may not briefly retain an old reservation at the next version.
    record.active_revision=None;record.status='cancelled';record.version+=1
    revision(db,record,actor,'cancel',dict(reason=reason,previous_revision=previous));return ident

def reserved_physical(db,unit):
    rows=[h for h in balances.allocation_rows(db) if h['unit']['id']==unit.id]
    return min(unit.physical_quantity,sum(h['physical'] for h in rows))
