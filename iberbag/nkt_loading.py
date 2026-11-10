"""Explicit PEG ledger. No FIFO, inferred pallet or mutable stock projection."""
import json
from hashlib import sha256
from sqlalchemy import select,func,inspect
from . import nkt
from .nkt_receiving import office,bounded_text,metadata
from .database import (NktPreparation as Prep,NktPlanRevision as Rev,NktOperation as Op,NktStockEvent as Event,
    NktCorrection as Correction,NktDocument as Document,NktPallet as Pallet,Location,Stock,ValmetPosition,Audit,now_utc)
from .receiving import ReceivingError,request_key
from .nkt_loading_pdf import render_pdf

LABELS={'draft':'Rascunho — preparado, sem novas reservas','reserve':'Lista de carregamento — reservado','cancel':'Cancelamento dos PEG não expedidos','ship':'Confirmação de carregamento','restore':'Correção — restituição física comprovada','metadata':'Correção de data / GT'}

def state_expression():
    e=Event.__table__;p=Pallet.__table__
    return func.coalesce(select(e.c.state).where(e.c.pallet_id==p.c.id,e.c.context_id==p.c.context_id).order_by(e.c.id.desc()).limit(1).scalar_subquery(),'available')

def on_hand_query(db):
    query=select(Pallet)
    if inspect(db.connection()).has_table('nkt_stock_events'):query=query.where(state_expression()!='shipped')
    return query

def states(db,pallet_ids=None):
    from .database import context_id
    t=Event.__table__
    recent=select(func.max(t.c.id).label('last')).where(t.c.context_id==context_id(db)).group_by(t.c.pallet_id).subquery()
    query=select(Event.pallet_id,Event.state,Event.id,Op.id,Op.preparation_id).join(recent,Event.id==recent.c.last).join(Op,Op.id==Event.operation_id)
    if pallet_ids is not None:query=query.where(Event.pallet_id.in_(pallet_ids))
    return {pid:dict(state=state,event=eid,operation=oid,preparation=prep) for pid,state,eid,oid,prep in db.execute(query)}

def state_for(all_states,ident):return all_states.get(ident,dict(state='available',event=None,operation=None,preparation=None))
def latest(db,prep):return db.scalar(select(Rev).where(Rev.preparation_id==prep.id,Rev.version==prep.version))
def last_operation(db,ident):return db.scalar(select(func.max(Op.id)).where(Op.preparation_id==ident))
def permitted(db,actor,prep=None):
    office(db,actor)
    if prep is not None and actor.role!='admin' and prep.created_by!=actor.id:raise ReceivingError('Só o autor ou Administrador altera a preparação.',403)

def pegs(value):
    if isinstance(value,str):value=value.splitlines()
    if not isinstance(value,list) or not 1<=len(value)<=60:raise ReceivingError('Indicar 1–60 PEG específicos, um por linha.',400)
    nkt.payload(None,'',[dict(peg=p,local='Chão') for p in value]);return value

def date_value(value):
    from datetime import date
    try:
        if not isinstance(value,str) or date.fromisoformat(value).isoformat()!=value:raise ValueError()
    except (ValueError,TypeError):raise ReceivingError('Indicar data comprovada no formato AAAA-MM-DD.',400)
    return value

def identify(db,requested):
    wanted=pegs(requested);catalog={p.peg.casefold():p for p in db.scalars(select(Pallet))};out=[]
    for code in wanted:
        pallet=catalog.get(code.casefold())
        if not pallet:raise ReceivingError('PEG inexistente neste contexto: '+code+'.')
        out.append(pallet)
    info=metadata(db,[p.entry_id for p in out]);result=[]
    for p in out:
        details=info.get(p.entry_id,{});content=next((i for i in details.get('items',[]) if i['peg']==p.peg),{})
        result.append(dict(pallet_id=p.id,peg=p.peg,material=content.get('material'),container=details.get('container'),entry_id=p.entry_id,local=p.local))
    return result

def plan(db,reference,planned_on,order,requested,prep=None):
    reference=bounded_text(reference,'Identificação da carga de saída');planned_on=date_value(planned_on)
    try:
        if isinstance(order,bool) or str(int(order))!=str(order) or not 0<=int(order)<=1000000:raise ValueError()
    except (ValueError,TypeError):raise ReceivingError('Ordem: inteiro entre 0 e 1000000.',400)
    items=identify(db,requested);current=states(db);ids={i['pallet_id'] for i in items}
    for item in items:
        s=state_for(current,item['pallet_id'])
        if s['state']!='available' and (prep is None or s['preparation']!=prep.id):raise ReceivingError('PEG reservado ou expedido por outra preparação: '+item['peg']+'.')
    if prep and any(s['state']=='shipped' and s['preparation']==prep.id and pid not in ids for pid,s in current.items()):raise ReceivingError('Não remover PEG já expedidos sem compensação administrativa.')
    return dict(reference=reference,planned_on=planned_on,order=int(order),items=items)

def audit(db,actor,action,details):db.add(Audit(username=actor.username,action='NKT_'+action,details=nkt.dump(details)))
def document(db,actor,prep,plan,kind,*,revision=None,operation=None,correction=None,extra='',items=None):
    raw=render_pdf(prep.id,prep.version,plan,LABELS[kind],actor.username,now_utc(),extra=extra,items=items)
    db.add(Document(revision_id=revision,operation_id=operation,correction_id=correction,pdf=raw,digest=sha256(raw).hexdigest(),actor=actor.username))

@nkt.command
def save(db,actor,key,reference,planned_on,order,requested,reason,ident=None,version=None):
    key=request_key(key);reason=bounded_text(reason,'Motivo/evidência',2000)
    prep=db.get(Prep,ident) if ident is not None else db.scalar(select(Prep).where(Prep.request_key==key))
    permitted(db,actor,prep)
    if prep:
        if ident is None:
            first=db.scalar(select(Rev).where(Rev.preparation_id==prep.id,Rev.version==1))
            raw=plan(db,reference,planned_on,order,requested,prep)
            if first.payload!=nkt.dump(raw):raise ReceivingError('Pedido repetido com outra seleção.')
            return prep.id
        if prep.status in ('loaded','cancelled') or prep.request_key!=key or prep.version!=version:raise ReceivingError('Preparação encerrada ou versão desatualizada.')
        raw=plan(db,reference,planned_on,order,requested,prep);prep.version+=1
    else:
        if ident is not None:raise ReceivingError('Preparação inexistente neste contexto.',404)
        raw=plan(db,reference,planned_on,order,requested);prep=Prep(request_key=key,created_by=actor.id);db.add(prep)
    db.flush();encoded=nkt.dump(raw);revision=Rev(preparation_id=prep.id,version=prep.version,payload=encoded,digest=nkt.digest(encoded),reason=reason,actor=actor.username);db.add(revision);db.flush()
    document(db,actor,prep,raw,'draft',revision=revision.id,extra='Não autoriza saída nem altera reservas existentes.');audit(db,actor,'PREPARAR',dict(preparation=prep.id,revision=revision.id,reason=reason,plan=raw));return prep.id

def event(pid,previous,state):return dict(pallet_id=pid,previous_event_id=previous,state=state)
def operation(db,actor,prep,revision,key,kind,payload,snapshot,*,correction=None,corrects=None):
    record=Op(preparation_id=prep.id,revision_id=revision.id,request_key=key,kind=kind,payload=nkt.dump(payload),snapshot=nkt.dump(snapshot),correction_id=correction,corrects_id=corrects,actor=actor.username)
    db.add(record);db.flush()
    for row in snapshot['events']:db.add(Event(operation_id=record.id,**row))
    db.flush();return record

def replay(db,key,ident,kind,payload):
    key=request_key(key);old=db.scalar(select(Op).where(Op.request_key==key))
    if old:
        if old.preparation_id!=ident or old.kind!=kind or old.payload!=nkt.dump(payload):raise ReceivingError('Chave repetida com efeitos diferentes.')
        return old

def get_preparation(db,actor,ident):
    prep=db.get(Prep,ident)
    if not prep:raise ReceivingError('Preparação inexistente neste contexto.',404)
    permitted(db,actor,prep);return prep

def current_revision(db,prep,version,digest=None):
    rev=latest(db,prep)
    if prep.version!=version or not rev or (digest is not None and (rev.digest!=digest or nkt.digest(rev.payload)!=digest)):raise ReceivingError('Revisão desatualizada; recarregar e conferir.')
    return rev

def changed_since(db,prep,before):
    if last_operation(db,prep.id)!=before:raise ReceivingError('Houve uma operação posterior. Rever novamente.')

def update_status(db,prep):
    if prep.status=='cancelled':return
    rev=db.get(Rev,prep.active_revision);items=json.loads(rev.payload)['items'];current=states(db,[i['pallet_id'] for i in items]);sent=sum(state_for(current,i['pallet_id'])['state']=='shipped' and state_for(current,i['pallet_id'])['preparation']==prep.id for i in items)
    prep.status='loaded' if sent==len(items) else ('partial' if sent else 'reserved')

@nkt.command
def reserve(db,actor,ident,version,digest,key,accepted):
    prep=get_preparation(db,actor,ident);request=dict(version=version,digest=digest)
    if not accepted:raise ReceivingError('Aceitar expressamente a seleção revista.',400)
    old=replay(db,key,ident,'reserve',request)
    if old:return old.id
    rev=current_revision(db,prep,version,digest)
    if prep.status in ('cancelled','loaded') or prep.active_revision==rev.id:raise ReceivingError('Preparação já confirmada/encerrada.')
    raw=json.loads(rev.payload);fresh=plan(db,raw['reference'],raw['planned_on'],raw['order'],[i['peg'] for i in raw['items']],prep)
    if fresh!=raw:raise ReceivingError('Metadados originais divergentes; rever sem inventar informação.')
    current=states(db);wanted={i['pallet_id'] for i in raw['items']};changes=[]
    for pid,s in current.items():
        if s['state']=='reserved' and s['preparation']==ident and pid not in wanted:changes.append(event(pid,s['event'],'available'))
    for item in raw['items']:
        pid=item['pallet_id'];s=state_for(current,pid)
        if s['state']=='available':changes.append(event(pid,s['event'],'reserved'))
    pending=[i for i in raw['items'] if state_for(current,i['pallet_id'])['state']!='shipped']
    snapshot=dict(plan=raw,before_operation=last_operation(db,ident),events=changes,pending_items=pending)
    op=operation(db,actor,prep,rev,key,'reserve',request,snapshot);prep.active_revision=rev.id;update_status(db,prep);db.flush()
    document(db,actor,prep,raw,'reserve',operation=op.id,extra=f'Carregar apenas estes PEG ainda reservados. Já expedidos nesta seleção: {len(raw["items"])-len(pending)}. Sem desconto na impressão.',items=pending);audit(db,actor,'RESERVAR',dict(operation=op.id,**snapshot));return op.id

@nkt.command
def ship(db,actor,ident,version,before,key,requested,loaded_on,gt):
    prep=get_preparation(db,actor,ident);requested=pegs(requested);loaded_on=date_value(loaded_on);gt=bounded_text(gt,'GT')
    request=dict(version=version,before=before,pegs=requested,loaded_on=loaded_on,gt=gt)
    old=replay(db,key,ident,'ship',request)
    if old:return old.id
    rev=current_revision(db,prep,version);changed_since(db,prep,before)
    if prep.status not in ('reserved','partial') or prep.active_revision!=rev.id:raise ReceivingError('Confirmar a revisão/reservas antes de carregar.')
    raw=json.loads(rev.payload);selected=identify(db,requested);allowed={i['pallet_id'] for i in raw['items']};current=states(db);changes=[]
    for item in selected:
        pid=item['pallet_id'];s=state_for(current,pid)
        if pid not in allowed or s['state']!='reserved' or s['preparation']!=ident:raise ReceivingError('PEG não reservado nesta preparação ou já expedido.')
        changes.append(event(pid,s['event'],'shipped'))
    snapshot=dict(plan=raw,before_operation=before,events=changes,loaded_on=loaded_on,gt=gt,loaded_items=selected)
    op=operation(db,actor,prep,rev,key,'ship',request,snapshot);update_status(db,prep);db.flush()
    document(db,actor,prep,raw,'ship',operation=op.id,extra=f'Carregamento {op.id} · Data real: {loaded_on} · GT: {gt}',items=selected);audit(db,actor,'CARREGAR',dict(operation=op.id,**snapshot));return op.id

@nkt.command
def cancel(db,actor,ident,version,before,key,reason):
    prep=get_preparation(db,actor,ident);reason=bounded_text(reason,'Motivo',2000);request=dict(version=version,before=before,reason=reason)
    old=replay(db,key,ident,'cancel',request)
    if old:return old.id
    rev=current_revision(db,prep,version);changed_since(db,prep,before)
    if prep.status in ('loaded','cancelled'):raise ReceivingError('Preparação carregada/cancelada não é um rascunho.')
    raw=json.loads(rev.payload);current=states(db);changes=[event(pid,s['event'],'available') for pid,s in current.items() if s['state']=='reserved' and s['preparation']==ident]
    released=identify(db,[p.peg for p in db.scalars(select(Pallet).where(Pallet.id.in_([e['pallet_id'] for e in changes])))]) if changes else []
    snapshot=dict(plan=raw,before_operation=before,events=changes,released_items=released)
    op=operation(db,actor,prep,rev,key,'cancel',request,snapshot);prep.status='cancelled';prep.active_revision=rev.id;db.flush();document(db,actor,prep,raw,'cancel',operation=op.id,extra=reason+'; carregamentos anteriores preservados.',items=released);audit(db,actor,'CANCELAR',dict(operation=op.id,reason=reason,**snapshot));return op.id

def correction_effects(db,prep,original,kind,requested,loaded_on,gt,reason):
    if original.kind!='ship' or original.preparation_id!=prep.id:raise ReceivingError('Referenciar um carregamento original desta preparação.')
    original_data=json.loads(original.snapshot);current=states(db);effects=[]
    if kind=='restore':
        selected=identify(db,requested);original_ids={i['pallet_id'] for i in original_data['loaded_items']};by_location={}
        for item in selected:
            pid=item['pallet_id'];s=state_for(current,pid)
            if pid not in original_ids or s['state']!='shipped' or s['operation']!=original.id:raise ReceivingError('Stock já compensado, reservado ou movimentado posteriormente; reconciliação necessária.')
            loc=db.scalar(select(Location).where(Location.codigo==item['local'],Location.active.is_(True)))
            if not loc:raise ReceivingError('Localização original inexistente/inativa; não restituir automaticamente.')
            from .warehouse_settings import occupied
            from .physical_locations import ensure_exclusive
            ensure_exclusive(db,[loc.codigo])
            occupation=sum(q for code,q in occupied(db,[loc.codigo]));by_location[loc.codigo]=by_location.get(loc.codigo,0)+1
            if loc.capacidade is not None and occupation+by_location[loc.codigo]>loc.capacidade:raise ReceivingError('Capacidade original insuficiente; restituição bloqueada.')
            effects.append(dict(item=item,event=s['event'],local=loc.codigo,capacity=loc.capacidade,occupied=occupation))
    elif kind=='metadata':
        if requested not in (None,'',[]):raise ReceivingError('Correção de GT/data não seleciona PEG nem altera stock.')
        date_value(loaded_on);bounded_text(gt,'GT corrigida')
    else:raise ReceivingError('Tipo de correção inválido.',400)
    return dict(version=prep.version,active=prep.active_revision,before_operation=last_operation(db,prep.id),original=original.id,kind=kind,loaded_on=loaded_on,gt=gt,reason=reason,effects=effects)

@nkt.command
def review_correction(db,actor,ident,original_id,key,kind,requested,loaded_on,gt,reason):
    if actor.role!='admin':raise ReceivingError('Correções apenas para Administrador.',403)
    prep=get_preparation(db,actor,ident);original=db.get(Op,original_id)
    if not original:raise ReceivingError('Carregamento original inexistente.',404)
    if not prep.active_revision or db.get(Rev,prep.active_revision).version!=prep.version:raise ReceivingError('Resolver a revisão pendente antes da correção.')
    reason=bounded_text(reason,'Motivo e comprovação física',2000);key=request_key(key)
    payload=dict(original=original_id,kind=kind,pegs=pegs(requested) if kind=='restore' else requested,loaded_on=loaded_on,gt=gt,reason=reason)
    old=db.scalar(select(Correction).where(Correction.request_key==key))
    if old:
        if old.preparation_id!=ident or old.payload!=nkt.dump(payload):raise ReceivingError('Chave de correção repetida com outra decisão.')
        return old.id
    snapshot=correction_effects(db,prep,original,kind,payload['pegs'],loaded_on,gt,reason);encoded=nkt.dump(snapshot)
    fix=Correction(preparation_id=ident,operation_id=original_id,request_key=key,payload=nkt.dump(payload),snapshot=encoded,digest=nkt.digest(encoded),actor=actor.username);db.add(fix);db.flush()
    raw=json.loads(db.get(Rev,prep.active_revision).payload)
    document(db,actor,prep,raw,kind,correction=fix.id,extra=f'REVISÃO — sem movimento. Original {original_id}. {reason}. GT/data propostas: {gt or "Não aplicável"} / {loaded_on or "Não aplicável"}',items=[e['item'] for e in snapshot['effects']] if kind=='restore' else json.loads(original.snapshot)['loaded_items'])
    audit(db,actor,'REVER_CORRECAO',dict(correction=fix.id,**snapshot));return fix.id

@nkt.command
def apply_correction(db,actor,ident,digest,accepted,key):
    if actor.role!='admin' or not accepted:raise ReceivingError('Administrador deve aceitar os efeitos revistos.',403)
    fix=db.get(Correction,ident)
    if not fix:raise ReceivingError('Correção inexistente neste contexto.',404)
    prep=get_preparation(db,actor,fix.preparation_id);request=dict(correction=ident,digest=digest);payload=json.loads(fix.payload);kind=payload['kind']
    old=replay(db,key,prep.id,kind,request)
    if old:return old.id
    if db.scalar(select(Op.id).where(Op.correction_id==ident)):raise ReceivingError('Correção já aplicada; não repetir a restituição.')
    if fix.digest!=digest or nkt.digest(fix.snapshot)!=digest:raise ReceivingError('Digest de correção inválido.')
    original=db.get(Op,fix.operation_id);effects=correction_effects(db,prep,original,kind,payload['pegs'],payload['loaded_on'],payload['gt'],payload['reason'])
    if nkt.dump(effects)!=fix.snapshot:raise ReceivingError('Estado alterado desde a revisão; refazer a conferência administrativa.')
    rev=latest(db,prep);raw=json.loads(rev.payload);changes=[event(e['item']['pallet_id'],e['event'],'available') for e in effects['effects']]
    snapshot=dict(plan=raw,before_operation=effects['before_operation'],events=changes,effects=effects)
    op=operation(db,actor,prep,rev,key,kind,request,snapshot,correction=fix.id,corrects=original.id)
    if kind=='restore' and prep.status!='cancelled':prep.status='reconciliation';db.flush()
    document(db,actor,prep,raw,kind,operation=op.id,extra=f"Operação original {original.id} · {payload['reason']} · GT corrigida: {payload['gt'] or 'Não aplicável'} · Data corrigida: {payload['loaded_on'] or 'Não aplicável'}",items=[e['item'] for e in effects['effects']] if kind=='restore' else json.loads(original.snapshot)['loaded_items'])
    audit(db,actor,'COMPENSAR',dict(operation=op.id,original=original.id,**snapshot));return op.id
