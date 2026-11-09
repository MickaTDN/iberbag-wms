"""Stable Valmet roll identities; never infer legacy identity/classification."""
from pathlib import Path
from alembic import op
import sqlalchemy as s
revision='ddn_0010'
down_revision='ddn_0009'
branch_labels=None
depends_on=None

def create(name,cols,refs=()):
    op.create_table(name,s.Column('id',s.Integer(),primary_key=True),s.Column('context_id',s.Integer(),s.ForeignKey('storage_contexts.id'),nullable=False,server_default='1'),*cols,
        s.UniqueConstraint('context_id','id'),*(s.ForeignKeyConstraint(['context_id',col],[target+'.context_id',target+'.id']) for col,target in refs))
    op.create_index('ix_'+name+'_context_id',name,['context_id'])

def upgrade():
    create('valmet_roll_events',[s.Column('unit_id',s.Integer(),nullable=False),s.Column('request_key',s.String(64),nullable=False),s.Column('action',s.String(20),nullable=False),
        s.Column('payload',s.Text(),nullable=False),s.Column('reason',s.Text(),nullable=False),s.Column('evidence',s.Text(),nullable=False),s.Column('actor',s.String(60),nullable=False),s.Column('created_at',s.DateTime(),nullable=False),s.UniqueConstraint('context_id','request_key',name='uq_valmet_roll_event_key')], [('unit_id','valmet_units')])
    create('valmet_rolls',[s.Column('unit_id',s.Integer(),nullable=False),s.Column('article',s.String(120)),s.Column('lot',s.String(120),nullable=False),s.Column('source_event',s.Integer()),s.Column('source_move',s.Integer())],
        [('unit_id','valmet_units'),('source_event','valmet_roll_events'),('source_move','valmet_control_moves')])
    create('valmet_roll_states',[s.Column('unit_id',s.Integer(),nullable=False),s.Column('present',s.Boolean(),nullable=False),s.Column('divisible',s.Boolean(),nullable=False)], [('id','valmet_rolls'),('unit_id','valmet_units')])
    if op.get_bind().dialect.name=='postgresql':
        conn=op.get_bind();schema=conn.scalar(s.text('SELECT current_schema()'));quote=conn.dialect.identifier_preparer.quote(schema)
        # Published revisions remain immutable. Extend only the two existing
        # guards' whole-pallet branch; individual IDs get additional guards below.
        old=Path(__file__).with_name('ddn_0008_valmet_shipping.sql').read_text(encoding='utf-8')
        for name in ('valmet_reservation_totals','valmet_apply_line'):
            import re
            definition=re.search(r'CREATE (?:OR REPLACE )?FUNCTION '+name+r'\(\).*?END \$\$;',old,re.S).group()
            definition=definition.replace('CREATE FUNCTION','CREATE OR REPLACE FUNCTION')
            for alias in ('u','z'):
                expression=alias+".kind='rolos' AND COALESCE(rule,'unknown')<>'yes'"
                definition=definition.replace(expression,expression+" AND NOT EXISTS(SELECT 1 FROM valmet_rolls rr WHERE rr.unit_id="+('u.id' if alias=='u' else 'z.unit_id')+" AND rr.context_id="+alias+".context_id)")
            conn.exec_driver_sql(definition.replace('__WMS_SCHEMA__',quote))
        epoch=Path(__file__).with_name('ddn_0009_valmet_control.sql').read_text(encoding='utf-8').split('CREATE FUNCTION valmet_control_position_state')[0]
        epoch=epoch.replace('CREATE FUNCTION','CREATE OR REPLACE FUNCTION',1)
        epoch=epoch.replace('\n$$;', "\n|| CASE WHEN EXISTS(SELECT 1 FROM valmet_roll_events WHERE context_id=ctx AND unit_id=uid) THEN jsonb_build_object('rolls',(SELECT max(id) FROM valmet_roll_events WHERE context_id=ctx AND unit_id=uid)) ELSE '{}'::jsonb END\n$$;")
        conn.exec_driver_sql(epoch)
        conn.exec_driver_sql(Path(__file__).with_suffix('.sql').read_text(encoding='utf-8').replace('__WMS_SCHEMA__',quote))

def downgrade():raise RuntimeError('Restore a verified backup; do not discard roll identities/history.')
