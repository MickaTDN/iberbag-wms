"""Fictitious Valmet goods; every operational test uses disposable PostgreSQL 17."""
import json
import re
import secrets
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select,func,text
from sqlalchemy.orm import Session,sessionmaker
from sqlalchemy.exc import DBAPIError
from fastapi.testclient import TestClient
from alembic import command
from iberbag import valmet as v,backup,transfer,receiving
from iberbag.database import (ValmetEntry,ValmetUnit,ValmetIdentification,ValmetItem,ValmetNumber,
    StorageContext,Receipt,LoadNumber,Audit,Location)
from iberbag.warehouse_settings import add_location,set_capacity,remove_location,rename_location,set_location_active,WarehouseError
from iberbag.migrations import config,REVISION
from iberbag.schema_upgrade import apply_upgrade
from test_receiving import warehouse
from test_transfer import pg_databases,docker_client
from test_ddn_contexts import scoped
from test_security_regressions import restricted_web
from conftest import BACKUP_PASSWORD,login_data

@pytest.fixture
def valmet(warehouse):
    engine,accounts=warehouse
    with scoped(engine,2) as db:
        add_location(db,'admin','Chão')
        for code in ('A2','H2'):
            add_location(db,'admin',code);set_capacity(db,'admin',code,'10')
    return engine,accounts

def row(kind='rolos',number='MANUAL-01',**kw):
    result=dict(kind=kind,number=number,physical_quantity=1,local='A2',content_total=5,
        supplier='FORNECEDOR FICTÍCIO',guide='GUIA FICTÍCIA',notes='VALMET 2 — nota livre',
        items=[('ART','LOT','2'),('ART','LOT','3')])
    result.update(kw);return result

def entry(engine,actor,rows,scope=2,key=None,confirm=True):
    with scoped(engine,scope) as db:
        ident=v.save_entry(db,actor,key or secrets.token_hex(16),'2027-01-02','',rows)
        if confirm:v.confirm_entry(db,actor,ident,1)
        return ident

def test_valmet_composition_counts_physical_units_once(valmet):
    engine,accounts=valmet;actor=accounts['operador'][0]
    ident=entry(engine,actor,[row(),row(number='MANUAL-02'),
        row('caixas','CAIXA-1',content_total=None,items=[('A','L1',''),('B','L2','')]),
        row('pecas','PECAS-1',content_total=12,items=[('A','L1','5'),('A','L2','7')])])
    with scoped(engine,2) as db:
        records,count=v.stock(db)
        assert count==4 and sum(r[0].physical_quantity for r in records)==4
        assert sum(r[3] for r in records)==4
        assert len(records[0][2])==2 and sum(i.quantity for i in records[0][2])==5
        assert len(v.stock(db,{'lot':'LOT'})[0])==2
        assert v.stock(db,{'article':'A','lot':'L2'})[1]==1  # Peças A/L2 only; not the A/L1 + B/L2 box.
        assert len(v.stock(db,{'notes':'VALMET 2'})[0])==4
        receipt=db.get(Receipt,db.get(ValmetEntry,ident).receipt_id)
        assert receipt.code=='27-001' and receipt.receiver=='operador'
        assert db.scalar(select(func.count()).select_from(ValmetNumber))==4
        v.confirm_entry(db,actor,ident,1)
        assert db.scalar(select(func.count()).select_from(ValmetUnit))==4

def test_valmet_pending_identification_keeps_original_units_and_locations(valmet):
    engine,accounts=valmet;actor=accounts['operador'][0]
    ident=entry(engine,actor,[row('paletes',None,physical_quantity=2,content_total=None,pending=True,items=[('KNOWN','','1')]),
        row('paletes',None,physical_quantity=3,local='H2',content_total=None,pending=True,items=[])])
    with scoped(engine,2) as db:
        before=db.scalars(select(ValmetUnit).order_by(ValmetUnit.id)).all()
        ids=[u.id for u in before];locations=[u.local for u in before]
        assert all(r[3]==0 for r in v.stock(db)[0])
        assert v.stock(db,{'article':'KNOWN'})[1]==1
        with pytest.raises(v.ReceivingError):v.identify(db,actor,ids[0],secrets.token_hex(16),[('A','L','3')],notes='RESPOSTA FICTÍCIA')
        key=secrets.token_hex(16)
        v.identify(db,actor,ids[0],key,[('A','L1','1'),('B','L2','1')],notes='RESPOSTA FICTÍCIA: distribuição comprovada A2')
        v.identify(db,actor,ids[0],key,[('A','L1','1'),('B','L2','1')],notes='RESPOSTA FICTÍCIA: distribuição comprovada A2')
        assert sum(r[3] for r in v.stock(db)[0])==2
        v.identify(db,actor,ids[1],secrets.token_hex(16),[('C','L3','3')],notes='RESPOSTA FICTÍCIA: conferido H2')
        assert sum(r[3] for r in v.stock(db)[0])==5
        assert v.stock(db,{'notes':'distribuição comprovada A2'})[1]==1
        assert [u.id for u in db.scalars(select(ValmetUnit).order_by(ValmetUnit.id))]==ids
        assert [u.local for u in db.scalars(select(ValmetUnit).order_by(ValmetUnit.id))]==locations
        assert db.scalar(select(func.count()).select_from(Receipt))==1
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action=='VALMET_IDENTIFICAR'))==2
        with pytest.raises(v.ReceivingError):v.identify(db,actor,ids[1],secrets.token_hex(16),[('C','L3','3')],notes='OUTRA')

def test_valmet_m2_vasilhame_and_unknown_location(valmet):
    engine,accounts=valmet
    entry(engine,accounts['admin'][0],[row('paletes',None,physical_quantity=2,content_total=None,material_m2='123,456',items=[('A','L','2')]),
        row('vasilhame',None,physical_quantity=4,content_total=None,brand='Filva',local='',items=[])])
    with scoped(engine,2) as db:
        records,count=v.stock(db)
        assert str(records[0][0].material_m2)=='123.456'
        assert sum(r[0].physical_quantity for r in records)==6
        assert v.stock(db,{'local':'Por localizar'})[1]==1
        assert records[1][0].brand=='Filva' and records[1][3]==0

@pytest.mark.parametrize('bad',[
    dict(physical_quantity=2),dict(number=''),dict(content_total=6),dict(material_m2='1.2'),
    dict(items=[]),dict(items=[('A','','5')]),dict(items=[('A','L','-1')]),
    dict(local='Z999'),dict(physical_quantity=0)])
def test_valmet_invalid_entries_rollback(valmet,bad):
    engine,accounts=valmet
    with pytest.raises(v.ReceivingError):entry(engine,accounts['admin'][0],[row(**bad)])
    with scoped(engine,2) as db:
        assert db.scalar(select(func.count()).select_from(ValmetUnit))==0
        assert db.scalar(select(func.count()).select_from(Receipt))==0
        assert db.scalar(select(func.count()).select_from(LoadNumber))==0

def test_valmet_ids_capacity_and_confirmations_are_concurrent_safe(valmet):
    engine,accounts=valmet;actor=accounts['admin'][0]
    with scoped(engine,2) as db:set_capacity(db,'admin','A2','1')
    ids=[entry(engine,actor,[row(number=number)],confirm=False) for number in ('N-1','N-2')]
    def confirm(ident):
        try:
            with scoped(engine,2) as db:v.confirm_entry(db,actor,ident,1)
            return True
        except v.ReceivingError:return False
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(confirm,ids))==[False,True]
    with scoped(engine,2) as db:
        assert db.scalar(select(func.sum(ValmetUnit.physical_quantity)))==1
        assert db.scalar(select(func.count()).select_from(Receipt))==1
        assert db.scalar(select(func.count()).select_from(LoadNumber))==1
        with pytest.raises(WarehouseError):remove_location(db,'admin','A2')
        with pytest.raises(WarehouseError):set_location_active(db,'admin','A2',False)
        with pytest.raises(WarehouseError):rename_location(db,'admin','A2','H2')
        number=db.scalar(select(ValmetUnit.number))
    with pytest.raises(v.ReceivingError):entry(engine,actor,[row(number=number,local='H2')])
    for sql in ('DELETE FROM valmet_numbers','UPDATE valmet_units SET physical_quantity=2','TRUNCATE valmet_items'):
        with engine.begin() as conn,pytest.raises(DBAPIError):conn.execute(text(sql))

def test_valmet_owner_wide_manual_ids_and_context_isolation(valmet):
    engine,accounts=valmet;actor=accounts['admin'][0]
    entry(engine,actor,[row()])
    with Session(engine) as db:
        ctx=StorageContext(client_id=2,warehouse_id=2,area='FICTÍCIO',active=True);db.add(ctx);db.commit();other=ctx.id
    with scoped(engine,other) as db:add_location(db,'admin','H2')
    with pytest.raises(v.ReceivingError):entry(engine,actor,[row(local='H2')],scope=other)
    for scope in (1,3,4,5):
        with scoped(engine,scope) as db:
            assert v.stock(db)[1]==0
            with pytest.raises(v.ReceivingError):v.save_entry(db,actor,secrets.token_hex(16),'2027-01-01','',[row()])
    with scoped(engine,2) as db,pytest.raises(WarehouseError):add_location(db,'admin','C214')
    with scoped(engine,2) as db,pytest.raises(receiving.ReceivingError):
        receiving.save_receipt(db,actor,secrets.token_hex(16),'','2027-01-01',[('A','L','1')])
    from iberbag.database import Client
    with Session(engine) as db:
        db.get(Client,2).pallet_operations=True;db.commit()
    with scoped(engine,2) as db,pytest.raises(receiving.ReceivingError):
        receiving.save_receipt(db,actor,secrets.token_hex(16),'','2027-01-01',[('A','L','1')])

def test_valmet_backup_restore_and_upgrade_preserve_units(valmet,pg_databases,tmp_path):
    engine,accounts=valmet;actor=accounts['admin'][0]
    entry(engine,actor,[row(),row('paletes',None,physical_quantity=2,content_total=None,pending=True,items=[]),
        row('paletes',None,physical_quantity=2,content_total=None,material_m2='123.456',items=[('A','L','2')])])
    with engine.connect() as conn:expected=transfer.inventory(transfer.read_data(conn))
    folder=tmp_path/'valmet-backup';target=pg_databases(restricted=True)
    backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert backup.restore(target,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'
    with target.connect() as conn:assert transfer.inventory(transfer.read_data(conn))==expected
    with scoped(target,2) as db:
        assert v.stock(db)[1]==3
        assert str(db.scalar(select(ValmetUnit.material_m2).where(ValmetUnit.material_m2.is_not(None))))=='123.456'
    with pytest.raises(v.ReceivingError):entry(target,actor,[row()])

def test_valmet_security_head_migration_and_recovery(pg_databases,tmp_path):
    engine=pg_databases(restricted=True);target=pg_databases(restricted=True)
    with engine.begin() as conn:
        cfg=config();cfg.attributes['connection']=conn;command.upgrade(cfg,'ddn_0003')
        conn.execute(text("INSERT INTO cargas(context_id,codigo,entrada) VALUES(1,'HISTÓRICA',NULL)"))
    from legacy_catalogue import seed_legacy_catalogue
    with scoped(engine) as db:seed_legacy_catalogue(db)
    with engine.connect() as conn:
        before=transfer.inventory(transfer.read_data(conn))
    folder=tmp_path/'old'
    backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    backup.restore(target,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert apply_upgrade(engine,folder)['revision']==REVISION
    with engine.connect() as conn:assert transfer.inventory(transfer.read_data(conn))==before

def test_valmet_http_roles_csrf_and_restricted_runtime(restricted_web):
    app,owner,runtime,role,accounts,tokens=restricted_web
    with scoped(owner,2) as db:
        add_location(db,'admin','A2');add_location(db,'admin','Chão')
    with TestClient(app) as client:
        client.cookies.set('wms_session',tokens['operador'][0]);client.cookies.set('ddn_context','2')
        assert client.get('/valmet/stock').status_code==200
        assert client.get('/settings/locations').status_code==403
        assert client.post('/valmet/entries/review',data={'csrf':'bad','context_id':2}).status_code==403
        data={'csrf':tokens['operador'][1],'context_id':2,'request_key':secrets.token_hex(16),'received_on':'2027-01-02',
            'u0_kind':'rolos','u0_number':'WEB-1','u0_physical_quantity':'1','u0_local':'A2','u0_content_total':'2',
            'u0_article0':'A','u0_lot0':'L','u0_amount0':'2'}
        response=client.post('/valmet/entries/review',data=data,follow_redirects=False)
        assert response.status_code==303
        path=response.headers['location'];assert client.get(path).status_code==200
        assert client.post(path+'/confirm',data={'csrf':tokens['operador'][1],'context_id':2,'version':1},follow_redirects=False).status_code==303
        assert 'WEB-1' in client.get('/valmet/stock?number=WEB-1').text
        pending=entry(runtime,accounts['operador'][0],[row('paletes',None,physical_quantity=2,content_total=None,local='Chão',pending=True,items=[])])
        with scoped(runtime,2) as db:unit=db.scalar(select(ValmetUnit.id).where(ValmetUnit.entry_id==pending))
        form=client.get(f'/valmet/units/{unit}/identify')
        key=re.search('name="request_key" value="([^"]+)"',form.text)[1]
        result=client.post(f'/valmet/units/{unit}/identify',data={'csrf':tokens['operador'][1],'context_id':2,
            'request_key':key,'article0':'IDENTIFICADO','lot0':'LOTE-NOVO','amount0':'2','notes':'RESPOSTA VALMET FICTÍCIA'},follow_redirects=False)
        assert result.status_code==303
        assert 'IDENTIFICADO' in client.get(f'/valmet/entries/{pending}').text
        client.cookies.set('ddn_context','1')
        assert client.get(path).status_code==403
        assert client.post(path+'/confirm',data={'csrf':tokens['operador'][1],'context_id':2,'version':1}).status_code==409
    with runtime.begin() as conn,pytest.raises(DBAPIError):conn.execute(text('DELETE FROM valmet_numbers'))
    with runtime.begin() as conn,pytest.raises(DBAPIError):conn.execute(text('INSERT INTO valmet_owners(client_id) VALUES(1)'))
    from iberbag.privileges import validate_runtime
    with owner.begin() as conn:conn.execute(text('GRANT INSERT ON valmet_owners TO PUBLIC'))
    with runtime.connect() as conn,pytest.raises(ValueError,match='manutenção'):validate_runtime(conn)


@pytest.mark.parametrize('kind',['fio','prateleiras','vazias'])
def test_valmet_remaining_categories_and_quantities(valmet,kind):
    engine,accounts=valmet
    numbered=kind=='vazias'
    entry(engine,accounts['operador'][0],[row(kind,'EMPTY-01' if numbered else None,
        physical_quantity=1 if numbered else 3,content_total=None,items=[('','EMPTY-LOT','')] if numbered else [('A','L','3')])])
    with scoped(engine,2) as db:assert v.stock(db)[0][0][0].physical_quantity==(1 if numbered else 3)


def test_valmet_identification_race_and_audit(valmet):
    engine,accounts=valmet;actor=accounts['operador'][0]
    entry(engine,actor,[row(pending=True,items=[],content_total=5)])
    with scoped(engine,2) as db:unit=db.scalar(select(ValmetUnit.id))
    def identify(_):
        with scoped(engine,2) as db:
            try:v.identify(db,actor,unit,secrets.token_hex(16),[('A','L','5')],5,'FICTÍCIO COM PROVA');return True
            except v.ReceivingError:return False
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(identify,range(2)))==[False,True]
    with scoped(engine,2) as db:
        assert db.scalar(select(func.count()).select_from(ValmetIdentification))==1
        assert db.scalar(select(func.sum(ValmetUnit.physical_quantity)))==1


def test_valmet_error_after_physical_insert_rolls_back(valmet,monkeypatch):
    engine,accounts=valmet;actor=accounts['admin'][0]
    ident=entry(engine,actor,[row()],confirm=False)
    def fail(*args,**kwargs):raise RuntimeError('Fictitious injected error')
    monkeypatch.setattr(v,'add_identification',fail)
    with scoped(engine,2) as db:
        with pytest.raises(RuntimeError):v.confirm_entry(db,actor,ident,1)
        assert db.get(ValmetEntry,ident).status=='draft'
        for model in (ValmetUnit,ValmetNumber,Receipt,LoadNumber):
            assert db.scalar(select(func.count()).select_from(model))==0


def test_valmet_sqlite_upgrade_is_read_only_for_operations(tmp_path):
    from sqlalchemy import create_engine,inspect
    engine=create_engine('sqlite:///'+str(tmp_path/'fictional.sqlite'))
    try:
        with engine.begin() as conn:
            cfg=config();cfg.attributes['connection']=conn;command.upgrade(cfg,'ddn_0003')
            conn.execute(text("INSERT INTO cargas(context_id,codigo) VALUES (1,'FICTÍCIA')"))
            before=conn.execute(text('SELECT * FROM cargas')).all()
            command.upgrade(cfg,'head')
            assert conn.execute(text('SELECT * FROM cargas')).all()==before
            assert set(transfer.VALMET_TABLES)<=set(inspect(conn).get_table_names())
        with Session(engine) as db,pytest.raises(v.ReceivingError,match='PostgreSQL'):
            v.save_entry(db,1,secrets.token_hex(16),'2027-01-01','',[row()])
    finally:engine.dispose()
