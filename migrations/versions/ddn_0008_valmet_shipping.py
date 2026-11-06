"""Valmet append-only fulfilment and trigger-maintained remaining stock."""
from pathlib import Path
from alembic import op
import sqlalchemy as s

revision='ddn_0008'
down_revision='ddn_0007'
branch_labels=None
depends_on=None

def table(name,cols,refs=(),constraints=()):
    op.create_table(name,s.Column('id',s.Integer(),primary_key=True),
        s.Column('context_id',s.Integer(),s.ForeignKey('storage_contexts.id'),nullable=False,server_default='1'),
        *cols,s.UniqueConstraint('context_id','id',name='uq_ctx_'+name),*constraints,
        *(s.ForeignKeyConstraint(['context_id',field],[target+'.context_id',target+'.id'],name='fk_ctx_'+name+'_'+field) for field,target in refs))
    op.create_index('ix_'+name+'_context_id',name,['context_id'])

def audit_columns():return [s.Column('actor',s.String(60),nullable=False),s.Column('created_at',s.DateTime(),nullable=False)]

def upgrade():
    table('valmet_positions',[s.Column('unit_id',s.Integer(),nullable=False),s.Column('root',s.Boolean(),nullable=False),
        s.Column('local',s.String(120)),s.Column('physical_quantity',s.Integer(),nullable=False),s.Column('materials',s.Text(),nullable=False),
        s.Column('uncertain',s.Boolean(),nullable=False)],refs=[('unit_id','valmet_units')],constraints=[s.CheckConstraint('physical_quantity>=0',name='valmet_position_nonnegative'),s.UniqueConstraint('context_id','unit_id','local',name='uq_valmet_positions_local')])
    op.create_index('ix_valmet_positions_unit','valmet_positions',['context_id','unit_id'])
    op.create_index('uq_valmet_positions_root','valmet_positions',['context_id','unit_id'],unique=True,postgresql_where=s.text('root'),sqlite_where=s.text('root'))
    table('valmet_shipments',[s.Column('preparation_id',s.Integer(),nullable=False),s.Column('revision_id',s.Integer(),nullable=False),
        s.Column('request_key',s.String(64),nullable=False),s.Column('payload',s.Text(),nullable=False),s.Column('loaded_on',s.DateTime(),nullable=False),
        s.Column('references',s.Text(),nullable=False),s.Column('snapshot',s.Text(),nullable=False),*audit_columns()],
        refs=[('preparation_id','valmet_preparations'),('revision_id','valmet_plan_revisions')],constraints=[s.UniqueConstraint('context_id','request_key')])
    table('valmet_shipment_lines',[s.Column('shipment_id',s.Integer(),nullable=False),s.Column('reservation_id',s.Integer(),nullable=False),
        s.Column('position_id',s.Integer(),nullable=False),s.Column('physical_quantity',s.Integer(),nullable=False),s.Column('materials',s.Text(),nullable=False),
        s.Column('unknown_ack',s.Boolean(),nullable=False)],refs=[('shipment_id','valmet_shipments'),('reservation_id','valmet_reservations'),('position_id','valmet_positions')],
        constraints=[s.CheckConstraint('physical_quantity>=0',name='valmet_shipment_nonnegative'),s.UniqueConstraint('context_id','shipment_id','position_id',name='uq_valmet_shipment_position')])
    table('valmet_occurrences',[s.Column('preparation_id',s.Integer(),nullable=False),s.Column('unit_id',s.Integer(),nullable=False),
        s.Column('version',s.Integer(),nullable=False),s.Column('kind',s.String(30),nullable=False),s.Column('request_key',s.String(64)),s.Column('resolves_id',s.Integer()),s.Column('details',s.Text(),nullable=False),*audit_columns()],
        refs=[('preparation_id','valmet_preparations'),('unit_id','valmet_units'),('resolves_id','valmet_occurrences')],constraints=[s.UniqueConstraint('context_id','request_key'),s.UniqueConstraint('context_id','resolves_id',name='uq_valmet_occurrence_resolution')])
    table('valmet_relocations',[s.Column('preparation_id',s.Integer(),nullable=False),s.Column('position_id',s.Integer(),nullable=False),
        s.Column('request_key',s.String(64),nullable=False),s.Column('payload',s.Text(),nullable=False),s.Column('target_local',s.String(120),nullable=False),s.Column('physical_quantity',s.Integer(),nullable=False),s.Column('materials',s.Text(),nullable=False),
        s.Column('note',s.Text(),nullable=False),*audit_columns()],refs=[('preparation_id','valmet_preparations'),('position_id','valmet_positions')],constraints=[s.UniqueConstraint('context_id','request_key')])
    table('valmet_documents',[s.Column('preparation_id',s.Integer(),nullable=False),s.Column('revision_id',s.Integer(),nullable=False),
        s.Column('pdf',s.LargeBinary(),nullable=False),s.Column('digest',s.String(64),nullable=False),*audit_columns()],
        refs=[('preparation_id','valmet_preparations'),('revision_id','valmet_plan_revisions')],constraints=[s.UniqueConstraint('context_id','revision_id',name='uq_valmet_document_revision')])
    if op.get_bind().dialect.name!='postgresql':
        # SQLite is read-only operationally; use batch to preserve its rows.
        with op.batch_alter_table('valmet_preparations') as batch:
            batch.drop_constraint('valmet_preparation_status',type_='check')
            batch.create_check_constraint('valmet_preparation_status',"status IN ('draft','reserved','ready','shipped','cancelled') AND version>0")
        return
    op.drop_constraint('valmet_preparation_status','valmet_preparations',type_='check')
    op.create_check_constraint('valmet_preparation_status','valmet_preparations',"status IN ('draft','reserved','ready','shipped','cancelled') AND version>0")
    conn=op.get_bind();schema=conn.scalar(s.text('SELECT current_schema()'))
    sql=Path(__file__).with_suffix('.sql').read_text(encoding='utf-8')
    conn.exec_driver_sql(sql.replace('__WMS_SCHEMA__',conn.dialect.identifier_preparer.quote(schema)))
    # Keep published guards' business rules, replacing only their stock source.
    for name in ('valmet_check_unit','valmet_location_guard','valmet_combined_stock_guard'):
        definition=conn.scalar(s.text('SELECT pg_get_functiondef(CAST(:fn AS regprocedure))'),{'fn':name+'()'})
        old='FROM valmet_units WHERE'
        if old not in definition:raise RuntimeError('Unexpected capacity guard; reconcile before upgrade.')
        conn.exec_driver_sql(definition.replace(old,'FROM valmet_positions WHERE'))
    definition=conn.scalar(s.text("SELECT pg_get_functiondef('valmet_handling_guard()'::regprocedure)"))
    conn.exec_driver_sql(definition.replace("p.status='reserved'","p.status IN ('reserved','ready')"))

def downgrade():raise RuntimeError('Restore a verified backup; never erase shipments or documents.')
