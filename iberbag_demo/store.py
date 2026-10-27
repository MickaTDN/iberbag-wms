"""Disposable JSON simulation in a dedicated, marked SQLite file, never an operational schema."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from threading import RLock
from . import MARKER


class DemoError(ValueError):
    pass


def safe_child(parent, name):
    parent=Path(parent).resolve(strict=True)
    child=parent/name
    if child.is_symlink() or (hasattr(child,'is_junction') and child.is_junction()) or child.resolve()!=child:
        raise DemoError('A pasta DEMO contém uma ligação externa. Usar uma pasta normal e descartável.')
    return child


class Store:
    def __init__(self, home):
        home=Path(home).resolve(strict=True)
        self.folder=safe_child(home,'DEMO');self.folder.mkdir(exist_ok=True)
        self.path=safe_child(self.folder,'estado.sqlite');self.lock=RLock()
        if self.path.exists():
            with self.connect() as conn:self.read(conn)
        else:
            # Refuse pre-existing files; never adopt or overwrite a user's database.
            with self.path.open('xb'):pass
            with self.connect() as conn:
                conn.execute('CREATE TABLE demo_state(id INTEGER PRIMARY KEY CHECK(id=1), marker TEXT NOT NULL, payload TEXT NOT NULL)')
                from .simulation import seed
                conn.execute('INSERT INTO demo_state VALUES (1,?,?)',(MARKER,json.dumps(seed(),ensure_ascii=False)))

    @contextmanager
    def connect(self):
        if safe_child(self.folder,'estado.sqlite')!=self.path:raise DemoError('Caminho DEMO inválido.')
        conn=sqlite3.connect(self.path,timeout=5)
        try:
            with conn:yield conn
        finally:conn.close()

    def read(self, conn):
        try:
            row=conn.execute('SELECT marker,payload FROM demo_state WHERE id=1').fetchone()
            if not row or row[0]!=MARKER:raise DemoError('Base sem identificação de demonstração. Não foi alterada.')
            state=json.loads(row[1])
            if state.get('marker')!=MARKER or state.get('version')!=1:raise DemoError('Versão DEMO incompatível. Conservar a pasta para análise.')
            return state
        except (sqlite3.DatabaseError,json.JSONDecodeError) as error:
            raise DemoError('Ficheiro existente não reconhecido como DEMO. Não foi substituído.') from error

    def snapshot(self):
        with self.lock,self.connect() as conn:return self.read(conn)

    def change(self, action):
        # A single local simulation; this does NOT reproduce PostgreSQL concurrency guarantees.
        with self.lock,self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            state=self.read(conn)
            result=action(state)
            conn.execute('UPDATE demo_state SET payload=? WHERE id=1',(json.dumps(state,ensure_ascii=False),))
            return result

    def reset(self):
        from .simulation import seed
        def replace(state):state.clear();state.update(seed())
        self.change(replace)  # Only the marked row; never recursively delete files/directories.
