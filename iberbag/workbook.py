"""Minimal XLSM reader using the standard library (never executes VBA macros).

Reads cached spreadsheet values from worksheet XML. Returns cell strings or numbers.
"""
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import re

N = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
     'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
     'p': 'http://schemas.openxmlformats.org/package/2006/relationships'}


def excel_date(number):
    return datetime(1899, 12, 30) + timedelta(days=float(number))


def column_number(cell_ref):
    letters = re.match(r'[A-Z]+', cell_ref)
    if not letters:
        raise ValueError(f'Referência inválida: {cell_ref}')
    result = 0
    for char in letters.group(0):
        result = result * 26 + ord(char) - 64
    return result - 1


def _shared_strings(z):
    if 'xl/sharedStrings.xml' not in z.namelist():
        return []
    root = ET.fromstring(z.read('xl/sharedStrings.xml'))
    return [''.join(node.text or '' for node in si.findall('.//x:t', N)) for si in root.findall('x:si', N)]


def _sheet_paths(z):
    book = ET.fromstring(z.read('xl/workbook.xml'))
    rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
    mappings = {r.attrib['Id']: r.attrib['Target'] for r in rels}
    output = {}
    for sheet in book.findall('x:sheets/x:sheet', N):
        target = mappings[sheet.attrib['{' + N['r'] + '}id']]
        clean = target.lstrip('/')
        if not clean.startswith('xl/'):
            clean = 'xl/' + clean
        output[sheet.attrib['name']] = clean
    return output


def sheet_rows(z, worksheet_path, shared):
    # Iterparse reduces RAM usage with large workbooks.
    with z.open(worksheet_path) as src:
        for _, row in ET.iterparse(src, events=('end',)):
            if row.tag != '{' + N['x'] + '}row':
                continue
            result = []
            for cell in row.findall('x:c', N):
                index = column_number(cell.attrib['r'])
                while len(result) <= index:
                    result.append(None)
                kind = cell.attrib.get('t')
                if kind == 'inlineStr':
                    value = ''.join(t.text or '' for t in cell.findall('.//x:t', N))
                else:
                    raw = cell.findtext('x:v', default=None, namespaces=N)
                    if raw is None:
                        value = None
                    elif kind == 's':
                        value = shared[int(raw)]
                    elif kind in ('str', 'e'):
                        value = raw
                    elif kind == 'b':
                        value = raw == '1'
                    else:
                        try:
                            n = float(raw)
                            value = int(n) if n.is_integer() else n
                        except ValueError:
                            value = raw
                result[index] = value
            yield int(row.attrib.get('r', 0)), result
            row.clear()


def workbook_rows(path, sheet_names=('STOCK', 'HistoricoEntradas')):
    with ZipFile(Path(path)) as z:
        shared = _shared_strings(z)
        sheets = _sheet_paths(z)
        missing = set(sheet_names).difference(sheets)
        if missing:
            raise ValueError('Folhas em falta: ' + ', '.join(sorted(missing)))
        return {sheet: list(sheet_rows(z, sheets[sheet], shared)) for sheet in sheet_names}
