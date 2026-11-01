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


@pytest.mark.parametrize('case', ['database', 'schema_create', 'schema_owner', 'table_owner', 'function_owner', 'public_create'])
def test_runtime_rejects_privileges_outside_current_schema(restricted_web, case):
    app, owner, runtime, role, accounts, tokens = restricted_web
    administrator = create_engine(make_url(os.environ['WMS_TEST_POSTGRES_URL']).set(database=owner.url.database))
    try:
        with administrator.begin() as connection:
            connection.exec_driver_sql('CREATE SCHEMA audit_extra')
            if case == 'database':
                statement = sql.SQL('GRANT CREATE ON DATABASE {} TO {}').format(sql.Identifier(owner.url.database), sql.Identifier(role))
            elif case in ('schema_create', 'public_create'):
                statement = sql.SQL('GRANT CREATE ON SCHEMA audit_extra TO {}').format(
                    sql.SQL('PUBLIC') if case == 'public_create' else sql.Identifier(role))
            elif case == 'schema_owner':
                statement = sql.SQL('ALTER SCHEMA audit_extra OWNER TO {}').format(sql.Identifier(role))
            elif case == 'table_owner':
                connection.exec_driver_sql('CREATE TABLE audit_extra.synthetic(id integer)')
                statement = sql.SQL('ALTER TABLE audit_extra.synthetic OWNER TO {}').format(sql.Identifier(role))
            else:
                connection.exec_driver_sql('CREATE FUNCTION audit_extra.synthetic() RETURNS integer LANGUAGE sql AS $$SELECT 1$$')
                statement = sql.SQL('ALTER FUNCTION audit_extra.synthetic() OWNER TO {}').format(sql.Identifier(role))
            connection.exec_driver_sql(statement.as_string())
        with runtime.connect() as connection, pytest.raises(ValueError):
            validate_runtime(connection)
    finally:
        administrator.dispose()

