from legacy_catalogue import seed_legacy_catalogue
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import re
import secrets
import pytest
from sqlalchemy import select, func, text, event, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from fastapi.testclient import TestClient
from alembic import command
from iberbag import dispatching as ops, backup, transfer
from iberbag.database import (Stock, Carga, DispatchOrder, DispatchLine, DispatchPick, Audit, User, Base)
from iberbag.migrations import config
from test_receiving import warehouse
from test_transfer import pg_databases, docker_client


def add_stock(engine, code, day='2020-01-01', qty=1, local='Chão', artigo='A', lote='L', missing=False):
    with Session(engine) as db:
        carga = db.scalar(select(Carga).where(Carga.codigo == code))
        if not carga and not missing:
            carga = Carga(codigo=code, entrada=datetime.fromisoformat(day) if day else None, funcionario=None)
            db.add(carga); db.flush()
        stock = Stock(artigo=artigo, lote=lote, carga=code, local=local, quantidade=qty)
        db.add(stock); db.commit()
        return stock.id


def order(engine, actor, rows=None):
    with Session(engine) as db:
        return ops.save_order(db, actor, secrets.token_hex(16), 'PEDIDO FICTÍCIO', rows or [('A','L','1')])


def review(engine, actor, ident):
    with Session(engine) as db: return ops.review_order(db, actor, ident, 1)


def check(engine, actor, ident):
    with Session(engine) as db:
        values = {p.id: str(p.quantidade) for p in ops.picks(db, ident)}
        return ops.check_order(db, actor, ident, values)


def confirm(engine, actor, ident):
    with Session(engine) as db: return ops.confirm_order(db, actor, ident)


def test_fifo_multiple_loads_locations_and_zero_rows(warehouse):
    engine, accounts = warehouse; actor = accounts['operador'][0]
    newest = add_stock(engine, 'NEW', '2022-01-01', 2)
    old1 = add_stock(engine, 'OLD', '2020-01-01', 1, 'A101')
    old2 = add_stock(engine, 'OLD', '2020-01-01', 2, 'Chão')
    ident = order(engine, actor, [('A','L','4')]); review(engine, actor, ident)
    with Session(engine) as db:
        assert [(p.stock_id,p.quantidade) for p in ops.picks(db, ident)] == [(old1,1),(old2,2),(newest,1)]
        assert ops.availability(db,'A','L') == {'total':5,'reserved':4,'free':1}
        assert db.get(Stock,old1).quantidade == 1
    check(engine,actor,ident); confirm(engine,actor,ident); confirm(engine,actor,ident)
    with Session(engine) as db:
        assert [db.get(Stock,x).quantidade for x in (old1,old2,newest)] == [0,0,1]
        assert db.get(DispatchOrder,ident).confirmed_by == 'operador'
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action=='CONFIRMAR_SAIDA')) == 1
    from iberbag.warehouse_settings import remove_location
    with Session(engine) as db: remove_location(db,'admin','A101')


def test_ties_are_stable_by_load_id_then_stock_id(warehouse):
    engine, accounts = warehouse; actor = accounts['admin'][0]
    first = add_stock(engine,'Z-FIRST',local='A102')
    second = add_stock(engine,'A-SECOND',local='A101')
    third = add_stock(engine,'Z-FIRST',local='Chão')
    ident = order(engine,actor,[('A','L','3')]); review(engine,actor,ident)
    with Session(engine) as db: assert [p.stock_id for p in ops.picks(db,ident)] == [first,third,second]


@pytest.mark.parametrize('missing', [True,False])
def test_unknown_date_blocks_even_with_sufficient_dated_stock(warehouse, missing):
    engine,accounts=warehouse; actor=accounts['operador'][0]
    add_stock(engine,'KNOWN',qty=5); add_stock(engine,'UNKNOWN',None,missing=missing)
    ident=order(engine,actor)
    with pytest.raises(ops.DispatchError,match='data de entrada'): review(engine,actor,ident)
    with Session(engine) as db:
        assert db.get(DispatchOrder,ident).status=='draft'
        assert ops.picks(db,ident)==[]
        assert db.scalar(select(func.sum(Stock.quantidade)))==6


def test_insufficient_stock_and_atomic_multiline_review(warehouse):
    engine,accounts=warehouse; actor=accounts['operador'][0]
    add_stock(engine,'ONE')
    ident=order(engine,actor,[('A','L','1'),('B','L2','1')])
    with pytest.raises(ops.DispatchError,match='insuficiente'): review(engine,actor,ident)
    with Session(engine) as db: assert ops.picks(db,ident)==[]


@pytest.mark.parametrize('actual', ['0','2'])
def test_physical_discrepancy_is_durable_and_blocks_other_orders(warehouse, actual):
    engine,accounts=warehouse; actor=accounts['operador'][0]
    add_stock(engine,'ONE',qty=5)
    ident=order(engine,actor); review(engine,actor,ident)
    with Session(engine) as db:
        pick=ops.picks(db,ident)[0]
        ops.check_order(db,actor,ident,{pick.id:actual},'Contagem física diferente')
        assert db.get(DispatchOrder,ident).status=='blocked'
        assert db.get(Stock,pick.stock_id).quantidade==5
        with pytest.raises(ops.DispatchError): ops.confirm_order(db,actor,ident)
        with pytest.raises(ops.DispatchError): ops.check_order(db,actor,ident,{pick.id:'1'})
    other=order(engine,accounts['other'][0])
    with pytest.raises(ops.DispatchError,match='bloqueado'): review(engine,accounts['other'][0],other)


@pytest.mark.parametrize('change', ['older','unknown','quantity','location'])
def test_confirmation_revalidates_fifo_and_does_not_partially_update(warehouse, change):
    engine,accounts=warehouse; actor=accounts['operador'][0]
    stock=add_stock(engine,'ONE',qty=2)
    ident=order(engine,actor); review(engine,actor,ident); check(engine,actor,ident)
    if change=='older': add_stock(engine,'OLDER','2010-01-01')
    elif change=='unknown': add_stock(engine,'UNKNOWN',None)
    else:
        with Session(engine) as db:
            if change=='quantity': db.get(Stock,stock).quantidade=0
            else: db.get(Stock,stock).local='A101'
            db.commit()
    with Session(engine) as db: before=transfer.inventory(transfer.read_data(db.connection()))['tables']['stock']
    confirm(engine,actor,ident)
    with Session(engine) as db:
        assert db.get(DispatchOrder,ident).status=='blocked'
        assert transfer.inventory(transfer.read_data(db.connection()))['tables']['stock']==before


@pytest.mark.parametrize('same', [True,False])
def test_concurrent_orders_or_duplicate_confirmations(warehouse,same):
    engine,accounts=warehouse; actor=accounts['operador'][0]; other=accounts['other'][0]
    add_stock(engine,'ONE')
    first=order(engine,actor)
    if same:
        review(engine,actor,first); check(engine,actor,first)
        second, other=first,actor
    else: second=order(engine,other)
    barrier=Barrier(2)
    def run(uid,ident):
        barrier.wait(timeout=5)
        try:
            if not same: review(engine,uid,ident); check(engine,uid,ident)
            confirm(engine,uid,ident)
            return 'ok'
        except ops.DispatchError: return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run,uid,ident) for uid,ident in [(actor,first),(other,second)]]
        result=[f.result(timeout=15) for f in futures]
    assert result.count('ok')==(2 if same else 1)
    with Session(engine) as db:
        assert db.scalar(select(func.sum(Stock.quantidade)))==0
        assert db.scalar(select(func.count()).select_from(DispatchOrder).where(DispatchOrder.status=='confirmed'))==1


def test_rollback_on_commit_failure_and_immutable_history(warehouse):
    engine,accounts=warehouse; actor=accounts['operador'][0]
    stock=add_stock(engine,'ONE')
    ident=order(engine,actor); review(engine,actor,ident); check(engine,actor,ident)
    with Session(engine) as db:
        def fail(session): raise RuntimeError('Injected failure')
        event.listen(db,'before_commit',fail)
        with pytest.raises(RuntimeError): ops.confirm_order(db,actor,ident)
    with Session(engine) as db:
        assert db.get(Stock,stock).quantidade==1
        assert db.get(DispatchOrder,ident).status=='checked'
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action=='CONFIRMAR_SAIDA'))==0
    confirm(engine,actor,ident)
    for statement in ['DELETE FROM dispatch_orders','UPDATE dispatch_lines SET quantidade=2','UPDATE dispatch_picks SET actual=0']:
        with engine.begin() as conn,pytest.raises(IntegrityError): conn.execute(text(statement))
    with engine.begin() as conn,pytest.raises(IntegrityError): conn.execute(text('UPDATE stock SET quantidade=-1'))


def test_draft_edit_version_and_invalid_quantities(warehouse):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    with Session(engine) as db:
        key=secrets.token_hex(16)
        ident=ops.save_order(db,actor,key,'R',[('A','L','1')])
        assert ops.save_order(db,actor,key,'R',[('A','L','1')])==ident
        ops.save_order(db,actor,key,'R2',[('A','L','2')],ident,1)
        with pytest.raises(ops.DispatchError,match='desatualizada'): ops.review_order(db,actor,ident,1)
        for qty in ['0','-1','0.5','2147483648']:
            with pytest.raises(ops.DispatchError): ops.save_order(db,actor,secrets.token_hex(16),'X',[('A','L',qty)])


def test_permissions_and_conference_validation(warehouse):
    engine,accounts=warehouse; actor=accounts['operador'][0]; other=accounts['other'][0]
    add_stock(engine,'ONE'); ident=order(engine,actor)
    with pytest.raises(ops.DispatchError) as error: review(engine,other,ident)
    assert error.value.status==403
    review(engine,actor,ident)
    with Session(engine) as db:
        pick=ops.picks(db,ident)[0]
        for values in [{},{pick.id:'-1'},{pick.id:'1.2'},{pick.id:'1',999:'1'}]:
            with pytest.raises(ops.DispatchError): ops.check_order(db,actor,ident,values)
        with pytest.raises(ops.DispatchError): ops.check_order(db,actor,ident,{pick.id:'0'})
        db.get(User,actor).active=False; db.commit()
        with pytest.raises(ops.DispatchError) as error: ops.check_order(db,actor,ident,{pick.id:'1'})
        assert error.value.status==403


def test_sqlite_dispatch_refused_and_payload_invalid(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'db.sqlite'))
    try:
        with Session(engine) as db,pytest.raises(ops.DispatchError) as error: ops.save_order(db,1,secrets.token_hex(16),'X',[('A','L','1')])
        assert error.value.status==503
        for rows in [[],[('A','','1')],[('A','L','1'),('A','L','2')]]:
            with pytest.raises(ops.DispatchError): ops.payload('X',rows)
    finally: engine.dispose()


def test_dispatch_ui_and_hidden_exhausted_stock(warehouse):
    engine,accounts=warehouse
    from iberbag.app import app,database
    stock=add_stock(engine,'UI-LOAD')
    def session():
        with Session(engine,expire_on_commit=False) as db: yield db
    def field(response,name): return re.search(r'name="'+name+r'" value="([^"]+)"',response.text).group(1)
    app.dependency_overrides[database]=session
    try:
        with TestClient(app) as client:
            assert client.get('/dispatches',follow_redirects=False).status_code==303
            client.post('/login',data={'username':'operador','password':accounts['operador'][1]})
            page=client.get('/dispatches/new'); csrf=field(page,'csrf')
            data=dict(csrf=csrf,request_key=field(page,'request_key'),reference='UI-ORDER',customer='CLIENTE FICTÍCIO',channel='normal',artigo_0='A',lote_0='L',quantidade_0='1',stock_id='999',carga='FORGED')
            response=client.post('/dispatches/save',data=data); url=str(response.url).split('testserver')[1]
            assert 'Stock registado' in response.text
            assert client.post(url+'/review',data={'version':'1'}).status_code==403
            ready=client.post(url+'/review',data={'csrf':csrf,'version':'1'})
            assert 'UI-LOAD' in ready.text and 'FORGED' not in ready.text
            with Session(engine) as db: pick=db.scalar(select(DispatchPick.id))
            # Office preparation no longer requires physical collection confirmation.
            confirmed=client.post(url+'/confirm',data={'csrf':csrf})
            assert 'stock descontado' in confirmed.text
            assert 'Não há' in client.get('/stock?artigo=A').text or '0 paletes' in client.get('/stock?artigo=A').text
            assert client.get(f'/stock/{stock}').status_code==200
            assert client.get('/settings').status_code==403
    finally: app.dependency_overrides.clear()


def test_backup_restore_and_transfer_preserve_dispatch_history(warehouse,pg_databases,tmp_path):
    engine,accounts=warehouse; actor=accounts['admin'][0]
    add_stock(engine,'ONE'); ident=order(engine,actor); review(engine,actor,ident); check(engine,actor,ident); confirm(engine,actor,ident)
    restored=pg_databases(restricted=True)
    folder=tmp_path/'backup'; backup.backup(engine,folder,runner=docker_client)
    assert backup.restore(restored,folder,runner=docker_client)['status']=='reconciled'
    with Session(restored) as db: assert db.get(DispatchOrder,ident).confirmed_by=='admin'
    with engine.connect() as conn: data=transfer.read_data(conn)
    source=tmp_path/'source.sqlite'; sqlite=create_engine('sqlite:///'+str(source))
    try:
        Base.metadata.create_all(sqlite)
        with sqlite.begin() as conn:
            for table in transfer.TABLES:
                if data[table.name]: conn.execute(table.insert(),data[table.name])
    finally: sqlite.dispose()
    bundle=tmp_path/'preview'; transfer.preview(source,bundle)
    dest=pg_databases(restricted=True)
    assert transfer.apply(bundle,dest)['status']=='reconciled'


def test_upgrade_0002_preserves_history_and_positive_stock(warehouse,pg_databases,tmp_path):
    from iberbag.schema_upgrade import preview,apply_upgrade
    from iberbag.database import seed_warehouse_layout
    legacy,restored=pg_databases(restricted=True),pg_databases(restricted=True)
    with legacy.begin() as conn:
        cfg=config(); cfg.attributes['connection']=conn; command.upgrade(cfg,'0002')
    with Session(legacy) as db: seed_legacy_catalogue(db)
    add_stock(legacy,'LEGACY',qty=3)
    assert preview(legacy)['from']=='0002'
    folder=tmp_path/'upgrade-backup'; backup.backup(legacy,folder,runner=docker_client)
    backup.restore(restored,folder,runner=docker_client)
    assert apply_upgrade(legacy,folder)['revision']=='0006'
    assert preview(legacy)['data']==preview(restored)['data']


def test_multiline_confirmation_is_all_or_nothing_when_last_fifo_changes(warehouse):
    engine,accounts=warehouse; actor=accounts['operador'][0]
    first=add_stock(engine,'ONE',artigo='A'); second=add_stock(engine,'TWO',artigo='B',lote='L2')
    ident=order(engine,actor,[('A','L','1'),('B','L2','1')]); review(engine,actor,ident); check(engine,actor,ident)
    add_stock(engine,'UNKNOWN',None,artigo='B',lote='L2')
    confirm(engine,actor,ident)
    with Session(engine) as db:
        assert db.get(DispatchOrder,ident).status=='blocked'
        assert db.get(Stock,first).quantidade==1 and db.get(Stock,second).quantidade==1


def test_exhausted_unknown_date_and_other_lot_do_not_block(warehouse):
    engine,accounts=warehouse; actor=accounts['operador'][0]
    add_stock(engine,'UNKNOWN',None,qty=0)
    add_stock(engine,'OTHER-LOT',None,lote='OUTRO')
    add_stock(engine,'KNOWN',qty=2)
    ident=order(engine,actor); review(engine,actor,ident); check(engine,actor,ident); confirm(engine,actor,ident)
    with Session(engine) as db: assert db.get(DispatchOrder,ident).status=='confirmed'


def test_nonoverlapping_lots_can_be_in_collection_simultaneously(warehouse):
    engine,accounts=warehouse; actor=accounts['operador'][0]; other=accounts['other'][0]
    add_stock(engine,'ONE'); add_stock(engine,'TWO',lote='OUTRO')
    first=order(engine,actor); second=order(engine,other,[('A','OUTRO','1')])
    review(engine,actor,first); review(engine,other,second)
    check(engine,other,second); confirm(engine,other,second)
    check(engine,actor,first); confirm(engine,actor,first)
    with Session(engine) as db: assert db.scalar(select(func.sum(Stock.quantidade)))==0
