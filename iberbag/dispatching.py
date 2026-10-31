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


def fifo(db, line, allow_shortage=False, consumed=None):
    from .lots import resolve
    article = resolve(db,line.lote,required=False)
    if article is not None and article != line.artigo:
        raise DispatchError('Artigo/lote contraditório. Exige reconciliação administrativa.')
    rows = db.execute(select(Stock, Carga).outerjoin(Carga, Carga.codigo == Stock.carga).where(
        Stock.artigo == line.artigo, Stock.lote == line.lote, Stock.quantidade > 0).order_by(
        Carga.entrada, Carga.id, Stock.id).with_for_update(of=Stock)).all()
    unknown = [stock.carga for stock, carga in rows if carga is None or carga.entrada is None]
    if unknown:
        raise DispatchError('FIFO bloqueado: data de entrada desconhecida ou carga em falta para '
                            + line.artigo + '/' + line.lote + ' (carga ' + unknown[0] + '). Requer resolução administrativa.')
    consumed = consumed if consumed is not None else {}
    if not allow_shortage and sum(stock.quantidade-consumed.get(stock.id,0) for stock, carga in rows) < line.quantidade:
        raise DispatchError('Stock insuficiente para ' + line.artigo + '/' + line.lote + '.')
    plan, needed = [], line.quantidade
    for stock, carga in rows:
        if not needed: break
        free=stock.quantidade-consumed.get(stock.id,0)
        if free<=0:continue
        if is_unassigned(stock.local) or db.scalar(select(Location.id).where(Location.codigo == stock.local)) is None:
            raise DispatchError('Localização FIFO não identificada no catálogo: requer resolução administrativa.')
        qty = min(needed, free)
        plan.append((stock, carga.entrada, qty))
        needed -= qty
        consumed[stock.id]=consumed.get(stock.id,0)+qty
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


@operation
def save_preparation(db, actor, key, reference, lot_rows, order_id=None, version=None):
    from .lots import resolve
    rows = [(resolve(db, required(lote, 'Lote')), required(lote, 'Lote'), quantidade) for lote, quantidade in lot_rows]
    # Reuse the validated draft writer inside this same transaction, without nested commit.
    if order_id:
        order = db.get(DispatchOrder, order_id); owned(order, actor)
        if not order.office or order.status != 'draft' or order.version != version:
            raise DispatchError('Rascunho desatualizado ou legado. Não foi alterado.')
        from sqlalchemy import delete
        db.execute(delete(DispatchPick).where(DispatchPick.line_id.in_(select(DispatchLine.id).where(DispatchLine.order_id == order_id))))
        order.reviewed_at = None
    ident = save_order.__wrapped__(db, actor, key, reference, rows, order_id, version)
    db.get(DispatchOrder, ident).office = True
    return ident


@operation
def review_preparation(db, actor, order_id, version):
    from sqlalchemy import delete
    from .lots import resolve
    order = db.get(DispatchOrder, order_id); owned(order, actor)
    if not order.office or order.version != version or order.status != 'draft':
        raise DispatchError('Preparação não editável ou versão desatualizada.')
    db.execute(delete(DispatchPick).where(DispatchPick.line_id.in_(select(DispatchLine.id).where(DispatchLine.order_id == order_id))))
    from .commercial import context
    commercial=context(db,order_id)
    consumed={}
    for line in lines(db, order_id):
        if resolve(db, line.lote) != line.artigo:
            raise DispatchError('A associação do lote mudou. Rever o rascunho.')
        if other_active(db, line, order_id): raise DispatchError('Existem reservas antigas para este lote; resolução administrativa necessária.')
        for stock, entrada, qty in fifo(db, line,allow_shortage=bool(commercial),consumed=consumed):
            db.add(DispatchPick(line_id=line.id,stock_id=stock.id,quantidade=qty,carga=stock.carga,local=stock.local,entrada=entrada))
    order.reviewed_at = now_utc()
    db.add(Audit(username=actor.username,action='REVER_PREPARACAO',details=f'Preparação {order.id}; sem reserva temporária.'))
    return order.id


@operation
def confirm_preparation(db, actor, order_id):
    from .lots import resolve
    from .database import DispatchDocument
    from .inventorying import clock
    from .preparation_pdf import render_pdf
    order = db.get(DispatchOrder, order_id); owned(order, actor)
    if order.status == 'confirmed': return order.id
    if not order.office or order.status != 'draft' or not order.reviewed_at:
        raise DispatchError('Rever a preparação antes de confirmar. Pedidos cancelados não descontam stock.')
    from .commercial import context
    commercial=context(db,order_id)
    plan = picks(db, order_id)
    if not plan and not commercial: raise DispatchError('Lista FIFO vazia.')
    consumed={}
    by_line = {line.id: line for line in lines(db, order_id)}
    if not by_line:raise DispatchError('Preparação sem linhas.')
    for line in by_line.values():
        if resolve(db,line.lote) != line.artigo or other_active(db,line,order_id):
            raise DispatchError('Lote em conflito ou com reservas antigas; requer reconciliação.')
        current = fifo(db,line,allow_shortage=bool(commercial),consumed=consumed)
        expected=[(s.id,s.carga,s.local,day,qty) for s,day,qty in current]
        recorded=[(p.stock_id,p.carga,p.local,p.entrada,p.quantidade) for p in plan if p.line_id==line.id]
        if expected != recorded:
            raise DispatchError('Stock/FIFO mudou desde a revisão. Atualizar e rever novamente, sem desconto.')
    for pick in plan: db.get(Stock,pick.stock_id).quantidade -= pick.quantidade
    order.status, order.confirmed_by, order.confirmed_at = 'confirmed', actor.username, now_utc()
    # Compatibility fields describe the office validation, not a warehouse collection.
    order.checked_by, order.checked_at = actor.username, order.confirmed_at
    db.flush()
    rows=[dict(lote=by_line[p.line_id].lote,artigo=by_line[p.line_id].artigo,
               quantidade=p.quantidade,carga=p.carga,local=p.local) for p in plan]
    from .contexts import label
    order.ddn_label = label(db)
    if commercial:
        from .database import CustomerPending
        from .commercial_pdf import render_preparation
        for line in by_line.values():
            missing=line.quantidade-sum(p.quantidade for p in plan if p.line_id==line.id)
            if missing:db.add(CustomerPending(line_id=line.id,quantidade=missing,created_at=order.confirmed_at,actor=actor.username))
        pdf=render_preparation(order,commercial,list(by_line.values()),plan)
    else:
        pdf=render_pdf(order,rows)
    versions={p.local:clock(db,p.local) for p in plan}
    db.add(DispatchDocument(order_id=order.id,pdf=pdf,sha256=hashlib.sha256(pdf).hexdigest(),stock_versions=json.dumps(versions)))
    db.add(Audit(username=actor.username,action='CONFIRMAR_PREPARACAO',details=f'Preparação {order.id}; stock descontado; PDF arquivado.'))
    return order.id


@operation
def cancel_order(db, actor, order_id, reason, returned=False):
    if actor.role != 'admin': raise DispatchError('Só Administrador pode cancelar pedidos.',403)
    from .receiving import required_note
    reason=required_note(reason,'Motivo')
    order=db.get(DispatchOrder,order_id)
    if order is None: raise DispatchError('Pedido inexistente.',404)
    if order.status=='confirmed': raise DispatchError('Preparação confirmada: usar ocorrência/inventário ou compensação, nunca cancelar o original.')
    if order.status=='cancelled': return order.id
    if order.status in ACTIVE and not returned:
        raise DispatchError('Confirmar que não há paletes em recolha ou que foram devolvidas antes de libertar a reserva.')
    if order.status in ACTIVE:
        from .inventorying import touch_location
        for local in {pick.local for pick in picks(db,order.id)}: touch_location(db,local)
    order.status='cancelled'; order.cancellation_reason=reason
    order.cancelled_by,order.cancelled_at=actor.username,now_utc()
    db.add(Audit(username=actor.username,action='CANCELAR_PEDIDO',details=f'Pedido {order.id}; {reason}; reservas libertadas.'))
    return order.id


@operation
def archive_legacy_document(db,actor,order_id):
    from .database import DispatchDocument
    from .preparation_pdf import render_pdf
    order=db.get(DispatchOrder,order_id)
    if not order or order.status!='confirmed':raise DispatchError('Só emitir documento de saída confirmada.')
    if db.get(DispatchDocument,order_id):return order_id
    by_line={line.id:line for line in lines(db,order_id)}
    rows=[dict(artigo=by_line[p.line_id].artigo,lote=by_line[p.line_id].lote,carga=p.carga,local=p.local,quantidade=p.quantidade) for p in picks(db,order_id)]
    if not rows:raise DispatchError('Sem seleção histórica comprovada para emitir documento.')
    from .contexts import label
    order.ddn_label=label(db)
    content=render_pdf(order,rows)
    db.add(DispatchDocument(order_id=order_id,pdf=content,sha256=hashlib.sha256(content).hexdigest(),stock_versions='{}'))
    db.add(Audit(username=actor.username,action='DOCUMENTO_LEGADO',details=f'Pedido {order_id}; seleção histórica, sem recalcular ou descontar stock.'))
    return order_id
