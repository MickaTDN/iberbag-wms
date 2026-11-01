"""Explicit maintenance-only runtime grants; never create roles or persist passwords."""
import argparse
import re
from sqlalchemy import text

IMMUTABLE = {'audit', 'dispatch_documents', 'receipt_documents', 'stock_adjustments',
             'lot_resolutions', 'preparation_completions', 'preparation_compensations',
             'customer_pending', 'pending_allocations', 'allocation_collections',
             'physical_location_claims'}


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
    if connection.scalar(text("SELECT has_database_privilege(:r,current_database(),'CREATE')"), {'r':role}):
        raise ValueError('Conta web não pode criar esquemas na base.')
    # Inspect persistent schemas including pg_catalog, not just current_schema.
    # has_*_privilege includes PUBLIC and other effective grants.
    if connection.scalar(text('''SELECT count(*) FROM pg_namespace n
            WHERE n.nspname !~ '^pg_(toast|temp)(_|$)'
            AND (n.nspowner=:oid OR has_schema_privilege(:r,n.oid,'CREATE'))'''), {'oid':oid,'r':role}):
        raise ValueError('Conta web não pode possuir ou criar objetos em esquemas da base.')
    if connection.scalar(text('''SELECT count(*) FROM (
            SELECT relnamespace AS namespace, relowner AS owner FROM pg_class
            UNION ALL SELECT pronamespace, proowner FROM pg_proc
            UNION ALL SELECT typnamespace, typowner FROM pg_type) o
            JOIN pg_namespace n ON n.oid=o.namespace
            WHERE n.nspname !~ '^pg_(toast|temp)(_|$)'
            AND o.owner=:oid'''), {'oid':oid}):
        raise ValueError('Conta web não pode ser owner de objetos, mesmo fora do esquema corrente.')
    schema = connection.scalar(text('SELECT current_schema()'))
    quote = connection.dialect.identifier_preparer.quote
    # Check effective privileges, including PUBLIC and grants external to this
    # tool. SQL triggers and restricted grants protect immutable history together.
    for table in sorted(IMMUTABLE):
        relation = quote(schema) + '.' + quote(table)
        for privilege in ('UPDATE','DELETE','TRUNCATE'):
            if connection.scalar(text('SELECT has_table_privilege(:r,:t,:p)'),
                                 {'r':role,'t':relation,'p':privilege}):
                raise ValueError('Conta web não pode modificar/apagar histórico ou atribuições físicas.')
    # No grant option, DDL/maintenance privilege or sequence UPDATE is required.
    from .database import Base
    for table in sorted(set(Base.metadata.tables) | {'alembic_version'}):
        relation = quote(schema) + '.' + quote(table)
        allowed = ('SELECT',) if table == 'alembic_version' else (('SELECT','INSERT') if table in IMMUTABLE else ('SELECT','INSERT','UPDATE','DELETE'))
        for privilege in ('TRIGGER','REFERENCES','TRUNCATE','MAINTAIN', *(p+' WITH GRANT OPTION' for p in allowed)):
            if connection.scalar(text('SELECT has_table_privilege(:r,:t,:p)'), {'r':role,'t':relation,'p':privilege}):
                raise ValueError('Conta web tem privilégios adicionais de DDL, manutenção ou delegação.')
    if connection.scalar(text('''SELECT count(*) FROM pg_class s JOIN pg_namespace n ON n.oid=s.relnamespace
            WHERE n.nspname !~ '^pg_(toast|temp)(_|$)'
            AND CASE WHEN s.relkind='S' THEN has_sequence_privilege(:r,s.oid,'UPDATE') ELSE false END'''), {'r':role}):
        raise ValueError('Conta web não pode alterar diretamente sequências.')


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
