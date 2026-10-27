"""Transactional reception and putaway. Public commands own commit/rollback."""
from functools import wraps
from datetime import date, datetime
import hashlib
import json
import re
from sqlalchemy import select, func, text, delete
from sqlalchemy.exc import IntegrityError, DataError
from .database import (User, Receipt, ReceiptLine, Putaway, Stock, Carga, Location, Audit, now_utc)
from .location_rules import is_floor
from .warehouse_settings import validate_location, WarehouseError

LOCK = 72834001  # Same lock as location edits: capacity validation cannot race catalogue changes.
MAX_INT = 2147483647


class ReceivingError(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def operation(fn):
    @wraps(fn)
    def wrapped(db, actor_id, *args, **kwargs):
        try:
            if db.bind.dialect.name != 'postgresql':
                raise ReceivingError('Operações de stock exigem PostgreSQL. SQLite permite apenas consulta.', 503)
            db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': LOCK})
            db.expire_all()
            actor = db.get(User, actor_id)
            if not actor or not actor.active or actor.role not in ('admin', 'operador'):
                raise ReceivingError('Operação não autorizada.', 403)
            result = fn(db, actor, *args, **kwargs)
            db.commit()
            return result
        except (IntegrityError, DataError) as error:
            db.rollback()
            raise ReceivingError('Conflito nos dados. Atualiza a página e revê a operação.') from error
        except Exception:
            db.rollback()
            raise
    return wrapped


def required(value, label):
    value = (value or '').strip()
    if not value or len(value) > 120 or any(ord(ch) < 32 for ch in value):
        raise ReceivingError(f'{label}: indicar 1–120 caracteres válidos.', 400)
    return value


def required_note(value,label='Motivo'):
    value=(value or '').strip()
    if not value or len(value)>2000:
        raise ReceivingError(label+': indicar 1–2000 caracteres.',400)
    return value


def quantity(value):
    if not re.fullmatch(r'[0-9]{1,10}', str(value)) or not 1 <= int(value) <= MAX_INT:
        raise ReceivingError('Indica uma quantidade inteira positiva de paletes.', 400)
    return int(value)


def request_key(value):
    if not re.fullmatch(r'[a-f0-9]{32}', value or ''):
        raise ReceivingError('Identificador de pedido inválido. Abre um novo formulário.', 400)
    return value


def receipt_payload(code, received_on, rows):
    code = required(code, 'Carga')
    try:
        received = datetime.combine(date.fromisoformat(received_on), datetime.min.time())
    except (ValueError, TypeError):
        raise ReceivingError('Indica uma data de entrada válida.', 400)
    if not 1 <= len(rows) <= 20:
        raise ReceivingError('Cada carga permite 1–20 grupos de artigo/lote por registo.', 400)
    lines = [(required(a, 'Artigo'), required(l, 'Lote'), quantity(q)) for a, l, q in rows]
    if len({(a, l) for a, l, q in lines}) != len(lines):
        raise ReceivingError('Agrupa cada artigo/lote numa única linha.', 400)
    digest = hashlib.sha256(json.dumps([code, received.isoformat(), lines], ensure_ascii=False).encode()).hexdigest()
    return code, received, lines, digest


def owned(record, actor):
    if record is None:
        raise ReceivingError('Registo não encontrado.', 404)
    if record.created_by != actor.id:
        raise ReceivingError('Só o autor pode editar ou confirmar este rascunho.', 403)


@operation
def save_receipt(db, actor, key, code, received_on, rows, receipt_id=None, version=None):
    key = request_key(key)
    code, received, lines, digest = receipt_payload(code, received_on, rows)
    from .lots import assert_lot
    seen = {}
    for artigo, lote, qty in lines:
        assert_lot(db, lote, artigo)
        if lote in seen and seen[lote] != artigo:
            raise ReceivingError('Um lote não pode pertencer a artigos diferentes.')
        seen[lote] = artigo
    if receipt_id is None:
        record = db.scalar(select(Receipt).where(Receipt.request_key == key))
        if record:
            owned(record, actor)
            if record.payload_hash != digest:
                raise ReceivingError('Este pedido já foi usado com outros dados. Abre um novo formulário.')
            return record.id
        record = Receipt(request_key=key, payload_hash=digest, code=code, received_on=received,
                         created_by=actor.id, version=1)
        db.add(record)
        db.flush()
    else:
        record = db.get(Receipt, receipt_id)
        owned(record, actor)
        if record.status != 'draft' or record.version != version or record.request_key != key:
            raise ReceivingError('O rascunho mudou ou já foi confirmado. Atualiza a página.')
        db.execute(delete(ReceiptLine).where(ReceiptLine.receipt_id == record.id))
        record.code, record.received_on, record.payload_hash = code, received, digest
        record.version += 1
    for artigo, lote, qty in lines:
        db.add(ReceiptLine(receipt_id=record.id, artigo=artigo, lote=lote, quantidade=qty))
    return record.id


@operation
def confirm_receipt(db, actor, receipt_id, version):
    record = db.scalar(select(Receipt).where(Receipt.id == receipt_id).with_for_update())
    owned(record, actor)
    if record.version != version:
        raise ReceivingError('A revisão apresentada está desatualizada. Revê novamente os dados.')
    if record.status == 'confirmed':
        return record.id  # Retry of the same confirmation has no side effects.
    if db.scalar(select(Carga.id).where(Carga.codigo == record.code)) is not None:
        raise ReceivingError('Já existe uma carga com este código. A entrada não foi duplicada.')
    if not db.scalar(select(ReceiptLine.id).where(ReceiptLine.receipt_id == record.id)):
        raise ReceivingError('A carga não contém artigos.')
    # A legacy stock code without a Carga row is also occupied; never attach new history to it.
    if db.scalar(select(Stock.id).where(Stock.carga == record.code)) is not None:
        raise ReceivingError('Há stock antigo com este código de carga. Requer reconciliação prévia.')
    from .lots import assert_lot
    for line in db.scalars(select(ReceiptLine).where(ReceiptLine.receipt_id == record.id)):
        assert_lot(db, line.lote, line.artigo)
    carga = Carga(codigo=record.code, entrada=record.received_on, funcionario=None)
    db.add(carga)
    db.flush()
    record.carga_id, record.receiver = carga.id, actor.username
    record.status, record.confirmed_at = 'confirmed', now_utc()
    db.flush()
    from .commercial import allocate_incoming
    allocate_incoming(db,actor,record)
    db.add(Audit(username=actor.username, action='CONFIRMAR_ENTRADA', details=f'Entrada {record.id}; carga {record.code}.'))
    return record.id


def remaining(db, line):
    placed = db.scalar(select(func.coalesce(func.sum(Putaway.quantidade), 0)).where(
        Putaway.line_id == line.id, Putaway.status == 'confirmed'))
    from .commercial import allocated
    return line.quantidade - placed - allocated(db,line.id)


def canonical_location(db, value):
    value = required(value, 'Localização')
    if is_floor(value):
        floors = [code for code in db.scalars(select(Location.codigo).order_by(Location.id)).all() if is_floor(code)]
        return value if value in floors else ('Chão' if 'Chão' in floors else (floors[0] if floors else 'Chão'))
    return value.upper()


def destination(db, value, qty):
    value = canonical_location(db, value)
    if not is_floor(value):
        value = value.upper()
        try:
            validate_location(db, value)
        except WarehouseError as error:
            raise ReceivingError(str(error), 400) from error
    location = db.scalar(select(Location).where(Location.codigo == value).with_for_update())
    if location is None or not location.active:
        raise ReceivingError('A posição não existe ou está inativa no catálogo de localizações.')
    # Do not trust stale/custom capacity values: physical positions always hold one pallet.
    used = db.scalar(select(func.coalesce(func.sum(Stock.quantidade), 0)).where(Stock.local == value))
    if not is_floor(value) and used + qty > 1:
        raise ReceivingError('Posição ocupada ou quantidade superior à capacidade de uma palete.')
    return value


def confirmed_line(db, line_id):
    line = db.scalar(select(ReceiptLine).where(ReceiptLine.id == line_id).with_for_update())
    if line is None:
        raise ReceivingError('Linha não encontrada.', 404)
    receipt = db.get(Receipt, line.receipt_id)
    if receipt.status != 'confirmed':
        raise ReceivingError('A receção ainda não foi confirmada.')
    from .lots import assert_lot
    assert_lot(db, line.lote, line.artigo)
    return line, receipt


@operation
def review_putaway(db, actor, key, line_id, local, quantidade):
    key, qty = request_key(key), quantity(quantidade)
    previous = db.scalar(select(Putaway).where(Putaway.request_key == key))
    if previous:
        owned(previous, actor)
        canonical = canonical_location(db, local)
        if (previous.line_id, previous.local, previous.quantidade) != (line_id, canonical, qty):
            raise ReceivingError('Pedido já utilizado com outros dados.')
        return previous.id
    line, receipt = confirmed_line(db, line_id)
    if qty > remaining(db, line):
        raise ReceivingError('Quantidade superior às paletes que aguardam arrumação.')
    local = destination(db, local, qty)
    record = Putaway(request_key=key, line_id=line.id, local=local, quantidade=qty, created_by=actor.id)
    db.add(record)
    db.flush()
    return record.id


@operation
def confirm_putaway(db, actor, putaway_id):
    record = db.scalar(select(Putaway).where(Putaway.id == putaway_id).with_for_update())
    owned(record, actor)
    if record.status == 'confirmed':
        return record.id
    line, receipt = confirmed_line(db, record.line_id)
    if record.quantidade > remaining(db, line):
        raise ReceivingError('As paletes já foram arrumadas por outra operação. Atualiza a lista.')
    destination(db, record.local, record.quantidade)
    stock = db.scalar(select(Stock).where(Stock.artigo == line.artigo, Stock.lote == line.lote,
        Stock.carga == receipt.code, Stock.local == record.local).with_for_update())
    if stock:
        if stock.quantidade + record.quantidade > MAX_INT:
            raise ReceivingError('Quantidade excede o limite técnico do grupo de stock.')
        stock.quantidade += record.quantidade
    else:
        stock = Stock(artigo=line.artigo, lote=line.lote, carga=receipt.code,
                      local=record.local, quantidade=record.quantidade)
        db.add(stock)
    db.flush()
    record.stock_id, record.arranger = stock.id, actor.username
    record.status, record.confirmed_at = 'confirmed', now_utc()
    db.add(Audit(username=actor.username, action='CONFIRMAR_ARRUMACAO',
        details=f'Arrumação {record.id}; entrada {receipt.id}; linha {line.id}; {record.quantidade} paletes em {record.local}; stock {stock.id}.'))
    return record.id
