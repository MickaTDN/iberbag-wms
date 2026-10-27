import re
import secrets
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from sqlalchemy import select, func, text, event, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from alembic import command
from fastapi.testclient import TestClient
from iberbag import transferring as ops, dispatching, receiving, backup, transfer
from iberbag.database import Stock, Carga, StockTransfer, Location, Audit, User, Base
from iberbag.migrations import config
from iberbag.warehouse_settings import set_location_active, WarehouseError
from test_transfer import pg_databases, docker_client
from test_receiving import warehouse
from test_dispatching import add_stock, order, review as review_dispatch, check as check_dispatch, confirm as confirm_dispatch
from legacy_catalogue import seed_legacy_catalogue


def review(engine, actor, stock, local, qty=1, key=None):
    with Session(engine) as db:
        return ops.review_transfer(db, actor, key or secrets.token_hex(16), stock, local, str(qty))


def confirm(engine, actor, ident):
    with Session(engine) as db: return ops.confirm_transfer(db, actor, ident)


@pytest.mark.parametrize('source,destination,quantity,total', [('A101','A102',1,1), ('Chão','A101',1,3), ('A101','Chão',1,1)])
def test_transfer_routes_and_partial_conservation(warehouse, source, destination, quantity, total):
    engine,accounts=warehouse; actor=accounts['operador'][0]
    stock=add_stock(engine,'CARGA','2020-01-02',total,source)
    ident=review(engine,actor,stock,destination,quantity)
    with Session(engine) as db: assert db.get(Stock,stock).quantidade==total
    confirm(engine,actor,ident); confirm(engine,actor,ident)
    with Session(engine) as db:
        record=db.get(StockTransfer,ident)
        assert record.status=='confirmed' and record.confirmed_by=='operador' and record.confirmed_at is not None
        assert db.get(Stock,stock).quantidade==total-quantity
        assert db.get(Stock,record.destination_stock_id).quantidade==quantity
        assert db.scalar(select(func.sum(Stock.quantidade)))==total
        assert db.scalar(select(Carga.entrada))==record.entrada
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action=='CONFIRMAR_TRANSFERENCIA'))==1
        assert db.get(Stock,record.destination_stock_id).carga=='CARGA'


@pytest.mark.parametrize('destination', ['A101','A128','Z999','(não indicado)'])
def test_same_or_invalid_destination(warehouse,destination):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    stock=add_stock(engine,'ONE',local='A101')
    with pytest.raises(ops.TransferError): review(engine,actor,stock,destination)


@pytest.mark.parametrize('quantity', ['0','-1','1.5','2147483648',''])
def test_invalid_quantities(warehouse,quantity):
    engine,accounts=warehouse; stock=add_stock(engine,'ONE')
    with pytest.raises(ops.TransferError): review(engine,accounts['operador'][0],stock,'A101',quantity)


def test_occupied_position_any_group_and_insufficient_quantity(warehouse):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    stock=add_stock(engine,'ONE',qty=3)
    add_stock(engine,'ONE',local='A101')  # Same article/lot/load is still occupied.
    add_stock(engine,'OTHER',local='A102',artigo='OTHER',lote='OTHER')
    for local,qty in [('A101',1),('A102',1),('A103',2),('A103',4)]:
        with pytest.raises(ops.TransferError): review(engine,actor,stock,local,qty)


def test_merges_floor_group_and_reuses_zero_destination(warehouse):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    floor=add_stock(engine,'ONE',qty=2)
    rack=add_stock(engine,'ONE',local='A101')
    ident=review(engine,actor,rack,'Chão'); confirm(engine,actor,ident)
    with Session(engine) as db:
        assert db.get(StockTransfer,ident).destination_stock_id==floor
        assert db.get(Stock,floor).quantidade==3
    second=review(engine,actor,floor,'A101'); confirm(engine,actor,second)
    with Session(engine) as db: assert db.get(StockTransfer,second).destination_stock_id==rack


def test_active_destination_and_putaway_protection(warehouse):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    stock=add_stock(engine,'ONE')
    ident=review(engine,actor,stock,'A101')
    with Session(engine) as db: set_location_active(db,'admin','A101',False)
    with pytest.raises(ops.TransferError,match='inativa'): confirm(engine,actor,ident)
    with pytest.raises(ops.TransferError): review(engine,actor,stock,'A101')
    with Session(engine) as db:
        assert db.get(Stock,stock).quantidade==1
        with pytest.raises(receiving.ReceivingError): receiving.destination(db,'A101',1)
        set_location_active(db,'admin','A101',True)
    confirm(engine,actor,ident)
    with Session(engine) as db,pytest.raises(WarehouseError): set_location_active(db,'admin','A101',False)


def test_date_and_fifo_preserved_and_unknown_stays_unknown(warehouse):
    engine,accounts=warehouse; actor=accounts['operador'][0]
    old=add_stock(engine,'OLD','2020-01-01',2)
    add_stock(engine,'NEW','2021-01-01',2)
    ident=review(engine,actor,old,'A101'); confirm(engine,actor,ident)
    pedido=order(engine,actor,[('A','L','3')]); review_dispatch(engine,actor,pedido)
    with Session(engine) as db:
        plan=dispatching.picks(db,pedido)
        assert [p.carga for p in plan]==['OLD','OLD','NEW']
        assert [p.quantidade for p in plan]==[1,1,1]
    unknown=add_stock(engine,'UNKNOWN',None,local='A102',artigo='UNKNOWN',lote='UNKNOWN-LOT')
    moved=review(engine,actor,unknown,'Chão'); confirm(engine,actor,moved)
    with Session(engine) as db: assert db.get(StockTransfer,moved).entrada is None
    unknown_order=order(engine,actor,[('UNKNOWN','UNKNOWN-LOT','1')])
    with pytest.raises(dispatching.DispatchError,match='desconhecida'): review_dispatch(engine,actor,unknown_order)


@pytest.mark.parametrize('state', ['ready','checked','blocked'])
def test_active_dispatch_blocks_selected_and_unselected_groups(warehouse,state):
    engine,accounts=warehouse; actor=accounts['operador'][0]
    selected=add_stock(engine,'OLD',qty=2)
    newer=add_stock(engine,'NEW','2021-01-01',local='A101')
    proposal=review(engine,actor,newer,'A102')
    pedido=order(engine,actor); review_dispatch(engine,actor,pedido)
    if state=='checked': check_dispatch(engine,actor,pedido)
    if state=='blocked':
        with Session(engine) as db:
            pick=dispatching.picks(db,pedido)[0]
            dispatching.check_order(db,actor,pedido,{pick.id:'0'},'Divergência fictícia')
    with Session(engine) as db: before=transfer.inventory(transfer.read_data(db.connection()))
    for group in (selected,newer):
        with pytest.raises(ops.TransferError,match='pedido de saída'): review(engine,actor,group,'A103')
    with pytest.raises(ops.TransferError): confirm(engine,actor,proposal)
    with Session(engine) as db:
        after=transfer.inventory(transfer.read_data(db.connection()))
        assert after==before  # No reservation, plan, quantity or state was changed.


@pytest.mark.parametrize('scenario', ['destination','source','duplicate'])
def test_concurrent_transfers(warehouse,scenario):
    engine,accounts=warehouse; actor=accounts['operador'][0]; other=accounts['other'][0]
    source=add_stock(engine,'ONE',qty=2 if scenario=='destination' else 1)
    first=review(engine,actor,source,'A101')
    second=first if scenario=='duplicate' else review(engine,other,source,'A101' if scenario=='destination' else 'A102')
    if scenario=='duplicate': other=actor
    barrier=Barrier(2)
    def run(uid,ident):
        barrier.wait(timeout=5)
        try: confirm(engine,uid,ident); return True
        except ops.TransferError: return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run,uid,ident) for uid,ident in [(actor,first),(other,second)]]
        result=[f.result(timeout=15) for f in futures]
    assert sum(result)==(2 if scenario=='duplicate' else 1)
    with Session(engine) as db:
        assert db.scalar(select(func.sum(Stock.quantidade)))==(2 if scenario=='destination' else 1)
        assert db.scalar(select(func.count()).select_from(StockTransfer).where(StockTransfer.status=='confirmed'))==1


def test_rollback_and_immutability(warehouse):
    engine,accounts=warehouse; actor=accounts['operador'][0]
    stock=add_stock(engine,'ONE'); ident=review(engine,actor,stock,'A101')
    with Session(engine) as db:
        def fail(session): raise RuntimeError('Injected failure')
        event.listen(db,'before_commit',fail)
        with pytest.raises(RuntimeError): ops.confirm_transfer(db,actor,ident)
    with Session(engine) as db:
        assert db.get(Stock,stock).quantidade==1
        assert db.scalar(select(func.count()).select_from(Stock))==1
        assert db.get(StockTransfer,ident).status=='draft'
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action=='CONFIRMAR_TRANSFERENCIA'))==0
    confirm(engine,actor,ident)
    for statement in ['DELETE FROM stock_transfers','UPDATE stock_transfers SET quantidade=2']:
        with engine.begin() as conn,pytest.raises(IntegrityError): conn.execute(text(statement))


def test_request_keys_and_permissions(warehouse):
    engine,accounts=warehouse; actor=accounts['operador'][0]; other=accounts['other'][0]
    stock=add_stock(engine,'ONE'); key=secrets.token_hex(16)
    ident=review(engine,actor,stock,'A101',key=key)
    assert review(engine,actor,stock,'A101',key=key)==ident
    with pytest.raises(ops.TransferError): review(engine,actor,stock,'A102',key=key)
    with pytest.raises(ops.TransferError) as error: confirm(engine,other,ident)
    assert error.value.status==403
    with Session(engine) as db:
        db.get(User,actor).active=False; db.commit()
    with pytest.raises(ops.TransferError) as error: confirm(engine,actor,ident)
    assert error.value.status==403


def test_sqlite_operational_transfer_refused(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'db.sqlite'))
    try:
        with Session(engine) as db,pytest.raises(ops.TransferError) as error:
            ops.review_transfer(db,1,secrets.token_hex(16),1,'A101','1')
        assert error.value.status==503
    finally: engine.dispose()


def test_interface_permissions_csrf_stock_and_map(warehouse):
    engine,accounts=warehouse
    from iberbag.app import app,database
    add_stock(engine,'UI-LOAD',qty=2)
    def session():
        with Session(engine,expire_on_commit=False) as db: yield db
    def field(response,name): return re.search(r'name="'+name+r'" value="([^"]+)"',response.text).group(1)
    app.dependency_overrides[database]=session
    try:
        with TestClient(app) as client,TestClient(app) as admin:
            assert client.get('/transfers',follow_redirects=False).status_code==303
            for c,role in ((client,'operador'),(admin,'admin')):
                assert c.post('/login',data={'username':role,'password':accounts[role][1]},follow_redirects=False).status_code==303
            form=client.get('/transfers/new?source=Chão'); csrf=field(form,'csrf')
            assert 'UI-LOAD' in form.text
            data=dict(csrf=csrf,request_key=field(form,'request_key'),stock_id=field(form,'stock_id'),destination='A101',quantidade='1')
            assert client.post('/transfers/review',data={key:value for key,value in data.items() if key!='csrf'}).status_code==403
            reviewed=client.post('/transfers/review',data=data)
            assert 'Rever transferência' in reviewed.text
            url=str(reviewed.url).split('testserver')[1]
            assert admin.post(url+'/confirm',data={'csrf':field(admin.get('/transfers/new'),'csrf')}).status_code==403
            assert 'Transferência confirmada' in client.post(url+'/confirm',data={'csrf':csrf}).text
            with Session(engine) as db: record=db.scalar(select(StockTransfer)); target=record.destination_stock_id
            assert 'Histórico de transferências' in client.get(f'/stock/{target}').text
            page=client.get('/map?corredor=A&andar=1')
            assert 'A101: 1 palete' in page.text
            assert client.post('/settings/locations/A102/active',data={'csrf':csrf,'active':'0'}).status_code==403
            admincsrf=field(admin.get('/transfers/new'),'csrf')
            assert admin.post('/settings/locations/A102/active',data={'csrf':admincsrf,'active':'0'}).status_code==200
            assert 'Posição inativa' in client.get('/map?corredor=A&andar=1').text
    finally: app.dependency_overrides.clear()


def test_transfer_history_backup_restore_and_test_migration(warehouse,pg_databases,tmp_path):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    stock=add_stock(engine,'ONE'); ident=review(engine,actor,stock,'A101'); confirm(engine,actor,ident)
    restored=pg_databases(restricted=True); folder=tmp_path/'backup'
    backup.backup(engine,folder,runner=docker_client)
    assert backup.restore(restored,folder,runner=docker_client)['status']=='reconciled'
    with Session(restored) as db: assert db.get(StockTransfer,ident).confirmed_by=='admin'
    with restored.begin() as conn,pytest.raises(IntegrityError): conn.execute(text('DELETE FROM stock_transfers'))
    with engine.connect() as conn: data=transfer.read_data(conn)
    source=tmp_path/'source.sqlite'; sqlite=create_engine('sqlite:///'+str(source))
    try:
        Base.metadata.create_all(sqlite)
        with sqlite.begin() as conn:
            for table in transfer.TABLES:
                if data[table.name]: conn.execute(table.insert(),data[table.name])
    finally: sqlite.dispose()
    bundle=tmp_path/'preview'; transfer.preview(source,bundle)
    target=pg_databases(restricted=True)
    assert transfer.apply(bundle,target)['status']=='reconciled'


def test_upgrade_0003_requires_verified_backup_and_preserves_stock(warehouse,pg_databases,tmp_path):
    from iberbag.schema_upgrade import preview,apply_upgrade
    legacy,restored=pg_databases(restricted=True),pg_databases(restricted=True)
    with legacy.begin() as conn:
        cfg=config(); cfg.attributes['connection']=conn; command.upgrade(cfg,'0003')
    with Session(legacy) as db: seed_legacy_catalogue(db)
    add_stock(legacy,'LEGACY',qty=3)
    folder=tmp_path/'backup'; backup.backup(legacy,folder,runner=docker_client)
    assert preview(legacy)['from']=='0003'
    with pytest.raises(ValueError,match='restauro'): apply_upgrade(legacy,folder)
    backup.restore(restored,folder,runner=docker_client)
    assert apply_upgrade(legacy,folder)['revision']=='0006'
    assert apply_upgrade(legacy,folder)['status']=='already_current'
    assert preview(legacy)['data']==preview(restored)['data']
    with Session(legacy) as db: assert all(db.scalars(select(Location.active)).all())


def test_dispatch_review_racing_transfer_has_consistent_fifo_plan(warehouse):
    engine,accounts=warehouse; actor=accounts['operador'][0]; other=accounts['other'][0]
    stock=add_stock(engine,'ONE',qty=2)
    proposal=review(engine,actor,stock,'A101')
    pedido=order(engine,other)
    barrier=Barrier(2)
    def move():
        barrier.wait(timeout=5)
        try: confirm(engine,actor,proposal); return True
        except ops.TransferError: return False
    def reserve():
        barrier.wait(timeout=5)
        review_dispatch(engine,other,pedido)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a,b=pool.submit(move),pool.submit(reserve)
        moved=a.result(timeout=15); b.result(timeout=15)
    with Session(engine) as db:
        assert db.get(StockTransfer,proposal).status==('confirmed' if moved else 'draft')
        assert db.scalar(select(func.sum(Stock.quantidade)))==2
        assert all(db.get(Stock,p.stock_id).local==p.local for p in dispatching.picks(db,pedido))
    check_dispatch(engine,other,pedido); confirm_dispatch(engine,other,pedido)


def test_changed_original_date_is_not_silently_reinterpreted(warehouse):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    stock=add_stock(engine,'ONE')
    proposal=review(engine,actor,stock,'A101')
    with Session(engine) as db:
        db.scalar(select(Carga)).entrada=None; db.commit()
    with pytest.raises(ops.TransferError,match='mudaram'): confirm(engine,actor,proposal)
    with Session(engine) as db:
        assert db.get(Stock,stock).quantidade==1
        assert db.get(StockTransfer,proposal).status=='draft'


def test_offline_migration_does_not_coerce_invalid_location_state(tmp_path):
    import sqlite3
    from iberbag.demo import create_demo
    source=tmp_path/'source.sqlite'
    create_demo(source,{role:secrets.token_urlsafe(24) for role in ('admin','operador')})
    with sqlite3.connect(source) as conn:
        conn.execute('UPDATE locations SET active=2 WHERE codigo=?',('A101',))
    before=transfer.digest_file(source)
    with pytest.raises(ValueError,match='locations.active'):
        transfer.preview(source,tmp_path/'preview')
    assert before==transfer.digest_file(source)
