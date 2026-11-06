"""Called only inside the authenticated receiving transaction and writer lock."""
from sqlalchemy import select, func, union_all
from .database import LoadNumber, Carga, Stock, Receipt, StorageContext
from .contexts import require_context


def allocate(db, receipt):
    # Capability/authorization is checked by the calling receipt service. The
    # owner-wide allocator is shared by grouped and individually tracked goods.
    ctx, owner, warehouse = require_context(db)
    year = receipt.received_on.year
    if year < 2027:
        return receipt.code
    # Core queries deliberately span ALL warehouses of this owner, not other owners.
    contexts = select(StorageContext.id).where(StorageContext.client_id == owner.id)
    codes = union_all(*[select(table.c[column]).where(table.c.context_id.in_(contexts))
        for table, column in ((Carga.__table__, 'codigo'), (Stock.__table__, 'carga'), (Receipt.__table__, 'code'))]).subquery()
    number = (db.scalar(select(func.max(LoadNumber.number)).where(
        LoadNumber.client_id == owner.id, LoadNumber.year == year)) or 0) + 1
    while True:
        if number > 2147483647:
            raise ValueError('Sequência anual esgotada; requer intervenção administrativa.')
        code = f'{year % 100:02d}-{number:03d}'
        if not db.scalar(select(func.count()).select_from(codes).where(codes.c[0] == code)):
            break
        number += 1
    db.add(LoadNumber(client_id=owner.id, year=year, number=number, receipt_id=receipt.id))
    db.flush()
    return code
