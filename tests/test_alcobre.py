"""Fictitious bobines/guides only; no business file or operational connection."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from io import BytesIO
from threading import Barrier
import json
import secrets
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func, text, event
from sqlalchemy.exc import DBAPIError
from iberbag import alcobre as s, alcobre_pdf, database as m, backup, transfer
from iberbag.privileges import validate_runtime
from iberbag.warehouse_settings import occupied, set_capacity, remove_location, set_location_active, WarehouseError
from test_receiving import warehouse
from test_ddn_contexts import scoped
from test_security_regressions import restricted_web
from test_transfer import pg_databases, docker_client
from conftest import BACKUP_PASSWORD

def pdf(numbers=None, *, end=True, header='BOBINA', duplicate=False):
    from reportlab.pdfgen.canvas import Canvas
    out=BytesIO();c=Canvas(out)
    c.drawString(50,760,'Guia inteiramente fictícia')
    c.drawString(50,720,header);c.drawString(250,720,'OUTRA COLUNA')
    for i,number in enumerate(numbers or ['000101','000102']):
        c.drawString(50,690-i*20,number);c.drawString(250,690-i*20,'999999')
    if duplicate:c.drawString(50,680,'000101')
    if end:c.drawString(250,600,'TOTAL')
    c.showPage();c.save();return out.getvalue()

def positions(engine, actor, count=4):
    with scoped(engine,5) as db:
        zone=db.scalar(select(m.AlcobreZone).where(m.AlcobreZone.code=='A'))
        assert db.scalar(select(func.count()).select_from(m.AlcobreZone))==14
        assert db.scalar(select(func.sum(m.AlcobreZone.positions)))==210
        for i in range(1,count+1):s.configure_slot(db,actor,zone.id,i,f'FICT-ALC-{i}')

def draft(engine, actor, mode='direct', source=None):
    with scoped(engine,5) as db:
        if source:return s.preview_file(db,actor,secrets.token_hex(16),source,'guia-ficticia.pdf')
        return s.create(db,actor,secrets.token_hex(16),mode,'Origem fictícia','2027-03-01')

def review(engine,actor,ident,rows=None,kind='receive',quantity=None,manual=False):
    with scoped(engine,5) as db:
        e=db.get(m.AlcobreEntry,ident)
        rid=s.review(db,actor,ident,e.version,secrets.token_hex(16),kind,rows or [],quantity,'Prova física fictícia comunicada ao escritório',manual)
        return rid,db.get(m.AlcobreReview,rid).digest

def confirm(engine,actor,rev,key=None):
    with scoped(engine,5) as db:return s.confirm(db,actor,*rev,key or secrets.token_hex(16),True)

def row(number='000101',local=None):return dict(number=number,local=local)

def inventory(engine):
    with engine.connect() as conn:return transfer.inventory(transfer.read_data(conn))

def test_alcobre_pdf_column_only_original_numbers_and_sqlite_refusal():
    source=pdf();report=alcobre_pdf.analyze(source)
    assert report['reliable'],report
    assert [r['number'] for r in report['candidates']]==['000101','000102']
    assert all(r['original']==r['number'] and r['page']==1 for r in report['candidates'])
    assert '999999' not in [r['number'] for r in report['candidates']]
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    with Session(create_engine('sqlite://')) as db:
        for fn,args in [(s.create,('a'*32,'pending')),(s.review,(1,0,'a'*32,'receive',[],1,'Prova')),(s.confirm,(1,'x','a'*32,True)),(s.configure_slot,(1,1,'FICT'))]:
            with pytest.raises(s.ReceivingError,match='PostgreSQL'):fn(db,1,*args)

@pytest.mark.parametrize('content',[pdf(header='OUTROS'),pdf(end=False),pdf(numbers=['000101','000101']),pdf(numbers=['NÚMERO ILEGÍVEL']),b'%PDF-1.7\nmalformed'])
def test_alcobre_pdf_ambiguous_layouts_duplicates_and_malformed_never_automatic(content):
    assert not alcobre_pdf.analyze(content)['reliable']
    for invalid in [b'not a PDF',b'%PDF-'+b'x'*(5*1024*1024)]:
        with pytest.raises(ValueError):alcobre_pdf.analyze(invalid)

def test_alcobre_pdf_admission_limit_blocks_without_starting_another_parser(monkeypatch):
    def forbidden(content):raise AssertionError('No extra worker may start at capacity')
    monkeypatch.setattr(alcobre_pdf,'_analyze_worker',forbidden)
    assert alcobre_pdf._PARSERS.acquire(blocking=False)
    assert alcobre_pdf._PARSERS.acquire(blocking=False)
    try:
        report=alcobre_pdf.analyze(pdf())
        assert not report['reliable'] and not report['candidates'] and 'ocupada' in report['issues'][0]
    finally:
        alcobre_pdf._PARSERS.release();alcobre_pdf._PARSERS.release()

def test_alcobre_pdf_never_reads_right_aligned_neighbor_as_bobine():
    from reportlab.pdfgen.canvas import Canvas
    out=BytesIO();c=Canvas(out)
    c.drawString(50,720,'BOBINA');c.drawString(250,720,'METROS')
    # Empty BOBINA cell, neighbor number begins left of its own header anchor.
    c.drawRightString(250,690,'999999');c.drawString(250,600,'TOTAL')
    c.showPage();c.save()
    report=alcobre_pdf.analyze(out.getvalue())
    assert not report['reliable'] and not report['candidates']

def test_alcobre_pdf_direct_putaway_preserves_one_identity_and_source(restricted_web):
    app,owner,engine,role,a,t=restricted_web;actor=a['admin'][0];positions(engine,actor,2)
    with engine.connect() as conn:validate_runtime(conn)
    source=pdf();ident=draft(engine,actor,source=source)
    with scoped(engine,5) as db:
        assert s.preview_file(db,actor,secrets.token_hex(16),source,'renamed.pdf')==ident
        assert s.stock(db)[1]==0
    rev=review(engine,actor,ident,[row(),row('000102')]);op=confirm(engine,actor,rev);assert confirm(engine,actor,rev)==op
    with scoped(engine,5) as db:
        assert s.stock(db,state='available')[1]==0 and s.stock(db,state='unlocated')[1]==2
        e=db.get(m.AlcobreEntry,ident);assert e.quantity==2 and e.source_pdf==source and e.source_digest==sha256(source).hexdigest()
        origin=(e.id,e.created_at,e.source_pdf,e.source_digest)
    placed=review(engine,actor,ident,[row('000101','FICT-ALC-1')],kind='place');confirm(engine,actor,placed)
    with scoped(engine,5) as db:
        assert s.stock(db,'0101',state='available')[1]==1 and s.stock(db,local='FICT-ALC-1')[1]==1
        assert sum(q for _,q in occupied(db,['FICT-ALC-1','FICT-ALC-2']))==1
        e=db.get(m.AlcobreEntry,ident);assert (e.id,e.created_at,e.source_pdf,e.source_digest)==origin
    with pytest.raises(s.ReceivingError):review(engine,actor,ident,[row('000101','FICT-ALC-2')],kind='place')
    with TestClient(app) as client:
        client.cookies.set('wms_session',t['admin'][0]);client.cookies.set('ddn_context','5')
        for path in ['/', '/alcobre/entries',f'/alcobre/entries/{ident}','/alcobre/stock','/alcobre/settings','/settings/locations','/map']:
            response=client.get(path);assert response.status_code==200,(path,response.text)
        before=inventory(engine)
        for _ in range(2):assert client.get(f'/alcobre/entries/{ident}/source.pdf').content==source
        assert inventory(engine)==before

def test_alcobre_partial_identification_conserves_physical_pending_and_known_locations(warehouse):
    engine,a=warehouse;actor=a['operador'][0];positions(engine,a['admin'][0],3)
    ident=draft(engine,actor,'pending');confirm(engine,actor,review(engine,actor,ident,quantity=3))
    with scoped(engine,5) as db:
        assert s.stock(db)[1]==0 and s.stock(db,state='pending')[2][0][1]==3
    rev=review(engine,actor,ident,[row()],kind='identify');confirm(engine,actor,rev);assert confirm(engine,actor,rev)
    with scoped(engine,5) as db:
        assert s.stock(db,state='unlocated')[1]==1 and s.stock(db,state='pending')[2][0][1]==2
        assert s.stock(db,state='available')[1]==0
    with pytest.raises(s.ReceivingError):review(engine,actor,ident,[row('000102'),row('000103'),row('000104')],kind='identify')
    last=review(engine,actor,ident,[row('000102','FICT-ALC-2'),row('000103','FICT-ALC-3')],kind='identify');confirm(engine,actor,last)
    confirm(engine,actor,review(engine,actor,ident,[row('000101','FICT-ALC-1')],kind='place'))
    with scoped(engine,5) as db:
        assert s.stock(db)[1]==s.stock(db,state='available')[1]==3 and not s.stock(db,state='pending')[2]
        assert db.scalar(select(func.count()).select_from(m.AlcobreEntry))==1
        assert db.get(m.AlcobreEntry,ident).quantity==3
        assert sum(q for _,q in occupied(db,[f'FICT-ALC-{i}' for i in range(1,4)]))==3

@pytest.mark.parametrize('conflict',['identity','capacity','pending'])
def test_alcobre_concurrent_confirmation_and_pending_reference_are_atomic(warehouse,conflict):
    engine,a=warehouse;actor=a['admin'][0];positions(engine,actor,1)
    ident=draft(engine,actor,'pending' if conflict=='pending' else 'direct')
    if conflict=='pending':confirm(engine,actor,review(engine,actor,ident,quantity=2))
    other=ident if conflict=='pending' else draft(engine,actor)
    revisions=[review(engine,actor,target,[row('BoB-001' if conflict=='identity' or i==0 else 'BoB-002','FICT-ALC-1' if conflict=='capacity' else None)],kind='identify' if conflict=='pending' else 'receive') for i,target in enumerate([ident,other])]
    barrier=Barrier(2)
    def run(rev):
        barrier.wait(timeout=10)
        try:confirm(engine,actor,rev);return True
        except s.ReceivingError:return False
    with ThreadPoolExecutor(2) as pool:assert sum(pool.map(run,revisions))==1
    with scoped(engine,5) as db:assert s.stock(db)[1]==1

def test_alcobre_invalid_numbers_capacity_zone_and_context_never_create_stock(warehouse):
    engine,a=warehouse;actor=a['admin'][0];positions(engine,actor,2);ident=draft(engine,actor)
    for rows in [[row('')],[row('001'),row('001')],[row('bOb'),row('BOB')],[row('0001','UNKNOWN')],[row('1','FICT-ALC-1'),row('2','FICT-ALC-1')]]:
        with pytest.raises(s.ReceivingError):review(engine,actor,ident,rows)
    with scoped(engine,5) as db:
        zone=db.scalar(select(m.AlcobreZone).where(m.AlcobreZone.code=='A'))
        with pytest.raises(s.ReceivingError):s.configure_slot(db,actor,zone.id,16,'FICT-16')
        with pytest.raises(s.ReceivingError):s.configure_slot(db,actor,zone.id,3,'A101')  # Owned by Iberbag/Ovar.
        with pytest.raises(s.ReceivingError):s.configure_slot(db,actor,zone.id,3,'Chão')
        with pytest.raises(s.ReceivingError):s.configure_zone(db,actor,'A',1,True,zone.id)
        with pytest.raises(WarehouseError):set_capacity(db,'admin','FICT-ALC-1','2')
    rev=review(engine,actor,ident,[row('1','FICT-ALC-1')]);confirm(engine,actor,rev)
    with scoped(engine,5) as db:
        for operation in [lambda:remove_location(db,'admin','FICT-ALC-1'),lambda:set_location_active(db,'admin','FICT-ALC-1',False)]:
            with pytest.raises(WarehouseError):operation()
    for ctx in (1,2,3,4):
        with scoped(engine,ctx) as db:
            with pytest.raises(s.ReceivingError):s.confirm(db,actor,*rev,secrets.token_hex(16),True)
            assert db.get(m.AlcobreEntry,ident) is None

def test_alcobre_confirmation_rollback_and_runtime_immutable_books(restricted_web):
    app,owner,engine,role,a,t=restricted_web;actor=a['admin'][0];ident=draft(engine,actor)
    rev=review(engine,actor,ident,[row('1'),row('2')]);before=inventory(engine)
    def fail(mapper,connection,target):
        if target.number=='2':raise RuntimeError('Injected failure between bobines')
    event.listen(m.AlcobreBobine,'after_insert',fail)
    try:
        with pytest.raises(RuntimeError):confirm(engine,actor,rev)
    finally:event.remove(m.AlcobreBobine,'after_insert',fail)
    assert inventory(engine)==before
    op=confirm(engine,actor,rev)
    with scoped(engine,5) as db:
        for name in ('alcobre_bobines','alcobre_placements','alcobre_reviews','alcobre_actions','alcobre_owners','alcobre_slots'):
            with pytest.raises(DBAPIError):db.execute(text('DELETE FROM '+name))
            db.rollback()
        with pytest.raises(DBAPIError):
            db.add(m.AlcobreBobine(entry_id=ident,action_id=op,client_id=5,number='UNREVIEWED',number_key='unreviewed'));db.commit()
        db.rollback()
        with pytest.raises(DBAPIError):db.execute(m.AlcobreEntry.__table__.update().where(m.AlcobreEntry.id==ident).values(quantity=10))
        db.rollback();assert s.stock(db)[1]==2

def test_alcobre_pdf_unreliable_corrections_and_repeated_numbers_require_proof(warehouse):
    engine,a=warehouse;actor=a['admin'][0];source=pdf(end=False);ident=draft(engine,actor,source=source)
    with pytest.raises(s.ReceivingError,match='manual'):review(engine,actor,ident,[row('PROVEN-001')])
    confirm(engine,actor,review(engine,actor,ident,[row('PROVEN-001')],manual=True))
    other=draft(engine,actor)
    with pytest.raises(s.ReceivingError):review(engine,actor,other,[row('proven-001')])
    with scoped(engine,5) as db:
        e=db.get(m.AlcobreEntry,ident);assert not json.loads(e.extraction)['reliable'] and e.source_pdf==source

def test_alcobre_http_upload_permissions_csrf_metadata_and_context(restricted_web):
    app,owner,engine,role,a,t=restricted_web;source=pdf()
    with TestClient(app) as client:
        client.cookies.set('ddn_context','5');client.cookies.set('wms_session',t['operador'][0])
        body=dict(csrf=t['operador'][1],context_id=5,request_key=secrets.token_hex(16))
        assert client.post('/alcobre/entries/upload',data=body,files={'pdf':('fict.pdf',source)}).status_code==403
        for route,data in [('/alcobre/settings/zones',dict(code='Z',positions=15,active='yes')),('/alcobre/settings/slots',dict(zone=1,position=1,code='FICT'))]:
            assert client.post(route,data=dict(body,**data)).status_code==403
        client.cookies.set('wms_session',t['admin'][0]);body['csrf']=t['admin'][1]
        assert client.post('/alcobre/entries/upload',data=dict(body,csrf='bad'),files={'pdf':('fict.pdf',source)}).status_code==403
        response=client.post('/alcobre/entries/upload',data=body,files={'pdf':('fict.pdf',source)})
        assert response.status_code==200,response.text
        ident=int(str(response.url).split('/')[-1])
        data=dict(csrf=t['admin'][1],context_id=5,key=secrets.token_hex(16),version=0,kind='receive',numbers='000101\n000102',places='',reason='Guia fictícia conferida',reference='GT-IN-FICT',received_on='2027-04-01')
        response=client.post(f'/alcobre/entries/{ident}/review',data=data);assert response.status_code==200,response.text
        with scoped(engine,5) as db:r=db.scalar(select(m.AlcobreReview).where(m.AlcobreReview.entry_id==ident));rev=r.id;digest=r.digest
        data=dict(csrf=t['admin'][1],context_id=5,key=secrets.token_hex(16),digest=digest,accepted='yes')
        response=client.post(f'/alcobre/reviews/{rev}/confirm',data=data);assert response.status_code==200,response.text
        assert client.post(f'/alcobre/reviews/{rev}/confirm',data=data).status_code==200
        assert client.get(f'/alcobre/entries/{ident}/source.pdf').content==source
        assert 'Pendente de localização' in client.get('/alcobre/stock?number=000101').text
        with scoped(engine,5) as db:
            e=db.get(m.AlcobreEntry,ident);assert (e.reference,e.received_on)==('GT-IN-FICT','2027-04-01')
        client.cookies.set('ddn_context','1')
        assert client.get(f'/alcobre/entries/{ident}').status_code==403
        assert client.post(f'/alcobre/reviews/{rev}/confirm',data=data).status_code==409
        assert client.get(f'/alcobre/entries/{ident}/source.pdf').status_code==403

def test_alcobre_ddn15_upgrade_encrypted_restore_and_original_histories(pg_databases,warehouse,tmp_path):
    from alembic import command
    from iberbag.migrations import config, REVISION
    from iberbag.schema_upgrade import apply_upgrade
    from test_nkt_loading import seed,prepare,reserve,ship
    from legacy_catalogue import seed_legacy_catalogue
    from iberbag.security import set_password
    engine=pg_databases(restricted=True);previous=pg_databases(restricted=True)
    with engine.begin() as conn:cfg=config();cfg.attributes['connection']=conn;command.upgrade(cfg,'ddn_0015')
    with scoped(engine) as db:
        seed_legacy_catalogue(db);u=m.User(username='admin',display_name='Fictício',role='admin',active=True);set_password(u,secrets.token_urlsafe(24));db.add(u);db.commit();actor=u.id
    seed(engine,actor);p=prepare(engine,actor);reserve(engine,actor,p);ship(engine,actor,p,['001'])
    with engine.connect() as conn:
        native=transfer.read_data(conn)
        for name in transfer.ALCOBRE_TABLES | (transfer.ALCOBRE_DISPATCH_TABLES | transfer.ALCOBRE_INVENTORY_TABLES):native.pop(name)
        native15=transfer.inventory(native);assert transfer.archive_inventory(conn,native15)==native15
    before=inventory(engine);folder=tmp_path/'before16'
    backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert backup.restore(previous,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'
    assert apply_upgrade(engine,folder)['revision']==REVISION=='ddn_0019'
    assert inventory(engine)==before
    with engine.connect() as conn:assert transfer.archive_inventory(conn,native15)==native15
    positions(engine,actor,2);ident=draft(engine,actor,'pending');confirm(engine,actor,review(engine,actor,ident,quantity=2))
    confirm(engine,actor,review(engine,actor,ident,[row('000FICT','FICT-ALC-1')],kind='identify'))
    source=pdf();guide=draft(engine,actor,source=source);confirm(engine,actor,review(engine,actor,guide,[row('000101','FICT-ALC-2'),row('000102')]))
    target=pg_databases(restricted=True);folder2=tmp_path/'after16';original=inventory(engine)
    backup.backup(engine,folder2,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert backup.restore(target,folder2,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'
    assert inventory(target)==original
    with scoped(target,5) as db:
        assert s.stock(db,state='available')[1]==2 and s.stock(db,state='unlocated')[1]==1 and s.stock(db,state='pending')[2][0][1]==1
        assert db.get(m.AlcobreEntry,guide).source_pdf==source

def test_alcobre_administrator_configures_zones_and_corrects_only_empty_positions(restricted_web):
    app,owner,engine,role,a,t=restricted_web;actor=a['admin'][0]
    with scoped(engine,5) as db:
        zone=s.configure_zone(db,actor,'Z',2)
        slot=s.configure_slot(db,actor,zone,1,'FICT-UNUSED')
        assert s.configure_slot(db,actor,zone,1,'FICT-CORRECTED')==slot
        assert s.configure_slot(db,actor,zone,1,'FICT-CORRECTED')==slot
        s.configure_slot(db,actor,zone,2,'FICT-Z2')
        s.configure_zone(db,actor,'Z',3,True,zone)
        s.configure_slot(db,actor,zone,3,'FICT-Z3')
        with pytest.raises(s.ReceivingError):s.configure_zone(db,a['operador'][0],'X',15)
    ident=draft(engine,actor);confirm(engine,actor,review(engine,actor,ident,[row('Z001','FICT-CORRECTED'),row('Z002','FICT-Z2'),row('Z003','FICT-Z3')]))
    with scoped(engine,5) as db:
        assert s.stock(db,state='available')[1]==3
        with pytest.raises(s.ReceivingError):s.configure_slot(db,actor,zone,1,'FICT-MOVED')
        with pytest.raises(s.ReceivingError):s.configure_zone(db,actor,'Z',3,False,zone)
        with pytest.raises(DBAPIError):db.execute(m.Location.__table__.update().where(m.Location.codigo=='FICT-Z2',m.Location.context_id==5).values(capacidade=2));db.commit()
        db.rollback()

@pytest.mark.parametrize('quantity',[0,-1,'1.5','',None,True,301])
def test_alcobre_unknown_material_requires_proved_positive_whole_quantity(warehouse,quantity):
    engine,a=warehouse;actor=a['admin'][0];ident=draft(engine,actor,'pending')
    with pytest.raises(s.ReceivingError):review(engine,actor,ident,quantity=quantity)
    with scoped(engine,5) as db:
        assert not s.stock(db)[2] and s.stock(db)[1]==0
        assert db.get(m.AlcobreEntry,ident).quantity is None

def test_alcobre_pdf_parsing_releases_lock_and_revalidates_actor(restricted_web,monkeypatch):
    from threading import Event
    app,owner,engine,role,a,t=restricted_web;entered=Event();release=Event()
    def slow(content):
        entered.set();assert release.wait(10)
        return dict(reliable=True,candidates=[dict(number='000101',page=1,x=50,y=690)],issues=[])
    monkeypatch.setattr(alcobre_pdf,'analyze',slow)
    def upload():
        with TestClient(app) as client:
            client.cookies.set('wms_session',t['admin'][0]);client.cookies.set('ddn_context','5')
            return client.post('/alcobre/entries/upload',data=dict(csrf=t['admin'][1],context_id=5,request_key=secrets.token_hex(16)),files={'pdf':('fict.pdf',pdf())})
    with ThreadPoolExecutor(1) as pool:
        future=pool.submit(upload)
        try:
            assert entered.wait(5)
            with owner.begin() as conn:
                assert conn.scalar(text('SELECT pg_try_advisory_xact_lock(72834001)'))
                conn.execute(m.User.__table__.update().where(m.User.id==a['admin'][0]).values(role='operador'))
        finally:release.set()
        assert future.result(timeout=10).status_code==403
    with scoped(engine,5) as db:assert db.scalar(select(func.count()).select_from(m.AlcobreEntry))==0

def test_alcobre_runtime_refuses_null_or_excess_identification_review(restricted_web):
    app,owner,engine,role,a,t=restricted_web;actor=a['admin'][0];ident=draft(engine,actor,'pending')
    confirm(engine,actor,review(engine,actor,ident,quantity=1))
    with scoped(engine,5) as db:
        for rows in [None,[row('FORGED-1'),row('FORGED-2')]]:
            raw=s.dump(dict(kind='identify',rows=rows,quantity=1,identified_before=0,manual=True))
            with pytest.raises(DBAPIError):
                db.add(m.AlcobreReview(entry_id=ident,request_key=secrets.token_hex(16),kind='identify',version=1,payload=raw,digest=s.digest(raw),reason='Prova fictícia inválida',actor='admin'));db.commit()
            db.rollback()
        assert s.stock(db)[1]==0 and s.stock(db,state='pending')[2][0][1]==1

def test_alcobre_upgrade_refuses_legacy_grouped_stock_without_hiding_or_converting_it(pg_databases):
    from alembic import command
    from iberbag.migrations import config
    from sqlalchemy import inspect
    from legacy_catalogue import seed_legacy_catalogue
    engine=pg_databases(restricted=True)
    with engine.begin() as conn:
        cfg=config();cfg.attributes['connection']=conn;command.upgrade(cfg,'ddn_0015')
    with scoped(engine) as db:seed_legacy_catalogue(db)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO stock(context_id,artigo,lote,carga,local,quantidade) VALUES (5,'FICT-LEGACY','FICT-LOT','FICT-ORIGIN','Chão',2)"))
    before=inventory(engine)
    with pytest.raises(RuntimeError,match='Stock Alcobre legado'):
        with engine.begin() as conn:
            cfg=config();cfg.attributes['connection']=conn;command.upgrade(cfg,'head')
    assert inventory(engine)==before
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT version_num FROM alembic_version'))=='ddn_0015'
        assert 'alcobre_bobines' not in inspect(conn).get_table_names()
        assert conn.scalar(text('SELECT quantidade FROM stock WHERE context_id=5'))==2

def test_alcobre_admin_completes_inactive_authors_draft_without_reassigning_history(restricted_web):
    app,owner,engine,role,a,t=restricted_web;actor=a['admin'][0];original=a['operador'][0]
    ident=draft(engine,original)
    with owner.begin() as conn:conn.execute(m.User.__table__.update().where(m.User.id==original).values(active=False))
    with pytest.raises(s.ReceivingError):review(engine,original,ident,[row()])
    rev=review(engine,actor,ident,[row()]);confirm(engine,actor,rev)
    with scoped(engine,5) as db:
        entry=db.get(m.AlcobreEntry,ident)
        assert entry.created_by==original and entry.confirmed_by=='admin'
        assert db.get(m.AlcobreReview,rev[0]).actor=='admin' and s.stock(db,state='unlocated')[1]==1

def test_alcobre_original_pdf_survives_importers_role_change(restricted_web):
    from iberbag.security import set_password
    from sqlalchemy.orm import Session
    app,owner,engine,role,a,t=restricted_web;original=a['admin'][0];source=pdf()
    ident=draft(engine,original,source=source)
    with Session(owner) as db:
        other=m.User(username='fict-admin-2',display_name='Administrador fictício',role='admin',active=True)
        set_password(other,secrets.token_urlsafe(24));db.add(other);db.flush();actor=other.id
        db.execute(m.User.__table__.update().where(m.User.id==original).values(role='operador'));db.commit()
    with scoped(engine,5) as db,pytest.raises(s.ReceivingError):s.preview_file(db,original,secrets.token_hex(16),source,'new.pdf')
    confirm(engine,actor,review(engine,actor,ident,[row(),row('000102')]))
    with scoped(engine,5) as db:
        entry=db.get(m.AlcobreEntry,ident)
        assert entry.source_pdf==source and entry.created_by==original and entry.confirmed_by=='fict-admin-2'
