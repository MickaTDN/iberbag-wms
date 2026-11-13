"""Synthetic end-to-end journeys. TLS exception belongs ONLY to test browsers."""
import json,secrets,shutil,socket,threading,time
from datetime import datetime,timedelta,timezone
from io import BytesIO
import pytest
from sqlalchemy import select,func
from fastapi.testclient import TestClient
from iberbag import database as m,inventorying,backup,transfer,physical_locations
from test_receiving import warehouse,receive
from test_transfer import pg_databases,docker_client
from test_security_regressions import restricted_web
from test_ddn_contexts import scoped
from test_shared_locations import activate,prepare
from test_cosmopak import plan,units
from test_cosmopak_packings import packing,command,picks
from test_pwa_commands import action
from conftest import BACKUP_PASSWORD

@pytest.fixture
def live_mobile(restricted_web,monkeypatch,tmp_path):
    import uvicorn
    import iberbag.app as web
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import hashes,serialization
    app,owner,runtime,role,users,tokens=restricted_web
    monkeypatch.setattr(web,'COOKIE_SECURE',True)
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')]);now=datetime.now(timezone.utc)
    cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1)).add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]),False).sign(key,hashes.SHA256())
    kp=tmp_path/'ephemeral.key';cp=tmp_path/'ephemeral.crt'
    kp.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));cp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
    base=f'https://localhost:{sock.getsockname()[1]}'
    server=uvicorn.Server(uvicorn.Config(app,ssl_keyfile=str(kp),ssl_certfile=str(cp),lifespan='off',access_log=False,log_level='error'))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    try:
        for _ in range(100):
            if server.started:break
            if not thread.is_alive():pytest.fail('Synthetic HTTPS server did not start')
            time.sleep(.05)
        assert server.started
        yield base,owner,users,tokens
    finally:
        server.should_exit=True;thread.join(timeout=10);sock.close()
        assert not thread.is_alive()


def test_real_mobile_offline_commit_lost_reply_office_close_and_restore(live_mobile,pg_databases,tmp_path):
    from playwright.sync_api import sync_playwright,expect
    base,engine,u,tokens=live_mobile;admin=u['admin'][0];op=u['operador'][0]
    activate(engine,admin)
    rec,line=receive(engine,admin,2,code='IB-FICT-OLD')
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=shutil.which('chromium'),args=['--no-sandbox','--ignore-certificate-errors'])
        ctx=browser.new_context(ignore_https_errors=True,viewport={'width':393,'height':851},is_mobile=True,has_touch=True)
        ctx.add_cookies([dict(name='wms_session',value=tokens['operador'][0],url=base,secure=True,httpOnly=True,sameSite='Lax'),dict(name='ddn_context',value='1',url=base,secure=True)])
        page=ctx.new_page();page.goto(base+'/pwa/')
        expect(page.locator('#status')).to_contain_text('Sessão individual validada')
        page.locator('#context').select_option('1');expect(page.locator('#status')).to_contain_text('Consulta WMS');page.locator('#phrase').fill('Frase fictícia para homologação')
        with page.expect_response(lambda r:'/pwa/api/tasks?' in r.url):page.locator('#unlock').click()
        def physical(local,stacked):
            replies=iter(['1',local,stacked,'Descarga fictícia comprovada',True])
            def dialog(d):
                value=next(replies)
                if d.type=='prompt':d.accept(value)
                elif value:d.accept()
                else:d.dismiss()
            page.on('dialog',dialog);page.get_by_role('button',name='Confirmar arrumação',exact=True).click()
            expect(page.locator('#status')).to_contain_text('persistido cifrado')
            page.remove_listener('dialog',dialog)
        ctx.set_offline(True);physical('A101',False)
        with scoped(engine,1) as db:assert db.scalar(select(func.count()).select_from(m.Stock))==0
        # Real POST reaches the restricted runtime and commits; browser loses reply.
        ctx.set_offline(False)
        def lose_reply(route):
            response=route.fetch();assert response.status==200;route.abort('connectionreset')
        ctx.route('**/pwa/api/commands',lose_reply)
        page.locator('#sync').click();expect(page.locator('#status')).to_contain_text('Envio interrompido')
        with scoped(engine,1) as db:assert db.scalar(select(func.sum(m.Stock.quantidade)))==1
        ctx.unroute('**/pwa/api/commands');page.locator('#offline').click()
        page.once('dialog',lambda d:d.accept());page.get_by_role('button',name='Rever e repetir exatamente esta ação').click()
        expect(page.locator('#queue')).to_contain_text('Pendente no dispositivo');page.locator('#sync').click();expect(page.locator('#queue')).to_contain_text('Confirmada pelo WMS')
        with scoped(engine,1) as db:assert db.scalar(select(func.sum(m.Stock.quantidade)))==1
        page.locator('#refresh').click();expect(page.locator('#status')).to_contain_text('Consulta WMS')
        physical('Chão',True);page.locator('#sync').click()
        expect(page.locator('#queue li')).to_have_count(2);expect(page.locator('#queue')).not_to_contain_text('Pendente no dispositivo')
        browser.close()
    order=prepare(engine,admin)
    with scoped(engine,1) as db:
        assert db.scalar(select(func.sum(m.Stock.quantidade)))==1
        assert physical_locations.common_state(db,'A101')['state']=='awaiting_collection'
        pick=db.scalar(select(m.DispatchPick).join(m.DispatchLine,m.DispatchLine.id==m.DispatchPick.line_id).where(m.DispatchLine.order_id==order))
        from iberbag.pwa_commands import execute
        a=action(op,1,'ib.collect',pick.id,db.get(m.DispatchOrder,order).version,dict(quantity=1,local='A101'))
        execute(db,op,a);execute(db,op,a)
        assert physical_locations.common_state(db,'A101')['state']=='awaiting_collection'
        with pytest.raises(ValueError):inventorying.close_preparation(db,op,order,'Sem aprovação administrativa')
        inventorying.close_preparation(db,admin,order,'Recolha e posição vazia comprovadas')
        assert physical_locations.common_state(db,'A101')['state']=='free'
        assert db.scalar(select(func.sum(m.Stock.quantidade)))==1
        archived=db.get(m.DispatchDocument,order).pdf
    from pypdf import PdfReader
    assert all(abs(float(p.mediabox.width)-595.2756)<1 for p in PdfReader(BytesIO(archived)).pages)
    with engine.connect() as conn:before=transfer.inventory(transfer.read_data(conn))
    folder=tmp_path/'ENSAIO-FICT';backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    target=pg_databases(restricted=True);assert backup.restore(target,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'
    with target.connect() as conn:assert transfer.inventory(transfer.read_data(conn))==before
    with scoped(target,1) as db:
        assert db.get(m.DispatchDocument,order).pdf==archived
        assert execute(db,op,a)['confirmed']
    with target.connect() as conn:assert transfer.inventory(transfer.read_data(conn))==before

def test_shared_rack_mobile_receiving_two_operators_partial_dispatch_inventory(restricted_web,pg_databases,tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from iberbag import cosmopak as c,cosmopak_packings as pack,transferring
    from iberbag.security import new_session,get_session
    import iberbag.app as web
    app,engine,runtime,role,u,tokens=restricted_web;admin=u['admin'][0];op=u['operador'][0];other=u['other'][0]
    monkeypatch.setattr(web,'COOKIE_SECURE',True)
    with scoped(engine,1) as db:
        token=new_session(db,db.get(m.User,other));db.flush();tokens['other']=(token,get_session(db,token).csrf_token);db.commit()
    for local in ('A101','A102'):activate(engine,admin,local)
    rec=plan(engine,admin,[('A','Fictício','L',1,50,''),('A','Fictício','L',1,23,'X')])
    clients=[]
    for name in ('operador','other'):
        client=TestClient(app,base_url='https://testserver');client.cookies.set('wms_session',tokens[name][0]);client.cookies.set('ddn_context','3');clients.append(client)
    def post(client,name,a):
        response=client.post('/pwa/api/commands',data={'csrf':tokens[name][1],'context_id':3,'action':json.dumps(a)},headers={'Origin':'https://testserver'})
        assert response.status_code==200,response.text
        return response.json()
    try:
        for unit,local,boxes in zip(units(engine,rec),('A101','A102'),(50,23)):
            post(clients[0],'operador',action(op,3,'cos.place',unit[0],unit[1],dict(local=local,boxes=boxes,lot='L')))
        with scoped(engine,3) as db:
            assert db.scalar(select(func.count()).select_from(m.Stock))==0
            h=db.get(m.CosmoReception,rec);c.metadata(db,admin,secrets.token_hex(16),rec,h.version,'GT-FICT','2027-04-01',None,'Chegada comprovada')
            version=db.get(m.CosmoReception,rec).version
        post(clients[0],'operador',action(op,3,'cos.unloaded',rec,version))
        with scoped(engine,3) as db:
            rev=c.review(db,admin,secrets.token_hex(16),rec,db.get(m.CosmoReception,rec).version,'Revisão administrativa')
            c.approve(db,admin,secrets.token_hex(16),rec,rev,True)
        ib_rec,ib_line=receive(engine,admin,1,code='IB-FICT-REUSE')
        from iberbag.pwa_commands import execute
        with scoped(engine,1) as db:
            arrival=action(op,1,'ib.putaway',ib_line,db.get(m.Receipt,ib_rec).version,dict(local='A101',quantity=1))
            with pytest.raises(ValueError):execute(db,op,arrival)
        ident=packing(engine,admin);command(engine,admin,ident,'confirm',{'accepted':True});rows=picks(engine,ident);gate=Barrier(2)
        def prepare_one(args):
            i,name,actor,row,boxes=args;gate.wait(timeout=10)
            return post(clients[i],name,action(actor,3,'pack.prepare',row[0],row[5],dict(lot='L',boxes=boxes,checked=True)))
        with ThreadPoolExecutor(2) as pool:list(pool.map(prepare_one,[(0,'operador',op,rows[0],50),(1,'other',other,rows[1],23)]))
        with scoped(engine,1) as db:execute(db,op,arrival)
        command(engine,op,ident,'finish_prepare')
        with scoped(engine,3) as db:
            label=pack.document(db,admin,secrets.token_hex(16),ident,'stage');label_bytes=db.get(m.CosmoPackingDocument,label).pdf
            assert db.scalar(select(func.sum(m.Stock.quantidade)))==2
        command(engine,other,ident,'start_load')
        for row,boxes in zip(picks(engine,ident),(50,23)):
            post(clients[1],'other',action(other,3,'pack.load',row[0],row[5],dict(lot='L',boxes=boxes,checked=True)))
        row=picks(engine,ident)[1];post(clients[1],'other',action(other,3,'pack.unload',row[0],row[5]))
        row=picks(engine,ident)[1];post(clients[0],'operador',action(op,3,'pack.return_stage',row[0],row[5],dict(lot='L',boxes=23,checked=True)))
        command(engine,op,ident,'finish_load')
        rev=command(engine,admin,ident,'review_dispatch',{'dispatched_on':'2027-04-03'})
        command(engine,admin,ident,'approve',{'accepted':True,'review_id':rev})
        with scoped(engine,3) as db:
            assert db.scalar(select(func.sum(m.Stock.quantidade)))==1
            assert db.get(m.CosmoPackingDocument,label).pdf==label_bytes
            pack.document(db,admin,secrets.token_hex(16),ident,'dispatch')
        with scoped(engine,1) as db:
            source=db.scalar(select(m.Stock).where(m.Stock.quantidade==1))
            move=transferring.review_transfer(db,op,secrets.token_hex(16),source.id,'Chão','1');transferring.confirm_transfer(db,op,move)
        from test_etapa6 import inventory,count,submit,approve
        inv,scopes=inventory(engine,op)
        count(engine,op,scopes['Chão'],[('ART','LOT','IB-FICT-REUSE','1')]);submit(engine,op,inv);approve(engine,admin,inv)
        with scoped(engine,1) as db:assert physical_locations.common_state(db,'A101')['state']=='free'
        with engine.connect() as conn:before=transfer.inventory(transfer.read_data(conn))
        folder=tmp_path/'MULTICLIENTE-FICT';backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
        restored=pg_databases(restricted=True);assert backup.restore(restored,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'
        with restored.connect() as conn:assert transfer.inventory(transfer.read_data(conn))==before
        for context in (2,4,5):
            with scoped(restored,context) as db:assert db.scalar(select(func.count()).select_from(m.Stock))==0
    finally:
        for client in clients:client.close()
