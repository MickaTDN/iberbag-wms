"""Read-only Valmet workbook analysis. No session, engine or operational command."""
from collections import Counter
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
import unicodedata
from zipfile import ZipFile
from defusedxml import ElementTree as ET
from .workbook import workbook_rows, validate_archive, MAX_FILE, N, _sheet_paths
from .valmet import BRANDS

SHEETS={'Caixas com Peças':'pecas','Caixas Vazias':'vazias','Caixas':'caixas','Prateleiras':'prateleiras','Paletes':'paletes','Vasilhame':'vasilhame','Paletes Fio':'fio','Rolos':'rolos'}
ALIASES={'numero':('N.º Armazém','Nº Armazém','N. Armazém','TDN'),'artigo':('Artigo',),'lote':('Lote',),'local':('Localização','Localizacao'),'fisico':('Paletes','Quantidade de paletes'),'quantidade':('Quantidade','Qtd','Rolos','Peças'),'m2':('m²','m2'),'nota':('Notas','Observações'),'referencia':('Referência','Vasilhame'),'divisivel':('Divisível','Divisivel')}

def text(value):
    if value is None:return None
    if isinstance(value,bool):raise ValueError('Booleano não é identificador/quantidade.')
    if isinstance(value,(int,float,Decimal)):
        d=Decimal(str(value))
        if not d.is_finite():raise ValueError('Número não finito.')
        return format(d.normalize(),'f')
    return unicodedata.normalize('NFC',str(value)).strip() or None

def key(value):return ''.join(c for c in unicodedata.normalize('NFKD',text(value) or '').casefold() if c.isalnum())
def identifier(value):return (text(value) or '').upper() or None

def quantity(value,decimal=False):
    raw=text(value)
    if raw is None:return None
    if ',' in raw and '.' in raw:raise ValueError('Separadores numéricos ambíguos.')
    try:d=Decimal(raw.replace(',','.'))
    except InvalidOperation:raise ValueError('Quantidade inválida.')
    if not d.is_finite() or d<0 or d>2147483647 or (not decimal and d!=d.to_integral_value()):raise ValueError('Quantidade fora dos limites/inteira exigida.')
    if decimal and d.as_tuple().exponent < -3:raise ValueError('m²: até três casas decimais.')
    return format(d,'f') if decimal else int(d)

def analyze_rows(sheets,context_id,snapshot=None):
    """Input: sheet -> [(Excel row, original cell list)]. Snapshot is explicit,
    scoped, disposable read data; never discover production configuration.
    Header must be the first supplied row. Unknown layouts block, not guessed.
    """
    report=dict(context_id=context_id,preview_only=True,units=[],issues=[],totals={},rows=0)
    def issue(code,refs,severity='error'):report['issues'].append(dict(code=code,severity=severity,refs=refs))
    if snapshot is None:issue('CATALOGO_NAO_VERIFICADO',[])
    elif snapshot.get('context_id')!=context_id or snapshot.get('is_valmet') is not True:raise ValueError('Snapshot/contexto Valmet incompatível.')
    if snapshot is not None and not {'locations','issued_numbers'}<=snapshot.keys():raise ValueError('Snapshot incompleto: catálogo e livro de números obrigatórios.')
    groups={};numbers={};signatures={}
    for title,kind in SHEETS.items():
        matches=[name for name in sheets if key(name)==key(title)]
        if len(matches)>1:issue('FOLHAS_AMBIGUAS',matches);continue
        sheet=matches[0] if matches else title
        data=sheets.get(sheet)
        if data is None:issue('FOLHA_EM_FALTA',[sheet]);continue
        if not data:issue('CABECALHO_EM_FALTA',[sheet]);continue
        header_row,header=data[0];columns={};seen=set()
        for index,value in enumerate(header):
            normalized=key(value)
            for field,aliases in ALIASES.items():
                if normalized and normalized in {key(a) for a in aliases}:
                    if field in columns:issue('COLUNA_REPETIDA',[f'{sheet}!{header_row}'])
                    columns[field]=index;seen.add(index)
        if not columns:issue('ESQUEMA_DESCONHECIDO',[f'{sheet}!{header_row}']);continue
        for rownum,raw in data[1:]:
            if not any(v is not None and v!='' for v in raw):continue
            signature=repr(raw)
            if (sheet,signature) in signatures:issue('LINHA_REPETIDA_AMBIGUA',[signatures[(sheet,signature)],f'{sheet}!{rownum}'])
            signatures[(sheet,signature)]=f'{sheet}!{rownum}'
            report['rows']+=1
            if report['rows']>100000:raise ValueError('Demasiadas linhas Valmet.')
            ref=f'{sheet}!{rownum}';values={field:raw[i] if i<len(raw) else None for field,i in columns.items()}
            original=dict(cells=list(raw),row=rownum,sheet=sheet)
            try:
                normalized={f:text(v) for f,v in values.items()}
                number=identifier(values.get('numero'));lot=identifier(values.get('lote'));article=identifier(values.get('artigo'))
                loc=identifier(values.get('local'));loc='Chão' if key(loc) in ('chao','floor') else loc
                phy=quantity(values.get('fisico'));amount=quantity(values.get('quantidade'));m2=quantity(values.get('m2'),True)
                mark=identifier(values.get('divisivel'))
                if mark not in (None,'X'):issue('DIVISIBILIDADE_INVALIDA',[ref])
            except (ValueError,InvalidOperation) as error:
                issue('VALOR_INVALIDO: '+str(error),[ref]);continue
            if any(i not in seen and v not in (None,'') for i,v in enumerate(raw)):issue('COLUNAS_NAO_MAPEADAS',[ref])
            brand=next((b for b in BRANDS if key(b)==key(values.get('referencia'))),None)
            if kind=='vasilhame':
                if not brand:issue('REFERENCIA_INVALIDA',[ref])
                if loc is None:issue('POR_LOCALIZAR',[ref],'warning')
            else:
                if not lot:issue('LOTE_EM_FALTA',[ref])
                if not loc:issue('LOCALIZACAO_EM_FALTA',[ref])
            if not article:issue('ARTIGO_DESCONHECIDO',[ref],'warning')
            if kind in ('rolos','caixas','pecas','vazias') and not number:issue('NUMERO_EM_FALTA',[ref])
            if kind=='vazias' and loc!='Chão':issue('CAIXAS_VAZIAS_FORA_CHAO',[ref])
            if number and kind in ('paletes','prateleiras','fio'):issue('IDENTIDADE_HISTORICA_EXIGE_ADAPTACAO_MODELO',[ref])
            if m2 is not None and kind!='paletes':issue('M2_FORA_PALETES',[ref])
            if kind in ('rolos','pecas') and amount is None:issue('CONTEUDO_DESCONHECIDO',[ref])
            if kind in ('paletes','fio','prateleiras','vasilhame') and not number and phy is None:issue('FISICO_DESCONHECIDO',[ref])
            groupkey=(kind,number) if number else (kind,ref)
            if isinstance(values.get('numero'),(int,float)):issue('IDENTIFICADOR_NUMERICO_REVER_FORMATO',[ref],'warning')
            if number and number in numbers and numbers[number]!=kind:issue('NUMERO_ENTRE_CATEGORIAS',[ref]+groups[(numbers[number],number)]['refs'])
            if number:numbers[number]=kind
            unit=groups.setdefault(groupkey,dict(kind=kind,number=number,local=loc,physical=1 if number else phy,contents=[],refs=[],originals=[],separation=None,brand=brand))
            if loc!=unit['local'] or brand!=unit['brand']:issue('UNIDADE_CONTRADITORIA',unit['refs']+[ref])
            if number and phy not in (None,1):issue('FISICO_NUMERADO_AMBIGUO',[ref])
            if kind=='rolos':
                allowed=mark=='X'
                if unit['separation'] is not None and unit['separation']!=allowed:issue('DIVISIBILIDADE_CONTRADITORIA',unit['refs']+[ref])
                unit['separation']=allowed if unit['separation'] is None else unit['separation']
            content=dict(article=article,lot=lot,quantity=amount,m2=m2,notes=normalized.get('nota'),ref=ref)
            # Repeated lot rows may be distinct rolls; never deduplicate or sum
            # indistinguishable rows silently. Keep every source row for review.
            if any({k:v for k,v in c.items() if k!='ref'}=={k:v for k,v in content.items() if k!='ref'} for c in unit['contents']):issue('LINHAS_INDISTINGUIVEIS',unit['refs']+[ref])
            unit['contents'].append(content);unit['refs'].append(ref);unit['originals'].append(original)
            if snapshot is not None:
                if number in snapshot.get('issued_numbers',[]):issue('NUMERO_JA_EMITIDO',[ref])
                if loc:
                    place=snapshot.get('locations',{}).get(loc)
                    if not place or not place.get('active') or not place.get('exclusive'):issue('LOCAL_INVALIDO_OU_OUTRO_CONTEXTO',[ref])
    report['units']=list(groups.values())
    for unit in report['units']:
        amounts={}
        for content in unit['contents']:
            lot=content['lot'];total=amounts.setdefault(lot or '(não identificado)',dict(known=0,unknown_rows=0))
            if content['quantity'] is None:total['unknown_rows']+=1
            else:total['known']+=content['quantity']
        unit['content_totals']=amounts
    totals=Counter();unknown=Counter();occupancy=Counter()
    for unit in report['units']:
        if unit['physical'] is None:unknown[unit['kind']]+=1
        else:totals[unit['kind']]+=unit['physical'];occupancy[unit['local']]+=unit['physical']
    report['totals']={kind:dict(known_physical=totals[kind],unknown_groups=unknown[kind]) for kind in SHEETS.values()}
    if snapshot is not None:
        for loc,amount in occupancy.items():
            place=snapshot.get('locations',{}).get(loc,{})
            if place.get('capacity') is not None and amount+place.get('occupied',0)>place['capacity']:issue('CAPACIDADE_EXCEDIDA',[r for u in report['units'] if u['local']==loc for r in u['refs']])
    report['blocking']=sum(i['severity']=='error' for i in report['issues'])
    return report

def analyze(path,context_id,snapshot=None):
    path=Path(path)
    if path.stat().st_size>MAX_FILE:raise ValueError('Excel demasiado grande.')
    # Cached formula results must never be mistaken for verified literal input.
    with ZipFile(path) as archive:
        validate_archive(archive)
        for name,target in _sheet_paths(archive).items():
            if key(name) in {key(s) for s in SHEETS} and ET.fromstring(archive.read(target),forbid_dtd=True).find('.//x:f',N) is not None:raise ValueError('Fórmulas exigem revisão; pré-visualização recusada.')
        names=[name for name in _sheet_paths(archive) if key(name) in {key(s) for s in SHEETS}]
    result=analyze_rows(workbook_rows(path,tuple(names)),context_id,snapshot)
    result['sha256']=sha256(path.read_bytes()).hexdigest()
    return result

if __name__=='__main__':
    import argparse
    import json
    parser=argparse.ArgumentParser(description='Pré-visualização Valmet, sem importação nem ligação à base de dados. O relatório pode conter informação confidencial: não publicar.')
    parser.add_argument('excel');parser.add_argument('--context-id',type=int,required=True);parser.add_argument('--snapshot',help='Catálogo JSON explicitamente fornecido, do contexto autorizado; nunca descoberto automaticamente.')
    args=parser.parse_args()
    snapshot=None
    if args.snapshot:
        source=Path(args.snapshot)
        if source.stat().st_size>MAX_FILE:parser.error('Snapshot demasiado grande.')
        snapshot=json.loads(source.read_text(encoding='utf-8'))
    try:report=analyze(args.excel,args.context_id,snapshot)
    except ValueError as error:parser.error(str(error))
    print(json.dumps(report,ensure_ascii=False,indent=2))
