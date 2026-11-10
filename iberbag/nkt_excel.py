"""Read-only bounded NKT listings. No application/database dependencies."""
from io import BytesIO
from hashlib import sha256
import re
import unicodedata
from zipfile import ZipFile,BadZipFile
from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException
from .workbook import validate_archive,_sheet_paths,_shared_strings,sheet_rows,N,MAX_FILE

MAX_PALLETS=300
MAX_CONTAINERS=20
MAX_RECORDS=300

def key(value):return ''.join(c for c in unicodedata.normalize('NFKD',str(value or '')).casefold() if c.isalnum())
def literal(value):return value.strip() if isinstance(value,str) else None

def expand(first,last):
    if not isinstance(first,str) or not isinstance(last,str):raise ValueError('Os limites PEG devem ser texto.')
    a=re.fullmatch(r'(.*?)([0-9]+)',first);b=re.fullmatch(r'(.*?)([0-9]+)',last)
    if not a or not b or a[1]!=b[1] or len(a[2])!=len(b[2]) or len(first)>120 or int(b[2])<int(a[2]):raise ValueError('Intervalo PEG ambíguo: prefixo, largura e limites devem coincidir.')
    count=int(b[2])-int(a[2])+1
    if count>MAX_PALLETS:raise ValueError('Intervalo excede o limite técnico de 300 paletes por receção.')
    return [a[1]+str(n).zfill(len(a[2])) for n in range(int(a[2]),int(b[2])+1)]

ALIASES={
 'peg':{'peg','pegindividual','numer peg','numeropeg'},
 'first':{'peginicial','peginicio','primeiropeg','peginitial','pegfrom'},
 'last':{'pegfinal','ult imopeg','ultimopeg','pegend','pegto'},
 'container':{'contentor','ncontentor','numerocontentor','container','containerno','containerid'},
 'material':{'material','tipodematerial','tipo','descricao','descricaodomaterial'},
 'date':{'datadechegada','datachegada','chegada','datadeentrada'},
 'notes':{'nota','notas','observacao','observacoes','estado'},
}
ALIASES={name:{key(v) for v in values} for name,values in ALIASES.items()}

def analyze_rows(sheets):
    report=dict(records=[],excluded=[],warnings=[],sheets=list(sheets))
    for sheet,rows in sheets.items():
        columns={};material=container=None;cq=False
        for number,values in rows:
            if not any(v is not None and v!='' for v in values):continue
            ref=f'{sheet}!{number}';raw=list(values)
            found={name:[i for i,v in enumerate(values) if key(v) in aliases] for name,aliases in ALIASES.items()}
            if (found['peg'] or found['first'] or found['last']) and (not columns or sum(bool(v) for v in found.values())>=2):
                if any(len(v)>1 for v in found.values()):raise ValueError(f'Cabeçalhos repetidos: {ref}.')
                columns={name:indices[0] for name,indices in found.items() if indices};continue
            cells=[literal(v) for v in values if v is not None and v!='']
            joined=' '.join(str(v) for v in values if v is not None)
            marker=key(joined)
            if 'cqnaovai' in marker:
                if len(cells)==1:cq=True
                is_cq=True
            else:is_cq=cq
            # Explicit labelled section headings only; never divide by totals/material.
            heading=re.fullmatch(r'\s*(Material|Tipo de material|Contentor|Container)\s*:\s*(.+)\s*',joined,re.I)
            if heading:
                if key(heading[1]) in ('contentor','container'):container=heading[2].strip();cq=False
                else:material=heading[2].strip();container=None;cq=False
                continue
            if not columns:
                raise ValueError(f'Layout sem cabeçalho PEG explícito: {ref}; fornecer listagem tabular ou usar entrada manual.')
            get=lambda name: values[columns[name]] if name in columns and columns[name]<len(values) else None
            peg,first,last=get('peg'),get('first'),get('last')
            own_material=literal(get('material'));own_container=literal(get('container'))
            if peg in (None,'') and first in (None,'') and last in (None,''):
                if is_cq:
                    report['excluded'].append(dict(ref=ref,raw=raw,pegs=[],reason='CQ — Não Vai'));continue
                if not own_material and not own_container:
                    raise ValueError(f'Linha sem PEG nem grupo explicitamente identificado: {ref}; corrigir a listagem ou usar introdução manual.')
                # A row with a container/material but no PEG is unresolved, not an empty load.
            issues=[];pegs=[]
            try:
                if peg not in (None,''):
                    if first not in (None,'') or last not in (None,''):raise ValueError('PEG individual e intervalo simultâneos.')
                    if not isinstance(peg,str):raise ValueError('PEG numérico: confirmar grafia/zeros a partir de prova, como texto.')
                    if peg!=peg.strip():raise ValueError('PEG contém espaços exteriores; corrigir explicitamente.')
                    pegs=[peg]
                else:pegs=expand(first,last)
            except ValueError as error:issues.append(str(error))
            record=dict(ref=ref,raw=raw,pegs=pegs,initial=first if isinstance(first,str) else None,final=last if isinstance(last,str) else None,material=own_material or material,container=own_container or container,received_on=literal(get('date')),issues=issues)
            if is_cq:report['excluded'].append(dict(record,reason='CQ — Não Vai'));continue
            for field in ('material','container'):
                if not record[field]:issues.append(f'{field}: associação em falta; confirmar manualmente.')
            report['records'].append(record)
            if len(report['records'])>MAX_RECORDS or sum(len(r['pegs']) for r in report['records'])>MAX_PALLETS:raise ValueError('Listagem excede os limites técnicos (300 linhas/paletes).')
    if not report['records']:raise ValueError('Sem paletes elegíveis para receção.')
    report['total_candidates']=sum(len(r['pegs']) for r in report['records'])
    return report

def analyze(content):
    if not isinstance(content,bytes) or not 1<=len(content)<=MAX_FILE:raise ValueError('Excel ausente ou demasiado grande (20 MiB).')
    try:
        with ZipFile(BytesIO(content)) as archive:
            validate_archive(archive);paths=_sheet_paths(archive)
            if not 1<=len(paths)<=20:raise ValueError('Número de folhas inválido (1–20).')
            sheets={};shared=_shared_strings(archive)
            for name,path in paths.items():
                depth=0
                with archive.open(path) as src:
                    for event,node in ET.iterparse(src,events=('start','end'),forbid_dtd=True):
                        if event=='start':
                            depth+=1
                            if depth>64:raise ValueError('XML demasiado profundo.')
                            if node.tag=='{'+N['x']+'}f':raise ValueError('Fórmulas não são aceites na listagem NKT.')
                        else:depth-=1;node.clear()
                rows=[]
                for row in sheet_rows(archive,path,shared):
                    rows.append(row)
                    if len(rows)>1000:raise ValueError('Folha excede 1000 linhas; usar uma listagem limitada à receção.')
                sheets[name]=rows
            report=analyze_rows(sheets)
    except (BadZipFile,DefusedXmlException,ET.ParseError,KeyError,IndexError,OverflowError) as error:raise ValueError('Excel malformado/inseguro; análise recusada.') from error
    return dict(report,sha256=sha256(content).hexdigest())
