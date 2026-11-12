"""Fictitious guide units; pairs never become two stock units or mix with normal."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from io import BytesIO
import hashlib
import secrets
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from pypdf import PdfReader

from iberbag import receiving as r, dispatching as d, commercial as c, inventorying as i, transferring as t
from iberbag import database as m, backup, transfer
from iberbag.stacked import WARNING, flag
from test_receiving import warehouse, receive
from test_transfer import pg_databases, docker_client
from test_security_regressions import restricted_web
from conftest import BACKUP_PASSWORD


def place(engine, actor, line, qty, stacked=False, local='Chão'):
    with Session(engine) as db:
        ident=r.review_putaway(db,actor,secrets.token_hex(16),line,local,str(qty),stacked)
        r.confirm_putaway(db,actor,ident)
        return ident


def prep(engine, actor, qty=1, stacked=False, *, legacy=False):
    with Session(engine) as db:
        if legacy:return d.save_order(db,actor,secrets.token_hex(16),'TESTE', [('ART','LOT',str(qty),stacked)])
        return c.save_preparation(db,actor,secrets.token_hex(16),'TESTE','Cliente fictício','normal',[('LOT',str(qty),'',stacked)])


def text_pdf(content):
    return ' '.join(' '.join(page.extract_text() for page in PdfReader(BytesIO(content)).pages).split())


def test_stacked_pure_flags_pdf_and_sqlite_write_guard(tmp_path):
    from iberbag.putaway_pdf import render_pdf
    from types import SimpleNamespace as NS
    from datetime import datetime
    from sqlalchemy import create_engine
    for value in ('false','yes',2,-1,1.5):
        with pytest.raises(r.ReceivingError):flag(value)
    assert flag(False) is False and flag('1') is True
    content=render_pdf(NS(id=1,local='Chão',quantidade=4,stacked=True,arranger='Fictício',confirmed_at=datetime(2026,1,1)),
        NS(artigo='ART',lote='LOT'),NS(code='FICT',received_on=datetime(2026,1,1)))
    assert WARNING in text_pdf(content) and '/MediaBox' in str(PdfReader(BytesIO(content)).pages[0])
    engine=create_engine('sqlite:///'+str(tmp_path/'isolated.db'))
    with Session(engine) as db, pytest.raises(r.ReceivingError,match='PostgreSQL'):
        r.review_putaway(db,1,'a'*32,1,'Chão','1',True)


def test_stacked_partial_putaway_separate_balances_documents_and_idempotence(warehouse):
    engine,u=warehouse;actor=u['operador'][0];_,line=receive(engine,actor,10)
    normal=place(engine,actor,line,6);stacked=place(engine,actor,line,4,True)
    with Session(engine) as db:
        r.confirm_putaway(db,actor,stacked)
        stocks=db.scalars(select(m.Stock).order_by(m.Stock.stacked)).all()
        assert [(s.stacked,s.quantidade) for s in stocks]==[(False,6),(True,4)]
        assert i.snapshot(db,'Chão')==[dict(artigo=s.artigo,lote=s.lote,carga=s.carga,quantidade=s.quantidade,stacked=s.stacked) for s in stocks]
        assert r.remaining(db,db.get(m.ReceiptLine,line))==0
        p=db.get(m.Putaway,stacked)
        assert p.stock_id==stocks[1].id and p.arranger=='operador'
        assert hashlib.sha256(p.document_pdf).hexdigest()==p.document_sha256
        assert WARNING in text_pdf(p.document_pdf)
        assert WARNING not in text_pdf(db.get(m.Putaway,normal).document_pdf)
        with pytest.raises(DBAPIError):
            db.execute(text('UPDATE putaways SET stacked=false WHERE id=:id'),{'id':stacked});db.commit()


def test_stacked_floor_only_and_idempotent_key_payload(warehouse):
    engine,u=warehouse;actor=u['operador'][0];_,line=receive(engine,actor,4)
    with Session(engine) as db:
        for local in ('A101','A102','(não indicado)'):
            with pytest.raises(r.ReceivingError):r.review_putaway(db,actor,secrets.token_hex(16),line,local,'1',True)
        with pytest.raises(r.ReceivingError,match='inteira'):
            r.review_putaway(db,actor,secrets.token_hex(16),line,'Chão','1.5',True)
        key=secrets.token_hex(16);ident=r.review_putaway(db,actor,key,line,'Chão','1',True)
        assert r.review_putaway(db,actor,key,line,'Chão','1',True)==ident
        with pytest.raises(r.ReceivingError):r.review_putaway(db,actor,key,line,'Chão','1',False)
        assert db.scalar(select(func.count()).select_from(m.Stock))==0


def test_stacked_fifo_each_type_unknown_dates_and_no_fallback(warehouse):
    engine,u=warehouse;actor=u['admin'][0]
    for date,code in [('2026-01-01','OLDER'),('2026-01-02','NEWER')]:
        with Session(engine) as db:
            ident=r.save_receipt(db,actor,secrets.token_hex(16),code,date,[('ART','LOT','4')]);r.confirm_receipt(db,actor,ident,1)
            line=db.scalar(select(m.ReceiptLine.id).where(m.ReceiptLine.receipt_id==ident))
        place(engine,actor,line,2);place(engine,actor,line,2,True)
    for stacked in (False,True):
        ident=prep(engine,actor,3,stacked)
        with Session(engine) as db:
            d.review_preparation(db,actor,ident,1)
            plan=d.picks(db,ident)
            assert [(p.carga,p.quantidade) for p in plan]==[('OLDER',2),('NEWER',1)]
            assert all(db.get(m.Stock,p.stock_id).stacked is stacked for p in plan)
            d.confirm_preparation(db,actor,ident);d.confirm_preparation(db,actor,ident)
            assert d.availability(db,'ART','LOT',stacked)['total']==1
            doc=db.get(m.DispatchDocument,ident)
            assert (WARNING in text_pdf(doc.pdf)) is stacked
            assert db.scalar(select(func.count()).select_from(m.DispatchDocument).where(m.DispatchDocument.order_id==ident))==1
    shortage=prep(engine,actor,2,True)
    with Session(engine) as db:
        with pytest.raises(r.ReceivingError,match='Stock insuficiente'):d.review_preparation(db,actor,shortage,1)
        assert not d.picks(db,shortage) and db.get(m.DispatchOrder,shortage).reviewed_at is None
        db.execute(text("UPDATE cargas SET entrada=NULL WHERE codigo='NEWER'"));db.commit()
        normal=prep(engine,actor,1,False)
        with pytest.raises(r.ReceivingError,match='FIFO bloqueado'):d.review_preparation(db,actor,normal,1)


def test_stacked_shortage_changed_after_review_rollback_and_concurrent_preparations(warehouse):
    engine,u=warehouse;actor=u['admin'][0];_,line=receive(engine,actor,2);place(engine,actor,line,2,True)
    ids=[prep(engine,actor,2,True) for _ in range(2)]
    for ident in ids:
        with Session(engine) as db:d.review_preparation(db,actor,ident,1)
    barrier=Barrier(2)
    def confirm(ident):
        barrier.wait()
        with Session(engine) as db:
            try:d.confirm_preparation(db,actor,ident);return True
            except r.ReceivingError:return False
    with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(confirm,ids))==[False,True]
    with Session(engine) as db:
        assert db.scalar(select(func.sum(m.Stock.quantidade)))==0
        assert db.scalar(select(func.count()).select_from(m.DispatchDocument))==1
        assert db.scalar(select(func.count()).select_from(m.CustomerPending))==0


def test_stacked_pdf_failure_rolls_back_stock_confirmation_and_archive(warehouse,monkeypatch):
    engine,u=warehouse;actor=u['admin'][0];_,line=receive(engine,actor,3)
    with Session(engine) as db:ident=r.review_putaway(db,actor,secrets.token_hex(16),line,'Chão','3',True)
    import iberbag.putaway_pdf as pdf
    with monkeypatch.context() as patch:
        def fail(*args):raise RuntimeError('Fictitious PDF failure')
        patch.setattr(pdf,'render_pdf',fail)
        with Session(engine) as db,pytest.raises(RuntimeError):r.confirm_putaway(db,actor,ident)
    with Session(engine) as db:
        assert not db.get(m.Putaway,ident).document_pdf and db.get(m.Putaway,ident).status=='draft'
        assert db.scalar(select(func.count()).select_from(m.Stock))==0
        r.confirm_putaway(db,actor,ident)
    order=prep(engine,actor,2,True)
    with Session(engine) as db:d.review_preparation(db,actor,order,1)
    import iberbag.commercial_pdf as cp
    monkeypatch.setattr(cp,'render_preparation',fail)
    with Session(engine) as db,pytest.raises(RuntimeError):d.confirm_preparation(db,actor,order)
    with Session(engine) as db:
        assert db.scalar(select(m.Stock.quantidade))==3 and db.get(m.DispatchOrder,order).status=='draft'
        assert db.get(m.DispatchDocument,order) is None


def test_stacked_inventory_preserves_type_admin_adjustment_and_transfer_guards(warehouse):
    engine,u=warehouse;actor=u['admin'][0];_,line=receive(engine,actor,5);place(engine,actor,line,2);place(engine,actor,line,3,True)
    with Session(engine) as db:
        stock=db.scalar(select(m.Stock).where(m.Stock.stacked.is_(True)))
        with pytest.raises(r.ReceivingError):t.review_transfer(db,actor,secrets.token_hex(16),stock.id,'A101','1')
        inv=i.create_inventory(db,actor,secrets.token_hex(16),'TESTE','partial',['Chão']);scope=i.scopes(db,inv)[0]
        assert all(row['after'] is None for row in i.differences(db,inv))
        rows=[('ART','LOT',stock.carga,'2',False),('ART','LOT',stock.carga,'2',True)]
        i.save_count(db,actor,scope.id,0,rows);i.submit_inventory(db,actor,inv)
        with pytest.raises(r.ReceivingError):i.approve_inventory(db,u['operador'][0],inv,'Fictício')
        i.approve_inventory(db,actor,inv,'Contagem física comprovada');i.approve_inventory(db,actor,inv,'Contagem física comprovada')
        adjustment=db.scalar(select(m.StockAdjustment))
        assert adjustment.stacked and (adjustment.before,adjustment.after,adjustment.delta)==(3,2,-1)
        assert sorted((s.stacked,s.quantidade) for s in db.scalars(select(m.Stock)))==[(False,2),(True,2)]
        # Normal transfer leaves pairs in place; target always remains normal.
        normal=db.scalar(select(m.Stock).where(m.Stock.stacked.is_(False)))
        move=t.review_transfer(db,actor,secrets.token_hex(16),normal.id,'A101','1');t.confirm_transfer(db,actor,move)
        assert not db.get(m.StockTransfer,move).stacked
        assert db.scalar(select(m.Stock.quantidade).where(m.Stock.stacked.is_(True)))==2


def test_stacked_inventory_stale_reservation_and_compensation_type(warehouse):
    engine,u=warehouse;actor=u['admin'][0];_,line=receive(engine,actor,3);place(engine,actor,line,3,True)
    reserved=prep(engine,actor,1,True,legacy=True)
    with Session(engine) as db:
        d.review_order(db,actor,reserved,1)
        inv=i.create_inventory(db,actor,secrets.token_hex(16),'RESERVA','partial',['Chão']);scope=i.scopes(db,inv)[0]
        code=db.scalar(select(m.Stock.carga))
        i.save_count(db,actor,scope.id,0,[('ART','LOT',code,'2',True)]);i.submit_inventory(db,actor,inv)
        with pytest.raises(r.ReceivingError,match='reserva'):i.approve_inventory(db,actor,inv,'Não pode')
        d.cancel_order(db,actor,reserved,'Devolução comprovada',True)
        with pytest.raises(r.ReceivingError,match='movimentos'):i.approve_inventory(db,actor,inv,'Desatualizada')
    order=prep(engine,actor,1,True)
    with Session(engine) as db:
        d.review_preparation(db,actor,order,1);d.confirm_preparation(db,actor,order)
        inv=i.create_inventory(db,actor,secrets.token_hex(16),'COMPENSAR','partial',['Chão']);scope=i.scopes(db,inv)[0]
        i.save_count(db,actor,scope.id,0,[('ART','LOT',code,'3',True)]);i.submit_inventory(db,actor,inv)
        i.approve_inventory(db,actor,inv,'Conjunto restituído comprovado',compensate_order=order)
        assert db.scalar(select(m.Stock.quantidade).where(m.Stock.stacked.is_(True)))==3
        assert db.scalar(select(func.count()).select_from(m.PreparationCompensation))==1


def test_stacked_restricted_runtime_sql_guards_context_permissions_and_csrf(restricted_web):
    app,owner,runtime,role,u,tokens=restricted_web;actor=u['operador'][0]
    _,line=receive(owner,actor,4)
    with Session(runtime) as db:
        ident=r.review_putaway(db,actor,secrets.token_hex(16),line,'Chão','2',True);r.confirm_putaway(db,actor,ident)
    with TestClient(app) as client:
        client.cookies.set('wms_session',tokens['operador'][0])
        base={'csrf':tokens['operador'][1],'context_id':'1','request_key':secrets.token_hex(16),'line_id':str(line),'local':'Chão','quantidade':'1','stacked':'1'}
        assert client.post('/putaway/review',data={**base,'csrf':'invalid'}).status_code==403
        assert client.post('/putaway/review',data={**base,'context_id':'2'}).status_code==409
        response=client.post('/putaway/review',data=base,follow_redirects=False);assert response.status_code==303
        assert client.get(f'/putaway/{ident}/document.pdf').content==client.get(f'/putaway/{ident}/document.pdf').content
        assert 'Sobrepostas' in client.get('/stock').text
        form=client.get('/dispatches/new').text
        assert 'checked' not in re.search(r'<input[^>]+name="stacked_0"[^>]*>',form).group()
        assert 'name="stacked"' not in client.get('/receipts/new').text
        saved=client.post('/dispatches/save',data={'csrf':tokens['operador'][1],'context_id':'1','request_key':secrets.token_hex(16),'customer':'Fictício','channel':'normal','lote_0':'LOT','quantidade_0':'1','stacked_0':'1'},follow_redirects=False)
        assert saved.status_code==303
        outgoing=int(saved.headers['location'].split('/')[-1])
        valid={'csrf':tokens['operador'][1],'context_id':'1','version':'1'}
        assert client.post(f'/dispatches/{outgoing}/review',data=valid,follow_redirects=False).status_code==303
        assert client.post(f'/dispatches/{outgoing}/confirm',data=valid,follow_redirects=False).status_code==303
        assert client.get(f'/dispatches/{outgoing}/document.pdf').content==client.get(f'/dispatches/{outgoing}/document.pdf').content
        with Session(runtime) as db:assert db.scalar(select(m.Stock.quantidade).where(m.Stock.stacked.is_(True)))==1
        order=int(client.post('/dispatches/save',data={'csrf':tokens['operador'][1],'context_id':'1','request_key':secrets.token_hex(16),'customer':'Fictício','channel':'normal','lote_0':'LOT','quantidade_0':'1'},follow_redirects=False).headers['location'].split('/')[-1])
        with Session(runtime) as db:assert not d.lines(db,order)[0].stacked and db.get(m.DispatchOrder,order).typed_selection
        client.cookies.set('wms_session',tokens['admin'][0])
        response=client.post('/context',data={'csrf':tokens['admin'][1],'selected_context':'2'},follow_redirects=False)
        assert response.status_code==303
        assert client.get(f'/putaway/{ident}/document.pdf').status_code==404
        # Test server context isolation directly too; no cross-client stock exposure.
    with Session(runtime) as db:
        db.info['ddn_context']=2
        assert db.scalar(select(func.count()).select_from(m.Stock))==0
    with Session(owner) as db:
        ctx=m.StorageContext(client_id=1,warehouse_id=2,area='Fictícia',active=True);db.add(ctx);db.commit();other_wh=ctx.id
    with Session(runtime,info={'ddn_context':other_wh}) as db:
        with pytest.raises(r.ReceivingError,match='Iberbag/Ovar'):
            r.review_putaway(db,actor,secrets.token_hex(16),line,'Chão','1',True)
        assert db.scalar(select(func.count()).select_from(m.Stock))==0
    for sql in ("UPDATE stock SET stacked=false WHERE stacked", "UPDATE stock SET local='A101' WHERE stacked", "INSERT INTO stock(context_id,artigo,lote,carga,local,quantidade,stacked) VALUES(2,'A','L','C','Chão',1,true)"):
        with runtime.begin() as conn,pytest.raises(DBAPIError):conn.execute(text(sql))


def test_stacked_restricted_runtime_rejects_cross_type_pick_and_adjustment(restricted_web):
    app,owner,runtime,role,u,tokens=restricted_web;actor=u['admin'][0];_,line=receive(owner,actor,2);place(owner,actor,line,2,True)
    order=prep(runtime,actor,1,False)
    with Session(runtime) as db:
        dl=d.lines(db,order)[0];stock=db.scalar(select(m.Stock));load=db.scalar(select(m.Carga))
        with pytest.raises(DBAPIError):
            db.add(m.DispatchPick(line_id=dl.id,stock_id=stock.id,carga=stock.carga,local=stock.local,entrada=load.entrada,quantidade=1));db.commit()
    with Session(runtime) as db:
        stock=db.scalar(select(m.Stock));inv=i.create_inventory(db,actor,secrets.token_hex(16),'FORJADO','partial',['Chão'])
        with pytest.raises(DBAPIError):
            db.add(m.StockAdjustment(inventory_id=inv,stock_id=stock.id,artigo=stock.artigo,lote=stock.lote,carga=stock.carga,
                local=stock.local,stacked=False,before=2,after=3,delta=1,reason='Forjado',actor='admin'));db.commit()
    with runtime.begin() as conn,pytest.raises(DBAPIError):
        conn.execute(text('UPDATE dispatch_orders SET typed_selection=false WHERE id=:id'),{'id':order})
    with Session(runtime) as db:
        forged=m.DispatchOrder(request_key=secrets.token_hex(16),payload_hash='a'*64,reference='FORJADO',created_by=actor,office=True)
        db.add(forged);db.flush()
        with pytest.raises(DBAPIError):
            db.add(m.DispatchLine(order_id=forged.id,artigo='ART',lote='LOT',quantidade=1,stacked=True));db.commit()
    with Session(runtime) as db:
        forged=m.DispatchOrder(request_key=secrets.token_hex(16),payload_hash='b'*64,reference='TEMP FICTÍCIO',created_by=actor,office=True)
        db.add(forged);db.flush();ident=forged.id
        db.execute(text('CREATE TEMP TABLE dispatch_orders(id integer,context_id integer,typed_selection boolean,status text)'))
        db.execute(text("INSERT INTO pg_temp.dispatch_orders VALUES(:id,1,true,'draft')"),{'id':ident})
        with pytest.raises(DBAPIError):
            db.add(m.DispatchLine(order_id=ident,artigo='ART',lote='LOT',quantidade=1,stacked=True));db.commit()


def test_stacked_upgrade18_encrypted_backup_restore_and_historical_documents(pg_databases,tmp_path):
    from alembic import command
    from iberbag.migrations import config,REVISION
    from iberbag.schema_upgrade import apply_upgrade
    from iberbag.security import set_password
    from legacy_catalogue import seed_legacy_catalogue
    engine=pg_databases(restricted=True);restored=pg_databases(restricted=True)
    with engine.begin() as conn:
        cfg=config();cfg.attributes['connection']=conn;command.upgrade(cfg,'ddn_0018')
    with Session(engine) as db:
        seed_legacy_catalogue(db);db.commit()
    # Seed old-schema rows explicitly: no new application ORM or projected identity.
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO stock(context_id,artigo,lote,carga,local,quantidade) VALUES(1,'ART','LOT','OLD','Chão',7)"))
    with engine.connect() as conn:before=transfer.inventory(transfer.read_data(conn))
    folder=tmp_path/'before19';backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert backup.restore(restored,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'
    assert apply_upgrade(engine,folder)['revision']==REVISION=='ddn_0021'
    with engine.connect() as conn:
        assert transfer.inventory(transfer.read_data(conn))==before
        assert conn.scalar(text('SELECT quantidade FROM stock'))==7 and conn.scalar(text('SELECT stacked FROM stock')) is False
    with Session(engine) as db:
        user=m.User(username='Fictício',display_name='Fictício',role='admin',active=True);set_password(user,secrets.token_urlsafe(24));db.add(user);db.commit();actor=user.id
    _,line=receive(engine,actor,3,'NEW');placed=place(engine,actor,line,2,True)
    order=prep(engine,actor,1,True)
    with Session(engine) as db:d.review_preparation(db,actor,order,1);d.confirm_preparation(db,actor,order)
    after=tmp_path/'after19';target=pg_databases(restricted=True)
    with engine.connect() as conn:expected=transfer.inventory(transfer.read_data(conn))
    backup.backup(engine,after,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert backup.restore(target,after,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'
    with target.connect() as conn:assert transfer.inventory(transfer.read_data(conn))==expected
    with Session(engine) as left,Session(target) as right:
        assert left.get(m.Putaway,placed).document_pdf==right.get(m.Putaway,placed).document_pdf
        assert left.get(m.DispatchDocument,order).pdf==right.get(m.DispatchDocument,order).pdf


def test_stacked_concurrent_mixed_putaway_never_exceeds_guide_units(warehouse):
    engine,u=warehouse;actor=u['admin'][0];_,line=receive(engine,actor,3)
    with Session(engine) as db:
        ids=[r.review_putaway(db,actor,secrets.token_hex(16),line,'Chão','2',kind) for kind in (False,True)]
    gate=Barrier(2)
    def confirm(ident):
        gate.wait()
        with Session(engine) as db:
            try:r.confirm_putaway(db,actor,ident);return True
            except r.ReceivingError:return False
    with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(confirm,ids))==[False,True]
    with Session(engine) as db:
        assert db.scalar(select(func.sum(m.Stock.quantidade)))==2
        assert r.remaining(db,db.get(m.ReceiptLine,line))==1


def test_stacked_mixed_lines_and_typed_reservations_do_not_mix(warehouse):
    engine,u=warehouse;actor=u['admin'][0];_,line=receive(engine,actor,6);place(engine,actor,line,3);place(engine,actor,line,3,True)
    reserved=prep(engine,actor,1,False,legacy=True)
    with Session(engine) as db:d.review_order(db,actor,reserved,1)
    stacked=prep(engine,actor,1,True)
    with Session(engine) as db:
        d.review_preparation(db,actor,stacked,1);d.confirm_preparation(db,actor,stacked)
        assert d.availability(db,'ART','LOT',False)==dict(total=3,reserved=1,free=2)
        assert d.availability(db,'ART','LOT',True)==dict(total=2,reserved=0,free=2)
        d.cancel_order(db,actor,reserved,'Sem recolha, libertação comprovada',True)
        mixed=c.save_preparation(db,actor,secrets.token_hex(16),'MISTA','Fictício','normal',[('LOT','1','',False),('LOT','1','',True)])
        d.review_preparation(db,actor,mixed,1);d.confirm_preparation(db,actor,mixed)
        assert sorted((s.stacked,s.quantidade) for s in db.scalars(select(m.Stock)))==[(False,2),(True,1)]
        assert {l.stacked for l in d.lines(db,mixed)}=={False,True}
        before=db.get(m.DispatchDocument,mixed).pdf
        d.confirm_preparation(db,actor,mixed)
        assert before==db.get(m.DispatchDocument,mixed).pdf


def test_stacked_new_office_review_blocks_legacy_shortage_without_losing_pendentes(warehouse):
    engine,u=warehouse;actor=u['admin'][0];_,line=receive(engine,actor,2);place(engine,actor,line,2,True)
    with Session(engine) as db:
        # An old normal draft may remain in the database, but revisiting it in the
        # current office interface must apply strict type selection too.
        legacy=c.save_preparation(db,actor,secrets.token_hex(16),'LEGADO','Fictício','normal',[('LOT','1','')])
        with pytest.raises(r.ReceivingError,match='Stock insuficiente'):
            d.review_preparation(db,actor,legacy,1,typed_selection=True)
        assert not d.picks(db,legacy) and db.scalar(select(func.count()).select_from(m.CustomerPending))==0
        assert db.scalar(select(m.Stock.quantidade))==2


def test_stacked_inventory_cannot_invent_pair_type_from_normal_stock(warehouse):
    engine,u=warehouse;actor=u['admin'][0];_,line=receive(engine,actor,2);place(engine,actor,line,2)
    with Session(engine) as db:
        stock=db.scalar(select(m.Stock));inv=i.create_inventory(db,actor,secrets.token_hex(16),'TIPO ERRADO','partial',['Chão']);scope=i.scopes(db,inv)[0]
        i.save_count(db,actor,scope.id,0,[('ART','LOT',stock.carga,'2',True)]);i.submit_inventory(db,actor,inv)
        with pytest.raises(r.ReceivingError,match='[Cc]onver'):i.approve_inventory(db,actor,inv,'Tipo sem comprovação')
        assert [(s.stacked,s.quantidade) for s in db.scalars(select(m.Stock))]==[(False,2)]
        assert db.scalar(select(func.count()).select_from(m.StockAdjustment))==0


@pytest.mark.parametrize('mixed', [False, True])
def test_stacked_inventory_never_converts_pairs_into_normal_units(warehouse,mixed):
    engine,u=warehouse;actor=u['admin'][0];_,line=receive(engine,actor,4 if mixed else 2)
    if mixed:place(engine,actor,line,2)
    place(engine,actor,line,2,True)
    with Session(engine) as db:
        code=db.scalar(select(m.Stock.carga));inv=i.create_inventory(db,actor,secrets.token_hex(16),'SEM CONVERSÃO','partial',['Chão']);scope=i.scopes(db,inv)[0]
        rows=[('ART','LOT',code,'3' if mixed else '2',False)]
        if mixed:rows.append(('ART','LOT',code,'1',True))
        i.save_count(db,actor,scope.id,0,rows);i.submit_inventory(db,actor,inv)
        with pytest.raises(r.ReceivingError,match='[Tt]ipo|[Cc]onver'):
            i.approve_inventory(db,actor,inv,'Requer reconciliação específica, sem conversão')
        assert sorted((s.stacked,s.quantidade) for s in db.scalars(select(m.Stock)))==([(False,2),(True,2)] if mixed else [(True,2)])
        assert db.scalar(select(func.count()).select_from(m.StockAdjustment))==0


def test_stacked_restricted_runtime_forbids_balanced_type_conversion(restricted_web):
    app,owner,runtime,role,u,tokens=restricted_web;actor=u['admin'][0];_,line=receive(owner,actor,4)
    place(owner,actor,line,2);place(owner,actor,line,2,True)
    with Session(runtime) as db:
        inv=i.create_inventory(db,actor,secrets.token_hex(16),'GUARDA SQL','partial',['Chão'])
        stocks=db.scalars(select(m.Stock).order_by(m.Stock.stacked)).all()
        with pytest.raises(DBAPIError,match='cannot convert stock types'):
            for stock,after in zip(stocks,[3,1]):
                stock.quantidade=after
                db.add(m.StockAdjustment(inventory_id=inv,stock_id=stock.id,artigo=stock.artigo,lote=stock.lote,carga=stock.carga,
                    local=stock.local,stacked=stock.stacked,before=2,after=after,delta=after-2,reason='Tentativa fictícia',actor='admin'))
            db.commit()
        db.rollback()
        assert [(s.stacked,s.quantidade) for s in db.scalars(select(m.Stock).order_by(m.Stock.stacked))]==[(False,2),(True,2)]
        assert db.scalar(select(func.count()).select_from(m.StockAdjustment))==0


def test_stacked_independent_positive_adjustment_missing_and_proven_pair_recovery(warehouse):
    engine,u=warehouse;actor=u['admin'][0];_,line=receive(engine,actor,4);place(engine,actor,line,2);place(engine,actor,line,2,True)
    with Session(engine) as db:
        code=db.scalar(select(m.Stock.carga))
        for rows,expected in [([('ART','LOT',code,'3',False),('ART','LOT',code,'2',True)],[(False,3),(True,2)]),
                              ([],[(False,0),(True,0)]),([('ART','LOT',code,'2',True)],[(False,0),(True,2)])]:
            inv=i.create_inventory(db,actor,secrets.token_hex(16),'PROVA INDEPENDENTE','partial',['Chão']);scope=i.scopes(db,inv)[0]
            i.save_count(db,actor,scope.id,0,rows,note='Conferência física fictícia, por tipo original',empty=not rows)
            i.submit_inventory(db,actor,inv);i.approve_inventory(db,actor,inv,'Quantidade comprovada no tipo original, sem conversão')
            assert [(s.stacked,s.quantidade) for s in db.scalars(select(m.Stock).order_by(m.Stock.stacked))]==expected
