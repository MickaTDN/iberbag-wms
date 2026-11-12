"""Offline v2.3 test-data transfer. Never opens the source SQLite for writing."""
import argparse
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
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
# Claims must precede locations/stock: their insertion triggers also register claims.
_claim = next(t for t in TABLES if t.name == 'physical_location_claims')
TABLES.remove(_claim)
TABLES.insert(next(i for i,t in enumerate(TABLES) if t.name == 'storage_contexts')+1, _claim)
PHYSICAL_TABLES = {'physical_location_claims'}
COSMO_TABLES = {'cosmo_receptions','cosmo_pallets','cosmo_states','cosmo_operations','cosmo_reviews'}
SHARED_TABLES = {'physical_sharing_reviews','physical_locations','physical_location_access','physical_location_state','physical_location_events'}
NUMBER_TABLES = {'load_numbers'}
VALMET_TABLES = {'valmet_owners','valmet_entries','valmet_units','valmet_numbers','valmet_identifications','valmet_items'}
PLANNING_TABLES = {'valmet_preparations','valmet_plan_revisions','valmet_reservations','valmet_handling'}
SHIPPING_TABLES = {'valmet_positions','valmet_shipments','valmet_shipment_lines','valmet_occurrences','valmet_relocations','valmet_documents'}
ALCOBRE_INVENTORY_TABLES = {'alcobre_inventories','alcobre_inventory_checks','alcobre_inventory_reviews','alcobre_inventory_operations','alcobre_inventory_documents'}
ALCOBRE_DISPATCH_TABLES = {'alcobre_dispatches','alcobre_dispatch_revisions','alcobre_dispatch_operations','alcobre_dispatch_events','alcobre_dispatch_documents'}
ALCOBRE_TABLES = {'alcobre_owners','alcobre_zones','alcobre_slots','alcobre_entries','alcobre_reviews','alcobre_actions','alcobre_bobines','alcobre_placements'}
NKT_INVENTORY_TABLES = {'nkt_inventories','nkt_inventory_checks','nkt_inventory_reviews','nkt_inventory_incidents'}
NKT_LOADING_TABLES = {'nkt_preparations','nkt_plan_revisions','nkt_operations','nkt_corrections','nkt_stock_events','nkt_documents'}
NKT_RECEIVING_TABLES = {'nkt_receivings','nkt_receiving_reviews','nkt_containers'}
NKT_TABLES = {'nkt_owners','nkt_entries','nkt_reviews','nkt_pallets'}
IMPORT_TABLES = {'valmet_import_drafts','valmet_import_revisions','valmet_import_confirmations'}
ROLL_TABLES = {'valmet_rolls','valmet_roll_states','valmet_roll_events'}
CONTROL_TABLES = {'valmet_inventories','valmet_counts','valmet_control_drafts','valmet_control_moves','valmet_inventory_decisions','valmet_control_documents'}
STACKED_DEFAULTS = {name: {'stacked': False} for name in ('stock','putaways','dispatch_lines','stock_transfers','inventory_counts','stock_adjustments')}
STACKED_DEFAULTS['putaways'].update(document_pdf=None, document_sha256=None)
STACKED_DEFAULTS['dispatch_orders'] = {'typed_selection': False}
LOCK = 72834002
RECEIVING_TABLES = {'receipts', 'receipt_lines', 'putaways'}
DISPATCH_TABLES = {'dispatch_orders', 'dispatch_lines', 'dispatch_picks'}
TRANSFER_TABLES = {'stock_transfers'}
INVENTORY_TABLES = {'lot_resolutions','dispatch_documents','preparation_completions','location_clocks','physical_inventories','inventory_scopes','inventory_counts','stock_incidents','stock_adjustments','preparation_compensations'}
COMMERCIAL_TABLES = {'mercadona_destinations','commercial_preparations','customer_pending','pending_allocations','allocation_collections','receipt_documents','incomplete_compositions'}
CONTEXT_TABLES = {'warehouses','clients','storage_contexts'}
OPERATIONAL_TABLES = RECEIVING_TABLES | DISPATCH_TABLES | TRANSFER_TABLES | INVENTORY_TABLES | COMMERCIAL_TABLES


def digest_file(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def json_value(value):
    if isinstance(value,Decimal):
        return str(value)  # Preserve exact m²; never convert through binary float.
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
    missing_legacy = missing - COSMO_TABLES - SHARED_TABLES - CONTEXT_TABLES - PHYSICAL_TABLES - NUMBER_TABLES - VALMET_TABLES - PLANNING_TABLES - SHIPPING_TABLES - CONTROL_TABLES - ROLL_TABLES - IMPORT_TABLES - NKT_TABLES - NKT_RECEIVING_TABLES - NKT_LOADING_TABLES - NKT_INVENTORY_TABLES - ALCOBRE_TABLES - ALCOBRE_DISPATCH_TABLES - ALCOBRE_INVENTORY_TABLES
    if missing & COSMO_TABLES not in (set(),COSMO_TABLES):raise ValueError('Estrutura Cosmopak incompleta.')
    if not missing & COSMO_TABLES and missing & SHARED_TABLES:raise ValueError('Cosmopak sem motor físico comum.')
    if missing & SHARED_TABLES not in (set(),SHARED_TABLES):raise ValueError('Estrutura física comum incompleta.')
    if not missing & SHARED_TABLES and missing & ALCOBRE_INVENTORY_TABLES:raise ValueError('Partilha sem esquema anterior completo.')
    if missing & ALCOBRE_INVENTORY_TABLES not in (set(),ALCOBRE_INVENTORY_TABLES):raise ValueError('Estrutura de inventários Alcobre incompleta.')
    if not missing & ALCOBRE_INVENTORY_TABLES and missing & ALCOBRE_DISPATCH_TABLES:raise ValueError('Inventários Alcobre sem livro de estados.')
    if missing & ALCOBRE_DISPATCH_TABLES not in (set(),ALCOBRE_DISPATCH_TABLES):raise ValueError('Estrutura de saídas Alcobre incompleta.')
    if not missing & ALCOBRE_DISPATCH_TABLES and missing & ALCOBRE_TABLES:raise ValueError('Saídas Alcobre sem entradas.')
    if missing & ALCOBRE_TABLES not in (set(),ALCOBRE_TABLES):raise ValueError('Estrutura Alcobre incompleta.')
    if not missing & ALCOBRE_TABLES and missing & NKT_INVENTORY_TABLES:raise ValueError('Alcobre sem esquema anterior completo.')
    if missing & NKT_INVENTORY_TABLES not in (set(),NKT_INVENTORY_TABLES):raise ValueError('Estrutura de inventários NKT incompleta.')
    if not missing & NKT_INVENTORY_TABLES and missing & NKT_LOADING_TABLES:raise ValueError('Inventários sem livro de stock NKT.')
    if missing & NKT_LOADING_TABLES not in (set(),NKT_LOADING_TABLES):raise ValueError('Estrutura de carregamentos NKT incompleta.')
    if not missing & NKT_LOADING_TABLES and missing & NKT_RECEIVING_TABLES:raise ValueError('Carregamentos sem entradas/contentores NKT.')
    if missing & NKT_RECEIVING_TABLES not in (set(),NKT_RECEIVING_TABLES):raise ValueError('Estrutura de receções NKT incompleta.')
    if not missing & NKT_RECEIVING_TABLES and missing & NKT_TABLES:raise ValueError('Receções sem livro NKT.')
    if missing & NKT_TABLES not in (set(),NKT_TABLES):raise ValueError('Estrutura NKT incompleta.')
    if missing & IMPORT_TABLES not in (set(),IMPORT_TABLES):raise ValueError('Esquema de importação Valmet incompleto.')
    if not missing & IMPORT_TABLES and missing & (ROLL_TABLES|CONTROL_TABLES):raise ValueError('Importação sem estrutura Valmet completa.')
    if missing & ROLL_TABLES not in (set(),ROLL_TABLES):raise ValueError('Esquema de rolos individuais incompleto.')
    if not missing & ROLL_TABLES and missing & (CONTROL_TABLES|SHIPPING_TABLES|PLANNING_TABLES|VALMET_TABLES):raise ValueError('Rolos sem estrutura Valmet completa.')
    if missing & CONTROL_TABLES not in (set(),CONTROL_TABLES):raise ValueError('Esquema de controlo Valmet incompleto.')
    if not missing & CONTROL_TABLES and missing & (SHIPPING_TABLES|PLANNING_TABLES|VALMET_TABLES|NUMBER_TABLES|PHYSICAL_TABLES|CONTEXT_TABLES):raise ValueError('Controlo sem estrutura Valmet completa.')
    if missing & SHIPPING_TABLES not in (set(),SHIPPING_TABLES):raise ValueError('Esquema de expedições incompleto.')
    if not missing & SHIPPING_TABLES and missing & (PLANNING_TABLES|VALMET_TABLES|NUMBER_TABLES|PHYSICAL_TABLES|CONTEXT_TABLES):raise ValueError('Expedições sem estrutura Valmet completa.')
    if missing & PLANNING_TABLES not in (set(),PLANNING_TABLES):raise ValueError('Esquema de reservas incompleto.')
    if not missing & PLANNING_TABLES and missing & (VALMET_TABLES|NUMBER_TABLES|PHYSICAL_TABLES|CONTEXT_TABLES):
        raise ValueError('Reservas sem esquema multicliente/Valmet completo.')
    if missing & VALMET_TABLES not in (set(),VALMET_TABLES):
        raise ValueError('Esquema Valmet incompleto.')
    legacy_revision = {frozenset(OPERATIONAL_TABLES): '0001',
        frozenset(DISPATCH_TABLES | TRANSFER_TABLES | INVENTORY_TABLES | COMMERCIAL_TABLES): '0002',
        frozenset(TRANSFER_TABLES | INVENTORY_TABLES | COMMERCIAL_TABLES): '0003',
        frozenset(INVENTORY_TABLES | COMMERCIAL_TABLES): '0004',
        frozenset(COMMERCIAL_TABLES): '0005', frozenset(): ('0006' if missing & CONTEXT_TABLES else ('ddn_0001' if missing & PHYSICAL_TABLES else REVISION))}.get(frozenset(missing_legacy))
    if names - expected - {'alembic_version'} or legacy_revision is None or inspector.get_view_names():
        raise ValueError('Esquema diferente da v2.3: tabelas em falta, adicionais ou vistas. Rever manualmente.')
    if missing & CONTEXT_TABLES not in (set(),CONTEXT_TABLES):
        raise ValueError('Catálogo multicliente incompleto.')
    if 'alembic_version' in names:
        versions = frozenset(connection.execute(text('SELECT version_num FROM alembic_version')).scalars())
        if missing & CONTEXT_TABLES:
            allowed = {frozenset({legacy_revision})}
            if legacy_revision == '0006':allowed.add(frozenset({'0007'}))
        elif missing & PHYSICAL_TABLES:
            allowed = {frozenset({'ddn_0001'}),frozenset({'ddn_0001','0007'})}
        else:
            allowed = ({frozenset({'ddn_0002'}),frozenset({'ddn_0002','0007'}),frozenset({'ddn_security_0001'})}
                       if missing & NUMBER_TABLES else ({frozenset({'ddn_0003'})} if missing & VALMET_TABLES else
                       ({frozenset({'ddn_0004'}),frozenset({'ddn_0005'}),frozenset({'ddn_0006'})} if missing & PLANNING_TABLES else
                       ({frozenset({'ddn_0007'})} if missing & SHIPPING_TABLES else ({frozenset({'ddn_0008'})} if missing & CONTROL_TABLES else ({frozenset({'ddn_0009'})} if missing & ROLL_TABLES else ({frozenset({'ddn_0010'})} if missing & IMPORT_TABLES else ({frozenset({'ddn_0011'})} if missing & NKT_TABLES else ({frozenset({'ddn_0012'})} if missing & NKT_RECEIVING_TABLES else ({frozenset({'ddn_0013'})} if missing & NKT_LOADING_TABLES else ({frozenset({'ddn_0014'})} if missing & NKT_INVENTORY_TABLES else ({frozenset({'ddn_0015'})} if missing & ALCOBRE_TABLES else ({frozenset({'ddn_0016'})} if missing & ALCOBRE_DISPATCH_TABLES else ({frozenset({'ddn_0017'})} if missing & ALCOBRE_INVENTORY_TABLES else ({frozenset({'ddn_0018'})} if 'stacked' not in {c['name'] for c in inspector.get_columns('stock')} else ({frozenset({'ddn_0019'})} if missing & SHARED_TABLES else ({frozenset({'ddn_0020'})} if missing & COSMO_TABLES else {frozenset({REVISION})})))))))))))))))))
        if versions not in allowed:
            raise ValueError('Revisão/heads incompatíveis com o esquema. Não usar stamp para contornar.')
    if connection.dialect.name == 'sqlite':
        if connection.scalar(text('SELECT count(*) FROM users WHERE active NOT IN (0, 1) OR active IS NULL')):
            raise ValueError('Valor booleano inválido em users.active; não será convertido.')
        if 'active' in {c['name'] for c in inspector.get_columns('locations')}:
            if connection.scalar(text('SELECT count(*) FROM locations WHERE active NOT IN (0, 1) OR active IS NULL')):
                raise ValueError('Valor booleano inválido em locations.active; não será convertido.')
    has_stacked = 'stacked' in {c['name'] for c in inspector.get_columns('stock')}
    result = {}
    for table in TABLES:
        if table.name in missing:
            result[table.name] = []
            continue
        columns = {c['name'] for c in inspector.get_columns(table.name)}
        legacy_locations = table.name == 'locations' and legacy_revision in ('0001','0002','0003') and columns == set(table.columns.keys()) - {'active','context_id'}
        defaults={}
        for field, value in STACKED_DEFAULTS.get(table.name, {}).items():
            if field not in columns:
                if has_stacked:
                    raise ValueError('Estrutura de paletes sobrepostas incompleta: '+table.name)
                defaults[field] = value
        if table.name=='alcobre_dispatch_events' and missing & ALCOBRE_INVENTORY_TABLES:defaults.update(control_operation_id=None,local=None)
        if 'context_id' in table.c and 'context_id' not in columns:
            defaults['context_id']=1
        if missing & NKT_INVENTORY_TABLES:
            if table.name=='nkt_operations':defaults.update(inventory_id=None,inventory_review_id=None)
            if table.name=='nkt_documents':defaults['inventory_review_id']=None
        if table.name=='dispatch_orders' and legacy_revision in ('0001','0002','0003','0004'):
            defaults.update(office=False,cancellation_reason=None,cancelled_by=None,cancelled_at=None)
        if table.name=='dispatch_lines' and legacy_revision in ('0001','0002','0003','0004','0005'):
            defaults.update(destination='',destination_order=0,destination_highlight=False)
        if columns != set(table.columns.keys())-set(defaults) and not legacy_locations:
            raise ValueError(f'Colunas incompatíveis: {table.name}.')
        query = select(*(col for col in table.columns if col.name in columns)).order_by(*(c for c in table.primary_key.columns if c.name in columns))
        result[table.name] = [dict(row) for row in connection.execute(query).mappings()]
        for row in result[table.name]:row.update(defaults)
        if legacy_locations:
            for row in result[table.name]:
                row['active'] = True  # All catalogue positions were implicitly active before 0004.
    if missing & CONTEXT_TABLES:
        from .contexts import WAREHOUSES, CLIENTS
        result['warehouses']=[dict(id=i,name=n,active=True) for i,n in WAREHOUSES]
        result['clients']=[dict(id=i,name=n,tracking=t,unit=u,pallet_operations=e,active=True) for i,n,t,u,e,w in CLIENTS]
        result['storage_contexts']=[dict(id=i,client_id=i,warehouse_id=w,area=n,active=True) for i,n,t,u,e,w in CLIENTS]
    if missing & PHYSICAL_TABLES:
        from .physical_locations import expected_claims
        result['physical_location_claims'] = expected_claims(result)
    if not any(row['key'] == 'warehouse_layout_seeded_vba' and row['value'] == '1'
               for row in result['app_config']):
        raise ValueError('Catálogo v2.3 não inicializado. Não será deduzido automaticamente.')
    if missing & VALMET_TABLES:
        result['valmet_owners']=[dict(client_id=row['id']) for row in result['clients'] if row['name']=='Valmet']
    if missing & SHIPPING_TABLES:result['valmet_positions']=initial_valmet_positions(result)
    else:
        for row in result['valmet_positions']:row['materials']=json.dumps(json.loads(row['materials']),sort_keys=True,ensure_ascii=False)
    if missing & NKT_TABLES:
        result['nkt_owners']=[dict(client_id=row['id']) for row in result['clients'] if row['name']=='NKT']
    if missing & ALCOBRE_TABLES:
        result['alcobre_owners'],result['alcobre_zones']=initial_alcobre_catalogue(result)
    from .cosmopak import validate_snapshot as validate_cosmo
    validate_cosmo(result)
    from .physical_locations import validate_shared_snapshot
    validate_shared_snapshot(result)
    return result

def initial_alcobre_catalogue(data):
    owners=[dict(client_id=c['id'],warehouse_id=w['id']) for c in data['clients'] for w in data['warehouses'] if c['name']=='Alcobre' and w['name']=='Ovar']
    contexts=[c for c in data['storage_contexts'] if any(c['client_id']==o['client_id'] and c['warehouse_id']==o['warehouse_id'] for o in owners)]
    zones=[dict(id=i,context_id=c['id'],code=code,positions=15,active=True) for c in contexts for i,code in enumerate('ABCDEFGHIJKLMN',1)]
    return owners,zones

def initial_valmet_positions(data):
    from types import SimpleNamespace
    from .valmet_planning import materials_for
    items=data['valmet_items'];rows=[]
    for unit in data['valmet_units']:
        content=[SimpleNamespace(**i) for i in items if i['unit_id']==unit['id'] and i['identification_id'] is not None]
        materials=materials_for(SimpleNamespace(**unit),content)
        rows.append(dict(id=unit['id'],context_id=unit['context_id'],unit_id=unit['id'],root=True,local=unit['local'],
            physical_quantity=unit['physical_quantity'],materials=json.dumps(materials,sort_keys=True,ensure_ascii=False),uncertain=False))
    return rows


def inventory(data):
    stock = data['stock']
    occupancy = Counter()
    for row in stock:
        occupancy[(row.get('context_id',1),row['local'])] += row['quantidade']
    capacities={(r.get('context_id',1),r['codigo']):r['capacidade'] for r in data['locations']}
    return {
        'tables': {name: {'rows': len(rows), 'sha256': fingerprint(rows)} for name, rows in data.items()},
        'stock': {key: len({row[key] for row in stock}) for key in ('artigo', 'lote', 'carga', 'local')},
        'paletes': sum(row['quantidade'] for row in stock),
        'associacoes_historicas_lote_contraditorias': sum(len({r['artigo'] for name in ('stock','receipt_lines','dispatch_lines','stock_transfers') for r in data[name] if (r.get('context_id',1),r['lote'])==lot})>1 for lot in {(r.get('context_id',1),r['lote']) for name in ('stock','receipt_lines','dispatch_lines','stock_transfers') for r in data[name]}),
        'cargas_sem_data': sum(row['entrada'] is None for row in data['cargas']),
        'cargas_sem_responsavel': sum(row['funcionario'] is None for row in data['cargas']),
        'locais_acima_capacidade': sum(capacities.get(loc) is not None and qty > capacities[loc] for loc, qty in occupancy.items()),
        'stock_sem_catalogo': sum((row.get('context_id',1),row['local']) not in {(r.get('context_id',1),r['codigo']) for r in data['locations']} for row in stock),
        'stock_sem_carga': sum((row.get('context_id',1),row['carga']) not in {(r.get('context_id',1),r['codigo']) for r in data['cargas']} for row in stock),
    }



def archive_inventory(connection, expected):
    """Reconcile historical archives in their original authenticated representation.

    New backups always use normalized inventory. Legacy PR9 archives predate
    context columns/catalogues. Only an actual pre-DDN schema may be projected;
    never hide another client's data by accepting an arbitrary table subset.
    """
    data = read_data(connection)
    # Project only additive default metadata when it exactly matches an authenticated
    # historical table digest. Never conceal pairs, new PDFs or typed preparations.
    pre_context_schema = not inspect(connection).has_table('storage_contexts')
    for name, defaults in STACKED_DEFAULTS.items():
        old = expected.get('tables', {}).get(name)
        if old and fingerprint(data[name]) != old['sha256']:
            if all(all(row.get(k) == v for k,v in defaults.items()) for row in data[name]):
                projected = [{k:v for k,v in row.items() if k not in defaults} for row in data[name]]
                old_context_projection = pre_context_schema and all(row.get('context_id',1)==1 for row in projected) and fingerprint([{k:v for k,v in row.items() if k!='context_id'} for row in projected])==old['sha256']
                if fingerprint(projected) == old['sha256'] or old_context_projection: data[name] = projected
    wanted = set(expected.get('tables', {}))
    current = {t.name for t in TABLES}
    if not wanted & COSMO_TABLES:
        if any(data[n] for n in COSMO_TABLES):raise ValueError('Backup antigo não abrange receções Cosmopak.')
        current-=COSMO_TABLES
    if not wanted & SHARED_TABLES:
        if any(data[n] for n in SHARED_TABLES):raise ValueError('Backup antigo não abrange partilha e ocupação física.')
        current-=SHARED_TABLES
    if not wanted & ALCOBRE_INVENTORY_TABLES:
        if any(data[n] for n in ALCOBRE_INVENTORY_TABLES) or any(r.get('control_operation_id') is not None or r.get('local') is not None for r in data['alcobre_dispatch_events']):raise ValueError('Backup antigo não abrange inventários/correções Alcobre.')
        current-=ALCOBRE_INVENTORY_TABLES
        old=expected.get('tables',{}).get('alcobre_dispatch_events')
        if old and fingerprint(data['alcobre_dispatch_events'])!=old['sha256']:
            data['alcobre_dispatch_events']=[{k:v for k,v in r.items() if k not in {'control_operation_id','local'}} for r in data['alcobre_dispatch_events']]
    if not wanted & ALCOBRE_DISPATCH_TABLES:

        if any(data[n] for n in ALCOBRE_DISPATCH_TABLES):raise ValueError('Backup anterior não abrange saídas Alcobre.')
        current-=ALCOBRE_DISPATCH_TABLES
    if not wanted & ALCOBRE_TABLES:
        owners,zones=initial_alcobre_catalogue(data)
        if any(data[n] for n in ALCOBRE_TABLES-{'alcobre_owners','alcobre_zones'}) or data['alcobre_owners']!=owners or data['alcobre_zones']!=zones:raise ValueError('Backup anterior não abrange dados ou catálogo Alcobre.')
        current-=ALCOBRE_TABLES
    if not wanted & NKT_INVENTORY_TABLES:
        if any(data[name] for name in NKT_INVENTORY_TABLES):raise ValueError('Backup anterior não abrange inventários NKT.')
        current-=NKT_INVENTORY_TABLES
    if not wanted & NKT_LOADING_TABLES:
        if any(data[name] for name in NKT_LOADING_TABLES):raise ValueError('Backup anterior não inclui carregamentos NKT.')
        current-=NKT_LOADING_TABLES
    if not wanted & NKT_RECEIVING_TABLES:
        if any(data[name] for name in NKT_RECEIVING_TABLES):raise ValueError('Backup anterior não inclui receções NKT por contentor.')
        current-=NKT_RECEIVING_TABLES
    if not wanted & NKT_TABLES:
        if any(data[name] for name in NKT_TABLES-{'nkt_owners'}):raise ValueError('Backup anterior não inclui entradas NKT.')
        current-=NKT_TABLES
    if not wanted & IMPORT_TABLES:
        if any(data[name] for name in IMPORT_TABLES):raise ValueError('Backup anterior não inclui importações históricas.')
        current-=IMPORT_TABLES
    if not wanted & ROLL_TABLES:
        if any(data[name] for name in ROLL_TABLES):raise ValueError('Backup anterior não inclui identidades de rolos.')
        current-=ROLL_TABLES
    if not wanted & CONTROL_TABLES:
        if any(data[name] for name in CONTROL_TABLES):raise ValueError('Backup anterior não abrange inventários/controlo Valmet novos.')
        current-=CONTROL_TABLES
    if not wanted & SHIPPING_TABLES:
        if any(data[name] for name in SHIPPING_TABLES-{'valmet_positions'}) or data['valmet_positions']!=initial_valmet_positions(data):
            raise ValueError('Backup anterior não abrange expedições, localizações ou documentos novos.')
        current-=SHIPPING_TABLES
    if not wanted & PLANNING_TABLES:
        if any(data[name] for name in PLANNING_TABLES):raise ValueError('Backup não abrange reservas/revisões novas.')
        current-=PLANNING_TABLES
    if wanted in (current-VALMET_TABLES,current-VALMET_TABLES-NUMBER_TABLES):
        if any(data[name] for name in VALMET_TABLES-{'valmet_owners'}) or ('load_numbers' not in wanted and data['load_numbers']):
            raise ValueError('Backup anterior não abrange movimentos novos.')
        return inventory({name:rows for name,rows in data.items() if name in wanted})
    if wanted == current - NUMBER_TABLES:
        if data['load_numbers']:
            raise ValueError('Backup anterior não abrange numeração emitida; fazer novo backup.')
        return inventory({name: rows for name, rows in data.items() if name in wanted})
    legacy = current - CONTEXT_TABLES - PHYSICAL_TABLES - NUMBER_TABLES - VALMET_TABLES
    if wanted == current:
        selected={name:rows for name,rows in data.items() if name in current}
        normalized=inventory(selected)
        if normalized==expected or wanted & NKT_INVENTORY_TABLES:return normalized
        # Authenticated archives made by 2C.3 predate nullable inventory edges.
        # Hide only null additive metadata, never a real inventory operation.
        if any(row.get('inventory_id') is not None or row.get('inventory_review_id') is not None for name in ('nkt_operations','nkt_documents') for row in selected.get(name,[])):raise ValueError('Backup antigo não inclui referências de inventário existentes.')
        legacy={name:[{k:v for k,v in row.items() if k not in ({'inventory_id','inventory_review_id'} if name=='nkt_operations' else {'inventory_review_id'} if name=='nkt_documents' else set())} for row in rows] for name,rows in selected.items()}
        return inventory(legacy)
    if wanted != legacy or set(inspect(connection).get_table_names()) & (CONTEXT_TABLES | PHYSICAL_TABLES):
        raise ValueError('Inventário histórico incompatível; não ocultar clientes/tabelas.')
    projected = {name:[{k:v for k,v in row.items() if k!='context_id'} for row in data[name]] for name in legacy}
    if any(row.get('context_id',1)!=1 for name in legacy for row in data[name]):
        raise ValueError('Não é permitido projetar dados de outros contextos.')
    actual = inventory(projected)
    # PR9's historical manifest used the original fixed Iberbag capacity metric.
    from .location_rules import capacity_for
    occupied = Counter()
    for row in projected['stock']:occupied[row['local']] += row['quantidade']
    actual['locais_acima_capacidade'] = sum(capacity_for(loc)==1 and qty>1 for loc,qty in occupied.items())
    return actual

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
    from .pg_environment import configure_process
    configure_process(engine.url)


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
    if any(data[name] for name in COSMO_TABLES | SHARED_TABLES | (VALMET_TABLES-{'valmet_owners'})|PLANNING_TABLES|SHIPPING_TABLES|CONTROL_TABLES|ROLL_TABLES|IMPORT_TABLES|(NKT_TABLES-{'nkt_owners'})|NKT_RECEIVING_TABLES|NKT_LOADING_TABLES|NKT_INVENTORY_TABLES|(ALCOBRE_TABLES-{'alcobre_owners','alcobre_zones'})|ALCOBRE_DISPATCH_TABLES|ALCOBRE_INVENTORY_TABLES):
        raise ValueError('Importação operacional Valmet não implementada. Usar backup/restauro PostgreSQL verificado.')
    with engine.begin() as connection:
        connection.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': LOCK})
        require_empty(connection)
        cfg = config()
        cfg.attributes['connection'] = connection
        command.upgrade(cfg, 'head')
        owners=connection.execute(select(Base.metadata.tables['valmet_owners'])).mappings().all()
        if list(owners)!=data['valmet_owners']:
            raise ValueError('Configuração Valmet divergente; reconciliar numa cópia, sem substituir atribuições.')
        nkt_owners=connection.execute(select(Base.metadata.tables['nkt_owners'])).mappings().all()
        if list(nkt_owners)!=data['nkt_owners']:raise ValueError('Configuração NKT divergente; reconciliar sem substituir atribuições.')
        for name in ('alcobre_owners','alcobre_zones'):
            actual=[dict(r) for r in connection.execute(select(Base.metadata.tables[name]).order_by(*Base.metadata.tables[name].primary_key)).mappings()]
            if actual!=data[name]:raise ValueError('Configuração Alcobre divergente; usar restauro reconciliado, sem substituir atribuições.')
        # The migration seeds only empty administrative catalogues; a snapshot
        # carries their exact values, including subsequently added clients.
        for name in ('storage_contexts','clients','warehouses'):
            connection.execute(Base.metadata.tables[name].delete())
        connection.execute(text('ALTER TABLE stock DISABLE TRIGGER stock_clock'))
        connection.execute(text('ALTER TABLE locations DISABLE TRIGGER catalogue_clock'))
        for table in TABLES:
            if table.name in ('valmet_owners','nkt_owners','alcobre_owners','alcobre_zones'):continue  # Verified immutable seed, never disable its guards.
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
        # Complete every deferred ledger/capacity validation before catalogue DDL.
        # Never disable a new safety trigger to make a historical copy pass.
        connection.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
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
