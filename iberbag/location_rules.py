"""Iberbag warehouse physical policy.

All physical rack locations hold one pallet, except the floor (Chão),
which is unlimited. Unassigned stock is historical import data, not a location.
"""
import unicodedata

FLOOR_LABEL = 'Chão'
UNASSIGNED = '(não indicado)'


def normalized_code(code: str) -> str:
    return ''.join(char for char in unicodedata.normalize('NFKD', (code or '').strip()).casefold() if not unicodedata.combining(char))


def is_floor(code: str) -> bool:
    return normalized_code(code) == 'chao'


def is_unassigned(code: str) -> bool:
    return not (code or '').strip() or (code or '').strip() == UNASSIGNED


def capacity_for(code: str) -> int | None:
    """None is unbounded for Chão; unknown is exempt until classified."""
    return None if is_floor(code) or is_unassigned(code) else 1
