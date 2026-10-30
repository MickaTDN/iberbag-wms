"""Office commercial context, append-only backorders and direct incoming allocation.

All commands share the PostgreSQL operation lock with stock, inventory and settings.
Customer allocation is an accounting dispatch, not stock or a temporary reservation.
"""
import hashlib
import json
from sqlalchemy import select, func, delete
from .database import (CommercialPreparation, MercadonaDestination, CustomerPending,
    PendingAllocation, AllocationCollection, Receipt, ReceiptLine, ReceiptDocument,
    DispatchOrder, DispatchLine, DispatchPick, IncompleteComposition, Stock, Audit, now_utc)
from .receiving import operation, ReceivingError, required, required_note, request_key, quantity, owned
from .lots import resolve


def context(db, order_id):
    return db.get(CommercialPreparation, order_id)


@operation
def save_preparation(db, actor, key, reference, customer, channel, rows, order_id=None, version=None):
    """Rows are lot, whole pallets, destination (blank for ordinary customers)."""
    key=request_key(key); reference=required(reference,'Encomenda'); customer=required(customer,'Cliente')
    if channel not in ('normal','mercadona'): raise ReceivingError('Tipo de preparação inválido.',400)
    if channel=='mercadona': customer='Mercadona'
    if not 1<=len(rows)<=20: raise ReceivingError('Indicar 1–20 linhas.',400)
    values=[(resolve(db,required(l,'Lote')),required(l,'Lote'),quantity(q),required(d,'Destino') if channel=='mercadona' else '') for l,q,d in rows]
    if len({(l,d) for a,l,q,d in values})!=len(values): raise ReceivingError('Agrupar o mesmo lote/destino numa única linha.',400)
    digest=hashlib.sha256(json.dumps([reference,customer,channel,values],ensure_ascii=False).encode()).hexdigest()
    if order_id is None:
        order=db.scalar(select(DispatchOrder).where(DispatchOrder.request_key==key))
        if order:
            owned(order,actor)
            if order.payload_hash!=digest or not context(db,order.id): raise ReceivingError('Pedido já utilizado com outros dados.')
            return order.id
        order=DispatchOrder(request_key=key,reference=reference,payload_hash=digest,created_by=actor.id,office=True,version=1)
        db.add(order);db.flush()
        info=CommercialPreparation(order_id=order.id,customer=customer,channel=channel);db.add(info)
    else:
        order=db.get(DispatchOrder,order_id);owned(order,actor)
        if not order.office or order.status!='draft' or order.version!=version or order.request_key!=key:
            raise ReceivingError('Rascunho desatualizado ou não editável.')
        info=context(db,order.id)
        if info is None:
            info=CommercialPreparation(order_id=order.id,customer=customer,channel=channel);db.add(info)
        db.execute(delete(DispatchPick).where(DispatchPick.line_id.in_(select(DispatchLine.id).where(DispatchLine.order_id==order.id))))
        db.execute(delete(DispatchLine).where(DispatchLine.order_id==order.id))
        order.reference,order.payload_hash,order.reviewed_at=reference,digest,None
        order.version+=1
        info.customer,info.channel=customer,channel
    for artigo,lote,qty,dest in values:
        option=db.scalar(select(MercadonaDestination).where(MercadonaDestination.name==dest,MercadonaDestination.active.is_(True))) if dest else None
        if channel=='mercadona' and not option: raise ReceivingError('Destino Mercadona inexistente ou inativo. Configurar em Definições.')
        db.add(DispatchLine(order_id=order.id,artigo=artigo,lote=lote,quantidade=qty,destination=dest,
            destination_order=option.sort_order if option else 0,destination_highlight=option.highlighted if option else False))
    db.add(Audit(username=actor.username,action='RASCUNHO_COMERCIAL',details=f'Preparação {order.id}; cliente {customer}; {channel}; revisão {order.version}.'))
    return order.id


def line_prepared(db,line_id):
    return db.scalar(select(func.coalesce(func.sum(DispatchPick.quantidade),0)).where(DispatchPick.line_id==line_id))


def satisfied(db,pending_id):
    return db.scalar(select(func.coalesce(func.sum(PendingAllocation.quantidade),0)).where(PendingAllocation.pending_id==pending_id))


def allocated(db,line_id):
    return db.scalar(select(func.coalesce(func.sum(PendingAllocation.quantidade),0)).where(PendingAllocation.receipt_line_id==line_id))


def pending_rows(db):
    return db.execute(select(CustomerPending,DispatchLine,DispatchOrder,CommercialPreparation).join(
        DispatchLine,DispatchLine.id==CustomerPending.line_id).join(DispatchOrder,DispatchOrder.id==DispatchLine.order_id).join(
        CommercialPreparation,CommercialPreparation.order_id==DispatchOrder.id).order_by(CustomerPending.created_at,CustomerPending.id)).all()


def incoming_plan(db,receipt_id):
    """Read-only simulation used for review; confirmations recompute under the shared lock."""
    demands=[]
    for pending,line,order,info in pending_rows(db):
        balance=pending.quantidade-satisfied(db,pending.id)
        if balance>0: demands.append([pending,line,order,info,balance])
    result=[]
    for line in db.scalars(select(ReceiptLine).where(ReceiptLine.receipt_id==receipt_id).order_by(ReceiptLine.id)):
        left=line.quantidade
        for item in demands:
            pending,old,order,info,balance=item
            if old.lote!=line.lote or not balance: continue
            if old.artigo!=line.artigo: raise ReceivingError('Pendente com artigo/lote contraditório; reconciliação administrativa necessária.')
            take=min(left,balance)
            if take:
                result.append(dict(line=line,pending=pending,order=order,customer=info.customer,destination=old.destination,quantidade=take))
                left-=take;item[4]-=take
        if left:result.append(dict(line=line,pending=None,order=None,customer='',destination='',quantidade=left))
    return result


def allocations(db,receipt_id=None):
    query=select(PendingAllocation,CustomerPending,DispatchLine,DispatchOrder,CommercialPreparation,ReceiptLine,Receipt).join(
        CustomerPending,CustomerPending.id==PendingAllocation.pending_id).join(DispatchLine,DispatchLine.id==CustomerPending.line_id).join(
        DispatchOrder,DispatchOrder.id==DispatchLine.order_id).join(CommercialPreparation,CommercialPreparation.order_id==DispatchOrder.id).join(
        ReceiptLine,ReceiptLine.id==PendingAllocation.receipt_line_id).join(Receipt,Receipt.id==ReceiptLine.receipt_id)
    if receipt_id is not None:query=query.where(Receipt.id==receipt_id)
    return db.execute(query.order_by(PendingAllocation.id)).all()


def allocate_incoming(db,actor,receipt):
    """Called inside reception confirmation, before commit; never creates Stock rows."""
    from .dispatching import other_active
    for item in incoming_plan(db,receipt.id):
        if not item['pending']:continue
        line=item['line']
        if other_active(db,line,-1): raise ReceivingError('Reservas antigas interferem com pendentes deste lote. Resolver administrativamente antes da entrada.')
        db.add(PendingAllocation(pending_id=item['pending'].id,receipt_line_id=line.id,quantidade=item['quantidade'],actor=actor.username))
        db.add(Audit(username=actor.username,action='ATRIBUIR_PENDENTE',details=f'Pendente {item["pending"].id}; entrada {receipt.id}; linha {line.id}; {item["quantidade"]} paletes carregadas contabilisticamente; separadas a aguardar recolha.'))
    db.flush()
    for allocation,*_ in allocations(db,receipt.id):invalidate_group_counts(db,allocation)
    archive_receipt(db,receipt)


def archive_receipt(db,receipt):
    from .commercial_pdf import render_receipt
    if db.get(ReceiptDocument,receipt.id): return
    rows=[]
    for alloc,pending,line,order,info,received,header in allocations(db,receipt.id):
        rows.append(dict(artigo=received.artigo,lote=received.lote,quantidade=alloc.quantidade,
            destination=info.customer+(' / '+line.destination if line.destination else ''),reference=order.reference,
            status='Cliente — carregada contabilisticamente; separar para recolha'))
    for line in db.scalars(select(ReceiptLine).where(ReceiptLine.receipt_id==receipt.id).order_by(ReceiptLine.id)):
        qty=line.quantidade-allocated(db,line.id)
        if qty:rows.append(dict(artigo=line.artigo,lote=line.lote,quantidade=qty,destination='Stock normal',reference='',status='Arrumação normal — registar localização no WMS'))
    from .contexts import label
    receipt.ddn_label=label(db)
    pdf=render_receipt(receipt,rows)
    db.add(ReceiptDocument(receipt_id=receipt.id,pdf=pdf,sha256=hashlib.sha256(pdf).hexdigest()))


@operation
def archive_legacy_receipt(db,actor,receipt_id):
    receipt=db.get(Receipt,receipt_id)
    if receipt is None or receipt.status!='confirmed':raise ReceivingError('Entrada confirmada não encontrada.',404)
    archive_receipt(db,receipt)
    db.add(Audit(username=actor.username,action='PDF_ENTRADA',details=f'Entrada {receipt_id}; documento histórico sem novos movimentos.'))
    return receipt_id


def outstanding_group(db,artigo,lote,carga):
    return db.scalar(select(PendingAllocation.id).join(ReceiptLine,ReceiptLine.id==PendingAllocation.receipt_line_id).join(
        Receipt,Receipt.id==ReceiptLine.receipt_id).outerjoin(AllocationCollection,AllocationCollection.allocation_id==PendingAllocation.id).where(
        ReceiptLine.artigo==artigo,ReceiptLine.lote==lote,Receipt.code==carga,AllocationCollection.allocation_id.is_(None)).limit(1))


def invalidate_group_counts(db,allocation):
    from .inventorying import touch_location
    line=db.get(ReceiptLine,allocation.receipt_line_id);receipt=db.get(Receipt,line.receipt_id)
    locations=set(db.scalars(select(Stock.local).where(Stock.artigo==line.artigo,Stock.lote==line.lote,Stock.carga==receipt.code)))
    for local in locations|{'Chão'}:touch_location(db,local)


@operation
def record_collection(db,actor,allocation_id,reason):
    if actor.role!='admin':raise ReceivingError('Só Administrador pode registar a resolução física.',403)
    reason=required_note(reason,'Comprovação da recolha integral')
    item=db.get(PendingAllocation,allocation_id)
    if not item:raise ReceivingError('Atribuição inexistente.',404)
    if db.get(AllocationCollection,allocation_id):return allocation_id
    db.add(AllocationCollection(allocation_id=allocation_id,reason=reason,actor=actor.username))
    invalidate_group_counts(db,item)
    db.add(Audit(username=actor.username,action='RECOLHA_PENDENTE',details=f'Atribuição {allocation_id}; {reason}; sem movimento de stock.'))
    return allocation_id


@operation
def save_destination(db,actor,name,sort_order,active=True,highlighted=False,ident=None):
    if actor.role!='admin':raise ReceivingError('Só Administrador pode configurar destinos.',403)
    name=required(name,'Destino')
    try:rank=int(sort_order)
    except (ValueError,TypeError):raise ReceivingError('Ordem inválida.',400)
    if not -100000<=rank<=100000:raise ReceivingError('Ordem inválida.',400)
    item=db.get(MercadonaDestination,ident) if ident else MercadonaDestination()
    if item is None:raise ReceivingError('Destino inexistente.',404)
    item.name,item.sort_order,item.active,item.highlighted=name,rank,active,highlighted
    db.add(item);db.flush()
    db.add(Audit(username=actor.username,action='DESTINO_MERCADONA',details=f'Destino {item.id}; {name}; ordem {rank}; ativo {active}; destaque {highlighted}.'))
    return item.id


@operation
def save_composition(db,actor,key,reference,rows,observation,receipt_line_id=None):
    key=request_key(key);reference=required(reference,'Referência do dossiê');observation=required_note(observation,'Observação')
    if not 1<=len(rows)<=30:raise ReceivingError('Indicar 1–30 medidas/caixas.',400)
    components=[]
    for measure,boxes in rows:
        try:box_count=quantity(boxes)
        except ReceivingError:raise ReceivingError('Caixas: indicar um número inteiro positivo, sem conversões.',400)
        components.append((required(measure,'Medida'),box_count))
    if len({m for m,b in components})!=len(components):raise ReceivingError('Agrupar cada medida numa linha.',400)
    payload=json.dumps(components,ensure_ascii=False)
    old=db.scalar(select(IncompleteComposition).where(IncompleteComposition.request_key==key))
    if old:
        if (old.reference,old.components,old.observation,old.receipt_line_id)!=(reference,payload,observation,receipt_line_id):raise ReceivingError('Pedido já usado com outros dados.')
        return old.id
    if receipt_line_id:
        line=db.get(ReceiptLine,receipt_line_id)
        if not line or line.quantidade!=1 or db.get(Receipt,line.receipt_id).status!='confirmed':raise ReceivingError('Referenciar apenas uma linha real confirmada de uma palete.')
    record=IncompleteComposition(request_key=key,reference=reference,components=payload,observation=observation,
        receipt_line_id=receipt_line_id,physical_pallets=1,actor=actor.username)
    db.add(record);db.flush()
    db.add(Audit(username=actor.username,action='COMPOSICAO_RASCUNHO',details=f'Dossiê {record.id}; uma palete; sem conversão ou movimento de stock.'))
    return record.id
