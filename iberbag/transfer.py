"""Offline v2.3 test-data transfer. Never opens the source SQLite for writing."""
import argparse
from collections import Counter
from datetime import date, datetime
import hashlib
import base64
import json
import os
from pathlib import Path
import sqlite3

from alembic import command
from sqlalchemy import create_engine, inspect, select, text
from .database import Base
from .migrations import config, REVISION

TABLES = list(Base.metadata.sorted_tables)
LOCK = 72834002
RECEIVING_TABLES = {'receipts', 'receipt_lines', 'putaways'}
DISPATCH_TABLES = {'dispatch_orders', 'dispatch_lines', 'dispatch_picks'}
TRANSFER_TABLES = {'stock_transfers'}
INVENTORY_TABLES = {'lot_resolutions','dispatch_documents','preparation_completions','location_clocks','physical_inventories','inventory_scopes','inventory_counts','stock_incidents','stock_adjustments','preparation_compensations'}
COMMERCIAL_TABLES = {'mercadona_destinations','commercial_preparations','customer_pending','pending_allocations','allocation_collections','receipt_documents','incomplete_compositions'}
OPERATIONAL_TABLES = RECEIVING_TABLES | DISPATCH_TABLES | TRANSFER_TABLES | INVENTORY_TABLES | COMMERCIAL_TABLES


def digest_file(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value,(bytes,bytearray,memoryview)):
        return base64.b64encode(value).decode('ascii')
    raise TypeError('Tipo de dados não suportado.')


def fingerprint(rows):
    payload = json.dumps(rows, default=json_value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode()).hexdigest()


def read_data(connection):
    inspector = inspect(connection)
    names = set(inspector.get_table_names())
    expected = {t.name for t in TABLES}
    missing = expected - names
    legacy_revision = {frozenset(OPERATIONAL_TABLES): '0001',
        frozenset(DISPATCH_TABLES | TRANSFER_TABLES | INVENTORY_TABLES | COMMERCIAL_TABLES): '0002',
        frozenset(TRANSFER_TABLES | INVENTORY_TABLES | COMMERCIAL_TABLES): '0003',
        frozenset(INVENTORY_TABLES | COMMERCIAL_TABLES): '0004',
        frozenset(COMMERCIAL_TABLES): '0005', frozenset(): REVISION}.get(frozenset(missing))
    if names - expected - {'alembic_version'} or legacy_revision is None or inspector.get_view_names():
        raise ValueError('Esquema diferente da v2.3: tabelas em falta, adicionais ou vistas. Rever manualmente.')
    if 'alembic_version' in names:
        versions = connection.execute(text('SELECT version_num FROM alembic_version')).scalars().all()
        if versions not in ([[legacy_revision], ['0006']] if not missing else [[legacy_revision]]):
            raise ValueError('Revisão incompatível.')
    if connection.dialect.name == 'sqlite':
        if connection.scalar(text('SELECT count(*) FROM users WHERE active NOT IN (0, 1) OR active IS NULL')):
            raise ValueError('Valor booleano inválido em users.active; não será convertido.')
        if 'active' in {c['name'] for c in inspector.get_columns('locations')}:
            if connection.scalar(text('SELECT count(*) FROM locations WHERE active NOT IN (0, 1) OR active IS NULL')):
                raise ValueError('Valor booleano inválido em locations.active; não será convertido.')
    result = {}
    for table in TABLES:
        if table.name in missing:
            result[table.name] = []
            continue
        columns = {c['name'] for c in inspector.get_columns(table.name)}
        legacy_locations = table.name == 'locations' and legacy_revision in ('0001','0002','0003') and columns == set(table.columns.keys()) - {'active'}
        defaults={}
        if table.name=='dispatch_orders' and legacy_revision in ('0001','0002','0003','0004'):
            defaults=dict(office=False,cancellation_reason=None,cancelled_by=None,cancelled_at=None)
        if table.name=='dispatch_lines' and legacy_revision in ('0001','0002','0003','0004','0005'):
            defaults=dict(destination='',destination_order=0,destination_highlight=False)
        if columns != set(table.columns.keys())-set(defaults) and not legacy_locations:
            raise ValueError(f'Colunas incompatíveis: {table.name}.')
        query = select(*(col for col in table.columns if col.name in columns)).order_by(*table.primary_key.columns)
        result[table.name] = [dict(row) for row in connection.execute(query).mappings()]
        for row in result[table.name]:row.update(defaults)
        if legacy_locations:
            for row in result[table.name]:
                row['active'] = True  # All catalogue positions were implicitly active before 0004.
    if not any(row['key'] == 'warehouse_layout_seeded_vba' and row['value'] == '1'
               for row in result['app_config']):
        raise ValueError('Catálogo v2.3 não inicializado. Não será deduzido automaticamente.')
    return result


def inventory(data):
    stock = data['stock']
    occupancy = Counter()
    for row in stock:
        occupancy[row['local']] += row['quantidade']
    from .location_rules import capacity_for
    return {
        'tables': {name: {'rows': len(rows), 'sha256': fingerprint(rows)} for name, rows in data.items()},
        'stock': {key: len({row[key] for row in stock}) for key in ('artigo', 'lote', 'carga', 'local')},
        'paletes': sum(row['quantidade'] for row in stock),
        'associacoes_historicas_lote_contraditorias': sum(len({r['artigo'] for name in ('stock','receipt_lines','dispatch_lines','stock_transfers') for r in data[name] if r['lote']==lot})>1 for lot in {r['lote'] for name in ('stock','receipt_lines','dispatch_lines','stock_transfers') for r in data[name]}),
        'cargas_sem_data': sum(row['entrada'] is None for row in data['cargas']),
        'cargas_sem_responsavel': sum(row['funcionario'] is None for row in data['cargas']),
        'locais_acima_capacidade': sum(capacity_for(loc) == 1 and qty > 1 for loc, qty in occupancy.items()),
        'stock_sem_catalogo': sum(row['local'] not in {r['codigo'] for r in data['locations']} for row in stock),
        'stock_sem_carga': sum(row['carga'] not in {r['codigo'] for r in data['cargas']} for row in stock),
    }


def differences(expected, actual):
    return [name for name in expected['tables'] if expected['tables'][name] != actual['tables'].get(name)]


def save_json(path, data):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)


def preview(source, bundle):
    source, bundle = Path(source).resolve(strict=True), Path(bundle)
    bundle.mkdir(parents=True, exist_ok=False, mode=0o700)
    snapshot = bundle / 'source.sqlite'
    # SQLite backup API includes committed WAL data and creates a consistent image.
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as origin:
        origin.execute('PRAGMA query_only=ON')
        with sqlite3.connect(snapshot) as copy:
            origin.backup(copy)
            if copy.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError('SQLite com falha de integridade.')
            if copy.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('SQLite com referências inválidas.')
    data = snapshot_data(snapshot)
    expected = dict(data, sessions=[])
    report = {'format': 1, 'scope': 'test-only', 'snapshot_sha256': digest_file(snapshot),
              'source': inventory(data), 'expected': inventory(expected),
              'sessions_revoked': len(data['sessions']),
              'policy': 'Preservar todos os campos e históricos; não copiar sessões; não corrigir anomalias.'}
    save_json(bundle / 'preview.json', report)
    return report


def snapshot_data(snapshot):
    uri = Path(snapshot).resolve().as_uri() + '?mode=ro'
    engine = create_engine('sqlite://', creator=lambda: sqlite3.connect(uri, uri=True))
    try:
        with engine.connect() as connection:
            return read_data(connection)
    finally:
        engine.dispose()


def load_bundle(bundle):
    bundle = Path(bundle)
    report = json.loads((bundle / 'preview.json').read_text(encoding='utf-8'))
    snapshot = bundle / 'source.sqlite'
    if report.get('format') != 1 or report.get('scope') != 'test-only':
        raise ValueError('Pré-visualização inválida.')
    if digest_file(snapshot) != report['snapshot_sha256']:
        raise ValueError('Cópia SQLite alterada após a pré-visualização.')
    data = snapshot_data(snapshot)
    if inventory(data) != report['source'] or inventory(dict(data, sessions=[])) != report['expected']:
        raise ValueError('Relatório não corresponde à cópia SQLite.')
    return dict(data, sessions=[]), report


def require_test_target(engine):
    if engine.url.drivername != 'postgresql+psycopg' or not (engine.url.database or '').endswith('_test'):
        raise ValueError('Destino exige PostgreSQL e nome terminado em _test. Produção não é suportada.')
    if engine.url.query:
        raise ValueError('Parâmetros de ligação personalizados não são aceites no destino de teste.')


def require_empty(connection):
    # Refuse objects in any user schema, not just the search_path.
    count = connection.scalar(text("""SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%'
        AND c.relkind IN ('r','p','v','m','S','f')"""))
    if count:
        raise ValueError('Destino não vazio. Criar outra base de teste; não apagar nem reimportar.')


def apply(bundle, engine):
    require_test_target(engine)
    data, report = load_bundle(bundle)
    with engine.begin() as connection:
        connection.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': LOCK})
        require_empty(connection)
        cfg = config()
        cfg.attributes['connection'] = connection
        command.upgrade(cfg, 'head')
        connection.execute(text('ALTER TABLE stock DISABLE TRIGGER stock_clock'))
        connection.execute(text('ALTER TABLE locations DISABLE TRIGGER catalogue_clock'))
        for table in TABLES:
            if data[table.name]:
                rows = data[table.name]
                if table.name == 'receipts':
                    rows = [dict(row, status='draft', carga_id=None, receiver=None, confirmed_at=None) for row in rows]
                if table.name == 'dispatch_orders':
                    rows = [dict(row, status='draft', reviewed_at=None, checked_by=None, checked_at=None, discrepancy=None, confirmed_by=None, confirmed_at=None) for row in rows]
                if table.name=='physical_inventories':
                    rows=[dict(row,status='draft',submitted_by=None,submitted_at=None,applied_by=None,applied_at=None,reason=None) for row in rows]
                connection.execute(table.insert(), rows)
            if 'id' in table.columns:
                maximum = max((row['id'] for row in data[table.name]), default=0)
                sequence = connection.scalar(text('SELECT pg_get_serial_sequence(:table, :column)'),
                                             {'table': table.name, 'column': 'id'})
                connection.execute(text('SELECT setval(CAST(:seq AS regclass), :value, :called)'),
                                   {'seq': sequence, 'value': max(1, maximum), 'called': maximum > 0})
        from .database import Receipt
        for row in data['receipts']:
            if row['status'] == 'confirmed':
                connection.execute(Receipt.__table__.update().where(Receipt.id == row['id']).values(**row))
        from .database import DispatchOrder
        for row in data['dispatch_orders']:
            if row['status'] != 'draft':
                connection.execute(DispatchOrder.__table__.update().where(DispatchOrder.id == row['id']).values(**row))
        from .database import PhysicalInventory
        for row in data['physical_inventories']:
            if row['status']!='draft':
                connection.execute(PhysicalInventory.__table__.update().where(PhysicalInventory.id==row['id']).values(**row))
        connection.execute(text('ALTER TABLE stock ENABLE TRIGGER stock_clock'))
        connection.execute(text('ALTER TABLE locations ENABLE TRIGGER catalogue_clock'))
        actual = inventory(read_data(connection))
        changed = differences(report['expected'], actual)
        if changed:
            raise ValueError('Reconciliação falhou: ' + ', '.join(changed))
    return {'status': 'reconciled', 'different_tables': [], 'inventory': actual,
            'sessions_revoked': report['sessions_revoked']}


def reconcile(bundle, engine):
    require_test_target(engine)
    _, report = load_bundle(bundle)
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        actual = inventory(read_data(connection))
    changed = differences(report['expected'], actual)
    return {'status': 'different' if changed else 'reconciled', 'different_tables': changed, 'inventory': actual}


def main():
    parser = argparse.ArgumentParser(description='Migração v2.3: apenas dados fictícios de teste.')
    parser.add_argument('action', choices=['preview', 'apply', 'reconcile'])
    parser.add_argument('--source', type=Path)
    parser.add_argument('--bundle', required=True, type=Path)
    parser.add_argument('--test-data-only', required=True, action='store_true')
    args = parser.parse_args()
    try:
        if args.action == 'preview':
            if not args.source:
                parser.error('--source é obrigatório na pré-visualização')
            report = preview(args.source, args.bundle)
        else:
            from .server import configure
            configure()
            engine = create_engine(os.environ['WMS_DATABASE_URL'])
            try:
                report = apply(args.bundle, engine) if args.action == 'apply' else reconcile(args.bundle, engine)
            finally:
                engine.dispose()
            save_json(args.bundle / (args.action + '-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.json'), report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if report.get('status') == 'different':
            raise SystemExit(1)
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, f'Operação recusada: {error}\n')
    except Exception:
        # Database exceptions may contain SQL parameters (password hashes/history).
        parser.exit(1, 'Operação falhou; verificar ligação, esquema e tipos de dados. Nenhuma credencial foi registada.\n')


if __name__ == '__main__':
    main()
