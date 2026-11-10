"""NKT controlled listings and immutable container receipts; no legacy backfill."""
from importlib import import_module
from pathlib import Path
from alembic import op
import sqlalchemy as s
revision='ddn_0013'
down_revision='ddn_0012'
branch_labels=None
depends_on=None

def upgrade():
    table=import_module('migrations.versions.ddn_0008_valmet_shipping').table
    table('nkt_receivings',[
        s.Column('client_id',s.Integer(),s.ForeignKey('clients.id'),nullable=False),s.Column('request_key',s.String(64),nullable=False),
        s.Column('source_name',s.String(240),nullable=False),s.Column('source_digest',s.String(64)),s.Column('report',s.Text(),nullable=False),
        s.Column('created_by',s.Integer(),s.ForeignKey('users.id'),nullable=False),s.Column('created_at',s.DateTime(),nullable=False),
        s.Column('version',s.Integer(),nullable=False),s.Column('status',s.String(20),nullable=False)],constraints=[s.UniqueConstraint('context_id','request_key'),s.CheckConstraint("version>=0 AND status IN ('draft','confirmed')",name='nkt_receiving_state')])
    op.create_index('uq_nkt_source_owner','nkt_receivings',['client_id','source_digest'],unique=True)
    table('nkt_receiving_reviews',[
        s.Column('receiving_id',s.Integer(),nullable=False),s.Column('version',s.Integer(),nullable=False),s.Column('payload',s.Text(),nullable=False),
        s.Column('digest',s.String(64),nullable=False),s.Column('reason',s.Text(),nullable=False),s.Column('actor',s.String(60),nullable=False),s.Column('created_at',s.DateTime(),nullable=False)],refs=[('receiving_id','nkt_receivings')],constraints=[s.UniqueConstraint('context_id','receiving_id','version')])
    table('nkt_containers',[
        s.Column('receiving_id',s.Integer(),nullable=False),s.Column('review_id',s.Integer(),nullable=False),s.Column('entry_id',s.Integer(),nullable=False),s.Column('carga_id',s.Integer(),nullable=False),
        s.Column('client_id',s.Integer(),s.ForeignKey('clients.id'),nullable=False),s.Column('name',s.String(120),nullable=False),s.Column('payload',s.Text(),nullable=False),
        s.Column('year',s.Integer()),s.Column('number',s.Integer()),s.Column('actor',s.String(60),nullable=False),s.Column('created_at',s.DateTime(),nullable=False)],refs=[('receiving_id','nkt_receivings'),('review_id','nkt_receiving_reviews'),('entry_id','nkt_entries'),('carga_id','cargas')],constraints=[s.UniqueConstraint('context_id','entry_id'),s.UniqueConstraint('context_id','carga_id'),s.UniqueConstraint('context_id','receiving_id','name'),s.CheckConstraint('(year IS NULL AND number IS NULL) OR (year>=2027 AND number>0)',name='nkt_container_number')])
    op.create_index('uq_nkt_container_number','nkt_containers',['client_id','year','number'],unique=True)
    if op.get_bind().dialect.name=='postgresql':
        conn=op.get_bind();schema=conn.dialect.identifier_preparer.quote(conn.scalar(s.text('SELECT current_schema()')))
        conn.exec_driver_sql(Path(__file__).with_suffix('.sql').read_text().replace('__WMS_SCHEMA__',schema))
def downgrade():raise RuntimeError('Restaurar backup verificado; nunca apagar contentores ou identidades PEG.')
