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
    d=data();d['Rolos'] += [(2,['001',None,'L1',' a2 ',None,1,None,'VALMET 2',None,'X']),(3,['001','ART','L1','A2',None,1,None,'nota',None,'X']),(4,['001',None,'L2','A2',None,1,None,None,None,'X'])]
    original=deepcopy(d);r=analyze_rows(d,2,snapshot());u=r['units'][0]
    assert u['physical']==1 and u['number']=='001' and len(u['contents'])==3 and u['separation'] is True
    assert sum(c['quantity'] for c in u['contents'])==3 and r['totals']['rolos']['known_physical']==1
    assert u['originals'][0]['cells'][3]==' a2 ' and u['refs']==['Rolos!2','Rolos!3','Rolos!4'] and d==original
    assert r['blocking']==0

@pytest.mark.parametrize('mark',[None,''])
def test_unmarked_rolls_not_divisible_and_contradictory_markings(mark):
    d=data();d['Rolos'] += [(2,['R',None,'A','A2',None,2,None,None,None,mark]),(3,['R',None,'B','A2',None,3,None,None,None,'X'])]
    r=analyze_rows(d,2,snapshot());assert r['units'][0]['separation']=='mixed'
    assert 'DIVISIBILIDADE_CONTRADITORIA' not in codes(r)

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
    r=analyze_rows(d,2,snapshot());assert 'UNIDADE_CONTRADITORIA' in codes(r)
    assert 'LINHAS_INDISTINGUIVEIS' not in codes(r)
    d['Caixas'] += [(2,[None,None,None,None,None,None,None,'Material fictício sem identificação'])];r=analyze_rows(d,2,snapshot());assert {'NUMERO_EM_FALTA','LOTE_EM_FALTA','LOCALIZACAO_EM_FALTA'}<=codes(r)

def test_safe_xlsx_read_only_and_formula_rejection(tmp_path):
    from openpyxl import Workbook
    path=tmp_path/'fiction.xlsx';book=Workbook();book.remove(book.active)
    for name in SHEETS:book.create_sheet(name).append(HEADER)
    book['Rolos'].append(['R',None,'A','A2',1,1,None,'nota',None,'X']);book.save(path)
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


LAYOUTS={
 'Caixas com Peças':['Artigo','Lote','Quantidade (Caixas)','Nº Armazém','Quantidade (peças)','Localização'],
 'Caixas Vazias':['Artigo','Lote','Nº Armazém'],
 'Caixas':['Artigo','Lote','Quantidade (Caixas)','Localização','Nº Armazém','Nota'],
 'Prateleiras':['Artigo','Lote','Quantidade (Paletes)','Localização','Nº Armazém','Nota'],
 'Paletes':['Artigo','Lote','Quantidade (Paletes)','Localização','Nº Armazém','Quantidade (m²)'],
 'Vasilhame':['Referência','Quantidade (Paletes)'],
 'Paletes Fio':['Artigo','Lote','Quantidade (Paletes)','Localização','Notas'],
 'Rolos':['Lote','Nº armazém','Localização','Divisível']}

def current_layouts(nondivisible=5):
    d={name:[(1,headers)] for name,headers in LAYOUTS.items()}
    samples={
     'Caixas com Peças':[[None,'L1',1,'PCS',20,'A2'],[None,'L2',None,'PCS',30,'A2']],
     'Caixas Vazias':[[None,'E1','EMPTY'],[None,'E2','EMPTY']],
     'Caixas':[[None,'C1',1,'A2','BOX','VALMET 2'],[None,'C2',None,'A2','BOX','nota livre']],
     'Prateleiras':[[None,'S1',1,'A2','SHELF','nota'],[None,'S2',None,'A2','SHELF','nota']],
     'Paletes':[[None,'P1',1,'A2','PALLET','12,345'],[None,'P2',None,'A2','PALLET',None]],
     'Vasilhame':[['Filva',4]],
     'Paletes Fio':[[None,'F1',3,'A2','nota']],
     'Rolos':[['R1','ROLL','A2','X']]*2+[['R2','ROLL','A2',None]]*nondivisible}
    for name,rows in samples.items():d[name].extend((i+2,list(row)) for i,row in enumerate(rows))
    return d

@pytest.mark.parametrize('nondivisible',[4,5])
def test_current_eight_layouts_mixed_rolls_and_legitimate_identical_rows(nondivisible):
    d=current_layouts(nondivisible);original=deepcopy(d);s=snapshot();s['locations']['A2']['capacity']=20
    r=analyze_rows(d,2,s);units={u['kind']:u for u in r['units']}
    assert {i['code'] for i in r['issues'] if i['severity']=='error'}=={'IDENTIDADE_HISTORICA_EXIGE_ADAPTACAO_MODELO'}
    rolls=units['rolos'];assert rolls['physical']==1 and rolls['separation']=='mixed'
    assert len(rolls['loading_groups']['individual'])==2 and len(rolls['loading_groups']['indivisible'])==nondivisible
    assert len(rolls['contents'])==2+nondivisible and sum(c['quantity'] for c in rolls['contents'])==2+nondivisible
    assert rolls['content_totals']=={'R1':dict(known=2,unknown_rows=0),'R2':dict(known=nondivisible,unknown_rows=0)}
    assert units['pecas']['physical']==1 and sum(c['quantity'] for c in units['pecas']['contents'])==50
    assert units['vazias']['local']=='Chão' and units['vazias']['contents'][0]['local_source']=='rule'
    assert all(c['quantity_state']=='presence' for c in units['vazias']['contents'])
    assert units['vasilhame']['physical']==4 and units['vasilhame']['local'] is None
    assert units['fio']['number'] is None and units['fio']['physical']==3
    assert units['paletes']['contents'][0]['m2']=='12.345' and units['paletes']['contents'][1]['m2'] is None
    assert units['caixas']['contents'][1]['physical_quantity'] is None and units['caixas']['physical']==1
    assert units['caixas']['contents'][1]['physical_state']=='presence' and r['schema_version']==2
    assert units['caixas']['contents'][0]['notes']=='VALMET 2' and d==original
    assert sum(t['known_physical'] for t in r['totals'].values())==13

def test_current_layout_xlsx_original_bytes_and_no_write(tmp_path):
    from openpyxl import Workbook
    book=Workbook();book.remove(book.active)
    for name,rows in current_layouts().items():
        sheet=book.create_sheet(name)
        for _,row in rows:sheet.append(row)
    path=tmp_path/'fiction-layouts.xlsx';book.save(path);before=path.read_bytes()
    result=analyze(path,2,snapshot());assert path.read_bytes()==before
    assert len(next(u for u in result['units'] if u['kind']=='rolos')['contents'])==7

@pytest.mark.parametrize('mark',['SIM','Y','XX'])
def test_invalid_roll_markings_still_block(mark):
    d={name:[(1,h)] for name,h in LAYOUTS.items()};d['Rolos'].append((2,['A','R','A2',mark]))
    assert 'DIVISIBILIDADE_INVALIDA' in codes(analyze_rows(d,2,snapshot()))
