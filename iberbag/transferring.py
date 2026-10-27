"""Location transfers: conserve article/lot/load quantities and existing FIFO reservations."""
from sqlalchemy import select
from .database import Stock, Carga, Location, StockTransfer, DispatchOrder, DispatchLine, Audit, now_utc
from .dispatching import ACTIVE
from .location_rules import is_floor, is_unassigned
from .receiving import (operation, ReceivingError as TransferError, quantity, request_key, owned,
                        canonical_location, destination, MAX_INT)


def original_date(db, carga):
    return db.scalar(select(Carga.entrada).where(Carga.codigo == carga))


def source_stock(db, stock_id, qty):
    stock = db.scalar(select(Stock).where(Stock.id == stock_id).with_for_update())
    if stock is None or stock.quantidade < qty:
        raise TransferError('Stock insuficiente na origem. Atualiza a consulta e revê a transferência.')
    if is_unassigned(stock.local) or db.scalar(select(Location.id).where(Location.codigo == stock.local)) is None:
        raise TransferError('A origem não está identificada no catálogo. Requer reconciliação administrativa.')
    return stock


def protect_dispatches(db, stock):
    from .lots import assert_lot
    assert_lot(db, stock.lote, stock.artigo)
    # Even a group outside the present plan can affect the deterministic FIFO selection.
    active = db.scalar(select(DispatchOrder.id).join(DispatchLine).where(
        DispatchLine.artigo == stock.artigo, DispatchLine.lote == stock.lote,
        DispatchOrder.status.in_(ACTIVE)).limit(1))
    if active is not None:
        raise TransferError(f'Transferência bloqueada: artigo/lote reservado ou em recolha/conferência no pedido de saída #{active}. '
                            'As listas e reservas de saída não foram alteradas.')


def target_location(db, stock, local, qty):
    local = canonical_location(db, local)
    if local == stock.local or (is_floor(local) and is_floor(stock.local)):
        raise TransferError('Origem e destino não podem ser a mesma localização.', 400)
    return destination(db, local, qty)


@operation
def review_transfer(db, actor, key, stock_id, local, quantidade):
    key, qty = request_key(key), quantity(quantidade)
    previous = db.scalar(select(StockTransfer).where(StockTransfer.request_key == key))
    if previous:
        owned(previous, actor)
        if (previous.source_stock_id, previous.destination_local, previous.quantidade) != (stock_id, canonical_location(db, local), qty):
            raise TransferError('Este pedido já foi usado com outros dados. Abre um novo formulário.')
        return previous.id
    stock = source_stock(db, stock_id, qty)
    protect_dispatches(db, stock)
    local = target_location(db, stock, local, qty)
    record = StockTransfer(request_key=key, source_stock_id=stock.id, artigo=stock.artigo,
        lote=stock.lote, carga=stock.carga, entrada=original_date(db, stock.carga), source_local=stock.local,
        destination_local=local, quantidade=qty, created_by=actor.id)
    db.add(record); db.flush()
    db.add(Audit(username=actor.username, action='REVER_TRANSFERENCIA', details=f'Transferência {record.id}; stock {stock.id}; {qty} paletes; {stock.local} → {local}.'))
    return record.id


@operation
def confirm_transfer(db, actor, transfer_id):
    record = db.scalar(select(StockTransfer).where(StockTransfer.id == transfer_id).with_for_update())
    owned(record, actor)
    if record.status == 'confirmed':
        return record.id
    stock = source_stock(db, record.source_stock_id, record.quantidade)
    if (stock.artigo, stock.lote, stock.carga, stock.local, original_date(db, stock.carga)) != (
            record.artigo, record.lote, record.carga, record.source_local, record.entrada):
        raise TransferError('Os dados da origem mudaram. Revê novamente antes de transferir.')
    protect_dispatches(db, stock)
    local = target_location(db, stock, record.destination_local, record.quantidade)
    if local != record.destination_local:
        raise TransferError('O catálogo de destino mudou. Revê a transferência.')
    target = db.scalar(select(Stock).where(Stock.artigo == stock.artigo, Stock.lote == stock.lote,
        Stock.carga == stock.carga, Stock.local == local).with_for_update())
    if target is None:
        target = Stock(artigo=stock.artigo, lote=stock.lote, carga=stock.carga, local=local, quantidade=record.quantidade)
        db.add(target)
    else:
        if target.quantidade + record.quantidade > MAX_INT:
            raise TransferError('O grupo de destino excede o limite técnico de quantidade.')
        target.quantidade += record.quantidade
    stock.quantidade -= record.quantidade
    db.flush()
    record.destination_stock_id, record.confirmed_by = target.id, actor.username
    record.status, record.confirmed_at = 'confirmed', now_utc()
    db.add(Audit(username=actor.username, action='CONFIRMAR_TRANSFERENCIA',
        details=f'Transferência {record.id}; {stock.id} → {target.id}; {record.quantidade} paletes; {record.source_local} → {local}.'))
    return record.id
