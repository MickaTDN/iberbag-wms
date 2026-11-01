"""Independent audit regressions; only synthetic disposable PostgreSQL databases."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import secrets
import time
from urllib.parse import urlencode

import httpx
from fastapi.testclient import TestClient
from psycopg import sql
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from iberbag.database import User, StorageContext
from iberbag.privileges import grant_runtime, validate_runtime
from iberbag.security import new_session
from test_receiving import warehouse
from test_transfer import pg_databases

LOCK = 72834001


@pytest.fixture
def restricted_web(warehouse, monkeypatch):
    import iberbag.app as web
    owner, accounts = warehouse
    admin = create_engine(make_url(os.environ['WMS_TEST_POSTGRES_URL']).set(database=owner.url.database))
    role, password = 'runtime_' + secrets.token_hex(6), secrets.token_urlsafe(32)
    with admin.begin() as connection:
        connection.exec_driver_sql(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(
            sql.Identifier(role), sql.Literal(password)).as_string())
    runtime = None
    try:
        with owner.begin() as connection:
            grant_runtime(connection, role, True)
        runtime = create_engine(owner.url.set(username=role, password=password))
        with runtime.connect() as connection:
            validate_runtime(connection)
        monkeypatch.setattr(web, 'SessionLocal', sessionmaker(bind=runtime, expire_on_commit=False))
        tokens = {}
        with Session(owner) as db:
            for name in ('admin', 'operador'):
                token = new_session(db, db.get(User, accounts[name][0]))
                from iberbag.security import get_session
                db.flush()
                tokens[name] = (token, get_session(db, token).csrf_token)
            db.commit()
        yield web.app, owner, runtime, role, accounts, tokens
    finally:
        if runtime:
            runtime.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(sql.SQL('DROP OWNED BY {} CASCADE').format(sql.Identifier(role)).as_string())
            connection.exec_driver_sql(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)).as_string())
        admin.dispose()


@pytest.mark.parametrize('case', ['anonymous', 'admin', 'operator', 'csrf', 'context'])
def test_slow_body_never_holds_global_lock(restricted_web, case):
    app, owner, runtime, role, accounts, tokens = restricted_web

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        credentials = tokens['operador' if case == 'operator' else 'admin']
        data = {'csrf': credentials[1] if case != 'csrf' else secrets.token_hex(16),
                'context_id': '2' if case == 'context' else '1',
                'name': 'ARMAZÉM FICTÍCIO ' + secrets.token_hex(5)}
        async def slow():
            entered.set()
            await release.wait()
            yield urlencode(data).encode()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            if case != 'anonymous':
                client.cookies.set('wms_session', credentials[0])
            task = asyncio.create_task(client.post('/settings/warehouses',
                headers={'Content-Type': 'application/x-www-form-urlencoded'}, content=slow()))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                with owner.begin() as connection:
                    available = connection.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': LOCK})
            finally:
                release.set()
                response = await asyncio.wait_for(task, 3)
            assert available, 'An unfinished HTTP body must not retain the global write lock.'
            assert response.status_code == {'admin': 303, 'context': 409}.get(case, 403)
        assert runtime.pool.checkedout() == 0
    asyncio.run(scenario())


@pytest.mark.parametrize('case', ['anonymous', 'csrf', 'operator', 'context'])
def test_rejected_post_does_not_wait_for_write_lock(restricted_web, case):
    app, owner, runtime, role, accounts, tokens = restricted_web
    token, csrf = tokens['operador' if case == 'operator' else 'admin']
    def post():
        with TestClient(app) as client:
            if case != 'anonymous': client.cookies.set('wms_session', token)
            return client.post('/settings/warehouses', data={
                'csrf': secrets.token_hex(16) if case == 'csrf' else csrf,
                'context_id': '2' if case == 'context' else '1', 'name': 'NUNCA CRIAR'}, follow_redirects=False)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with owner.begin() as connection:
            connection.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': LOCK})
            future = pool.submit(post)
            time.sleep(.2)
            deadline = time.monotonic() + 2
            while not future.done() and time.monotonic() < deadline: time.sleep(.02)
            completed_before_release = future.done()
        response = future.result(timeout=5)
    assert completed_before_release, 'Rejected requests must not queue behind an authorized writer.'
    assert response.status_code == (409 if case == 'context' else 403)


@pytest.mark.parametrize('case', ['database', 'public_database', 'schema_create', 'system_schema_create', 'schema_owner', 'table_owner', 'function_owner', 'type_owner', 'public_create', 'public_audit_delete', 'grant_option'])
def test_runtime_rejects_privileges_outside_current_schema(restricted_web, case):
    app, owner, runtime, role, accounts, tokens = restricted_web
    administrator = create_engine(make_url(os.environ['WMS_TEST_POSTGRES_URL']).set(database=owner.url.database))
    try:
        with administrator.begin() as connection:
            connection.exec_driver_sql('CREATE SCHEMA audit_extra')
            if case in ('database','public_database'):
                statement = sql.SQL('GRANT CREATE ON DATABASE {} TO {}').format(sql.Identifier(owner.url.database),
                    sql.SQL('PUBLIC') if case=='public_database' else sql.Identifier(role))
            elif case in ('schema_create', 'public_create'):
                statement = sql.SQL('GRANT CREATE ON SCHEMA audit_extra TO {}').format(
                    sql.SQL('PUBLIC') if case == 'public_create' else sql.Identifier(role))
            elif case == 'system_schema_create':
                statement = sql.SQL('GRANT CREATE ON SCHEMA pg_catalog TO {}').format(sql.Identifier(role))
            elif case == 'schema_owner':
                statement = sql.SQL('ALTER SCHEMA audit_extra OWNER TO {}').format(sql.Identifier(role))
            elif case == 'table_owner':
                connection.exec_driver_sql('CREATE TABLE audit_extra.synthetic(id integer)')
                statement = sql.SQL('ALTER TABLE audit_extra.synthetic OWNER TO {}').format(sql.Identifier(role))
            elif case == 'function_owner':
                connection.exec_driver_sql('CREATE FUNCTION audit_extra.synthetic() RETURNS integer LANGUAGE sql AS $$SELECT 1$$')
                statement = sql.SQL('ALTER FUNCTION audit_extra.synthetic() OWNER TO {}').format(sql.Identifier(role))
            elif case == 'type_owner':
                connection.exec_driver_sql("CREATE TYPE audit_extra.synthetic AS ENUM ('fictitious')")
                statement = sql.SQL('ALTER TYPE audit_extra.synthetic OWNER TO {}').format(sql.Identifier(role))
            elif case == 'public_audit_delete':
                statement = sql.SQL('GRANT DELETE ON audit TO PUBLIC')
            else:
                statement = sql.SQL('GRANT SELECT ON stock TO {} WITH GRANT OPTION').format(sql.Identifier(role))
            connection.exec_driver_sql(statement.as_string())
        with runtime.connect() as connection, pytest.raises(ValueError):
            validate_runtime(connection)
    finally:
        administrator.dispose()


@pytest.mark.parametrize('change', ['role', 'session', 'context'])
def test_write_revalidates_after_waiting_for_lock(restricted_web, change):
    from iberbag.database import LoginSession, Warehouse
    app, owner, runtime, role, accounts, tokens = restricted_web
    token, csrf = tokens['admin']
    name = 'NÃO CRIAR ' + secrets.token_hex(6)
    def post():
        with TestClient(app) as client:
            client.cookies.set('wms_session', token)
            return client.post('/settings/warehouses', data={'csrf': csrf,'context_id':1,'name':name}, follow_redirects=False)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with owner.begin() as connection:
            connection.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': LOCK})
            future = pool.submit(post)
            deadline = time.monotonic() + 4
            waiting = False
            while time.monotonic() < deadline:
                waiting = bool(connection.scalar(text('''SELECT count(*) FROM pg_locks l
                    JOIN pg_stat_activity a ON a.pid=l.pid
                    WHERE a.usename=:role AND l.locktype='advisory' AND NOT l.granted'''), {'role':role}))
                if waiting: break
                time.sleep(.02)
            assert waiting, 'The authorized request must wait for the writer lock.'
            if change == 'role':
                connection.execute(User.__table__.update().where(User.id == accounts['admin'][0]).values(role='operador'))
            elif change == 'session':
                connection.execute(LoginSession.__table__.delete().where(LoginSession.user_id == accounts['admin'][0]))
            else:
                connection.execute(StorageContext.__table__.update().where(StorageContext.id == 1).values(active=False))
        response = future.result(timeout=5)
    assert response.status_code == (409 if change == 'context' else 403)
    with Session(owner) as db:
        assert db.scalar(select(Warehouse.id).where(Warehouse.name == name)) is None
    assert runtime.pool.checkedout() == 0


def test_every_post_rejects_bad_csrf_without_waiting_for_lock(restricted_web):
    import re
    app, owner, runtime, role, accounts, tokens = restricted_web
    # OpenAPI traverses included routers too; app.routes may contain lazy routers.
    paths = [re.sub(r'\{[^}]+\}', '1', path) for path,methods in app.openapi()['paths'].items() if 'post' in methods]
    assert '/receipts/review' in paths and '/inventory/1/approve' in paths
    def post(path):
        with TestClient(app) as client:
            client.cookies.set('wms_session', tokens['admin'][0])
            return client.post(path, data={'csrf':secrets.token_hex(16),'context_id':1}, follow_redirects=False)
    with ThreadPoolExecutor(max_workers=1) as pool:
        for path in paths:
            with owner.begin() as connection:
                connection.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key':LOCK})
                future = pool.submit(post,path)
                deadline = time.monotonic() + 2
                while not future.done() and time.monotonic() < deadline: time.sleep(.01)
                finished = future.done()
            response = future.result(timeout=5)
            assert finished, path
            assert response.status_code == 403, path


@pytest.mark.parametrize('scenario', ['cancel','disconnect','timeout','size'])
def test_incomplete_body_timeout_cancel_and_disconnect_release_resources(restricted_web, scenario):
    from iberbag.http_body import BoundedPostBody
    app, owner, runtime, role, accounts, tokens = restricted_web
    async def run():
        entered = asyncio.Event()
        async def slow():
            entered.set()
            await asyncio.Event().wait()
            yield b''
        wrapped = BoundedPostBody(app, timeout=.05)
        if scenario == 'disconnect':
            messages = iter([{'type':'http.request','body':b'csrf=','more_body':True},{'type':'http.disconnect'}])
            async def receive(): return next(messages)
            async def send(message): pass
            await wrapped({'type':'http','method':'POST','path':'/settings/warehouses','headers':[]},receive,send)
        else:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=wrapped),base_url='http://testserver') as client:
                client.cookies.set('wms_session', tokens['admin'][0])
                if scenario == 'size':
                    response = await client.post('/receipts/review',content=b'x'*65537)
                    assert response.status_code == 413
                else:
                    task = asyncio.create_task(client.post('/settings/warehouses',content=slow(),
                            headers={'Content-Type':'application/x-www-form-urlencoded'}))
                    await asyncio.wait_for(entered.wait(), 1)
                    if scenario == 'cancel':
                        task.cancel()
                        with pytest.raises(asyncio.CancelledError): await task
                    else:
                        assert (await asyncio.wait_for(task,1)).status_code == 408
        assert runtime.pool.checkedout() == 0
        with owner.begin() as connection:
            assert connection.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'),{'key':LOCK})
    asyncio.run(run())


def test_authenticated_concurrent_forms_finish_atomically(restricted_web):
    from iberbag.database import Warehouse
    app, owner, runtime, role, accounts, tokens = restricted_web
    names = ['FICTÍCIO '+secrets.token_hex(6) for _ in range(2)]
    def post(name):
        with TestClient(app) as client:
            client.cookies.set('wms_session', tokens['admin'][0])
            return client.post('/settings/warehouses',data={'csrf':tokens['admin'][1],'context_id':1,'name':name},follow_redirects=False).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(post,names)) == [303,303]
    with Session(owner) as db:
        assert set(db.scalars(select(Warehouse.name).where(Warehouse.name.in_(names)))) == set(names)


def test_admin_posts_refuse_operator_before_lock(restricted_web):
    app, owner, runtime, role, accounts, tokens = restricted_web
    paths = ['/settings/clients','/settings/clients/1','/settings/warehouses','/settings/contexts',
             '/settings/capacity','/settings/mercadona','/settings/locations/A101/active',
             '/settings/corridors','/settings/corridors/A/update','/settings/corridors/A/delete',
             '/settings/locations','/settings/locations/A101/delete','/settings/locations/A101/rename',
             '/users','/users/1/toggle','/users/1/reset-password','/inventory/incidents/1/resolve',
             '/inventory/lots/resolve','/inventory/preparations/1/complete','/inventory/1/approve',
             '/pending/allocations/1/collect','/dispatches/1/cancel']
    def post(path):
        with TestClient(app) as client:
            client.cookies.set('wms_session',tokens['operador'][0])
            return client.post(path,data={'csrf':tokens['operador'][1],'context_id':1},follow_redirects=False)
    with ThreadPoolExecutor(max_workers=1) as pool:
        for path in paths:
            with owner.begin() as connection:
                connection.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key':LOCK})
                future = pool.submit(post,path)
                deadline = time.monotonic()+2
                while not future.done() and time.monotonic()<deadline:time.sleep(.01)
                finished = future.done()
            response=future.result(timeout=5)
            assert finished,path
            assert response.status_code==403,path


def test_database_error_after_lock_releases_transaction(restricted_web):
    from sqlalchemy import event
    app, owner, runtime, role, accounts, tokens = restricted_web
    def fail_after_lock(connection,cursor,statement,parameters,context,executemany):
        if 'pg_advisory_xact_lock(72834001)' in statement:
            raise RuntimeError('Synthetic failure after writer lock')
    event.listen(runtime,'after_cursor_execute',fail_after_lock)
    try:
        with TestClient(app,raise_server_exceptions=False) as client:
            client.cookies.set('wms_session',tokens['admin'][0])
            assert client.post('/settings/warehouses',data={'csrf':tokens['admin'][1],'context_id':1,'name':'NUNCA CRIAR'}).status_code==500
    finally:event.remove(runtime,'after_cursor_execute',fail_after_lock)
    with owner.begin() as connection:
        assert connection.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'),{'key':LOCK})
    assert runtime.pool.checkedout()==0

