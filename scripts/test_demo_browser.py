"""Exercise native HTML forms in Chrome/Edge against an isolated source DEMO.

Test dependency only: Playwright controls an installed browser. No browser settings,
production files, cookies, request bodies or token values are logged or archived.
"""
import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import secrets
import socket
import sys
import tempfile
import threading
import time

import uvicorn


@contextmanager
def local_server(home):
    from iberbag_demo.app import create_app,header_kind
    from starlette.datastructures import Headers

    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1',0));listener.listen(128)
        port=listener.getsockname()[1];origin=f'http://127.0.0.1:{port}'
        app=create_app(home,port,secrets.token_hex(32));observed=[]

        async def observe(scope,receive,send):
            if scope['type']=='http' and scope['method']=='POST':
                headers=Headers(scope=scope)
                kinds={name:header_kind(headers,name,expected) for name,expected in
                       (('host',f'127.0.0.1:{port}'),('origin',origin),('referer',origin))}
                async def sent(message):
                    if message['type']=='http.response.start':observed.append(dict(kinds,status=message['status']))
                    await send(message)
                await app(scope,receive,sent)
            else:await app(scope,receive,send)

        server=uvicorn.Server(uvicorn.Config(observe,host='127.0.0.1',port=port,proxy_headers=False,
                                             access_log=False,log_level='error',lifespan='off'))
        thread=threading.Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True);thread.start()
        try:
            deadline=time.monotonic()+10
            while not server.started:
                if not thread.is_alive() or time.monotonic()>=deadline:raise AssertionError('Servidor de teste indisponível.')
                time.sleep(.05)
            yield origin,app.state.store,observed
        finally:
            server.should_exit=True;thread.join(timeout=10)
            assert not thread.is_alive(),'Servidor de teste não encerrou.'


def run(package,channel=None,executable=None):
    # Import only the package under examination, not the operational application.
    sys.path.insert(0,str(package.resolve()))
    from iberbag_demo import simulation as sim
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory(prefix='iberbag-browser-') as root:
        home=Path(root);env=dict(os.environ)
        if os.name!='nt':env.update(HOME=root,XDG_CACHE_HOME=root,XDG_CONFIG_HOME=root)
        with local_server(home) as (origin,store,observed),sync_playwright() as playwright:
            options=dict(headless=True,env=env,timeout=30000)
            if executable:options['executable_path']=str(executable)
            elif channel:options['channel']=channel
            browser=playwright.chromium.launch(**options)
            try:
                context=browser.new_context();context.set_default_timeout(10000)
                # Do not contact external sites, even for the hostile-form test below.
                context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(origin+'/') else route.abort())
                page=context.new_page()

                def open_page(path):
                    response=page.goto(origin+path)
                    assert response.status==200 and response.headers['referrer-policy']=='same-origin'

                def click(name,status=303):
                    with page.expect_response(lambda r:r.request.method=='POST') as sent:
                        page.get_by_role('button',name=name,exact=True).click()
                    assert sent.value.status==status,f'Formulário recusado: {name} ({sent.value.status})'
                    page.wait_for_load_state('load')

                # Reproduce the previous defect using only the OLD response policy.
                def old_policy(route):
                    response=route.fetch()
                    route.fulfill(response=response,headers={**response.headers,'referrer-policy':'no-referrer'})
                page.route(origin+'/dispatches/new',old_policy)
                page.goto(origin+'/dispatches/new')
                page.locator('[name=lote_0]').fill('LOTE-DEMO-A')
                page.locator('[name=quantidade_0]').fill('1')
                before=store.snapshot();click('Guardar e rever seleção FIFO simulada',403)
                assert store.snapshot()==before and observed[-1]==dict(host='local',origin='null',referer='ausente',status=403)
                page.unroute(origin+'/dispatches/new',old_policy)
                print('Política anterior reproduzida: Host=local; Origin=null; Referer=ausente; HTTP=403.',flush=True)
                observed.clear()

                # Every following mutation uses real native form submission.
                for channel_name,lot,qty in [('normal','LOTE-DEMO-A','12'),('mercadona','LOTE-DEMO-C','1')]:
                    open_page('/dispatches/new?channel='+channel_name)
                    page.locator('[name=lote_0]').fill(lot);page.locator('[name=quantidade_0]').fill(qty)
                    if channel_name=='mercadona':page.locator('[name=destination_0]').select_option(store.snapshot()['destinations'][0])
                    click('Guardar e rever seleção FIFO simulada')
                    order=store.snapshot()['orders'][-1];before=sum(r['quantidade'] for r in store.snapshot()['stock'])
                    click('Confirmar preparação simulada')
                    assert store.snapshot()['orders'][-1]['status']=='confirmed'
                    assert sum(r['quantidade'] for r in store.snapshot()['stock'])==before-sum(p['quantidade'] for p in order['picks'])
                    url=origin+f'/documents/order-{order["id"]}.pdf'
                    pdf=context.request.get(url).body();assert pdf.startswith(b'%PDF')
                    before=store.snapshot();assert context.request.get(url).body()==pdf and store.snapshot()==before

                open_page('/receipts/new')
                for name,value in dict(code='DEMO-NAVEGADOR',day='2025-03-01',artigo_0='ART-DEMO-SACO',lote_0='LOTE-DEMO-B',quantidade_0='4').items():
                    page.locator(f'[name={name}]').fill(value)
                before=store.snapshot();click('Rever entrada simulada e pendentes',200);assert store.snapshot()==before
                click('Confirmar receção simulada');receipt=store.snapshot()['receipts'][-1]
                assert receipt['lines'][0]['remaining']==1
                assert context.request.get(origin+f'/documents/receipt-{receipt["id"]}.pdf').body().startswith(b'%PDF')
                open_page('/putaway')
                form=page.locator(f'form:has(input[name=receipt_id][value="{receipt["id"]}"])')
                form.locator('[name=local]').fill('A104')
                with page.expect_response(lambda r:r.request.method=='POST') as sent:form.get_by_role('button').click()
                assert sent.value.status==303;page.wait_for_load_state('load')
                stock=next(s for s in store.snapshot()['stock'] if s['local']=='A104' and s['quantidade'])
                open_page('/transfers');page.locator('[name=stock_id]').select_option(str(stock['id']))
                page.locator('[name=local]').fill('A105');click('Confirmar transferência simulada')
                assert sim.occupied(store.snapshot(),'A104')==0 and sim.occupied(store.snapshot(),'A105')==1

                open_page('/inventory');page.locator('[name=locations]').fill('A105 A106')
                click('Criar inventário simulado');iid=store.snapshot()['inventories'][-1]['id']
                for location in ('A105','A106'):
                    open_page(f'/inventory/{iid}?local={location}')
                    page.locator('[name=empty]').check();click('Guardar contagem simulada')
                click('Submeter contagens sem alterar stock')
                assert sim.occupied(store.snapshot(),'A105')==1
                page.locator('[name=reason]').fill('Ajuste fictício do navegador');click('Aprovar e aplicar ajustes simulados')
                assert sim.occupied(store.snapshot(),'A105')==0
                assert context.request.get(origin+f'/documents/inventory-{iid}.pdf').body().startswith(b'%PDF')
                assert observed and all(r==dict(host='local',origin='local',referer='local',status=r['status']) and r['status'] in (200,303) for r in observed)
                print(f'{channel or "Chromium"}: {len(observed)} formulários nativos locais aprovados; entradas, saídas, Mercadona, arrumação, transferências, inventário e PDFs.',flush=True)

                # Same local origin without a CSRF token remains forbidden.
                open_page('/dispatches/new');before=store.snapshot()
                page.locator('[name=lote_0]').fill('LOTE-DEMO-A');page.locator('[name=quantidade_0]').fill('1')
                csrf=page.locator('form[action="/dispatches/save"] [name=csrf]').input_value()
                page.locator('form[action="/dispatches/save"] [name=csrf]').evaluate('(e)=>e.value=""')
                click('Guardar e rever seleção FIFO simulada',403);assert store.snapshot()==before

                # An external native form, even with a valid token, is rejected.
                # The external document is fulfilled in memory; no external network call.
                context.route('http://external.invalid/',lambda route:route.fulfill(content_type='text/html',body=
                    f'<form method="post" action="{origin}/demo/reset"><input name="csrf" value="{csrf}"><input name="confirm" value="REPOR"><button>Teste externo</button></form>'))
                page.goto('http://external.invalid/');click('Teste externo',403)
                assert store.snapshot()==before and observed[-1]['origin']=='externo-ou-invalido'
                print('CSRF em falta e formulário externo recusados (HTTP 403), sem alterar dados. Apenas classificações de cabeçalhos registadas.',flush=True)
            finally:browser.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--package',type=Path,default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--channel',choices=('chrome','msedge'))
    parser.add_argument('--executable',type=Path)
    args=parser.parse_args();run(args.package,args.channel,args.executable)
