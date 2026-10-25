"""Safe edits of the physical warehouse location catalogue (not of stock)."""
import re
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from .database import Corridor, Location, Stock, Audit
from .location_rules import is_floor, is_unassigned

CODE = re.compile(r'([A-Z])([1-9])([0-9]{2})\Z')

class WarehouseError(ValueError):
    pass

def validate_corridor(code, floors, positions):
    code = (code or '').strip().upper()
    try:
        floors, positions = int(floors), int(positions)
    except (ValueError, TypeError):
        raise WarehouseError('Indica números válidos para os andares e as posições.')
    if not re.fullmatch('[A-Z]', code):
        raise WarehouseError('O corredor deve ser uma única letra, de A a Z.')
    if not 1 <= floors <= 9 or not 1 <= positions <= 99:
        raise WarehouseError('São permitidos 1–9 andares e 1–99 posições por andar.')
    return code, floors, positions

def codes_for(code, floors, positions):
    return {f'{code}{level}{slot:02d}' for level in range(1, floors + 1) for slot in range(1, positions + 1)}

def corridor_locations(db, code):
    # Explicitly parse the catalogue instead of relying on wildcard LIKE.
    entries = db.scalars(select(Location).where(Location.codigo.like(f'{code}%'))).all()
    return {e.codigo: e for e in entries if CODE.fullmatch(e.codigo) and e.codigo[0] == code}

def occupied(db, codes):
    if not codes:
        return []
    return db.execute(select(Stock.local, func.sum(Stock.quantidade))
                      .where(Stock.local.in_(sorted(codes)))
                      .group_by(Stock.local)).all()

def validate_location(db, code):
    code = (code or '').strip().upper()
    match = CODE.fullmatch(code)
    if not match:
        raise WarehouseError('Código inválido. Exemplo: C214 (letra + andar + posição de dois dígitos).')
    floor, slot = int(match.group(2)), int(match.group(3))
    corridor = db.scalar(select(Corridor).where(Corridor.codigo == match.group(1)))
    if corridor is None:
        raise WarehouseError('O corredor não existe. Cria primeiro o corredor nas Definições.')
    if not 1 <= slot <= corridor.posicoes or floor > corridor.andares:
        raise WarehouseError('A localização está fora dos andares/posições configurados para este corredor.')
    return code

def audit(db, username, event, details):
    db.add(Audit(username=username, action=event, details=details))

def add_corridor(db: Session, user, code, floors, positions):
    code, floors, positions = validate_corridor(code, floors, positions)
    if db.scalar(select(Corridor).where(Corridor.codigo == code)) is not None:
        raise WarehouseError(f'O corredor {code} já existe.')
    wanted = codes_for(code, floors, positions)
    existing = set(db.scalars(select(Location.codigo).where(Location.codigo.in_(wanted))).all())
    db.add(Corridor(codigo=code, andares=floors, posicoes=positions))
    db.add_all([Location(codigo=c, capacidade=1) for c in sorted(wanted-existing)])
    audit(db, user, 'CRIAR_CORREDOR', f'{code}: {floors} andares, {positions} posições/andar.')
    db.commit()
    return code

def update_corridor(db: Session, user, code, floors, positions):
    current = db.scalar(select(Corridor).where(Corridor.codigo == code))
    if current is None:
        raise WarehouseError('Corredor inexistente.')
    _, floors, positions = validate_corridor(code, floors, positions)
    existing = corridor_locations(db, code)
    desired = codes_for(code, floors, positions)
    drop = set(existing) - desired
    # Include stocked codes that may exist without a catalogue row (legacy Excel).
    stock_codes = db.scalars(select(Stock.local).where(Stock.local.like(f'{code}%'))).all()
    out_of_range_stock = {c for c in stock_codes if CODE.fullmatch(c) and c[0] == code and c not in desired}
    occupied_drop = occupied(db, drop | out_of_range_stock)
    if occupied_drop:
        first = occupied_drop[0][0]
        raise WarehouseError(f'Não é possível eliminar posições com stock (ex.: {first}). Transfere primeiro as paletes.')
    # A rare stock record can have a location without a catalogue Location row.
    occupied_not_in_catalogue = occupied(db, desired - set(existing))
    missing_with_stock = {c for c, qty in occupied_not_in_catalogue if qty > 1}
    if missing_with_stock:
        raise WarehouseError('Existem posições sem catálogo e com ocupação superior a 1. Valida antes de as ativar.')
    for c in drop:
        db.delete(existing[c])
    for c in sorted(desired - set(existing)):
        db.add(Location(codigo=c, capacidade=1))
    current.andares, current.posicoes = floors, positions
    audit(db, user, 'ALTERAR_CORREDOR', f'{code}: {floors} andares, {positions} posições/andar; adicionadas {len(desired-set(existing))}; removidas {len(drop)}.')
    db.commit()

def remove_corridor(db: Session, user, code):
    corridor = db.scalar(select(Corridor).where(Corridor.codigo == code))
    if corridor is None:
        raise WarehouseError('Corredor inexistente.')
    existing = corridor_locations(db, code)
    if occupied(db, existing):
        raise WarehouseError('Não podes eliminar o corredor enquanto existirem paletes nas suas posições.')
    # Include stray stocked positions of this code, even if missing in the catalogue.
    stray = db.scalars(select(Stock.local).where(Stock.local.like(f'{code}%'))).all()
    if any(CODE.fullmatch(loc) and loc[0] == code for loc in stray):
        raise WarehouseError('Ainda há stock registado neste corredor. Não foi eliminado.')
    for loc in existing.values():
        db.delete(loc)
    db.delete(corridor)
    audit(db, user, 'REMOVER_CORREDOR', f'{code} ({len(existing)} posições removidas, sem stock).')
    db.commit()

def add_location(db: Session, user, code):
    code = validate_location(db, code)
    if db.scalar(select(Location).where(Location.codigo == code)):
        raise WarehouseError(f'A localização {code} já existe.')
    if occupied(db, [code]):
        raise WarehouseError('Há stock nesta posição sem registo no catálogo; verifica-o primeiro.')
    db.add(Location(codigo=code, capacidade=1))
    audit(db, user, 'CRIAR_LOCALIZACAO', code)
    db.commit()
    return code

def remove_location(db: Session, user, code):
    code = (code or '').strip().upper()
    if is_floor(code) or is_unassigned(code):
        raise WarehouseError('O Chão e os registos sem localização não podem ser eliminados.')
    item = db.scalar(select(Location).where(Location.codigo == code))
    if item is None:
        raise WarehouseError('A localização não existe.')
    if occupied(db, [code]):
        raise WarehouseError('Esta localização tem stock. Transfere a palete antes de a eliminar.')
    db.delete(item)
    audit(db, user, 'REMOVER_LOCALIZACAO', code)
    db.commit()

def rename_location(db: Session, user, oldcode, newcode):
    oldcode = (oldcode or '').strip().upper()
    newcode = validate_location(db, newcode)
    old = db.scalar(select(Location).where(Location.codigo == oldcode))
    if old is None or is_floor(oldcode) or is_unassigned(oldcode):
        raise WarehouseError('Localização original inexistente ou não editável.')
    if occupied(db, [oldcode]):
        raise WarehouseError('Esta localização tem stock. Não é seguro alterar o código enquanto estiver ocupada.')
    if oldcode == newcode:
        raise WarehouseError('O código novo é igual ao atual.')
    if db.scalar(select(Location).where(Location.codigo == newcode)):
        raise WarehouseError('Já existe uma localização com o novo código.')
    if occupied(db, [newcode]):
        raise WarehouseError('Há stock associado ao código novo. Não é possível usá-lo.')
    old.codigo = newcode
    audit(db, user, 'ALTERAR_LOCALIZACAO', f'{oldcode} -> {newcode}.')
    db.commit()
    return newcode
