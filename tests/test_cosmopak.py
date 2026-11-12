"""Fictitious Camião DDN files and disposable databases only."""
from io import BytesIO
import hashlib,json,secrets
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,text,func
from sqlalchemy.exc import DBAPIError
from alembic import command
from iberbag import cosmopak as c,cosmopak_excel as x,database as m,physical_locations as p,receiving as r,backup,transfer
from iberbag.migrations import config,REVISION
from iberbag.schema_upgrade import apply_upgrade
from test_receiving import warehouse
from test_ddn_contexts import scoped
from test_transfer import pg_databases,docker_client
from test_shared_locations import activate,receive as iberbag_receive,hold as iberbag_hold
from test_security_regressions import restricted_web
from conftest import BACKUP_PASSWORD


def workbook(rows=None,mutate=None):
    from openpyxl import Workbook
    b=Workbook();w=b.active;w.title='Camião DDN'
    w.append([None,'Artigo','Descrição','Lote','Quantidade de paletes','Número de caixas por palete',None,None,'Palete incompleta',None,'IGNORAR'])
    for row in rows or [('0001','Descrição fictícia','0007',1,50,''),('0001','Descrição fictícia','0007',1,23,'X')]:
        a,d,l,q,bx,mark=row;w.append([None,a,d,l,q,bx,None,None,mark,None,'=1+1'])
    b.create_sheet('BD Artigos').append(['=1/0'])
    if mutate:mutate(b,w)
    out=BytesIO();b.save(out);return out.getvalue()


def plan(engine,actor,rows=None,key=None):
    with scoped(engine,3) as db:return c.preview_file(db,actor,key or secrets.token_hex(16),workbook(rows),'FICTICIO.xlsx')


def units(engine,ident):
    with scoped(engine,3) as db:return [(a.id,b.version,b.status,b.local,b.actual_boxes,b.decision) for a,b in c.pallets(db,ident)]


def place(engine,actor,pallet,local,boxes=None,reason='Posição e conteúdo conferidos fisicamente',vacated=False,key=None):
    with scoped(engine,3) as db:
        state=db.scalar(select(m.CosmoState).where(m.CosmoState.pallet_id==pallet))
        return c.change_pallet(db,actor,key or secrets.token_hex(16),pallet,state.version,'place',local,boxes,reason,vacated)


def review(engine,admin,operator,ident,day='2027-03-04'):
    with scoped(engine,3) as db:
        rec=db.get(m.CosmoReception,ident)
        c.metadata(db,admin,secrets.token_hex(16),ident,rec.version,'GT-FICTICIA',day,'HIST-FICTICIA','Guia em papel e chegada comprovadas')
        c.finish_unloading(db,operator,secrets.token_hex(16),ident,rec.version,'Descarga e faltas conferidas')
        return c.review(db,admin,secrets.token_hex(16),ident,rec.version,'Revisão administrativa integral comprovada')


def confirm(engine,admin,ident,rev,key=None):
    with scoped(engine,3) as db:return c.approve(db,admin,key or secrets.token_hex(16),ident,rev,True)


def test_cosmo_excel_selected_sheet_box_counts_and_original_identity():
    data=workbook([('0001','Fictício','0007',2,50,''),('0001','Fictício','0007',1,23,'X')]);before=hashlib.sha256(data).hexdigest()
    report=x.analyze(data)
    assert report['sha256']==before and report['pallets']==3 and report['boxes']==123 and not report['issues']
    assert [r['boxes'] for r in report['rows']]==[50,23] and [r['incomplete'] for r in report['rows']]==[False,True]
    assert report['rows'][0]['article']=='0001' and report['rows'][0]['lot']=='0007' and report['rows'][0]['row']==2
    def numeric(b,w):w['B2']=12;w['B2'].number_format='000000'
    assert x.analyze(workbook(mutate=numeric))['rows'][0]['article']=='000012'


@pytest.mark.parametrize('field,value',[(0,None),(2,None),(3,0),(3,-1),(3,1.5),(3,1001),(4,None),(4,-1),(4,2.5),(5,'?')])
def test_cosmo_excel_missing_ambiguous_and_invalid_quantities(field,value):
    row=['A','Fictício','L',1,5,''];row[field]=value
    if field==3 and value==1001:
        with pytest.raises(ValueError):x.analyze(workbook([row]))
    else:assert x.analyze(workbook([row]))['issues']


def test_cosmo_excel_formula_xml_zip_limits_and_sqlite_write_prohibition():
    with pytest.raises(ValueError):x.analyze(b'not an excel')
    with pytest.raises(ValueError):x.analyze(b'x'*(x.MAX_FILE+1))
    with pytest.raises(ValueError,match='fórmula'):x.analyze(workbook([('=1','Fictício','L',1,5,'')]))
    def missing(b,w):w.title='Outra folha'
    with pytest.raises(ValueError,match='Camião DDN'):x.analyze(workbook(mutate=missing))
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    with Session(create_engine('sqlite://')) as db,pytest.raises(c.ReceivingError,match='PostgreSQL'):
        c.finish_unloading(db,1,secrets.token_hex(16),1,1,'Fictício')


def test_cosmo_planned_pallets_individual_partial_stock_and_atomic_approval(warehouse):
    engine,u=warehouse;admin=u['admin'][0];op=u['operador'][0]
    for code in ('A101','A102','A103'):activate(engine,admin,code)
    ident=plan(engine,admin,[('0001','Fictício','0007',2,50,''),('0001','Fictício','0007',1,23,'X')])
    rows=units(engine,ident);assert len(rows)==3
    for item,code in zip(rows,['A101','A102','A103']):place(engine,op,item[0],code)
    with scoped(engine,3) as db:
        assert db.scalar(select(func.count()).select_from(m.Stock))==0
        assert all(p.common_state(db,code)['state']=='pending_putaway' for code in ('A101','A102','A103'))
        assert db.get(m.Client,3).pallet_operations is False
    rev=review(engine,admin,op,ident);key=secrets.token_hex(16);receipt=confirm(engine,admin,ident,rev,key)
    assert confirm(engine,admin,ident,rev,key)==receipt
    with scoped(engine,3) as db:
        rec=db.get(m.Receipt,receipt);assert rec.code=='27-001'
        assert db.scalar(select(func.sum(m.Stock.quantidade)))==3 and db.scalar(select(func.count()).select_from(m.CosmoOperation).where(m.CosmoOperation.kind=='approve'))==3
        assert [s.actual_boxes for a,s in c.pallets(db,ident)]==[50,50,23]
        assert all(p.common_state(db,code)['state']=='occupied' for code in ('A101','A102','A103'))
    with scoped(engine) as db:assert db.scalar(select(func.sum(m.Stock.quantidade))) is None


def test_cosmo_missing_and_divergent_boxes_need_explicit_decisions(warehouse):
    engine,u=warehouse;admin=u['admin'][0];op=u['operador'][0];activate(engine,admin)
    ident=plan(engine,admin);a,b=units(engine,ident)
    place(engine,op,a[0],'A101',19)
    with scoped(engine,3) as db:
        c.change_pallet(db,op,secrets.token_hex(16),b[0],b[1],'missing',reason='Falta fisicamente confirmada')
        rec=db.get(m.CosmoReception,ident)
        c.metadata(db,admin,secrets.token_hex(16),ident,rec.version,'G','2027-01-01',None,'Guia comprovada')
        c.finish_unloading(db,op,secrets.token_hex(16),ident,rec.version,'Descarga concluída com faltas')
        with pytest.raises(c.ReceivingError):c.review(db,admin,secrets.token_hex(16),ident,rec.version,'Revisão ainda divergente')
    for pid,decision in ((a[0],'accept'),(b[0],'exclude')):
        with scoped(engine,3) as db:
            state=db.scalar(select(m.CosmoState).where(m.CosmoState.pallet_id==pid))
            c.change_pallet(db,admin,secrets.token_hex(16),pid,state.version,'decide',reason='Quantidade/falta comprovada pela descarga',decision=decision)
    with scoped(engine,3) as db:rev=c.review(db,admin,secrets.token_hex(16),ident,db.get(m.CosmoReception,ident).version,'Diferenças resolvidas explicitamente')
    confirm(engine,admin,ident,rev)
    with scoped(engine,3) as db:
        assert db.scalar(select(func.sum(m.Stock.quantidade)))==1
        assert [s.actual_boxes for p,s in c.pallets(db,ident)]==[19,None]
        assert db.scalar(select(m.CosmoPallet.boxes).where(m.CosmoPallet.id==a[0]))==50


def test_cosmo_correction_requires_physical_vacancy_and_missing_never_releases(warehouse):
    engine,u=warehouse;admin=u['admin'][0];op=u['operador'][0]
    activate(engine,admin);activate(engine,admin,'A102');ident=plan(engine,admin);a=units(engine,ident)[0]
    with pytest.raises(c.ReceivingError):place(engine,op,a[0],'Chão')
    place(engine,op,a[0],'A101')
    with pytest.raises(c.ReceivingError):place(engine,op,a[0],'A102')
    place(engine,op,a[0],'A102',vacated=True)
    with scoped(engine,3) as db:
        assert p.common_state(db,'A101')['state']=='free' and p.common_state(db,'A102')['source_id']==a[0]
        state=db.scalar(select(m.CosmoState).where(m.CosmoState.pallet_id==a[0]))
        c.change_pallet(db,op,secrets.token_hex(16),a[0],state.version,'missing',reason='Não encontrada; investigar sem libertar')
        assert p.common_state(db,'A102')['state']=='pending_putaway'
        state=db.scalar(select(m.CosmoState).where(m.CosmoState.pallet_id==a[0]))
        with pytest.raises(c.ReceivingError):c.change_pallet(db,admin,secrets.token_hex(16),a[0],state.version,'decide',decision='exclude',reason='Não pode libertar automaticamente')
    line=iberbag_receive(engine,op)
    with pytest.raises(c.ReceivingError):iberbag_hold(engine,op,line,local='A102')


def test_cosmo_concurrent_owners_or_trucks_get_one_position(warehouse):
    engine,u=warehouse;admin=u['admin'][0];op=u['operador'][0];activate(engine,admin)
    ids=[plan(engine,admin,[('A','Fictício','L',1,7,'')]) for _ in range(2)];pal=[units(engine,v)[0][0] for v in ids];barrier=Barrier(2)
    def attempt(pid):
        barrier.wait(timeout=10)
        try:place(engine,op,pid,'A101');return True
        except c.ReceivingError:return False
    with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(attempt,pal))==[False,True]
    line=iberbag_receive(engine,op)
    with pytest.raises(c.ReceivingError):iberbag_hold(engine,op,line)


def test_cosmo_approval_rollback_versions_numbering_and_multiple_trucks(warehouse,monkeypatch):
    engine,u=warehouse;admin=u['admin'][0];op=u['operador'][0]
    activate(engine,admin);activate(engine,admin,'A102')
    ids=[]
    for code in ('A101','A102'):
        ident=plan(engine,admin,[('A','Fictício','L',1,7,'')]);place(engine,op,units(engine,ident)[0][0],code);ids.append((ident,review(engine,admin,op,ident)))
    original=c.add_review
    def fail(*args,**kwargs):
        if args[4]=='approve':raise RuntimeError('Falha fictícia após stock, antes de conclusão')
        return original(*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(c,'add_review',fail)
        with pytest.raises(RuntimeError):confirm(engine,admin,*ids[0])
    with scoped(engine,3) as db:
        assert db.scalar(select(func.count()).select_from(m.Stock))==0
        assert p.common_state(db,'A101')['state']=='pending_putaway'
        assert db.scalar(select(func.count()).select_from(m.LoadNumber))==0
    receipts=[confirm(engine,admin,*v) for v in ids]
    with scoped(engine,3) as db:assert set(db.scalars(select(m.Receipt.code)))=={'27-001','27-002'} and len(set(receipts))==2


def test_cosmo_revisions_replays_and_context_permissions(warehouse):
    engine,u=warehouse;admin=u['admin'][0];op=u['operador'][0];activate(engine,admin)
    data=workbook([('A','Fictício','L',1,7,'')]);key=secrets.token_hex(16)
    with scoped(engine,3) as db:
        ident=c.preview_file(db,admin,key,data,'FICT.xlsx');assert c.preview_file(db,admin,key,data,'FICT.xlsx')==ident
        with pytest.raises(c.ReceivingError):c.preview_file(db,op,secrets.token_hex(16),data,'FICT.xlsx')
    pid=units(engine,ident)[0][0];place(engine,op,pid,'A101');rev=review(engine,admin,op,ident)
    place(engine,op,pid,'A101',6)
    with pytest.raises(c.ReceivingError,match='desatualizada'):confirm(engine,admin,ident,rev)
    with pytest.raises(c.ReceivingError):confirm(engine,op,ident,rev)
    with scoped(engine) as db,pytest.raises(c.ReceivingError,match='Cosmopak'):c.finish_unloading(db,op,secrets.token_hex(16),ident,1,'Contexto errado')


def test_cosmo_runtime_http_csrf_authorization_and_projection_guards(restricted_web,monkeypatch):
    app,owner,runtime,role,u,tokens=restricted_web;admin=u['admin'][0]
    activate(owner,admin)
    with TestClient(app) as client:
        client.cookies.set('ddn_context','3');client.cookies.set('wms_session',tokens['operador'][0])
        body=dict(csrf=tokens['operador'][1],context_id='3',request_key=secrets.token_hex(16))
        assert client.post('/cosmopak/receptions/upload',data=body,files={'excel':('FICT.xlsx',workbook())}).status_code==403
        client.cookies.set('wms_session',tokens['admin'][0]);body['csrf']=tokens['admin'][1]
        calls=[];original=c.analyze
        def analyze(data):calls.append(1);return original(data)
        with monkeypatch.context() as patch:
            patch.setattr(c,'analyze',analyze)
            assert client.post('/cosmopak/receptions/upload',data=dict(body,csrf='bad'),files={'excel':('FICT.xlsx',workbook())}).status_code==403 and not calls
            response=client.post('/cosmopak/receptions/upload',data=body,files={'excel':('FICT.xlsx',workbook([('A','Fictício','L',1,7,'')]))},follow_redirects=False)
        assert response.status_code==303 and calls==[1]
        path=response.headers['location'];ident=int(path.rsplit('/',1)[1]);pid=units(owner,ident)[0][0]
        assert 'COMPLETA' in client.get(path).text and 'Camião DDN!2' in client.get(path).text
        assert client.get('/').headers.get('content-type','').startswith('text/html')
        with scoped(owner,3) as db:archived=db.get(m.CosmoReception,ident).source_excel
        assert client.get(path+'/source.xlsx').content==archived
        data=dict(csrf=tokens['admin'][1],context_id='3',request_key=secrets.token_hex(16),version='0',kind='place',local='A101',reason='Conferência física fictícia')
        assert client.post(f'/cosmopak/pallets/{pid}',data=dict(data,csrf='bad')).status_code==403
        response=client.post(f'/cosmopak/pallets/{pid}',data=data,follow_redirects=False);assert response.status_code==303
        assert 'Descarga, aguarda aprovação' in client.get('/cosmopak/stock?lot=L').text
        client.cookies.set('ddn_context','1')
        assert client.get(path).status_code==403
    for statement in ('UPDATE cosmo_states SET status=\'approved\'','DELETE FROM cosmo_operations','UPDATE cosmo_receptions SET version=99',"UPDATE cosmo_pallets SET boxes=999",'TRUNCATE cosmo_states'):
        with runtime.begin() as conn,pytest.raises(DBAPIError):conn.execute(text(statement))


def test_cosmo_migration20_and_encrypted_restore_preserve_all_owners_and_pending(pg_databases,tmp_path):
    from sqlalchemy.orm import Session
    from iberbag.security import set_password
    engine=pg_databases(restricted=True);target=pg_databases(restricted=True)
    with engine.begin() as conn:
        cfg=config();cfg.attributes['connection']=conn;command.upgrade(cfg,'ddn_0020')
    with Session(engine) as db:
        m.seed_warehouse_layout(db)
        user=m.User(username='fict-admin',display_name='Fictício',role='admin',active=True);set_password(user,secrets.token_urlsafe(24));db.add(user);db.commit();admin=user.id
    activate(engine,admin);activate(engine,admin,'A102')
    from test_shared_locations import put
    line=iberbag_receive(engine,admin);put(engine,admin,line,local='A103')
    with engine.connect() as conn:before=transfer.inventory(transfer.read_data(conn))
    old=tmp_path/'old';backup.backup(engine,old,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert backup.restore(target,old,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'
    assert apply_upgrade(engine,old)['revision']==REVISION=='ddn_0021'
    with engine.connect() as conn:assert transfer.inventory(transfer.read_data(conn))==before
    first=plan(engine,admin,[('A','Fictício','L',1,8,'X')]);place(engine,admin,units(engine,first)[0][0],'A101');confirm(engine,admin,first,review(engine,admin,admin,first))
    second=plan(engine,admin,[('A','Fictício','L',1,4,'')]);place(engine,admin,units(engine,second)[0][0],'A102')
    with engine.connect() as conn:expected=transfer.inventory(transfer.read_data(conn))
    target=pg_databases(restricted=True)
    folder=tmp_path/'cosmo';backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert backup.restore(target,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'
    with target.connect() as conn:assert transfer.inventory(transfer.read_data(conn))==expected
    with scoped(target,3) as db:
        assert p.common_state(db,'A101')['state']=='occupied' and p.common_state(db,'A102')['state']=='pending_putaway'
        assert db.get(m.CosmoReception,first).source_excel.startswith(b'PK')


def test_cosmo_approval_concurrency_is_idempotent_and_invalid_previews_never_stock(warehouse):
    engine,u=warehouse;admin=u['admin'][0];activate(engine,admin)
    ident=plan(engine,admin,[('A','Fictício','L',1,3,'')]);place(engine,admin,units(engine,ident)[0][0],'A101');rev=review(engine,admin,admin,ident);barrier=Barrier(2)
    def approve(_):barrier.wait(timeout=10);return confirm(engine,admin,ident,rev)
    with ThreadPoolExecutor(2) as pool:assert len(set(pool.map(approve,[1,2])))==1
    bad=plan(engine,admin,[('A','Fictício','L',1,None,'')]);assert units(engine,bad)==[]
    with scoped(engine,3) as db:
        rec=db.get(m.CosmoReception,bad)
        with pytest.raises(c.ReceivingError):c.finish_unloading(db,admin,secrets.token_hex(16),bad,rec.version,'Não existe quantidade comprovada')
        assert db.scalar(select(func.sum(m.Stock.quantidade)))==1


def test_cosmo_iberbag_accounting_release_waits_for_physical_completion(warehouse):
    from test_shared_locations import put,prepare
    from iberbag.inventorying import close_preparation
    engine,u=warehouse;admin=u['admin'][0];activate(engine,admin)
    line=iberbag_receive(engine,admin);put(engine,admin,line);order=prepare(engine,admin)
    ident=plan(engine,admin,[('A','Fictício','L',1,3,'')]);pid=units(engine,ident)[0][0]
    with pytest.raises(c.ReceivingError):place(engine,admin,pid,'A101')
    with scoped(engine) as db:close_preparation(db,admin,order,'Retirada física comprovada no escritório')
    place(engine,admin,pid,'A101');confirm(engine,admin,ident,review(engine,admin,admin,ident))
    with scoped(engine,3) as db:assert db.scalar(select(func.sum(m.Stock.quantidade)))==1
    with scoped(engine) as db:assert db.scalar(select(func.sum(m.Stock.quantidade)))==0


def test_cosmo_all_missing_and_historical_reference_never_invent_stock_or_dates(warehouse):
    engine,u=warehouse;admin=u['admin'][0];op=u['operador'][0];ident=plan(engine,admin,[('A','Fictício','L',1,0,'X')])
    with scoped(engine,3) as db:
        rec=db.get(m.CosmoReception,ident);assert rec.arrived_on is None and rec.receipt_id is None
        pid=units(engine,ident)[0][0]
        c.change_pallet(db,op,secrets.token_hex(16),pid,0,'missing',reason='Falta integral comprovada')
        state=db.scalar(select(m.CosmoState).where(m.CosmoState.pallet_id==pid))
        c.change_pallet(db,admin,secrets.token_hex(16),pid,state.version,'decide',decision='exclude',reason='Confirmar falta total; não criar stock')
        with pytest.raises(c.ReceivingError):c.metadata(db,admin,secrets.token_hex(16),ident,rec.version,'GT','2026-01-01',None,'Sem referência histórica não inventar')
    rev=review(engine,admin,op,ident,'2026-01-01');receipt=confirm(engine,admin,ident,rev)
    with scoped(engine,3) as db:
        assert db.get(m.Receipt,receipt).code=='HIST-FICTICIA' and db.scalar(select(func.count()).select_from(m.Stock))==0
        assert db.scalar(select(func.count()).select_from(m.LoadNumber))==0


def test_cosmo_location_correction_preserves_observed_boxes_and_conflict_rolls_back(warehouse):
    engine,u=warehouse;admin=u['admin'][0];activate(engine,admin);activate(engine,admin,'A102')
    ident=plan(engine,admin);a,b=units(engine,ident)
    place(engine,admin,a[0],'A101',11);place(engine,admin,b[0],'A102')
    with pytest.raises(c.ReceivingError):place(engine,admin,a[0],'A102',vacated=True)
    with scoped(engine,3) as db:
        assert p.common_state(db,'A101')['source_id']==a[0]
        assert db.scalar(select(m.CosmoState.actual_boxes).where(m.CosmoState.pallet_id==a[0]))==11
    place(engine,admin,a[0],'A101')
    assert units(engine,ident)[0][4]==11


def test_cosmo_numeric_precision_and_repeated_huge_strings_are_rejected():
    from zipfile import ZipFile,ZIP_DEFLATED
    from defusedxml import ElementTree as ET
    def rewrite(payload,tag,value):
        source=ZipFile(BytesIO(payload));out=BytesIO()
        with source,ZipFile(out,'w',ZIP_DEFLATED) as dest:
            for name in source.namelist():
                data=source.read(name)
                if name=='xl/worksheets/sheet1.xml':
                    root=ET.fromstring(data)
                    cell=root.find(f".//x:c[@r='{tag}']/x:v",x.N);cell.text=value
                    data=ET.tostring(root)
                dest.writestr(name,data)
        return out.getvalue()
    precise=rewrite(workbook(),'E2','1.0000000000000001')
    assert x.analyze(precise)['issues']
    with pytest.raises(ValueError):x.analyze(workbook([('A','x'*1000,'L',1,3,'')]))


def test_cosmo_generic_divergence_without_box_difference_still_blocks_until_decision(warehouse):
    engine,u=warehouse;admin=u['admin'][0];operator=u['operador'][0];activate(engine,admin)
    ident=plan(engine,admin,[('A','Fictício','L',1,3,'')]);pid=units(engine,ident)[0][0]
    with scoped(engine,3) as db:
        c.change_pallet(db,operator,secrets.token_hex(16),pid,0,'place','A101',reason='Divergência adicional a investigar',decision='unresolved')
        state=db.scalar(select(m.CosmoState).where(m.CosmoState.pallet_id==pid));assert state.decision=='unresolved'
        with pytest.raises(c.ReceivingError):c.change_pallet(db,operator,secrets.token_hex(16),pid,state.version,'decide',decision='accept',reason='Operador não decide')
    with pytest.raises(c.ReceivingError):review(engine,admin,operator,ident)
    with scoped(engine,3) as db:
        state=db.scalar(select(m.CosmoState).where(m.CosmoState.pallet_id==pid))
        c.change_pallet(db,admin,secrets.token_hex(16),pid,state.version,'decide',decision='accept',reason='Identidade e conteúdo previstos comprovados pela investigação')
        rev=c.review(db,admin,secrets.token_hex(16),ident,db.get(m.CosmoReception,ident).version,'Revisão após resolução da divergência')
    confirm(engine,admin,ident,rev)


def test_cosmo_restored_function_acls_cannot_forge_projection_writers(restricted_web):
    from psycopg import sql
    app,owner,runtime,role,u,tokens=restricted_web
    names=('ddn_cosmo_initial','ddn_cosmo_operation','ddn_cosmo_physical','ddn_cosmo_review')
    with owner.begin() as conn:
        for name in names:
            conn.exec_driver_sql(sql.SQL('GRANT EXECUTE ON FUNCTION {}() TO {}').format(sql.Identifier(name),sql.Identifier(role)).as_string())
    for index,name in enumerate(names):
        with runtime.begin() as conn:
            conn.execute(text(f'CREATE TEMP TABLE fake_cosmo_{index}(id integer)'))
            conn.execute(text(f'CREATE TRIGGER fake_writer BEFORE INSERT ON fake_cosmo_{index} FOR EACH ROW EXECUTE FUNCTION public.{name}()'))
            with pytest.raises(DBAPIError,match='Untrusted Cosmopak'):
                conn.execute(text(f'INSERT INTO fake_cosmo_{index}(id) VALUES(1)'))
    with runtime.connect() as conn:assert conn.scalar(text('SELECT count(*) FROM cosmo_states'))==0
