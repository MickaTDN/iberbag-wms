"""Explicit allowlist distribution. Never package repository working files or existing databases."""
import argparse
import hashlib
from pathlib import Path
import shutil

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
    if standalone:
        raise RuntimeError('Executável autónomo suspenso após alerta antivírus. Usar pacote de código e esclarecer a deteção com o fornecedor.')
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    # Source fallback includes only these modules; cannot import an operational service.
    (output/'iberbag').mkdir()
    for name in ('__init__.py','commercial_pdf.py'):shutil.copyfile(ROOT/'iberbag'/name,output/'iberbag'/name)
    resources(output)
    for name in MODULES:shutil.copyfile(ROOT/'iberbag_demo'/name,output/'iberbag_demo'/name)
    shutil.copyfile(ROOT/'requirements-demo.txt',output/'requirements-demo.txt')

    for name in ('INICIAR_DEMO.bat','PARAR_DEMO.bat','INSTALAR_DEMO.bat','LEIA-ME_DEMO.txt'):
        shutil.copyfile(ROOT/'packaging/demo_source'/name,output/name)
    forbidden={'.exe','.dll','.pyd','.db','.sqlite','.xls','.xlsx','.xlsm','.dump','.env'}
    if any(p.suffix.lower() in forbidden or p.suffix.lower().startswith(('.xls','.sqlite')) or p.name.startswith('.env') for p in output.rglob('*') if p.is_file()):raise RuntimeError('Ficheiro inesperado no pacote; publicação recusada.')
    hashes=[hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(output).as_posix() for p in sorted(output.rglob('*')) if p.is_file()]
    (output/'MANIFESTO_SHA256.txt').write_text('\n'.join(hashes)+'\n',encoding='utf-8')
    return Path(shutil.make_archive(str(output),'zip',output))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--standalone',action='store_true')
    args=parser.parse_args();print(build(args.output,args.standalone))
