"""Local-only UI sharing the operational layout, without importing its app or services."""
import base64
import hmac
import hashlib
import logging
import secrets
from pathlib import Path
from types import SimpleNamespace as R
from urllib.parse import parse_qs,quote,urlsplit
from fastapi import FastAPI,Request,HTTPException
from fastapi.responses import Response,RedirectResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import FileSystemLoader
import iberbag
from . import MARKER,BANNER
from .store import Store,DemoError
from . import simulation as sim
from .inventory_print import inventory_map_pdf


security_log=logging.getLogger('iberbag_demo.security')


def header_kind(headers,name,expected):
    """Classify only; never put raw headers, URLs, cookies or tokens in diagnostics."""
    values=headers.getlist(name)
    if not values:return 'ausente'
    if len(values)!=1:return 'repetido'
    value=values[0]
    if name=='origin' and value=='null':return 'null'
    if name!='referer':return 'local' if value==expected else 'externo-ou-invalido'
    try:
        # Referrer paths/queries may contain secrets. Inspect the origin, never log the URL.
        if any(ord(c)<=32 for c in value) or '\\' in value:return 'externo-ou-invalido'
        parsed=urlsplit(value)
        if parsed.fragment or parsed.username is not None or parsed.password is not None:return 'externo-ou-invalido'
        return 'local' if f'{parsed.scheme}://{parsed.netloc}'==expected else 'externo-ou-invalido'
    except ValueError:return 'externo-ou-invalido'


def create_app(home,port,control_token,stop_callback=lambda:None):
    store=Store(home)
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    app.state.store=store
    sessions={}
    base=Path(iberbag.__file__).parent
    templates=Jinja2Templates(directory=Path(__file__).parent/'templates')
    templates.env.loader=FileSystemLoader([str(Path(__file__).parent/'templates'),str(base/'templates')])
    app.mount('/static',StaticFiles(directory=base/'static'),name='static')
    origin=f'http://127.0.0.1:{port}'

    @app.middleware('http')
    async def local_only(request,call_next):
        # No proxy trust, CORS, DNS names or network clients. Reject DNS rebinding/foreign origins.
        kinds={name:header_kind(request.headers,name,expected) for name,expected in
            (('host',f'127.0.0.1:{port}'),('origin',origin),('referer',origin))}
        reason=None
        if not request.client or request.client.host!='127.0.0.1' or kinds['host']!='local':
            reason='acesso-local';detail='DEMO acessível apenas no próprio computador.'
        elif kinds['origin'] not in ('ausente','local') or kinds['referer'] not in ('ausente','local'):
            reason='origem';detail='Origem externa recusada.'
            if kinds['origin']=='null':
                detail='Origem não identificada (Origin: null). Feche os separadores antigos e volte a abrir a DEMO pelo endereço 127.0.0.1 apresentado na janela.'
        if reason:
            security_log.warning('Pedido DEMO recusado: motivo=%s; Host=%s; Origin=%s; Referer=%s',reason,kinds['host'],kinds['origin'],kinds['referer'])
            return JSONResponse({'detail':detail},403)
        session_id=request.cookies.get('iberbag_demo')
        fresh=session_id not in sessions
        if fresh:
            if len(sessions)>=64:sessions.clear()
            session_id=secrets.token_hex(32);sessions[session_id]=dict(role='admin',csrf=secrets.token_hex(32))
        request.state.session=sessions[session_id]
        response=await call_next(request)
        if fresh:response.set_cookie('iberbag_demo',session_id,httponly=True,samesite='strict')
        # no-referrer makes Chromium send Origin: null on same-origin form POSTs.
        # same-origin preserves local Origin/Referer without sending a referrer externally.
        response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'same-origin',
            'Content-Security-Policy':"default-src 'self'; script-src 'none'; style-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'"})
        return response

    def render(request,view,**values):
        session=request.state.session
        user=R(id=1,role=session['role'],display_name='Escritório DEMO',username='Pessoa Fictícia')
        return templates.TemplateResponse(request,view,dict(request=request,user=user,csrf=session['csrf'],demo_mode=True,banner=BANNER,
            state=store.snapshot(),sim=sim,**values))

    async def form(request,admin=False):
        if request.headers.get('content-type','').split(';')[0]!='application/x-www-form-urlencoded':raise HTTPException(400,'Formulário inválido.')
        body=bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body)>65536:raise HTTPException(413,'Formulário demasiado grande.')
        try:fields=parse_qs(body.decode('utf-8','strict'),keep_blank_values=True)
        except UnicodeError:raise HTTPException(400,'Codificação inválida.')
        if any(len(v)!=1 for v in fields.values()):raise HTTPException(400,'Campos repetidos.')
        data={k:v[0] for k,v in fields.items()}
        if not hmac.compare_digest(data.get('csrf',''),request.state.session['csrf']):raise HTTPException(403,'Formulário expirado ou inválido (CSRF).')
        if admin and request.state.session['role']!='admin':raise HTTPException(403,'Aprovação/configuração apenas no perfil Administrador demonstrativo.')
        return data

    def key(data):
        value=data.get('key','')
        if not re_key(value):raise DemoError('Pedido inválido. Abrir um formulário novo.')
        return value

    def redirect(url):return RedirectResponse(url,303)
    def actor(request):return 'Administrador Fictício' if request.state.session['role']=='admin' else 'Operador Fictício'

    @app.exception_handler(DemoError)
    async def error(request,exc):
        response=render(request,'demo_message.html',title='Simulação não confirmada',message=str(exc));response.status_code=409
        return response

    @app.get('/__demo/health')
    def health():return dict(application=MARKER,mode='simulation',local_only=True,instance=hashlib.sha256(control_token.encode()).hexdigest())

    @app.post('/__demo/stop')
    def stop(request:Request):
        if not hmac.compare_digest(request.headers.get('x-demo-control',''),control_token):raise HTTPException(403,'Pedido de encerramento inválido.')
        stop_callback();return dict(application=MARKER,status='stopping')

    @app.get('/')
    def dashboard(request:Request):return render(request,'demo_dashboard.html')

    @app.get('/stock')
    def stock(request:Request,artigo:str='',lote:str='',local:str=''):
        state=store.snapshot()
        rows=[s for s in state['stock'] if s['quantidade']>0 and all(v.casefold() in s[k].casefold() for k,v in [('artigo',artigo),('lote',lote),('local',local)])]
        return render(request,'demo_stock.html',rows=rows,filters=dict(artigo=artigo,lote=lote,local=local),detail=None)

    @app.get('/stock/{ident}')
    def stock_detail(ident:int,request:Request):return render(request,'demo_stock.html',rows=[],filters={},detail=sim.get(store.snapshot(),'stock',ident))

    @app.get('/map')
    def map_page(request:Request,corredor:str='A',andar:int=1):
        if corredor not in 'ABCDEFGHIJKLMNOPQRS' or len(corredor)!=1:corredor='A'
        floors=5 if corredor in 'LM' else 4;andar=max(1,min(andar,floors));s=store.snapshot()
        return render(request,'demo_map.html',corredor=corredor,andar=andar,floors=floors,
            slots=[dict(code=f'{corredor}{andar}{p:02d}',qty=sim.occupied(s,f'{corredor}{andar}{p:02d}')) for p in range(1,28)])

    @app.get('/receipts')
    def receipts(request:Request):return render(request,'demo_receipts.html',new=False,receipt=None,request_key=secrets.token_hex(16))

    @app.get('/receipts/new')
    def receipt_new(request:Request):return render(request,'demo_receipts.html',new=True,receipt=None,request_key=secrets.token_hex(16))

    @app.get('/receipts/{ident}')
    def receipt_detail(ident:int,request:Request):return render(request,'demo_receipts.html',new=False,receipt=sim.get(store.snapshot(),'receipts',ident),request_key='')

    @app.post('/receipts/review')
    async def receipt_review(request:Request):
        data=await form(request)
        # The draft is a read-only preview; token/fields are posted back for explicit confirmation.
        rows=rows_from(data,('artigo','lote','quantidade'))
        s=store.snapshot()
        sim.receive(s,key(data),data.get('code'),data.get('day'),rows,actor(request))
        preview=s['receipts'][-1];assigned=[a for a in s['allocations'] if a['receipt_id']==preview['id']]
        return render(request,'demo_receipt_review.html',data=data,preview=preview,assigned=assigned)

    @app.post('/receipts/confirm')
    async def receipt_confirm(request:Request):
        data=await form(request)
        ident=store.change(lambda s:sim.receive(s,key(data),data.get('code'),data.get('day'),rows_from(data,('artigo','lote','quantidade')),actor(request)))
        return redirect(f'/receipts/{ident}')

    @app.get('/putaway')
    def putaway(request:Request):return render(request,'demo_putaway.html',request_key=secrets.token_hex(16))

    @app.post('/putaway')
    async def putaway_apply(request:Request):
        data=await form(request)
        store.change(lambda s:sim.putaway(s,int_value(data.get('receipt_id')),int_value(data.get('line')),data.get('local'),data.get('quantidade'),key(data),actor(request)))
        return redirect('/putaway')

    @app.get('/dispatches')
    def dispatches(request:Request):return render(request,'demo_dispatches.html',new=False,order=None,channel='normal',request_key='')

    @app.get('/dispatches/new')
    def dispatch_new(request:Request,channel:str='normal'):return render(request,'demo_dispatches.html',new=True,order=None,channel=channel if channel=='mercadona' else 'normal',request_key=secrets.token_hex(16))

    @app.post('/dispatches/save')
    async def dispatch_save(request:Request):
        data=await form(request)
        rows=rows_from(data,('lote','quantidade','destination'))
        ident=store.change(lambda s:sim.save_preparation(s,key(data),data.get('customer'),data.get('channel'),data.get('reference'),rows,actor(request)))
        return redirect(f'/dispatches/{ident}')

    @app.get('/dispatches/{ident}')
    def dispatch_detail(ident:int,request:Request):return render(request,'demo_dispatches.html',new=False,order=sim.get(store.snapshot(),'orders',ident),channel='',request_key='')

    @app.post('/dispatches/{ident}/confirm')
    async def dispatch_confirm(ident:int,request:Request):
        await form(request);store.change(lambda s:sim.confirm_preparation(s,ident,actor(request)))
        return redirect(f'/dispatches/{ident}')

    @app.get('/pending')
    def pending(request:Request,customer:str='',lote:str=''):
        rows=[p for p in store.snapshot()['pending'] if customer.casefold() in p['customer'].casefold() and lote.casefold() in p['lote'].casefold()]
        return render(request,'demo_pending.html',rows=rows,customer=customer,lote=lote)

    @app.post('/pending/{ident}/collect')
    async def collect(ident:int,request:Request):
        await form(request,True)
        def apply(s):
            item=sim.get(s,'allocations',ident)
            if not item['collected']:item['collected']=True;s['stock_version']+=1;sim.event(s,'Recolha','Recolha física simulada, sem novo desconto.',actor(request))
        store.change(apply);return redirect('/pending')

    @app.get('/transfers')
    def transfers(request:Request):return render(request,'demo_transfers.html',request_key=secrets.token_hex(16))

    @app.post('/transfers')
    async def transfer(request:Request):
        data=await form(request)
        store.change(lambda s:sim.move(s,int_value(data.get('stock_id')),data.get('local'),data.get('quantidade'),key(data),actor(request)))
        return redirect('/transfers')

    @app.get('/inventory/mapa-verificacao.pdf')
    def inventory_print(request:Request, corredor:str='A', formato:str='compacto'):
        try:
            content=inventory_map_pdf(store.snapshot(),corredor,formato)
        except ValueError:
            raise HTTPException(400,'Selecionar um corredor e formato validos.')
        return Response(content,media_type='application/pdf',
                        headers={'Content-Disposition':'inline; filename="inventario-demo-'+corredor.upper()+'-'+formato+'.pdf"'})

    @app.get('/inventory')
    def inventories(request:Request):return render(request,'demo_inventory.html',inv=None,request_key=secrets.token_hex(16),rows=[])

    @app.post('/inventory/create')
    async def inventory_create(request:Request):
        data=await form(request)
        ident=store.change(lambda s:sim.create_inventory(s,data.get('kind'),data.get('locations','').split(),key(data),actor(request)))
        return redirect(f'/inventory/{ident}')

    @app.get('/inventory/{ident}')
    def inventory_detail(ident:int,request:Request,local:str='',page:int=1):
        inv=sim.get(store.snapshot(),'inventories',ident);page=max(1,page)
        return render(request,'demo_inventory.html',inv=inv,request_key='',rows=sim.differences(inv),local=local,page=page,scope=inv['scope'][(page-1)*50:page*50])

    @app.post('/inventory/{ident}/count')
    async def inventory_count(ident:int,request:Request):
        data=await form(request)
        rows=[]
        for raw in data.get('groups','').splitlines():
            if not raw.strip():continue
            cells=[c.strip() for c in raw.split(';')]
            if len(cells)!=4:raise DemoError('Usar artigo;lote;carga;paletes, uma linha por grupo.')
            rows.append(cells)
        store.change(lambda s:sim.save_count(s,ident,data.get('local'),rows,data.get('empty')=='1',actor(request)))
        return redirect(f'/inventory/{ident}?local='+quote(data.get('local','')))

    @app.post('/inventory/{ident}/submit')
    async def inventory_submit(ident:int,request:Request):
        await form(request);store.change(lambda s:sim.submit_inventory(s,ident,actor(request)));return redirect(f'/inventory/{ident}')

    @app.post('/inventory/{ident}/approve')
    async def inventory_approve(ident:int,request:Request):
        data=await form(request,True);store.change(lambda s:sim.approve(s,ident,data.get('reason'),actor(request)));return redirect(f'/inventory/{ident}')

    @app.get('/settings')
    @app.get('/settings/locations')
    @app.get('/settings/mercadona')
    @app.get('/users')
    @app.get('/audit')
    def settings(request:Request):return render(request,'demo_settings.html',request_key=secrets.token_hex(16))

    @app.post('/settings/location')
    async def setting_location(request:Request):
        data=await form(request,True)
        def apply(s):
            local=data.get('local')
            if local not in s['locations'] or local=='Chão':raise DemoError('Escolher uma posição normal existente.')
            if sim.occupied(s,local):raise DemoError('Não alterar uma posição ocupada.')
            if local in s['inactive']:s['inactive'].remove(local)
            else:s['inactive'].append(local)
            s['stock_version']+=1;sim.event(s,'Definições',f'Estado simulado de {local} alterado.',actor(request))
        store.change(apply);return redirect('/settings/locations')

    @app.post('/settings/destination')
    async def destination(request:Request):
        data=await form(request,True)
        def apply(s):
            name=sim.label(data.get('name'))
            if name not in s['destinations']:s['destinations'].append(name)
        store.change(apply);return redirect('/settings/mercadona')

    @app.get('/compositions')
    def compositions(request:Request):return render(request,'demo_message.html',title='Composições de paletes incompletas — ilustrativo',message='A versão principal prepara dossiês documentais. Esta DEMO não altera caixas, cria lotes sintéticos nem simula a expedição de paletes mistas. Uma palete física continua a ocupar uma posição.')

    @app.post('/demo/role')
    async def role(request:Request):
        data=await form(request)
        if data.get('role') not in ('admin','operador'):raise DemoError('Perfil inválido.')
        request.state.session['role']=data['role'];return redirect('/settings')

    @app.post('/logout')
    async def logout(request:Request):
        await form(request);request.state.session['role']='operador';return redirect('/settings')

    @app.post('/demo/exit')
    async def exit_demo(request:Request):
        await form(request);stop_callback()
        return render(request,'demo_message.html',title='Demonstração encerrada',message='Pode fechar este separador. Apenas o servidor local da demonstração foi encerrado.')

    @app.post('/inventory/{ident}/example-count')
    async def example_count(ident:int,request:Request):
        data=await form(request)
        if data.get('confirm')!='SIMULAR':raise DemoError('Confirmar explicitamente o preenchimento fictício.')
        def apply(s):
            inv=sim.get(s,'inventories',ident)
            if inv['status']!='draft' or inv['version']!=s['stock_version']:raise DemoError('Inventário já submetido ou desatualizado.')
            for local in inv['scope']:
                rows=[(r['artigo'],r['lote'],r['carga'],str(r['quantidade'])) for r in inv['baseline'] if r['local']==local and r['quantidade']>0]
                sim.save_count(s,ident,local,rows,not rows,actor(request))
        store.change(apply);return redirect(f'/inventory/{ident}')

    @app.get('/demo/reset')
    def reset_page(request:Request):return render(request,'demo_reset.html')

    @app.post('/demo/reset')
    async def reset(request:Request):
        data=await form(request,True)
        if data.get('confirm')!='REPOR':raise DemoError('Confirmar explicitamente a reposição dos dados fictícios.')
        store.reset();return render(request,'demo_message.html',title='Reposição concluída',message='Os dados fictícios iniciais desta demonstração foram recriados. Nenhum ficheiro externo foi apagado.')

    @app.get('/documents/{key}.pdf')
    def pdf(key:str):
        value=store.snapshot()['documents'].get(key)
        if value is None:raise HTTPException(404,'PDF simulado não disponível.')
        return Response(base64.b64decode(value),media_type='application/pdf',headers={'Content-Disposition':f'inline; filename="demo-{key}.pdf"'})

    return app


def re_key(value):
    import re
    return bool(re.fullmatch('[a-f0-9]{32}',value))


def int_value(value):
    try:return int(value)
    except (ValueError,TypeError):raise DemoError('Escolher um registo válido.')


def rows_from(data,columns):
    rows=[]
    for i in range(20):
        row=tuple(data.get(f'{c}_{i}','').strip() for c in columns)
        if any(row):rows.append(row)
    return rows
