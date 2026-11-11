"""Exact-number Alcobre guides. Reservation is not expedition; originals are retained."""
from datetime import date
from hashlib import sha256
import json
from sqlalchemy import select, func, inspect
from . import alcobre as a, database as m
from .receiving import ReceivingError, request_key

REASONS={'available':'Disponível','reserved_self':'Reservada nesta guia','reserved_other':'Reservada noutra guia','shipped':'Já expedida','blocked':'Bloqueada por ocorrência de recolha','unlocated':'Identificada mas sem localização comprovada','unknown':'Número inexistente no stock identificado','unknown_pending':'Sem identidade comprovada; existem receções por identificar, sem associação ao número pedido','inactive':'Localização/zona inativa ou configuração indisponível'}

def enabled_events(db):return inspect(db.connection()).has_table('alcobre_dispatch_events')
def state_expression():
    e=m.AlcobreDispatchEvent.__table__;b=m.AlcobreBobine.__table__
    return func.coalesce(select(e.c.state).where(e.c.bobine_id==b.c.id,e.c.context_id==b.c.context_id).order_by(e.c.id.desc()).limit(1).scalar_subquery(),'available')

def states(db):
    if not enabled_events(db):return {}
    recent=select(func.max(m.AlcobreDispatchEvent.id).label('last')).group_by(m.AlcobreDispatchEvent.bobine_id).subquery()
    query=select(m.AlcobreDispatchEvent,m.AlcobreDispatchOperation.dispatch_id).join(recent,m.AlcobreDispatchEvent.id==recent.c.last).join(m.AlcobreDispatchOperation,m.AlcobreDispatchOperation.id==m.AlcobreDispatchEvent.operation_id)
    return {e.bobine_id:dict(state=e.state,event=e.id,operation=e.operation_id,dispatch=d) for e,d in db.execute(query)}

def current(all_states,ident):return all_states.get(ident,dict(state='available',event=None,operation=None,dispatch=None))

def live_placements(db):
    query=select(m.AlcobrePlacement).join(m.AlcobreBobine,m.AlcobreBobine.id==m.AlcobrePlacement.bobine_id)
    return query.where(state_expression()!='shipped') if enabled_events(db) else query

def numbers_value(numbers):
    if isinstance(numbers,str):numbers=numbers.splitlines()
    return [r['number'] for r in a.rows_value([dict(number=n,local=None) for n in numbers] if isinstance(numbers,list) else None)]

def plan(db,numbers,ident=None,reference=None):
    requested=numbers_value(numbers);reference=a.value(reference,'Referência da guia',200,optional=True)
    catalog={b.number_key:b for b in db.scalars(select(m.AlcobreBobine))};placements={p.bobine_id:p for p in db.scalars(select(m.AlcobrePlacement))};all_states=states(db)
    pending=any(e.quantity>a.identified(db,e.id) for e in db.scalars(select(m.AlcobreEntry).where(m.AlcobreEntry.mode=='pending',m.AlcobreEntry.status=='confirmed')))
    items=[]
    for wanted in requested:
        b=catalog.get(wanted.casefold());p=placements.get(b.id) if b else None;s=current(all_states,b.id) if b else current({},None)
        reason=('unknown_pending' if pending else 'unknown') if not b else ('unlocated' if not p else s['state'])
        if b and s['state']=='reserved':reason='reserved_self' if s['dispatch']==ident else 'reserved_other'
        if b and p and reason in ('available','reserved_self'):
            loc=db.scalar(select(m.Location).where(m.Location.codigo==p.local,m.Location.active.is_(True),m.Location.capacidade==1))
            slot=db.scalar(select(m.AlcobreSlot).join(m.AlcobreZone,m.AlcobreZone.id==m.AlcobreSlot.zone_id).where(m.AlcobreSlot.local==p.local,m.AlcobreZone.active.is_(True)))
            if not loc or not slot:reason='inactive'
        items.append(dict(requested=wanted,bobine_id=b.id if b else None,number=b.number if b else None,entry_id=b.entry_id if b else None,placement_id=p.id if p else None,local=p.local if p else None,state=s['state'] if b else None,before_event=s['event'] if b else None,reason=reason,available=reason in ('available','reserved_self')))
    return dict(reference=reference,items=items)

def get_guide(db,actor,ident):
    guide=db.get(m.AlcobreDispatch,ident)
    if not guide:raise ReceivingError('Guia de saída inexistente neste contexto.',404)
    a.permission(actor,guide);return guide

def last_operation(db,ident):return db.scalar(select(func.max(m.AlcobreDispatchOperation.id)).where(m.AlcobreDispatchOperation.dispatch_id==ident))
def latest(db,guide):return db.scalar(select(m.AlcobreDispatchRevision).where(m.AlcobreDispatchRevision.dispatch_id==guide.id,m.AlcobreDispatchRevision.version==guide.version))

def document(db,actor,guide,raw,kind,*,revision=None,operation=None,details=None):
    from .alcobre_dispatch_pdf import render_pdf
    data=render_pdf(guide.id,guide.version,raw,kind,actor.username,m.now_utc(),details or {})
    if len(data)>4*1024*1024:raise ReceivingError('Documento excede o limite seguro; não truncar a guia.',400)
    db.add(m.AlcobreDispatchDocument(revision_id=revision,operation_id=operation,pdf=data,digest=sha256(data).hexdigest(),actor=actor.username))

@a.command
def create(db,actor,key,content,filename,report):
    a.permission(actor,admin=True);key=request_key(key);filename=a.value(filename,'Nome da guia PDF',240)
    if '/' in filename or '\\' in filename or not isinstance(content,bytes) or not content.startswith(b'%PDF-') or len(content)>5*1024*1024:raise ReceivingError('Guia PDF inválida.',400)
    sha=sha256(content).hexdigest();old=db.scalar(select(m.AlcobreDispatch).where(m.AlcobreDispatch.request_key==key))
    if old:
        a.permission(actor,old)
        if old.source_digest!=sha:raise ReceivingError('Chave repetida com outra guia.')
        return old.id
    old=db.scalar(select(m.AlcobreDispatch).where(m.AlcobreDispatch.source_digest==sha))
    if old:a.permission(actor,old);return old.id
    guide=m.AlcobreDispatch(request_key=key,source_name=filename,source_pdf=content,source_digest=sha,extraction=a.dump(report),created_by=actor.id)
    db.add(guide);db.flush();a.audit(db,actor,'SAIDA_IMPORTAR',dict(dispatch=guide.id,sha256=sha));return guide.id

def preview_file(db,actor,key,content,filename,reauthorize=None):
    from .alcobre_pdf import analyze
    user=db.get(m.User,actor)
    if not user or not user.active or user.role!='admin' or not a.enabled(db):raise ReceivingError('Importar guia exige Administrador.',403)
    db.rollback();report=analyze(content)
    if reauthorize:reauthorize()
    return create(db,actor,key,content,filename,report)

@a.command
def review(db,actor,ident,version,key,numbers,reason,manual=False,reference=None):
    guide=get_guide(db,actor,ident);key=request_key(key);reason=a.value(reason,'Prova/observação',2000)
    if guide.status in ('confirmed','cancelled') or guide.version!=version:raise ReceivingError('Guia encerrada ou revisão desatualizada.')
    requested=numbers_value(numbers);source=json.loads(guide.extraction)
    if not manual and (not source.get('reliable') or requested!=[r['number'] for r in source.get('candidates',[])]):raise ReceivingError('Extração ambígua/corrigida: exigir transcrição manual comprovada.',400)
    raw=plan(db,requested,ident,reference);encoded=a.dump(raw)
    old=db.scalar(select(m.AlcobreDispatchRevision).where(m.AlcobreDispatchRevision.request_key==key))
    if old:
        if old.dispatch_id!=ident or old.payload!=encoded or old.reason!=reason or old.manual!=bool(manual):raise ReceivingError('Chave de revisão repetida com outra decisão.')
        return old.id
    guide.version+=1
    r=m.AlcobreDispatchRevision(dispatch_id=ident,version=guide.version,request_key=key,payload=encoded,digest=a.digest(encoded),manual=bool(manual),reason=reason,actor=actor.username)
    db.add(r);db.flush();document(db,actor,guide,raw,'review',revision=r.id);a.audit(db,actor,'SAIDA_REVER',dict(dispatch=ident,revision=r.id,reason=reason,manual=bool(manual),plan=raw));return r.id

def replay(db,guide,key,kind,payload):
    old=db.scalar(select(m.AlcobreDispatchOperation).where(m.AlcobreDispatchOperation.request_key==request_key(key)))
    if old:
        if old.dispatch_id!=guide.id or old.kind!=kind or old.payload!=a.dump(payload):raise ReceivingError('Chave repetida com efeitos diferentes.')
        return old.id

def operation(db,actor,guide,rev,key,kind,payload,snapshot):
    op=m.AlcobreDispatchOperation(dispatch_id=guide.id,revision_id=rev.id if rev else None,request_key=request_key(key),kind=kind,payload=a.dump(payload),snapshot=a.dump(snapshot),actor=actor.username)
    db.add(op);db.flush()
    for e in snapshot['events']:db.add(m.AlcobreDispatchEvent(operation_id=op.id,**e))
    db.flush();return op

def event(ident,before,state):return dict(bobine_id=ident,previous_id=before,state=state)

@a.command
def prepare(db,actor,ident,version,digest,key,accepted):
    guide=get_guide(db,actor,ident);request=dict(version=version,digest=digest)
    if not accepted:raise ReceivingError('Aceitar expressamente a lista e todas as SEM STOCK.',400)
    old=replay(db,guide,key,'prepare',request)
    if old:return old
    rev=latest(db,guide)
    if guide.status in ('confirmed','cancelled') or guide.version!=version or not rev or rev.digest!=digest or a.digest(rev.payload)!=digest or guide.active_revision==rev.id:raise ReceivingError('Guia encerrada ou revisão já preparada/desatualizada.')
    raw=json.loads(rev.payload);fresh=plan(db,[r['requested'] for r in raw['items']],ident,raw['reference'])
    if fresh!=raw:raise ReceivingError('Stock/reservas/localizações mudaram; criar nova revisão, sem substituições.')
    all_states=states(db);wanted={r['bobine_id'] for r in raw['items'] if r['available']};changes=[]
    for uid,s in all_states.items():
        if s['state']=='reserved' and s['dispatch']==ident and uid not in wanted:changes.append(event(uid,s['event'],'available'))
    for r in raw['items']:
        if r['available'] and r['state']=='available':changes.append(event(r['bobine_id'],r['before_event'],'reserved'))
    snap=dict(plan=raw,before=last_operation(db,ident),events=changes)
    op=operation(db,actor,guide,rev,key,'prepare',request,snap);guide.active_revision=rev.id;guide.status='prepared';db.flush()
    document(db,actor,guide,raw,'prepare',operation=op.id);a.audit(db,actor,'SAIDA_PREPARAR',dict(dispatch=ident,operation=op.id,**snap));return op.id

def prepared_basis(db,guide,rev,before):
    if guide.status!='prepared' or guide.active_revision!=rev.id or last_operation(db,guide.id)!=before:raise ReceivingError('Confirmar a preparação atual; houve uma operação/revisão posterior.')
    op=db.get(m.AlcobreDispatchOperation,before)
    if not op or op.kind!='prepare':raise ReceivingError('Preparação atual inexistente.')
    raw=json.loads(rev.payload);fresh=plan(db,[r['requested'] for r in raw['items']],guide.id,raw['reference']);all_states=states(db)
    for old,now in zip(raw['items'],fresh['items']):
        if old['available']:
            state=current(all_states,old['bobine_id'])
            match=state['operation']==op.id if old['state']=='available' else state['event']==old['before_event']
            if state['state']!='reserved' or state['dispatch']!=guide.id or not match or any(old[k]!=now[k] for k in ('requested','number','bobine_id','placement_id','local','entry_id')):raise ReceivingError('Bobine/reserva/localização mudou; rever toda a guia antes de confirmar.')
        elif old!=now:raise ReceivingError('Uma SEM STOCK mudou; criar revisão e preparação explícitas, sem atribuição automática.')
    return raw,all_states

@a.command
def confirm(db,actor,ident,version,before,key,collected,exceptions,loaded_on,accepted):
    guide=get_guide(db,actor,ident)
    if not accepted:raise ReceivingError('Aceitar a confirmação única da guia e todas as exceções.',400)
    try:
        if not isinstance(loaded_on,str) or date.fromisoformat(loaded_on).isoformat()!=loaded_on:raise ValueError()
    except (TypeError,ValueError):raise ReceivingError('Indicar data efetiva comprovada.',400)
    collected=[] if collected in (None,[]) else numbers_value(collected)
    if not isinstance(exceptions,dict):raise ReceivingError('Indicar as diferenças de recolha.',400)
    clean={}
    for number,resolution in exceptions.items():
        number=a.value(number,'Bobine da ocorrência')
        if not isinstance(resolution,dict) or set(resolution)!={'status','reason','local','physical_confirmed'} or resolution['status'] not in ('present','not_found'):
            raise ReceivingError('Classificar cada não recolhida: presente no armazém ou não encontrada.',400)
        reason=a.value(resolution['reason'],'Motivo da não recolha',2000)
        if resolution['status']=='present':
            if resolution['physical_confirmed'] is not True:raise ReceivingError('Confirmar fisicamente a presença antes de libertar a reserva.',400)
            local=a.value(resolution['local'],'Localização física comprovada')
        else:
            if resolution['physical_confirmed'] is not False or resolution['local'] is not None:raise ReceivingError('Não encontrada não pode declarar presença/localização comprovada.',400)
            local=None
        clean[number]=dict(status=resolution['status'],reason=reason,local=local,physical_confirmed=resolution['physical_confirmed'])
    exceptions=clean
    request=dict(version=version,before=before,collected=collected,exceptions=exceptions,loaded_on=loaded_on)
    old=replay(db,guide,key,'confirm',request)
    if old:return old
    rev=latest(db,guide)
    if not rev or guide.version!=version:raise ReceivingError('Revisão desatualizada.')
    raw,all_states=prepared_basis(db,guide,rev,before)
    candidates={r['requested']:r for r in raw['items'] if r['available']}
    if not set(collected)<=set(candidates) or set(exceptions)!=set(candidates)-set(collected):raise ReceivingError('Assinalar exatamente as bobines recolhidas e justificar cada reservada não recolhida; não expedir SEM STOCK.',400)
    changes=[];items=[]
    for r in raw['items']:
        item=dict(r)
        if r['available']:
            sent=r['requested'] in collected;s=current(all_states,r['bobine_id'])
            resolution=None if sent else exceptions[r['requested']]
            if resolution and resolution['status']=='present':
                from .warehouse_settings import occupied
                if resolution['local']!=r['local'] or sum(q for _,q in occupied(db,[resolution['local']]))!=1:
                    raise ReceivingError('A presença deve ser comprovada na posição atual e exclusiva; divergências de localização exigem reconciliação, sem transferência implícita.')
            outcome='shipped' if sent else resolution['status']
            changes.append(event(r['bobine_id'],s['event'],{'shipped':'shipped','present':'available','not_found':'blocked'}[outcome]))
            item.update(outcome=outcome,difference=None if sent else resolution['reason'],resolution=resolution)
        else:item.update(outcome='exception',difference=REASONS[r['reason']],resolution=None)
        items.append(item)
    snap=dict(plan=dict(raw,items=items),before=before,events=changes,loaded_on=loaded_on,collected=len(collected),exceptions=len(items)-len(collected))
    op=operation(db,actor,guide,rev,key,'confirm',request,snap);guide.status='confirmed';db.flush();document(db,actor,guide,snap['plan'],'confirm',operation=op.id,details=snap)
    a.audit(db,actor,'SAIDA_CONFIRMAR',dict(dispatch=ident,operation=op.id,**snap));return op.id

@a.command
def cancel(db,actor,ident,version,before,key,reason):
    guide=get_guide(db,actor,ident);reason=a.value(reason,'Motivo do cancelamento',2000);request=dict(version=version,before=before,reason=reason)
    old=replay(db,guide,key,'cancel',request)
    if old:return old
    rev=latest(db,guide)
    if guide.status in ('confirmed','cancelled') or guide.version!=version or last_operation(db,ident)!=before:raise ReceivingError('Só cancelar guias ainda não expedidas e sem operação posterior.')
    changes=[event(uid,s['event'],'available') for uid,s in states(db).items() if s['state']=='reserved' and s['dispatch']==ident]
    raw=json.loads(rev.payload) if rev else dict(reference=None,items=[]);snap=dict(plan=raw,before=before,events=changes,reason=reason)
    op=operation(db,actor,guide,rev,key,'cancel',request,snap);guide.status='cancelled';db.flush();document(db,actor,guide,raw,'cancel',operation=op.id,details=snap)
    a.audit(db,actor,'SAIDA_CANCELAR',dict(dispatch=ident,operation=op.id,**snap));return op.id
