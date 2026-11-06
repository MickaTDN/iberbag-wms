"""PR17 corrections, fictitious goods only; reuse transactional Valmet fixtures."""
import json
import secrets
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select,func,text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from alembic import command
from iberbag import valmet as v,backup,transfer
from iberbag.database import ValmetUnit,ValmetItem,ValmetEntry,ValmetNumber,Receipt,LoadNumber,User,now_utc
from iberbag.migrations import config,REVISION
from iberbag.schema_upgrade import apply_upgrade
from iberbag.warehouse_settings import set_capacity,add_location,set_location_active,WarehouseError,occupied
from iberbag.security import set_password
from test_valmet import valmet,row,entry
from test_receiving import warehouse
from test_ddn_contexts import scoped
from test_security_regressions import restricted_web
from test_transfer import pg_databases,docker_client
from conftest import BACKUP_PASSWORD

def empty(number='EMPTY',article='',lot='LOT',**kw):
    return row('vazias',number,content_total=None,items=[(article,lot,'')],**kw)

def test_rules_are_identical_on_windows_sqlite_consultation():
    data=json.loads(v.payload('2027-01-01','',[empty(local='A2',pending=True)]))['rows'][0]
    assert data['local']=='Chão' and data['pending'] is False
    assert data['items']==[[None,'LOT',None]]
    lots=[('A','L1','1'),('B','L2','1')]
    assert len(v.composition('paletes',1,None,lots))==2
    with pytest.raises(v.ReceivingError):v.composition('fio',1,None,lots)
    with pytest.raises(v.ReceivingError):v.payload('2027-01-01','',[empty(lot='')])

def test_normal_pallet_many_lots_one_physical_place(valmet):
    engine,accounts=valmet;actor=accounts['operador'][0]
    with scoped(engine,2) as db:set_capacity(db,'admin','A2','1')
    ident=entry(engine,actor,[row('paletes',None,content_total=None,items=[('A','L1','1'),('B','L2','1'),('A','L3','')])])
    with scoped(engine,2) as db:
        units,count=v.stock(db)
        assert count==1 and units[0][0].physical_quantity==1 and units[0][3]==1
        assert len(units[0][2])==3 and sum(q for loc,q in occupied(db,['A2']))==1
        v.confirm_entry(db,actor,ident,1)
        assert v.stock(db,{'lot':'L2'})[1]==1
        assert db.scalar(select(func.count()).select_from(ValmetUnit))==1
    with pytest.raises(v.ReceivingError):entry(engine,actor,[row('paletes',None,content_total=None,items=[('A','L4','1')])])

@pytest.mark.parametrize('kind,physical,total,items',[
    ('paletes',1,None,[('A','L','2')]),
    ('paletes',2,None,[('A','L1','2'),('B','L2','2')]),
    ('fio',1,None,[('A','L1','1'),('A','L2','1')]),
    ('prateleiras',1,None,[('A','L1','1'),('A','L2','1')]),
    ('rolos',1,1,[('A','L1','1'),('A','L2','1')]),
    ('pecas',1,1,[('A','L1','1'),('A','L2','1')]),
])
def test_composition_limits_do_not_change_other_categories(valmet,kind,physical,total,items):
    engine,accounts=valmet
    with pytest.raises(v.ReceivingError):
        entry(engine,accounts['admin'][0],[row(kind,None if kind in v.GROUPED else 'N1',physical_quantity=physical,content_total=total,items=items)])

def test_pending_multilot_pallet_identified_without_new_stock(valmet):
    engine,accounts=valmet;actor=accounts['admin'][0]
    ident=entry(engine,actor,[row('paletes',None,content_total=None,pending=True,items=[('A','','1'),('B','','1')])])
    with scoped(engine,2) as db:
        unit=db.scalar(select(ValmetUnit))
        assert v.stock(db)[0][0][3]==0
        v.identify(db,actor,unit.id,secrets.token_hex(16),[('A','L1','1'),('B','L2','1')],notes='Distribuição manual comprovada na mesma palete')
        assert v.stock(db)[0][0][3]==1
        assert db.scalar(select(func.count()).select_from(ValmetUnit))==1
        assert db.get(ValmetUnit,unit.id).entry_id==ident

@pytest.mark.parametrize('article',['','KNOWN-ARTICLE'])
def test_empty_boxes_lot_number_and_automatic_floor(valmet,article):
    engine,accounts=valmet;actor=accounts['operador'][0]
    ident=entry(engine,actor,[empty(article=article,local='A2',pending=True)])
    with scoped(engine,2) as db:
        data,count=v.stock(db,{'lot':'LOT','number':'EMPTY'})
        assert count==1 and data[0][0].local=='Chão' and data[0][3]==1
        assert data[0][0].number=='EMPTY' and data[0][2][0].article==(article or None)
        assert sum(q for loc,q in occupied(db,['Chão']))==1 and occupied(db,['A2'])==[]
        assert v.stock(db,{'article':'KNOWN-ARTICLE'})[1]==bool(article)
        v.confirm_entry(db,actor,ident,1)
        assert db.scalar(select(func.count()).select_from(ValmetNumber))==1
    with scoped(engine,1) as db:assert v.stock(db,{'number':'EMPTY'})[1]==0
    with pytest.raises(v.ReceivingError):entry(engine,actor,[empty(local='H2')])

@pytest.mark.parametrize('fields',[
    {'items':[]},{'items':[('A','','')]},{'items':[('','','')]},
    {'number':''},{'number':None},{'physical_quantity':2},
])
def test_empty_box_required_identity_not_bypassed_by_pending(valmet,fields):
    engine,accounts=valmet
    data=empty(pending=True);data.update(fields)
    with pytest.raises(v.ReceivingError):entry(engine,accounts['admin'][0],[data])
    with scoped(engine,2) as db:
        assert db.scalar(select(func.count()).select_from(ValmetUnit))==0
        assert db.scalar(select(func.count()).select_from(Receipt))==0

def test_unavailable_floor_rolls_back_entire_receipt(valmet):
    engine,accounts=valmet;actor=accounts['admin'][0]
    with scoped(engine,2) as db:set_location_active(db,'admin','Chão',False)
    ident=entry(engine,actor,[row(),empty()],confirm=False)
    with scoped(engine,2) as db:
        with pytest.raises(v.ReceivingError):v.confirm_entry(db,actor,ident,1)
        for model in (ValmetUnit,ValmetNumber,Receipt,LoadNumber):assert db.scalar(select(func.count()).select_from(model))==0
        assert db.get(ValmetEntry,ident).status=='draft'

def test_database_enforces_empty_floor_and_deferred_lot(valmet):
    engine,accounts=valmet;actor=accounts['admin'][0]
    draft=entry(engine,actor,[empty()],confirm=False)
    with pytest.raises(DBAPIError):
        with scoped(engine,2) as db:
            unit=ValmetUnit(entry_id=draft,kind='vazias',number='RAW',physical_quantity=1,local='A2',supplier='',guide='',notes='')
            db.add(unit);db.flush();db.refresh(unit)
            assert unit.local=='Chão'
            db.commit()  # No lot was inserted: deferred integrity check rejects it.
    with scoped(engine,2) as db:assert db.scalar(select(func.count()).select_from(ValmetNumber))==0

def test_old_draft_without_empty_box_lot_is_revalidated(valmet):
    engine,accounts=valmet;actor=accounts['admin'][0]
    with scoped(engine,2) as db:
        draft=ValmetEntry(request_key=secrets.token_hex(16),created_by=actor,
            payload=json.dumps({'received_on':'2027-01-01','code':'AUTO','rows':[row('vazias','OLD',content_total=None,items=[])]}))
        db.add(draft);db.commit();ident=draft.id
        with pytest.raises(v.ReceivingError):v.confirm_entry(db,actor,ident,1)
        assert db.scalar(select(func.count()).select_from(Receipt))==0

def test_empty_boxes_concurrent_repeated_number(valmet):
    engine,accounts=valmet;actor=accounts['admin'][0]
    drafts=[entry(engine,actor,[empty()],confirm=False) for _ in range(2)]
    def confirm(ident):
        with scoped(engine,2) as db:
            try:v.confirm_entry(db,actor,ident,1);return True
            except v.ReceivingError:return False
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(confirm,drafts))==[False,True]
    with scoped(engine,2) as db:
        assert db.scalar(select(func.sum(ValmetUnit.physical_quantity)))==1
        assert db.scalar(select(ValmetUnit.local))=='Chão'

def test_corrected_boxes_http_permissions_and_search(restricted_web):
    app,owner,runtime,role,accounts,tokens=restricted_web
    with scoped(owner,2) as db:add_location(db,'admin','Chão')
    with TestClient(app) as client:
        client.cookies.set('wms_session',tokens['operador'][0]);client.cookies.set('ddn_context','2')
        data={'csrf':tokens['operador'][1],'context_id':2,'request_key':secrets.token_hex(16),'received_on':'2027-01-01',
            'u0_kind':'vazias','u0_number':'HTTP-BOX','u0_physical_quantity':'1','u0_local':'H2','u0_lot0':'BOX-LOT'}
        response=client.post('/valmet/entries/review',data=data,follow_redirects=False)
        assert response.status_code==303;path=response.headers['location']
        assert client.post(path+'/confirm',data={'csrf':tokens['operador'][1],'context_id':2,'version':1},follow_redirects=False).status_code==303
        assert 'HTTP-BOX' in client.get('/valmet/stock?lot=BOX-LOT&number=HTTP-BOX').text
        assert client.get('/settings/locations').status_code==403
        assert client.post(path+'/confirm',data={'csrf':'invalid','context_id':2,'version':1}).status_code==403
        client.cookies.set('ddn_context','1')
        assert client.get('/valmet/stock?lot=BOX-LOT').status_code==403

def test_upgrade_preserves_confirmed_old_boxes_and_verified_backup(pg_databases,tmp_path,monkeypatch):
    from legacy_catalogue import seed_legacy_catalogue
    engine=pg_databases(restricted=True);restored=pg_databases(restricted=True)
    with engine.begin() as conn:
        cfg=config();cfg.attributes['connection']=conn;command.upgrade(cfg,'ddn_0004')
    with scoped(engine) as db:
        seed_legacy_catalogue(db)
        user=User(username='fictional',display_name='Fictício',role='admin',active=True)
        set_password(user,secrets.token_urlsafe(24));db.add(user);db.commit();actor=user.id
    with scoped(engine,2) as db:add_location(db,'fictional','H2')
    old=json.dumps({'received_on':'2026-01-01','code':'HISTORICAL','rows':[row('vazias','OLD-BOX',local='H2',content_total=None,items=[],pending=False)]})
    # Only the historical fixture uses old application semantics; real ddn_0004
    # SQL guards are still active throughout. No historical trigger is disabled.
    with monkeypatch.context() as patch:
        patch.setattr(v,'payload',lambda *a,**kw:old)
        patch.setattr(v,'composition',lambda *a,**kw:[])
        ident=entry(engine,actor,[empty()])
    with engine.connect() as conn:before=transfer.inventory(transfer.read_data(conn))
    folder=tmp_path/'verified-old-boxes'
    backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert backup.restore(restored,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'
    assert apply_upgrade(engine,folder)['revision']==REVISION
    with engine.connect() as conn:assert transfer.inventory(transfer.read_data(conn))==before
    with scoped(engine,2) as db:
        assert db.scalar(select(ValmetUnit.local))=='H2'
        assert db.get(ValmetEntry,ident).status=='confirmed'
