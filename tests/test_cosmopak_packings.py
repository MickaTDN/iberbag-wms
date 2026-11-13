"""Synthetic PACKINGs; no enterprise Excel or production data."""
from io import BytesIO
import json,secrets
import pytest
from sqlalchemy import select,func,text
from iberbag import database as m,cosmopak_packings as s,cosmopak_packing_excel as x,physical_locations as p
from test_receiving import warehouse
from test_transfer import pg_databases,docker_client
from test_security_regressions import restricted_web
from test_ddn_contexts import scoped
from test_cosmopak import plan,units,place,review,confirm
from test_shared_locations import activate

MAPPING=dict(owner='A',article='B',lot='C',boxes='D',pallets='E',first_row=2)

def workbook(rows=None):
    from openpyxl import Workbook
    b=Workbook();w=b.active;w.title='Folha2';b.create_sheet('Folha1')
    w.append(['Responsável','Artigo','Lote','Caixas por palete','Paletes'])
    for row in rows or [('DAVID NETO','A','L',50,1),('Mecalux','A','L',20,1),('DDN','A','L',23,1)]:w.append(row)
    w['G1']='000-PACK-FICT'
    out=BytesIO();b.save(out);return out.getvalue()


def command(engine,actor,ident,kind,payload=None,pick=None,pallet=None,reason='Conferência física e decisão fictícias',version=None,key=None):
    with scoped(engine,3) as db:
        if version is None:
            if pick:version=db.get(m.CosmoPackingPick,pick).version
            elif kind=='return_rack':version=db.scalar(select(m.CosmoState.version).where(m.CosmoState.pallet_id==pallet))
            else:version=s.header(db,ident).version
        return s.command(db,actor,key or secrets.token_hex(16),ident,kind,version,payload,reason,pick,pallet)


def packing(engine,actor,rows=None,reference='000-PACK-FICT'):
    with scoped(engine,3) as db:ident=s.preview(db,actor,secrets.token_hex(16),workbook(rows),'PACKING-FICT.xlsx')
    command(engine,actor,ident,'map',dict(mapping=MAPPING,reference=reference,reference_cell='G1' if reference=='000-PACK-FICT' else None))
    return ident


def stock(engine,actor,boxes=(50,23)):
    for i in range(len(boxes)):activate(engine,actor,f'A1{i+1:02d}')
    ident=plan(engine,actor,[('A','Fictício','L',1,b,'X' if b!=50 else '') for b in boxes])
    for row,i in zip(units(engine,ident),range(len(boxes))):place(engine,actor,row[0],f'A1{i+1:02d}')
    confirm(engine,actor,ident,review(engine,actor,actor,ident))


def picks(engine,ident):
    with scoped(engine,3) as db:return [(v.id,v.pallet_id,v.prepared,v.loaded,v.active,v.version) for v in db.scalars(select(m.CosmoPackingPick).where(m.CosmoPackingPick.packing_id==ident).order_by(m.CosmoPackingPick.id))]


def conference(boxes=50,**extra):return dict(dict(lot='L',boxes=boxes,checked=True,physical=True),**extra)


def test_packing_explicit_mapping_preserves_order_filters_and_blocks_ambiguity():
    report=x.analyze(workbook());out=x.map_rows(report,MAPPING,'000-PACK-FICT','G1','Fonte fictícia conferida')
    assert [r['source_row'] for r in out['lines']]==[2,4] and [r['boxes'] for r in out['lines']]==[50,23]
    assert [r['row'] for r in out['excluded']]==[3]
    with pytest.raises(ValueError):x.map_rows(report,{},'000-PACK-FICT','G1','Conferência')
    with pytest.raises(ValueError):x.map_rows(x.analyze(workbook([('???','A','L',5,1)])),MAPPING,'P',None,'Não adivinhar responsável')


def test_packing_receipt_reservation_stage_load_and_office_dispatch(warehouse):
    engine,u=warehouse;admin=u['admin'][0];operator=u['operador'][0];stock(engine,admin)
    ident=packing(engine,admin);command(engine,admin,ident,'confirm',{'accepted':True});first,second=picks(engine,ident)
    assert first[1] and second[1] and not first[2]
    for pick,boxes in ((first,50),(second,23)):command(engine,operator,ident,'prepare',conference(boxes),pick=pick[0])
    with scoped(engine,3) as db:
        assert all(p.common_state(db,l)['state']=='free' for l in ('A101','A102'))
        assert db.scalar(select(func.sum(m.Stock.quantidade)))==2
        assert db.scalar(select(func.count()).select_from(m.Stock).where(m.Stock.local=='Zona de preparação',m.Stock.quantidade==2))==1
    command(engine,operator,ident,'finish_prepare');command(engine,operator,ident,'start_load')
    for pick,boxes in ((first,50),(second,23)):command(engine,operator,ident,'load',conference(boxes),pick=pick[0])
    with scoped(engine,3) as db:assert db.scalar(select(func.sum(m.Stock.quantidade)))==2
    command(engine,operator,ident,'finish_load');rev=command(engine,admin,ident,'review_dispatch',{'dispatched_on':'2027-04-05'})
    command(engine,admin,ident,'approve',{'review_id':rev,'accepted':True})
    with scoped(engine,3) as db:
        assert db.scalar(select(func.sum(m.Stock.quantidade)))==0
        assert s.header(db,ident).stage=='shipped' and all(not row[4] for row in picks(engine,ident))


def close_dispatch(engine,admin,operator,ident):
    command(engine,operator,ident,'finish_prepare');command(engine,operator,ident,'start_load')
    for pick in picks(engine,ident):
        if pick[2]:
            with scoped(engine,3) as db:boxes=db.get(m.CosmoPackingLine,db.get(m.CosmoPackingPick,pick[0]).line_id).boxes
            command(engine,operator,ident,'load',conference(boxes),pick=pick[0])
    command(engine,operator,ident,'finish_load');rev=command(engine,admin,ident,'review_dispatch',{'dispatched_on':'2027-04-05'})
    command(engine,admin,ident,'approve',{'review_id':rev,'accepted':True})


def test_packing_partial_releases_reservation_not_physical_presence_and_regroups(warehouse):
    engine,u=warehouse;a=u['admin'][0];op=u['operador'][0];stock(engine,a)
    ident=packing(engine,a);command(engine,a,ident,'confirm',{'accepted':True});one,two=picks(engine,ident)
    for pick,boxes in ((one,50),(two,23)):command(engine,op,ident,'prepare',conference(boxes),pick=pick[0])
    command(engine,op,ident,'unprepare',pick=two[0]);close_dispatch(engine,a,op,ident)
    with scoped(engine,3) as db:
        state=db.scalar(select(m.CosmoState).where(m.CosmoState.pallet_id==two[1]));assert state.status=='approved' and state.local=='Zona de preparação'
        zone=db.scalar(select(m.PhysicalStagingPosition).where(m.PhysicalStagingPosition.unit_id==two[1]));assert zone.group_id==ident and zone.phase=='staged'
        assert db.scalar(select(func.sum(m.Stock.quantidade)))==1
    nextid=packing(engine,a,[('DDN','A','L',23,1)],'SEGUNDO-FICT');command(engine,a,nextid,'confirm',{'accepted':True});pick=picks(engine,nextid)[0]
    assert pick[1]==two[1]
    with pytest.raises(s.ReceivingError):command(engine,op,nextid,'prepare',conference(23,physical=False),pick=pick[0])
    command(engine,op,nextid,'prepare',conference(23),pick=pick[0])
    with scoped(engine,3) as db:
        assert db.scalar(select(m.PhysicalStagingPosition.group_id).where(m.PhysicalStagingPosition.unit_id==two[1]))==nextid
        assert db.scalar(select(func.sum(m.Stock.quantidade)))==1
    close_dispatch(engine,a,op,nextid)
    with scoped(engine,3) as db:assert db.scalar(select(func.sum(m.Stock.quantidade)))==0


def test_packing_toggles_reopen_and_physical_returns_never_invent_movements(warehouse):
    engine,u=warehouse;a=u['admin'][0];op=u['operador'][0];stock(engine,a,(50,))
    ident=packing(engine,a,[('DDN','A','L',50,1)]);command(engine,a,ident,'confirm',{'accepted':True});pick=picks(engine,ident)[0]
    command(engine,op,ident,'prepare',conference(),pick=pick[0]);command(engine,op,ident,'unprepare',pick=pick[0])
    with scoped(engine,3) as db:assert db.scalar(select(func.count()).select_from(m.CosmoPackingMove))==1
    command(engine,op,ident,'prepare',conference(physical=False),pick=pick[0]);command(engine,op,ident,'finish_prepare')
    with pytest.raises(s.ReceivingError):command(engine,op,ident,'reopen_prepare')
    command(engine,a,ident,'reopen_prepare');command(engine,op,ident,'finish_prepare');command(engine,op,ident,'start_load')
    with pytest.raises(s.ReceivingError):command(engine,a,ident,'reopen_prepare')
    command(engine,op,ident,'load',conference(),pick=pick[0]);command(engine,op,ident,'unload',pick=pick[0])
    with scoped(engine,3) as db:
        assert db.scalar(select(m.PhysicalStagingPosition.phase))=='loading'
        assert db.scalar(select(func.sum(m.Stock.quantidade)))==1
    with pytest.raises(s.ReceivingError):command(engine,op,ident,'finish_load')
    command(engine,op,ident,'return_stage',conference(),pick=pick[0]);command(engine,op,ident,'finish_load');command(engine,a,ident,'reopen_load')
    command(engine,op,ident,'load',conference(),pick=pick[0]);command(engine,op,ident,'finish_load')
    rev=command(engine,a,ident,'review_dispatch',{'dispatched_on':'2027-04-06'})
    key=secrets.token_hex(16)
    with scoped(engine,3) as db:v=s.header(db,ident).version
    original=command(engine,a,ident,'approve',{'review_id':rev,'accepted':True},version=v,key=key)
    assert command(engine,a,ident,'approve',{'review_id':rev,'accepted':True},version=v,key=key)==original
    with pytest.raises(s.ReceivingError):command(engine,a,ident,'reopen_load')
    with scoped(engine,3) as db:assert db.scalar(select(func.sum(m.Stock.quantidade)))==0


def test_packing_missing_exact_boxes_no_pooling_and_returns_require_free_rack(warehouse):
    engine,u=warehouse;a=u['admin'][0];op=u['operador'][0];stock(engine,a)
    ident=packing(engine,a,[('DDN','A','L',73,1),('DDN','A','L',50,1)]);command(engine,a,ident,'confirm',{'accepted':True});missing,pick=picks(engine,ident)
    assert missing[1] is None and pick[1]
    with pytest.raises(s.ReceivingError):command(engine,op,ident,'finish_prepare')
    with pytest.raises(s.ReceivingError):command(engine,op,ident,'prepare',conference(),pick=missing[0])
    command(engine,op,ident,'exception',pick=missing[0]);command(engine,op,ident,'prepare',conference(),pick=pick[0]);command(engine,op,ident,'unprepare',pick=pick[0])
    close_dispatch(engine,a,op,ident)
    with pytest.raises(s.ReceivingError):command(engine,op,ident,'return_rack',conference(local='A102'),pallet=pick[1])
    with scoped(engine,3) as db:assert db.scalar(select(m.CosmoState.local).where(m.CosmoState.pallet_id==pick[1]))=='Zona de preparação'
    command(engine,op,ident,'return_rack',conference(local='A101'),pallet=pick[1])
    with scoped(engine,3) as db:
        assert p.common_state(db,'A101')['state']=='occupied'
        assert db.scalar(select(func.sum(m.Stock.quantidade)))==2
        assert db.scalar(select(m.PhysicalStagingPosition.phase).where(m.PhysicalStagingPosition.unit_id==pick[1]))=='rack'


def test_packing_concurrent_exclusive_reservations_and_stale_flags(warehouse):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    engine,u=warehouse;a=u['admin'][0];op=u['operador'][0];other=u['other'][0];stock(engine,a,(50,))
    ids=[packing(engine,a,[('DDN','A','L',50,1)],f'CONCORRENTE-{i}') for i in range(2)]
    barrier=Barrier(2)
    def reserve(ident):barrier.wait(timeout=10);command(engine,a,ident,'confirm',{'accepted':True})
    with ThreadPoolExecutor(2) as pool:list(pool.map(reserve,ids))
    ps=[picks(engine,i)[0] for i in ids];assert sum(row[1] is not None for row in ps)==1
    ident=ids[next(i for i,row in enumerate(ps) if row[1])];pick=picks(engine,ident)[0]
    def prepare(actor):
        barrier.wait(timeout=10)
        try:command(engine,actor,ident,'prepare',conference(),pick=pick[0],version=0);return True
        except s.ReceivingError:return False
    with ThreadPoolExecutor(2) as pool:assert sum(pool.map(prepare,[op,other]))==1
    with scoped(engine,3) as db:assert db.scalar(select(func.count()).select_from(m.CosmoPackingMove))==1


def test_packing_equivalent_substitution_is_explicit_scoped_and_unreserved(warehouse):
    engine,u=warehouse;a=u['admin'][0];op=u['operador'][0];stock(engine,a,(50,50,23))
    ident=packing(engine,a,[('DDN','A','L',50,1)]);command(engine,a,ident,'confirm',{'accepted':True});pick=picks(engine,ident)[0]
    with scoped(engine,3) as db:pal=db.scalars(select(m.CosmoPallet).order_by(m.CosmoPallet.id)).all();different=pal[2].id;equivalent=pal[1].id
    with pytest.raises(s.ReceivingError):command(engine,a,ident,'substitute',pick=pick[0],pallet=different)
    with pytest.raises(s.ReceivingError):command(engine,op,ident,'substitute',pick=pick[0],pallet=equivalent)
    command(engine,a,ident,'substitute',pick=pick[0],pallet=equivalent)
    with scoped(engine,3) as db:
        assert db.get(m.CosmoPackingPick,pick[0]).pallet_id==equivalent
        assert p.common_state(db,'A101')['state']==p.common_state(db,'A102')['state']=='occupied'
        assert db.scalar(select(func.count()).select_from(m.CosmoPackingMove))==0


def test_packing_runtime_http_permissions_csrf_isolation_and_archive(restricted_web):
    from fastapi.testclient import TestClient
    from sqlalchemy.exc import DBAPIError
    app,owner,runtime,role,u,tokens=restricted_web;a=u['admin'][0];stock(owner,a,(50,))
    with TestClient(app) as client:
        client.cookies.set('ddn_context','3');client.cookies.set('wms_session',tokens['operador'][0]);body=dict(context_id='3',csrf=tokens['operador'][1],request_key=secrets.token_hex(16))
        assert client.post('/cosmopak/packings/upload',data=body,files={'excel':('FICT.xlsx',workbook())}).status_code==403
        client.cookies.set('wms_session',tokens['admin'][0]);body['csrf']=tokens['admin'][1]
        assert client.post('/cosmopak/packings/upload',data=dict(body,csrf='bad'),files={'excel':('FICT.xlsx',workbook())}).status_code==403
        response=client.post('/cosmopak/packings/upload',data=body,files={'excel':('FICT.xlsx',workbook([('DDN','A','L',50,1)]))},follow_redirects=False)
        assert response.status_code==303;path=response.headers['location'];ident=int(path.rsplit('/',1)[1])
        assert 'Mapeamento explícito' in client.get(path).text
        form=dict(body,version=0,**MAPPING,reference='000-PACK-FICT',reference_cell='G1',reason='Prova fictícia')
        assert client.post(path+'/map',data=form,follow_redirects=False).status_code==303
        assert 'Revisão antes de reservar' in client.get(path).text
        form.update(request_key=secrets.token_hex(16),version=1,accepted='yes')
        assert client.post(path+'/confirm',data=form,follow_redirects=False).status_code==303
        assert 'Marcar Preparada' in client.get(path).text
        assert client.get(path+'/source.xlsx').content.startswith(b'PK')
        client.cookies.set('ddn_context','1');assert client.get(path).status_code==403
    for statement in ('UPDATE cosmo_packing_picks SET loaded=true','DELETE FROM cosmo_packing_actions','UPDATE physical_staging_positions SET phase=\'shipped\'','TRUNCATE cosmo_packing_ledger','UPDATE cosmo_packing_states SET stage=\'shipped\''):
        with runtime.begin() as conn,pytest.raises(DBAPIError):conn.execute(text(statement))
    with scoped(runtime,3) as db,pytest.raises(s.ReceivingError):s.command(db,a,secrets.token_hex(16),ident,'approve',2,{'accepted':True},'Sem revisão')


def test_packing_rollback_between_movement_output_audit_and_commit(warehouse):
    from sqlalchemy import event
    engine,u=warehouse;a=u['admin'][0];op=u['operador'][0];stock(engine,a,(50,));ident=packing(engine,a,[('DDN','A','L',50,1)])
    command(engine,a,ident,'confirm',{'accepted':True});pick=picks(engine,ident)[0]
    with scoped(engine,3) as db:
        def fail(session):raise RuntimeError('Falha fictícia antes de commit')
        event.listen(db,'before_commit',fail)
        with pytest.raises(RuntimeError):s.command(db,op,secrets.token_hex(16),ident,'prepare',0,conference(),'Movimento físico fictício',pick[0])
    with scoped(engine,3) as db:
        assert not db.get(m.CosmoPackingPick,pick[0]).prepared
        assert db.scalar(select(func.count()).select_from(m.CosmoPackingMove))==0
        assert p.common_state(db,'A101')['state']=='occupied'
    command(engine,op,ident,'prepare',conference(),pick=pick[0],version=0)


def test_packing_documents_frozen_and_encrypted_restore_reconciles_all_books(warehouse,pg_databases,tmp_path,monkeypatch):
    from conftest import BACKUP_PASSWORD
    from iberbag import backup,transfer
    from datetime import datetime
    monkeypatch.setattr(s,'now_utc',lambda:datetime(2027,4,5,6,7,8,123450))
    engine,u=warehouse;a=u['admin'][0];op=u['operador'][0];stock(engine,a);ident=packing(engine,a)
    command(engine,a,ident,'confirm',{'accepted':True});one,two=picks(engine,ident)
    command(engine,op,ident,'prepare',conference(),pick=one[0]);command(engine,op,ident,'exception',pick=two[0])
    with scoped(engine,3) as db:label=s.document(db,a,secrets.token_hex(16),ident,'stage');before=db.get(m.CosmoPackingDocument,label).pdf
    close_dispatch(engine,a,op,ident)
    with scoped(engine,3) as db:
        doc=s.document(db,a,secrets.token_hex(16),ident,'dispatch');archived=db.get(m.CosmoPackingDocument,doc).pdf
        assert db.get(m.CosmoPackingDocument,label).pdf==before
    from pypdf import PdfReader
    reader=PdfReader(BytesIO(archived));assert 'Não expedida' in ''.join(page.extract_text() for page in reader.pages)
    assert all(abs(float(page.mediabox.width)-595.2756)<1 for page in reader.pages)
    with engine.connect() as conn:expected=transfer.inventory(transfer.read_data(conn))
    folder=tmp_path/'PACKING';backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD);target=pg_databases(restricted=True)
    assert backup.restore(target,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'
    with target.connect() as conn:assert transfer.inventory(transfer.read_data(conn))==expected
    with scoped(target,3) as db:assert db.get(m.CosmoPackingDocument,doc).pdf==archived


def test_packing_excel_numeric_identifiers_safe_formats_and_limits():
    from openpyxl import load_workbook
    book=load_workbook(BytesIO(workbook()));ws=book['Folha2'];ws['B2']=12;ws['B2'].number_format='000000';ws['C2']=45;out=BytesIO();book.save(out)
    report=x.analyze(out.getvalue());mapped=x.map_rows(report,MAPPING,'P',None,'Transcrição oficial fictícia')
    assert mapped['lines'][0]['article']=='000012' and mapped['lines'][0]['lot']=='45'
    ws['B2']=1234567890123456;out=BytesIO();book.save(out)
    with pytest.raises(ValueError):x.map_rows(x.analyze(out.getvalue()),MAPPING,'P',None,'Prova')
    ws['B2']='=1+1';out=BytesIO();book.save(out)
    with pytest.raises(ValueError):x.map_rows(x.analyze(out.getvalue()),MAPPING,'P',None,'Prova')
    with pytest.raises(ValueError):x.map_rows(x.analyze(workbook([('DDN','A','L',5,1001)])),MAPPING,'P',None,'Prova')


def test_packing_sql_rejects_forged_source_multiplicity_and_temporary_writers(restricted_web):
    from sqlalchemy.exc import DBAPIError
    from psycopg import sql
    app,owner,runtime,role,u,tokens=restricted_web;a=u['admin'][0]
    with scoped(runtime,3) as db:ident=s.preview(db,a,secrets.token_hex(16),workbook(),'FICT.xlsx')
    payload=x.map_rows(x.analyze(workbook()),MAPPING,'000-PACK-FICT','G1','Prova fictícia');payload['lines'].append(dict(payload['lines'][0],ordinal=2))
    with scoped(runtime,3) as db,pytest.raises(DBAPIError):
        db.add(m.CosmoPackingAction(packing_id=ident,request_key=secrets.token_hex(16),kind='map',expected_version=0,payload=json.dumps(payload),reason='Não multiplicar origem',actor_id=a));db.commit()
    for index,name in enumerate(('ddn_packing_initialize','ddn_packing_command','ddn_staging_apply')):
        with owner.begin() as conn:conn.exec_driver_sql(sql.SQL('GRANT EXECUTE ON FUNCTION {}() TO {}').format(sql.Identifier(name),sql.Identifier(role)).as_string())
        with runtime.begin() as conn:
            conn.execute(text(f'CREATE TEMP TABLE fake_packing_{index}(id integer)'))
            conn.execute(text(f'CREATE TRIGGER fake_writer AFTER INSERT ON fake_packing_{index} FOR EACH ROW EXECUTE FUNCTION public.{name}()'))
            with pytest.raises(DBAPIError,match='Untrusted'):conn.execute(text(f'INSERT INTO fake_packing_{index} VALUES(1)'))
    with scoped(runtime,3) as db:assert s.header(db,ident).stage=='draft' and db.scalar(select(func.count()).select_from(m.CosmoPackingLine))==0


def test_packing_migration21_preserves_identity_stock_and_verified_recovery(pg_databases,tmp_path):
    from alembic import command as alembic
    from sqlalchemy.orm import Session
    from iberbag.migrations import config,REVISION
    from iberbag.security import set_password
    from iberbag import backup,transfer
    from iberbag.schema_upgrade import apply_upgrade
    from conftest import BACKUP_PASSWORD
    engine=pg_databases(restricted=True)
    with engine.begin() as conn:cfg=config();cfg.attributes['connection']=conn;alembic.upgrade(cfg,'ddn_0021')
    with Session(engine) as db:
        m.seed_warehouse_layout(db);user=m.User(username='FICT-ADMIN',display_name='Fictício',role='admin',active=True);set_password(user,secrets.token_urlsafe(24));db.add(user);db.commit();a=user.id
    stock(engine,a,(50,))
    with engine.connect() as conn:before=transfer.inventory(transfer.read_data(conn))
    folder=tmp_path/'ANTES';backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD);restored=pg_databases(restricted=True)
    assert backup.restore(restored,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'
    assert apply_upgrade(engine,folder)['revision']==REVISION=='ddn_0022'
    with engine.connect() as conn:assert transfer.inventory(transfer.read_data(conn))==before
    with scoped(engine,3) as db:assert db.scalar(select(func.sum(m.Stock.quantidade)))==1


def test_packing_a4_complete_report_and_sqlite_refuses_commands():
    from iberbag.cosmopak_packing_pdf import render_pdf
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine
    from datetime import datetime
    from pypdf import PdfReader
    rows=[dict(line=dict(source_row=i+2,ordinal=1,article='ART-FICT-'+str(i),lot='000-L-FICT',boxes=50),pallet=dict(id=i+1,incomplete=False),pick=dict(prepared=True,loaded=False,reason='Falta física fictícia')) for i in range(105)]
    data=dict(packing=dict(reference='000-PACKING-FICTICIO-LONGO-'+'X'*90,dispatched_on='2027-04-05'),items=rows)
    pdf=render_pdf(data,'dispatch','Fictício',datetime(2027,4,5));pages=PdfReader(BytesIO(pdf)).pages;txt=''.join(p.extract_text() for p in pages)
    assert 'ART-FICT-104' in txt and '105 paletes' in txt and len(pages)>1
    with Session(create_engine('sqlite://')) as db:
        for action in (lambda:s.command(db,1,'a'*32,1,'confirm',0,{'accepted':True},'Fictício'),lambda:s.document(db,1,'b'*32,1,'stage'),lambda:s.store(db,1,'c'*32,b'x','FICT.xlsx',{})):
            with pytest.raises(s.ReceivingError,match='PostgreSQL'):action()


def test_packing_two_operators_complete_distinct_rows_and_dispatch_failure_rolls_back(warehouse):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy import event
    from iberbag import transfer
    from copy import deepcopy
    engine,u=warehouse;a=u['admin'][0];op=u['operador'][0];other=u['other'][0];stock(engine,a)
    ident=packing(engine,a);command(engine,a,ident,'confirm',{'accepted':True});rows=picks(engine,ident)
    duplicate=workbook([('DDN','A','L',1,1)])
    with scoped(engine,3) as db:again=s.preview(db,a,secrets.token_hex(16),duplicate,'OUTRO-FICT.xlsx')
    with pytest.raises(s.ReceivingError):command(engine,a,again,'map',dict(mapping=MAPPING,reference='000-PACK-FICT',reference_cell='G1'))
    barrier=Barrier(2)
    def prepare(args):
        actor,row,boxes=args;barrier.wait(timeout=10);return command(engine,actor,ident,'prepare',conference(boxes),pick=row[0],version=0)
    with ThreadPoolExecutor(2) as pool:assert len(list(pool.map(prepare,[(op,rows[0],50),(other,rows[1],23)])))==2
    command(engine,op,ident,'finish_prepare');command(engine,op,ident,'start_load')
    for row,boxes in zip(rows,(50,23)):command(engine,op,ident,'load',conference(boxes),pick=row[0])
    command(engine,op,ident,'finish_load');rev=command(engine,a,ident,'review_dispatch',{'dispatched_on':'2027-05-06'})
    with scoped(engine,3) as db:
        v=s.header(db,ident).version
        def fail(session):raise RuntimeError('Falha fictícia no commit da saída')
        event.listen(db,'before_commit',fail)
        with pytest.raises(RuntimeError):s.command(db,a,secrets.token_hex(16),ident,'approve',v,{'accepted':True,'review_id':rev},'Confirmação fictícia')
    with scoped(engine,3) as db:
        assert s.header(db,ident).stage=='dispatch_reviewed' and db.scalar(select(func.sum(m.Stock.quantidade)))==2
        assert all(r[4] for r in picks(engine,ident))
    command(engine,a,ident,'approve',{'accepted':True,'review_id':rev},version=v)
    with engine.connect() as conn:data=transfer.read_data(conn)
    corrupted=deepcopy(data);corrupted['cosmo_packing_picks'][0]['loaded']=False
    with pytest.raises(ValueError):s.validate_snapshot(corrupted)
    corrupted=deepcopy(data);corrupted['cosmo_packing_states'][0]['stage']='loading'
    with pytest.raises(ValueError):s.validate_snapshot(corrupted)


def test_packing_stage_label_uses_latest_immutable_completion_not_print_date(warehouse,monkeypatch):
    from datetime import datetime
    from pypdf import PdfReader
    engine,u=warehouse;a=u['admin'][0];op=u['operador'][0];stock(engine,a,(50,))
    ident=packing(engine,a,[('DDN','A','L',50,1)]);command(engine,a,ident,'confirm',{'accepted':True});pick=picks(engine,ident)[0]
    command(engine,op,ident,'prepare',conference(),pick=pick[0])
    monkeypatch.setattr(s,'now_utc',lambda:datetime(2027,4,5,11,12,13))
    command(engine,op,ident,'finish_prepare')
    monkeypatch.setattr(s,'now_utc',lambda:datetime(2027,4,8,17,18,19))
    key=secrets.token_hex(16)
    with scoped(engine,3) as db:
        first=s.document(db,a,key,ident,'stage');original=db.get(m.CosmoPackingDocument,first).pdf
    text_pdf=lambda data:''.join(p.extract_text() for p in PdfReader(BytesIO(data)).pages)
    assert 'Preparação concluída: 05/04/2027 11:12:13 UTC' in text_pdf(original)
    assert '000-PACK-FICT' in text_pdf(original) and 'Previstas: 1 paletes' in text_pdf(original) and 'Preparadas: 1' in text_pdf(original)
    command(engine,a,ident,'reopen_prepare')
    with scoped(engine,3) as db:
        pending=s.document(db,a,secrets.token_hex(16),ident,'stage')
        assert 'Preparação ainda não concluída' in text_pdf(db.get(m.CosmoPackingDocument,pending).pdf)
    monkeypatch.setattr(s,'now_utc',lambda:datetime(2027,4,10,9,8,7));command(engine,op,ident,'finish_prepare')
    monkeypatch.setattr(s,'now_utc',lambda:datetime(2027,4,12,15,16,17))
    with scoped(engine,3) as db:
        second=s.document(db,a,secrets.token_hex(16),ident,'stage')
        assert 'Preparação concluída: 10/04/2027 09:08:07 UTC' in text_pdf(db.get(m.CosmoPackingDocument,second).pdf)
        assert s.document(db,a,key,ident,'stage')==first
        assert db.get(m.CosmoPackingDocument,first).pdf==original
