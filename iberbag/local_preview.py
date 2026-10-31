"""Discardable SQLite preview. Sanitize inherited configuration BEFORE app imports."""
import getpass
import os
from pathlib import Path
import tempfile


def isolate(folder):
    for name in list(os.environ):
        if name.startswith(('WMS_', 'PG')):
            os.environ.pop(name)
    database = Path(folder).resolve() / 'preview.sqlite'
    os.environ['WMS_DATABASE_URL'] = 'sqlite:///' + str(database)
    os.environ['WMS_COOKIE_SECURE'] = '0'
    return database


def main():
    with tempfile.TemporaryDirectory(prefix='iberbag-preview-') as folder:
        database = isolate(folder)
        password = getpass.getpass('Nova senha fictícia (12–200 caracteres; não usar senha real): ')
        if not 12 <= len(password) <= 200:
            raise SystemExit('Senha inválida.')
        from .demo import create_demo
        create_demo(database, {role: password for role in ('admin', 'operador')})
        print('PRÉ-VISUALIZAÇÃO — DADOS FICTÍCIOS. Contas admin_teste / operador_teste.')
        print('Apenas consulta SQLite; movimentos operacionais recusados. Dados eliminados ao encerrar.')
        import uvicorn
        uvicorn.run('iberbag.app:app', host='127.0.0.1', port=8000, proxy_headers=False)


if __name__ == '__main__':
    main()
