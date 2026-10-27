"""Database models and transactions. SQLite for local tests, PostgreSQL for shared LAN."""
import os
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select, text, create_engine, String, Integer, DateTime, Boolean, BigInteger, LargeBinary, ForeignKey, CheckConstraint, UniqueConstraint, Index, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, relationship

ROOT = Path(__file__).resolve().parent.parent
DATABASE_URL = os.getenv('WMS_DATABASE_URL', f'sqlite:///{ROOT / "iberbag-dev.db"}')
params = {'connect_args': {'check_same_thread': False, 'timeout': 30}} if DATABASE_URL.startswith('sqlite') else {'pool_pre_ping': True, 'pool_size': 10, 'max_overflow': 10}
engine = create_engine(DATABASE_URL, future=True, **params)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class Base(DeclarativeBase):
    pass

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

class Stock(Base):
    __tablename__ = 'stock'
    id: Mapped[int] = mapped_column(primary_key=True)
    artigo: Mapped[str] = mapped_column(String(120), nullable=False)
    lote: Mapped[str] = mapped_column(String(120), nullable=False)
    carga: Mapped[str] = mapped_column(String(120), nullable=False)
    local: Mapped[str] = mapped_column(String(120), nullable=False)
    quantidade: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (
        UniqueConstraint('artigo', 'lote', 'carga', 'local', name='stock_key_unique'),
        CheckConstraint('quantidade >= 0', name='stock_nonnegative'),
        Index('ix_stock_article_lot_location', 'artigo', 'lote', 'local'),
    )

class Carga(Base):
    __tablename__ = 'cargas'
    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    entrada: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    funcionario: Mapped[str | None] = mapped_column(String(120), nullable=True)

class Location(Base):
    __tablename__ = 'locations'
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text('true'), nullable=False)
    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    capacidade: Mapped[int | None] = mapped_column(Integer, nullable=True)
    __table_args__ = (CheckConstraint('capacidade IS NULL OR capacidade > 0', name='positive_capacity'),)

class Corridor(Base):
    __tablename__ = 'corridors'
    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(1), unique=True, nullable=False)
    andares: Mapped[int] = mapped_column(Integer, nullable=False)
    posicoes: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (
        CheckConstraint('andares BETWEEN 1 AND 9', name='valid_corridor_floors'),
        CheckConstraint('posicoes BETWEEN 1 AND 99', name='valid_corridor_slots'),
    )

class AppConfig(Base):
    __tablename__ = 'app_config'
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(String(200), nullable=False)

class Audit(Base):
    __tablename__ = 'audit'
    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)
    username: Mapped[str] = mapped_column(String(60), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False, default='')

class ImportBatch(Base):
    __tablename__ = 'imports'
    id: Mapped[int] = mapped_column(primary_key=True)
    source_sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)
    pallets: Mapped[int] = mapped_column(Integer, nullable=False)
    groups: Mapped[int] = mapped_column(Integer, nullable=False)


def sync_location_policy(db):
    """Idempotent policy migration for existing v2 databases. Does not move stock."""
    from .location_rules import FLOOR_LABEL, capacity_for, is_floor
    known = db.scalars(select(Location)).all()
    if not any(is_floor(loc.codigo) for loc in known):
        db.add(Location(codigo=FLOOR_LABEL, capacidade=None))
    changed = 0
    for loc in known:
        target = capacity_for(loc.codigo)
        if loc.capacidade != target:
            loc.capacidade = target
            changed += 1
    if changed:
        db.add(Audit(username='sistema', action='REGRA_CAPACIDADE',
                     details=f'Política automática: posições físicas = 1 palete; Chão ilimitado. {changed} localizações atualizadas.'))
    db.commit()
    return changed


def seed_warehouse_layout(db):
    """Import the VBA's *theoretical* warehouse map only once; preserve edits.

    A-S: 27 slots per level; 4 levels, except L and M with 5.
    Existing stock is never modified, moved or deleted.
    """
    from .location_rules import FLOOR_LABEL
    if db.get(AppConfig, 'warehouse_layout_seeded_vba'):
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
        sync_location_policy(db)
        seed_warehouse_layout(db)


class Receipt(Base):
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


class ReceiptLine(Base):
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


class Putaway(Base):
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
        CheckConstraint("status IN ('draft','confirmed')", name='putaway_status_valid'),
        CheckConstraint("(status = 'draft' AND stock_id IS NULL AND arranger IS NULL AND confirmed_at IS NULL) OR (status = 'confirmed' AND stock_id IS NOT NULL AND arranger IS NOT NULL AND confirmed_at IS NOT NULL)", name='putaway_confirmation_valid'),
    )


class DispatchOrder(Base):
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


class DispatchLine(Base):
    __tablename__ = 'dispatch_lines'
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('dispatch_orders.id'), nullable=False, index=True)
    artigo: Mapped[str] = mapped_column(String(120), nullable=False)
    lote: Mapped[str] = mapped_column(String(120), nullable=False)
    quantidade: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (
        UniqueConstraint('order_id', 'artigo', 'lote', name='dispatch_line_unique'),
        CheckConstraint('quantidade > 0', name='dispatch_line_positive'),
    )


class DispatchPick(Base):
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


class StockTransfer(Base):
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


class LotResolution(Base):
    __tablename__ = 'lot_resolutions'
    id: Mapped[int] = mapped_column(primary_key=True)
    lote: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    artigo: Mapped[str] = mapped_column(String(120), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(60), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)


class DispatchDocument(Base):
    __tablename__ = 'dispatch_documents'
    order_id: Mapped[int] = mapped_column(ForeignKey('dispatch_orders.id'), primary_key=True)
    pdf: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    stock_versions: Mapped[str] = mapped_column(Text, nullable=False)


class PreparationCompletion(Base):
    __tablename__ = 'preparation_completions'
    order_id: Mapped[int] = mapped_column(ForeignKey('dispatch_orders.id'), primary_key=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(60), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)


class LocationClock(Base):
    __tablename__ = 'location_clocks'
    local: Mapped[str] = mapped_column(String(120), primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class PhysicalInventory(Base):
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


class InventoryScope(Base):
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


class InventoryCount(Base):
    __tablename__ = 'inventory_counts'
    id: Mapped[int] = mapped_column(primary_key=True)
    scope_id: Mapped[int] = mapped_column(ForeignKey('inventory_scopes.id'), nullable=False, index=True)
    artigo: Mapped[str | None] = mapped_column(String(120))
    lote: Mapped[str | None] = mapped_column(String(120))
    carga: Mapped[str | None] = mapped_column(String(120))
    quantidade: Mapped[int] = mapped_column(Integer, nullable=False)
    pending: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    __table_args__ = (CheckConstraint('quantidade > 0', name='inventory_count_positive'),)


class StockIncident(Base):
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


class StockAdjustment(Base):
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


class PreparationCompensation(Base):
    __tablename__ = 'preparation_compensations'
    order_id: Mapped[int] = mapped_column(ForeignKey('dispatch_orders.id'), primary_key=True)
    inventory_id: Mapped[int] = mapped_column(ForeignKey('physical_inventories.id'), nullable=False, unique=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(60), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)
