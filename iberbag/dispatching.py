"""Conservative FIFO: durable pick plans, per article/lot exclusion, atomic confirmation."""
import hashlib
import json
import re
from sqlalchemy import select, func
from .database import DispatchOrder, DispatchLine, DispatchPick, Stock, Carga, Location, Audit, now_utc
from .receiving import operation, ReceivingError as DispatchError, required, quantity, request_key, owned
from .location_rules import is_unassigned

ACTIVE = ('ready', 'checked', 'blocked')


def lines(db, order_id):
    return db.scalars(select(DispatchLine).where(DispatchLine.order_id == order_id).order_by(DispatchLine.id)).all()


def picks(db, order_id):
    return db.scalars(select(DispatchPick).join(DispatchLine).where(DispatchLine.order_id == order_id).order_by(DispatchPick.id)).all()


def availability(db, artigo, lote):
    total = db.scalar(select(func.coalesce(func.sum(Stock.quantidade), 0)).where(
        Stock.artigo == artigo, Stock.lote == lote, Stock.quantidade > 0))
    reserved = db.scalar(select(func.coalesce(func.sum(DispatchPick.quantidade), 0)).join(DispatchLine).join(DispatchOrder).where(
        DispatchLine.artigo == artigo, DispatchLine.lote == lote, DispatchOrder.status.in_(ACTIVE)))
    return {'total': total, 'reserved': reserved, 'free': max(0, total - reserved)}


def payload(reference, rows):
    reference = required(reference, 'Referência do pedido')
    if not 1 <= len(rows) <= 20:
        raise DispatchError('Indica 1–20 grupos de artigo/lote.', 400)
    values = [(required(a, 'Artigo'), required(l, 'Lote'), quantity(q)) for a, l, q in rows]
    if len({(a, l) for a, l, q in values}) != len(values):
        raise DispatchError('Agrupa cada artigo/lote numa única linha.', 400)
    digest = hashlib.sha256(json.dumps([reference, values], ensure_ascii=False).encode()).hexdigest()
    return reference, values, digest


@operation
def save_order(db, actor, key, reference, rows, order_id=None, version=None):
    key = request_key(key)
    reference, values, digest = payload(reference, rows)
    if order_id is None:
        order = db.scalar(select(DispatchOrder).where(DispatchOrder.request_key == key))
        if order:
            owned(order, actor)
            if order.payload_hash != digest:
                raise DispatchError('Pedido já utilizado com outros dados.')
            return order.id
        order = DispatchOrder(request_key=key, reference=reference, payload_hash=digest, created_by=actor.id, version=1)
        db.add(order); db.flush()
    else:
        from sqlalchemy import delete
        order = db.get(DispatchOrder, order_id); owned(order, actor)
        if order.status != 'draft' or order.version != version or order.request_key != key:
            raise DispatchError('Revisão desatualizada ou pedido já em recolha.')
        db.execute(delete(DispatchLine).where(DispatchLine.order_id == order.id))
        order.reference, order.payload_hash = reference, digest
        order.version += 1
    for artigo, lote, qty in values:
        db.add(DispatchLine(order_id=order.id, artigo=artigo, lote=lote, quantidade=qty))
    db.add(Audit(username=actor.username, action='RASCUNHO_SAIDA', details=json.dumps(
        {'pedido': order.id, 'revisao': order.version, 'referencia': reference, 'linhas': values}, ensure_ascii=False)))
    return order.id


def fifo(db, line):
    rows = db.execute(select(Stock, Carga).outerjoin(Carga, Carga.codigo == Stock.carga).where(
        Stock.artigo == line.artigo, Stock.lote == line.lote, Stock.quantidade > 0).order_by(
        Carga.entrada, Carga.id, Stock.id).with_for_update(of=Stock)).all()
    unknown = [stock.carga for stock, carga in rows if carga is None or carga.entrada is None]
    if unknown:
        raise DispatchError('FIFO bloqueado: data de entrada desconhecida ou carga em falta para '
                            + line.artigo + '/' + line.lote + ' (carga ' + unknown[0] + '). Requer resolução administrativa.')
    if sum(stock.quantidade for stock, carga in rows) < line.quantidade:
        raise DispatchError('Stock insuficiente para ' + line.artigo + '/' + line.lote + '.')
    plan, needed = [], line.quantidade
    for stock, carga in rows:
        if not needed: break
        if is_unassigned(stock.local) or db.scalar(select(Location.id).where(Location.codigo == stock.local)) is None:
            raise DispatchError('Localização FIFO não identificada no catálogo: requer resolução administrativa.')
        qty = min(needed, stock.quantidade)
        plan.append((stock, carga.entrada, qty))
        needed -= qty
    return plan


def other_active(db, line, order_id):
    return db.scalar(select(DispatchOrder.id).join(DispatchLine).where(
        DispatchOrder.id != order_id, DispatchOrder.status.in_(ACTIVE),
        DispatchLine.artigo == line.artigo, DispatchLine.lote == line.lote).limit(1))


@operation
def review_order(db, actor, order_id, version):
    order = db.get(DispatchOrder, order_id); owned(order, actor)
    if order.version != version:
        raise DispatchError('Revisão desatualizada.')
    if order.status in ACTIVE + ('confirmed',): return order.id
    order_lines = lines(db, order.id)
    if not order_lines: raise DispatchError('Pedido sem linhas.')
    for line in order_lines:
        busy = other_active(db, line, order.id)
        if busy:
            raise DispatchError(f'Artigo/lote em recolha ou bloqueado no pedido #{busy}. Aguardar a sua conclusão/resolução administrativa.')
        for stock, entrada, qty in fifo(db, line):
            db.add(DispatchPick(line_id=line.id, stock_id=stock.id, quantidade=qty, carga=stock.carga,
                                local=stock.local, entrada=entrada))
    db.flush()  # Freeze the plan before leaving draft state.
    order.status, order.reviewed_at = 'ready', now_utc()
    db.add(Audit(username=actor.username, action='LISTA_RECOLHA', details=f'Pedido {order.id}; FIFO revisto e reservado.'))
    return order.id


def actual_quantity(value):
    if not re.fullmatch(r'[0-9]{1,10}', str(value)) or int(value) > 2147483647:
        raise DispatchError('Conferência exige paletes inteiras, incluindo zero quando nada foi encontrado.', 400)
    return int(value)


@operation
def check_order(db, actor, order_id, actuals, note=''):
    order = db.get(DispatchOrder, order_id); owned(order, actor)
    plan = picks(db, order.id)
    if set(actuals) != {p.id for p in plan}:
        raise DispatchError('Conferir todos e apenas os grupos da lista de recolha.', 400)
    values = {ident: actual_quantity(qty) for ident, qty in actuals.items()}
    note = (note or '').strip()
    if len(note) > 1000: raise DispatchError('Observação demasiado longa.', 400)
    if order.status in ('checked', 'blocked', 'confirmed'):
        if all(p.actual == values[p.id] for p in plan) and (order.discrepancy or '') == note:
            return order.id
        raise DispatchError('A conferência já foi registada e não pode ser substituída.')
    if order.status != 'ready': raise DispatchError('Rever o pedido antes de conferir.')
    mismatch = any(p.quantidade != values[p.id] for p in plan)
    if mismatch and not note:
        raise DispatchError('Descreve a divergência física; será necessária resolução administrativa.', 400)
    for pick in plan: pick.actual = values[pick.id]
    db.flush()  # Persist the conference while the frozen plan is still ready.
    order.status = 'blocked' if mismatch else 'checked'
    order.checked_by, order.checked_at = actor.username, now_utc()
    order.discrepancy = note or None
    db.add(Audit(username=actor.username, action='DIVERGENCIA_SAIDA' if mismatch else 'CONFERIR_SAIDA',
                 details=f'Pedido {order.id}; quantidades conferidas na lista preservada.'))
    return order.id


def block(db, actor, order, reason):
    # A safety hold is durable: returning the error outside the transaction would lose it.
    order.status = 'blocked'
    order.discrepancy = ((order.discrepancy + '\n') if order.discrepancy else '') + 'Bloqueio: ' + reason
    db.add(Audit(username=actor.username, action='BLOQUEAR_SAIDA', details=f'Pedido {order.id}; {reason}'))
    return order.id


@operation
def confirm_order(db, actor, order_id):
    order = db.scalar(select(DispatchOrder).where(DispatchOrder.id == order_id).with_for_update()); owned(order, actor)
    if order.status == 'confirmed': return order.id
    if order.status != 'checked': raise DispatchError('Conferência em falta ou pedido bloqueado. Não foi alterado stock.')
    plan = picks(db, order.id)
    if not plan or any(p.actual != p.quantidade for p in plan):
        return block(db, actor, order, 'Quantidades divergentes: requer resolução administrativa.')
    for line in lines(db, order.id):
        if other_active(db, line, order.id):
            return block(db, actor, order, 'Conflito com outro pedido ativo. Requer resolução administrativa.')
        try: current = fifo(db, line)
        except DispatchError as error: return block(db, actor, order, str(error))
        expected = [(s.id, s.carga, s.local, day, qty) for s, day, qty in current]
        recorded = [(p.stock_id, p.carga, p.local, p.entrada, p.quantidade) for p in plan if p.line_id == line.id]
        if expected != recorded:
            return block(db, actor, order, 'O stock/FIFO mudou desde a recolha. Não escolher outra carga: requer resolução administrativa.')
    # All lines validated before making any change.
    for pick in plan:
        stock = db.get(Stock, pick.stock_id)
        stock.quantidade -= pick.quantidade
    order.status, order.confirmed_by, order.confirmed_at = 'confirmed', actor.username, now_utc()
    db.add(Audit(username=actor.username, action='CONFIRMAR_SAIDA', details=f'Pedido {order.id}; grupos e quantidades no histórico imutável.'))
    return order.id
