"""Mobile allowlist, composing existing services under one transaction.

Audit is the existing immutable, context-scoped command receipt. No stock engine
or alternate projection is introduced. The shared write lock serializes keys.
"""
import json
from sqlalchemy import select
from . import database as m, receiving as r
from .contexts import require_context

KINDS={'ib.putaway','ib.collect','ib.shortage','cos.place','cos.missing','cos.unloaded',
       'pack.prepare','pack.unprepare','pack.exception','pack.finish_prepare',
       'pack.start_load','pack.load','pack.unload','pack.return_stage','pack.finish_load'}

def encode(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)

def previous(db,key):
    # Exact JSON key, never a substring match; only our versioned audit records.
    for row in db.scalars(select(m.Audit).where(m.Audit.action=='PWA_COMMAND',m.Audit.details.contains('"key":'+json.dumps(key),autoescape=True))):
        data=json.loads(row.details)
        if data['key']==key:return data

@r.operation(operational=False)
def execute(db,actor,action):
    if not isinstance(action,dict) or set(action)!={'key','user','context','kind','target','version','payload','depends'}:
        raise r.ReceivingError('Formato de ação inválido.',400)
    key=r.request_key(action['key']);kind=action['kind'];p=action['payload']
    if kind not in KINDS or not isinstance(p,dict):raise r.ReceivingError('Comando móvel não autorizado.',403)
    if action['user']!=actor.id or action['context']!=m.context_id(db):raise r.ReceivingError('Identidade/contexto diferente.',403)
    for field in ('target','version'):
        if type(action[field]) is not int or action[field]<0:raise r.ReceivingError('Referência/versão inválida.',400)
    deps=action['depends']
    if not isinstance(deps,list) or len(deps)>100 or len(set(deps))!=len(deps):raise r.ReceivingError('Dependências inválidas.',400)
    old=previous(db,key)
    if old:
        if old['action']!=action:raise r.ReceivingError('Chave utilizada com outros dados.')
        return old['result']
    for dep in deps:
        prior=previous(db,r.request_key(dep))
        if not prior or prior['action']['user']!=actor.id:raise r.ReceivingError('Dependência não confirmada.')
    row=require_context(db)
    if row[2].id!=1 or row[1].id not in (1,3) or (kind.startswith('ib.')!=(row[1].id==1)):
        raise r.ReceivingError('Operação fora do contexto autorizado.',403)
    if p.get('physical') is not True:raise r.ReceivingError('Confirmar explicitamente a operação física.',400)
    reason=r.required_note(p.get('reason'),'Prova física / motivo')
    ident=action['target'];version=action['version'];result=None
    # __wrapped__ retains all domain validation; the outer operation provides
    # the identical lock, runtime/actor checks and ONE commit for action+receipt.
    if kind.startswith('ib.'):require_context(db,operational=True)
    if kind=='ib.putaway':
        line,receipt=r.confirmed_line(db,ident)
        if receipt.version!=version:raise r.ReceivingError('Entrada alterada. Rever.')
        if type(p.get('stacked',False)) is not bool:raise r.ReceivingError('Tipo inválido.',400)
        result=r.review_putaway.__wrapped__(db,actor,key,ident,p.get('local'),p.get('quantity'),p.get('stacked',False))
        r.confirm_putaway.__wrapped__(db,actor,result)
    elif kind.startswith('ib.'):
        pick=db.get(m.DispatchPick,ident)
        if not pick:raise r.ReceivingError('Linha inexistente.',404)
        line=db.get(m.DispatchLine,pick.line_id);order=db.get(m.DispatchOrder,line.order_id)
        if not order.office or order.status!='confirmed' or order.version!=version or db.get(m.PreparationCompletion,order.id) or db.get(m.PreparationCompensation,order.id):raise r.ReceivingError('Preparação não disponível.')
        if kind=='ib.shortage':
            from .inventorying import report_shortage
            result=report_shortage.__wrapped__(db,actor,key,ident,p.get('quantity'),reason,p.get('observation',''))
        else:
            if p.get('quantity')!=pick.quantidade or p.get('local')!=pick.local:raise r.ReceivingError('Conferência divergente: comunicar ocorrência.')
            result=ident # Audit-only physical conference: no release or discount.
    elif kind.startswith('cos.'):
        from . import cosmopak as c
        if kind=='cos.unloaded':result=c.finish_unloading.__wrapped__(db,actor,key,ident,version,reason)
        else:
            pallet=db.get(m.CosmoPallet,ident)
            if kind=='cos.place' and (not pallet or p.get('lot')!=pallet.lot):raise r.ReceivingError('Lote conferido não corresponde à palete.')
            result=c.change_pallet.__wrapped__(db,actor,key,ident,version,kind.split('.')[1],p.get('local'),p.get('boxes'),reason,p.get('vacated') is True)
    else:
        from . import cosmopak_packings as c
        command=kind.split('.')[1];pick_id=None;packing=ident
        if command in ('prepare','unprepare','exception','load','unload','return_stage'):
            pick=db.get(m.CosmoPackingPick,ident)
            if not pick:raise r.ReceivingError('Palete inexistente.',404)
            packing=pick.packing_id;pick_id=ident
        result=c.command.__wrapped__(db,actor,key,packing,command,version,dict(lot=p.get('lot'),boxes=p.get('boxes'),checked=p.get('checked') is True,physical=True) if command in ('prepare','load','return_stage') else {},reason,pick_id)
    answer=dict(confirmed=True,result=result,key=key)
    db.add(m.Audit(username=actor.username,action='PWA_COMMAND',details=encode(dict(key=key,action=action,result=answer))))
    db.flush()
    return answer
