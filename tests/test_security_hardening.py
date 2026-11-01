"""Fictional security regressions; no production connections or original workbook."""
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import zipfile
import pytest
from fastapi.testclient import TestClient
from defusedxml.common import DefusedXmlException
from sqlalchemy import select, text, create_engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from iberbag.app import app
from iberbag.database import User, LoginSession, SessionLocal
from iberbag.security import set_password
from iberbag import workbook, backup
from iberbag.backup_crypto import encrypt, decrypt
from iberbag.private_files import private_folder
from conftest import login_data, BACKUP_PASSWORD
from test_receiving import warehouse, receive, draft
from test_transfer import pg_databases, docker_client


@pytest.fixture
def credentials():
    name='security_'+secrets.token_hex(6);password=secrets.token_urlsafe(25)
    with SessionLocal() as db:
        u=User(username=name,display_name='Pessoa Fictícia',role='operador',active=True)
        set_password(u,password);db.add(u);db.commit()
    return dict(username=name,password=password)


@pytest.mark.parametrize('headers',[{'Origin':'https://external.invalid'},{'Origin':'null'},
    {'Referer':'https://external.invalid/login'},{'Origin':'http://testserver:81'},
    [('Origin','http://testserver'),('Origin','https://external.invalid')]])
def test_login_foreign_origin_with_valid_token_is_refused(credentials,headers):
    with TestClient(app) as client:
        data=login_data(client,credentials)
        with SessionLocal() as db: before=len(db.scalars(select(LoginSession)).all())
        assert client.post('/login',data=data,headers=headers).status_code==403
        with SessionLocal() as db: assert len(db.scalars(select(LoginSession)).all())==before


def test_login_missing_forged_cookie_and_legitimate_retry(credentials):
    with TestClient(app) as client:
        assert client.post('/login',data=credentials).status_code==403
        data=login_data(client,credentials)
        forged=dict(data,login_csrf=secrets.token_hex(32))
        assert client.post('/login',data=forged).status_code==403
        response=client.post('/login',data=login_data(client,dict(credentials,password=secrets.token_urlsafe(25))))
        assert response.status_code==401 and 'name="login_csrf"' in response.text
        response=client.post('/login',data=login_data(client,credentials),headers={'Origin':'http://testserver','Referer':'http://testserver/login'},follow_redirects=False)
        assert response.status_code==303 and 'wms_login' not in client.cookies
        assert client.get('/stock').status_code==200


def test_preview_sanitizes_before_any_database_import(tmp_path):
    code='''
import os,socket,sys
from pathlib import Path
socket.socket.connect=lambda *args:(_ for _ in ()).throw(AssertionError('Network forbidden'))
from iberbag.local_preview import isolate
path=isolate(sys.argv[1])
assert os.environ['WMS_DATABASE_URL'].startswith('sqlite:///')
assert 'PGHOST' not in os.environ and 'WMS_TEST_POSTGRES_URL' not in os.environ
from iberbag.demo import create_demo
import secrets
create_demo(path,{r:secrets.token_urlsafe(25) for r in ('admin','operador')})
assert path.is_file()
'''
    env=dict(os.environ,WMS_DATABASE_URL='postgresql+psycopg://invalid:invalid@192.0.2.1/production',PGHOST='192.0.2.1',WMS_TEST_POSTGRES_URL='invalid')
    subprocess.run([sys.executable,'-c',code,str(tmp_path)],env=env,check=True,capture_output=True,timeout=20)


def manifest():return dict(format=2,created_at='FICTICIO',inventory={},encryption='AES-256-GCM/PBKDF2-SHA256-600000')


def test_envelope_authentication_wrong_key_metadata_and_tampering():
    data=b'PGDMP FICTICIO '+secrets.token_bytes(3000000);meta=manifest();encrypted=BytesIO()
    encrypt(BytesIO(data),encrypted,BACKUP_PASSWORD,meta)
    content=encrypted.getvalue();assert b'PGDMP FICTICIO' not in content
    out=BytesIO();decrypt(BytesIO(content),out,BACKUP_PASSWORD,meta);assert out.read()==data
    for password,body,m in [(secrets.token_urlsafe(32),content,meta),
                           (BACKUP_PASSWORD,content[:-1]+bytes([content[-1]^1]),meta),
                           (BACKUP_PASSWORD,content,dict(meta,inventory={'changed':True})),
                           (BACKUP_PASSWORD,content[:10],meta)]:
        with pytest.raises(ValueError):decrypt(BytesIO(body),BytesIO(),password,m)


def test_private_directory_restricts_inheritance_and_refuses_links(tmp_path):
    folder=private_folder(tmp_path/'private',create=True);assert private_folder(folder)==folder
    if os.name!='nt':
        assert folder.stat().st_mode & 0o077==0
        folder.chmod(0o755)
        with pytest.raises(ValueError):private_folder(folder)
        link=tmp_path/'link';link.symlink_to(folder,target_is_directory=True)
        with pytest.raises(ValueError):private_folder(link)


@pytest.mark.parametrize('cell',['ZZZZZZZZ1','XFE1','A0','A1048577','A1garbage','../A1','a1'])
def test_workbook_cell_reference_limits(cell):
    with pytest.raises(ValueError):workbook.column_number(cell)


@pytest.mark.parametrize('case',['ratio','duplicates','traversal','total','entries'])
def test_workbook_archive_limits(tmp_path,monkeypatch,case):
    p=tmp_path/'fictional.xlsx'
    with zipfile.ZipFile(p,'w',compression=zipfile.ZIP_DEFLATED) as z:
        if case=='ratio':z.writestr('xl/large.xml','a'*100000)
        elif case=='duplicates':z.writestr('x','1');z.writestr('x','2')
        elif case=='traversal':z.writestr('../escape','x')
        elif case=='total':monkeypatch.setattr(workbook,'MAX_EXPANDED',1);z.writestr('x','12')
        else:monkeypatch.setattr(workbook,'MAX_ENTRIES',1);z.writestr('a','a');z.writestr('b','b')
    with pytest.raises(ValueError):workbook.workbook_rows(p)


def test_workbook_rejects_entities_duplicate_cells_and_slot_budget(monkeypatch):
    for xml in [b'<!DOCTYPE x [<!ENTITY a "secret">]><x>&a;</x>',
        b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><row r="1"><c r="A1"/><c r="A1"/></row></worksheet>',
        b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><row r="1"><c r="XFD1"/></row></worksheet>']:
        archive=BytesIO()
        with zipfile.ZipFile(archive,'w') as z:z.writestr('sheet.xml',xml)
        monkeypatch.setattr(workbook,'MAX_SLOTS',100)
        with zipfile.ZipFile(archive) as z,pytest.raises((ValueError,DefusedXmlException)):list(workbook.sheet_rows(z,'sheet.xml',[]))


def test_audit_immutable_even_for_owner(warehouse):
    engine,_=warehouse
    for sql in ("UPDATE audit SET details='changed'",'DELETE FROM audit','TRUNCATE audit'):
        with pytest.raises(DBAPIError),engine.begin() as c:c.execute(text(sql))


def test_wrong_backup_password_never_touches_destination(warehouse,pg_databases,tmp_path):
    origin,_=warehouse;dest=pg_databases();folder=tmp_path/'encrypted'
    backup.backup(origin,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    with pytest.raises(ValueError,match='senha|alterado'):
        backup.restore(dest,folder,runner=docker_client,passphrase=secrets.token_urlsafe(32))
    from iberbag.transfer import require_empty
    with dest.connect() as c:require_empty(c)
    assert backup.restore(dest,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'


def test_runtime_role_cannot_ddl_or_erase_audit_and_can_move(warehouse):
    from iberbag.privileges import grant_runtime,validate_runtime
    from iberbag import receiving
    engine,accounts=warehouse;role='runtime_'+secrets.token_hex(6);password=secrets.token_urlsafe(32)
    # Only the disposable test server's admin creates the runtime account.
    from sqlalchemy.engine import make_url
    admin=create_engine(make_url(os.environ['WMS_TEST_POSTGRES_URL']).set(database=engine.url.database))
    from psycopg import sql
    try:
        with admin.begin() as c:c.exec_driver_sql(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role),sql.Literal(password)).as_string())
        with engine.begin() as c:grant_runtime(c,role,True)
        runtime=create_engine(engine.url.set(username=role,password=password))
        try:
            with runtime.connect() as c:validate_runtime(c)
            for statement in ('DELETE FROM audit','TRUNCATE audit','CREATE TABLE unexpected(id int)','ALTER TABLE audit DISABLE TRIGGER ALL'):
                with pytest.raises(DBAPIError),runtime.begin() as c:c.execute(text(statement))
            with Session(runtime) as db:
                ident=receiving.save_receipt(db,accounts['operador'][0],secrets.token_hex(16),'SECURITY-FICTICIA','2026-01-01',[('ART','LOT','1')])
                receiving.confirm_receipt(db,accounts['operador'][0],ident,1)
        finally:runtime.dispose()
    finally:
        with admin.begin() as c:
            c.exec_driver_sql(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)).as_string())
            c.exec_driver_sql(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)).as_string())
        admin.dispose()


def test_legacy_restore_requires_explicit_authorization(warehouse,pg_databases,tmp_path):
    from iberbag.transfer import inventory,read_data,digest_file,save_json,require_empty
    engine,_=warehouse;dest=pg_databases();folder=private_folder(tmp_path/'legacy',create=True)
    archive=folder/'database.dump'
    with archive.open('xb') as output:docker_client('pg_dump',['--format=custom','--no-owner','--no-privileges'],engine.url,stdout=output)
    with engine.connect() as c:expected=inventory(read_data(c))
    save_json(folder/'manifest.json',dict(format=1,archive_sha256=digest_file(archive),inventory=expected))
    with pytest.raises(ValueError,match='antigo não cifrado'):backup.restore(dest,folder,runner=docker_client)
    with dest.connect() as c:require_empty(c)
    assert backup.restore(dest,folder,runner=docker_client,allow_legacy=True)['status']=='reconciled'


def test_security_upgrade_from_0006_preserves_all_data(pg_databases,tmp_path):
    from alembic import command
    from iberbag.migrations import config
    from iberbag.database import seed_warehouse_layout
    from iberbag.transfer import inventory,read_data
    from iberbag.schema_upgrade import apply_upgrade
    engine=pg_databases(restricted=True);restored=pg_databases(restricted=True)
    with engine.begin() as c:
        cfg=config();cfg.attributes['connection']=c;command.upgrade(cfg,'0006')
    with Session(engine) as db:
        from legacy_catalogue import seed_legacy_catalogue
        seed_legacy_catalogue(db)
    with engine.connect() as c:before=inventory(read_data(c))
    folder=tmp_path/'upgrade';backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    backup.restore(restored,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert apply_upgrade(engine,folder)['revision']==__import__('iberbag.migrations',fromlist=['REVISION']).REVISION
    with engine.connect() as c:assert inventory(read_data(c))==before
    with pytest.raises(DBAPIError),engine.begin() as c:c.execute(text('DELETE FROM audit'))
