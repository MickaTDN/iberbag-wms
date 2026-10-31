"""Consistent PostgreSQL backup plus full-row reconciliation on test restore."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import getpass
from .backup_crypto import encrypt, decrypt, key
from .private_files import private_folder, owned_file

from sqlalchemy import create_engine, text
from .transfer import (LOCK, digest_file, inventory, read_data, require_empty,
                       require_test_target, save_json, differences, archive_inventory)


def client(name):
    folder = os.environ.get('WMS_PG_BIN')
    if folder:
        path = Path(folder) / (name + ('.exe' if os.name == 'nt' else ''))
    elif os.name == 'nt':
        path = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'PostgreSQL' / '17' / 'bin' / (name + '.exe')
    else:
        path = shutil.which(name)
    if not path or not Path(path).is_file():
        raise ValueError('Ferramentas PostgreSQL 17 não encontradas. Configurar WMS_PG_BIN.')
    return str(path)


def run_client(name, args, url, *, stdin=None, stdout=None):
    # No password in command arguments, URLs, output or saved configuration.
    env = dict(os.environ)
    for key in ('PGSERVICE', 'PGSERVICEFILE', 'PGOPTIONS', 'PGHOST', 'PGPORT', 'PGDATABASE', 'PGUSER'):
        env.pop(key, None)
    env['PGPASSWORD'] = url.password or ''
    version = subprocess.run([client(name), '--version'], capture_output=True, check=True, env=env)
    if b' 17.' not in version.stdout:
        raise ValueError('Usar ferramentas PostgreSQL 17.')
    command = [client(name), '--host', url.host or '127.0.0.1', '--port', str(url.port or 5432),
               '--username', url.username or '', '--dbname', url.database, '--no-password', *args]
    result = subprocess.run(command, env=env, stdin=stdin, stdout=stdout, stderr=subprocess.PIPE)
    if result.returncode:
        raise ValueError(f'{name} falhou. Verificar serviço, credenciais e permissões; saída sensível omitida.')


def backup(engine, folder, runner=run_client, *, passphrase=None):
    key(passphrase, bytes(16))  # Validate before contacting the database or creating files.
    if engine.url.drivername != 'postgresql+psycopg' or engine.url.query:
        raise ValueError('Usar uma ligação PostgreSQL simples, sem parâmetros personalizados.')
    folder = private_folder(folder, create=True)
    archive = owned_file(folder, 'database.dump')
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        with connection.begin():
            connection.execute(text('SET TRANSACTION READ ONLY'))
            snapshot = connection.scalar(text('SELECT pg_export_snapshot()'))
            expected = inventory(read_data(connection))
            report = {'format': 2, 'created_at': datetime.now().isoformat(), 'inventory': expected,
                      'encryption': 'AES-256-GCM/PBKDF2-SHA256-600000', 'restore_verified': False}
            # pg_dump needs a file descriptor. Plaintext exists only in a private,
            # disposable temporary file inside the ACL-restricted backup folder.
            with tempfile.TemporaryFile(dir=folder) as plain:
                runner('pg_dump', ['--format=custom', '--no-owner', '--no-privileges',
                                   '--snapshot=' + snapshot], engine.url, stdout=plain)
                plain.seek(0)
                with archive.open('xb') as output: encrypt(plain, output, passphrase, report)
    report['archive_sha256'] = digest_file(archive)
    save_json(folder / 'manifest.json', report)
    return report


def restore(engine, folder, runner=run_client, *, passphrase=None, allow_legacy=False):
    require_test_target(engine)
    folder = private_folder(folder)
    archive = owned_file(folder, 'database.dump')
    manifest = json.loads(owned_file(folder, 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format') not in (1, 2) or digest_file(archive) != manifest['archive_sha256']:
        raise ValueError('Backup alterado ou manifesto inválido.')
    with tempfile.TemporaryFile(dir=folder) as plain:
        if manifest['format'] == 2:
            with archive.open('rb') as source: decrypt(source, plain, passphrase, manifest)
        elif allow_legacy:
            with archive.open('rb') as source: shutil.copyfileobj(source, plain)
            plain.seek(0)
        else: raise ValueError('Backup antigo não cifrado: exigir autorização --legacy-plaintext explícita.')
        return _restore_plain(engine, folder, runner, manifest, plain)


def _restore_plain(engine, folder, runner, manifest, plain):
    with engine.connect() as connection:
        # Session lock also covers pg_restore running in a separate connection.
        connection.execute(text('SELECT pg_advisory_lock(:key)'), {'key': LOCK})
        try:
            require_empty(connection)
            connection.commit()
            runner('pg_restore', ['--single-transaction', '--exit-on-error', '--no-owner', '--no-privileges'],
                   engine.url, stdin=plain)
            with connection.begin():
                connection.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
                actual = archive_inventory(connection, manifest['inventory'])
            changed = differences(manifest['inventory'], actual)
            report = {'status': 'different' if changed else 'reconciled', 'different_tables': changed,
                      'archive_sha256': manifest['archive_sha256'], 'inventory': actual}
            save_json(folder / ('restore-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.json'), report)
            if changed:
                raise ValueError('Restauro divergente: não usar a base. Tabelas: ' + ', '.join(changed))
            return report
        finally:
            connection.rollback()
            connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': LOCK})
            connection.commit()


def main():
    parser = argparse.ArgumentParser(description='Backup e ensaio de restauro PostgreSQL.')
    parser.add_argument('action', choices=['backup', 'restore-test'])
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--legacy-plaintext', action='store_true', help='autorizar apenas leitura de backup antigo não cifrado')
    args = parser.parse_args()
    password = getpass.getpass('Senha de cifra do backup (guardar separadamente): ')
    if args.action == 'backup' and password != getpass.getpass('Confirmar senha de cifra: '):
        parser.error('Senhas de cifra diferentes.')
    from .server import configure
    configure()
    engine = create_engine(os.environ['WMS_DATABASE_URL'])
    try:
        result = backup(engine, args.folder, passphrase=password) if args.action == 'backup' else restore(engine, args.folder, passphrase=password, allow_legacy=args.legacy_plaintext)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError) as error:
        parser.exit(1, f'Operação recusada: {error}\n')
    except Exception:
        parser.exit(1, 'Operação falhou. Verificar PostgreSQL, ferramentas e permissões.\n')
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
