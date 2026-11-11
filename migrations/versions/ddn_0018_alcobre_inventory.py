"""Alcobre evidenced inventories/corrections; never rewrite historical books."""
from importlib import import_module
from pathlib import Path
from alembic import op
import sqlalchemy as s
revision='ddn_0018'
down_revision='ddn_0017'
branch_labels=None
depends_on=None

def upgrade():
    h=import_module('migrations.versions.ddn_0008_valmet_shipping');table=h.table;audit=h.audit_columns
    table('alcobre_inventories',[s.Column('request_key',s.String(64),nullable=False),s.Column('title',s.String(120),nullable=False),s.Column('mode',s.String(20),nullable=False),s.Column('scope',s.Text(),nullable=False),s.Column('opening',s.Text(),nullable=False),s.Column('version',s.Integer(),nullable=False),s.Column('status',s.String(20),nullable=False),s.Column('created_by',s.Integer(),s.ForeignKey('users.id'),nullable=False),s.Column('created_at',s.DateTime(),nullable=False)],constraints=[s.UniqueConstraint('context_id','request_key'),s.CheckConstraint("mode IN ('complete','partial') AND status IN ('draft','confirmed') AND version>0",name='alcobre_inventory_status')])
    table('alcobre_inventory_checks',[s.Column('inventory_id',s.Integer(),nullable=False),s.Column('bobine_id',s.Integer()),s.Column('entry_id',s.Integer()),s.Column('number',s.String(120)),s.Column('local',s.String(120)),s.Column('result',s.String(20),nullable=False),s.Column('snapshot',s.Text(),nullable=False),s.Column('evidence',s.Text(),nullable=False),s.Column('resolves_id',s.Integer()),*audit()],refs=[('inventory_id','alcobre_inventories'),('bobine_id','alcobre_bobines'),('entry_id','alcobre_entries'),('resolves_id','alcobre_inventory_checks')],constraints=[s.UniqueConstraint('context_id','resolves_id'),s.CheckConstraint("result IN ('found','missing','elsewhere','unverified','excluded','empty','unknown','resolved')",name='alcobre_inventory_result')])
    table('alcobre_inventory_reviews',[s.Column('inventory_id',s.Integer(),nullable=False),s.Column('request_key',s.String(64),nullable=False),s.Column('version',s.Integer(),nullable=False),s.Column('kind',s.String(20),nullable=False),s.Column('payload',s.Text(),nullable=False),s.Column('digest',s.String(64),nullable=False),s.Column('reason',s.Text(),nullable=False),*audit()],refs=[('inventory_id','alcobre_inventories')],constraints=[s.UniqueConstraint('context_id','request_key'),s.CheckConstraint("kind IN ('conference','approve')",name='alcobre_inventory_review_kind')])
    table('alcobre_inventory_operations',[s.Column('inventory_id',s.Integer(),nullable=False),s.Column('review_id',s.Integer(),nullable=False),s.Column('request_key',s.String(64),nullable=False),s.Column('payload',s.Text(),nullable=False),s.Column('snapshot',s.Text(),nullable=False),*audit()],refs=[('inventory_id','alcobre_inventories'),('review_id','alcobre_inventory_reviews')],constraints=[s.UniqueConstraint('context_id','inventory_id'),s.UniqueConstraint('context_id','review_id'),s.UniqueConstraint('context_id','request_key')])
    table('alcobre_inventory_documents',[s.Column('review_id',s.Integer()),s.Column('operation_id',s.Integer()),s.Column('pdf',s.LargeBinary(),nullable=False),s.Column('digest',s.String(64),nullable=False),*audit()],refs=[('review_id','alcobre_inventory_reviews'),('operation_id','alcobre_inventory_operations')],constraints=[s.UniqueConstraint('context_id','review_id'),s.UniqueConstraint('context_id','operation_id'),s.CheckConstraint('(review_id IS NULL) <> (operation_id IS NULL)',name='alcobre_inventory_document_origin')])
    with op.batch_alter_table('alcobre_dispatch_events') as batch:
        batch.alter_column('operation_id',existing_type=s.Integer(),nullable=True)
        batch.add_column(s.Column('control_operation_id',s.Integer()))
        batch.add_column(s.Column('local',s.String(120)))
        batch.create_foreign_key('fk_ctx_alcobre_dispatch_events_control_operation_id','alcobre_inventory_operations',['context_id','control_operation_id'],['context_id','id'])
        batch.create_unique_constraint('uq_alcobre_control_unit',['context_id','control_operation_id','bobine_id'])
        batch.drop_constraint('alcobre_dispatch_event_state',type_='check')
        batch.create_check_constraint('alcobre_dispatch_event_state',"state IN ('available','reserved','shipped','blocked','missing')")
        batch.create_check_constraint('alcobre_event_origin','(operation_id IS NULL) <> (control_operation_id IS NULL)')
    if op.get_bind().dialect.name!='postgresql':return
    conn=op.get_bind()
    if conn.scalar(s.text('SELECT EXISTS(SELECT 1 FROM alcobre_placements WHERE id<=0) OR EXISTS(SELECT 1 FROM alcobre_dispatch_events WHERE id<=0)')):raise RuntimeError('Nonpositive historical ledger identity; reconcile explicitly before upgrading.')
    schema=conn.dialect.identifier_preparer.quote(conn.scalar(s.text('SELECT current_schema()')))
    conn.exec_driver_sql(Path(__file__).with_suffix('.sql').read_text().replace('__WMS_SCHEMA__',schema))
    for name in ('alcobre_guard','alcobre_location_guard','alcobre_dispatch_plan','alcobre_dispatch_guard'):
        definition=conn.scalar(s.text('SELECT pg_get_functiondef(CAST(:fn AS regprocedure))'),dict(fn=name+'()' if name not in ('alcobre_dispatch_plan',) else name+'(jsonb,integer,integer,text)'))
        # Current locations are a read-only projection of the complete event chain.
        updated=definition.replace('FROM alcobre_placements','FROM alcobre_current_placements()').replace('JOIN alcobre_placements','JOIN alcobre_current_placements()')
        updated=updated.replace("alcobre_current_state(bobine_id) <> 'shipped'", "alcobre_current_state(bobine_id) NOT IN ('shipped','missing')").replace("alcobre_current_state(bobine_id)<>'shipped'", "alcobre_current_state(bobine_id) NOT IN ('shipped','missing')").replace("alcobre_current_state(p.bobine_id)<>'shipped'", "alcobre_current_state(p.bobine_id) NOT IN ('shipped','missing')")
        if name=='alcobre_dispatch_guard':
            marker=' PERFORM pg_advisory_xact_lock(72834001);'
            if marker not in updated:raise RuntimeError('Unexpected dispatch guard; reconcile explicitly.')
            updated=updated.replace(marker,marker+"\n IF TG_TABLE_NAME='alcobre_dispatch_events' THEN\n  IF NEW.control_operation_id IS NOT NULL THEN RETURN NEW; END IF;\n  IF NEW.local IS NOT NULL THEN RAISE EXCEPTION 'Dispatch cannot change location'; END IF;\n END IF;",1)
        if name=='alcobre_guard':
            marker=" IF TG_TABLE_NAME='alcobre_placements' THEN"
            updated=updated.replace(marker,marker+"\n  IF NEW.id<=0 THEN RAISE EXCEPTION 'Positive placement identity required'; END IF;\n  IF alcobre_current_state(NEW.bobine_id)<>'available' OR EXISTS(SELECT 1 FROM alcobre_dispatch_events WHERE bobine_id=NEW.bobine_id AND control_operation_id IS NOT NULL) OR alcobre_inventory_blocked(NEW.local,NEW.context_id) THEN RAISE EXCEPTION 'Identity already corrected, blocked or location under investigation'; END IF;",1)
        if name=='alcobre_dispatch_plan':
            updated=updated.replace("WHEN u.placement_id IS NULL THEN 'unlocated' ELSE st END","WHEN st IN ('shipped','missing','blocked') THEN st WHEN u.placement_id IS NULL THEN 'unlocated' ELSE st END")
            marker="  result=result||jsonb_build_array"
            updated=updated.replace(marker,"  IF u.local IS NOT NULL AND why IN ('available','reserved_self') AND alcobre_inventory_blocked(u.local,ctx) THEN why='inventory_incident'; END IF;\n"+marker)
        if updated==definition:raise RuntimeError('Unexpected Alcobre function; reconcile explicitly.')
        conn.exec_driver_sql(updated)

def downgrade():raise RuntimeError('Restore a verified backup; never erase inventories, corrections or history.')
