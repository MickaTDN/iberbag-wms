"""NKT per-entry/container inventories, missing ledger and evidenced compensation."""
from importlib import import_module
from pathlib import Path
from alembic import op
import sqlalchemy as s
revision='ddn_0015'
down_revision='ddn_0014'
branch_labels=None
depends_on=None
ORIGIN="(kind IN ('inventory_missing','inventory_restore') AND preparation_id IS NULL AND revision_id IS NULL AND inventory_id IS NOT NULL AND inventory_review_id IS NOT NULL AND correction_id IS NULL) OR (kind NOT IN ('inventory_missing','inventory_restore') AND preparation_id IS NOT NULL AND revision_id IS NOT NULL AND inventory_id IS NULL AND inventory_review_id IS NULL)"

def upgrade():
    h=import_module('migrations.versions.ddn_0008_valmet_shipping');table=h.table;audit=h.audit_columns
    table('nkt_inventories',[s.Column('request_key',s.String(64),nullable=False),s.Column('title',s.String(120),nullable=False),s.Column('mode',s.String(20),nullable=False),s.Column('scope',s.Text(),nullable=False),s.Column('opening',s.Text(),nullable=False),s.Column('created_by',s.Integer(),s.ForeignKey('users.id'),nullable=False),s.Column('created_at',s.DateTime(),nullable=False),s.Column('version',s.Integer(),nullable=False),s.Column('status',s.String(20),nullable=False)],constraints=[s.UniqueConstraint('context_id','request_key'),s.CheckConstraint("mode IN ('complete','partial') AND status IN ('draft','confirmed') AND version>0",name='nkt_inventory_state')])
    table('nkt_inventory_checks',[s.Column('inventory_id',s.Integer(),nullable=False),s.Column('entry_id',s.Integer(),nullable=False),s.Column('state',s.String(20),nullable=False),s.Column('communicated',s.String(120)),s.Column('full_peg',s.Boolean(),nullable=False),s.Column('pallet_id',s.Integer()),s.Column('snapshot',s.Text(),nullable=False),s.Column('reason',s.Text(),nullable=False),*audit()],refs=[('inventory_id','nkt_inventories'),('entry_id','nkt_entries'),('pallet_id','nkt_pallets')],constraints=[s.CheckConstraint("state IN ('located','not_located','excluded')",name='nkt_inventory_check_state')])
    table('nkt_inventory_reviews',[s.Column('inventory_id',s.Integer(),nullable=False),s.Column('request_key',s.String(64),nullable=False),s.Column('kind',s.String(20),nullable=False),s.Column('version',s.Integer(),nullable=False),s.Column('payload',s.Text(),nullable=False),s.Column('digest',s.String(64),nullable=False),s.Column('original_id',s.Integer()),s.Column('reason',s.Text(),nullable=False),*audit()],refs=[('inventory_id','nkt_inventories')],constraints=[s.UniqueConstraint('context_id','request_key'),s.CheckConstraint("kind IN ('conference','confirm','restore')",name='nkt_inventory_review_kind')])
    table('nkt_inventory_incidents',[s.Column('inventory_id',s.Integer(),nullable=False),s.Column('communicated',s.String(120)),s.Column('kind',s.String(40),nullable=False),s.Column('details',s.Text(),nullable=False),s.Column('resolves_id',s.Integer()),*audit()],refs=[('inventory_id','nkt_inventories'),('resolves_id','nkt_inventory_incidents')],constraints=[s.UniqueConstraint('context_id','resolves_id')])
    # Nullable additive edges preserve every old row and keep the historical ledger.
    with op.batch_alter_table('nkt_operations') as batch:
        batch.alter_column('preparation_id',existing_type=s.Integer(),nullable=True)
        batch.alter_column('revision_id',existing_type=s.Integer(),nullable=True)
        batch.add_column(s.Column('inventory_id',s.Integer()))
        batch.add_column(s.Column('inventory_review_id',s.Integer()))
        for field,target in [('inventory_id','nkt_inventories'),('inventory_review_id','nkt_inventory_reviews')]:batch.create_foreign_key('fk_ctx_nkt_operations_'+field,target,['context_id',field],['context_id','id'])
        batch.create_unique_constraint('uq_nkt_inventory_operation_review',['context_id','inventory_review_id'])
        batch.drop_constraint('nkt_operation_kind',type_='check')
        batch.create_check_constraint('nkt_operation_kind',"kind IN ('reserve','cancel','ship','restore','metadata','inventory_missing','inventory_restore')")
        batch.create_check_constraint('nkt_operation_origin',ORIGIN)
    op.create_index('uq_nkt_inventory_confirmation','nkt_operations',['context_id','inventory_id'],unique=True,postgresql_where=s.text("kind='inventory_missing'"),sqlite_where=s.text("kind='inventory_missing'"))
    with op.batch_alter_table('nkt_stock_events') as batch:
        batch.drop_constraint('nkt_event_state',type_='check');batch.create_check_constraint('nkt_event_state',"state IN ('available','reserved','shipped','missing')")
    with op.batch_alter_table('nkt_documents') as batch:
        batch.add_column(s.Column('inventory_review_id',s.Integer()))
        batch.create_foreign_key('fk_ctx_nkt_documents_inventory_review_id','nkt_inventory_reviews',['context_id','inventory_review_id'],['context_id','id'])
        batch.create_unique_constraint('uq_nkt_inventory_document_review',['context_id','inventory_review_id'])
        batch.drop_constraint('nkt_document_origin',type_='check')
        batch.create_check_constraint('nkt_document_origin','(CASE WHEN revision_id IS NULL THEN 0 ELSE 1 END)+(CASE WHEN operation_id IS NULL THEN 0 ELSE 1 END)+(CASE WHEN correction_id IS NULL THEN 0 ELSE 1 END)+(CASE WHEN inventory_review_id IS NULL THEN 0 ELSE 1 END)=1')
    if op.get_bind().dialect.name=='postgresql':
        conn=op.get_bind();schema=conn.dialect.identifier_preparer.quote(conn.scalar(s.text('SELECT current_schema()')))
        conn.exec_driver_sql(Path(__file__).with_suffix('.sql').read_text().replace('__WMS_SCHEMA__',schema))
        for name in ('nkt_guard','nkt_location_guard','nkt_combined_stock','nkt_loading_guard'):
            definition=conn.scalar(s.text('SELECT pg_get_functiondef(CAST(:fn AS regprocedure))'),dict(fn=name+'()'))
            import re
            updated=re.sub(r"nkt_current_state\(([^)]+)\)\s*<>\s*'shipped'",r"nkt_current_state(\1) NOT IN ('shipped','missing')",definition)
            if updated==definition:raise RuntimeError('Unexpected NKT occupation guard; reconcile explicitly.')
            if name=='nkt_loading_guard':
                marker=' PERFORM pg_advisory_xact_lock(72834001);'
                if marker not in updated:raise RuntimeError('Unexpected loading guard layout.')
                updated=updated.replace(marker,marker+"\n IF TG_TABLE_NAME='nkt_operations' THEN\n  IF NEW.kind IN ('inventory_missing','inventory_restore') THEN RETURN NEW; END IF;\n ELSIF TG_TABLE_NAME='nkt_stock_events' THEN\n  IF EXISTS(SELECT 1 FROM nkt_operations WHERE id=NEW.operation_id AND context_id=NEW.context_id AND kind IN ('inventory_missing','inventory_restore')) THEN RETURN NEW; END IF;\n END IF;",1)
            conn.exec_driver_sql(updated)

def downgrade():raise RuntimeError('Restore a verified backup; never erase inventory findings or PEG history.')
