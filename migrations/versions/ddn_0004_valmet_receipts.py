"""Valmet physical units and append-only identification, no legacy stock rewrite."""
from alembic import op
import sqlalchemy as s

revision='ddn_0004'
down_revision='ddn_0003'
branch_labels=None
depends_on=None

def scoped(name, columns, refs=(), uniques=(), checks=()):
    op.create_table(name,s.Column('id',s.Integer(),primary_key=True),
        s.Column('context_id',s.Integer(),s.ForeignKey('storage_contexts.id'),nullable=False,server_default='1'),
        *columns,s.UniqueConstraint('context_id','id',name='uq_ctx_'+name),
        *(s.ForeignKeyConstraint(['context_id',field],[table+'.context_id',table+'.id'],name='fk_ctx_'+name+'_'+field) for field,table in refs),
        *(s.UniqueConstraint('context_id',field) for field in uniques),
        *(s.CheckConstraint(expr,name=key) for key,expr in checks))
    op.create_index('ix_'+name+'_context_id',name,['context_id'])

def upgrade():
    op.create_table('valmet_owners',s.Column('client_id',s.Integer(),s.ForeignKey('clients.id',deferrable=True,initially='DEFERRED'),primary_key=True))
    op.execute("INSERT INTO valmet_owners(client_id) SELECT id FROM clients WHERE name='Valmet'")
    scoped('valmet_entries',[
        s.Column('request_key',s.String(64),nullable=False),s.Column('payload',s.Text(),nullable=False),
        s.Column('version',s.Integer(),nullable=False),s.Column('status',s.String(20),nullable=False),
        s.Column('created_by',s.Integer(),s.ForeignKey('users.id'),nullable=False),s.Column('created_at',s.DateTime(),nullable=False),
        s.Column('receipt_id',s.Integer())],refs=[('receipt_id','receipts')],uniques=['request_key','receipt_id'],
        checks=[('valmet_entry_state',"status IN ('draft','confirmed') AND version>0 AND ((status='draft' AND receipt_id IS NULL) OR (status='confirmed' AND receipt_id IS NOT NULL))")])
    scoped('valmet_units',[
        s.Column('entry_id',s.Integer(),nullable=False),s.Column('kind',s.String(30),nullable=False),s.Column('number',s.String(120)),
        s.Column('legacy_unnumbered',s.Boolean(),nullable=False,server_default=s.false()),
        s.Column('physical_quantity',s.Integer(),nullable=False),s.Column('local',s.String(120)),s.Column('material_m2',s.Numeric(18,3)),
        s.Column('content_total',s.Integer()),s.Column('supplier',s.String(120),nullable=False),s.Column('guide',s.String(120),nullable=False),
        s.Column('notes',s.Text(),nullable=False),s.Column('brand',s.String(40))],refs=[('entry_id','valmet_entries')],
        checks=[('valmet_unit_quantities','physical_quantity>0 AND (content_total IS NULL OR content_total>0) AND (material_m2 IS NULL OR material_m2>=0)')])
    op.create_table('valmet_numbers',s.Column('client_id',s.Integer(),s.ForeignKey('clients.id'),primary_key=True),
        s.Column('number',s.String(120),primary_key=True),s.Column('unit_id',s.Integer(),s.ForeignKey('valmet_units.id'),unique=True,nullable=False))
    scoped('valmet_identifications',[
        s.Column('unit_id',s.Integer(),nullable=False),s.Column('request_key',s.String(64),nullable=False),
        s.Column('actor',s.String(60),nullable=False),s.Column('created_at',s.DateTime(),nullable=False),
        s.Column('content_total',s.Integer()),s.Column('notes',s.Text(),nullable=False)],
        refs=[('unit_id','valmet_units')],uniques=['unit_id','request_key'])
    scoped('valmet_items',[
        s.Column('unit_id',s.Integer(),nullable=False),s.Column('identification_id',s.Integer()),s.Column('article',s.String(120)),
        s.Column('lot',s.String(120)),s.Column('quantity',s.Integer())],
        refs=[('identification_id','valmet_identifications'),('unit_id','valmet_units')],checks=[('valmet_item_quantity','quantity IS NULL OR quantity>0')])
    if op.get_bind().dialect.name!='postgresql':return
    op.execute('''CREATE FUNCTION valmet_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Valmet history is append-only' USING ERRCODE='23514'; END $$''')
    for table in ('valmet_owners','valmet_units','valmet_numbers','valmet_identifications','valmet_items'):
        op.execute(f'CREATE TRIGGER valmet_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable()')
    op.execute('CREATE TRIGGER valmet_confirmed_entry BEFORE UPDATE OR DELETE ON valmet_entries FOR EACH ROW EXECUTE FUNCTION wms_immutable_confirmation()')
    op.execute('''CREATE FUNCTION valmet_check_unit() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE cap integer; used bigint; wh integer; owner integer;
      BEGIN
        PERFORM pg_advisory_xact_lock(72834001);
        IF NOT EXISTS(SELECT 1 FROM valmet_entries e JOIN storage_contexts c ON c.id=e.context_id
             JOIN valmet_owners v ON v.client_id=c.client_id WHERE e.id=NEW.entry_id AND e.context_id=NEW.context_id AND e.status='draft')
          THEN RAISE EXCEPTION 'Invalid Valmet entry/context'; END IF;
        IF NEW.kind NOT IN ('paletes','fio','prateleiras','rolos','caixas','vazias','pecas','vasilhame')
          OR (NEW.kind IN ('rolos','caixas','vazias','pecas') AND (NEW.physical_quantity<>1 OR (NEW.number IS NULL AND NOT NEW.legacy_unnumbered)))
          OR (NEW.legacy_unnumbered AND (NEW.number IS NOT NULL OR NEW.kind NOT IN ('rolos','caixas','vazias','pecas')))
          OR (NEW.number IS NOT NULL AND (NEW.number<>upper(btrim(NEW.number)) OR NEW.number=''))
          THEN RAISE EXCEPTION 'Invalid physical unit'; END IF;
        IF NEW.local IS NOT NULL THEN
          IF NOT EXISTS(SELECT 1 FROM locations WHERE context_id=NEW.context_id AND codigo=NEW.local AND active)
            THEN RAISE EXCEPTION 'Unknown or inactive location'; END IF;
          SELECT capacidade INTO cap FROM locations WHERE context_id=NEW.context_id AND codigo=NEW.local;
          SELECT COALESCE(sum(physical_quantity),0) INTO used FROM valmet_units WHERE context_id=NEW.context_id AND local=NEW.local;
          used=used+(SELECT COALESCE(sum(quantidade),0) FROM stock WHERE context_id=NEW.context_id AND local=NEW.local);
          IF cap IS NOT NULL AND used+NEW.physical_quantity>cap THEN RAISE EXCEPTION 'Location capacity exceeded'; END IF;
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER valmet_unit_guard BEFORE INSERT ON valmet_units FOR EACH ROW EXECUTE FUNCTION valmet_check_unit();
      CREATE FUNCTION valmet_register_number() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NEW.number IS NOT NULL THEN
          INSERT INTO valmet_numbers(client_id,number,unit_id)
            SELECT client_id,NEW.number,NEW.id FROM storage_contexts WHERE id=NEW.context_id;
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER valmet_unit_number AFTER INSERT ON valmet_units FOR EACH ROW EXECUTE FUNCTION valmet_register_number();
      CREATE FUNCTION valmet_content_guard() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NEW.identification_id IS NOT NULL AND NOT EXISTS(
          SELECT 1 FROM valmet_identifications WHERE id=NEW.identification_id AND context_id=NEW.context_id AND unit_id=NEW.unit_id)
          THEN RAISE EXCEPTION 'Identification belongs to another unit'; END IF;
        IF NEW.identification_id IS NOT NULL AND (NEW.article IS NULL OR btrim(NEW.article)='' OR NEW.lot IS NULL OR btrim(NEW.lot)='')
          THEN RAISE EXCEPTION 'Incomplete identification'; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER valmet_content_unit BEFORE INSERT ON valmet_items FOR EACH ROW EXECUTE FUNCTION valmet_content_guard();
      CREATE FUNCTION valmet_location_guard() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE used bigint;
      BEGIN
        PERFORM pg_advisory_xact_lock(72834001);
        SELECT COALESCE(sum(physical_quantity),0) INTO used FROM valmet_units WHERE context_id=OLD.context_id AND local=OLD.codigo;
        IF used>0 THEN
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Location has Valmet stock'; END IF;
          IF NEW.codigo<>OLD.codigo OR NEW.context_id<>OLD.context_id OR NOT NEW.active
            OR (NEW.capacidade IS NOT NULL AND NEW.capacidade<used+(SELECT COALESCE(sum(quantidade),0) FROM stock WHERE context_id=OLD.context_id AND local=OLD.codigo))
            THEN RAISE EXCEPTION 'Location has Valmet stock'; END IF;
        END IF;
        IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW;
      END $$;
      CREATE TRIGGER valmet_occupied_location BEFORE UPDATE OR DELETE ON locations FOR EACH ROW EXECUTE FUNCTION valmet_location_guard();
      CREATE FUNCTION valmet_combined_stock_guard() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE used bigint; cap integer;
      BEGIN
        PERFORM pg_advisory_xact_lock(72834001);
        SELECT COALESCE(sum(physical_quantity),0) INTO used FROM valmet_units WHERE context_id=NEW.context_id AND local=NEW.local;
        IF used>0 THEN
          SELECT capacidade INTO cap FROM locations WHERE context_id=NEW.context_id AND codigo=NEW.local;
          IF cap IS NOT NULL AND used+NEW.quantidade+(SELECT COALESCE(sum(quantidade),0) FROM stock WHERE context_id=NEW.context_id AND local=NEW.local AND id<>NEW.id)>cap
            THEN RAISE EXCEPTION 'Combined capacity exceeded'; END IF;
        END IF;RETURN NEW;
      END $$;
      CREATE TRIGGER valmet_combined_stock BEFORE INSERT OR UPDATE ON stock FOR EACH ROW EXECUTE FUNCTION valmet_combined_stock_guard();
    ''')

def downgrade():
    raise RuntimeError('Restaurar backup verificado; não apagar entradas ou identificações Valmet.')
