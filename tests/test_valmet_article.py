"""Unknown articles are not invented and do not hide operationally known goods."""
import secrets
import json
import pytest
from sqlalchemy import select,func
from fastapi.testclient import TestClient
from iberbag import valmet as v
from iberbag.database import ValmetUnit,ValmetItem,ValmetIdentification,Receipt
from test_valmet import valmet,row,entry
from test_receiving import warehouse
from test_transfer import pg_databases
from test_ddn_contexts import scoped
from test_security_regressions import restricted_web

@pytest.mark.parametrize('kind',list(v.KINDS))
def test_known_lot_without_article_available_by_category(valmet,kind):
    engine,accounts=valmet
    grouped=kind in v.GROUPED
    data=row(kind,None if grouped else 'MANUAL',physical_quantity=1,content_total=None if grouped or kind in ('caixas','vazias') else 2,
        items=[('', 'KNOWN-LOT', '1' if grouped else '2')],pending=True)
    if kind=='vasilhame':data['brand']='Filva'
    entry(engine,accounts['operador'][0],[data])
    with scoped(engine,2) as db:
        records,count=v.stock(db,{'lot':'KNOWN-LOT'})
        assert count==1 and records[0][3]==1
        assert records[0][2][0].article is None
        assert db.scalar(select(func.count()).select_from(ValmetIdentification))==1
        assert v.stock(db,{'article':'UNKNOWN'})[1]==0
        if not grouped:assert v.stock(db,{'number':'MANUAL'})[1]==1
    with scoped(engine,1) as db:assert v.stock(db,{'lot':'KNOWN-LOT'})[1]==0

def test_incomplete_lot_stays_physical_and_completion_needs_no_fake_article(valmet):
    engine,accounts=valmet;actor=accounts['admin'][0]
    entry(engine,actor,[row(pending=True,items=[('','','5')])])
    with scoped(engine,2) as db:
        unit=db.scalar(select(ValmetUnit))
        assert v.stock(db)[0][0][3]==0 and unit.physical_quantity==1
        v.identify(db,actor,unit.id,secrets.token_hex(16),[('','PROVEN-LOT','5')],5,'Identificação comprovada por lote e N.º Armazém')
        assert v.stock(db,{'lot':'PROVEN-LOT','number':'MANUAL-01'})[0][0][3]==1
        assert db.scalar(select(func.count()).select_from(ValmetUnit))==1
        assert db.scalar(select(func.count()).select_from(Receipt))==1

@pytest.mark.parametrize('change',[{'number':''},{'items':[('','','5')]},{'content_total':6},{'physical_quantity':2}])
def test_article_exception_does_not_relax_physical_identity_or_totals(valmet,change):
    engine,accounts=valmet
    data=row(items=[('','LOT','5')]);data.update(change)
    with pytest.raises(v.ReceivingError):entry(engine,accounts['admin'][0],[data])
    with scoped(engine,2) as db:assert v.stock(db)[1]==0

def test_unknown_article_http_authentication_and_csrf(restricted_web):
    app,owner,runtime,role,accounts,tokens=restricted_web
    from iberbag.warehouse_settings import add_location
    with scoped(owner,2) as db:add_location(db,'admin','A2')
    with TestClient(app) as client:
        assert client.get('/valmet/stock').status_code==403
        client.cookies.set('wms_session',tokens['operador'][0]);client.cookies.set('ddn_context','2')
        data={'csrf':tokens['operador'][1],'context_id':2,'request_key':secrets.token_hex(16),'received_on':'2027-01-01',
            'u0_kind':'rolos','u0_number':'HTTP-N','u0_physical_quantity':'1','u0_local':'A2','u0_content_total':'5',
            'u0_lot0':'LOT-HTTP','u0_amount0':'5','u0_pending':'yes'}
        response=client.post('/valmet/entries/review',data=data,follow_redirects=False)
        assert response.status_code==303;path=response.headers['location']
        assert client.post(path+'/confirm',data={'csrf':'invalid','context_id':2,'version':1}).status_code==403
        assert client.post(path+'/confirm',data={'csrf':tokens['operador'][1],'context_id':2,'version':1},follow_redirects=False).status_code==303
        text=client.get('/valmet/stock?lot=LOT-HTTP&number=HTTP-N').text
        assert 'HTTP-N' in text and 'Artigo desconhecido' in text
        assert 'Aguarda identificação Valmet</strong>' not in text

def test_parser_rule_runs_on_windows_sqlite_too():
    data=json.loads(v.payload('2027-01-01','',[row(items=[('','L','5')],pending=True)]))['rows'][0]
    assert data['pending'] is False and data['items'][0][0] is None
    insufficient=json.loads(v.payload('2027-01-01','',[row(items=[('','','5')],pending=True)]))['rows'][0]
    assert insufficient['pending'] is True

def test_article_migration_and_encrypted_recovery_preserve_history(pg_databases,tmp_path):
    from sqlalchemy.orm import Session
    from alembic import command
    from iberbag.database import User
    from iberbag.security import set_password
    from iberbag.migrations import config,REVISION
    from iberbag.schema_upgrade import apply_upgrade
    from iberbag import backup,transfer
    from iberbag.warehouse_settings import add_location,set_capacity
    from legacy_catalogue import seed_legacy_catalogue
    from test_transfer import docker_client
    from conftest import BACKUP_PASSWORD
    engine=pg_databases(restricted=True);target=pg_databases(restricted=True)
    with engine.begin() as conn:
        cfg=config();cfg.attributes['connection']=conn;command.upgrade(cfg,'ddn_0005')
    with scoped(engine) as db:
        seed_legacy_catalogue(db)
        user=User(username='fictitious',display_name='Fictício',role='admin',active=True)
        set_password(user,secrets.token_urlsafe(24));db.add(user);db.commit();actor=user.id
    with scoped(engine,2) as db:
        add_location(db,'fictitious','A2');set_capacity(db,'fictitious','A2','3')
    entry(engine,actor,[row()])
    with engine.connect() as conn:before=transfer.inventory(transfer.read_data(conn))
    folder=tmp_path/'verified-article'
    backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert backup.restore(target,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'
    assert apply_upgrade(engine,folder)['revision']==REVISION
    with engine.connect() as conn:assert transfer.inventory(transfer.read_data(conn))==before
    entry(engine,actor,[row(number='UNKNOWN-ARTICLE',items=[('','NEW-LOT','5')])])
    with scoped(engine,2) as db:assert v.stock(db,{'lot':'NEW-LOT','number':'UNKNOWN-ARTICLE'})[0][0][3]==1
