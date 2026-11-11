"""Iberbag guide units: a stacked unit is always one indivisible pair."""
from .location_rules import is_floor
from .receiving import ReceivingError

WARNING = 'SOBREPOSTAS — 2 PALETES FÍSICAS, RETIRAR JUNTAS'


def flag(value):
    # Never interpret the text "false" as true, or accept fractional physical units.
    if value is True or value == '1': return True
    if value is False or value is None or value == '0' or value == '': return False
    raise ReceivingError('Tipo de paletes inválido.', 400)


def enabled(db):
    from .contexts import require_context
    ctx, client, warehouse = require_context(db)
    return client.id == 1 and warehouse.id == 1


def validate(db, stacked, local=None):
    stacked = flag(stacked)
    if stacked:
        # Permanent seeded owner/warehouse keys, independent of editable display names.
        if not enabled(db):
            raise ReceivingError('Paletes sobrepostas apenas em Iberbag/Ovar.')
        if local is not None and not is_floor(local):
            raise ReceivingError('Paletes sobrepostas só podem permanecer no Chão.')
    return stacked
