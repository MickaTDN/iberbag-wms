"""Explicit joined upgrade after verified backup/restore; never runs at startup."""
import json
from pathlib import Path
from .private_files import private_folder, owned_file
from alembic import command
from sqlalchemy import text
from .migrations import config, REVISION
from .transfer import inventory, read_data, archive_inventory, digest_file, LOCK

SUPPORTED_HEADS = {frozenset({v}) for v in ('0001','0002','0003','0004','0005','0006','0007','ddn_0001','ddn_0002',REVISION)} | {
    frozenset({'0007','ddn_0001'}),frozenset({'0007','ddn_0002'})}


def heads(connection):
    values=frozenset(connection.execute(text('SELECT version_num FROM alembic_version')).scalars())
    if values not in SUPPORTED_HEADS:
        raise ValueError('Revisões desconhecidas ou combinação de heads inválida.')
    return values


def revision(connection):
    values=sorted(heads(connection))
    return values[0] if len(values)==1 else values


def preview(engine):
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        old=revision(connection)
        changes=[] if old==REVISION else ['junção 0007 + ddn_0002; auditoria imutável; contextos e atribuições físicas exclusivos; valores de negócio preservados']
        return {'from':old,'to':REVISION,'schema_changes':changes,
                'data':inventory(read_data(connection)),'existing_data_changes':'none'}


def apply_upgrade(engine, folder):
    if engine.dialect.name!='postgresql':raise ValueError('Atualização operacional exige PostgreSQL.')
    folder=private_folder(folder)
    manifest=json.loads(owned_file(folder,'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format') not in (1,2) or digest_file(owned_file(folder,'database.dump'))!=manifest['archive_sha256']:
        raise ValueError('Backup inválido ou alterado.')
    verified=False
    for path in folder.glob('restore-*.json'):
        report=json.loads(owned_file(folder,path.name).read_text(encoding='utf-8'))
        if (report.get('status')=='reconciled' and report.get('different_tables')==[]
            and report.get('archive_sha256')==manifest['archive_sha256'] and report.get('inventory')==manifest['inventory']):
            verified=True
    if not verified:raise ValueError('É necessário um relatório de restauro reconciliado deste backup.')
    with engine.begin() as connection:
        connection.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':LOCK})
        connection.execute(text('SELECT pg_advisory_xact_lock(72834001)'))
        old=heads(connection)
        if old==frozenset({REVISION}):return {'status':'already_current','revision':REVISION}
        if archive_inventory(connection,manifest['inventory'])!=manifest['inventory']:
            raise ValueError('A base mudou desde o backup. Parar aplicação, fazer novo backup e ensaiar restauro.')
        before=inventory(read_data(connection))
        cfg=config();cfg.attributes['connection']=connection
        command.upgrade(cfg,REVISION)
        if heads(connection)!=frozenset({REVISION}) or inventory(read_data(connection))!=before:
            raise ValueError('Dados divergentes após atualização. Transação revertida.')
    return {'status':'upgraded','revision':REVISION,'existing_data_changes':'none'}
