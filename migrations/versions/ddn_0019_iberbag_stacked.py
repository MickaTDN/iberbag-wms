"""Iberbag stacked pairs remain guide units, never two stock units."""
from alembic import op
import sqlalchemy as sa

revision = 'ddn_0019'
down_revision = 'ddn_0018'
branch_labels = None
depends_on = None

TYPED = ('stock','putaways','dispatch_lines','stock_transfers','inventory_counts','stock_adjustments')


def upgrade():
    for table in TYPED:
        op.add_column(table, sa.Column('stacked', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('dispatch_orders', sa.Column('typed_selection', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('putaways', sa.Column('document_pdf', sa.LargeBinary(), nullable=True))
    op.add_column('putaways', sa.Column('document_sha256', sa.String(64), nullable=True))
    for table, name, columns in (
        ('stock','stock_key_unique',['context_id','artigo','lote','carga','local','stacked']),
        ('dispatch_lines','dispatch_line_unique',['context_id','order_id','artigo','lote','destination','stacked'])):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(name, type_='unique')
            batch.create_unique_constraint(name, columns)
    if op.get_bind().dialect.name != 'postgresql': return
    op.execute('''CREATE FUNCTION ddn_stacked_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE expected boolean; source_type boolean; target_type boolean; code text;
    BEGIN
      -- Serialize with operational catalogue, reservations, inventory and FIFO checks.
      PERFORM pg_advisory_xact_lock(72834001);
      IF TG_TABLE_NAME='stock' AND TG_OP='UPDATE' THEN
        IF NEW.stacked IS DISTINCT FROM OLD.stacked THEN
          RAISE EXCEPTION 'Stock type is immutable' USING ERRCODE='23514';
        END IF;
      END IF;
      IF TG_TABLE_NAME='dispatch_picks' THEN
        SELECT stacked INTO expected FROM dispatch_lines WHERE id=NEW.line_id AND context_id=NEW.context_id;
        SELECT stacked INTO source_type FROM stock WHERE id=NEW.stock_id AND context_id=NEW.context_id;
        IF expected IS DISTINCT FROM source_type THEN
          RAISE EXCEPTION 'FIFO stock type mismatch' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END IF;
      IF NEW.stacked AND NOT EXISTS(SELECT 1 FROM storage_contexts WHERE id=NEW.context_id AND client_id=1 AND warehouse_id=1) THEN
        RAISE EXCEPTION 'Stacked pairs only in Iberbag Ovar' USING ERRCODE='23514';
      END IF;
      IF TG_TABLE_NAME='dispatch_lines' THEN
        IF NEW.stacked AND NOT EXISTS(SELECT 1 FROM dispatch_orders WHERE id=NEW.order_id AND context_id=NEW.context_id AND typed_selection) THEN
          RAISE EXCEPTION 'Stacked lines require strict typed selection' USING ERRCODE='23514';
        END IF;
        IF EXISTS(SELECT 1 FROM dispatch_picks p JOIN stock s ON s.id=p.stock_id AND s.context_id=p.context_id
          WHERE p.line_id=NEW.id AND p.context_id=NEW.context_id AND s.stacked IS DISTINCT FROM NEW.stacked) THEN
          RAISE EXCEPTION 'Cannot relabel a typed pick plan' USING ERRCODE='23514';
        END IF;
      END IF;
      IF TG_TABLE_NAME IN ('stock','putaways','stock_adjustments') THEN code:=NEW.local;
      ELSIF TG_TABLE_NAME='inventory_counts' THEN
        SELECT local INTO code FROM inventory_scopes WHERE id=NEW.scope_id AND context_id=NEW.context_id;
      ELSIF TG_TABLE_NAME='stock_transfers' THEN code:=NEW.destination_local;
      END IF;
      IF NEW.stacked AND code IS NOT NULL AND lower(btrim(code)) NOT IN ('chão','chao') THEN
        RAISE EXCEPTION 'Stacked pairs require floor' USING ERRCODE='23514';
      END IF;
      IF TG_TABLE_NAME IN ('putaways','stock_adjustments') THEN
       IF NEW.stock_id IS NOT NULL THEN
        SELECT stacked INTO expected FROM stock WHERE id=NEW.stock_id AND context_id=NEW.context_id;
        IF NEW.stacked IS DISTINCT FROM expected THEN
          RAISE EXCEPTION 'Ledger stock type mismatch' USING ERRCODE='23514';
        END IF;
      END IF;
      ELSIF TG_TABLE_NAME='stock_transfers' THEN
        SELECT stacked INTO source_type FROM stock WHERE id=NEW.source_stock_id AND context_id=NEW.context_id;
        IF NEW.stacked IS DISTINCT FROM source_type THEN
          RAISE EXCEPTION 'Transfer source type mismatch' USING ERRCODE='23514';
        END IF;
        IF NEW.destination_stock_id IS NOT NULL THEN
          SELECT stacked INTO target_type FROM stock WHERE id=NEW.destination_stock_id AND context_id=NEW.context_id;
          IF NEW.stacked IS DISTINCT FROM target_type THEN
            RAISE EXCEPTION 'Transfer cannot convert stock type' USING ERRCODE='23514';
          END IF;
        END IF;
      END IF;
      IF TG_TABLE_NAME='stock_adjustments' THEN
        IF NEW.stacked AND NEW.delta>0 AND NOT EXISTS(
          SELECT 1 FROM putaways p JOIN receipt_lines l ON l.id=p.line_id AND l.context_id=p.context_id
          JOIN receipts r ON r.id=l.receipt_id AND r.context_id=l.context_id
          WHERE p.context_id=NEW.context_id AND p.stacked AND p.status='confirmed'
            AND l.artigo=NEW.artigo AND l.lote=NEW.lote AND r.code=NEW.carga) THEN
          RAISE EXCEPTION 'Positive pair adjustment requires original putaway evidence' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NEW;
    END $$''')
    for table in TYPED + ('dispatch_picks',):
        op.execute(f'CREATE TRIGGER stacked_integrity BEFORE INSERT OR UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION ddn_stacked_guard()')
    op.execute('''CREATE FUNCTION ddn_stacked_dispatch_complete() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.typed_selection AND NOT NEW.typed_selection THEN
        RAISE EXCEPTION 'Cannot downgrade typed FIFO validation' USING ERRCODE='23514';
      END IF;
      IF NEW.status='confirmed' AND OLD.status<>'confirmed' AND NEW.typed_selection THEN
        IF EXISTS(SELECT 1 FROM dispatch_lines l WHERE l.order_id=NEW.id AND l.context_id=NEW.context_id
          AND l.quantidade<>(SELECT coalesce(sum(p.quantidade),0) FROM dispatch_picks p WHERE p.line_id=l.id AND p.context_id=l.context_id)) THEN
          RAISE EXCEPTION 'Insufficient selected stock type' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NEW;
    END $$''')
    op.execute('CREATE TRIGGER stacked_dispatch_complete BEFORE UPDATE ON dispatch_orders FOR EACH ROW EXECUTE FUNCTION ddn_stacked_dispatch_complete()')
    schema = op.get_bind().dialect.identifier_preparer.quote(op.get_bind().scalar(sa.text('SELECT current_schema()')))
    for fn in ('ddn_stacked_guard()','ddn_stacked_dispatch_complete()'):
        op.execute(f'ALTER FUNCTION {fn} SET search_path TO pg_catalog, {schema}, pg_temp')
        op.execute(f'REVOKE ALL ON FUNCTION {fn} FROM PUBLIC')


def downgrade():
    raise RuntimeError('Reversão destrutiva bloqueada. Restaurar backup verificado numa base separada.')
