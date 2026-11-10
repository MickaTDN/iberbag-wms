"""Fictitious stable rolls only; isolated PostgreSQL 17."""
import secrets,json
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import pytest
from sqlalchemy import select,text,func
from sqlalchemy.exc import DBAPIError
from iberbag import valmet as v,valmet_rolls as r,valmet_planning as p,valmet_shipping as s,valmet_control as c,backup,transfer
from iberbag.database import ValmetRoll as Roll,ValmetRollState as State,ValmetRollEvent as Event,ValmetPosition as Pos,ValmetPreparation as Prep,ValmetInventory as Inv,ValmetControlDraft as Draft,ValmetShipmentLine as Line
from test_valmet import valmet,row,entry
from test_receiving import warehouse
from test_transfer import pg_databases,docker_client
from test_ddn_contexts import scoped
from test_security_regressions import restricted_web
from test_valmet_planning import draft
from test_valmet_shipping import send,physical
from conftest import BACKUP_PASSWORD

def receive(engine,actor,flags,lots=None,number='ROLLS'):
    lots=lots or ['A']*len(flags);rows=[dict(article=None,lot=lot,divisible=flag) for lot,flag in zip(lots,flags)]
    return entry(engine,actor,[row('rolos',number,content_total=len(flags),items=[],rolls=rows)])

def reserve(engine,actor,lines,ids=None,extra=False):
    ident=draft(engine,actor,lines)
    with scoped(engine,2) as db:
        from iberbag.valmet_balances import source_key
        pos=db.scalar(select(Pos).where(Pos.unit_id==1,Pos.physical_quantity>0))
        p.review(db,actor,ident,1,roll_selection={source_key(pos):ids} if ids is not None else None)
        p.confirm(db,actor,ident,1,accept_extra=extra)
    return ident

@pytest.mark.parametrize('flags',[[True]*4,[False]*4,[True,True,False,False,False,False,False]])
def test_individual_rolls_select_bundles_and_release_last_pallet(valmet,flags):
    engine,a=valmet;actor=a['admin'][0];receive(engine,actor,flags)
    with scoped(engine,2) as db:before=r.present(db,1);assert len(before)==len(flags);ids=[x['id'] for x in before]
    ident=reserve(engine,actor,[('rolos','A','1')],ids=[ids[0]],extra=not flags[0])
    with scoped(engine,2) as db:
        selected=json.loads(db.get(Prep,ident).review)['selections'][0]['roll_ids'];assert len(selected)==(1 if flags[0] else len(flags))
    send(engine,actor,ident,extra_ack=not flags[0]);assert physical(engine)==(1 if flags[0] else 0)
    with scoped(engine,2) as db:
        remaining=r.present(db,1);assert [x['id'] for x in remaining]==[i for i in ids if i not in selected]
    if remaining:
        rest=reserve(engine,actor,[('rolos','A',str(len(remaining)))],extra=True);send(engine,actor,rest,extra_ack=True)
    assert physical(engine)==0


def test_mixed_multilot_reserves_disjoint_ids_and_never_splits_nondivisible_set(valmet):
    engine,a=valmet;actor=a['admin'][0];receive(engine,actor,[True,True,False,False],['A','A','A','B'])
    with scoped(engine,2) as db:ids=[x['id'] for x in r.present(db,1)]
    one=reserve(engine,actor,[('rolos','A','1')],ids=[ids[0]])
    second=reserve(engine,actor,[('rolos','A','1')],ids=[ids[1]])
    with pytest.raises(v.ReceivingError):reserve(engine,actor,[('rolos','A','1')],ids=[ids[0]])
    group=draft(engine,actor,[('rolos','A','1')])
    with scoped(engine,2) as db:
        plan=p.review(db,actor,group,1,roll_selection={1:[ids[2]]});assert plan['extras']==[dict(kind='rolos',lot='B',quantity=1)]
        with pytest.raises(v.ReceivingError):p.confirm(db,actor,group,1)
        p.confirm(db,actor,group,1,accept_extra=True)
        s.ready(db,actor,group,1)
        with pytest.raises(v.ReceivingError):s.ship(db,actor,group,1,secrets.token_hex(16),'2027-01-03',['GT FICT'],[(1,'A',1)],extra_ack=True)
    send(engine,actor,one);send(engine,actor,second);assert physical(engine)==1
    send(engine,actor,group,extra_ack=True);assert physical(engine)==0


@pytest.mark.parametrize('case',['same_pallet','same_lot_as_bundle','other_pallet','other_nondivisible_pallet','unrequested_bundle'])
def test_roll_selection_rejects_unrequested_rolls_outside_mandatory_bundle(valmet,case):
    engine,a=valmet;actor=a['admin'][0]
    flags,lots=([True,False],['A','B']) if case=='unrequested_bundle' else ([False,False,True],['A','B','B' if case=='same_lot_as_bundle' else 'C'])
    receive(engine,actor,flags,lots)
    if case in ('other_pallet','other_nondivisible_pallet'):
        receive(engine,actor,[case=='other_pallet'],['C'],number='OTHER-ROLLS')
    with scoped(engine,2) as db:
        ids=[x['id'] for x in r.present(db,1)]
        selection={1:[ids[0],ids[1] if case=='unrequested_bundle' else ids[2]]}
        if case in ('other_pallet','other_nondivisible_pallet'):selection={1:[ids[0]],2:[r.present(db,2)[0]['id']]}
        before=[(uid,r.present(db,uid)) for uid in selection]
    order=draft(engine,actor,[('rolos','A','1')])
    with scoped(engine,2) as db:
        with pytest.raises(v.ReceivingError,match='Rolos fora do pedido'):
            p.review(db,actor,order,1,roll_selection=selection)
        assert not p.active(db) and db.get(Prep,order).review is None
        # A review saved by the former validation must also fail on confirmation,
        # even when the office explicitly accepts legitimate excess quantities.
        units={u['id']:u for u in p.source(db)};record=db.get(Prep,order)
        record.review=p.dump(p.summarize(db,record,{uid:r.selection(units[uid],chosen) for uid,chosen in selection.items()}));db.commit()
        with pytest.raises(v.ReceivingError,match='Rolos fora do pedido'):
            p.confirm(db,actor,order,1,accept_extra=True)
        assert not p.active(db) and db.get(Prep,order).active_revision is None
        assert [(uid,r.present(db,uid)) for uid in selection]==before
        assert db.scalar(select(func.count()).select_from(Line))==0


@pytest.mark.parametrize('extra_lot',['A','B'])
def test_roll_selection_keeps_explicit_acceptance_of_mandatory_excess(valmet,extra_lot):
    engine,a=valmet;actor=a['operador'][0]
    receive(engine,actor,[False,False,True],['A',extra_lot,'C'])
    order=draft(engine,actor,[('rolos','A','1')])
    with scoped(engine,2) as db:
        ids=[x['id'] for x in r.present(db,1)]
        plan=p.review(db,actor,order,1,roll_selection={1:[ids[0]]})
        assert plan['selections'][0]['roll_ids']==ids[:2]
        assert plan['extras']==[dict(kind='rolos',lot=extra_lot,quantity=1)]
        with pytest.raises(v.ReceivingError,match='Confirmar explicitamente'):
            p.confirm(db,actor,order,1)
        assert not p.active(db)
        p.confirm(db,actor,order,1,accept_extra=True)
        p.confirm(db,actor,order,1,accept_extra=True)
        assert len(p.active(db))==1 and len(r.present(db,1))==3


def test_legacy_policy_never_creates_individual_identity_and_proof_is_required(valmet):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[dict(row(),rolls=None)])
    with scoped(engine,2) as db:
        with pytest.raises(v.ReceivingError):p.configure(db,actor,1,'yes',note='Política antiga não identifica rolos')
        assert not r.present(db,1) and not p.source(db)
        with pytest.raises(v.ReceivingError):r.reconcile(db,a['operador'][0],1,secrets.token_hex(16),[dict(lot='LOT',article=None,divisible=True)]*5,'Prova','Conferido')
        with pytest.raises(v.ReceivingError):r.reconcile(db,actor,1,secrets.token_hex(16),[dict(lot='LOT',article=None,divisible=True)]*4,'Prova','Conferido')
        key=secrets.token_hex(16);event=r.reconcile(db,actor,1,key,[dict(lot='LOT',article=None,divisible=False)]*5,'Rastreabilidade','Cinco rolos individualmente conferidos')
        assert r.reconcile(db,actor,1,key,[dict(lot='LOT',article=None,divisible=False)]*5,'Rastreabilidade','Cinco rolos individualmente conferidos')==event
        assert len(r.present(db,1))==5 and p.source(db)[0]['rolls']
        assert all(x['article']=='ART' for x in r.present(db,1))

@pytest.mark.parametrize('same',[False,True])
def test_rolls_concurrent_reservations_and_idempotent_shipments(valmet,same):
    engine,a=valmet;actor=a['admin'][0];receive(engine,actor,[True]*4)
    with scoped(engine,2) as db:ids=[x['id'] for x in r.present(db,1)]
    orders=[draft(engine,actor,[('rolos','A','1')]) for _ in range(2)]
    with scoped(engine,2) as db:
        for n,order in enumerate(orders):p.review(db,actor,order,1,roll_selection={1:[ids[0] if same else ids[n]]})
    def run(order):
        try:
            with scoped(engine,2) as db:p.confirm(db,actor,order,1)
            return True
        except v.ReceivingError:return False
    with ThreadPoolExecutor(2) as pool:result=list(pool.map(run,orders))
    assert sum(result)==(1 if same else 2)
    chosen=orders[result.index(True)]
    with scoped(engine,2) as db:
        s.ready(db,actor,chosen,1);key=secrets.token_hex(16)
        load=s.ship(db,actor,chosen,1,key,'2027-01-03',['GT FICT'])
        assert s.ship(db,actor,chosen,1,key,'2027-01-03',['GT FICT'])==load
        assert len(r.present(db,1))==3


def test_rolls_partial_loading_explicit_ids_cancellation_and_rollback(valmet,monkeypatch):
    engine,a=valmet;actor=a['admin'][0];receive(engine,actor,[True,True,False,False],['A','A','B','C'])
    with scoped(engine,2) as db:ids=[x['id'] for x in r.present(db,1)]
    order=reserve(engine,actor,[('rolos','A','2'),('rolos','B','1')],ids=ids,extra=True)
    with scoped(engine,2) as db:
        s.ready(db,actor,order,1)
        s.ship(db,actor,order,1,secrets.token_hex(16),'2027-01-03',['GT FICT'],roll_ids={1:[ids[1]]})
        record=db.get(Prep,order);assert record.status=='ready' and len(r.present(db,1))==3
        assert [x['id'] for x in r.present(db,1)]==[ids[0],ids[2],ids[3]]
        p.cancel(db,actor,order,record.version,'Não carregar remanescente')
    new=reserve(engine,actor,[('rolos','A','1')],ids=[ids[0]])
    old=p.revision
    def fail(db,record,actor,action,snapshot):
        if action=='shipment':raise RuntimeError('Falha fictícia após estados/projeção')
        return old(db,record,actor,action,snapshot)
    monkeypatch.setattr(p,'revision',fail)
    with pytest.raises(RuntimeError):send(engine,actor,new)
    with scoped(engine,2) as db:assert len(r.present(db,1))==3 and len(p.active(db))==1
    assert physical(engine)==1


def test_rolls_inventory_zero_unknown_transfer_adjustment_and_returns(valmet):
    engine,a=valmet;actor=a['admin'][0];receive(engine,actor,[True]*3,['A','A','B'])
    with scoped(engine,2) as db:
        ids=[x['id'] for x in r.present(db,1)];pos=db.scalar(select(Pos));pid=pos.id
        inv=c.create_inventory(db,actor,secrets.token_hex(16),'partial')
        missing=c.save_count(db,actor,inv,1,secrets.token_hex(16),f'P:{pid}','count',1,{'A':1,'B':1})
        c.submit(db,actor,inv,db.get(Inv,inv).version)
        with pytest.raises(v.ReceivingError):c.decide(db,actor,inv,db.get(Inv,inv).version,missing,secrets.token_hex(16),'approve','Diferença','Prova',True)
        counted=c.save_count(db,actor,inv,db.get(Inv,inv).version,secrets.token_hex(16),f'P:{pid}','count',1,{'A':1,'B':1},recount=True,evidence='IDs conferidos',roll_ids=[ids[1],ids[2]])
        c.submit(db,actor,inv,db.get(Inv,inv).version)
        c.decide(db,actor,inv,db.get(Inv,inv).version,counted,secrets.token_hex(16),'approve','Rolo em falta','IDs fisicamente comprovados',True)
        assert [x['id'] for x in r.present(db,1)]==ids[1:]
        transfer_id=c.save_transfer(db,actor,secrets.token_hex(16),pid,'H2',1,'A','Mover palete completa','Localização conferida')
        c.confirm(db,actor,transfer_id,1)
        assert db.scalar(select(Pos.local).where(Pos.physical_quantity==1))=='H2'
        assert [x['id'] for x in r.present(db,1)]==ids[1:]
    order=reserve(engine,actor,[('rolos','A','1')],ids=[ids[1]]);send(engine,actor,order)
    with scoped(engine,2) as db:
        current=db.scalar(select(Pos).where(Pos.physical_quantity==1));line=db.scalar(select(Line.id))
        change=dict(keep=[ids[2]],restore=[ids[1]],add=[])
        draftid=c.save_adjustment(db,actor,secrets.token_hex(16),current.id,'H2',1,{'A':1,'B':1},'Reposição comprovada','Este ID regressou fisicamente',True,basis='return',lineids=[line],roll_change=change)
        c.confirm(db,actor,draftid,1)
        assert [x['id'] for x in r.present(db,1)]==ids[1:] and db.scalar(select(func.count()).select_from(Roll))==3
        found=c.save_adjustment(db,actor,secrets.token_hex(16),current.id,'H2',1,{'A':2,'B':1},'ID encontrado após inventário','ID original efetivamente encontrado, sem saída desse ID',True,roll_change=dict(keep=ids[1:],restore=[ids[0]],add=[]))
        c.confirm(db,actor,found,1)
        assert [x['id'] for x in r.present(db,1)]==ids


def test_rolls_runtime_projection_immutable_permissions_context_and_archived_documents(restricted_web):
    from fastapi.testclient import TestClient
    from iberbag.warehouse_settings import add_location
    app,owner,runtime,role,a,tokens=restricted_web;actor=a['admin'][0]
    with scoped(owner,2) as db:add_location(db,'admin','Chão')
    entry(runtime,actor,[dict(row('rolos','R',local='Chão',items=[]),rolls=[dict(lot='A',article=None,divisible=True)]*2,content_total=2)])
    with scoped(runtime,2) as db:ids=[x['id'] for x in r.present(db,1)]
    order=reserve(runtime,actor,[('rolos','A','1')],ids=[ids[0]])
    with scoped(runtime,2) as db:
        document=s.issue_document(db,actor,order,1)
        from iberbag.database import ValmetDocument
        before=bytes(db.get(ValmetDocument,document).pdf)
        from pypdf import PdfReader
        assert 'Rolo R'+str(ids[0]) in PdfReader(BytesIO(before)).pages[0].extract_text()
        for table in ('valmet_rolls','valmet_roll_states','valmet_roll_events'):
            with pytest.raises(DBAPIError):db.execute(text(f'DELETE FROM {table}'))
            db.rollback()
    with TestClient(app) as client:
        assert client.get('/valmet/units/1/rolls').status_code==403
        client.cookies.set('wms_session',tokens['operador'][0]);client.cookies.set('ddn_context','2')
        assert client.get('/valmet/units/1/rolls').status_code==200
        assert client.post('/valmet/units/1/rolls',data=dict(csrf=tokens['operador'][1],context_id=2,mode='register')).status_code==403
        client.cookies.set('wms_session',tokens['admin'][0])
        assert client.post('/valmet/units/1/rolls',data=dict(csrf='bad',context_id=2)).status_code==403
        assert client.post('/valmet/units/1/rolls',data=dict(csrf=tokens['admin'][1],context_id=1)).status_code==409
        assert client.get('/valmet/stock').status_code==200
        client.cookies.set('ddn_context','1');assert client.get('/valmet/units/1/rolls').status_code==403
    send(runtime,actor,order)
    with scoped(runtime,2) as db:
        assert bytes(db.get(ValmetDocument,document).pdf)==before
        with pytest.raises(v.ReceivingError):r.classify(db,actor,1,secrets.token_hex(16),{ids[1]:False},'Alteração','Não pode alterar conjunto após saídas')
    with scoped(runtime,1) as db:assert not r.present(db,1)


def test_rolls_backup_restore_preserves_ids_classes_holds_and_pdf(valmet,pg_databases,tmp_path):
    engine,a=valmet;actor=a['admin'][0];receive(engine,actor,[True,True,False,False],['A','A','B','C'])
    with scoped(engine,2) as db:ids=[x['id'] for x in r.present(db,1)]
    order=reserve(engine,actor,[('rolos','A','2')],ids=ids[:2])
    with scoped(engine,2) as db:s.issue_document(db,actor,order,1)
    with engine.connect() as conn:expected=transfer.inventory(transfer.read_data(conn))
    folder=tmp_path/'rolls';target=pg_databases(restricted=True)
    backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert backup.restore(target,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'
    with target.connect() as conn:assert transfer.inventory(transfer.read_data(conn))==expected
    with scoped(target,2) as db:assert [x['id'] for x in r.present(db,1)]==ids and len(p.active(db))==1
    send(target,actor,order);assert physical(target)==1


def test_rolls_ddn9_upgrade_never_backfills_legacy_or_changes_pdfs(pg_databases,tmp_path):
    from alembic import command
    from iberbag.migrations import config,REVISION
    from iberbag.schema_upgrade import apply_upgrade
    from iberbag.security import set_password
    from iberbag.database import User
    from legacy_catalogue import seed_legacy_catalogue
    from iberbag.warehouse_settings import add_location
    engine=pg_databases(restricted=True);target=pg_databases(restricted=True)
    with engine.begin() as conn:
        cfg=config();cfg.attributes['connection']=conn;command.upgrade(cfg,'ddn_0009')
    with scoped(engine) as db:
        seed_legacy_catalogue(db);user=User(username='admin',display_name='Fictício',role='admin',active=True);set_password(user,secrets.token_urlsafe(24));db.add(user);db.commit();actor=user.id
    with scoped(engine,2) as db:add_location(db,'admin','Chão')
    entry(engine,actor,[dict(row(local='Chão'),rolls=None)])
    with scoped(engine,2) as db:
        p.configure(db,actor,1,'yes',note='Política antiga comprovada')
        legacy=p.save(db,actor,secrets.token_hex(16),'ANTIGA','2027-01-01','D',[('rolos','LOT',1)])
        p.review(db,actor,legacy,1);p.confirm(db,actor,legacy,1)
        s.issue_document(db,actor,legacy,1)
        another=p.save(db,actor,secrets.token_hex(16),'OUTRA ANTIGA','2027-01-01','D',[('rolos','LOT',1)])
        p.review(db,actor,another,1);p.confirm(db,actor,another,1)
    with engine.connect() as conn:before=transfer.inventory(transfer.read_data(conn))
    folder=tmp_path/'ddn9';backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD);backup.restore(target,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert apply_upgrade(engine,folder)['revision']==REVISION=='ddn_0015'
    with engine.connect() as conn:assert transfer.inventory(transfer.read_data(conn))==before
    with scoped(engine,2) as db:
        assert not r.present(db,1) and not p.source(db)
        with pytest.raises(v.ReceivingError):s.ready(db,actor,legacy,1)
        with pytest.raises(v.ReceivingError):r.reconcile(db,actor,1,secrets.token_hex(16),[dict(article=None,lot='LOT',divisible=True)]*5,'Prova','Reservas antigas ainda ativas')
        p.cancel(db,actor,legacy,1,'Libertar reserva histórica sem saída')
        assert len(p.active(db))==1
        with pytest.raises(v.ReceivingError):r.reconcile(db,actor,1,secrets.token_hex(16),[dict(article=None,lot='LOT',divisible=True)]*5,'Prova','Ainda existe outra reserva histórica')
        p.cancel(db,actor,another,1,'Libertar segunda reserva preservando histórico')
        r.reconcile(db,actor,1,secrets.token_hex(16),[dict(article=None,lot='LOT',divisible=True)]*5,'Reconciliação','Cada rolo remanescente foi conferido individualmente')
        assert len(r.present(db,1))==5


def test_rolls_admin_historical_difference_reconciliation_and_no_fake_ids(valmet):
    engine,a=valmet;actor=a['admin'][0];entry(engine,actor,[dict(row(),rolls=None)])
    with scoped(engine,2) as db:
        pos=db.scalar(select(Pos));raw=[dict(article=None,lot='LOT',divisible=False)]*4
        ident=c.save_adjustment(db,actor,secrets.token_hex(16),pos.id,'A2',1,{'LOT':4},'Falta histórica comprovada','Quatro rolos reais, classificados um a um',True,roll_change=dict(keep=[],restore=[],add=raw))
        c.confirm(db,actor,ident,1);ids=[x['id'] for x in r.present(db,1)];assert len(ids)==4
        with pytest.raises(v.ReceivingError):c.save_adjustment(db,actor,secrets.token_hex(16),pos.id,'A2',1,{'LOT':3},'Falta','Prova',True)
        inv=c.create_inventory(db,actor,secrets.token_hex(16),'partial')
        with pytest.raises(v.ReceivingError):r.classify(db,actor,1,secrets.token_hex(16),{ids[0]:True},'Alterar','Inventário em curso protege referência')
        draft=c.save_transfer(db,actor,secrets.token_hex(16),pos.id,'H2',1,'LOT','Mover','Palete completa')
        c.confirm(db,actor,draft,1)
        with pytest.raises(v.ReceivingError):c.save_adjustment(db,actor,secrets.token_hex(16),pos.id,'H2',1,{'FAKE':1},'Erro','Não inventar lote',True,roll_change=dict(keep=[],restore=[],add=[dict(article=None,lot='FAKE',divisible=True)]))


def test_rolls_parsers_and_sqlite_never_operate_without_postgres():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    assert r.parse_lines('| L | X\nART | L | -')==[dict(article=None,lot='L',divisible=True),dict(article='ART',lot='L',divisible=False)]
    for data in ('| L |','| L | yes','| | X'):
        with pytest.raises(v.ReceivingError):r.parse_lines(data)
    with Session(create_engine('sqlite://')) as db:
        with pytest.raises(v.ReceivingError,match='PostgreSQL'):r.reconcile(db,1,1,secrets.token_hex(16),[],'','')
        with pytest.raises(v.ReceivingError,match='PostgreSQL'):r.classify(db,1,1,secrets.token_hex(16),{},'','')


def test_rolls_stock_article_search_uses_remaining_identities_not_original_totals(valmet):
    engine,a=valmet;actor=a['admin'][0]
    entry(engine,actor,[row('rolos','ARTICLES',content_total=2,items=[],rolls=[dict(article='ART-A',lot='L',divisible=True),dict(article='ART-B',lot='L',divisible=True)])])
    with scoped(engine,2) as db:
        ids=[x['id'] for x in r.present(db,1)]
        assert v.stock(db,{'article':'ART-A','lot':'L'})[1]==1
    order=reserve(engine,actor,[('rolos','L','1')],ids=[ids[0]]);send(engine,actor,order)
    with scoped(engine,2) as db:
        assert v.stock(db,{'article':'ART-A','lot':'L'})[1]==0
        assert v.stock(db,{'article':'ART-B','lot':'L'})[1]==1
        assert p.source(db)[0]['articles']=={'L':['ART-B']}


def test_rolls_postgres_rejects_forged_partial_bundle_and_temp_writer_trigger(valmet):
    from datetime import datetime
    from iberbag.database import ValmetShipment
    engine,a=valmet;actor=a['admin'][0];receive(engine,actor,[True,False,False],['A','B','C'])
    with scoped(engine,2) as db:ids=[x['id'] for x in r.present(db,1)]
    with scoped(engine,2) as db:
        assert db.scalar(text('SELECT valmet_roll_ids_valid(1,2,NULL)')) is False
        with pytest.raises(DBAPIError,match='Explicit classification IDs required'):
            db.add(Event(unit_id=1,request_key=secrets.token_hex(16),action='classify',payload=p.dump(dict(flags=[dict(id=ids[0],divisible=False)])),reason='Prova inválida',evidence='IDs em falta devem ser recusados no PostgreSQL',actor='admin'));db.flush()
        db.rollback()
    order=reserve(engine,actor,[('rolos','B','1')],ids=[ids[1]],extra=True)
    with scoped(engine,2) as db:
        s.ready(db,actor,order,1);prep=db.get(Prep,order)
        from iberbag.database import ValmetReservation
        hold=db.scalar(select(ValmetReservation).where(ValmetReservation.revision_id==prep.active_revision))
        unit=p.source(db,order)[0]
        output=dict(unit=unit,physical=1,materials={'B':1},exclusive=False,roll_ids=[ids[1]],out_physical=0)
        with pytest.raises(DBAPIError,match='Invalid individual roll shipment'):
            header=ValmetShipment(preparation_id=order,revision_id=prep.active_revision,request_key=secrets.token_hex(16),payload='{}',loaded_on=datetime(2027,1,3),references='["GT FICT"]',snapshot=p.dump(dict(selections=[output],actual={})),actor='admin')
            db.add(header);db.flush()
            db.add(Line(shipment_id=header.id,reservation_id=hold.id,position_id=unit['position_id'],physical_quantity=0,materials='{"B":1}',unknown_ack=False));db.flush()
        db.rollback();assert len(r.present(db,1))==3 and db.scalar(select(func.count()).select_from(Line))==0
    with engine.connect() as conn:
        schema=conn.scalar(text('SELECT current_schema()'));quoted=conn.dialect.identifier_preparer.quote(schema)
        conn.exec_driver_sql(f'CREATE TEMP TABLE spoof_roll_event (LIKE {quoted}.valmet_roll_events INCLUDING DEFAULTS)')
        conn.exec_driver_sql('CREATE TRIGGER forged AFTER INSERT ON spoof_roll_event FOR EACH ROW EXECUTE FUNCTION valmet_roll_event_apply()')
        with pytest.raises(DBAPIError,match='Invalid roll event trigger'):conn.exec_driver_sql(f'INSERT INTO spoof_roll_event SELECT * FROM {quoted}.valmet_roll_events LIMIT 1')
        conn.rollback()
    assert physical(engine)==1
