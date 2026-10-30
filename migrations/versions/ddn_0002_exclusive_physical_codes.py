"""Prevent capacity duplication under different owner/area labels.

Published ddn_0001 stays unchanged. No stock quantities or history are rewritten.
"""
from alembic import op
import sqlalchemy as sa
revision = 'ddn_0002'
down_revision = 'ddn_0001'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    # Frozen rules, not imports of future application code.
    rows = conn.execute(sa.text("""
        SELECT c.warehouse_id, lower(trim(l.codigo)) AS code, l.context_id FROM locations l
        JOIN storage_contexts c ON c.id=l.context_id
        UNION
        SELECT c.warehouse_id, lower(trim(s.local)) AS code, s.context_id FROM stock s
        JOIN storage_contexts c ON c.id=s.context_id
        ORDER BY warehouse_id, code, context_id
    """)).mappings().all()
    claims = {}
    for row in rows:
        if row['code'] in ('','chão','chao','(não indicado)'):
            continue
        key = (row['warehouse_id'], row['code'])
        if key in claims and claims[key] != row['context_id']:
            raise RuntimeError('Código físico em vários contextos do mesmo armazém. Migração bloqueada sem alterar stock; reconciliar configurações numa cópia.')
        claims[key] = row['context_id']
    op.create_table('physical_location_claims',
        sa.Column('warehouse_id',sa.Integer(),sa.ForeignKey('warehouses.id'),primary_key=True),
        sa.Column('code',sa.String(120),primary_key=True),
        sa.Column('context_id',sa.Integer(),sa.ForeignKey('storage_contexts.id'),nullable=False))
    table = sa.table('physical_location_claims',sa.column('warehouse_id'),sa.column('code'),sa.column('context_id'))
    if claims:
        conn.execute(table.insert(),[dict(warehouse_id=w,code=code,context_id=ctx) for (w,code),ctx in sorted(claims.items())])
    if conn.dialect.name != 'postgresql':
        return
    op.execute("""
        CREATE FUNCTION ddn_claim_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'Physical claims cannot be edited or removed' USING ERRCODE='23514';
            END IF;
            IF NEW.warehouse_id IS DISTINCT FROM (SELECT warehouse_id FROM storage_contexts WHERE id=NEW.context_id)
               OR NEW.code<>lower(btrim(NEW.code)) OR NEW.code IN ('','chão','chao','(não indicado)') THEN
                RAISE EXCEPTION 'Invalid physical claim identity' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER physical_claim_guard BEFORE INSERT OR UPDATE OR DELETE ON physical_location_claims
        FOR EACH ROW EXECUTE FUNCTION ddn_claim_guard();
        CREATE TRIGGER physical_claim_truncate BEFORE TRUNCATE ON physical_location_claims
        FOR EACH STATEMENT EXECUTE FUNCTION ddn_claim_guard();
    """)
    op.execute("""
        CREATE FUNCTION ddn_claim_position() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE physical_code text; wh integer; owner integer;
        BEGIN
            PERFORM pg_advisory_xact_lock(72834001);
            IF TG_TABLE_NAME='locations' THEN physical_code=lower(btrim(NEW.codigo));
            ELSE physical_code=lower(btrim(NEW.local)); END IF;
            IF physical_code IN ('','chão','chao','(não indicado)') THEN RETURN NEW; END IF;
            SELECT warehouse_id INTO wh FROM storage_contexts WHERE id=NEW.context_id;
            INSERT INTO physical_location_claims(warehouse_id,code,context_id) VALUES(wh,physical_code,NEW.context_id)
                ON CONFLICT(warehouse_id,code) DO NOTHING;
            SELECT context_id INTO owner FROM physical_location_claims WHERE warehouse_id=wh AND code=physical_code;
            IF owner IS DISTINCT FROM NEW.context_id THEN
                RAISE EXCEPTION 'Physical position belongs to another context; sharing unsupported' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER exclusive_physical_location BEFORE INSERT OR UPDATE OF codigo,context_id ON locations
            FOR EACH ROW EXECUTE FUNCTION ddn_claim_position();
        CREATE TRIGGER exclusive_physical_stock BEFORE INSERT OR UPDATE OF local,context_id ON stock
            FOR EACH ROW EXECUTE FUNCTION ddn_claim_position();
    """)


def downgrade():
    raise RuntimeError('Reversão destrutiva bloqueada. Restaurar backup verificado numa base separada.')
