"""Minimal XLSM reader using the standard library (never executes VBA macros).

Reads cached spreadsheet values from worksheet XML. Returns cell strings or numbers.
"""
from pathlib import Path
from zipfile import ZipFile
from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException
import math
from pathlib import PurePosixPath
from datetime import datetime, timedelta
import re

MAX_FILE = 20 * 1024 * 1024
MAX_ENTRY = 8 * 1024 * 1024
MAX_EXPANDED = 64 * 1024 * 1024
MAX_ENTRIES = 2048
MAX_RATIO = 200
MAX_ROWS = 100_000
MAX_SLOTS = 500_000
MAX_STRINGS = 100_000
MAX_TEXT = 32767


def validate_archive(z):
    infos = z.infolist()
    names = [i.filename for i in infos]
    if len(infos) > MAX_ENTRIES or len(names) != len(set(names)):
        raise ValueError('Excel: entradas excessivas ou repetidas.')
    if sum(i.file_size for i in infos) > MAX_EXPANDED:
        raise ValueError('Excel: dimensão descomprimida excessiva.')
    for i in infos:
        if (i.file_size > MAX_ENTRY or i.file_size > MAX_RATIO * max(1, i.compress_size)
                or i.flag_bits & 1 or i.filename.startswith('/') or '\\' in i.filename
                or '..' in PurePosixPath(i.filename).parts):
            raise ValueError('Excel: arquivo inválido ou limite de expansão excedido.')


def checked_text(value):
    if len(value) > MAX_TEXT: raise ValueError('Excel: texto demasiado longo.')
    return value


N = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
     'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
     'p': 'http://schemas.openxmlformats.org/package/2006/relationships'}


def excel_date(number):
    return datetime(1899, 12, 30) + timedelta(days=float(number))


def column_number(cell_ref):
    match = re.fullmatch(r'([A-Z]{1,3})([1-9][0-9]{0,6})', cell_ref or '')
    if not match or int(match[2]) > 1048576: raise ValueError('Excel: referência de célula inválida.')
    result = 0
    for char in match[1]: result = result * 26 + ord(char) - 64
    if result > 16384: raise ValueError('Excel: coluna fora do limite.')
    return result - 1


def _shared_strings(z):
    if 'xl/sharedStrings.xml' not in z.namelist():
        return []
    root = ET.fromstring(z.read('xl/sharedStrings.xml'), forbid_dtd=True)
    strings = root.findall('x:si', N)
    if len(strings) > MAX_STRINGS: raise ValueError('Excel: demasiadas strings.')
    return [checked_text(''.join(node.text or '' for node in si.findall('.//x:t', N))) for si in strings]


def _sheet_paths(z):
    book = ET.fromstring(z.read('xl/workbook.xml'), forbid_dtd=True)
    rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'), forbid_dtd=True)
    if any(r.get('TargetMode') == 'External' for r in rels): raise ValueError('Excel: relação externa recusada.')
    mappings = {r.attrib['Id']: r.attrib['Target'] for r in rels}
    output = {}
    for sheet in book.findall('x:sheets/x:sheet', N):
        target = mappings[sheet.attrib['{' + N['r'] + '}id']]
        clean = target.lstrip('/')
        if not clean.startswith('xl/'):
            clean = 'xl/' + clean
        if '..' in PurePosixPath(clean).parts or '\\' in clean or ':' in clean or clean not in z.namelist():
            raise ValueError('Excel: caminho de folha inválido.')
        if sheet.attrib['name'] in output: raise ValueError('Excel: nome de folha repetido.')
        output[sheet.attrib['name']] = clean
    return output


def sheet_rows(z, worksheet_path, shared):
    # Iterparse reduces RAM usage with large workbooks.
    rows = slots = depth = previous = 0
    with z.open(worksheet_path) as src:
        for event, row in ET.iterparse(src, events=('start', 'end'), forbid_dtd=True):
            if event == 'start':
                depth += 1
                if depth > 64: raise ValueError('Excel: XML demasiado profundo.')
                continue
            depth -= 1
            if row.tag != '{' + N['x'] + '}row':
                continue
            rows += 1
            if rows > MAX_ROWS: raise ValueError('Excel: demasiadas linhas.')
            result = []
            seen = set()
            for cell in row.findall('x:c', N):
                index = column_number(cell.attrib['r'])
                if index in seen: raise ValueError('Excel: célula repetida.')
                seen.add(index)
                growth = max(0, index + 1 - len(result)); slots += growth
                if slots > MAX_SLOTS: raise ValueError('Excel: demasiadas células/espaços.')
                while len(result) <= index:
                    result.append(None)
                kind = cell.attrib.get('t')
                if kind == 'inlineStr':
                    value = checked_text(''.join(t.text or '' for t in cell.findall('.//x:t', N)))
                else:
                    raw = cell.findtext('x:v', default=None, namespaces=N)
                    if raw is None:
                        value = None
                    elif kind == 's':
                        if not raw.isdigit() or int(raw) >= len(shared): raise ValueError('Excel: referência de string inválida.')
                        value = shared[int(raw)]
                    elif kind in ('str', 'e'):
                        value = checked_text(raw)
                    elif kind == 'b':
                        value = raw == '1'
                    else:
                        try:
                            n = float(raw)
                        except ValueError:
                            value = checked_text(raw)
                        else:
                            if not math.isfinite(n): raise ValueError('Excel: número não finito.')
                            value = int(n) if n.is_integer() else n
                result[index] = value
            number = int(row.attrib.get('r', 0))
            if number <= previous: raise ValueError('Excel: linhas repetidas ou fora de ordem.')
            previous = number
            if not 1 <= number <= 1048576: raise ValueError('Excel: número de linha inválido.')
            if any(int(re.search(r'[0-9]+$', c.attrib['r'])[0]) != number for c in row.findall('x:c', N)):
                raise ValueError('Excel: linha e célula contraditórias.')
            yield number, result
            row.clear()


def _workbook_rows(path, sheet_names=('STOCK', 'HistoricoEntradas')):
    if Path(path).stat().st_size > MAX_FILE: raise ValueError('Excel: ficheiro demasiado grande.')
    with ZipFile(Path(path)) as z:
        validate_archive(z)
        shared = _shared_strings(z)
        sheets = _sheet_paths(z)
        missing = set(sheet_names).difference(sheets)
        if missing:
            raise ValueError('Folhas em falta: ' + ', '.join(sorted(missing)))
        return {sheet: list(sheet_rows(z, sheets[sheet], shared)) for sheet in sheet_names}


def workbook_rows(path, sheet_names=('STOCK', 'HistoricoEntradas')):
    from zipfile import BadZipFile
    try:
        return _workbook_rows(path, sheet_names)
    except (DefusedXmlException, ET.ParseError, BadZipFile, KeyError, IndexError, OverflowError) as error:
        raise ValueError('Excel malformado ou inseguro; importação recusada.') from error
