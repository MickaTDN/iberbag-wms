"""Cross-stage Alcobre office exercises; only synthetic PDFs and disposable PG17."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from hashlib import sha256
from io import BytesIO
import json,secrets,re
from threading import Barrier
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import select,func,text
from iberbag import alcobre as incoming,alcobre_dispatch as outgoing,alcobre_inventory as inventory,database as m,backup,transfer,receiving
from iberbag.privileges import validate_runtime
from iberbag.warehouse_settings import add_location,occupied
from iberbag.alcobre_inventory_pdf import render_pdf
from test_security_regressions import restricted_web
from test_receiving import warehouse,receive as iberbag_receive,draft as iberbag_putaway
from test_ddn_contexts import scoped
from test_transfer import pg_databases,docker_client
from test_alcobre import positions,draft,review as receipt_review,confirm as receive,row,pdf as receipt_pdf
from test_alcobre_dispatch import source,review as dispatch_review,prepare,confirm as ship,cancel,resolution,snapshot
from test_alcobre_inventory import create as new_inventory,record,review as inventory_review,confirm as approve
from conftest import BACKUP_PASSWORD


def foreign_snapshot(engine):
    with engine.connect() as conn:
        return {name:[r for r in data if r.get('context_id') not in (None,5) or (r.get('context_id') is None and r.get('client_id') not in (None,5)) or name in ('clients','warehouses')] for name,data in transfer.read_data(conn).items()}


def seed_other_customers(owner,engine,actor):
    _,line=iberbag_receive(engine,actor,3,code='FICT-IBERBAG-IN')
    placed=iberbag_putaway(engine,actor,line,qty=3)
    with scoped(engine,1) as db:receiving.confirm_putaway(db,actor,placed)
    with scoped(engine,2) as db:add_location(db,'admin','Chão')
    from test_valmet import entry,row as valmet_row
    entry(engine,actor,[valmet_row('caixas','000101',local='Chão',content_total=None,items=[('FICT-ART','FICT-LOT',None)])])
    from test_nkt_receiving import stage,confirm,manual_rows
    from test_nkt_loading import prepare as nkt_prepare,reserve as nkt_reserve,ship as nkt_ship
    confirm(engine,actor,stage(engine,actor,manual_rows('000101','000102','FICT-NKT-IN','Material fictício')))
    nkt=nkt_prepare(engine,actor,['000101','000102']);nkt_reserve(engine,actor,nkt);nkt_ship(engine,actor,nkt,['000101'],gt='GT-FICT-ISOLAMENTO')
    # Fixture-only historical Cosmopak stock; no new Cosmopak operational flow.
    with scoped(owner,3) as db:
        db.add(m.Location(codigo='Chão',capacidade=None,active=True));db.flush();db.add(m.Stock(artigo='FICT-ART',lote='FICT-LOT',carga='FICT-COSMOPAK',local='Chão',quantidade=2));db.commit()
    with engine.connect() as conn:
        data=transfer.read_data(conn)
        assert all(any(r.get('context_id')==ctx for name,rows in data.items() for r in rows if name in ('stock','valmet_units','nkt_pallets')) for ctx in (1,2,3,4))


def balances(engine,*,physical,available,reserved=0,shipped=0,blocked=0,missing=0):
    with scoped(engine,5) as db:
        for state,count in [('on_hand',physical),('available',available),('reserved',reserved),('shipped',shipped),('blocked',blocked),('missing',missing)]:assert incoming.stock(db,state=state)[1]==count,(state,count)


def pdfs(db):
    return {kind:{d.id:bytes(d.pdf) for d in db.scalars(select(model))} for kind,model in [('dispatch',m.AlcobreDispatchDocument),('inventory',m.AlcobreInventoryDocument)]}


def test_integrated_alcobre_receipts_dispatch_inventory_and_full_encrypted_recovery(restricted_web,pg_databases,tmp_path):
    app,owner,engine,role,users,t=restricted_web;actor=users['admin'][0]
    with engine.connect() as conn:
        validate_runtime(conn)
        assert 170000<=int(conn.scalar(text('SHOW server_version_num')))<180000
        assert conn.scalar(text('SHOW transaction_isolation'))=='read committed'
    seed_other_customers(owner,engine,actor);foreign=foreign_snapshot(engine)
    positions(engine,actor,6)
    with scoped(engine,5) as db:
        content=receipt_pdf(['000101','000102','000103'])
        entry=incoming.preview_file(db,actor,secrets.token_hex(16),content,'fict-entrada.pdf')
        assert incoming.preview_file(db,actor,secrets.token_hex(16),content,'renamed.pdf')==entry
    receive(engine,actor,receipt_review(engine,actor,entry,[row('000101','FICT-ALC-1'),row('000102','FICT-ALC-2'),row('000103')]))
    pending=draft(engine,actor,'pending');receive(engine,actor,receipt_review(engine,actor,pending,quantity=2))
    with scoped(engine,5) as db:
        original=db.get(m.AlcobreEntry,entry);assert original.source_pdf==content and original.source_digest==sha256(content).hexdigest()
        assert incoming.stock(db,state='pending')[2][0][1]==2
    balances(engine,physical=3,available=2)
    with pytest.raises(incoming.ReceivingError):receipt_review(engine,actor,draft(engine,actor),[row('000101')])
    with pytest.raises(incoming.ReceivingError):receipt_review(engine,actor,draft(engine,actor),[row('CAPACITY','FICT-ALC-1')])
    holder=source(engine,actor,['000102']);dispatch_review(engine,actor,holder);prepare(engine,actor,holder)
    guide=source(engine,actor,['000101','000102','000103','999001']);dispatch_review(engine,actor,guide);prepare(engine,actor,guide)
    with scoped(engine,5) as db:
        raw=json.loads(outgoing.latest(db,db.get(m.AlcobreDispatch,guide)).payload)
        assert [r['reason'] for r in raw['items']]==['available','reserved_other','unlocated','unknown_pending']
        assert [r['requested'] for r in raw['items']]==['000101','000102','000103','999001']
        before_docs=pdfs(db)
    key=secrets.token_hex(16);first=ship(engine,actor,guide,['000101'],key=key)
    # A reserved elsewhere or unavailable number is never silently added to a guide.
    balances(engine,physical=2,available=0,reserved=1,shipped=1)
    with scoped(engine,5) as db:assert not occupied(db,['FICT-ALC-1']) and sum(q for _,q in occupied(db,['FICT-ALC-2']))==1
    receive(engine,actor,receipt_review(engine,actor,draft(engine,actor),[row('000201','FICT-ALC-1')]))
    assert foreign_snapshot(engine)==foreign
    cancel(engine,actor,holder)
    present=source(engine,actor,['000102']);dispatch_review(engine,actor,present);prepare(engine,actor,present)
    with pytest.raises(incoming.ReceivingError):ship(engine,actor,present,exceptions={'000102':dict(resolution(),physical_confirmed=False)})
    ship(engine,actor,present,exceptions={'000102':resolution()})
    with scoped(engine,5) as db:assert sum(q for _,q in occupied(db,['FICT-ALC-2']))==1
    missing_guide=source(engine,actor,['000102']);dispatch_review(engine,actor,missing_guide);prepare(engine,actor,missing_guide);ship(engine,actor,missing_guide,exceptions={'000102':resolution('not_found')})
    balances(engine,physical=3,available=1,shipped=1,blocked=1)
    found=new_inventory(engine,actor,['FICT-ALC-2']);record(engine,actor,found,'found','000102','FICT-ALC-2');approve(engine,actor,found)
    final=source(engine,actor,['000102']);dispatch_review(engine,actor,final);prepare(engine,actor,final);ship(engine,actor,final,['000102'])
    # Partial identification stays on the original receipt, then putaway and dispatch.
    receive(engine,actor,receipt_review(engine,actor,pending,[row('000301')],kind='identify'))
    with scoped(engine,5) as db:assert incoming.stock(db,state='pending')[2][0][1]==1
    receive(engine,actor,receipt_review(engine,actor,pending,[row('000301','FICT-ALC-3')],kind='place'))
    pending_out=source(engine,actor,['000301']);dispatch_review(engine,actor,pending_out);prepare(engine,actor,pending_out);ship(engine,actor,pending_out,['000301'])
    receive(engine,actor,receipt_review(engine,actor,pending,[row('000302','FICT-ALC-4')],kind='identify'))
    # Administrative absence releases only after approval; correction preserves original location.
    absence=new_inventory(engine,actor,['FICT-ALC-1']);record(engine,actor,absence,'missing','000201');approve(engine,actor,absence)
    recovery=new_inventory(engine,actor,['FICT-ALC-1']);record(engine,actor,recovery,'found','000201','FICT-ALC-1');approve(engine,actor,recovery)
    moved=new_inventory(engine,actor,['FICT-ALC-1']);record(engine,actor,moved,'elsewhere','000201','FICT-ALC-5');approve(engine,actor,moved)
    with scoped(engine,5) as db:
        current=incoming.stock(db,'000201',state='available')[0][0][1];assert current.local=='FICT-ALC-5'
        historical=db.scalar(select(m.AlcobrePlacement).join(m.AlcobreBobine,m.AlcobreBobine.id==m.AlcobrePlacement.bobine_id).where(m.AlcobreBobine.number=='000201'));assert historical.local=='FICT-ALC-1'
        assert db.scalar(select(func.count()).select_from(m.AlcobreEntry).where(m.AlcobreEntry.id==pending))==1
        assert {b.entry_id for b in db.scalars(select(m.AlcobreBobine).where(m.AlcobreBobine.number.in_(['000301','000302'])))}=={pending}
    complete=new_inventory(engine,actor,mode='complete')
    for number,local in [('000103','FICT-ALC-6'),('000201','FICT-ALC-5'),('000302','FICT-ALC-4')]:record(engine,actor,complete,'found',number,local)
    for local in ['FICT-ALC-1','FICT-ALC-2','FICT-ALC-3']:record(engine,actor,complete,'empty',local=local)
    approve(engine,actor,complete)
    balances(engine,physical=3,available=3,shipped=3)
    # Unknown, including unlabelled material, creates investigation, never identities/quantities.
    unknown=new_inventory(engine,actor,['FICT-ALC-1']);report=record(engine,actor,unknown,'unknown',local='FICT-ALC-1')
    with scoped(engine,5) as db:assert db.scalar(select(func.count()).select_from(m.AlcobreBobine))==6
    record(engine,actor,unknown,'resolved',resolves=report);record(engine,actor,unknown,'empty',local='FICT-ALC-1');approve(engine,actor,unknown)
    assert foreign_snapshot(engine)==foreign
    with TestClient(app) as client:
        client.cookies.set('wms_session',t['admin'][0]);client.cookies.set('ddn_context','5')
        with scoped(engine,5) as db:documents=pdfs(db)
        for kind,docs in documents.items():
            for ident,data in docs.items():
                prefix='dispatch-documents' if kind=='dispatch' else 'inventory-documents'
                assert client.get(f'/alcobre/{prefix}/{ident}.pdf').content==data==client.get(f'/alcobre/{prefix}/{ident}.pdf').content
                assert all(abs(float(p.mediabox.width)-595.28)<1 and abs(float(p.mediabox.height)-841.89)<1 for p in PdfReader(BytesIO(data)).pages)
        for kind,docs in before_docs.items():assert all(documents[kind][i]==b for i,b in docs.items())
        assert client.get(f'/alcobre/entries/{entry}/source.pdf').content==content
        for ctx in (1,2,3,4):
            client.cookies.set('ddn_context',str(ctx));assert client.get('/alcobre/stock').status_code==403
    assert foreign_snapshot(engine)==foreign
    before=snapshot(engine);folder=tmp_path/'integrated-alcobre';target=pg_databases(restricted=True)
    backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert backup.restore(target,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'
    assert snapshot(target)==before and foreign_snapshot(target)==foreign
    with target.connect() as conn:assert conn.scalar(text('SELECT version_num FROM alembic_version'))=='ddn_0022'
    with scoped(target,5) as db:assert pdfs(db)==documents
    assert first and foreign_snapshot(engine)==foreign


def test_integrated_alcobre_review_stale_after_location_correction_or_receipt_putaway(warehouse):
    engine,u=warehouse;actor=u['admin'][0];positions(engine,actor,3)
    entry=draft(engine,actor);receive(engine,actor,receipt_review(engine,actor,entry,[row('000101')]))
    # A stale putaway must not overwrite an inventory correction of an unlocated unit.
    putaway=receipt_review(engine,actor,entry,[row('000101','FICT-ALC-1')],kind='place')
    inv=new_inventory(engine,actor,mode='complete');record(engine,actor,inv,'found','000101','FICT-ALC-2')
    for code in ['FICT-ALC-1','FICT-ALC-3']:record(engine,actor,inv,'empty',local=code)
    approve(engine,actor,inv);before=snapshot(engine)
    with pytest.raises(incoming.ReceivingError):receive(engine,actor,putaway)
    assert snapshot(engine)==before
    guide=source(engine,actor,['000101']);dispatch_review(engine,actor,guide)
    move=new_inventory(engine,actor,['FICT-ALC-2']);record(engine,actor,move,'elsewhere','000101','FICT-ALC-3');approve(engine,actor,move)
    before=snapshot(engine)
    with pytest.raises(incoming.ReceivingError):prepare(engine,actor,guide)
    assert snapshot(engine)==before
    dispatch_review(engine,actor,guide);prepare(engine,actor,guide);ship(engine,actor,guide,['000101'])
    with scoped(engine,5) as db:assert not occupied(db,['FICT-ALC-1','FICT-ALC-2','FICT-ALC-3'])


@pytest.mark.parametrize('stage',['before_pdf','after_pdf','after_audit'])
def test_integrated_alcobre_failures_between_ledger_document_and_audit_are_atomic(restricted_web,monkeypatch,stage):
    app,owner,engine,role,u,t=restricted_web;actor=u['admin'][0];positions(engine,actor,1)
    entry=draft(engine,actor);receive(engine,actor,receipt_review(engine,actor,entry,[row('000101','FICT-ALC-1')]))
    inv=new_inventory(engine,actor);record(engine,actor,inv,'missing','000101');rev=inventory_review(engine,actor,inv);before=snapshot(engine)
    original_archive=inventory.archive;original_audit=incoming.audit
    def archive(db,*args,**kwargs):
        if stage=='before_pdf':raise RuntimeError('Fictitious pre-PDF fault')
        result=original_archive(db,*args,**kwargs);db.flush()
        if stage=='after_pdf':raise RuntimeError('Fictitious post-PDF fault')
        return result
    def audit(db,user,action,details):
        result=original_audit(db,user,action,details);db.flush()
        if action=='INVENTARIO_CONFIRMAR':raise RuntimeError('Fictitious post-audit fault')
        return result
    if stage=='after_audit':monkeypatch.setattr(incoming,'audit',audit)
    else:monkeypatch.setattr(inventory,'archive',archive)
    key=secrets.token_hex(16)
    with pytest.raises(RuntimeError):approve(engine,actor,inv,rev,key)
    assert snapshot(engine)==before
    monkeypatch.setattr(inventory,'archive',original_archive);monkeypatch.setattr(incoming,'audit',original_audit)
    result=approve(engine,actor,inv,rev,key);assert approve(engine,actor,inv,rev,key)==result
    with scoped(engine,5) as db:assert incoming.stock(db,state='missing')[1]==1


def test_integrated_alcobre_a4_inventory_keeps_every_long_number_and_unknown_quantity():
    rows=[dict(source=dict(number=str(i).zfill(120),local='FICT-'+str(i)),result='unverified' if i%2 else 'found',local=None if i%2 else 'FICT-'+str(i),action='none',evidence='Prova fictícia integral',recorded_quantity=1,physical_quantity=None if i%2 else 1,difference=None if i%2 else 0) for i in range(300)]
    rows[177]['evidence']='W'*2000  # A legal long proof must still allow row continuation.
    report=dict(rows=rows,locations=[],pending=[],incidents=[dict(id=1,number=None,local=None,evidence='Identidade desconhecida sem número inventado',resolved=False)],events=[],blocking=['Não concluir sem conferir'])
    raw=render_pdf(1,1,'Inventário fictício extenso',report,'conference','Fictício',datetime(2027,1,1),'Sem movimento')
    reader=PdfReader(BytesIO(raw));words=''.join(p.extract_text() for p in reader.pages).replace('\n','')
    readable=re.sub(r'\s+','',words)
    assert len(reader.pages)>1 and 'Desconhecido' in readable and 'Semnúmerocomprovado' in readable
    for item in rows:assert item['source']['number'] in words
    assert sum(len(run) for run in re.findall(r'W{2,}',words))>=2000
    assert all(abs(float(p.mediabox.width)-595.28)<1 and abs(float(p.mediabox.height)-841.89)<1 for p in reader.pages)


def test_integrated_alcobre_two_authenticated_users_cannot_adjust_a_reserved_bobine(restricted_web):
    app,owner,engine,role,u,t=restricted_web;admin=u['admin'][0];operator=u['operador'][0]
    positions(engine,admin,1);entry=draft(engine,operator);receive(engine,operator,receipt_review(engine,operator,entry,[row('000101','FICT-ALC-1')]))
    guide=source(engine,admin,['000101']);rid,digest=dispatch_review(engine,admin,guide)
    inv=new_inventory(engine,operator);barrier=Barrier(2)
    with scoped(engine,5) as db:version=db.get(m.AlcobreDispatch,guide).version
    def run(index):
        user='admin' if index==0 else 'operador'
        with TestClient(app) as client:
            client.cookies.set('wms_session',t[user][0]);client.cookies.set('ddn_context','5')
            data=dict(csrf=t[user][1],context_id=5)
            barrier.wait(timeout=10)
            if index==0:return client.post(f'/alcobre/dispatches/{guide}/prepare',data=dict(data,version=version,digest=digest,key=secrets.token_hex(16),accepted='yes'))
            return client.post(f'/alcobre/inventories/{inv}/record',data=dict(data,version=1,result='missing',number='000101',evidence='Conferência fictícia física do segundo utilizador'))
    with ThreadPoolExecutor(2) as pool:responses=list(pool.map(run,range(2)))
    assert all(r.status_code==200 for r in responses),[(r.status_code,r.text[:200]) for r in responses]
    balances(engine,physical=1,available=0,reserved=1)
    with pytest.raises(incoming.ReceivingError):inventory_review(engine,admin,inv)
    with TestClient(app) as client:
        client.cookies.set('wms_session',t['operador'][0]);client.cookies.set('ddn_context','5')
        with scoped(engine,5) as db:ver=db.get(m.AlcobreInventory,inv).version
        assert client.post(f'/alcobre/inventories/{inv}/review',data=dict(csrf=t['operador'][1],context_id=5,version=ver,key=secrets.token_hex(16),reason='Sem autorização')).status_code==403
        assert client.post(f'/alcobre/inventories/{inv}/record',data=dict(csrf='bad',context_id=5,version=ver,result='missing',number='000101',evidence='Inválida')).status_code==403
    cancel(engine,admin,guide)
    with pytest.raises(incoming.ReceivingError):inventory_review(engine,admin,inv)
    record(engine,operator,inv,'missing','000101');approve(engine,admin,inv)
    balances(engine,physical=0,available=0,missing=1)
    with scoped(engine,5) as db:assert not occupied(db,['FICT-ALC-1'])
