from conftest import BACKUP_PASSWORD, login_data
"""Phase 1: synthetic owners/warehouses, real PostgreSQL, existing operations."""
import secrets
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
import pytest
from alembic import command
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import select, func, text, update, delete, inspect, Table, MetaData
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.exc import IntegrityError
from iberbag import contexts, receiving, dispatching, commercial, transferring, inventorying, backup, transfer
from iberbag.database import (Base, User, Client, Warehouse, StorageContext, Stock, Carga, Location, Corridor,
    Receipt, ReceiptLine, DispatchOrder, DispatchLine, DispatchDocument, CustomerPending, Audit, AppConfig,
    ReceiptDocument, PendingAllocation, StockTransfer, PhysicalInventory, InventoryScope, InventoryCount)
from iberbag.migrations import config, REVISION
from iberbag.warehouse_settings import add_corridor, set_capacity, WarehouseError
from test_receiving import warehouse
from test_transfer import pg_databases, docker_client
from legacy_catalogue import seed_legacy_catalogue


def scoped(engine, ident=1):
    return Session(engine, info={'ddn_context':ident})


def add_context(engine, actor, wh=1):
    with scoped(engine) as db:
        return contexts.create_client(db,actor,'Fictício '+secrets.token_hex(4),wh,'Área '+secrets.token_hex(4),True)


def receive(engine, actor, scope=1, article='ART', lot='LOT', qty=5, code='CARGA-IGUAL'):
    with scoped(engine,scope) as db:
        ident=receiving.save_receipt(db,actor,secrets.token_hex(16),code,'2020-01-02',[(article,lot,str(qty))])
        receiving.confirm_receipt(db,actor,ident,1)
        line=db.scalar(select(ReceiptLine.id).where(ReceiptLine.receipt_id==ident))
        place=receiving.review_putaway(db,actor,secrets.token_hex(16),line,'Chão',str(qty))
        receiving.confirm_putaway(db,actor,place)
        return ident,db.scalar(select(Stock.id).where(Stock.carga==code))


def prepare(engine,actor,scope,qty=2):
    with scoped(engine,scope) as db:
        ident=commercial.save_preparation(db,actor,secrets.token_hex(16),'TESTE','DESTINATÁRIO FICTÍCIO','normal',[('LOT',str(qty),'')])
        dispatching.review_preparation(db,actor,ident,1)
        dispatching.confirm_preparation(db,actor,ident)
        return ident


def test_initial_catalogues_and_no_invented_specific_workflows(warehouse):
    engine,accounts=warehouse
    with scoped(engine) as db:
        assert {w.name for w in db.scalars(select(Warehouse))}=={'Ovar','Escapães'}
        assert {c.name for c in db.scalars(select(Client))}=={'Iberbag','Valmet','Cosmopak','NKT','Alcobre'}
        assert contexts.require_context(db,4)[2].name=='Escapães'
        assert db.scalar(select(func.count()).select_from(Location))==2107
    with scoped(engine,4) as db, pytest.raises(receiving.ReceivingError,match='fase posterior'):
        receiving.save_receipt(db,accounts['admin'][0],secrets.token_hex(16),'TESTE','2020-01-01',[('A','L','1')])


def test_same_article_lot_load_and_location_are_isolated(warehouse):
    engine,accounts=warehouse;actor=accounts['admin'][0];other=add_context(engine,actor)
    _,first=receive(engine,actor);_,second=receive(engine,actor,other)
    with scoped(engine,other) as db:
        assert db.get(Stock,first) is None
        assert db.get(Stock,second).quantidade==5
        assert db.scalar(select(func.sum(Stock.quantidade)))==5
        assert db.scalars(select(Stock.id)).all()==[second]
        db.execute(update(Stock).values(quantidade=4));db.commit()
    with scoped(engine) as db:assert db.get(Stock,first).quantidade==5
    order=prepare(engine,actor,other)
    with scoped(engine) as db:
        assert db.get(DispatchOrder,order) is None
        assert db.get(Stock,first).quantidade==5
    with scoped(engine,other) as db:
        assert db.get(Stock,second).quantidade==2
        content='\n'.join(p.extract_text() for p in PdfReader(BytesIO(db.get(DispatchDocument,order).pdf)).pages)
        assert contexts.label(db) in content
        assert 'DDN WMS' in content
        assert db.get(DispatchOrder,order).confirmed_at


def test_lot_identity_is_per_context_even_in_union_queries(warehouse):
    engine,accounts=warehouse;actor=accounts['admin'][0];other=add_context(engine,actor)
    receive(engine,actor,article='ART-A');receive(engine,actor,other,article='ART-B')
    from iberbag.lots import resolve
    with scoped(engine) as db:assert resolve(db,'LOT')=='ART-A'
    with scoped(engine,other) as db:assert resolve(db,'LOT')=='ART-B'


def test_ovar_stock_is_never_available_in_escapaes(warehouse):
    # Same owner in both warehouses: a client-only filter must fail this test.
    engine,accounts=warehouse;actor=accounts['admin'][0]
    with scoped(engine) as db:
        ctx=StorageContext(client_id=1,warehouse_id=2,area='Iberbag',active=True)
        db.add(ctx);db.flush();other=ctx.id
        db.connection().execute(Location.__table__.insert().values(context_id=other,codigo='Chão',capacidade=None,active=True))
        db.commit()
    receive(engine,actor)
    with scoped(engine,other) as db:
        assert contexts.require_context(db)[1].id==1
        assert contexts.require_context(db)[2].name=='Escapães'
        assert db.scalar(select(func.count()).select_from(Stock))==0
        with pytest.raises(receiving.ReceivingError,match='inexistente'):
            commercial.save_preparation(db,actor,secrets.token_hex(16),'TESTE','TESTE','normal',[('LOT','1','')])
    receive(engine,actor,other,qty=3)
    prepare(engine,actor,other,1)
    with scoped(engine,other) as db:assert db.scalar(select(func.sum(Stock.quantidade)))==2
    with scoped(engine) as db:assert db.scalar(select(func.sum(Stock.quantidade)))==5


def test_pending_allocations_do_not_cross_owners(warehouse):
    engine,accounts=warehouse;actor=accounts['admin'][0];other=add_context(engine,actor)
    receive(engine,actor,qty=1);prepare(engine,actor,1,3)
    receive(engine,actor,other,qty=4)
    with scoped(engine) as db:
        assert db.scalar(select(CustomerPending.quantidade))==2
        assert db.scalar(select(func.count()).select_from(PendingAllocation))==0
    with scoped(engine,other) as db:assert db.scalar(select(func.sum(Stock.quantidade)))==4


def test_context_capacity_and_catalogue_are_resolved_exactly(warehouse):
    engine,accounts=warehouse;actor=accounts['admin'][0];other=add_context(engine,actor)
    with scoped(engine,other) as db:
        add_corridor(db,'admin','T',1,1)
        set_capacity(db,'admin','T101','3')
    receipt,stock=receive(engine,actor,other,qty=4)
    with scoped(engine,other) as db:
        ident=transferring.review_transfer(db,actor,secrets.token_hex(16),stock,'T101','3')
        transferring.confirm_transfer(db,actor,ident)
        assert db.scalar(select(func.sum(Stock.quantidade)).where(Stock.local=='T101'))==3
        with pytest.raises(receiving.ReceivingError,match='capacidade'):
            transferring.review_transfer(db,actor,secrets.token_hex(16),stock,'T101','1')
        with pytest.raises(WarehouseError):set_capacity(db,'admin','T101','2')
    with scoped(engine) as db:
        assert db.scalar(select(Location.capacidade).where(Location.codigo=='A101'))==1
        assert db.scalar(select(func.count()).select_from(StockTransfer))==0
    with scoped(engine) as db, pytest.raises(receiving.ReceivingError):
        transferring.review_transfer(db,actor,secrets.token_hex(16),stock,'B101','1')


def test_inventory_scope_clock_and_adjustments_are_isolated(warehouse):
    engine,accounts=warehouse;actor=accounts['admin'][0];other=add_context(engine,actor)
    receive(engine,actor,qty=5);_,stock=receive(engine,actor,other,qty=5)
    with scoped(engine,other) as db:
        inv=inventorying.create_inventory(db,actor,secrets.token_hex(16),'FICTÍCIO','full',[])
        scopes=inventorying.scopes(db,inv);assert [s.local for s in scopes]==['Chão']
        inventorying.save_count(db,actor,scopes[0].id,scopes[0].version,[('ART','LOT','CARGA-IGUAL','4')])
        inventorying.submit_inventory(db,actor,inv)
        inventorying.approve_inventory(db,actor,inv,'Contagem fictícia')
        assert db.get(Stock,stock).quantidade==4
    with scoped(engine) as db:
        assert db.get(PhysicalInventory,inv) is None
        assert db.scalar(select(func.sum(Stock.quantidade)))==5


def test_duplicate_request_keys_and_concurrent_preparations_stay_scoped(warehouse):
    engine,accounts=warehouse;actor=accounts['admin'][0];other=add_context(engine,actor)
    key=secrets.token_hex(16)
    for scope in (1,other):
        with scoped(engine,scope) as db:
            ident=receiving.save_receipt(db,actor,key,'DUP','2020-01-01',[('A','L','1')])
            assert receiving.save_receipt(db,actor,key,'DUP','2020-01-01',[('A','L','1')])==ident
        receive(engine,actor,scope,qty=5)
    with ThreadPoolExecutor(2) as pool:
        orders=list(pool.map(lambda ctx:prepare(engine,actor,ctx),[1,other]))
    for ctx,order in zip((1,other),orders):
        with scoped(engine,ctx) as db:
            dispatching.confirm_preparation(db,actor,order)
            assert db.scalar(select(func.sum(Stock.quantidade)))==3


def test_server_and_composite_foreign_keys_refuse_wrong_context(warehouse):
    engine,accounts=warehouse;actor=accounts['admin'][0];other=add_context(engine,actor)
    receipt,stock=receive(engine,actor)
    with scoped(engine,other) as db:
        assert db.get(Receipt,receipt) is None
        with pytest.raises(receiving.ReceivingError):receiving.confirm_receipt(db,actor,receipt,1)
        db.add(Stock(context_id=1,artigo='X',lote='Y',carga='Z',local='Chão',quantidade=1))
        with pytest.raises(ValueError,match='contexto'):db.commit()
    with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(ReceiptLine.__table__.insert().values(context_id=other,receipt_id=receipt,artigo='X',lote='Y',quantidade=1))
    with engine.begin() as conn:
        with pytest.raises(IntegrityError):conn.execute(text('UPDATE stock SET context_id=:ctx WHERE id=:id'),{'ctx':other,'id':stock})
    with scoped(engine) as db:
        db.get(Stock,stock);db.info['ddn_context']=other
        with pytest.raises(ValueError,match='imutável'):db.scalar(select(Stock))


def test_administrator_create_client_and_operator_denied(warehouse):
    engine,accounts=warehouse
    with scoped(engine) as db,pytest.raises(PermissionError):
        contexts.create_client(db,accounts['operador'][0],'NEGADO',1,'NEGADO',True)
    ident=add_context(engine,accounts['admin'][0])
    receive(engine,accounts['admin'][0],ident)
    with scoped(engine,ident) as db:
        assert contexts.require_context(db)[1].pallet_operations
        assert db.scalar(select(func.sum(Stock.quantidade)))==5


def test_http_context_selection_roles_csrf_and_stale_forms(warehouse,monkeypatch):
    import iberbag.app as web
    engine,accounts=warehouse;actor=accounts['admin'][0];other=add_context(engine,actor)
    receive(engine,actor,qty=7);receive(engine,actor,other,qty=4)
    monkeypatch.setattr(web,'SessionLocal',sessionmaker(bind=engine,expire_on_commit=False))
    with TestClient(web.app) as client:
        assert client.post('/login',data=login_data(client,{'username':'admin','password':accounts['admin'][1]}),follow_redirects=False).status_code==303
        with scoped(engine) as db:
            from iberbag.security import get_session
            csrf=get_session(db,client.cookies.get('wms_session')).csrf_token
        assert 'DDN WMS' in client.get('/settings/clients').text
        assert client.post('/context',data={'selected_context':other},follow_redirects=False).status_code==403
        assert client.post('/context',data={'selected_context':other,'csrf':csrf,'context_id':'1'},follow_redirects=False).status_code==303
        assert 'Chão</b><strong>4</strong>' in client.get('/map').text
        assert '4 paletes' in client.get('/stock').text
        assert client.post('/settings/clients',data={'csrf':csrf,'context_id':'1','name':'WRONG'},follow_redirects=False).status_code==409
        assert client.post('/settings/clients',data={'csrf':csrf,'context_id':other,'name':'WEB FICTÍCIO','warehouse':'2','area':'WEB FICTÍCIO','pallet_operations':'yes'},follow_redirects=False).status_code==303
        assert client.get('/stock/999999').status_code==404
    with TestClient(web.app) as client:
        client.post('/login',data=login_data(client,{'username':'operador','password':accounts['operador'][1]}))
        assert client.get('/settings/clients').status_code==403
        assert client.post('/settings/clients',data={'name':'FORBIDDEN'}).status_code==403
    with scoped(engine) as db:
        assert db.scalar(select(Client.id).where(Client.name=='WEB FICTÍCIO'))
        assert not db.scalar(select(Client.id).where(Client.name=='FORBIDDEN'))


def test_migration_preserves_every_legacy_column_and_document(warehouse,pg_databases):
    """Freeze a complete 0006 accounting history; no historical ORM uses new columns."""
    engine,accounts=warehouse;actor=accounts['admin'][0]
    receive(engine,actor,qty=5);prepare(engine,actor,1,7)
    with engine.connect() as conn:
        legacy={t.name:[{k:v for k,v in r.items() if k!='context_id'} for r in conn.execute(select(t)).mappings()]
                for t in Base.metadata.sorted_tables if t.name not in transfer.CONTEXT_TABLES | transfer.PHYSICAL_TABLES}
    target=pg_databases(restricted=True)
    with target.begin() as conn:
        cfg=config();cfg.attributes['connection']=conn;command.upgrade(cfg,'0006')
        conn.execute(text('ALTER TABLE stock DISABLE TRIGGER stock_clock'))
        conn.execute(text('ALTER TABLE locations DISABLE TRIGGER catalogue_clock'))
        # This is synthetic fixture restoration, not an application migration path.
        for table in Base.metadata.sorted_tables:
            if table.name in transfer.CONTEXT_TABLES | transfer.PHYSICAL_TABLES or not legacy[table.name]:continue
            reflected=Table(table.name,MetaData(),autoload_with=conn)
            # Preserve insertion guards; restore headers in draft and finalise last.
            rows=legacy[table.name]
            if table.name=='receipts':rows=[dict(r,status='draft',carga_id=None,receiver=None,confirmed_at=None) for r in rows]
            if table.name=='dispatch_orders':rows=[dict(r,status='draft',checked_by=None,checked_at=None,confirmed_by=None,confirmed_at=None) for r in rows]
            conn.execute(reflected.insert(),rows)
        for name in ('receipts','dispatch_orders'):
            reflected=Table(name,MetaData(),autoload_with=conn)
            for row in legacy[name]:conn.execute(reflected.update().where(reflected.c.id==row['id']).values(**row))
        conn.execute(text('ALTER TABLE stock ENABLE TRIGGER stock_clock'))
        conn.execute(text('ALTER TABLE locations ENABLE TRIGGER catalogue_clock'))
        conn.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
        command.upgrade(cfg,REVISION)
        for name,expected in legacy.items():
            table=Base.metadata.tables[name]
            actual=[{k:v for k,v in r.items() if k!='context_id'} for r in conn.execute(select(table)).mappings()]
            assert sorted(actual,key=repr)==sorted(expected,key=repr),name
        assert conn.scalar(text('SELECT count(*) FROM stock WHERE context_id<>1'))==0
        assert conn.scalar(text('SELECT count(*) FROM clients'))==5


def test_backup_restore_keeps_all_contexts_and_archived_pdfs(warehouse,pg_databases,tmp_path):
    engine,accounts=warehouse;actor=accounts['admin'][0];other=add_context(engine,actor,2)
    receipt,stock=receive(engine,actor);other_receipt,other_stock=receive(engine,actor,other)
    order=prepare(engine,actor,other,7)
    folder=tmp_path/'all-contexts'
    backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    target=pg_databases(restricted=True)
    assert backup.restore(target,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'
    with engine.connect() as a,target.connect() as b:
        assert transfer.inventory(transfer.read_data(a))==transfer.inventory(transfer.read_data(b))
    with scoped(target,other) as db,scoped(engine,other) as original:
        assert db.get(Stock,other_stock).quantidade==0
        assert db.get(Stock,stock) is None
        assert db.get(DispatchDocument,order).pdf==original.get(DispatchDocument,order).pdf
        assert db.get(ReceiptDocument,other_receipt).pdf==original.get(ReceiptDocument,other_receipt).pdf
        assert db.scalar(select(CustomerPending.quantidade))==2
    with scoped(target) as db:assert db.get(Stock,stock).quantidade==5
