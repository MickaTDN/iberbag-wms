"""Individual NKT PEG ledger; no stock/data backfill."""
from importlib import import_module
from pathlib import Path
from alembic import op
import sqlalchemy as s
revision='ddn_0012'
down_revision='ddn_0011'
branch_labels=None
depends_on=None

def upgrade():
    table=import_module('migrations.versions.ddn_0008_valmet_shipping').table
    op.create_table('nkt_owners',s.Column('client_id',s.Integer(),s.ForeignKey('clients.id',deferrable=True,initially='DEFERRED'),primary_key=True))
    op.execute("INSERT INTO nkt_owners(client_id) SELECT id FROM clients WHERE name='NKT'")
    table('nkt_entries',[s.Column('request_key',s.String(64),nullable=False),s.Column('payload',s.Text(),nullable=False),s.Column('version',s.Integer(),nullable=False),s.Column('status',s.String(20),nullable=False),s.Column('created_by',s.Integer(),s.ForeignKey('users.id'),nullable=False),s.Column('created_at',s.DateTime(),nullable=False),s.Column('confirmed_by',s.String(60)),s.Column('confirmed_at',s.DateTime())],constraints=[s.UniqueConstraint('context_id','request_key'),s.CheckConstraint("version>0 AND ((status='draft' AND confirmed_by IS NULL AND confirmed_at IS NULL) OR (status='confirmed' AND confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL))",name='nkt_entry_state')])
    table('nkt_reviews',[s.Column('entry_id',s.Integer(),nullable=False),s.Column('version',s.Integer(),nullable=False),s.Column('payload',s.Text(),nullable=False),s.Column('digest',s.String(64),nullable=False),s.Column('actor',s.String(60),nullable=False),s.Column('created_at',s.DateTime(),nullable=False)],refs=[('entry_id','nkt_entries')],constraints=[s.UniqueConstraint('context_id','entry_id','version')])
    table('nkt_pallets',[s.Column('entry_id',s.Integer(),nullable=False),s.Column('client_id',s.Integer(),s.ForeignKey('clients.id'),nullable=False),s.Column('peg',s.String(120),nullable=False),s.Column('local',s.String(120),nullable=False),s.Column('actor',s.String(60),nullable=False),s.Column('created_at',s.DateTime(),nullable=False)],refs=[('entry_id','nkt_entries')])
    op.create_index('uq_nkt_owner_peg','nkt_pallets',['client_id',s.text('lower(peg)')],unique=True)
    op.create_index('ix_nkt_context_local','nkt_pallets',['context_id','local'])
    if op.get_bind().dialect.name=='postgresql':
        conn=op.get_bind();schema=conn.dialect.identifier_preparer.quote(conn.scalar(s.text('SELECT current_schema()')))
        conn.exec_driver_sql(Path(__file__).with_suffix('.sql').read_text().replace('__WMS_SCHEMA__',schema))
def downgrade():raise RuntimeError('Restaurar backup verificado; não apagar PEG históricos.')
