"""Historical baseline imports use only generated fictitious eight-sheet workbooks."""
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json,secrets
import pytest
from sqlalchemy import select,func,text
from sqlalchemy.exc import DBAPIError
from iberbag import valmet_import as service,valmet as v,valmet_planning as p,valmet_rolls as rolls,backup,transfer
from iberbag import database as m
from test_valmet import valmet,row,entry
from test_receiving import warehouse
from test_transfer import pg_databases,docker_client
from test_ddn_contexts import scoped
from test_security_regressions import restricted_web
from test_valmet_planning import draft
from test_valmet_shipping import send
from conftest import BACKUP_PASSWORD

LAYOUTS={
'Caixas com Peças':['Artigo','Lote','Quantidade (Caixas)','Nº Armazém','Quantidade (peças)','Localização'],
'Caixas Vazias':['Artigo','Lote','Nº Armazém'],
'Caixas':['Artigo','Lote','Quantidade (Caixas)','Localização','Nº Armazém','Nota'],
'Prateleiras':['Artigo','Lote','Quantidade (Paletes)','Localização','Nº Armazém','Nota'],
'Paletes':['Artigo','Lote','Quantidade (Paletes)','Localização','Nº Armazém','Quantidade (m²)'],
'Vasilhame':['Referência','Quantidade (Paletes)'],
'Paletes Fio':['Artigo','Lote','Quantidade (Paletes)','Localização','Notas'],
'Rolos':['Lote','Nº armazém','Localização','Divisível']}
def workbook(change=None):
    from openpyxl import Workbook
    b=Workbook();b.remove(b.active)
    for name,header in LAYOUTS.items():b.create_sheet(name).append(header)
    data={'Caixas com Peças':[['ART','PC',1,'PIECES',12,'A2']], 'Caixas Vazias':[[None,'EMPTY','EMPTY-1']],
    'Caixas':[['ART','BOX',1,'A2','BOX-1','VALMET 2']], 'Prateleiras':[['ART','SHELF',1,'A2','SHELF-1','nota']],
    'Paletes':[['ART','PALLET',1,'A2','PALLET-1',12.5]],'Vasilhame':[['Filva',2]],
    'Paletes Fio':[['ART','FIO',2,'Chão','nota livre']], 'Rolos':[['A','R-1','A2','X'],['A','R-1','A2','X'],['B','R-1','A2',None],['C','R-1','A2',None]]}
    for name,rows in data.items():
        for r in rows:b[name].append(r)
    if change:change(b)
    out=BytesIO();b.save(out);return out.getvalue()
def decisions():return dict(historical_numbers=['Paletes!2','Prateleiras!2'],warnings=['ARTIGO_DESCONHECIDO','POR_LOCALIZAR'])
def stage(engine,actor,content=None,choices=None):
    with scoped(engine,2) as db:
        ident=service.preview_file(db,actor,secrets.token_hex(16),content or workbook())
        revision=service.reconcile(db,actor,ident,secrets.token_hex(16),choices if choices is not None else decisions(),'Prova fictícia: Excel e existência física conferidos no escritório')
        rec=db.get(m.ValmetImportRevision,revision)
        return ident,revision,rec.digest,json.loads(rec.plan)
def confirm(engine,actor,revision,digest,key=None):
    with scoped(engine,2) as db:return service.confirm(db,actor,revision,key or secrets.token_hex(16),digest,True)

def test_historical_import_all_layouts_official_stock_rolls_and_idempotency(valmet):
    engine,a=valmet;actor=a['admin'][0];content=workbook();original=sha256(content).hexdigest()
    ident,revision,digest,plan=stage(engine,actor,content)
    assert plan['blocking']==0,plan['issues']
    assert plan['physical']==dict(pecas=1,vazias=1,caixas=1,prateleiras=1,paletes=1,vasilhame=2,fio=2,rolos=1)
    assert dict(kind='rolos',local='A2',quantity=1) in plan['physical_by_location']
    assert dict(kind='rolos',lot='A',local='A2',presences=1) in plan['physical_by_lot']
    with scoped(engine,2) as db:assert db.scalar(select(func.count()).select_from(m.ValmetUnit))==0
    key=secrets.token_hex(16);record=confirm(engine,actor,revision,digest,key)
    assert confirm(engine,actor,revision,digest,key)==record
    with scoped(engine,2) as db:
        assert db.scalar(select(func.count()).select_from(m.Receipt))==0
        assert db.scalar(select(func.count()).select_from(m.LoadNumber))==0
        assert db.scalar(select(func.count()).select_from(m.Carga))==0
        units=db.scalars(select(m.ValmetUnit).order_by(m.ValmetUnit.id)).all();assert len(units)==8
        assert sum(p.physical_quantity for p in db.scalars(select(m.ValmetPosition)))==10
        rolled=next(u for u in units if u.kind=='rolos');ids=rolls.present(db,rolled.id)
        assert len(ids)==4 and [r['divisible'] for r in ids]==[True,True,False,False]
        assert [r['lot'] for r in ids]==['A','A','B','C']
        audit=json.loads(db.scalar(select(m.Audit.details).where(m.Audit.action=='VALMET_IMPORT_CONFIRMAR')))
        mapping=next(x for x in audit['mapping'] if x['unit_id']==rolled.id)
        assert mapping['rolls']==[dict(id=r['id'],ref=f'Rolos!{i+2}') for i,r in enumerate(ids)]
        assert next(u for u in units if u.kind=='vazias').local=='Chão'
        assert next(u for u in units if u.kind=='vasilhame').local is None
        assert str(next(u for u in units if u.kind=='paletes').material_m2)=='12.500'
        assert db.get(m.ValmetImportDraft,ident).digest==original and sha256(content).hexdigest()==original
        assert db.scalar(select(m.ValmetEntry)).status=='imported'
        assert len(v.stock(db,{'notes':'VALMET 2'})[0])==1
    order=draft(engine,actor,[('rolos','A','1')])
    with scoped(engine,2) as db:p.review(db,actor,order,1);p.confirm(db,actor,order,1)
    send(engine,actor,order)
    with scoped(engine,2) as db:assert len(rolls.present(db,rolled.id))==3
    assert confirm(engine,actor,revision,digest)==record

@pytest.mark.parametrize('case',['quantity','duplicate','formula','capacity','identity','unknown_location','missing_sheet','material_quantity','extra_sheet'])
def test_historical_import_blocking_unknowns_and_catalogue(valmet,case):
    engine,a=valmet;actor=a['admin'][0]
    def change(b):
        if case=='quantity':b['Paletes Fio']['C2']=None
        if case=='duplicate':b['Caixas'].append(['ART','BOX',1,'A2','BOX-1','VALMET 2'])
        if case=='formula':b['Paletes']['F2']='=1+2'
        if case=='capacity':b['Paletes Fio']['C2']=100;b['Paletes Fio']['D2']='A2'
        if case=='identity':b['Caixas Vazias']['C2']='BOX-1'
        if case=='unknown_location':b['Paletes Fio']['D2']='FOREIGN'
        if case=='missing_sheet':del b['Rolos']
        if case=='extra_sheet':b.create_sheet('Mercadoria desconhecida').append(['Não ignorar silenciosamente'])
        if case=='material_quantity':b['Paletes Fio']['F1']='Quantidade';b['Paletes Fio']['F2']=99
    if case=='formula':
        with scoped(engine,2) as db:
            with pytest.raises(v.ReceivingError):service.preview_file(db,actor,secrets.token_hex(16),workbook(change))
        return
    _,rev,digest,plan=stage(engine,actor,workbook(change))
    assert plan['blocking']>0
    with pytest.raises(v.ReceivingError):confirm(engine,actor,rev,digest)
    with scoped(engine,2) as db:assert db.scalar(select(func.count()).select_from(m.ValmetUnit))==0

def test_historical_import_reconciliation_provenance_unknown_not_zero(valmet):
    engine,a=valmet;actor=a['admin'][0]
    content=workbook(lambda b:setattr(b['Paletes Fio']['C2'],'value',None))
    ident,rev,digest,plan=stage(engine,actor,content);assert plan['blocking']
    choices=decisions();choices['rows']={'Paletes Fio!2':{'fisico':3}}
    with scoped(engine,2) as db:
        new=service.reconcile(db,actor,ident,secrets.token_hex(16),choices,'Contagem física fictícia: três paletes comprovadas')
        r=db.get(m.ValmetImportRevision,new);new_digest=r.digest;data=json.loads(r.plan);assert data['blocking']==0
        original=json.loads(db.get(m.ValmetImportDraft,ident).report);assert original['source_sheets']['Paletes Fio'][1][1][2] is None
        assert data['rows'][-2]['physical_quantity']==3
        assert data['origins'][-2]['originals'][0]['cells'][2] is None
        assert data['origins'][-2]['corrected_cells'][0]['cells'][2]==3
        with pytest.raises(v.ReceivingError):service.confirm(db,actor,new,secrets.token_hex(16),r.digest,False)
    confirm(engine,actor,new,new_digest)

def test_historical_import_stale_reservations_failure_rollback_and_concurrency(valmet,monkeypatch):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('paletes',None,physical_quantity=2,items=[('ART','OLD',2)],content_total=None,local='Chão')])
    choices=decisions();choices['warnings'].append('STOCK_EXISTENTE_EXIGE_PROVA_DE_NAO_DUPLICACAO')
    ident,rev,digest,plan=stage(engine,actor,choices=choices);assert not plan['blocking']
    order=draft(engine,actor,[('paletes','OLD','1')])
    with scoped(engine,2) as db:p.review(db,actor,order,1);p.confirm(db,actor,order,1)
    with pytest.raises(v.ReceivingError,match='mudaram'):confirm(engine,actor,rev,digest)
    with scoped(engine,2) as db:
        rev=service.reconcile(db,actor,ident,secrets.token_hex(16),choices,'Prova revista após reserva, mercadoria adicional distinta');digest=db.get(m.ValmetImportRevision,rev).digest
    old=v.add_identification;calls=[]
    def fail(*args,**kw):
        calls.append(1)
        if len(calls)==3:raise RuntimeError('Falha fictícia durante importação')
        return old(*args,**kw)
    monkeypatch.setattr(v,'add_identification',fail)
    with pytest.raises(RuntimeError):confirm(engine,actor,rev,digest)
    with scoped(engine,2) as db:
        assert db.scalar(select(func.count()).select_from(m.ValmetUnit))==1 and len(p.active(db))==1
        assert not db.scalar(select(m.ValmetImportConfirmation.id))
    monkeypatch.setattr(v,'add_identification',old)
    def run(_):return confirm(engine,actor,rev,digest)
    with ThreadPoolExecutor(2) as pool:assert len(set(pool.map(run,range(2))))==1
    with scoped(engine,2) as db:assert db.scalar(select(func.count()).select_from(m.ValmetUnit))==9 and len(p.active(db))==1

def test_historical_import_permissions_runtime_isolation_csrf_and_books(restricted_web):
    from fastapi.testclient import TestClient
    from iberbag.warehouse_settings import add_location,set_capacity
    app,owner,runtime,role,a,tokens=restricted_web;actor=a['admin'][0]
    with scoped(owner,2) as db:
        for loc in ('A2','Chão'):add_location(db,'admin',loc)
        set_capacity(db,'admin','A2','20')
    with scoped(runtime,2) as db:
        with pytest.raises(v.ReceivingError):service.preview_file(db,a['operador'][0],secrets.token_hex(16),workbook())
    with TestClient(app) as client:
        assert client.get('/valmet/imports').status_code==403
        client.cookies.set('wms_session',tokens['admin'][0]);client.cookies.set('ddn_context','2')
        assert client.get('/valmet/imports').status_code==200
        def upload(csrf,scope):return client.post('/valmet/imports/upload',data=dict(csrf=csrf,context_id=scope,request_key=secrets.token_hex(16)),files={'excel':('fiction.xlsx',workbook())},follow_redirects=False)
        assert upload('bad',2).status_code==403
        assert upload(tokens['admin'][1],1).status_code==409
        response=upload(tokens['admin'][1],2);assert response.status_code==303,response.text
        ident=int(response.headers['location'].rsplit('/',1)[-1]);assert client.get(response.headers['location']).status_code==200
    with scoped(runtime,2) as db:
        rev=service.reconcile(db,actor,ident,secrets.token_hex(16),decisions(),'Prova fictícia runtime restrito');digest=db.get(m.ValmetImportRevision,rev).digest
    confirm(runtime,actor,rev,digest)
    with scoped(runtime,2) as db:entry_id=db.scalar(select(m.ValmetImportConfirmation.entry_id))
    with TestClient(app) as client:
        client.cookies.set('wms_session',tokens['operador'][0]);client.cookies.set('ddn_context','2')
        assert 'Importação histórica confirmada' in client.get(f'/valmet/entries/{entry_id}').text
        assert client.get(f'/valmet/imports/{ident}').status_code==403
    with scoped(runtime,2) as db:
        for table in ('valmet_import_drafts','valmet_import_revisions','valmet_import_confirmations'):
            with pytest.raises(DBAPIError):db.execute(text('DELETE FROM '+table))
            db.rollback()
        with pytest.raises(DBAPIError):db.execute(text("UPDATE valmet_entries SET payload='{}' WHERE status='imported'"))
        db.rollback()
    with scoped(runtime,1) as db:
        assert not db.scalar(select(m.ValmetUnit.id))
        with pytest.raises(v.ReceivingError):service.confirm(db,actor,rev,secrets.token_hex(16),digest,True)

def test_historical_import_backup_restore_and_altered_file_no_second_baseline(valmet,pg_databases,tmp_path):
    engine,a=valmet;actor=a['admin'][0]
    _,rev,digest,_=stage(engine,actor);confirm(engine,actor,rev,digest)
    with engine.connect() as conn:before=transfer.inventory(transfer.read_data(conn))
    folder=tmp_path/'historical';target=pg_databases(restricted=True)
    backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert backup.restore(target,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'
    with target.connect() as conn:assert transfer.inventory(transfer.read_data(conn))==before
    _,rev,digest,plan=stage(target,actor,workbook(lambda b:setattr(b['Caixas']['F2'],'value','alterado fictício')))
    assert plan['blocking']
    with pytest.raises(v.ReceivingError):confirm(target,actor,rev,digest)

def test_historical_import_multilot_unknown_presence_and_existing_identity_cannot_be_renamed(valmet):
    engine,a=valmet;actor=a['admin'][0]
    def change(b):
        b['Paletes'].append([None,'PALLET-OTHER',None,'A2','PALLET-1',None])
        b['Caixas com Peças'].append([None,'PC-OTHER',None,'PIECES',5,'A2'])
    _,rev,digest,plan=stage(engine,actor,workbook(change));assert not plan['blocking'],plan['issues']
    confirm(engine,actor,rev,digest)
    with scoped(engine,2) as db:
        u=db.scalar(select(m.ValmetUnit).where(m.ValmetUnit.number=='PALLET-1'))
        pos=db.scalar(select(m.ValmetPosition).where(m.ValmetPosition.unit_id==u.id));assert pos.physical_quantity==1
        assert json.loads(pos.materials)=={'PALLET':1,'PALLET-OTHER':1}
        assert db.scalar(select(m.ValmetUnit.content_total).where(m.ValmetUnit.number=='PIECES'))==17
    # A changed digest plus renaming an already-issued identity cannot hide overlap.
    content=workbook();choices=decisions();choices['rows']={'Caixas!2':{'numero':'RENAMED'}}
    _,_,_,plan=stage(engine,actor,content,choices)
    assert any(i['code']=='NUMERO_JA_EMITIDO' and 'Caixas!2' in i['refs'] for i in plan['issues'])


def test_historical_import_integrates_inventory_transfer_adjustment_and_archived_pdf(valmet):
    from iberbag import valmet_control as c,valmet_shipping as s
    engine,a=valmet;actor=a['admin'][0]
    _,rev,digest,_=stage(engine,actor);confirm(engine,actor,rev,digest)
    with scoped(engine,2) as db:
        unit=db.scalar(select(m.ValmetUnit).where(m.ValmetUnit.kind=='rolos'));pos=db.scalar(select(m.ValmetPosition).where(m.ValmetPosition.unit_id==unit.id))
        ids=[r['id'] for r in rolls.present(db,unit.id)]
        move=c.save_transfer(db,actor,secrets.token_hex(16),pos.id,'H2',1,'A','Palete inteira','Conferência física fictícia');c.confirm(db,actor,move,1)
        assert [r['id'] for r in rolls.present(db,unit.id)]==ids
        piece=db.scalar(select(m.ValmetPosition).join(m.ValmetUnit,m.ValmetUnit.id==m.ValmetPosition.unit_id).where(m.ValmetUnit.kind=='pecas'))
        adjust=c.save_adjustment(db,actor,secrets.token_hex(16),piece.id,'A2',1,{'PC':11},'Peça em falta','Conferência real fictícia da caixa indivisível',True);c.confirm(db,actor,adjust,1)
        inv=c.create_inventory(db,actor,secrets.token_hex(16),'partial')
        count=c.save_count(db,actor,inv,1,secrets.token_hex(16),f'P:{piece.id}','count',1,{'PC':11})
        assert db.get(m.ValmetCount,count).id==count and db.get(m.ValmetPosition,piece.id).physical_quantity==1
    order=draft(engine,actor,[('rolos','A','1')])
    with scoped(engine,2) as db:
        p.review(db,actor,order,1);p.confirm(db,actor,order,1);doc=s.issue_document(db,actor,order,1);before=bytes(db.get(m.ValmetDocument,doc).pdf)
    send(engine,actor,order)
    with scoped(engine,2) as db:assert bytes(db.get(m.ValmetDocument,doc).pdf)==before


def test_historical_import_ddn10_upgrade_preserves_existing_stock_reservations_and_pdf(pg_databases,tmp_path):
    from alembic import command
    from iberbag.migrations import config,REVISION
    from iberbag.schema_upgrade import apply_upgrade
    from iberbag.security import set_password
    from legacy_catalogue import seed_legacy_catalogue
    from iberbag.warehouse_settings import add_location
    from iberbag import valmet_shipping as s
    engine=pg_databases(restricted=True);target=pg_databases(restricted=True)
    with engine.begin() as conn:
        cfg=config();cfg.attributes['connection']=conn;command.upgrade(cfg,'ddn_0010')
    with scoped(engine) as db:
        seed_legacy_catalogue(db);user=m.User(username='admin',display_name='Fictício',role='admin',active=True);set_password(user,secrets.token_urlsafe(24));db.add(user);db.commit();actor=user.id
    with scoped(engine,2) as db:add_location(db,'admin','Chão')
    entry(engine,actor,[row('paletes',None,local='Chão',physical_quantity=2,content_total=None,items=[('ART','OLD',2)])])
    order=draft(engine,actor,[('paletes','OLD','1')])
    with scoped(engine,2) as db:p.review(db,actor,order,1);p.confirm(db,actor,order,1);s.issue_document(db,actor,order,1)
    with engine.connect() as conn:before=transfer.inventory(transfer.read_data(conn))
    folder=tmp_path/'ddn10';backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD);backup.restore(target,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert apply_upgrade(engine,folder)['revision']==REVISION=='ddn_0013'
    with engine.connect() as conn:assert transfer.inventory(transfer.read_data(conn))==before
    with scoped(engine,2) as db:assert len(p.active(db))==1 and not db.scalar(select(m.ValmetImportDraft.id))


def test_historical_upload_parser_bounds_and_sqlite_never_imports():
    import asyncio
    from starlette.requests import Request
    from fastapi import HTTPException
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from iberbag.valmet_import_upload import upload_form
    for body,typ in ((b'bad','multipart/form-data; boundary=no'),(b'','text/plain')):
        request=Request({'type':'http','method':'POST','path':'/valmet/imports/upload','headers':[(b'content-type',typ.encode())],'state':{'wms_body':body}})
        with pytest.raises(HTTPException):asyncio.run(upload_form(request))
    engine=create_engine('sqlite://')
    with Session(engine) as db:
        with pytest.raises(v.ReceivingError,match='PostgreSQL'):service.confirm(db,1,1,secrets.token_hex(16),'bad',True)
    with engine.connect() as conn:assert not engine.dialect.get_table_names(conn)
    engine.dispose()
    from iberbag.http_body import BoundedPostBody
    async def bounded(path,size,slow=False):
        reached=[];messages=[]
        async def app(scope,receive,send):
            reached.append(True)
            await send({'type':'http.response.start','status':200,'headers':[]});await send({'type':'http.response.body','body':b''})
        async def receive():
            if slow:await asyncio.sleep(1)
            return {'type':'http.request','body':b'x'*size,'more_body':False}
        async def send(message):messages.append(message)
        scope={'type':'http','method':'POST','path':path,'http_version':'1.1','headers':[(b'content-length',str(size).encode())]}
        await BoundedPostBody(app,timeout=.01)(scope,receive,send)
        return next(m['status'] for m in messages if m['type']=='http.response.start'),bool(reached)
    assert asyncio.run(bounded('/valmet/imports/upload',65537))==(200,True)
    assert asyncio.run(bounded('/valmet/entries/review',65537))==(413,False)
    assert asyncio.run(bounded('/valmet/imports/upload',20*1024*1024+16385))==(413,False)
    assert asyncio.run(bounded('/valmet/imports/upload',1,True))==(408,False)

@pytest.mark.parametrize('choices',[[],{'warnings':'all'},{'rows':[]},{'historical_numbers':'Paletes!2'},{'m2_totals':[]},{'rows':{'NO!2':{'fisico':1}}}])
def test_historical_invalid_decisions_never_write_stock(valmet,choices):
    engine,a=valmet;actor=a['admin'][0]
    with scoped(engine,2) as db:
        ident=service.preview_file(db,actor,secrets.token_hex(16),workbook())
        with pytest.raises(v.ReceivingError):service.reconcile(db,actor,ident,secrets.token_hex(16),choices,'Comprovação fictícia')
        assert not db.scalar(select(m.ValmetImportRevision.id)) and not db.scalar(select(m.ValmetUnit.id))


def test_historical_latest_revision_digest_and_capacity_change_are_revalidated(valmet):
    from iberbag.warehouse_settings import set_capacity
    engine,a=valmet;actor=a['admin'][0];ident,old,old_digest,plan=stage(engine,actor)
    with scoped(engine,2) as db:
        new=service.reconcile(db,actor,ident,secrets.token_hex(16),decisions(),'Nova revisão comprovada');new_digest=db.get(m.ValmetImportRevision,new).digest
    with pytest.raises(v.ReceivingError,match='desatualizada'):confirm(engine,actor,old,old_digest)
    with pytest.raises(v.ReceivingError,match='digest'):confirm(engine,actor,new,'0'*64)
    with scoped(engine,2) as db:set_capacity(db,'admin','A2','11')
    with pytest.raises(v.ReceivingError,match='mudaram'):confirm(engine,actor,new,new_digest)
    with scoped(engine,2) as db:assert not db.scalar(select(m.ValmetUnit.id))
