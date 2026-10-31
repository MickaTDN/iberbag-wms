from conftest import BACKUP_PASSWORD, login_data
"""Final PR #10 review: exclusive physical capacity and cross-owner reservations."""
import secrets
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from alembic import command
from sqlalchemy import select, text, func, inspect
from sqlalchemy.exc import IntegrityError
from iberbag import dispatching, receiving, transferring, backup, transfer
from iberbag.database import (Location, Stock, Corridor, PhysicalLocationClaim, DispatchOrder, ReceiptDocument,
                               Audit, StorageContext)
from iberbag.migrations import config, REVISION
from iberbag.schema_upgrade import apply_upgrade
from iberbag.warehouse_settings import add_corridor, remove_location, rename_location, WarehouseError
from test_ddn_contexts import scoped, add_context, receive
from test_receiving import warehouse
from test_transfer import pg_databases, docker_client
from legacy_catalogue import seed_legacy_catalogue


def test_ovar_physical_code_cannot_be_duplicated_for_cosmopak(warehouse):
    engine,accounts=warehouse
    with scoped(engine,3) as db:
        with pytest.raises(WarehouseError,match='Partilha'):
            add_corridor(db,'admin','A',1,1)
        assert db.scalar(select(func.count()).select_from(Corridor))==0
        assert db.scalar(select(func.count()).select_from(Location))==0
    for target in ('locations','stock'):
        with engine.begin() as conn,pytest.raises(IntegrityError):
            if target=='locations':
                conn.execute(text("INSERT INTO locations(context_id,codigo,capacidade,active) VALUES (3,' a101 ',1,true)"))
            else:
                conn.execute(text("INSERT INTO stock(context_id,artigo,lote,carga,local,quantidade) VALUES (3,'A','L','C','A101',0)"))
    with scoped(engine) as db:assert db.scalar(select(Location.capacidade).where(Location.codigo=='A101'))==1


def test_deleting_location_does_not_release_physical_claim(warehouse):
    engine,accounts=warehouse
    with scoped(engine) as db:remove_location(db,'admin','A102')
    with engine.begin() as conn,pytest.raises(IntegrityError):
        conn.execute(text("INSERT INTO locations(context_id,codigo,capacidade,active) VALUES (3,'A102',1,true)"))
    with engine.begin() as conn,pytest.raises(IntegrityError):
        conn.execute(text("DELETE FROM physical_location_claims WHERE code='a102'"))
    with engine.begin() as conn,pytest.raises(IntegrityError):
        conn.execute(text('TRUNCATE physical_location_claims'))
    with engine.begin() as conn,pytest.raises(IntegrityError):
        conn.execute(text("INSERT INTO physical_location_claims(warehouse_id,code,context_id) VALUES (2,'z101',1)"))


def test_same_code_in_another_warehouse_is_a_different_position(warehouse):
    engine,accounts=warehouse;ctx=add_context(engine,accounts['admin'][0],2)
    with scoped(engine,ctx) as db:add_corridor(db,'admin','A',1,1)
    with engine.connect() as conn:
        assert conn.scalar(select(func.count()).select_from(PhysicalLocationClaim).where(PhysicalLocationClaim.code=='a101'))==2


def test_concurrent_physical_claims_allow_exactly_one_context(warehouse):
    engine,accounts=warehouse;actor=accounts['admin'][0]
    owners=[add_context(engine,actor),add_context(engine,actor)];barrier=Barrier(2)
    def claim(ctx):
        barrier.wait(timeout=10)
        try:
            with engine.begin() as conn:
                conn.execute(text("INSERT INTO locations(context_id,codigo,capacidade,active) VALUES (:ctx,'T101',1,true)"),{'ctx':ctx})
            return True
        except IntegrityError:return False
    with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(claim,owners))==[False,True]
    with engine.connect() as conn:
        assert conn.scalar(select(func.count()).select_from(PhysicalLocationClaim).where(PhysicalLocationClaim.code=='t101'))==1
        assert conn.scalar(text("SELECT count(*) FROM locations WHERE codigo='T101'"))==1


def test_reserved_stock_cancellation_and_movements_are_contextual(warehouse):
    engine,accounts=warehouse;actor=accounts['admin'][0];other=add_context(engine,actor)
    _,first=receive(engine,actor);_,second=receive(engine,actor,other)
    with scoped(engine) as db:
        order=dispatching.save_order(db,actor,secrets.token_hex(16),'RESERVA A',[('ART','LOT','2')])
        dispatching.review_order(db,actor,order,1)
        with pytest.raises(receiving.ReceivingError,match='reservado'):
            transferring.review_transfer(db,actor,secrets.token_hex(16),first,'A101','1')
    with scoped(engine,other) as db:
        add_corridor(db,'admin','T',1,1)
        move=transferring.review_transfer(db,actor,secrets.token_hex(16),second,'T101','1')
        transferring.confirm_transfer(db,actor,move)
        second_order=dispatching.save_order(db,actor,secrets.token_hex(16),'RESERVA B',[('ART','LOT','2')])
        dispatching.review_order(db,actor,second_order,1)
        assert {p.stock_id for p in dispatching.picks(db,second_order)}.isdisjoint({first})
    with scoped(engine) as db:
        dispatching.cancel_order(db,actor,order,'Cancelamento fictício',returned=True)
        dispatching.cancel_order(db,actor,order,'Cancelamento fictício',returned=True)
        assert db.get(Stock,first).quantidade==5
        assert db.get(DispatchOrder,second_order) is None
    with scoped(engine,other) as db:
        assert db.get(DispatchOrder,second_order).status=='ready'
        dispatching.check_order(db,actor,second_order,{p.id:str(p.quantidade) for p in dispatching.picks(db,second_order)})
        dispatching.confirm_order(db,actor,second_order)
        assert db.scalar(select(func.sum(Stock.quantidade)))==3
    with scoped(engine) as db:assert db.get(Stock,first).quantidade==5


def test_upgrade_refuses_ambiguous_existing_physical_codes_without_changes(pg_databases):
    engine=pg_databases(restricted=True)
    with engine.begin() as conn:
        cfg=config();cfg.attributes['connection']=conn;command.upgrade(cfg,'ddn_0001')
        conn.execute(text("INSERT INTO locations(context_id,codigo,capacidade,active) VALUES (1,'A101',1,true),(3,' a101 ',2,true)"))
    with pytest.raises(RuntimeError,match='Migração bloqueada'):
        with engine.begin() as conn:
            cfg=config();cfg.attributes['connection']=conn;command.upgrade(cfg,REVISION)
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT version_num FROM alembic_version'))=='ddn_0001'
        assert 'physical_location_claims' not in inspect(conn).get_table_names()
        assert conn.scalar(text('SELECT sum(capacidade) FROM locations'))==3
        assert conn.scalar(text('SELECT count(*) FROM stock'))==0


def test_ddn1_backup_restore_upgrade_preserves_legacy_data(pg_databases,tmp_path):
    origin,restored=pg_databases(restricted=True),pg_databases(restricted=True)
    with origin.begin() as conn:
        cfg=config();cfg.attributes['connection']=conn;command.upgrade(cfg,'ddn_0001')
    with scoped(origin) as db:seed_legacy_catalogue(db)
    folder=tmp_path/'ddn1'
    backup.backup(origin,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert backup.restore(restored,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'
    with origin.connect() as conn:before=transfer.inventory(transfer.read_data(conn))
    assert apply_upgrade(origin,folder)['status']=='upgraded'
    with origin.connect() as conn:
        assert transfer.inventory(transfer.read_data(conn))==before
        assert conn.scalar(select(func.count()).select_from(PhysicalLocationClaim))==2106


def test_sqlite_upgrade_preserves_uppercase_floor_as_unlimited(tmp_path):
    """SQLite lower() is ASCII-only; legacy CHÃO must not become a finite claim."""
    from sqlalchemy import create_engine
    engine=create_engine('sqlite:///'+str(tmp_path/'fictitious.db'))
    try:
        with engine.begin() as conn:
            cfg=config();cfg.attributes['connection']=conn;command.upgrade(cfg,'ddn_0001')
            conn.execute(text("INSERT INTO app_config(context_id,key,value) VALUES (1,'warehouse_layout_seeded_vba','1')"))
            conn.execute(text("INSERT INTO locations(context_id,codigo,capacidade,active) VALUES (1,'CHÃO',NULL,true)"))
            before=transfer.inventory(transfer.read_data(conn))
            command.upgrade(cfg,REVISION)
            assert conn.scalar(text('SELECT count(*) FROM physical_location_claims'))==0
            assert transfer.inventory(transfer.read_data(conn))==before
    finally:engine.dispose()
