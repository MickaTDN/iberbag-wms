"""Office conference is not stock; only an evidenced administrative approval adjusts."""
import json
from hashlib import sha256
from sqlalchemy import select,func,text,inspect,case,and_
from sqlalchemy.orm import aliased
from . import database as m,alcobre as a
from .receiving import ReceivingError,request_key

def enabled(db):return inspect(db.connection()).has_table('alcobre_inventories')

def placements(db):
    if not enabled(db):return m.AlcobrePlacement
    b=m.AlcobreBobine.__table__;p=m.AlcobrePlacement.__table__;e=m.AlcobreDispatchEvent.__table__;ctx=m.context_id(db)
    last=select(e.c.bobine_id,func.max(e.c.id).label('last')).where(e.c.context_id==ctx,e.c.control_operation_id.is_not(None)).group_by(e.c.bobine_id).subquery()
    local=case((e.c.id.is_(None),p.c.local),else_=e.c.local)
    query=select(case((e.c.id.is_(None),p.c.id),else_=-e.c.id).label('id'),b.c.context_id,b.c.id.label('bobine_id'),p.c.action_id,local.label('local')).select_from(b.outerjoin(p,and_(p.c.bobine_id==b.c.id,p.c.context_id==ctx)).outerjoin(last,last.c.bobine_id==b.c.id).outerjoin(e,e.c.id==last.c.last)).where(b.c.context_id==ctx,local.is_not(None)).subquery()
    return aliased(m.AlcobrePlacement,query,adapt_on_names=True)

def incident_locations(db):
    if not enabled(db):return set()
    q=m.AlcobreInventoryCheck;r=aliased(q)
    return set(db.scalars(select(q.local).where(q.result=='unknown',q.local.is_not(None),~select(r.id).where(r.resolves_id==q.id).exists())))

def call(db,name,**params):
    # Names are hard-coded by callers; values are always bound parameters.
    statements={
        'alcobre_inventory_scope':text('SELECT alcobre_inventory_scope(:kind,CAST(:selected AS jsonb),:ctx)'),
        'alcobre_inventory_unit':text('SELECT alcobre_inventory_unit(:unit_id,:ctx)'),
        'alcobre_inventory_location':text('SELECT alcobre_inventory_location(:code,:ctx)'),
        'alcobre_inventory_pending':text('SELECT alcobre_inventory_pending(:entry,:ctx)'),
        'alcobre_inventory_summary':text('SELECT alcobre_inventory_summary(:inv_id,:ctx)'),
    }
    if name not in statements:raise ValueError('Unsupported inventory query')
    return db.scalar(statements[name],dict(params,ctx=m.context_id(db)))

def inventory(db,actor,ident,admin=False):
    inv=db.get(m.AlcobreInventory,ident)
    if not inv:raise ReceivingError('Inventário inexistente neste contexto.',404)
    a.permission(actor,inv,admin);return inv

def open_inventory(inv,version):
    if inv.status!='draft' or inv.version!=version:raise ReceivingError('Inventário confirmado ou versão desatualizada; recarregar.')

def summary(db,inv):return call(db,'alcobre_inventory_summary',inv_id=inv.id)

def archive(db,actor,inv,payload,kind,*,review=None,operation=None,reason=''):
    from .alcobre_inventory_pdf import render_pdf
    raw=render_pdf(inv.id,inv.version,inv.title,payload,kind,actor.username,m.now_utc(),reason)
    if len(raw)>4*1024*1024:raise ReceivingError('Documento demasiado grande; não truncar linhas.',400)
    db.add(m.AlcobreInventoryDocument(review_id=review,operation_id=operation,pdf=raw,digest=sha256(raw).hexdigest(),actor=actor.username))

def review_record(db,actor,inv,key,kind,reason):
    payload=a.dump(summary(db,inv));key=request_key(key)
    old=db.scalar(select(m.AlcobreInventoryReview).where(m.AlcobreInventoryReview.request_key==key))
    if old:
        if old.inventory_id!=inv.id or old.payload!=payload or old.reason!=reason or old.kind!=kind:raise ReceivingError('Chave repetida com outra revisão.')
        return old
    rev=m.AlcobreInventoryReview(inventory_id=inv.id,request_key=key,version=inv.version,kind=kind,payload=payload,digest=a.digest(payload),reason=reason,actor=actor.username);db.add(rev);db.flush()
    archive(db,actor,inv,json.loads(payload),kind,review=rev.id,reason=reason);return rev

def conference(db,actor,inv):return review_record(db,actor,inv,sha256(f'alcobre-inventory:{inv.id}:{inv.version}'.encode()).hexdigest()[:32],'conference','Conferência arquivada; não altera stock.')

@a.command
def create(db,actor,key,title,mode,locations=None,zones=None):
    key=request_key(key);title=a.value(title,'Título do inventário');locations=locations or [];zones=zones or []
    if not isinstance(locations,list) or not isinstance(zones,list) or len(locations)+len(zones)>500:raise ReceivingError('Âmbito inválido.',400)
    selected=set(a.value(v,'Localização') for v in locations)
    for zone in zones:
        zone=a.value(zone,'Zona',20)
        found=list(db.scalars(select(m.AlcobreSlot.local).join(m.AlcobreZone,m.AlcobreZone.id==m.AlcobreSlot.zone_id).where(m.AlcobreZone.code==zone,m.AlcobreZone.active.is_(True))))
        if not found:raise ReceivingError('Zona sem posições físicas configuradas neste contexto.',400)
        selected.update(found)
    if mode=='complete' and (selected or zones):raise ReceivingError('Inventário completo não aceita restrições parciais.',400)
    scope=call(db,'alcobre_inventory_scope',kind=mode,selected=a.dump(sorted(selected)))
    old=db.scalar(select(m.AlcobreInventory).where(m.AlcobreInventory.request_key==key))
    if old:
        if old.title!=title or old.mode!=mode or old.created_by!=actor.id or (mode=='partial' and json.loads(old.scope)['locations']!=sorted(selected)):raise ReceivingError('Pedido repetido com outro âmbito.')
        return old.id
    opening=dict(units=[call(db,'alcobre_inventory_unit',unit_id=i) for i in scope['units']],pending=[call(db,'alcobre_inventory_pending',entry=i) for i in scope['pending']])
    inv=m.AlcobreInventory(request_key=key,title=title,mode=mode,scope=a.dump(scope),opening=a.dump(opening),created_by=actor.id);db.add(inv);db.flush();conference(db,actor,inv)
    a.audit(db,actor,'INVENTARIO_ABRIR',dict(inventory=inv.id,scope=scope,opening=opening));return inv.id

@a.command
def record(db,actor,ident,version,result,evidence,number=None,local=None,entry_id=None,resolves=None):
    inv=inventory(db,actor,ident);open_inventory(inv,version);evidence=a.value(evidence,'Prova/observação',2000)
    number=a.value(number,'Número da bobine',optional=True);local=a.value(local,'Localização comprovada',optional=True);unit=None;src={}
    if resolves is not None:
        a.permission(actor,admin=True);original=db.get(m.AlcobreInventoryCheck,resolves)
        if not original or original.inventory_id!=inv.id or original.result!='unknown':raise ReceivingError('Ocorrência fora deste inventário.',404)
        number=original.number;local=original.local;result='resolved';src=dict(original=original.id)
    elif number:
        unit=db.scalar(select(m.AlcobreBobine).where(m.AlcobreBobine.number_key==number.casefold()))
        if unit and unit.id in json.loads(inv.scope)['units']:
            number=unit.number;src=call(db,'alcobre_inventory_unit',unit_id=unit.id)
        else:
            if unit and call(db,'alcobre_inventory_unit',unit_id=unit.id)['state']!='shipped':raise ReceivingError('Bobine fora do âmbito; não alterar inventário por inferência.')
            unit=None;result='unknown'
    elif entry_id is not None:src=call(db,'alcobre_inventory_pending',entry=entry_id)
    else:src=call(db,'alcobre_inventory_location',code=local)
    check=m.AlcobreInventoryCheck(inventory_id=inv.id,bobine_id=unit.id if unit else None,entry_id=entry_id,number=number,local=local,result=result,snapshot=a.dump(src),evidence=evidence,resolves_id=resolves,actor=actor.username)
    db.add(check);inv.version+=1;db.flush();conference(db,actor,inv);a.audit(db,actor,'INVENTARIO_CONFERIR',dict(inventory=inv.id,check=check.id,result=result,number=number,local=local,evidence=evidence,snapshot=src,resolves=resolves));return check.id

@a.command
def review(db,actor,ident,version,key,reason):
    inv=inventory(db,actor,ident,True);open_inventory(inv,version);reason=a.value(reason,'Motivo administrativo',2000);raw=summary(db,inv)
    if raw['blocking']:raise ReceivingError('Conferência incompleta/desatualizada: '+' '.join(raw['blocking']))
    rev=review_record(db,actor,inv,key,'approve',reason);a.audit(db,actor,'INVENTARIO_REVER',dict(inventory=ident,review=rev.id,reason=reason,digest=rev.digest));return rev.id

@a.command
def confirm(db,actor,ident,review_id,digest,key,accepted):
    inv=inventory(db,actor,ident,True);key=request_key(key);payload=dict(review=review_id,digest=digest)
    if not accepted:raise ReceivingError('Administrador deve aceitar expressamente os efeitos revistos.',403)
    old=db.scalar(select(m.AlcobreInventoryOperation).where(m.AlcobreInventoryOperation.request_key==key))
    if old:
        if old.inventory_id!=ident or old.payload!=a.dump(payload):raise ReceivingError('Chave repetida com efeitos diferentes.')
        return old.id
    rev=db.get(m.AlcobreInventoryReview,review_id)
    if not rev or rev.inventory_id!=ident or rev.kind!='approve' or rev.digest!=digest or a.digest(rev.payload)!=digest:raise ReceivingError('Revisão/digest inválido.')
    open_inventory(inv,rev.version);raw=summary(db,inv)
    if raw['blocking'] or a.dump(raw)!=rev.payload:raise ReceivingError('Stock/reservas/conferências mudaram; repetir conferência e revisão.')
    op=m.AlcobreInventoryOperation(inventory_id=ident,review_id=review_id,request_key=key,payload=a.dump(payload),snapshot=a.dump(raw),actor=actor.username);db.add(op);db.flush()
    for item in raw['events']:db.add(m.AlcobreDispatchEvent(control_operation_id=op.id,**item))
    db.flush();inv.status='confirmed';db.flush();archive(db,actor,inv,raw,'result',operation=op.id,reason=rev.reason)
    a.audit(db,actor,'INVENTARIO_CONFIRMAR',dict(inventory=ident,operation=op.id,reason=rev.reason,**raw));return op.id
