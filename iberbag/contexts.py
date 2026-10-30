"""Global administration and explicit stock-owner/warehouse/area boundaries."""
from sqlalchemy import select, text
from .database import Client, Warehouse, StorageContext, Location, Audit, User, context_id

WAREHOUSES = ((1, 'Ovar'), (2, 'Escapães'))
CLIENTS = ((1, 'Iberbag', 'grouped', 'palete', True, 1),
           (2, 'Valmet', 'individual', None, False, 1),
           (3, 'Cosmopak', 'grouped_with_composition', 'palete', False, 1),
           (4, 'NKT', 'peg', 'palete', False, 2),
           (5, 'Alcobre', 'individual', 'bobine', False, 1))


def seed_contexts(db):
    """Only a NEW database may be seeded; migration seeds existing databases."""
    if db.get(StorageContext, 1):
        return
    for ident, name in WAREHOUSES:
        db.add(Warehouse(id=ident, name=name, active=True))
    for ident, name, tracking, unit, enabled, wh in CLIENTS:
        db.add(Client(id=ident, name=name, tracking=tracking, unit=unit, pallet_operations=enabled, active=True))
    db.flush()
    for ident, name, tracking, unit, enabled, wh in CLIENTS:
        db.add(StorageContext(id=ident, client_id=ident, warehouse_id=wh, area=name, active=True))
    db.flush()


def available(db):
    return db.execute(select(StorageContext, Client, Warehouse).join(Client).join(Warehouse).where(
        StorageContext.active.is_(True), Client.active.is_(True), Warehouse.active.is_(True))
        .order_by(Warehouse.name, Client.name)).all()


def require_context(db, ident=None, operational=False):
    ident = context_id(db) if ident is None else ident
    row = db.execute(select(StorageContext, Client, Warehouse).join(Client).join(Warehouse).where(
        StorageContext.id == ident, StorageContext.active.is_(True),
        Client.active.is_(True), Warehouse.active.is_(True))).first()
    if row is None:
        raise ValueError('Contexto cliente/armazém inexistente ou inativo.')
    if operational and not row[1].pallet_operations:
        raise ValueError('Este cliente tem apenas estrutura configurada. O seu processo operacional exige uma fase posterior.')
    return row


def label(db):
    ctx, client, warehouse = require_context(db)
    return f'{client.name} · {warehouse.name} · Área {ctx.area}'


def name(value):
    value = (value or '').strip()
    if not 1 <= len(value) <= 120 or any(ord(c) < 32 for c in value):
        raise ValueError('Nome/área: indicar 1–120 caracteres válidos.')
    return value


def create_client(db, actor_id, client_name, warehouse_id, area, pallet_operations=False):
    """No client-specific rules. Enable only the existing grouped-pallet process."""
    try:
        if db.bind.dialect.name == 'postgresql':
            db.execute(text('SELECT pg_advisory_xact_lock(72834001)'))
        actor = db.get(User, actor_id)
        if not actor or not actor.active or actor.role != 'admin':
            raise PermissionError('Apenas Administrador pode configurar clientes.')
        warehouse = db.get(Warehouse, warehouse_id)
        if not warehouse or not warehouse.active:
            raise ValueError('Armazém inexistente ou inativo.')
        client_name, area = name(client_name), name(area)
        client = Client(name=client_name, tracking='grouped' if pallet_operations else None,
                        unit='palete' if pallet_operations else None, pallet_operations=pallet_operations, active=True)
        db.add(client); db.flush()
        ctx = StorageContext(client_id=client.id, warehouse_id=warehouse.id, area=area, active=True)
        db.add(ctx); db.flush()
        db.execute(Location.__table__.insert().values(context_id=ctx.id,codigo='Chão',capacidade=None,active=True))
        db.add(Audit(username=actor.username, action='CRIAR_CLIENTE',
                     details=f'Cliente {client.id}; contexto {ctx.id}; armazém {warehouse.id}; área {area}.'))
        db.commit()
        return ctx.id
    except Exception:
        db.rollback()
        raise
