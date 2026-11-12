"""Fictional load-level inventory only. No company Excel or production connection."""
import json,secrets
from sqlalchemy import select,func,text
from iberbag import database as m,nkt,nkt_inventory as inv,nkt_loading as loading,backup,transfer
from test_receiving import warehouse
from test_ddn_contexts import scoped
from test_nkt_loading import seed,prepare,reserve,ship
from test_transfer import pg_databases,docker_client
from test_security_regressions import restricted_web
from conftest import BACKUP_PASSWORD

def create(engine,actor,mode='complete',scope=None):
    with scoped(engine,4) as db:return inv.create(db,actor,secrets.token_hex(16),'Inventário fictício',mode,scope)

def mark(engine,actor,ident,state,entry=None,peg=None):
    with scoped(engine,4) as db:return inv.record(db,actor,ident,db.get(m.NktInventory,ident).version,state,entry,peg,'Informação fictícia comunicada pelo colaborador')

def review(engine,actor,ident):
    with scoped(engine,4) as db:
        r=inv.review(db,actor,ident,db.get(m.NktInventory,ident).version,secrets.token_hex(16),'Revisão física comprovada fictícia')
        return r,db.get(m.NktInventoryReview,r).digest

def confirm(engine,actor,ident,rev,key=None):
    with scoped(engine,4) as db:return inv.confirm(db,actor,ident,*rev,key or secrets.token_hex(16),True)

def entries(engine):
    with scoped(engine,4) as db:return db.scalars(select(m.NktEntry.id).order_by(m.NktEntry.id)).all()

def test_nkt_inventory_load_conference_and_atomic_missing(warehouse):
    engine,a=warehouse;actor=a['admin'][0];seed(engine,actor);first,second=entries(engine);ident=create(engine,actor)
    mark(engine,actor,ident,'located',peg='001');mark(engine,actor,ident,'not_located',entry=first);mark(engine,actor,ident,'located',peg='001');mark(engine,actor,ident,'not_located',entry=second)
    with scoped(engine,4) as db:
        s=inv.summary(db,db.get(m.NktInventory,ident));assert not s['blocking'] and s['totals']['located']==s['totals']['not_located']==1
        assert nkt.stock(db)[1]==6 and db.scalar(select(func.count()).select_from(m.NktInventoryCheck).where(m.NktInventoryCheck.entry_id==first))==3
    rev=review(engine,actor,ident);op=confirm(engine,actor,ident,rev);assert confirm(engine,actor,ident,rev)==op
    with scoped(engine,4) as db:
        assert nkt.stock(db)[1]==3 and nkt.stock(db,status='missing')[1]==3 and nkt.stock(db,status='shipped')[1]==0
        assert db.scalar(select(func.count()).select_from(m.NktPallet))==6
        assert db.get(m.NktOperation,op).kind=='inventory_missing' and db.get(m.NktOperation,op).preparation_id is None

import pytest
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from sqlalchemy import event
from sqlalchemy.exc import DBAPIError


def restore_review(engine,actor,ident,original,code,all_load=True,pegs=None):
    with scoped(engine,4) as db:
        r=inv.review_restore(db,actor,ident,original,secrets.token_hex(16),code,all_load,pegs,'Paletes encontradas e comprovadas fictícias')
        assert isinstance(r,int),r
        return r,db.get(m.NktInventoryReview,r).digest

def restore(engine,actor,revision,key=None):
    with scoped(engine,4) as db:return inv.apply_restore(db,actor,*revision,key or secrets.token_hex(16),True)


def test_nkt_inventory_full_suffix_resolves_one_peg_entire_load_and_ambiguity(warehouse):
    from test_nkt_receiving import stage,confirm as receive
    engine,a=warehouse;actor=a['admin'][0]
    for container,pegs in [('FICT-A',['AAA0001','AAA0002']),('FICT-B',['BBB0001','BBB0003'])]:
        rec=stage(engine,actor,[dict(container=container,material='Material '+container,received_on='2027-01-01',pegs=pegs)]);receive(engine,actor,rec)
    ident=create(engine,actor);result=mark(engine,actor,ident,'located',peg='0001')
    assert result.get('incident') and 'ambíguo' in result['message']
    with scoped(engine,4) as db:
        snap=inv.summary(db,db.get(m.NktInventory,ident));assert snap['totals']['unverified']==2 and nkt.stock(db)[1]==4
        assert db.get(m.NktInventoryIncident,result['incident']).kind=='ambiguous_peg'
        inv.resolve_incident(db,actor,result['incident'],'Requerido e recebido PEG completo fictício','AAA0001')
    mark(engine,actor,ident,'located',peg='aaa0001');mark(engine,actor,ident,'located',peg='0003');confirm(engine,actor,ident,review(engine,actor,ident))
    with scoped(engine,4) as db:
        assert nkt.stock(db)[1]==4
        rows=db.scalars(select(m.NktInventoryCheck).where(m.NktInventoryCheck.state=='located')).all()
        assert [r.communicated for r in rows]==['aaa0001','0003'] and all(r.pallet_id for r in rows)


def test_nkt_inventory_partial_unverified_is_not_missing_and_outside_scope_untouched(warehouse):
    engine,a=warehouse;actor=a['admin'][0];seed(engine,actor);first,second=entries(engine);ident=create(engine,actor,'partial',[first])
    with pytest.raises(inv.ReceivingError):review(engine,actor,ident)
    mark(engine,actor,ident,'not_located',entry=first);confirm(engine,actor,ident,review(engine,actor,ident))
    with scoped(engine,4) as db:
        assert nkt.stock(db)[1]==3 and all(p.entry_id==second for p in nkt.stock(db)[0])
        assert nkt.stock(db,status='missing')[1]==3


@pytest.mark.parametrize('selected',[['001'],['001','002','003']])
def test_nkt_inventory_reserved_loads_excluded_wholly_even_when_partially_reserved(warehouse,selected):
    engine,a=warehouse;actor=a['admin'][0];seed(engine,actor);first,second=entries(engine);prep=prepare(engine,actor,selected);reserve(engine,actor,prep);ident=create(engine,actor)
    result=mark(engine,actor,ident,'not_located',entry=first);assert result.get('incident')
    with scoped(engine,4) as db:
        inv.resolve_incident(db,actor,result['incident'],'Carga excluída por reservas, sem inferir ausência')
        snap=inv.summary(db,db.get(m.NktInventory,ident));assert snap['totals']['excluded']==1 and snap['groups'][0]['source']['physical']==3
    mark(engine,actor,ident,'not_located',entry=second);confirm(engine,actor,ident,review(engine,actor,ident))
    with scoped(engine,4) as db:
        assert nkt.stock(db)[1]==3 and nkt.stock(db,status='reserved')[1]==len(selected) and nkt.stock(db,status='missing')[1]==3
        assert all(p.entry_id==second for p in nkt.stock(db,status='missing')[0])


def test_nkt_inventory_rejects_reserve_cancel_net_zero_movement_after_conference(warehouse):
    engine,a=warehouse;actor=a['admin'][0];seed(engine,actor);first,second=entries(engine);ident=create(engine,actor)
    mark(engine,actor,ident,'located',peg='001');mark(engine,actor,ident,'not_located',entry=second);old=review(engine,actor,ident)
    prep=prepare(engine,actor,['004']);reserve(engine,actor,prep)
    with pytest.raises(inv.ReceivingError):confirm(engine,actor,ident,old)
    with scoped(engine,4) as db:
        p=db.get(m.NktPreparation,prep);loading.cancel(db,actor,prep,p.version,loading.last_operation(db,prep),secrets.token_hex(16),'Cancelamento fictício da reserva')
    with pytest.raises(inv.ReceivingError):confirm(engine,actor,ident,old)
    mark(engine,actor,ident,'not_located',entry=second);confirm(engine,actor,ident,review(engine,actor,ident))
    with scoped(engine,4) as db:assert nkt.stock(db,status='missing')[1]==3


def test_nkt_inventory_complete_refuses_new_arrivals_and_shipments_need_reconference(warehouse):
    from test_nkt_receiving import stage,confirm as receive,manual_rows
    engine,a=warehouse;actor=a['admin'][0];seed(engine,actor);first,second=entries(engine);ident=create(engine,actor)
    mark(engine,actor,ident,'located',peg='001');mark(engine,actor,ident,'located',peg='004');old=review(engine,actor,ident)
    receive(engine,actor,stage(engine,actor,manual_rows('007','008')))
    with pytest.raises(inv.ReceivingError):confirm(engine,actor,ident,old)
    partial=create(engine,actor,'partial',[first]);mark(engine,actor,partial,'not_located',entry=first);old=review(engine,actor,partial)
    prep=prepare(engine,actor,['001','002','003']);reserve(engine,actor,prep);ship(engine,actor,prep,['001','002','003'])
    with pytest.raises(inv.ReceivingError):confirm(engine,actor,partial,old)
    mark(engine,actor,partial,'excluded',entry=first);confirm(engine,actor,partial,review(engine,actor,partial))
    with scoped(engine,4) as db:assert nkt.stock(db,status='missing')[1]==0 and nkt.stock(db,status='shipped')[1]==3


def test_nkt_inventory_revisions_unknown_material_and_legacy_loads_remain_blocked(warehouse):
    from test_nkt import setup,save,confirm as old_receive
    engine,a=warehouse;actor=a['admin'][0];setup(engine);old_receive(engine,actor,save(engine,actor,['LEGACY-PEG'],'ESC-01'));ident=create(engine,actor)
    with scoped(engine,4) as db:
        assert nkt.stock(db)[1]==1 and inv.summary(db,db.get(m.NktInventory,ident))['blocking']
    with pytest.raises(inv.ReceivingError):review(engine,actor,ident)
    result=mark(engine,actor,ident,'located',peg='UNKNOWN-PEG');assert result.get('incident')
    with scoped(engine,4) as db:
        inv.report_unknown(db,actor,ident,db.get(m.NktInventory,ident).version,None,'Material físico sem identidade comprovada')
        assert db.scalar(select(func.count()).select_from(m.NktPallet))==1 and db.scalar(select(func.count()).select_from(m.NktInventoryIncident))==3


def test_nkt_inventory_found_load_restored_without_new_identity_and_double_restore(warehouse):
    engine,a=warehouse;actor=a['admin'][0];seed(engine,actor);first,second=entries(engine);ident=create(engine,actor,'partial',[first]);mark(engine,actor,ident,'not_located',entry=first);original=confirm(engine,actor,ident,review(engine,actor,ident))
    rev=restore_review(engine,actor,ident,original,'001');key=secrets.token_hex(16);op=restore(engine,actor,rev,key);assert restore(engine,actor,rev,key)==op
    with scoped(engine,4) as db:
        assert nkt.stock(db)[1]==6 and nkt.stock(db,status='missing')[1]==0
        assert db.scalar(select(func.count()).select_from(m.NktPallet))==6 and db.scalar(select(func.count()).select_from(m.NktEntry))==2
        assert db.get(m.NktOperation,original).kind=='inventory_missing' and db.get(m.NktOperation,op).corrects_id==original
        assert db.get(m.NktInventory,ident).status=='confirmed'
    with pytest.raises(inv.ReceivingError):restore_review(engine,actor,ident,original,'001')


def test_nkt_inventory_partial_recovery_and_reserved_load_or_later_movements_block(warehouse):
    engine,a=warehouse;actor=a['admin'][0];seed(engine,actor);first,second=entries(engine);ident=create(engine,actor,'partial',[first]);mark(engine,actor,ident,'not_located',entry=first);original=confirm(engine,actor,ident,review(engine,actor,ident))
    rev=restore_review(engine,actor,ident,original,'001',False,['001']);restore(engine,actor,rev)
    prep=prepare(engine,actor,['001']);reserve(engine,actor,prep)
    with pytest.raises(inv.ReceivingError):restore_review(engine,actor,ident,original,'002',False,['002'])
    ship(engine,actor,prep,['001'])
    with pytest.raises(inv.ReceivingError):restore_review(engine,actor,ident,original,'001',False,['001'])
    rest=restore_review(engine,actor,ident,original,'002',False,['002','003']);restore(engine,actor,rest)
    with scoped(engine,4) as db:assert nkt.stock(db)[1]==5 and nkt.stock(db,status='shipped')[1]==1 and nkt.stock(db,status='missing')[1]==0


def test_nkt_inventory_concurrent_confirmations_rollback_and_stale_other_inventory(warehouse):
    engine,a=warehouse;actor=a['admin'][0];seed(engine,actor);first,second=entries(engine);one=create(engine,actor,'partial',[first]);two=create(engine,actor,'partial',[first])
    for ident in [one,two]:mark(engine,actor,ident,'not_located',entry=first)
    rev1=review(engine,actor,one);rev2=review(engine,actor,two)
    def fail(mapper,connection,target):
        if target.state=='missing' and connection.scalar(select(m.NktPallet.peg).where(m.NktPallet.id==target.pallet_id))=='002':raise RuntimeError('Falha fictícia durante o ajuste')
    event.listen(m.NktStockEvent,'after_insert',fail)
    try:
        with pytest.raises(RuntimeError):confirm(engine,actor,one,rev1)
    finally:event.remove(m.NktStockEvent,'after_insert',fail)
    with scoped(engine,4) as db:assert nkt.stock(db)[1]==6 and db.get(m.NktInventory,one).status=='draft'
    def execute(data):
        try:confirm(engine,actor,*data);return True
        except inv.ReceivingError:return False
    with ThreadPoolExecutor(2) as pool:assert sum(pool.map(execute,[(one,rev1),(two,rev2)]))==1
    with scoped(engine,4) as db:assert nkt.stock(db)[1]==3 and nkt.stock(db,status='missing')[1]==3


def test_nkt_inventory_runtime_immutable_permission_csrf_isolation_and_pdfs(restricted_web):
    from fastapi.testclient import TestClient
    from pypdf import PdfReader
    from iberbag.privileges import validate_runtime
    app,owner,engine,role,a,t=restricted_web;actor=a['admin'][0];seed(engine,actor);first,second=entries(engine)
    with engine.connect() as conn:validate_runtime(conn)
    with TestClient(app) as client:
        assert client.get('/nkt/inventories').status_code==403
        client.cookies.set('wms_session',t['operador'][0]);client.cookies.set('ddn_context','4')
        assert client.get('/nkt/inventories').status_code==200
        body=dict(csrf=t['operador'][1],context_id=4,key=secrets.token_hex(16),title='Inventário WEB fictício',mode='partial',**{f'entry_{first}':'yes'})
        assert client.post('/nkt/inventories/new',data=dict(body,csrf='bad')).status_code==403
        assert client.post('/nkt/inventories/new',data=dict(body,context_id=1)).status_code==409
        page=client.post('/nkt/inventories/new',data=body);assert page.status_code==200,page.text
        ident=int(str(page.url).split('/')[-1]);version=1
        body=dict(csrf=t['operador'][1],context_id=4,version=version,state='not_located',entry=first,reason='Comunicada ausência fictícia')
        page=client.post(f'/nkt/inventories/{ident}/check',data=body);assert page.status_code==200,page.text
        assert client.post(f'/nkt/inventories/{ident}/review',data=dict(body,key=secrets.token_hex(16))).status_code==403
        with scoped(engine,4) as db:
            assert nkt.stock(db)[1]==6
            doc=db.scalar(select(m.NktDocument).where(m.NktDocument.inventory_review_id.is_not(None)).order_by(m.NktDocument.id.desc()));raw=doc.pdf;did=doc.id
            assert 'Não localizada' in PdfReader(BytesIO(raw)).pages[0].extract_text()
        assert client.get(f'/nkt/documents/{did}.pdf').content==raw==client.get(f'/nkt/documents/{did}.pdf').content
        client.cookies.set('wms_session',t['admin'][0])
        rev=review(engine,actor,ident)
        with scoped(engine,4) as db:ver=db.get(m.NktInventory,ident).version
        final=dict(csrf=t['admin'][1],context_id=4,review=rev[0],digest=rev[1],key=secrets.token_hex(16),accepted='yes')
        assert client.post(f'/nkt/inventories/{ident}/confirm',data=dict(final,csrf='bad')).status_code==403
        page=client.post(f'/nkt/inventories/{ident}/confirm',data=final);assert page.status_code==200,page.text
        assert 'Em falta' in client.get('/nkt/stock?status=missing').text
        with scoped(engine,4) as db:original=db.scalar(select(m.NktOperation.id).where(m.NktOperation.inventory_id==ident,m.NktOperation.kind=='inventory_missing'))
        restore_data=dict(csrf=t['admin'][1],context_id=4,original=original,key=secrets.token_hex(16),peg='001',all_load='yes',reason='Carga encontrada e comprovada fictícia')
        page=client.post(f'/nkt/inventories/{ident}/restore/review',data=restore_data);assert page.status_code==200,page.text
        assert 'Recuperação revista' in page.text
        with scoped(engine,4) as db:r=db.scalar(select(m.NktInventoryReview).where(m.NktInventoryReview.inventory_id==ident,m.NktInventoryReview.kind=='restore'));rid=r.id;digest=r.digest
        page=client.post(f'/nkt/inventory-reviews/{rid}/restore',data=dict(csrf=t['admin'][1],context_id=4,key=secrets.token_hex(16),digest=digest,accepted='yes'));assert page.status_code==200,page.text
        assert 'Restituição já aplicada' in page.text
        client.cookies.set('ddn_context','2');assert client.get(f'/nkt/inventories/{ident}').status_code==403 and client.get(f'/nkt/documents/{did}.pdf').status_code==403
    with scoped(engine,4) as db:
        for table in ['nkt_inventory_checks','nkt_inventory_reviews','nkt_inventory_incidents']:
            with pytest.raises(DBAPIError):db.execute(text('DELETE FROM '+table))
            db.rollback()
        with pytest.raises(inv.ReceivingError):inv.review(db,a['operador'][0],ident,ver,secrets.token_hex(16),'Não autorizado')
    with scoped(engine,1) as db:assert not db.scalar(select(m.NktInventory.id))


def test_nkt_inventory_runtime_direct_cannot_discount_reserved_or_forge_restore(restricted_web):
    app,owner,engine,role,a,t=restricted_web;actor=a['admin'][0];seed(engine,actor);first,second=entries(engine);ident=create(engine,actor,'partial',[first]);mark(engine,actor,ident,'not_located',entry=first);rev=review(engine,actor,ident)
    with scoped(engine,4) as db:
        header=db.get(m.NktInventory,ident)
        with pytest.raises(DBAPIError,match='Explicit inventory review'):
            inv.review_record(db,db.get(m.User,actor),header,secrets.token_hex(16),'confirm',dict(scope=[first],groups=None,blocking=None),'Não omitir cargas por valores nulos')
        db.rollback()
    prep=prepare(engine,actor,['001']);reserve(engine,actor,prep)
    with scoped(engine,4) as db:
        user=db.get(m.User,actor);header=db.get(m.NktInventory,ident);r=db.get(m.NktInventoryReview,rev[0]);snap=json.loads(r.payload)
        changes=[loading.event(p['id'],p['event'],'missing') for row in snap['groups'] for p in row['source']['pallets'] if p['state']=='available']
        with pytest.raises(DBAPIError):inv.operation(db,user,header,r,secrets.token_hex(16),'inventory_missing',{},dict(inventory_id=ident,report=snap,events=changes))
        db.rollback();assert nkt.stock(db,status='reserved')[1]==1 and nkt.stock(db,status='missing')[1]==0
    with scoped(engine,4) as db:
        p=db.get(m.NktPreparation,prep);loading.cancel(db,actor,prep,p.version,loading.last_operation(db,prep),secrets.token_hex(16),'Prova fictícia')
    mark(engine,actor,ident,'not_located',entry=first);original=confirm(engine,actor,ident,review(engine,actor,ident))
    restore_rev=restore_review(engine,actor,ident,original,'001',False,['001'])
    with scoped(engine,4) as db:
        header=db.get(m.NktInventory,ident);r=db.get(m.NktInventoryReview,restore_rev[0]);report=json.loads(r.payload);other=next(p for p in report['source']['pallets'] if p['peg']=='002')
        with pytest.raises(DBAPIError,match='outside restoration effects'):
            inv.operation(db,db.get(m.User,actor),header,r,secrets.token_hex(16),'inventory_restore',{},dict(inventory_id=ident,report=report,events=[loading.event(other['id'],other['event'],'available')]),original)
        db.rollback();assert nkt.stock(db,status='missing')[1]==3


def native14_loading(engine,actor):
    """Actual frozen 0014 SQL columns/guards; never ORM inserts of future fields."""
    from sqlalchemy import Table,MetaData
    from iberbag.nkt_loading_pdf import render_pdf
    from hashlib import sha256
    with scoped(engine,4) as db:raw=loading.plan(db,'OUT-14-FICT','2027-02-01',0,['001','004'])
    with engine.begin() as conn:
        tables={name:Table(name,MetaData(),autoload_with=conn) for name in ['nkt_preparations','nkt_plan_revisions','nkt_operations','nkt_stock_events','nkt_documents']}
        now=m.now_utc();p=tables['nkt_preparations'];r=tables['nkt_plan_revisions'];o=tables['nkt_operations'];e=tables['nkt_stock_events'];d=tables['nkt_documents']
        ident=conn.scalar(p.insert().values(context_id=4,request_key=secrets.token_hex(16),created_by=actor,created_at=now,version=1,status='draft').returning(p.c.id))
        payload=nkt.dump(raw);rid=conn.scalar(r.insert().values(context_id=4,preparation_id=ident,version=1,payload=payload,digest=nkt.digest(payload),reason='Prova fictícia histórica',actor='admin',created_at=now).returning(r.c.id))
        def doc(revision=None,operation=None,label='Rascunho',items=None):
            pdf=render_pdf(ident,1,raw,label,'admin',now,items=items)
            conn.execute(d.insert().values(context_id=4,revision_id=revision,operation_id=operation,pdf=pdf,digest=sha256(pdf).hexdigest(),actor='admin',created_at=now));return pdf
        doc(revision=rid)
        changes=[loading.event(i['pallet_id'],None,'reserved') for i in raw['items']];snapshot=dict(plan=raw,before_operation=None,events=changes)
        opid=conn.scalar(o.insert().values(context_id=4,preparation_id=ident,revision_id=rid,request_key=secrets.token_hex(16),kind='reserve',payload='{}',snapshot=nkt.dump(snapshot),actor='admin',created_at=now).returning(o.c.id))
        eventids={row['pallet_id']:conn.scalar(e.insert().values(context_id=4,operation_id=opid,**row).returning(e.c.id)) for row in changes}
        conn.execute(p.update().where(p.c.id==ident).values(active_revision=rid,status='reserved'));doc(operation=opid,label='Reservado')
        first=raw['items'][0];changes=[loading.event(first['pallet_id'],eventids[first['pallet_id']],'shipped')];snapshot=dict(plan=raw,before_operation=opid,events=changes,loaded_items=[first],loaded_on='2027-02-02',gt='GT-14-FICT')
        shipped=conn.scalar(o.insert().values(context_id=4,preparation_id=ident,revision_id=rid,request_key=secrets.token_hex(16),kind='ship',payload='{}',snapshot=nkt.dump(snapshot),actor='admin',created_at=now).returning(o.c.id))
        conn.execute(e.insert().values(context_id=4,operation_id=shipped,**changes[0]));conn.execute(p.update().where(p.c.id==ident).values(status='partial'));pdf=doc(operation=shipped,label='Carregado',items=[first])
    return ident,shipped,pdf


def test_nkt_inventory_ddn14_upgrade_native_archive_and_encrypted_recovery(pg_databases,warehouse,tmp_path):
    from alembic import command
    from iberbag.migrations import config,REVISION
    from iberbag.schema_upgrade import apply_upgrade
    from iberbag.security import set_password
    from legacy_catalogue import seed_legacy_catalogue
    engine=pg_databases(restricted=True);target=pg_databases(restricted=True)
    with engine.begin() as conn:cfg=config();cfg.attributes['connection']=conn;command.upgrade(cfg,'ddn_0014')
    with scoped(engine) as db:
        seed_legacy_catalogue(db);u=m.User(username='admin',display_name='Fictício',role='admin',active=True);set_password(u,secrets.token_urlsafe(24));db.add(u);db.commit();actor=u.id
    seed(engine,actor);prep,ship_id,pdf=native14_loading(engine,actor)
    with engine.connect() as conn:
        before=transfer.inventory(transfer.read_data(conn));native=transfer.read_data(conn)
        for name in transfer.NKT_INVENTORY_TABLES | transfer.ALCOBRE_TABLES | (transfer.ALCOBRE_DISPATCH_TABLES | transfer.ALCOBRE_INVENTORY_TABLES):native.pop(name)
        for row in native['nkt_operations']:row.pop('inventory_id');row.pop('inventory_review_id')
        for row in native['nkt_documents']:row.pop('inventory_review_id')
        legacy=transfer.inventory(native);assert transfer.archive_inventory(conn,legacy)==legacy
    folder=tmp_path/'prior14';backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD);backup.restore(target,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert apply_upgrade(engine,folder)['revision']==REVISION=='ddn_0021'
    with engine.connect() as conn:assert transfer.inventory(transfer.read_data(conn))==before and transfer.archive_inventory(conn,legacy)==legacy
    first,second=entries(engine);ident=create(engine,actor);mark(engine,actor,ident,'not_located',entry=first);original=confirm(engine,actor,ident,review(engine,actor,ident))
    with scoped(engine,4) as db:
        assert nkt.stock(db,status='shipped')[1]==1 and nkt.stock(db,status='reserved')[1]==1 and nkt.stock(db,status='missing')[1]==2
        assert db.scalar(select(m.NktDocument.pdf).where(m.NktDocument.operation_id==ship_id))==pdf
        assert json.loads(db.get(m.NktOperation,ship_id).snapshot)['gt']=='GT-14-FICT'
    restore(engine,actor,restore_review(engine,actor,ident,original,'002'))
    restored=pg_databases(restricted=True);folder2=tmp_path/'after15';backup.backup(engine,folder2,runner=docker_client,passphrase=BACKUP_PASSWORD);backup.restore(restored,folder2,runner=docker_client,passphrase=BACKUP_PASSWORD)
    with engine.connect() as a,restored.connect() as b:assert transfer.inventory(transfer.read_data(a))==transfer.inventory(transfer.read_data(b))
    with scoped(restored,4) as db:
        assert nkt.stock(db)[1]==5 and nkt.stock(db,status='reserved')[1]==1
        assert db.scalar(select(m.NktDocument.pdf).where(m.NktDocument.operation_id==ship_id))==pdf
        assert db.get(m.NktPreparation,prep).status=='partial'


def test_nkt_inventory_a4_pdf_unknown_and_zero_vs_unverified_and_sqlite_refusal():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from datetime import datetime
    from pypdf import PdfReader
    from iberbag.nkt_inventory_pdf import render_pdf
    g=dict(entry_id=1,metadata=None,physical=2,reserved=0,available=2,excluded='legacy_unmapped',pallets=[dict(peg='000A',state='available')])
    data=dict(groups=[dict(source=g,decision='unverified',communicated=None)],totals=dict(located=0,not_located=0,excluded=0,unverified=1),blocking=['Material/carga por identificar'])
    pdf=render_pdf(1,1,'conference',data,'Fictício',datetime(2027,1,1),'Prova fictícia');text=PdfReader(BytesIO(pdf)).pages[0].extract_text()
    assert 'Por conferir' in text and 'desconto: 0' in text and 'Não informado' in text
    g['metadata']=dict(carga='FICT-LOAD',container='FICT-C',received_on='2027-01-01',items=[dict(peg=str(i).zfill(6),material=str(i)+' material '+('M'*450)) for i in range(60)])
    g.update(physical=60,available=60,excluded=None,pallets=[dict(peg=str(i).zfill(6),state='available') for i in range(60)])
    large=render_pdf(1,1,'conference',data,'Fictício',datetime(2027,1,1),'Dados longos fictícios sem truncagem')
    assert len(PdfReader(BytesIO(large)).pages)>1
    with Session(create_engine('sqlite://')) as db,pytest.raises(inv.ReceivingError,match='PostgreSQL'):inv.confirm(db,1,1,1,'digest','a'*32,True)


def test_nkt_inventory_recovery_revalidates_capacity_and_unknown_peg_is_pending(warehouse):
    engine,a=warehouse;actor=a['admin'][0]
    from test_nkt_receiving import stage,confirm as receive,manual_rows
    receive(engine,actor,stage(engine,actor,manual_rows('001','002')))
    first=entries(engine)[0];ident=create(engine,actor);mark(engine,actor,ident,'not_located',entry=first);original=confirm(engine,actor,ident,review(engine,actor,ident))
    rev=restore_review(engine,actor,ident,original,'001')
    # Synthetic catalogue inconsistency, without disabling any SQL protection.
    # The original stock is absent; restoration must still enforce current capacity.
    with scoped(engine,4) as db:db.execute(m.Location.__table__.update().where(m.Location.context_id==4,m.Location.codigo=='Chão').values(capacidade=1));db.commit()
    with pytest.raises(inv.ReceivingError,match='Capacidade'):restore(engine,actor,rev)
    with scoped(engine,4) as db:
        assert nkt.stock(db,status='missing')[1]==2
        pending=inv.review_restore(db,actor,ident,original,secrets.token_hex(16),'UNKNOWN',True,None,'Etiqueta desconhecida encontrada fictícia')
        assert isinstance(pending,dict) and db.get(m.NktInventoryIncident,pending['incident']).kind=='unknown_peg'
        assert db.scalar(select(func.count()).select_from(m.NktPallet))==2


def test_nkt_inventory_nkt_new_preparation_form_remains_usable(restricted_web):
    from fastapi.testclient import TestClient
    app,owner,engine,role,a,t=restricted_web
    with TestClient(app) as client:
        client.cookies.set('wms_session',t['admin'][0]);client.cookies.set('ddn_context','4')
        response=client.get('/nkt/preparations/new')
        assert response.status_code==200,response.text


def test_nkt_inventory_four_character_full_peg_requires_explicit_disambiguation(warehouse):
    from test_nkt_receiving import stage,confirm as receive
    engine,a=warehouse;actor=a['admin'][0]
    for name,peg in [('SHORT','0001'),('LONG','LONG0001')]:receive(engine,actor,stage(engine,actor,[dict(container=name,material='Material fictício',received_on='2027-01-01',pegs=[peg])]))
    ident=create(engine,actor);bad=mark(engine,actor,ident,'located',peg='0001');assert bad.get('incident')
    with scoped(engine,4) as db:
        inv.resolve_incident(db,actor,bad['incident'],'PEG completo confirmado, sem escolher por sufixo','0001')
        inv.record(db,actor,ident,db.get(m.NktInventory,ident).version,'located',communicated='0001',reason='Comprovado PEG completo de quatro caracteres',full_peg=True)
    mark(engine,actor,ident,'located',peg='LONG0001');confirm(engine,actor,ident,review(engine,actor,ident))
    with scoped(engine,4) as db:assert nkt.stock(db)[1]==2 and db.scalar(select(m.NktInventoryCheck.full_peg).where(m.NktInventoryCheck.communicated=='0001')) is True
