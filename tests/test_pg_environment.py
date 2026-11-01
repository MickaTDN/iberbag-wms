"""Transport routing and explicit TLS; no external PostgreSQL connections."""
import os
import secrets
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.engine import URL
from iberbag import pg_environment as policy
from iberbag import backup


@pytest.fixture(autouse=True)
def restore_environment():
    before={k:v for k,v in os.environ.items() if k.startswith(('PG','WMS_PG_'))}
    try:yield
    finally:
        for key in list(os.environ):
            if key.startswith(('PG','WMS_PG_')):os.environ.pop(key)
        os.environ.update(before)


def synthetic_url(**query):
    return URL.create('postgresql+psycopg',username='synthetic',password=secrets.token_urlsafe(24),
                      host='127.0.0.1',port=5432,database='fictitious_test',query=query)


def test_inherited_routes_and_credentials_never_reach_libpq():
    source={'PATH':os.environ.get('PATH',''),'PGHOSTADDR':'127.0.0.2','PGSERVICE':'production',
            'PGSERVICEFILE':'private-services','PGOPTIONS':'-c search_path=wrong','PGUSER':'unexpected',
            'PGDATABASE':'unexpected','PGPASSWORD':secrets.token_urlsafe(24),'PGPASSFILE':'private-passfile',
            'WMS_DATABASE_URL':'do-not-copy','WMS_DB_NAME':'production'}
    url=synthetic_url()
    env=policy.client_environment(url,source)
    assert set(k for k in env if k.startswith('PG'))=={'PGPASSWORD','PGPASSFILE','PGCONNECT_TIMEOUT'}
    assert env['PGPASSWORD']==url.password and env['PGPASSFILE']==os.devnull
    assert not any(k.startswith('WMS_') for k in env)
    assert policy.isolated_test_environment(source)=={'PATH':source['PATH']}


def test_explicit_tls_preserved_without_legacy_tls_downgrade():
    source={'WMS_PG_SSLMODE':'verify-full','WMS_PG_SSLROOTCERT':'fictional-ca.pem',
            'WMS_PG_SSLCERT':'fictional-client.pem','WMS_PG_SSLKEY':'fictional-client.key',
            'WMS_PG_SSL_MIN_PROTOCOL_VERSION':'TLSv1.2','PGHOSTADDR':'127.0.0.2'}
    env=policy.client_environment(synthetic_url(),source)
    assert env['PGSSLMODE']=='verify-full' and env['PGSSLROOTCERT']=='fictional-ca.pem'
    assert env['PGSSLCERT']=='fictional-client.pem' and env['PGSSLKEY']=='fictional-client.key'
    assert env['PGSSLMINPROTOCOLVERSION']=='TLSv1.2' and 'PGHOSTADDR' not in env
    with pytest.raises(ValueError):policy.client_environment(synthetic_url(),{'PGSSLMODE':'verify-full'})
    env=policy.client_environment(synthetic_url(sslmode='verify-full',sslrootcert='explicit-ca'),{})
    assert env['PGSSLMODE']=='verify-full' and env['PGSSLROOTCERT']=='explicit-ca'
    with pytest.raises(ValueError):policy.client_environment(synthetic_url(service='production'),{})
    with pytest.raises(ValueError):policy.client_environment(synthetic_url(sslmode='invalid'),{})


def test_pg_dump_receives_controlled_environment_and_no_password_argv(monkeypatch):
    for key in list(os.environ):
        if key.startswith(('PG','WMS_PG_')):monkeypatch.delenv(key)
    monkeypatch.setenv('PGHOSTADDR','127.0.0.2')
    monkeypatch.setenv('PGSERVICE','production')
    monkeypatch.setenv('WMS_PG_SSLMODE','verify-full')
    url=synthetic_url();calls=[]
    def run(args,**kwargs):
        calls.append((args,kwargs['env']))
        return SimpleNamespace(returncode=0,stdout=b'pg_dump (PostgreSQL) 17.5')
    with patch.object(backup,'client',return_value='synthetic-pg-client'),patch.object(backup.subprocess,'run',side_effect=run):
        for command in ('pg_dump','pg_restore'):backup.run_client(command,[],url)
    for argv,env in calls:
        assert url.password not in ' '.join(argv)
        assert env['PGPASSWORD']==url.password and env['PGSSLMODE']=='verify-full'
        assert 'PGHOSTADDR' not in env and 'PGSERVICE' not in env


def test_process_policy_removes_hidden_routes_before_engine_connections(monkeypatch):
    for key in list(os.environ):
        if key.startswith(('PG','WMS_PG_')):monkeypatch.delenv(key)
    monkeypatch.setenv('PGHOSTADDR','127.0.0.2')
    monkeypatch.setenv('PGSERVICE','production')
    monkeypatch.setenv('PGOPTIONS','-c search_path=unexpected')
    monkeypatch.setenv('WMS_PG_SSLMODE','verify-full')
    policy.configure_process(synthetic_url())
    assert 'PGHOSTADDR' not in os.environ and 'PGSERVICE' not in os.environ and 'PGOPTIONS' not in os.environ
    assert os.environ['PGSSLMODE']=='verify-full'

