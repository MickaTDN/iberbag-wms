"""Independent Valmet inventory evidence and immutable control movements."""
from pathlib import Path
from alembic import op
import sqlalchemy as s

revision='ddn_0009'
down_revision='ddn_0008'
branch_labels=None
depends_on=None

def table(name,cols,refs=(),constraints=()):
    op.create_table(name,s.Column('id',s.Integer(),primary_key=True),s.Column('context_id',s.Integer(),s.ForeignKey('storage_contexts.id'),nullable=False,server_default='1'),
        *cols,s.UniqueConstraint('context_id','id',name='uq_ctx_'+name),*constraints,
        *(s.ForeignKeyConstraint(['context_id',f],[target+'.context_id',target+'.id'],name='fk_ctx_'+name+'_'+f) for f,target in refs))
    op.create_index('ix_'+name+'_context_id',name,['context_id'])

def stamp():return [s.Column('actor',s.String(60),nullable=False),s.Column('created_at',s.DateTime(),nullable=False)]

def upgrade():
    table('valmet_inventories',[s.Column('request_key',s.String(64),nullable=False),s.Column('scope',s.Text(),nullable=False),s.Column('reference',s.Text(),nullable=False),
        s.Column('status',s.String(20),nullable=False),s.Column('version',s.Integer(),nullable=False),s.Column('created_by',s.Integer(),s.ForeignKey('users.id'),nullable=False),s.Column('created_at',s.DateTime(),nullable=False)],
        constraints=[s.UniqueConstraint('context_id','request_key'),s.CheckConstraint("status IN ('draft','submitted','closed') AND version>0",name='valmet_inventory_state')])
    table('valmet_counts',[s.Column('inventory_id',s.Integer(),nullable=False),s.Column('target',s.String(160),nullable=False),s.Column('request_key',s.String(64),nullable=False),
        s.Column('version',s.Integer(),nullable=False),s.Column('kind',s.String(20),nullable=False),s.Column('payload',s.Text(),nullable=False),s.Column('reference',s.Text(),nullable=False),*stamp()],
        refs=[('inventory_id','valmet_inventories')],constraints=[s.UniqueConstraint('context_id','request_key')])
    op.create_index('ix_valmet_counts_target','valmet_counts',['context_id','inventory_id','target','id'])
    table('valmet_control_drafts',[s.Column('request_key',s.String(64),nullable=False),s.Column('kind',s.String(20),nullable=False),s.Column('position_id',s.Integer(),nullable=False),
        s.Column('inventory_id',s.Integer()),s.Column('count_id',s.Integer()),s.Column('payload',s.Text(),nullable=False),s.Column('reference',s.Text(),nullable=False),s.Column('version',s.Integer(),nullable=False),
        s.Column('status',s.String(20),nullable=False),s.Column('created_by',s.Integer(),s.ForeignKey('users.id'),nullable=False),s.Column('created_at',s.DateTime(),nullable=False)],
        refs=[('position_id','valmet_positions'),('inventory_id','valmet_inventories'),('count_id','valmet_counts')],constraints=[s.UniqueConstraint('context_id','request_key'),s.CheckConstraint("kind IN ('transfer','adjustment') AND status IN ('draft','confirmed') AND version>0",name='valmet_control_draft_state')])
    table('valmet_control_moves',[s.Column('draft_id',s.Integer(),nullable=False),s.Column('unit_id',s.Integer(),nullable=False),s.Column('snapshot',s.Text(),nullable=False),*stamp()],
        refs=[('draft_id','valmet_control_drafts'),('unit_id','valmet_units')],constraints=[s.UniqueConstraint('context_id','draft_id')])
    table('valmet_inventory_decisions',[s.Column('inventory_id',s.Integer(),nullable=False),s.Column('count_id',s.Integer(),nullable=False),s.Column('move_id',s.Integer()),s.Column('request_key',s.String(64),nullable=False),
        s.Column('kind',s.String(20),nullable=False),s.Column('reason',s.Text(),nullable=False),*stamp()],refs=[('inventory_id','valmet_inventories'),('count_id','valmet_counts'),('move_id','valmet_control_moves')],constraints=[s.UniqueConstraint('context_id','request_key')])
    op.create_index('uq_valmet_decision_final','valmet_inventory_decisions',['context_id','count_id'],unique=True,postgresql_where=s.text("kind IN ('approve','omit')"),sqlite_where=s.text("kind IN ('approve','omit')"))
    table('valmet_control_documents',[s.Column('inventory_id',s.Integer(),nullable=False),s.Column('version',s.Integer(),nullable=False),s.Column('kind',s.String(20),nullable=False),s.Column('snapshot',s.Text(),nullable=False),
        s.Column('pdf',s.LargeBinary(),nullable=False),s.Column('digest',s.String(64),nullable=False),*stamp()],refs=[('inventory_id','valmet_inventories')],constraints=[s.UniqueConstraint('context_id','inventory_id','version','kind',name='uq_valmet_control_document')])
    if op.get_bind().dialect.name=='postgresql':
        conn=op.get_bind();schema=conn.scalar(s.text('SELECT current_schema()'))
        conn.exec_driver_sql(Path(__file__).with_suffix('.sql').read_text(encoding='utf-8').replace('__WMS_SCHEMA__',conn.dialect.identifier_preparer.quote(schema)))

def downgrade():raise RuntimeError('Restore a verified backup; never remove control history.')
