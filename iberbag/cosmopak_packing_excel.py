"""Read-only Folha2 preview and explicitly confirmed column mapping."""
from io import BytesIO
from decimal import Decimal
from hashlib import sha256
import re
from zipfile import ZipFile,BadZipFile
from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException
from .workbook import validate_archive,_sheet_paths,_shared_strings,sheet_rows,N,MAX_FILE
from .cosmopak_excel import integer,key

FIELDS=('owner','article','lot','boxes')

def analyze(content):
    if not isinstance(content,bytes) or not 1<=len(content)<=MAX_FILE:raise ValueError('Excel excede os limites técnicos.')
    try:
        with ZipFile(BytesIO(content)) as z:
            validate_archive(z);paths=_sheet_paths(z)
            if 'Folha2' not in paths:raise ValueError('Folha2 em falta.')
            styles=[];formats={}
            if 'xl/styles.xml' in z.namelist():
                st=ET.fromstring(z.read('xl/styles.xml'),forbid_dtd=True)
                formats={int(v.attrib['numFmtId']):v.attrib['formatCode'] for v in st.findall('x:numFmts/x:numFmt',N)}
                styles=[int(v.attrib.get('numFmtId','0')) for v in st.findall('x:cellXfs/x:xf',N)]
            depth=0;formulas=[];numeric=[];literals={};identifiers={};errors=[]
            with z.open(paths['Folha2']) as src:
                for event,node in ET.iterparse(src,events=('start','end'),forbid_dtd=True):
                    if event=='start':
                        depth+=1
                        if depth>64:raise ValueError('XML demasiado profundo.')
                    else:
                        depth-=1
                        if node.tag=='{'+N['x']+'}c':
                            if node.find('x:f',N) is not None:formulas.append(node.attrib.get('r',''))
                            ref=node.attrib.get('r','')
                            if node.attrib.get('t')=='e':errors.append(ref)
                            if node.attrib.get('t','n')=='n' and node.find('x:v',N) is not None:
                                value=node.findtext('x:v',None,N)
                                if value is None or len(value)>512:raise ValueError('Literal numérico demasiado longo.')
                                numeric.append(ref);literals[ref]=value
                                style=node.attrib.get('s','0')
                                index=int(style) if style.isdigit() else -1
                                fmtid=styles[index] if 0<=index<len(styles) else (0 if index==0 and not styles else -1)
                                fmt=formats.get(fmtid,{0:'General',1:'0',49:'General'}.get(fmtid))
                                if re.fullmatch('[0-9]{1,15}',value) and fmt and (fmt=='General' or re.fullmatch('0{1,120}',fmt)):
                                    identifiers[ref]=value if fmt=='General' else value.zfill(len(fmt))
                            if len(formulas)+len(numeric)+len(errors)>27000:raise ValueError('Demasiadas células.')
                            node.clear()
            rows=[]
            for number,values in sheet_rows(z,paths['Folha2'],_shared_strings(z)):
                if number>1001 or len(values)>26 or any(isinstance(v,str) and len(v)>512 for v in values):raise ValueError('Folha2 excede 1000 linhas, 26 colunas ou o limite de texto.')
                values=[identifiers.get(f'{chr(65+i)}{number}',v) for i,v in enumerate(values)]
                rows.append(dict(row=number,cells=values))
    except (BadZipFile,DefusedXmlException,ET.ParseError,KeyError,IndexError,OverflowError) as error:raise ValueError('Excel malformado ou inseguro.') from error
    return dict(sha256=sha256(content).hexdigest(),sheet='Folha2',rows=rows,formulas=formulas,numeric=numeric,numeric_literals=literals,identifiers=identifiers,errors=errors)


def map_rows(report,mapping,reference,reference_cell=None,reason=None):
    """Never infer headers, ownership, reference or units from ambiguous columns."""
    if not isinstance(mapping,dict) or not set(FIELDS)<=set(mapping):raise ValueError('Confirmar todas as colunas obrigatórias.')
    columns={}
    for field in FIELDS+('pallets',):
        value=mapping.get(field)
        if field=='pallets' and not value:continue
        if not isinstance(value,str) or not re.fullmatch('[A-Z]',value):raise ValueError('Indicar letras de coluna A–Z.')
        columns[field]=ord(value)-65
    if len(set(columns.values()))!=len(columns):raise ValueError('Colunas de significado diferente não podem coincidir.')
    if 'pallets' not in columns and mapping.get('one_row_one_pallet') is not True:raise ValueError('Confirmar explicitamente uma palete por linha ou mapear a quantidade.')
    first=mapping.get('first_row')
    if type(first) is not int or not 1<=first<=1001:raise ValueError('Indicar a primeira linha de dados.')
    if not isinstance(reference,str) or not 1<=len(reference)<=120 or reference!=reference.strip() or any(ord(c)<32 for c in reference):raise ValueError('Referência oficial do PACKING obrigatória, preservando zeros.')
    cells={f'{chr(65+i)}{r["row"]}':v for r in report['rows'] for i,v in enumerate(r['cells'])}
    if reference_cell:
        if not re.fullmatch('[A-Z][1-9][0-9]{0,3}',reference_cell) or cells.get(reference_cell)!=reference or reference_cell in report['formulas'] or reference_cell in report['numeric']:raise ValueError('Referência deve corresponder exatamente à célula textual comprovada.')
    elif not isinstance(reason,str) or not reason.strip():raise ValueError('Sem célula inequívoca, exigir transcrição oficial comprovada e motivo.')
    output=[];excluded=[]
    for row in report['rows']:
        if row['row']<first:continue
        values=row['cells'];get=lambda field:values[columns[field]] if len(values)>columns[field] else None
        if not any(get(f) not in (None,'') for f in FIELDS):continue
        owner=key(get('owner'))
        if owner=='mecalux':excluded.append(dict(row=row['row'],owner=get('owner'),reason='Mecalux'));continue
        if owner not in ('davidneto','ddn','davidnetoddn'):raise ValueError(f'Folha2!{row["row"]}: titular ausente/ambíguo; confirmar explicitamente a origem, sem presumir DDN.')
        for field,index in columns.items():
            ref=f'{chr(65+index)}{row["row"]}'
            if ref in report['formulas'] or ref in report.get('errors',[]):raise ValueError(ref+': fórmula em coluna utilizada.')
            if field in ('article','lot') and ref in report['numeric'] and ref not in report.get('identifiers',{}):raise ValueError(ref+': identificador numérico; confirmar grafia/zeros como texto.')
        article,lot=get('article'),get('lot')
        if any(not isinstance(v,str) or not v.strip() or v!=v.strip() or len(v)>120 or any(ord(c)<32 for c in v) for v in (article,lot)):raise ValueError('Artigo/lote textual conhecido obrigatório.')
        literal=lambda field:report.get('numeric_literals',{}).get(f'{chr(65+columns[field])}{row["row"]}',get(field))
        boxes=integer(literal('boxes'));quantity=integer(literal('pallets'),True) if 'pallets'in columns else 1
        if len(output)+quantity>1000:raise ValueError('PACKING excede 1000 paletes.')
        for ordinal in range(1,quantity+1):output.append(dict(source_row=row['row'],ordinal=ordinal,article=article,lot=lot,boxes=boxes))
    if not output:raise ValueError('Sem paletes inequivocamente atribuídas a DAVID NETO/DDN.')
    return dict(reference=reference,reference_key=reference.casefold(),reference_cell=reference_cell,mapping=mapping,lines=output,excluded=excluded,reason=reason)
