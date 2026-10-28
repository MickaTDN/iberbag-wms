"""Each run gets a fresh SQLite file or an isolated PostgreSQL schema."""
import os
import tempfile
import uuid
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

os.environ['WMS_COOKIE_SECURE'] = '0'
_test_url = os.environ.get('WMS_TEST_POSTGRES_URL')
_admin = None
_schema = None
if _test_url:
    url = make_url(_test_url)
    if url.drivername != 'postgresql+psycopg' or not url.database.endswith('_test'):
        raise RuntimeError('Use uma base PostgreSQL descartável com nome terminado em _test.')
    _schema = 'test_' + uuid.uuid4().hex
    _admin = create_engine(url)
    with _admin.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA {_schema}')
    url = url.update_query_dict({'options': f'-csearch_path={_schema}'})
    os.environ['WMS_DATABASE_URL'] = url.render_as_string(hide_password=False)
else:
    os.environ['WMS_DATABASE_URL'] = 'sqlite:///' + str(Path(tempfile.mkdtemp()) / 'tests.db')

from iberbag.migrations import prepare_database
prepare_database()

@pytest.fixture(scope='session', autouse=True)
def cleanup_database():
    yield
    from iberbag.database import engine
    engine.dispose()
    if _admin:
        with _admin.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA {_schema} CASCADE')
        _admin.dispose()


def login_data(client, data):
    import re
    token = re.search('name="login_csrf" value="([^"]+)"', client.get('/login').text)[1]
    return dict(data, login_csrf=token)

import secrets
BACKUP_PASSWORD = secrets.token_urlsafe(32)
