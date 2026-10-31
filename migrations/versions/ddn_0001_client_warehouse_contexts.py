"""DDN stock-owner / warehouse contexts; preserves legacy records verbatim.

Revision ddn_0001 is deliberately distinct from security PR #9's 0007.
This independent PR is based on main 0006. Do not merge Alembic branches blindly.
"""
from alembic import op
import sqlalchemy as sa
revision = 'ddn_0001'
down_revision = '0006'
branch_labels = None
depends_on = None

SCOPED = ['app_config',
 'audit',
 'cargas',
 'corridors',
 'dispatch_orders',
 'imports',
 'location_clocks',
 'locations',
 'lot_resolutions',
 'mercadona_destinations',
 'physical_inventories',
 'stock',
 'commercial_preparations',
 'dispatch_documents',
 'dispatch_lines',
 'inventory_scopes',
 'preparation_compensations',
 'preparation_completions',
 'receipts',
 'stock_adjustments',
 'stock_transfers',
 'customer_pending',
 'dispatch_picks',
 'inventory_counts',
 'receipt_documents',
 'receipt_lines',
 'incomplete_compositions',
 'pending_allocations',
 'putaways',
 'stock_incidents',
 'allocation_collections']
UNIQUES = {'allocation_collections': [('uq_ctx_allocation_collections', ['context_id', 'allocation_id'])],
 'app_config': [],
 'audit': [('uq_ctx_audit', ['context_id', 'id'])],
 'cargas': [('uq_ctx_cargas', ['context_id', 'id']), (None, ['context_id', 'codigo'])],
 'commercial_preparations': [('uq_ctx_commercial_preparations', ['context_id', 'order_id'])],
 'corridors': [('uq_ctx_corridors', ['context_id', 'id']), (None, ['context_id', 'codigo'])],
 'customer_pending': [(None, ['context_id', 'line_id']),
                      ('uq_ctx_customer_pending', ['context_id', 'id'])],
 'dispatch_documents': [('uq_ctx_dispatch_documents', ['context_id', 'order_id'])],
 'dispatch_lines': [('uq_ctx_dispatch_lines', ['context_id', 'id']),
                    ('dispatch_line_unique',
                     ['context_id', 'order_id', 'artigo', 'lote', 'destination'])],
 'dispatch_orders': [('uq_ctx_dispatch_orders', ['context_id', 'id']),
                     (None, ['context_id', 'request_key'])],
 'dispatch_picks': [('uq_ctx_dispatch_picks', ['context_id', 'id']),
                    ('dispatch_pick_unique', ['context_id', 'line_id', 'stock_id'])],
 'imports': [('uq_ctx_imports', ['context_id', 'id']), (None, ['context_id', 'source_sha256'])],
 'incomplete_compositions': [('uq_ctx_incomplete_compositions', ['context_id', 'id']),
                             (None, ['context_id', 'request_key']),
                             (None, ['context_id', 'receipt_line_id'])],
 'inventory_counts': [('uq_ctx_inventory_counts', ['context_id', 'id'])],
 'inventory_scopes': [('inventory_scope_unique', ['context_id', 'inventory_id', 'local']),
                      ('uq_ctx_inventory_scopes', ['context_id', 'id'])],
 'location_clocks': [],
 'locations': [('uq_ctx_locations', ['context_id', 'id']), (None, ['context_id', 'codigo'])],
 'lot_resolutions': [('uq_ctx_lot_resolutions', ['context_id', 'id'])],
 'mercadona_destinations': [('uq_ctx_mercadona_destinations', ['context_id', 'id']),
                            (None, ['context_id', 'name'])],
 'pending_allocations': [('uq_ctx_pending_allocations', ['context_id', 'id']),
                         ('pending_allocation_unique', ['context_id', 'pending_id', 'receipt_line_id'])],
 'physical_inventories': [('uq_ctx_physical_inventories', ['context_id', 'id']),
                          (None, ['context_id', 'request_key'])],
 'preparation_compensations': [('uq_ctx_preparation_compensations', ['context_id', 'order_id']),
                               (None, ['context_id', 'inventory_id'])],
 'preparation_completions': [('uq_ctx_preparation_completions', ['context_id', 'order_id'])],
 'putaways': [(None, ['context_id', 'request_key']), ('uq_ctx_putaways', ['context_id', 'id'])],
 'receipt_documents': [('uq_ctx_receipt_documents', ['context_id', 'receipt_id'])],
 'receipt_lines': [('receipt_line_group_unique', ['context_id', 'receipt_id', 'artigo', 'lote']),
                   ('uq_ctx_receipt_lines', ['context_id', 'id'])],
 'receipts': [(None, ['context_id', 'carga_id']),
              (None, ['context_id', 'request_key']),
              ('uq_ctx_receipts', ['context_id', 'id'])],
 'stock': [('stock_key_unique', ['context_id', 'artigo', 'lote', 'carga', 'local']),
           ('uq_ctx_stock', ['context_id', 'id'])],
 'stock_adjustments': [('uq_ctx_stock_adjustments', ['context_id', 'id']),
                       ('adjustment_unique', ['context_id', 'inventory_id', 'stock_id'])],
 'stock_incidents': [('uq_ctx_stock_incidents', ['context_id', 'id']),
                     (None, ['context_id', 'request_key'])],
 'stock_transfers': [(None, ['context_id', 'request_key']),
                     ('uq_ctx_stock_transfers', ['context_id', 'id'])]}
EDGES = {'allocation_collections': [('fk_ctx_allocation_collections_allocation_id',
                             ['context_id', 'allocation_id'],
                             'pending_allocations',
                             ['context_id', 'id'])],
 'app_config': [],
 'audit': [],
 'cargas': [],
 'commercial_preparations': [('fk_ctx_commercial_preparations_order_id',
                              ['context_id', 'order_id'],
                              'dispatch_orders',
                              ['context_id', 'id'])],
 'corridors': [],
 'customer_pending': [('fk_ctx_customer_pending_line_id',
                       ['context_id', 'line_id'],
                       'dispatch_lines',
                       ['context_id', 'id'])],
 'dispatch_documents': [('fk_ctx_dispatch_documents_order_id',
                         ['context_id', 'order_id'],
                         'dispatch_orders',
                         ['context_id', 'id'])],
 'dispatch_lines': [('fk_ctx_dispatch_lines_order_id',
                     ['context_id', 'order_id'],
                     'dispatch_orders',
                     ['context_id', 'id'])],
 'dispatch_orders': [],
 'dispatch_picks': [('fk_ctx_dispatch_picks_line_id',
                     ['context_id', 'line_id'],
                     'dispatch_lines',
                     ['context_id', 'id']),
                    ('fk_ctx_dispatch_picks_stock_id',
                     ['context_id', 'stock_id'],
                     'stock',
                     ['context_id', 'id'])],
 'imports': [],
 'incomplete_compositions': [('fk_ctx_incomplete_compositions_receipt_line_id',
                              ['context_id', 'receipt_line_id'],
                              'receipt_lines',
                              ['context_id', 'id'])],
 'inventory_counts': [('fk_ctx_inventory_counts_scope_id',
                       ['context_id', 'scope_id'],
                       'inventory_scopes',
                       ['context_id', 'id'])],
 'inventory_scopes': [('fk_ctx_inventory_scopes_inventory_id',
                       ['context_id', 'inventory_id'],
                       'physical_inventories',
                       ['context_id', 'id'])],
 'location_clocks': [],
 'locations': [],
 'lot_resolutions': [],
 'mercadona_destinations': [],
 'pending_allocations': [('fk_ctx_pending_allocations_pending_id',
                          ['context_id', 'pending_id'],
                          'customer_pending',
                          ['context_id', 'id']),
                         ('fk_ctx_pending_allocations_receipt_line_id',
                          ['context_id', 'receipt_line_id'],
                          'receipt_lines',
                          ['context_id', 'id'])],
 'physical_inventories': [],
 'preparation_compensations': [('fk_ctx_preparation_compensations_order_id',
                                ['context_id', 'order_id'],
                                'dispatch_orders',
                                ['context_id', 'id']),
                               ('fk_ctx_preparation_compensations_inventory_id',
                                ['context_id', 'inventory_id'],
                                'physical_inventories',
                                ['context_id', 'id'])],
 'preparation_completions': [('fk_ctx_preparation_completions_order_id',
                              ['context_id', 'order_id'],
                              'dispatch_orders',
                              ['context_id', 'id'])],
 'putaways': [('fk_ctx_putaways_stock_id', ['context_id', 'stock_id'], 'stock', ['context_id', 'id']),
              ('fk_ctx_putaways_line_id',
               ['context_id', 'line_id'],
               'receipt_lines',
               ['context_id', 'id'])],
 'receipt_documents': [('fk_ctx_receipt_documents_receipt_id',
                        ['context_id', 'receipt_id'],
                        'receipts',
                        ['context_id', 'id'])],
 'receipt_lines': [('fk_ctx_receipt_lines_receipt_id',
                    ['context_id', 'receipt_id'],
                    'receipts',
                    ['context_id', 'id'])],
 'receipts': [('fk_ctx_receipts_carga_id', ['context_id', 'carga_id'], 'cargas', ['context_id', 'id'])],
 'stock': [],
 'stock_adjustments': [('fk_ctx_stock_adjustments_stock_id',
                        ['context_id', 'stock_id'],
                        'stock',
                        ['context_id', 'id']),
                       ('fk_ctx_stock_adjustments_inventory_id',
                        ['context_id', 'inventory_id'],
                        'physical_inventories',
                        ['context_id', 'id'])],
 'stock_incidents': [('fk_ctx_stock_incidents_scope_id',
                      ['context_id', 'scope_id'],
                      'inventory_scopes',
                      ['context_id', 'id']),
                     ('fk_ctx_stock_incidents_pick_id',
                      ['context_id', 'pick_id'],
                      'dispatch_picks',
                      ['context_id', 'id']),
                     ('fk_ctx_stock_incidents_inventory_id',
                      ['context_id', 'inventory_id'],
                      'physical_inventories',
                      ['context_id', 'id'])],
 'stock_transfers': [('fk_ctx_stock_transfers_source_stock_id',
                      ['context_id', 'source_stock_id'],
                      'stock',
                      ['context_id', 'id']),
                     ('fk_ctx_stock_transfers_destination_stock_id',
                      ['context_id', 'destination_stock_id'],
                      'stock',
                      ['context_id', 'id'])]}

def upgrade():
    op.create_table('warehouses', sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(120), nullable=False, unique=True), sa.Column('active', sa.Boolean(), nullable=False))
    op.create_table('clients', sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(120), nullable=False, unique=True), sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('tracking', sa.String(40)), sa.Column('unit', sa.String(40)),
        sa.Column('pallet_operations', sa.Boolean(), nullable=False))
    op.create_table('storage_contexts', sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('client_id', sa.Integer(), sa.ForeignKey('clients.id'), nullable=False),
        sa.Column('warehouse_id', sa.Integer(), sa.ForeignKey('warehouses.id'), nullable=False),
        sa.Column('area', sa.String(120), nullable=False), sa.Column('active', sa.Boolean(), nullable=False),
        sa.UniqueConstraint('warehouse_id','area',name='warehouse_area_unique'),
        sa.UniqueConstraint('client_id','warehouse_id',name='client_warehouse_unique'))
    conn = op.get_bind()
    conn.execute(sa.text("INSERT INTO warehouses(id,name,active) VALUES (1,'Ovar',true),(2,'Escapães',true)"))
    seeds = [(1,'Iberbag','grouped','palete',True,1), (2,'Valmet','individual',None,False,1),
             (3,'Cosmopak','grouped_with_composition','palete',False,1), (4,'NKT','peg','palete',False,2),
             (5,'Alcobre','individual','bobine',False,1)]
    for ident,name,tracking,unit,enabled,wh in seeds:
        conn.execute(sa.text('INSERT INTO clients(id,name,tracking,unit,pallet_operations,active) VALUES(:id,:name,:tracking,:unit,:enabled,true)'),
                     dict(id=ident,name=name,tracking=tracking,unit=unit,enabled=enabled))
        conn.execute(sa.text('INSERT INTO storage_contexts(id,client_id,warehouse_id,area,active) VALUES(:id,:id,:wh,:area,true)'),
                     dict(id=ident,wh=wh,area=name))
    # Adding a constant DEFAULT labels old rows without firing UPDATE triggers.
    # Existing immutable ledgers, PDF bytes, quantities and dates stay untouched.
    for table in SCOPED:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column('context_id', sa.Integer(), nullable=False, server_default='1'))
            batch.create_foreign_key('fk_context_'+table, 'storage_contexts', ['context_id'], ['id'])
            batch.create_index('ix_'+table+'_context_id', ['context_id'])
    inspector = sa.inspect(conn)
    for table in SCOPED:
        current = inspector.get_unique_constraints(table)
        with op.batch_alter_table(table, naming_convention={'uq':'uq_%(table_name)s_%(column_0_name)s','pk':'%(table_name)s_pkey'}) as batch:
            for item in current:
                cols = item['column_names']
                batch.drop_constraint(item['name'] or 'uq_'+table+'_'+cols[0], type_='unique')
            if table in ('app_config','location_clocks'):
                pk = inspector.get_pk_constraint(table)
                batch.drop_constraint(pk['name'] or table+'_pkey', type_='primary')
                batch.create_primary_key(table+'_pkey', ['context_id']+pk['constrained_columns'])
            for name,cols in UNIQUES[table]:
                batch.create_unique_constraint(name or 'uq_'+table+'_'+cols[-1], cols)
    # Composite foreign keys prohibit cross-owner references even in direct SQL.
    for table,edges in EDGES.items():
        with op.batch_alter_table(table) as batch:
            for name,cols,target,remote in edges:
                batch.create_foreign_key(name,target,cols,remote)
    if conn.dialect.name == 'postgresql':
        for table in ('clients','warehouses','storage_contexts'):
            conn.execute(sa.text("SELECT setval(pg_get_serial_sequence(:t,'id'),(SELECT max(id) FROM "+table+"))"), {'t':table})
        op.execute("""
        CREATE FUNCTION ddn_context_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.context_id IS DISTINCT FROM OLD.context_id THEN
                RAISE EXCEPTION 'Owner/warehouse context is immutable' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END $$
        """)
        for table in SCOPED:
            op.execute(f'CREATE TRIGGER immutable_context BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION ddn_context_immutable()')
        op.execute("""
        CREATE FUNCTION ddn_context_identity_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.client_id,NEW.warehouse_id,NEW.area) IS DISTINCT FROM (OLD.client_id,OLD.warehouse_id,OLD.area) THEN
                RAISE EXCEPTION 'Context identity cannot be reassigned' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END $$
        """)
        op.execute('CREATE TRIGGER immutable_identity BEFORE UPDATE ON storage_contexts FOR EACH ROW EXECUTE FUNCTION ddn_context_identity_guard()')
        # Location versions are also scoped. Never bump another owner's clock.
        op.execute("""
        CREATE OR REPLACE FUNCTION wms_clock_bump() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE old_code text; new_code text; ctx integer;
        BEGIN
            IF TG_OP='UPDATE' AND NEW IS NOT DISTINCT FROM OLD THEN RETURN NEW; END IF;
            IF TG_OP<>'INSERT' THEN
                ctx=OLD.context_id;
                IF TG_TABLE_NAME='stock' THEN old_code=OLD.local; ELSE old_code=OLD.codigo; END IF;
            END IF;
            IF TG_OP<>'DELETE' THEN
                ctx=NEW.context_id;
                IF TG_TABLE_NAME='stock' THEN new_code=NEW.local; ELSE new_code=NEW.codigo; END IF;
            END IF;
            IF old_code IS NOT NULL THEN
                INSERT INTO location_clocks(context_id,local,version) VALUES(ctx,old_code,1)
                ON CONFLICT(context_id,local) DO UPDATE SET version=location_clocks.version+1;
            END IF;
            IF new_code IS NOT NULL AND new_code IS DISTINCT FROM old_code THEN
                INSERT INTO location_clocks(context_id,local,version) VALUES(ctx,new_code,1)
                ON CONFLICT(context_id,local) DO UPDATE SET version=location_clocks.version+1;
            END IF;
            IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW;
        END $$
        """)

def downgrade():
    raise RuntimeError('Reversão destrutiva bloqueada. Restaurar backup verificado numa base separada.')
