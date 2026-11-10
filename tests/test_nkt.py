"""Fictional PEG pallets only, disposable PostgreSQL 17."""
import json,secrets
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select,func,text
from sqlalchemy.exc import DBAPIError
from iberbag import database as m,nkt as n,backup,transfer
from iberbag.warehouse_settings import add_location,set_capacity,remove_location,rename_location,WarehouseError
from iberbag.privileges import validate_runtime
from test_receiving import warehouse
from test_transfer import pg_databases,docker_client
from test_ddn_contexts import scoped
from test_security_regressions import restricted_web
from conftest import BACKUP_PASSWORD

def setup(engine):
    with scoped(engine,4) as db:
        add_location(db,'admin','ESC-01');add_location(db,'admin','Chão');set_capacity(db,'admin','ESC-01','2')

def save(engine,actor,pegs,loc='ESC-01',scope=4,key=None):
    with scoped(engine,scope) as db:return n.save(db,actor,key or secrets.token_hex(16),None,'Fictício',[dict(peg=p,local=loc) for p in pegs])

def confirm(engine,actor,ident,version=1,scope=4):
    with scoped(engine,scope) as db:
        rec=db.get(m.NktEntry,ident)
        return n.confirm(db,actor,ident,version,n.digest(rec.payload) if rec else '0'*64)

def test_nkt_individual_identity_review_history_capacity_and_never_reuse(restricted_web):
    app,owner,engine,role,a,tokens=restricted_web;setup(owner);actor=a['admin'][0]
    with engine.connect() as conn:validate_runtime(conn)
    ident=save(engine,actor,['000AbC','000002'])
    with scoped(engine,4) as db:
        assert not n.stock(db)[1];review=db.scalar(select(m.NktReview));assert json.loads(review.payload)['received_on'] is None
    assert confirm(engine,actor,ident)==ident and confirm(engine,actor,ident)==ident
    with scoped(engine,4) as db:
        rows,total=n.stock(db);assert total==2 and [r.peg for r in rows]==['000AbC','000002']
        assert n.stock(db,'abc')[1]==1 and n.stock(db,local='esc-01')[1]==2
        assert db.scalar(select(func.count()).select_from(m.Receipt))==0
        assert db.scalar(select(func.count()).select_from(m.Stock))==0
        assert db.get(m.NktEntry,ident).confirmed_by=='admin'
        assert n.save(db,actor,db.get(m.NktEntry,ident).request_key,None,'Fictício',[dict(peg=p,local='ESC-01') for p in ('000AbC','000002')])==ident
        assert db.scalar(select(func.count()).select_from(m.NktReview))==1
        for table in ('nkt_pallets','nkt_reviews'):
            with pytest.raises(DBAPIError):db.execute(text('DELETE FROM '+table))
            db.rollback()
    with pytest.raises(n.ReceivingError):save(engine,actor,['000aBc'],'Chão')
    with pytest.raises(n.ReceivingError):save(engine,actor,['THIRD'])
    with scoped(engine,4) as db:
        for operation in (lambda:remove_location(db,'admin','ESC-01'),lambda:rename_location(db,'admin','ESC-01','ESC-02'),lambda:set_capacity(db,'admin','ESC-01','1')):
            with pytest.raises((WarehouseError,DBAPIError)):operation()
            db.rollback()
    with scoped(engine,1) as db:assert not db.scalar(select(m.NktPallet.id))

@pytest.mark.parametrize('pegs',[[1],[''],[' A'],['a\n'],['ABC','abc'],['0'*121]])
def test_nkt_invalid_identity_never_becomes_stock(warehouse,pegs):
    engine,a=warehouse;setup(engine)
    with pytest.raises(n.ReceivingError):save(engine,a['admin'][0],pegs)
    with scoped(engine,4) as db:assert not n.stock(db)[1] and not db.scalar(select(m.NktEntry.id))

@pytest.mark.parametrize('same',[True,False])
def test_nkt_concurrent_identity_capacity_and_atomic_rollback(warehouse,same):
    engine,a=warehouse;setup(engine);actor=a['admin'][0]
    with scoped(engine,4) as db:set_capacity(db,'admin','ESC-01','1')
    ids=[save(engine,actor,[peg]) for peg in ('PEG-A','peg-a' if same else 'PEG-B')]
    def run(ident):
        try:confirm(engine,actor,ident);return True
        except n.ReceivingError:return False
    with ThreadPoolExecutor(2) as pool:assert sum(pool.map(run,ids))==1
    with scoped(engine,4) as db:assert n.stock(db)[1]==1 and db.scalar(select(func.count()).select_from(m.NktReview))==2


def test_nkt_owner_wide_identity_contexts_and_failed_confirmation(warehouse,monkeypatch):
    engine,a=warehouse;setup(engine);actor=a['admin'][0]
    with scoped(engine,1) as db:
        ctx=m.StorageContext(client_id=4,warehouse_id=1,area='NKT fictício',active=True);db.add(ctx);db.commit();other=ctx.id
    with scoped(engine,other) as db:add_location(db,'admin','NKT-OVAR')
    first=save(engine,actor,['000PEG']);confirm(engine,actor,first)
    with pytest.raises(n.ReceivingError):save(engine,actor,['000peg'],'NKT-OVAR',scope=other)
    with scoped(engine,other) as db:assert n.stock(db)[1]==0
    with scoped(engine,1) as db,pytest.raises(n.ReceivingError):n.confirm(db,actor,first,1,'fake')
    second=save(engine,actor,['PEG-2','PEG-3'],'Chão')
    from sqlalchemy import event
    def fail(mapper,connection,target):
        if target.peg=='PEG-3':raise RuntimeError('Falha fictícia entre duas paletes')
    event.listen(m.NktPallet,'after_insert',fail)
    try:
        with pytest.raises(RuntimeError):confirm(engine,actor,second)
    finally:event.remove(m.NktPallet,'after_insert',fail)
    with scoped(engine,4) as db:assert n.stock(db)[1]==1 and db.get(m.NktEntry,second).status=='draft'
    confirm(engine,actor,second)


def test_nkt_server_permissions_csrf_context_and_revision(restricted_web):
    from fastapi.testclient import TestClient
    app,owner,engine,role,a,tokens=restricted_web;setup(owner);actor=a['operador'][0]
    with TestClient(app) as client:
        assert client.get('/nkt/stock').status_code==403
        client.cookies.set('wms_session',tokens['operador'][0]);client.cookies.set('ddn_context','4')
        assert client.get('/').status_code==200 and client.get('/nkt/entries/new').status_code==200
        legacy=save(engine,actor,['000Web'])  # existing pre-container draft editing remains compatible
        with scoped(engine,4) as db:legacy_key=db.get(m.NktEntry,legacy).request_key
        body=dict(csrf=tokens['operador'][1],context_id=4,key=legacy_key,id=legacy,version=1,peg0='000Web',local0='ESC-01')
        assert client.post('/nkt/entries/review',data=dict(body,csrf='invalid')).status_code==403
        assert client.post('/nkt/entries/review',data=dict(body,context_id=1)).status_code==409
        response=client.post('/nkt/entries/review',data=body,follow_redirects=False);assert response.status_code==303,response.text
        ident=int(response.headers['location'].split('/')[-1]);assert client.get(response.headers['location']).status_code==200
        with scoped(engine,4) as db:digest=n.digest(db.get(m.NktEntry,ident).payload)
        data=dict(csrf=tokens['operador'][1],context_id=4,version=2,digest=digest,accepted='yes')
        assert client.post(f'/nkt/entries/{ident}/confirm',data=dict(data,digest='bad')).status_code==409
        assert client.post(f'/nkt/entries/{ident}/confirm',data=data).status_code==200
        assert '000Web' in client.get('/nkt/stock?peg=web').text
        client.cookies.set('ddn_context','2');assert client.get('/nkt/stock').status_code==403
    with scoped(engine,4) as db,pytest.raises(n.ReceivingError):n.save(db,a['admin'][0],body['key'],None,'Fictício',[dict(peg='Other',local='Chão')],ident,1)


def test_nkt_backup_restore_and_prior_head_upgrade(pg_databases,warehouse,tmp_path):
    from alembic import command
    from iberbag.migrations import config,REVISION
    from iberbag.schema_upgrade import apply_upgrade
    from legacy_catalogue import seed_legacy_catalogue
    from iberbag.security import set_password
    engine=pg_databases(restricted=True);target=pg_databases(restricted=True)
    with engine.begin() as conn:
        cfg=config();cfg.attributes['connection']=conn;command.upgrade(cfg,'ddn_0011')
    with scoped(engine) as db:
        seed_legacy_catalogue(db);u=m.User(username='admin',display_name='Fictício',role='admin',active=True);set_password(u,secrets.token_urlsafe(24));db.add(u);db.commit();actor=u.id
    with engine.connect() as conn:before=transfer.inventory(transfer.read_data(conn))
    folder=tmp_path/'before';backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD);backup.restore(target,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert apply_upgrade(engine,folder)['revision']==REVISION=='ddn_0013'
    with engine.connect() as conn:assert transfer.inventory(transfer.read_data(conn))==before
    setup(engine);ident=save(engine,actor,['000Restore']);confirm(engine,actor,ident)
    with engine.connect() as conn:before=transfer.inventory(transfer.read_data(conn))
    target2=pg_databases(restricted=True);folder2=tmp_path/'after'
    backup.backup(engine,folder2,runner=docker_client,passphrase=BACKUP_PASSWORD);backup.restore(target2,folder2,runner=docker_client,passphrase=BACKUP_PASSWORD)
    with target2.connect() as conn:assert transfer.inventory(transfer.read_data(conn))==before
    assert confirm(target2,actor,ident)==ident


def test_nkt_sqlite_writes_disabled_and_original_text_preserved():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    assert json.loads(n.payload(None,'',[dict(peg='000AbC',local='L')]))['rows'][0]['peg']=='000AbC'
    with Session(create_engine('sqlite://')) as db,pytest.raises(n.ReceivingError,match='PostgreSQL'):n.confirm(db,1,1,1,'digest')
