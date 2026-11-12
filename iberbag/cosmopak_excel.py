"""Bounded read-only Camião DDN analysis; ignores BD Artigos and column K."""
from decimal import Decimal,InvalidOperation
from hashlib import sha256
from io import BytesIO
import re,unicodedata
from zipfile import ZipFile,BadZipFile
from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException
from .workbook import validate_archive,_sheet_paths,_shared_strings,sheet_rows,N,MAX_FILE

SHEET='Camião DDN'
COLUMNS={'B':'article','C':'description','D':'lot','E':'pallets','F':'boxes','I':'incomplete'}
MAX_PALLETS=1000

def key(v):return ''.join(c for c in unicodedata.normalize('NFKD',str(v or '')).casefold() if c.isalnum())

def integer(v,positive=False):
    if v is None or isinstance(v,bool):raise ValueError('Quantidade desconhecida/booleana.')
    raw=str(v).strip()
    if not re.fullmatch(r'[0-9]+(?:[.,]0+)?',raw):raise ValueError('Quantidade inteira não ambígua obrigatória.')
    d=Decimal(raw.replace(',','.'))
    if not (1 if positive else 0)<=d<=2147483647:raise ValueError('Quantidade fora do limite.')
    return int(d)


def analyze(content):
    if not isinstance(content,bytes) or not 1<=len(content)<=MAX_FILE:raise ValueError('Excel ausente ou excede 20 MiB.')
    try:
        with ZipFile(BytesIO(content)) as z:
            validate_archive(z);paths=_sheet_paths(z)
            if SHEET not in paths:raise ValueError('Folha Camião DDN em falta.')
            shared=_shared_strings(z);styles=[];formats={}
            if 'xl/styles.xml' in z.namelist():
                st=ET.fromstring(z.read('xl/styles.xml'),forbid_dtd=True)
                formats={int(n.attrib['numFmtId']):n.attrib['formatCode'] for n in st.findall('x:numFmts/x:numFmt',N)}
                styles=[int(n.attrib.get('numFmtId','0')) for n in st.findall('x:cellXfs/x:xf',N)]
            raw={};depth=0
            with z.open(paths[SHEET]) as source:
                for event,node in ET.iterparse(source,events=('start','end'),forbid_dtd=True):
                    if event=='start':
                        depth+=1
                        if depth>64:raise ValueError('XML demasiado profundo.')
                        continue
                    depth-=1
                    if node.tag=='{'+N['x']+'}c':
                        ref=node.attrib.get('r','');col=re.sub('[0-9]','',ref)
                        if col in COLUMNS:
                            if node.find('x:f',N) is not None:raise ValueError(f'{SHEET}!{ref}: fórmula recusada.')
                            if node.attrib.get('t')=='e':raise ValueError(f'{SHEET}!{ref}: erro de célula.')
                            raw[ref]=dict(type=node.attrib.get('t','n'),value=node.findtext('x:v',None,N),style=node.attrib.get('s','0'))
                            if len(raw)>6006:raise ValueError('Folha excede os limites técnicos.')
                        node.clear()
            rows=[]
            for row in sheet_rows(z,paths[SHEET],shared):
                rows.append(row)
                if len(rows)>1001:raise ValueError('Folha excede 1000 linhas de dados.')
            output=[];issues=[];header=False;total=boxes=0
            for number,values in rows:
                get=lambda letter:values[ord(letter)-65] if len(values)>ord(letter)-65 else None
                if not any(get(c) not in (None,'') for c in COLUMNS):continue
                if not header:
                    if key(get('B'))=='artigo' and key(get('D'))=='lote' and 'palet' in key(get('E')) and 'caix' in key(get('F')):
                        header=True;continue
                    raise ValueError(f'{SHEET}!{number}: cabeçalho B/C/D/E/F/I não reconhecido; não adivinhar o layout.')
                if any(isinstance(get(c),str) and len(get(c))>512 for c in COLUMNS):raise ValueError(f'{SHEET}!{number}: texto excede o limite técnico de célula.')
                literal=lambda col:raw.get(f'{col}{number}',{}).get('value') if raw.get(f'{col}{number}',{}).get('type')=='n' and raw.get(f'{col}{number}',{}).get('value') is not None else get(col)
                record=dict(row=number,raw={c:dict(raw.get(f'{c}{number}',{}),display=get(c)) for c in COLUMNS})
                try:
                    for col,field in (('B','article'),('C','description'),('D','lot')):
                        value=get(col);cell=raw.get(f'{col}{number}',{})
                        if value is None or isinstance(value,bool):raise ValueError(field+': identificação ausente.')
                        if cell.get('type')=='n':
                            value=cell['value']
                            if not value or not re.fullmatch('[0-9]+',value) or len(value)>15:raise ValueError('Identificador numérico ambíguo; fornecer texto.')
                            if not cell['style'].isdigit():raise ValueError('Estilo numérico inválido.')
                            index=int(cell['style'])
                            if (styles and index>=len(styles)) or (not styles and index!=0):raise ValueError('Estilo numérico inexistente.')
                            fmtid=styles[index] if styles else 0
                            fmt=formats.get(fmtid,{0:'General',1:'0',49:'General'}.get(fmtid))
                            if fmt is None:raise ValueError('Formato de identificador não confirmado; fornecer texto.')
                            if fmt!='General':
                                if len(fmt)>120 or not re.fullmatch('0+',fmt):raise ValueError('Formato de identificador ambíguo; fornecer texto.')
                                value=value.zfill(len(fmt))
                        value=str(value)
                        if not value.strip() or len(value)>(240 if field=='description' else 120) or any(ord(c)<32 for c in value):raise ValueError(field+': valor inválido.')
                        if value!=value.strip():raise ValueError('Espaços exteriores no identificador; corrigir explicitamente.')
                        record[field]=value
                    record['pallets']=integer(literal('E'),True);record['boxes']=integer(literal('F'))
                    mark=get('I')
                    if raw.get(f'I{number}',{}).get('type')=='n' and literal('I') is not None:
                        flag=Decimal(str(literal('I')))
                        if flag not in (0,1):raise ValueError('Marca numérica de palete incompleta ambígua.')
                        mark=str(int(flag))
                    normal=key(mark)
                    if mark is None or (isinstance(mark,str) and not mark.strip()):record['incomplete']=False
                    elif isinstance(mark,bool):record['incomplete']=mark
                    elif normal in ('nao','n','completa','0'):record['incomplete']=False
                    elif normal in ('x','sim','s','incompleta','1'):record['incomplete']=True
                    else:raise ValueError('Marca Completa/Incompleta desconhecida.')
                    total+=record['pallets'];boxes+=record['pallets']*record['boxes']
                    if total>MAX_PALLETS:raise ValueError('Receção excede 1000 paletes.')
                except (ValueError,InvalidOperation,KeyError) as error:issues.append(dict(ref=f'{SHEET}!{number}',error=str(error)))
                output.append(record)
            if not header or not output:raise ValueError('Sem paletes previstas na folha Camião DDN.')
            if total>MAX_PALLETS:raise ValueError('Receção excede 1000 paletes.')
    except (BadZipFile,DefusedXmlException,ET.ParseError,IndexError,OverflowError,KeyError) as error:raise ValueError('Excel malformado/inseguro; análise recusada.') from error
    return dict(sheet=SHEET,sha256=sha256(content).hexdigest(),rows=output,issues=issues,pallets=total,boxes=boxes)
