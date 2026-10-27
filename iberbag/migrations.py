"""Explicit schema preparation; never adopt or modify an unversioned database."""
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from .database import engine, SessionLocal, AppConfig, seed_warehouse_layout

REVISION = "0006"

def config():
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parent.parent / "migrations"))
    return cfg

def check_database():
    with engine.connect() as connection:
        tables = inspect(connection).get_table_names()
        if "alembic_version" not in tables:
            raise RuntimeError("Base sem versão. Preparar uma base NOVA com python -m iberbag.cli prepare-db.")
        if connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all() != [REVISION]:
            raise RuntimeError("Versão da base incompatível; rever migrações antes de arrancar.")
    with SessionLocal() as db:
        if not db.get(AppConfig, "warehouse_layout_seeded_vba"):
            raise RuntimeError("Catálogo por inicializar. Executar prepare-db antes do servidor.")

def prepare_database():
    # One operator runs this before starting any application processes.
    with engine.connect() as connection:
        tables = inspect(connection).get_table_names()
        if tables and "alembic_version" not in tables:
            raise RuntimeError("Base existente sem versão: recusada. Fazer backup e ensaiar migração numa cópia.")
        if "alembic_version" in tables:
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            if revision != REVISION:
                raise RuntimeError("Base existente: usar upgrade-db --preview e depois --apply com backup/restauro verificados.")
    command.upgrade(config(), "head")
    with SessionLocal() as db:
        seed_warehouse_layout(db)
    check_database()
