"""Valmet reservation revisions; no debit or existing data rewrite."""
from alembic import op
import sqlalchemy as s

revision='ddn_0007'
down_revision='ddn_0006'
branch_labels=None
depends_on=None

def table(name,cols,refs=(),unique=(),checks=()):
    op.create_table(name,s.Column('id',s.Integer(),primary_key=True),
        s.Column('context_id',s.Integer(),s.ForeignKey('storage_contexts.id'),nullable=False,server_default='1'),
        *cols,s.UniqueConstraint('context_id','id',name='uq_ctx_'+name),
        *(s.ForeignKeyConstraint(['context_id',f],[t+'.context_id',t+'.id'],name='fk_ctx_'+name+'_'+f) for f,t in refs),
        *(s.UniqueConstraint('context_id',f) for f in unique),*(s.CheckConstraint(c,name=k) for k,c in checks))
    op.create_index('ix_'+name+'_context_id',name,['context_id'])

def upgrade():
    table('valmet_preparations',[
        s.Column('request_key',s.String(64),nullable=False),s.Column('payload',s.Text(),nullable=False),s.Column('review',s.Text()),
        s.Column('version',s.Integer(),nullable=False),s.Column('status',s.String(20),nullable=False),s.Column('active_revision',s.Integer()),
        s.Column('created_by',s.Integer(),s.ForeignKey('users.id'),nullable=False),s.Column('created_at',s.DateTime(),nullable=False)],
        unique=['request_key'],checks=[('valmet_preparation_status',"status IN ('draft','reserved','cancelled') AND version>0")])
    table('valmet_plan_revisions',[s.Column('preparation_id',s.Integer(),nullable=False),s.Column('version',s.Integer(),nullable=False),
        s.Column('action',s.String(20),nullable=False),s.Column('snapshot',s.Text(),nullable=False),s.Column('actor',s.String(60),nullable=False),
        s.Column('created_at',s.DateTime(),nullable=False)],refs=[('preparation_id','valmet_preparations')])
    table('valmet_reservations',[s.Column('revision_id',s.Integer(),nullable=False),s.Column('unit_id',s.Integer(),nullable=False),
        s.Column('physical',s.Integer(),nullable=False),s.Column('materials',s.Text(),nullable=False),s.Column('exclusive',s.Boolean(),nullable=False),
        s.UniqueConstraint('context_id','revision_id','unit_id',name='uq_valmet_reservation_unit')],
        refs=[('revision_id','valmet_plan_revisions'),('unit_id','valmet_units')],checks=[('valmet_reservation_positive','physical>0')])
    table('valmet_handling',[s.Column('unit_id',s.Integer(),nullable=False),s.Column('separation',s.String(12),nullable=False),
        s.Column('reconciled',s.Boolean(),nullable=False),s.Column('unlocated',s.Boolean(),nullable=False),s.Column('note',s.Text(),nullable=False),
        s.Column('actor',s.String(60),nullable=False),s.Column('created_at',s.DateTime(),nullable=False)],
        refs=[('unit_id','valmet_units')],checks=[('valmet_separation',"separation IN ('yes','no','unknown')")])
    for name,table_name,columns in (
        ('ix_valmet_preparations_active','valmet_preparations',['context_id','active_revision']),
        ('ix_valmet_revisions_preparation','valmet_plan_revisions',['context_id','preparation_id','id']),
        ('ix_valmet_reservations_unit','valmet_reservations',['context_id','unit_id']),
        ('ix_valmet_handling_unit','valmet_handling',['context_id','unit_id','id'])):
        op.create_index(name,table_name,columns)
    if op.get_bind().dialect.name!='postgresql':return
    for name in ('valmet_plan_revisions','valmet_reservations','valmet_handling'):
        op.execute(f'CREATE TRIGGER valmet_history BEFORE UPDATE OR DELETE OR TRUNCATE ON {name} FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable()')
    op.execute('''CREATE FUNCTION valmet_active_revision_guard() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        PERFORM pg_advisory_xact_lock(72834001);
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Preparation history cannot be deleted'; END IF;
        IF TG_OP='UPDATE' AND (OLD.status='cancelled' OR NEW.context_id<>OLD.context_id OR
          NEW.created_by<>OLD.created_by OR NEW.request_key<>OLD.request_key OR NEW.version<OLD.version)
          THEN RAISE EXCEPTION 'Invalid preparation history change'; END IF;
        IF NEW.active_revision IS NOT NULL AND NOT EXISTS(SELECT 1 FROM valmet_plan_revisions WHERE
          id=NEW.active_revision AND context_id=NEW.context_id AND preparation_id=NEW.id AND action='reserve' AND version<=NEW.version)
          THEN RAISE EXCEPTION 'Invalid active reservation revision'; END IF;
        IF (NEW.status='reserved') IS DISTINCT FROM (NEW.active_revision IS NOT NULL) THEN RAISE EXCEPTION 'Invalid reservation state'; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER valmet_active_revision BEFORE INSERT OR UPDATE OR DELETE ON valmet_preparations FOR EACH ROW EXECUTE FUNCTION valmet_active_revision_guard();
    ''')
    op.execute('''CREATE FUNCTION valmet_hold_guard() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE snap jsonb;
      BEGIN
        PERFORM pg_advisory_xact_lock(72834001);
        SELECT snapshot::jsonb INTO snap FROM valmet_plan_revisions r JOIN valmet_preparations p
          ON p.id=r.preparation_id AND p.context_id=r.context_id
          WHERE r.id=NEW.revision_id AND r.context_id=NEW.context_id AND r.action='reserve'
            AND r.version=p.version AND p.status<>'cancelled';
        IF snap IS NULL OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(snap->'selections') s WHERE
          (s->'unit'->>'id')::integer=NEW.unit_id AND (s->>'physical')::integer=NEW.physical
          AND (s->>'exclusive')::boolean=NEW.exclusive AND s->'materials'=NEW.materials::jsonb)
          OR EXISTS(SELECT 1 FROM valmet_reservations WHERE revision_id=NEW.revision_id AND unit_id=NEW.unit_id)
          THEN RAISE EXCEPTION 'Reservation does not match its immutable review'; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER valmet_hold_insert BEFORE INSERT ON valmet_reservations FOR EACH ROW EXECUTE FUNCTION valmet_hold_guard();
      CREATE FUNCTION valmet_reservation_totals() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE u record; n integer; exclusive_count integer; phys bigint; mat record; cap bigint; rule record; whole boolean;
      BEGIN
        PERFORM pg_advisory_xact_lock(72834001);
        FOR u IN SELECT DISTINCT v.* FROM valmet_units v JOIN valmet_reservations h ON h.unit_id=v.id AND h.context_id=v.context_id
          JOIN valmet_plan_revisions r ON r.id=h.revision_id AND r.context_id=h.context_id
          JOIN valmet_preparations p ON p.active_revision=r.id AND p.context_id=r.context_id WHERE p.status='reserved' AND p.context_id=NEW.context_id
        LOOP
          SELECT * INTO rule FROM valmet_handling WHERE unit_id=u.id AND context_id=u.context_id ORDER BY id DESC LIMIT 1;
          IF NOT EXISTS(SELECT 1 FROM valmet_identifications WHERE unit_id=u.id AND context_id=u.context_id)
            OR (u.legacy_unnumbered AND NOT (u.kind='vazias' AND COALESCE(rule.reconciled,false)))
            OR (u.local IS NULL AND NOT (u.kind='vasilhame' AND COALESCE(rule.unlocated,false)))
            OR (u.local IS NOT NULL AND NOT EXISTS(SELECT 1 FROM locations WHERE context_id=u.context_id AND codigo=u.local AND active))
            THEN RAISE EXCEPTION 'Unavailable physical unit'; END IF;
          SELECT count(*),count(*) FILTER(WHERE h.exclusive),sum(h.physical) INTO n,exclusive_count,phys
          FROM valmet_reservations h JOIN valmet_plan_revisions r ON r.id=h.revision_id AND r.context_id=h.context_id
            JOIN valmet_preparations p ON p.active_revision=r.id AND p.context_id=r.context_id
            WHERE p.status='reserved' AND h.unit_id=u.id AND h.context_id=u.context_id;
          IF (exclusive_count>0 AND n>1) OR (u.kind NOT IN ('rolos','lab') AND phys>u.physical_quantity)
            THEN RAISE EXCEPTION 'Physical unit already reserved'; END IF;
          whole=(u.kind IN ('caixas','pecas') OR (u.kind IN ('paletes','vazias') AND u.physical_quantity=1)
            OR (u.kind='rolos' AND COALESCE(rule.separation,'unknown')<>'yes'));
          IF whole AND (exclusive_count<>1 OR phys<>u.physical_quantity)
            THEN RAISE EXCEPTION 'Indivisible unit must be reserved whole'; END IF;
          IF EXISTS(SELECT 1 FROM valmet_reservations h JOIN valmet_plan_revisions r ON r.id=h.revision_id AND r.context_id=h.context_id
              JOIN valmet_preparations p ON p.active_revision=r.id AND p.context_id=r.context_id
              WHERE p.status='reserved' AND h.unit_id=u.id AND h.context_id=u.context_id AND h.physical>u.physical_quantity)
            THEN RAISE EXCEPTION 'Invalid physical quantity'; END IF;
          IF whole AND EXISTS(SELECT 1 FROM valmet_items i WHERE i.unit_id=u.id AND i.context_id=u.context_id
            AND i.identification_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM valmet_reservations h
              JOIN valmet_plan_revisions r ON r.id=h.revision_id AND r.context_id=h.context_id
              JOIN valmet_preparations p ON p.active_revision=r.id AND p.context_id=r.context_id
              WHERE p.status='reserved' AND h.unit_id=u.id AND h.context_id=u.context_id AND h.materials::jsonb ? i.lot))
            THEN RAISE EXCEPTION 'Incomplete indivisible composition'; END IF;
          FOR mat IN SELECT x.key,sum(CASE WHEN x.value ~ '^[1-9][0-9]{0,9}$' THEN x.value::bigint ELSE -2147483648 END) AS qty,
              bool_and(x.value ~ '^[1-9][0-9]{0,9}$') AS valid
            FROM valmet_reservations h JOIN valmet_plan_revisions r ON r.id=h.revision_id AND r.context_id=h.context_id
              JOIN valmet_preparations p ON p.active_revision=r.id AND p.context_id=r.context_id,
              LATERAL jsonb_each_text(h.materials::jsonb) x
            WHERE p.status='reserved' AND h.unit_id=u.id AND h.context_id=u.context_id GROUP BY x.key
          LOOP
            IF NOT mat.valid THEN RAISE EXCEPTION 'Invalid material quantity'; END IF;
            IF u.kind='vasilhame' AND mat.key=u.brand THEN cap=u.physical_quantity;
            ELSIF u.kind IN ('caixas','vazias') OR (u.kind='paletes' AND u.physical_quantity=1) THEN
              SELECT CASE WHEN count(*)>0 THEN u.physical_quantity END INTO cap FROM valmet_items i
                WHERE i.unit_id=u.id AND i.context_id=u.context_id AND i.identification_id IS NOT NULL AND i.lot=mat.key;
            ELSE
              SELECT CASE WHEN count(*)>0 AND count(quantity)=count(*) THEN sum(quantity) END INTO cap FROM valmet_items i
                WHERE i.unit_id=u.id AND i.context_id=u.context_id AND i.identification_id IS NOT NULL AND i.lot=mat.key;
            END IF;
            IF (whole AND mat.qty<>cap) OR (cap IS NULL AND NOT (u.kind='lab' AND exclusive_count=1 AND n=1 AND EXISTS(
              SELECT 1 FROM valmet_items WHERE unit_id=u.id AND context_id=u.context_id AND lot=mat.key AND identification_id IS NOT NULL))) OR mat.qty>cap
              THEN RAISE EXCEPTION 'Material already reserved or unidentified'; END IF;
          END LOOP;
        END LOOP;
        RETURN NULL;
      END $$;
      CREATE CONSTRAINT TRIGGER valmet_reservation_capacity AFTER INSERT OR UPDATE ON valmet_preparations
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION valmet_reservation_totals();
      CREATE CONSTRAINT TRIGGER valmet_hold_capacity AFTER INSERT ON valmet_reservations
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION valmet_reservation_totals();
      CREATE FUNCTION valmet_handling_guard() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE kind text; legacy boolean;
      BEGIN
        PERFORM pg_advisory_xact_lock(72834001);
        SELECT u.kind,u.legacy_unnumbered INTO kind,legacy FROM valmet_units u WHERE id=NEW.unit_id AND context_id=NEW.context_id;
        IF (NEW.separation<>'unknown' AND kind<>'rolos') OR (NEW.reconciled AND NOT (kind='vazias' AND legacy))
          OR (NEW.reconciled AND (SELECT count(DISTINCT lot) FROM valmet_items WHERE unit_id=NEW.unit_id
            AND context_id=NEW.context_id AND identification_id IS NOT NULL)<>1)
          OR (NEW.unlocated AND kind<>'vasilhame') OR btrim(NEW.note)=''
          OR EXISTS(SELECT 1 FROM valmet_reservations h JOIN valmet_plan_revisions r ON r.id=h.revision_id AND r.context_id=h.context_id
            JOIN valmet_preparations p ON p.active_revision=r.id AND p.context_id=r.context_id
            WHERE p.status='reserved' AND h.context_id=NEW.context_id AND h.unit_id=NEW.unit_id)
          THEN RAISE EXCEPTION 'Invalid or reserved handling rule'; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER valmet_handling_insert BEFORE INSERT ON valmet_handling FOR EACH ROW EXECUTE FUNCTION valmet_handling_guard();
    ''')
    # Extend only the fixed category list and explicit historical empty groups.
    # Existing published revision files and data are never changed.
    conn=op.get_bind()
    definition=conn.scalar(s.text("SELECT pg_get_functiondef('valmet_check_unit()'::regprocedure)"))
    old="'pecas','vasilhame')"
    if old not in definition:raise RuntimeError('Unexpected Valmet guard; reconcile before upgrade.')
    definition=definition.replace(old,"'pecas','vasilhame','lab')")
    old="NEW.kind IN ('rolos','caixas','vazias','pecas') AND (NEW.physical_quantity<>1"
    if definition.count(old)!=1:raise RuntimeError('Unexpected physical guard; reconcile before upgrade.')
    definition=definition.replace(old, "NEW.kind IN ('rolos','caixas','vazias','pecas','lab') AND ((NEW.physical_quantity<>1 AND NOT (NEW.kind='vazias' AND NEW.legacy_unnumbered))")
    conn.exec_driver_sql(definition)

def downgrade():raise RuntimeError('Restore verified backup; never delete reservation history.')
