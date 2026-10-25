import secrets
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from iberbag.app import app
from iberbag.database import SessionLocal, User, Stock, Audit, engine, init_db
from iberbag.security import set_password, get_session


@pytest.fixture
def accounts():
    password = secrets.token_urlsafe(24)
    names = ['review_' + secrets.token_hex(6) for _ in range(2)]
    ids = []
    with SessionLocal() as db:
        for name in names:
            user = User(username=name, display_name=name, role='admin', active=True)
            set_password(user, password)
            db.add(user)
            db.flush()
            ids.append(user.id)
        db.commit()
    with TestClient(app) as first, TestClient(app) as second:
        for client, name in zip((first, second), names):
            response = client.post('/login', data={'username': name, 'password': password}, follow_redirects=False)
            assert response.status_code == 303
            assert 'HttpOnly' in response.headers['set-cookie']
            assert 'SameSite=strict' in response.headers['set-cookie']
        yield first, second, ids


def csrf(client):
    with SessionLocal() as db:
        return get_session(db, client.cookies['wms_session']).csrf_token


def test_csrf_and_session_revocation(accounts):
    first, second, ids = accounts
    assert first.post(f'/users/{ids[1]}/toggle', data={}).status_code == 403
    assert second.get('/settings').status_code == 200
    assert first.post(f'/users/{ids[1]}/reset-password', data={
        'csrf': csrf(first), 'password': secrets.token_urlsafe(24)
    }, follow_redirects=False).status_code == 303
    assert second.get('/stock', follow_redirects=False).status_code == 303


@pytest.mark.skipif(engine.dialect.name != 'postgresql', reason='Requires real PostgreSQL locks')
def test_concurrent_admin_deactivation(accounts):
    first, second, ids = accounts
    tokens = [csrf(first), csrf(second)]
    barrier = Barrier(2)
    def toggle(client, uid, token):
        barrier.wait(timeout=5)
        return client.post(f'/users/{uid}/toggle', data={'csrf': token}, follow_redirects=False).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(toggle, first, ids[1], tokens[0])
        b = pool.submit(toggle, second, ids[0], tokens[1])
        assert sorted([a.result(timeout=15), b.result(timeout=15)]) == [303, 403]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(User).where(User.id.in_(ids), User.active.is_(True))) == 1


def test_transaction_rollback_preserves_stock_and_audit():
    marker = secrets.token_hex(12)
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(Stock))
        db.add(Audit(username='test', action=marker, details='fictício'))
        db.add(Stock(artigo=marker, lote=marker, carga=marker, local='A102', quantidade=0))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        assert db.scalar(select(func.count()).select_from(Stock)) == before
        assert db.scalar(select(Audit.id).where(Audit.action == marker)) is None


def test_large_form_rejected():
    with TestClient(app) as client:
        assert client.post('/login', content=b'x' * 9000,
                           headers={'content-type': 'application/x-www-form-urlencoded'}).status_code == 413


def test_secure_cookie(monkeypatch, accounts):
    import iberbag.app as module
    monkeypatch.setattr(module, 'COOKIE_SECURE', True)
    first, second, ids = accounts
    # Create fresh credentials, then authenticate over HTTPS.
    password = secrets.token_urlsafe(24)
    with SessionLocal() as db:
        user = db.get(User, ids[0])
        name = user.username
        set_password(user, password)
        db.commit()
    with TestClient(app, base_url='https://testserver') as client:
        response = client.post('/login', data={'username': name, 'password': password}, follow_redirects=False)
        assert 'Secure' in response.headers['set-cookie']
        assert client.get('/settings').status_code == 200


def test_prepare_repeatable_and_startup_read_only():
    from iberbag.migrations import prepare_database
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(Audit))
        stock = db.execute(select(Stock.artigo, Stock.lote, Stock.carga, Stock.local, Stock.quantidade).order_by(Stock.id)).all()
    prepare_database()
    prepare_database()
    init_db()
    with SessionLocal() as db:
        assert before == db.scalar(select(func.count()).select_from(Audit))
        assert stock == db.execute(select(Stock.artigo, Stock.lote, Stock.carga, Stock.local, Stock.quantidade).order_by(Stock.id)).all()


def test_existing_unversioned_database_refused(tmp_path):
    import os, sqlite3, subprocess, sys
    target = tmp_path / 'legacy.db'
    with sqlite3.connect(target) as db:
        db.execute('create table sentinel (value text)')
        db.execute("insert into sentinel values ('preserve')")
    env = dict(os.environ, WMS_DATABASE_URL='sqlite:///' + str(target))
    result = subprocess.run([sys.executable, '-m', 'iberbag.cli', 'prepare-db'], env=env, capture_output=True)
    assert result.returncode != 0
    with sqlite3.connect(target) as db:
        assert db.execute('select * from sentinel').fetchall() == [('preserve',)]
        assert db.execute("select name from sqlite_master where type='table'").fetchall() == [('sentinel',)]


def test_network_launcher_rejects_sqlite(monkeypatch):
    from iberbag.server import configure
    monkeypatch.setenv('WMS_DATABASE_URL', 'sqlite:///:memory:')
    with pytest.raises(SystemExit, match='PostgreSQL'):
        configure()


def test_health_checks_database():
    from iberbag.app import database
    from sqlalchemy.exc import OperationalError
    class Unavailable:
        def execute(self, *args):
            raise OperationalError('unavailable', {}, Exception())
    with TestClient(app) as client:
        assert client.get('/health').status_code == 200
        app.dependency_overrides[database] = lambda: Unavailable()
        try:
            response = client.get('/health')
            assert response.status_code == 503
            assert response.json() == {'status': 'unavailable'}
        finally:
            app.dependency_overrides.clear()
