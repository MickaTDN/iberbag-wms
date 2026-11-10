"""Alcobre individual bobines; receipt quantities never become invented identities."""
from datetime import date
from functools import wraps
from hashlib import sha256
import json
from sqlalchemy import select, func, text, inspect
from sqlalchemy.exc import DBAPIError
from . import database as m
from .contexts import require_context
from .receiving import ReceivingError, request_key

def dump(value): return json.dumps(value, ensure_ascii=False, sort_keys=True)
def digest(value): return sha256(value.encode()).hexdigest()

def enabled(db):
    ctx, owner, wh = require_context(db)
    return enabled_if_present(db, owner.id, wh.id)

def enabled_if_present(db, owner, warehouse=None):
    if not inspect(db.connection()).has_table('alcobre_owners'): return False
    row = db.get(m.AlcobreOwner, owner)
    return row is not None and (warehouse is None or row.warehouse_id == warehouse)

def command(fn):
    @wraps(fn)
    def run(db, actor_id, *args, **kwargs):
        try:
            if db.bind.dialect.name != 'postgresql': raise ReceivingError('Operações Alcobre exigem PostgreSQL.', 503)
            m.require_write_isolation(db)
            db.execute(text('SELECT pg_advisory_xact_lock(72834001)')); db.expire_all()
            actor = db.get(m.User, actor_id)
            if not actor or not actor.active or actor.role not in ('admin', 'operador') or not enabled(db):
                raise ReceivingError('Operação Alcobre não autorizada neste contexto.', 403)
            result = fn(db, actor, *args, **kwargs); db.commit(); return result
        except DBAPIError as error:
            db.rollback(); raise ReceivingError('Conflito de bobine, posição ou revisão; operação integralmente revertida.') from error
        except Exception: db.rollback(); raise
    return run

def value(raw, label, maximum=120, optional=False):
    if optional and raw in (None, ''): return None
    if not isinstance(raw, str) or not 1 <= len(raw) <= maximum or raw != raw.strip() or any(ord(c) < 32 for c in raw):
        raise ReceivingError(label + ': texto válido, sem espaços exteriores nem caracteres de controlo.', 400)
    return raw

def permission(actor, entry=None, admin=False):
    if (admin and actor.role != 'admin') or (entry and actor.role != 'admin' and entry.created_by != actor.id):
        raise ReceivingError('Só o autor ou Administrador pode alterar esta receção.', 403)

def entry_for(db, actor, ident):
    entry = db.get(m.AlcobreEntry, ident)
    if not entry: raise ReceivingError('Receção inexistente neste contexto.', 404)
    permission(actor, entry); return entry

def audit(db, actor, action, details):
    db.add(m.Audit(username=actor.username, action='ALCOBRE_' + action, details=dump(details)))

def identified(db, ident):
    return db.scalar(select(func.count()).select_from(m.AlcobreBobine).where(m.AlcobreBobine.entry_id == ident))

def rows_value(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 300: raise ReceivingError('Indicar 1–300 bobines comprovadas.', 400)
    seen = set(); clean = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'number', 'local'}: raise ReceivingError('Indicar apenas número de bobine e localização.', 400)
        number = value(row['number'], 'Número da bobine')
        if number.casefold() in seen: raise ReceivingError('Bobine repetida na lista, incluindo variantes de maiúsculas/minúsculas.', 400)
        seen.add(number.casefold()); clean.append(dict(number=number, local=value(row['local'], 'Localização', optional=True)))
    return clean

def validate_rows(db, entry, kind, rows):
    ctx, owner, wh = require_context(db)
    issued = {n.casefold() for n in db.connection().scalars(select(m.AlcobreBobine.__table__.c.number).where(m.AlcobreBobine.__table__.c.client_id == owner.id))}
    for row in rows:
        if kind == 'place':
            unit = db.scalar(select(m.AlcobreBobine).where(m.AlcobreBobine.entry_id == entry.id, m.AlcobreBobine.number_key == row['number'].casefold()))
            if not unit or db.scalar(select(m.AlcobrePlacement.id).where(m.AlcobrePlacement.bobine_id == unit.id)):
                raise ReceivingError('Bobine não pertence à entrada ou já está arrumada; não há transferências nesta fase.')
            row['number'] = unit.number
            if not row['local']: raise ReceivingError('Indicar posição comprovada para arrumar.', 400)
        elif row['number'].casefold() in issued: raise ReceivingError('Número de bobine já registado para este proprietário.')
    locations = [r['local'] for r in rows if r['local']]
    if len(set(locations)) != len(locations): raise ReceivingError('Uma posição só pode receber uma bobine.')
    from .physical_locations import ensure_exclusive
    from .warehouse_settings import occupied
    try: ensure_exclusive(db, locations)
    except ValueError as error: raise ReceivingError(str(error)) from error
    for code in locations:
        slot = db.scalar(select(m.AlcobreSlot).join(m.AlcobreZone, m.AlcobreZone.id == m.AlcobreSlot.zone_id)
                         .join(m.Location, m.Location.codigo == m.AlcobreSlot.local)
                         .where(m.AlcobreSlot.local == code, m.AlcobreZone.active.is_(True), m.Location.active.is_(True), m.Location.capacidade == 1))
        if not slot: raise ReceivingError('Posição Alcobre não configurada/ativa; associar código físico em Definições.')
        if sum(q for _, q in occupied(db, [code])): raise ReceivingError('Posição já ocupada: ' + code + '.')
    return rows

@command
def create(db, actor, key, mode, reference=None, received_on=None, *, source=None, source_name=None, extraction=None):
    key = request_key(key)
    if mode not in ('direct', 'pending'): raise ReceivingError('Tipo de receção inválido.', 400)
    reference = value(reference, 'Guia/origem', 200, optional=True)
    if received_on:
        try:
            if date.fromisoformat(received_on).isoformat() != received_on: raise ValueError()
        except (ValueError, TypeError): raise ReceivingError('Data efetiva inválida; não será inventada.', 400)
    source_sha = sha256(source).hexdigest() if source else None
    if source:
        permission(actor, admin=True)
        if mode != 'direct' or len(source) > 5*1024*1024 or not source.startswith(b'%PDF-'): raise ReceivingError('Guia PDF inválida.', 400)
        source_name = value(source_name, 'Nome do ficheiro', 240)
        if '/' in source_name or '\\' in source_name: raise ReceivingError('Nome do ficheiro inválido.', 400)
    old = db.scalar(select(m.AlcobreEntry).where(m.AlcobreEntry.request_key == key))
    if old:
        permission(actor, old)
        if (old.mode, old.reference, old.received_on, old.source_digest) != (mode, reference, received_on or None, source_sha): raise ReceivingError('Chave repetida com dados diferentes.')
        return old.id
    if source_sha:
        old = db.scalar(select(m.AlcobreEntry).where(m.AlcobreEntry.source_digest == source_sha))
        if old: permission(actor, old); return old.id
    entry = m.AlcobreEntry(request_key=key, mode=mode, reference=reference, received_on=received_on or None,
                          source_name=source_name, source_pdf=source, source_digest=source_sha,
                          extraction=dump(extraction or {}), created_by=actor.id)
    db.add(entry); db.flush(); audit(db, actor, 'RASCUNHO', dict(entry=entry.id, mode=mode, source_digest=source_sha)); return entry.id

def preview_file(db, actor, key, content, filename, reauthorize=None):
    # Parsing is bounded in a separate process, outside every writing transaction.
    from .alcobre_pdf import analyze
    user = db.get(m.User, actor)
    if not user or not user.active or user.role != 'admin' or not enabled(db): raise ReceivingError('Importar PDF exige Administrador.', 403)
    db.rollback()
    report = analyze(content)
    if reauthorize: reauthorize()
    return create(db, actor, key, 'direct', source=content, source_name=filename, extraction=report)

@command
def review(db, actor, ident, version, key, kind, rows, quantity, reason, manual=False, reference=None, received_on=None):
    entry = entry_for(db, actor, ident); key = request_key(key); reason = value(reason, 'Prova/observação', 2000)
    if kind not in ('receive', 'identify', 'place'): raise ReceivingError('Operação inválida.', 400)
    if kind == 'receive':
        if entry.status != 'draft' or entry.version != version: raise ReceivingError('Rascunho/revisão desatualizados.')
        if entry.mode == 'pending':
            if rows: raise ReceivingError('Material sem identificação não tem números de bobine inventados.', 400)
            if isinstance(quantity, bool) or not str(quantity).isdigit() or not 1 <= int(quantity) <= 300: raise ReceivingError('Indicar 1–300 bobines fisicamente comprovadas.', 400)
            rows = []; quantity = int(quantity)
        else:
            rows = validate_rows(db, entry, kind, rows_value(rows)); quantity = len(rows)
            if entry.source_pdf and not manual:
                source = json.loads(entry.extraction)
                if not source.get('reliable') or [r['number'] for r in rows] != [r['number'] for r in source.get('candidates', [])]:
                    raise ReceivingError('Extração não fiável ou corrigida: confirmar transcrição manual comprovada; não confirmar automaticamente.')
    else:
        if entry.status != 'confirmed': raise ReceivingError('Confirmar a receção original primeiro.')
        if kind == 'identify' and entry.mode != 'pending': raise ReceivingError('Entrada direta já tem identidade comprovada.')
        rows = validate_rows(db, entry, kind, rows_value(rows)); quantity = entry.quantity
        if kind == 'identify' and len(rows) > quantity - identified(db, ident): raise ReceivingError('Identificação excede as bobines pendentes; não criar nova entrada.')
    reference = entry.reference if reference is None else value(reference, 'Guia/origem', 200, optional=True)
    received_on = entry.received_on if received_on is None else (received_on or None)
    if received_on:
        try:
            if date.fromisoformat(received_on).isoformat() != received_on: raise ValueError()
        except (ValueError, TypeError): raise ReceivingError('Data efetiva inválida.', 400)
    if kind != 'receive' and (reference, received_on) != (entry.reference, entry.received_on): raise ReceivingError('Metadados da entrada confirmada são imutáveis.')
    raw = dict(kind=kind, rows=rows, quantity=quantity, identified_before=identified(db, ident), manual=bool(manual), reference=reference, received_on=received_on)
    encoded = dump(raw)
    old = db.scalar(select(m.AlcobreReview).where(m.AlcobreReview.request_key == key))
    if old:
        if old.entry_id != ident or old.payload != encoded or old.reason != reason: raise ReceivingError('Chave de revisão repetida com outra decisão.')
        return old.id
    if kind == 'receive': entry.version += 1
    revision = m.AlcobreReview(entry_id=ident, request_key=key, kind=kind, version=entry.version,
                              payload=encoded, digest=digest(encoded), reason=reason, actor=actor.username)
    db.add(revision); db.flush(); audit(db, actor, 'REVER', dict(entry=ident, review=revision.id, **raw, reason=reason)); return revision.id

@command
def confirm(db, actor, revision_id, expected_digest, key, accepted):
    if not accepted: raise ReceivingError('Aceitar expressamente a revisão e a prova física.', 400)
    rev = db.get(m.AlcobreReview, revision_id)
    if not rev or rev.digest != expected_digest or digest(rev.payload) != expected_digest: raise ReceivingError('Revisão/digest inválido.')
    entry = entry_for(db, actor, rev.entry_id); key = request_key(key)
    old = db.scalar(select(m.AlcobreAction).where(m.AlcobreAction.request_key == key))
    if old:
        if old.review_id != revision_id: raise ReceivingError('Chave repetida com outra operação.')
        return old.id
    old = db.scalar(select(m.AlcobreAction).where(m.AlcobreAction.review_id == revision_id))
    if old: return old.id
    raw = json.loads(rev.payload)
    if rev.kind == 'receive':
        if entry.status != 'draft' or entry.version != rev.version: raise ReceivingError('Receção já confirmada ou revisão desatualizada.')
    elif entry.status != 'confirmed' or (rev.kind == 'identify' and identified(db, entry.id) != raw['identified_before']):
        raise ReceivingError('Stock mudou desde a revisão; rever novamente.')
    rows = validate_rows(db, entry, rev.kind, raw['rows'])
    operation = m.AlcobreAction(entry_id=entry.id, review_id=rev.id, request_key=key, actor=actor.username)
    db.add(operation); db.flush(); owner = require_context(db)[1]
    for row in rows:
        if rev.kind != 'place':
            unit = m.AlcobreBobine(entry_id=entry.id, action_id=operation.id, client_id=owner.id, number=row['number'], number_key=row['number'].casefold())
            db.add(unit); db.flush()
        else: unit = db.scalar(select(m.AlcobreBobine).where(m.AlcobreBobine.entry_id == entry.id, m.AlcobreBobine.number_key == row['number'].casefold()))
        if row['local']: db.add(m.AlcobrePlacement(bobine_id=unit.id, action_id=operation.id, local=row['local'])); db.flush()
    if rev.kind == 'receive':
        entry.reference=raw['reference'];entry.received_on=raw['received_on']
        entry.quantity = raw['quantity']; entry.status = 'confirmed'; entry.confirmed_by = actor.username; entry.confirmed_at = m.now_utc(); db.flush()
    audit(db, actor, 'CONFIRMAR_' + rev.kind.upper(), dict(entry=entry.id, operation=operation.id, review=rev.id, digest=expected_digest, reason=rev.reason, **raw)); return operation.id

@command
def configure_zone(db, actor, code, positions, active=True, ident=None):
    permission(actor, admin=True); code = value(code, 'Zona', 20)
    if not code.isalnum() or code != code.upper(): raise ReceivingError('Zona: letras/números em maiúsculas.', 400)
    if isinstance(positions, bool) or not str(positions).isdigit() or not 1 <= int(positions) <= 500: raise ReceivingError('Posições: 1–500.', 400)
    zone = db.get(m.AlcobreZone, ident) if ident else None
    if ident and not zone: raise ReceivingError('Zona inexistente.', 404)
    if zone:
        slots = db.scalars(select(m.AlcobreSlot).where(m.AlcobreSlot.zone_id == ident)).all()
        if any(s.position > int(positions) for s in slots): raise ReceivingError('Existem posições físicas associadas fora do novo limite.')
        if not active and any(db.scalar(select(m.AlcobrePlacement.id).where(m.AlcobrePlacement.local == s.local)) for s in slots): raise ReceivingError('Zona ocupada não pode ser desativada.')
        zone.code = code; zone.positions = int(positions); zone.active = bool(active)
    else: zone = m.AlcobreZone(code=code, positions=int(positions), active=bool(active)); db.add(zone)
    db.flush(); audit(db, actor, 'ZONA', dict(zone=zone.id, code=code, positions=int(positions), active=bool(active))); return zone.id

@command
def configure_slot(db, actor, zone_id, position, code):
    permission(actor, admin=True); code = value(code, 'Código físico comprovado').upper()
    from .location_rules import is_floor, is_unassigned
    if is_floor(code) or is_unassigned(code): raise ReceivingError('Uma posição Alcobre não é Chão nem localização desconhecida.', 400)
    zone = db.get(m.AlcobreZone, zone_id)
    if not zone or not zone.active or isinstance(position, bool) or not str(position).isdigit() or not 1 <= int(position) <= zone.positions: raise ReceivingError('Zona/posição inválida.', 400)
    old = db.scalar(select(m.AlcobreSlot).where(m.AlcobreSlot.zone_id == zone_id, m.AlcobreSlot.position == int(position)))
    if old and old.local == code:return old.id
    if old and db.scalar(select(m.AlcobrePlacement.id).where(m.AlcobrePlacement.local==old.local)):raise ReceivingError('Posição ocupada não pode mudar de código físico.')
    from .physical_locations import ensure_exclusive
    try: ensure_exclusive(db, [code])
    except ValueError as error: raise ReceivingError(str(error)) from error
    loc = db.scalar(select(m.Location).where(m.Location.codigo == code))
    if loc and (loc.capacidade != 1 or not loc.active): raise ReceivingError('A posição exige capacidade de uma bobine e localização ativa.')
    if not loc: db.add(m.Location(codigo=code, capacidade=1, active=True)); db.flush()
    slot = old or m.AlcobreSlot(zone_id=zone_id, position=int(position), local=code)
    previous=old.local if old else None;slot.local=code;db.add(slot);db.flush()
    audit(db, actor, 'POSICAO', dict(zone=zone_id, position=int(position), previous=previous, local=code)); return slot.id

def stock(db, number='', local='', state='all', page=1):
    if not enabled(db): raise ReceivingError('Consulta Alcobre não autorizada.', 403)
    if state not in ('all', 'available', 'unlocated', 'pending'): raise ReceivingError('Estado inválido.', 400)
    query = select(m.AlcobreBobine, m.AlcobrePlacement, m.AlcobreEntry).join(m.AlcobreEntry, m.AlcobreEntry.id == m.AlcobreBobine.entry_id).outerjoin(m.AlcobrePlacement, m.AlcobrePlacement.bobine_id == m.AlcobreBobine.id)
    if number: query = query.where(m.AlcobreBobine.number.icontains(number, autoescape=True))
    if local: query = query.where(m.AlcobrePlacement.local.icontains(local, autoescape=True))
    if state == 'available': query = query.where(m.AlcobrePlacement.id.is_not(None))
    if state == 'unlocated': query = query.where(m.AlcobrePlacement.id.is_(None))
    if state == 'pending': query = query.where(m.AlcobreBobine.id < 0)
    count = db.scalar(select(func.count()).select_from(query.subquery()))
    units = db.execute(query.order_by(m.AlcobreBobine.id).offset((page-1)*100).limit(100)).all()
    pending = []
    # Unknown numbers/locations cannot match a number/location search.
    if state in ('all', 'pending') and not number and not local:
        for entry in db.scalars(select(m.AlcobreEntry).where(m.AlcobreEntry.status == 'confirmed', m.AlcobreEntry.mode == 'pending').order_by(m.AlcobreEntry.id)):
            remaining = entry.quantity - identified(db, entry.id)
            if remaining: pending.append((entry, remaining))
    return units, count, pending
