"""NKT explicit PEG reservations, shipments, compensation and archived A4 sheets."""
from importlib import import_module
from pathlib import Path
from alembic import op
import sqlalchemy as s
revision='ddn_0014'
down_revision='ddn_0013'
branch_labels=None
depends_on=None

def upgrade():
    helpers=import_module('migrations.versions.ddn_0008_valmet_shipping');table=helpers.table;audit=helpers.audit_columns
    table('nkt_preparations',[s.Column('request_key',s.String(64),nullable=False),s.Column('created_by',s.Integer(),s.ForeignKey('users.id'),nullable=False),s.Column('created_at',s.DateTime(),nullable=False),s.Column('version',s.Integer(),nullable=False),s.Column('active_revision',s.Integer()),s.Column('status',s.String(30),nullable=False)],constraints=[s.UniqueConstraint('context_id','request_key'),s.CheckConstraint("version>0 AND status IN ('draft','reserved','partial','loaded','cancelled','reconciliation')",name='nkt_preparation_state')])
    table('nkt_plan_revisions',[s.Column('preparation_id',s.Integer(),nullable=False),s.Column('version',s.Integer(),nullable=False),s.Column('payload',s.Text(),nullable=False),s.Column('digest',s.String(64),nullable=False),s.Column('reason',s.Text(),nullable=False),*audit()],refs=[('preparation_id','nkt_preparations')],constraints=[s.UniqueConstraint('context_id','preparation_id','version')])
    table('nkt_operations',[s.Column('preparation_id',s.Integer(),nullable=False),s.Column('revision_id',s.Integer(),nullable=False),s.Column('request_key',s.String(64),nullable=False),s.Column('kind',s.String(20),nullable=False),s.Column('payload',s.Text(),nullable=False),s.Column('snapshot',s.Text(),nullable=False),s.Column('correction_id',s.Integer()),s.Column('corrects_id',s.Integer()),*audit()],refs=[('preparation_id','nkt_preparations'),('revision_id','nkt_plan_revisions'),('corrects_id','nkt_operations')],constraints=[s.UniqueConstraint('context_id','request_key'),s.UniqueConstraint('context_id','correction_id'),s.CheckConstraint("kind IN ('reserve','cancel','ship','restore','metadata')",name='nkt_operation_kind')])
    table('nkt_corrections',[s.Column('preparation_id',s.Integer(),nullable=False),s.Column('operation_id',s.Integer(),nullable=False),s.Column('request_key',s.String(64),nullable=False),s.Column('payload',s.Text(),nullable=False),s.Column('snapshot',s.Text(),nullable=False),s.Column('digest',s.String(64),nullable=False),*audit()],refs=[('preparation_id','nkt_preparations'),('operation_id','nkt_operations')],constraints=[s.UniqueConstraint('context_id','request_key')])
    # correction_id is validated in SQL; keeping this edge unidirectional avoids a restore-time FK cycle.
    table('nkt_stock_events',[s.Column('operation_id',s.Integer(),nullable=False),s.Column('pallet_id',s.Integer(),nullable=False),s.Column('previous_event_id',s.Integer()),s.Column('state',s.String(20),nullable=False)],refs=[('operation_id','nkt_operations'),('pallet_id','nkt_pallets'),('previous_event_id','nkt_stock_events')],constraints=[s.UniqueConstraint('context_id','operation_id','pallet_id'),s.CheckConstraint("state IN ('available','reserved','shipped')",name='nkt_event_state')])
    op.create_index('ix_nkt_event_latest','nkt_stock_events',['context_id','pallet_id','id'])
    table('nkt_documents',[s.Column('revision_id',s.Integer()),s.Column('operation_id',s.Integer()),s.Column('correction_id',s.Integer()),s.Column('pdf',s.LargeBinary(),nullable=False),s.Column('digest',s.String(64),nullable=False),*audit()],refs=[('revision_id','nkt_plan_revisions'),('operation_id','nkt_operations'),('correction_id','nkt_corrections')],constraints=[*(s.UniqueConstraint('context_id',f) for f in ('revision_id','operation_id','correction_id')),s.CheckConstraint('(CASE WHEN revision_id IS NULL THEN 0 ELSE 1 END)+(CASE WHEN operation_id IS NULL THEN 0 ELSE 1 END)+(CASE WHEN correction_id IS NULL THEN 0 ELSE 1 END)=1',name='nkt_document_origin')])
    if op.get_bind().dialect.name=='postgresql':
        conn=op.get_bind();schema=conn.dialect.identifier_preparer.quote(conn.scalar(s.text('SELECT current_schema()')))
        conn.exec_driver_sql(Path(__file__).with_suffix('.sql').read_text().replace('__WMS_SCHEMA__',schema))
        for name in ('nkt_guard','nkt_location_guard','nkt_combined_stock'):
            definition=conn.scalar(s.text('SELECT pg_get_functiondef(CAST(:fn AS regprocedure))'),dict(fn=name+'()'))
            # Replace only the occupation source, preserving all published entry/identity rules.
            import re
            updated=re.sub(r'SELECT count\(\*\) INTO used FROM nkt_pallets WHERE ([^;]+);',r"SELECT count(*) INTO used FROM nkt_pallets WHERE \1 AND nkt_current_state(id)<>'shipped';",definition)
            if updated==definition:raise RuntimeError('Unexpected NKT capacity guard; reconcile explicitly.')
            conn.exec_driver_sql(updated)

def downgrade():raise RuntimeError('Restore a verified backup; never delete PEG movements or archived documents.')
