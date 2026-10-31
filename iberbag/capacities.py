"""Read-only capacity export; capacities are configured per context/location."""
import csv
from sqlalchemy import func, select
from .database import Location, Stock

HEADERS = ('localizacao', 'stock_atual', 'capacidade_maxima')


def export_csv(db, filepath):
    records = db.execute(select(Location.codigo, Location.capacidade, func.coalesce(func.sum(Stock.quantidade), 0).label('used'))
                         .outerjoin(Stock, Stock.local == Location.codigo)
                         .group_by(Location.codigo, Location.capacidade).order_by(Location.codigo)).all()
    with open(filepath, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle, delimiter=';')
        writer.writerow(HEADERS)
        for code, maximum, used in records:
            writer.writerow((code, used, maximum if maximum is not None else ''))
    return len(records)
