"""Fictitious mobile commands against PostgreSQL and restricted HTTP sessions."""
import json,secrets
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from iberbag import database as m,pwa_commands as s
from test_receiving import warehouse,receive
from test_transfer import pg_databases,docker_client
from test_security_regressions import restricted_web
from test_ddn_contexts import scoped
from test_cosmopak_packings import stock,packing,command,picks

def action(user,ctx,kind,target,version,payload=None):
    return dict(key=secrets.token_hex(16),user=user,context=ctx,kind=kind,target=target,version=version,payload=dict(physical=True,reason='Prova física fictícia',**(payload or {})),depends=[])

def test_mobile_putaway_atomic_partial_stacked_repeat_and_rollback(warehouse,monkeypatch):
    engine,u=warehouse;operator=u['operador'][0];rec,line=receive(engine,operator,3)
    with scoped(engine,1) as db:
        version=db.get(m.Receipt,rec).version
        normal=action(operator,1,'ib.putaway',line,version,dict(local='Chão',quantity=1,stacked=False))
        first=s.execute(db,operator,normal);assert s.execute(db,operator,normal)==first
        stacked=action(operator,1,'ib.putaway',line,version,dict(local='Chão',quantity=1,stacked=True))
        s.execute(db,operator,stacked)
        assert sorted((v.stacked,v.quantidade) for v in db.scalars(select(m.Stock)))==[(False,1),(True,1)]
        bad=action(operator,1,'ib.putaway',line,version,dict(local='A101',quantity=1,stacked=True))
        with pytest.raises(ValueError):s.execute(db,operator,bad)
        assert s.previous(db,bad['key']) is None
        original=s.encode
        monkeypatch.setattr(s,'encode',lambda _:(_ for _ in ()).throw(RuntimeError('Fictitious failure before receipt')))
        pending=action(operator,1,'ib.putaway',line,version,dict(local='Chão',quantity=1))
        with pytest.raises(RuntimeError):s.execute(db,operator,pending)
        assert db.scalar(select(func.sum(m.Stock.quantidade)))==2
        monkeypatch.setattr(s,'encode',original)
        s.execute(db,operator,pending);assert db.scalar(select(func.sum(m.Stock.quantidade)))==3
        for changed_quantity in (2,True,1.0):
            changed=dict(normal,payload=dict(normal['payload'],quantity=changed_quantity))
            with pytest.raises(ValueError):s.execute(db,operator,changed)

def test_mobile_packing_restricted_session_csrf_replay_and_office_boundary(restricted_web,monkeypatch):
    app,owner,runtime,role,users,tokens=restricted_web
    import iberbag.app as module
    monkeypatch.setattr(module,'COOKIE_SECURE',True)
    admin=users['admin'][0];operator=users['operador'][0]
    stock(owner,admin,(50,23));ident=packing(owner,admin);command(owner,admin,ident,'confirm',{'accepted':True})
    with TestClient(app,base_url='https://testserver') as client:
        client.cookies.set('wms_session',tokens['operador'][0]);client.cookies.set('ddn_context','3')
        info=client.get('/pwa/api/session').json()
        def post(a,csrf=None,origin='https://testserver'):
            return client.post('/pwa/api/commands',data={'csrf':info['csrf'] if csrf is None else csrf,'context_id':'3','action':json.dumps(a)},headers={'Origin':origin})
        for pick,boxes in zip(picks(owner,ident),(50,23)):
            a=action(operator,3,'pack.prepare',pick[0],pick[5],dict(lot='L',boxes=boxes,checked=True))
            assert post(a,csrf='invalid').status_code==403
            assert post(a,origin='https://evil.test').status_code==403
            assert post(dict(a,user=admin)).status_code==403
            assert post(dict(a,context=1)).status_code==403
            assert post(dict(a,kind='pack.approve')).status_code==403
            response=post(a);assert response.status_code==200,response.text
            assert post(a).json()==response.json()
            wrong=action(operator,3,'pack.prepare',pick[0],pick[5],dict(lot='L',boxes=boxes,checked=True))
            assert post(wrong).status_code==409
        def stage(kind):
            with scoped(owner,3) as db:version=db.get(m.CosmoPackingState,ident).version
            return post(action(operator,3,'pack.'+kind,ident,version))
        assert stage('finish_prepare').status_code==200
        assert stage('start_load').status_code==200
        first,second=picks(owner,ident)
        assert post(action(operator,3,'pack.load',first[0],first[5],dict(lot='L',boxes=50,checked=True))).status_code==200
        assert post(action(operator,3,'pack.unload',second[0],second[5])).status_code==200
        assert stage('finish_load').status_code==200
        with scoped(owner,3) as db:
            assert db.scalar(select(func.sum(m.Stock.quantidade)))==2
            assert db.get(m.CosmoPackingState,ident).stage=='load_closed'
        client.cookies.clear();assert post(a).status_code==403

def test_mobile_same_key_concurrent_devices_and_context_isolation(warehouse):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    engine,u=warehouse;op=u['operador'][0];rec,line=receive(engine,op,1)
    with scoped(engine,1) as db:version=db.get(m.Receipt,rec).version
    a=action(op,1,'ib.putaway',line,version,dict(local='Chão',quantity=1));barrier=Barrier(2)
    def run():
        with scoped(engine,1) as db:barrier.wait();return s.execute(db,op,a)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run) for _ in range(2)];results=[f.result(timeout=30) for f in futures]
    assert results[0]==results[1]
    with scoped(engine,1) as db:
        assert db.scalar(select(func.sum(m.Stock.quantidade)))==1
        assert db.scalar(select(func.count()).select_from(m.Audit).where(m.Audit.action=='PWA_COMMAND'))==1
    with scoped(engine,3) as db:
        with pytest.raises(ValueError):s.execute(db,op,a)
        assert db.scalar(select(func.count()).select_from(m.Stock))==0

def test_mobile_encrypted_payload_lost_reply_and_exact_retry():
    import shutil
    from pathlib import Path
    from playwright.sync_api import sync_playwright
    root=Path(__file__).resolve().parents[1]/'iberbag/pwa'
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=shutil.which('chromium'),args=['--no-sandbox'])
        ctx=browser.new_context();ctx.route('https://wms.test/**',lambda r:r.fulfill(body=(root/'vault.js').read_bytes(),content_type='text/javascript') if r.request.url.endswith('vault.js') else r.fulfill(body='<html>Fictício</html>',content_type='text/html'))
        page=ctx.new_page();page.goto('https://wms.test/pwa/')
        assert page.evaluate("""async()=>{const {Vault}=await import('/pwa/vault.js');const v=new Vault(10,3),auth=async()=>({user:10,context:3});await v.unlock('Frase fictícia muito segura',auth);const command={key:'a'.repeat(32),user:10,context:3,kind:'pack.prepare',target:12,version:0,payload:{lot:'LOTE-FICT',boxes:23,physical:true,checked:true,reason:'Prova fictícia'},depends:[]};const id=await v.enqueueMobile(command,'PACK-FICT');command.payload.boxes=999;let calls=[];try{await v.sync(auth,{mobile:async row=>{calls.push(JSON.stringify(row.command));throw Error('Resposta perdida após commit');}});}catch{}if((await v.get()).queue[0].state!=='sent')return false;v.lock();await v.unlock('Frase fictícia muito segura',auth);await v.retry(id,auth);await v.sync(auth,{mobile:async row=>{calls.push(JSON.stringify(row.command));return {confirmed:true};}});const q=(await v.get()).queue[0];return calls[0]===calls[1]&&q.command.payload.boxes===23&&q.command.key==='a'.repeat(32)&&q.state==='confirmed';}""")
        browser.close()

def test_mobile_receiving_cosmo_capacity_and_iberbag_collection_no_discount(warehouse):
    from test_cosmopak import plan,units
    from test_shared_locations import activate
    from test_dispatching import add_stock
    from test_etapa6 import prepare,confirm
    engine,u=warehouse;admin=u['admin'][0];op=u['operador'][0]
    activate(engine,admin,'A101')
    reception=plan(engine,admin);first,second=units(engine,reception)
    with scoped(engine,3) as db:
        a=action(op,3,'cos.place',first[0],first[1],dict(local='A101',boxes=50,lot='0007'))
        s.execute(db,op,a)
        collision=action(op,3,'cos.place',second[0],second[1],dict(local='A101',boxes=23,lot='0007'))
        with pytest.raises(ValueError):s.execute(db,op,collision)
        floor=dict(collision,key=secrets.token_hex(16),payload=dict(collision['payload'],local='Chão'))
        with pytest.raises(ValueError):s.execute(db,op,floor)
        assert db.scalar(select(func.count()).select_from(m.Stock))==0
        assert db.get(m.CosmoPallet,second[0]).incomplete
    add_stock(engine,'MOBILE-FICT',qty=3)
    ident=prepare(engine,admin);confirm(engine,admin,ident)
    with scoped(engine,1) as db:
        pick=db.scalar(select(m.DispatchPick).join(m.DispatchLine,m.DispatchLine.id==m.DispatchPick.line_id).where(m.DispatchLine.order_id==ident))
        version=db.get(m.DispatchOrder,ident).version
        before=db.scalar(select(func.sum(m.Stock.quantidade)))
        a=action(op,1,'ib.collect',pick.id,version,dict(local=pick.local,quantity=pick.quantidade))
        s.execute(db,op,a);s.execute(db,op,a)
        shortage=action(op,1,'ib.shortage',pick.id,version,dict(quantity=1))
        s.execute(db,op,shortage);s.execute(db,op,shortage)
        assert db.scalar(select(func.sum(m.Stock.quantidade)))==before
        assert db.get(m.PreparationCompletion,ident) is None
        assert db.scalar(select(func.count()).select_from(m.StockIncident))==1

def test_mobile_backup_restores_command_receipt_and_exact_replay(warehouse,pg_databases,tmp_path):
    from conftest import BACKUP_PASSWORD
    from iberbag import backup,transfer
    engine,u=warehouse;op=u['operador'][0];rec,line=receive(engine,op,1)
    with scoped(engine,1) as db:
        a=action(op,1,'ib.putaway',line,db.get(m.Receipt,rec).version,dict(local='Chão',quantity=1))
        result=s.execute(db,op,a)
    with engine.connect() as conn:before=transfer.inventory(transfer.read_data(conn))
    folder=tmp_path/'PWA-FICT';backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    restored=pg_databases(restricted=True)
    assert backup.restore(restored,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'
    with restored.connect() as conn:assert transfer.inventory(transfer.read_data(conn))==before
    with scoped(restored,1) as db:assert s.execute(db,op,a)==result
    with restored.connect() as conn:assert transfer.inventory(transfer.read_data(conn))==before

def test_mobile_offline_chain_blocks_successors_and_retries_exactly():
    import shutil
    from pathlib import Path
    from playwright.sync_api import sync_playwright
    root=Path(__file__).resolve().parents[1]/'iberbag/pwa'
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=shutil.which('chromium'),args=['--no-sandbox'])
        ctx=browser.new_context();ctx.route('https://wms.test/**',lambda r:r.fulfill(body=(root/'vault.js').read_bytes(),content_type='text/javascript') if r.request.url.endswith('vault.js') else r.fulfill(body='<html>Fictício</html>',content_type='text/html'))
        page=ctx.new_page();page.goto('https://wms.test/pwa/')
        assert page.evaluate("""async()=>{
         const {Vault}=await import('/pwa/vault.js');const v=new Vault(10,3),auth=async()=>({user:10,context:3});await v.unlock('Frase fictícia segura para fila',auth);
         const kinds=['prepare','finish_prepare','start_load','load','unload','return_stage','finish_load'];
         for(let i=0;i<kinds.length;i++)await v.enqueueMobile({key:String(i+1).padStart(32,'0'),user:10,context:3,kind:'pack.'+kinds[i],target:12,version:0,payload:{physical:true},depends:[]},'PACK-FICT');
         let rows=(await v.get()).queue,original=JSON.stringify(rows.map(q=>q.command));
         if(rows[6].depends.length!==1||rows[6].command.depends.length!==1||rows[6].depends[0]!==rows[5].id)return false;
         const calls=[];await v.sync(auth,{mobile:async q=>{calls.push(q.command.kind);return {confirmed:calls.length===1};}});
         rows=(await v.get()).queue;if(calls.length!==2||rows[1].state!=='conflict'||rows.slice(2).some(q=>q.state!=='pending'))return false;
         // Legacy records from PR39 with empty local links must also be blocked.
         await v.change(d=>{for(const q of d.queue)q.depends=[];});await v.sync(auth,{mobile:async()=>{throw Error('Successor must not send');}});
         await v.retry(rows[1].id,auth);await v.sync(auth,{mobile:async()=>({confirmed:true})});
         rows=(await v.get()).queue;if(!rows.every(q=>q.state==='confirmed')||JSON.stringify(rows.map(q=>q.command))!==original)return false;
         const second=new Vault(10,3);await second.unlock('Frase fictícia segura para fila',auth);
         await Promise.all([v,second].map((vault,i)=>vault.enqueueMobile({key:String(8+i).padStart(32,'0'),user:10,context:3,kind:'pack.prepare',target:12+i,version:0,payload:{physical:true},depends:[]},'PACK-CONCURRENT')));
         rows=(await v.get()).queue;const last=rows.slice(-2);if(!last[1].depends.includes(last[0].id)||!last[1].command.depends.includes(last[0].idempotency))return false;
         await v.change(d=>{d.queue[d.queue.length-2].state='sent';for(const q of d.queue.slice(-2)){q.depends=[];q.command.depends=[];}});
         let sent=0;await v.sync(auth,{mobile:async()=>{sent++;return {confirmed:true};}});return sent===0&&(await v.get()).queue.at(-1).state==='pending';
        }""")
        # Separate tabs must establish local links while holding the same lock.
        other=ctx.new_page();other.goto('https://wms.test/pwa/')
        for tab in (page,other):
            tab.evaluate("""async()=>{const {Vault}=await import('/pwa/vault.js');window.tabVault=new Vault(10,3);await tabVault.unlock('Frase fictícia segura para fila',async()=>({user:10,context:3}));}""")
        for i,tab in enumerate((page,other)):
            tab.evaluate("""i=>{window.saved=tabVault.enqueueMobile({key:String(20+i).padStart(32,'0'),user:10,context:3,kind:'pack.prepare',target:20+i,version:0,payload:{physical:true},depends:[]},'PACK-TABS');}""",i)
        for tab in (page,other):tab.evaluate('async()=>await saved')
        assert page.evaluate("""async()=>{const rows=(await tabVault.get()).queue.filter(q=>q.reference==='PACK-TABS');return rows.length===2&&rows[1].depends.includes(rows[0].id)&&rows[1].command.depends.includes(rows[0].idempotency);}""")
        assert page.evaluate("""async()=>{for(let i=0;i<105;i++)await tabVault.enqueueMobile({key:String(100+i).padStart(32,'0'),user:10,context:3,kind:'pack.prepare',target:100+i,version:0,payload:{physical:true},depends:[]},'PACK-LONG');const rows=(await tabVault.get()).queue.filter(q=>q.reference==='PACK-LONG');return rows.length===105&&rows.every((q,i)=>q.command.depends.length===(i?1:0)&&(!i||q.depends[0]===rows[i-1].id));}""")
        browser.close()


def test_mobile_server_dependencies_revalidate_without_consuming_successors(warehouse):
    engine,u=warehouse;op=u['operador'][0];other=u['other'][0];rec,line=receive(engine,op,3)
    with scoped(engine,1) as db:
        version=db.get(m.Receipt,rec).version
        first=action(op,1,'ib.putaway',line,version,dict(local='Chão',quantity=1))
        second=action(op,1,'ib.putaway',line,version,dict(local='Chão',quantity=1));second['depends']=[first['key']]
        original=json.dumps(second,sort_keys=True)
        with pytest.raises(ValueError):s.execute(db,op,second)
        assert s.previous(db,second['key']) is None
        result=s.execute(db,op,first);assert s.execute(db,op,first)==result
        answer=s.execute(db,op,second);assert s.execute(db,op,second)==answer
        assert json.dumps(second,sort_keys=True)==original
        foreign=action(other,1,'ib.putaway',line,version,dict(local='Chão',quantity=1));foreign['depends']=[second['key']]
        with pytest.raises(ValueError):s.execute(db,other,foreign)
        assert db.scalar(select(func.sum(m.Stock.quantidade)))==2
        independent=action(other,1,'ib.putaway',line,version,dict(local='Chão',quantity=1))
        s.execute(db,other,independent)
        assert db.scalar(select(func.sum(m.Stock.quantidade)))==3
