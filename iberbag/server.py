"""Windows/LAN entry point: PostgreSQL only, loopback HTTP behind HTTPS."""
import argparse
import getpass
import os
from sqlalchemy.engine import URL, make_url


def configure():
    value = os.environ.get('WMS_DATABASE_URL')
    if not value:
        value = URL.create(
            'postgresql+psycopg',
            username=os.environ.get('WMS_DB_USER', 'iberbag_app'),
            password=getpass.getpass('Palavra-passe PostgreSQL: '),
            host=os.environ.get('WMS_DB_HOST', '127.0.0.1'),
            port=int(os.environ.get('WMS_DB_PORT', '5432')),
            database=os.environ.get('WMS_DB_NAME', 'iberbag'),
        ).render_as_string(hide_password=False)
    if make_url(value).drivername != 'postgresql+psycopg':
        raise SystemExit('O servidor de rede exige PostgreSQL (psycopg).')
    os.environ['WMS_DATABASE_URL'] = value
    os.environ['WMS_COOKIE_SECURE'] = '1'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'admin', 'serve', 'upgrade-preview', 'upgrade-apply'], default='serve', nargs='?')
    parser.add_argument('--backup-folder')
    args = parser.parse_args()
    configure()
    if args.action.startswith('upgrade-'):
        import json
        from .database import engine
        from .schema_upgrade import preview, apply_upgrade
        if args.action == 'upgrade-apply' and not args.backup_folder:
            parser.error('--backup-folder é obrigatório')
        try:
            result = preview(engine) if args.action == 'upgrade-preview' else apply_upgrade(engine, args.backup_folder)
        except Exception:
            raise SystemExit('Atualização recusada. Rever versão, ligação e backup/restauro verificado com servidor parado.')
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.action == 'prepare':
        from .migrations import prepare_database
        prepare_database()
        print('Base preparada.')
    elif args.action == 'admin':
        import subprocess, sys
        raise SystemExit(subprocess.call([sys.executable, '-m', 'iberbag.cli', 'create-admin']))
    else:
        from .migrations import check_database
        check_database()
        from .database import engine
        from .privileges import validate_runtime
        with engine.connect() as connection:
            validate_runtime(connection)
        import uvicorn
        uvicorn.run('iberbag.app:app', host='127.0.0.1', port=8000, workers=2,
                    proxy_headers=True, forwarded_allow_ips='127.0.0.1')


if __name__ == '__main__':
    main()
