"""Article may be unknown; operational lot identity remains mandatory."""
from alembic import op

revision='ddn_0006'
down_revision='ddn_0005'
branch_labels=None
depends_on=None

def upgrade():
    if op.get_bind().dialect.name!='postgresql':return
    op.execute('''CREATE OR REPLACE FUNCTION valmet_content_guard() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NEW.identification_id IS NOT NULL AND NOT EXISTS(
          SELECT 1 FROM valmet_identifications WHERE id=NEW.identification_id AND context_id=NEW.context_id AND unit_id=NEW.unit_id)
          THEN RAISE EXCEPTION 'Identification belongs to another unit'; END IF;
        IF NEW.identification_id IS NOT NULL AND (NEW.lot IS NULL OR btrim(NEW.lot)='')
          THEN RAISE EXCEPTION 'Incomplete operational lot identification'; END IF;
        RETURN NEW;
      END $$''')

def downgrade():
    raise RuntimeError('Não inventar artigos nem alterar históricos; restaurar backup verificado.')
