"""Synthetic fixture generator; never imports Excel or an existing database."""
from datetime import datetime
import getpass
from pathlib import Path
import secrets
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session
from .database import Base, User, Stock, Carga, Audit, ImportBatch, seed_warehouse_layout
from .security import set_password, new_session


def create_demo(path, passwords):
    path = Path(path)
    with path.open('xb'):
        pass
    engine = create_engine(URL.create('sqlite', database=str(path)))
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            seed_warehouse_layout(db)
            for role in ('admin', 'operador'):
                user = User(username=role + '_teste', display_name='Conta fictícia ' + role, role=role, active=True)
                set_password(user, passwords[role])
                db.add(user)
                db.flush()
                new_session(db, user)
            db.add_all([
                Carga(codigo='CARGA-FICTICIA-1', entrada=datetime(2020, 1, 2), funcionario='Pessoa Fictícia'),
                Carga(codigo='CARGA-FICTICIA-2', entrada=None, funcionario=None),
                Stock(artigo='ART-FICTICIO', lote='LOTE-1', carga='CARGA-FICTICIA-1', local='A101', quantidade=1),
                Stock(artigo='ART-FICTICIO', lote='LOTE-2', carga='CARGA-FICTICIA-2', local='A101', quantidade=1),
                Stock(artigo='OUTRO-FICTICIO', lote='LOTE-3', carga='CARGA-FICTICIA-2', local='Chão', quantidade=5),
                Stock(artigo='SEM-LOCAL-FICTICIO', lote='LOTE-4', carga='SEM-CARGA-FICTICIA', local='(não indicado)', quantidade=1),
                Audit(username='admin_teste', action='ENSAIO_FICTICIO', details='Dados gerados para ensaio, sem dados reais.'),
                ImportBatch(source_sha256=secrets.token_hex(32), pallets=8, groups=4),
            ])
            db.commit()
    finally:
        engine.dispose()


def main():
    target = Path(input('Novo ficheiro SQLite de teste (ex.: C:\\IberbagEnsaios\\ficticio.db): ').strip().strip('"'))
    target.parent.mkdir(parents=True, exist_ok=True)
    passwords = {role: getpass.getpass(f'Nova palavra-passe para {role}_teste (12–200 caracteres): ') for role in ('admin', 'operador')}
    if any(not 12 <= len(p) <= 200 for p in passwords.values()):
        raise SystemExit('As palavras-passe devem ter 12–200 caracteres.')
    create_demo(target, passwords)
    print('Base fictícia criada: 4 grupos, 8 paletes. Contas: admin_teste e operador_teste.')


if __name__ == '__main__':
    main()
