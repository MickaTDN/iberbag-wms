"""Security integration: challenge CSRF, contextual forms and least-privilege SQL."""
import os
import secrets
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text, func
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker
from iberbag.database import LoginSession, Stock, Audit, PhysicalLocationClaim, Warehouse
from iberbag import contexts, receiving, transferring
from iberbag.warehouse_settings import add_corridor
from iberbag.security import get_session
from conftest import login_data
from test_receiving import warehouse
from test_transfer import pg_databases


def scoped(engine, context_id=1):
    return Session(engine, info={'ddn_context':context_id})


def new_context(engine, actor):
    with scoped(engine) as db:
        return contexts.create_client(db,actor,'Segurança fictícia '+secrets.token_hex(4),2,
                                      'Área fictícia '+secrets.token_hex(4),True)


def web_session_token(client, engine):
    with scoped(engine) as db:
        return get_session(db,client.cookies.get('wms_session')).csrf_token


def test_login_csrf_preserves_context_and_blocks_external_and_stale_forms(warehouse,monkeypatch):
    import iberbag.app as web
    engine,accounts=warehouse;other=new_context(engine,accounts['admin'][0])
    monkeypatch.setattr(web,'SessionLocal',sessionmaker(bind=engine,expire_on_commit=False))
    creds={'username':'admin','password':accounts['admin'][1]}
    with TestClient(web.app) as client:
        client.cookies.set('ddn_context',str(other))
        assert client.post('/login',data=creds).status_code==403
        data=login_data(client,creds)
        assert client.post('/login',data=data,headers={'Origin':'https://external.invalid'}).status_code==403
        with scoped(engine) as db:assert db.scalar(select(func.count()).select_from(LoginSession))==0
        assert client.post('/login',data=login_data(client,creds),headers={
            'Origin':'http://testserver','Referer':'http://testserver/login'},follow_redirects=False).status_code==303
        assert 'DDN WMS' in client.get('/stock').text
        assert client.cookies.get('ddn_context')==str(other)
        csrf=web_session_token(client,engine)
        assert client.post('/settings/warehouses',data={'csrf':csrf,'context_id':1,'name':'WRONG'}).status_code==409
        assert client.post('/settings/warehouses',data={'csrf':csrf,'context_id':other,'name':'ARMAZÉM FICTÍCIO'}).status_code==200
        with scoped(engine) as db:
            assert not db.scalar(select(Warehouse.id).where(Warehouse.name=='WRONG'))
            assert db.scalar(select(Warehouse.id).where(Warehouse.name=='ARMAZÉM FICTÍCIO'))


def test_secure_login_challenge_and_operator_admin_denial_in_other_context(warehouse,monkeypatch):
    import iberbag.app as web
    engine,accounts=warehouse;other=new_context(engine,accounts['admin'][0])
    monkeypatch.setattr(web,'SessionLocal',sessionmaker(bind=engine,expire_on_commit=False))
    monkeypatch.setattr(web,'COOKIE_SECURE',True)
    import iberbag.context_routes as context_routes
    monkeypatch.setattr(context_routes,'COOKIE_SECURE',True)
    with TestClient(web.app,base_url='https://testserver') as client:
        client.cookies.set('ddn_context',str(other))
        response=client.get('/login')
        cookie=response.headers['set-cookie']
        assert '__Host-wms_login=' in cookie and 'Secure' in cookie and 'HttpOnly' in cookie and 'Path=/' in cookie
        creds={'username':'operador','password':accounts['operador'][1]}
        assert client.post('/login',data=login_data(client,creds),headers={'Origin':'https://testserver'},follow_redirects=False).status_code==303
        csrf=web_session_token(client,engine)
        assert client.get('/settings/clients').status_code==403
        assert client.post('/settings/warehouses',data={'csrf':csrf,'context_id':other,'name':'FORBIDDEN'}).status_code==403
        response=client.post('/context',data={'csrf':csrf,'context_id':other,'selected_context':1},follow_redirects=False)
        assert response.status_code==303 and 'Secure' in response.headers['set-cookie']
        with scoped(engine) as db:assert not db.scalar(select(Warehouse.id).where(Warehouse.name=='FORBIDDEN'))


def test_restricted_runtime_can_write_contextual_stock_but_not_physical_claims_or_history(warehouse):
    from iberbag.privileges import grant_runtime,validate_runtime,IMMUTABLE
    from psycopg import sql
    engine,accounts=warehouse;actor=accounts['admin'][0];other=new_context(engine,actor)
    role='runtime_'+secrets.token_hex(6);password=secrets.token_urlsafe(32)
    admin=create_engine(make_url(os.environ['WMS_TEST_POSTGRES_URL']).set(database=engine.url.database))
    runtime=None
    try:
        with admin.begin() as connection:
            connection.exec_driver_sql(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role),sql.Literal(password)).as_string())
        with engine.begin() as connection:grant_runtime(connection,role,True)
        runtime=create_engine(engine.url.set(username=role,password=password))
        with runtime.connect() as connection:
            validate_runtime(connection)
            for table in IMMUTABLE:
                for privilege in ('UPDATE','DELETE','TRUNCATE'):
                    assert not connection.scalar(text('SELECT has_table_privilege(:r,:t,:p)'),{'r':role,'t':table,'p':privilege})
        with scoped(runtime,other) as db:
            add_corridor(db,'admin','T',1,1)
            receipt=receiving.save_receipt(db,actor,secrets.token_hex(16),'FICTÍCIA','2020-01-01',[('ART','LOT','2')])
            receiving.confirm_receipt(db,actor,receipt,1)
            from iberbag.database import ReceiptLine
            line=db.scalar(select(ReceiptLine.id).where(ReceiptLine.receipt_id==receipt))
            putaway=receiving.review_putaway(db,actor,secrets.token_hex(16),line,'Chão','2')
            receiving.confirm_putaway(db,actor,putaway)
            stock=db.scalar(select(Stock.id))
            move=transferring.review_transfer(db,actor,secrets.token_hex(16),stock,'T101','1')
            transferring.confirm_transfer(db,actor,move)
            assert db.scalar(select(func.sum(Stock.quantidade)))==2
        with scoped(runtime) as db:assert db.scalar(select(func.count()).select_from(Stock))==0
        for statement in ('DELETE FROM audit','TRUNCATE audit','UPDATE physical_location_claims SET context_id=context_id',
                          'DELETE FROM physical_location_claims','TRUNCATE physical_location_claims',
                          'CREATE TABLE forbidden(id int)','ALTER TABLE audit DISABLE TRIGGER ALL'):
            with pytest.raises(DBAPIError),runtime.begin() as connection:connection.execute(text(statement))
        # Effective grants from another administrative source must be caught too.
        with engine.begin() as connection:connection.exec_driver_sql(sql.SQL('GRANT DELETE ON physical_location_claims TO {}').format(sql.Identifier(role)).as_string())
        with runtime.connect() as connection,pytest.raises(ValueError,match='atribuições físicas'):validate_runtime(connection)
    finally:
        if runtime:runtime.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)).as_string())
            connection.exec_driver_sql(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)).as_string())
        admin.dispose()
