"""Generate synthetic historical catalogue fixtures without writing newer columns."""
from sqlalchemy import create_engine, MetaData, Table, select
from sqlalchemy.orm import Session
from iberbag.database import Base, seed_warehouse_layout


def seed_legacy_catalogue(db):
    memory = create_engine('sqlite://')
    try:
        Base.metadata.create_all(memory)
        with Session(memory) as source:
            seed_warehouse_layout(source)
            for name in ('corridors', 'locations', 'app_config', 'audit'):
                target = Table(name, MetaData(), autoload_with=db.connection())
                rows = source.execute(select(Base.metadata.tables[name])).mappings().all()
                db.execute(target.insert(), [{key: row[key] for key in target.columns.keys() if key != 'id'} for row in rows])
            db.commit()
    finally:
        memory.dispose()
