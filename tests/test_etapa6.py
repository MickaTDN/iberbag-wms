import json
import re
import secrets
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from sqlalchemy import select, func, text, event
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from fastapi.testclient import TestClient
from pypdf import PdfReader
from iberbag import dispatching as prep, inventorying as inv, receiving, transferring, backup, transfer
from iberbag.database import (Stock, Carga, User, DispatchOrder, DispatchDocument, DispatchPick, PhysicalInventory,
    InventoryScope, InventoryCount, StockAdjustment, StockIncident, PreparationCompensation, Location, Base)
from test_receiving import warehouse
from test_transfer import pg_databases, docker_client
from test_dispatching import add_stock, order as legacy_order, review as legacy_review


def prepare(engine,actor,rows=None):
    with Session(engine) as db:
        ident=prep.save_preparation(db,actor,secrets.token_hex(16),'ENSAIO ESCRITÓRIO',rows or [('L','1')])
        prep.review_preparation(db,actor,ident,1)
        return ident


def confirm(engine,actor,ident):
    with Session(engine) as db:return prep.confirm_preparation(db,actor,ident)


def inventory(engine,actor,locations=('Chão',),kind='partial'):
    with Session(engine) as db:
        ident=inv.create_inventory(db,actor,secrets.token_hex(16),'CONTAGEM FICTÍCIA',kind,locations)
        scopes=inv.scopes(db,ident)
        return ident,{s.local:s.id for s in scopes}


def count(engine,actor,scope,rows=(),empty=False):
    with Session(engine) as db:
        item=db.get(InventoryScope,scope)
        return inv.save_count(db,actor,scope,item.version,rows,'Contagem fictícia observada',empty)


def submit(engine,actor,ident):
    with Session(engine) as db:return inv.submit_inventory(db,actor,ident)


def approve(engine,actor,ident,compensate=None):
    with Session(engine) as db:return inv.approve_inventory(db,actor,ident,'Recontagem física comprovada',compensate)


def test_lot_only_immediate_debit_and_immutable_pdf(warehouse):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    old=add_stock(engine,'OLD','2020-01-01',2)
    new=add_stock(engine,'NEW','2021-01-01',3)
    add_stock(engine,'LOT2',qty=2,lote='L2')
    ident=prepare(engine,actor,[('L','3'),('L2','1')])
    with Session(engine) as db:
        assert db.get(Stock,old).quantidade==2
        assert prep.availability(db,'A','L')['reserved']==0
    confirm(engine,actor,ident);confirm(engine,actor,ident)
    with Session(engine) as db:
        assert db.get(Stock,old).quantidade==0 and db.get(Stock,new).quantidade==2
        doc=db.get(DispatchDocument,ident); first=doc.pdf
        extracted='\n'.join(page.extract_text() for page in PdfReader(BytesIO(first)).pages)
        assert 'OLD' in extracted and 'NEW' in extracted and 'L2' in extracted and 'Total: 4 paletes' in extracted
        assert db.get(DispatchOrder,ident).status=='confirmed'
    add_stock(engine,'AFTER','2010-01-01',1,lote='AFTER')
    with Session(engine) as db: assert db.get(DispatchDocument,ident).pdf==first
    with engine.begin() as conn,pytest.raises(IntegrityError):conn.execute(text('DELETE FROM dispatch_documents'))


@pytest.mark.parametrize('case',['missing','contradictory','unknown_date','insufficient','zero','fraction'])
def test_preparation_errors_do_not_debit(warehouse,case):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    add_stock(engine,'ONE',None if case=='unknown_date' else '2020-01-01')
    if case=='contradictory':add_stock(engine,'OTHER',artigo='B')
    rows=[('X' if case=='missing' else 'L','2' if case=='insufficient' else '0' if case=='zero' else '1.5' if case=='fraction' else '1')]
    with pytest.raises(receiving.ReceivingError):prepare(engine,actor,rows)
    with Session(engine) as db:assert db.scalar(select(func.sum(Stock.quantidade)))==(2 if case=='contradictory' else 1)


def test_lot_unique_new_entries_and_imports(warehouse):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    add_stock(engine,'ONE')
    with Session(engine) as db,pytest.raises(receiving.ReceivingError):
        receiving.save_receipt(db,actor,secrets.token_hex(16),'BAD','2026-01-01',[('B','L','1')])
    from iberbag.importer import Scan,import_stock
    report=Scan(); report.groups[('X','SAME','C','Chão')]=1;report.groups[('Y','SAME','C','Chão')]=1
    with Session(engine) as db,pytest.raises(ValueError):import_stock(db,report)


def test_concurrent_preparations_only_one_debits(warehouse):
    engine,accounts=warehouse; a,b=accounts['admin'][0],accounts['operador'][0]
    add_stock(engine,'ONE'); first=prepare(engine,a);second=prepare(engine,b)
    barrier=Barrier(2)
    def run(actor,ident):
        barrier.wait(timeout=5)
        try:confirm(engine,actor,ident);return True
        except receiving.ReceivingError:return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run,actor,ident) for actor,ident in [(a,first),(b,second)]]
        assert sum(f.result(timeout=20) for f in futures)==1
    with Session(engine) as db:assert db.scalar(select(func.sum(Stock.quantidade)))==0


def test_cancel_legacy_releases_reservations_without_debit(warehouse):
    engine,accounts=warehouse; admin=accounts['admin'][0];operator=accounts['operador'][0]
    stock=add_stock(engine,'ONE'); old=legacy_order(engine,operator);legacy_review(engine,operator,old)
    with Session(engine) as db:
        with pytest.raises(receiving.ReceivingError):prep.cancel_order(db,operator,old,'Cancelamento',True)
        with pytest.raises(receiving.ReceivingError):prep.cancel_order(db,admin,old,'Cancelamento',False)
        prep.cancel_order(db,admin,old,'Recolha devolvida e verificada',True)
        prep.cancel_order(db,admin,old,'Repetição',True)
        assert prep.availability(db,'A','L')['reserved']==0
        assert db.get(Stock,stock).quantidade==1
    new=prepare(engine,admin);confirm(engine,admin,new)
    with Session(engine) as db,pytest.raises(receiving.ReceivingError):prep.cancel_order(db,admin,new,'Não apagar',True)


def test_after_print_incident_never_automatically_changes_stock(warehouse):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    stock=add_stock(engine,'ONE'); ident=prepare(engine,actor);confirm(engine,actor,ident)
    with Session(engine) as db:
        pick=prep.picks(db,ident)[0];key=secrets.token_hex(16)
        case=inv.report_shortage(db,actor,key,pick.id,'1','Falta comunicada pelo armazém')
        assert inv.report_shortage(db,actor,key,pick.id,'1','Falta comunicada pelo armazém')==case
        assert db.get(Stock,stock).quantidade==0
        with pytest.raises(receiving.ReceivingError):inv.close_preparation(db,actor,ident,'Não concluir com falta')
        inv.resolve_incident(db,actor,case,'Verificado: não repor paletes inexistentes')
        assert db.get(Stock,stock).quantidade==0
    with engine.begin() as conn,pytest.raises(IntegrityError):conn.execute(text('DELETE FROM stock_incidents'))


def test_partial_empty_vs_uncounted_and_operator_permissions(warehouse):
    engine,accounts=warehouse; admin=accounts['admin'][0];op=accounts['operador'][0]
    floor=add_stock(engine,'ONE',qty=3);outside=add_stock(engine,'OUTSIDE',local='A101',lote='OTHER')
    ident,scopes=inventory(engine,op)
    with Session(engine) as db:
        assert inv.differences(db,ident)[0]['after'] is None
    with pytest.raises(receiving.ReceivingError):submit(engine,op,ident)
    count(engine,op,scopes['Chão'],empty=True);submit(engine,op,ident)
    with Session(engine) as db: assert db.get(Stock,floor).quantidade==3
    with pytest.raises(receiving.ReceivingError) as error:approve(engine,op,ident)
    assert error.value.status==403
    approve(engine,admin,ident);approve(engine,admin,ident)
    with Session(engine) as db:
        assert db.get(Stock,floor).quantidade==0 and db.get(Stock,outside).quantidade==1
        adjustment=db.scalar(select(StockAdjustment))
        assert (adjustment.before,adjustment.after,adjustment.delta)==(3,0,-3)
    with engine.begin() as conn,pytest.raises(IntegrityError):conn.execute(text('DELETE FROM stock_adjustments'))


def test_complete_inventory_all_mandatory_locations(warehouse):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    add_stock(engine,'ANOMALY',local='FORA',lote='FORA')
    ident,scopes=inventory(engine,actor,kind='full')
    assert len(scopes)==2108 and 'Chão' in scopes and 'FORA' in scopes
    with pytest.raises(receiving.ReceivingError,match='Falta contar'):submit(engine,actor,ident)
    # Synthetic empty warehouse, all ordinary positions independently declared empty by fixture.
    with engine.begin() as conn:conn.execute(text('UPDATE inventory_scopes SET counted=true,version=1 WHERE inventory_id=:id AND local<>:loc'),{'id':ident,'loc':'FORA'})
    with pytest.raises(receiving.ReceivingError,match='FORA'):submit(engine,actor,ident)
    count(engine,actor,scopes['FORA'],empty=True);submit(engine,actor,ident)
    approve(engine,actor,ident)


def test_positive_adjustment_and_unknown_pallet_pending(warehouse):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    stock=add_stock(engine,'ONE')
    ident,scopes=inventory(engine,actor)
    count(engine,actor,scopes['Chão'],[('A','L','ONE','3')]);submit(engine,actor,ident);approve(engine,actor,ident)
    with Session(engine) as db:assert db.get(Stock,stock).quantidade==3
    unknown,scopes=inventory(engine,actor)
    count(engine,actor,scopes['Chão'],[('','','','1')]);submit(engine,actor,unknown)
    with pytest.raises(receiving.ReceivingError,match='pendente'):approve(engine,actor,unknown)
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(StockIncident))==1
        assert db.get(Stock,stock).quantidade==3


def test_positive_without_real_date_is_blocked(warehouse):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    stock=add_stock(engine,'UNKNOWN',None)
    ident,scopes=inventory(engine,actor);count(engine,actor,scopes['Chão'],[('A','L','UNKNOWN','2')]);submit(engine,actor,ident)
    with pytest.raises(receiving.ReceivingError,match='data conhecida'):approve(engine,actor,ident)
    with Session(engine) as db:assert db.get(Stock,stock).quantidade==1


def test_inventory_capacity(warehouse):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    add_stock(engine,'ONE',local='A101')
    ident,scopes=inventory(engine,actor,['A101']);count(engine,actor,scopes['A101'],[('A','L','ONE','2')]);submit(engine,actor,ident)
    with pytest.raises(receiving.ReceivingError,match='Capacidade'):approve(engine,actor,ident)


def test_movements_aba_and_simultaneous_inventories(warehouse):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    stock=add_stock(engine,'ONE',qty=2)
    stale,ss=inventory(engine,actor);count(engine,actor,ss['Chão'],[('A','L','ONE','1')]);submit(engine,actor,stale)
    with Session(engine) as db:
        move=transferring.review_transfer(db,actor,secrets.token_hex(16),stock,'A101','1');transferring.confirm_transfer(db,actor,move)
        target=db.scalar(select(Stock.id).where(Stock.local=='A101'))
        back=transferring.review_transfer(db,actor,secrets.token_hex(16),target,'Chão','1');transferring.confirm_transfer(db,actor,back)
    with pytest.raises(receiving.ReceivingError,match='movimentos'):approve(engine,actor,stale)
    first,fs=inventory(engine,actor);second,ss=inventory(engine,actor)
    for ident,scope in [(first,fs['Chão']),(second,ss['Chão'])]:
        count(engine,actor,scope,[('A','L','ONE','1')]);submit(engine,actor,ident)
    approve(engine,actor,first)
    with pytest.raises(receiving.ReceivingError,match='movimentos'):approve(engine,actor,second)


def test_adjustment_against_reservation_and_commit_rollback(warehouse):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    stock=add_stock(engine,'ONE',qty=2)
    ident,scopes=inventory(engine,actor);count(engine,actor,scopes['Chão'],[('A','L','ONE','1')]);submit(engine,actor,ident)
    old=legacy_order(engine,actor);legacy_review(engine,actor,old)
    with pytest.raises(receiving.ReceivingError,match='reserva'):approve(engine,actor,ident)
    with Session(engine) as db:prep.cancel_order(db,actor,old,'Nunca recolhido',True)
    with pytest.raises(receiving.ReceivingError,match='movimentos'):approve(engine,actor,ident)
    ident,scopes=inventory(engine,actor);count(engine,actor,scopes['Chão'],[('A','L','ONE','1')]);submit(engine,actor,ident)
    with Session(engine) as db:
        def fail(session):raise RuntimeError('Injected failure')
        event.listen(db,'before_commit',fail)
        with pytest.raises(RuntimeError):inv.approve_inventory(db,actor,ident,'Comprovado')
    with Session(engine) as db:
        assert db.get(Stock,stock).quantidade==2
        assert db.get(PhysicalInventory,ident).status=='submitted'
        assert db.scalar(select(func.count()).select_from(StockAdjustment))==0
    approve(engine,actor,ident)


def test_printed_pallets_not_duplicated_and_completion_invalidates_counts(warehouse):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    stock=add_stock(engine,'ONE');prep_id=prepare(engine,actor);confirm(engine,actor,prep_id)
    ident,scopes=inventory(engine,actor);count(engine,actor,scopes['Chão'],[('A','L','ONE','1')]);submit(engine,actor,ident)
    with pytest.raises(receiving.ReceivingError,match='impressa'):approve(engine,actor,ident)
    with Session(engine) as db:inv.close_preparation(db,actor,prep_id,'Execução física verificada')
    with pytest.raises(receiving.ReceivingError,match='movimentos'):approve(engine,actor,ident)
    with Session(engine) as db:assert db.get(Stock,stock).quantidade==0


def test_safe_compensation_requires_exact_physical_restitution(warehouse):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    stock=add_stock(engine,'ONE');prep_id=prepare(engine,actor);confirm(engine,actor,prep_id)
    ident,scopes=inventory(engine,actor);count(engine,actor,scopes['Chão'],[('A','L','ONE','1')]);submit(engine,actor,ident)
    approve(engine,actor,ident,prep_id);approve(engine,actor,ident,prep_id)
    with Session(engine) as db:
        assert db.get(Stock,stock).quantidade==1
        assert db.get(DispatchOrder,prep_id).status=='confirmed'
        assert db.get(PreparationCompensation,prep_id).inventory_id==ident
        assert db.get(DispatchDocument,prep_id).pdf.startswith(b'%PDF')


def test_compensation_after_later_movement_is_blocked(warehouse):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    stock=add_stock(engine,'ONE',qty=2);prep_id=prepare(engine,actor);confirm(engine,actor,prep_id)
    with Session(engine) as db:
        move=transferring.review_transfer(db,actor,secrets.token_hex(16),stock,'A101','1');transferring.confirm_transfer(db,actor,move)
    ident,scopes=inventory(engine,actor);count(engine,actor,scopes['Chão'],[('A','L','ONE','1')]);submit(engine,actor,ident)
    with pytest.raises(receiving.ReceivingError,match='posteriores'):approve(engine,actor,ident,prep_id)


def test_lot_adjudication_preserves_historical_conflicts(warehouse):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    add_stock(engine,'ONE');add_stock(engine,'OTHER',qty=0,artigo='B')
    with pytest.raises(receiving.ReceivingError,match='contraditórias'):prepare(engine,actor)
    with Session(engine) as db:inv.resolve_lot(db,actor,'L','A','Documentação histórica verificada')
    ident=prepare(engine,actor);confirm(engine,actor,ident)
    with Session(engine) as db:assert db.scalar(select(func.count()).select_from(Stock).where(Stock.artigo=='B'))==1


def test_inventory_ui_and_office_pdf_without_operator_confirmation(warehouse):
    engine,accounts=warehouse
    from iberbag.app import app,database
    add_stock(engine,'ONE')
    def session():
        with Session(engine,expire_on_commit=False) as db:yield db
    def field(response,name):return re.search(r'name="'+name+r'" value="([^"]+)"',response.text).group(1)
    app.dependency_overrides[database]=session
    try:
        with TestClient(app) as client:
            client.post('/login',data={'username':'admin','password':accounts['admin'][1]})
            form=client.get('/dispatches/new');csrf=field(form,'csrf')
            assert 'name="artigo_0"' not in form.text
            saved=client.post('/dispatches/save',data=dict(csrf=csrf,request_key=field(form,'request_key'),customer='CLIENTE FICTÍCIO',channel='normal',lote_0='L',quantidade_0='1'))
            url=str(saved.url).split('testserver')[1]
            client.post(url+'/review',data={'csrf':csrf,'version':'1'})
            assert client.post(url+'/check',data={'csrf':csrf}).status_code==409
            assert 'stock descontado' in client.post(url+'/confirm',data={'csrf':csrf}).text
            first=client.get(url+'/document.pdf');assert first.headers['content-type']=='application/pdf'
            assert first.content==client.get(url+'/document.pdf').content
            form=client.get('/inventory')
            created=client.post('/inventory/create',data=dict(csrf=csrf,request_key=field(form,'request_key'),reference='UI INV',kind='partial',locations='Chão'))
            assert 'ainda não contadas' in created.text
            assert client.post('/inventory/create',data=dict(request_key=secrets.token_hex(16),reference='BAD',kind='partial',locations='Chão')).status_code==403
            assert client.get('/inventory/incidents').status_code==200
    finally:app.dependency_overrides.clear()


def test_backup_restore_includes_documents_inventory_and_incidents(warehouse,pg_databases,tmp_path):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    add_stock(engine,'ONE',qty=2);prep_id=prepare(engine,actor);confirm(engine,actor,prep_id)
    ident,scopes=inventory(engine,actor);count(engine,actor,scopes['Chão'],empty=True);submit(engine,actor,ident);approve(engine,actor,ident)
    with Session(engine) as db:
        pick=prep.picks(db,prep_id)[0]
        inv.report_shortage(db,actor,secrets.token_hex(16),pick.id,'1','Falta fictícia')
    folder=tmp_path/'backup';backup.backup(engine,folder,runner=docker_client)
    restored=pg_databases(restricted=True)
    assert backup.restore(restored,folder,runner=docker_client)['status']=='reconciled'
    with Session(engine) as original,Session(restored) as copy:
        assert original.get(DispatchDocument,prep_id).pdf==copy.get(DispatchDocument,prep_id).pdf
        assert copy.get(PhysicalInventory,ident).status=='applied'
        assert copy.scalar(select(func.count()).select_from(StockIncident))==1


def test_reconcile_contradictory_positive_history_without_rewriting_it(warehouse):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    add_stock(engine,'RIGHT');wrong=add_stock(engine,'WRONG',artigo='B')
    with Session(engine) as db:inv.resolve_lot(db,actor,'L','A','Artigo comprovado por documentação')
    with pytest.raises(receiving.ReceivingError):prepare(engine,actor)
    ident,scopes=inventory(engine,actor);count(engine,actor,scopes['Chão'],[('A','L','RIGHT','1')]);submit(engine,actor,ident);approve(engine,actor,ident)
    with Session(engine) as db:assert db.get(Stock,wrong).quantidade==0 and db.get(Stock,wrong).artigo=='B'
    preparation=prepare(engine,actor);confirm(engine,actor,preparation)


def test_unexpected_identified_group_and_submitted_count_immutable(warehouse):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    add_stock(engine,'KNOWN',qty=0)
    ident,scopes=inventory(engine,actor,['A101'])
    count(engine,actor,scopes['A101'],[('NEW-ARTICLE','NEW-LOT','KNOWN','1')]);submit(engine,actor,ident)
    with engine.begin() as conn,pytest.raises(IntegrityError):conn.execute(text('UPDATE inventory_counts SET quantidade=2'))
    approve(engine,actor,ident)
    with Session(engine) as db:assert db.scalar(select(Stock.quantidade).where(Stock.artigo=='NEW-ARTICLE'))==1


@pytest.mark.parametrize('same',[True,False])
def test_simultaneous_adjustments_or_repeat_approval(warehouse,same):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    stock=add_stock(engine,'ONE')
    first,fs=inventory(engine,actor);count(engine,actor,fs['Chão'],empty=True);submit(engine,actor,first)
    if same:second=first
    else:
        second,ss=inventory(engine,actor);count(engine,actor,ss['Chão'],empty=True);submit(engine,actor,second)
    barrier=Barrier(2)
    def run(ident):
        barrier.wait(timeout=5)
        try:approve(engine,actor,ident);return True
        except receiving.ReceivingError:return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run,ident) for ident in (first,second)]
        assert sum(f.result(timeout=20) for f in futures)==(2 if same else 1)
    with Session(engine) as db:
        assert db.get(Stock,stock).quantidade==0
        assert db.scalar(select(func.count()).select_from(StockAdjustment))==1


def test_legacy_confirmed_document_archival_does_not_debit_again(warehouse):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    stock=add_stock(engine,'ONE');ident=legacy_order(engine,actor);legacy_review(engine,actor,ident)
    with Session(engine) as db:
        values={p.id:str(p.quantidade) for p in prep.picks(db,ident)}
        prep.check_order(db,actor,ident,values);prep.confirm_order(db,actor,ident)
        prep.archive_legacy_document(db,actor,ident);prep.archive_legacy_document(db,actor,ident)
        assert db.get(Stock,stock).quantidade==0
        assert db.get(DispatchDocument,ident).stock_versions=='{}'


def test_pdf_failure_rolls_back_immediate_debit_and_clock(warehouse,monkeypatch):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    stock=add_stock(engine,'ONE');ident=prepare(engine,actor)
    with Session(engine) as db:before=inv.clock(db,'Chão')
    def fail(*args):raise RuntimeError('PDF failure')
    monkeypatch.setattr('iberbag.preparation_pdf.render_pdf',fail)
    with pytest.raises(RuntimeError):confirm(engine,actor,ident)
    with Session(engine) as db:
        assert db.get(Stock,stock).quantidade==1
        assert db.get(DispatchOrder,ident).status=='draft'
        assert db.get(DispatchDocument,ident) is None
        assert inv.clock(db,'Chão')==before


def test_compensation_cannot_overfill_original_position(warehouse):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    add_stock(engine,'ONE',local='A101');ident=prepare(engine,actor);confirm(engine,actor,ident)
    counted,scopes=inventory(engine,actor,['A101'])
    count(engine,actor,scopes['A101'],[('A','L','ONE','2')]);submit(engine,actor,counted)
    with pytest.raises(receiving.ReceivingError,match='Capacidade'):approve(engine,actor,counted,ident)
    with Session(engine) as db:assert db.scalar(select(func.sum(Stock.quantidade)))==0


def test_draft_lot_typo_can_be_corrected_but_confirmed_identity_is_binding(warehouse):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    with Session(engine) as db:
        key=secrets.token_hex(16)
        ident=receiving.save_receipt(db,actor,key,'LOAD','2026-01-01',[('TYPO','NEWLOT','1')])
        receiving.save_receipt(db,actor,key,'LOAD','2026-01-01',[('CORRECT','NEWLOT','1')],ident,1)
        receiving.confirm_receipt(db,actor,ident,2)
        with pytest.raises(receiving.ReceivingError):
            receiving.save_receipt(db,actor,secrets.token_hex(16),'BAD','2026-01-01',[('TYPO','NEWLOT','1')])


def test_positive_inventory_does_not_duplicate_pending_receipt(warehouse):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    with Session(engine) as db:
        receipt=receiving.save_receipt(db,actor,secrets.token_hex(16),'REAL-RECEIPT','2026-01-01',[('A','NEW-L','1')])
        receiving.confirm_receipt(db,actor,receipt,1)
    ident,scopes=inventory(engine,actor)
    count(engine,actor,scopes['Chão'],[('A','NEW-L','REAL-RECEIPT','1')]);submit(engine,actor,ident)
    with pytest.raises(receiving.ReceivingError,match='por arrumar'):approve(engine,actor,ident)
    with Session(engine) as db:assert db.scalar(select(func.count()).select_from(Stock))==0


def test_upgrade_0004_keeps_legacy_confirmed_records(warehouse,pg_databases,tmp_path):
    from sqlalchemy import Table,MetaData
    from alembic import command
    from datetime import datetime
    from iberbag.migrations import config, REVISION
    from iberbag.security import set_password
    from iberbag.schema_upgrade import preview,apply_upgrade
    from legacy_catalogue import seed_legacy_catalogue
    legacy,restored=pg_databases(restricted=True),pg_databases(restricted=True)
    with legacy.begin() as conn:
        cfg=config();cfg.attributes['connection']=conn;command.upgrade(cfg,'0004')
    stamp=datetime(2020,1,1)
    with Session(legacy) as db:
        seed_legacy_catalogue(db)
        user=User(username='legado',display_name='Fictício',role='admin',active=True)
        set_password(user,secrets.token_urlsafe(24));db.add(user);db.flush();actor=user.id
        def insert(name,values):
            table=Table(name,MetaData(),autoload_with=db.connection())
            return db.execute(table.insert().values(**values).returning(table.c.id)).scalar_one()
        insert('cargas',dict(codigo='LEGADO',entrada=stamp,funcionario=None))
        from types import SimpleNamespace
        a=SimpleNamespace(id=insert('stock',dict(artigo='A',lote='L',carga='LEGADO',local='Chão',quantidade=0)))
        b=SimpleNamespace(id=insert('stock',dict(artigo='A',lote='L',carga='LEGADO',local='A101',quantidade=0)))
        insert('stock_transfers',dict(request_key=secrets.token_hex(16),source_stock_id=a.id,destination_stock_id=b.id,
            artigo='A',lote='L',carga='LEGADO',entrada=stamp,source_local='Chão',destination_local='A101',quantidade=1,
            created_by=actor,created_at=stamp,status='confirmed',confirmed_by='legado',confirmed_at=stamp))
        order_id=insert('dispatch_orders',dict(request_key=secrets.token_hex(16),payload_hash=secrets.token_hex(32),version=1,
            reference='SAÍDA LEGADA FICTÍCIA',created_by=actor,created_at=stamp,status='draft'))
        line_id=insert('dispatch_lines',dict(order_id=order_id,artigo='A',lote='L',quantidade=1))
        insert('dispatch_picks',dict(line_id=line_id,stock_id=b.id,quantidade=1,actual=1,carga='LEGADO',local='A101',entrada=stamp))
        db.execute(text("UPDATE dispatch_orders SET status='confirmed',reviewed_at=:day,checked_by='legado',checked_at=:day,confirmed_by='legado',confirmed_at=:day WHERE id=:id"),dict(day=stamp,id=order_id));db.commit()
    folder=tmp_path/'legacy';backup.backup(legacy,folder,runner=docker_client);backup.restore(restored,folder,runner=docker_client)
    assert preview(legacy)['from']=='0004'
    assert apply_upgrade(legacy,folder)['revision']==REVISION
    with Session(legacy) as db:
        assert db.get(DispatchOrder,order_id).status=='confirmed' and not db.get(DispatchOrder,order_id).office
        prep.archive_legacy_document(db,actor,order_id)
        assert db.get(DispatchDocument,order_id).pdf.startswith(b'%PDF')
        assert db.scalar(select(func.sum(Stock.quantidade)))==0


def test_unknown_identity_does_not_mean_old_groups_are_zero(warehouse):
    engine,accounts=warehouse;actor=accounts['admin'][0]
    add_stock(engine,'ONE')
    ident,scopes=inventory(engine,actor)
    count(engine,actor,scopes['Chão'],[('','','','1')])
    with Session(engine) as db:
        difference=inv.differences(db,ident)[0]
        assert difference['after'] is None and difference['delta'] is None


def test_admin_can_consult_pending_preparation_without_impersonating_owner(warehouse):
    engine,accounts=warehouse
    from iberbag.app import app,database
    add_stock(engine,'ONE');ident=prepare(engine,accounts['operador'][0])
    def session():
        with Session(engine,expire_on_commit=False) as db:yield db
    app.dependency_overrides[database]=session
    try:
        with TestClient(app) as admin,TestClient(app) as other:
            for c,role in ((admin,'admin'),(other,'other')):
                c.post('/login',data={'username':role,'password':accounts[role][1]})
            assert admin.get(f'/dispatches/{ident}').status_code==200
            assert other.get(f'/dispatches/{ident}').status_code==404
    finally:app.dependency_overrides.clear()


def test_compensation_refuses_changed_load_date(warehouse):
    from datetime import datetime
    engine,accounts=warehouse;actor=accounts['admin'][0]
    add_stock(engine,'ONE');ident=prepare(engine,actor);confirm(engine,actor,ident)
    with Session(engine) as db:
        db.scalar(select(Carga)).entrada=datetime(2021,1,1);db.commit()
    counted,scopes=inventory(engine,actor);count(engine,actor,scopes['Chão'],[('A','L','ONE','1')]);submit(engine,actor,counted)
    with pytest.raises(receiving.ReceivingError,match='Data/carga'):approve(engine,actor,counted,ident)
