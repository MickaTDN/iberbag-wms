"""Continuous NKT office exercises using generated Excel and disposable PG17 only."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from io import BytesIO
import json
import secrets
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import select, text

from iberbag import backup, database as m, nkt, nkt_inventory as inv, nkt_loading as loading, transfer
from iberbag.privileges import validate_runtime
from iberbag.warehouse_settings import occupied
from test_ddn_contexts import scoped
from test_nkt_inventory import create, mark, review, confirm, restore_review, restore
from test_nkt_loading import prepare, reserve, ship, amend, fix, apply_fix, seed
from test_nkt_receiving import stage, confirm as receive, workbook, manual_rows
from test_security_regressions import restricted_web
from test_receiving import warehouse
from test_transfer import pg_databases, docker_client
from conftest import BACKUP_PASSWORD


def snapshot(engine):
    with engine.connect() as conn:
        return transfer.inventory(transfer.read_data(conn))


def foreign_snapshot(engine):
    with engine.connect() as conn:
        return {name: [r for r in rows if r.get('context_id') not in (None, 4)]
                for name, rows in transfer.read_data(conn).items()}


def cancel(engine, actor, ident):
    with scoped(engine, 4) as db:
        p = db.get(m.NktPreparation, ident)
        return loading.cancel(db, actor, ident, p.version, loading.last_operation(db, ident),
                              secrets.token_hex(16), 'Cancelamento explícito fictício do remanescente')


def balances(engine, *, physical, reserved, shipped, missing):
    with scoped(engine, 4) as db:
        assert nkt.stock(db)[1] == physical
        assert nkt.stock(db, status='available')[1] == physical - reserved
        assert nkt.stock(db, status='reserved')[1] == reserved
        assert nkt.stock(db, status='shipped')[1] == shipped
        assert nkt.stock(db, status='missing')[1] == missing
        assert sum(q for _, q in occupied(db, ['Chão'])) == physical
        assert nkt.stock(db, status='all')[1] == physical + shipped + missing


def test_integrated_nkt_receipt_loading_inventory_compensation_and_encrypted_recovery(restricted_web, pg_databases, tmp_path):
    app, owner, engine, role, accounts, tokens = restricted_web
    actor = accounts['admin'][0]
    with engine.connect() as conn:
        validate_runtime(conn)
        assert 170000 <= int(conn.scalar(text('SHOW server_version_num'))) < 180000
        assert conn.scalar(text('SHOW transaction_isolation')) == 'read committed'
    outside = foreign_snapshot(engine)
    excel = workbook()  # Three containers, two materials, 60 PEG; CQ 61–62 excluded.
    receipt = stage(engine, actor, content=excel)
    assert receive(engine, actor, receipt) == receive(engine, actor, receipt)
    email = stage(engine, actor, manual_rows('MAIL0101', 'MAIL0103', 'EMAIL-C', 'Material C'))
    receive(engine, actor, email)
    with scoped(engine, 4) as db:
        containers = db.scalars(select(m.NktContainer).order_by(m.NktContainer.id)).all()
        entry_ids = [c.entry_id for c in containers]
        provenance = {p.id: (p.peg, p.entry_id, p.local) for p in db.scalars(select(m.NktPallet))}
        original_containers = [(c.id, c.entry_id, c.payload) for c in containers]
        assert len(containers) == 4 and len(provenance) == 63
        assert all(p[2] == 'Chão' for p in provenance.values())
        assert nkt.stock(db, '000061', status='all')[1] == 0
        source = db.get(m.NktReceiving, receipt[0])
        assert source.source_digest == sha256(excel).hexdigest()
        assert len(json.loads(source.report)['excluded']) == 1
    balances(engine, physical=63, reserved=0, shipped=0, missing=0)

    prep = prepare(engine, actor, ['000001', '000002', '000021', '000041'])
    reserve(engine, actor, prep)
    with scoped(engine, 4) as db:
        old_doc = db.scalar(select(m.NktDocument).where(m.NktDocument.operation_id == loading.last_operation(db, prep)))
        old_document = old_doc.id, bytes(old_doc.pdf)
    amend(engine, actor, prep, ['000001', '000003', '000021', '000041'])
    reserve(engine, actor, prep)
    balances(engine, physical=63, reserved=4, shipped=0, missing=0)
    conference = create(engine, actor)
    mark(engine, actor, conference, 'located', peg='0101')  # Unique suffix, whole email load.
    with scoped(engine, 4) as db:
        report = inv.summary(db, db.get(m.NktInventory, conference))
        assert report['totals']['excluded'] == 3 and not report['blocking']
        assert sum(g['source']['physical'] for g in report['groups'] if g['decision'] == 'excluded') == 60
    before_movements = review(engine, actor, conference)
    first = ship(engine, actor, prep, ['000001'], gt='GT-INTEGRATED-1', day='2027-02-02')
    second = ship(engine, actor, prep, ['000021'], gt='GT-INTEGRATED-2', day='2027-02-03')
    cancel(engine, actor, prep)
    with pytest.raises(inv.ReceivingError):
        confirm(engine, actor, conference, before_movements)
    for peg in ['000002', '000022', '000041']:
        mark(engine, actor, conference, 'located', peg=peg)
    unchanged = confirm(engine, actor, conference, review(engine, actor, conference))
    with scoped(engine, 4) as db:
        assert json.loads(db.get(m.NktOperation, unchanged).snapshot)['events'] == []
        original_gt = db.get(m.NktOperation, second).snapshot
        original_pdf = bytes(db.scalar(select(m.NktDocument.pdf).where(m.NktDocument.operation_id == second)))
    apply_fix(engine, actor, fix(engine, actor, prep, second, 'metadata', day='2027-02-04', gt='GT-CORRECTED-FICT'))
    correction = fix(engine, actor, prep, first, pegs=['000001'])
    key = secrets.token_hex(16)
    assert apply_fix(engine, actor, correction, key) == apply_fix(engine, actor, correction, key)
    balances(engine, physical=62, reserved=0, shipped=1, missing=0)

    # A compensated shipment is physical again and must participate in inventories.
    waiting = prepare(engine, actor, ['000001'])
    missing_inv = create(engine, actor, 'partial', [entry_ids[0]])
    mark(engine, actor, missing_inv, 'not_located', entry=entry_ids[0])
    missing_review = review(engine, actor, missing_inv)
    absence = confirm(engine, actor, missing_inv, missing_review)
    assert confirm(engine, actor, missing_inv, missing_review) == absence
    balances(engine, physical=42, reserved=0, shipped=1, missing=20)
    with pytest.raises(loading.ReceivingError):
        reserve(engine, actor, waiting)
    with pytest.raises(loading.ReceivingError):
        prepare(engine, actor, ['000001'])
    with pytest.raises(loading.ReceivingError):
        ship(engine, actor, prep, ['000001'])

    partial = restore_review(engine, actor, missing_inv, absence, '000001', False, ['000001'])
    assert restore(engine, actor, partial) == restore(engine, actor, partial)
    reserve(engine, actor, waiting)
    with pytest.raises(inv.ReceivingError, match='reservas'):
        restore_review(engine, actor, missing_inv, absence, '000002', False, ['000002'])
    cancel(engine, actor, waiting)
    remaining = [f'{i:06d}' for i in range(2, 21)]
    restore(engine, actor, restore_review(engine, actor, missing_inv, absence, '000002', False, remaining))
    final = prepare(engine, actor, ['000001', '000003'])
    reserve(engine, actor, final)
    ship(engine, actor, final, ['000001'], gt='GT-INTEGRATED-3', day='2027-02-07')
    ship(engine, actor, final, ['000003'], gt='GT-INTEGRATED-4', day='2027-02-08')
    pending = prepare(engine, actor, ['MAIL0101'])
    reserve(engine, actor, pending)
    balances(engine, physical=60, reserved=1, shipped=3, missing=0)

    with scoped(engine, 4) as db:
        assert {p.id: (p.peg, p.entry_id, p.local) for p in db.scalars(select(m.NktPallet))} == provenance
        assert [(c.id, c.entry_id, c.payload) for c in db.scalars(select(m.NktContainer).order_by(m.NktContainer.id))] == original_containers
        assert db.get(m.NktOperation, second).snapshot == original_gt
        assert bytes(db.scalar(select(m.NktDocument.pdf).where(m.NktDocument.operation_id == second))) == original_pdf
        documents = {d.id: bytes(d.pdf) for d in db.scalars(select(m.NktDocument))}
        for d in db.scalars(select(m.NktDocument)):
            assert sha256(d.pdf).hexdigest() == d.digest
            for page in PdfReader(BytesIO(d.pdf)).pages:
                assert abs(float(page.mediabox.width) - 595.28) < 1
                assert abs(float(page.mediabox.height) - 841.89) < 1
        assert documents[old_document[0]] == old_document[1]
    before = snapshot(engine)
    with TestClient(app) as client:
        client.cookies.set('wms_session', tokens['admin'][0])
        client.cookies.set('ddn_context', '4')
        for path in ['/nkt/stock', '/nkt/stock?status=shipped', '/nkt/entries', '/nkt/receivings',
                     f'/nkt/receivings/{receipt[0]}', '/nkt/preparations', f'/nkt/preparations/{prep}',
                     f'/nkt/preparations/{final}', '/nkt/inventories', f'/nkt/inventories/{conference}',
                     f'/nkt/inventories/{missing_inv}']:
            response = client.get(path)
            assert response.status_code == 200, (path, response.text)
        for ident, raw in documents.items():
            assert client.get(f'/nkt/documents/{ident}.pdf').content == raw
            assert client.get(f'/nkt/documents/{ident}.pdf').content == raw
        for context in ['1', '2', '3', '5']:
            client.cookies.set('ddn_context', context)
            assert client.get(f'/nkt/preparations/{prep}').status_code == 403
            assert client.get(f'/nkt/inventories/{missing_inv}').status_code == 403
            assert client.get(f'/nkt/documents/{old_document[0]}.pdf').status_code == 403
    assert snapshot(engine) == before and foreign_snapshot(engine) == outside
    target = pg_databases(restricted=True)
    folder = tmp_path / 'nkt-integrated-encrypted'
    backup.backup(engine, folder, runner=docker_client, passphrase=BACKUP_PASSWORD)
    assert backup.restore(target, folder, runner=docker_client, passphrase=BACKUP_PASSWORD)['status'] == 'reconciled'
    assert snapshot(target) == before
    balances(target, physical=60, reserved=1, shipped=3, missing=0)
    # Replaying old receipts/adjustments after recovery must not replenish stock again.
    receive(target, actor, receipt)
    assert confirm(target, actor, missing_inv, missing_review) == absence
    restore(target, actor, partial)
    assert snapshot(target) == before


@pytest.mark.parametrize('failure', ['exception', 'disconnect'])
def test_integrated_nkt_inventory_failure_preserves_shipments_reservations_and_documents(restricted_web, monkeypatch, failure):
    app, owner, engine, role, accounts, tokens = restricted_web
    actor = accounts['admin'][0]
    seed(engine, actor)
    prep = prepare(engine, actor, ['004', '005'])
    reserve(engine, actor, prep)
    ship(engine, actor, prep, ['004'])
    ident = create(engine, actor)
    with scoped(engine, 4) as db:
        first = db.scalar(select(m.NktEntry.id).order_by(m.NktEntry.id))
    mark(engine, actor, ident, 'not_located', entry=first)
    revision = review(engine, actor, ident)
    before = snapshot(engine)
    original = inv.document

    def fail(db, *args, **kwargs):
        if args[3] == 'result':  # Events and header already flushed, PDF not yet archived.
            if failure == 'exception':
                raise RuntimeError('Injected failure before inventory PDF')
            pid = db.scalar(text('SELECT pg_backend_pid()'))
            with owner.begin() as conn:
                assert conn.scalar(text('SELECT pg_terminate_backend(:pid)'), {'pid': pid})
        return original(db, *args, **kwargs)

    monkeypatch.setattr(inv, 'document', fail)
    key = secrets.token_hex(16)
    with pytest.raises((RuntimeError, inv.ReceivingError)):
        confirm(engine, actor, ident, revision, key)
    assert snapshot(engine) == before
    balances(engine, physical=5, reserved=1, shipped=1, missing=0)
    monkeypatch.setattr(inv, 'document', original)
    result = confirm(engine, actor, ident, revision, key)
    assert confirm(engine, actor, ident, revision, key) == result
    balances(engine, physical=2, reserved=1, shipped=1, missing=3)


def test_integrated_nkt_inventory_and_reservation_race_never_adjusts_reserved_load(restricted_web):
    app, owner, engine, role, accounts, tokens = restricted_web
    actor = accounts['admin'][0]
    seed(engine, actor)
    prep = prepare(engine, actor, ['001'])
    with scoped(engine, 4) as db:
        first = db.scalar(select(m.NktEntry.id).order_by(m.NktEntry.id))
    ident = create(engine, actor, 'partial', [first])
    mark(engine, actor, ident, 'not_located', entry=first)
    revision = review(engine, actor, ident)
    barrier = Barrier(2)

    def run(kind):
        barrier.wait(timeout=10)
        try:
            if kind == 'reserve':
                reserve(engine, actor, prep)
            else:
                confirm(engine, actor, ident, revision)
            return kind
        except inv.ReceivingError:
            return None

    with ThreadPoolExecutor(2) as pool:
        winners = [result for result in pool.map(run, ['reserve', 'missing']) if result]
    assert len(winners) == 1
    if winners == ['reserve']:
        balances(engine, physical=6, reserved=1, shipped=0, missing=0)
        cancel(engine, actor, prep)
        with pytest.raises(inv.ReceivingError):
            confirm(engine, actor, ident, revision)  # Net-zero does not revive old proof.
        mark(engine, actor, ident, 'located', peg='001')
        confirm(engine, actor, ident, review(engine, actor, ident))
    else:
        balances(engine, physical=3, reserved=0, shipped=0, missing=3)
        with pytest.raises(loading.ReceivingError):
            reserve(engine, actor, prep)
