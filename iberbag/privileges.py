"""Explicit maintenance-only runtime grants; never create roles or persist passwords."""
import argparse
import re
from sqlalchemy import text

IMMUTABLE = {'audit', 'dispatch_documents', 'receipt_documents', 'stock_adjustments',
             'lot_resolutions', 'preparation_completions', 'preparation_compensations',
             'customer_pending', 'pending_allocations', 'allocation_collections'}


def role_flags(connection, role):
    row = connection.execute(text('SELECT oid,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=:r'), {'r':role}).first()
    if row is None or any(row[1:]): raise ValueError('Conta inexistente ou com privilégios administrativos.')
    if connection.scalar(text('SELECT count(*) FROM pg_auth_members WHERE member=:oid'), {'oid':row[0]}):
        raise ValueError('Conta de execução não pode herdar outros roles.')
    return row[0]


def validate_runtime(connection, role=None):
    role = role or connection.scalar(text('SELECT current_user'))
    oid = role_flags(connection, role)
    if connection.scalar(text('SELECT count(*) FROM pg_database WHERE datname=current_database() AND datdba=:oid'), {'oid':oid}):
        raise ValueError('Conta web não pode ser owner da base.')
    if connection.scalar(text('SELECT count(*) FROM pg_namespace WHERE nspname=current_schema() AND nspowner=:oid'), {'oid':oid}):
        raise ValueError('Conta web não pode ser owner do esquema.')
    if connection.scalar(text("SELECT count(*) FROM pg_class WHERE relnamespace=current_schema()::regnamespace AND relowner=:oid"), {'oid':oid}):
        raise ValueError('Conta web não pode ser owner das tabelas/sequências.')
    for privilege in ('UPDATE','DELETE','TRUNCATE'):
        if connection.scalar(text('SELECT has_table_privilege(:r,\'audit\',:p)'), {'r':role,'p':privilege}):
            raise ValueError('Conta web não pode modificar/apagar auditoria.')
    if connection.scalar(text('SELECT has_schema_privilege(:r,current_schema(),\'CREATE\')'), {'r':role}):
        raise ValueError('Conta web não pode criar objetos no esquema.')


def grant_runtime(connection, role, apply=False):
    if not re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', role): raise ValueError('Nome de role inválido.')
    role_flags(connection, role)
    if role == connection.scalar(text('SELECT current_user')): raise ValueError('Executar com conta de manutenção separada.')
    from .database import Base
    q = connection.dialect.identifier_preparer.quote
    schema = connection.scalar(text('SELECT current_schema()'))
    database = connection.scalar(text('SELECT current_database()'))
    statements = [f'GRANT CONNECT ON DATABASE {q(database)} TO {q(role)}',
                  f'GRANT USAGE ON SCHEMA {q(schema)} TO {q(role)}',
                  f'REVOKE CREATE ON SCHEMA {q(schema)} FROM {q(role)}',
                  f'REVOKE ALL ON ALL TABLES IN SCHEMA {q(schema)} FROM {q(role)}',
                  f'REVOKE ALL ON ALL SEQUENCES IN SCHEMA {q(schema)} FROM {q(role)}']
    for name in sorted(Base.metadata.tables):
        permissions = 'SELECT, INSERT' if name in IMMUTABLE else 'SELECT, INSERT, UPDATE, DELETE'
        statements.append(f'GRANT {permissions} ON {q(schema)}.{q(name)} TO {q(role)}')
    statements += [f'GRANT SELECT ON {q(schema)}.alembic_version TO {q(role)}',
                   f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {q(schema)} TO {q(role)}']
    if apply:
        for statement in statements: connection.execute(text(statement))
        validate_runtime(connection, role)
    return statements


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--role',required=True);parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    from .server import configure
    configure()
    from .database import engine
    with engine.begin() as connection:
        print('\n'.join(grant_runtime(connection,args.role,args.apply)))


if __name__=='__main__': main()
