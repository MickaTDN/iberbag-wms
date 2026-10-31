"""Security/Fase 1 integration: only disposable, synthetic PostgreSQL databases."""
from collections import Counter
from datetime import datetime
import secrets
import tempfile

import pytest
from alembic import command
from sqlalchemy import MetaData, Table, event, select, text
from sqlalchemy.exc import DBAPIError

from conftest import BACKUP_PASSWORD
from iberbag import backup, dispatching, inventorying, receiving, transfer, transferring
from iberbag.backup_crypto import encrypt
from iberbag.database import (Base, ReceiptLine, Stock, DispatchDocument,
    ReceiptDocument, DispatchOrder)
from iberbag.migrations import config, REVISION
from iberbag.private_files import private_folder
from iberbag.schema_upgrade import apply_upgrade
from test_ddn_contexts import scoped, receive, prepare, add_context
from test_receiving import warehouse
from test_transfer import pg_databases, docker_client
from legacy_catalogue import seed_legacy_catalogue


ORIGINS = ('0006', '0007', 'ddn_0001', 'ddn_0002',
           ('0007', 'ddn_0001'), ('0007', 'ddn_0002'))


def full_history(engine, actor):
    """One owner, with reservation, PDF, allocation, movement and physical count."""
    receive(engine, actor, qty=5)
    prepared = prepare(engine, actor, 1, 7)
    with scoped(engine) as db:
        entry = receiving.save_receipt(db, actor, secrets.token_hex(16), 'NEXT', '2020-01-03', [('ART', 'LOT', '4')])
        receiving.confirm_receipt(db, actor, entry, 1)
        line = db.scalar(select(ReceiptLine.id).where(ReceiptLine.receipt_id == entry))
        placed = receiving.review_putaway(db, actor, secrets.token_hex(16), line, 'Chão', '2')
        receiving.confirm_putaway(db, actor, placed)
        stock = db.scalar(select(Stock.id).where(Stock.carga == 'NEXT'))
        moved = transferring.review_transfer(db, actor, secrets.token_hex(16), stock, 'A101', '1')
        transferring.confirm_transfer(db, actor, moved)
        inv = inventorying.create_inventory(db, actor, secrets.token_hex(16), 'FICTITIOUS COUNT', 'partial', ['Chão'])
        scope = inventorying.scopes(db, inv)[0]
        inventorying.save_count(db, actor, scope.id, scope.version, [], empty=True)
        inventorying.submit_inventory(db, actor, inv)
        inventorying.approve_inventory(db, actor, inv, 'Synthetic physical count')
        reserved = dispatching.save_order(db, actor, secrets.token_hex(16), 'FICTITIOUS RESERVATION', [('ART', 'LOT', '1')])
        dispatching.review_order(db, actor, reserved, 1)
    return prepared, reserved


def snapshot_legacy(engine):
    with engine.connect() as conn:
        return {t.name: [{k: v for k, v in row.items() if k != 'context_id'}
                for row in conn.execute(select(t)).mappings()]
            for t in Base.metadata.sorted_tables
            if t.name not in transfer.CONTEXT_TABLES | transfer.PHYSICAL_TABLES}


def clone_history(source, target, origins):
    """Fixture creation uses the historical SQL schema, never newer ORM columns."""
    expected = snapshot_legacy(source)
    origins = origins if isinstance(origins, tuple) else (origins,)
    with target.begin() as conn:
        cfg = config(); cfg.attributes['connection'] = conn
        for origin in origins:
            command.upgrade(cfg, origin)
        conn.execute(text('ALTER TABLE stock DISABLE TRIGGER stock_clock'))
        conn.execute(text('ALTER TABLE locations DISABLE TRIGGER catalogue_clock'))
        for model in Base.metadata.sorted_tables:
            if model.name not in expected or not expected[model.name]:
                continue
            table = Table(model.name, MetaData(), autoload_with=conn)
            rows = [{key: value for key, value in row.items() if key in table.c}
                    for row in expected[model.name]]
            if model.name == 'receipts':
                rows = [dict(row, status='draft', carga_id=None, receiver=None, confirmed_at=None) for row in rows]
            if model.name == 'dispatch_orders':
                rows = [dict(row, status='draft', checked_by=None, checked_at=None, confirmed_by=None, confirmed_at=None) for row in rows]
            if model.name == 'physical_inventories':
                rows = [dict(row, status='draft', submitted_by=None, submitted_at=None, applied_by=None, applied_at=None, reason=None) for row in rows]
            conn.execute(table.insert(), rows)
        for name in ('receipts', 'dispatch_orders', 'physical_inventories'):
            table = Table(name, MetaData(), autoload_with=conn)
            for row in expected[name]:
                values = {key: value for key, value in row.items() if key in table.c}
                conn.execute(table.update().where(table.c.id == row['id']).values(**values))
        conn.execute(text('ALTER TABLE stock ENABLE TRIGGER stock_clock'))
        conn.execute(text('ALTER TABLE locations ENABLE TRIGGER catalogue_clock'))
        conn.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
    return expected


@pytest.mark.parametrize('origin', ORIGINS)
def test_encrypted_verified_upgrade_origin_matrix(warehouse, pg_databases, tmp_path, origin):
    source, accounts = warehouse
    prepared, reserved = full_history(source, accounts['admin'][0])
    target, restored = pg_databases(restricted=True), pg_databases(restricted=True)
    expected = clone_history(source, target, origin)
    with target.connect() as conn:
        before = transfer.inventory(transfer.read_data(conn))
    folder = tmp_path/'verified-origin'
    manifest = backup.backup(target, folder, runner=docker_client, passphrase=BACKUP_PASSWORD)
    assert manifest['format'] == 2
    assert backup.restore(restored, folder, runner=docker_client, passphrase=BACKUP_PASSWORD)['status'] == 'reconciled'
    assert apply_upgrade(target, folder)['revision'] == REVISION
    assert apply_upgrade(target, folder)['status'] == 'already_current'
    with target.connect() as conn:
        assert conn.execute(text('SELECT version_num FROM alembic_version')).scalars().all() == [REVISION]
        assert transfer.inventory(transfer.read_data(conn)) == before
        for name, rows in expected.items():
            actual = [{k: v for k, v in row.items() if k != 'context_id'}
                      for row in conn.execute(select(Base.metadata.tables[name])).mappings()]
            assert sorted(actual, key=repr) == sorted(rows, key=repr), name
    with scoped(target) as db, scoped(source) as original:
        assert db.get(DispatchOrder, reserved).status == 'ready'
        assert db.get(DispatchDocument, prepared).pdf == original.get(DispatchDocument, prepared).pdf
    for statement in ('UPDATE audit SET details=details', 'DELETE FROM audit', 'TRUNCATE audit'):
        with target.begin() as conn, pytest.raises(DBAPIError):
            conn.execute(text(statement))


def test_encrypted_recovery_preserves_all_owners_warehouses_and_claims(warehouse, pg_databases, tmp_path):
    source, accounts = warehouse; actor = accounts['admin'][0]
    full_history(source, actor)
    other = add_context(source, actor, 2)
    received, stock = receive(source, actor, other, qty=5)
    order = prepare(source, actor, other, 2)
    with source.connect() as conn:
        expected = transfer.inventory(transfer.read_data(conn))
        # Legacy compatibility must never hide the other owners/catalogues.
        subset = set(Base.metadata.tables)-transfer.CONTEXT_TABLES-transfer.PHYSICAL_TABLES
        with pytest.raises(ValueError):
            transfer.archive_inventory(conn, {'tables': {name: {} for name in subset}})
    folder = tmp_path/'all-contexts'
    backup.backup(source, folder, runner=docker_client, passphrase=BACKUP_PASSWORD)
    restored = pg_databases(restricted=True)
    assert backup.restore(restored, folder, runner=docker_client, passphrase=BACKUP_PASSWORD)['status'] == 'reconciled'
    with restored.connect() as conn:
        assert transfer.inventory(transfer.read_data(conn)) == expected
    with scoped(restored, other) as db, scoped(source, other) as original:
        assert db.get(Stock, stock).quantidade == 3
        assert db.get(DispatchDocument, order).pdf == original.get(DispatchDocument, order).pdf
        assert db.get(ReceiptDocument, received).pdf == original.get(ReceiptDocument, received).pdf
    with restored.begin() as conn, pytest.raises(DBAPIError):
        conn.execute(text("INSERT INTO locations(context_id,codigo,capacidade,active) VALUES (3,'A101',1,true)"))
    with restored.begin() as conn, pytest.raises(DBAPIError):
        conn.execute(text('DELETE FROM physical_location_claims'))


def test_upgrade_rollback_retains_original_heads_and_history(warehouse, pg_databases, tmp_path):
    source, accounts = warehouse; full_history(source, accounts['admin'][0])
    target, restored = pg_databases(restricted=True), pg_databases(restricted=True)
    clone_history(source, target, 'ddn_0002')
    with target.connect() as conn:
        before = transfer.inventory(transfer.read_data(conn))
    folder = tmp_path/'rollback'
    backup.backup(target, folder, runner=docker_client, passphrase=BACKUP_PASSWORD)
    backup.restore(restored, folder, runner=docker_client, passphrase=BACKUP_PASSWORD)
    def reject_audit_function(conn, cursor, statement, parameters, context, executemany):
        # Fail after the function and first trigger exist: PostgreSQL must roll
        # back partial DDL as well as retain the original data and revision.
        if 'CREATE TRIGGER immutable_audit_truncate' in statement:
            raise RuntimeError('Synthetic migration failure')
    event.listen(target, 'before_cursor_execute', reject_audit_function)
    try:
        with pytest.raises(RuntimeError, match='Synthetic migration failure'):
            apply_upgrade(target, folder)
    finally:
        event.remove(target, 'before_cursor_execute', reject_audit_function)
    with target.connect() as conn:
        assert conn.execute(text('SELECT version_num FROM alembic_version')).scalars().all() == ['ddn_0002']
        assert transfer.inventory(transfer.read_data(conn)) == before
        assert conn.scalar(text("SELECT count(*) FROM pg_proc WHERE proname='wms_audit_guard'")) == 0
        assert conn.scalar(text("SELECT count(*) FROM pg_trigger WHERE tgname='immutable_audit'")) == 0


def test_existing_pr9_encrypted_archive_uses_original_authenticated_inventory(pg_databases, tmp_path):
    """Real pre-DDN manifest shape/AAD; not a new backup tool reading an old DB."""
    source, restored = pg_databases(restricted=True), pg_databases(restricted=True)
    with source.begin() as conn:
        cfg = config(); cfg.attributes['connection'] = conn; command.upgrade(cfg, '0007')
    with scoped(source) as db:
        seed_legacy_catalogue(db)
    with source.begin() as conn:
        conn.execute(text("INSERT INTO cargas(codigo,entrada,funcionario) VALUES ('SYNTHETIC-LEGACY','2020-01-01',NULL)"))
        conn.execute(text("UPDATE locations SET capacidade=19 WHERE codigo='A101'"))
        conn.execute(text("INSERT INTO stock(artigo,lote,carga,local,quantidade) VALUES ('ART','LOT','SYNTHETIC-LEGACY','A101',3)"))
    native = {}
    with source.connect() as conn:
        for name in sorted(set(Base.metadata.tables)-transfer.CONTEXT_TABLES-transfer.PHYSICAL_TABLES):
            table = Table(name, MetaData(), autoload_with=conn)
            native[name] = [dict(row) for row in conn.execute(select(table).order_by(*table.primary_key.columns)).mappings()]
    original_inventory = transfer.inventory(native)
    # PR #9 used the historical fixed one-pallet policy in its inventory metric.
    occupancy = Counter()
    for row in native['stock']: occupancy[row['local']] += row['quantidade']
    from iberbag.location_rules import capacity_for
    original_inventory['locais_acima_capacidade'] = sum(capacity_for(code) == 1 and qty > 1 for code, qty in occupancy.items())
    assert original_inventory['locais_acima_capacidade'] == 1
    folder = private_folder(tmp_path/'old-encrypted', create=True)
    manifest = dict(format=2, created_at=datetime.now().isoformat(), inventory=original_inventory,
                    encryption='AES-256-GCM/PBKDF2-SHA256-600000', restore_verified=False)
    archive = folder/'database.dump'
    with tempfile.TemporaryFile(dir=folder) as plain:
        docker_client('pg_dump', ['--format=custom', '--no-owner', '--no-privileges'], source.url, stdout=plain)
        plain.seek(0)
        with archive.open('xb') as output: encrypt(plain, output, BACKUP_PASSWORD, manifest)
    manifest['archive_sha256'] = transfer.digest_file(archive)
    transfer.save_json(folder/'manifest.json', manifest)
    exact_manifest = (folder/'manifest.json').read_bytes()
    assert backup.restore(restored, folder, runner=docker_client, passphrase=BACKUP_PASSWORD)['status'] == 'reconciled'
    assert apply_upgrade(source, folder)['revision'] == REVISION
    assert (folder/'manifest.json').read_bytes() == exact_manifest
    with source.connect() as conn:
        for name, rows in native.items():
            table = Table(name, MetaData(), autoload_with=conn)
            actual = [{k: v for k, v in row.items() if k != 'context_id'}
                      for row in conn.execute(select(table).order_by(*table.primary_key.columns)).mappings()]
            assert actual == rows, name
        assert conn.scalar(text("SELECT capacidade FROM locations WHERE context_id=1 AND codigo='A101'")) == 19


def test_mismatched_heads_refuse_startup_and_upgrade_without_adoption(warehouse, monkeypatch):
    """Synthetic corrupt metadata must never cause an automatic adoption/upgrade."""
    import iberbag.migrations as migrations
    from iberbag.schema_upgrade import preview
    engine, _ = warehouse
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO alembic_version(version_num) VALUES ('0007')"))
    with engine.connect() as conn, pytest.raises(ValueError):
        transfer.read_data(conn)
    with pytest.raises(ValueError):preview(engine)
    monkeypatch.setattr(migrations,'engine',engine)
    with pytest.raises(RuntimeError):migrations.check_database()
    with pytest.raises(RuntimeError):migrations.prepare_database()
    with engine.connect() as conn:
        assert set(conn.execute(text('SELECT version_num FROM alembic_version')).scalars()) == {'0007',REVISION}
