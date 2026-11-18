"""Noninteractive service entry; credential read only by the virtual service identity."""
import argparse,base64,json,os,sys
from pathlib import Path

def configure(profile, encoded):
    from sqlalchemy.engine import URL
    if profile.get('database')!='ddn_wms_ensaio_test' or profile.get('user')!='ddn_wms_runtime':
        raise ValueError('Test target required')
    ports=[profile[k] for k in ('postgres','http','https')]
    if any(type(p) is not int or not 1024<=p<=65535 or p in {5432,8000,55432,8765,8443} for p in ports) or len(set(ports))!=3:
        raise ValueError('Dedicated ports required')
    if len(encoded)>16384:raise ValueError('Credential too large')
    password=base64.b64decode(encoded,validate=True).decode('utf-8')
    if not password or '\x00' in password:raise ValueError('Credential invalid')
    for name in list(os.environ):
        if name.upper().startswith(('PG','WMS_')):os.environ.pop(name)
    os.environ['WMS_DATABASE_URL']=URL.create('postgresql+psycopg',username='ddn_wms_runtime',password=password,host='127.0.0.1',port=profile['postgres'],database='ddn_wms_ensaio_test').render_as_string(hide_password=False)
    os.environ['WMS_HTTP_PORT']=str(profile['http']);os.environ['WMS_COOKIE_SECURE']='1'

def main():
    try:
        if os.name!='nt':raise ValueError('Windows service required')
        parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);args=parser.parse_args()
        profile=json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
        from .windows_credentials import read_secret
        configure(profile,base64.b64encode(read_secret(profile['secret'])).decode('ascii'))
        sys.argv=[sys.argv[0],'serve']
        from .server import main as serve
        serve()
    except (Exception,SystemExit):
        print('DDN_WMS_START_REFUSED',file=sys.stderr)
        return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
