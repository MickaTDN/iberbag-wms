"""Empty-box lot identity and automatic floor for NEW units; preserve ddn_0004."""
from alembic import op

revision='ddn_0005'
down_revision='ddn_0004'
branch_labels=None
depends_on=None

def upgrade():
    if op.get_bind().dialect.name!='postgresql':return
    op.execute('''
      CREATE OR REPLACE FUNCTION valmet_content_guard() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE unit_kind text;
      BEGIN
        SELECT kind INTO unit_kind FROM valmet_units WHERE id=NEW.unit_id AND context_id=NEW.context_id;
        IF NEW.identification_id IS NOT NULL AND NOT EXISTS(
          SELECT 1 FROM valmet_identifications WHERE id=NEW.identification_id AND context_id=NEW.context_id AND unit_id=NEW.unit_id)
          THEN RAISE EXCEPTION 'Identification belongs to another unit'; END IF;
        IF NEW.identification_id IS NOT NULL AND
          (NEW.lot IS NULL OR btrim(NEW.lot)='' OR
            (unit_kind<>'vazias' AND (NEW.article IS NULL OR btrim(NEW.article)='')))
          THEN RAISE EXCEPTION 'Incomplete identification'; END IF;
        RETURN NEW;
      END $$;
      CREATE FUNCTION valmet_empty_floor() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NEW.kind='vazias' AND NOT NEW.legacy_unnumbered THEN
          NEW.local='Chão';
          IF NOT EXISTS(SELECT 1 FROM locations WHERE context_id=NEW.context_id AND codigo='Chão' AND active AND capacidade IS NULL)
            THEN RAISE EXCEPTION 'Empty boxes require an active unlimited floor in this context'; END IF;
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER valmet_empty_floor BEFORE INSERT ON valmet_units
        FOR EACH ROW EXECUTE FUNCTION valmet_empty_floor();
      CREATE FUNCTION valmet_empty_lot_required() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE target integer; ctx integer;
      BEGIN
        IF TG_TABLE_NAME='valmet_units' THEN target=NEW.id; ELSE target=NEW.unit_id; END IF;
        ctx=NEW.context_id;
        IF EXISTS(SELECT 1 FROM valmet_units WHERE id=target AND context_id=ctx AND kind='vazias' AND NOT legacy_unnumbered) THEN
          IF NOT EXISTS(SELECT 1 FROM valmet_items WHERE unit_id=target AND context_id=ctx AND lot IS NOT NULL AND btrim(lot)<>'')
            OR EXISTS(SELECT 1 FROM valmet_items WHERE unit_id=target AND context_id=ctx AND (lot IS NULL OR btrim(lot)=''))
            THEN RAISE EXCEPTION 'Empty boxes require their lot'; END IF;
        END IF;
        RETURN NULL;
      END $$;
      CREATE CONSTRAINT TRIGGER valmet_empty_lot_unit AFTER INSERT ON valmet_units
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION valmet_empty_lot_required();
      CREATE CONSTRAINT TRIGGER valmet_empty_lot_item AFTER INSERT ON valmet_items
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION valmet_empty_lot_required();
    ''')

def downgrade():
    raise RuntimeError('Não enfraquecer validações nem reescrever histórico; restaurar backup verificado.')
