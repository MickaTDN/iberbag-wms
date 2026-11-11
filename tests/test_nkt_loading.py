"""Only fictitious NKT PEG, listings, GT and dates; disposable PostgreSQL 17."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from hashlib import sha256
import json,secrets
import pytest
from sqlalchemy import select,func,text,event
from sqlalchemy.exc import DBAPIError
from iberbag import nkt_loading as s,nkt,backup,transfer,database as m
from iberbag.privileges import validate_runtime
from iberbag.warehouse_settings import occupied,WarehouseError,add_location,rename_location,remove_location,set_capacity
from test_receiving import warehouse
from test_ddn_contexts import scoped
from test_security_regressions import restricted_web
from test_transfer import pg_databases,docker_client
from test_nkt_receiving import stage as receipt_stage,confirm as receive,manual_rows
from conftest import BACKUP_PASSWORD

def seed(engine,actor):
    rec=receipt_stage(engine,actor,[*manual_rows('001','003','FICT-IN-A','Material A'),*manual_rows('004','006','FICT-IN-B','Material B')]);receive(engine,actor,rec)

def prepare(engine,actor,pegs=None,ref='FICT-OUT',day='2027-02-01',order=1):
    with scoped(engine,4) as db:return s.save(db,actor,secrets.token_hex(16),ref,day,order,pegs or ['001','004'],'Pedido NKT fictício')

def reserve(engine,actor,ident,key=None):
    with scoped(engine,4) as db:
        p=db.get(m.NktPreparation,ident);r=s.latest(db,p)
        return s.reserve(db,actor,ident,p.version,r.digest,key or secrets.token_hex(16),True)

def ship(engine,actor,ident,pegs,key=None,gt='GT-FICT-1',day='2027-02-02'):
    with scoped(engine,4) as db:
        p=db.get(m.NktPreparation,ident)
        return s.ship(db,actor,ident,p.version,s.last_operation(db,ident),key or secrets.token_hex(16),pegs,day,gt)

def amend(engine,actor,ident,pegs=None,day='2027-02-05',order=3):
    with scoped(engine,4) as db:
        p=db.get(m.NktPreparation,ident);raw=json.loads(s.latest(db,p).payload)
        return s.save(db,actor,p.request_key,raw['reference'],day,order,pegs if pegs is not None else [i['peg'] for i in raw['items']],'Alteração explicitamente comprovada',ident,p.version)

def fix(engine,actor,ident,original,kind='restore',pegs=None,day=None,gt=None):
    with scoped(engine,4) as db:
        f=s.review_correction(db,actor,ident,original,secrets.token_hex(16),kind,pegs or ['001'] if kind=='restore' else '',day,gt,'Prova física e motivo fictícios')
        return f,db.get(m.NktCorrection,f).digest

def apply_fix(engine,actor,correction,key=None):
    with scoped(engine,4) as db:return s.apply_correction(db,actor,*correction,True,key or secrets.token_hex(16))

def test_nkt_explicit_selection_reservations_and_no_stock_deduction(restricted_web):
    app,owner,engine,role,a,t=restricted_web;actor=a['admin'][0];seed(engine,actor)
    with engine.connect() as conn:validate_runtime(conn)
    ident=prepare(engine,actor,['004','001']);key=secrets.token_hex(16);op=reserve(engine,actor,ident,key)
    assert reserve(engine,actor,ident,key)==op
    with scoped(engine,4) as db:
        assert nkt.stock(db)[1]==6 and nkt.stock(db,status='available')[1]==4 and nkt.stock(db,status='reserved')[1]==2
        rev=s.latest(db,db.get(m.NktPreparation,ident));raw=json.loads(rev.payload)
        assert [i['peg'] for i in raw['items']]==['004','001'] and [i['material'] for i in raw['items']]==['Material B','Material A']
        assert [i['container'] for i in raw['items']]==['FICT-IN-B','FICT-IN-A']
        docs=db.scalars(select(m.NktDocument)).all();assert len(docs)==2
        for doc in docs:assert sha256(doc.pdf).hexdigest()==doc.digest
        assert db.scalar(select(func.count()).select_from(m.NktPallet))==6
    for requested in [['001'],['004'],['999'],['001','001']]:
        with pytest.raises(s.ReceivingError):prepare(engine,actor,requested)
    with scoped(engine,1) as db:assert not db.scalar(select(m.NktStockEvent.id))

@pytest.mark.parametrize('same',['PEG-A','peg-a'])
def test_nkt_reservations_concurrent_case_duplicates(warehouse,same):
    from test_nkt import setup,save,confirm
    engine,a=warehouse;actor=a['admin'][0];setup(engine);entry=save(engine,actor,['PEG-A'],'Chão');confirm(engine,actor,entry)
    ids=[prepare(engine,actor,[code]) for code in ['PEG-A',same]]
    def run(ident):
        try:reserve(engine,actor,ident);return True
        except s.ReceivingError:return False
    with ThreadPoolExecutor(2) as pool:assert sum(pool.map(run,ids))==1
    with scoped(engine,4) as db:assert nkt.stock(db,status='reserved')[1]==1 and db.scalar(select(func.count()).select_from(m.NktStockEvent))==1


def test_nkt_schedule_revision_preserves_pegs_holds_and_immutable_pdf(warehouse):
    from pypdf import PdfReader
    engine,a=warehouse;actor=a['admin'][0];seed(engine,actor);ident=prepare(engine,actor);reserve(engine,actor,ident)
    with scoped(engine,4) as db:
        old=db.scalar(select(m.NktDocument).where(m.NktDocument.operation_id.is_not(None)));oldid=old.id;oldbytes=old.pdf;events=db.scalar(select(func.count()).select_from(m.NktStockEvent))
    amend(engine,actor,ident,day='2027-03-02',order=5)
    with pytest.raises(s.ReceivingError):ship(engine,actor,ident,['001'])
    reserve(engine,actor,ident)
    with scoped(engine,4) as db:
        assert db.scalar(select(func.count()).select_from(m.NktStockEvent))==events
        assert db.get(m.NktDocument,oldid).pdf==oldbytes
        docs=db.scalars(select(m.NktDocument).order_by(m.NktDocument.id)).all()
        assert len(docs)==4
        for d in docs:
            pdf=PdfReader(BytesIO(d.pdf));assert len(pdf.pages)==1
            assert '001' in pdf.pages[0].extract_text() and '004' in pdf.pages[0].extract_text()
        assert '2027-03-02' in PdfReader(BytesIO(docs[-1].pdf)).pages[0].extract_text()
    amend(engine,actor,ident,['001','005']);reserve(engine,actor,ident)
    with scoped(engine,4) as db:
        assert nkt.stock(db,'004',status='available')[1]==1 and nkt.stock(db,'005',status='reserved')[1]==1


def test_nkt_partial_shipments_distinct_dates_gt_and_idempotence(warehouse):
    engine,a=warehouse;actor=a['admin'][0];seed(engine,actor);ident=prepare(engine,actor,['001','002','004']);reserve(engine,actor,ident)
    with scoped(engine,4) as db:
        p=db.get(m.NktPreparation,ident);version=p.version;before=s.last_operation(db,ident)
    key=secrets.token_hex(16);first=ship(engine,actor,ident,['001'],key)
    with scoped(engine,4) as db:
        assert s.ship(db,actor,ident,version,before,key,['001'],'2027-02-02','GT-FICT-1')==first
        assert db.get(m.NktPreparation,ident).status=='partial'
        assert nkt.stock(db)[1]==5 and nkt.stock(db,status='reserved')[1]==2 and nkt.stock(db,status='shipped')[1]==1
        for requested in [['001'],['006'],['UNKNOWN']]:
            with pytest.raises(s.ReceivingError):s.ship(db,actor,ident,version,s.last_operation(db,ident),secrets.token_hex(16),requested,'2027-02-02','GT-FICT-2')
    second=ship(engine,actor,ident,['002'],gt='GT-FICT-2',day='2027-02-03')
    third=ship(engine,actor,ident,['004'],gt='GT-FICT-3',day='2027-02-04')
    with scoped(engine,4) as db:
        assert db.get(m.NktPreparation,ident).status=='loaded' and nkt.stock(db)[1]==3 and nkt.stock(db,status='reserved')[1]==0
        assert [json.loads(db.get(m.NktOperation,i).snapshot)['gt'] for i in [first,second,third]]==['GT-FICT-1','GT-FICT-2','GT-FICT-3']
        assert db.scalar(select(func.count()).select_from(m.NktPallet))==6
    with pytest.raises(s.ReceivingError):prepare(engine,actor,['001'])


def test_nkt_partial_cancellation_releases_only_unshipped_and_reprint(warehouse):
    from pypdf import PdfReader
    engine,a=warehouse;actor=a['admin'][0];seed(engine,actor);ident=prepare(engine,actor);reserve(engine,actor,ident);ship(engine,actor,ident,['001'])
    with scoped(engine,4) as db:
        p=db.get(m.NktPreparation,ident);before=s.last_operation(db,ident);key=secrets.token_hex(16)
        op=s.cancel(db,actor,ident,p.version,before,key,'Pedido fictício cancelado parcialmente')
        assert s.cancel(db,actor,ident,p.version,before,key,'Pedido fictício cancelado parcialmente')==op
        assert nkt.stock(db,'001',status='shipped')[1]==1 and nkt.stock(db,'004',status='available')[1]==1
        doc=db.scalar(select(m.NktDocument).where(m.NktDocument.operation_id==op));txt=PdfReader(BytesIO(doc.pdf)).pages[0].extract_text()
        assert '004' in txt and '001' not in txt
    second=prepare(engine,actor,['004']);reserve(engine,actor,second)
    with pytest.raises(s.ReceivingError):ship(engine,actor,ident,['004'])


def test_nkt_administrative_compensation_and_gt_correction_preserve_original(warehouse):
    engine,a=warehouse;actor=a['admin'][0];seed(engine,actor);ident=prepare(engine,actor);reserve(engine,actor,ident);original=ship(engine,actor,ident,['001'])
    with scoped(engine,4) as db:
        before=db.get(m.NktOperation,original).snapshot;oldpdf=db.scalar(select(m.NktDocument.pdf).where(m.NktDocument.operation_id==original))
    metadata=fix(engine,actor,ident,original,'metadata',day='2027-02-05',gt='GT-CORR-FICT');apply_fix(engine,actor,metadata)
    correction=fix(engine,actor,ident,original);key=secrets.token_hex(16);op=apply_fix(engine,actor,correction,key);assert apply_fix(engine,actor,correction,key)==op
    with scoped(engine,4) as db:
        assert db.get(m.NktOperation,original).snapshot==before and db.scalar(select(m.NktDocument.pdf).where(m.NktDocument.operation_id==original))==oldpdf
        assert nkt.stock(db)[1]==6 and nkt.stock(db,status='reserved')[1]==1 and db.get(m.NktPreparation,ident).status=='reconciliation'
        for table in ['nkt_stock_events','nkt_operations','nkt_plan_revisions','nkt_documents','nkt_corrections']:
            with pytest.raises(DBAPIError):db.execute(text('DELETE FROM '+table))
            db.rollback()
    with pytest.raises(s.ReceivingError):fix(engine,actor,ident,original)
    with pytest.raises(s.ReceivingError):ship(engine,actor,ident,['004'])
    amend(engine,actor,ident);reserve(engine,actor,ident);ship(engine,actor,ident,['001','004'],gt='GT-NEW-FICT')
    with pytest.raises(s.ReceivingError):apply_fix(engine,actor,correction)


def test_nkt_correction_revalidates_capacity_and_later_operations(warehouse):
    from test_nkt import setup,save,confirm
    engine,a=warehouse;actor=a['admin'][0];setup(engine)
    with scoped(engine,4) as db:set_capacity(db,'admin','ESC-01','1')
    entry=save(engine,actor,['OLD-PEG']);confirm(engine,actor,entry);ident=prepare(engine,actor,['OLD-PEG']);reserve(engine,actor,ident);original=ship(engine,actor,ident,['OLD-PEG'])
    correction=fix(engine,actor,ident,original,pegs=['OLD-PEG'])
    another=save(engine,actor,['NEW-PEG']);confirm(engine,actor,another)
    with pytest.raises(s.ReceivingError):apply_fix(engine,actor,correction)
    with scoped(engine,4) as db:assert nkt.stock(db,'OLD-PEG',status='shipped')[1]==1 and sum(q for _,q in occupied(db,['ESC-01']))==1


def test_nkt_loading_atomic_failure_stale_versions_and_concurrent_confirmation(warehouse):
    engine,a=warehouse;actor=a['admin'][0];seed(engine,actor);ident=prepare(engine,actor);reserve(engine,actor,ident)
    def fail(mapper,connection,target):
        if target.state=='shipped' and connection.scalar(select(m.NktPallet.peg).where(m.NktPallet.id==target.pallet_id))=='004':raise RuntimeError('Falha fictícia entre dois PEG')
    event.listen(m.NktStockEvent,'after_insert',fail)
    try:
        with pytest.raises(RuntimeError):ship(engine,actor,ident,['001','004'])
    finally:event.remove(m.NktStockEvent,'after_insert',fail)
    with scoped(engine,4) as db:
        assert nkt.stock(db,status='shipped')[1]==0 and nkt.stock(db,status='reserved')[1]==2
        before=s.last_operation(db,ident);version=db.get(m.NktPreparation,ident).version
    def run(index):
        with scoped(engine,4) as db:
            try:s.ship(db,actor,ident,version,before,secrets.token_hex(16),['001'],'2027-02-01',f'GT-FICT-{index}');return True
            except s.ReceivingError:return False
    with ThreadPoolExecutor(2) as pool:assert sum(pool.map(run,range(2)))==1
    amend(engine,actor,ident)
    with pytest.raises(s.ReceivingError):ship(engine,actor,ident,['004'])
    reserve(engine,actor,ident)
    with scoped(engine,4) as db:assert len(json.loads(db.scalar(select(m.NktOperation.snapshot).where(m.NktOperation.kind=='reserve').order_by(m.NktOperation.id.desc())))['pending_items'])==1


def test_nkt_loading_http_permissions_csrf_context_and_pdf_reprints(restricted_web):
    from fastapi.testclient import TestClient
    app,owner,engine,role,a,tokens=restricted_web;seed(engine,a['admin'][0]);actor=a['operador'][0]
    with TestClient(app) as client:
        assert client.get('/nkt/preparations').status_code==403
        client.cookies.set('wms_session',tokens['operador'][0]);client.cookies.set('ddn_context','4')
        body=dict(csrf=tokens['operador'][1],context_id=4,key=secrets.token_hex(16),reference='OUT-FICT',planned_on='2027-02-01',order=0,pegs='001\n004',reason='E-mail fictício NKT')
        assert client.post('/nkt/preparations/review',data=dict(body,csrf='bad')).status_code==403
        assert client.post('/nkt/preparations/review',data=dict(body,context_id=1)).status_code==409
        response=client.post('/nkt/preparations/review',data=body);assert response.status_code==200,response.text
        ident=int(str(response.url).split('/')[-1])
        with scoped(engine,4) as db:r=s.latest(db,db.get(m.NktPreparation,ident));data=dict(csrf=body['csrf'],context_id=4,version=1,digest=r.digest,key=secrets.token_hex(16),accepted='yes')
        assert client.post(f'/nkt/preparations/{ident}/reserve',data=data).status_code==200
        assert client.get('/nkt/preparations').status_code==200 and 'Reservado' in client.get('/nkt/stock').text
        assert client.post(f'/nkt/preparations/{ident}/corrections/review',data=dict(csrf=body['csrf'],context_id=4)).status_code==403
        with scoped(engine,4) as db:doc=db.scalar(select(m.NktDocument).where(m.NktDocument.operation_id.is_not(None)));did=doc.id;raw=doc.pdf;count=db.scalar(select(func.count()).select_from(m.NktStockEvent))
        assert client.get(f'/nkt/documents/{did}.pdf').content==raw==client.get(f'/nkt/documents/{did}.pdf').content
        with scoped(engine,4) as db:assert db.scalar(select(func.count()).select_from(m.NktStockEvent))==count
        client.cookies.set('ddn_context','2');assert client.get(f'/nkt/documents/{did}.pdf').status_code==403
    with scoped(engine,4) as db,pytest.raises(s.ReceivingError):s.review_correction(db,actor,ident,1,secrets.token_hex(16),'restore',['001'],None,None,'Motivo fictício')


def test_nkt_loading_prior_head_upgrade_encrypted_restore_and_original_metadata(pg_databases,warehouse,tmp_path):
    from alembic import command
    from iberbag.migrations import config,REVISION
    from iberbag.schema_upgrade import apply_upgrade
    from legacy_catalogue import seed_legacy_catalogue
    from iberbag.security import set_password
    engine=pg_databases(restricted=True);target=pg_databases(restricted=True)
    with engine.begin() as conn:cfg=config();cfg.attributes['connection']=conn;command.upgrade(cfg,'ddn_0013')
    with scoped(engine) as db:
        seed_legacy_catalogue(db);u=m.User(username='admin',display_name='Fictício',role='admin',active=True);set_password(u,secrets.token_urlsafe(24));db.add(u);db.commit();actor=u.id
    seed(engine,actor)
    with engine.connect() as conn:before=transfer.inventory(transfer.read_data(conn))
    folder=tmp_path/'prior';backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD);backup.restore(target,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert apply_upgrade(engine,folder)['revision']==REVISION=='ddn_0019'
    with engine.connect() as conn:assert transfer.inventory(transfer.read_data(conn))==before
    ident=prepare(engine,actor);reserve(engine,actor,ident);original=ship(engine,actor,ident,['001']);correction=fix(engine,actor,ident,original);apply_fix(engine,actor,correction)
    folder2=tmp_path/'after';target2=pg_databases(restricted=True);backup.backup(engine,folder2,runner=docker_client,passphrase=BACKUP_PASSWORD);backup.restore(target2,folder2,runner=docker_client,passphrase=BACKUP_PASSWORD)
    with engine.connect() as a,target2.connect() as b:assert transfer.inventory(transfer.read_data(a))==transfer.inventory(transfer.read_data(b))
    with scoped(target2,4) as db:assert nkt.stock(db)[1]==6 and len(db.scalars(select(m.NktDocument)).all())==5


def test_nkt_loading_sqlite_never_writes_and_pdf_keeps_unknown_material():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from pypdf import PdfReader
    from iberbag.nkt_loading_pdf import render_pdf
    from datetime import datetime
    plan=dict(reference='FICT-OUT',planned_on='2027-01-01',order=0,items=[dict(peg='000A',material=None)])
    raw=render_pdf(1,1,plan,'Rascunho','Fictício',datetime(2027,1,1));r=PdfReader(BytesIO(raw));assert len(r.pages)==1 and 'Não informado' in r.pages[0].extract_text()
    with Session(create_engine('sqlite://')) as db,pytest.raises(s.ReceivingError,match='PostgreSQL'):s.reserve(db,1,1,1,'digest','a'*32,True)


def test_nkt_loading_direct_runtime_cannot_forge_reservations_or_capacity(restricted_web):
    app,owner,engine,role,a,t=restricted_web;actor=a['admin'][0]
    from test_nkt import setup,save,confirm
    setup(owner);entry=save(engine,actor,['PEG-A','PEG-B']);confirm(engine,actor,entry)
    ident=prepare(engine,actor,['PEG-A','PEG-B']);reserve(engine,actor,ident);original=ship(engine,actor,ident,['PEG-A','PEG-B'])
    correction=fix(engine,actor,ident,original,pegs=['PEG-A','PEG-B'])
    with scoped(owner,4) as db:set_capacity(db,'admin','ESC-01','1')
    # Bypass application preflight deliberately: runtime SQL must reject the second
    # return in a batched INSERT even though a valid old correction preview exists.
    with scoped(engine,4) as db:
        prep=db.get(m.NktPreparation,ident);rev=s.latest(db,prep);f=db.get(m.NktCorrection,correction[0]);effects=json.loads(f.snapshot);changes=[s.event(e['item']['pallet_id'],e['event'],'available') for e in effects['effects']]
        snapshot=dict(plan=json.loads(rev.payload),before_operation=s.last_operation(db,ident),events=changes,effects=effects)
        with pytest.raises(DBAPIError,match='Restoration exceeds physical capacity'):s.operation(db,db.get(m.User,actor),prep,rev,secrets.token_hex(16),'restore',dict(correction=f.id,digest=f.digest),snapshot,correction=f.id,corrects=original)
        db.rollback();assert nkt.stock(db,status='shipped')[1]==2
    new_entry=save(engine,actor,['PEG-C'],'Chão');confirm(engine,actor,new_entry)
    new_prep=prepare(engine,actor,['PEG-C'])
    with scoped(engine,4) as db:
        prep=db.get(m.NktPreparation,new_prep);rev=s.latest(db,prep);user=db.get(m.User,actor);raw=json.loads(rev.payload)
        snapshot=dict(plan=raw,before_operation=None,events=[])
        op=s.operation(db,user,prep,rev,secrets.token_hex(16),'reserve',{},snapshot)
        prep.active_revision=rev.id;prep.status='reserved';db.flush();s.document(db,user,prep,raw,'reserve',operation=op.id)
        with pytest.raises(DBAPIError,match='Incomplete exclusive reservations'):db.commit()
        db.rollback();assert nkt.stock(db,status='reserved')[1]==0
    with scoped(engine,4) as db:
        with pytest.raises(DBAPIError):db.execute(text('UPDATE nkt_stock_events SET state=\'available\''))
        db.rollback()


def test_nkt_loading_validation_and_draft_cancel_keep_identity(warehouse):
    engine,a=warehouse;actor=a['admin'][0];seed(engine,actor)
    for codes in [[],['001','001'],['UNKNOWN']]:
        with pytest.raises(s.ReceivingError):
            with scoped(engine,4) as db:s.save(db,actor,secrets.token_hex(16),'FICT','2027-02-01',0,codes,'Comprovado fictício')
    ident=prepare(engine,actor)
    with scoped(engine,4) as db:
        p=db.get(m.NktPreparation,ident);before=s.last_operation(db,ident)
        s.cancel(db,actor,ident,p.version,before,secrets.token_hex(16),'Rascunho cancelado fictício')
        assert nkt.stock(db,status='available')[1]==6
    new=prepare(engine,actor,['001']);reserve(engine,actor,new)
    for day,gt in [('','GT-FICT'),('2027-02-01',''),('not-date','GT-FICT')]:
        with pytest.raises(s.ReceivingError):ship(engine,actor,new,['001'],day=day,gt=gt)
    with scoped(engine,4) as db:assert nkt.stock(db,status='available')[1]==5


def test_nkt_a4_one_load_per_page_and_no_silent_text_truncation():
    from datetime import datetime
    from pypdf import PdfReader
    from iberbag.nkt_loading_pdf import render_pdf
    plan=dict(reference='FICT-60',planned_on='2027-02-01',order=2,items=[dict(peg=str(i).zfill(6),material='Material fictício') for i in range(60)])
    raw=render_pdf(1,1,plan,'Reservado','Fictício',datetime(2027,1,1));reader=PdfReader(BytesIO(raw));assert len(reader.pages)==1
    assert '000000' in reader.pages[0].extract_text() and '000059' in reader.pages[0].extract_text() and '60 paletes' in reader.pages[0].extract_text()
    for i,item in enumerate(plan['items']):item['material']=str(i)+'M'*499
    with pytest.raises(s.ReceivingError,match='folha A4'):render_pdf(1,1,plan,'Reservado','Fictício',datetime(2027,1,1))
    plan['items']=[dict(peg='PEG-📦',material='Fictício')]
    with pytest.raises(s.ReceivingError,match='Caracteres'):render_pdf(1,1,plan,'Reservado','Fictício',datetime(2027,1,1))


def test_nkt_cancel_pending_revision_then_compensate_without_reviving_reservations(warehouse):
    engine,a=warehouse;actor=a['admin'][0];seed(engine,actor);ident=prepare(engine,actor);reserve(engine,actor,ident);original=ship(engine,actor,ident,['001'])
    amend(engine,actor,ident,['001','005'])
    with scoped(engine,4) as db:
        p=db.get(m.NktPreparation,ident);s.cancel(db,actor,ident,p.version,s.last_operation(db,ident),secrets.token_hex(16),'Cancelamento após revisão fictícia')
    correction=fix(engine,actor,ident,original);apply_fix(engine,actor,correction)
    with scoped(engine,4) as db:
        assert db.get(m.NktPreparation,ident).status=='cancelled' and nkt.stock(db,status='available')[1]==6 and nkt.stock(db,status='reserved')[1]==0
    with pytest.raises(s.ReceivingError):reserve(engine,actor,ident)
