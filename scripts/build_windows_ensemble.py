"""Allowlisted source/wheel package, never collects worktree databases or secrets."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def build(wheels, output, components=None):
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    if not re.fullmatch('[a-f0-9]{40}', commit): raise ValueError('Invalid commit')
    names = subprocess.check_output(['git', 'ls-files'], cwd=ROOT, text=True).splitlines()
    files = {}
    for name in names:
        p = Path(name)
        if ((p.parts[0] in ('iberbag', 'migrations') and p.suffix in ('.py','.html','.css','.js','.svg','.txt','.png','.webmanifest'))
            or name in ('requirements-prod.lock', 'windows/assistente.py', 'windows/components.lock.json', 'windows/WinSW-LICENSE.txt', 'windows/segredo_servico.ps1', 'windows/provisionar_servicos.ps1', 'windows/INSTALAR_DDN_ENSAIO.bat', 'windows/GESTAO_DDN.bat', 'windows/gestao.ps1', 'windows/gestao_launcher.ps1', 'windows/atalho_assistente.ps1',
                        'docs/DDN_PROVISIONAMENTO_ASSISTIDO.md', 'docs/SCM_E_CREDENCIAIS_WINDOWS.md',
                        'docs/INSTALADOR_DDN_ENSAIO.md', 'docs/INSTALACAO_WINDOWS.md', 'docs/PWA_OPERADORES.md',
                        'docs/HOMOLOGACAO_IBERBAG_COSMOPAK.md')):
            if (ROOT / p).is_symlink(): raise ValueError('Symlink in package')
            target = p.name if name.endswith('.bat') else name
            files[target] = (ROOT / p).read_bytes()
    for p in Path(wheels).iterdir():
        if p.is_symlink() or not p.is_file() or p.suffix != '.whl': raise ValueError('Only wheels accepted')
        files['wheels/' + p.name] = p.read_bytes()
    if not any(n.startswith('wheels/') for n in files): raise ValueError('Missing wheels')
    if components is not None:
        lock=json.loads((ROOT/'windows/components.lock.json').read_text())
        for entry in lock.values():
            if hashlib.sha256((Path(components)/entry['file']).read_bytes()).hexdigest()!=entry['sha256']:
                raise ValueError('Component hash differs')
        files['components/WinSW.NET461.exe']=(Path(components)/lock['winsw']['file']).read_bytes()
        with zipfile.ZipFile(Path(components)/lock['caddy']['file']) as z:
            files['components/caddy.exe']=z.read('caddy.exe')
            for name in z.namelist():
                if Path(name).name.lower() in ('license','license.txt'):files['components/Caddy-LICENSE.txt']=z.read(name)
        files['components/BUNDLED.json']=json.dumps({'caddy_sha256':hashlib.sha256(files['components/caddy.exe']).hexdigest(),'caddy_version':lock['caddy']['version'],'winsw_sha256':lock['winsw']['sha256']}).encode()
    files['DDN_ENSAIO.json'] = json.dumps(dict(database='ddn_wms_ensaio_test',user='ddn_wms_runtime',postgres=55433,http=8100,https=8447)).encode()
    files['LEIA-ME_DDN_ENSAIO.txt'] = (ROOT / 'docs/INSTALADOR_DDN_ENSAIO.md').read_bytes()
    files['PROCEDIMENTO_TECNICO_DDN.txt'] = (ROOT / 'docs/SCM_E_CREDENCIAIS_WINDOWS.md').read_bytes()
    manifest = dict(mode='homologacao', commit=commit, files={k:hashlib.sha256(v).hexdigest() for k,v in files.items()})
    files['MANIFEST.json'] = json.dumps(manifest,sort_keys=True,indent=2).encode()
    output = Path(output);output.mkdir(parents=True,exist_ok=True)
    archive = output / ('DDN_WMS_Windows_Ensaio_' + commit[:8] + '.zip')
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for name,data in sorted(files.items()): z.writestr(name,data)
    (output / (archive.name + '.sha256')).write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n')
    (output / 'COMMIT.txt').write_text(commit+'\n')
    return archive


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--wheels',required=True);parser.add_argument('--output',required=True);parser.add_argument('--components')
    args=parser.parse_args();print(build(args.wheels,args.output,args.components))
