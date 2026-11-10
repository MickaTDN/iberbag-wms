"""Alcobre individual bobines and explicit physical position catalogue; additive."""
from importlib import import_module
from pathlib import Path
from alembic import op
import sqlalchemy as s
revision='ddn_0016'
down_revision='ddn_0015'
branch_labels=None
depends_on=None

def upgrade():
    h=import_module('migrations.versions.ddn_0008_valmet_shipping');table=h.table;audit=h.audit_columns
    op.create_table('alcobre_owners',s.Column('client_id',s.Integer(),s.ForeignKey('clients.id',deferrable=True,initially='DEFERRED'),primary_key=True),s.Column('warehouse_id',s.Integer(),s.ForeignKey('warehouses.id',deferrable=True,initially='DEFERRED'),nullable=False))
    op.execute("INSERT INTO alcobre_owners SELECT c.id,w.id FROM clients c CROSS JOIN warehouses w WHERE c.name='Alcobre' AND w.name='Ovar'")
    op.create_table('alcobre_zones',s.Column('id',s.Integer(),primary_key=True),s.Column('context_id',s.Integer(),s.ForeignKey('storage_contexts.id',deferrable=True,initially='DEFERRED'),nullable=False,server_default='1'),s.Column('code',s.String(20),nullable=False),s.Column('positions',s.Integer(),nullable=False),s.Column('active',s.Boolean(),nullable=False),s.UniqueConstraint('context_id','id',name='uq_ctx_alcobre_zones'),s.UniqueConstraint('context_id','code'),s.CheckConstraint('positions BETWEEN 1 AND 500',name='alcobre_zone_positions'))
    op.create_index('ix_alcobre_zones_context_id','alcobre_zones',['context_id'])
    # Logical zones/counts only: physical codes require administrator evidence.
    for i,code in enumerate('ABCDEFGHIJKLMN',1):
        op.execute(s.text("INSERT INTO alcobre_zones(id,context_id,code,positions,active) SELECT :i,c.id,:code,15,true FROM storage_contexts c JOIN alcobre_owners a ON a.client_id=c.client_id AND a.warehouse_id=c.warehouse_id").bindparams(i=i,code=code))
    if op.get_bind().dialect.name=='postgresql':op.execute("SELECT setval(pg_get_serial_sequence('alcobre_zones','id'),greatest(coalesce((SELECT max(id) FROM alcobre_zones),0),1),true)")
    table('alcobre_slots',[s.Column('zone_id',s.Integer(),nullable=False),s.Column('position',s.Integer(),nullable=False),s.Column('local',s.String(120),nullable=False)],refs=[('zone_id','alcobre_zones')],constraints=[s.UniqueConstraint('context_id','zone_id','position'),s.UniqueConstraint('context_id','local'),s.ForeignKeyConstraint(['context_id','local'],['locations.context_id','locations.codigo'])])
    table('alcobre_entries',[s.Column('request_key',s.String(64),nullable=False),s.Column('mode',s.String(20),nullable=False),s.Column('reference',s.String(200)),s.Column('received_on',s.String(10)),s.Column('source_name',s.String(240)),s.Column('source_pdf',s.LargeBinary()),s.Column('source_digest',s.String(64)),s.Column('extraction',s.Text(),nullable=False),s.Column('version',s.Integer(),nullable=False),s.Column('status',s.String(20),nullable=False),s.Column('quantity',s.Integer()),s.Column('created_by',s.Integer(),s.ForeignKey('users.id'),nullable=False),s.Column('created_at',s.DateTime(),nullable=False),s.Column('confirmed_by',s.String(60)),s.Column('confirmed_at',s.DateTime())],constraints=[s.UniqueConstraint('context_id','request_key'),s.UniqueConstraint('context_id','source_digest'),s.CheckConstraint("mode IN ('direct','pending') AND version>=0 AND status IN ('draft','confirmed')",name='alcobre_entry_state'),s.CheckConstraint('quantity IS NULL OR quantity BETWEEN 1 AND 300',name='alcobre_quantity'),s.CheckConstraint('(source_pdf IS NULL AND source_digest IS NULL AND source_name IS NULL) OR (source_pdf IS NOT NULL AND source_digest IS NOT NULL AND source_name IS NOT NULL)',name='alcobre_source_complete')])
    table('alcobre_reviews',[s.Column('entry_id',s.Integer(),nullable=False),s.Column('request_key',s.String(64),nullable=False),s.Column('kind',s.String(20),nullable=False),s.Column('version',s.Integer(),nullable=False),s.Column('payload',s.Text(),nullable=False),s.Column('digest',s.String(64),nullable=False),s.Column('reason',s.Text(),nullable=False),*audit()],refs=[('entry_id','alcobre_entries')],constraints=[s.UniqueConstraint('context_id','request_key'),s.CheckConstraint("kind IN ('receive','identify','place')",name='alcobre_review_kind')])
    table('alcobre_actions',[s.Column('entry_id',s.Integer(),nullable=False),s.Column('review_id',s.Integer(),nullable=False),s.Column('request_key',s.String(64),nullable=False),*audit()],refs=[('entry_id','alcobre_entries'),('review_id','alcobre_reviews')],constraints=[s.UniqueConstraint('context_id','request_key'),s.UniqueConstraint('context_id','review_id')])
    table('alcobre_bobines',[s.Column('entry_id',s.Integer(),nullable=False),s.Column('action_id',s.Integer(),nullable=False),s.Column('client_id',s.Integer(),s.ForeignKey('clients.id'),nullable=False),s.Column('number',s.String(120),nullable=False),s.Column('number_key',s.String(240),nullable=False)],refs=[('entry_id','alcobre_entries'),('action_id','alcobre_actions')])
    op.create_index('uq_alcobre_owner_number','alcobre_bobines',['client_id','number_key'],unique=True)
    # lower() is an additional database safeguard; Python casefold covers Unicode.
    op.create_index('uq_alcobre_owner_number_lower','alcobre_bobines',['client_id',s.text('lower(number)')],unique=True)
    table('alcobre_placements',[s.Column('bobine_id',s.Integer(),nullable=False),s.Column('action_id',s.Integer(),nullable=False),s.Column('local',s.String(120),nullable=False)],refs=[('bobine_id','alcobre_bobines'),('action_id','alcobre_actions')],constraints=[s.UniqueConstraint('context_id','bobine_id'),s.UniqueConstraint('context_id','local'),s.ForeignKeyConstraint(['context_id','local'],['alcobre_slots.context_id','alcobre_slots.local'])])
    if op.get_bind().dialect.name=='postgresql':
        conn=op.get_bind();schema=conn.dialect.identifier_preparer.quote(conn.scalar(s.text('SELECT current_schema()')))
        conn.exec_driver_sql(Path(__file__).with_suffix('.sql').read_text().replace('__WMS_SCHEMA__',schema))

def downgrade():raise RuntimeError('Restaurar backup verificado; não apagar identidades ou históricos Alcobre.')
