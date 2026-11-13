"""Cosmopak PACKINGs and shared logical staging; additive, no historical rewrite."""
from pathlib import Path
from alembic import op
import sqlalchemy as s
revision='ddn_0022'
down_revision='ddn_0021'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('cosmo_packings',
        s.Column('id',s.Integer(),nullable=False,primary_key=True),
        s.Column('request_key',s.String(length=64),nullable=False),
        s.Column('source_name',s.String(length=240),nullable=False),
        s.Column('source_sha256',s.String(length=64),nullable=False),
        s.Column('source_excel',s.LargeBinary(),nullable=False),
        s.Column('report',s.Text(),nullable=False),
        s.Column('created_by',s.Integer(),nullable=False),
        s.Column('created_at',s.DateTime(),nullable=False),
        s.Column('context_id',s.Integer(),nullable=False,server_default=s.text('1')),
        s.ForeignKeyConstraint(['context_id'],['storage_contexts.id']),
        s.ForeignKeyConstraint(['created_by'],['users.id']),
        s.UniqueConstraint('context_id','request_key'),
        s.UniqueConstraint('context_id','id',name='uq_ctx_cosmo_packings'))
    op.create_index('ix_cosmo_packings_context_id','cosmo_packings',['context_id'],unique=False)
    op.create_table('cosmo_packing_states',
        s.Column('id',s.Integer(),nullable=False,primary_key=True),
        s.Column('packing_id',s.Integer(),nullable=False),
        s.Column('reference',s.String(length=120),nullable=True),
        s.Column('reference_key',s.String(length=120),nullable=True),
        s.Column('stage',s.String(length=24),nullable=False),
        s.Column('version',s.Integer(),nullable=False),
        s.Column('mapping_id',s.Integer(),nullable=True),
        s.Column('loading_started',s.Boolean(),nullable=False),
        s.Column('review_id',s.Integer(),nullable=True),
        s.Column('dispatched_on',s.DateTime(),nullable=True),
        s.Column('final_snapshot',s.Text(),nullable=True),
        s.Column('context_id',s.Integer(),nullable=False,server_default=s.text('1')),
        s.CheckConstraint("stage IN ('draft','reviewed','preparing','prepared','loading','load_closed','dispatch_reviewed','shipped') AND version>=0",name='cosmo_packing_stage_valid'),
        s.ForeignKeyConstraint(['context_id'],['storage_contexts.id']),
        s.ForeignKeyConstraint(['context_id', 'packing_id'],['cosmo_packings.context_id', 'cosmo_packings.id'],name='fk_ctx_cosmo_packing_states_packing_id'),
        s.UniqueConstraint('context_id','packing_id'),
        s.UniqueConstraint('context_id','reference_key'),
        s.UniqueConstraint('context_id','id',name='uq_ctx_cosmo_packing_states'))
    op.create_index('ix_cosmo_packing_states_context_id','cosmo_packing_states',['context_id'],unique=False)
    op.create_table('cosmo_packing_lines',
        s.Column('id',s.Integer(),nullable=False,primary_key=True),
        s.Column('packing_id',s.Integer(),nullable=False),
        s.Column('mapping_id',s.Integer(),nullable=False),
        s.Column('source_row',s.Integer(),nullable=False),
        s.Column('ordinal',s.Integer(),nullable=False),
        s.Column('article',s.String(length=120),nullable=False),
        s.Column('lot',s.String(length=120),nullable=False),
        s.Column('boxes',s.Integer(),nullable=False),
        s.Column('context_id',s.Integer(),nullable=False,server_default=s.text('1')),
        s.CheckConstraint('source_row>0 AND ordinal>0 AND boxes>=0',name='cosmo_packing_line_valid'),
        s.ForeignKeyConstraint(['context_id'],['storage_contexts.id']),
        s.ForeignKeyConstraint(['context_id', 'packing_id'],['cosmo_packings.context_id', 'cosmo_packings.id'],name='fk_ctx_cosmo_packing_lines_packing_id'),
        s.UniqueConstraint('context_id','mapping_id','source_row','ordinal'),
        s.UniqueConstraint('context_id','id',name='uq_ctx_cosmo_packing_lines'))
    op.create_index('ix_cosmo_packing_lines_context_id','cosmo_packing_lines',['context_id'],unique=False)
    op.create_index('ix_cosmo_packing_lines_packing_id','cosmo_packing_lines',['packing_id'],unique=False)
    op.create_table('cosmo_packing_picks',
        s.Column('id',s.Integer(),nullable=False,primary_key=True),
        s.Column('packing_id',s.Integer(),nullable=False),
        s.Column('line_id',s.Integer(),nullable=False),
        s.Column('pallet_id',s.Integer(),nullable=True),
        s.Column('active',s.Boolean(),nullable=False),
        s.Column('prepared',s.Boolean(),nullable=False),
        s.Column('loaded',s.Boolean(),nullable=False),
        s.Column('reason',s.Text(),nullable=True),
        s.Column('prepared_by',s.Integer(),nullable=True),
        s.Column('prepared_at',s.DateTime(),nullable=True),
        s.Column('loaded_by',s.Integer(),nullable=True),
        s.Column('loaded_at',s.DateTime(),nullable=True),
        s.Column('version',s.Integer(),nullable=False),
        s.Column('context_id',s.Integer(),nullable=False,server_default=s.text('1')),
        s.ForeignKeyConstraint(['context_id'],['storage_contexts.id']),
        s.ForeignKeyConstraint(['prepared_by'],['users.id']),
        s.ForeignKeyConstraint(['loaded_by'],['users.id']),
        s.ForeignKeyConstraint(['context_id', 'line_id'],['cosmo_packing_lines.context_id', 'cosmo_packing_lines.id'],name='fk_ctx_cosmo_packing_picks_line_id'),
        s.ForeignKeyConstraint(['context_id', 'packing_id'],['cosmo_packings.context_id', 'cosmo_packings.id'],name='fk_ctx_cosmo_packing_picks_packing_id'),
        s.ForeignKeyConstraint(['context_id', 'pallet_id'],['cosmo_pallets.context_id', 'cosmo_pallets.id'],name='fk_ctx_cosmo_packing_picks_pallet_id'),
        s.UniqueConstraint('context_id','line_id'),
        s.UniqueConstraint('context_id','id',name='uq_ctx_cosmo_packing_picks'))
    op.create_index('ix_cosmo_packing_picks_context_id','cosmo_packing_picks',['context_id'],unique=False)
    op.create_index('ix_cosmo_packing_picks_packing_id','cosmo_packing_picks',['packing_id'],unique=False)
    op.create_index('uq_cosmo_active_pallet','cosmo_packing_picks',['context_id', 'pallet_id'],unique=True,postgresql_where=s.text('active AND pallet_id IS NOT NULL'),sqlite_where=s.text('active AND pallet_id IS NOT NULL'))
    op.create_table('cosmo_packing_actions',
        s.Column('id',s.Integer(),nullable=False,primary_key=True),
        s.Column('packing_id',s.Integer(),nullable=False),
        s.Column('request_key',s.String(length=64),nullable=False),
        s.Column('kind',s.String(length=24),nullable=False),
        s.Column('pick_id',s.Integer(),nullable=True),
        s.Column('pallet_id',s.Integer(),nullable=True),
        s.Column('expected_version',s.Integer(),nullable=False),
        s.Column('payload',s.Text(),nullable=False),
        s.Column('reason',s.Text(),nullable=False),
        s.Column('actor_id',s.Integer(),nullable=False),
        s.Column('created_at',s.DateTime(),nullable=False),
        s.Column('context_id',s.Integer(),nullable=False,server_default=s.text('1')),
        s.ForeignKeyConstraint(['context_id'],['storage_contexts.id']),
        s.ForeignKeyConstraint(['actor_id'],['users.id']),
        s.ForeignKeyConstraint(['context_id', 'packing_id'],['cosmo_packings.context_id', 'cosmo_packings.id'],name='fk_ctx_cosmo_packing_actions_packing_id'),
        s.ForeignKeyConstraint(['context_id', 'pallet_id'],['cosmo_pallets.context_id', 'cosmo_pallets.id'],name='fk_ctx_cosmo_packing_actions_pallet_id'),
        s.ForeignKeyConstraint(['context_id', 'pick_id'],['cosmo_packing_picks.context_id', 'cosmo_packing_picks.id'],name='fk_ctx_cosmo_packing_actions_pick_id'),
        s.UniqueConstraint('context_id','request_key'),
        s.UniqueConstraint('context_id','id',name='uq_ctx_cosmo_packing_actions'))
    op.create_index('ix_cosmo_packing_actions_context_id','cosmo_packing_actions',['context_id'],unique=False)
    op.create_index('ix_cosmo_packing_actions_packing_id','cosmo_packing_actions',['packing_id'],unique=False)
    op.create_table('cosmo_packing_ledger',
        s.Column('id',s.Integer(),nullable=False,primary_key=True),
        s.Column('action_id',s.Integer(),nullable=False),
        s.Column('before_state',s.Text(),nullable=False),
        s.Column('after_state',s.Text(),nullable=False),
        s.Column('context_id',s.Integer(),nullable=False,server_default=s.text('1')),
        s.ForeignKeyConstraint(['context_id'],['storage_contexts.id']),
        s.ForeignKeyConstraint(['context_id', 'action_id'],['cosmo_packing_actions.context_id', 'cosmo_packing_actions.id'],name='fk_ctx_cosmo_packing_ledger_action_id'),
        s.UniqueConstraint('context_id','action_id'),
        s.UniqueConstraint('context_id','id',name='uq_ctx_cosmo_packing_ledger'))
    op.create_index('ix_cosmo_packing_ledger_context_id','cosmo_packing_ledger',['context_id'],unique=False)
    op.create_table('cosmo_packing_moves',
        s.Column('id',s.Integer(),nullable=False,primary_key=True),
        s.Column('action_id',s.Integer(),nullable=False),
        s.Column('pallet_id',s.Integer(),nullable=False),
        s.Column('before_state',s.Text(),nullable=False),
        s.Column('after_state',s.Text(),nullable=False),
        s.Column('context_id',s.Integer(),nullable=False,server_default=s.text('1')),
        s.ForeignKeyConstraint(['context_id'],['storage_contexts.id']),
        s.ForeignKeyConstraint(['context_id', 'action_id'],['cosmo_packing_actions.context_id', 'cosmo_packing_actions.id'],name='fk_ctx_cosmo_packing_moves_action_id'),
        s.ForeignKeyConstraint(['context_id', 'pallet_id'],['cosmo_pallets.context_id', 'cosmo_pallets.id'],name='fk_ctx_cosmo_packing_moves_pallet_id'),
        s.UniqueConstraint('context_id','action_id','pallet_id'),
        s.UniqueConstraint('context_id','id',name='uq_ctx_cosmo_packing_moves'))
    op.create_index('ix_cosmo_packing_moves_context_id','cosmo_packing_moves',['context_id'],unique=False)
    op.create_index('ix_cosmo_packing_moves_pallet_id','cosmo_packing_moves',['pallet_id'],unique=False)
    op.create_table('cosmo_packing_documents',
        s.Column('id',s.Integer(),nullable=False,primary_key=True),
        s.Column('packing_id',s.Integer(),nullable=False),
        s.Column('request_key',s.String(length=64),nullable=False),
        s.Column('kind',s.String(length=20),nullable=False),
        s.Column('snapshot',s.Text(),nullable=False),
        s.Column('pdf',s.LargeBinary(),nullable=False),
        s.Column('digest',s.String(length=64),nullable=False),
        s.Column('actor_id',s.Integer(),nullable=False),
        s.Column('created_at',s.DateTime(),nullable=False),
        s.Column('context_id',s.Integer(),nullable=False,server_default=s.text('1')),
        s.ForeignKeyConstraint(['actor_id'],['users.id']),
        s.ForeignKeyConstraint(['context_id'],['storage_contexts.id']),
        s.ForeignKeyConstraint(['context_id', 'packing_id'],['cosmo_packings.context_id', 'cosmo_packings.id'],name='fk_ctx_cosmo_packing_documents_packing_id'),
        s.UniqueConstraint('context_id','request_key'),
        s.UniqueConstraint('context_id','id',name='uq_ctx_cosmo_packing_documents'))
    op.create_index('ix_cosmo_packing_documents_context_id','cosmo_packing_documents',['context_id'],unique=False)
    op.create_table('physical_staging_areas',
        s.Column('id',s.Integer(),nullable=False,primary_key=True),
        s.Column('warehouse_id',s.Integer(),nullable=False),
        s.Column('label',s.String(length=120),nullable=False),
        s.ForeignKeyConstraint(['warehouse_id'],['warehouses.id'],deferrable=True,initially='DEFERRED'),
        s.UniqueConstraint('warehouse_id'))
    op.create_table('physical_staging_positions',
        s.Column('id',s.Integer(),nullable=False,primary_key=True),
        s.Column('area_id',s.Integer(),nullable=False),
        s.Column('unit_kind',s.String(length=30),nullable=False),
        s.Column('unit_id',s.Integer(),nullable=False),
        s.Column('group_kind',s.String(length=30),nullable=False),
        s.Column('group_id',s.Integer(),nullable=False),
        s.Column('phase',s.String(length=20),nullable=False),
        s.Column('version',s.Integer(),nullable=False),
        s.Column('context_id',s.Integer(),nullable=False,server_default=s.text('1')),
        s.CheckConstraint("phase IN ('staged','loading','rack','shipped') AND version>0",name='physical_staging_phase'),
        s.ForeignKeyConstraint(['context_id'],['storage_contexts.id']),
        s.ForeignKeyConstraint(['area_id'],['physical_staging_areas.id']),
        s.UniqueConstraint('context_id','unit_kind','unit_id'),
        s.UniqueConstraint('context_id','id',name='uq_ctx_physical_staging_positions'))
    op.create_index('ix_physical_staging_positions_context_id','physical_staging_positions',['context_id'],unique=False)
    op.create_table('physical_staging_events',
        s.Column('id',s.Integer(),nullable=False,primary_key=True),
        s.Column('area_id',s.Integer(),nullable=False),
        s.Column('unit_kind',s.String(length=30),nullable=False),
        s.Column('unit_id',s.Integer(),nullable=False),
        s.Column('group_kind',s.String(length=30),nullable=False),
        s.Column('group_id',s.Integer(),nullable=False),
        s.Column('phase',s.String(length=20),nullable=False),
        s.Column('source_kind',s.String(length=30),nullable=False),
        s.Column('source_id',s.Integer(),nullable=False),
        s.Column('before_state',s.Text(),nullable=False),
        s.Column('after_state',s.Text(),nullable=False),
        s.Column('context_id',s.Integer(),nullable=False,server_default=s.text('1')),
        s.ForeignKeyConstraint(['context_id'],['storage_contexts.id']),
        s.ForeignKeyConstraint(['area_id'],['physical_staging_areas.id']),
        s.UniqueConstraint('context_id','source_kind','source_id','unit_kind','unit_id'),
        s.UniqueConstraint('context_id','id',name='uq_ctx_physical_staging_events'))
    op.create_index('ix_physical_staging_events_context_id','physical_staging_events',['context_id'],unique=False)
    con=op.get_bind()
    unique=next(c['name'] for c in s.inspect(con).get_unique_constraints('cosmo_states') if set(c['column_names'])=={'context_id','stock_id'})
    with op.batch_alter_table('cosmo_states',naming_convention={'uq':'uq_%(table_name)s_%(column_0_name)s_%(column_1_name)s'}) as batch:
        batch.drop_constraint(unique or 'uq_cosmo_states_context_id_stock_id',type_='unique')
        batch.drop_constraint('cosmo_state_valid',type_='check')
        batch.create_check_constraint('cosmo_state_valid',"status IN ('planned','placed','missing','blocked','approved','shipped') AND decision IN ('unresolved','accept','exclude') AND version>=0 AND (actual_boxes IS NULL OR actual_boxes>=0)")
    if con.dialect.name=='postgresql':
        op.execute(Path(__file__).with_suffix('.sql').read_text(encoding='utf-8'))
        schema=con.dialect.identifier_preparer.quote(con.scalar(s.text('SELECT current_schema()')))
        for name in ('ddn_packing_projection','ddn_packing_source','ddn_packing_initialize','ddn_packing_command','ddn_packing_document','ddn_staging_apply','ddn_packing_reconcile','ddn_packing_move','ddn_packing_snapshot','ddn_cosmo_stock'):
            args={'ddn_packing_move':'cosmo_packing_actions, integer, text, integer, text','ddn_packing_snapshot':'integer, integer'}.get(name,'')
            op.execute(f'ALTER FUNCTION {name}({args}) SET search_path TO pg_catalog, {schema}, pg_temp')
            op.execute(f'REVOKE ALL ON FUNCTION {name}({args}) FROM PUBLIC')
        # Extend source-typed rack transitions without rewriting ddn_0020/21.
        definition=con.scalar(s.text("SELECT pg_get_functiondef('ddn_shared_event()'::regprocedure)"))
        anchor="  ELSIF NEW.kind IN('cosmo_place','cosmo_vacate','cosmo_approve') THEN"
        if definition.count(anchor)!=1:raise RuntimeError('Motor físico inesperado; reconciliar antes de migrar.')
        branch=Path(__file__).with_name('ddn_0022_common_branch.sql').read_text(encoding='utf-8')
        op.execute(definition.replace(anchor,branch+'\n'+anchor))
        definition=con.scalar(s.text("SELECT pg_get_functiondef('ddn_claim_position()'::regprocedure)"))
        anchor='  SELECT warehouse_id INTO wh FROM storage_contexts WHERE id=NEW.context_id;'
        if definition.count(anchor)!=1:raise RuntimeError('Catálogo físico inesperado.')
        branch="""
  IF EXISTS(SELECT 1 FROM physical_staging_areas WHERE warehouse_id=wh AND lower(label)=physical_code) THEN
    IF TG_TABLE_NAME='stock' AND EXISTS(SELECT 1 FROM storage_contexts WHERE id=NEW.context_id AND client_id=3 AND warehouse_id=1)
      AND pg_trigger_depth()>1 AND current_user=pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid=TG_RELID)) THEN RETURN NEW;END IF;
    RAISE EXCEPTION 'Shared staging area is not a rack or a direct stock entry' USING ERRCODE='23514';
  END IF;
"""
        op.execute(definition.replace(anchor,anchor+branch))
        definition=con.scalar(s.text("SELECT pg_get_functiondef('ddn_shared_stock_guard()'::regprocedure)"))
        anchor="    IF h.state='pending_transfer' AND NOT EXISTS"
        if definition.count(anchor)!=1:raise RuntimeError('Guarda de capacidade inesperada.')
        branch="""    IF h.state='pending_transfer' AND h.source_kind='cosmo_packing' THEN
      IF NOT EXISTS(SELECT 1 FROM cosmo_packing_actions packing_action JOIN cosmo_pallets packing_pallet ON packing_pallet.id=packing_action.pallet_id AND packing_pallet.context_id=packing_action.context_id
        JOIN cosmo_states material_state ON material_state.pallet_id=packing_pallet.id AND material_state.context_id=packing_pallet.context_id JOIN stock origin_stock ON origin_stock.id=material_state.stock_id AND origin_stock.context_id=material_state.context_id
        WHERE packing_action.id=h.source_id AND packing_action.kind='return_rack' AND packing_action.context_id=NEW.context_id AND packing_action.payload::jsonb->>'local'=NEW.local
        AND packing_pallet.article=NEW.artigo AND packing_pallet.lot=NEW.lote AND origin_stock.carga=NEW.carga) THEN RAISE EXCEPTION 'Proven packing rack return required' USING ERRCODE='23514';END IF;
    ELSIF h.state='pending_transfer' AND NOT EXISTS"""
        op.execute(definition.replace(anchor,branch))
    else:
        op.execute("INSERT INTO physical_staging_areas(id,warehouse_id,label) VALUES(1,1,'Zona de preparação')")

def downgrade():
    raise RuntimeError('Restaurar backup verificado numa base separada; nunca apagar expedições ou PDFs.')
