"""Isolated demonstration tests; can run without SQLAlchemy/psycopg/Alembic installed."""
import base64
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import re
import secrets
import subprocess
import sys
import zipfile
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from iberbag_demo import simulation as sim,MARKER,BANNER
from iberbag_demo.store import Store,DemoError
from iberbag_demo.app import create_app


@pytest.fixture
def state():return sim.seed()


@pytest.fixture
def local(tmp_path):
    app=create_app(tmp_path,8765,secrets.token_hex(32))
    with TestClient(app,base_url='http://127.0.0.1:8765',client=('127.0.0.1',1234)) as client:
        page=client.get('/');csrf=re.search('name="csrf" value="([^"]+)"',page.text)[1]
        yield client,app.state.store,csrf


def test_seed_is_fictitious_representative_and_capacity_safe(state):
    assert state['marker']==MARKER and len(set(state['lots'].values()))==3
    assert state['lots']['LOTE-DEMO-A']==state['lots']['LOTE-DEMO-B']
    assert len(state['locations'])==2107 and 'L527' in state['locations'] and 'S427' in state['locations']
    assert all(sim.occupied(state,l)<=1 for l in state['locations'] if l!='Chão')
    assert len(state['loads'])>=4 and len(state['pending'])>=2 and state['allocations'] and state['adjustments']
    assert all(l.startswith('LOTE-DEMO') for l in state['lots'])


def test_simulated_fifo_immediate_partial_and_duplicate(state):
    oid=sim.save_preparation(state,'a','CLIENTE FICTÍCIO','normal','ENS','' or [('LOTE-DEMO-A','12','')],'Pessoa Fictícia')
    plan=deepcopy(sim.get(state,'orders',oid)['picks'])
    assert plan and plan[0]['carga']=='DEMO-ANTIGA'
    before=sum(s['quantidade'] for s in state['stock']);sim.confirm_preparation(state,oid,'Fictício')
    assert sum(s['quantidade'] for s in state['stock'])==before-sum(p['quantidade'] for p in plan)
    pending=[p for p in state['pending'] if p['order_id']==oid]
    assert pending[0]['original']==12-sum(p['quantidade'] for p in plan)
    snapshot=deepcopy(state);sim.confirm_preparation(state,oid,'Fictício');assert state==snapshot


@pytest.mark.parametrize('case',['unknown_date','missing_lot','fraction','negative'])
def test_invalid_fifo_cannot_become_backorder(state,case):
    if case=='unknown_date':state['loads']['DEMO-ANTIGA']=None
    with pytest.raises(DemoError):sim.save_preparation(state,'a','C','normal','R',[(('MISSING' if case=='missing_lot' else 'LOTE-DEMO-A'), '1.5' if case=='fraction' else '-1' if case=='negative' else '20','')],'Fictício')


def test_incoming_chronology_excess_and_physical_collection(local):
    client,store,csrf=local
    fields=dict(csrf=csrf,key='a'*32,code='DEMO-TEST',day='2025-01-01',artigo_0='ART-DEMO-SACO',lote_0='LOTE-DEMO-B',quantidade_0='4')
    before=store.snapshot();review=client.post('/receipts/review',data=fields)
    assert review.status_code==200 and 'CLIENTE DEMO ALFA' in review.text and store.snapshot()==before
    saved=client.post('/receipts/confirm',data=fields);assert saved.status_code==200
    receipt=store.snapshot()['receipts'][-1];assert receipt['lines'][0]['remaining']==1
    new=store.snapshot()['allocations'][1:];assert [(a['customer'],a['quantidade']) for a in new]==[('CLIENTE DEMO ALFA',1),('CLIENTE DEMO BETA',2)]
    snapshot=store.snapshot();client.post('/receipts/confirm',data=fields);assert store.snapshot()==snapshot
    total=sum(s['quantidade'] for s in snapshot['stock']);client.post('/pending/1/collect',data={'csrf':csrf})
    assert sum(s['quantidade'] for s in store.snapshot()['stock'])==total
    first=client.get('/documents/receipt-'+str(receipt['id'])+'.pdf').content
    assert first==client.get('/documents/receipt-'+str(receipt['id'])+'.pdf').content
    assert BANNER in PdfReader(BytesIO(first)).pages[0].extract_text()


def test_putaway_transfer_capacity_and_new_article_lot_constraint(state):
    rid=sim.receive(state,'new','DEMO-TEST','2025-01-01',[('ART-DEMO-SACO','LOTE-DEMO-A','3')],'Fictício')
    with pytest.raises(DemoError):sim.putaway(state,rid,0,'A104','2','invalid','Fictício')
    sim.putaway(state,rid,0,'A104','1','ok','Fictício')
    stock=next(s for s in state['stock'] if s['local']=='A104' and s['quantidade'])
    sim.move(state,stock['id'],'A105','1','move','Fictício')
    assert sim.occupied(state,'A104')==0 and sim.occupied(state,'A105')==1
    with pytest.raises(DemoError):sim.putaway(state,rid,0,'A105','1','bad','Fictício')
    with pytest.raises(DemoError):sim.receive(state,'bad','DEMO-BAD','2025-01-01',[('WRONG','LOTE-DEMO-A','1')],'Fictício')


def test_inventory_unknown_zero_partial_full_and_rollback(local):
    client,store,csrf=local
    response=client.post('/inventory/create',data=dict(csrf=csrf,key='a'*32,kind='partial',locations='A102 A103'))
    iid=store.snapshot()['inventories'][-1]['id'];url=f'/inventory/{iid}'
    assert 'desconhecido' in response.text.lower()
    assert client.post(url+'/submit',data={'csrf':csrf}).status_code==409
    for local in ('A102','A103'):assert client.post(url+'/count',data=dict(csrf=csrf,local=local,empty=1)).status_code==200
    assert client.post(url+'/submit',data={'csrf':csrf}).status_code==200
    client.post('/demo/role',data=dict(csrf=csrf,role='operador'))
    assert client.post(url+'/approve',data=dict(csrf=csrf,reason='Fictício')).status_code==403
    client.post('/demo/role',data=dict(csrf=csrf,role='admin'))
    assert client.post(url+'/approve',data=dict(csrf=csrf,reason='Fictício')).status_code==200
    assert sim.occupied(store.snapshot(),'A102')==0
    first=store.snapshot();client.post(url+'/approve',data=dict(csrf=csrf,reason='Fictício'));assert store.snapshot()==first
    client.post('/inventory/create',data=dict(csrf=csrf,key='b'*32,kind='full'))
    full=store.snapshot()['inventories'][-1];assert len(full['scope'])==2107 and not full['counts']
    f='/inventory/'+str(full['id'])
    assert client.post(f+'/example-count',data=dict(csrf=csrf)).status_code==409
    assert client.post(f+'/example-count',data=dict(csrf=csrf,confirm='SIMULAR')).status_code==200
    assert client.post(f+'/submit',data=dict(csrf=csrf)).status_code==200
    pdf=client.get('/documents/inventory-'+str(full['id'])+'.pdf');assert pdf.content.startswith(b'%PDF')


def test_inventory_movements_and_store_failure_rollback(tmp_path):
    store=Store(tmp_path);before=store.snapshot()
    def bad(s):
        s['stock'][0]['quantidade']=1000
        raise DemoError('Erro fictício')
    with pytest.raises(DemoError):store.change(bad)
    assert store.snapshot()==before
    def stale(s):
        iid=sim.create_inventory(s,'partial',['A104'],'new','Fictício')
        s['stock_version']+=1
        sim.save_count(s,iid,'A104',[],True,'Fictício')
    with pytest.raises(DemoError,match='movimentos'):store.change(stale)
    assert store.snapshot()==before


@pytest.mark.parametrize('url',['/','/stock?artigo=ART-DEMO','/map?corredor=M&andar=5','/receipts','/receipts/new','/receipts/1','/dispatches','/dispatches/new?channel=mercadona','/dispatches/1','/putaway','/transfers','/inventory','/inventory/2','/pending','/settings','/users','/audit','/compositions','/demo/reset'])
def test_navigation_and_permanent_banner(local,url):
    client,store,csrf=local
    response=client.get(url);assert response.status_code==200 and BANNER in response.text
    assert 'https://' not in response.text and '<script' not in response.text


def test_localhost_csrf_and_control_isolation(local):
    client,store,csrf=local;before=store.snapshot()
    assert client.get('/',headers={'host':'evil.example'}).status_code==403
    assert client.post('/demo/reset',data=dict(csrf=csrf,confirm='REPOR'),headers={'origin':'https://evil.example'}).status_code==403
    assert client.post('/demo/reset',data=dict(confirm='REPOR')).status_code==403
    assert client.post('/__demo/stop',headers={'x-demo-control':'wrong'}).status_code==403
    with TestClient(client.app,base_url='http://127.0.0.1:8765',client=('192.0.2.1',1234)) as remote:assert remote.get('/').status_code==403
    assert before==store.snapshot()


def test_reset_only_owned_data_and_refuses_external_links(local,tmp_path):
    client,store,csrf=local
    external=tmp_path/'pessoal.txt';external.write_text('NAO ALTERAR')
    extra=store.folder/'guardar.txt';extra.write_text('CONSERVAR')
    store.change(lambda s:s['destinations'].append('TESTE FICTÍCIO'))
    assert client.post('/demo/reset',data=dict(csrf=csrf)).status_code==409
    assert client.post('/demo/reset',data=dict(csrf=csrf,confirm='REPOR')).status_code==200
    assert 'TESTE FICTÍCIO' not in store.snapshot()['destinations']
    assert external.read_text()=='NAO ALTERAR' and extra.read_text()=='CONSERVAR'


def test_existing_non_demo_database_never_overwritten(tmp_path):
    folder=tmp_path/'DEMO';folder.mkdir();path=folder/'estado.sqlite';path.write_bytes(b'FICHEIRO DO UTILIZADOR')
    with pytest.raises(DemoError):Store(tmp_path)
    assert path.read_bytes()==b'FICHEIRO DO UTILIZADOR'


def test_simulator_imports_no_operational_database_and_ignores_env(tmp_path):
    import os
    code='''
import socket,sys,secrets
from pathlib import Path
original=socket.socket.connect
def no_network(*args,**kwargs):raise AssertionError('Ligação de rede proibida no seed')
socket.socket.connect=no_network
from iberbag_demo.app import create_app
app=create_app(sys.argv[1],8877,secrets.token_hex(32))
assert 'iberbag.database' not in sys.modules and 'iberbag.app' not in sys.modules
assert app.state.store.path.parent==Path(sys.argv[1])/'DEMO'
assert app.state.store.snapshot()['marker']=='IBERBAG_LOCAL_DEMO_V1'
'''
    env=dict(os.environ,WMS_DATABASE_URL='postgresql+psycopg://invalid:invalid@192.0.2.1/production',WMS_PG_BIN=str(tmp_path/'forbidden'),PGHOST='192.0.2.1')
    subprocess.run([sys.executable,'-c',code,str(tmp_path)],env=env,check=True,capture_output=True,timeout=20)


def test_demo_pdf_watermark_on_every_page():
    state=sim.seed();sim.archive(state,'long','Exemplo longo',('Artigo','Lote'),[('ART-DEMO','LOTE-DEMO') for _ in range(150)])
    reader=PdfReader(BytesIO(base64.b64decode(state['documents']['long'])))
    assert len(reader.pages)>1 and all(BANNER in p.extract_text() for p in reader.pages)


def test_fallback_package_allowlist(tmp_path):
    from scripts.build_demo import build
    archive=build(tmp_path/'fallback')
    with zipfile.ZipFile(archive) as z:
        names=z.namelist();assert 'INICIAR_DEMO.bat' in names and 'INSTALAR_DEMO.bat' in names
        assert 'iberbag/database.py' not in names and 'iberbag/app.py' not in names
        assert not any(n.endswith(('.xlsm','.db','.sqlite','.env','.dump')) for n in names)


def test_real_loopback_start_reuse_flows_and_stop(tmp_path):
    from scripts.smoke_demo_package import smoke
    # Source invocation covers Linux/Windows; frozen binary is tested separately by the workflow.
    smoke(tmp_path,[sys.executable,'-m','iberbag_demo'])


def test_demo_root_links_are_refused(tmp_path):
    home=tmp_path/'home';home.mkdir();other=tmp_path/'other';other.mkdir()
    sentinel=other/'dados.txt';sentinel.write_text('CONSERVAR')
    try:(home/'DEMO').symlink_to(other,target_is_directory=True)
    except OSError:pytest.skip('Criação de symlink indisponível sem privilégios nesta máquina.')
    with pytest.raises(DemoError):Store(home)
    assert sentinel.read_text()=='CONSERVAR' and not (other/'estado.sqlite').exists()


def test_stop_never_terminates_an_unrelated_local_service(tmp_path):
    from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
    from threading import Thread
    from iberbag_demo.__main__ import stop
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            data=b'{"application":"OUTRO_PROGRAMA"}'
            self.send_response(200);self.end_headers();self.wfile.write(data)
        def log_message(self,*args):pass
        def do_POST(self):raise AssertionError('Não enviar encerramento a outro serviço')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    folder=tmp_path/'DEMO';folder.mkdir();(folder/'servidor.json').write_text(json.dumps(dict(application=MARKER,port=server.server_port,token=secrets.token_hex(32))))
    try:
        stop(tmp_path);assert thread.is_alive()
    finally:server.shutdown();thread.join(timeout=2);server.server_close()


def test_demo_rejects_additional_operational_tables(tmp_path):
    import sqlite3
    store=Store(tmp_path)
    with sqlite3.connect(store.path) as conn:conn.execute('CREATE TABLE stock(id INTEGER)')
    before=store.path.read_bytes()
    with pytest.raises(DemoError,match='exclusiva'):Store(tmp_path)
    assert store.path.read_bytes()==before


def test_fallback_isolated_entry_uses_only_its_package(tmp_path):
    from scripts.build_demo import build
    folder=tmp_path/'Pacote fonte ação';build(folder)
    code="import sys;sys.path.insert(0,sys.argv[1]);from iberbag_demo.app import create_app;app=create_app(sys.argv[1],8877,__import__('secrets').token_hex(32));assert 'iberbag.database' not in sys.modules;assert 'iberbag.app' not in sys.modules"
    subprocess.run([sys.executable,'-I','-B','-c',code,str(folder)],cwd=folder,check=True,capture_output=True,timeout=20)
