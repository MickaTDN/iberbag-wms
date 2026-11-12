"""Common physical rack, fictitious goods, original owner-scoped services."""
import secrets
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from sqlalchemy import select,func,text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from fastapi.testclient import TestClient
from iberbag import database as m,physical_locations as p,receiving as r,dispatching as d,commercial as c,transferring as t,inventorying as i
from test_receiving import warehouse
from test_transfer import pg_databases,docker_client
from test_ddn_contexts import scoped
from test_security_regressions import restricted_web
from conftest import BACKUP_PASSWORD


def activate(engine,actor,code='A101'):
    with scoped(engine) as db:
        ident=p.review_sharing(db,actor,secrets.token_hex(16),code,'Conferência física fictícia: posição única, livre e capacidade uma palete',True)
        p.confirm_sharing(db,actor,ident);return ident


def logistics_fixture(engine):
    # Explicit fictitious harness for the future client adapter. Activation itself
    # must never enable unimplemented Cosmopak workflows or alter client capabilities.
    with Session(engine) as db:
        assert db.get(m.Client,3).pallet_operations is False
        db.get(m.Client,3).pallet_operations=True;db.commit()


def receive(engine,actor,ctx=1,qty=1,code=None):
    with scoped(engine,ctx) as db:
        ident=r.save_receipt(db,actor,secrets.token_hex(16),code or secrets.token_hex(8),'2026-01-01',[('ART','LOT',str(qty))]);r.confirm_receipt(db,actor,ident,1)
        return db.scalar(select(m.ReceiptLine.id).where(m.ReceiptLine.receipt_id==ident))


def hold(engine,actor,line,ctx=1,local='A101'):
    with scoped(engine,ctx) as db:return r.review_putaway(db,actor,secrets.token_hex(16),line,local,'1')


def put(engine,actor,line,ctx=1,local='A101'):
    ident=hold(engine,actor,line,ctx,local)
    with scoped(engine,ctx) as db:r.confirm_putaway(db,actor,ident)
    return ident


def prepare(engine,actor,ctx=1):
    with scoped(engine,ctx) as db:
        ident=c.save_preparation(db,actor,secrets.token_hex(16),'ENCOMENDA FICTÍCIA','CLIENTE FICTÍCIO','normal',[('LOT','1','',False)])
        d.review_preparation(db,actor,ident,1);d.confirm_preparation(db,actor,ident);return ident


def test_shared_activation_is_opt_in_one_identity_and_preserves_claim_ids(warehouse):
    engine,u=warehouse;actor=u['admin'][0]
    with Session(engine) as db:
        before=db.scalar(select(m.Location.id).where(m.Location.codigo=='A101'))
        claim=db.connection().scalar(text("SELECT context_id FROM physical_location_claims WHERE warehouse_id=1 AND code='a101'"))
        assert db.get(m.Client,3).pallet_operations is False
    ident=activate(engine,actor)
    with scoped(engine) as db:
        p.confirm_sharing(db,actor,ident)
        assert db.scalar(select(m.Location.id).where(m.Location.codigo=='A101'))==before
        assert db.get(m.Client,3).pallet_operations is False
        assert db.scalar(select(func.count()).select_from(m.PhysicalLocation))==1
        assert db.connection().scalar(text("SELECT count(*) FROM locations WHERE codigo='A101'"))==2
        assert db.connection().scalar(text("SELECT context_id FROM physical_location_claims WHERE warehouse_id=1 AND code='a101'"))==claim
        assert p.common_state(db,'A101')['state']=='free' and p.shared_location(db,'Chão') is None


def test_shared_pending_putaway_cannot_be_double_assigned_and_admin_cancel_requires_proof(warehouse):
    engine,u=warehouse;actor=u['admin'][0];operator=u['operador'][0];activate(engine,actor);logistics_fixture(engine)
    first=receive(engine,operator);second=receive(engine,operator,3)
    reserved=hold(engine,operator,first)
    with scoped(engine) as db:
        assert p.common_state(db,'A101')['state']=='pending_putaway'
        with pytest.raises(r.ReceivingError):r.review_putaway(db,operator,secrets.token_hex(16),first,'A102','1')
    with pytest.raises(r.ReceivingError):hold(engine,operator,second,3)
    with scoped(engine) as db:
        with pytest.raises(r.ReceivingError):p.cancel_putaway(db,operator,reserved,'Sem autorização',True)
        with pytest.raises(r.ReceivingError):p.cancel_putaway(db,actor,reserved,'Sem prova',False)
        p.cancel_putaway(db,actor,reserved,'Posição comprovadamente vazia',True)
        p.cancel_putaway(db,actor,reserved,'Posição comprovadamente vazia',True)
        with pytest.raises(r.ReceivingError):r.confirm_putaway(db,operator,reserved)
        assert db.get(m.Putaway,reserved).status=='cancelled'
    placed=put(engine,operator,second,3)
    with scoped(engine,3) as db:assert db.get(m.Putaway,placed).stock_id==p.common_state(db,'A101')['stock_id']
    with scoped(engine) as db:assert db.scalar(select(func.count()).select_from(m.Stock))==0


def test_shared_accounting_dispatch_does_not_release_then_physical_completion_allows_cosmopak(warehouse):
    engine,u=warehouse;actor=u['admin'][0];activate(engine,actor);logistics_fixture(engine)
    first=receive(engine,actor);put(engine,actor,first);second=receive(engine,actor,3)
    order=prepare(engine,actor)
    with scoped(engine) as db:
        assert db.scalar(select(m.Stock.quantidade))==0
        assert p.common_state(db,'A101')['state']=='awaiting_collection'
        document=db.get(m.DispatchDocument,order).pdf
    with pytest.raises(r.ReceivingError):hold(engine,actor,second,3)
    with scoped(engine) as db:
        with pytest.raises(r.ReceivingError):i.close_preparation(db,u['operador'][0],order,'Sem autorização')
        i.close_preparation(db,actor,order,'Conferência física: palete retirada, posição vazia')
        assert p.common_state(db,'A101')['state']=='free'
    put(engine,actor,second,3)
    with scoped(engine) as db:
        i.close_preparation(db,actor,order,'Repetição da prova')
        assert p.common_state(db,'A101')['context_id']==3
        assert db.get(m.DispatchDocument,order).pdf==document
    with scoped(engine,3) as db:assert db.scalar(select(m.Stock.quantidade))==1


def test_shared_concurrent_owners_exactly_one_pending_assignment(warehouse):
    engine,u=warehouse;actor=u['admin'][0];activate(engine,actor);logistics_fixture(engine)
    entries=[(1,receive(engine,actor)),(3,receive(engine,actor,3))];gate=Barrier(2)
    def assign(entry):
        ctx,line=entry;gate.wait()
        try:return ctx,hold(engine,actor,line,ctx)
        except r.ReceivingError:return ctx,None
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(assign,entries))
    assert sum(ident is not None for ctx,ident in results)==1
    for ctx,ident in results:
        if ident:
            with scoped(engine,ctx) as db:r.confirm_putaway(db,actor,ident);r.confirm_putaway(db,actor,ident)
    with engine.connect() as conn:assert conn.scalar(text("SELECT sum(quantidade) FROM stock WHERE local='A101'"))==1


@pytest.mark.parametrize('case',['capacity','inactive','pending','changed','other_owner','floor'])
def test_shared_activation_rejects_unreconciled_catalogue_and_stale_review(warehouse,case):
    engine,u=warehouse;actor=u['admin'][0]
    with scoped(engine) as db:
        if case=='changed':
            ident=p.review_sharing(db,actor,secrets.token_hex(16),'A101','Prova fictícia',True)
            db.scalar(select(m.Location).where(m.Location.codigo=='A101')).capacidade=2;db.commit()
            with pytest.raises(r.ReceivingError):p.confirm_sharing(db,actor,ident)
            return
        if case=='capacity':db.scalar(select(m.Location).where(m.Location.codigo=='A101')).capacidade=2;db.commit()
        if case=='inactive':db.scalar(select(m.Location).where(m.Location.codigo=='A101')).active=False;db.commit()
        if case=='pending':
            line=receive(engine,actor);r.review_putaway(db,actor,secrets.token_hex(16),line,'A101','1')
        with pytest.raises(r.ReceivingError):p.review_sharing(db,actor,secrets.token_hex(16),'Chão' if case=='floor' else ('FICT-ALCOBRE' if case=='other_owner' else 'A101'),'Prova fictícia',True)
        assert db.scalar(select(func.count()).select_from(m.PhysicalLocation))==0


def test_shared_inventory_missing_and_recovery_do_not_overwrite_foreign_occupancy(warehouse):
    engine,u=warehouse;actor=u['admin'][0];activate(engine,actor);logistics_fixture(engine)
    first=receive(engine,actor);put(engine,actor,first);second=receive(engine,actor,3)
    with scoped(engine) as db:
        inv=i.create_inventory(db,actor,secrets.token_hex(16),'FALTA COMPROVADA','partial',['A101']);scope=i.scopes(db,inv)[0]
        i.save_count(db,actor,scope.id,0,[],empty=True);i.submit_inventory(db,actor,inv);i.approve_inventory(db,actor,inv,'Ausência comprovada fisicamente')
        assert p.common_state(db,'A101')['state']=='free'
        code=db.scalar(select(m.Stock.carga))
        recovery=i.create_inventory(db,actor,secrets.token_hex(16),'RECUPERAR','partial',['A101']);scope=i.scopes(db,recovery)[0]
        i.save_count(db,actor,scope.id,0,[('ART','LOT',code,'1')]);i.submit_inventory(db,actor,recovery)
    put(engine,actor,second,3)
    with scoped(engine) as db:
        with pytest.raises(r.ReceivingError):i.approve_inventory(db,actor,recovery,'Não sobrepor outra ocupação')
        assert db.scalar(select(m.Stock.quantidade))==0 and p.common_state(db,'A101')['context_id']==3


def test_shared_transfer_releases_only_confirmed_source_and_keeps_both_contexts_isolated(warehouse):
    engine,u=warehouse;actor=u['admin'][0];activate(engine,actor);activate(engine,actor,'A102');logistics_fixture(engine)
    first=receive(engine,actor);put(engine,actor,first);second=receive(engine,actor,3)
    with scoped(engine) as db:
        stock=db.scalar(select(m.Stock));move=t.review_transfer(db,actor,secrets.token_hex(16),stock.id,'A102','1')
        assert p.common_state(db,'A101')['state']=='occupied'
        t.confirm_transfer(db,actor,move);t.confirm_transfer(db,actor,move)
        assert p.common_state(db,'A101')['state']=='free' and p.common_state(db,'A102')['state']=='occupied'
        assert db.scalar(select(func.sum(m.Stock.quantidade)))==1
    put(engine,actor,second,3)
    with scoped(engine,3) as db:
        assert p.common_state(db,'A102')['context_id']==1
        assert db.scalar(select(m.Stock.local).where(m.Stock.quantidade>0))=='A101'


def test_shared_sql_projection_and_events_are_protected_and_forms_enforce_admin_csrf(restricted_web):
    app,owner,runtime,role,u,tokens=restricted_web;actor=u['admin'][0]
    with TestClient(app) as client:
        client.cookies.set('wms_session',tokens['operador'][0])
        assert client.get('/settings/shared-locations').status_code==403
        client.cookies.set('wms_session',tokens['admin'][0])
        data={'context_id':'1','csrf':tokens['admin'][1],'request_key':secrets.token_hex(16),'code':'A101','reason':'Conferência física fictícia','physical_proof':'1'}
        assert client.post('/settings/shared-locations/review',data={**data,'csrf':'bad'}).status_code==403
        response=client.post('/settings/shared-locations/review',data=data,follow_redirects=False);assert response.status_code==303
        assert client.post(response.headers['location']+'/confirm',data={'csrf':tokens['admin'][1],'context_id':'1'},follow_redirects=False).status_code==303
        assert 'Ocupação' in client.get('/physical-occupancy').text
        line=receive(runtime,actor,qty=2)
        ordinary=hold(runtime,actor,line,local='A102');shared=hold(runtime,actor,line)
        assert 'cancel-physical-assignment' not in client.get(f'/putaway/{ordinary}').text
        assert 'cancel-physical-assignment' in client.get(f'/putaway/{shared}').text
    for statement in ("UPDATE physical_location_state SET state='free'",'DELETE FROM physical_location_events','UPDATE physical_locations SET capacity=2',"INSERT INTO stock(context_id,artigo,lote,carga,local,quantidade) VALUES(3,'F','F','F','A101',1)"):
        with runtime.begin() as conn,pytest.raises(DBAPIError):conn.execute(text(statement))


def test_shared_rollback_does_not_leave_pending_claim_or_partial_stock(warehouse,monkeypatch):
    engine,u=warehouse;actor=u['admin'][0];activate(engine,actor);line=receive(engine,actor)
    with scoped(engine) as db:ident=r.review_putaway(db,actor,secrets.token_hex(16),line,'A101','1')
    import iberbag.putaway_pdf as pdf
    def fail(*a):raise RuntimeError('Falha fictícia')
    with monkeypatch.context() as patch:
        patch.setattr(pdf,'render_pdf',fail)
        with scoped(engine) as db,pytest.raises(RuntimeError):r.confirm_putaway(db,actor,ident)
    with scoped(engine) as db:
        assert p.common_state(db,'A101')['state']=='pending_putaway' and db.scalar(select(func.count()).select_from(m.Stock))==0
        r.confirm_putaway(db,actor,ident);assert p.common_state(db,'A101')['state']=='occupied'


def test_shared_sqlite_never_activates_operational_sharing(tmp_path):
    from sqlalchemy import create_engine
    with Session(create_engine('sqlite:///'+str(tmp_path/'isolated.db'))) as db,pytest.raises(r.ReceivingError,match='PostgreSQL'):
        p.review_sharing(db,1,secrets.token_hex(16),'A101','Teste fictício',True)


def test_shared_migration19_and_encrypted_restore_preserve_history_and_pending_occupancy(pg_databases,tmp_path):
    from alembic import command
    from iberbag.migrations import config,REVISION
    from iberbag.schema_upgrade import apply_upgrade
    from iberbag.security import set_password
    from legacy_catalogue import seed_legacy_catalogue
    from iberbag import backup,transfer
    engine=pg_databases(restricted=True);verified=pg_databases(restricted=True)
    with engine.begin() as conn:
        cfg=config();cfg.attributes['connection']=conn;command.upgrade(cfg,'ddn_0019')
    with scoped(engine) as db:
        seed_legacy_catalogue(db);user=m.User(username='Administrador fictício',display_name='Fictício',role='admin',active=True);set_password(user,secrets.token_urlsafe(24));db.add(user);db.commit();actor=user.id
    first=receive(engine,actor);put(engine,actor,first,local='A101')
    with engine.connect() as conn:before=transfer.inventory(transfer.read_data(conn))
    folder=tmp_path/'before20';backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert backup.restore(verified,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'
    assert apply_upgrade(engine,folder)['revision']==REVISION=='ddn_0021'
    with engine.connect() as conn:assert transfer.inventory(transfer.read_data(conn))==before
    activate(engine,actor);activate(engine,actor,'A102');logistics_fixture(engine)
    order=prepare(engine,actor);other=receive(engine,actor,3);pending=hold(engine,actor,other,3,'A102')
    with scoped(engine) as db:
        assert p.common_state(db,'A101')['state']=='awaiting_collection'
        with engine.connect() as conn:
            assert transfer.archive_inventory(conn,before)!=before
            legacy=dict(before,tables={k:v for k,v in before['tables'].items() if k not in transfer.SHARED_TABLES | transfer.COSMO_TABLES})
            with pytest.raises(ValueError):transfer.archive_inventory(conn,legacy)
        original=db.get(m.DispatchDocument,order).pdf
    after=tmp_path/'after20';target=pg_databases(restricted=True)
    with engine.connect() as conn:expected=transfer.inventory(transfer.read_data(conn))
    backup.backup(engine,after,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert backup.restore(target,after,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'
    with target.connect() as conn:assert transfer.inventory(transfer.read_data(conn))==expected
    with scoped(target) as db:
        assert db.get(m.DispatchDocument,order).pdf==original
        assert p.common_state(db,'A101')['state']=='awaiting_collection'
        i.close_preparation(db,actor,order,'Recolha física confirmada após restauro')
    with scoped(target,3) as db:
        r.confirm_putaway(db,actor,pending);assert p.common_state(db,'A102')['state']=='occupied'


def test_shared_inventory_compensation_keeps_physical_position_and_original_pdf(warehouse):
    engine,u=warehouse;actor=u['admin'][0];activate(engine,actor);line=receive(engine,actor);put(engine,actor,line);order=prepare(engine,actor)
    with scoped(engine) as db:
        stock=db.scalar(select(m.Stock));document=db.get(m.DispatchDocument,order).pdf
        inv=i.create_inventory(db,actor,secrets.token_hex(16),'RESTITUIÇÃO COMPROVADA','partial',['A101']);scope=i.scopes(db,inv)[0]
        i.save_count(db,actor,scope.id,0,[('ART','LOT',stock.carga,'1')]);i.submit_inventory(db,actor,inv)
        i.approve_inventory(db,actor,inv,'Palete comprovadamente presente, documento retirado',compensate_order=order)
        assert p.common_state(db,'A101')['state']=='occupied' and db.get(m.Stock,stock.id).quantidade==1
        assert db.get(m.DispatchDocument,order).pdf==document


def test_shared_completed_order_replay_cannot_release_another_pallet(warehouse):
    engine,u=warehouse;actor=u['admin'][0];activate(engine,actor);logistics_fixture(engine)
    one=receive(engine,actor);put(engine,actor,one);order=prepare(engine,actor)
    with scoped(engine) as db:i.close_preparation(db,actor,order,'Posição efetivamente livre')
    two=receive(engine,actor,3);put(engine,actor,two,3)
    with scoped(engine) as db:
        d.confirm_preparation(db,actor,order)
        assert p.common_state(db,'A101')['context_id']==3 and p.common_state(db,'A101')['state']=='occupied'


def test_shared_restored_function_acl_cannot_use_temporary_writer_trigger(restricted_web):
    app,owner,runtime,role,u,tokens=restricted_web;actor=u['admin'][0];activate(owner,actor);line=receive(owner,actor)
    from psycopg import sql
    # pg_restore --no-privileges can restore default PUBLIC EXECUTE. Source OID
    # verification must protect the definer even under that deliberately weaker ACL.
    with owner.begin() as conn:
        conn.exec_driver_sql(sql.SQL('GRANT EXECUTE ON FUNCTION ddn_shared_event() TO {}').format(sql.Identifier(role)).as_string())
    with scoped(runtime) as db:
        draft=m.Putaway(request_key=secrets.token_hex(16),line_id=line,local='A101',quantidade=1,created_by=actor)
        db.add(draft);db.flush()
        db.execute(text('CREATE TEMP TABLE forged_physical_events (LIKE public.physical_location_events INCLUDING DEFAULTS)'))
        db.execute(text('CREATE TRIGGER forged_writer BEFORE INSERT ON pg_temp.forged_physical_events FOR EACH ROW EXECUTE FUNCTION public.ddn_shared_event()'))
        with pytest.raises(DBAPIError,match='Untrusted physical event trigger'):
            db.execute(text("INSERT INTO pg_temp.forged_physical_events(context_id,physical_id,kind,ref_id,actor_id,reason,created_at) VALUES(1,1,'putaway_hold',:ref,:actor,'Fictício',CURRENT_TIMESTAMP)"),{'ref':draft.id,'actor':actor})
        db.rollback()
        assert p.common_state(db,'A101')['state']=='free'
        assert db.scalar(select(func.count()).select_from(m.PhysicalLocationEvent))==1


def test_shared_runtime_concurrent_assignment_and_physical_release_rollback(restricted_web):
    app,owner,runtime,role,u,tokens=restricted_web;actor=u['admin'][0];activate(runtime,actor);logistics_fixture(owner)
    source={ctx:receive(owner,actor,ctx) for ctx in (1,3)};gate=Barrier(2)
    def reserve(ctx):
        gate.wait()
        try:return ctx,hold(runtime,actor,source[ctx],ctx)
        except r.ReceivingError:return ctx,None
    with ThreadPoolExecutor(2) as pool:rows=list(pool.map(reserve,(1,3)))
    winner,ident=next((ctx,ident) for ctx,ident in rows if ident is not None);loser=3 if winner==1 else 1
    assert sum(ident is not None for ctx,ident in rows)==1
    with scoped(runtime,winner) as db:r.confirm_putaway(db,actor,ident)
    order=prepare(runtime,actor,winner)
    from sqlalchemy import event
    def fail(session):raise RuntimeError('Falha fictícia antes do commit')
    with scoped(runtime,winner) as db:
        event.listen(db,'before_commit',fail)
        with pytest.raises(RuntimeError):i.close_preparation(db,actor,order,'Prova física fictícia de recolha')
    with scoped(runtime,winner) as db:
        assert p.common_state(db,'A101')['state']=='awaiting_collection'
        assert db.get(m.PreparationCompletion,order) is None
    with pytest.raises(r.ReceivingError):hold(runtime,actor,source[loser],loser)
    with scoped(runtime,winner) as db:i.close_preparation(db,actor,order,'Recolha física comprovada')
    put(runtime,actor,source[loser],loser)
    with scoped(runtime,loser) as db:assert p.common_state(db,'A101')['context_id']==loser


def test_shared_snapshot_rejects_divergent_projection_or_double_physical_stock(warehouse):
    from iberbag.transfer import read_data,fingerprint
    import copy
    engine,u=warehouse;actor=u['admin'][0];activate(engine,actor);line=receive(engine,actor);put(engine,actor,line)
    with engine.connect() as conn:data=read_data(conn)
    before=fingerprint(data)
    for bad in ('projection','double_stock','capacity'):
        changed=copy.deepcopy(data)
        if bad=='projection':changed['physical_location_state'][0]['state']='free'
        elif bad=='capacity':changed['physical_locations'][0]['capacity']=2
        else:
            row=dict(changed['stock'][0]);row.update(id=row['id']+1000,context_id=3);changed['stock'].append(row)
        with pytest.raises(ValueError):p.validate_shared_snapshot(changed)
    assert fingerprint(data)==before


def test_shared_seed_identities_use_resolved_contexts_and_reject_name_impostors(warehouse):
    """Seed PKs identify owners; names and context sequence numbers do not."""
    from sqlalchemy.exc import IntegrityError
    engine,u=warehouse;actor=u['admin'][0]
    for statement,state in (("UPDATE clients SET id=300 WHERE id=3",'23503'),
                            ("UPDATE warehouses SET id=100 WHERE id=1",'23503'),
                            ("UPDATE storage_contexts SET client_id=2 WHERE id=3",'23514')):
        with engine.begin() as conn,pytest.raises(IntegrityError) as blocked:
            conn.execute(text(statement))
        assert blocked.value.orig.sqlstate==state
    with engine.begin() as conn:
        # Only unused synthetic context metadata is changed by this test, never
        # stock/history. A context PK must not be confused with its owner PK.
        conn.execute(text('UPDATE storage_contexts SET id=30 WHERE id=3'))
        conn.execute(text("UPDATE clients SET name='Proprietário fictício C' WHERE id=3"))
        conn.execute(text("UPDATE warehouses SET name='Armazém fictício O' WHERE id=1"))
        conn.execute(text("INSERT INTO clients(id,name,active,pallet_operations) VALUES(60,'Cosmopak',true,false)"))
        conn.execute(text("INSERT INTO storage_contexts(id,client_id,warehouse_id,area,active) VALUES(60,60,1,'Imitação fictícia',true),(61,3,2,'Outro armazém',true)"))
    activate(engine,actor)
    with engine.connect() as conn:
        assert set(conn.scalars(text('SELECT context_id FROM physical_location_access')))=={1,30}
        assert conn.scalar(text("SELECT count(*) FROM locations WHERE codigo='A101' AND context_id=30"))==1
    for ctx in (60,61):
        with scoped(engine,ctx) as db:
            assert p.physical_statuses(db)=={}
        with engine.begin() as conn,pytest.raises(IntegrityError):
            conn.execute(text('INSERT INTO physical_location_access(physical_id,context_id) SELECT id,:ctx FROM physical_locations'),{'ctx':ctx})
