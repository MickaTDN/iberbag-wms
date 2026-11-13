"""Synthetic read-only mobile API and Chromium local infrastructure."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from iberbag import database as m
from test_security_regressions import restricted_web
from test_receiving import warehouse
from test_transfer import pg_databases,docker_client
from test_cosmopak_packings import stock,packing,command
from test_ddn_contexts import scoped


def test_pwa_secure_context_session_isolation_and_no_stock_commands(restricted_web,monkeypatch):
    app,owner,runtime,role,users,tokens=restricted_web
    import iberbag.app as module
    monkeypatch.setattr(module,'COOKIE_SECURE',True)
    a=users['admin'][0];stock(owner,a,(50,));ident=packing(owner,a,[('DDN','A','L',50,1)]);command(owner,a,ident,'confirm',{'accepted':True})
    with TestClient(app,base_url='https://testserver') as client:
        assert client.get('/pwa/api/session').status_code==401
        client.cookies.set('wms_session',tokens['operador'][0]);client.cookies.set('ddn_context','3')
        body=client.get('/pwa/api/session').json();assert [c['label'] for c in body['contexts']]==['Cosmopak / Ovar','Iberbag / Ovar']
        response=client.get('/pwa/api/tasks?context_id=3');assert response.status_code==200 and response.headers['cache-control']=='no-store'
        assert response.json()['tasks'][0]['lines'][0]['boxes']==50
        assert 'COMPLETA' in response.text and 'source_excel' not in response.text and 'csrf' not in response.text
        from test_cosmopak_packings import picks,conference
        pick=picks(owner,ident)[0]
        command(owner,a,ident,'prepare',conference(),pick=pick[0]);command(owner,a,ident,'finish_prepare');command(owner,a,ident,'start_load')
        command(owner,a,ident,'load',conference(),pick=pick[0]);command(owner,a,ident,'unload',pick=pick[0],reason='Nota privada fictícia não necessária à consulta')
        pending=client.get('/pwa/api/tasks?context_id=3')
        assert 'No camião — conferência pendente' in pending.text and 'Nota privada' not in pending.text
        assert client.get('/pwa/api/tasks?context_id=1').status_code==403
        assert client.post('/pwa/api/command',json={'operation':'load'}).status_code in (404,405)
        client.cookies.set('ddn_context','1');assert client.get('/pwa/api/tasks?context_id=1').json()['tasks']==[]
        from test_dispatching import add_stock,order as legacy,review as legacy_review,check as legacy_check,confirm as legacy_confirm
        from test_etapa6 import prepare,confirm as office_confirm
        add_stock(owner,'PWA-FICT-LEGACY',qty=2)
        old=legacy(owner,a);legacy_review(owner,a,old);legacy_check(owner,a,old);legacy_confirm(owner,a,old)
        assert client.get('/pwa/api/tasks?context_id=1').json()['tasks']==[]
        current=prepare(owner,a)
        assert client.get('/pwa/api/tasks?context_id=1').json()['tasks']==[]
        office_confirm(owner,a,current)
        tasks=client.get('/pwa/api/tasks?context_id=1').json()['tasks']
        assert len(tasks)==1 and tasks[0]['id']==current and tasks[0]['kind']=='Recolha'
        from iberbag.inventorying import close_preparation
        with scoped(owner,1) as db:close_preparation(db,a,current,'Retirada física fictícia comprovada')
        assert client.get('/pwa/api/tasks?context_id=1').json()['tasks']==[]
        client.cookies.set('ddn_context','2');assert client.get('/pwa/api/tasks?context_id=2').status_code==403
        shell=client.get('/pwa');assert 'script-src \'self\'' in shell.headers['content-security-policy'] and 'Definições' not in shell.text
    from conftest import login_data
    with TestClient(app,base_url='https://testserver') as client:
        response=client.post('/login?mobile=1',data=login_data(client,{'username':'operador','password':users['operador'][1]}),follow_redirects=False)
        assert response.status_code==303 and response.headers['location']=='/pwa'
        info=client.get('/pwa/api/session').json()
        assert client.post('/logout',data={'csrf':info['csrf'],'context_id':str(info['current'])}).status_code==200
        assert client.get('/pwa/api/session').status_code==401
    with TestClient(app,base_url='http://testserver') as client:assert client.get('/pwa').status_code==503
    with scoped(owner,3) as db:assert db.scalar(select(func.sum(m.Stock.quantidade)))==1


def test_pwa_browser_encryption_queue_recovery_dependencies_and_isolation(tmp_path):
    from playwright.sync_api import sync_playwright
    from pathlib import Path
    with sync_playwright() as p:
        import shutil
        browser=p.chromium.launch(headless=True,executable_path=shutil.which('chromium'),args=['--no-sandbox'])
        context=browser.new_context(viewport={'width':393,'height':851},is_mobile=True,has_touch=True)
        root=Path(__file__).resolve().parents[1]/'iberbag'/'pwa'
        def route(r):
            name=r.request.url.rsplit('/',1)[-1]
            if name in ('vault.js','app.js','app.css','manifest.webmanifest','icon.svg'):r.fulfill(body=(root/name).read_bytes(),content_type='text/javascript' if name.endswith('.js') else 'text/plain')
            else:r.fulfill(body='<html><body>Fictício</body></html>',content_type='text/html')
        context.route('https://wms.test/**',route);page=context.new_page();page.goto('https://wms.test/pwa')
        result=page.evaluate("""async()=>{
 const {Vault}=await import('/pwa/vault.js');const auth=async()=>({user:10,context:3});
 const v=new Vault(10,3);await v.unlock('Frase local fictícia forte',auth);
 await v.copy({user:10,context:3,synced_at:'FICTICIO',tasks:[],csrf:'NUNCA-GUARDAR'});
 const a=await v.enqueue({operation:'prepare',reference:'PACK-FICT',version:1});
 const b=await v.enqueue({operation:'load',reference:'PACK-FICT',version:1,depends:[a]});
 if(!(await v.get()).queue.every(q=>/^[a-f0-9]{32}$/.test(q.idempotency)))throw Error('Chave incompatível com serviços WMS');
 let failures=0;try{await v.enqueue({operation:'load',reference:'OUTRO',version:1});}catch{failures++;}
 try{await v.sync(async()=>({user:20,context:3}),{prepare:async()=>({confirmed:true})});}catch{failures++;}
 await v.sync(auth);if((await v.get()).queue.some(q=>q.state!=='pending'))throw Error('Adapter ativo');
 let count=0;try{await v.sync(auth,{prepare:async()=>{count++;throw Error('Rede interrompida');}});}catch{failures++;}
 if((await v.get()).queue[0].state!=='sent')throw Error('Perda de ação enviada');
 await v.retry(a,auth);await v.sync(auth,{prepare:async()=>({confirmed:true}),load:async()=>({confirmed:false})});
 const state=await v.get();if(state.queue[1].state!=='conflict')throw Error('Conflito perdido');
 if(state.copy.csrf)throw Error('Segredo guardado');v.lock();try{await v.get();}catch{failures++;}
 window.Vault=Vault;window.auth=auth;window.ids=[a,b];return failures;
}""")
        assert result==4
        page.reload()
        assert page.evaluate("""async()=>{const {Vault}=await import('/pwa/vault.js');const v=new Vault(10,3);await v.unlock('Frase local fictícia forte',async()=>({user:10,context:3}));const d=await v.get();return d.queue.length===2&&d.queue[0].state==='confirmed'&&d.queue[1].state==='conflict';}""")
        assert page.evaluate("""async()=>{const {Vault}=await import('/pwa/vault.js');const v=new Vault(10,3);try{await v.unlock('Frase local incorreta',async()=>({user:10,context:3}));return false;}catch{return true;}}""")
        browser.close()


def test_pwa_real_https_service_worker_offline_shell_and_update_keep_queue(tmp_path):
    import shutil,ssl,threading,json
    from datetime import datetime,timedelta,timezone
    from pathlib import Path
    from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
    from functools import partial
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from playwright.sync_api import sync_playwright
    root=tmp_path/'public';shutil.copytree(Path(__file__).resolve().parents[1]/'iberbag/pwa',root/'pwa')
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')]);now=datetime.now(timezone.utc)
    cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1)).add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]),False).sign(key,hashes.SHA256())
    kp=tmp_path/'key.pem';cp=tmp_path/'cert.pem';kp.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));cp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self,*args):pass
        def end_headers(self):self.send_header('Cache-Control','no-cache');super().end_headers()
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(Handler,directory=str(root)))
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(cp,kp);server.socket=tls.wrap_socket(server.socket,server_side=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=shutil.which('chromium'),args=['--no-sandbox','--ignore-certificate-errors'])
            ctx=browser.new_context(ignore_https_errors=True,viewport={'width':393,'height':851},is_mobile=True,has_touch=True)
            ctx.route('**/pwa/api/session',lambda r:r.fulfill(json={'user':10,'csrf':'FICTICIO','current':3,'contexts':[{'id':3,'label':'Cosmopak / Ovar'}]}))
            page=ctx.new_page();base=f'https://localhost:{server.server_port}'
            page.goto(base+'/pwa/index.html');page.wait_for_function('navigator.serviceWorker.controller !== null')
            page.wait_for_function("document.getElementById('status').textContent.includes('Sessão individual validada')")
            assert page.locator('#update').is_hidden()
            page.locator('#refresh').click()
            assert 'Selecionar contexto' in page.locator('#status').inner_text()
            page.evaluate("""async()=>{const {Vault}=await import('/pwa/vault.js');const v=new Vault(10,3);await v.unlock('Frase fictícia local segura',async()=>({user:10,context:3}));await v.enqueue({operation:'prepare',reference:'PENDENTE-FICT',version:1});}""")
            ctx.set_offline(True);page.reload();assert page.locator('h1').inner_text()=='DDN WMS'
            assert page.evaluate("""async()=>{const keys=await caches.keys();for(const k of keys){for(const r of await (await caches.open(k)).keys()){if(r.url.includes('/api/')||r.url.includes('/login'))return false;}}return true;}""")
            ctx.set_offline(False)
            css=root/'pwa/app.css';css.write_text(css.read_text()+'\n/* SYNTHETIC-RELEASE-2 */')
            sw=root/'pwa/sw.js';sw.write_text(sw.read_text().replace("const VERSION='","const VERSION='synthetic-next-"))
            page.evaluate('async()=>{const r=await navigator.serviceWorker.getRegistration();await r.update();}')
            page.wait_for_function('navigator.serviceWorker.getRegistration().then(r=>!!r.waiting)')
            assert not page.evaluate("async()=>(await (await fetch('/pwa/app.css')).text()).includes('SYNTHETIC-RELEASE-2')")
            with page.expect_navigation(wait_until='domcontentloaded'):
                page.locator('#update').click()
            assert page.evaluate("async()=>(await (await fetch('/pwa/app.css')).text()).includes('SYNTHETIC-RELEASE-2')")
            assert page.evaluate("""async()=>{const {Vault}=await import('/pwa/vault.js');const v=new Vault(10,3);await v.unlock('Frase fictícia local segura',async()=>({user:10,context:3}));return (await v.get()).queue[0].reference==='PENDENTE-FICT';}""")
            ctx.route('**/context',lambda r:r.fulfill(body='OK'))
            ctx.route('**/pwa/api/tasks?**',lambda r:r.fulfill(json={'user':10,'context':3,'synced_at':'FICTICIO','tasks':[]}))
            page.locator('#context').select_option('3')
            page.wait_for_function("document.getElementById('status').textContent.includes('Consulta WMS')")
            page.locator('#phrase').fill('Frase fictícia local segura');page.locator('#unlock').click()
            page.wait_for_function("document.getElementById('queue').textContent.includes('PENDENTE-FICT')")
            # Mobile UI persists explicit physical evidence before sending it.
            mobile_task={'kind':'Receção','reference':'REC-FICT','id':7,'version':1,'status':'draft','lines':[{'id':55,'version':2,'article':'A','lot':'L','quantity':1,'boxes':23,'classification':'INCOMPLETA','location':None,'status':'planned'}]}
            ctx.route('**/pwa/api/tasks?**',lambda r:r.fulfill(json={'user':10,'context':3,'synced_at':'FICTICIO','tasks':[mobile_task]}))
            page.locator('#refresh').click();page.get_by_role('button',name='Confirmar posição',exact=True).wait_for()
            replies=iter(['A101','23','L',None,'Local e identidade comprovados',None])
            def physical_dialog(d):
                value=next(replies)
                d.accept(value) if d.type=='prompt' else d.accept()
            page.on('dialog',physical_dialog)
            page.get_by_role('button',name='Confirmar posição',exact=True).click()
            page.wait_for_function("document.getElementById('status').textContent.includes('persistido cifrado')")
            page.remove_listener('dialog',physical_dialog)
            assert page.evaluate("""async()=>{const {Vault}=await import('/pwa/vault.js');const v=new Vault(10,3);await v.unlock('Frase fictícia local segura',async()=>({user:10,context:3}));const q=(await v.get()).queue.find(q=>q.operation==='mobile');return q.state==='pending'&&q.command.target===55&&q.command.version===2&&q.command.payload.boxes===23&&q.command.payload.physical===true&&q.command.payload.local==='A101';}""")
            ctx.route('**/pwa/api/commands',lambda r:r.fulfill(json={'confirmed':True}))
            page.locator('#sync').click()
            page.wait_for_function("document.getElementById('queue').textContent.includes('Confirmada pelo WMS')")
            ctx.set_offline(True);page.locator('#logout').click()
            page.wait_for_function("document.getElementById('tasks').textContent===''&&document.getElementById('queue').textContent===''")
            ctx.set_offline(False);page.locator('#erase').click()
            page.wait_for_function("document.getElementById('status').textContent.includes('Iniciar sessão e selecionar contexto')")
            exists="""async()=>new Promise(resolve=>{const r=indexedDB.open('ddn-operator-v1');r.onsuccess=()=>{const d=r.result,q=d.transaction('vaults').objectStore('vaults').get('10:3');q.onsuccess=()=>{d.close();resolve(!!q.result);};};})"""
            assert page.evaluate(exists)
            page.reload();page.wait_for_function("document.getElementById('status').textContent.includes('Sessão individual validada')")
            page.locator('#context').select_option('3');page.wait_for_function("document.getElementById('status').textContent.includes('Consulta WMS')")
            # Forgotten phrase: no unlock, but a fresh authenticated identity.
            page.once('dialog',lambda d:d.dismiss());page.locator('#erase').click();page.wait_for_timeout(300)
            assert page.evaluate(exists)
            page.once('dialog',lambda d:d.accept());page.locator('#erase').click()
            page.wait_for_function("document.getElementById('status').textContent.includes('Dados locais eliminados explicitamente')")
            assert not page.evaluate(exists)
            browser.close()
    finally:server.shutdown();server.server_close();thread.join(timeout=5)


def test_pwa_browser_vault_tampering_concurrent_tabs_and_expired_auth(tmp_path):
    import shutil
    from pathlib import Path
    from playwright.sync_api import sync_playwright
    root=Path(__file__).resolve().parents[1]/'iberbag/pwa'
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=shutil.which('chromium'),args=['--no-sandbox'])
        ctx=browser.new_context()
        ctx.route('https://wms.test/**',lambda r:r.fulfill(body=(root/'vault.js').read_bytes(),content_type='text/javascript') if r.request.url.endswith('vault.js') else r.fulfill(body='<html>Fictício</html>',content_type='text/html'))
        pages=[ctx.new_page(),ctx.new_page()]
        for page in pages:
            page.goto('https://wms.test/pwa/')
            page.evaluate("""async()=>{const {Vault}=await import('/pwa/vault.js');window.v=new Vault(10,3);await v.unlock('Frase fictícia isolada local',async()=>({user:10,context:3}));}""")
        # Browser tasks run in separate pages concurrently, serialized by Web Locks.
        pages[0].evaluate("window.hold=navigator.locks.request('ddn-vault-10:3',()=>new Promise(resolve=>window.release=resolve));void 0")
        pages[0].wait_for_function('typeof window.release==="function"')
        pages[0].evaluate("window.pending=Promise.all(Array.from({length:8},(_,i)=>v.enqueue({operation:'prepare',reference:'A'+i,version:1})));void 0")
        pages[1].evaluate("window.pending=Promise.all(Array.from({length:8},(_,i)=>v.enqueue({operation:'prepare',reference:'B'+i,version:1})));void 0")
        pages[0].evaluate('release()')
        for page in pages:page.evaluate('async()=>await pending')
        assert pages[0].evaluate('async()=>(await v.get()).queue.length')==16
        assert pages[0].evaluate("""async()=>{try{await v.sync(async()=>{throw Error('Sessão expirada');},{prepare:async()=>({confirmed:true})});}catch{}return (await v.get()).queue.every(q=>q.state==='pending');}""")
        assert pages[0].evaluate("""async()=>{const d=await new Promise((yes,no)=>{const r=indexedDB.open('ddn-operator-v1');r.onsuccess=()=>yes(r.result);r.onerror=no;});await new Promise((yes,no)=>{const t=d.transaction('vaults','readwrite'),s=t.objectStore('vaults'),r=s.get('10:3');r.onsuccess=()=>{const a=r.result;const b=new Uint8Array(a.cipher);b[0]^=1;a.cipher=b.buffer;s.put(a,'10:3');};t.oncomplete=yes;t.onabort=no;});d.close();try{await v.get();return false;}catch{return true;}}""")
        browser.close()


def test_pwa_erase_requires_online_identity_even_when_locked_or_phrase_forgotten():
    import shutil
    from pathlib import Path
    from playwright.sync_api import sync_playwright
    root=Path(__file__).resolve().parents[1]/'iberbag/pwa'
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=shutil.which('chromium'),args=['--no-sandbox'])
        ctx=browser.new_context();reply={'status':200,'body':{'user':10,'current':3}}
        def route(r):
            if r.request.url.endswith('vault.js'):r.fulfill(body=(root/'vault.js').read_bytes(),content_type='text/javascript')
            elif '/api/session' in r.request.url:r.fulfill(status=reply['status'],json=reply['body'])
            else:r.fulfill(body='<html>Fictício</html>',content_type='text/html')
        ctx.route('https://wms.test/**',route);page=ctx.new_page();page.goto('https://wms.test/pwa/')
        page.evaluate("""async()=>{const {Vault}=await import('/pwa/vault.js');window.Vault=Vault;window.v=new Vault(10,3);await v.unlock('Frase local fictícia segura',async()=>({user:10,context:3}));await v.enqueue({operation:'prepare',reference:'PENDENTE',version:1});v.lock();window.exists=()=>new Promise((resolve,reject)=>{const r=indexedDB.open('ddn-operator-v1');r.onsuccess=()=>{const d=r.result,q=d.transaction('vaults').objectStore('vaults').get('10:3');q.onsuccess=()=>{d.close();resolve(!!q.result);};q.onerror=reject;};});}""")
        async_erase="async()=>{try{await v.erase(true);return false;}catch{return await exists();}}"
        assert page.evaluate("async()=>{try{await v.erase(false);}catch{}return await exists();}")
        for status,body in ((401,{}),(200,{'user':11,'current':3}),(200,{'user':10,'current':1})):
            reply.update(status=status,body=body);assert page.evaluate(async_erase)
        reply.update(status=200,body={'user':10,'current':3})
        # A transport failure is refused even if the browser reports online.
        ctx.route('**/pwa/api/session',lambda r:r.abort('internetdisconnected'))
        assert page.evaluate(async_erase)
        ctx.unroute('**/pwa/api/session')
        ctx.set_offline(True);assert page.evaluate(async_erase)
        # Same retained object after a failed offline logout cannot erase, even
        # when the server still has a valid cookie after connection recovers.
        page.evaluate('v.revoke()');ctx.set_offline(False);assert page.evaluate(async_erase)
        # Logout while online validation is still pending also preserves the vault.
        assert page.evaluate("""async()=>{const pending=new Vault(10,3),original=window.fetch;let release;window.fetch=()=>new Promise(resolve=>release=resolve);const result=pending.erase(true).then(()=>false,()=>exists());while(!release)await new Promise(r=>setTimeout(r,0));pending.revoke();release(new Response(JSON.stringify({user:10,current:3}),{status:200}));try{return await result;}finally{window.fetch=original;}}""")
        # Fresh online identity can delete without the forgotten phrase/key.
        assert page.evaluate("async()=>{const fresh=new Vault(10,3);await fresh.erase(true);return !(await exists());}")
        browser.close()
