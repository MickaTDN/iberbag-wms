"""Explicit allowlist distribution. Never package repository working files or existing databases."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parent.parent


def build(output,standalone=False):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    if standalone:
        if sys.platform!='win32':raise RuntimeError('O executável Windows só pode ser construído num runner Windows.')
        work=output.parent/'pyinstaller-work'
        subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--name','IBERBAG_DEMO','--contents-directory','_runtime',
            '--distpath',str(output.parent/'pyinstaller-dist'),'--workpath',str(work),'--specpath',str(work),
            '--paths',str(ROOT),'--collect-data','reportlab','--collect-submodules','reportlab.pdfbase',
            '--add-data',str(ROOT/'iberbag/templates')+';iberbag/templates',
            '--add-data',str(ROOT/'iberbag/static')+';iberbag/static',
            '--add-data',str(ROOT/'iberbag_demo/templates')+';iberbag_demo/templates',
            '--exclude-module','sqlalchemy','--exclude-module','psycopg','--exclude-module','alembic','--exclude-module','openpyxl',
            '--exclude-module','iberbag.database','--exclude-module','iberbag.app',str(ROOT/'scripts/demo_entry.py')],check=True)
        source=output.parent/'pyinstaller-dist'/'IBERBAG_DEMO'
        shutil.copytree(source,output,dirs_exist_ok=True)
    else:
        # Source fallback includes only these modules; cannot import an operational service.
        (output/'iberbag').mkdir()
        for name in ('__init__.py','commercial_pdf.py'):shutil.copyfile(ROOT/'iberbag'/name,output/'iberbag'/name)
        for folder in ('templates','static'):shutil.copytree(ROOT/'iberbag'/folder,output/'iberbag'/folder)
        shutil.copytree(ROOT/'iberbag_demo',output/'iberbag_demo',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        shutil.copyfile(ROOT/'requirements-demo.txt',output/'requirements-demo.txt')
        shutil.copyfile(ROOT/'INSTALAR_DEMO.bat',output/'INSTALAR_DEMO.bat')
    for name in ('INICIAR_DEMO.bat','PARAR_DEMO.bat','LEIA-ME_DEMO.txt'):shutil.copyfile(ROOT/name,output/name)
    forbidden={'.db','.sqlite','.xls','.xlsx','.xlsm','.dump','.env'}
    if any(p.suffix.lower() in forbidden for p in output.rglob('*') if p.is_file()):raise RuntimeError('Ficheiro inesperado no pacote; publicação recusada.')
    return Path(shutil.make_archive(str(output),'zip',output))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--standalone',action='store_true')
    args=parser.parse_args();print(build(args.output,args.standalone))
