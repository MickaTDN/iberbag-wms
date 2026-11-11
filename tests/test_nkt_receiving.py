"""Synthetic listings only; no company workbook is read by these tests."""
from io import BytesIO
from hashlib import sha256
from concurrent.futures import ThreadPoolExecutor
import json,secrets
import pytest
from sqlalchemy import select,func,text,event
from sqlalchemy.exc import DBAPIError
from iberbag import database as m,nkt_receiving as s,nkt_excel as x,nkt,backup,transfer
from iberbag.privileges import validate_runtime
from test_receiving import warehouse
from test_ddn_contexts import scoped
from test_security_regressions import restricted_web
from test_transfer import pg_databases,docker_client
from conftest import BACKUP_PASSWORD

def workbook(rows=None):
    from openpyxl import Workbook
    b=Workbook();w=b.active;w.title='Listagem fictícia'
    w.append(['Tipo de material','Contentor','PEG Inicial','PEG Final','Observações'])
    for row in rows or [['Material A','FICT-C1','000001','000020',''],['Material A','FICT-C2','000021','000040',''],['Material B','FICT-C3','000041','000060',''],['Material B','NÃO RECEBER','000061','000062','CQ — Não Vai']]:w.append(row)
    out=BytesIO();b.save(out);return out.getvalue()

def manual_rows(first='000001',last='000003',name='C-FICT',material='Material livre fictício',day='2027-01-10'):
    return [dict(container=name,material=material,received_on=day,first=first,last=last)]

def stage(engine,actor,rows=None,content=None):
    with scoped(engine,4) as db:
        ident=s.preview_file(db,actor,secrets.token_hex(16),content,'FICTICIO.xlsx') if content else s.create(db,actor,secrets.token_hex(16),'Manual/e-mail',dict(records=[],excluded=[],warnings=[]))
        if content and rows is None:
            source=json.loads(db.get(m.NktReceiving,ident).report)
            rows=[dict(ref=r['ref'],container=r['container'],material=r['material'],received_on='2027-01-10',pegs=r['pegs']) for r in source['records']]
        revision=s.review(db,actor,ident,0,rows or manual_rows(),'Prova fictícia: distribuição e data conferidas no e-mail de teste.')
        return ident,revision,db.get(m.NktReceivingReview,revision).digest

def confirm(engine,actor,rec):
    with scoped(engine,4) as db:return s.confirm(db,actor,*rec,True)

def test_nkt_excel_explicit_containers_preserve_all_pegs_and_cq():
    content=workbook();report=x.analyze(content)
    assert report['sha256']==sha256(content).hexdigest() and report['total_candidates']==60
    assert [len(r['pegs']) for r in report['records']]==[20,20,20]
    assert report['records'][0]['pegs']==[str(i).zfill(6) for i in range(1,21)]
    assert report['records'][1]['material']==report['records'][0]['material']
    assert report['excluded'][0]['pegs']==['000061','000062']
    assert report['records'][2]['ref']=='Listagem fictícia!4'
    assert x.analyze(workbook([['A','C1','001','002',''],['A','C2','003','009','']]))['total_candidates']==9

@pytest.mark.parametrize('first,last',[('001','02'),('A001','B002'),('004','003'),('A','B'),('001','999'),(1,'020')])
def test_nkt_invalid_ranges_never_invent_identifiers(first,last):
    with pytest.raises(ValueError):x.expand(first,last)

def test_nkt_excel_ambiguous_missing_material_numeric_peg_and_formulas():
    report=x.analyze(workbook([['A',None,'001','003',''],[None,None,'004','006','']]))
    assert all(r['container'] is None for r in report['records'])
    assert report['records'][1]['material'] is None
    rows=[dict(ref=r['ref'],container='C1',material=r['material'],received_on='2027-01-01',pegs=r['pegs']) for r in report['records']]
    with pytest.raises(s.ReceivingError):s.plan_rows(report,rows)
    with pytest.raises(ValueError,match='Fórmulas'):x.analyze(workbook([['A','C1','=1','003','']]))
    numeric=x.analyze_rows({'F':[(1,['PEG','Contentor','Material']),(2,[12,'C','A'])]})
    assert not numeric['records'][0]['pegs'] and 'numérico' in numeric['records'][0]['issues'][0]
    missing=x.analyze(workbook([['A','C1','001','002',''],['A','C2',None,None,'']]))
    assert len(missing['records'])==2 and not missing['records'][1]['pegs'] and missing['records'][1]['issues']
    assert x.expand('Aa0001','Aa0003')==['Aa0001','Aa0002','Aa0003']


def test_nkt_excel_split_requires_explicit_full_distribution_and_cq_exclusion():
    report=x.analyze(workbook([['Material A',None,'000001','000060','']]))
    ref=report['records'][0]['ref']
    rows=[dict(r,ref=ref) for r in [*manual_rows('000001','000020','C1'),*manual_rows('000021','000040','C2'),*manual_rows('000041','000060','C3')]]
    assert len(s.plan_rows(report,rows))==3
    with pytest.raises(s.ReceivingError,match='preservar todos'):s.plan_rows(report,rows[:-1])
    with pytest.raises(s.ReceivingError):s.plan_rows(report,rows+[rows[0]])
    cq=x.analyze(workbook())
    rows=[dict(ref=r['ref'],container=r['container'],material=r['material'],received_on='2027-01-01',pegs=r['pegs']) for r in cq['records']]
    rows[0]['pegs']=['000061']
    with pytest.raises(s.ReceivingError,match='CQ'):s.plan_rows(cq,rows)


def test_nkt_containers_atomic_entries_material_dates_and_numbering(restricted_web):
    app,owner,engine,role,a,tokens=restricted_web;actor=a['admin'][0]
    with engine.connect() as conn:validate_runtime(conn)
    content=workbook();rec=stage(engine,actor,content=content)
    with scoped(engine,4) as db:
        assert nkt.stock(db)[1]==0 and not db.scalar(select(m.Location).where(m.Location.codigo=='Chão'))
        assert s.preview_file(db,actor,secrets.token_hex(16),content,'RENAMED.xlsx')==rec[0]
    confirm(engine,actor,rec);confirm(engine,actor,rec)
    with scoped(engine,4) as db:
        assert nkt.stock(db)[1]==60
        containers=db.scalars(select(m.NktContainer).order_by(m.NktContainer.id)).all()
        assert len(containers)==3 and len({c.entry_id for c in containers})==3 and len({c.carga_id for c in containers})==3
        assert [db.get(m.Carga,c.carga_id).codigo for c in containers]==['27-001','27-002','27-003']
        assert all(p.local=='Chão' for p in db.scalars(select(m.NktPallet)))
        info=s.metadata(db,[c.entry_id for c in containers]);assert all(v['received_on']=='2027-01-10' for v in info.values())
        assert {i['material'] for v in info.values() for i in v['items']}=={'Material A','Material B'}
        for table in ('nkt_containers','nkt_receiving_reviews'):
            with pytest.raises(DBAPIError):db.execute(text('DELETE FROM '+table))
            db.rollback()
        with pytest.raises(DBAPIError):db.execute(text('UPDATE cargas SET entrada=NULL WHERE id=:id'),dict(id=containers[0].carga_id))
        db.rollback()
    other=stage(engine,actor,manual_rows('000063','000064',day='2028-01-01'));confirm(engine,actor,other)
    with scoped(engine,4) as db:assert db.scalar(select(m.Carga.codigo).where(m.Carga.entrada==__import__('datetime').datetime(2028,1,1)))=='28-001'
    with scoped(engine,1) as db:assert not db.scalar(select(m.NktPallet.id)) and not db.scalar(select(m.NktContainer.id))


def test_nkt_receiving_review_changes_missing_date_and_duplicate_cases(warehouse):
    engine,a=warehouse;actor=a['admin'][0]
    with scoped(engine,4) as db:
        ident=s.create(db,actor,secrets.token_hex(16),'E-mail fictício',dict(records=[],excluded=[],warnings=[]))
        for rows in [manual_rows(material=''),manual_rows(day=''),manual_rows(name=''),[dict(container='C',material='A',received_on='2027-01-01',pegs=['001','001'])]]:
            with pytest.raises(s.ReceivingError):s.review(db,actor,ident,0,rows,'Prova fictícia')
        r=s.review(db,actor,ident,0,manual_rows(),'Conferido fictício')
        assert s.review(db,actor,ident,0,manual_rows(),'Conferido fictício')==r
        with pytest.raises(s.ReceivingError):s.confirm(db,actor,ident,r,'bad',True)
        with pytest.raises(s.ReceivingError):s.confirm(db,actor,ident,r,db.get(m.NktReceivingReview,r).digest,False)
        newer=s.review(db,actor,ident,1,manual_rows(material='Material corrigido'),'Correção comprovada fictícia')
        with pytest.raises(s.ReceivingError):s.confirm(db,actor,ident,r,db.get(m.NktReceivingReview,r).digest,True)
        digest=db.get(m.NktReceivingReview,newer).digest
    confirm(engine,actor,(ident,newer,digest))
    with scoped(engine,4) as db:
        assert db.scalar(select(func.count()).select_from(m.NktReceivingReview))==2
        with pytest.raises(s.ReceivingError):s.review(db,actor,ident,2,manual_rows(),'Não permitido')
    with pytest.raises(s.ReceivingError):stage(engine,actor,[dict(container='C2',material='A',received_on='2027-01-01',pegs=['000001'])])


def test_nkt_receiving_concurrent_batches_and_midway_rollback(warehouse):
    engine,a=warehouse;actor=a['admin'][0]
    recs=[stage(engine,actor,manual_rows(name=f'C-{i}')) for i in range(2)]
    def execute(rec):
        try:confirm(engine,actor,rec);return True
        except s.ReceivingError:return False
    with ThreadPoolExecutor(2) as pool:assert sum(pool.map(execute,recs))==1
    rec=stage(engine,actor,[*manual_rows('000010','000011','C3'),*manual_rows('000012','000013','C4')])
    def failure(mapper,connection,target):
        if target.name=='C4':raise RuntimeError('Falha fictícia depois do primeiro contentor')
    event.listen(m.NktContainer,'after_insert',failure)
    try:
        with pytest.raises(RuntimeError):confirm(engine,actor,rec)
    finally:event.remove(m.NktContainer,'after_insert',failure)
    with scoped(engine,4) as db:
        assert nkt.stock(db)[1]==3 and db.get(m.NktReceiving,rec[0]).status=='draft'
        assert db.scalar(select(func.count()).select_from(m.Carga))==1
    confirm(engine,actor,rec)
    with scoped(engine,4) as db:assert nkt.stock(db)[1]==7 and [c.number for c in db.scalars(select(m.NktContainer).order_by(m.NktContainer.number))]==[1,2,3]


def test_nkt_receiving_runtime_permissions_http_csrf_and_wrong_context(restricted_web):
    from fastapi.testclient import TestClient
    app,owner,engine,role,a,tokens=restricted_web
    with TestClient(app) as client:
        assert client.get('/nkt/receivings').status_code==403
        client.cookies.set('wms_session',tokens['operador'][0]);client.cookies.set('ddn_context','4')
        body=dict(csrf=tokens['operador'][1],context_id=4,request_key=secrets.token_hex(16))
        assert client.post('/nkt/receivings/manual',data=dict(body,csrf='bad')).status_code==403
        assert client.post('/nkt/receivings/manual',data=dict(body,context_id=2)).status_code==409
        upload=dict(csrf=body['csrf'],context_id='4',request_key=body['request_key'])
        assert client.post('/nkt/receivings/upload',data=upload,files={'excel':('FICT.xlsx',workbook())}).status_code==403
        response=client.post('/nkt/receivings/manual',data=body);assert response.status_code==200,response.text
        ident=int(str(response.url).split('/')[-1])
        row=dict(csrf=body['csrf'],context_id=4,version=0,row_count=1,container0='C',material0='Material X',received_on0='2027-01-01',first0='PEG0001',last0='PEG0002',reason='E-mail fictício conferido')
        response=client.post(f'/nkt/receivings/{ident}/review',data=row);assert response.status_code==200,response.text
        assert 'PEG0001' in response.text and "script-src 'none'" in response.headers['content-security-policy'] and '<script>' not in response.text
        with scoped(engine,4) as db:r=db.scalar(select(m.NktReceivingReview).where(m.NktReceivingReview.receiving_id==ident));revision=r.id;digest=r.digest
        confirm_data=dict(csrf=body['csrf'],context_id=4,review=revision,digest=digest,accepted='yes')
        assert client.post(f'/nkt/receivings/{ident}/confirm',data=dict(confirm_data,csrf='bad')).status_code==403
        assert client.post(f'/nkt/receivings/{ident}/confirm',data=confirm_data).status_code==200
        assert 'Material X' in client.get('/nkt/stock?peg=PEG0001').text
        client.cookies.set('ddn_context','2');assert client.get(f'/nkt/receivings/{ident}').status_code==403
        client.cookies.set('ddn_context','4');client.cookies.set('wms_session',tokens['admin'][0])
        admin_data=dict(csrf=tokens['admin'][1],context_id='4',request_key=secrets.token_hex(16))
        response=client.post('/nkt/receivings/upload',data=admin_data,files={'excel':('FICTICIO.xlsx',workbook())})
        assert response.status_code==200,response.text
        assert 'FICTICIO.xlsx' in response.text and 'SHA-256' in response.text
        assert client.post('/nkt/entries/review',data=dict(csrf=tokens['admin'][1],context_id=4,key=secrets.token_hex(16),peg0='NO-BYPASS',local0='OTHER')).status_code==409
    with scoped(engine,4) as db,pytest.raises(s.ReceivingError):s.review(db,a['admin'][0],ident,1,manual_rows(),'Outra pessoa')


def test_nkt_receiving_encrypted_restore_and_prior_head_upgrade(pg_databases,warehouse,tmp_path):
    from alembic import command
    from iberbag.migrations import config,REVISION
    from iberbag.schema_upgrade import apply_upgrade
    from legacy_catalogue import seed_legacy_catalogue
    from iberbag.security import set_password
    engine=pg_databases(restricted=True);restored=pg_databases(restricted=True)
    with engine.begin() as conn:
        cfg=config();cfg.attributes['connection']=conn;command.upgrade(cfg,'ddn_0012')
    with scoped(engine) as db:
        seed_legacy_catalogue(db);u=m.User(username='admin',display_name='Fictício',role='admin',active=True);set_password(u,secrets.token_urlsafe(24));db.add(u);db.commit();actor=u.id
    # A real legacy NKT pallet outside Chão remains untouched by the new flow.
    from test_nkt import setup,save,confirm as old_confirm
    setup(engine);legacy=save(engine,actor,['000LEGACY']);old_confirm(engine,actor,legacy)
    with engine.connect() as conn:before=transfer.inventory(transfer.read_data(conn))
    folder=tmp_path/'prior';backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD);backup.restore(restored,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert apply_upgrade(engine,folder)['revision']==REVISION=='ddn_0019'
    with engine.connect() as conn:assert transfer.inventory(transfer.read_data(conn))==before
    rec=stage(engine,actor,content=workbook());confirm(engine,actor,rec)
    restored2=pg_databases(restricted=True);folder=tmp_path/'new'
    backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD);backup.restore(restored2,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    with engine.connect() as a,restored2.connect() as b:assert transfer.inventory(transfer.read_data(a))==transfer.inventory(transfer.read_data(b))
    confirm(restored2,actor,rec)
    with scoped(restored2,4) as db:
        assert nkt.stock(db)[1]==61 and db.scalar(select(m.NktPallet.local).where(m.NktPallet.peg=='000LEGACY'))=='ESC-01'
        assert json.loads(db.get(m.NktReceiving,rec[0]).report)['records'][0]['ref']=='Listagem fictícia!2'

def test_nkt_excel_zip_xml_layout_limits_and_sqlite_never_receive():
    from zipfile import ZipFile
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    for content in (b'not Excel',b''):
        with pytest.raises(ValueError):x.analyze(content)
    out=BytesIO()
    with ZipFile(out,'w') as z:z.writestr('../outside.xml','x')
    with pytest.raises(ValueError):x.analyze(out.getvalue())
    with pytest.raises(ValueError):x.analyze_rows({'F':[(1,['Sem cabeçalho PEG'])]})
    with Session(create_engine('sqlite://')) as db,pytest.raises(s.ReceivingError,match='PostgreSQL'):s.confirm(db,1,1,1,'hash',True)


def test_nkt_repeated_concurrent_files_altered_listing_and_source_immutability(warehouse):
    engine,a=warehouse;actor=a['admin'][0];content=workbook()
    def preview(index):
        with scoped(engine,4) as db:return s.preview_file(db,actor,secrets.token_hex(16),content,f'FICT-{index}.xlsx')
    with ThreadPoolExecutor(2) as pool:ids=list(pool.map(preview,range(2)))
    assert ids[0]==ids[1]
    rec=stage(engine,actor,content=content);confirm(engine,actor,rec)
    changed=workbook([['Material A','C-OUTRO','000001','000020','Nova nota fictícia']])
    with pytest.raises(s.ReceivingError):stage(engine,actor,content=changed)
    with scoped(engine,4) as db:
        with pytest.raises(DBAPIError):db.execute(text("UPDATE nkt_receivings SET source_name='changed' WHERE id=:id"),dict(id=rec[0]))
        db.rollback();assert nkt.stock(db)[1]==60
        assert db.scalar(select(func.count()).select_from(m.NktContainer))==3
    # Proven case-only duplicate with an alphabetic PEG is still owner-wide.
    other=stage(engine,actor,[dict(container='FICT-C4',material='M',received_on='2027-01-01',pegs=['000AbC'])]);confirm(engine,actor,other)
    with pytest.raises(s.ReceivingError):stage(engine,actor,[dict(container='FICT-C5',material='M',received_on='2027-01-01',pegs=['000aBc'])])


def test_nkt_upload_parsing_never_holds_writer_lock(restricted_web,monkeypatch):
    from fastapi.testclient import TestClient
    app,owner,engine,role,a,tokens=restricted_web;original=s.analyze;calls=[]
    def inspect(content):
        with engine.begin() as conn:assert conn.scalar(text('SELECT pg_try_advisory_xact_lock(72834001)'))
        calls.append(True);return original(content)
    monkeypatch.setattr(s,'analyze',inspect)
    with TestClient(app) as client:
        client.cookies.set('wms_session',tokens['admin'][0]);client.cookies.set('ddn_context','4')
        body=dict(csrf=tokens['admin'][1],context_id='4',request_key=secrets.token_hex(16))
        assert client.post('/nkt/receivings/upload',data=dict(body,csrf='wrong'),files={'excel':('FICT.xlsx',workbook())}).status_code==403 and not calls
        response=client.post('/nkt/receivings/upload',data=body,files={'excel':('FICT.xlsx',workbook())})
        assert response.status_code==200,response.text
        assert calls==[True]


def test_nkt_manual_mixed_materials_pre2027_and_floor_catalogue(warehouse):
    engine,a=warehouse;actor=a['operador'][0]
    rows=[dict(container='EMAIL-C',material='A',received_on='2026-11-01',pegs=['000AA']),dict(container='EMAIL-C',material='B',received_on='2026-11-01',pegs=['000BB'])]
    rec=stage(engine,actor,rows);confirm(engine,actor,rec)
    with scoped(engine,4) as db:
        containers=db.scalars(select(m.NktContainer)).all();assert len(containers)==1 and containers[0].year is None
        assert {i['material'] for i in json.loads(containers[0].payload)['items']}=={'A','B'}
        assert all(p.local=='Chão' for p in db.scalars(select(m.NktPallet)))
    with scoped(engine,1) as db,pytest.raises(s.ReceivingError):s.create(db,actor,secrets.token_hex(16),'Manual',dict(records=[]))
