"""One local process, prebound loopback socket, authenticated graceful stop; no PID killing."""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time
import urllib.request
import urllib.error
from contextlib import contextmanager
from . import MARKER
from .store import DemoError,safe_child


def default_home():
    return Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent.parent


def local_folder(home):
    home=Path(home).resolve(strict=True)
    folder=safe_child(home,'DEMO');folder.mkdir(exist_ok=True)
    return folder


def metadata(folder):
    path=safe_child(folder,'servidor.json')
    if not path.exists():return None
    try:data=json.loads(path.read_text(encoding='utf-8'))
    except (ValueError,OSError):raise DemoError('Ficheiro de controlo não reconhecido; não foi substituído.')
    if data.get('application')!=MARKER or not isinstance(data.get('port'),int) or not 1<=data['port']<=65535 or not isinstance(data.get('token'),str) or len(data['token'])!=64:
        raise DemoError('Controlo DEMO incompatível; não executar outro programa a partir deste ficheiro.')
    return data


def request_control(data,action='health'):
    # Explicitly disable proxies: never read a production proxy URL from the environment.
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url=f'http://127.0.0.1:{data["port"]}/__demo/{action}'
    req=urllib.request.Request(url,data=b'' if action=='stop' else None,headers={'x-demo-control':data['token']})
    with opener.open(req,timeout=1) as response:return json.load(response)


def alive(data):
    if not data:return False
    try:
        result=request_control(data)
        return result.get('application')==MARKER and hmac.compare_digest(result.get('instance',''),hashlib.sha256(data['token'].encode()).hexdigest())
    except (OSError,ValueError,urllib.error.URLError):return False


@contextmanager
def instance_lock(folder):
    path=safe_child(folder,'instancia.lock')
    with path.open('a+b') as stream:
        stream.seek(0)
        contents=stream.read()
        if contents not in (b'',MARKER.encode()):raise DemoError('Ficheiro de bloqueio não reconhecido. Não foi alterado.')
        if not contents:stream.write(MARKER.encode());stream.flush()
        stream.seek(0)
        acquired=False
        try:
            if os.name=='nt':
                import msvcrt
                try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1);acquired=True
                except OSError:pass
            else:
                import fcntl
                try:fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB);acquired=True
                except BlockingIOError:pass
            yield acquired
        finally:
            if acquired:
                stream.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


def browser(port):
    url=f'http://127.0.0.1:{port}/'
    try:
        if os.name=='nt':os.startfile(url)
        else:
            import webbrowser
            webbrowser.open(url)
    except OSError:print('Abra o navegador neste endereço: '+url,flush=True)


def start(home,port=0,open_browser=True):
    folder=local_folder(home)
    with instance_lock(folder) as acquired:
        if not acquired:
            for _ in range(30):
                data=metadata(folder)
                if alive(data):
                    print('A demonstração já está iniciada neste computador.',flush=True)
                    if open_browser:browser(data['port'])
                    return
                time.sleep(.1)
            raise DemoError('Outra instância DEMO está a iniciar ou encerrar. Aguarde alguns segundos.')
        old=metadata(folder)
        if alive(old):raise DemoError('Instância anterior ainda ativa; encerrar com PARAR_DEMO.bat.')
        # A socket chosen and held by this process avoids the usual free-port race.
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as listener:
            try:listener.bind(('127.0.0.1',port));listener.listen(128)
            except OSError as error:raise DemoError('Porta local indisponível. Feche outra DEMO ou deixe a porta automática.') from error
            listener.setblocking(False);actual_port=listener.getsockname()[1]
            token=secrets.token_hex(32)
            import tempfile
            temp=safe_child(folder,'temporarios');temp.mkdir(exist_ok=True);tempfile.tempdir=str(temp)
            from .app import create_app
            import uvicorn
            container={}
            app=create_app(home,actual_port,token,lambda:setattr(container['server'],'should_exit',True))
            server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=actual_port,workers=1,proxy_headers=False,
                access_log=False,log_level='warning',lifespan='off'))
            container['server']=server
            data=dict(application=MARKER,port=actual_port,token=token)
            path=safe_child(folder,'servidor.json');staged=safe_child(folder,'controlo-'+secrets.token_hex(8)+'.tmp')
            with staged.open('x',encoding='utf-8') as stream:json.dump(data,stream)
            os.replace(staged,path)
            done=threading.Event()
            def ready():
                for _ in range(100):
                    if done.wait(.1):return
                    if alive(data):
                        print(f'DEMONSTRAÇÃO — DADOS FICTÍCIOS\nAbra http://127.0.0.1:{actual_port}/\nPara encerrar: PARAR_DEMO.bat ou Ctrl+C nesta janela.',flush=True)
                        if open_browser:browser(actual_port)
                        return
            thread=threading.Thread(target=ready,daemon=True);thread.start()
            try:server.run(sockets=[listener])
            finally:
                done.set();thread.join(timeout=2)
                current=metadata(folder)
                if current and hmac.compare_digest(current['token'],token):path.unlink()
                print('Demonstração encerrada. Nenhum outro programa foi terminado.',flush=True)


def stop(home):
    folder=local_folder(home);data=metadata(folder)
    if not alive(data):print('A demonstração desta pasta já está encerrada.');return
    result=request_control(data,'stop')
    if result.get('application')!=MARKER or result.get('status')!='stopping':raise DemoError('Resposta de encerramento inesperada; nenhum processo foi terminado.')
    print('Encerramento pedido apenas à demonstração desta pasta.')


def main():
    parser=argparse.ArgumentParser(description='Iberbag WMS — DEMONSTRAÇÃO LOCAL, dados fictícios.')
    parser.add_argument('action',choices=['start','stop'])
    parser.add_argument('--home',type=Path,default=default_home())
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    try:
        if not 0<=args.port<=65535:raise DemoError('A porta deve ser 0 (automática) ou 1–65535.')
        if args.action=='start':start(args.home,args.port,not args.no_browser)
        else:stop(args.home)
    except (DemoError,OSError,ImportError) as error:
        print('Não foi possível iniciar/encerrar a DEMO: '+str(error),file=sys.stderr)
        print('Não utilizar os scripts do servidor operacional. Consultar LEIA-ME_DEMO.txt.',file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':sys.exit(main())
