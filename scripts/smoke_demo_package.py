"""End-to-end test of an extracted/frozen package over real loopback HTTP, without PostgreSQL."""
import base64
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import urllib.error


def smoke(folder,command=None):
    folder=Path(folder).resolve()
    if command is None:raise RuntimeError('Ensaio do executável suspenso após alerta antivírus. Usar --source com Python oficial.')
    env=dict(os.environ,WMS_DATABASE_URL='postgresql+psycopg://invalid:invalid@192.0.2.1/forbidden',PGHOST='192.0.2.1')
    env['PYTHONPATH']=str(Path(__file__).resolve().parent.parent)
    output=folder/'ensaio-arranque.txt'
    external=folder.parent/'sentinela-demo-externa.txt'
    if external.exists():raise RuntimeError('Usar pasta de ensaio nova; não substituir sentinelas existentes.')
    external.write_bytes(b'FICHEIRO EXTERNO FICTICIO NAO ALTERAR')
    original=external.read_bytes()
    proc=None
    with output.open('w',encoding='utf-8') as log:
        try:
            proc=subprocess.Popen(command+['start','--home',str(folder),'--no-browser'],cwd=folder,env=env,stdout=log,stderr=log)
            control=folder/'DEMO'/'servidor.json';data=None
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            deadline=time.monotonic()+40
            while time.monotonic()<deadline:
                if proc.poll() is not None:raise AssertionError('Servidor terminou no arranque: '+output.read_text(encoding='utf-8',errors='replace'))
                if control.exists():
                    data=json.loads(control.read_text(encoding='utf-8'))
                    try:
                        with opener.open(f'http://127.0.0.1:{data["port"]}/__demo/health',timeout=1) as r:
                            health=json.load(r)
                        if health['application']=='IBERBAG_LOCAL_DEMO_V1':break
                    except OSError:pass
                time.sleep(.1)
            else:raise AssertionError('DEMO não ficou disponível em 40 segundos.')
            base=f'http://127.0.0.1:{data["port"]}'
            def get(url):
                with opener.open(base+url,timeout=5) as r:return r.read()
            def post(url,fields):
                req=urllib.request.Request(base+url,data=urllib.parse.urlencode(fields).encode())
                with opener.open(req,timeout=10) as r:return r.geturl(),r.read()
            page=get('/').decode();assert 'DEMONSTRAÇÃO — DADOS FICTÍCIOS' in page
            csrf=re.search('name="csrf" value="([^"]+)"',page)[1]
            for url in ('/stock','/map?corredor=L&andar=5','/pending','/receipts','/putaway','/dispatches','/transfers','/inventory','/settings','/compositions'):
                assert 'DEMONSTRAÇÃO'.encode() in get(url)
            assert b'LOTE-DEMO-A' in get('/stock?lote=LOTE-DEMO-A')
            assert get('/documents/order-1.pdf').startswith(b'%PDF')
            before=get('/documents/order-1.pdf')
            url,page=post('/dispatches/save',dict(csrf=csrf,key='a'*32,customer='CLIENTE DEMO TESTE',channel='normal',reference='ENSAIO PACOTE',lote_0='LOTE-DEMO-A',quantidade_0='12'))
            path=urllib.parse.urlsplit(url).path
            post(path+'/confirm',dict(csrf=csrf));first=get('/documents/order-'+path.rsplit('/',1)[1]+'.pdf')
            post(path+'/confirm',dict(csrf=csrf));assert get('/documents/order-'+path.rsplit('/',1)[1]+'.pdf')==first
            fields=dict(csrf=csrf,key='b'*32,code='DEMO-PACOTE-ENTRADA',day='2025-01-02',artigo_0='ART-DEMO-SACO',lote_0='LOTE-DEMO-B',quantidade_0='4')
            assert b'CLIENTE DEMO' in post('/receipts/review',fields)[1]
            receipt_url,_=post('/receipts/confirm',fields);rid=int(urllib.parse.urlsplit(receipt_url).path.rsplit('/',1)[1])
            post('/putaway',dict(csrf=csrf,key='c'*32,receipt_id=rid,line=0,local='A104',quantidade=1))
            stock_page=get('/stock?local=A104').decode();stock_id=re.search('/stock/([0-9]+)',stock_page)[1]
            post('/transfers',dict(csrf=csrf,key='d'*32,stock_id=stock_id,local='A105',quantidade=1))
            inv_url,_=post('/inventory/create',dict(csrf=csrf,key='e'*32,kind='partial',locations='A105 A106'));ipath=urllib.parse.urlsplit(inv_url).path
            for local in ('A105','A106'):post(ipath+'/count',dict(csrf=csrf,local=local,empty=1))
            post(ipath+'/submit',dict(csrf=csrf));post(ipath+'/approve',dict(csrf=csrf,reason='Ajuste fictício do ensaio'))
            assert b'CLIENTE DEMO' in get('/pending')
            assert get('/documents/order-1.pdf')==before
            try:
                opener.open(urllib.request.Request(base+'/__demo/health',headers={'Host':'external.invalid'}),timeout=2)
            except urllib.error.HTTPError as error:
                assert error.code==403
            else:raise AssertionError('Host externo aceite')
            assert 'Reposição concluída'.encode() in post('/demo/reset',dict(csrf=csrf,confirm='REPOR'))[1]
            assert original==external.read_bytes()
            repeated=subprocess.run(command+['start','--home',str(folder),'--no-browser'],cwd=folder,env=env,capture_output=True,timeout=15)
            assert repeated.returncode==0 and proc.poll() is None
            stop_command=command+['stop','--home',str(folder)]
            if os.name=='nt' and (folder/'PARAR_DEMO.bat').exists():
                stop_command=[str(Path(os.environ['SystemRoot'])/'System32'/'cmd.exe'),'/d','/c',str(folder/'PARAR_DEMO.bat')]
            stopped=subprocess.run(stop_command,cwd=folder,env=env,capture_output=True,timeout=15)
            assert stopped.returncode==0
            assert proc.wait(timeout=15)==0
            assert not control.exists()
            print('Pacote: arranque local, navegação, FIFO/pendentes, entradas, arrumação, transferência, inventário, PDFs, reposição e encerramento verificados.')
        finally:
            if proc and proc.poll() is None:
                subprocess.run(command+['stop','--home',str(folder)],cwd=folder,env=env,capture_output=True,timeout=10)
                try:proc.wait(timeout=10)
                except subprocess.TimeoutExpired:proc.terminate();proc.wait(timeout=10)  # Only the child created by this test.
            external.unlink()


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('folder',type=Path);parser.add_argument('--source',action='store_true')
    args=parser.parse_args()
    command=None
    if args.source:
        python=args.folder.resolve()/'demo-env'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        entry='import sys;sys.path.insert(0,sys.argv.pop(1));from iberbag_demo.__main__ import main;raise SystemExit(main())'
        command=[str(python),'-I','-B','-c',entry,str(args.folder.resolve())]
    smoke(args.folder,command)
