"""Explicit additive upgrade after a verified backup/restore; no automatic startup writes."""
import json
from pathlib import Path
from alembic import command
from sqlalchemy import text
from .migrations import config, REVISION
from .transfer import inventory, read_data, digest_file, LOCK


def revision(connection):
    return connection.scalar(text('SELECT version_num FROM alembic_version'))


def preview(engine):
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        return {'from': revision(connection), 'to': REVISION,
                'adds': ['receipts', 'receipt_lines', 'putaways', 'immutable history triggers (PostgreSQL)'],
                'data': inventory(read_data(connection)), 'existing_data_changes': 'none'}


def apply_upgrade(engine, folder):
    if engine.dialect.name != 'postgresql':
        raise ValueError('Atualização operacional exige PostgreSQL.')
    folder = Path(folder)
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format') != 1 or digest_file(folder / 'database.dump') != manifest['archive_sha256']:
        raise ValueError('Backup inválido ou alterado.')
    verified = False
    for path in folder.glob('restore-*.json'):
        report = json.loads(path.read_text(encoding='utf-8'))
        if (report.get('status') == 'reconciled' and report.get('different_tables') == []
                and report.get('archive_sha256') == manifest['archive_sha256']
                and report.get('inventory') == manifest['inventory']):
            verified = True
    if not verified:
        raise ValueError('É necessário um relatório de restauro reconciliado deste backup.')
    with engine.begin() as connection:
        connection.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': LOCK})
        connection.execute(text('SELECT pg_advisory_xact_lock(72834001)'))
        old = revision(connection)
        if old == REVISION:
            return {'status': 'already_current', 'revision': REVISION}
        if old != '0001':
            raise ValueError('Só é suportada a atualização 0001 → 0002.')
        before = inventory(read_data(connection))
        if before != manifest['inventory']:
            raise ValueError('A base mudou desde o backup. Parar aplicação, fazer novo backup e ensaiar restauro.')
        cfg = config()
        cfg.attributes['connection'] = connection
        command.upgrade(cfg, REVISION)
        if inventory(read_data(connection)) != before:
            raise ValueError('Dados divergentes após atualização. Transação revertida.')
    return {'status': 'upgraded', 'revision': REVISION, 'existing_data_changes': 'none'}
