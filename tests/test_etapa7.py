from conftest import BACKUP_PASSWORD
from conftest import login_data
"""Etapa 7: exclusively synthetic PostgreSQL warehouse/accounting scenarios."""
import json
import re
import secrets
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from pypdf import PdfReader
from sqlalchemy import select,func,text
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from fastapi.testclient import TestClient
from alembic import command
from iberbag import commercial as biz,dispatching as dispatch,receiving as receipt,inventorying as inv,backup,transfer
from iberbag.database import (Stock,Carga,DispatchOrder,DispatchLine,DispatchDocument,Receipt,ReceiptLine,ReceiptDocument,
    CustomerPending,PendingAllocation,AllocationCollection,CommercialPreparation,MercadonaDestination,IncompleteComposition,
    Audit,LocationClock,User)
from iberbag.migrations import config,REVISION
from test_receiving import warehouse
from test_dispatching import add_stock,order as legacy_order,review as legacy_review
from test_transfer import pg_databases,docker_client
from test_etapa6 import inventory,count,submit,approve


def preparation(engine,actor,qty=10,customer='CLIENTE FICTÍCIO',rows=None,review=True,channel='normal'):
    with Session(engine) as db:
        ident=biz.save_preparation(db,actor,secrets.token_hex(16),'ENCOMENDA FICTÍCIA',customer,channel,rows or [('L',str(qty),'')])
        if review:dispatch.review_preparation(db,actor,ident,1)
        return ident


def confirm(engine,actor,ident):
    with Session(engine) as db:return dispatch.confirm_preparation(db,actor,ident)


def incoming(engine,actor,qty,code=None,do_confirm=True):
    with Session(engine) as db:
        ident=receipt.save_receipt(db,actor,secrets.token_hex(16),code or secrets.token_hex(8),'2026-01-10',[('A','L',str(qty))])
        if do_confirm:receipt.confirm_receipt(db,actor,ident,1)
        line=db.scalar(select(ReceiptLine.id).where(ReceiptLine.receipt_id==ident))
        return ident,line


def shortage(engine,actor,qty=4,customer='CLIENTE FICTÍCIO'):
    ident=preparation(engine,actor,qty,customer);confirm(engine,actor,ident)
    with Session(engine) as db:return ident,db.scalar(select(CustomerPending).join(DispatchLine).where(DispatchLine.order_id==ident)).id


def pdf_text(data):return '\n'.join(p.extract_text() for p in PdfReader(BytesIO(data)).pages)


def test_sufficient_and_partial_immediate_debit_idempotent_and_pdf(warehouse):
    engine,accounts=warehouse;actor=accounts['admin'][0]
    old=add_stock(engine,'OLD','2020-01-01',2);new=add_stock(engine,'NEW','2021-01-01',4)
    ident=preparation(engine,actor,10)
    with Session(engine) as db:
        assert [p.quantidade for p in dispatch.picks(db,ident)]==[2,4]
        assert db.get(Stock,old).quantidade==2 and db.scalar(select(func.count()).select_from(CustomerPending))==0
    confirm(engine,actor,ident);confirm(engine,actor,ident)
    with Session(engine) as db:
        assert db.get(Stock,old).quantidade==db.get(Stock,new).quantidade==0
        pending=db.scalar(select(CustomerPending));assert pending.quantidade==4 and biz.satisfied(db,pending.id)==0
        first=db.get(DispatchDocument,ident).pdf
        content=pdf_text(first)
        assert 'Total pedido: 10' in content and 'Total preparado: 6' in content and 'Total pendente: 4' in content
        assert 'CLIENTE FICTÍCIO' in content and 'OLD' in content and 'NEW' in content
    add_stock(engine,'AFTER','2022-01-01',20)
    with Session(engine) as db:assert db.get(DispatchDocument,ident).pdf==first
    second=preparation(engine,actor,3);confirm(engine,actor,second)
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(CustomerPending))==1
        assert sum(p.quantidade for p in dispatch.picks(db,second))==3
        assert db.scalar(select(func.sum(Stock.quantidade)))==17


def test_all_missing_known_lot_can_confirm_without_pick(warehouse):
    engine,accounts=warehouse;actor=accounts['admin'][0]
    add_stock(engine,'KNOWN',qty=0)
    ident,pending=shortage(engine,actor)
    with Session(engine) as db:
        assert dispatch.picks(db,ident)==[] and db.get(CustomerPending,pending).quantidade==4
        assert db.get(DispatchOrder,ident).status=='confirmed'
        assert 'PENDENTE' in pdf_text(db.get(DispatchDocument,ident).pdf)


@pytest.mark.parametrize('case',['unknown_date','untraceable_location','contradictory','unknown_lot','invalid_qty'])
def test_shortage_cannot_bypass_traceability(warehouse,case):
    engine,accounts=warehouse;actor=accounts['admin'][0]
    add_stock(engine,'KNOWN',None if case=='unknown_date' else '2020-01-01',1,local='FORA' if case=='untraceable_location' else 'Chão')
    if case=='contradictory':add_stock(engine,'WRONG',artigo='B')
    with pytest.raises(receipt.ReceivingError):
        preparation(engine,actor,rows=[('MISSING' if case=='unknown_lot' else 'L','1.5' if case=='invalid_qty' else '10','')])
    with Session(engine) as db:assert db.scalar(select(func.count()).select_from(CustomerPending))==0


def test_pending_oldest_first_partial_full_and_normal_excess(warehouse):
    engine,accounts=warehouse;actor=accounts['admin'][0];add_stock(engine,'KNOWN',qty=0)
    o1,p1=shortage(engine,actor,4,'CLIENTE PRIMEIRO');o2,p2=shortage(engine,actor,3,'CLIENTE SEGUNDO')
    first,line1=incoming(engine,actor,2)
    with Session(engine) as db:
        assert biz.satisfied(db,p1)==2 and biz.satisfied(db,p2)==0 and receipt.remaining(db,db.get(ReceiptLine,line1))==0
        receipt.confirm_receipt(db,actor,first,1)
        assert biz.satisfied(db,p1)==2
    second,line2=incoming(engine,actor,8)
    with Session(engine) as db:
        assert biz.satisfied(db,p1)==4 and biz.satisfied(db,p2)==3
        assert receipt.remaining(db,db.get(ReceiptLine,line2))==3
        assert db.scalar(select(func.sum(Stock.quantidade)))==0
        rows=biz.allocations(db,second);assert [r[0].quantidade for r in rows]==[2,3]
        original=db.get(ReceiptDocument,second).pdf;content=pdf_text(original)
        assert 'CLIENTE PRIMEIRO' in content and 'CLIENTE SEGUNDO' in content and 'Stock normal' in content and 'Total da carga: 8' in content
        putaway=receipt.review_putaway(db,actor,secrets.token_hex(16),line2,'Chão','3');receipt.confirm_putaway(db,actor,putaway)
        assert receipt.remaining(db,db.get(ReceiptLine,line2))==0
        assert db.scalar(select(func.sum(Stock.quantidade)))==3
        with pytest.raises(receipt.ReceivingError):receipt.review_putaway(db,actor,secrets.token_hex(16),line2,'A101','1')
        assert db.get(ReceiptDocument,second).pdf==original
        for allocation,*_ in biz.allocations(db):
            biz.record_collection(db,actor,allocation.id,'Recolha integral fictícia comprovada')
            biz.record_collection(db,actor,allocation.id,'Repetição sem segundo desconto')
        assert db.scalar(select(func.sum(Stock.quantidade)))==3
        assert db.scalar(select(func.count()).select_from(AllocationCollection))==3


def test_preview_is_read_only_and_confirm_allocates_current_balance(warehouse):
    engine,accounts=warehouse;actor=accounts['admin'][0];add_stock(engine,'KNOWN',qty=0)
    order,pending=shortage(engine,actor,3)
    first,line=incoming(engine,actor,2,do_confirm=False)
    with Session(engine) as db:
        plan=biz.incoming_plan(db,first);assert plan[0]['quantidade']==2 and plan[0]['pending'].id==pending
        assert biz.satisfied(db,pending)==0 and db.get(ReceiptDocument,first) is None
    incoming(engine,actor,2)
    with Session(engine) as db:
        receipt.confirm_receipt(db,actor,first,1)
        assert biz.satisfied(db,pending)==3 and receipt.remaining(db,db.get(ReceiptLine,line))==1
        content=pdf_text(db.get(ReceiptDocument,first).pdf);assert 'Stock normal' in content


def test_receipt_pdf_failure_rolls_back_entire_allocation(warehouse,monkeypatch):
    from iberbag import commercial_pdf
    engine,accounts=warehouse;actor=accounts['admin'][0];add_stock(engine,'KNOWN',qty=0)
    order,pending=shortage(engine,actor,3);ident,line=incoming(engine,actor,2,do_confirm=False)
    def fail(*args):raise RuntimeError('Falha fictícia de impressão')
    monkeypatch.setattr(commercial_pdf,'render_receipt',fail)
    with Session(engine) as db,pytest.raises(RuntimeError):receipt.confirm_receipt(db,actor,ident,1)
    with Session(engine) as db:
        assert db.get(Receipt,ident).status=='draft' and biz.satisfied(db,pending)==0
        assert db.get(ReceiptDocument,ident) is None
        assert db.scalar(select(Carga.id).where(Carga.codigo==db.get(Receipt,ident).code)) is None


def test_preparation_pdf_failure_rolls_back_debit_and_pending(warehouse,monkeypatch):
    from iberbag import commercial_pdf
    engine,accounts=warehouse;actor=accounts['admin'][0];stock=add_stock(engine,'KNOWN',qty=2)
    ident=preparation(engine,actor,4)
    def fail(*args):raise RuntimeError('Falha fictícia')
    monkeypatch.setattr(commercial_pdf,'render_preparation',fail)
    with pytest.raises(RuntimeError):confirm(engine,actor,ident)
    with Session(engine) as db:
        assert db.get(Stock,stock).quantidade==2 and db.get(DispatchOrder,ident).status=='draft'
        assert db.scalar(select(func.count()).select_from(CustomerPending))==0


def test_mercadona_multiple_destinations_same_lot_and_ordered_document(warehouse):
    engine,accounts=warehouse;actor=accounts['admin'][0];stock=add_stock(engine,'KNOWN',qty=3)
    with Session(engine) as db:
        one=biz.save_destination(db,actor,'DESTINO A FICTÍCIO',20)
        two=biz.save_destination(db,actor,'DESTINO B FICTÍCIO',10,highlighted=True)
    ident=preparation(engine,actor,rows=[('L','2','DESTINO A FICTÍCIO'),('L','3','DESTINO B FICTÍCIO')],channel='mercadona')
    confirm(engine,actor,ident)
    with Session(engine) as db:
        assert db.get(Stock,stock).quantidade==0
        lines=dispatch.lines(db,ident)
        assert [biz.line_prepared(db,l.id) for l in lines]==[2,1]
        assert db.scalar(select(CustomerPending)).quantidade==2
        doc=db.get(DispatchDocument,ident).pdf;content=pdf_text(doc)
        assert content.index('Destino: DESTINO B')<content.index('Destino: DESTINO A')
        biz.save_destination(db,actor,'RENOMEADO FICTÍCIO',99,False,False,two)
        assert db.get(DispatchDocument,ident).pdf==doc
    incoming(engine,actor,2)
    with Session(engine) as db:
        allocation=biz.allocations(db)[0];assert allocation[2].destination=='DESTINO B FICTÍCIO'
        assert allocation[4].customer=='Mercadona'
        with pytest.raises(receipt.ReceivingError):biz.save_preparation(db,actor,secrets.token_hex(16),'INVÁLIDA','Mercadona','mercadona',[('L','1','DESTINO B FICTÍCIO')])


def test_normal_document_sorted_by_location_not_fifo_selection(warehouse):
    engine,accounts=warehouse;actor=accounts['admin'][0]
    add_stock(engine,'OLD','2020-01-01',1,local='B101');add_stock(engine,'NEW','2021-01-01',1,local='A101')
    ident=preparation(engine,actor,3);confirm(engine,actor,ident)
    with Session(engine) as db:
        assert [p.local for p in dispatch.picks(db,ident)]==['B101','A101']
        content=pdf_text(db.get(DispatchDocument,ident).pdf)
        assert content.index('A101')<content.index('B101')<content.index('S/ STOCK')


def test_concurrent_confirm_creates_one_pending_and_no_negative(warehouse):
    engine,accounts=warehouse;actor=accounts['admin'][0];add_stock(engine,'KNOWN',qty=2)
    first=preparation(engine,actor,5);barrier=Barrier(2)
    def run():barrier.wait(timeout=5);return confirm(engine,actor,first)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run) for _ in range(2)];assert [f.result(timeout=20) for f in futures]==[first,first]
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(CustomerPending))==1 and db.scalar(select(CustomerPending.quantidade))==3
        assert db.scalar(select(func.sum(Stock.quantidade)))==0


def test_concurrent_entries_cannot_overfill_pending(warehouse):
    engine,accounts=warehouse;actor=accounts['admin'][0];add_stock(engine,'KNOWN',qty=0);order,pending=shortage(engine,actor,3)
    r1,l1=incoming(engine,actor,2,do_confirm=False);r2,l2=incoming(engine,actor,2,do_confirm=False);barrier=Barrier(2)
    def run(ident):
        barrier.wait(timeout=5)
        with Session(engine) as db:return receipt.confirm_receipt(db,actor,ident,1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run,r) for r in (r1,r2)];assert {f.result(timeout=20) for f in futures}=={r1,r2}
    with Session(engine) as db:
        assert biz.satisfied(db,pending)==3
        assert receipt.remaining(db,db.get(ReceiptLine,l1))+receipt.remaining(db,db.get(ReceiptLine,l2))==1


def test_competing_preparations_and_receipt_are_serializable(warehouse):
    engine,accounts=warehouse;actor=accounts['admin'][0];add_stock(engine,'KNOWN',qty=2)
    first=preparation(engine,actor,4);second=preparation(engine,actor,4);receipt_id,line=incoming(engine,actor,3,do_confirm=False)
    barrier=Barrier(3)
    def run(action,ident):
        barrier.wait(timeout=5)
        try:
            with Session(engine) as db:
                return dispatch.confirm_preparation(db,actor,ident) if action=='out' else receipt.confirm_receipt(db,actor,ident,1)
        except receipt.ReceivingError:return None
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(run,action,ident) for action,ident in [('out',first),('out',second),('in',receipt_id)]]
        results=[f.result(timeout=20) for f in futures]
        assert sum(r is not None for r in results[:2])==1 and results[2]==receipt_id
    with Session(engine) as db:
        assert db.scalar(select(func.sum(Stock.quantidade)))==0
        assert db.scalar(select(func.sum(CustomerPending.quantidade)))==2
        used=db.scalar(select(func.coalesce(func.sum(PendingAllocation.quantidade),0)))
        assert used in (0,2) and receipt.remaining(db,db.get(ReceiptLine,line))==3-used


def test_inventory_does_not_recreate_uncollected_customer_goods(warehouse):
    engine,accounts=warehouse;actor=accounts['admin'][0];add_stock(engine,'KNOWN',qty=0);shortage(engine,actor,2)
    receipt_id,line=incoming(engine,actor,2,code='CLIENT-HELD')
    ident,scopes=inventory(engine,actor);count(engine,actor,scopes['Chão'],[('A','L','CLIENT-HELD','2')]);submit(engine,actor,ident)
    with pytest.raises(receipt.ReceivingError,match='aguardar recolha'):approve(engine,actor,ident)
    with Session(engine) as db:
        allocation=db.scalar(select(PendingAllocation));biz.record_collection(db,actor,allocation.id,'Recolha integral fictícia comprovada')
    with pytest.raises(receipt.ReceivingError,match='mudou|movimento|confer'):approve(engine,actor,ident)
    with Session(engine) as db:assert db.scalar(select(func.sum(Stock.quantidade)))==0


def test_legacy_reservations_block_pending_allocation_and_transfers_remain_protected(warehouse):
    engine,accounts=warehouse;actor=accounts['admin'][0];add_stock(engine,'KNOWN',qty=0);shortage(engine,actor,2)
    stock=add_stock(engine,'NOW',qty=1)
    legacy=legacy_order(engine,actor);legacy_review(engine,actor,legacy)
    r,l=incoming(engine,actor,2,do_confirm=False)
    with Session(engine) as db:
        with pytest.raises(receipt.ReceivingError,match='Reservas antigas'):receipt.confirm_receipt(db,actor,r,1)
        assert db.get(Receipt,r).status=='draft'
        dispatch.cancel_order(db,actor,legacy,'Recolha não iniciada',True)
        receipt.confirm_receipt(db,actor,r,1)
        assert db.get(Stock,stock).quantidade==1


def test_pending_prevents_automatic_compensation_and_cannot_cancel_confirmed(warehouse):
    engine,accounts=warehouse;actor=accounts['admin'][0];add_stock(engine,'KNOWN',qty=1)
    order=preparation(engine,actor,2);confirm(engine,actor,order)
    ident,scopes=inventory(engine,actor);count(engine,actor,scopes['Chão'],[('A','L','KNOWN','1')]);submit(engine,actor,ident)
    with pytest.raises(receipt.ReceivingError,match='pendentes comerciais'):approve(engine,actor,ident,order)
    with Session(engine) as db,pytest.raises(receipt.ReceivingError):dispatch.cancel_order(db,actor,order,'Não apagar histórico')


def test_ledger_is_immutable_and_limits_reject_sql_overallocation(warehouse):
    engine,accounts=warehouse;actor=accounts['admin'][0];add_stock(engine,'KNOWN',qty=0);order,pending=shortage(engine,actor,3)
    ident,line=incoming(engine,actor,1)
    for table in ('customer_pending','pending_allocations','receipt_documents'):
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:conn.execute(text(f'DELETE FROM {table}'))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:conn.execute(text("UPDATE commercial_preparations SET customer='ALTERADO'"))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO pending_allocations(pending_id,receipt_line_id,quantidade,actor,created_at) VALUES (:p,:l,9,'ficticio',now())"),{'p':pending,'l':line})
    with Session(engine) as db:assert biz.satisfied(db,pending)==1


def test_composition_draft_preserves_boxes_no_stock_and_no_normal_pallet_ids(warehouse):
    engine,accounts=warehouse;actor=accounts['operador'][0]
    with Session(engine) as db:
        key=secrets.token_hex(16);args=(key,'MISTA FICTÍCIA',[('MEDIDA A','3'),('MEDIDA B','7')],'Componentes a identificar')
        ident=biz.save_composition(db,actor,*args);assert biz.save_composition(db,actor,*args)==ident
        row=db.get(IncompleteComposition,ident)
        assert row.physical_pallets==1 and json.loads(row.components)==[['MEDIDA A',3],['MEDIDA B',7]]
        assert db.scalar(select(func.count()).select_from(Stock))==0
        with pytest.raises(receipt.ReceivingError):biz.save_composition(db,actor,secrets.token_hex(16),'BAD',[('A','1.5')],'Inválido')


def login(client,role,accounts):
    assert client.post('/login',data=login_data(client, {'username':role,'password':accounts[role][1]}),follow_redirects=False).status_code==303
    return re.search('name="csrf" value="([^"]+)"',client.get('/').text)[1]


def test_ui_permissions_csrf_pdf_and_zero_pick_confirmation(warehouse,monkeypatch):
    from iberbag import app as module
    engine,accounts=warehouse;actor=accounts['admin'][0];add_stock(engine,'KNOWN',qty=0)
    def dependency():
        with Session(engine) as db:yield db
    module.app.dependency_overrides[module.database]=dependency
    try:
        with TestClient(module.app) as client:
            csrf=login(client,'admin',accounts)
            page=client.get('/dispatches/new').text;assert 'name="customer"' in page and 'name="artigo_0"' not in page
            response=client.post('/dispatches/save',data={'csrf':csrf,'request_key':secrets.token_hex(16),'reference':'UI FICTÍCIA','customer':'CLIENTE UI','channel':'normal','lote_0':'L','quantidade_0':'3'},follow_redirects=False)
            assert response.status_code==303
            url=response.headers['location'];ident=int(url.rsplit('/',1)[1])
            assert client.post(url+'/review',data={'csrf':csrf,'version':'1'}).status_code==200
            assert 'Confirmar preparação e descontar stock' in client.get(url).text
            assert client.post(url+'/confirm',data={'csrf':csrf}).status_code==200
            first=client.get(url+'/document.pdf');assert first.status_code==200 and 'CLIENTE UI' in pdf_text(first.content)
            assert client.get('/pending').status_code==200
            incoming_id,line=incoming(engine,actor,2)
            assert client.get(f'/receipts/{incoming_id}').status_code==200
            assert client.get(f'/receipts/{incoming_id}/document.pdf').status_code==200
            assert 'Separadas' in client.get('/pending/allocations').text
            assert client.get('/compositions').status_code==200
            client.post('/logout',data={'csrf':csrf});csrf=login(client,'operador',accounts)
            assert client.get('/settings/mercadona').status_code==403
            assert client.post('/settings/mercadona',data={'csrf':csrf,'name':'BAD','sort_order':'0'}).status_code==403
            with Session(engine) as db:allocation=db.scalar(select(PendingAllocation.id))
            assert client.post(f'/pending/allocations/{allocation}/collect',data={'csrf':csrf,'reason':'Fictício'}).status_code==403
            assert client.post('/compositions',data={'reference':'NO CSRF'}).status_code==403
            assert client.get(url+'/document.pdf').content==first.content
    finally:module.app.dependency_overrides.clear()


def test_stage7_backup_restore_and_0005_upgrade(warehouse,pg_databases,tmp_path,monkeypatch):
    engine,accounts=warehouse;actor=accounts['admin'][0];add_stock(engine,'KNOWN',qty=0);order,pending=shortage(engine,actor,3)
    receipt_id,line=incoming(engine,actor,2)
    with Session(engine) as db:
        biz.save_composition(db,actor,secrets.token_hex(16),'FICTÍCIO',[('M','3')],'A esclarecer')
        biz.save_destination(db,actor,'DESTINO FICTÍCIO',1)
    folder=tmp_path/'backup7';backup.backup(engine,folder,runner=docker_client, passphrase=BACKUP_PASSWORD)
    target=pg_databases(restricted=True);assert backup.restore(target,folder,runner=docker_client, passphrase=BACKUP_PASSWORD)['status']=='reconciled'
    with Session(engine) as original,Session(target) as restored:
        assert restored.get(ReceiptDocument,receipt_id).pdf==original.get(ReceiptDocument,receipt_id).pdf
        assert restored.get(DispatchDocument,order).pdf==original.get(DispatchDocument,order).pdf
        assert biz.satisfied(restored,pending)==2 and restored.scalar(select(func.count()).select_from(IncompleteComposition))==1
    legacy=pg_databases(restricted=True)
    with legacy.begin() as conn:
        cfg=config();cfg.attributes['connection']=conn;command.upgrade(cfg,'0005')
        conn.execute(text("INSERT INTO app_config(key,value) VALUES ('warehouse_layout_seeded_vba','1')"))
    old_folder=tmp_path/'old5';backup.backup(legacy,old_folder,runner=docker_client, passphrase=BACKUP_PASSWORD)
    restore=pg_databases(restricted=True);backup.restore(restore,old_folder,runner=docker_client, passphrase=BACKUP_PASSWORD)
    from iberbag.schema_upgrade import apply_upgrade,preview
    assert preview(legacy)['from']=='0005' and apply_upgrade(legacy,old_folder)['revision']==REVISION
    with legacy.connect() as conn,restore.connect() as old_conn:
        assert transfer.inventory(transfer.read_data(conn))==transfer.inventory(transfer.read_data(old_conn))


def test_stale_plan_requires_review_before_new_shortfall(warehouse):
    engine,accounts=warehouse;actor=accounts['admin'][0];add_stock(engine,'KNOWN',qty=2)
    first=preparation(engine,actor,4);second=preparation(engine,actor,4)
    confirm(engine,actor,first)
    with pytest.raises(receipt.ReceivingError,match='mudou'):confirm(engine,actor,second)
    with Session(engine) as db:dispatch.review_preparation(db,actor,second,1)
    confirm(engine,actor,second)
    with Session(engine) as db:
        assert db.scalar(select(func.sum(CustomerPending.quantidade)))==6
        assert db.scalar(select(func.sum(Stock.quantidade)))==0


def test_current_logical_test_migration_preserves_commercial_ledgers(warehouse,pg_databases,tmp_path):
    from sqlalchemy import create_engine
    from iberbag.database import Base
    engine,accounts=warehouse;actor=accounts['admin'][0];add_stock(engine,'KNOWN',qty=0)
    order,pending=shortage(engine,actor,3);receipt_id,line=incoming(engine,actor,2)
    with Session(engine) as db:
        allocation_id=db.scalar(select(PendingAllocation.id));biz.record_collection(db,actor,allocation_id,'Recolha fictícia integral')
    with engine.connect() as conn:data=transfer.read_data(conn)
    source=tmp_path/'synthetic.db';sqlite=create_engine('sqlite:///'+str(source))
    # Offline synthetic fixture creation only, never an application operational write.
    Base.metadata.create_all(sqlite)
    with sqlite.begin() as conn:
        for table in transfer.TABLES:
            if data[table.name]:conn.execute(table.insert(),data[table.name])
        conn.execute(text('CREATE TABLE alembic_version(version_num VARCHAR(32) NOT NULL)'))
        conn.execute(text('INSERT INTO alembic_version VALUES (:revision)'),{'revision':REVISION})
    sqlite.dispose();before=transfer.digest_file(source)
    bundle=tmp_path/'preview7';transfer.preview(source,bundle)
    destination=pg_databases(restricted=True)
    assert transfer.apply(bundle,destination)['status']=='reconciled'
    with Session(destination) as db:
        assert biz.satisfied(db,pending)==2 and db.get(AllocationCollection,allocation_id) is not None
        assert db.get(ReceiptDocument,receipt_id).pdf==data['receipt_documents'][0]['pdf']
    assert before==transfer.digest_file(source)


def test_cancel_draft_creates_neither_pending_nor_debit_and_has_audit(warehouse):
    engine,accounts=warehouse;actor=accounts['admin'][0];stock=add_stock(engine,'KNOWN',qty=1)
    ident=preparation(engine,actor,3)
    with Session(engine) as db:
        dispatch.cancel_order(db,actor,ident,'Encomenda fictícia retirada')
        with pytest.raises(receipt.ReceivingError):dispatch.confirm_preparation(db,actor,ident)
        assert db.get(Stock,stock).quantidade==1 and db.scalar(select(func.count()).select_from(CustomerPending))==0
        assert db.scalar(select(Audit.id).where(Audit.action=='CANCELAR_PEDIDO')) is not None


def test_0005_upgrade_preserves_confirmed_office_stock_pdf_and_identity(warehouse,pg_databases,tmp_path):
    from datetime import datetime
    from types import SimpleNamespace
    from sqlalchemy import Table,MetaData
    from iberbag.preparation_pdf import render_pdf
    from iberbag.schema_upgrade import apply_upgrade
    old=pg_databases(restricted=True);restored=pg_databases(restricted=True);stamp=datetime(2020,1,1)
    with old.begin() as conn:
        cfg=config();cfg.attributes['connection']=conn;command.upgrade(cfg,'0005')
    def insert(conn,name,values):
        table=Table(name,MetaData(),autoload_with=conn)
        return conn.execute(table.insert().values(**values).returning(table.c.id)).scalar_one()
    from iberbag.database import seed_warehouse_layout
    with Session(old) as db:seed_warehouse_layout(db)
    with warehouse[0].connect() as conn:user=dict(conn.execute(select(User.__table__).limit(1)).mappings().one())
    with old.begin() as conn:
        actor=insert(conn,'users',user)
        insert(conn,'cargas',dict(codigo='LEGACY5',entrada=stamp,funcionario=None))
        stock=insert(conn,'stock',dict(artigo='A',lote='L',carga='LEGACY5',local='Chão',quantidade=0))
        order=insert(conn,'dispatch_orders',dict(office=True,request_key=secrets.token_hex(16),reference='ESCRITÓRIO LEGADO FICTÍCIO',
            payload_hash=secrets.token_hex(32),created_by=actor,created_at=stamp,version=1,status='draft'))
        line=insert(conn,'dispatch_lines',dict(order_id=order,artigo='A',lote='L',quantidade=1))
        insert(conn,'dispatch_picks',dict(line_id=line,stock_id=stock,carga='LEGACY5',entrada=stamp,local='Chão',quantidade=1))
        conn.execute(text("UPDATE dispatch_orders SET status='confirmed',reviewed_at=:s,checked_at=:s,checked_by='admin',confirmed_at=:s,confirmed_by='admin' WHERE id=:id"),{'s':stamp,'id':order})
        pdf=render_pdf(SimpleNamespace(id=order,reference='ESCRITÓRIO LEGADO FICTÍCIO',confirmed_at=stamp,confirmed_by='admin'),[dict(artigo='A',lote='L',carga='LEGACY5',local='Chão',quantidade=1)])
        doc=Table('dispatch_documents',MetaData(),autoload_with=conn)
        import hashlib
        conn.execute(doc.insert().values(order_id=order,pdf=pdf,sha256=hashlib.sha256(pdf).hexdigest(),stock_versions='{}'))
    folder=tmp_path/'legacy5-populated';backup.backup(old,folder,runner=docker_client, passphrase=BACKUP_PASSWORD);backup.restore(restored,folder,runner=docker_client, passphrase=BACKUP_PASSWORD)
    with pytest.raises(ValueError,match='restauro'):
        # A folder with archive/manifest alone is insufficient; the real proof remains required.
        import shutil
        unverified=tmp_path/'unverified';unverified.mkdir()
        for name in ('manifest.json','database.dump'):shutil.copyfile(folder/name,unverified/name)
        apply_upgrade(old,unverified)
    assert apply_upgrade(old,folder)['revision']==REVISION
    assert apply_upgrade(old,folder)['status']=='already_current'
    with Session(old) as db:
        assert db.get(Stock,stock).quantidade==0 and db.get(DispatchDocument,order).pdf==pdf
        assert dispatch.lines(db,order)[0].destination=='' and biz.context(db,order) is None
        assert db.scalar(select(func.count()).select_from(CustomerPending))==0


def test_multilot_commercial_preserves_original_and_prepared_quantities(warehouse):
    engine,accounts=warehouse;actor=accounts['admin'][0]
    add_stock(engine,'L1',qty=2);add_stock(engine,'L2',qty=3,lote='L2')
    ident=preparation(engine,actor,rows=[('L','4',''),('L2','1','')]);confirm(engine,actor,ident)
    with Session(engine) as db:
        lines=dispatch.lines(db,ident)
        assert [(l.artigo,l.lote,l.quantidade,biz.line_prepared(db,l.id)) for l in lines]==[('A','L',4,2),('A','L2',1,1)]
        assert db.scalar(select(CustomerPending.quantidade))==2 and db.scalar(select(func.sum(Stock.quantidade)))==2


def test_same_receipt_concurrent_retry_does_not_duplicate_allocation(warehouse):
    engine,accounts=warehouse;actor=accounts['admin'][0];add_stock(engine,'KNOWN',qty=0);order,pending=shortage(engine,actor,3)
    ident,line=incoming(engine,actor,2,do_confirm=False);barrier=Barrier(2)
    def run():
        barrier.wait(timeout=5)
        with Session(engine) as db:return receipt.confirm_receipt(db,actor,ident,1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run) for _ in range(2)]
        assert [f.result(timeout=20) for f in futures]==[ident,ident]
    with Session(engine) as db:
        assert biz.satisfied(db,pending)==2 and db.scalar(select(func.count()).select_from(PendingAllocation))==1
        assert db.scalar(select(func.count()).select_from(ReceiptDocument))==1
