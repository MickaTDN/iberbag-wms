"""Validation & one-off stock import from the original Iberbag workbook."""
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from hashlib import sha256
from sqlalchemy import func, select
from .workbook import workbook_rows, excel_date
from .database import Stock, Carga, Location, ImportBatch, Audit
from .location_rules import FLOOR_LABEL, capacity_for, is_unassigned, is_floor

UNKNOWN = '(não indicado)'

def clean(value):
    return str(value).strip() if value is not None and str(value).strip() else UNKNOWN

@dataclass
class Scan:
    groups: Counter = field(default_factory=Counter)
    loads: dict = field(default_factory=dict)
    problems: list = field(default_factory=list)
    skipped_totals: int = 0
    ignored: int = 0
    input_rows: int = 0
    digest: str = ''

    @property
    def pallets(self):
        return sum(self.groups.values())

    @property
    def missing_fields(self):
        return sum(qty for key, qty in self.groups.items() if UNKNOWN in key)

    @property
    def loads_without_date(self):
        return sorted({key[2] for key in self.groups if key[2] not in self.loads or self.loads[key[2]][0] is None})


def analyze(path):
    path = Path(path)
    parsed = workbook_rows(path)
    scan = Scan(digest=sha256(path.read_bytes()).hexdigest())
    for number, line in parsed['STOCK']:
        if number <= 2:
            if number == 2:
                scan.skipped_totals += 1
            continue
        cols = (line + [None] * 5)[:5]
        qty, art, lot, loc, load = cols
        if not any(v is not None for v in cols):
            continue
        scan.input_rows += 1
        try:
            amount = int(qty)
            if amount <= 0 or (isinstance(qty, (float, int)) and qty != amount):
                raise ValueError()
        except (ValueError, TypeError):
            scan.ignored += 1
            scan.problems.append(f'Linha STOCK {number}: quantidade inválida ({qty!r}).')
            continue
        key = (clean(art), clean(lot), clean(load), clean(loc))
        scan.groups[key] += amount
        if UNKNOWN in key:
            scan.problems.append(f'Linha STOCK {number}: artigo, lote, carga ou localização por preencher.')
    for rowno, line in parsed['HistoricoEntradas']:
        if rowno < 2:
            continue
        vals = (line + [None] * 3)[:3]
        load, arrived, employee = vals
        if not load:
            continue
        dt = None
        if isinstance(arrived, (int, float)):
            try:
                dt = excel_date(arrived)
            except (ValueError, OverflowError):
                scan.problems.append(f'Histórico de entradas {rowno}: data de entrada inválida.')
        elif isinstance(arrived, str) and arrived:
            try:
                dt = __import__('datetime').datetime.fromisoformat(arrived)
            except ValueError:
                scan.problems.append(f'Histórico de entradas {rowno}: data não reconhecida.')
        key = clean(load)
        # latest known record per carga, matching VBA's reverse lookup
        scan.loads[key] = (dt, clean(employee) if employee else None)
    return scan


def import_stock(db, report: Scan, username='importador'):
    from sqlalchemy import text
    from .lots import assert_lot
    if db.bind.dialect.name != 'postgresql':
        raise ValueError('Importação de stock exige PostgreSQL; análise sem gravação continua disponível.')
    from .database import context_id
    if context_id(db) != 1:
        raise ValueError('O importador Excel legado destina-se apenas a Iberbag/Ovar. Outros formatos exigem uma fase própria.')
    db.execute(text('SELECT pg_advisory_xact_lock(72834001)'))
    lotes = {}
    for (art, lot, load, loc), qty in report.groups.items():
        if UNKNOWN in (art,lot,load) or not all(str(v).strip() for v in (art,lot,load)):
            raise ValueError('Identidade de artigo/lote/carga incompleta. Esclarecer antes de importar.')
        assert_lot(db, lot, art)
        if lot in lotes and lotes[lot] != art:
            raise ValueError('Importação contraditória: um lote pertence a vários artigos.')
        lotes[lot] = art
    if db.scalar(select(func.count()).select_from(Stock)):
        raise ValueError('Já existe stock. Importação cancelada para evitar duplicados.')
    if db.scalar(select(func.count()).select_from(ImportBatch).where(ImportBatch.source_sha256 == report.digest)):
        raise ValueError('Este ficheiro já foi importado.')
    for (art, lot, load, loc), qty in report.groups.items():
        db.add(Stock(artigo=art, lote=lot, carga=load, local=loc, quantidade=qty))
    existing_locations = set(db.scalars(select(Location.codigo)).all())
    for loc in sorted(({key[3] for key in report.groups} | {FLOOR_LABEL}) - existing_locations):
        db.add(Location(codigo=loc, capacidade=capacity_for(loc)))
    for load in sorted({key[2] for key in report.groups} | set(report.loads)):
        dt, employee = report.loads.get(load, (None, None))
        db.add(Carga(codigo=load, entrada=dt, funcionario=employee))
    db.add(ImportBatch(source_sha256=report.digest, pallets=report.pallets,
                       groups=len(report.groups)))
    db.add(Audit(username=username, action='IMPORTAR_STOCK',
                 details=f'{report.pallets} paletes, {len(report.groups)} grupos; {report.missing_fields} paletes com campos incompletos; {len(report.loads_without_date)} cargas sem data.'))
    db.commit()
