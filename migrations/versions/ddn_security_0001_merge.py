"""Join published security and multiclient histories; no data rewrite."""
revision = 'ddn_security_0001'
down_revision = ('0007', 'ddn_0002')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    raise RuntimeError('Reversão destrutiva/de segurança bloqueada. Restaurar backup verificado numa base separada.')
