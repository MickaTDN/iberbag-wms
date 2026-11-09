"""Synthetic workbooks only; analysis never opens a database."""
from copy import deepcopy
from hashlib import sha256
import pytest
from iberbag.valmet_preview import analyze_rows,analyze,quantity,identifier,SHEETS

HEADER=['N.º Armazém','Artigo','Lote','Localização','Paletes','Quantidade','m²','Notas','Referência','Divisível']
def data():return {s:[(1,HEADER)] for s in SHEETS}
def snapshot():return dict(context_id=2,is_valmet=True,issued_numbers=[],locations={'A2':dict(active=True,exclusive=True,capacity=10,occupied=0),'Chão':dict(active=True,exclusive=True,capacity=None)})
def codes(r):return {i['code'] for i in r['issues']}

def test_grouping_repeated_lots_one_pallet_originals_and_divisibility():
    d=data();d['Rolos'] += [(2,['001',None,'L1',' a2 ',None,2,None,'VALMET 2',None,'X']),(3,['001','ART','L1','A2',None,3,None,'nota',None,'X']),(4,['001',None,'L2','A2',None,4,None,None,None,'X'])]
    original=deepcopy(d);r=analyze_rows(d,2,snapshot());u=r['units'][0]
    assert u['physical']==1 and u['number']=='001' and len(u['contents'])==3 and u['separation'] is True
    assert sum(c['quantity'] for c in u['contents'])==9 and r['totals']['rolos']['known_physical']==1
    assert u['originals'][0]['cells'][3]==' a2 ' and u['refs']==['Rolos!2','Rolos!3','Rolos!4'] and d==original
    assert r['blocking']==0

@pytest.mark.parametrize('mark',[None,''])
def test_unmarked_rolls_not_divisible_and_contradictory_markings(mark):
    d=data();d['Rolos'] += [(2,['R',None,'A','A2',None,2,None,None,None,mark]),(3,['R',None,'B','A2',None,3,None,None,None,'X'])]
    r=analyze_rows(d,2,snapshot());assert r['units'][0]['separation'] is False
    assert 'DIVISIBILIDADE_CONTRADITORIA' in codes(r)

@pytest.mark.parametrize('sheet',['Caixas','Caixas Vazias','Caixas com Peças','Paletes','Prateleiras'])
def test_numbered_multilot_units_keep_historic_numbers(sheet):
    d=data();d[sheet]+=[(2,[123,None,'A','Chão',1,2]),(3,[123,None,'B','Chão',1,3])]
    r=analyze_rows(d,2,snapshot());assert len(r['units'])==1 and r['units'][0]['number']=='123' and r['units'][0]['physical']==1
    assert ('IDENTIDADE_HISTORICA_EXIGE_ADAPTACAO_MODELO' in codes(r))==(sheet in ('Paletes','Prateleiras'))

@pytest.mark.parametrize('value',[None,''])
def test_blank_quantities_never_zero(value):
    d=data();d['Paletes'] += [(2,[None,None,'A','A2',value,None,'12,345','VALMET 2'])]
    r=analyze_rows(d,2,snapshot());assert r['totals']['paletes']['unknown_groups']==1
    assert r['units'][0]['contents'][0]['m2']=='12.345' and 'FISICO_DESCONHECIDO' in codes(r)

@pytest.mark.parametrize('value',['1.000,5','1,000.5',True,-1,'2.5','NaN'])
def test_ambiguous_invalid_quantities_block(value):
    with pytest.raises(ValueError):quantity(value)

def test_identity_catalogue_capacity_context_and_no_mutation():
    d=data();d['Rolos'] += [(2,['R',None,'A','A2',1,2])];s=snapshot();s['issued_numbers']=['R'];s['locations']['A2']['exclusive']=False
    before=deepcopy(s);r=analyze_rows(d,2,s);assert {'NUMERO_JA_EMITIDO','LOCAL_INVALIDO_OU_OUTRO_CONTEXTO'}<=codes(r);assert s==before
    with pytest.raises(ValueError):analyze_rows(d,3,s)
    s['is_valmet']=False
    with pytest.raises(ValueError):analyze_rows(d,2,s)
    assert 'CATALOGO_NAO_VERIFICADO' in codes(analyze_rows(d,2))

def test_contradictory_duplicates_and_missing_fields():
    d=data();d['Rolos'] += [(2,['R',None,'A','A2',1,2]),(3,['R',None,'A','A2',1,2]),(4,['R',None,'B','Chão',1,None])]
    r=analyze_rows(d,2,snapshot());assert {'LINHAS_INDISTINGUIVEIS','UNIDADE_CONTRADITORIA','CONTEUDO_DESCONHECIDO'}<=codes(r)
    d['Caixas'] += [(2,[None,None,None,None,None,None,None,'Material fictício sem identificação'])];r=analyze_rows(d,2,snapshot());assert {'NUMERO_EM_FALTA','LOTE_EM_FALTA','LOCALIZACAO_EM_FALTA'}<=codes(r)

def test_safe_xlsx_read_only_and_formula_rejection(tmp_path):
    from openpyxl import Workbook
    path=tmp_path/'fiction.xlsx';book=Workbook();book.remove(book.active)
    for name in SHEETS:book.create_sheet(name).append(HEADER)
    book['Rolos'].append(['R',None,'A','A2',1,3,None,'nota',None,'X']);book.save(path)
    before=sha256(path.read_bytes()).hexdigest();r=analyze(path,2,snapshot());assert r['sha256']==before and sha256(path.read_bytes()).hexdigest()==before and r['blocking']==0
    book['Rolos']['F2']='=1+2';book.save(path)
    with pytest.raises(ValueError,match='Fórmulas'):analyze(path,2,snapshot())


def test_preview_never_uses_database_and_detects_capacity_and_missing_sheets(monkeypatch):
    import sqlalchemy
    from sqlalchemy.orm import Session
    def forbidden(*args,**kwargs):raise AssertionError('No database access permitted')
    monkeypatch.setattr(sqlalchemy,'create_engine',forbidden);monkeypatch.setattr(Session,'commit',forbidden)
    d=data();d['Paletes Fio'] += [(2,[None,None,'A','A2',3])]
    s=snapshot();s['locations']['A2']['capacity']=2
    assert 'CAPACIDADE_EXCEDIDA' in codes(analyze_rows(d,2,s))
    del d['Caixas'];assert 'FOLHA_EM_FALTA' in codes(analyze_rows(d,2,s))
    assert identifier(' 001 ' )=='001' and identifier(12.0)=='12'


def test_duplicate_numbers_across_categories_and_uppercase_sheet_names():
    d=data();d['Caixas'] += [(2,['R',None,'A','Chão'])];d['ROLOS']=d.pop('Rolos');d['ROLOS'] += [(2,['R',None,'B','A2',1,3])]
    assert 'NUMERO_ENTRE_CATEGORIAS' in codes(analyze_rows(d,2,snapshot()))
