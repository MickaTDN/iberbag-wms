"""Phase 2A uses fictitious owners, disposable PostgreSQL and existing services."""
import secrets
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select, text, func
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.exc import DBAPIError
from fastapi.testclient import TestClient
from iberbag import receiving, backup, transfer
from iberbag.database import Receipt, LoadNumber, StorageContext, Carga
from test_receiving import warehouse
from test_ddn_contexts import scoped, add_context
from test_transfer import docker_client, pg_databases
from conftest import login_data, BACKUP_PASSWORD
from test_security_regressions import restricted_web


def entry(engine, actor, scope=1, year=2027, key=None):
    with scoped(engine, scope) as db:
        ident = receiving.save_receipt(db, actor, key or secrets.token_hex(16), '', f'{year}-01-01', [('ART', 'LOT', '1')])
        receiving.confirm_receipt(db, actor, ident, 1)
        receiving.confirm_receipt(db, actor, ident, 1)
        return db.get(Receipt, ident).code


def test_owner_year_warehouse_numbering_and_history(warehouse):
    engine, accounts = warehouse; actor = accounts['admin'][0]
    other = add_context(engine, actor)
    with Session(engine) as db:
        ctx = StorageContext(client_id=1, warehouse_id=2, area='TESTE', active=True)
        db.add(ctx); db.commit(); second_warehouse = ctx.id
    assert entry(engine, actor) == '27-001'
    assert entry(engine, actor, other) == '27-001'
    assert entry(engine, actor, second_warehouse) == '27-002'
    assert entry(engine, actor, year=2028) == '28-001'
    with scoped(engine) as db:
        assert db.scalar(select(func.count()).select_from(LoadNumber)) == 4
        assert set(db.scalars(select(Carga.codigo))) == {'27-001', '28-001'}


def test_concurrent_numbering_retry_and_rollback(warehouse,monkeypatch):
    engine, accounts = warehouse; actor = accounts['operador'][0]
    with ThreadPoolExecutor(max_workers=4) as pool:
        codes = list(pool.map(lambda _: entry(engine, actor), range(4)))
    assert set(codes) == {'27-001','27-002','27-003','27-004'}
    with scoped(engine) as db:
        ident = receiving.save_receipt(db, actor, secrets.token_hex(16), '', '2027-01-01', [('ART','LOT','1')])
        with pytest.raises(receiving.ReceivingError):receiving.confirm_receipt(db, accounts['other'][0], ident, 1)
        assert db.scalar(select(func.count()).select_from(LoadNumber)) == 4
        from iberbag import commercial
        original=commercial.allocate_incoming
        def fail(*args):raise RuntimeError('Fictitious failure after allocation')
        monkeypatch.setattr(commercial,'allocate_incoming',fail)
        with pytest.raises(RuntimeError):receiving.confirm_receipt(db,actor,ident,1)
        assert db.scalar(select(func.count()).select_from(LoadNumber)) == 4
        monkeypatch.setattr(commercial,'allocate_incoming',original)
        receiving.confirm_receipt(db,actor,ident,1)
        assert db.get(Receipt,ident).code=='27-005'
    assert entry(engine, actor) == '27-006'
    for sql in ('DELETE FROM load_numbers','UPDATE load_numbers SET number=900','TRUNCATE load_numbers'):
        with engine.begin() as conn, pytest.raises(DBAPIError):conn.execute(text(sql))


def test_legacy_collision_all_warehouses_and_unchanged_references(warehouse):
    engine, accounts = warehouse; actor = accounts['admin'][0]
    with Session(engine) as db:
        ctx = StorageContext(client_id=1, warehouse_id=2, area='HISTÓRICO', active=True)
        db.add(ctx);db.commit(); scope=ctx.id
    with scoped(engine,scope) as db:
        db.add(Carga(codigo='27-001', entrada=None));db.commit()
    assert entry(engine,actor) == '27-002'
    with scoped(engine,scope) as db:assert db.scalar(select(Carga.codigo)) == '27-001'
    with scoped(engine) as db:
        ident=receiving.save_receipt(db,actor,secrets.token_hex(16),'ANTIGA','2026-01-01',[('A','L','1')])
        receiving.confirm_receipt(db,actor,ident,1)
        assert db.get(Receipt,ident).code == 'ANTIGA'


def test_numbering_backup_restore_preserves_next_number(warehouse,pg_databases,tmp_path):
    engine,accounts=warehouse;actor=accounts['admin'][0]
    assert entry(engine,actor) == '27-001'
    folder=tmp_path/'numbering'
    backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    target=pg_databases(restricted=True)
    assert backup.restore(target,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'
    assert entry(target,actor) == '27-002'


def test_office_navigation_context_roles_csrf(warehouse,monkeypatch):
    import iberbag.app as web
    engine,accounts=warehouse
    monkeypatch.setattr(web,'SessionLocal',sessionmaker(bind=engine,expire_on_commit=False))
    with TestClient(web.app) as client:
        client.post('/login',data=login_data(client,{'username':'operador','password':accounts['operador'][1]}))
        assert 'href="/receipts"' in client.get('/').text
        import re
        csrf=re.search('name="csrf" value="([^"]+)"',client.get('/receipts/new').text)[1]
        response=client.post('/receipts/review',data={'csrf':csrf,'context_id':1,
            'request_key':secrets.token_hex(16),'received_on':'2027-01-01',
            'artigo_0':'ART','lote_0':'LOT','quantidade_0':'1'},follow_redirects=False)
        assert response.status_code==303
        path=response.headers['location']
        assert 'name="code" value=""' in client.get(path+'/edit').text
        assert client.post(path+'/confirm',data={'csrf':csrf,'context_id':1,'version':1},follow_redirects=False).status_code==303
        assert '27-001' in client.get(path).text
        assert client.get('/settings/locations').status_code==403
        client.cookies.set('ddn_context','2')
        page=client.get('/').text
        assert 'Valmet' in page and 'Processo específico ainda não implementado' in page
        assert 'href="/receipts"' not in page
        assert client.post('/context',data={'selected_context':1,'context_id':2,'csrf':'invalid'}).status_code==403
        assert client.post('/receipts/review',data={'context_id':2,'csrf':'invalid'}).status_code==403


def test_prior_security_head_upgrades_without_rewriting_loads(pg_databases):
    from alembic import command
    from iberbag.migrations import config
    engine=pg_databases(restricted=True)
    with engine.begin() as conn:
        cfg=config();cfg.attributes['connection']=conn
        command.upgrade(cfg,'ddn_security_0001')
        conn.execute(text("INSERT INTO cargas(context_id,codigo,entrada) VALUES (1,'27-001',NULL)"))
        before=conn.execute(text('SELECT * FROM cargas')).all()
        command.upgrade(cfg,'head')
        assert conn.execute(text('SELECT * FROM cargas')).all()==before
        assert conn.scalar(text('SELECT count(*) FROM load_numbers'))==0


def test_sqlite_migration_preserves_references_but_refuses_stock_writes(tmp_path):
    from sqlalchemy import create_engine
    from alembic import command
    from iberbag.migrations import config
    engine=create_engine('sqlite:///'+str(tmp_path/'fictional.sqlite'))
    try:
        with engine.begin() as conn:
            cfg=config();cfg.attributes['connection']=conn
            command.upgrade(cfg,'ddn_security_0001')
            conn.execute(text("INSERT INTO cargas(context_id,codigo,entrada) VALUES (1,'HISTORICAL',NULL)"))
            before=conn.execute(text('SELECT * FROM cargas')).all()
            command.upgrade(cfg,'head')
            assert conn.execute(text('SELECT * FROM cargas')).all()==before
        with Session(engine) as db,pytest.raises(receiving.ReceivingError,match='PostgreSQL'):
            receiving.save_receipt(db,1,secrets.token_hex(16),'','2027-01-01',[('A','L','1')])
    finally:engine.dispose()


def test_restricted_runtime_numbers_but_cannot_rewrite_ledger(restricted_web):
    app,owner,runtime,role,accounts,tokens=restricted_web
    assert entry(runtime,accounts['operador'][0]) == '27-001'
    for sql in ('UPDATE load_numbers SET number=500','DELETE FROM load_numbers','TRUNCATE load_numbers'):
        with runtime.begin() as conn,pytest.raises(DBAPIError):conn.execute(text(sql))
    with runtime.connect() as conn:
        assert conn.scalar(text('SELECT count(*) FROM load_numbers'))==1
