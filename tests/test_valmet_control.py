"""Only fictitious goods; operational cases use disposable PostgreSQL 17."""
import json
import secrets
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import pytest
from sqlalchemy import select,func,text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from iberbag import valmet as v,valmet_control as c,valmet_planning as p,valmet_shipping as shipping,backup,transfer
from iberbag.database import (ValmetUnit,ValmetPosition as Position,ValmetInventory as Inventory,ValmetCount as Count,
    ValmetControlDraft as Draft,ValmetControlMove as Move,ValmetInventoryDecision as Decision,ValmetIdentification,ValmetShipmentLine,ValmetOccurrence,
    ValmetControlDocument as Document,User,Stock,Receipt)
from test_valmet import configure,valmet,row,entry
from test_receiving import warehouse
from test_transfer import pg_databases,docker_client
from test_ddn_contexts import scoped
from test_security_regressions import restricted_web
from test_valmet_shipping import prepare,send,physical
from conftest import BACKUP_PASSWORD

def positions(engine):
    with scoped(engine,2) as db:return db.scalars(select(Position).order_by(Position.id)).all()

def inventory(engine,actor,mode='partial',**kw):
    with scoped(engine,2) as db:return c.create_inventory(db,actor,secrets.token_hex(16),mode,**kw)

def count(engine,actor,ident,target,qty=None,mat=None,kind='count',**kw):
    with scoped(engine,2) as db:
        if target.startswith('P:') and qty is not None:
            pos=db.get(Position,int(target[2:]))
            if pos and db.get(ValmetUnit,pos.unit_id).kind=='rolos' and kind=='count':
                from iberbag import valmet_rolls as rolls
                ids=[];need=dict(mat or {})
                for r in rolls.present(db,pos.unit_id):
                    if need.get(r['lot'],0):ids.append(r['id']);need[r['lot']]-=1
                kw['roll_ids']=ids if not any(need.values()) else None
        return c.save_count(db,actor,ident,db.get(Inventory,ident).version,secrets.token_hex(16),target,kind,qty,mat,**kw)

def submit(engine,actor,ident):
    with scoped(engine,2) as db:return c.submit(db,actor,ident,db.get(Inventory,ident).version)

def approve(engine,actor,ident,countid,action='approve',**kw):
    with scoped(engine,2) as db:return c.decide(db,actor,ident,db.get(Inventory,ident).version,countid,secrets.token_hex(16),action,'Motivo fictício','Conferência física fictícia comprovada',**kw)

def adjust(engine,actor,pid,qty,mat,**kw):
    with scoped(engine,2) as db:
        pos=db.get(Position,pid)
        if db.get(ValmetUnit,pos.unit_id).kind=='rolos' and 'roll_change' not in kw:
            from iberbag import valmet_rolls as rolls
            keep=[];new=[];need=dict(mat)
            for r in rolls.present(db,pos.unit_id):
                if need.get(r['lot'],0):keep.append(r['id']);need[r['lot']]-=1
            for lot,q in need.items():new.extend(dict(article=None,lot=lot,divisible=False) for _ in range(q))
            kw['roll_change']=dict(keep=keep,restore=[],add=new)
        ident=c.save_adjustment(db,actor,secrets.token_hex(16),pid,kw.pop('target','A2'),qty,mat,'Motivo fictício','Contagem física fictícia comprovada',**kw)
        return c.confirm(db,actor,ident,1)

def test_control_partial_zero_unknown_revisions_and_counts_do_not_change_stock(valmet):
    engine,a=valmet;actor=a['operador'][0]
    entry(engine,actor,[row('fio',None,physical_quantity=3,content_total=None,items=[('','A','3')]),row('fio',None,local='H2',physical_quantity=2,content_total=None,items=[('','B','2')])])
    pid=positions(engine)[0].id;ident=inventory(engine,a['admin'][0],local='A2')
    with scoped(engine,2) as db:
        targets=c.inventory_targets(db,db.get(Inventory,ident));assert f'P:{pid}' in targets and not any(t.get('local')=='H2' for t in targets.values())
        assert c.latest_counts(db,ident)=={}
    blank=count(engine,actor,ident,f'P:{pid}',None,None)
    zero=count(engine,actor,ident,f'P:{pid}',0,None)
    with scoped(engine,2) as db:
        assert json.loads(db.get(Count,blank).payload)['physical'] is None
        assert json.loads(db.get(Count,zero).payload)['physical']==0
        assert c.latest_counts(db,ident)[f'P:{pid}'].id==zero
        assert db.scalar(select(func.count()).select_from(Count))==2
    submit(engine,actor,ident);assert physical(engine)==5
    with pytest.raises(v.ReceivingError):approve(engine,actor,ident,zero)
    approve(engine,a['admin'][0],ident,zero,proven=True)
    assert physical(engine)==2
    with scoped(engine,2) as db:
        assert db.scalar(select(ValmetUnit.physical_quantity).where(ValmetUnit.id==1))==3
        assert db.scalar(select(Position.physical_quantity).where(Position.local=='H2'))==2

def test_control_complete_includes_floor_and_uncounted_locations_cannot_close(valmet):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row()]);pid=positions(engine)[0].id
    ident=inventory(engine,actor,'complete');submit(engine,actor,ident)
    with scoped(engine,2) as db:
        targets=c.inventory_targets(db,db.get(Inventory,ident));assert {'L:A2','L:H2','L:Chão',f'P:{pid}'}<=set(targets)
        with pytest.raises(v.ReceivingError):c.close_inventory(db,actor,ident,db.get(Inventory,ident).version)
    values=count(engine,actor,ident,f'P:{pid}',1,{'LOT':5})
    blank=count(engine,actor,ident,'L:H2',None,None);submit(engine,actor,ident)
    with pytest.raises(v.ReceivingError):approve(engine,actor,ident,blank)
    marks=[count(engine,actor,ident,key,1 if key=='L:A2' else 0,{}) for key in ('L:A2','L:H2','L:Chão')]
    submit(engine,actor,ident)
    for cid in [values]+marks:approve(engine,actor,ident,cid)
    with scoped(engine,2) as db:
        c.close_inventory(db,actor,ident,db.get(Inventory,ident).version);assert db.get(Inventory,ident).status=='closed'
        assert db.scalar(select(func.count()).select_from(Move))==0
    assert physical(engine)==1

@pytest.mark.parametrize('kind',['paletes','fio','prateleiras'])
def test_control_lot_scope_preserves_other_materials_and_partial_location_proof(valmet,kind):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row(kind,None,physical_quantity=5,content_total=None,items=[('','A','3'),('','B','2')])]);pid=positions(engine)[0].id
    ident=inventory(engine,actor,lot='A')
    cid=count(engine,actor,ident,f'P:{pid}',1,{'A':1},observed='H2');submit(engine,actor,ident)
    approve(engine,actor,ident,cid,proven=True)
    with scoped(engine,2) as db:
        live={z.local:(z.physical_quantity,json.loads(z.materials)) for z in db.scalars(select(Position).where(Position.physical_quantity>0))}
        assert live=={'A2':(2,{'B':2}),'H2':(1,{'A':1})}
        assert db.scalar(select(ValmetUnit.physical_quantity))==5
    assert physical(engine)==3

@pytest.mark.parametrize('kind',['paletes','rolos','caixas','pecas','vazias','lab','vasilhame'])
def test_control_category_counts_and_positive_or_content_corrections(valmet,kind):
    engine,a=valmet;actor=a['admin'][0]
    if kind=='paletes':data=row(kind,None,content_total=None,items=[('','A','1'),('','B','1')]);qty=1;mat={'A':1,'B':1}
    elif kind in ('caixas','vazias'):data=row(kind,'UNIT',content_total=None,items=[('','A',''),('','B','')]);qty=1;mat={'A':1,'B':1}
    elif kind=='vasilhame':data=row(kind,None,brand='Filva',physical_quantity=2,content_total=None,items=[]);qty=3;mat={'Filva':3}
    else:data=row(kind,'UNIT',content_total=5,items=[('','A','2'),('','B','3')]);qty=1;mat={'A':3,'B':3}
    entry(engine,actor,[data]);pid=positions(engine)[0].id;ident=inventory(engine,actor)
    cid=count(engine,actor,ident,f'P:{pid}',qty,mat);submit(engine,actor,ident)
    if kind=='rolos':
        with pytest.raises(v.ReceivingError):approve(engine,actor,ident,cid,proven=True)
        adjust(engine,actor,pid,qty,mat,proven=True)
    else:approve(engine,actor,ident,cid,proven=True)
    with scoped(engine,2) as db:
        z=db.get(Position,pid);assert z.physical_quantity==qty and json.loads(z.materials)==mat
        assert db.scalar(select(func.count()).select_from(ValmetUnit))==1

def test_control_unknown_material_found_elsewhere_stays_pending_without_fake_identity(valmet):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('paletes',None,physical_quantity=2,content_total=None,pending=True,items=[])])
    pid=positions(engine)[0].id;ident=inventory(engine,actor)
    cid=count(engine,actor,ident,f'P:{pid}',3,{},observed='H2',evidence='Material desconhecido encontrado')
    unknown=count(engine,actor,ident,'U:'+secrets.token_hex(16),1,None,kind='unknown',observed='H2',evidence='Caixa encontrada, sem identificação comprovada')
    submit(engine,actor,ident)
    for target in (cid,unknown):
        with pytest.raises(v.ReceivingError):approve(engine,actor,ident,target,proven=True)
        approve(engine,actor,ident,target,action='defer')
    assert physical(engine)==2
    with scoped(engine,2) as db:
        assert db.scalar(select(func.count()).select_from(Move))==0 and not p.source(db)

def test_control_lab_unknown_needs_explicit_complete_evidence_and_admin_clear(valmet):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('lab','LAB',content_total=None,items=[('','A','')])]);pid=positions(engine)[0].id
    prep=prepare(engine,actor,[('lab','A','3')]);send(engine,actor,prep,[(1,'A','1')],unknown_ack=True)
    ident=inventory(engine,actor);unknown=count(engine,actor,ident,f'P:{pid}',1,{'A':None});submit(engine,actor,ident)
    with pytest.raises(v.ReceivingError):approve(engine,actor,ident,unknown,proven=True,clear=True)
    counted=count(engine,actor,ident,f'P:{pid}',1,{'A':4},recount=True,evidence='Quatro bobines A efetivamente contadas na palete LAB')
    submit(engine,actor,ident);approve(engine,actor,ident,counted,proven=True,clear=True)
    with scoped(engine,2) as db:assert p.source(db)[0]['available']=={'A':4} and not db.get(Position,pid).uncertain
    assert physical(engine)==1

def test_control_transfer_partial_groups_atomic_capacity_and_history(valmet):
    engine,a=valmet;actor=a['operador'][0]
    entry(engine,actor,[row('fio',None,physical_quantity=5,content_total=None,items=[('','A','3'),('','B','2')])]);pid=positions(engine)[0].id
    with scoped(engine,2) as db:
        draft=c.save_transfer(db,actor,secrets.token_hex(16),pid,'H2',2,'A','Transferir A','Duas paletes A conferidas')
        assert physical(engine)==5
        move=c.confirm(db,actor,draft,1);assert c.confirm(db,actor,draft,1)==move
        assert db.scalar(select(func.count()).select_from(Move))==1
        current={z.local:json.loads(z.materials) for z in db.scalars(select(Position).where(Position.physical_quantity>0))}
        assert current=={'A2':{'A':1,'B':2},'H2':{'A':2}}
        assert db.scalar(select(func.count()).select_from(Receipt))==1
        assert db.scalar(select(func.count()).select_from(Stock))==0
    assert physical(engine)==5

@pytest.mark.parametrize('kind',['paletes','rolos','caixas','pecas','lab'])
def test_control_transfer_never_splits_individual_identity_even_separable_rolls(valmet,kind):
    engine,a=valmet;actor=a['admin'][0]
    data=row(kind,None if kind=='paletes' else 'UNIT',content_total=None if kind in ('paletes','caixas') else 5,items=[('','A','1'),('','B','1')] if kind=='paletes' else [('','A','2'),('','B','3')])
    entry(engine,actor,[data]);pid=positions(engine)[0].id
    if kind=='rolos':
        with scoped(engine,2) as db:configure(db,actor,1,'yes',note='Separação autorizada para expedir, não para dividir palete')
    with scoped(engine,2) as db:
        with pytest.raises(v.ReceivingError):c.save_transfer(db,actor,secrets.token_hex(16),pid,'H2',0,'A','Motivo','Prova')
        draft=c.save_transfer(db,actor,secrets.token_hex(16),pid,'H2',1,'A','Mudança inteira','Unidade integral comprovada')
        c.confirm(db,actor,draft,1)
        assert db.scalar(select(Position.local).where(Position.physical_quantity>0))=='H2'
        assert db.scalar(select(func.count()).select_from(ValmetUnit))==1
    assert physical(engine)==1

def test_control_reserved_ready_and_unresolved_occurrences_block_transfers_and_adjustments(valmet):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row()]);pid=positions(engine)[0].id;prep=prepare(engine,actor,[('rolos','LOT','5')])
    with scoped(engine,2) as db:
        shipping.ready(db,actor,prep,1)
        draft=c.save_transfer(db,actor,secrets.token_hex(16),pid,'H2',1,'LOT','Mover','Prova')
        with pytest.raises(v.ReceivingError):c.confirm(db,actor,draft,1)
        with pytest.raises(v.ReceivingError):adjust(engine,actor,pid,1,{'LOT':4},proven=True)
        p.cancel(db,actor,prep,1,'Cancelar apenas reserva, não stock')
        with pytest.raises(v.ReceivingError):c.confirm(db,actor,draft,1)  # reservation ABA is detected
        c.save_transfer(db,actor,db.get(Draft,draft).request_key,pid,'H2',1,'LOT','Nova revisão','Reserva cancelada e unidade reconferida',ident=draft,version=1)
        c.confirm(db,actor,draft,2)
    assert physical(engine)==1

def test_control_adjustment_admin_only_reason_identity_and_no_fictitious_lots(valmet):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row()]);pid=positions(engine)[0].id
    with pytest.raises(v.ReceivingError):adjust(engine,a['operador'][0],pid,1,{'LOT':4},proven=True)
    with pytest.raises(v.ReceivingError):adjust(engine,actor,pid,2,{'LOT':10},proven=True)
    with pytest.raises(v.ReceivingError):adjust(engine,actor,pid,1,{'FAKE':5},proven=True)
    with scoped(engine,2) as db,pytest.raises(v.ReceivingError):c.save_adjustment(db,actor,secrets.token_hex(16),pid,'A2',1,{'LOT':3},'','Prova',True)
    assert physical(engine)==1
    adjust(engine,actor,pid,1,{'LOT':7},proven=True)
    with scoped(engine,2) as db:
        assert json.loads(db.get(Position,pid).materials)=={'LOT':7}
        assert db.scalar(select(ValmetUnit.content_total))==5

def test_control_proven_shipment_compensation_never_duplicates_or_rewrites(valmet):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('caixas','BOX',content_total=None,items=[('','A',''),('','B','')])]);pid=positions(engine)[0].id
    prep=prepare(engine,actor,[('caixas','A','1')],extra=True);send(engine,actor,prep,extra_ack=True)
    with scoped(engine,2) as db:line=db.scalar(select(ValmetShipmentLine.id));before=shipping.shipments(db,prep)[0].snapshot
    with pytest.raises(v.ReceivingError):adjust(engine,actor,pid,1,{'A':1,'B':1},proven=True)
    adjust(engine,actor,pid,1,{'A':1,'B':1},proven=True,basis='return',lineids=[line])
    assert physical(engine)==1
    with scoped(engine,2) as db:assert shipping.shipments(db,prep)[0].snapshot==before
    with pytest.raises(v.ReceivingError):adjust(engine,actor,pid,1,{'A':2,'B':1},proven=True,basis='return',lineids=[line])

def test_control_inventory_stale_after_movements_partial_reconciliation_and_recount(valmet):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('fio',None,physical_quantity=3,content_total=None,items=[('','A','3')]),row('fio',None,local='H2',physical_quantity=3,content_total=None,items=[('','B','3')])])
    first,second=[z.id for z in positions(engine)];ident=inventory(engine,actor,'complete')
    ca=count(engine,actor,ident,f'P:{first}',2,{'A':2});cb=count(engine,actor,ident,f'P:{second}',2,{'B':2});submit(engine,actor,ident)
    adjust(engine,actor,first,4,{'A':4},proven=True)
    with pytest.raises(v.ReceivingError):approve(engine,actor,ident,ca,proven=True)
    approve(engine,actor,ident,cb,proven=True)
    fresh=count(engine,actor,ident,f'P:{first}',2,{'A':2},recount=True,evidence='Nova conferência após o movimento')
    with scoped(engine,2) as db:
        report=next(row for row in c.inventory_summary(db,db.get(Inventory,ident)) if row['target'].get('position')==first)
        assert report['target']['materials']=={'A':4} and report['target']['physical']==4
    submit(engine,actor,ident);approve(engine,actor,ident,fresh,proven=True)
    assert physical(engine)==4

def test_control_parsers_keep_unknown_and_zero_separate_without_postgres():
    assert c.number('',True) is None and c.number('0',True)==0
    assert c.materials('',True) is None and c.materials('{"A":null}',True)=={'A':None}
    with pytest.raises(v.ReceivingError):c.materials('{"A":1,"A":2}')
    for value in ('1.5','-1','1e2','2147483648',False):
        with pytest.raises(v.ReceivingError):c.number(value)

def test_control_pdfs_keep_headers_unknowns_versions_and_physical_counts_on_sqlite():
    from datetime import datetime
    from pypdf import PdfReader
    from iberbag.valmet_control_pdf import render_pdf
    snapshot=dict(status='draft',actor='FICTÍCIO',rows=[dict(target=dict(kind='rolos',number='TEST',local='A2',physical=1,materials={'A':2,'B':3}),count=None,decisions=[])])
    pdf=PdfReader(BytesIO(render_pdf(1,1,'count',snapshot,datetime(2027,1,2))))
    text0=pdf.pages[0].extract_text();assert text0.splitlines()[:2]==['Valmet','02/01/2027'] and 'Nãocontado' in ''.join(text0.split())
    snapshot['rows'][0]['target']['materials']={f'LOTE-{i:03d}-'+('F'*70):None for i in range(100)}
    long=PdfReader(BytesIO(render_pdf(1,1,'differences',snapshot,datetime(2027,1,2))))
    assert len(long.pages)>1 and all(page.extract_text().splitlines()[:2]==['Valmet','02/01/2027'] for page in long.pages)

@pytest.mark.parametrize('function,args',[(c.create_inventory,('key','complete')),(c.save_count,(1,1,'key','P:1','count')),(c.submit,(1,1)),(c.save_transfer,('key',1,'Chão',1,'L','Motivo','Prova')),(c.save_adjustment,('key',1,'Chão',1,{},'Motivo','Prova')),(c.confirm,(1,1)),(c.decide,(1,1,1,'key','approve','Motivo','Prova')),(c.close_inventory,(1,1)),(c.issue_document,(1,1,'count'))])
def test_control_sqlite_rejects_all_operational_commands(function,args):
    from sqlalchemy import create_engine
    with Session(create_engine('sqlite://')) as db,pytest.raises(v.ReceivingError,match='PostgreSQL'):function(db,1,*args)

def test_control_capacity_floor_unknown_vasilhame_and_rollback(valmet):
    from iberbag.warehouse_settings import set_capacity
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('fio',None,physical_quantity=3,content_total=None,items=[('','A','3')]),row('vazias','EMPTY',content_total=None,items=[('','E','')]),row('vasilhame',None,brand='Filva',physical_quantity=2,content_total=None,local='',items=[])])
    first,box,vas=positions(engine)
    with scoped(engine,2) as db:
        set_capacity(db,'admin','H2','1')
        bad=c.save_transfer(db,actor,secrets.token_hex(16),first.id,'H2',2,'A','Mudança','Duas paletes conferidas')
        with pytest.raises(v.ReceivingError):c.confirm(db,actor,bad,1)
        assert db.get(Draft,bad).status=='draft' and db.scalar(select(func.count()).select_from(Move))==0
        badbox=c.save_transfer(db,actor,secrets.token_hex(16),box.id,'A2',1,'E','Mudança','Prova')
        with pytest.raises(v.ReceivingError):c.confirm(db,actor,badbox,1)
        with pytest.raises(v.ReceivingError):c.save_transfer(db,a['operador'][0],secrets.token_hex(16),vas.id,'Chão',1,'Filva','Mudança','Prova',located=True)
        with pytest.raises(v.ReceivingError):c.save_transfer(db,actor,secrets.token_hex(16),vas.id,'Chão',1,'Filva','Mudança','Prova')
        valid=c.save_transfer(db,actor,secrets.token_hex(16),vas.id,'Chão',1,'Filva','Localização comprovada','Palete Filva localizada fisicamente',located=True)
        c.confirm(db,actor,valid,1)
        assert sum(z.physical_quantity for z in db.scalars(select(Position).where(Position.unit_id==vas.unit_id)))==2
    assert physical(engine)==6

def test_control_occurrences_require_explicit_resolution_and_no_silent_reservations(valmet):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row()]);pid=positions(engine)[0].id;prep=prepare(engine,actor,[('rolos','LOT','5')])
    with scoped(engine,2) as db:
        occurrence=shipping.report(db,actor,prep,1,1,'missing','Não encontrado, conferir')
        p.cancel(db,actor,prep,1,'Cancelar reserva, manter ocorrência')
        draft=c.save_transfer(db,actor,secrets.token_hex(16),pid,'H2',1,'LOT','Mudança','Prova')
        with pytest.raises(v.ReceivingError):c.confirm(db,actor,draft,1)
    with pytest.raises(v.ReceivingError):adjust(engine,actor,pid,1,{'LOT':4},proven=True)
    adjust(engine,actor,pid,1,{'LOT':4},proven=True,resolves=[occurrence])
    with scoped(engine,2) as db:
        assert not c.unresolved(db,1) and not p.active(db)
        assert db.scalar(select(ValmetOccurrence).where(ValmetOccurrence.resolves_id==occurrence))
        assert p.source(db)[0]['available']=={'LOT':4}

def test_control_pending_identification_after_count_never_refills_original_stock(valmet):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('fio',None,physical_quantity=3,content_total=None,pending=True,items=[])])
    pid=positions(engine)[0].id
    adjust(engine,actor,pid,2,{},proven=True)
    with scoped(engine,2) as db:
        v.identify(db,actor,1,secrets.token_hex(16),[('','A','3')],notes='Identificação da entrada original')
        z=db.get(Position,pid);assert z.physical_quantity==2 and json.loads(z.materials)=={} and z.uncertain
        assert not p.source(db)
    adjust(engine,actor,pid,2,{'A':2},proven=True,clear=True)
    with scoped(engine,2) as db:assert p.source(db)[0]['available']=={'A':2}
    assert physical(engine)==2

@pytest.mark.parametrize('same_key',[False,True])
def test_control_concurrent_confirmation_never_doubles_movements_or_capacity(valmet,same_key):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('fio',None,physical_quantity=3,content_total=None,items=[('','A','3')])]);pid=positions(engine)[0].id
    with scoped(engine,2) as db:
        first=c.save_transfer(db,actor,secrets.token_hex(16),pid,'H2',2,'A','Mover','Prova')
        second=first if same_key else c.save_transfer(db,actor,secrets.token_hex(16),pid,'H2',2,'A','Mover','Prova')
    def run(ident):
        try:
            with scoped(engine,2) as db:return c.confirm(db,actor,ident,1)
        except v.ReceivingError:return None
    with ThreadPoolExecutor(2) as pool:result=list(pool.map(run,[first,second]))
    assert sum(r is not None for r in result)==(2 if same_key else 1)
    with scoped(engine,2) as db:
        assert db.scalar(select(func.count()).select_from(Move))==1
        assert {z.local:z.physical_quantity for z in db.scalars(select(Position))}=={'A2':1,'H2':2}
    assert physical(engine)==3

def test_control_simultaneous_inventories_reservation_aba_and_new_receipts(valmet):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('fio',None,physical_quantity=4,content_total=None,items=[('','A','4')])]);pid=positions(engine)[0].id
    first=inventory(engine,actor,'complete');second=inventory(engine,actor,'complete')
    ca=count(engine,actor,first,f'P:{pid}',3,{'A':3});cb=count(engine,actor,second,f'P:{pid}',2,{'A':2})
    submit(engine,actor,first);submit(engine,actor,second);approve(engine,actor,first,ca,proven=True)
    with pytest.raises(v.ReceivingError):approve(engine,actor,second,cb,proven=True)
    fresh=count(engine,actor,second,f'P:{pid}',2,{'A':2},recount=True,evidence='Nova contagem física')
    submit(engine,actor,second);prep=prepare(engine,actor,[('fio','A','1')])
    with scoped(engine,2) as db:p.cancel(db,actor,prep,1,'Libertar reserva')
    with pytest.raises(v.ReceivingError):approve(engine,actor,second,fresh,proven=True)
    entry(engine,actor,[row('fio',None,local='H2',physical_quantity=2,content_total=None,items=[('','B','2')])])
    with scoped(engine,2) as db:
        targets=c.inventory_targets(db,db.get(Inventory,first));assert f'P:{positions(engine)[1].id}' in targets
    assert physical(engine)==5

def test_control_failure_after_projection_rolls_back_entire_command(valmet,monkeypatch):
    engine,a=valmet;actor=a['admin'][0];entry(engine,actor,[row()]);pid=positions(engine)[0].id
    with scoped(engine,2) as db:draft=c.save_transfer(db,actor,secrets.token_hex(16),pid,'H2',1,'LOT','Mover','Prova')
    original=c.journal
    def fail(db,actor,action,details):
        if action=='MOVIMENTO':raise RuntimeError('Falha fictícia após projeção')
        return original(db,actor,action,details)
    monkeypatch.setattr(c,'journal',fail)
    with scoped(engine,2) as db,pytest.raises(RuntimeError):c.confirm(db,actor,draft,1)
    with scoped(engine,2) as db:
        assert db.get(Draft,draft).status=='draft' and db.scalar(select(func.count()).select_from(Move))==0
        assert db.get(Position,pid).local=='A2'
    assert physical(engine)==1

def test_control_pdf_archived_without_movements_backup_restores_all_contexts(valmet,pg_databases,tmp_path):
    engine,a=valmet;actor=a['admin'][0];entry(engine,actor,[row()]);pid=positions(engine)[0].id
    ident=inventory(engine,actor);cid=count(engine,actor,ident,f'P:{pid}',1,{'LOT':4});submit(engine,actor,ident);approve(engine,actor,ident,cid,proven=True)
    with scoped(engine,2) as db:
        version=db.get(Inventory,ident).version
        doc=c.issue_document(db,actor,ident,version,'differences');content=bytes(db.get(Document,doc).pdf)
        assert c.issue_document(db,actor,ident,version,'differences')==doc
        assert db.scalar(select(func.count()).select_from(Move))==1
    pending=inventory(engine,actor)
    pending_count=count(engine,actor,pending,f'P:{pid}',1,{'LOT':3})
    with engine.connect() as connection:expected=transfer.inventory(transfer.read_data(connection))
    folder=tmp_path/'control';target=pg_databases(restricted=True)
    backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert backup.restore(target,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'
    with target.connect() as connection:assert transfer.inventory(transfer.read_data(connection))==expected
    with scoped(target,2) as db:
        assert bytes(db.get(Document,doc).pdf)==content
        assert c.issue_document(db,actor,ident,version,'differences')==doc
        assert json.loads(db.get(Position,pid).materials)=={'LOT':4}
    submit(target,actor,pending);approve(target,actor,pending,pending_count,proven=True)
    with scoped(target,2) as db:assert json.loads(db.get(Position,pid).materials)=={'LOT':3}

def test_control_http_runtime_csrf_roles_context_forms_and_document_isolation(restricted_web):
    app,owner,runtime,role,a,tokens=restricted_web;actor=a['admin'][0]
    from iberbag.warehouse_settings import add_location
    from iberbag.database import StorageContext
    from iberbag.privileges import validate_runtime
    with scoped(owner,2) as db:add_location(db,'admin','Chão')
    entry(runtime,actor,[row(local='Chão')]);pid=positions(runtime)[0].id
    ident=inventory(runtime,actor,local='Chão')
    from iberbag.valmet_rolls import present
    with scoped(runtime,2) as db:roll_ids=[r['id'] for r in present(db,1)]
    with TestClient(app) as client:
        assert client.get('/valmet/inventories').status_code==403
        client.cookies.set('wms_session',tokens['operador'][0]);client.cookies.set('ddn_context','2')
        form=dict(csrf=tokens['operador'][1],context_id=2,version=1)
        for path in ('/valmet/inventories','/valmet/transfers',f'/valmet/inventories/{ident}',f'/valmet/inventories/{ident}/count?target=P:{pid}',f'/valmet/controls/new?kind=transfer&position={pid}'):
            assert client.get(path).status_code==200,path
        assert client.get('/valmet/adjustments').status_code==403
        assert client.get(f'/valmet/controls/new?kind=adjustment&position={pid}').status_code==403
        path=f'/valmet/inventories/{ident}/count'
        body=dict(form,key=secrets.token_hex(16),target=f'P:{pid}',kind='count',physical=1,lot0='LOT',q0=4,evidence='Contado pelo escritório',roll_counted='yes',roll_ids=' '.join(map(str,roll_ids[:4])))
        assert client.post(path,data=dict(body,csrf='wrong')).status_code==403
        assert client.post(path,data=dict(body,context_id=1)).status_code==409
        result=client.post(path,data=body,follow_redirects=False);assert result.status_code==303
        assert client.post(path,data=body,follow_redirects=False).status_code==303
        with scoped(runtime,2) as db:
            assert db.scalar(select(func.count()).select_from(Count))==1
            ver=db.get(Inventory,ident).version;cid=db.scalar(select(Count.id))
        assert client.post(f'/valmet/inventories/{ident}/submit',data=dict(form,version=ver),follow_redirects=False).status_code==303
        assert client.post(f'/valmet/inventories/{ident}/decide',data=form).status_code==403
        client.cookies.set('wms_session',tokens['admin'][0]);form.update(csrf=tokens['admin'][1])
        with scoped(runtime,2) as db:ver=db.get(Inventory,ident).version
        assert client.get('/valmet/adjustments').status_code==200
        assert client.get(f'/valmet/controls/new?kind=adjustment&position={pid}').status_code==200
        assert client.post(f'/valmet/inventories/{ident}/decide',data=dict(form,version=ver,count=cid,key=secrets.token_hex(16),action='approve',reason='Diferença conferida',evidence='Prova física fictícia',proven='yes'),follow_redirects=False).status_code==303
        with scoped(runtime,2) as db:ver=db.get(Inventory,ident).version
        result=client.post(f'/valmet/inventories/{ident}/document',data=dict(form,version=ver,kind='differences'),follow_redirects=False)
        assert result.status_code==303;pdfpath=result.headers['location'];original=client.get(pdfpath).content;assert original.startswith(b'%PDF')
        page=client.get(f'/valmet/inventories/{ident}');assert page.status_code==200
        import re
        assert all(re.fullmatch('[a-f0-9]{32}',key) for key in re.findall('name="key" value="([^"]+)"',page.text))
        review=client.post('/valmet/controls/review',data=dict(form,key=secrets.token_hex(16),kind='adjustment',position=pid,target='Chão',physical=1,lot0='LOT',q0=3,reason='Conferência',evidence='Nova contagem',proven='yes',roll_keep=' '.join(map(str,roll_ids[:3]))),follow_redirects=False)
        assert review.status_code==303 and client.get(review.headers['location']).status_code==200
        assert client.post(review.headers['location']+'/confirm',data=form,follow_redirects=False).status_code==303
        assert client.get(pdfpath).content==original
        with Session(owner) as db:
            ctx=StorageContext(client_id=2,warehouse_id=2,area='VALMET FICTÍCIO',active=True);db.add(ctx);db.commit();other=ctx.id
        client.cookies.set('ddn_context',str(other))
        for path in (pdfpath,f'/valmet/inventories/{ident}',review.headers['location']):assert client.get(path).status_code==404
        assert client.post(review.headers['location']+'/confirm',data=dict(form,context_id=other)).status_code==404
        client.cookies.set('ddn_context','1')
        assert client.get('/valmet/inventories').status_code==403
    with runtime.connect() as connection:validate_runtime(connection)
    with scoped(runtime,2) as db:assert json.loads(db.get(Position,pid).materials)=={'LOT':3}
    with scoped(runtime,1) as db:assert not c.lookup(db) and not list(db.scalars(select(Move)))

def test_control_runtime_guards_immutable_ledgers_and_definer_spoofing(restricted_web):
    app,owner,runtime,role,a,tokens=restricted_web;actor=a['admin'][0]
    from iberbag.warehouse_settings import add_location
    from iberbag.privileges import validate_runtime
    with scoped(owner,2) as db:add_location(db,'admin','Chão')
    entry(runtime,actor,[row(local='Chão')]);pid=positions(runtime)[0].id
    ident=inventory(runtime,actor);cid=count(runtime,actor,ident,f'P:{pid}',1,{'LOT':4});submit(runtime,actor,ident);approve(runtime,actor,ident,cid,proven=True)
    with scoped(runtime,2) as db:
        doc=c.issue_document(db,actor,ident,db.get(Inventory,ident).version,'differences')
        for table in ('valmet_counts','valmet_control_moves','valmet_inventory_decisions','valmet_control_documents','audit','valmet_positions'):
            with pytest.raises(DBAPIError):db.execute(text(f'DELETE FROM {table}'))
            db.rollback()
        with pytest.raises(DBAPIError):db.execute(text('UPDATE valmet_positions SET physical_quantity=99'))
        db.rollback()
    # A maintenance owner could always change data, but even its forged temp trigger
    # must not exercise the SECURITY DEFINER writer for an arbitrary table.
    with owner.connect() as connection:
        connection.exec_driver_sql('CREATE TEMP TABLE spoof_writer (LIKE valmet_control_moves INCLUDING DEFAULTS)')
        connection.exec_driver_sql('CREATE TRIGGER forged BEFORE INSERT ON spoof_writer FOR EACH ROW EXECUTE FUNCTION valmet_apply_control()')
        with pytest.raises(DBAPIError):connection.exec_driver_sql("INSERT INTO spoof_writer SELECT * FROM valmet_control_moves LIMIT 1")
        connection.rollback()
    with runtime.connect() as connection:validate_runtime(connection)
    assert physical(runtime)==1

def test_control_migrate_ddn8_preserves_shipments_reservations_documents_and_positions(pg_databases,tmp_path):
    from alembic import command
    from iberbag.migrations import config,REVISION
    from iberbag.schema_upgrade import apply_upgrade
    from iberbag.security import set_password
    from legacy_catalogue import seed_legacy_catalogue
    from iberbag.warehouse_settings import add_location
    engine=pg_databases(restricted=True);target=pg_databases(restricted=True)
    with engine.begin() as connection:
        cfg=config();cfg.attributes['connection']=connection;command.upgrade(cfg,'ddn_0008')
    with scoped(engine) as db:
        seed_legacy_catalogue(db);user=User(username='admin',display_name='Fictício',role='admin',active=True)
        set_password(user,secrets.token_urlsafe(24));db.add(user);db.commit();actor=user.id
    with scoped(engine,2) as db:add_location(db,'admin','Chão')
    entry(engine,actor,[row('fio',None,local='Chão',physical_quantity=4,content_total=None,items=[('','A','4')])])
    prep=prepare(engine,actor,[('fio','A','3')])
    with scoped(engine,2) as db:shipping.issue_document(db,actor,prep,1)
    send(engine,actor,prep,[(1,'A','1')])
    with engine.connect() as connection:before=transfer.inventory(transfer.read_data(connection))
    folder=tmp_path/'ddn8';backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    backup.restore(target,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert apply_upgrade(engine,folder)['revision']==REVISION=='ddn_0016'
    with engine.connect() as connection:
        assert transfer.inventory(transfer.read_data(connection))==before
        assert connection.execute(text('SELECT version_num FROM alembic_version')).scalars().all()==['ddn_0016']
    with scoped(engine,2) as db:
        assert sum(h.physical for h in p.active(db))==2
        assert db.scalar(select(func.count()).select_from(Move))==0
        assert json.loads(db.scalar(select(Position.materials)))=={'A':3}
    send(engine,actor,prep);assert physical(engine)==1

def test_control_case_insensitive_lot_scope_and_multilot_pallet_counts_once(valmet):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('paletes',None,content_total=None,items=[('','LOTE-A','1'),('','LOTE-B','1')])]);pid=positions(engine)[0].id
    ident=inventory(engine,actor,lot='lote-a')
    with scoped(engine,2) as db:
        selected=c.inventory_targets(db,db.get(Inventory,ident))[f'P:{pid}'];assert selected['keys']==['LOTE-A'] and selected['physical']==1
    with pytest.raises(v.ReceivingError):count(engine,actor,ident,f'P:{pid}',0,{'LOTE-A':0})
    cid=count(engine,actor,ident,f'P:{pid}',1,{'LOTE-A':1});submit(engine,actor,ident);approve(engine,actor,ident,cid)
    assert physical(engine)==1
    with scoped(engine,2) as db:assert json.loads(db.get(Position,pid).materials)=={'LOTE-A':1,'LOTE-B':1}

def test_control_lab_unknown_and_legacy_empty_boxes_cannot_become_available_or_transfer(valmet):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('lab','LAB',content_total=None,items=[('','A','')])]);pid=positions(engine)[0].id
    prep=prepare(engine,actor,[('lab','A','2')]);send(engine,actor,prep,[(1,'A','1')],unknown_ack=True)
    with scoped(engine,2) as db:
        draft=c.save_transfer(db,actor,secrets.token_hex(16),pid,'H2',1,'A','Mover','Só localização, sem contagem')
        with pytest.raises(v.ReceivingError):c.confirm(db,actor,draft,1)
    with pytest.raises(v.ReceivingError):adjust(engine,actor,pid,1,{'A':None},proven=True,clear=True)
    with scoped(engine,2) as db:
        assert db.get(Position,pid).uncertain and json.loads(db.get(Position,pid).materials)=={'A':None} and not p.source(db)

def test_control_legacy_empty_box_identity_not_invented(valmet):
    from iberbag.database import ValmetEntry,ValmetItem
    engine,a=valmet;actor=a['admin'][0]
    eid=entry(engine,actor,[row('vazias','UNUSED',content_total=None,items=[('','E','')])],confirm=False)
    with scoped(engine,2) as db:
        unit=ValmetUnit(entry_id=eid,kind='vazias',legacy_unnumbered=True,physical_quantity=2,local='Chão',supplier='',guide='',notes='Histórico fictício sem número')
        db.add(unit);db.flush();uid=unit.id
        ident=ValmetIdentification(unit_id=uid,request_key=secrets.token_hex(16),actor='admin',notes='Prova antiga');db.add(ident);db.flush()
        db.add(ValmetItem(unit_id=uid,identification_id=ident.id,lot='E'));db.commit()
    pos=positions(engine)[0]
    with scoped(engine,2) as db:
        draft=c.save_transfer(db,actor,secrets.token_hex(16),pos.id,'A2',2,'E','Mover','Prova')
        with pytest.raises(v.ReceivingError):c.confirm(db,actor,draft,1)
    inv=inventory(engine,actor);cid=count(engine,actor,inv,f'P:{pos.id}',3,{'E':3});submit(engine,actor,inv)
    with pytest.raises(v.ReceivingError):approve(engine,actor,inv,cid,proven=True)
    with pytest.raises(v.ReceivingError):adjust(engine,actor,pos.id,3,{'E':3},target='Chão',proven=True)
    with scoped(engine,2) as db:assert db.get(ValmetUnit,uid).number is None and not p.source(db)

def test_control_multiple_shipment_compensations_bounded_and_reserved_returns_blocked(valmet):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('fio',None,physical_quantity=5,content_total=None,items=[('','A','5')])]);pid=positions(engine)[0].id
    prep=prepare(engine,actor,[('fio','A','4')]);send(engine,actor,prep,[(1,'A','2')]);send(engine,actor,prep)
    with scoped(engine,2) as db:lines=list(db.scalars(select(ValmetShipmentLine.id).order_by(ValmetShipmentLine.id)))
    assert len(lines)==2 and physical(engine)==1
    adjust(engine,actor,pid,4,{'A':4},proven=True,basis='return',lineids=lines)
    adjust(engine,actor,pid,1,{'A':1},proven=True)
    with pytest.raises(v.ReceivingError):adjust(engine,actor,pid,3,{'A':3},proven=True,basis='return',lineids=lines)
    reserved=prepare(engine,actor,[('fio','A','1')])
    with pytest.raises(v.ReceivingError):adjust(engine,actor,pid,2,{'A':2},proven=True,basis='return',lineids=lines)
    with scoped(engine,2) as db:p.cancel(db,actor,reserved,1,'Libertar reserva explicitamente')
    adjust(engine,actor,pid,2,{'A':2},proven=True,basis='return',lineids=lines)
    assert physical(engine)==2
    with pytest.raises(v.ReceivingError):adjust(engine,actor,pid,3,{'A':3},proven=True,basis='return',lineids=lines)


def test_control_location_totals_cannot_hide_material_or_treat_blank_as_zero(valmet):
    engine,a=valmet;actor=a['admin'][0];entry(engine,actor,[row()]);pid=positions(engine)[0].id
    ident=inventory(engine,actor,'complete');cid=count(engine,actor,ident,f'P:{pid}',1,{'LOT':5})
    marks=[count(engine,actor,ident,key,0,{}) for key in ('L:A2','L:H2','L:Chão')]
    submit(engine,actor,ident)
    for target in [cid]+marks:approve(engine,actor,ident,target)
    with scoped(engine,2) as db,pytest.raises(v.ReceivingError):c.close_inventory(db,actor,ident,db.get(Inventory,ident).version)
    corrected=count(engine,actor,ident,'L:A2',1,{})
    submit(engine,actor,ident);approve(engine,actor,ident,corrected)
    with scoped(engine,2) as db:c.close_inventory(db,actor,ident,db.get(Inventory,ident).version)
    assert physical(engine)==1

    second=inventory(engine,actor,'complete')
    recorded=count(engine,actor,second,f'P:{pid}',1,{'LOT':5})
    marks=[count(engine,actor,second,key,1 if key=='L:A2' else 0,{}) for key in ('L:A2','L:H2','L:Chão')]
    submit(engine,actor,second)
    for item in [recorded]+marks:approve(engine,actor,second,item)
    adjust(engine,actor,pid,1,{'LOT':4},proven=True)
    with scoped(engine,2) as db,pytest.raises(v.ReceivingError):c.close_inventory(db,actor,second,db.get(Inventory,second).version)
    fresh=count(engine,actor,second,f'P:{pid}',1,{'LOT':4},recount=True,evidence='Nova conferência após ajuste posterior à primeira aprovação')
    submit(engine,actor,second);approve(engine,actor,second,fresh)
    with scoped(engine,2) as db:c.close_inventory(db,actor,second,db.get(Inventory,second).version)
