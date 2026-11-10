"""NKT physical pallet receipts. PEG is text, never a grouped stock quantity."""
from functools import wraps
from hashlib import sha256
from datetime import date
import json
from sqlalchemy import select,func,text
from sqlalchemy.exc import DBAPIError
from .database import NktOwner,NktEntry,NktReview,NktPallet,User,Location,Stock,ValmetPosition,Audit,now_utc,require_write_isolation
from .contexts import require_context
from .receiving import ReceivingError,request_key

def enabled_if_present(db,owner_id):
    from sqlalchemy import inspect
    return inspect(db.connection()).has_table('nkt_owners') and db.get(NktOwner,owner_id) is not None

def enabled(db):
    ctx,owner,warehouse=require_context(db)
    return db.get(NktOwner,owner.id) is not None

def dump(value):return json.dumps(value,ensure_ascii=False,sort_keys=True)
def digest(value):return sha256(value.encode()).hexdigest()
def command(fn):
    @wraps(fn)
    def run(db,actor_id,*args,**kwargs):
        try:
            if db.bind.dialect.name!='postgresql':raise ReceivingError('Operações NKT exigem PostgreSQL.',503)
            require_write_isolation(db);db.execute(text('SELECT pg_advisory_xact_lock(72834001)'));db.expire_all()
            actor=db.get(User,actor_id)
            if not actor or not actor.active or actor.role not in ('admin','operador') or not enabled(db):raise ReceivingError('Operação NKT não autorizada neste contexto.',403)
            result=fn(db,actor,*args,**kwargs);db.commit();return result
        except DBAPIError as error:
            db.rollback();raise ReceivingError('Conflito de PEG, capacidade ou revisão; operação integralmente revertida.') from error
        except Exception:db.rollback();raise
    return run

def payload(day,notes,rows):
    if day:
        try:date.fromisoformat(day)
        except (ValueError,TypeError):raise ReceivingError('Data de entrada inválida.',400)
    if not isinstance(notes,str) or len(notes)>2000 or any(ord(c)<32 and c not in '\n\r\t' for c in notes):raise ReceivingError('Observações até 2000 caracteres.',400)
    if not isinstance(rows,list) or not 1<=len(rows)<=60:raise ReceivingError('Indicar 1–60 paletes individuais.',400)
    clean=[];seen=set()
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'peg','local'}:raise ReceivingError('Indicar apenas PEG e localização por palete.',400)
        peg=row.get('peg');local=row.get('local')
        if not isinstance(peg,str) or not 1<=len(peg)<=120 or peg!=peg.strip() or any(ord(c)<32 for c in peg):raise ReceivingError('PEG: texto de 1–120 caracteres, sem espaços exteriores ou caracteres de controlo; zeros preservados.',400)
        if peg.casefold() in seen:raise ReceivingError('PEG repetido no pedido, incluindo diferenças de maiúsculas/minúsculas.',400)
        seen.add(peg.casefold())
        if not isinstance(local,str) or not local.strip() or len(local)>120:raise ReceivingError('Selecionar localização do catálogo.',400)
        clean.append(dict(peg=peg,local=local.strip()))
    return dump(dict(received_on=day or None,notes=notes,rows=clean))

def validate(db,data):
    ctx,owner,wh=require_context(db)
    # Connection query deliberately owner-wide, bypassing the ORM context filter.
    issued=list(db.connection().scalars(select(NktPallet.__table__.c.peg).where(NktPallet.__table__.c.client_id==owner.id)))
    taken={peg.casefold() for peg in issued};locations={};wanted={}
    for row in data['rows']:
        if row['peg'].casefold() in taken:raise ReceivingError('PEG já emitido para este proprietário; nunca pode ser reutilizado.')
        loc=db.scalar(select(Location).where(func.lower(Location.codigo)==row['local'].lower(),Location.active.is_(True)))
        if not loc:raise ReceivingError('Localização inexistente/inativa neste contexto; configurar em Definições.')
        row['local']=loc.codigo;locations[loc.codigo]=loc;wanted[loc.codigo]=wanted.get(loc.codigo,0)+1
    from .physical_locations import ensure_exclusive
    ensure_exclusive(db,wanted)
    for code,qty in wanted.items():
        from .nkt_loading import on_hand_query
        used=db.scalar(select(func.count()).select_from(on_hand_query(db).where(NktPallet.local==code).subquery()))
        used+=db.scalar(select(func.coalesce(func.sum(Stock.quantidade),0)).where(Stock.local==code))
        used+=db.scalar(select(func.coalesce(func.sum(ValmetPosition.physical_quantity),0)).where(ValmetPosition.local==code))
        if locations[code].capacidade is not None and used+qty>locations[code].capacidade:raise ReceivingError('Capacidade insuficiente na localização '+code+'.')
    return data

@command
def save(db,actor,key,day,notes,rows,ident=None,version=None):
    key=request_key(key);data=json.loads(payload(day,notes,rows));encoded=dump(data)
    entry=db.get(NktEntry,ident) if ident is not None else db.scalar(select(NktEntry).where(NktEntry.request_key==key))
    if entry:
        if entry.created_by!=actor.id:raise ReceivingError('Só o autor pode rever este rascunho.',403)
        if ident is None:
            if entry.payload!=encoded:raise ReceivingError('Pedido repetido com dados diferentes.')
            return entry.id
        if entry.status!='draft' or entry.request_key!=key or entry.version!=version:raise ReceivingError('Revisão desatualizada.')
        entry.payload=dump(validate(db,data));entry.version+=1
    else:
        if ident is not None:raise ReceivingError('Entrada inexistente neste contexto.',404)
        entry=NktEntry(request_key=key,payload=dump(validate(db,data)),created_by=actor.id);db.add(entry)
    encoded=entry.payload
    db.flush();review=NktReview(entry_id=entry.id,version=entry.version,payload=encoded,digest=digest(encoded),actor=actor.username);db.add(review)
    db.add(Audit(username=actor.username,action='NKT_REVER',details=dump(dict(entry=entry.id,version=entry.version,payload=data))));return entry.id

@command
def confirm(db,actor,ident,version,expected_digest):
    entry=db.get(NktEntry,ident)
    if not entry:raise ReceivingError('Entrada inexistente neste contexto.',404)
    if entry.created_by!=actor.id:raise ReceivingError('Só o autor confirma.',403)
    if entry.version!=version or digest(entry.payload)!=expected_digest:raise ReceivingError('Revisão/digest desatualizados; rever novamente.')
    if entry.status=='confirmed':return ident
    data=validate(db,json.loads(entry.payload));ctx,owner,wh=require_context(db)
    if dump(data)!=entry.payload:raise ReceivingError('O catálogo mudou; rever a localização.')
    for row in data['rows']:db.add(NktPallet(entry_id=ident,client_id=owner.id,peg=row['peg'],local=row['local'],actor=actor.username))
    db.flush();entry.status='confirmed';entry.confirmed_by=actor.username;entry.confirmed_at=now_utc();db.flush()
    db.add(Audit(username=actor.username,action='NKT_CONFIRMAR',details=dump(dict(entry=ident,version=version,digest=expected_digest,pallets=data['rows']))));return ident

def stock(db,peg='',local='',page=1,status='on_hand'):
    if not enabled(db):raise ReceivingError('Consulta NKT não autorizada neste contexto.',403)
    from .nkt_loading import on_hand_query,state_expression
    from sqlalchemy import inspect
    if status not in ('on_hand','available','reserved','shipped','all'):raise ReceivingError('Estado de stock inválido.',400)
    query=on_hand_query(db) if status=='on_hand' else select(NktPallet)
    if status in ('available','reserved','shipped'):
        if inspect(db.connection()).has_table('nkt_stock_events'):query=query.where(state_expression()==status)
        elif status!='available':query=query.where(NktPallet.id<0)
    if peg:query=query.where(NktPallet.peg.icontains(peg,autoescape=True))
    if local:query=query.where(NktPallet.local.icontains(local,autoescape=True))
    total=db.scalar(select(func.count()).select_from(query.subquery()))
    return db.scalars(query.order_by(NktPallet.id).offset((page-1)*100).limit(100)).all(),total
