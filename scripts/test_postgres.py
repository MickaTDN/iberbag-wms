"""Run the suite against disposable PostgreSQL 17 (requires Docker)."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from sqlalchemy.engine import URL

def main():
    name = 'iberbag-test-' + secrets.token_hex(6)
    password = secrets.token_urlsafe(32)
    try:
        subprocess.run(['docker', 'run', '--detach', '--rm', '--name', name,
                        '-e', 'POSTGRES_PASSWORD=' + password, '-e', 'POSTGRES_DB=iberbag_test',
                        '-p', '127.0.0.1::5432', 'postgres:17@sha256:b8faeff7712663c2773a190181e1246e37e364b887611c04658c28b44e2408fe'], check=True, stdout=subprocess.DEVNULL)
        for attempt in range(60):
            if subprocess.run(['docker', 'exec', name, 'pg_isready', '-U', 'postgres'],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError('PostgreSQL não arrancou em 30 segundos.')
        info = json.loads(subprocess.check_output(['docker', 'inspect', name]))[0]
        port = int(info['NetworkSettings']['Ports']['5432/tcp'][0]['HostPort'])
        url = URL.create('postgresql+psycopg', username='postgres', password=password,
                         host='127.0.0.1', port=port, database='iberbag_test')
        env = dict(os.environ, WMS_TEST_POSTGRES_URL=url.render_as_string(hide_password=False),
                   WMS_TEST_PG_CONTAINER=name)
        result = subprocess.run([sys.executable, '-m', 'pytest', '-q'], env=env,
                                cwd=Path(__file__).resolve().parent.parent)
    finally:
        subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
