"""Fictitious fulfilment, current balances and immutable evidence on PostgreSQL 17."""
import secrets
import json
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select,func,text
from sqlalchemy.exc import DBAPIError
from fastapi.testclient import TestClient
from pypdf import PdfReader
from iberbag import valmet as v,valmet_planning as p,valmet_shipping as s,backup,transfer
from iberbag.database import (ValmetUnit,ValmetPosition,ValmetPreparation as Prep,ValmetReservation as Hold,
    ValmetPlanRevision as Rev,ValmetShipment as Shipment,ValmetShipmentLine as Line,ValmetOccurrence as Occurrence,
    ValmetDocument as Document,ValmetRelocation as Relocation,Location,Stock,CustomerPending)
from test_valmet import valmet,row,entry
from test_valmet_planning import draft,propose,confirm,units
from test_receiving import warehouse
from test_transfer import pg_databases,docker_client
from test_ddn_contexts import scoped
from test_security_regressions import restricted_web
from conftest import BACKUP_PASSWORD

def prepare(engine,actor,lines,extra=False,destination='DESTINO FICTÍCIO'):
    ident=draft(engine,actor,lines,destination=destination);propose(engine,actor,ident)
    confirm(engine,actor,ident,accept_extra=extra,accept_uncontrolled=True,accept_unlocated=True)
    return ident

def send(engine,actor,ident,lines=None,key=None,**kwargs):
    with scoped(engine,2) as db:
        version=db.get(Prep,ident).version;s.ready(db,actor,ident,version)
        return s.ship(db,actor,ident,version,key or secrets.token_hex(16),'2027-03-01',['GT FICT-1','CMR FICT-2'],lines,**kwargs)

def physical(engine):
    with scoped(engine,2) as db:return db.scalar(select(func.sum(ValmetPosition.physical_quantity))) or 0

def test_shipping_without_pdf_or_individual_checks_and_repeated_confirmation(valmet):
    engine,a=valmet;actor=a['operador'][0]
    entry(engine,actor,[row('paletes',None,physical_quantity=5,content_total=None,items=[('','LOT','5')])])
    ident=prepare(engine,actor,[('paletes','LOT','4')]);key=secrets.token_hex(16)
    assert physical(engine)==5
    with scoped(engine,2) as db:
        s.ready(db,actor,ident,1);s.ready(db,actor,ident,1)
        assert db.get(Prep,ident).status=='ready'
        assert physical(engine)==5
        first=s.ship(db,actor,ident,1,key,'2027-03-01',['GT FICT','CMR FICT'])
        assert s.ship(db,actor,ident,1,key,'2027-03-01',['GT FICT','CMR FICT'])==first
        assert db.get(Prep,ident).status=='shipped' and not p.active(db)
        assert db.scalar(select(func.count()).select_from(Document))==0
        assert db.scalar(select(func.count()).select_from(CustomerPending))==0
        assert db.scalar(select(ValmetUnit.physical_quantity))==5
        assert v.stock(db)[0][0][0].physical_quantity==1
        assert p.source(db)[0]['available']=={'LOT':1}
        with pytest.raises(v.ReceivingError):s.ship(db,actor,ident,1,secrets.token_hex(16),'2027-03-01',['GT FICT'])
    assert physical(engine)==1

@pytest.mark.parametrize('kind',['paletes','fio','prateleiras'])
def test_shipping_partial_groups_multiple_loads_keep_remaining_reservations(valmet,kind):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row(kind,None,physical_quantity=5,content_total=None,items=[('','LOT','5')])])
    uid=units(engine)[0].id;ident=prepare(engine,actor,[(kind,'LOT','4')])
    first=send(engine,actor,ident,[(uid,'LOT','2')])
    assert physical(engine)==3
    with scoped(engine,2) as db:
        record=db.get(Prep,ident);assert record.status=='ready'
        assert sum(h.physical for h in p.active(db))==2
        assert p.source(db)[0]['available']=={'LOT':1}
        assert json.loads(db.get(Shipment,first).snapshot)['actual']['shortages'][0]['quantity']==2
        assert len(json.loads(db.get(Shipment,first).references))==2
    send(engine,actor,ident)
    assert physical(engine)==1
    with scoped(engine,2) as db:
        assert len(s.shipments(db,ident))==2 and not s.summary(db,db.get(Prep,ident))['shortages']
        other=prepare(engine,actor,[(kind,'LOT','2')])
        assert json.loads(db.get(Prep,other).review)['shortages'][0]['quantity']==1

@pytest.mark.parametrize('kind',['paletes','caixas','vazias','pecas'])
def test_shipping_whole_multilot_units_only_once(valmet,kind):
    engine,a=valmet;actor=a['admin'][0]
    items=[('','A','2'),('','B','3')] if kind=='pecas' else [('','A',''),('','B','')]
    entry(engine,actor,[row(kind,None if kind=='paletes' else 'BOX',content_total=5 if kind=='pecas' else None,items=items)])
    uid=units(engine)[0].id;ident=prepare(engine,actor,[(kind,'A','1')],extra=True)
    with pytest.raises(v.ReceivingError):send(engine,actor,ident,[(uid,'A','1')],extra_ack=True)
    assert physical(engine)==1
    with pytest.raises(v.ReceivingError):send(engine,actor,ident)
    send(engine,actor,ident,extra_ack=True)
    assert physical(engine)==0
    with scoped(engine,2) as db:
        summary=s.summary(db,db.get(Prep,ident));assert summary['physical']==1
        assert any(x['lot']=='B' for x in summary['extras'])
        assert db.scalar(select(ValmetUnit.physical_quantity))==1

@pytest.mark.parametrize('separation',['yes','no','unknown'])
def test_shipping_rolls_remainders_and_indivisibility(valmet,separation):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row(items=[('','A','2'),('','B','3')])]);uid=units(engine)[0].id
    if separation!='unknown':
        with scoped(engine,2) as db:p.configure(db,actor,uid,separation,note='Fictício comprovado')
    ident=prepare(engine,actor,[('rolos','A','2')],extra=separation!='yes')
    if separation=='yes':
        send(engine,actor,ident,[(uid,'A','1')]);assert physical(engine)==1
        with scoped(engine,2) as db:
            assert p.source(db)[0]['materials']=={'A':1,'B':3}
            assert p.source(db)[0]['available']=={'A':0,'B':3}
        send(engine,actor,ident);assert physical(engine)==1
        next_id=prepare(engine,actor,[('rolos','B','3')]);send(engine,actor,next_id);assert physical(engine)==0
    else:
        with pytest.raises(v.ReceivingError):send(engine,actor,ident,[(uid,'A','1')],extra_ack=True)
        send(engine,actor,ident,extra_ack=True);assert physical(engine)==0

@pytest.mark.parametrize('controlled',[True,False])
def test_shipping_lab_partial_keeps_pallet_and_unknown_saldo_block(valmet,controlled):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('lab','LAB',content_total=10 if controlled else None,items=[('','A','10' if controlled else '')])])
    uid=units(engine)[0].id;ident=prepare(engine,actor,[('lab','A','3')])
    if not controlled:
        with pytest.raises(v.ReceivingError):send(engine,actor,ident,[(uid,'A','1')])
    send(engine,actor,ident,[(uid,'A','1')],unknown_ack=not controlled)
    assert physical(engine)==1
    with scoped(engine,2) as db:
        pos=db.scalar(select(ValmetPosition))
        if controlled:
            assert json.loads(pos.materials)=={'A':9} and not pos.uncertain
            assert p.source(db)[0]['available']=={'A':7}
        else:
            assert json.loads(pos.materials)=={'A':None} and pos.uncertain and not p.source(db)
            assert not p.active(db)  # release obsolete reservation; retain safety block

def test_shipping_vasilhame_all_brands_no_pdf_or_destination(valmet):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('vasilhame',None,brand=brand,local='Chão',physical_quantity=2,content_total=None,items=[]) for brand in v.BRANDS])
    ident=prepare(engine,actor,[('vasilhame',brand,'1') for brand in v.BRANDS],destination='')
    with scoped(engine,2) as db,pytest.raises(v.ReceivingError):s.issue_document(db,actor,ident,1)
    send(engine,actor,ident)
    assert physical(engine)==8
    with scoped(engine,2) as db:
        assert db.scalar(select(func.count()).select_from(Document))==0
        assert all(list(u['available'].values())==[1] for u in p.source(db))

def test_shipping_piece_optimization_excess_actuals_and_close_partial(valmet):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('pecas',f'BOX-{q}',content_total=q,items=[('','A',str(q))]) for q in (10,25,40)])
    ident=prepare(engine,actor,[('pecas','A','30')],extra=True)
    with scoped(engine,2) as db:
        chosen=json.loads(db.get(Rev,db.get(Prep,ident).active_revision).snapshot)['selections']
        assert sorted(s['materials']['A'] for s in chosen)==[10,25]
    box10=units(engine)[0].id
    load=send(engine,actor,ident,[(box10,'A','10')],close=True)
    assert physical(engine)==2
    with scoped(engine,2) as db:
        actual=json.loads(db.get(Shipment,load).snapshot)['actual']
        assert actual['shortages']==[dict(kind='pecas',lot='A',quantity=20)] and not actual['extras']
        assert not p.active(db) and db.get(Prep,ident).status=='shipped'

def test_shipping_pdf_is_optional_a4_immutable_and_current_revision(valmet):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('caixas','BOX',content_total=None,items=[('','A',''),('','B','')])])
    ident=prepare(engine,actor,[('caixas','A','1')],extra=True)
    with scoped(engine,2) as db:
        docid=s.issue_document(db,actor,ident,1);assert s.issue_document(db,actor,ident,1)==docid
        old=bytes(db.get(Document,docid).pdf);pdf=PdfReader(BytesIO(old));text_pdf=pdf.pages[0].extract_text()
        assert abs(float(pdf.pages[0].mediabox.width)-595.2756)<1
        assert 'Valmet' in text_pdf and 'N.º Armazém' in text_pdf and 'A: 1' in text_pdf and 'B: 1' in text_pdf
        assert physical(engine)==1
        p.save(db,actor,db.get(Prep,ident).request_key,'ALTERADA','2027-02-01','DESTINO',[('caixas','A','1')],ident,1)
        with pytest.raises(v.ReceivingError):s.issue_document(db,actor,ident,2)
    propose(engine,actor,ident,2);confirm(engine,actor,ident,2,accept_extra=True)
    with scoped(engine,2) as db:
        assert bytes(db.get(Document,docid).pdf)==old
        assert db.scalar(select(func.count()).select_from(Document))==1
        new=s.issue_document(db,actor,ident,2);assert new!=docid
        for table in ('valmet_documents','valmet_shipments','valmet_shipment_lines','valmet_relocations','valmet_occurrences'):
            with pytest.raises(DBAPIError):
                with db.begin_nested():db.execute(text('DELETE FROM '+table))

def test_shipping_missing_block_substitution_and_admin_resolution(valmet):
    engine,a=valmet;actor=a['operador'][0]
    entry(engine,actor,[row(number='ONE'),row(number='TWO')]);uid=units(engine)[0].id
    ident=prepare(engine,actor,[('rolos','LOT','5')])
    with scoped(engine,2) as db:
        s.report(db,actor,ident,1,uid,'missing','Não encontrada — fictício',lot='LOT',amount=5)
        assert physical(engine)==2
        with pytest.raises(v.ReceivingError):s.ready(db,actor,ident,1)
        occurrence=db.scalar(select(Occurrence.id).where(Occurrence.kind=='missing'))
        with pytest.raises(v.ReceivingError):s.resolve(db,actor,occurrence,'Sem permissão')
        assert all(u['origin_id']!=uid for u in p.source(db))
        p.save(db,actor,db.get(Prep,ident).request_key,'REF','2027-02-01','D',[('rolos','LOT','5')],ident,1)
    propose(engine,actor,ident,2);confirm(engine,actor,ident,2)
    send(engine,actor,ident);assert physical(engine)==1
    with scoped(engine,2) as db:
        assert not p.source(db)
        s.resolve(db,a['admin'][0],occurrence,'Unidade encontrada e conferida fisicamente na origem fictícia')
        assert p.source(db)[0]['origin_id']==uid

def test_shipping_location_proof_splits_groups_and_preserves_original_entry(valmet):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('fio',None,physical_quantity=5,content_total=None,items=[('','A','5')])]);uid=units(engine)[0].id
    ident=prepare(engine,actor,[('fio','A','2')])
    with scoped(engine,2) as db:
        s.ready(db,actor,ident,1)
        s.relocate(db,actor,ident,1,uid,'H2','2','Duas paletes constatadas / devolvidas a H2','A')
        record=db.get(Prep,ident);assert record.status=='reserved' and record.version==2
        assert db.get(ValmetUnit,uid).local=='A2' and db.get(ValmetUnit,uid).physical_quantity==5
        counts={u.local:u.physical_quantity for u,i,items,avail in v.stock(db)[0]}
        assert counts=={'A2':3,'H2':2}
    send(engine,actor,ident);assert physical(engine)==3
    with scoped(engine,2) as db:assert v.stock(db)[0][0][0].local=='A2'

def test_shipping_location_capacity_failure_rolls_back_and_direct_observation(valmet):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row()]);uid=units(engine)[0].id;ident=prepare(engine,actor,[('rolos','LOT','5')])
    with scoped(engine,2) as db:
        from iberbag.warehouse_settings import set_capacity
        set_capacity(db,'admin','H2','1')
    entry(engine,actor,[row(number='OCCUPIED',local='H2')])
    with scoped(engine,2) as db:
        before=p.active(db)[0].id
        with pytest.raises(v.ReceivingError):s.relocate(db,actor,ident,1,uid,'H2',1,'Prova fictícia')
        assert p.active(db)[0].id==before and db.get(Prep,ident).version==1
        assert db.scalar(select(func.count()).select_from(Relocation))==0
        s.report(db,actor,ident,1,uid,'location','Encontrada em H2 e carregada diretamente',lot='LOT',amount=1,observed='H2')
        oid=db.scalar(select(Occurrence.id));s.resolve(db,actor,oid,'Conferido; segue diretamente sem armazenar em H2')
    load=send(engine,actor,ident)
    with scoped(engine,2) as db:
        assert json.loads(db.get(Shipment,load).snapshot)['selections'][0]['observed_local']=='H2'
        assert db.scalar(select(func.count()).select_from(Relocation))==0

@pytest.mark.parametrize('same_key',[True,False])
def test_shipping_concurrency_and_runtime_projection_protection(restricted_web,same_key):
    app,owner,runtime,role,a,tokens=restricted_web;actor=a['admin'][0]
    from iberbag.warehouse_settings import add_location
    with scoped(owner,2) as db:add_location(db,'admin','Chão')
    entry(runtime,actor,[row('paletes',None,local='Chão',content_total=None,items=[('','A','1')])])
    ident=prepare(runtime,actor,[('paletes','A','1')])
    with scoped(runtime,2) as db:s.ready(db,actor,ident,1)
    key=secrets.token_hex(16)
    def run(_):
        try:
            with scoped(runtime,2) as db:return s.ship(db,actor,ident,1,key if same_key else secrets.token_hex(16),'2027-03-01',['GT FICT'])
        except v.ReceivingError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,range(2)))
    if same_key:assert len(set(results))==1 and results[0] is not None
    else:assert sum(x is not None for x in results)==1
    assert physical(runtime)==0
    with runtime.begin() as c,pytest.raises(DBAPIError):c.execute(text('UPDATE valmet_positions SET physical_quantity=5'))
    # Reclaimed capacity is real, despite the immutable original unit remaining.
    entry(runtime,actor,[row('paletes',None,local='Chão',content_total=None,items=[('','A','1')])])
    assert physical(runtime)==1

@pytest.mark.parametrize('writer,when',[('valmet_position_init','AFTER'),('valmet_content_projection','AFTER'),('valmet_apply_line','BEFORE'),('valmet_apply_location','BEFORE')])
def test_shipping_definer_functions_refuse_temp_trigger_spoofing(restricted_web,writer,when):
    app,owner,runtime,role,a,tokens=restricted_web
    # PUBLIC TEMP privilege must not allow laundering a definer writer through
    # a caller-owned trigger. Table identity is checked before any NEW fields.
    with pytest.raises(DBAPIError,match='Invalid projection trigger source'):
        with runtime.begin() as c:
            c.execute(text('CREATE TEMPORARY TABLE projection_probe(v integer)'))
            c.exec_driver_sql(f'CREATE TRIGGER spoof {when} INSERT ON projection_probe FOR EACH ROW EXECUTE FUNCTION {writer}()')
            c.execute(text('INSERT INTO projection_probe(v) VALUES(1)'))
    with runtime.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM pg_proc WHERE proname IN ('valmet_position_init','valmet_content_projection','valmet_apply_line','valmet_apply_location') AND prosecdef AND proconfig IS NOT NULL"))==4

def test_shipping_confirmed_contents_cannot_recreate_debited_stock(restricted_web):
    app,owner,runtime,role,a,tokens=restricted_web;actor=a['admin'][0]
    from iberbag.warehouse_settings import add_location
    from iberbag.database import ValmetItem,ValmetIdentification
    with scoped(owner,2) as db:add_location(db,'admin','Chão')
    entry(runtime,actor,[row(local='Chão')]);ident=prepare(runtime,actor,[('rolos','LOT','5')]);send(runtime,actor,ident)
    with scoped(runtime,2) as db:
        unit=db.scalar(select(ValmetUnit));known=db.scalar(select(ValmetIdentification))
        db.add(ValmetItem(unit_id=unit.id,identification_id=known.id,article=None,lot='LOT',quantity=5))
        with pytest.raises(DBAPIError,match='Confirmed contents cannot be appended or reset'):db.commit()
        db.rollback()
    assert physical(runtime)==0
    from iberbag.privileges import validate_runtime
    with owner.begin() as c:c.execute(text('GRANT UPDATE ON valmet_positions TO PUBLIC'))
    try:
        with runtime.connect() as c,pytest.raises(ValueError):validate_runtime(c)
    finally:
        with owner.begin() as c:c.execute(text('REVOKE UPDATE ON valmet_positions FROM PUBLIC'))

def test_shipping_races_new_reservation_without_resurrecting_original_quantity(restricted_web):
    app,owner,runtime,role,a,tokens=restricted_web;actor=a['admin'][0]
    from iberbag.warehouse_settings import add_location
    with scoped(owner,2) as db:add_location(db,'admin','Chão')
    entry(runtime,actor,[row('fio',None,local='Chão',physical_quantity=3,content_total=None,items=[('','A','3')])])
    first=prepare(runtime,actor,[('fio','A','2')]);second=draft(runtime,a['operador'][0],[('fio','A','2')]);propose(runtime,a['operador'][0],second)
    with scoped(runtime,2) as db:s.ready(db,actor,first,1)
    def operation(which):
        if which=='ship':
            with scoped(runtime,2) as db:s.ship(db,actor,first,1,secrets.token_hex(16),'2027-03-01',['GT FICT'])
        else:
            try:confirm(runtime,a['operador'][0],second)
            except v.ReceivingError:pass
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(operation,['ship','reserve']))
    assert physical(runtime)==1
    with scoped(runtime,2) as db:
        assert sum(h.physical for h in p.active(db))<=1
        assert db.scalar(select(ValmetUnit.physical_quantity))==3

def test_shipping_lab_pallet_leaves_only_by_explicit_choice_and_number_never_reused(valmet):
    engine,a=valmet;actor=a['admin'][0]
    data=row('lab','LAB',content_total=3,items=[('','A','3')]);entry(engine,actor,[data])
    uid=units(engine)[0].id;ident=prepare(engine,actor,[('lab','A','3')])
    send(engine,actor,ident,lab_pallets=[uid]);assert physical(engine)==0
    with pytest.raises(v.ReceivingError):entry(engine,actor,[data])

def test_shipping_unknown_lab_return_location_preserves_block_and_idempotence(valmet):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('lab','LAB',content_total=None,items=[('','A','')])]);uid=units(engine)[0].id
    ident=prepare(engine,actor,[('lab','A','3')]);send(engine,actor,ident,[(uid,'A','1')],unknown_ack=True)
    with scoped(engine,2) as db:
        version=db.get(Prep,ident).version
        moved=s.relocate(db,actor,ident,version,uid,'H2',1,'Palete remanescente devolvida e constatada')
        assert s.relocate(db,actor,ident,version,uid,'H2',1,'Palete remanescente devolvida e constatada')==moved
        remaining=db.scalar(select(ValmetPosition).where(ValmetPosition.physical_quantity>0))
        assert remaining.local=='H2' and remaining.uncertain and json.loads(remaining.materials)=={'A':None}
        assert physical(engine)==1 and not p.source(db)
        assert len(s.shipments(db,ident))==1 and db.get(Prep,ident).status=='shipped'

@pytest.mark.parametrize('name',['ready','report','resolve','relocate','ship','issue_document'])
def test_shipping_sqlite_refuses_every_operational_write(tmp_path,name):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    cases=dict(ready=(1,1),report=(1,1,1,'missing','Fictício'),resolve=(1,'Fictício'),
        relocate=(1,1,1,'H2',1,'Prova fictícia'),ship=(1,1,secrets.token_hex(16),'2027-03-01',['GT FICT']),issue_document=(1,1))
    engine=create_engine('sqlite:///'+str(tmp_path/'limited.sqlite'))
    with Session(engine) as db:
        with pytest.raises(v.ReceivingError,match='PostgreSQL') as error:getattr(s,name)(db,1,*cases[name])
        assert error.value.status==503
    engine.dispose()
    assert not list(tmp_path.iterdir())  # refusal happens before even opening SQLite

def test_shipping_invalid_refs_over_quantity_and_rollback(valmet,monkeypatch):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('paletes',None,physical_quantity=3,content_total=None,items=[('','A','3')])]);uid=units(engine)[0].id
    ident=prepare(engine,actor,[('paletes','A','2')])
    with pytest.raises(v.ReceivingError):send(engine,actor,ident,[(uid,'A','3')])
    with scoped(engine,2) as db:
        with pytest.raises(v.ReceivingError):s.ship(db,actor,ident,1,secrets.token_hex(16),'2027-03-01',[])
    with scoped(engine,2) as db:
        from datetime import datetime
        record=db.get(Prep,ident);hold=p.active(db)[0];pos=db.scalar(select(ValmetPosition))
        fake=Shipment(preparation_id=ident,revision_id=record.active_revision,request_key=secrets.token_hex(16),payload='{}',loaded_on=datetime(2027,3,1),references='["GT FICT"]',snapshot='{}',actor='admin')
        db.add(fake);db.flush()
        db.add(Line(shipment_id=fake.id,reservation_id=hold.id,position_id=pos.id,physical_quantity=100,materials='{"A":100}',unknown_ack=False))
        with pytest.raises(DBAPIError):db.flush()
        db.rollback()
    assert physical(engine)==3
    original=p.revision
    def fail(*args,**kw):
        if args[3]=='shipment':raise RuntimeError('Erro fictício depois do desconto')
        return original(*args,**kw)
    monkeypatch.setattr(p,'revision',fail)
    with pytest.raises(RuntimeError):send(engine,actor,ident)
    assert physical(engine)==3
    with scoped(engine,2) as db:
        assert db.get(Prep,ident).status=='ready' and sum(h.physical for h in p.active(db))==2
        assert db.scalar(select(func.count()).select_from(Shipment))==0

def test_shipping_backup_restores_ledger_positions_documents_and_history(valmet,pg_databases,tmp_path):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('paletes',None,physical_quantity=4,content_total=None,items=[('','A','4')])]);uid=units(engine)[0].id
    ident=prepare(engine,actor,[('paletes','A','3')])
    with scoped(engine,2) as db:s.issue_document(db,actor,ident,1)
    send(engine,actor,ident,[(uid,'A','1')])
    with engine.connect() as c:expected=transfer.inventory(transfer.read_data(c))
    folder=tmp_path/'shipping';target=pg_databases(restricted=True)
    backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert backup.restore(target,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'
    with target.connect() as c:assert transfer.inventory(transfer.read_data(c))==expected
    send(target,actor,ident)
    assert physical(target)==1

def test_shipping_pdf_and_reference_rules_on_windows_sqlite_without_writes():
    from datetime import datetime
    from iberbag.valmet_pdf import render_pdf
    plan=dict(payload=dict(reference='FICTÍCIO',destination='DESTINO'),selections=[dict(unit=dict(kind='caixas',number='TEST',local='A2'),materials={'A':1,'B':1},physical=1)])
    pdf=PdfReader(BytesIO(render_pdf(1,1,plan,datetime(2027,1,2))))
    assert pdf.pages[0].extract_text().splitlines()[:2]==['Valmet','02/01/2027']
    plan['selections'][0]['materials']={f'LOTE-FICTICIO-{i:03d}-'+('X'*60):1 for i in range(100)}
    many=PdfReader(BytesIO(render_pdf(1,1,plan,datetime(2027,1,2))))
    assert len(many.pages)>1
    assert all(page.extract_text().splitlines()[:2]==['Valmet','02/01/2027'] for page in many.pages)
    assert s.references(['GT FICT','CMR FICT'])==['GT FICT','CMR FICT']
    for refs in ([],['FICT'],['GT FICT','GT FICT']):
        with pytest.raises(v.ReceivingError):s.references(refs)

def test_shipping_http_permissions_csrf_context_documents_and_history(restricted_web):
    app,owner,runtime,role,a,tokens=restricted_web;actor=a['operador'][0]
    from iberbag.warehouse_settings import add_location
    with scoped(owner,2) as db:add_location(db,'admin','Chão')
    entry(runtime,actor,[row(number='HTTP-ONE',local='Chão')]);ident=prepare(runtime,actor,[('rolos','LOT','5')])
    with TestClient(app) as client:
        path=f'/valmet/preparations/{ident}'
        assert client.get('/valmet/shipments').status_code==403
        client.cookies.set('wms_session',tokens['operador'][0]);client.cookies.set('ddn_context','2')
        form=dict(csrf=tokens['operador'][1],context_id=2,version=1)
        assert client.get(path).status_code==200
        assert client.post(path+'/ready',data=dict(form,csrf='invalid')).status_code==403
        issued=client.post(path+'/document',data=form,follow_redirects=False);assert issued.status_code==303
        pdf_path=issued.headers['location'];original=client.get(pdf_path).content
        assert original.startswith(b'%PDF')
        assert client.post(path+'/ready',data=form,follow_redirects=False).status_code==303
        html=client.get(path);assert html.status_code==200 and 'Confirmar expedição' in html.text
        ship_form=dict(form,key=secrets.token_hex(16),day='2027-03-01',references='GT HTTP\nCMR HTTP')
        assert client.post(path+'/ship',data=dict(ship_form,context_id=1)).status_code==409
        result=client.post(path+'/ship',data=ship_form,follow_redirects=False);assert result.status_code==303
        assert 'Resumo efetivo arquivado' in client.get(result.headers['location']).text
        assert client.post(path+'/ship',data=ship_form,follow_redirects=False).headers['location']==result.headers['location']
        assert client.get(pdf_path).content==original
        assert client.get('/valmet/shipments?lot=LOT&number=HTTP-ONE&document=GT+HTTP&date=2027-03-01').status_code==200
        assert 'Carregamento' in client.get('/valmet/shipments?lot=LOT').text
        assert client.get(path).status_code==200
        client.cookies.set('ddn_context','1')
        assert client.get(pdf_path).status_code==403 and client.get(result.headers['location']).status_code==403
    assert physical(owner)==0

@pytest.mark.parametrize('kind',['not_loaded','quantity','substitution'])
def test_shipping_edit_after_collection_no_mandatory_pdf_and_real_shortage(valmet,kind):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('fio',None,physical_quantity=2,content_total=None,items=[('','A','2')]),
        row('fio',None,local='H2',physical_quantity=2,content_total=None,items=[('','A','2')])])
    first,second=[u.id for u in units(engine)];ident=prepare(engine,actor,[('fio','A','3')])
    with scoped(engine,2) as db:
        pdfid=s.issue_document(db,actor,ident,1);original=bytes(db.get(Document,pdfid).pdf)
        s.ready(db,actor,ident,1)
        report=s.report(db,actor,ident,1,first,kind,'Exceção fictícia','A',1)
        assert s.report(db,actor,ident,1,first,kind,'Exceção fictícia','A',1)==report
        assert physical(engine)==4
        with pytest.raises(v.ReceivingError):s.ready(db,actor,ident,1)
        p.save(db,actor,db.get(Prep,ident).request_key,'ALTERADA','2027-03-01','DESTINO',[('fio','A','2')],ident,1)
        with pytest.raises(v.ReceivingError):s.ready(db,actor,ident,2)
    propose(engine,actor,ident,2,manual=[(second,'A','2')]);confirm(engine,actor,ident,2)
    with scoped(engine,2) as db:assert bytes(db.get(Document,pdfid).pdf)==original and db.scalar(select(func.count()).select_from(Document))==1
    load=send(engine,actor,ident)
    with scoped(engine,2) as db:
        actual=json.loads(db.get(Shipment,load).snapshot)['actual']
        assert actual['shortages']==[dict(kind='fio',lot='A',quantity=1)]
        assert actual['physical']==2
    assert physical(engine)==2

def test_shipping_same_owner_other_warehouse_cannot_read_documents_or_debit(restricted_web):
    app,owner,runtime,role,a,tokens=restricted_web;actor=a['admin'][0]
    from iberbag.warehouse_settings import add_location
    from iberbag.database import StorageContext
    with scoped(owner,2) as db:add_location(db,'admin','Chão')
    entry(runtime,actor,[row(local='Chão')]);ident=prepare(runtime,actor,[('rolos','LOT','5')])
    with scoped(runtime,2) as db:doc=s.issue_document(db,actor,ident,1)
    load=send(runtime,actor,ident)
    from sqlalchemy.orm import Session
    with Session(owner) as db:
        other=StorageContext(client_id=2,warehouse_id=2,area='VALMET FICTÍCIO',active=True);db.add(other);db.commit();context_id=other.id
    with scoped(runtime,context_id) as db:
        assert s.shipments(db,ident)==[] and db.get(Shipment,load) is None and db.get(Document,doc) is None
        with pytest.raises(v.ReceivingError):s.ship(db,actor,ident,1,secrets.token_hex(16),'2027-03-01',['GT FICT'])
    with TestClient(app) as client:
        client.cookies.set('wms_session',tokens['admin'][0]);client.cookies.set('ddn_context',str(context_id))
        assert client.get(f'/valmet/documents/{doc}.pdf').status_code==404
        assert client.get(f'/valmet/shipments/{load}').status_code==404

def test_shipping_migrate_ddn7_preserves_old_legacy_box_reservations_but_blocks_loading(pg_databases,tmp_path):
    from alembic import command
    from iberbag.migrations import config,REVISION
    from iberbag.schema_upgrade import apply_upgrade
    from iberbag.security import set_password
    from iberbag.database import User,ValmetIdentification,ValmetItem
    from legacy_catalogue import seed_legacy_catalogue
    from iberbag.warehouse_settings import add_location
    engine=pg_databases(restricted=True);target=pg_databases(restricted=True)
    with engine.begin() as c:
        cfg=config();cfg.attributes['connection']=c;command.upgrade(cfg,'ddn_0007')
    with scoped(engine) as db:
        seed_legacy_catalogue(db);user=User(username='admin',display_name='Fictício',role='admin',active=True)
        set_password(user,secrets.token_urlsafe(24));db.add(user);db.commit();actor=user.id
    with scoped(engine,2) as db:add_location(db,'admin','Chão')
    e=entry(engine,actor,[row('vazias','UNUSED',content_total=None,items=[('','LEGACY','')])],confirm=False)
    with scoped(engine,2) as db:
        u=ValmetUnit(entry_id=e,kind='vazias',legacy_unnumbered=True,physical_quantity=18,local='Chão',supplier='',guide='',notes='Histórico fictício')
        db.add(u);db.flush();uid=u.id
        known=ValmetIdentification(unit_id=uid,request_key=secrets.token_hex(16),actor='admin',notes='Prova antiga');db.add(known);db.flush()
        db.add(ValmetItem(unit_id=uid,identification_id=known.id,lot='LEGACY'));db.commit()
        p.configure(db,actor,uid,'unknown',reconciled=True,note='Contagem histórica comprovada')
        ident=p.save(db,actor,secrets.token_hex(16),'LEGACY','2027-01-01','D',[('vazias','LEGACY','5')])
        record=db.get(Prep,ident);actor_obj=db.get(User,actor)
        snap=dict(payload=json.loads(record.payload),selections=[dict(unit=dict(id=uid,kind='vazias',number=None,local='Chão'),physical=5,materials={'LEGACY':5},exclusive=False)])
        rev=p.revision(db,record,actor_obj,'reserve',snap);p.reserve_rows(db,rev,snap['selections']);record.active_revision=rev.id;record.status='reserved';db.commit()
        old_hold=db.scalar(select(Hold.id))
    with engine.connect() as c:before=transfer.inventory(transfer.read_data(c))
    folder=tmp_path/'ddn7';backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    backup.restore(target,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert apply_upgrade(engine,folder)['revision']==REVISION
    with engine.connect() as c:assert transfer.inventory(transfer.read_data(c))==before
    with scoped(engine,2) as db:
        assert db.get(Hold,old_hold).physical==5 and db.get(ValmetUnit,uid).physical_quantity==18
        with pytest.raises(v.ReceivingError):s.ready(db,actor,ident,1)
        with pytest.raises(v.ReceivingError):s.ship(db,actor,ident,1,secrets.token_hex(16),'2027-03-01',['GT FICT'])
        assert db.scalar(select(func.count()).select_from(Shipment))==0
        p.cancel(db,actor,ident,1,'Não expedir caixas sem N.º Armazém')
        assert not p.active(db) and not p.source(db)
