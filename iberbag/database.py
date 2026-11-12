"""Database models and transactions. SQLite for local tests, PostgreSQL for shared LAN."""
import os
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import func, select, text, create_engine, String, Integer, DateTime, Boolean, BigInteger, LargeBinary, ForeignKey, CheckConstraint, UniqueConstraint, Index, Text, ForeignKeyConstraint, event, inspect as sa_inspect, PrimaryKeyConstraint, Numeric
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, relationship, Session, with_loader_criteria

ROOT = Path(__file__).resolve().parent.parent
DATABASE_URL = os.getenv('WMS_DATABASE_URL', f'sqlite:///{ROOT / "iberbag-dev.db"}')
if DATABASE_URL.startswith('postgresql'):
    from .pg_environment import configure_process
    configure_process(DATABASE_URL, allow_url_options=True)
params = {'connect_args': {'check_same_thread': False, 'timeout': 30}} if DATABASE_URL.startswith('sqlite') else {'pool_pre_ping': True, 'pool_size': 10, 'max_overflow': 10}
engine = create_engine(DATABASE_URL, future=True, **params)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def require_write_isolation(db):
    # A snapshot taken before waiting for an advisory lock must not keep old
    # permissions/stock visible after the lock. Never lower isolation silently.
    if db.bind.dialect.name == 'postgresql' and db.connection().get_isolation_level() != 'READ COMMITTED':
        raise ValueError('Escritas WMS exigem READ COMMITTED; rever a configuração transacional.')

class Base(DeclarativeBase):
    pass


class Warehouse(Base):
    __tablename__ = 'warehouses'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

class Client(Base):
    """Stock owner; distinct from the commercial consignee on an order."""
    __tablename__ = 'clients'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    tracking: Mapped[str | None] = mapped_column(String(40))
    unit: Mapped[str | None] = mapped_column(String(40))
    pallet_operations: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

class StorageContext(Base):
    __tablename__ = 'storage_contexts'
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey('clients.id'), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey('warehouses.id'), nullable=False)
    area: Mapped[str] = mapped_column(String(120), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (UniqueConstraint('warehouse_id', 'area', name='warehouse_area_unique'),
                     UniqueConstraint('client_id', 'warehouse_id', name='client_warehouse_unique'))

class PhysicalLocationClaim(Base):
    """One physical code per warehouse, never reassigned by deleting a location."""
    __tablename__ = 'physical_location_claims'
    warehouse_id: Mapped[int] = mapped_column(ForeignKey('warehouses.id'), primary_key=True)
    code: Mapped[str] = mapped_column(String(120), primary_key=True)
    context_id: Mapped[int] = mapped_column(ForeignKey('storage_contexts.id'), nullable=False)

class Scoped:
    context_id: Mapped[int] = mapped_column(ForeignKey('storage_contexts.id'), nullable=False,
                                           default=1, server_default='1', index=True)

class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_salt: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)
    __table_args__ = (CheckConstraint("role IN ('admin','operador')", name='roles_valid'),)

class LoginSession(Base):
    __tablename__ = 'sessions'
    id: Mapped[int] = mapped_column(primary_key=True)
    token_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    csrf_token: Mapped[str] = mapped_column(String(100), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    user: Mapped[User] = relationship()

class FailedLogin(Base):
    __tablename__ = 'failed_logins'
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    remote_ip: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)

class Stock(Scoped, Base):
    stacked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'), nullable=False)
    __tablename__ = 'stock'
    id: Mapped[int] = mapped_column(primary_key=True)
    artigo: Mapped[str] = mapped_column(String(120), nullable=False)
    lote: Mapped[str] = mapped_column(String(120), nullable=False)
    carga: Mapped[str] = mapped_column(String(120), nullable=False)
    local: Mapped[str] = mapped_column(String(120), nullable=False)
    quantidade: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (
        UniqueConstraint('artigo', 'lote', 'carga', 'local', 'stacked', name='stock_key_unique'),
        CheckConstraint('quantidade >= 0', name='stock_nonnegative'),
        Index('ix_stock_article_lot_location', 'artigo', 'lote', 'local'),
    )

class Carga(Scoped, Base):
    __tablename__ = 'cargas'
    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    entrada: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    funcionario: Mapped[str | None] = mapped_column(String(120), nullable=True)

class Location(Scoped, Base):
    __tablename__ = 'locations'
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text('true'), nullable=False)
    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    capacidade: Mapped[int | None] = mapped_column(Integer, nullable=True)
    __table_args__ = (CheckConstraint('capacidade IS NULL OR capacidade > 0', name='positive_capacity'),)

class Corridor(Scoped, Base):
    __tablename__ = 'corridors'
    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(1), unique=True, nullable=False)
    andares: Mapped[int] = mapped_column(Integer, nullable=False)
    posicoes: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (
        CheckConstraint('andares BETWEEN 1 AND 9', name='valid_corridor_floors'),
        CheckConstraint('posicoes BETWEEN 1 AND 99', name='valid_corridor_slots'),
    )

class AppConfig(Scoped, Base):
    __tablename__ = 'app_config'
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(String(200), nullable=False)

class Audit(Scoped, Base):
    __tablename__ = 'audit'
    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)
    username: Mapped[str] = mapped_column(String(60), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False, default='')

class ImportBatch(Scoped, Base):
    __tablename__ = 'imports'
    id: Mapped[int] = mapped_column(primary_key=True)
    source_sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)
    pallets: Mapped[int] = mapped_column(Integer, nullable=False)
    groups: Mapped[int] = mapped_column(Integer, nullable=False)


def sync_location_policy(db):
    """Ensure floor exists, preserving configured capacities and all stock."""
    from .location_rules import FLOOR_LABEL, is_floor
    known = db.scalars(select(Location)).all()
    if not any(is_floor(loc.codigo) for loc in known):
        db.add(Location(codigo=FLOOR_LABEL, capacidade=None))
    changed = 0  # Capacities are now explicit catalogue settings; never overwrite them.
    db.commit()
    return changed


def seed_warehouse_layout(db):
    """Import the VBA's *theoretical* warehouse map only once; preserve edits.

    A-S: 27 slots per level; 4 levels, except L and M with 5.
    Existing stock is never modified, moved or deleted.
    """
    from .location_rules import FLOOR_LABEL
    from .contexts import seed_contexts
    seed_contexts(db)
    if db.scalar(select(AppConfig).where(AppConfig.key == 'warehouse_layout_seeded_vba')):
        return 0
    existing = set(db.scalars(select(Location.codigo)).all())
    new = []
    for ch in 'ABCDEFGHIJKLMNOPQRS':
        levels = 5 if ch in 'LM' else 4
        db.add(Corridor(codigo=ch, andares=levels, posicoes=27))
        for level in range(1, levels + 1):
            for number in range(1, 28):
                code = f'{ch}{level}{number:02d}'
                if code not in existing:
                    new.append(Location(codigo=code, capacidade=1))
    if FLOOR_LABEL not in existing and 'Chao' not in existing:
        new.append(Location(codigo=FLOOR_LABEL, capacidade=None))
    db.add_all(new)
    db.add(AppConfig(key='warehouse_layout_seeded_vba', value='1'))
    db.add(Audit(username='sistema', action='IMPORTAR_MAPA_VBA',
                 details=f'19 corredores A-S; 27 posições por andar; L e M com 5 andares; restantes com 4. {len(new)} localizações novas.'))
    db.commit()
    return len(new)


def init_db():
    """Legacy SQLite development only; PostgreSQL is prepared explicitly."""
    if engine.dialect.name == "postgresql":
        from .migrations import check_database
        check_database()
        return
    from sqlalchemy import select
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        from .contexts import seed_contexts
        seed_contexts(db)
        sync_location_policy(db)
        seed_warehouse_layout(db)


class Receipt(Scoped, Base):
    __tablename__ = 'receipts'
    id: Mapped[int] = mapped_column(primary_key=True)
    request_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    code: Mapped[str] = mapped_column(String(120), nullable=False)
    received_on: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default='draft', nullable=False)
    carga_id: Mapped[int | None] = mapped_column(ForeignKey('cargas.id'), unique=True)
    receiver: Mapped[str | None] = mapped_column(String(60))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (
        CheckConstraint("status IN ('draft','confirmed')", name='receipt_status_valid'),
        CheckConstraint("(status = 'draft' AND carga_id IS NULL AND receiver IS NULL AND confirmed_at IS NULL) OR (status = 'confirmed' AND carga_id IS NOT NULL AND receiver IS NOT NULL AND confirmed_at IS NOT NULL)", name='receipt_confirmation_valid'),
    )


class LoadNumber(Base):
    """Append-only allocation, shared by the owner's warehouses, never by owners."""
    __tablename__ = 'load_numbers'
    client_id: Mapped[int] = mapped_column(ForeignKey('clients.id'), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    number: Mapped[int] = mapped_column(Integer, primary_key=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey('receipts.id'), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)
    __table_args__ = (CheckConstraint('year >= 2027 AND number > 0', name='load_number_valid'),)


class ReceiptLine(Scoped, Base):
    __tablename__ = 'receipt_lines'
    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey('receipts.id'), nullable=False, index=True)
    artigo: Mapped[str] = mapped_column(String(120), nullable=False)
    lote: Mapped[str] = mapped_column(String(120), nullable=False)
    quantidade: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (
        UniqueConstraint('receipt_id', 'artigo', 'lote', name='receipt_line_group_unique'),
        CheckConstraint('quantidade > 0', name='receipt_line_positive'),
    )


class Putaway(Scoped, Base):
    document_pdf: Mapped[bytes | None] = mapped_column(LargeBinary)
    document_sha256: Mapped[str | None] = mapped_column(String(64))
    stacked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'), nullable=False)
    __tablename__ = 'putaways'
    id: Mapped[int] = mapped_column(primary_key=True)
    request_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    line_id: Mapped[int] = mapped_column(ForeignKey('receipt_lines.id'), nullable=False, index=True)
    local: Mapped[str] = mapped_column(String(120), nullable=False)
    quantidade: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default='draft', nullable=False)
    stock_id: Mapped[int | None] = mapped_column(ForeignKey('stock.id'), index=True)
    arranger: Mapped[str | None] = mapped_column(String(60))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (
        CheckConstraint('quantidade > 0', name='putaway_positive'),
        CheckConstraint("status IN ('draft','confirmed','cancelled')", name='putaway_status_valid'),
        CheckConstraint("(status IN ('draft','cancelled') AND stock_id IS NULL AND arranger IS NULL AND confirmed_at IS NULL) OR (status = 'confirmed' AND stock_id IS NOT NULL AND arranger IS NOT NULL AND confirmed_at IS NOT NULL)", name='putaway_confirmation_valid'),
    )


class DispatchOrder(Scoped, Base):
    typed_selection: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'), nullable=False)
    __tablename__ = 'dispatch_orders'
    office: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'), nullable=False)
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    cancelled_by: Mapped[str | None] = mapped_column(String(60))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    id: Mapped[int] = mapped_column(primary_key=True)
    request_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reference: Mapped[str] = mapped_column(String(120), nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default='draft')
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    checked_by: Mapped[str | None] = mapped_column(String(60))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    discrepancy: Mapped[str | None] = mapped_column(Text)
    confirmed_by: Mapped[str | None] = mapped_column(String(60))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (
        CheckConstraint("status IN ('draft','ready','checked','blocked','confirmed','cancelled')", name='dispatch_state_valid'),
        CheckConstraint("status IN ('draft','cancelled') OR reviewed_at IS NOT NULL", name='dispatch_review_valid'),
        CheckConstraint("status NOT IN ('checked','blocked','confirmed') OR (checked_at IS NOT NULL AND checked_by IS NOT NULL)", name='dispatch_check_valid'),
        CheckConstraint("status <> 'blocked' OR discrepancy IS NOT NULL", name='dispatch_discrepancy_required'),
        CheckConstraint("(status = 'confirmed' AND confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL) OR (status <> 'confirmed' AND confirmed_by IS NULL AND confirmed_at IS NULL)", name='dispatch_confirmation_valid'),
    )


class DispatchLine(Scoped, Base):
    stacked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'), nullable=False)
    __tablename__ = 'dispatch_lines'
    destination: Mapped[str] = mapped_column(String(120), nullable=False, default='', server_default='')
    destination_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default='0')
    destination_highlight: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text('false'))
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('dispatch_orders.id'), nullable=False, index=True)
    artigo: Mapped[str] = mapped_column(String(120), nullable=False)
    lote: Mapped[str] = mapped_column(String(120), nullable=False)
    quantidade: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (
        UniqueConstraint('order_id', 'artigo', 'lote', 'destination', 'stacked', name='dispatch_line_unique'),
        CheckConstraint('quantidade > 0', name='dispatch_line_positive'),
    )


class DispatchPick(Scoped, Base):
    __tablename__ = 'dispatch_picks'
    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey('dispatch_lines.id'), nullable=False, index=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey('stock.id'), nullable=False, index=True)
    quantidade: Mapped[int] = mapped_column(Integer, nullable=False)
    actual: Mapped[int | None] = mapped_column(Integer)
    carga: Mapped[str] = mapped_column(String(120), nullable=False)
    local: Mapped[str] = mapped_column(String(120), nullable=False)
    entrada: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    __table_args__ = (
        UniqueConstraint('line_id', 'stock_id', name='dispatch_pick_unique'),
        CheckConstraint('quantidade > 0', name='dispatch_pick_positive'),
        CheckConstraint('actual IS NULL OR actual >= 0', name='dispatch_actual_nonnegative'),
    )


class StockTransfer(Scoped, Base):
    stacked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'), nullable=False)
    __tablename__ = 'stock_transfers'
    id: Mapped[int] = mapped_column(primary_key=True)
    request_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    source_stock_id: Mapped[int] = mapped_column(ForeignKey('stock.id'), nullable=False, index=True)
    destination_stock_id: Mapped[int | None] = mapped_column(ForeignKey('stock.id'), index=True)
    artigo: Mapped[str] = mapped_column(String(120), nullable=False)
    lote: Mapped[str] = mapped_column(String(120), nullable=False)
    carga: Mapped[str] = mapped_column(String(120), nullable=False)
    entrada: Mapped[datetime | None] = mapped_column(DateTime)
    source_local: Mapped[str] = mapped_column(String(120), nullable=False)
    destination_local: Mapped[str] = mapped_column(String(120), nullable=False)
    quantidade: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default='draft', nullable=False)
    confirmed_by: Mapped[str | None] = mapped_column(String(60))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (
        CheckConstraint('quantidade > 0', name='transfer_positive'),
        CheckConstraint('source_local <> destination_local', name='transfer_different_locations'),
        CheckConstraint("status IN ('draft','confirmed')", name='transfer_status_valid'),
        CheckConstraint("(status = 'draft' AND destination_stock_id IS NULL AND confirmed_by IS NULL AND confirmed_at IS NULL) OR (status = 'confirmed' AND destination_stock_id IS NOT NULL AND confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL)", name='transfer_confirmation_valid'),
    )


class LotResolution(Scoped, Base):
    __tablename__ = 'lot_resolutions'
    id: Mapped[int] = mapped_column(primary_key=True)
    lote: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    artigo: Mapped[str] = mapped_column(String(120), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(60), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)


class DispatchDocument(Scoped, Base):
    __tablename__ = 'dispatch_documents'
    order_id: Mapped[int] = mapped_column(ForeignKey('dispatch_orders.id'), primary_key=True)
    pdf: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    stock_versions: Mapped[str] = mapped_column(Text, nullable=False)


class PreparationCompletion(Scoped, Base):
    __tablename__ = 'preparation_completions'
    order_id: Mapped[int] = mapped_column(ForeignKey('dispatch_orders.id'), primary_key=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(60), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)


class LocationClock(Scoped, Base):
    __tablename__ = 'location_clocks'
    local: Mapped[str] = mapped_column(String(120), primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class PhysicalInventory(Scoped, Base):
    __tablename__ = 'physical_inventories'
    id: Mapped[int] = mapped_column(primary_key=True)
    request_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    reference: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default='draft', nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)
    submitted_by: Mapped[str | None] = mapped_column(String(60))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    applied_by: Mapped[str | None] = mapped_column(String(60))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime)
    reason: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (CheckConstraint("kind IN ('partial','full')", name='inventory_kind'),
        CheckConstraint("status IN ('draft','submitted','applied')", name='inventory_status'))


class InventoryScope(Scoped, Base):
    __tablename__ = 'inventory_scopes'
    id: Mapped[int] = mapped_column(primary_key=True)
    inventory_id: Mapped[int] = mapped_column(ForeignKey('physical_inventories.id'), nullable=False, index=True)
    local: Mapped[str] = mapped_column(String(120), nullable=False)
    baseline: Mapped[str] = mapped_column(Text, nullable=False)
    clock: Mapped[int] = mapped_column(BigInteger, nullable=False)
    counted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    counted_by: Mapped[str | None] = mapped_column(String(60))
    counted_at: Mapped[datetime | None] = mapped_column(DateTime)
    note: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint('inventory_id', 'local', name='inventory_scope_unique'),)


class InventoryCount(Scoped, Base):
    stacked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'), nullable=False)
    __tablename__ = 'inventory_counts'
    id: Mapped[int] = mapped_column(primary_key=True)
    scope_id: Mapped[int] = mapped_column(ForeignKey('inventory_scopes.id'), nullable=False, index=True)
    artigo: Mapped[str | None] = mapped_column(String(120))
    lote: Mapped[str | None] = mapped_column(String(120))
    carga: Mapped[str | None] = mapped_column(String(120))
    quantidade: Mapped[int] = mapped_column(Integer, nullable=False)
    pending: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    __table_args__ = (CheckConstraint('quantidade > 0', name='inventory_count_positive'),)


class StockIncident(Scoped, Base):
    __tablename__ = 'stock_incidents'
    observation: Mapped[str | None] = mapped_column(Text)
    id: Mapped[int] = mapped_column(primary_key=True)
    request_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    pick_id: Mapped[int | None] = mapped_column(ForeignKey('dispatch_picks.id'), index=True)
    scope_id: Mapped[int | None] = mapped_column(ForeignKey('inventory_scopes.id'), index=True)
    quantidade: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(60), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default='open', nullable=False)
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[str | None] = mapped_column(String(60))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    inventory_id: Mapped[int | None] = mapped_column(ForeignKey('physical_inventories.id'))
    __table_args__ = (CheckConstraint('quantidade > 0', name='incident_positive'),
        CheckConstraint('(pick_id IS NOT NULL) OR (scope_id IS NOT NULL)', name='incident_origin'))


class StockAdjustment(Scoped, Base):
    stacked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'), nullable=False)
    __tablename__ = 'stock_adjustments'
    id: Mapped[int] = mapped_column(primary_key=True)
    inventory_id: Mapped[int] = mapped_column(ForeignKey('physical_inventories.id'), nullable=False, index=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey('stock.id'), nullable=False, index=True)
    artigo: Mapped[str] = mapped_column(String(120), nullable=False)
    lote: Mapped[str] = mapped_column(String(120), nullable=False)
    carga: Mapped[str] = mapped_column(String(120), nullable=False)
    local: Mapped[str] = mapped_column(String(120), nullable=False)
    before: Mapped[int] = mapped_column(Integer, nullable=False)
    after: Mapped[int] = mapped_column(Integer, nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(60), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)
    __table_args__ = (UniqueConstraint('inventory_id','stock_id',name='adjustment_unique'),
        CheckConstraint('"before" >= 0 AND "after" >= 0 AND delta = "after" - "before"', name='adjustment_values'))


class PreparationCompensation(Scoped, Base):
    __tablename__ = 'preparation_compensations'
    order_id: Mapped[int] = mapped_column(ForeignKey('dispatch_orders.id'), primary_key=True)
    inventory_id: Mapped[int] = mapped_column(ForeignKey('physical_inventories.id'), nullable=False, unique=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(60), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)


class MercadonaDestination(Scoped, Base):
    __tablename__ = 'mercadona_destinations'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    highlighted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class CommercialPreparation(Scoped, Base):
    __tablename__ = 'commercial_preparations'
    order_id: Mapped[int] = mapped_column(ForeignKey('dispatch_orders.id'), primary_key=True)
    customer: Mapped[str] = mapped_column(String(120), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    __table_args__ = (CheckConstraint("channel IN ('normal','mercadona')", name='commercial_channel'),)


class CustomerPending(Scoped, Base):
    __tablename__ = 'customer_pending'
    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey('dispatch_lines.id'), unique=True, nullable=False)
    quantidade: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(60), nullable=False)
    __table_args__ = (CheckConstraint('quantidade > 0', name='customer_pending_positive'),)


class PendingAllocation(Scoped, Base):
    __tablename__ = 'pending_allocations'
    id: Mapped[int] = mapped_column(primary_key=True)
    pending_id: Mapped[int] = mapped_column(ForeignKey('customer_pending.id'), nullable=False, index=True)
    receipt_line_id: Mapped[int] = mapped_column(ForeignKey('receipt_lines.id'), nullable=False, index=True)
    quantidade: Mapped[int] = mapped_column(Integer, nullable=False)
    actor: Mapped[str] = mapped_column(String(60), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)
    __table_args__ = (UniqueConstraint('pending_id','receipt_line_id',name='pending_allocation_unique'),
                     CheckConstraint('quantidade > 0', name='pending_allocation_positive'))


class AllocationCollection(Scoped, Base):
    __tablename__ = 'allocation_collections'
    allocation_id: Mapped[int] = mapped_column(ForeignKey('pending_allocations.id'), primary_key=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(60), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)


class ReceiptDocument(Scoped, Base):
    __tablename__ = 'receipt_documents'
    receipt_id: Mapped[int] = mapped_column(ForeignKey('receipts.id'), primary_key=True)
    pdf: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class IncompleteComposition(Scoped, Base):
    """Documentary draft only: not a pallet identity or a stock movement."""
    __tablename__ = 'incomplete_compositions'
    id: Mapped[int] = mapped_column(primary_key=True)
    request_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    receipt_line_id: Mapped[int | None] = mapped_column(ForeignKey('receipt_lines.id'), unique=True)
    reference: Mapped[str] = mapped_column(String(120), nullable=False)
    components: Mapped[str] = mapped_column(Text, nullable=False)
    observation: Mapped[str] = mapped_column(Text, nullable=False)
    physical_pallets: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    actor: Mapped[str] = mapped_column(String(60), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)
    __table_args__ = (CheckConstraint('physical_pallets = 1', name='composition_one_physical_pallet'),)


class ValmetOwner(Base):
    __tablename__ = 'valmet_owners'
    client_id: Mapped[int] = mapped_column(ForeignKey('clients.id',deferrable=True,initially='DEFERRED'), primary_key=True)

class ValmetEntry(Scoped, Base):
    __tablename__ = 'valmet_entries'
    id: Mapped[int] = mapped_column(primary_key=True)
    request_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default='draft')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)
    receipt_id: Mapped[int | None] = mapped_column(ForeignKey('receipts.id'), unique=True)
    __table_args__ = (CheckConstraint("status IN ('draft','confirmed','imported') AND version > 0 AND ((status IN ('draft','imported') AND receipt_id IS NULL) OR (status='confirmed' AND receipt_id IS NOT NULL))", name='valmet_entry_state'),)

class ValmetUnit(Scoped, Base):
    __tablename__ = 'valmet_units'
    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey('valmet_entries.id'), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    number: Mapped[str | None] = mapped_column(String(120))
    legacy_unnumbered: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'), nullable=False)
    physical_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    local: Mapped[str | None] = mapped_column(String(120))
    material_m2: Mapped[object | None] = mapped_column(Numeric(18,3))
    content_total: Mapped[int | None] = mapped_column(Integer)
    supplier: Mapped[str] = mapped_column(String(120), nullable=False)
    guide: Mapped[str] = mapped_column(String(120), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    brand: Mapped[str | None] = mapped_column(String(40))
    __table_args__ = (CheckConstraint('physical_quantity > 0 AND (content_total IS NULL OR content_total > 0) AND (material_m2 IS NULL OR material_m2 >= 0)', name='valmet_unit_quantities'),)

class ValmetNumber(Base):
    __tablename__ = 'valmet_numbers'
    client_id: Mapped[int] = mapped_column(ForeignKey('clients.id'), primary_key=True)
    number: Mapped[str] = mapped_column(String(120), primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey('valmet_units.id'), unique=True, nullable=False)

class ValmetIdentification(Scoped, Base):
    __tablename__ = 'valmet_identifications'
    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey('valmet_units.id'), unique=True, nullable=False)
    request_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    actor: Mapped[str] = mapped_column(String(60), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)
    content_total: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str] = mapped_column(Text, nullable=False)

class ValmetItem(Scoped, Base):
    __tablename__ = 'valmet_items'
    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey('valmet_units.id'), nullable=False)
    identification_id: Mapped[int | None] = mapped_column(ForeignKey('valmet_identifications.id'))
    article: Mapped[str | None] = mapped_column(String(120))
    lot: Mapped[str | None] = mapped_column(String(120))
    quantity: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (CheckConstraint('quantity IS NULL OR quantity > 0', name='valmet_item_quantity'),)

class ValmetPreparation(Scoped, Base):
    __tablename__='valmet_preparations'
    id: Mapped[int]=mapped_column(primary_key=True)
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    payload: Mapped[str]=mapped_column(Text,nullable=False)
    review: Mapped[str | None]=mapped_column(Text)
    version: Mapped[int]=mapped_column(Integer,nullable=False,default=1)
    status: Mapped[str]=mapped_column(String(20),nullable=False,default='draft')
    active_revision: Mapped[int | None]=mapped_column(Integer)
    created_by: Mapped[int]=mapped_column(ForeignKey('users.id'),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    __table_args__=(CheckConstraint("status IN ('draft','reserved','ready','shipped','cancelled') AND version>0",name='valmet_preparation_status'),
        Index('ix_valmet_preparations_active','context_id','active_revision'))

class ValmetPlanRevision(Scoped, Base):
    __tablename__='valmet_plan_revisions'
    id: Mapped[int]=mapped_column(primary_key=True)
    preparation_id: Mapped[int]=mapped_column(ForeignKey('valmet_preparations.id'),nullable=False)
    version: Mapped[int]=mapped_column(Integer,nullable=False)
    action: Mapped[str]=mapped_column(String(20),nullable=False)
    snapshot: Mapped[str]=mapped_column(Text,nullable=False)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    __table_args__=(Index('ix_valmet_revisions_preparation','context_id','preparation_id','id'),)

class ValmetReservation(Scoped, Base):
    __tablename__='valmet_reservations'
    id: Mapped[int]=mapped_column(primary_key=True)
    revision_id: Mapped[int]=mapped_column(ForeignKey('valmet_plan_revisions.id'),nullable=False)
    unit_id: Mapped[int]=mapped_column(ForeignKey('valmet_units.id'),nullable=False)
    physical: Mapped[int]=mapped_column(Integer,nullable=False)
    materials: Mapped[str]=mapped_column(Text,nullable=False)
    exclusive: Mapped[bool]=mapped_column(Boolean,nullable=False)
    __table_args__=(CheckConstraint('physical>0',name='valmet_reservation_positive'),
        UniqueConstraint('revision_id','unit_id',name='uq_valmet_reservation_unit'),
        Index('ix_valmet_reservations_unit','context_id','unit_id'))

class ValmetHandling(Scoped, Base):
    __tablename__='valmet_handling'
    id: Mapped[int]=mapped_column(primary_key=True)
    unit_id: Mapped[int]=mapped_column(ForeignKey('valmet_units.id'),nullable=False)
    separation: Mapped[str]=mapped_column(String(12),nullable=False)
    reconciled: Mapped[bool]=mapped_column(Boolean,nullable=False,default=False)
    unlocated: Mapped[bool]=mapped_column(Boolean,nullable=False,default=False)
    note: Mapped[str]=mapped_column(Text,nullable=False)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    __table_args__=(CheckConstraint("separation IN ('yes','no','unknown')",name='valmet_separation'),
        Index('ix_valmet_handling_unit','context_id','unit_id','id'))

class ValmetPosition(Scoped, Base):
    """Rebuildable projection; only ledger triggers may write it. No new pallet ID."""
    __tablename__='valmet_positions'
    id: Mapped[int]=mapped_column(primary_key=True)
    unit_id: Mapped[int]=mapped_column(ForeignKey('valmet_units.id'),nullable=False)
    root: Mapped[bool]=mapped_column(Boolean,nullable=False,default=False)
    local: Mapped[str | None]=mapped_column(String(120))
    physical_quantity: Mapped[int]=mapped_column(Integer,nullable=False)
    materials: Mapped[str]=mapped_column(Text,nullable=False)
    uncertain: Mapped[bool]=mapped_column(Boolean,nullable=False,default=False)
    __table_args__=(CheckConstraint('physical_quantity>=0',name='valmet_position_nonnegative'),
        UniqueConstraint('unit_id','local',name='uq_valmet_positions_local'),Index('ix_valmet_positions_unit','context_id','unit_id'),
        Index('uq_valmet_positions_root','context_id','unit_id',unique=True,postgresql_where=text('root'),sqlite_where=text('root')))

class ValmetShipment(Scoped, Base):
    __tablename__='valmet_shipments'
    id: Mapped[int]=mapped_column(primary_key=True)
    preparation_id: Mapped[int]=mapped_column(ForeignKey('valmet_preparations.id'),nullable=False)
    revision_id: Mapped[int]=mapped_column(ForeignKey('valmet_plan_revisions.id'),nullable=False)
    request_key: Mapped[str]=mapped_column(String(64),nullable=False,unique=True)
    payload: Mapped[str]=mapped_column(Text,nullable=False)
    loaded_on: Mapped[datetime]=mapped_column(DateTime,nullable=False)
    references: Mapped[str]=mapped_column(Text,nullable=False)
    snapshot: Mapped[str]=mapped_column(Text,nullable=False)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,nullable=False,default=now_utc)

class ValmetShipmentLine(Scoped, Base):
    __tablename__='valmet_shipment_lines'
    id: Mapped[int]=mapped_column(primary_key=True)
    shipment_id: Mapped[int]=mapped_column(ForeignKey('valmet_shipments.id'),nullable=False)
    reservation_id: Mapped[int]=mapped_column(ForeignKey('valmet_reservations.id'),nullable=False)
    position_id: Mapped[int]=mapped_column(ForeignKey('valmet_positions.id'),nullable=False)
    physical_quantity: Mapped[int]=mapped_column(Integer,nullable=False)
    materials: Mapped[str]=mapped_column(Text,nullable=False)
    unknown_ack: Mapped[bool]=mapped_column(Boolean,nullable=False,default=False)
    __table_args__=(CheckConstraint('physical_quantity>=0',name='valmet_shipment_nonnegative'),UniqueConstraint('shipment_id','position_id',name='uq_valmet_shipment_position'))

class ValmetOccurrence(Scoped, Base):
    __tablename__='valmet_occurrences'
    id: Mapped[int]=mapped_column(primary_key=True)
    preparation_id: Mapped[int]=mapped_column(ForeignKey('valmet_preparations.id'),nullable=False)
    unit_id: Mapped[int]=mapped_column(ForeignKey('valmet_units.id'),nullable=False)
    version: Mapped[int]=mapped_column(Integer,nullable=False)
    kind: Mapped[str]=mapped_column(String(30),nullable=False)
    request_key: Mapped[str | None]=mapped_column(String(64),unique=True)
    resolves_id: Mapped[int | None]=mapped_column(ForeignKey('valmet_occurrences.id'))
    details: Mapped[str]=mapped_column(Text,nullable=False)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,nullable=False,default=now_utc)
    __table_args__=(UniqueConstraint('resolves_id',name='uq_valmet_occurrence_resolution'),)

class ValmetRelocation(Scoped, Base):
    __tablename__='valmet_relocations'
    id: Mapped[int]=mapped_column(primary_key=True)
    preparation_id: Mapped[int]=mapped_column(ForeignKey('valmet_preparations.id'),nullable=False)
    position_id: Mapped[int]=mapped_column(ForeignKey('valmet_positions.id'),nullable=False)
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    payload: Mapped[str]=mapped_column(Text,nullable=False)
    target_local: Mapped[str]=mapped_column(String(120),nullable=False)
    physical_quantity: Mapped[int]=mapped_column(Integer,nullable=False)
    materials: Mapped[str]=mapped_column(Text,nullable=False)
    note: Mapped[str]=mapped_column(Text,nullable=False)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,nullable=False,default=now_utc)

class ValmetDocument(Scoped, Base):
    __tablename__='valmet_documents'
    id: Mapped[int]=mapped_column(primary_key=True)
    preparation_id: Mapped[int]=mapped_column(ForeignKey('valmet_preparations.id'),nullable=False)
    revision_id: Mapped[int]=mapped_column(ForeignKey('valmet_plan_revisions.id'),nullable=False)
    pdf: Mapped[bytes]=mapped_column(LargeBinary,nullable=False)
    digest: Mapped[str]=mapped_column(String(64),nullable=False)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,nullable=False,default=now_utc)
    __table_args__=(UniqueConstraint('revision_id',name='uq_valmet_document_revision'),)

class ValmetInventory(Scoped, Base):
    __tablename__='valmet_inventories'
    id: Mapped[int]=mapped_column(primary_key=True)
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    scope: Mapped[str]=mapped_column(Text,nullable=False)
    reference: Mapped[str]=mapped_column(Text,nullable=False)
    status: Mapped[str]=mapped_column(String(20),nullable=False,default='draft')
    version: Mapped[int]=mapped_column(Integer,nullable=False,default=1)
    created_by: Mapped[int]=mapped_column(ForeignKey('users.id'),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,nullable=False,default=now_utc)
    __table_args__=(CheckConstraint("status IN ('draft','submitted','closed') AND version>0",name='valmet_inventory_state'),)

class ValmetCount(Scoped, Base):
    __tablename__='valmet_counts'
    id: Mapped[int]=mapped_column(primary_key=True)
    inventory_id: Mapped[int]=mapped_column(ForeignKey('valmet_inventories.id'),nullable=False)
    target: Mapped[str]=mapped_column(String(160),nullable=False)
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    version: Mapped[int]=mapped_column(Integer,nullable=False)
    kind: Mapped[str]=mapped_column(String(20),nullable=False)
    payload: Mapped[str]=mapped_column(Text,nullable=False)
    reference: Mapped[str]=mapped_column(Text,nullable=False)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,nullable=False,default=now_utc)
    __table_args__=(Index('ix_valmet_counts_target','context_id','inventory_id','target','id'),)

class ValmetControlDraft(Scoped, Base):
    __tablename__='valmet_control_drafts'
    id: Mapped[int]=mapped_column(primary_key=True)
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    kind: Mapped[str]=mapped_column(String(20),nullable=False)
    position_id: Mapped[int]=mapped_column(ForeignKey('valmet_positions.id'),nullable=False)
    inventory_id: Mapped[int | None]=mapped_column(ForeignKey('valmet_inventories.id'))
    count_id: Mapped[int | None]=mapped_column(ForeignKey('valmet_counts.id'))
    payload: Mapped[str]=mapped_column(Text,nullable=False)
    reference: Mapped[str]=mapped_column(Text,nullable=False)
    version: Mapped[int]=mapped_column(Integer,nullable=False,default=1)
    status: Mapped[str]=mapped_column(String(20),nullable=False,default='draft')
    created_by: Mapped[int]=mapped_column(ForeignKey('users.id'),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,nullable=False,default=now_utc)
    __table_args__=(CheckConstraint("kind IN ('transfer','adjustment') AND status IN ('draft','confirmed') AND version>0",name='valmet_control_draft_state'),)

class ValmetControlMove(Scoped, Base):
    __tablename__='valmet_control_moves'
    id: Mapped[int]=mapped_column(primary_key=True)
    draft_id: Mapped[int]=mapped_column(ForeignKey('valmet_control_drafts.id'),unique=True,nullable=False)
    unit_id: Mapped[int]=mapped_column(ForeignKey('valmet_units.id'),nullable=False)
    snapshot: Mapped[str]=mapped_column(Text,nullable=False)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,nullable=False,default=now_utc)

class ValmetInventoryDecision(Scoped, Base):
    __tablename__='valmet_inventory_decisions'
    id: Mapped[int]=mapped_column(primary_key=True)
    inventory_id: Mapped[int]=mapped_column(ForeignKey('valmet_inventories.id'),nullable=False)
    count_id: Mapped[int]=mapped_column(ForeignKey('valmet_counts.id'),nullable=False)
    move_id: Mapped[int | None]=mapped_column(ForeignKey('valmet_control_moves.id'))
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    kind: Mapped[str]=mapped_column(String(20),nullable=False)
    reason: Mapped[str]=mapped_column(Text,nullable=False)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,nullable=False,default=now_utc)
    __table_args__=(Index('uq_valmet_decision_final','context_id','count_id',unique=True,postgresql_where=text("kind IN ('approve','omit')"),sqlite_where=text("kind IN ('approve','omit')")),)

class ValmetControlDocument(Scoped, Base):
    __tablename__='valmet_control_documents'
    id: Mapped[int]=mapped_column(primary_key=True)
    inventory_id: Mapped[int]=mapped_column(ForeignKey('valmet_inventories.id'),nullable=False)
    version: Mapped[int]=mapped_column(Integer,nullable=False)
    kind: Mapped[str]=mapped_column(String(20),nullable=False)
    snapshot: Mapped[str]=mapped_column(Text,nullable=False)
    pdf: Mapped[bytes]=mapped_column(LargeBinary,nullable=False)
    digest: Mapped[str]=mapped_column(String(64),nullable=False)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,nullable=False,default=now_utc)
    __table_args__=(UniqueConstraint('inventory_id','version','kind',name='uq_valmet_control_document'),)

class ValmetRoll(Scoped, Base):
    __tablename__='valmet_rolls'
    __table_args__=(Index('ix_valmet_rolls_unit','context_id','unit_id','id'),)
    id: Mapped[int]=mapped_column(primary_key=True)
    unit_id: Mapped[int]=mapped_column(ForeignKey('valmet_units.id'),nullable=False)
    article: Mapped[str | None]=mapped_column(String(120))
    lot: Mapped[str]=mapped_column(String(120),nullable=False)
    source_event: Mapped[int | None]=mapped_column(ForeignKey('valmet_roll_events.id'))
    source_move: Mapped[int | None]=mapped_column(ForeignKey('valmet_control_moves.id'))

class ValmetRollState(Scoped, Base):
    __tablename__='valmet_roll_states'
    __table_args__=(Index('ix_valmet_roll_states_unit','context_id','unit_id','id'),)
    id: Mapped[int]=mapped_column(ForeignKey('valmet_rolls.id'),primary_key=True)
    unit_id: Mapped[int]=mapped_column(ForeignKey('valmet_units.id'),nullable=False)
    present: Mapped[bool]=mapped_column(Boolean,nullable=False)
    divisible: Mapped[bool]=mapped_column(Boolean,nullable=False)

class ValmetRollEvent(Scoped, Base):
    __tablename__='valmet_roll_events'
    __table_args__=(Index('ix_valmet_roll_events_unit','context_id','unit_id','id'),)
    id: Mapped[int]=mapped_column(primary_key=True)
    unit_id: Mapped[int]=mapped_column(ForeignKey('valmet_units.id'),nullable=False)
    request_key: Mapped[str]=mapped_column(String(64),nullable=False,unique=True)
    action: Mapped[str]=mapped_column(String(20),nullable=False)
    payload: Mapped[str]=mapped_column(Text,nullable=False)
    reason: Mapped[str]=mapped_column(Text,nullable=False)
    evidence: Mapped[str]=mapped_column(Text,nullable=False)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,nullable=False,default=now_utc)

# Every business key lives inside an immutable client/warehouse context. IDs stay
# globally unique, preserving all historical references and archived documents.
class ValmetImportDraft(Scoped, Base):
    __tablename__ = 'valmet_import_drafts'
    id: Mapped[int] = mapped_column(primary_key=True)
    request_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    report: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(60), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)

class ValmetImportRevision(Scoped, Base):
    __tablename__ = 'valmet_import_revisions'
    id: Mapped[int] = mapped_column(primary_key=True)
    draft_id: Mapped[int] = mapped_column(ForeignKey('valmet_import_drafts.id'), nullable=False)
    request_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    plan: Mapped[str] = mapped_column(Text, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(60), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)

class ValmetImportConfirmation(Scoped, Base):
    __tablename__ = 'valmet_import_confirmations'
    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey('valmet_import_revisions.id'), unique=True, nullable=False)
    entry_id: Mapped[int] = mapped_column(ForeignKey('valmet_entries.id'), unique=True, nullable=False)
    client_id: Mapped[int] = mapped_column(ForeignKey('clients.id'), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    request_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    actor: Mapped[str] = mapped_column(String(60), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)
    __table_args__ = (UniqueConstraint('context_id'),Index('uq_valmet_import_owner_digest','client_id','digest',unique=True))

class NktOwner(Base):
    __tablename__='nkt_owners'
    client_id: Mapped[int]=mapped_column(ForeignKey('clients.id',deferrable=True,initially='DEFERRED'),primary_key=True)

class NktEntry(Scoped,Base):
    __tablename__='nkt_entries'
    id: Mapped[int]=mapped_column(primary_key=True)
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    payload: Mapped[str]=mapped_column(Text,nullable=False)
    version: Mapped[int]=mapped_column(Integer,nullable=False,default=1)
    status: Mapped[str]=mapped_column(String(20),nullable=False,default='draft')
    created_by: Mapped[int]=mapped_column(ForeignKey('users.id'),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,nullable=False,default=now_utc)
    confirmed_by: Mapped[str|None]=mapped_column(String(60))
    confirmed_at: Mapped[datetime|None]=mapped_column(DateTime)
    __table_args__=(CheckConstraint("version>0 AND ((status='draft' AND confirmed_by IS NULL AND confirmed_at IS NULL) OR (status='confirmed' AND confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL))",name='nkt_entry_state'),)

class NktReview(Scoped,Base):
    __tablename__='nkt_reviews'
    id: Mapped[int]=mapped_column(primary_key=True)
    entry_id: Mapped[int]=mapped_column(ForeignKey('nkt_entries.id'),nullable=False)
    version: Mapped[int]=mapped_column(Integer,nullable=False)
    payload: Mapped[str]=mapped_column(Text,nullable=False)
    digest: Mapped[str]=mapped_column(String(64),nullable=False)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,nullable=False,default=now_utc)
    __table_args__=(UniqueConstraint('entry_id','version'),)

class NktPallet(Scoped,Base):
    __tablename__='nkt_pallets'
    id: Mapped[int]=mapped_column(primary_key=True)
    entry_id: Mapped[int]=mapped_column(ForeignKey('nkt_entries.id'),nullable=False)
    client_id: Mapped[int]=mapped_column(ForeignKey('clients.id'),nullable=False)
    peg: Mapped[str]=mapped_column(String(120),nullable=False)
    local: Mapped[str]=mapped_column(String(120),nullable=False)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,nullable=False,default=now_utc)

Index('uq_nkt_owner_peg',NktPallet.client_id,func.lower(NktPallet.peg),unique=True)
Index('ix_nkt_context_local',NktPallet.context_id,NktPallet.local)

class NktReceiving(Scoped,Base):
    __tablename__='nkt_receivings'
    id: Mapped[int]=mapped_column(primary_key=True)
    client_id: Mapped[int]=mapped_column(ForeignKey('clients.id'),nullable=False)
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    source_name: Mapped[str]=mapped_column(String(240),nullable=False)
    source_digest: Mapped[str|None]=mapped_column(String(64))
    report: Mapped[str]=mapped_column(Text,nullable=False,deferred=True)
    created_by: Mapped[int]=mapped_column(ForeignKey('users.id'),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    version: Mapped[int]=mapped_column(Integer,default=0,nullable=False)
    status: Mapped[str]=mapped_column(String(20),default='draft',nullable=False)
    __table_args__=(Index('uq_nkt_source_owner','client_id','source_digest',unique=True),CheckConstraint("version>=0 AND status IN ('draft','confirmed')",name='nkt_receiving_state'))

class NktReceivingReview(Scoped,Base):
    __tablename__='nkt_receiving_reviews'
    id: Mapped[int]=mapped_column(primary_key=True)
    receiving_id: Mapped[int]=mapped_column(ForeignKey('nkt_receivings.id'),nullable=False)
    version: Mapped[int]=mapped_column(Integer,nullable=False)
    payload: Mapped[str]=mapped_column(Text,nullable=False)
    digest: Mapped[str]=mapped_column(String(64),nullable=False)
    reason: Mapped[str]=mapped_column(Text,nullable=False)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    __table_args__=(UniqueConstraint('receiving_id','version'),)

class NktContainer(Scoped,Base):
    __tablename__='nkt_containers'
    id: Mapped[int]=mapped_column(primary_key=True)
    receiving_id: Mapped[int]=mapped_column(ForeignKey('nkt_receivings.id'),nullable=False)
    review_id: Mapped[int]=mapped_column(ForeignKey('nkt_receiving_reviews.id'),nullable=False)
    entry_id: Mapped[int]=mapped_column(ForeignKey('nkt_entries.id'),unique=True,nullable=False)
    carga_id: Mapped[int]=mapped_column(ForeignKey('cargas.id'),unique=True,nullable=False)
    client_id: Mapped[int]=mapped_column(ForeignKey('clients.id'),nullable=False)
    name: Mapped[str]=mapped_column(String(120),nullable=False)
    payload: Mapped[str]=mapped_column(Text,nullable=False)
    year: Mapped[int|None]=mapped_column(Integer)
    number: Mapped[int|None]=mapped_column(Integer)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    __table_args__=(UniqueConstraint('receiving_id','name'),Index('uq_nkt_container_number','client_id','year','number',unique=True),CheckConstraint('(year IS NULL AND number IS NULL) OR (year>=2027 AND number>0)',name='nkt_container_number'))

class NktPreparation(Scoped,Base):
    __tablename__='nkt_preparations'
    id: Mapped[int]=mapped_column(primary_key=True)
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    created_by: Mapped[int]=mapped_column(ForeignKey('users.id'),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    version: Mapped[int]=mapped_column(Integer,default=1,nullable=False)
    active_revision: Mapped[int|None]=mapped_column(Integer)
    status: Mapped[str]=mapped_column(String(30),default='draft',nullable=False)
    __table_args__=(CheckConstraint("version>0 AND status IN ('draft','reserved','partial','loaded','cancelled','reconciliation')",name='nkt_preparation_state'),)

class NktPlanRevision(Scoped,Base):
    __tablename__='nkt_plan_revisions'
    id: Mapped[int]=mapped_column(primary_key=True)
    preparation_id: Mapped[int]=mapped_column(ForeignKey('nkt_preparations.id'),nullable=False)
    version: Mapped[int]=mapped_column(Integer,nullable=False)
    payload: Mapped[str]=mapped_column(Text,nullable=False)
    digest: Mapped[str]=mapped_column(String(64),nullable=False)
    reason: Mapped[str]=mapped_column(Text,nullable=False)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    __table_args__=(UniqueConstraint('preparation_id','version'),)

class NktCorrection(Scoped,Base):
    __tablename__='nkt_corrections'
    id: Mapped[int]=mapped_column(primary_key=True)
    preparation_id: Mapped[int]=mapped_column(ForeignKey('nkt_preparations.id'),nullable=False)
    operation_id: Mapped[int]=mapped_column(ForeignKey('nkt_operations.id'),nullable=False)
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    payload: Mapped[str]=mapped_column(Text,nullable=False)
    snapshot: Mapped[str]=mapped_column(Text,nullable=False)
    digest: Mapped[str]=mapped_column(String(64),nullable=False)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)

class NktOperation(Scoped,Base):
    __tablename__='nkt_operations'
    id: Mapped[int]=mapped_column(primary_key=True)
    preparation_id: Mapped[int|None]=mapped_column(ForeignKey('nkt_preparations.id'))
    revision_id: Mapped[int|None]=mapped_column(ForeignKey('nkt_plan_revisions.id'))
    inventory_id: Mapped[int|None]=mapped_column(ForeignKey('nkt_inventories.id'))
    inventory_review_id: Mapped[int|None]=mapped_column(ForeignKey('nkt_inventory_reviews.id'),unique=True)
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    kind: Mapped[str]=mapped_column(String(20),nullable=False)
    payload: Mapped[str]=mapped_column(Text,nullable=False)
    snapshot: Mapped[str]=mapped_column(Text,nullable=False)
    correction_id: Mapped[int|None]=mapped_column(Integer,unique=True)
    corrects_id: Mapped[int|None]=mapped_column(ForeignKey('nkt_operations.id'))
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    __table_args__=(CheckConstraint("kind IN ('reserve','cancel','ship','restore','metadata','inventory_missing','inventory_restore')",name='nkt_operation_kind'),CheckConstraint("(kind IN ('inventory_missing','inventory_restore') AND preparation_id IS NULL AND revision_id IS NULL AND inventory_id IS NOT NULL AND inventory_review_id IS NOT NULL AND correction_id IS NULL) OR (kind NOT IN ('inventory_missing','inventory_restore') AND preparation_id IS NOT NULL AND revision_id IS NOT NULL AND inventory_id IS NULL AND inventory_review_id IS NULL)",name='nkt_operation_origin'),Index('uq_nkt_inventory_confirmation','context_id','inventory_id',unique=True,postgresql_where=text("kind='inventory_missing'"),sqlite_where=text("kind='inventory_missing'")))

class NktStockEvent(Scoped,Base):
    __tablename__='nkt_stock_events'
    id: Mapped[int]=mapped_column(primary_key=True)
    operation_id: Mapped[int]=mapped_column(ForeignKey('nkt_operations.id'),nullable=False)
    pallet_id: Mapped[int]=mapped_column(ForeignKey('nkt_pallets.id'),nullable=False)
    previous_event_id: Mapped[int|None]=mapped_column(ForeignKey('nkt_stock_events.id'))
    state: Mapped[str]=mapped_column(String(20),nullable=False)
    __table_args__=(UniqueConstraint('operation_id','pallet_id'),CheckConstraint("state IN ('available','reserved','shipped','missing')",name='nkt_event_state'),Index('ix_nkt_event_latest','context_id','pallet_id','id'))

class NktDocument(Scoped,Base):
    __tablename__='nkt_documents'
    id: Mapped[int]=mapped_column(primary_key=True)
    revision_id: Mapped[int|None]=mapped_column(ForeignKey('nkt_plan_revisions.id'),unique=True)
    operation_id: Mapped[int|None]=mapped_column(ForeignKey('nkt_operations.id'),unique=True)
    correction_id: Mapped[int|None]=mapped_column(ForeignKey('nkt_corrections.id'),unique=True)
    inventory_review_id: Mapped[int|None]=mapped_column(ForeignKey('nkt_inventory_reviews.id'),unique=True)
    pdf: Mapped[bytes]=mapped_column(LargeBinary,nullable=False)
    digest: Mapped[str]=mapped_column(String(64),nullable=False)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    __table_args__=(CheckConstraint('(CASE WHEN revision_id IS NULL THEN 0 ELSE 1 END)+(CASE WHEN operation_id IS NULL THEN 0 ELSE 1 END)+(CASE WHEN correction_id IS NULL THEN 0 ELSE 1 END)+(CASE WHEN inventory_review_id IS NULL THEN 0 ELSE 1 END)=1',name='nkt_document_origin'),)

class NktInventory(Scoped,Base):
    __tablename__='nkt_inventories'
    id: Mapped[int]=mapped_column(primary_key=True)
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    title: Mapped[str]=mapped_column(String(120),nullable=False)
    mode: Mapped[str]=mapped_column(String(20),nullable=False)
    scope: Mapped[str]=mapped_column(Text,nullable=False)
    opening: Mapped[str]=mapped_column(Text,nullable=False)
    created_by: Mapped[int]=mapped_column(ForeignKey('users.id'),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    version: Mapped[int]=mapped_column(Integer,default=1,nullable=False)
    status: Mapped[str]=mapped_column(String(20),default='draft',nullable=False)
    __table_args__=(CheckConstraint("mode IN ('complete','partial') AND status IN ('draft','confirmed') AND version>0",name='nkt_inventory_state'),)

class NktInventoryCheck(Scoped,Base):
    __tablename__='nkt_inventory_checks'
    id: Mapped[int]=mapped_column(primary_key=True)
    inventory_id: Mapped[int]=mapped_column(ForeignKey('nkt_inventories.id'),nullable=False)
    entry_id: Mapped[int]=mapped_column(ForeignKey('nkt_entries.id'),nullable=False)
    state: Mapped[str]=mapped_column(String(20),nullable=False)
    communicated: Mapped[str|None]=mapped_column(String(120))
    full_peg: Mapped[bool]=mapped_column(Boolean,default=False,nullable=False)
    pallet_id: Mapped[int|None]=mapped_column(ForeignKey('nkt_pallets.id'))
    snapshot: Mapped[str]=mapped_column(Text,nullable=False)
    reason: Mapped[str]=mapped_column(Text,nullable=False)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    __table_args__=(CheckConstraint("state IN ('located','not_located','excluded')",name='nkt_inventory_check_state'),)

class NktInventoryReview(Scoped,Base):
    __tablename__='nkt_inventory_reviews'
    id: Mapped[int]=mapped_column(primary_key=True)
    inventory_id: Mapped[int]=mapped_column(ForeignKey('nkt_inventories.id'),nullable=False)
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    kind: Mapped[str]=mapped_column(String(20),nullable=False)
    version: Mapped[int]=mapped_column(Integer,nullable=False)
    payload: Mapped[str]=mapped_column(Text,nullable=False)
    digest: Mapped[str]=mapped_column(String(64),nullable=False)
    original_id: Mapped[int|None]=mapped_column(Integer)
    reason: Mapped[str]=mapped_column(Text,nullable=False)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    __table_args__=(CheckConstraint("kind IN ('conference','confirm','restore')",name='nkt_inventory_review_kind'),)

class NktInventoryIncident(Scoped,Base):
    __tablename__='nkt_inventory_incidents'
    id: Mapped[int]=mapped_column(primary_key=True)
    inventory_id: Mapped[int]=mapped_column(ForeignKey('nkt_inventories.id'),nullable=False)
    communicated: Mapped[str|None]=mapped_column(String(120))
    kind: Mapped[str]=mapped_column(String(40),nullable=False)
    details: Mapped[str]=mapped_column(Text,nullable=False)
    resolves_id: Mapped[int|None]=mapped_column(ForeignKey('nkt_inventory_incidents.id'),unique=True)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)

class AlcobreOwner(Base):
    __tablename__='alcobre_owners'
    client_id: Mapped[int]=mapped_column(ForeignKey('clients.id',deferrable=True,initially='DEFERRED'),primary_key=True)
    warehouse_id: Mapped[int]=mapped_column(ForeignKey('warehouses.id',deferrable=True,initially='DEFERRED'),nullable=False)

class AlcobreZone(Scoped,Base):
    __tablename__='alcobre_zones'
    context_id: Mapped[int]=mapped_column(ForeignKey('storage_contexts.id',deferrable=True,initially='DEFERRED'),nullable=False,default=1,server_default='1',index=True)
    id: Mapped[int]=mapped_column(primary_key=True)
    code: Mapped[str]=mapped_column(String(20),nullable=False)
    positions: Mapped[int]=mapped_column(Integer,nullable=False)
    active: Mapped[bool]=mapped_column(Boolean,default=True,nullable=False)
    __table_args__=(UniqueConstraint('code'),CheckConstraint('positions BETWEEN 1 AND 500',name='alcobre_zone_positions'))

class AlcobreSlot(Scoped,Base):
    __tablename__='alcobre_slots'
    id: Mapped[int]=mapped_column(primary_key=True)
    zone_id: Mapped[int]=mapped_column(ForeignKey('alcobre_zones.id'),nullable=False)
    position: Mapped[int]=mapped_column(Integer,nullable=False)
    local: Mapped[str]=mapped_column(String(120),nullable=False)
    __table_args__=(UniqueConstraint('zone_id','position'),UniqueConstraint('local'),ForeignKeyConstraint(['context_id','local'],['locations.context_id','locations.codigo']))

class AlcobreEntry(Scoped,Base):
    __tablename__='alcobre_entries'
    id: Mapped[int]=mapped_column(primary_key=True)
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    mode: Mapped[str]=mapped_column(String(20),nullable=False)
    reference: Mapped[str|None]=mapped_column(String(200))
    received_on: Mapped[str|None]=mapped_column(String(10))
    source_name: Mapped[str|None]=mapped_column(String(240))
    source_pdf: Mapped[bytes|None]=mapped_column(LargeBinary)
    source_digest: Mapped[str|None]=mapped_column(String(64))
    extraction: Mapped[str]=mapped_column(Text,nullable=False)
    version: Mapped[int]=mapped_column(Integer,default=0,nullable=False)
    status: Mapped[str]=mapped_column(String(20),default='draft',nullable=False)
    quantity: Mapped[int|None]=mapped_column(Integer)
    created_by: Mapped[int]=mapped_column(ForeignKey('users.id'),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    confirmed_by: Mapped[str|None]=mapped_column(String(60))
    confirmed_at: Mapped[datetime|None]=mapped_column(DateTime)
    __table_args__=(CheckConstraint("mode IN ('direct','pending') AND version>=0 AND status IN ('draft','confirmed')",name='alcobre_entry_state'),CheckConstraint('quantity IS NULL OR quantity BETWEEN 1 AND 300',name='alcobre_quantity'),CheckConstraint('(source_pdf IS NULL AND source_digest IS NULL AND source_name IS NULL) OR (source_pdf IS NOT NULL AND source_digest IS NOT NULL AND source_name IS NOT NULL)',name='alcobre_source_complete'),UniqueConstraint('source_digest'))

class AlcobreReview(Scoped,Base):
    __tablename__='alcobre_reviews'
    id: Mapped[int]=mapped_column(primary_key=True)
    entry_id: Mapped[int]=mapped_column(ForeignKey('alcobre_entries.id'),nullable=False)
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    kind: Mapped[str]=mapped_column(String(20),nullable=False)
    version: Mapped[int]=mapped_column(Integer,nullable=False)
    payload: Mapped[str]=mapped_column(Text,nullable=False)
    digest: Mapped[str]=mapped_column(String(64),nullable=False)
    reason: Mapped[str]=mapped_column(Text,nullable=False)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    __table_args__=(CheckConstraint("kind IN ('receive','identify','place')",name='alcobre_review_kind'),)

class AlcobreAction(Scoped,Base):
    __tablename__='alcobre_actions'
    id: Mapped[int]=mapped_column(primary_key=True)
    entry_id: Mapped[int]=mapped_column(ForeignKey('alcobre_entries.id'),nullable=False)
    review_id: Mapped[int]=mapped_column(ForeignKey('alcobre_reviews.id'),unique=True,nullable=False)
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)

class AlcobreBobine(Scoped,Base):
    __tablename__='alcobre_bobines'
    id: Mapped[int]=mapped_column(primary_key=True)
    entry_id: Mapped[int]=mapped_column(ForeignKey('alcobre_entries.id'),nullable=False)
    action_id: Mapped[int]=mapped_column(ForeignKey('alcobre_actions.id'),nullable=False)
    client_id: Mapped[int]=mapped_column(ForeignKey('clients.id'),nullable=False)
    number: Mapped[str]=mapped_column(String(120),nullable=False)
    number_key: Mapped[str]=mapped_column(String(240),nullable=False)
    __table_args__=(Index('uq_alcobre_owner_number','client_id','number_key',unique=True),)

class AlcobrePlacement(Scoped,Base):
    __tablename__='alcobre_placements'
    id: Mapped[int]=mapped_column(primary_key=True)
    bobine_id: Mapped[int]=mapped_column(ForeignKey('alcobre_bobines.id'),unique=True,nullable=False)
    action_id: Mapped[int]=mapped_column(ForeignKey('alcobre_actions.id'),nullable=False)
    local: Mapped[str]=mapped_column(String(120),nullable=False)
    __table_args__=(ForeignKeyConstraint(['context_id','local'],['alcobre_slots.context_id','alcobre_slots.local']),)

class AlcobreDispatch(Scoped,Base):
    __tablename__='alcobre_dispatches'
    id: Mapped[int]=mapped_column(primary_key=True)
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    source_name: Mapped[str]=mapped_column(String(240),nullable=False)
    source_pdf: Mapped[bytes]=mapped_column(LargeBinary,nullable=False)
    source_digest: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    extraction: Mapped[str]=mapped_column(Text,nullable=False)
    created_by: Mapped[int]=mapped_column(ForeignKey('users.id'),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    version: Mapped[int]=mapped_column(Integer,default=0,nullable=False)
    active_revision: Mapped[int|None]=mapped_column(Integer)
    status: Mapped[str]=mapped_column(String(20),default='draft',nullable=False)
    __table_args__=(CheckConstraint("version>=0 AND status IN ('draft','prepared','confirmed','cancelled')",name='alcobre_dispatch_status'),)

class AlcobreDispatchRevision(Scoped,Base):
    __tablename__='alcobre_dispatch_revisions'
    id: Mapped[int]=mapped_column(primary_key=True)
    dispatch_id: Mapped[int]=mapped_column(ForeignKey('alcobre_dispatches.id'),nullable=False)
    version: Mapped[int]=mapped_column(Integer,nullable=False)
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    payload: Mapped[str]=mapped_column(Text,nullable=False)
    digest: Mapped[str]=mapped_column(String(64),nullable=False)
    reason: Mapped[str]=mapped_column(Text,nullable=False)
    manual: Mapped[bool]=mapped_column(Boolean,nullable=False)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    __table_args__=(UniqueConstraint('dispatch_id','version'),)

class AlcobreDispatchOperation(Scoped,Base):
    __tablename__='alcobre_dispatch_operations'
    id: Mapped[int]=mapped_column(primary_key=True)
    dispatch_id: Mapped[int]=mapped_column(ForeignKey('alcobre_dispatches.id'),nullable=False)
    revision_id: Mapped[int|None]=mapped_column(ForeignKey('alcobre_dispatch_revisions.id'))
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    kind: Mapped[str]=mapped_column(String(20),nullable=False)
    payload: Mapped[str]=mapped_column(Text,nullable=False)
    snapshot: Mapped[str]=mapped_column(Text,nullable=False)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    __table_args__=(CheckConstraint("kind IN ('prepare','confirm','cancel')",name='alcobre_dispatch_operation_kind'),)

class AlcobreDispatchEvent(Scoped,Base):
    __tablename__='alcobre_dispatch_events'
    id: Mapped[int]=mapped_column(primary_key=True)
    operation_id: Mapped[int|None]=mapped_column(ForeignKey('alcobre_dispatch_operations.id'))
    control_operation_id: Mapped[int|None]=mapped_column(ForeignKey('alcobre_inventory_operations.id'))
    local: Mapped[str|None]=mapped_column(String(120))
    bobine_id: Mapped[int]=mapped_column(ForeignKey('alcobre_bobines.id'),nullable=False)
    previous_id: Mapped[int|None]=mapped_column(ForeignKey('alcobre_dispatch_events.id'))
    state: Mapped[str]=mapped_column(String(20),nullable=False)
    __table_args__=(UniqueConstraint('operation_id','bobine_id'),UniqueConstraint('control_operation_id','bobine_id'),CheckConstraint('(operation_id IS NULL) <> (control_operation_id IS NULL)',name='alcobre_event_origin'),CheckConstraint("state IN ('available','reserved','shipped','blocked','missing')",name='alcobre_dispatch_event_state'))

class AlcobreDispatchDocument(Scoped,Base):
    __tablename__='alcobre_dispatch_documents'
    id: Mapped[int]=mapped_column(primary_key=True)
    revision_id: Mapped[int|None]=mapped_column(ForeignKey('alcobre_dispatch_revisions.id'),unique=True)
    operation_id: Mapped[int|None]=mapped_column(ForeignKey('alcobre_dispatch_operations.id'),unique=True)
    pdf: Mapped[bytes]=mapped_column(LargeBinary,nullable=False)
    digest: Mapped[str]=mapped_column(String(64),nullable=False)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    __table_args__=(CheckConstraint('(revision_id IS NULL) <> (operation_id IS NULL)',name='alcobre_dispatch_document_origin'),)

class AlcobreInventory(Scoped,Base):
    __tablename__='alcobre_inventories'
    id: Mapped[int]=mapped_column(primary_key=True)
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    title: Mapped[str]=mapped_column(String(120),nullable=False)
    mode: Mapped[str]=mapped_column(String(20),nullable=False)
    scope: Mapped[str]=mapped_column(Text,nullable=False)
    opening: Mapped[str]=mapped_column(Text,nullable=False)
    version: Mapped[int]=mapped_column(Integer,default=1,nullable=False)
    status: Mapped[str]=mapped_column(String(20),default='draft',nullable=False)
    created_by: Mapped[int]=mapped_column(ForeignKey('users.id'),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    __table_args__=(CheckConstraint("mode IN ('complete','partial') AND status IN ('draft','confirmed') AND version>0",name='alcobre_inventory_status'),)

class AlcobreInventoryCheck(Scoped,Base):
    __tablename__='alcobre_inventory_checks'
    id: Mapped[int]=mapped_column(primary_key=True)
    inventory_id: Mapped[int]=mapped_column(ForeignKey('alcobre_inventories.id'),nullable=False)
    bobine_id: Mapped[int|None]=mapped_column(ForeignKey('alcobre_bobines.id'))
    entry_id: Mapped[int|None]=mapped_column(ForeignKey('alcobre_entries.id'))
    number: Mapped[str|None]=mapped_column(String(120))
    local: Mapped[str|None]=mapped_column(String(120))
    result: Mapped[str]=mapped_column(String(20),nullable=False)
    snapshot: Mapped[str]=mapped_column(Text,nullable=False)
    evidence: Mapped[str]=mapped_column(Text,nullable=False)
    resolves_id: Mapped[int|None]=mapped_column(ForeignKey('alcobre_inventory_checks.id'),unique=True)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    __table_args__=(Index('ix_alcobre_inventory_check_unit','context_id','inventory_id','bobine_id','id'),Index('ix_alcobre_inventory_check_local','context_id','local','id'),CheckConstraint("result IN ('found','missing','elsewhere','unverified','excluded','empty','unknown','resolved')",name='alcobre_inventory_result'),)

class AlcobreInventoryReview(Scoped,Base):
    __tablename__='alcobre_inventory_reviews'
    id: Mapped[int]=mapped_column(primary_key=True)
    inventory_id: Mapped[int]=mapped_column(ForeignKey('alcobre_inventories.id'),nullable=False)
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    version: Mapped[int]=mapped_column(Integer,nullable=False)
    kind: Mapped[str]=mapped_column(String(20),nullable=False)
    payload: Mapped[str]=mapped_column(Text,nullable=False)
    digest: Mapped[str]=mapped_column(String(64),nullable=False)
    reason: Mapped[str]=mapped_column(Text,nullable=False)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    __table_args__=(CheckConstraint("kind IN ('conference','approve')",name='alcobre_inventory_review_kind'),)

class AlcobreInventoryOperation(Scoped,Base):
    __tablename__='alcobre_inventory_operations'
    id: Mapped[int]=mapped_column(primary_key=True)
    inventory_id: Mapped[int]=mapped_column(ForeignKey('alcobre_inventories.id'),unique=True,nullable=False)
    review_id: Mapped[int]=mapped_column(ForeignKey('alcobre_inventory_reviews.id'),unique=True,nullable=False)
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    payload: Mapped[str]=mapped_column(Text,nullable=False)
    snapshot: Mapped[str]=mapped_column(Text,nullable=False)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)

class AlcobreInventoryDocument(Scoped,Base):
    __tablename__='alcobre_inventory_documents'
    id: Mapped[int]=mapped_column(primary_key=True)
    review_id: Mapped[int|None]=mapped_column(ForeignKey('alcobre_inventory_reviews.id'),unique=True)
    operation_id: Mapped[int|None]=mapped_column(ForeignKey('alcobre_inventory_operations.id'),unique=True)
    pdf: Mapped[bytes]=mapped_column(LargeBinary,nullable=False)
    digest: Mapped[str]=mapped_column(String(64),nullable=False)
    actor: Mapped[str]=mapped_column(String(60),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    __table_args__=(CheckConstraint('(review_id IS NULL) <> (operation_id IS NULL)',name='alcobre_inventory_document_origin'),)


class PhysicalSharingReview(Base):
    __tablename__='physical_sharing_reviews'
    id: Mapped[int]=mapped_column(primary_key=True)
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    code: Mapped[str]=mapped_column(String(120),nullable=False)
    label: Mapped[str]=mapped_column(String(120),nullable=False)
    source_context_id: Mapped[int]=mapped_column(ForeignKey('storage_contexts.id'),nullable=False)
    other_context_id: Mapped[int]=mapped_column(ForeignKey('storage_contexts.id'),nullable=False)
    expected: Mapped[str]=mapped_column(Text,nullable=False)
    reason: Mapped[str]=mapped_column(Text,nullable=False)
    created_by: Mapped[int]=mapped_column(ForeignKey('users.id'),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    status: Mapped[str]=mapped_column(String(20),default='draft',nullable=False)
    confirmed_by: Mapped[int|None]=mapped_column(ForeignKey('users.id'))
    confirmed_at: Mapped[datetime|None]=mapped_column(DateTime)
    __table_args__=(CheckConstraint("status IN ('draft','confirmed')",name='physical_review_status'),)

class PhysicalLocation(Base):
    """One common rack identity/capacity; original exclusive claims remain historical."""
    __tablename__='physical_locations'
    id: Mapped[int]=mapped_column(primary_key=True)
    warehouse_id: Mapped[int]=mapped_column(ForeignKey('warehouses.id'),nullable=False)
    code: Mapped[str]=mapped_column(String(120),nullable=False)
    label: Mapped[str]=mapped_column(String(120),nullable=False)
    capacity: Mapped[int]=mapped_column(Integer,default=1,nullable=False)
    review_id: Mapped[int]=mapped_column(ForeignKey('physical_sharing_reviews.id'),unique=True,nullable=False)
    __table_args__=(UniqueConstraint('warehouse_id','code'),CheckConstraint('warehouse_id=1 AND capacity=1',name='physical_shared_capacity'),
        ForeignKeyConstraint(['warehouse_id','code'],['physical_location_claims.warehouse_id','physical_location_claims.code']))

class PhysicalLocationAccess(Base):
    __tablename__='physical_location_access'
    physical_id: Mapped[int]=mapped_column(ForeignKey('physical_locations.id'),primary_key=True)
    context_id: Mapped[int]=mapped_column(ForeignKey('storage_contexts.id'),primary_key=True)

class PhysicalLocationState(Base):
    """Read-only runtime projection; accounting stock alone never releases it."""
    __tablename__='physical_location_state'
    physical_id: Mapped[int]=mapped_column(ForeignKey('physical_locations.id'),primary_key=True)
    state: Mapped[str]=mapped_column(String(30),default='free',nullable=False)
    context_id: Mapped[int|None]=mapped_column(ForeignKey('storage_contexts.id'))
    stock_id: Mapped[int|None]=mapped_column(Integer)
    source_kind: Mapped[str|None]=mapped_column(String(30))
    source_id: Mapped[int|None]=mapped_column(Integer)
    previous: Mapped[str|None]=mapped_column(Text)
    version: Mapped[int]=mapped_column(Integer,default=0,nullable=False)
    __table_args__=(ForeignKeyConstraint(['context_id','stock_id'],['stock.context_id','stock.id']),
        CheckConstraint("state IN ('free','pending_putaway','pending_transfer','pending_inventory','occupied','awaiting_collection')",name='physical_state_valid'))

class PhysicalLocationEvent(Scoped,Base):
    __tablename__='physical_location_events'
    id: Mapped[int]=mapped_column(primary_key=True)
    physical_id: Mapped[int]=mapped_column(ForeignKey('physical_locations.id'),nullable=False,index=True)
    kind: Mapped[str]=mapped_column(String(30),nullable=False)
    ref_id: Mapped[int]=mapped_column(Integer,nullable=False)
    stock_id: Mapped[int|None]=mapped_column(ForeignKey('stock.id'))
    actor_id: Mapped[int]=mapped_column(ForeignKey('users.id'),nullable=False)
    reason: Mapped[str]=mapped_column(Text,nullable=False)
    before_state: Mapped[str]=mapped_column(Text,default='',server_default='',nullable=False)
    after_state: Mapped[str]=mapped_column(Text,default='',server_default='',nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    __table_args__=(UniqueConstraint('physical_id','kind','ref_id'),)

class CosmoReception(Scoped,Base):
    __tablename__='cosmo_receptions'
    id: Mapped[int]=mapped_column(primary_key=True)
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    source_name: Mapped[str]=mapped_column(String(240),nullable=False)
    source_sha256: Mapped[str]=mapped_column(String(64),nullable=False)
    source_excel: Mapped[bytes]=mapped_column(LargeBinary,nullable=False,deferred=True)
    report: Mapped[str]=mapped_column(Text,nullable=False,deferred=True)
    created_by: Mapped[int]=mapped_column(ForeignKey('users.id'),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    version: Mapped[int]=mapped_column(Integer,default=1,nullable=False)
    status: Mapped[str]=mapped_column(String(20),default='planned',nullable=False)
    guide: Mapped[str|None]=mapped_column(String(120))
    arrived_on: Mapped[datetime|None]=mapped_column(DateTime)
    legacy_code: Mapped[str|None]=mapped_column(String(120))
    unloaded_by: Mapped[int|None]=mapped_column(ForeignKey('users.id'))
    unloaded_at: Mapped[datetime|None]=mapped_column(DateTime)
    receipt_id: Mapped[int|None]=mapped_column(ForeignKey('receipts.id'),unique=True)
    __table_args__=(CheckConstraint("status IN ('planned','unloaded','reviewed','approved') AND version>0",name='cosmo_reception_valid'),)

class CosmoPallet(Scoped,Base):
    __tablename__='cosmo_pallets'
    id: Mapped[int]=mapped_column(primary_key=True)
    reception_id: Mapped[int]=mapped_column(ForeignKey('cosmo_receptions.id'),nullable=False,index=True)
    source_row: Mapped[int]=mapped_column(Integer,nullable=False)
    ordinal: Mapped[int]=mapped_column(Integer,nullable=False)
    article: Mapped[str]=mapped_column(String(120),nullable=False)
    description: Mapped[str]=mapped_column(String(240),nullable=False)
    lot: Mapped[str]=mapped_column(String(120),nullable=False)
    boxes: Mapped[int]=mapped_column(Integer,nullable=False)
    incomplete: Mapped[bool]=mapped_column(Boolean,nullable=False)
    __table_args__=(UniqueConstraint('reception_id','source_row','ordinal'),CheckConstraint('source_row>0 AND ordinal>0 AND boxes>=0',name='cosmo_pallet_valid'))

class CosmoState(Scoped,Base):
    __tablename__='cosmo_states'
    id: Mapped[int]=mapped_column(primary_key=True)
    pallet_id: Mapped[int]=mapped_column(ForeignKey('cosmo_pallets.id'),unique=True,nullable=False)
    status: Mapped[str]=mapped_column(String(20),default='planned',nullable=False)
    local: Mapped[str|None]=mapped_column(String(120))
    actual_boxes: Mapped[int|None]=mapped_column(Integer)
    decision: Mapped[str]=mapped_column(String(20),default='unresolved',nullable=False)
    stock_id: Mapped[int|None]=mapped_column(ForeignKey('stock.id'))
    version: Mapped[int]=mapped_column(Integer,default=0,nullable=False)
    __table_args__=(CheckConstraint("status IN ('planned','placed','missing','blocked','approved','shipped') AND decision IN ('unresolved','accept','exclude') AND version>=0 AND (actual_boxes IS NULL OR actual_boxes>=0)",name='cosmo_state_valid'),)

class CosmoOperation(Scoped,Base):
    __tablename__='cosmo_operations'
    id: Mapped[int]=mapped_column(primary_key=True)
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    pallet_id: Mapped[int]=mapped_column(ForeignKey('cosmo_pallets.id'),nullable=False,index=True)
    kind: Mapped[str]=mapped_column(String(20),nullable=False)
    expected_version: Mapped[int]=mapped_column(Integer,nullable=False)
    local: Mapped[str|None]=mapped_column(String(120))
    actual_boxes: Mapped[int|None]=mapped_column(Integer)
    decision: Mapped[str|None]=mapped_column(String(20))
    vacated: Mapped[bool]=mapped_column(Boolean,default=False,nullable=False)
    reason: Mapped[str]=mapped_column(Text,nullable=False)
    actor_id: Mapped[int]=mapped_column(ForeignKey('users.id'),nullable=False)
    stock_id: Mapped[int|None]=mapped_column(ForeignKey('stock.id'))
    receipt_id: Mapped[int|None]=mapped_column(ForeignKey('receipts.id'))
    before_state: Mapped[str]=mapped_column(Text,default='',nullable=False)
    after_state: Mapped[str]=mapped_column(Text,default='',nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    __table_args__=(CheckConstraint("kind IN ('place','missing','decide','approve')",name='cosmo_operation_valid'),)

class CosmoReview(Scoped,Base):
    __tablename__='cosmo_reviews'
    id: Mapped[int]=mapped_column(primary_key=True)
    reception_id: Mapped[int]=mapped_column(ForeignKey('cosmo_receptions.id'),nullable=False,index=True)
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    kind: Mapped[str]=mapped_column(String(20),nullable=False)
    expected_version: Mapped[int]=mapped_column(Integer,nullable=False)
    payload: Mapped[str]=mapped_column(Text,nullable=False)
    reason: Mapped[str]=mapped_column(Text,nullable=False)
    actor_id: Mapped[int]=mapped_column(ForeignKey('users.id'),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)
    __table_args__=(CheckConstraint("kind IN ('metadata','unloaded','review','approve')",name='cosmo_review_valid'),)

class CosmoPacking(Scoped,Base):
    __tablename__='cosmo_packings'
    id: Mapped[int]=mapped_column(primary_key=True)
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    source_name: Mapped[str]=mapped_column(String(240),nullable=False)
    source_sha256: Mapped[str]=mapped_column(String(64),nullable=False)
    source_excel: Mapped[bytes]=mapped_column(LargeBinary,nullable=False,deferred=True)
    report: Mapped[str]=mapped_column(Text,nullable=False,deferred=True)
    created_by: Mapped[int]=mapped_column(ForeignKey('users.id'),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)

class CosmoPackingState(Scoped,Base):
    __tablename__='cosmo_packing_states'
    id: Mapped[int]=mapped_column(primary_key=True)
    packing_id: Mapped[int]=mapped_column(ForeignKey('cosmo_packings.id'),unique=True,nullable=False)
    reference: Mapped[str|None]=mapped_column(String(120))
    reference_key: Mapped[str|None]=mapped_column(String(120),unique=True)
    stage: Mapped[str]=mapped_column(String(24),default='draft',nullable=False)
    version: Mapped[int]=mapped_column(Integer,default=0,nullable=False)
    mapping_id: Mapped[int|None]=mapped_column(Integer)
    loading_started: Mapped[bool]=mapped_column(Boolean,default=False,nullable=False)
    review_id: Mapped[int|None]=mapped_column(Integer)
    dispatched_on: Mapped[datetime|None]=mapped_column(DateTime)
    final_snapshot: Mapped[str|None]=mapped_column(Text)
    __table_args__=(CheckConstraint("stage IN ('draft','reviewed','preparing','prepared','loading','load_closed','dispatch_reviewed','shipped') AND version>=0",name='cosmo_packing_stage_valid'),)

class CosmoPackingLine(Scoped,Base):
    __tablename__='cosmo_packing_lines'
    id: Mapped[int]=mapped_column(primary_key=True)
    packing_id: Mapped[int]=mapped_column(ForeignKey('cosmo_packings.id'),nullable=False,index=True)
    mapping_id: Mapped[int]=mapped_column(Integer,nullable=False)
    source_row: Mapped[int]=mapped_column(Integer,nullable=False)
    ordinal: Mapped[int]=mapped_column(Integer,nullable=False)
    article: Mapped[str]=mapped_column(String(120),nullable=False)
    lot: Mapped[str]=mapped_column(String(120),nullable=False)
    boxes: Mapped[int]=mapped_column(Integer,nullable=False)
    __table_args__=(UniqueConstraint('mapping_id','source_row','ordinal'),CheckConstraint('source_row>0 AND ordinal>0 AND boxes>=0',name='cosmo_packing_line_valid'))

class CosmoPackingPick(Scoped,Base):
    __tablename__='cosmo_packing_picks'
    id: Mapped[int]=mapped_column(primary_key=True)
    packing_id: Mapped[int]=mapped_column(ForeignKey('cosmo_packings.id'),nullable=False,index=True)
    line_id: Mapped[int]=mapped_column(ForeignKey('cosmo_packing_lines.id'),unique=True,nullable=False)
    pallet_id: Mapped[int|None]=mapped_column(ForeignKey('cosmo_pallets.id'))
    active: Mapped[bool]=mapped_column(Boolean,default=True,nullable=False)
    prepared: Mapped[bool]=mapped_column(Boolean,default=False,nullable=False)
    loaded: Mapped[bool]=mapped_column(Boolean,default=False,nullable=False)
    reason: Mapped[str|None]=mapped_column(Text)
    prepared_by: Mapped[int|None]=mapped_column(ForeignKey('users.id'))
    prepared_at: Mapped[datetime|None]=mapped_column(DateTime)
    loaded_by: Mapped[int|None]=mapped_column(ForeignKey('users.id'))
    loaded_at: Mapped[datetime|None]=mapped_column(DateTime)
    version: Mapped[int]=mapped_column(Integer,default=0,nullable=False)
    __table_args__=(Index('uq_cosmo_active_pallet','context_id','pallet_id',unique=True,postgresql_where=text('active AND pallet_id IS NOT NULL'),sqlite_where=text('active AND pallet_id IS NOT NULL')),)

class CosmoPackingAction(Scoped,Base):
    __tablename__='cosmo_packing_actions'
    id: Mapped[int]=mapped_column(primary_key=True)
    packing_id: Mapped[int]=mapped_column(ForeignKey('cosmo_packings.id'),nullable=False,index=True)
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    kind: Mapped[str]=mapped_column(String(24),nullable=False)
    pick_id: Mapped[int|None]=mapped_column(ForeignKey('cosmo_packing_picks.id'))
    pallet_id: Mapped[int|None]=mapped_column(ForeignKey('cosmo_pallets.id'))
    expected_version: Mapped[int]=mapped_column(Integer,nullable=False)
    payload: Mapped[str]=mapped_column(Text,nullable=False)
    reason: Mapped[str]=mapped_column(Text,nullable=False)
    actor_id: Mapped[int]=mapped_column(ForeignKey('users.id'),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)

class CosmoPackingLedger(Scoped,Base):
    __tablename__='cosmo_packing_ledger'
    id: Mapped[int]=mapped_column(primary_key=True)
    action_id: Mapped[int]=mapped_column(ForeignKey('cosmo_packing_actions.id'),unique=True,nullable=False)
    before_state: Mapped[str]=mapped_column(Text,nullable=False)
    after_state: Mapped[str]=mapped_column(Text,nullable=False)

class CosmoPackingMove(Scoped,Base):
    __tablename__='cosmo_packing_moves'
    id: Mapped[int]=mapped_column(primary_key=True)
    action_id: Mapped[int]=mapped_column(ForeignKey('cosmo_packing_actions.id'),nullable=False)
    pallet_id: Mapped[int]=mapped_column(ForeignKey('cosmo_pallets.id'),nullable=False,index=True)
    before_state: Mapped[str]=mapped_column(Text,nullable=False)
    after_state: Mapped[str]=mapped_column(Text,nullable=False)
    __table_args__=(UniqueConstraint('action_id','pallet_id'),)

class CosmoPackingDocument(Scoped,Base):
    __tablename__='cosmo_packing_documents'
    id: Mapped[int]=mapped_column(primary_key=True)
    packing_id: Mapped[int]=mapped_column(ForeignKey('cosmo_packings.id'),nullable=False)
    request_key: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    kind: Mapped[str]=mapped_column(String(20),nullable=False)
    snapshot: Mapped[str]=mapped_column(Text,nullable=False)
    pdf: Mapped[bytes]=mapped_column(LargeBinary,nullable=False)
    digest: Mapped[str]=mapped_column(String(64),nullable=False)
    actor_id: Mapped[int]=mapped_column(ForeignKey('users.id'),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc,nullable=False)

class PhysicalStagingArea(Base):
    """Shared logical area of the existing physical engine; no capacity counter."""
    __tablename__='physical_staging_areas'
    id: Mapped[int]=mapped_column(primary_key=True)
    warehouse_id: Mapped[int]=mapped_column(ForeignKey('warehouses.id'),unique=True,nullable=False)
    label: Mapped[str]=mapped_column(String(120),nullable=False)

class PhysicalStagingPosition(Scoped,Base):
    __tablename__='physical_staging_positions'
    id: Mapped[int]=mapped_column(primary_key=True)
    area_id: Mapped[int]=mapped_column(ForeignKey('physical_staging_areas.id'),nullable=False)
    unit_kind: Mapped[str]=mapped_column(String(30),nullable=False)
    unit_id: Mapped[int]=mapped_column(Integer,nullable=False)
    group_kind: Mapped[str]=mapped_column(String(30),nullable=False)
    group_id: Mapped[int]=mapped_column(Integer,nullable=False)
    phase: Mapped[str]=mapped_column(String(20),nullable=False)
    version: Mapped[int]=mapped_column(Integer,nullable=False)
    __table_args__=(UniqueConstraint('unit_kind','unit_id'),CheckConstraint("phase IN ('staged','loading','rack','shipped') AND version>0",name='physical_staging_phase'),)

class PhysicalStagingEvent(Scoped,Base):
    __tablename__='physical_staging_events'
    id: Mapped[int]=mapped_column(primary_key=True)
    area_id: Mapped[int]=mapped_column(ForeignKey('physical_staging_areas.id'),nullable=False)
    unit_kind: Mapped[str]=mapped_column(String(30),nullable=False)
    unit_id: Mapped[int]=mapped_column(Integer,nullable=False)
    group_kind: Mapped[str]=mapped_column(String(30),nullable=False)
    group_id: Mapped[int]=mapped_column(Integer,nullable=False)
    phase: Mapped[str]=mapped_column(String(20),nullable=False)
    source_kind: Mapped[str]=mapped_column(String(30),nullable=False)
    source_id: Mapped[int]=mapped_column(Integer,nullable=False)
    before_state: Mapped[str]=mapped_column(Text,default='',nullable=False)
    after_state: Mapped[str]=mapped_column(Text,default='',nullable=False)
    __table_args__=(UniqueConstraint('source_kind','source_id','unit_kind','unit_id'),)

for _table in list(Base.metadata.tables.values()):
    if _table.name in ('physical_location_claims','physical_location_access','physical_location_state') or 'context_id' not in _table.c:
        continue
    for _unique in list(_table.constraints):
        if isinstance(_unique, UniqueConstraint) and 'context_id' not in _unique.columns:
            _cols = [c.name for c in _unique.columns]
            _table.constraints.remove(_unique)
            _table.append_constraint(UniqueConstraint('context_id', *_cols, name=_unique.name))
    _pk = [c.name for c in _table.primary_key.columns]
    if _table.name in ('app_config', 'location_clocks'):
        # These two legacy text keys may repeat between contexts.
        _table.c.context_id.primary_key = True
        _table.append_constraint(PrimaryKeyConstraint('context_id', *_pk))
    else:
        _table.append_constraint(UniqueConstraint('context_id', *_pk, name='uq_ctx_' + _table.name))
for _table in list(Base.metadata.tables.values()):
    if _table.name in ('physical_location_claims','physical_location_access','physical_location_state') or 'context_id' not in _table.c:
        continue
    for _fk in list(_table.foreign_key_constraints):
        if len(_fk.elements) != 1:
            continue
        _edge = _fk.elements[0]
        _target = _edge.column.table
        if 'context_id' in _target.c:
            _table.constraints.remove(_fk)
            _edge.parent.foreign_keys.discard(_edge)
            _table.foreign_keys.discard(_edge)
            _table.append_constraint(ForeignKeyConstraint(
                ['context_id', _edge.parent.name],
                [_target.name + '.context_id', _target.name + '.' + _edge.column.name],
                name='fk_ctx_' + _table.name + '_' + _edge.parent.name))


def context_id(db):
    value = db.info.get('ddn_context', 1)
    fixed = db.info.setdefault('ddn_context_fixed', value)
    if value != fixed or not isinstance(value, int) or value < 1:
        raise ValueError('O contexto da sessão é imutável. Abrir uma nova sessão.')
    return value

@event.listens_for(Session, 'do_orm_execute')
def _scope_queries(state):
    scope = context_id(state.session)
    if state.is_select or state.is_update or state.is_delete:
        state.statement = state.statement.options(with_loader_criteria(
            Scoped, lambda model: model.context_id == scope, include_aliases=True))

@event.listens_for(Session, 'before_flush')
def _scope_writes(db, flush_context, instances):
    scope = context_id(db)
    for record in db.new | db.dirty | db.deleted:
        if not isinstance(record, Scoped):
            continue
        if record in db.new and record.context_id is None:
            record.context_id = scope
        if record.context_id != scope or (record not in db.new and sa_inspect(record).attrs.context_id.history.has_changes()):
            raise ValueError('Registo fora do contexto ou tentativa de mudar o proprietário/armazém.')

# PostgreSQL enforces physical ownership with database triggers. Keep SQLite
# consultation fixtures consistent without enabling operational SQLite services.
@event.listens_for(Session, 'before_flush')
def _sqlite_physical_claims(db, flush_context, instances):
    if db.bind.dialect.name == 'sqlite':
        from .physical_locations import claim_sqlite
        for record in db.new | db.dirty:
            if isinstance(record, (Location, Stock)):
                claim_sqlite(db, record)
