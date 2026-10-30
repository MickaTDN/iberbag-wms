"""Conservative exclusive ownership of physical codes, independent of area labels.

This is a safety registry, NOT support for shared positions. Claims are retained
when a catalogue row or stock group is removed. No cross-client reassignment.
"""
from sqlalchemy import select

EXEMPT = {'', 'chão', 'chao', '(não indicado)'}


def physical_code(value):
    code = (value or '').strip(' ').lower()
    return None if code in EXEMPT else code


def expected_claims(data):
    """Virtual metadata for legacy reconciliation; never writes the source."""
    warehouses = {r['id']: r['warehouse_id'] for r in data['storage_contexts']}
    claimed = {}
    for table, field in (('locations', 'codigo'), ('stock', 'local')):
        for row in data[table]:
            code = physical_code(row[field])
            if code is None:
                continue
            ctx = row.get('context_id', 1)
            key = (warehouses[ctx], code)
            if key in claimed and claimed[key] != ctx:
                raise ValueError('Código físico atribuído a contextos diferentes no mesmo armazém. Partilha bloqueada; reconciliar antes de atualizar.')
            claimed[key] = ctx
    return [dict(warehouse_id=wh, code=code, context_id=ctx)
            for (wh, code), ctx in sorted(claimed.items())]


def ensure_exclusive(db, codes):
    """Read the global registry through Connection, deliberately outside ORM scope."""
    from .database import PhysicalLocationClaim, StorageContext, context_id
    ctx = context_id(db)
    wanted = {key for value in codes if (key := physical_code(value)) is not None}
    if not wanted:
        return
    wh = db.connection().scalar(select(StorageContext.warehouse_id).where(StorageContext.id == ctx))
    if wh is None:
        raise ValueError('Contexto inexistente.')
    conflict = db.connection().scalar(select(PhysicalLocationClaim.code).where(
        PhysicalLocationClaim.warehouse_id == wh, PhysicalLocationClaim.code.in_(wanted),
        PhysicalLocationClaim.context_id != ctx).limit(1))
    if conflict:
        raise ValueError('Código físico já atribuído a outro contexto neste armazém. Partilha entre clientes ainda não suportada.')


def claim_sqlite(db, record):
    """SQLite consultation fixtures only; operational transactions remain forbidden."""
    from .database import PhysicalLocationClaim, StorageContext, Location
    code = physical_code(record.codigo if isinstance(record, Location) else record.local)
    if code is None:
        return
    ensure_exclusive(db, [code])
    ctx = record.context_id
    wh = db.connection().scalar(select(StorageContext.warehouse_id).where(StorageContext.id == ctx))
    table = PhysicalLocationClaim.__table__
    if not db.connection().scalar(select(table.c.context_id).where(table.c.warehouse_id == wh, table.c.code == code)):
        db.connection().execute(table.insert().values(warehouse_id=wh, code=code, context_id=ctx))
