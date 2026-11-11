"""Only generated guides and fictitious bobines; disposable PostgreSQL 17."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from hashlib import sha256
from io import BytesIO
import json,secrets
from threading import Barrier
import pytest
from sqlalchemy import select,func,event,text
from sqlalchemy.exc import DBAPIError
from fastapi.testclient import TestClient
from pypdf import PdfReader
from iberbag import alcobre as a,alcobre_dispatch as s,database as m,transfer,backup
from iberbag.alcobre_dispatch_pdf import render_pdf
from test_receiving import warehouse
from test_security_regressions import restricted_web
from test_transfer import pg_databases,docker_client
from test_ddn_contexts import scoped
from test_alcobre import positions,draft as incoming,review as incoming_review,confirm as receive,row
from conftest import BACKUP_PASSWORD

def pdf(numbers=None,label=None,end=True):
    from reportlab.pdfgen.canvas import Canvas
    out=BytesIO();c=Canvas(out);c.drawString(50,770,label or 'GUIA FICTÍCIA '+secrets.token_hex(4))
    c.drawString(50,720,'BOBINA');c.drawString(250,720,'METROS');c.drawString(400,720,'MADEIRA')
    for i,n in enumerate(numbers or ['000101','000102']):
        c.drawString(50,690-18*i,n);c.drawString(250,690-18*i,'999999');c.drawString(400,690-18*i,'888888')
    if end:c.drawString(250,650-18*len(numbers or ['a','b']),'TOTAL')
    c.showPage();c.save();return out.getvalue()

def seed(engine,actor):
    positions(engine,actor,3)
    ident=incoming(engine,actor);receive(engine,actor,incoming_review(engine,actor,ident,[row('000101','FICT-ALC-1'),row('000102','FICT-ALC-2'),row('000103','FICT-ALC-3'),row('000104')]))
    unknown=incoming(engine,actor,'pending');receive(engine,actor,incoming_review(engine,actor,unknown,quantity=2))
    return ident,unknown

def source(engine,actor,numbers=None,content=None):
    content=content or pdf(numbers)
    with scoped(engine,5) as db:return s.preview_file(db,actor,secrets.token_hex(16),content,'fict-saida.pdf')

def review(engine,actor,ident,numbers=None,manual=False):
    with scoped(engine,5) as db:
        guide=db.get(m.AlcobreDispatch,ident)
        if numbers is None:numbers=[r['number'] for r in json.loads(guide.extraction)['candidates']]
        rid=s.review(db,actor,ident,guide.version,secrets.token_hex(16),numbers,'Conferência fictícia da guia e posições',manual,'REF FICTÍCIA')
        return rid,db.get(m.AlcobreDispatchRevision,rid).digest

def prepare(engine,actor,ident,key=None):
    with scoped(engine,5) as db:
        g=db.get(m.AlcobreDispatch,ident);r=s.latest(db,g)
        return s.prepare(db,actor,ident,g.version,r.digest,key or secrets.token_hex(16),True)

def confirm(engine,actor,ident,collected=None,exceptions=None,key=None,day='2027-03-03'):
    with scoped(engine,5) as db:
        g=db.get(m.AlcobreDispatch,ident)
        return s.confirm(db,actor,ident,g.version,s.last_operation(db,ident),key or secrets.token_hex(16),collected or [],exceptions or {},day,True)

def cancel(engine,actor,ident,key=None):
    with scoped(engine,5) as db:
        g=db.get(m.AlcobreDispatch,ident)
        return s.cancel(db,actor,ident,g.version,s.last_operation(db,ident),key or secrets.token_hex(16),'Cancelamento comprovado fictício')

def resolution(status='present',local='FICT-ALC-2'):
    return dict(status=status,reason='Conferência física fictícia comunicada ao escritório',local=local if status=='present' else None,physical_confirmed=status=='present')

def snapshot(engine):
    with engine.connect() as conn:return transfer.inventory(transfer.read_data(conn))

def test_alcobre_outgoing_mixed_guide_reserves_only_exact_available_and_confirms_once(restricted_web):
    app,owner,engine,role,users,t=restricted_web;actor=users['admin'][0];seed(engine,actor)
    content=pdf(['000102','999001','000104','000101']);ident=source(engine,actor,content=content)
    with scoped(engine,5) as db:
        original=db.get(m.AlcobreDispatch,ident);assert original.source_pdf==content and original.source_digest==sha256(content).hexdigest()
        assert [r['number'] for r in json.loads(original.extraction)['candidates']]==['000102','999001','000104','000101']
        assert s.preview_file(db,actor,secrets.token_hex(16),content,'renamed.pdf')==ident
    rev=review(engine,actor,ident);key=secrets.token_hex(16);op=prepare(engine,actor,ident,key);assert prepare(engine,actor,ident,key)==op
    with scoped(engine,5) as db:
        assert a.stock(db,state='reserved')[1]==2 and a.stock(db,state='available')[1]==1 and a.stock(db,state='on_hand')[1]==4
        r=json.loads(db.get(m.AlcobreDispatchRevision,rev[0]).payload)
        assert [x['reason'] for x in r['items']]==['available','unknown_pending','unlocated','available']
        assert not any(x['requested'] in ('999999','888888') for x in r['items'])
        g=db.get(m.AlcobreDispatch,ident);version=g.version
    finalkey=secrets.token_hex(16)
    result=confirm(engine,actor,ident,['000102','000101'],key=finalkey)
    with scoped(engine,5) as db:
        assert s.confirm(db,actor,ident,version,op,finalkey,['000102','000101'],{},'2027-03-03',True)==result
        assert db.get(m.AlcobreDispatch,ident).status=='confirmed'
        assert a.stock(db,state='shipped')[1]==2 and a.stock(db,state='reserved')[1]==0 and a.stock(db,state='on_hand')[1]==2
        raw=json.loads(db.get(m.AlcobreDispatchOperation,result).snapshot)
        assert raw['collected']==2 and raw['exceptions']==2
        assert [r['outcome'] for r in raw['plan']['items']]==['shipped','exception','exception','shipped']
        assert db.scalar(select(func.count()).select_from(m.AlcobreBobine))==4
    for call in [lambda:confirm(engine,actor,ident,['000102']),lambda:cancel(engine,actor,ident),lambda:review(engine,actor,ident)]:
        with pytest.raises(s.ReceivingError):call()

def test_alcobre_departure_reuses_position_keeps_original_identity_and_location(warehouse):
    engine,u=warehouse;actor=u['admin'][0];entry,_=seed(engine,actor)
    ident=source(engine,actor,['000101']);review(engine,actor,ident);prepare(engine,actor,ident);confirm(engine,actor,ident,['000101'])
    from iberbag.warehouse_settings import occupied
    with scoped(engine,5) as db:assert not occupied(db,['FICT-ALC-1'])
    replacement=incoming(engine,actor);receive(engine,actor,incoming_review(engine,actor,replacement,[row('NEW-BOBINE','FICT-ALC-1')]))
    with scoped(engine,5) as db:
        assert sum(q for _,q in occupied(db,['FICT-ALC-1']))==1
        rows,count,_=a.stock(db,'000101',state='shipped');assert count==1 and rows[0][1].local=='FICT-ALC-1' and rows[0][0].entry_id==entry
        assert a.stock(db,'000101',state='available')[1]==0
    with pytest.raises(a.ReceivingError):incoming_review(engine,actor,incoming(engine,actor),[row('000101')])
    another=source(engine,actor,['000101']);rid,_=review(engine,actor,another)
    with scoped(engine,5) as db:assert json.loads(db.get(m.AlcobreDispatchRevision,rid).payload)['items'][0]['reason']=='shipped'

def test_alcobre_uncollected_bobine_is_exception_blocked_not_shipped_or_free(warehouse):
    engine,u=warehouse;actor=u['admin'][0];seed(engine,actor);ident=source(engine,actor);review(engine,actor,ident);prepare(engine,actor,ident)
    with pytest.raises(s.ReceivingError):confirm(engine,actor,ident,['000101'])
    confirm(engine,actor,ident,['000101'],{'000102':resolution('not_found')})
    with scoped(engine,5) as db:
        assert a.stock(db,state='shipped')[1]==1 and a.stock(db,state='blocked')[1]==1
        assert a.stock(db,'000102',state='available')[1]==0 and a.stock(db,'000102',state='on_hand')[1]==1
        from iberbag.warehouse_settings import occupied
        assert sum(q for _,q in occupied(db,['FICT-ALC-2']))==1
    other=source(engine,actor,['000102']);r,_=review(engine,actor,other)
    with scoped(engine,5) as db:assert json.loads(db.get(m.AlcobreDispatchRevision,r).payload)['items'][0]['reason']=='blocked'
    with pytest.raises(a.ReceivingError):receive(engine,actor,incoming_review(engine,actor,incoming(engine,actor),[row('REPLACE','FICT-ALC-2')]))

@pytest.mark.parametrize('raw',[False,True])
def test_alcobre_cancel_before_expedition_releases_reservations_preserves_books(warehouse,raw):
    engine,u=warehouse;actor=u['admin'][0];seed(engine,actor);ident=source(engine,actor)
    if not raw:review(engine,actor,ident);prepare(engine,actor,ident)
    key=secrets.token_hex(16)
    with scoped(engine,5) as db:g=db.get(m.AlcobreDispatch,ident);version=g.version;before=s.last_operation(db,ident)
    op=cancel(engine,actor,ident,key)
    with scoped(engine,5) as db:
        assert s.cancel(db,actor,ident,version,before,key,'Cancelamento comprovado fictício')==op
        assert a.stock(db,state='available')[1]==3 and a.stock(db,state='reserved')[1]==0
        assert db.scalar(select(func.count()).select_from(m.AlcobreBobine))==4
        assert db.get(m.AlcobreDispatch,ident).status=='cancelled'
    with pytest.raises(s.ReceivingError):review(engine,actor,ident)

def test_alcobre_two_guides_concurrent_reservation_never_overlap(warehouse):
    engine,u=warehouse;actor=u['admin'][0];seed(engine,actor)
    ids=[source(engine,actor,['000101']) for _ in range(2)]
    for i in ids:review(engine,actor,i)
    barrier=Barrier(2)
    def run(ident):
        barrier.wait(timeout=10)
        try:prepare(engine,actor,ident);return ident
        except s.ReceivingError:return None
    with ThreadPoolExecutor(2) as pool:winners=[x for x in pool.map(run,ids) if x]
    assert len(winners)==1;loser=next(x for x in ids if x not in winners)
    rev,_=review(engine,actor,loser)
    with scoped(engine,5) as db:assert json.loads(db.get(m.AlcobreDispatchRevision,rev).payload)['items'][0]['reason']=='reserved_other'
    prepare(engine,actor,loser)
    with pytest.raises(s.ReceivingError):confirm(engine,actor,loser,['000101'])
    cancel(engine,actor,winners[0])
    with pytest.raises(s.ReceivingError):confirm(engine,actor,loser)
    review(engine,actor,loser);prepare(engine,actor,loser);confirm(engine,actor,loser,['000101'])

def test_alcobre_revision_releases_only_removed_numbers_and_archives_old_pdf(warehouse):
    engine,u=warehouse;actor=u['admin'][0];seed(engine,actor);ident=source(engine,actor);review(engine,actor,ident);prepare(engine,actor,ident)
    with scoped(engine,5) as db:old={d.id:bytes(d.pdf) for d in db.scalars(select(m.AlcobreDispatchDocument))}
    review(engine,actor,ident,['000101','000103'],manual=True)
    with pytest.raises(s.ReceivingError):confirm(engine,actor,ident,['000101'])
    prepare(engine,actor,ident)
    with scoped(engine,5) as db:
        assert a.stock(db,'000102',state='available')[1]==1 and a.stock(db,'000103',state='reserved')[1]==1
        assert all(db.get(m.AlcobreDispatchDocument,did).pdf==raw for did,raw in old.items())
    confirm(engine,actor,ident,['000101','000103'])

def test_alcobre_stock_changes_between_review_prepare_and_confirmation_require_revision(warehouse):
    engine,u=warehouse;actor=u['admin'][0];entry,_=seed(engine,actor)
    ident=source(engine,actor,['000104']);review(engine,actor,ident);prepare(engine,actor,ident)
    with scoped(engine,5) as db:zone=db.scalar(select(m.AlcobreZone.id).where(m.AlcobreZone.code=='A'));a.configure_slot(db,actor,zone,4,'FICT-ALC-4')
    receive(engine,actor,incoming_review(engine,actor,entry,[row('000104','FICT-ALC-4')],kind='place'))
    with pytest.raises(s.ReceivingError):confirm(engine,actor,ident)
    review(engine,actor,ident);prepare(engine,actor,ident);confirm(engine,actor,ident,['000104'])

@pytest.mark.parametrize('failure',['exception','disconnect'])
def test_alcobre_full_confirmation_rollback_and_retry(restricted_web,monkeypatch,failure):
    app,owner,engine,role,u,t=restricted_web;actor=u['admin'][0];seed(engine,actor)
    ident=source(engine,actor);review(engine,actor,ident);prepare(engine,actor,ident);before=snapshot(engine);original=s.document
    def fail(db,*args,**kwargs):
        if args[3]=='confirm':
            if failure=='exception':raise RuntimeError('Injected failure after stock events before PDF')
            pid=db.scalar(text('SELECT pg_backend_pid()'))
            with owner.begin() as conn:assert conn.scalar(text('SELECT pg_terminate_backend(:p)'),{'p':pid})
        return original(db,*args,**kwargs)
    monkeypatch.setattr(s,'document',fail);key=secrets.token_hex(16)
    with pytest.raises((RuntimeError,s.ReceivingError)):confirm(engine,actor,ident,['000101'],{'000102':resolution()},key=key)
    assert snapshot(engine)==before
    monkeypatch.setattr(s,'document',original);confirm(engine,actor,ident,['000101'],{'000102':resolution()},key=key)

def test_alcobre_duplicate_and_ambiguous_pdf_cannot_be_prepared_silently(warehouse):
    engine,u=warehouse;actor=u['admin'][0];seed(engine,actor)
    bad=source(engine,actor,content=pdf(['000101','000101']))
    with pytest.raises(s.ReceivingError):review(engine,actor,bad)
    with pytest.raises(s.ReceivingError):review(engine,actor,bad,['000101','000101'],manual=True)
    unknown=source(engine,actor,content=pdf(['000101'],end=False))
    with pytest.raises(s.ReceivingError):review(engine,actor,unknown,['000101'])
    review(engine,actor,unknown,['000101'],manual=True);prepare(engine,actor,unknown)

def test_alcobre_pdf_archive_navigation_permissions_csrf_and_context(restricted_web):
    app,owner,engine,role,u,t=restricted_web;actor=u['admin'][0];seed(engine,actor);content=pdf(['000101','000104','000999'])
    ident=source(engine,actor,content=content);review(engine,actor,ident);prepare(engine,actor,ident)
    with TestClient(app) as client:
        client.cookies.set('wms_session',t['admin'][0]);client.cookies.set('ddn_context','5')
        for path in ['/alcobre/dispatches',f'/alcobre/dispatches/{ident}']:
            response=client.get(path);assert response.status_code==200,response.text
            assert 'Saídas Alcobre' in response.text
        with scoped(engine,5) as db:
            docs={d.id:bytes(d.pdf) for d in db.scalars(select(m.AlcobreDispatchDocument))};g=db.get(m.AlcobreDispatch,ident);ver=g.version;before=s.last_operation(db,ident)
        original=snapshot(engine)
        assert client.get(f'/alcobre/dispatches/{ident}/source.pdf').content==content
        for did,raw in docs.items():
            assert client.get(f'/alcobre/dispatch-documents/{did}.pdf').content==raw==client.get(f'/alcobre/dispatch-documents/{did}.pdf').content
        assert snapshot(engine)==original
        body=dict(csrf=t['admin'][1],context_id=5,version=ver,before=before,key=secrets.token_hex(16),loaded_on='2027-03-03',accepted='yes',result0='shipped')
        assert client.post(f'/alcobre/dispatches/{ident}/confirm',data=dict(body,csrf='bad')).status_code==403
        response=client.post(f'/alcobre/dispatches/{ident}/confirm',data=body);assert response.status_code==200,response.text
        assert client.post(f'/alcobre/dispatches/{ident}/confirm',data=body).status_code==200
        client.cookies.set('wms_session',t['operador'][0])
        assert client.post('/alcobre/dispatches/upload',data=dict(csrf=t['operador'][1],context_id=5,request_key=secrets.token_hex(16)),files={'pdf':('fict.pdf',content)}).status_code==403
        assert client.post(f'/alcobre/dispatches/{ident}/cancel',data=dict(body,csrf=t['operador'][1],reason='No permission')).status_code==403
        client.cookies.set('wms_session',t['admin'][0]);client.cookies.set('ddn_context','2')
        assert client.get(f'/alcobre/dispatches/{ident}').status_code==403
        assert client.get(f'/alcobre/dispatch-documents/{next(iter(docs))}.pdf').status_code==403

def test_alcobre_runtime_cannot_forge_expedition_or_incomplete_preparation(restricted_web):
    app,owner,engine,role,u,t=restricted_web;actor=u['admin'][0];seed(engine,actor)
    ident=source(engine,actor,['000101','999001']);rid,digest=review(engine,actor,ident)
    with scoped(engine,5) as db:
        guide=db.get(m.AlcobreDispatch,ident);rev=db.get(m.AlcobreDispatchRevision,rid);raw=json.loads(rev.payload);real=raw['items'][0]
        with pytest.raises(DBAPIError):
            s.operation(db,db.get(m.User,actor),guide,rev,secrets.token_hex(16),'prepare',dict(version=guide.version,digest=digest),dict(plan=raw,before=None,events=[]));db.commit()
        db.rollback()
    prepare(engine,actor,ident)
    with scoped(engine,5) as db:
        for name in ('alcobre_dispatch_revisions','alcobre_dispatch_operations','alcobre_dispatch_events','alcobre_dispatch_documents'):
            with pytest.raises(DBAPIError):db.execute(text('DELETE FROM '+name))
            db.rollback()
        opid=s.last_operation(db,ident);state=s.states(db)[real['bobine_id']]
        with pytest.raises(DBAPIError):
            db.add(m.AlcobreDispatchEvent(operation_id=opid,bobine_id=real['bobine_id'],previous_id=state['event'],state='shipped'));db.commit()
        db.rollback();assert a.stock(db,state='shipped')[1]==0 and a.stock(db,state='reserved')[1]==1

def test_alcobre_a4_full_rows_totals_and_sqlite_operational_refusal():
    rows=[dict(requested=str(i).zfill(120),number=str(i).zfill(120),local='POS-FICT',available=i%2==0,reason='available' if i%2==0 else 'unknown') for i in range(300)]
    raw=render_pdf(1,1,dict(reference='FICT',items=rows),'prepare','Fictício',datetime(2027,1,1),{})
    reader=PdfReader(BytesIO(raw));assert len(reader.pages)>1
    text=''.join(p.extract_text() for p in reader.pages).replace('\n','')
    assert 'Total solicitado: 300' in text and 'SEM STOCK na seleção: 150' in text
    for rowdata in rows:assert rowdata['requested'] in text
    assert all(abs(float(p.mediabox.width)-595.28)<1 and abs(float(p.mediabox.height)-841.89)<1 for p in reader.pages)
    confirmed=[dict(rows[0],outcome='shipped',difference=None,resolution=None),
        dict(rows[1],available=True,reason='available',outcome='present',difference='Presente mas não carregada',resolution=resolution(local='POS-FICT')),
        dict(rows[2],outcome='not_found',difference='Não encontrada',resolution=resolution('not_found')),
        dict(rows[3],outcome='exception',difference='Número inexistente',resolution=None)]
    result=render_pdf(1,1,dict(reference='FICT',items=confirmed),'confirm','Fictício',datetime(2027,1,1),dict(loaded_on='2027-01-01',collected=1,exceptions=3))
    resulttext=' '.join(p.extract_text() for p in PdfReader(BytesIO(result)).pages)
    for expected in ['PRESENTE NO ARMAZÉM','NÃO ENCONTRADA','Presença física confirmada','Presentes não carregadas: 1','Não encontradas: 1','SEM STOCK: 1']:
        assert expected in resulttext
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    with Session(create_engine('sqlite://')) as db:
        for fn,args in [(s.create,('a'*32,b'%PDF-fict','f.pdf',{})),(s.review,(1,1,'a'*32,['1'],'Prova')),(s.prepare,(1,1,'digest','a'*32,True)),(s.confirm,(1,1,1,'a'*32,[],{},'2027-01-01',True)),(s.cancel,(1,1,None,'a'*32,'Motivo'))]:
            with pytest.raises(s.ReceivingError,match='PostgreSQL'):fn(db,1,*args)

def test_alcobre_ddn16_upgrade_encrypted_restore_and_guide_history(pg_databases,warehouse,tmp_path):
    from alembic import command
    from iberbag.migrations import config,REVISION
    from iberbag.schema_upgrade import apply_upgrade
    from legacy_catalogue import seed_legacy_catalogue
    from iberbag.security import set_password
    engine=pg_databases(restricted=True);old_target=pg_databases(restricted=True)
    with engine.begin() as conn:cfg=config();cfg.attributes['connection']=conn;command.upgrade(cfg,'ddn_0016')
    with scoped(engine) as db:
        seed_legacy_catalogue(db);user=m.User(username='admin',display_name='Fictício',role='admin',active=True);set_password(user,secrets.token_urlsafe(24));db.add(user);db.commit();actor=user.id
    seed(engine,actor);before=snapshot(engine);folder=tmp_path/'prior17'
    backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD);backup.restore(old_target,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert apply_upgrade(engine,folder)['revision']==REVISION=='ddn_0018' and snapshot(engine)==before
    with engine.connect() as conn:
        native=transfer.read_data(conn)
        for name in (transfer.ALCOBRE_DISPATCH_TABLES | transfer.ALCOBRE_INVENTORY_TABLES):native.pop(name)
        assert transfer.archive_inventory(conn,transfer.inventory(native))==transfer.inventory(native)
    ids=[source(engine,actor,[num]) for num in ['000101','000102','000103']]
    for ident in ids:review(engine,actor,ident);prepare(engine,actor,ident)
    confirm(engine,actor,ids[0],exceptions={'000101':resolution(local='FICT-ALC-1')});confirm(engine,actor,ids[1],['000102']);raw=source(engine,actor,['999001']);cancel(engine,actor,raw);original=snapshot(engine)
    target=pg_databases(restricted=True);folder=tmp_path/'after17';backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert backup.restore(target,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'
    assert snapshot(target)==original
    with scoped(target,5) as db:assert a.stock(db,state='reserved')[1]==1 and a.stock(db,state='shipped')[1]==1 and a.stock(db,state='available')[1]==1

def test_alcobre_complete_sem_stock_guide_confirms_exceptions_without_events(warehouse):
    engine,u=warehouse;actor=u['admin'][0];seed(engine,actor)
    ident=source(engine,actor,['999001','000104']);review(engine,actor,ident);prepare(engine,actor,ident)
    op=confirm(engine,actor,ident)
    with scoped(engine,5) as db:
        snapshot=json.loads(db.get(m.AlcobreDispatchOperation,op).snapshot)
        assert snapshot['events']==[] and snapshot['collected']==0 and snapshot['exceptions']==2
        assert a.stock(db,state='on_hand')[1]==4 and a.stock(db,state='shipped')[1]==0

@pytest.mark.parametrize('present',[False,True])
def test_alcobre_concurrent_whole_guide_confirmations_discount_once(warehouse,present):
    engine,u=warehouse;actor=u['admin'][0];seed(engine,actor);ident=source(engine,actor);review(engine,actor,ident);prepare(engine,actor,ident)
    with scoped(engine,5) as db:version=db.get(m.AlcobreDispatch,ident).version;before=s.last_operation(db,ident)
    key=secrets.token_hex(16);barrier=Barrier(2)
    def run(index):
        barrier.wait(timeout=10)
        with scoped(engine,5) as db:return s.confirm(db,actor,ident,version,before,key,['000101'] if present else ['000101','000102'],{'000102':resolution()} if present else {},'2027-03-03',True)
    with ThreadPoolExecutor(2) as pool:ops=list(pool.map(run,range(2)))
    assert ops[0]==ops[1]
    with scoped(engine,5) as db:
        assert db.scalar(select(func.count()).select_from(m.AlcobreDispatchOperation).where(m.AlcobreDispatchOperation.kind=='confirm'))==1
        assert a.stock(db,state='shipped')[1]==(1 if present else 2) and a.stock(db,state='on_hand')[1]==(3 if present else 2)

def test_alcobre_confirmation_cannot_repoint_active_revision(restricted_web,monkeypatch):
    app,owner,engine,role,u,t=restricted_web;actor=u['admin'][0];seed(engine,actor)
    ident=source(engine,actor);old,_=review(engine,actor,ident);review(engine,actor,ident);prepare(engine,actor,ident)
    before=snapshot(engine);original=s.operation
    def tamper(db,actor,g,*args,**kwargs):
        op=original(db,actor,g,*args,**kwargs);g.active_revision=old;return op
    monkeypatch.setattr(s,'operation',tamper)
    with pytest.raises(s.ReceivingError):confirm(engine,actor,ident,['000101','000102'])
    assert snapshot(engine)==before


def test_alcobre_noncollected_present_releases_only_reservation_preserves_position_and_history(restricted_web):
    app,owner,engine,role,u,t=restricted_web;actor=u['admin'][0];seed(engine,actor)
    ident=source(engine,actor,['000101','000102','000103','999001']);review(engine,actor,ident);prepare(engine,actor,ident)
    choices={'000102':resolution(),'000103':resolution('not_found')}
    with scoped(engine,5) as db:g=db.get(m.AlcobreDispatch,ident);version=g.version;before=s.last_operation(db,ident)
    key=secrets.token_hex(16);op=confirm(engine,actor,ident,['000101'],choices,key=key)
    with scoped(engine,5) as db:
        assert a.stock(db,state='shipped')[1]==1 and a.stock(db,state='available')[1]==1 and a.stock(db,state='blocked')[1]==1 and a.stock(db,state='reserved')[1]==0
        assert a.stock(db,state='on_hand')[1]==3
        from iberbag.warehouse_settings import occupied
        assert not occupied(db,['FICT-ALC-1'])
        assert sum(q for _,q in occupied(db,['FICT-ALC-2']))==sum(q for _,q in occupied(db,['FICT-ALC-3']))==1
        snap=json.loads(db.get(m.AlcobreDispatchOperation,op).snapshot)
        assert [x['outcome'] for x in snap['plan']['items']]==['shipped','present','not_found','exception']
        assert snap['plan']['items'][1]['resolution']==choices['000102'] and snap['collected']==1 and snap['exceptions']==3
        assert [e['state'] for e in snap['events']]==['shipped','available','blocked']
        assert db.scalar(select(func.count()).select_from(m.AlcobreBobine))==4
        audit=db.scalar(select(m.Audit).where(m.Audit.action=='ALCOBRE_SAIDA_CONFIRMAR'))
        assert json.loads(audit.details)['plan']['items'][1]['resolution']==choices['000102']
        doc=db.scalar(select(m.AlcobreDispatchDocument).where(m.AlcobreDispatchDocument.operation_id==op));data=bytes(doc.pdf)
        textpdf=' '.join(p.extract_text() for p in PdfReader(BytesIO(data)).pages)
        assert 'PRESENTE NO ARMAZÉM' in textpdf and 'NÃO ENCONTRADA' in textpdf and 'FICT-ALC-2' in textpdf and '000103' in textpdf
    for local in ['FICT-ALC-2','FICT-ALC-3']:
        with pytest.raises(a.ReceivingError):incoming_review(engine,actor,incoming(engine,actor),[row('INVALID-'+local,local)])
    with scoped(engine,5) as db:assert s.confirm(db,actor,ident,version,before,key,['000101'],choices,'2027-03-03',True)==op
    other=source(engine,actor,['000102']);review(engine,actor,other);prepare(engine,actor,other);confirm(engine,actor,other,['000102'])
    after=snapshot(engine)
    with scoped(engine,5) as db:assert s.confirm(db,actor,ident,version,before,key,['000101'],choices,'2027-03-03',True)==op
    assert snapshot(engine)==after
    with scoped(engine,5) as db:assert db.get(m.AlcobreDispatchDocument,doc.id).pdf==data


@pytest.mark.parametrize('bad',[
    'Motivo isolado não classifica a bobine',
    dict(status='present',reason='Prova',local='FICT-ALC-2',physical_confirmed=False),
    dict(status='present',reason='Prova',local='FICT-ALC-2',physical_confirmed='true'),
    dict(status='present',reason='Prova',local=None,physical_confirmed=True),
    dict(status='present',reason='Prova',local='FICT-ALC-1',physical_confirmed=True),
    dict(status='present',reason='',local='FICT-ALC-2',physical_confirmed=True),
    dict(status='not_found',reason='Prova',local='FICT-ALC-2',physical_confirmed=False),
    dict(status='not_found',reason='Prova',local=None,physical_confirmed=True),
    dict(status='unknown',reason='Prova',local=None,physical_confirmed=False),
])
def test_alcobre_noncollected_requires_explicit_classification_physical_proof_and_current_location(warehouse,bad):
    engine,u=warehouse;actor=u['admin'][0];seed(engine,actor);ident=source(engine,actor);review(engine,actor,ident);prepare(engine,actor,ident)
    before=snapshot(engine)
    with pytest.raises(s.ReceivingError):confirm(engine,actor,ident,['000101'],{'000102':bad})
    assert snapshot(engine)==before
    with pytest.raises(s.ReceivingError):confirm(engine,actor,ident,['000101'],{})
    assert snapshot(engine)==before


@pytest.mark.parametrize('tamper',['proof','location','event','outcome','missing_release'])
def test_alcobre_runtime_rejects_unproved_release_and_forged_classification(restricted_web,monkeypatch,tamper):
    app,owner,engine,role,u,t=restricted_web;actor=u['admin'][0];seed(engine,actor)
    ident=source(engine,actor);review(engine,actor,ident);prepare(engine,actor,ident);before=snapshot(engine);original=s.operation
    def forge(db,actor,g,rev,key,kind,payload,snap):
        if tamper=='proof':payload['exceptions']['000102']['physical_confirmed']=False;snap['plan']['items'][1]['resolution']['physical_confirmed']=False
        elif tamper=='location':payload['exceptions']['000102']['local']='FICT-ALC-1';snap['plan']['items'][1]['resolution']['local']='FICT-ALC-1'
        elif tamper=='event':snap['events'][1]['state']='blocked'
        elif tamper=='missing_release':snap['events'][1]['state']='available'
        else:snap['plan']['items'][1]['outcome']='not_found'
        return original(db,actor,g,rev,key,kind,payload,snap)
    monkeypatch.setattr(s,'operation',forge)
    with pytest.raises(s.ReceivingError):confirm(engine,actor,ident,['000101'],{'000102':resolution('not_found') if tamper=='missing_release' else resolution()})
    assert snapshot(engine)==before


def test_alcobre_confirm_present_concurrent_with_other_guide_never_silently_reassigns(warehouse):
    engine,u=warehouse;actor=u['admin'][0];seed(engine,actor)
    first=source(engine,actor,['000101']);review(engine,actor,first);prepare(engine,actor,first)
    second=source(engine,actor,['000101']);review(engine,actor,second)
    barrier=Barrier(2)
    def run(which):
        barrier.wait(timeout=10)
        if which==0:return confirm(engine,actor,first,exceptions={'000101':resolution(local='FICT-ALC-1')})
        try:return prepare(engine,actor,second)
        except s.ReceivingError:return None
    with ThreadPoolExecutor(2) as pool:list(pool.map(run,range(2)))
    with scoped(engine,5) as db:assert a.stock(db,state='reserved')[1]==0 and a.stock(db,state='shipped')[1]==0 and a.stock(db,state='available')[1]==3
    with pytest.raises(s.ReceivingError):confirm(engine,actor,second,['000101'])
    review(engine,actor,second);prepare(engine,actor,second);confirm(engine,actor,second,['000101'])
    with scoped(engine,5) as db:assert a.stock(db,state='shipped')[1]==1 and a.stock(db,state='available')[1]==2


def test_alcobre_http_whole_guide_requires_classification_and_explicit_presence(restricted_web):
    app,owner,engine,role,u,t=restricted_web;actor=u['admin'][0];seed(engine,actor)
    ident=source(engine,actor,['000101','000102','000103']);review(engine,actor,ident);prepare(engine,actor,ident)
    with scoped(engine,5) as db:version=db.get(m.AlcobreDispatch,ident).version;before=s.last_operation(db,ident)
    body=dict(csrf=t['admin'][1],context_id=5,version=version,before=before,key=secrets.token_hex(16),loaded_on='2027-03-03',accepted='yes',result0='shipped',result1='present',reason1='Permanece fisicamente no armazém',local1='FICT-ALC-2',result2='not_found',reason2='Não encontrada na conferência')
    with TestClient(app) as client:
        client.cookies.set('wms_session',t['admin'][0]);client.cookies.set('ddn_context','5')
        page=client.get(f'/alcobre/dispatches/{ident}');assert 'Presente no armazém, não carregada' in page.text and 'Não encontrada' in page.text
        unchanged=snapshot(engine)
        assert client.post(f'/alcobre/dispatches/{ident}/confirm',data=dict(body,physical1='yes',result2='')).status_code==400
        assert snapshot(engine)==unchanged
        assert client.post(f'/alcobre/dispatches/{ident}/confirm',data=body).status_code==400
        assert snapshot(engine)==unchanged
        response=client.post(f'/alcobre/dispatches/{ident}/confirm',data=dict(body,physical1='yes'));assert response.status_code==200,response.text
        assert 'PRESENTE NO ARMAZÉM' in response.text and 'NÃO ENCONTRADA' in response.text
        after=snapshot(engine)
        with scoped(engine,5) as db:docs={d.id:bytes(d.pdf) for d in db.scalars(select(m.AlcobreDispatchDocument))}
        for did,raw in docs.items():assert client.get(f'/alcobre/dispatch-documents/{did}.pdf').content==raw==client.get(f'/alcobre/dispatch-documents/{did}.pdf').content
        assert snapshot(engine)==after
