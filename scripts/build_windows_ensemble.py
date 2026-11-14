"""Allowlisted source/wheel package, never collects worktree databases or secrets."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def build(wheels, output):
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    if not re.fullmatch('[a-f0-9]{40}', commit): raise ValueError('Invalid commit')
    names = subprocess.check_output(['git', 'ls-files'], cwd=ROOT, text=True).splitlines()
    files = {}
    for name in names:
        p = Path(name)
        if ((p.parts[0] in ('iberbag', 'migrations') and p.suffix in ('.py','.html','.css','.js','.svg','.txt','.png','.webmanifest'))
            or name in ('requirements-prod.lock', 'windows/assistente.py', 'windows/INSTALAR_DDN_ENSAIO.bat', 'windows/GESTAO_DDN.bat', 'windows/gestao.ps1', 'windows/gestao_launcher.ps1', 'windows/atalho_assistente.ps1',
                        'docs/DDN_PROVISIONAMENTO_ASSISTIDO.md',
                        'docs/INSTALADOR_DDN_ENSAIO.md', 'docs/INSTALACAO_WINDOWS.md', 'docs/PWA_OPERADORES.md',
                        'docs/HOMOLOGACAO_IBERBAG_COSMOPAK.md')):
            if (ROOT / p).is_symlink(): raise ValueError('Symlink in package')
            target = p.name if name.endswith('.bat') else name
            files[target] = (ROOT / p).read_bytes()
    for p in Path(wheels).iterdir():
        if p.is_symlink() or not p.is_file() or p.suffix != '.whl': raise ValueError('Only wheels accepted')
        files['wheels/' + p.name] = p.read_bytes()
    if not any(n.startswith('wheels/') for n in files): raise ValueError('Missing wheels')
    files['DDN_ENSAIO.json'] = json.dumps(dict(database='ddn_wms_ensaio_test',user='ddn_wms_runtime',postgres=55433,http=8100,https=8447)).encode()
    files['LEIA-ME_DDN_ENSAIO.txt'] = (ROOT / 'docs/INSTALADOR_DDN_ENSAIO.md').read_bytes()
    files['PROCEDIMENTO_TECNICO_DDN.txt'] = (ROOT / 'docs/DDN_PROVISIONAMENTO_ASSISTIDO.md').read_bytes()
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
    parser=argparse.ArgumentParser();parser.add_argument('--wheels',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();print(build(args.wheels,args.output))
