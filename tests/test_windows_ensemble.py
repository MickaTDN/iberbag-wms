"""Fictitious package/coexistence tests; never installs system components."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

ROOT=Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location('ensemble',ROOT/'windows/assistente.py')
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)


def settings(**changes):
    return dict(database='ddn_wms_ensaio_test',user='ddn_wms_runtime',postgres=55433,http=8100,https=8447,**changes)


@pytest.mark.parametrize('field,value', [('database','gva'),('database','production_test'),('user','postgres'),('postgres',5432),('http',55433),('https',443),('http',True)])
def test_ensemble_rejects_unsafe_targets(field,value):
    conf=settings();conf[field]=value
    with pytest.raises(ValueError):a.validate(conf)


def test_ensemble_environment_never_inherits_database_or_bootstrap_injection(monkeypatch):
    for name in ('PGHOSTADDR','WMS_DATABASE_URL','PIP_INDEX_URL','PYTHONPATH','DDN_TEST_PG_PORT'):
        monkeypatch.setenv(name,'FICTITIOUS-UNTRUSTED')
    env=a.isolated_environment()
    assert not any(k.upper().startswith(('PG','WMS_','PIP_','PYTHON','DDN_')) for k in env)
    assert a.validate(settings())['postgres']==55433


@pytest.mark.parametrize('case', ['existing','occupied','unsupported','cancelled','failure'])
def test_ensemble_install_never_repairs_deletes_or_touches_other_services(tmp_path,monkeypatch,case):
    monkeypatch.setattr(a,'verify_bundle',lambda root:None)
    monkeypatch.setattr(a.platform,'system',lambda:'Linux' if case=='unsupported' else 'Windows')
    monkeypatch.setattr(a.platform,'machine',lambda:'AMD64')
    monkeypatch.setattr(a.sys,'getwindowsversion',lambda:SimpleNamespace(build=22631,product_type=1),raising=False)
    monkeypatch.setattr(a,'probe',lambda p:'ocupada' if case=='occupied' else 'livre')
    monkeypatch.setattr('builtins.input',lambda prompt:'INSTALAR ENSAIO' if case=='failure' else 'CANCELAR')
    other=tmp_path/'GVA';other.mkdir();sentinel=other/'sentinel';sentinel.write_bytes(b'FICTITIOUS')
    if case=='existing':(tmp_path/'.ddn-venv').mkdir()
    calls=[]
    def run(*args,**kwargs):
        calls.append(args)
        raise RuntimeError('Synthetic bootstrap failure')
    monkeypatch.setattr(a.subprocess,'run',run)
    if case=='cancelled':a.install(tmp_path)
    else:
        with pytest.raises((ValueError,RuntimeError)):a.install(tmp_path)
    assert sentinel.read_bytes()==b'FICTITIOUS'
    assert len(calls)==(1 if case=='failure' else 0)


def test_ensemble_package_manifest_detects_tampering_and_traversal(tmp_path):
    p=tmp_path/'payload.txt';p.write_bytes(b'FICTITIOUS')
    import hashlib
    manifest=dict(mode='homologacao',commit='a'*40,files={'payload.txt':hashlib.sha256(p.read_bytes()).hexdigest()})
    target=tmp_path/'MANIFEST.json';target.write_text(json.dumps(manifest))
    assert a.verify_bundle(tmp_path)['mode']=='homologacao'
    p.write_bytes(b'ALTERED')
    with pytest.raises(ValueError):a.verify_bundle(tmp_path)
    manifest['files']={'../outside':'0'*64};target.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):a.verify_bundle(tmp_path)


def test_ensemble_build_allowlist_excludes_untracked_secrets(tmp_path):
    from scripts.build_windows_ensemble import build
    wheels=tmp_path/'wheels';wheels.mkdir();(wheels/'synthetic.whl').write_bytes(b'SYNTHETIC-NOT-INSTALLABLE')
    archive=build(wheels,tmp_path/'out')
    import zipfile
    with zipfile.ZipFile(archive) as z:
        names=z.namelist()
        assert 'MANIFEST.json' in names and 'INSTALAR_DDN_ENSAIO.bat' in names
        assert 'iberbag/pwa/manifest.webmanifest' in names and 'iberbag/pwa/icon-192.png' in names
        assert json.loads(z.read('DDN_ENSAIO.json')) == settings()
        assert not any(n.endswith(('.db','.xlsm','.xlsx','.pem','.key','.env')) or n.startswith(('tests/','.git/')) for n in names)
        z.extractall(tmp_path/'extracted')
    a.verify_bundle(tmp_path/'extracted')


def test_ensemble_menu_uses_only_dedicated_test_target(monkeypatch):
    from iberbag import windows_menu
    monkeypatch.setattr('sys.argv',['assistant','--isolated-ensemble'])
    monkeypatch.setenv('DDN_TEST_PG_PORT','55439');monkeypatch.setenv('DDN_TEST_HTTP_PORT','8109')
    monkeypatch.setenv('WMS_DATABASE_URL','postgresql+psycopg://fictional/production')
    monkeypatch.setenv('PGHOSTADDR','192.0.2.1')
    answers=iter(['5','0']);monkeypatch.setattr('builtins.input',lambda prompt:next(answers))
    calls=[];monkeypatch.setattr(windows_menu.subprocess,'call',lambda args,env:calls.append((args,env)) or 0)
    windows_menu.main()
    args,env=calls[0]
    assert env['WMS_DB_NAME']=='ddn_wms_ensaio_test' and env['WMS_DB_USER']=='ddn_wms_runtime'
    assert env['WMS_DB_HOST']=='127.0.0.1' and env['WMS_DB_PORT']=='55439' and env['WMS_HTTP_PORT']=='8109'
    assert 'WMS_DATABASE_URL' not in env and 'PGHOSTADDR' not in env
    assert args[-1]=='serve'


def test_ensemble_menu_reserved_port_never_starts_process(monkeypatch):
    from iberbag import windows_menu
    monkeypatch.setattr('sys.argv',['assistant','--isolated-ensemble'])
    monkeypatch.setenv('DDN_TEST_PG_PORT','5432')
    monkeypatch.setattr(windows_menu.subprocess,'call',lambda *a,**k:pytest.fail('Must not start'))
    with pytest.raises(SystemExit):windows_menu.main()


@pytest.mark.parametrize('target,allowed',[('gva_restore_test',False),('ddn_wms_restore_probe_test',True)])
def test_ensemble_restore_stays_in_ddn_namespace(monkeypatch,target,allowed):
    from iberbag import windows_menu
    monkeypatch.setattr('sys.argv',['assistant','--isolated-ensemble'])
    monkeypatch.setenv('DDN_TEST_PG_PORT','55433');monkeypatch.setenv('DDN_TEST_HTTP_PORT','8100')
    answers=iter(['7','DDN ISOLADO','FICTITIOUS-BACKUP',target,'0'])
    monkeypatch.setattr('builtins.input',lambda prompt:next(answers))
    calls=[];monkeypatch.setattr(windows_menu.subprocess,'call',lambda args,env:calls.append((args,env)) or 0)
    windows_menu.main()
    assert len(calls)==int(allowed)
    if allowed:
        assert calls[0][1]['WMS_DB_NAME']==target and calls[0][1]['WMS_DB_USER']=='ddn_wms_owner'


@pytest.mark.parametrize('field,port',[('postgres',55432),('http',8765),('https',8443)])
def test_ensemble_known_gva_ports_are_never_adopted(field,port):
    config=settings();config[field]=port
    with pytest.raises(ValueError):a.validate(config)


def test_ensemble_custom_ports_are_checked_instead_of_defaults(tmp_path,monkeypatch):
    custom=settings();custom.update(postgres=55439,http=8119,https=8459)
    (tmp_path/'DDN_LOCAL.json').write_text(json.dumps(custom))
    assert a.load_settings(tmp_path)==custom
    monkeypatch.setattr(a,'verify_bundle',lambda root:None)
    monkeypatch.setattr(a.platform,'system',lambda:'Windows')
    monkeypatch.setattr(a.platform,'machine',lambda:'AMD64')
    monkeypatch.setattr(a.sys,'getwindowsversion',lambda:SimpleNamespace(build=22631,product_type=1),raising=False)
    checked=[]
    def probe(port):
        checked.append(port)
        return 'ocupada' if port==8459 else 'livre'
    monkeypatch.setattr(a,'probe',probe)
    monkeypatch.setattr(a.subprocess,'run',lambda *x,**k:pytest.fail('Occupied custom port must block installation'))
    with pytest.raises(ValueError):a.install(tmp_path)
    assert checked==[55439,8119,8459]
