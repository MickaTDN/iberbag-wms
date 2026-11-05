"""Annual load allocation per stock owner, without rewriting legacy references."""
from alembic import op
import sqlalchemy as sa

revision = 'ddn_0003'
down_revision = 'ddn_security_0001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('load_numbers',
        sa.Column('client_id', sa.Integer(), sa.ForeignKey('clients.id'), primary_key=True),
        sa.Column('year', sa.Integer(), primary_key=True),
        sa.Column('number', sa.Integer(), primary_key=True),
        sa.Column('receipt_id', sa.Integer(), sa.ForeignKey('receipts.id'), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint('year >= 2027 AND number > 0', name='load_number_valid'))
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('''CREATE FUNCTION protect_load_number() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'Numeração de cargas imutável'; END IF;
          IF NOT EXISTS (SELECT 1 FROM receipts r JOIN storage_contexts c ON c.id=r.context_id
            WHERE r.id=NEW.receipt_id AND c.client_id=NEW.client_id
            AND EXTRACT(YEAR FROM r.received_on)=NEW.year AND r.status='draft') THEN
            RAISE EXCEPTION 'Origem da numeração incompatível';
          END IF;
          RETURN NEW;
        END $$''')
        op.execute('CREATE TRIGGER load_numbers_immutable BEFORE INSERT OR UPDATE OR DELETE ON load_numbers FOR EACH ROW EXECUTE FUNCTION protect_load_number()')
        op.execute('CREATE TRIGGER load_numbers_no_truncate BEFORE TRUNCATE ON load_numbers FOR EACH STATEMENT EXECUTE FUNCTION protect_load_number()')


def downgrade():
    raise RuntimeError('Restaurar backup verificado numa base separada; não apagar numeração emitida.')
