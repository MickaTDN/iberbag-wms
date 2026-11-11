"""Exact-bobine outgoing guides, immutable reservations/expeditions and archived A4."""
from importlib import import_module
from pathlib import Path
from alembic import op
import sqlalchemy as s
revision='ddn_0017'
down_revision='ddn_0016'
branch_labels=None
depends_on=None

def upgrade():
    h=import_module('migrations.versions.ddn_0008_valmet_shipping');table=h.table;audit=h.audit_columns
    table('alcobre_dispatches',[s.Column('request_key',s.String(64),nullable=False),s.Column('source_name',s.String(240),nullable=False),s.Column('source_pdf',s.LargeBinary(),nullable=False),s.Column('source_digest',s.String(64),nullable=False),s.Column('extraction',s.Text(),nullable=False),s.Column('created_by',s.Integer(),s.ForeignKey('users.id'),nullable=False),s.Column('created_at',s.DateTime(),nullable=False),s.Column('version',s.Integer(),nullable=False),s.Column('active_revision',s.Integer()),s.Column('status',s.String(20),nullable=False)],constraints=[s.UniqueConstraint('context_id','request_key'),s.UniqueConstraint('context_id','source_digest'),s.CheckConstraint("version>=0 AND status IN ('draft','prepared','confirmed','cancelled')",name='alcobre_dispatch_status')])
    table('alcobre_dispatch_revisions',[s.Column('dispatch_id',s.Integer(),nullable=False),s.Column('version',s.Integer(),nullable=False),s.Column('request_key',s.String(64),nullable=False),s.Column('payload',s.Text(),nullable=False),s.Column('digest',s.String(64),nullable=False),s.Column('reason',s.Text(),nullable=False),s.Column('manual',s.Boolean(),nullable=False),*audit()],refs=[('dispatch_id','alcobre_dispatches')],constraints=[s.UniqueConstraint('context_id','dispatch_id','version'),s.UniqueConstraint('context_id','request_key')])
    table('alcobre_dispatch_operations',[s.Column('dispatch_id',s.Integer(),nullable=False),s.Column('revision_id',s.Integer()),s.Column('request_key',s.String(64),nullable=False),s.Column('kind',s.String(20),nullable=False),s.Column('payload',s.Text(),nullable=False),s.Column('snapshot',s.Text(),nullable=False),*audit()],refs=[('dispatch_id','alcobre_dispatches'),('revision_id','alcobre_dispatch_revisions')],constraints=[s.UniqueConstraint('context_id','request_key'),s.CheckConstraint("kind IN ('prepare','confirm','cancel')",name='alcobre_dispatch_operation_kind')])
    op.create_index('uq_alcobre_dispatch_confirmation','alcobre_dispatch_operations',['context_id','dispatch_id'],unique=True,postgresql_where=s.text("kind='confirm'"),sqlite_where=s.text("kind='confirm'"))
    table('alcobre_dispatch_events',[s.Column('operation_id',s.Integer(),nullable=False),s.Column('bobine_id',s.Integer(),nullable=False),s.Column('previous_id',s.Integer()),s.Column('state',s.String(20),nullable=False)],refs=[('operation_id','alcobre_dispatch_operations'),('bobine_id','alcobre_bobines'),('previous_id','alcobre_dispatch_events')],constraints=[s.UniqueConstraint('context_id','operation_id','bobine_id'),s.CheckConstraint("state IN ('available','reserved','shipped','blocked')",name='alcobre_dispatch_event_state')])
    op.create_index('ix_alcobre_dispatch_bobine_event','alcobre_dispatch_events',['context_id','bobine_id','id'])
    table('alcobre_dispatch_documents',[s.Column('revision_id',s.Integer()),s.Column('operation_id',s.Integer()),s.Column('pdf',s.LargeBinary(),nullable=False),s.Column('digest',s.String(64),nullable=False),*audit()],refs=[('revision_id','alcobre_dispatch_revisions'),('operation_id','alcobre_dispatch_operations')],constraints=[s.UniqueConstraint('context_id','revision_id'),s.UniqueConstraint('context_id','operation_id'),s.CheckConstraint('(revision_id IS NULL) <> (operation_id IS NULL)',name='alcobre_dispatch_document_origin')])
    uniques=s.inspect(op.get_bind()).get_unique_constraints('alcobre_placements')
    target=next(x for x in uniques if set(x['column_names'])=={'context_id','local'})
    if op.get_bind().dialect.name=='postgresql':
        op.drop_constraint(target['name'],'alcobre_placements',type_='unique')
        conn=op.get_bind();schema=conn.dialect.identifier_preparer.quote(conn.scalar(s.text('SELECT current_schema()')))
        conn.exec_driver_sql(Path(__file__).with_suffix('.sql').read_text().replace('__WMS_SCHEMA__',schema))
        # Published books remain intact. Only live occupation changes; a departed
        # bobine's immutable placement remains traceable and the slot is reusable.
        definition=conn.scalar(s.text("SELECT pg_get_functiondef('alcobre_guard()'::regprocedure)"))
        old='SELECT count(*) INTO used FROM alcobre_placements WHERE local=NEW.local AND context_id=NEW.context_id;'
        if old not in definition:raise RuntimeError('Unexpected Alcobre guard; reconcile before upgrade.')
        conn.exec_driver_sql(definition.replace(old,"SELECT count(*) INTO used FROM alcobre_placements WHERE local=NEW.local AND context_id=NEW.context_id AND alcobre_current_state(bobine_id)<>'shipped';"))
    else:
        convention={'uq':'uq_%(table_name)s_%(column_0_name)s_%(column_1_name)s'}
        with op.batch_alter_table('alcobre_placements',naming_convention=convention) as batch:
            batch.drop_constraint(target['name'] or 'uq_alcobre_placements_context_id_local',type_='unique')

def downgrade():raise RuntimeError('Restaurar backup verificado; não apagar guias, reservas ou expedições.')
