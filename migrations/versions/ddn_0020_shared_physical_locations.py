"""Opt-in physical rack sharing; no legacy ownership, balance or PDF is rewritten."""
from importlib import import_module
from pathlib import Path
from alembic import op
import sqlalchemy as s
revision='ddn_0020'
down_revision='ddn_0019'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('physical_sharing_reviews',s.Column('id',s.Integer(),primary_key=True),
        s.Column('request_key',s.String(64),nullable=False,unique=True),s.Column('code',s.String(120),nullable=False),s.Column('label',s.String(120),nullable=False),
        s.Column('source_context_id',s.Integer(),s.ForeignKey('storage_contexts.id'),nullable=False),s.Column('other_context_id',s.Integer(),s.ForeignKey('storage_contexts.id'),nullable=False),
        s.Column('expected',s.Text(),nullable=False),s.Column('reason',s.Text(),nullable=False),s.Column('created_by',s.Integer(),s.ForeignKey('users.id'),nullable=False),s.Column('created_at',s.DateTime(),nullable=False),
        s.Column('status',s.String(20),nullable=False),s.Column('confirmed_by',s.Integer(),s.ForeignKey('users.id')),s.Column('confirmed_at',s.DateTime()),s.CheckConstraint("status IN ('draft','confirmed')",name='physical_review_status'))
    op.create_table('physical_locations',s.Column('id',s.Integer(),primary_key=True),s.Column('warehouse_id',s.Integer(),s.ForeignKey('warehouses.id'),nullable=False),
        s.Column('code',s.String(120),nullable=False),s.Column('label',s.String(120),nullable=False),s.Column('capacity',s.Integer(),nullable=False),
        s.Column('review_id',s.Integer(),s.ForeignKey('physical_sharing_reviews.id'),nullable=False,unique=True),s.UniqueConstraint('warehouse_id','code'),
        s.ForeignKeyConstraint(['warehouse_id','code'],['physical_location_claims.warehouse_id','physical_location_claims.code']),s.CheckConstraint('warehouse_id=1 AND capacity=1',name='physical_shared_capacity'))
    op.create_table('physical_location_access',s.Column('physical_id',s.Integer(),s.ForeignKey('physical_locations.id'),primary_key=True),s.Column('context_id',s.Integer(),s.ForeignKey('storage_contexts.id'),primary_key=True))
    op.create_table('physical_location_state',s.Column('physical_id',s.Integer(),s.ForeignKey('physical_locations.id'),primary_key=True),s.Column('state',s.String(30),nullable=False),
        s.Column('context_id',s.Integer(),s.ForeignKey('storage_contexts.id')),s.Column('stock_id',s.Integer()),s.Column('source_kind',s.String(30)),s.Column('source_id',s.Integer()),
        s.Column('previous',s.Text()),s.Column('version',s.Integer(),nullable=False),s.ForeignKeyConstraint(['context_id','stock_id'],['stock.context_id','stock.id']),
        s.CheckConstraint("state IN ('free','pending_putaway','pending_transfer','pending_inventory','occupied','awaiting_collection')",name='physical_state_valid'))
    h=import_module('migrations.versions.ddn_0008_valmet_shipping')
    h.table('physical_location_events',[s.Column('physical_id',s.Integer(),s.ForeignKey('physical_locations.id'),nullable=False),s.Column('kind',s.String(30),nullable=False),s.Column('ref_id',s.Integer(),nullable=False),
        s.Column('stock_id',s.Integer()),s.Column('actor_id',s.Integer(),s.ForeignKey('users.id'),nullable=False),s.Column('reason',s.Text(),nullable=False),s.Column('before_state',s.Text(),server_default='',nullable=False),
        s.Column('after_state',s.Text(),server_default='',nullable=False),s.Column('created_at',s.DateTime(),nullable=False)],refs=[('stock_id','stock')],constraints=[s.UniqueConstraint('context_id','physical_id','kind','ref_id')])
    op.create_index('ix_physical_location_events_physical_id','physical_location_events',['physical_id'])
    with op.batch_alter_table('putaways') as batch:
        batch.drop_constraint('putaway_status_valid',type_='check');batch.drop_constraint('putaway_confirmation_valid',type_='check')
        batch.create_check_constraint('putaway_status_valid',"status IN ('draft','confirmed','cancelled')")
        batch.create_check_constraint('putaway_confirmation_valid',"(status IN ('draft','cancelled') AND stock_id IS NULL AND arranger IS NULL AND confirmed_at IS NULL) OR (status='confirmed' AND stock_id IS NOT NULL AND arranger IS NOT NULL AND confirmed_at IS NOT NULL)")
    if op.get_bind().dialect.name!='postgresql':return
    op.execute(Path(__file__).with_suffix('.sql').read_text(encoding='utf-8'))
    schema=op.get_bind().dialect.identifier_preparer.quote(op.get_bind().scalar(s.text('SELECT current_schema()')))
    for name in ('ddn_shared_review_guard','ddn_shared_catalogue_guard','ddn_shared_initial_state','ddn_shared_activation_complete','ddn_shared_event','ddn_shared_stock_guard','ddn_shared_stock_capacity','ddn_shared_cancel_guard','ddn_shared_projection_guard','ddn_shared_putaway_complete','ddn_claim_position'):
        op.execute(f'ALTER FUNCTION {name}() SET search_path TO pg_catalog, {schema}, pg_temp')
        op.execute(f'REVOKE ALL ON FUNCTION {name}() FROM PUBLIC')


def downgrade():
    raise RuntimeError('Reversão destrutiva bloqueada. Restaurar backup verificado numa base separada.')
