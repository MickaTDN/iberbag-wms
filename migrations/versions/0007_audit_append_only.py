"""Protect administrative audit without rewriting any existing record."""
from alembic import op

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.execute("""CREATE FUNCTION wms_audit_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Audit is append-only' USING ERRCODE='23514'; END $$""")
        op.execute('CREATE TRIGGER immutable_audit BEFORE UPDATE OR DELETE ON audit FOR EACH ROW EXECUTE FUNCTION wms_audit_guard()')
        op.execute('CREATE TRIGGER immutable_audit_truncate BEFORE TRUNCATE ON audit FOR EACH STATEMENT EXECUTE FUNCTION wms_audit_guard()')


def downgrade():
    raise RuntimeError('Downgrade de segurança recusado; preservar histórico e rever recuperação.')
