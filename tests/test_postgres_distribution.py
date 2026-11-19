"""All archives and PE headers are synthetic; nothing is executed."""
import hashlib,importlib.util,zipfile
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('pgimport',Path(__file__).parents[1]/'windows/import_postgres.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
URL='https://get.enterprisedb.com/postgresql/postgresql-17.11-windows-x64-binaries.zip'

def archive(tmp_path,extra=None):
    p=tmp_path/'fictitious.zip';pe=bytearray(128);pe[:2]=b'MZ';pe[60:64]=(64).to_bytes(4,'little');pe[64:70]=b'PE\0\0\x64\x86'
    with zipfile.ZipFile(p,'w') as z:
        for name in m.REQUIRED:z.writestr('pgsql/'+name,pe)
        z.writestr('pgsql/share/FICTITIOUS.txt',b'NOT REAL')
        if extra:z.writestr(*extra)
    return p,hashlib.sha256(p.read_bytes()).hexdigest()


def test_offline_preview_import_reconciliation_and_no_overwrite(tmp_path):
    p,h=archive(tmp_path);before=p.read_bytes();out=tmp_path/'new'
    r=m.process(p,URL,'17.11',h);assert len(r['files'])==6 and not out.exists()
    with pytest.raises(ValueError):m.process(p,URL,'17.11',h,out)
    m.process(p,URL,'17.11',h,out,True)
    assert all(hashlib.sha256((out/name).read_bytes()).hexdigest()==digest for name,digest in r['files'].items())
    with pytest.raises(FileExistsError):m.process(p,URL,'17.11',h,out,True)
    assert p.read_bytes()==before

@pytest.mark.parametrize('extra',[('../escape',b'x'),('pgsql/bin/POSTGRES.EXE',b'x'),('pgsql/data/PG_VERSION',b'17'),('pgsql/CON.txt',b'x'),('pgsql/evil:stream',b'x')])
def test_distribution_refuses_ambiguous_or_unsafe_paths(tmp_path,extra):
    p,h=archive(tmp_path,extra)
    with pytest.raises(ValueError):m.process(p,URL,'17.11',h)

@pytest.mark.parametrize('origin,version,digest', [('https://unknown.invalid/a.zip','17.11',None),(URL,'16.9',None),(URL,'17.11','0'*64)])
def test_distribution_refuses_untrusted_origin_version_or_digest(tmp_path,origin,version,digest):
    p,h=archive(tmp_path)
    with pytest.raises(ValueError):m.process(p,origin,version,digest or h)


@pytest.mark.parametrize('name',['pgsql/share/timezone/Etc/GMT+1','pgsql/share/locale/sr@latin/LC_MESSAGES/postgres.mo'])
def test_official_timezone_and_locale_names_are_preserved(tmp_path,name):
    p,h=archive(tmp_path,(name,b'FICTITIOUS'));out=tmp_path/'new'
    report=m.process(p,URL,'17.11',h,out,True)
    assert 'components/postgres/'+name.removeprefix('pgsql/') in report['files']
