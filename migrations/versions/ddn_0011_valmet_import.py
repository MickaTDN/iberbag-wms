"""Controlled historical baseline, no data backfill or fabricated receipts."""
from alembic import op
import sqlalchemy as s
revision='ddn_0011'
down_revision='ddn_0010'
branch_labels=None
depends_on=None

def upgrade():
    from importlib import import_module
    table=import_module('migrations.versions.ddn_0008_valmet_shipping').table
    common=lambda:[s.Column('request_key',s.String(64),nullable=False),s.Column('digest',s.String(64),nullable=False),s.Column('actor',s.String(60),nullable=False),s.Column('created_at',s.DateTime(),nullable=False)]
    table('valmet_import_drafts',[*common(),s.Column('report',s.Text(),nullable=False)],constraints=[s.UniqueConstraint('context_id','request_key')])
    table('valmet_import_revisions',[*common(),s.Column('draft_id',s.Integer(),nullable=False),s.Column('plan',s.Text(),nullable=False)],refs=[('draft_id','valmet_import_drafts')],constraints=[s.UniqueConstraint('context_id','request_key')])
    table('valmet_import_confirmations',[*common(),s.Column('revision_id',s.Integer(),nullable=False),s.Column('entry_id',s.Integer(),nullable=False),s.Column('client_id',s.Integer(),s.ForeignKey('clients.id'),nullable=False)],refs=[('revision_id','valmet_import_revisions'),('entry_id','valmet_entries')],constraints=[s.UniqueConstraint('context_id'),s.UniqueConstraint('context_id','request_key'),s.UniqueConstraint('context_id','revision_id'),s.UniqueConstraint('context_id','entry_id')])
    op.create_index('uq_valmet_import_owner_digest','valmet_import_confirmations',['client_id','digest'],unique=True)
    expr="status IN ('draft','confirmed','imported') AND version>0 AND ((status IN ('draft','imported') AND receipt_id IS NULL) OR (status='confirmed' AND receipt_id IS NOT NULL))"
    if op.get_bind().dialect.name!='postgresql':
        with op.batch_alter_table('valmet_entries') as b:
            b.drop_constraint('valmet_entry_state',type_='check');b.create_check_constraint('valmet_entry_state',expr)
        return
    op.drop_constraint('valmet_entry_state','valmet_entries',type_='check');op.create_check_constraint('valmet_entry_state','valmet_entries',expr)
    from pathlib import Path
    conn=op.get_bind();schema=conn.dialect.identifier_preparer.quote(conn.scalar(s.text('SELECT current_schema()')))
    conn.exec_driver_sql(Path(__file__).with_suffix('.sql').read_text(encoding='utf-8').replace('__WMS_SCHEMA__',schema))

def downgrade():raise RuntimeError('Restaurar backup verificado; nunca apagar proveniência histórica.')
