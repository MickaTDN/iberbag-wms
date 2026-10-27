"""Explicit allowlist distribution. Never package repository working files or existing databases."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parent.parent


MODULES=('__init__.py','__main__.py','app.py','store.py','simulation.py')
TEMPLATES=('demo_dashboard.html','demo_dispatches.html','demo_inventory.html','demo_map.html','demo_message.html',
    'demo_pending.html','demo_putaway.html','demo_receipt_review.html','demo_receipts.html','demo_reset.html',
    'demo_settings.html','demo_stock.html','demo_transfers.html')


def resources(target):
    for sub in ('iberbag/templates','iberbag/static','iberbag_demo/templates'):(target/sub).mkdir(parents=True,exist_ok=True)
    shutil.copyfile(ROOT/'iberbag/templates/base.html',target/'iberbag/templates/base.html')
    shutil.copyfile(ROOT/'iberbag/static/style.css',target/'iberbag/static/style.css')
    for name in TEMPLATES:shutil.copyfile(ROOT/'iberbag_demo/templates'/name,target/'iberbag_demo/templates'/name)


def build(output,standalone=False):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    if standalone:
        if sys.platform!='win32':raise RuntimeError('O executável Windows só pode ser construído num runner Windows.')
        work=output.parent/'pyinstaller-work'
        assets=output.parent/'demo-package-resources';assets.mkdir(exist_ok=False);resources(assets)
        subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--name','IBERBAG_DEMO','--contents-directory','_runtime',
            '--distpath',str(output.parent/'pyinstaller-dist'),'--workpath',str(work),'--specpath',str(work),
            '--paths',str(ROOT),'--collect-data','reportlab','--collect-submodules','reportlab.pdfbase',
            '--add-data',str(assets/'iberbag/templates')+';iberbag/templates',
            '--add-data',str(assets/'iberbag/static')+';iberbag/static',
            '--add-data',str(assets/'iberbag_demo/templates')+';iberbag_demo/templates',
            '--exclude-module','sqlalchemy','--exclude-module','psycopg','--exclude-module','alembic','--exclude-module','openpyxl',
            '--exclude-module','iberbag.database','--exclude-module','iberbag.app',str(ROOT/'scripts/demo_entry.py')],check=True)
        source=output.parent/'pyinstaller-dist'/'IBERBAG_DEMO'
        shutil.copytree(source,output,dirs_exist_ok=True)
    else:
        # Source fallback includes only these modules; cannot import an operational service.
        (output/'iberbag').mkdir()
        for name in ('__init__.py','commercial_pdf.py'):shutil.copyfile(ROOT/'iberbag'/name,output/'iberbag'/name)
        resources(output)
        for name in MODULES:shutil.copyfile(ROOT/'iberbag_demo'/name,output/'iberbag_demo'/name)
        shutil.copyfile(ROOT/'requirements-demo.txt',output/'requirements-demo.txt')
        shutil.copyfile(ROOT/'INSTALAR_DEMO.bat',output/'INSTALAR_DEMO.bat')
    for name in ('INICIAR_DEMO.bat','PARAR_DEMO.bat','LEIA-ME_DEMO.txt'):shutil.copyfile(ROOT/name,output/name)
    forbidden={'.db','.sqlite','.xls','.xlsx','.xlsm','.dump','.env'}
    if any(p.suffix.lower() in forbidden or p.suffix.lower().startswith(('.xls','.sqlite')) or p.name.startswith('.env') for p in output.rglob('*') if p.is_file()):raise RuntimeError('Ficheiro inesperado no pacote; publicação recusada.')
    return Path(shutil.make_archive(str(output),'zip',output))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--standalone',action='store_true')
    args=parser.parse_args();print(build(args.output,args.standalone))
