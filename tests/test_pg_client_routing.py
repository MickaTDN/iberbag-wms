"""Regression also runnable against the vulnerable pre-fix commit."""
import os
import secrets
from types import SimpleNamespace
from unittest.mock import patch
from sqlalchemy.engine import URL
from iberbag import backup


def test_pg_clients_drop_inherited_hostaddr_and_services(monkeypatch):
    for key in list(os.environ):
        if key.startswith(('PG','WMS_PG_')):monkeypatch.delenv(key)
    monkeypatch.setenv('PGHOSTADDR','127.0.0.2')
    monkeypatch.setenv('PGSERVICE','unexpected')
    monkeypatch.setenv('PGSERVICEFILE','unexpected-services')
    url=URL.create('postgresql+psycopg',username='synthetic',password=secrets.token_urlsafe(24),
                   host='127.0.0.1',port=5432,database='fictitious_test')
    calls=[]
    def run(args,**kwargs):
        calls.append((args,kwargs['env']))
        return SimpleNamespace(returncode=0,stdout=b'pg_dump (PostgreSQL) 17.5')
    with patch.object(backup,'client',return_value='synthetic-client'),patch.object(backup.subprocess,'run',side_effect=run):
        for command in ('pg_dump','pg_restore'):backup.run_client(command,[],url)
    assert all('PGHOSTADDR' not in env and 'PGSERVICE' not in env and 'PGSERVICEFILE' not in env for argv,env in calls)
    assert all(url.password not in ' '.join(argv) for argv,env in calls)
