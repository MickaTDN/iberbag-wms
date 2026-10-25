import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import httpx
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from psycopg import sql

from iberbag import transfer, backup
from iberbag.demo import create_demo


@pytest.fixture
def source(tmp_path):
    passwords = {role: secrets.token_urlsafe(24) for role in ('admin', 'operador')}
    original = tmp_path / 'ficticio.db'
    create_demo(original, passwords)
    original_hash = transfer.digest_file(original)
    bundle = tmp_path / 'preview'
    report = transfer.preview(original, bundle)
    yield original, bundle, passwords, report
    assert transfer.digest_file(original) == original_hash


@pytest.fixture
def pg_databases():
    value = os.environ.get('WMS_TEST_POSTGRES_URL')
    if not value:
        pytest.skip('Requires disposable PostgreSQL')
    url = make_url(value)
    assert url.database.endswith('_test')
    admin = create_engine(url, isolation_level='AUTOCOMMIT')
    engines = []
    roles = []
    def create(*, restricted=False):
        name = 'review_' + secrets.token_hex(8) + '_test'
        password = secrets.token_urlsafe(24)
        role = 'app_' + secrets.token_hex(8)
        with admin.connect() as connection:
            if restricted:
                connection.exec_driver_sql(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}').format(
                    sql.Identifier(role), sql.Literal(password)).as_string())
                roles.append(role)
                connection.exec_driver_sql(sql.SQL('CREATE DATABASE {} OWNER {}').format(
                    sql.Identifier(name), sql.Identifier(role)).as_string())
            else:
                connection.exec_driver_sql(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)).as_string())
        target_url = url.set(database=name)
        if restricted:
            target_url = target_url.set(username=role, password=password)
        engine = create_engine(target_url)
        engines.append(engine)
        return engine
    yield create
    for engine in engines:
        engine.dispose()
        with admin.connect() as connection:
            connection.exec_driver_sql(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(engine.url.database)).as_string())
    with admin.connect() as connection:
        for role in roles:
            connection.exec_driver_sql(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)).as_string())
    admin.dispose()


def test_preview_preserves_source_and_reports_missing_information(source):
    original, bundle, passwords, report = source
    assert report['source']['paletes'] == 8
    assert report['source']['cargas_sem_data'] == 1
    assert report['source']['cargas_sem_responsavel'] == 1
    assert report['source']['locais_acima_capacidade'] == 1
    assert report['source']['stock_sem_catalogo'] == 1
    assert report['source']['stock_sem_carga'] == 1
    assert report['sessions_revoked'] == 2
    assert report['expected']['tables']['sessions']['rows'] == 0
    text_report = (bundle / 'preview.json').read_text()
    assert all(password not in text_report for password in passwords.values())
    assert 'password_hash' not in text_report
    with pytest.raises(FileExistsError):
        transfer.preview(original, bundle)


def test_preview_captures_committed_wal(tmp_path):
    original = tmp_path / 'wal.db'
    create_demo(original, {role: secrets.token_urlsafe(24) for role in ('admin', 'operador')})
    with sqlite3.connect(original) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('UPDATE stock SET quantidade=6 WHERE local=?', ('Chão',))
        writer.commit()
        before = transfer.digest_file(original)
        wal_before = transfer.digest_file(str(original) + '-wal')
        report = transfer.preview(original, tmp_path / 'wal-preview')
        assert report['source']['paletes'] == 9
        assert before == transfer.digest_file(original)
        assert wal_before == transfer.digest_file(str(original) + '-wal')


def test_changed_snapshot_refused(source):
    _, bundle, _, _ = source
    with sqlite3.connect(bundle / 'source.sqlite') as db:
        db.execute('UPDATE stock SET quantidade=7 WHERE local=?', ('Chão',))
    with pytest.raises(ValueError, match='alterada'):
        transfer.load_bundle(bundle)


def test_production_target_refused(source):
    _, bundle, _, _ = source
    target = create_engine('postgresql+psycopg://localhost/iberbag')
    try:
        with pytest.raises(ValueError, match='_test'):
            transfer.apply(bundle, target)
        with pytest.raises(ValueError, match='_test'):
            backup.restore(target, bundle)
    finally:
        target.dispose()


def test_migration_reconciliation_idempotence_and_sequences(source, pg_databases):
    _, bundle, _, report = source
    target = pg_databases()
    assert transfer.apply(bundle, target)['status'] == 'reconciled'
    assert transfer.reconcile(bundle, target)['different_tables'] == []
    with pytest.raises(ValueError, match='não vazio'):
        transfer.apply(bundle, target)
    assert transfer.reconcile(bundle, target)['status'] == 'reconciled'
    with target.begin() as connection:
        assert connection.scalar(text('SELECT count(*) FROM sessions')) == 0
        assert connection.scalar(text('SELECT entrada FROM cargas WHERE codigo=:code'), {'code': 'CARGA-FICTICIA-2'}) is None
        assert connection.scalar(text('SELECT funcionario FROM cargas WHERE codigo=:code'), {'code': 'CARGA-FICTICIA-2'}) is None
        maximum = connection.scalar(text('SELECT max(id) FROM audit'))
        new_id = connection.scalar(text("INSERT INTO audit (occurred_at,username,action,details) VALUES (now(),'test','TEST','fictício') RETURNING id"))
        assert new_id > maximum
    assert transfer.reconcile(bundle, target)['different_tables'] == ['audit']


def test_reconciliation_failure_rolls_back_schema(source, pg_databases, monkeypatch):
    _, bundle, _, _ = source
    target = pg_databases()
    monkeypatch.setattr(transfer, 'differences', lambda expected, actual: ['stock'])
    with pytest.raises(ValueError, match='Reconciliação'):
        transfer.apply(bundle, target)
    with target.connect() as connection:
        transfer.require_empty(connection)


def test_unknown_table_is_not_silently_dropped(tmp_path):
    original = tmp_path / 'extra.db'
    create_demo(original, {role: secrets.token_urlsafe(24) for role in ('admin', 'operador')})
    with sqlite3.connect(original) as db:
        db.execute('CREATE TABLE unknown_history (event text)')
    with pytest.raises(ValueError, match='Esquema diferente'):
        transfer.preview(original, tmp_path / 'unknown')


def docker_client(name, args, url, *, stdin=None, stdout=None):
    container = os.environ.get('WMS_TEST_PG_CONTAINER')
    if not container:
        pytest.skip('Backup integration requires scripts/test_postgres.py')
    command = ['docker', 'exec', '-i', '--env', 'PGPASSWORD', container, name,
               '--host', '127.0.0.1', '--port', '5432', '--username', url.username,
               '--dbname', url.database, '--no-password', *args]
    # Same PostgreSQL tools and archive streams as Windows; executable runs in Docker.
    result = subprocess.run(command, env=dict(os.environ, PGPASSWORD=url.password), stdin=stdin,
                            stdout=stdout, stderr=subprocess.PIPE)
    if result.returncode:
        raise ValueError('PostgreSQL archive command failed')


def test_backup_restore_complete_and_tamper_refused(source, pg_databases, tmp_path):
    _, bundle, _, _ = source
    origin, restored, untouched = pg_databases(), pg_databases(), pg_databases()
    transfer.apply(bundle, origin)
    folder = tmp_path / 'backup'
    manifest = backup.backup(origin, folder, runner=docker_client)
    assert manifest['restore_verified'] is False
    assert backup.restore(restored, folder, runner=docker_client)['status'] == 'reconciled'
    assert transfer.reconcile(bundle, restored)['status'] == 'reconciled'
    assert transfer.reconcile(bundle, origin)['status'] == 'reconciled'
    with pytest.raises(ValueError, match='não vazio'):
        backup.restore(restored, folder, runner=docker_client)
    with (folder / 'database.dump').open('ab') as stream:
        stream.write(b'changed')
    with pytest.raises(ValueError, match='alterado'):
        backup.restore(untouched, folder, runner=docker_client)
    with untouched.connect() as connection:
        transfer.require_empty(connection)


def test_two_http_clients_share_migrated_postgres_and_permissions(source, pg_databases, tmp_path):
    _, bundle, passwords, _ = source
    target = pg_databases()
    transfer.apply(bundle, target)
    import socket
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, WMS_DATABASE_URL=target.url.render_as_string(hide_password=False), WMS_COOKIE_SECURE='0')
    with (tmp_path / 'server.log').open('w') as log:
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'iberbag.app:app', '--host', '127.0.0.1',
                                    '--port', str(port), '--workers', '2'], env=env, stdout=log, stderr=log)
        try:
            base = f'http://127.0.0.1:{port}'
            for _ in range(100):
                if process.poll() is not None:
                    pytest.fail('Server exited before readiness')
                try:
                    if httpx.get(base + '/health', timeout=1).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail('Server did not become ready')
            barrier = Barrier(2)
            def workstation(role):
                with httpx.Client(base_url=base, timeout=15) as client:
                    assert client.post('/login', data={'username': role + '_teste', 'password': passwords[role]}).status_code == 303
                    barrier.wait(timeout=5)
                    for _ in range(3):
                        response = client.get('/stock?artigo=ART-FICTICIO')
                        assert response.status_code == 200
                        assert 'LOTE-1' in response.text and 'LOTE-2' in response.text and '2 paletes' in response.text
                        assert client.get('/map').status_code == 200
                    expected = 200 if role == 'admin' else 403
                    assert client.get('/settings').status_code == expected
                    assert client.get('/users').status_code == expected
                    assert client.get('/audit').status_code == expected
                    assert client.post('/settings/locations/A101/delete', data={}).status_code == 403
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(workstation, role) for role in ('admin', 'operador')]
                for future in futures:
                    future.result(timeout=30)
            with target.connect() as connection:
                assert connection.scalar(text('SELECT sum(quantidade) FROM stock')) == 8
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_invalid_postgres_data_rolls_back_all_tables(tmp_path, pg_databases):
    original = tmp_path / 'bad-length.db'
    create_demo(original, {role: secrets.token_urlsafe(24) for role in ('admin', 'operador')})
    with sqlite3.connect(original) as db:
        db.execute('UPDATE stock SET artigo=?', ('x' * 121,))
    bundle = tmp_path / 'bad-length-preview'
    transfer.preview(original, bundle)
    target = pg_databases()
    from sqlalchemy.exc import DataError
    with pytest.raises(DataError):
        transfer.apply(bundle, target)
    with target.connect() as connection:
        transfer.require_empty(connection)


def test_backup_snapshot_excludes_concurrent_change_and_restore_failure_is_atomic(source, pg_databases, tmp_path):
    _, bundle, _, _ = source
    origin, restored, failed = pg_databases(), pg_databases(), pg_databases()
    transfer.apply(bundle, origin)
    def concurrent_dump(name, args, url, **kwargs):
        with origin.begin() as connection:
            connection.execute(text("UPDATE stock SET quantidade=99 WHERE local='Chão'"))
        docker_client(name, args, url, **kwargs)
    folder = tmp_path / 'snapshot-backup'
    manifest = backup.backup(origin, folder, runner=concurrent_dump)
    assert manifest['inventory']['paletes'] == 8
    backup.restore(restored, folder, runner=docker_client)
    assert transfer.reconcile(bundle, restored)['status'] == 'reconciled'
    assert transfer.reconcile(bundle, origin)['different_tables'] == ['stock']
    # A checksum-valid but truncated archive must fail without partial tables.
    archive = folder / 'database.dump'
    archive.write_bytes(archive.read_bytes()[:2000])
    manifest['archive_sha256'] = transfer.digest_file(archive)
    (folder / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='archive command failed'):
        backup.restore(failed, folder, runner=docker_client)
    with failed.connect() as connection:
        transfer.require_empty(connection)


def test_non_superuser_can_migrate_backup_and_restore(source, pg_databases, tmp_path):
    _, bundle, passwords, _ = source
    origin = pg_databases(restricted=True)
    restored = pg_databases(restricted=True)
    transfer.apply(bundle, origin)
    with origin.connect() as connection:
        assert connection.scalar(text('SELECT rolsuper FROM pg_roles WHERE rolname=current_user')) is False
    folder = tmp_path / 'restricted-backup'
    backup.backup(origin, folder, runner=docker_client)
    backup.restore(restored, folder, runner=docker_client)
    assert transfer.reconcile(bundle, restored)['status'] == 'reconciled'
    from sqlalchemy.orm import Session
    from sqlalchemy import select
    from iberbag.database import User
    from iberbag.security import verify_password
    with Session(restored) as db:
        for role in ('admin', 'operador'):
            user = db.scalar(select(User).where(User.username == role + '_teste'))
            assert verify_password(user, passwords[role])
            assert user.role == role
