"""Planning uses fictitious receipts; physical source rows never move or change."""
import json
import secrets
import re
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select,func,text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from fastapi.testclient import TestClient
from alembic import command
from iberbag import valmet as v,valmet_planning as p,transfer,backup
from iberbag.database import (ValmetUnit,ValmetPreparation as Prep,ValmetReservation as Hold,
    ValmetPlanRevision as Rev,ValmetIdentification,ValmetHandling,ValmetItem,Audit,Stock,StorageContext,User,CustomerPending)
from test_valmet import valmet,row,entry
from test_receiving import warehouse
from test_transfer import pg_databases,docker_client
from test_ddn_contexts import scoped
from test_security_regressions import restricted_web
from conftest import BACKUP_PASSWORD

def draft(engine,actor,lines,scope=2,destination='DESTINO FICTÍCIO'):
    with scoped(engine,scope) as db:return p.save(db,actor,secrets.token_hex(16),'ENCOMENDA FICTÍCIA','2027-02-01',destination,lines)

def propose(engine,actor,ident,version=1,manual=None):
    with scoped(engine,2) as db:return p.review(db,actor,ident,version,manual)

def confirm(engine,actor,ident,version=1,**kw):
    with scoped(engine,2) as db:return p.confirm(db,actor,ident,version,**kw)

def units(engine):
    with scoped(engine,2) as db:return db.scalars(select(ValmetUnit).order_by(ValmetUnit.id)).all()

def test_planning_multilot_physical_shortage_and_cancel(valmet):
    engine,a=valmet;actor=a['operador'][0]
    entry(engine,actor,[row('paletes',None,content_total=None,items=[('','A','1'),('','B','1')])])
    ident=draft(engine,actor,[('paletes','A','1'),('paletes','B','1'),('paletes','MISSING','6')])
    with scoped(engine,2) as db:assert not p.active(db)
    plan=propose(engine,actor,ident)
    assert plan['physical']==1 and not plan['extras']
    assert plan['shortages']==[dict(kind='paletes',lot='MISSING',quantity=6)]
    confirm(engine,actor,ident);confirm(engine,actor,ident)
    with scoped(engine,2) as db:
        assert len(p.active(db))==1 and v.stock(db)[0][0][3]==0
        assert db.scalar(select(func.sum(ValmetUnit.physical_quantity)))==1
        assert db.scalar(select(func.count()).select_from(Stock))==0
        assert db.scalar(select(func.count()).select_from(CustomerPending))==0
        assert db.scalar(select(func.count()).select_from(Hold))==1
        p.cancel(db,actor,ident,1,'Cancelamento fictício')
        p.cancel(db,actor,ident,1,'Repetição')
        assert len(p.active(db))==0 and v.stock(db)[0][0][3]==1
        assert db.scalar(select(func.count()).select_from(Hold))==1
        assert db.scalar(select(func.count()).select_from(Rev).where(Rev.action=='cancel'))==1

@pytest.mark.parametrize('kind',['paletes','fio','prateleiras'])
def test_planning_grouped_partial_and_shared_location(valmet,kind):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row(kind,None,physical_quantity=5,content_total=None,items=[('','A','3'),('','B','2')])])
    ident=draft(engine,actor,[(kind,'A','2'),(kind,'B','1')]);plan=propose(engine,actor,ident)
    assert plan['physical']==3 and not plan['shortages'];confirm(engine,actor,ident)
    with scoped(engine,2) as db:
        assert v.stock(db)[0][0][3]==2
        source=p.source(db)[0];assert source['available']=={'A':1,'B':1}
        assert db.scalar(select(func.sum(ValmetUnit.physical_quantity)))==5

@pytest.mark.parametrize('separation',['unknown','no','yes'])
def test_planning_roll_separability_and_extras(valmet,separation):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row(items=[('','A','2'),('','B','3')])]);uid=units(engine)[0].id
    if separation!='unknown':
        with scoped(engine,2) as db:p.configure(db,actor,uid,separation,note='Regra comprovada fictícia')
    ident=draft(engine,actor,[('rolos','A','1')]);plan=propose(engine,actor,ident)
    if separation=='yes':
        assert not plan['extras'];confirm(engine,actor,ident)
        with scoped(engine,2) as db:assert p.source(db)[0]['available']=={'A':1,'B':3}
        second=draft(engine,actor,[('rolos','B','2')]);propose(engine,actor,second);confirm(engine,actor,second)
        with scoped(engine,2) as db:
            assert p.reserved_physical(db,db.get(ValmetUnit,uid))==1
            assert p.source(db)[0]['available']=={'A':1,'B':1}
    else:
        assert plan['selections'][0]['materials']=={'A':2,'B':3}
        with pytest.raises(v.ReceivingError):confirm(engine,actor,ident)
        confirm(engine,actor,ident,accept_extra=True)
        other=draft(engine,actor,[('rolos','B','2')]);assert propose(engine,actor,other)['shortages'][0]['quantity']==2
    with scoped(engine,2) as db:
        with pytest.raises(v.ReceivingError):p.configure(db,actor,uid,'yes',note='Bloqueada por reserva')

@pytest.mark.parametrize('kind',['caixas','vazias'])
def test_planning_whole_boxes_extra_lots_and_article_null(valmet,kind):
    engine,a=valmet;actor=a['operador'][0]
    entry(engine,actor,[row(kind,'BOX',content_total=None,items=[('','A',''),('','B','')])])
    ident=draft(engine,actor,[(kind,'A','1')]);plan=propose(engine,actor,ident)
    assert plan['physical']==1 and plan['extras']==[dict(kind=kind,lot='B',quantity=1)]
    assert plan['extra_sources'][0]['number']=='BOX'
    with pytest.raises(v.ReceivingError):confirm(engine,actor,ident)
    confirm(engine,actor,ident,accept_extra=True)
    with scoped(engine,2) as db:assert v.stock(db,{'lot':'B','number':'BOX'})[0][0][3]==0

@pytest.mark.parametrize('amount,expected,extra',[(30,[10,25],5),(25,[25],0),(40,[40],0),(80,[10,25,40],0)])
def test_planning_piece_box_optimization(valmet,amount,expected,extra):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('pecas',f'BOX-{q}',content_total=q,items=[('','A',str(q))]) for q in (10,25,40)])
    ident=draft(engine,actor,[('pecas','A',str(amount))]);plan=propose(engine,actor,ident)
    assert sorted(s['materials']['A'] for s in plan['selections'])==expected
    assert sum(l['quantity'] for l in plan['extras'])==extra
    assert sum(l['quantity'] for l in plan['shortages'])==max(0,amount-75)
    confirm(engine,actor,ident,accept_extra=bool(extra))

def test_piece_box_ties_and_limits_without_database():
    candidates=[dict(id=i,available={'A':q}) for i,q in enumerate((10,15,25,25),1)]
    assert p.boxes(candidates,'A',25)==(3,)
    with pytest.raises(v.ReceivingError):p.boxes(candidates*33,'A',25)
    assert p.payload('REF','2027-01-01','',[('vasilhame','Filva','1')])['destination']==''
    with pytest.raises(v.ReceivingError):p.payload('REF','2027-01-01','D',[('vasilhame','Filva','1'),('rolos','LOT','1')])

@pytest.mark.parametrize('q',['0','-1','1.5','','2147483648','1e2'])
def test_planning_invalid_quantities_without_database(q):
    with pytest.raises(v.ReceivingError):p.payload('REF','2027-01-01','D',[('rolos','LOT',q)])

def test_planning_two_warehouses_never_share_reservations(valmet):
    engine,a=valmet;actor=a['admin'][0]
    from iberbag.warehouse_settings import add_location
    with Session(engine) as db:
        c=StorageContext(client_id=2,warehouse_id=2,area='VALMET FICTÍCIO',active=True)
        db.add(c);db.commit();other=c.id
    with scoped(engine,other) as db:add_location(db,'admin','A2')
    entry(engine,actor,[row('paletes',None,content_total=None,items=[('','LOT','1')])])
    entry(engine,actor,[row('paletes',None,content_total=None,items=[('','LOT','1')])],scope=other)
    ident=draft(engine,actor,[('paletes','LOT','1')]);propose(engine,actor,ident);confirm(engine,actor,ident)
    with scoped(engine,other) as db:
        assert v.stock(db)[0][0][3]==1 and not p.active(db)
        assert db.get(Prep,ident) is None
        assert p.source(db)[0]['local']=='A2'

def test_planning_duplicate_confirmation_and_partial_race(restricted_web):
    app,owner,runtime,role,a,tokens=restricted_web;actor=a['admin'][0]
    from iberbag.warehouse_settings import add_location
    with scoped(owner,2) as db:add_location(db,'admin','Chão')
    entry(runtime,actor,[row('fio',None,local='Chão',physical_quantity=3,content_total=None,items=[('','A','3')])])
    ids=[draft(runtime,actor,[('fio','A','2')]) for _ in range(2)]
    for ident in ids:propose(runtime,actor,ident)
    def reserve(ident):
        try:confirm(runtime,actor,ident);return True
        except v.ReceivingError:return False
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(reserve,ids))==[False,True]
    with scoped(runtime,2) as db:
        winner=db.scalar(select(Prep.id).where(Prep.status=='reserved'))
        assert sum(h.physical for h in p.active(db))==2
    with ThreadPoolExecutor(max_workers=2) as pool:assert all(pool.map(reserve,[winner,winner]))
    with scoped(runtime,2) as db:assert db.scalar(select(func.count()).select_from(Hold))==1

def test_planning_empty_boxes_and_reconciled_legacy_groups(valmet):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('vazias','BOX',local='H2',content_total=None,items=[('','A','')])])
    # Future import shape, deliberately introduced only as a fictitious fixture;
    # the production UI/importer cannot manufacture unidentified historical IDs.
    ident=entry(engine,actor,[row('vazias','UNUSED',content_total=None,items=[('','A','')])],confirm=False)
    with scoped(engine,2) as db:
        u=ValmetUnit(entry_id=ident,kind='vazias',legacy_unnumbered=True,physical_quantity=18,local='Chão',supplier='',guide='',notes='Histórico fictício')
        db.add(u);db.flush();uid=u.id
        identification=ValmetIdentification(unit_id=uid,request_key=secrets.token_hex(16),actor='admin',notes='Prova histórica');db.add(identification);db.flush()
        db.add(ValmetItem(unit_id=uid,identification_id=identification.id,lot='LEGACY'));db.commit()
        assert all(x['id']!=uid for x in p.source(db))
        p.configure(db,actor,uid,'unknown',reconciled=True,note='Conferência física fictícia 18 caixas, lote comprovado')
    prep=draft(engine,actor,[('vazias','LEGACY','5')]);plan=propose(engine,actor,prep)
    assert plan['physical']==5 and not plan['selections'][0]['unit']['number'];confirm(engine,actor,prep)
    with scoped(engine,2) as db:
        assert p.source(db)[-1]['available']=={'LEGACY':13}
        assert db.get(ValmetUnit,uid).physical_quantity==18
        assert db.scalar(select(ValmetUnit.local).where(ValmetUnit.number=='BOX'))=='Chão'

@pytest.mark.parametrize('controlled',[True,False])
def test_planning_lab_partial_without_physical_debit(valmet,controlled):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('lab','LAB',content_total=10 if controlled else None,items=[('','A','10' if controlled else '')])])
    prep=draft(engine,actor,[('lab','A','3')]);plan=propose(engine,actor,prep)
    assert plan['physical']==1 and plan['uncontrolled']==(not controlled)
    if not controlled:
        with pytest.raises(v.ReceivingError):confirm(engine,actor,prep)
    confirm(engine,actor,prep,accept_uncontrolled=not controlled)
    with scoped(engine,2) as db:
        assert db.scalar(select(ValmetUnit.physical_quantity))==1
        if controlled:assert p.source(db)[0]['available']=={'A':7}
        else:assert not p.source(db)  # no invented remainder; exclusive hold

def test_planning_vasilhame_unlocated_requires_two_validations(valmet):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('vasilhame',None,physical_quantity=4,content_total=None,brand='Filva',local='',items=[])])
    uid=units(engine)[0].id;prep=draft(engine,actor,[('vasilhame','Filva','3')],destination='')
    assert propose(engine,actor,prep)['shortages'][0]['quantity']==3
    with scoped(engine,2) as db:p.configure(db,actor,uid,'unknown',unlocated=True,note='Histórico comprovado e seleção segura fictícia')
    plan=propose(engine,actor,prep);assert plan['unlocated']
    with pytest.raises(v.ReceivingError):confirm(engine,actor,prep)
    confirm(engine,actor,prep,accept_unlocated=True)
    with scoped(engine,2) as db:
        assert p.source(db)[0]['available']=={'Filva':1}
        assert db.scalar(select(ValmetUnit.local)) is None

def test_planning_edit_reservations_and_rollback(valmet,monkeypatch):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('fio',None,physical_quantity=5,content_total=None,items=[('','A','5')])])
    prep=draft(engine,actor,[('fio','A','2')]);propose(engine,actor,prep);confirm(engine,actor,prep)
    with scoped(engine,2) as db:
        r=db.get(Prep,prep);key=r.request_key
        p.save(db,actor,key,'REF','2027-02-01','D',[('fio','A','4')],prep,1)
        assert sum(h.physical for h in p.active(db))==2
    propose(engine,actor,prep,2)
    original=p.revision
    def fail(*args,**kw):
        result=original(*args,**kw)
        if args[3]=='reserve':raise RuntimeError('Falha fictícia após escrita')
        return result
    monkeypatch.setattr(p,'revision',fail)
    with pytest.raises(RuntimeError):confirm(engine,actor,prep,2)
    with scoped(engine,2) as db:assert sum(h.physical for h in p.active(db))==2
    monkeypatch.setattr(p,'revision',original);confirm(engine,actor,prep,2)
    with scoped(engine,2) as db:
        assert sum(h.physical for h in p.active(db))==4
        assert db.scalar(select(func.count()).select_from(Hold))==2
        assert v.stock(db)[0][0][3]==1
        with pytest.raises(v.ReceivingError):p.cancel(db,actor,prep,1,'Revisão antiga')
        with pytest.raises(v.ReceivingError):p.review(db,actor,prep,2)

@pytest.mark.parametrize('kind',['caixas','paletes'])
def test_planning_concurrent_different_lots_same_box(restricted_web,kind):
    app,owner,runtime,role,a,tokens=restricted_web;actor=a['admin'][0]
    from iberbag.warehouse_settings import add_location
    with scoped(owner,2) as db:add_location(db,'admin','Chão')
    entry(runtime,actor,[row(kind,'BOX' if kind=='caixas' else None,local='Chão',content_total=None,items=[('','A',''),('','B','')])])
    ids=[draft(runtime,actor,[(kind,lot,'1')]) for lot in ('A','B')]
    for ident in ids:propose(runtime,actor,ident)
    def run(ident):
        try:confirm(runtime,actor,ident,accept_extra=True);return True
        except v.ReceivingError:return False
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(run,ids))==[False,True]
    with scoped(runtime,2) as db:
        assert len(p.active(db))==1 and db.scalar(select(func.sum(ValmetUnit.physical_quantity)))==1

def test_planning_lab_unknown_composition_never_invents_remaining_without_database():
    from types import SimpleNamespace
    unit=SimpleNamespace(kind='lab',physical_quantity=1)
    for quantities in ((None,5),(5,None)):
        items=[SimpleNamespace(lot='A',quantity=q) for q in quantities]
        assert p.materials_for(unit,items)=={'A':None}

def test_planning_permissions_context_and_insufficient_identity(valmet):
    engine,a=valmet;actor=a['operador'][0]
    entry(engine,actor,[row(pending=True,items=[('','','5')])])
    prep=draft(engine,actor,[('rolos','UNKNOWN','5')]);assert not propose(engine,actor,prep)['selections']
    confirm(engine,actor,prep)
    with scoped(engine,2) as db:
        assert not p.active(db)
        with pytest.raises(v.ReceivingError):p.cancel(db,a['other'][0],prep,1,'Sem permissão')
        with pytest.raises(v.ReceivingError):p.configure(db,actor,units(engine)[0].id,'yes',note='Sem permissão')
        db.get(User,actor).active=False;db.commit()
        with pytest.raises(v.ReceivingError):p.cancel(db,actor,prep,1,'Conta desativada')
    for scope in (1,3,4,5):
        with scoped(engine,scope) as db:
            assert db.get(Prep,prep) is None and not p.active(db) and not p.source(db)
            with pytest.raises(v.ReceivingError):p.review(db,a['admin'][0],prep,1)

def test_planning_manual_review_staleness_and_snapshot(valmet):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row(number='N1'),row(number='N2')]);uid=units(engine)[1].id
    with scoped(engine,2) as db:p.configure(db,actor,uid,'yes',note='Comprovado')
    prep=draft(engine,actor,[('rolos','LOT','2')]);propose(engine,actor,prep,manual=[(uid,'LOT','2')])
    with scoped(engine,2) as db:old=p.review_key(db.get(Prep,prep))
    with pytest.raises(v.ReceivingError):propose(engine,actor,prep,manual=[(uid,'OTHER','1')])
    second=draft(engine,actor,[('rolos','LOT','1')]);propose(engine,actor,second,manual=[(uid,'LOT','1')]);confirm(engine,actor,second)
    with pytest.raises(v.ReceivingError):confirm(engine,actor,prep)
    propose(engine,actor,prep,manual=[(uid,'LOT','2')])
    with scoped(engine,2) as db:
        with pytest.raises(v.ReceivingError):p.confirm(db,actor,prep,1,expected_review=old)
    confirm(engine,actor,prep)

def test_planning_http_runtime_csrf_context_and_balances(restricted_web):
    app,owner,runtime,role,a,tokens=restricted_web
    from iberbag.warehouse_settings import add_location
    with scoped(owner,2) as db:add_location(db,'admin','Chão')
    entry(runtime,a['operador'][0],[row('paletes',None,local='Chão',content_total=None,items=[('','LOT','1')])])
    with TestClient(app) as client:
        assert client.get('/valmet/preparations').status_code==403
        client.cookies.set('wms_session',tokens['operador'][0]);client.cookies.set('ddn_context','2')
        assert client.get('/valmet/preparations/new').status_code==200
        data=dict(csrf=tokens['operador'][1],context_id=2,key=secrets.token_hex(16),reference='TEST',day='2027-02-01',destination='D',kind0='paletes',lot0='LOT',q0='1')
        bad=dict(data,csrf='invalid');assert client.post('/valmet/preparations/save',data=bad).status_code==403
        result=client.post('/valmet/preparations/save',data=data,follow_redirects=False);assert result.status_code==303
        path=result.headers['location'];form=dict(csrf=data['csrf'],context_id=2,version=1)
        assert client.get(path).status_code==200
        assert client.post(path+'/review',data=form,follow_redirects=False).status_code==303
        html=client.get(path).text;review_key=re.search('name="review_key" value="([^"]+)"',html)[1]
        assert client.post(path+'/confirm',data=dict(form,review_key=review_key,context_id=1)).status_code==409
        assert client.post(path+'/confirm',data=dict(form,review_key=review_key),follow_redirects=False).status_code==303
        stock=client.get('/valmet/stock?lot=LOT').text
        assert '1 / 1 / 0' in stock and 'Reservado: zero' not in stock
        assert client.get('/valmet/preparations').status_code==200
        assert client.get(path+'/edit').status_code==200
        uid=units(owner)[0].id
        assert client.post(f'/valmet/units/{uid}/handling',data=dict(form,separation='yes',note='X')).status_code==403
        client.cookies.set('ddn_context','1');assert client.get(path).status_code==403

def test_planning_immutable_audit_and_database_overbooking(valmet):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('paletes',None,content_total=None,items=[('','A','1')])])
    prep=draft(engine,actor,[('paletes','A','1')]);propose(engine,actor,prep);confirm(engine,actor,prep)
    for sql in ('DELETE FROM valmet_preparations','TRUNCATE valmet_plan_revisions','UPDATE valmet_reservations SET physical=2','DELETE FROM valmet_handling'):
        with engine.begin() as c,pytest.raises(DBAPIError):c.execute(text(sql))
    # A second, syntactically valid copy of the immutable selection still cannot
    # reserve the same physical stock via direct SQL or a service regression.
    other=draft(engine,actor,[('paletes','A','1')])
    with scoped(engine,2) as db:
        r=db.get(Rev,db.get(Prep,prep).active_revision);snapshot=r.snapshot
        new=Rev(preparation_id=other,version=1,action='reserve',snapshot=snapshot,actor='admin');db.add(new);db.flush()
        selected=json.loads(snapshot)['selections'][0]
        db.add(Hold(revision_id=new.id,unit_id=selected['unit']['id'],physical=1,materials=p.dump(selected['materials']),exclusive=True))
        db.get(Prep,other).active_revision=new.id;db.get(Prep,other).status='reserved'
        with pytest.raises(DBAPIError):db.commit()
        db.rollback();assert len(p.active(db))==1

def test_planning_backup_restore_reconciles_all_revisions(valmet,pg_databases,tmp_path):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row()]);uid=units(engine)[0].id
    with scoped(engine,2) as db:p.configure(db,actor,uid,'yes',note='Prova fictícia')
    prep=draft(engine,actor,[('rolos','LOT','2')]);propose(engine,actor,prep);confirm(engine,actor,prep)
    with engine.connect() as c:expected=transfer.inventory(transfer.read_data(c))
    target=pg_databases(restricted=True);folder=tmp_path/'planning-backup'
    backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert backup.restore(target,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'
    with target.connect() as c:assert transfer.inventory(transfer.read_data(c))==expected
    with scoped(target,2) as db:assert p.source(db)[0]['available']=={'LOT':3} and len(p.active(db))==1

def test_planning_ddn6_migration_preserves_every_legacy_byte(pg_databases,tmp_path):
    from iberbag.migrations import config,REVISION
    from iberbag.schema_upgrade import apply_upgrade
    from iberbag.security import set_password
    from legacy_catalogue import seed_legacy_catalogue
    engine=pg_databases(restricted=True);target=pg_databases(restricted=True)
    with engine.begin() as c:
        cfg=config();cfg.attributes['connection']=c;command.upgrade(cfg,'ddn_0006')
    with scoped(engine) as db:
        seed_legacy_catalogue(db);user=User(username='admin',display_name='Fictício',role='admin',active=True)
        set_password(user,secrets.token_urlsafe(24));db.add(user);db.commit();actor=user.id
    from iberbag.warehouse_settings import add_location
    with scoped(engine,2) as db:add_location(db,'admin','Chão')
    # Baseline service without new planning reads; no new tables exist yet.
    entry(engine,actor,[row(local='Chão')])
    with engine.connect() as c:expected=transfer.inventory(transfer.read_data(c))
    folder=tmp_path/'old-planning';backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    backup.restore(target,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert apply_upgrade(engine,folder)['revision']==REVISION
    with engine.connect() as c:
        assert transfer.inventory(transfer.read_data(c))==expected
        assert c.scalar(text('SELECT version_num FROM alembic_version'))=='ddn_0007'
    prep=draft(engine,actor,[('rolos','LOT','5')]);propose(engine,actor,prep);confirm(engine,actor,prep)

def test_planning_sqlite_never_performs_operational_writes(tmp_path):
    from sqlalchemy import create_engine
    engine=create_engine('sqlite:///'+str(tmp_path/'simulation.db'))
    with Session(engine) as db,pytest.raises(v.ReceivingError,match='PostgreSQL'):
        p.save(db,1,secrets.token_hex(16),'REF','2027-01-01','D',[('paletes','A','1')])
    engine.dispose()
