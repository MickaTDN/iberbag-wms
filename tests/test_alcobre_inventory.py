"""Fictitious individual bobines; no company documents or production databases."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from io import BytesIO
import json,secrets
from threading import Barrier
import pytest
from sqlalchemy import select,func,text
from sqlalchemy.exc import DBAPIError
from fastapi.testclient import TestClient
from pypdf import PdfReader
from iberbag import alcobre as a,alcobre_inventory as s,alcobre_dispatch as dispatch,database as m,transfer,backup
from iberbag.alcobre_inventory_pdf import render_pdf
from test_receiving import warehouse
from test_security_regressions import restricted_web
from test_ddn_contexts import scoped
from test_transfer import pg_databases,docker_client
from test_alcobre_dispatch import seed,source,review as dispatch_review,prepare,confirm as ship,resolution,snapshot
from test_alcobre import draft as incoming,review as incoming_review,confirm as receive,row
from conftest import BACKUP_PASSWORD


def create(engine,actor,locations=None,mode='partial'):
    with scoped(engine,5) as db:return s.create(db,actor,secrets.token_hex(16),'Conferência inteiramente fictícia',mode,locations if locations is not None else (['FICT-ALC-1'] if mode=='partial' else []))

def record(engine,actor,ident,result,number=None,local=None,entry=None,resolves=None):
    with scoped(engine,5) as db:
        return s.record(db,actor,ident,db.get(m.AlcobreInventory,ident).version,result,'Prova física fictícia recebida no escritório',number,local,entry,resolves)

def review(engine,actor,ident):
    with scoped(engine,5) as db:
        inv=db.get(m.AlcobreInventory,ident);rid=s.review(db,actor,ident,inv.version,secrets.token_hex(16),'Aprovação administrativa com prova fictícia');return rid,db.get(m.AlcobreInventoryReview,rid).digest

def confirm(engine,actor,ident,rev=None,key=None):
    rid,digest=rev or review(engine,actor,ident)
    with scoped(engine,5) as db:return s.confirm(db,actor,ident,rid,digest,key or secrets.token_hex(16),True)

def test_alcobre_inventory_missing_recovery_and_position_correction_keep_identity(warehouse):
    engine,u=warehouse;actor=u['admin'][0];seed(engine,actor)
    inv=create(engine,actor);before=snapshot(engine)
    record(engine,actor,inv,'missing','000101')
    with scoped(engine,5) as db:
        assert a.stock(db,state='on_hand')[1]==4 and a.stock(db,state='missing')[1]==0
        raw=s.summary(db,db.get(m.AlcobreInventory,inv));assert raw['rows'][0]['action']=='missing' and raw['rows'][0]['recorded_quantity']==1 and raw['rows'][0]['physical_quantity']==0 and raw['rows'][0]['difference']==-1 and not raw['blocking']
    rev=review(engine,actor,inv);key=secrets.token_hex(16);op=confirm(engine,actor,inv,rev,key)
    assert confirm(engine,actor,inv,rev,key)==op
    from iberbag.warehouse_settings import occupied
    with scoped(engine,5) as db:
        assert a.stock(db,'000101',state='missing')[1]==1 and a.stock(db,state='on_hand')[1]==3
        assert not occupied(db,['FICT-ALC-1'])
        original=db.scalar(select(m.AlcobrePlacement).join(m.AlcobreBobine,m.AlcobreBobine.id==m.AlcobrePlacement.bobine_id).where(m.AlcobreBobine.number=='000101'));original_id=original.id
        assert original.local=='FICT-ALC-1'
    other=incoming(engine,actor);receive(engine,actor,incoming_review(engine,actor,other,[row('FICT-REPLACEMENT','FICT-ALC-1')]))
    recovery=create(engine,actor,['FICT-ALC-1']);record(engine,actor,recovery,'found','000101','FICT-ALC-1');record(engine,actor,recovery,'found','FICT-REPLACEMENT','FICT-ALC-1')
    with pytest.raises(a.ReceivingError):review(engine,actor,recovery)
    # A proved, free configured position is required; never overwrite another unit.
    from test_alcobre import positions
    positions(engine,actor,4)
    record(engine,actor,recovery,'elsewhere','000101','FICT-ALC-4');confirm(engine,actor,recovery)
    with scoped(engine,5) as db:
        units,total,_=a.stock(db,'000101',state='available');assert total==1 and units[0][1].local=='FICT-ALC-4'
        assert db.get(m.AlcobrePlacement,original_id).local=='FICT-ALC-1'
        assert db.scalar(select(func.count()).select_from(m.AlcobreBobine))==5
        assert sum(q for _,q in occupied(db,['FICT-ALC-4']))==1
    move=create(engine,actor,['FICT-ALC-4']);record(engine,actor,move,'elsewhere','000101','FICT-ALC-1')
    with pytest.raises(a.ReceivingError):review(engine,actor,move)
    # Restore-to-a-different-position is a correction, never a new receipt or GT.
    docs=[]
    with scoped(engine,5) as db:
        assert all(e.operation_id is None for e in db.scalars(select(m.AlcobreDispatchEvent).where(m.AlcobreDispatchEvent.control_operation_id.is_not(None))))
        docs=[bytes(d.pdf) for d in db.scalars(select(m.AlcobreInventoryDocument))]
    assert docs and before!=snapshot(engine)


def test_alcobre_inventory_blocked_found_and_stock_dispatch_use_corrected_location(warehouse):
    engine,u=warehouse;actor=u['admin'][0];seed(engine,actor)
    guide=source(engine,actor,['000101']);dispatch_review(engine,actor,guide);prepare(engine,actor,guide);ship(engine,actor,guide,exceptions={'000101':resolution('not_found')})
    inv=create(engine,actor);record(engine,actor,inv,'found','000101','FICT-ALC-1');confirm(engine,actor,inv)
    with scoped(engine,5) as db:assert a.stock(db,'000101',state='available')[1]==1
    from test_alcobre import positions
    positions(engine,actor,4)
    move=create(engine,actor);record(engine,actor,move,'elsewhere','000101','FICT-ALC-4');confirm(engine,actor,move)
    nextguide=source(engine,actor,['000101']);rid,_=dispatch_review(engine,actor,nextguide)
    with scoped(engine,5) as db:assert json.loads(db.get(m.AlcobreDispatchRevision,rid).payload)['items'][0]['local']=='FICT-ALC-4'
    prepare(engine,actor,nextguide);ship(engine,actor,nextguide,['000101'])
    with scoped(engine,5) as db:
        assert a.stock(db,'000101',state='shipped')[1]==1
        from iberbag.warehouse_settings import occupied
        assert not occupied(db,['FICT-ALC-1','FICT-ALC-4'])


def test_alcobre_inventory_complete_unverified_empty_pending_are_distinct(warehouse):
    engine,u=warehouse;actor=u['admin'][0];entry,pending=seed(engine,actor)
    from test_alcobre import positions
    positions(engine,actor,4)
    inv=create(engine,actor,mode='complete')
    with scoped(engine,5) as db:
        raw=s.summary(db,db.get(m.AlcobreInventory,inv));assert len(raw['rows'])==4 and len(raw['locations'])==4 and raw['blocking']
        assert all(r['result']=='unverified' and r['physical_quantity'] is None and r['difference'] is None for r in raw['rows'])
    with pytest.raises(a.ReceivingError):review(engine,actor,inv)
    for n,loc in [('000101','FICT-ALC-1'),('000102','FICT-ALC-2'),('000103','FICT-ALC-3')]:record(engine,actor,inv,'found',n,loc)
    record(engine,actor,inv,'empty',local='FICT-ALC-4')
    with pytest.raises(a.ReceivingError):review(engine,actor,inv)
    record(engine,actor,inv,'found','000104','FICT-ALC-4')
    record(engine,actor,inv,'excluded',entry=pending)
    confirm(engine,actor,inv)
    with scoped(engine,5) as db:
        assert a.stock(db,state='available')[1]==4 and a.stock(db,state='on_hand')[1]==4
        assert db.get(m.AlcobreInventory,inv).status=='confirmed'
        assert a.stock(db,state='pending')[2][0][1]==2
    with pytest.raises(a.ReceivingError):incoming_review(engine,actor,entry,[row('000104','FICT-ALC-4')],kind='place')


def test_alcobre_inventory_unknown_incident_does_not_create_stock_and_blocks_location(warehouse):
    engine,u=warehouse;actor=u['admin'][0];seed(engine,actor)
    inv=create(engine,actor);check=record(engine,actor,inv,'unknown','FICT-UNKNOWN','FICT-ALC-1')
    with scoped(engine,5) as db:
        assert db.scalar(select(func.count()).select_from(m.AlcobreBobine))==4
        assert 'FICT-ALC-1' in s.incident_locations(db) and a.stock(db,state='available')[1]==2
        assert dispatch.plan(db,['000101'])['items'][0]['reason']=='inventory_incident'
    record(engine,actor,inv,'found','000101','FICT-ALC-1')
    with pytest.raises(a.ReceivingError):review(engine,actor,inv)
    with pytest.raises(a.ReceivingError):record(engine,u['operador'][0],inv,'resolved',resolves=check)
    record(engine,actor,inv,'resolved',resolves=check)
    with pytest.raises(a.ReceivingError):review(engine,actor,inv)
    record(engine,actor,inv,'found','000101','FICT-ALC-1');confirm(engine,actor,inv)
    with scoped(engine,5) as db:
        assert not s.incident_locations(db) and a.stock(db,state='available')[1]==3
        assert db.scalar(select(func.count()).select_from(m.AlcobreBobine))==4


def test_alcobre_inventory_reserved_or_shipped_never_adjusted_and_stale_counts_block(warehouse):
    engine,u=warehouse;actor=u['admin'][0];seed(engine,actor);inv=create(engine,actor)
    record(engine,actor,inv,'missing','000101');rev=review(engine,actor,inv)
    guide=source(engine,actor,['000101']);dispatch_review(engine,actor,guide);prepare(engine,actor,guide)
    before=snapshot(engine)
    with pytest.raises(a.ReceivingError):confirm(engine,actor,inv,rev)
    assert snapshot(engine)==before
    record(engine,actor,inv,'missing','000101')
    with pytest.raises(a.ReceivingError):review(engine,actor,inv)
    record(engine,actor,inv,'excluded','000101');confirm(engine,actor,inv)
    with scoped(engine,5) as db:assert a.stock(db,state='reserved')[1]==1
    ship(engine,actor,guide,['000101'])
    new=create(engine,actor);record(engine,actor,new,'found','000101','FICT-ALC-1')
    with scoped(engine,5) as db:
        assert s.summary(db,db.get(m.AlcobreInventory,new))['incidents'] and a.stock(db,'000101',state='shipped')[1]==1


def test_alcobre_inventory_pdf_sqlite_is_read_only_and_absence_not_unknown():
    report=dict(rows=[dict(source=dict(number='0000001',local='FICT-1'),result='unverified',local=None,action='none',evidence=None,recorded_quantity=1,physical_quantity=None,difference=None)],pending=[],incidents=[],locations=[dict(local='FICT-1',result='unverified',counted=False),dict(local='FICT-2',result='empty',counted=True)],events=[],blocking=['Por conferir'])
    raw=render_pdf(1,1,'Inventário fictício',report,'conference','Fictício',datetime(2027,1,1),'Só conferência')
    reader=PdfReader(BytesIO(raw));words=' '.join(p.extract_text() for p in reader.pages)
    assert 'POR CONFERIR' in words and 'zero contado' in words and '0000001' in words and 'Desconhecido' in words
    assert all(abs(float(p.mediabox.width)-595.28)<1 and abs(float(p.mediabox.height)-841.89)<1 for p in reader.pages)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    with Session(create_engine('sqlite://')) as db:
        for fn,args in [(s.create,('a'*32,'FICT','complete')),(s.record,(1,1,'missing','Prova','0001')),(s.review,(1,1,'a'*32,'Prova')),(s.confirm,(1,1,'digest','a'*32,True))]:
            with pytest.raises(a.ReceivingError,match='PostgreSQL'):fn(db,1,*args)


def test_alcobre_inventory_concurrent_confirmations_and_conflicting_inventories(warehouse):
    engine,u=warehouse;actor=u['admin'][0];seed(engine,actor)
    inventories=[create(engine,actor) for _ in range(2)]
    for inv in inventories:record(engine,actor,inv,'missing','000101')
    revisions=[review(engine,actor,i) for i in inventories];barrier=Barrier(2)
    def run(index):
        barrier.wait(timeout=10)
        try:return confirm(engine,actor,inventories[index],revisions[index])
        except a.ReceivingError:return None
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(run,range(2)))
    assert sum(r is not None for r in results)==1
    with scoped(engine,5) as db:
        assert a.stock(db,state='missing')[1]==1
        assert db.scalar(select(func.count()).select_from(m.AlcobreInventoryOperation))==1
        assert db.scalar(select(func.count()).select_from(m.AlcobreDispatchEvent).where(m.AlcobreDispatchEvent.control_operation_id.is_not(None)))==1
    recovery=create(engine,actor);record(engine,actor,recovery,'found','000101','FICT-ALC-1');rev=review(engine,actor,recovery);key=secrets.token_hex(16);barrier=Barrier(2)
    def replay(index):barrier.wait(timeout=10);return confirm(engine,actor,recovery,rev,key)
    with ThreadPoolExecutor(2) as pool:ops=list(pool.map(replay,range(2)))
    assert ops[0]==ops[1]
    with scoped(engine,5) as db:assert a.stock(db,'000101',state='available')[1]==1


@pytest.mark.parametrize('change',['reserve_cancel','new_receipt','move'])
def test_alcobre_inventory_movements_after_count_require_new_conference(warehouse,change):
    engine,u=warehouse;actor=u['admin'][0];seed(engine,actor);inv=create(engine,actor)
    record(engine,actor,inv,'found','000101','FICT-ALC-1');rev=review(engine,actor,inv)
    if change=='reserve_cancel':
        from test_alcobre_dispatch import cancel
        guide=source(engine,actor,['000101']);dispatch_review(engine,actor,guide);prepare(engine,actor,guide);cancel(engine,actor,guide)
    elif change=='new_receipt':
        empty=create(engine,actor);record(engine,actor,empty,'missing','000101');confirm(engine,actor,empty)
        receive(engine,actor,incoming_review(engine,actor,incoming(engine,actor),[row('FICT-NEW','FICT-ALC-1')]))
    else:
        from test_alcobre import positions
        positions(engine,actor,4);other=create(engine,actor);record(engine,actor,other,'elsewhere','000101','FICT-ALC-4');confirm(engine,actor,other)
    before=snapshot(engine)
    with pytest.raises(a.ReceivingError):confirm(engine,actor,inv,rev)
    assert snapshot(engine)==before


@pytest.mark.parametrize('failure',['exception','disconnect'])
def test_alcobre_inventory_failure_rolls_back_event_location_document_and_audit(restricted_web,monkeypatch,failure):
    app,owner,engine,role,u,t=restricted_web;actor=u['admin'][0];seed(engine,actor);inv=create(engine,actor)
    record(engine,actor,inv,'missing','000101');rev=review(engine,actor,inv);before=snapshot(engine);original=s.archive
    def fail(db,*args,**kwargs):
        if failure=='exception':raise RuntimeError('Injected fictitious archive failure')
        pid=db.scalar(text('SELECT pg_backend_pid()'))
        with owner.begin() as conn:assert conn.scalar(text('SELECT pg_terminate_backend(:pid)'),dict(pid=pid))
        return original(db,*args,**kwargs)
    monkeypatch.setattr(s,'archive',fail);key=secrets.token_hex(16)
    with pytest.raises((a.ReceivingError,RuntimeError)):confirm(engine,actor,inv,rev,key)
    assert snapshot(engine)==before
    monkeypatch.setattr(s,'archive',original);confirm(engine,actor,inv,rev,key)
    with scoped(engine,5) as db:assert a.stock(db,state='missing')[1]==1


def test_alcobre_inventory_permissions_csrf_context_and_archived_pdf(restricted_web):
    app,owner,engine,role,u,t=restricted_web;actor=u['admin'][0];seed(engine,actor)
    inv=create(engine,actor);record(engine,actor,inv,'missing','000101');rev=review(engine,actor,inv)
    with TestClient(app) as client:
        client.cookies.set('wms_session',t['admin'][0]);client.cookies.set('ddn_context','5')
        assert client.get('/alcobre/inventories').status_code==200
        page=client.get(f'/alcobre/inventories/{inv}');assert page.status_code==200,page.text
        assert 'Inventários e correções Alcobre' in page.text and 'Confirmar falta' in page.text
        body=dict(csrf=t['admin'][1],context_id=5,review=rev[0],digest=rev[1],key=secrets.token_hex(16),accepted='yes')
        assert client.post(f'/alcobre/inventories/{inv}/confirm',data=dict(body,csrf='bad')).status_code==403
        client.cookies.set('wms_session',t['operador'][0])
        assert client.post(f'/alcobre/inventories/{inv}/confirm',data=dict(body,csrf=t['operador'][1])).status_code==403
        with pytest.raises(a.ReceivingError):confirm(engine,u['operador'][0],inv,rev)
        with scoped(engine,2) as db:
            with pytest.raises(a.ReceivingError):s.create(db,actor,secrets.token_hex(16),'Wrong context','complete')
        client.cookies.set('wms_session',t['admin'][0]);client.cookies.set('ddn_context','2')
        assert client.get(f'/alcobre/inventories/{inv}').status_code==403
        client.cookies.set('ddn_context','5')
        response=client.post(f'/alcobre/inventories/{inv}/confirm',data=body);assert response.status_code==200,response.text
        with scoped(engine,5) as db:docs={d.id:bytes(d.pdf) for d in db.scalars(select(m.AlcobreInventoryDocument))}
        before=snapshot(engine)
        for ident,raw in docs.items():assert client.get(f'/alcobre/inventory-documents/{ident}.pdf').content==raw==client.get(f'/alcobre/inventory-documents/{ident}.pdf').content
        assert client.post(f'/alcobre/inventories/{inv}/confirm',data=body).status_code==200
        assert snapshot(engine)==before
        client.cookies.set('ddn_context','2');assert client.get(f'/alcobre/inventory-documents/{next(iter(docs))}.pdf').status_code==403


@pytest.mark.parametrize('tamper',['event','location','nonpositive','snapshot'])
def test_alcobre_inventory_sql_runtime_rejects_forged_corrections(restricted_web,monkeypatch,tamper):
    from sqlalchemy import event
    app,owner,engine,role,u,t=restricted_web;actor=u['admin'][0];seed(engine,actor)
    inv=create(engine,actor);record(engine,actor,inv,'missing','000101');rev=review(engine,actor,inv);before=snapshot(engine)
    def alter(mapper,connection,target):
        if tamper=='event':target.state='available'
        elif tamper=='location':target.local='FICT-ALC-2'
        elif tamper=='nonpositive':target.id=0
        else:target.previous_id=12345678
    event.listen(m.AlcobreDispatchEvent,'before_insert',alter)
    try:
        with pytest.raises(a.ReceivingError):confirm(engine,actor,inv,rev)
    finally:event.remove(m.AlcobreDispatchEvent,'before_insert',alter)
    assert snapshot(engine)==before
    with scoped(engine,5) as db:
        for name in ['alcobre_inventory_checks','alcobre_inventory_reviews','alcobre_inventory_operations','alcobre_inventory_documents','alcobre_dispatch_events']:
            with pytest.raises(DBAPIError):db.execute(text('DELETE FROM '+name))
            db.rollback()
        # Direct replacement of a projected placement is impossible: only append-only events exist.
        with pytest.raises(DBAPIError):db.execute(text("UPDATE alcobre_placements SET local='FICT-ALC-2'"))
        db.rollback()
        with pytest.raises(DBAPIError):db.execute(text('CREATE TABLE unsafe_runtime(id integer)'))
        db.rollback()


def test_alcobre_inventory_empty_unknown_and_contradictory_counts_do_not_become_stock(warehouse):
    engine,u=warehouse;actor=u['admin'][0];seed(engine,actor)
    from test_alcobre import positions
    positions(engine,actor,4);inv=create(engine,actor,['FICT-ALC-4'])
    with pytest.raises(a.ReceivingError):review(engine,actor,inv)
    record(engine,actor,inv,'empty',local='FICT-ALC-4');confirm(engine,actor,inv)
    with scoped(engine,5) as db:assert db.scalar(select(func.count()).select_from(m.AlcobreBobine))==4
    complete=create(engine,actor,mode='complete')
    for n,loc in [('000101','FICT-ALC-1'),('000102','FICT-ALC-2'),('000103','FICT-ALC-3'),('000104','FICT-ALC-4')]:record(engine,actor,complete,'found',n,loc)
    record(engine,actor,complete,'empty',local='FICT-ALC-4')
    with pytest.raises(a.ReceivingError):review(engine,actor,complete)
    # The last explicit empty count cannot silently be overridden by an older presence claim.
    with scoped(engine,5) as db:assert any('contraditórias' in x for x in s.summary(db,db.get(m.AlcobreInventory,complete))['blocking'])


def test_alcobre_inventory_operator_can_count_own_but_not_approve(warehouse):
    engine,u=warehouse;admin=u['admin'][0];actor=u['operador'][0];seed(engine,admin)
    inv=create(engine,actor);record(engine,actor,inv,'missing','000101')
    with pytest.raises(a.ReceivingError):review(engine,actor,inv)
    confirm(engine,admin,inv)
    with scoped(engine,5) as db:assert a.stock(db,state='missing')[1]==1


def test_alcobre_inventory_ddn17_upgrade_and_encrypted_recovery_preserve_guide_books(pg_databases,tmp_path,monkeypatch):
    from alembic import command
    from iberbag.migrations import config,REVISION
    from iberbag.schema_upgrade import apply_upgrade
    from iberbag.security import set_password
    from legacy_catalogue import seed_legacy_catalogue
    engine=pg_databases(restricted=True);verified=pg_databases(restricted=True)
    with engine.begin() as conn:cfg=config();cfg.attributes['connection']=conn;command.upgrade(cfg,'ddn_0017')
    with scoped(engine) as db:
        seed_legacy_catalogue(db);actor_row=m.User(username='admin',display_name='Fictício',role='admin',active=True);set_password(actor_row,secrets.token_urlsafe(24));db.add(actor_row);db.commit();actor=actor_row.id
    seed(engine,actor)
    # Test-only old-column insertion runs the published 0017 guards unchanged.
    original=dispatch.operation
    def old_operation(db,user,guide,rev,key,kind,payload,snap):
        op=m.AlcobreDispatchOperation(dispatch_id=guide.id,revision_id=rev.id,request_key=key,kind=kind,payload=a.dump(payload),snapshot=a.dump(snap),actor=user.username);db.add(op);db.flush()
        for e in snap['events']:db.execute(m.AlcobreDispatchEvent.__table__.insert().values(context_id=5,operation_id=op.id,**e))
        return op
    monkeypatch.setattr(dispatch,'operation',old_operation)
    ids=[source(engine,actor,[n]) for n in ['000101','000102']]
    for ident in ids:dispatch_review(engine,actor,ident);prepare(engine,actor,ident)
    ship(engine,actor,ids[0],['000101']);before=snapshot(engine);folder=tmp_path/'before18'
    backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD);backup.restore(verified,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert apply_upgrade(engine,folder)['revision']==REVISION=='ddn_0020' and snapshot(engine)==before
    monkeypatch.setattr(dispatch,'operation',original)
    with engine.connect() as conn:
        native=transfer.read_data(conn)
        for name in transfer.ALCOBRE_INVENTORY_TABLES:native.pop(name)
        for e in native['alcobre_dispatch_events']:
            e.pop('control_operation_id');e.pop('local')
        assert transfer.archive_inventory(conn,transfer.inventory(native))==transfer.inventory(native)
    inv=create(engine,actor,['FICT-ALC-3']);record(engine,actor,inv,'missing','000103');confirm(engine,actor,inv)
    with engine.connect() as conn:
        with pytest.raises(ValueError):transfer.archive_inventory(conn,transfer.inventory(native))
    original_snapshot=snapshot(engine);after=tmp_path/'after18';target=pg_databases(restricted=True)
    backup.backup(engine,after,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert backup.restore(target,after,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled' and snapshot(target)==original_snapshot
    with scoped(target,5) as db:
        assert a.stock(db,state='shipped')[1]==1 and a.stock(db,state='reserved')[1]==1 and a.stock(db,state='missing')[1]==1
        assert db.scalar(select(func.count()).select_from(m.AlcobreInventoryDocument))>=3


def test_alcobre_inventory_incident_after_reservation_blocks_shipping_until_decision(warehouse):
    engine,u=warehouse;actor=u['admin'][0];seed(engine,actor)
    guide=source(engine,actor,['000101']);dispatch_review(engine,actor,guide);prepare(engine,actor,guide)
    inv=create(engine,actor);check=record(engine,actor,inv,'unknown','FICT-UNEXPECTED','FICT-ALC-1')
    before=snapshot(engine)
    with pytest.raises(a.ReceivingError):ship(engine,actor,guide,['000101'])
    assert snapshot(engine)==before
    record(engine,actor,inv,'found','000101','FICT-ALC-1');record(engine,actor,inv,'resolved',resolves=check)
    with pytest.raises(a.ReceivingError):review(engine,actor,inv)
    record(engine,actor,inv,'found','000101','FICT-ALC-1');confirm(engine,actor,inv)
    with scoped(engine,5) as db:assert a.stock(db,state='reserved')[1]==1
    ship(engine,actor,guide,['000101'])
    with scoped(engine,5) as db:assert a.stock(db,state='shipped')[1]==1


def test_alcobre_inventory_concurrent_counts_use_versions_without_lost_history(warehouse):
    engine,u=warehouse;actor=u['admin'][0];seed(engine,actor);inv=create(engine,actor);barrier=Barrier(2)
    with scoped(engine,5) as db:version=db.get(m.AlcobreInventory,inv).version
    def run(result):
        barrier.wait(timeout=10)
        try:
            with scoped(engine,5) as db:return s.record(db,actor,inv,version,result,'Conferência fictícia simultânea','000101','FICT-ALC-1' if result=='found' else None)
        except a.ReceivingError:return None
    with ThreadPoolExecutor(2) as pool:values=list(pool.map(run,['found','missing']))
    assert sum(i is not None for i in values)==1
    with scoped(engine,5) as db:
        assert db.scalar(select(func.count()).select_from(m.AlcobreInventoryCheck))==1
        assert db.get(m.AlcobreInventory,inv).version==version+1 and a.stock(db,state='missing')[1]==0


def test_alcobre_inventory_empty_count_invalid_after_unknown_resolved_elsewhere(warehouse):
    engine,u=warehouse;actor=u['admin'][0];seed(engine,actor)
    from test_alcobre import positions
    positions(engine,actor,4)
    first=create(engine,actor,['FICT-ALC-4']);record(engine,actor,first,'empty',local='FICT-ALC-4');rev=review(engine,actor,first)
    second=create(engine,actor,['FICT-ALC-4']);incident=record(engine,actor,second,'unknown','FICT-UNCERTAIN','FICT-ALC-4')
    record(engine,actor,second,'resolved',resolves=incident)
    with pytest.raises(a.ReceivingError):confirm(engine,actor,first,rev)
    record(engine,actor,first,'empty',local='FICT-ALC-4');confirm(engine,actor,first)
    with scoped(engine,5) as db:assert a.stock(db,state='on_hand')[1]==4


def test_alcobre_inventory_catalog_change_shows_blocking_without_unsafe_confirmation(warehouse):
    engine,u=warehouse;actor=u['admin'][0];seed(engine,actor)
    from test_alcobre import positions
    from iberbag.warehouse_settings import set_location_active
    positions(engine,actor,4)
    inv=create(engine,actor,['FICT-ALC-4']);record(engine,actor,inv,'empty',local='FICT-ALC-4');rev=review(engine,actor,inv)
    with scoped(engine,5) as db:set_location_active(db,'admin','FICT-ALC-4',False)
    with scoped(engine,5) as db:assert any('Catálogo/âmbito' in item for item in s.summary(db,db.get(m.AlcobreInventory,inv))['blocking'])
    before=snapshot(engine)
    with pytest.raises(a.ReceivingError):confirm(engine,actor,inv,rev)
    assert snapshot(engine)==before


def test_alcobre_inventory_unlabelled_found_material_never_gets_invented_identity(warehouse):
    engine,u=warehouse;actor=u['admin'][0];seed(engine,actor)
    from test_alcobre import positions
    positions(engine,actor,4);inv=create(engine,actor,['FICT-ALC-4'])
    incident=record(engine,actor,inv,'unknown',local='FICT-ALC-4')
    with scoped(engine,5) as db:
        item=db.get(m.AlcobreInventoryCheck,incident)
        assert item.number is None and item.bobine_id is None and item.entry_id is None
        assert db.scalar(select(func.count()).select_from(m.AlcobreBobine))==4
        assert s.summary(db,db.get(m.AlcobreInventory,inv))['blocking']
    with pytest.raises(a.ReceivingError):review(engine,actor,inv)
    record(engine,actor,inv,'resolved',resolves=incident)
    record(engine,actor,inv,'unverified',local='FICT-ALC-4')
    with pytest.raises(a.ReceivingError):review(engine,actor,inv)
    record(engine,actor,inv,'empty',local='FICT-ALC-4')
    confirm(engine,actor,inv)
    with scoped(engine,5) as db:
        assert db.scalar(select(func.count()).select_from(m.AlcobreBobine))==4
        assert db.scalar(select(func.count()).select_from(m.AlcobreDispatchEvent).where(m.AlcobreDispatchEvent.control_operation_id.is_not(None)))==0
