"""Cosmopak planned individual pallets, using the common physical rack ledger."""
from importlib import import_module
from pathlib import Path
from alembic import op
import sqlalchemy as s
revision='ddn_0021'
down_revision='ddn_0020'
branch_labels=None
depends_on=None

def upgrade():
    h=import_module('migrations.versions.ddn_0008_valmet_shipping')
    def c(name,typ,nullable=False):return s.Column(name,typ,nullable=nullable)
    def actor():return c('actor_id',s.Integer())
    def created():return c('created_at',s.DateTime())
    def key():return c('request_key',s.String(64))
    def user(field):return s.ForeignKeyConstraint([field],['users.id'])
    h.table('cosmo_receptions',[key(),c('source_name',s.String(240)),c('source_sha256',s.String(64)),c('source_excel',s.LargeBinary()),c('report',s.Text()),c('created_by',s.Integer()),created(),c('version',s.Integer()),c('status',s.String(20)),c('guide',s.String(120),True),c('arrived_on',s.DateTime(),True),c('legacy_code',s.String(120),True),c('unloaded_by',s.Integer(),True),c('unloaded_at',s.DateTime(),True),c('receipt_id',s.Integer(),True)],refs=[('receipt_id','receipts')],constraints=[user('created_by'),user('unloaded_by'),s.UniqueConstraint('context_id','request_key'),s.UniqueConstraint('context_id','receipt_id'),s.CheckConstraint("status IN ('planned','unloaded','reviewed','approved') AND version>0",name='cosmo_reception_valid')])
    h.table('cosmo_pallets',[c('reception_id',s.Integer()),c('source_row',s.Integer()),c('ordinal',s.Integer()),c('article',s.String(120)),c('description',s.String(240)),c('lot',s.String(120)),c('boxes',s.Integer()),c('incomplete',s.Boolean())],refs=[('reception_id','cosmo_receptions')],constraints=[s.UniqueConstraint('context_id','reception_id','source_row','ordinal'),s.CheckConstraint('source_row>0 AND ordinal>0 AND boxes>=0',name='cosmo_pallet_valid')])
    h.table('cosmo_states',[c('pallet_id',s.Integer()),c('status',s.String(20)),c('local',s.String(120),True),c('actual_boxes',s.Integer(),True),c('decision',s.String(20)),c('stock_id',s.Integer(),True),c('version',s.Integer())],refs=[('pallet_id','cosmo_pallets'),('stock_id','stock')],constraints=[s.UniqueConstraint('context_id','pallet_id'),s.UniqueConstraint('context_id','stock_id'),s.CheckConstraint("status IN ('planned','placed','missing','blocked','approved') AND decision IN ('unresolved','accept','exclude') AND version>=0 AND (actual_boxes IS NULL OR actual_boxes>=0)",name='cosmo_state_valid')])
    h.table('cosmo_operations',[key(),c('pallet_id',s.Integer()),c('kind',s.String(20)),c('expected_version',s.Integer()),c('local',s.String(120),True),c('actual_boxes',s.Integer(),True),c('decision',s.String(20),True),c('vacated',s.Boolean()),c('reason',s.Text()),actor(),c('stock_id',s.Integer(),True),c('receipt_id',s.Integer(),True),c('before_state',s.Text()),c('after_state',s.Text()),created()],refs=[('pallet_id','cosmo_pallets'),('stock_id','stock'),('receipt_id','receipts')],constraints=[user('actor_id'),s.UniqueConstraint('context_id','request_key'),s.CheckConstraint("kind IN ('place','missing','decide','approve')",name='cosmo_operation_valid')])
    h.table('cosmo_reviews',[c('reception_id',s.Integer()),key(),c('kind',s.String(20)),c('expected_version',s.Integer()),c('payload',s.Text()),c('reason',s.Text()),actor(),created()],refs=[('reception_id','cosmo_receptions')],constraints=[user('actor_id'),s.UniqueConstraint('context_id','request_key'),s.CheckConstraint("kind IN ('metadata','unloaded','review','approve')",name='cosmo_review_valid')])
    for table,col in [('cosmo_pallets','reception_id'),('cosmo_operations','pallet_id'),('cosmo_reviews','reception_id')]:op.create_index('ix_'+table+'_'+col,table,[col])
    if op.get_bind().dialect.name!='postgresql':return
    op.execute(Path(__file__).with_suffix('.sql').read_text(encoding='utf-8'))
    # Extend the published engine in place, retaining its table-origin guards,
    # ACLs and all old branches. Assert anchors rather than silently patch drift.
    con=op.get_bind()
    event=con.scalar(s.text("SELECT pg_get_functiondef('ddn_shared_event()'::regprocedure)"))
    branch=Path(__file__).with_name('ddn_0021_common_branch.sql').read_text(encoding='utf-8')
    anchor="  ELSIF NEW.kind IN('putaway_hold','putaway_commit','putaway_cancel') THEN"
    if event.count(anchor)!=1:raise RuntimeError('Motor físico inesperado; revisão manual necessária.')
    op.execute(event.replace(anchor,branch+'\n'+anchor))
    guard=con.scalar(s.text("SELECT pg_get_functiondef('ddn_shared_stock_guard()'::regprocedure)"))
    anchor="    IF h.state='pending_putaway' AND NOT EXISTS"
    if guard.count(anchor)!=1:raise RuntimeError('Guarda física inesperada; revisão manual necessária.')
    new="""    IF h.state='pending_putaway' AND h.source_kind='cosmopak' THEN
      IF NOT EXISTS(SELECT 1 FROM cosmo_pallets a JOIN cosmo_states b ON b.pallet_id=a.id AND b.context_id=a.context_id
        JOIN cosmo_receptions r ON r.id=a.reception_id AND r.context_id=a.context_id
        WHERE a.id=h.source_id AND a.context_id=NEW.context_id AND a.article=NEW.artigo AND a.lot=NEW.lote
        AND b.local=NEW.local AND b.status='placed' AND b.decision='accept' AND r.status='reviewed') THEN
        RAISE EXCEPTION 'Cosmopak identified physical receipt required' USING ERRCODE='23514';END IF;
    ELSIF h.state='pending_putaway' AND NOT EXISTS"""
    op.execute(guard.replace(anchor,new))
    schema=con.dialect.identifier_preparer.quote(con.scalar(s.text('SELECT current_schema()')))
    for name in ('ddn_cosmo_guard','ddn_cosmo_initial','ddn_cosmo_operation','ddn_cosmo_review','ddn_cosmo_complete','ddn_cosmo_stock','ddn_cosmo_projection','ddn_cosmo_physical'):
        op.execute(f'ALTER FUNCTION {name}() SET search_path TO pg_catalog, {schema}, pg_temp')
        op.execute(f'REVOKE ALL ON FUNCTION {name}() FROM PUBLIC')

def downgrade():raise RuntimeError('Restaurar backup verificado numa base separada; não apagar paletes/históricos.')
