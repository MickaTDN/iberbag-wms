from legacy_catalogue import seed_legacy_catalogue
import re
import secrets
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import select, text, func, event
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from iberbag import receiving as ops, backup
from iberbag.database import Base, Receipt, ReceiptLine, Putaway, User, Stock, Audit, Carga, Location, seed_warehouse_layout
from iberbag.migrations import config
from iberbag.security import set_password
from test_transfer import pg_databases, docker_client


@pytest.fixture
def warehouse(pg_databases):
    engine = pg_databases(restricted=True)
    with engine.begin() as conn:
        cfg = config(); cfg.attributes['connection'] = conn
        command.upgrade(cfg, 'head')
    accounts = {}
    with Session(engine) as db:
        seed_warehouse_layout(db)
        for role in ('admin', 'operador', 'other'):
            password = secrets.token_urlsafe(24)
            user = User(username=role, display_name=role, role='operador' if role == 'other' else role, active=True)
            set_password(user, password); db.add(user); db.flush()
            accounts[role] = (user.id, password)
        db.commit()
    return engine, accounts


def receive(engine, actor, qty=2, code=None):
    with Session(engine) as db:
        receipt = ops.save_receipt(db, actor, secrets.token_hex(16), code or secrets.token_hex(8), '2026-01-10', [('ART', 'LOT', str(qty))])
        ops.confirm_receipt(db, actor, receipt, 1)
        line = db.scalar(select(ReceiptLine.id).where(ReceiptLine.receipt_id == receipt))
        return receipt, line


def draft(engine, actor, line, local='Chão', qty=1):
    with Session(engine) as db:
        return ops.review_putaway(db, actor, secrets.token_hex(16), line, local, str(qty))


def test_reception_review_edit_confirm_and_duplicate(warehouse):
    engine, accounts = warehouse; actor = accounts['operador'][0]
    with Session(engine) as db:
        key = secrets.token_hex(16)
        ident = ops.save_receipt(db, actor, key, 'NOVA', '2026-01-10', [('A', 'L1', '2'), ('B', 'L2', '3')])
        assert ops.save_receipt(db, actor, key, 'NOVA', '2026-01-10', [('A', 'L1', '2'), ('B', 'L2', '3')]) == ident
        assert db.scalar(select(func.count()).select_from(Carga)) == 0
        ops.save_receipt(db, actor, key, 'NOVA', '2026-01-11', [('A', 'L', '4')], ident, 1)
        with pytest.raises(ops.ReceivingError): ops.confirm_receipt(db, actor, ident, 1)
        ops.confirm_receipt(db, actor, ident, 2)
        ops.confirm_receipt(db, actor, ident, 2)
        assert db.scalar(select(func.count()).select_from(Carga)) == 1
        assert db.scalar(select(func.count()).select_from(Stock)) == 0
        receipt = db.get(Receipt, ident)
        assert receipt.receiver == 'operador' and receipt.confirmed_at is not None
        assert db.get(Carga, receipt.carga_id).funcionario is None
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == 'CONFIRMAR_ENTRADA')) == 1
    with pytest.raises(ops.ReceivingError): receive(engine, actor, code='NOVA')


@pytest.mark.parametrize('quantity', ['0', '-1', '1.5', '1e2', '2147483648', ''])
def test_invalid_quantities(warehouse, quantity):
    engine, accounts = warehouse
    with Session(engine) as db, pytest.raises(ops.ReceivingError):
        ops.save_receipt(db, accounts['admin'][0], secrets.token_hex(16), 'BAD', '2026-01-10', [('A', 'L', quantity)])
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Receipt)) == 0


def test_pending_capacity_and_multiple_arrangers_on_floor(warehouse):
    engine, accounts = warehouse; actor = accounts['operador'][0]; other = accounts['other'][0]
    receipt, line = receive(engine, actor, 4)
    with Session(engine) as db:
        for local, qty in [('A101', '2'), ('A128', '1'), ('A999', '1'), ('(não indicado)', '1')]:
            with pytest.raises(ops.ReceivingError): ops.review_putaway(db, actor, secrets.token_hex(16), line, local, qty)
    first = draft(engine, actor, line, 'A101')
    with Session(engine) as db:
        ops.confirm_putaway(db, actor, first); ops.confirm_putaway(db, actor, first)
        assert ops.remaining(db, db.get(ReceiptLine, line)) == 3
        with pytest.raises(ops.ReceivingError): ops.review_putaway(db, other, secrets.token_hex(16), line, 'A101', '1')
    for uid, qty in [(actor, 1), (other, 2)]:
        ident = draft(engine, uid, line, 'Chão', qty)
        with Session(engine) as db: ops.confirm_putaway(db, uid, ident)
    with Session(engine) as db:
        assert ops.remaining(db, db.get(ReceiptLine, line)) == 0
        assert db.scalar(select(Stock.quantidade).where(Stock.local == 'Chão')) == 3
        events = db.scalars(select(Putaway).where(Putaway.local == 'Chão', Putaway.status == 'confirmed')).all()
        assert {e.arranger for e in events} == {'operador', 'other'}
        assert len({e.stock_id for e in events}) == 1
        assert all(e.confirmed_at is not None for e in events)


def test_occupied_legacy_position_and_removed_position(warehouse):
    engine, accounts = warehouse; actor = accounts['operador'][0]
    _, line = receive(engine, actor)
    with Session(engine) as db:
        db.add(Stock(artigo='OUTRO', lote='OUTRO', carga='LEGADO', local='A101', quantidade=1)); db.commit()
        with pytest.raises(ops.ReceivingError): ops.review_putaway(db, actor, secrets.token_hex(16), line, 'A101', '1')
    ident = draft(engine, actor, line, 'A102')
    from iberbag.warehouse_settings import remove_location
    with Session(engine) as db: remove_location(db, 'admin', 'A102')
    with Session(engine) as db, pytest.raises(ops.ReceivingError): ops.confirm_putaway(db, actor, ident)


def test_rollback_and_immutable_confirmations(warehouse):
    engine, accounts = warehouse; actor = accounts['operador'][0]
    receipt, line = receive(engine, actor)
    ident = draft(engine, actor, line)
    with Session(engine) as db:
        def fail(session): raise RuntimeError('injected commit failure')
        event.listen(db, 'before_commit', fail)
        with pytest.raises(RuntimeError): ops.confirm_putaway(db, actor, ident)
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Stock)) == 0
        assert db.get(Putaway, ident).status == 'draft'
        ops.confirm_putaway(db, actor, ident)
    for sql in [f'DELETE FROM receipts WHERE id={receipt}', f'UPDATE receipt_lines SET quantidade=99 WHERE id={line}',
                f'DELETE FROM putaways WHERE id={ident}']:
        with engine.begin() as conn, pytest.raises(IntegrityError): conn.execute(text(sql))


@pytest.mark.parametrize('scenario', ['same_position', 'same_line', 'same_confirmation'])
def test_atomic_racing_confirmations(warehouse, scenario):
    engine, accounts = warehouse; actor = accounts['operador'][0]; other = accounts['other'][0]
    _, line = receive(engine, actor, 1)
    first = draft(engine, actor, line, 'A101')
    if scenario == 'same_position':
        _, second_line = receive(engine, other, 1)
        second = draft(engine, other, second_line, 'A101')
    elif scenario == 'same_line': second = draft(engine, other, line, 'A102')
    else: second, other = first, actor
    barrier = Barrier(2)
    def run(uid, ident):
        barrier.wait(timeout=5)
        try:
            with Session(engine) as db: ops.confirm_putaway(db, uid, ident)
            return 'ok'
        except ops.ReceivingError: return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, uid, ident) for uid, ident in [(actor, first), (other, second)]]
        outcomes = [future.result(timeout=15) for future in futures]
    assert outcomes.count('ok') == (2 if scenario == 'same_confirmation' else 1)
    with Session(engine) as db:
        assert db.scalar(select(func.sum(Stock.quantidade))) == 1
        assert db.scalar(select(func.count()).select_from(Putaway).where(Putaway.status == 'confirmed')) == 1


def test_ui_permissions_and_stock_traceability(warehouse):
    engine, accounts = warehouse
    from iberbag.app import app, database
    def session():
        with Session(engine, expire_on_commit=False) as db: yield db
    app.dependency_overrides[database] = session
    def field(response, name):
        return re.search(r'name="' + name + r'" value="([^"]+)"', response.text).group(1)
    try:
        with TestClient(app) as client, TestClient(app) as other:
            assert client.get('/receipts', follow_redirects=False).status_code == 303
            for c, role in [(client, 'operador'), (other, 'other')]:
                assert c.post('/login', data={'username': role, 'password': accounts[role][1]}, follow_redirects=False).status_code == 303
            form = client.get('/receipts/new'); csrf = field(form, 'csrf')
            data = dict(csrf=csrf, request_key=field(form, 'request_key'), code='UI-CARGA', received_on='2026-01-10', artigo_0='UI-ART', lote_0='UI-LOT', quantidade_0='2')
            response = client.post('/receipts/review', data=data)
            assert 'Rever entrada' in response.text
            url = str(response.url).split('testserver')[1]
            assert client.post(url + '/confirm', data={}, headers={'content-type': 'application/x-www-form-urlencoded'}).status_code == 403
            assert other.post(url + '/confirm', data={'csrf': field(other.get('/receipts/new'), 'csrf'), 'version': '1'}).status_code == 403
            assert client.post(url + '/confirm', data={'csrf': csrf, 'version': '1'}).status_code == 200
            assert 'UI-ART' in client.get('/putaway').text
            page = client.get('/putaway')
            data = dict(csrf=csrf, request_key=field(page, 'request_key'), line_id=field(page, 'line_id'), local='Chão', quantidade='2')
            reviewed = client.post('/putaway/review', data=data)
            assert 'Rever arrumação' in reviewed.text
            url = str(reviewed.url).split('testserver')[1]
            confirmed = client.post(url + '/confirm', data={'csrf': csrf})
            assert 'Arrumação confirmada' in confirmed.text
            with Session(engine) as db: stock_id = db.scalar(select(Stock.id))
            stock = client.get('/stock?artigo=UI-ART').text
            assert 'UI-CARGA' not in stock and 'UI-LOT' in stock
            detail = client.get(f'/stock/{stock_id}').text
            assert 'UI-CARGA' in detail and 'operador' in detail and '10/01/2026' in detail
            assert client.get('/settings').status_code == 403
            assert client.post(url + '/confirm', data={'csrf': csrf}).status_code == 200
    finally: app.dependency_overrides.clear()


def test_verified_upgrade_preserves_legacy_data(warehouse, pg_databases, tmp_path):
    # A genuine 0001 schema, with no automatic startup upgrade.
    legacy, restored = pg_databases(), pg_databases()
    with legacy.begin() as conn:
        cfg = config(); cfg.attributes['connection'] = conn; command.upgrade(cfg, '0001')
    with Session(legacy) as db:
        seed_legacy_catalogue(db)
        db.execute(text("INSERT INTO cargas(codigo,entrada,funcionario) VALUES ('LEGADO-FICTICIO',NULL,NULL)"))
        db.execute(text("INSERT INTO stock(artigo,lote,carga,local,quantidade) VALUES ('LEGADO','LOTE','LEGADO-FICTICIO','A101',2)"))
        db.execute(text("INSERT INTO audit(occurred_at,username,action,details) VALUES (CURRENT_TIMESTAMP,'ficticio','HISTORICO','Preservar sem inventar dados.')"))
        db.commit()
    from iberbag.schema_upgrade import preview, apply_upgrade
    assert preview(legacy)['from'] == '0001'
    folder = tmp_path / 'backup'
    backup.backup(legacy, folder, runner=docker_client)
    with pytest.raises(ValueError, match='restauro'): apply_upgrade(legacy, folder)
    backup.restore(restored, folder, runner=docker_client)
    assert apply_upgrade(legacy, folder)['status'] == 'upgraded'
    assert apply_upgrade(legacy, folder)['status'] == 'already_current'
    assert preview(legacy)['data'] == preview(restored)['data']


def test_permissions_disabled_user_and_draft_owner(warehouse):
    engine, accounts = warehouse; actor = accounts['operador'][0]; other = accounts['other'][0]
    with Session(engine) as db:
        ident = ops.save_receipt(db, actor, secrets.token_hex(16), 'DRAFT', '2026-01-10', [('A', 'L', '1')])
        with pytest.raises(ops.ReceivingError) as error: ops.confirm_receipt(db, other, ident, 1)
        assert error.value.status == 403
        db.get(User, actor).active = False; db.commit()
        with pytest.raises(ops.ReceivingError) as error: ops.confirm_receipt(db, actor, ident, 1)
        assert error.value.status == 403
        assert db.get(Receipt, ident).status == 'draft'


def test_sqlite_operational_writes_are_refused(tmp_path):
    from sqlalchemy import create_engine
    engine = create_engine('sqlite:///' + str(tmp_path / 'closed.db'))
    try:
        with Session(engine) as db, pytest.raises(ops.ReceivingError) as error:
            ops.save_receipt(db, 1, secrets.token_hex(16), 'TEST', '2026-01-10', [('A', 'L', '1')])
        assert error.value.status == 503
    finally: engine.dispose()


@pytest.mark.parametrize('code,day,rows', [('', '2026-01-10', [('A','L','1')]), ('A','invalid',[('A','L','1')]),
    ('A','2026-01-10',[]), ('A','2026-01-10',[('A','','1')]), ('A','2026-01-10',[('A','L','1'),('A','L','2')])])
def test_invalid_reception_fields(code, day, rows):
    with pytest.raises(ops.ReceivingError): ops.receipt_payload(code, day, rows)


def test_confirmed_history_backup_restore_and_legacy_upgrade_data(warehouse, pg_databases, tmp_path):
    engine, accounts = warehouse
    actor = accounts['admin'][0]
    receipt, line = receive(engine, actor, 2)
    ident = draft(engine, actor, line, 'Chão', 2)
    with Session(engine) as db: ops.confirm_putaway(db, actor, ident)
    folder = tmp_path / 'operational-backup'
    backup.backup(engine, folder, runner=docker_client)
    restored = pg_databases(restricted=True)
    assert backup.restore(restored, folder, runner=docker_client)['status'] == 'reconciled'
    with Session(restored) as db:
        assert db.get(Putaway, ident).arranger == 'admin'
        assert ops.remaining(db, db.get(ReceiptLine, line)) == 0
    with restored.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(text('DELETE FROM putaways'))


def test_catalogue_deletion_racing_putaway_cannot_remove_occupied_position(warehouse):
    engine, accounts = warehouse; actor = accounts['operador'][0]
    _, line = receive(engine, actor, 1)
    ident = draft(engine, actor, line, 'A101')
    from iberbag.warehouse_settings import remove_location, WarehouseError
    barrier = Barrier(2)
    def place():
        barrier.wait(timeout=5)
        try:
            with Session(engine) as db: ops.confirm_putaway(db, actor, ident)
            return True
        except ops.ReceivingError: return False
    def remove():
        barrier.wait(timeout=5)
        try:
            with Session(engine) as db: remove_location(db, 'admin', 'A101')
            return True
        except WarehouseError: return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(place), pool.submit(remove)
        assert a.result(timeout=15) != b.result(timeout=15)
    with Session(engine) as db:
        quantity = db.scalar(select(func.coalesce(func.sum(Stock.quantidade), 0)).where(Stock.local == 'A101'))
        exists = db.scalar(select(Location.id).where(Location.codigo == 'A101'))
        assert quantity == 0 or exists is not None


def test_test_transfer_preserves_operational_history(warehouse, pg_databases, tmp_path):
    from sqlalchemy import create_engine
    from iberbag import transfer
    engine, accounts = warehouse; actor = accounts['operador'][0]
    receipt, line = receive(engine, actor, 1)
    ident = draft(engine, actor, line, 'A101')
    with Session(engine) as db: ops.confirm_putaway(db, actor, ident)
    with engine.connect() as conn: data = transfer.read_data(conn)
    source = tmp_path / 'operational.sqlite'
    sqlite = create_engine('sqlite:///' + str(source))
    try:
        Base.metadata.create_all(sqlite)
        with sqlite.begin() as conn:
            for table in transfer.TABLES:
                if data[table.name]: conn.execute(table.insert(), data[table.name])
    finally: sqlite.dispose()
    bundle = tmp_path / 'operational-preview'
    transfer.preview(source, bundle)
    destination = pg_databases(restricted=True)
    assert transfer.apply(bundle, destination)['status'] == 'reconciled'
    assert transfer.reconcile(bundle, destination)['different_tables'] == []
    with Session(destination) as db:
        assert db.get(Receipt, receipt).receiver == 'operador'
        assert db.get(Putaway, ident).arranger == 'operador'


def test_legacy_floor_spelling_is_preserved(warehouse):
    engine, accounts = warehouse; actor = accounts['operador'][0]
    with Session(engine) as db:
        db.scalar(select(Location).where(Location.codigo == 'Chão')).codigo = 'Chao'; db.commit()
    _, line = receive(engine, actor, 3)
    key = secrets.token_hex(16)
    with Session(engine) as db:
        ident = ops.review_putaway(db, actor, key, line, 'Chão', '3')
        assert ops.review_putaway(db, actor, key, line, 'Chão', '3') == ident
        ops.confirm_putaway(db, actor, ident)
        assert db.scalar(select(Stock.local)) == 'Chao'
        assert db.scalar(select(Stock.quantidade)) == 3
