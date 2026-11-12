"""Individual Cosmopak reception, over existing stock/load/physical services."""
from collections import Counter
from datetime import date,datetime
import hashlib,json,secrets
from sqlalchemy import select,inspect
from .database import (CosmoReception as Reception,CosmoPallet as Pallet,CosmoState as State,
    CosmoOperation as Operation,CosmoReview as Review,Receipt,ReceiptLine,Carga,Stock,Audit,now_utc)
from .receiving import operation,ReceivingError,required,required_note,request_key
from . import physical_locations as physical
from .cosmopak_excel import analyze,integer


def enabled(db):
    from .contexts import require_context
    _,owner,warehouse=require_context(db)
    return owner.id==physical.SHARING_OWNER_IDS[1] and warehouse.id==physical.SHARING_WAREHOUSE_ID


def guard(db,actor,admin=False):
    if not enabled(db):raise ReceivingError('Contexto Cosmopak/Ovar obrigatório.',403)
    if admin and actor.role!='admin':raise ReceivingError('Apenas Administrador.',403)


def dump(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def record(db,ident):
    row=db.get(Reception,ident)
    if not row:raise ReceivingError('Receção inexistente neste contexto.',404)
    return row


def pallets(db,ident):
    return db.execute(select(Pallet,State).join(State,State.pallet_id==Pallet.id).where(Pallet.reception_id==ident).order_by(Pallet.source_row,Pallet.ordinal)).all()


def snapshot(db,rec):
    return dict(reception_id=rec.id,version=rec.version,guide=rec.guide,
        arrived_on=rec.arrived_on.date().isoformat() if rec.arrived_on else None,legacy_code=rec.legacy_code,
        pallets=[dict(id=a.id,version=b.version,status=b.status,local=b.local,actual_boxes=b.actual_boxes,
            decision=b.decision,physical_version=(physical.common_state(db,b.local) or {}).get('version')) for a,b in pallets(db,rec.id)])


def previous(db,model,key,**fields):
    old=db.scalar(select(model).where(model.request_key==request_key(key)))
    if old:
        if any(getattr(old,k)!=v for k,v in fields.items()):raise ReceivingError('Pedido já usado com outros dados.')
        return old


def refresh(db):db.flush();db.expire_all()


def preview_file(db,actor,key,content,filename,reauthorize=None):
    # No write lock or transaction held during ZIP/XML processing.
    if db.in_transaction():db.rollback()
    try:report=analyze(content)
    except ValueError as error:raise ReceivingError(str(error),400) from error
    if reauthorize:reauthorize()
    return store(db,actor,key,content,filename,report)


@operation(operational=False)
def store(db,actor,key,content,filename,report):
    guard(db,actor,True);key=request_key(key)
    if not isinstance(filename,str) or not 1<=len(filename)<=240 or any(ord(c)<32 or c in '/\\' for c in filename):raise ReceivingError('Nome de ficheiro inválido.',400)
    old=previous(db,Reception,key,source_sha256=hashlib.sha256(content).hexdigest(),source_name=filename)
    if old:return old.id
    rec=Reception(request_key=key,source_name=filename,source_sha256=report['sha256'],source_excel=content,report=dump(report),created_by=actor.id)
    db.add(rec);db.flush()
    if not report['issues']:
        for row in report['rows']:
            for ordinal in range(1,row['pallets']+1):
                db.add(Pallet(reception_id=rec.id,source_row=row['row'],ordinal=ordinal,article=row['article'],description=row['description'],lot=row['lot'],boxes=row['boxes'],incomplete=row['incomplete']))
    db.add(Audit(username=actor.username,action='COSMOPAK_IMPORTAR_PREVISAO',details=f'Receção {rec.id}; SHA-256 {rec.source_sha256}; sem stock oficial.'))
    return rec.id


@operation(operational=False)
def change_pallet(db,actor,key,pallet_id,version,kind,local=None,actual_boxes=None,reason='',vacated=False,decision=None):
    guard(db,actor,kind=='decide');reason=required_note(reason,'Confirmação física / motivo')
    if kind not in ('place','missing','decide'):raise ReceivingError('Operação inválida.',400)
    if actual_boxes not in (None,''):
        try:actual_boxes=integer(actual_boxes)
        except ValueError as error:raise ReceivingError(str(error),400) from error
    else:actual_boxes=None
    if kind=='place':
        local=required(local,'Localização').upper()
        h=physical.common_state(db,local)
        if not h:raise ReceivingError('Escolher uma estante partilhada ativada. Chão não é permitido.',400)
    else:local=None
    fields=dict(pallet_id=pallet_id,expected_version=version,kind=kind,local=local,actual_boxes=actual_boxes,reason=reason,vacated=vacated is True,decision=decision)
    old=previous(db,Operation,key,**fields)
    if old:return old.id
    row=db.get(Pallet,pallet_id)
    if not row:raise ReceivingError('Palete inexistente neste contexto.',404)
    state=db.scalar(select(State).where(State.pallet_id==row.id))
    if state.version!=version or state.status=='approved':raise ReceivingError('Palete alterada ou aprovada; recarregar.')
    op=Operation(request_key=request_key(key),actor_id=actor.id,**fields);db.add(op);refresh(db)
    db.add(Audit(username=actor.username,action='COSMOPAK_'+kind.upper(),details=f'Palete interna {pallet_id}; movimento {op.id}; {reason}'))
    return op.id


def add_review(db,actor,key,rec,kind,version,payload,reason):
    reason=required_note(reason,'Comprovação / decisão')
    fields=dict(reception_id=rec.id,kind=kind,expected_version=version,payload=dump(payload),reason=reason)
    old=previous(db,Review,key,**fields)
    if old:return old.id
    if rec.version!=version:raise ReceivingError('Receção alterada. Reabrir a revisão.')
    row=Review(request_key=request_key(key),actor_id=actor.id,**fields);db.add(row);refresh(db)
    db.add(Audit(username=actor.username,action='COSMOPAK_'+kind.upper(),details=f'Receção {rec.id}; revisão {row.id}; {reason}'))
    return row.id


@operation(operational=False)
def metadata(db,actor,key,ident,version,guide,arrived_on,legacy_code,reason):
    guard(db,actor,True);rec=record(db,ident)
    try:day=date.fromisoformat(arrived_on)
    except (ValueError,TypeError):raise ReceivingError('Indicar data efetiva de chegada.',400)
    code=required(legacy_code,'Referência histórica da carga') if day.year<2027 else None
    payload=dict(guide=required(guide,'Guia de transporte'),arrived_on=day.isoformat(),legacy_code=code)
    return add_review(db,actor,key,rec,'metadata',version,payload,reason)


@operation(operational=False)
def finish_unloading(db,actor,key,ident,version,reason):
    guard(db,actor);rec=record(db,ident)
    return add_review(db,actor,key,rec,'unloaded',version,{},reason)


@operation(operational=False)
def review(db,actor,key,ident,version,reason):
    guard(db,actor,True);rec=record(db,ident)
    return add_review(db,actor,key,rec,'review',version,snapshot(db,rec),reason)


@operation(operational=False)
def approve(db,actor,key,ident,review_id,accepted=False):
    guard(db,actor,True);key=request_key(key);rec=record(db,ident)
    if accepted is not True:raise ReceivingError('Aceitar explicitamente a revisão integral.',400)
    rev=db.get(Review,review_id)
    if not rev or rev.reception_id!=ident or rev.kind!='review':raise ReceivingError('Revisão administrativa inexistente.',404)
    old=db.scalar(select(Review).where(Review.reception_id==ident,Review.kind=='approve'))
    if rec.status=='approved':
        if not old or json.loads(old.payload)['review_id']!=review_id:raise ReceivingError('Receção aprovada com outra revisão.')
        return rec.receipt_id
    expected=json.loads(rev.payload);current=snapshot(db,rec);current['version']=expected['version']
    if rec.status!='reviewed' or rec.version!=rev.expected_version+1 or current!=expected:raise ReceivingError('A revisão está desatualizada; rever descarga e localizações novamente.')
    receipt=Receipt(request_key=secrets.token_hex(16),payload_hash=hashlib.sha256(rev.payload.encode()).hexdigest(),code=rec.legacy_code or 'Automática na confirmação',received_on=rec.arrived_on,created_by=actor.id)
    db.add(receipt);db.flush()
    from .load_numbering import allocate
    receipt.code=allocate(db,receipt)
    if db.scalar(select(Carga.id).where(Carga.codigo==receipt.code)) or db.scalar(select(Stock.id).where(Stock.carga==receipt.code)):raise ReceivingError('Referência de carga já utilizada.')
    carga=Carga(codigo=receipt.code,entrada=rec.arrived_on,funcionario=None);db.add(carga);db.flush()
    units=pallets(db,ident);totals=Counter((p.article,p.lot) for p,s in units if s.decision=='accept')
    for (article,lot),qty in totals.items():db.add(ReceiptLine(receipt_id=receipt.id,artigo=article,lote=lot,quantidade=qty))
    db.flush();receipt.carga_id=carga.id;receipt.receiver=actor.username;receipt.status='confirmed';receipt.confirmed_at=now_utc();db.flush()
    for pallet,state in units:
        if state.decision!='accept':continue
        stock=db.scalar(select(Stock).where(Stock.artigo==pallet.article,Stock.lote==pallet.lot,Stock.carga==receipt.code,Stock.local==state.local,Stock.stacked.is_(False)))
        if stock:raise ReceivingError('Há um saldo anterior nesta posição/grupo; reconciliar.')
        stock=Stock(artigo=pallet.article,lote=pallet.lot,carga=receipt.code,local=state.local,quantidade=1);db.add(stock);db.flush()
        op=Operation(request_key=hashlib.sha256((key+':'+str(pallet.id)).encode()).hexdigest()[:32],pallet_id=pallet.id,kind='approve',expected_version=state.version,actor_id=actor.id,stock_id=stock.id,receipt_id=receipt.id,reason='Aprovação integral da revisão '+str(rev.id))
        db.add(op);db.flush()
    add_review(db,actor,key,rec,'approve',rec.version,dict(receipt_id=receipt.id,review_id=rev.id),'Aprovação integral da receção comprovada')
    return receipt.id


def validate_snapshot(data):
    """Pure archive reconciliation, never repairs physical or accounting balances."""
    from .transfer import json_value
    receipts={r['id']:r for r in data['cosmo_receptions']}
    stock={r['id']:r for r in data['stock']}
    contexts={r['id']:r for r in data['storage_contexts']}
    for rec in receipts.values():
        ctx=contexts.get(rec['context_id'])
        if not ctx or ctx['client_id']!=3 or ctx['warehouse_id']!=1 or hashlib.sha256(rec['source_excel']).hexdigest()!=rec['source_sha256']:raise ValueError('Origem/contexto Cosmopak divergente.')
    for pallet in data['cosmo_pallets']:
        state=next((s for s in data['cosmo_states'] if s['pallet_id']==pallet['id']),None)
        operations=[r for r in data['cosmo_operations'] if r['pallet_id']==pallet['id']]
        if not state or state['context_id']!=pallet['context_id']:raise ValueError('Projeção Cosmopak ausente/fora do contexto.')
        if operations:
            expected=json.loads(max(operations,key=lambda o:o['id'])['after_state'])
            if expected!=json.loads(json.dumps(state,default=json_value)) or state['version']!=len(operations):raise ValueError('Livro e projeção Cosmopak divergentes.')
        elif state['status']!='planned' or state['version']!=0 or state['local'] is not None:raise ValueError('Palete Cosmopak sem movimento comprovado.')
        rec=receipts[pallet['reception_id']]
        if state['status'] in ('placed','blocked','approved'):
            master=next((m for m in data['physical_locations'] if m['warehouse_id']==1 and m['label']==state['local']),None)
            physical_state=next((h for h in data['physical_location_state'] if master and h['physical_id']==master['id']),None)
            if not physical_state or physical_state['context_id']!=state['context_id']:raise ValueError('Ocupação Cosmopak sem correspondência física comum.')
            if state['status']!='approved' and (physical_state['state']!='pending_putaway' or physical_state['source_kind']!='cosmopak' or physical_state['source_id']!=pallet['id']):raise ValueError('Origem da atribuição Cosmopak divergente.')
            if state['status']=='approved' and (physical_state['state']!='occupied' or physical_state['stock_id']!=state['stock_id']):raise ValueError('Stock Cosmopak sem ocupação física correspondente.')
        if state['status']=='missing' and state['local'] is not None:raise ValueError('Falta Cosmopak não pode libertar uma posição sem prova física.')
        if state['status']=='approved':
            receipt=next((r for r in data['receipts'] if r['id']==rec['receipt_id'] and r['context_id']==rec['context_id']),None)
            if rec['status']!='approved' or not receipt or receipt['status']!='confirmed':raise ValueError('Receção Cosmopak sem aprovação integral.')
            goods=stock.get(state['stock_id'])
            if not goods or goods['context_id']!=pallet['context_id'] or goods['quantidade']!=1 or goods['artigo']!=pallet['article'] or goods['lote']!=pallet['lot'] or goods['local']!=state['local'] or goods['carga']!=receipt['code']:raise ValueError('Stock oficial Cosmopak divergente da identidade física.')
