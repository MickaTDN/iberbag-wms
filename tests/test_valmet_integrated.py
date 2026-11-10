"""One continuous office lifecycle; only generated fictional workbooks/PG17."""
import json,secrets
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select,func,text
from iberbag import database as m,valmet as v,valmet_planning as p,valmet_shipping as s,valmet_control as c,valmet_rolls as r,backup,transfer
from iberbag.privileges import validate_runtime
from iberbag.warehouse_settings import add_location,set_capacity
from test_security_regressions import restricted_web
from test_receiving import warehouse
from test_transfer import pg_databases,docker_client
from test_ddn_contexts import scoped
from test_valmet_import import stage,confirm,workbook
from test_valmet import entry,row
from test_valmet_planning import draft
from conftest import BACKUP_PASSWORD


def state(engine):
    with engine.connect() as conn:return transfer.inventory(transfer.read_data(conn))

def order(engine,actor,lines,extra=False):
    ident=draft(engine,actor,lines)
    with scoped(engine,2) as db:p.review(db,actor,ident,1);p.confirm(db,actor,ident,1,accept_extra=extra)
    return ident

def setup(owner):
    with scoped(owner,2) as db:
        for loc in ('A2','H2','E2','Chão'):add_location(db,'admin',loc)
        set_capacity(db,'admin','A2','20');set_capacity(db,'admin','H2','1')

def position(db,kind):
    return db.scalar(select(m.ValmetPosition).join(m.ValmetUnit,m.ValmetUnit.id==m.ValmetPosition.unit_id).where(m.ValmetUnit.kind==kind,m.ValmetPosition.physical_quantity>0).order_by(m.ValmetPosition.id))

def counted_inventory(engine,actor,mode,**scope):
    with scoped(engine,2) as db:
        ident=c.create_inventory(db,actor,secrets.token_hex(16),mode,**scope)
        targets=c.inventory_targets(db,db.get(m.ValmetInventory,ident))
        marks=[]
        for key,t in targets.items():
            if t['position']:
                pos=db.get(m.ValmetPosition,t['position']);qty=pos.physical_quantity;content=json.loads(pos.materials)
                ids=[x['id'] for x in r.present(db,pos.unit_id)] if t['kind']=='rolos' else None
            else:
                qty=sum(x['physical'] for x in targets.values() if x['position'] and x['local']==t['local']);content={};ids=None
            if not t['position'] and qty==0:
                unknown=c.save_count(db,actor,ident,db.get(m.ValmetInventory,ident).version,secrets.token_hex(16),key,'count',None,None)
                assert json.loads(db.get(m.ValmetCount,unknown).payload)['physical'] is None
            marks.append(c.save_count(db,actor,ident,db.get(m.ValmetInventory,ident).version,secrets.token_hex(16),key,'count',qty,content,roll_ids=ids,recount=not t['position'] and qty==0,evidence='Localização explicitamente contada vazia' if qty==0 else 'Conferência fictícia'))
        c.submit(db,actor,ident,db.get(m.ValmetInventory,ident).version)
        for mark in marks:c.decide(db,actor,ident,db.get(m.ValmetInventory,ident).version,mark,secrets.token_hex(16),'approve','Conferência integrada fictícia','Todos os grupos fisicamente conferidos',True)
        c.close_inventory(db,actor,ident,db.get(m.ValmetInventory,ident).version)
        assert db.get(m.ValmetInventory,ident).status=='closed'
    return ident


def test_integrated_valmet_office_lifecycle_and_reconciled_recovery(restricted_web,pg_databases,tmp_path):
    app,owner,engine,role,a,tokens=restricted_web;actor=a['admin'][0];setup(owner)
    with engine.connect() as conn:validate_runtime(conn)
    with scoped(engine,1) as db:foreign_before=[dict(x) for x in db.execute(select(m.Stock.__table__)).mappings()]
    def multilots(b):b['Paletes'].append([None,'PALLET-B',None,'A2','PALLET-1',None])
    source,revision,digest,plan=stage(engine,actor,workbook(multilots));assert not plan['blocking']
    confirmation=confirm(engine,actor,revision,digest)
    assert confirm(engine,actor,revision,digest)==confirmation
    with scoped(engine,2) as db:
        rows,total=v.stock(db);assert total==8 and sum(u.physical_quantity for u,i,items,avail in rows)==10
        for kind,amount in plan['physical'].items():assert sum(u.physical_quantity for u,i,items,avail in rows if u.kind==kind)==amount
        for field,value in (('article','ART'),('lot','PALLET-B'),('number','PALLET-1'),('local','A2')):assert v.stock(db,{field:value})[1]>0
        pallet=position(db,'paletes');assert json.loads(pallet.materials)=={'PALLET':1,'PALLET-B':1}
        assert str(db.get(m.ValmetUnit,pallet.unit_id).material_m2)=='12.500'
        audit=json.loads(db.scalar(select(m.Audit.details).where(m.Audit.action=='VALMET_IMPORT_CONFIRMAR')))
        assert len(audit['mapping'])==8 and len(next(x for x in audit['mapping'] if x['rolls'])['rolls'])==4
    entry(engine,actor,[row('fio',None,local='Chão',physical_quantity=3,content_total=None,items=[('ART','FIO',3)]),row('caixas','REPLACEMENT',local='Chão',content_total=None,items=[('ART','BOX',None)])])
    with scoped(engine,2) as db:assert sum(u.physical_quantity for u,i,it,avail in v.stock(db)[0])==14
    prep=order(engine,actor,[('rolos','A','2'),('fio','FIO','2')])
    other=order(engine,actor,[('rolos','A','1')])
    with scoped(engine,2) as db:
        assert json.loads(db.get(m.ValmetPreparation,other).review)['shortages']
        p.cancel(db,actor,other,1,'Sem duplicar reserva')
        first=s.issue_document(db,actor,prep,1);pdf=bytes(db.get(m.ValmetDocument,first).pdf)
        before=state(engine)
        assert s.issue_document(db,actor,prep,1)==first and state(engine)==before
        assert bytes(db.get(m.ValmetDocument,first).pdf)==pdf
        s.ready(db,actor,prep,1);key=secrets.token_hex(16)
        selection=json.loads(db.get(m.ValmetPlanRevision,db.get(m.ValmetPreparation,prep).active_revision).snapshot)['selections']
        rol=next(x for x in selection if x['unit']['kind']=='rolos');uid=rol['unit']['origin_id']
        s.ship(db,actor,prep,1,key,'2027-03-01',['GT FICT-1','CMR FICT-1'],[(rol['unit']['id'],'A',1)])
        assert s.ship(db,actor,prep,1,key,'2027-03-01',['GT FICT-1','CMR FICT-1'],[(rol['unit']['id'],'A',1)])
        assert len(r.present(db,uid))==3
        s.ship(db,actor,prep,db.get(m.ValmetPreparation,prep).version,secrets.token_hex(16),'2027-03-02',['GT FICT-2','CMR FICT-2'])
        assert len(r.present(db,uid))==2 and db.scalar(select(m.ValmetPosition.physical_quantity).where(m.ValmetPosition.unit_id==uid,m.ValmetPosition.root))==1
    contenders=[draft(engine,actor,[('rolos','B','1')]) for _ in range(2)]
    for contender in contenders:
        with scoped(engine,2) as db:p.review(db,actor,contender,1)
    def reserve(contender):
        try:
            with scoped(engine,2) as db:p.confirm(db,actor,contender,1,accept_extra=True)
            return contender
        except v.ReceivingError:return None
    with ThreadPoolExecutor(2) as pool:winners=[x for x in pool.map(reserve,contenders) if x]
    assert len(winners)==1
    with scoped(engine,2) as db:p.cancel(db,actor,winners[0],1,'Libertar conjunto inteiro antes da preparação final')
    bundle=order(engine,actor,[('rolos','B','1')],extra=True)
    with scoped(engine,2) as db:
        s.ready(db,actor,bundle,1)
        with pytest.raises(v.ReceivingError):s.ship(db,actor,bundle,1,secrets.token_hex(16),'2027-03-03',['GT FICT-3'],[(uid,'B',1)],extra_ack=True)
        s.ship(db,actor,bundle,1,secrets.token_hex(16),'2027-03-03',['GT FICT-3','CMR FICT-3'],extra_ack=True)
        assert not r.present(db,uid)
        assert db.scalar(select(m.ValmetPosition.physical_quantity).where(m.ValmetPosition.unit_id==uid,m.ValmetPosition.root))==0
        assert bytes(db.get(m.ValmetDocument,first).pdf)==pdf
    # Missing historical box is replaced by the newly received box, not erased.
    missing=order(engine,actor,[('caixas','BOX','1')])
    with scoped(engine,2) as db:
        chosen=json.loads(db.get(m.ValmetPreparation,missing).review)['selections'][0]['unit']['id']
        event=s.report(db,actor,missing,1,chosen,'missing','Caixa não encontrada fictícia',lot='BOX',amount=1)
        with pytest.raises(v.ReceivingError):s.ready(db,actor,missing,1)
        rec=db.get(m.ValmetPreparation,missing)
        p.save(db,actor,rec.request_key,'Substituição fictícia','2027-03-04','Destino fictício',[('caixas','BOX',1)],missing,1)
        p.review(db,actor,missing,2);p.confirm(db,actor,missing,2)
        assert json.loads(db.get(m.ValmetPreparation,missing).review)['selections'][0]['unit']['id']!=chosen
        s.resolve(db,actor,event,'Caixa original encontrada; não descontar nem recriar stock')
        replacement=json.loads(db.get(m.ValmetPreparation,missing).review)['selections'][0]['unit']['id']
        changed=s.report(db,actor,missing,2,replacement,'substitution','Substituição comprovada pela nova entrada',lot='BOX',amount=1)
        s.resolve(db,actor,changed,'Revisão e unidade substituta conferidas no escritório')
        s.ready(db,actor,missing,2);s.ship(db,actor,missing,2,secrets.token_hex(16),'2027-03-04',['GT FICT-4'])
    # Location discrepancy rebinds a reserved quantitative group transactionally.
    locating=order(engine,actor,[('fio','FIO','1')])
    with scoped(engine,2) as db:
        key=json.loads(db.get(m.ValmetPreparation,locating).review)['selections'][0]['unit']['id']
        s.ready(db,actor,locating,1);s.relocate(db,actor,locating,1,key,'H2',1,'Uma palete constatada em H2','FIO')
        rec=db.get(m.ValmetPreparation,locating);assert rec.version==2
        s.ready(db,actor,locating,2);s.ship(db,actor,locating,2,secrets.token_hex(16),'2027-03-05',['GT FICT-5'])
        assert db.scalar(select(func.sum(m.ValmetPosition.physical_quantity)).where(m.ValmetPosition.local=='H2'))==0
        piece=position(db,'pecas');pid=piece.id
        with pytest.raises(v.ReceivingError):c.save_adjustment(db,a['operador'][0],secrets.token_hex(16),pid,'A2',1,{'PC':11},'Contagem','Prova',True)
        adjustment=c.save_adjustment(db,actor,secrets.token_hex(16),pid,'A2',1,{'PC':11},'Uma peça em falta','Contagem fictícia comprovada',True);c.confirm(db,actor,adjustment,1)
        vas=position(db,'vasilhame');move=c.save_transfer(db,actor,secrets.token_hex(16),vas.id,'Chão',2,'Filva','Localização comprovada','Duas paletes fictícias conferidas',located=True);c.confirm(db,actor,move,1)
        box=position(db,'caixas');move=c.save_transfer(db,actor,secrets.token_hex(16),box.id,'H2',1,'BOX','Transferência inteira','Caixa física indivisível conferida');c.confirm(db,actor,move,1)
    counted_inventory(engine,actor,'partial',local='H2')
    counted_inventory(engine,actor,'complete')
    with scoped(engine,2) as db:
        assert json.loads(db.get(m.ValmetPosition,pid).materials)=={'PC':11}
        assert db.scalar(select(func.sum(m.ValmetPosition.physical_quantity)))==9
        assert not p.active(db)
    with scoped(engine,1) as db:assert [dict(x) for x in db.execute(select(m.Stock.__table__)).mappings()]==foreign_before
    with scoped(engine,2) as db:
        before_positions=[tuple(x) for x in db.execute(select(m.ValmetPosition.id,m.ValmetPosition.local,m.ValmetPosition.physical_quantity,m.ValmetPosition.materials).order_by(m.ValmetPosition.id))]
        piece=position(db,'pecas')
        with pytest.raises(v.ReceivingError):
            bad=c.save_transfer(db,actor,secrets.token_hex(16),piece.id,'H2',1,'PC','Destino cheio','Não ultrapassar capacidade')
            c.confirm(db,actor,bad,1)
        assert [tuple(x) for x in db.execute(select(m.ValmetPosition.id,m.ValmetPosition.local,m.ValmetPosition.physical_quantity,m.ValmetPosition.materials).order_by(m.ValmetPosition.id))]==before_positions
        assert bytes(db.get(m.ValmetDocument,first).pdf)==pdf
    before=state(engine);folder=tmp_path/'integrated';target=pg_databases(restricted=True)
    backup.backup(engine,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)
    assert backup.restore(target,folder,runner=docker_client,passphrase=BACKUP_PASSWORD)['status']=='reconciled'
    assert state(target)==before
    assert confirm(target,actor,revision,digest)==confirmation
    assert state(target)==before
    _,second,second_digest,blocked=stage(target,actor,workbook(lambda b:setattr(b['Caixas']['F2'],'value','Ficheiro fictício alterado')))
    assert any(i['code']=='BASE_HISTORICA_JA_CONFIRMADA' for i in blocked['issues'])
    with pytest.raises(v.ReceivingError):confirm(target,actor,second,second_digest)


@pytest.mark.parametrize('failure',['exception','disconnect'])
def test_integrated_valmet_failed_shipment_does_not_change_restorable_books(restricted_web,monkeypatch,failure):
    app,owner,engine,role,a,tokens=restricted_web;actor=a['admin'][0];setup(owner)
    _,rev,digest,_=stage(engine,actor);confirm(engine,actor,rev,digest)
    prep=order(engine,actor,[('rolos','A','1')])
    with scoped(engine,2) as db:s.issue_document(db,actor,prep,1);s.ready(db,actor,prep,1)
    before=state(engine);original=p.revision
    def fail(db,record,actor,action,snapshot):
        if action=='shipment':
            if failure=='exception':raise RuntimeError('Falha fictícia depois de projetar a saída')
            pid=db.scalar(text('SELECT pg_backend_pid()'))
            with owner.begin() as conn:assert conn.scalar(text('SELECT pg_terminate_backend(:pid)'),{'pid':pid})
        return original(db,record,actor,action,snapshot)
    monkeypatch.setattr(p,'revision',fail)
    with scoped(engine,2) as db,pytest.raises((RuntimeError,v.ReceivingError)):
        s.ship(db,actor,prep,1,secrets.token_hex(16),'2027-03-01',['GT FICT FAIL'])
    assert state(engine)==before
    monkeypatch.setattr(p,'revision',original)
    with scoped(engine,2) as db:s.ship(db,actor,prep,1,secrets.token_hex(16),'2027-03-01',['GT FICT RECOVERED'])
