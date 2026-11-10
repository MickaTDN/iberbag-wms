"""NKT load-level conference. One known physical PEG locates its entire load."""
import json
from hashlib import sha256
from sqlalchemy import select,func
from . import nkt,nkt_loading as loading
from .nkt_receiving import office,bounded_text,metadata
from .receiving import ReceivingError,request_key
from .database import (NktInventory as Inventory,NktInventoryCheck as Check,NktInventoryReview as Review,
    NktInventoryIncident as Incident,NktEntry,NktPallet as Pallet,NktOperation as Op,NktStockEvent as Event,NktDocument as Document,
    Location,User,now_utc)

class IdentificationError(ReceivingError):
    def __init__(self,message,kind,candidates=()):super().__init__(message);self.kind=kind;self.candidates=list(candidates)

def resolve(db,communicated,*,full=False):
    code=bounded_text(communicated,'PEG comunicado')
    candidates=[p for p in db.scalars(select(Pallet).order_by(Pallet.id)) if (p.peg[-4:].casefold()==code.casefold() if len(code)==4 and not full else p.peg.casefold()==code.casefold())]
    if not candidates:raise IdentificationError('PEG não identificado neste contexto; ocorrência pendente.','unknown_peg')
    if len(candidates)!=1:raise IdentificationError('Identificador ambíguo; exigir PEG completo, sem escolher uma carga.','ambiguous_peg',[dict(pallet_id=p.id,peg=p.peg,entry_id=p.entry_id) for p in candidates])
    return candidates[0]

def groups(db):
    pallets=db.scalars(select(Pallet).order_by(Pallet.id)).all();current=loading.states(db);info=metadata(db,[p.entry_id for p in pallets]);out={}
    for p in pallets:
        entry=out.setdefault(p.entry_id,dict(entry_id=p.entry_id,metadata=info.get(p.entry_id),pallets=[]))
        state=loading.state_for(current,p.id)
        entry['pallets'].append(dict(id=p.id,peg=p.peg,local=p.local,**state))
    for g in out.values():
        g['physical']=sum(p['state'] in ('available','reserved') for p in g['pallets']);g['reserved']=sum(p['state']=='reserved' for p in g['pallets']);g['available']=sum(p['state']=='available' for p in g['pallets'])
        g['excluded']='legacy_unmapped' if not g['metadata'] else ('reserved' if g['reserved'] else ('no_physical' if not g['physical'] else None))
    return out

def permission(db,actor,inv=None,admin=False):
    office(db,actor,admin)
    if inv and actor.role!='admin' and inv.created_by!=actor.id:raise ReceivingError('Só o autor ou Administrador regista conferências.',403)

def get_inventory(db,actor,ident,admin=False):
    inv=db.get(Inventory,ident)
    if not inv:raise ReceivingError('Inventário inexistente neste contexto.',404)
    permission(db,actor,inv,admin);return inv

def open_inventory(inv,version):
    if inv.status!='draft' or inv.version!=version:raise ReceivingError('Inventário confirmado ou versão desatualizada; recarregar.')

def incident(db,actor,inv,code,kind,details,resolves=None):
    row=Incident(inventory_id=inv.id,communicated=code,kind=kind,details=nkt.dump(details),resolves_id=resolves,actor=actor.username);db.add(row);db.flush()
    loading.audit(db,actor,'INVENTARIO_OCORRENCIA',dict(inventory=inv.id,incident=row.id,kind=kind,details=details));return row.id

def checks(db,inv):
    result={}
    for row in db.scalars(select(Check).where(Check.inventory_id==inv.id).order_by(Check.id)):result[row.entry_id]=row
    return result

def summary(db,inv):
    catalog=groups(db);latest=checks(db,inv);scope=json.loads(inv.scope);rows=[];blocking=[];totals={name:0 for name in ('located','not_located','excluded','unverified')}
    if inv.mode=='complete' and {ident for ident,g in catalog.items() if g['physical']}-set(scope):blocking.append('Existem novas cargas físicas fora do âmbito inicial; criar novo inventário completo.')
    for ident in scope:
        g=catalog.get(ident);check=latest.get(ident)
        if not g:raise ReceivingError('Carga original já não pode ser reconciliada; não inferir ausência.')
        decision=check.state if check else 'unverified';totals[decision]+=1
        if not check:blocking.append(f'Entrada {ident}: ainda não conferida.')
        elif check.snapshot!=nkt.dump(g):blocking.append(f'Entrada {ident}: stock/reservas mudaram; repetir conferência ou rever exclusão.')
        if g['excluded']=='legacy_unmapped':blocking.append(f'Entrada {ident}: sem carga/contentor comprovado; investigação obrigatória.')
        elif g['excluded'] and decision!='excluded':blocking.append(f'Entrada {ident}: exige exclusão explícita atual, sem ajustar reservas.')
        elif not g['excluded'] and decision=='excluded':blocking.append(f'Entrada {ident}: deixou de estar excluída; conferir a carga.')
        rows.append(dict(source=g,decision=decision,check_id=check.id if check else None,communicated=check.communicated if check else None,resolved=check.pallet_id if check else None,full_peg=check.full_peg if check else False))
    incidents=db.scalars(select(Incident).where(Incident.inventory_id==inv.id).order_by(Incident.id)).all();resolved={i.resolves_id for i in incidents if i.resolves_id}
    pending=[i.id for i in incidents if not i.resolves_id and i.id not in resolved]
    if pending:blocking.append('Ocorrências pendentes exigem decisão administrativa explícita.')
    return dict(scope=scope,mode=inv.mode,groups=rows,totals=totals,pending_incidents=pending,blocking=blocking,current_scope=sorted(ident for ident,g in catalog.items() if g['physical']))

def review_record(db,actor,inv,key,kind,payload,reason,original=None):
    key=request_key(key);encoded=nkt.dump(payload);old=db.scalar(select(Review).where(Review.request_key==key))
    if old:
        if old.inventory_id!=inv.id or old.kind!=kind or old.payload!=encoded or old.reason!=reason or old.original_id!=original:raise ReceivingError('Revisão repetida com efeitos diferentes.')
        return old
    row=Review(inventory_id=inv.id,request_key=key,kind=kind,version=inv.version,payload=encoded,digest=nkt.digest(encoded),reason=reason,original_id=original,actor=actor.username);db.add(row);db.flush()
    document(db,actor,inv,payload,kind,review=row.id,reason=reason);loading.audit(db,actor,'INVENTARIO_REVER',dict(inventory=inv.id,review=row.id,kind=kind,digest=row.digest,reason=reason));return row

def document(db,actor,inv,payload,kind,*,review=None,operation=None,reason=''):
    from .nkt_inventory_pdf import render_pdf
    raw=render_pdf(inv.id,inv.version,kind,payload,actor.username,now_utc(),reason)
    db.add(Document(inventory_review_id=review,operation_id=operation,pdf=raw,digest=sha256(raw).hexdigest(),actor=actor.username))

def conference_pdf(db,actor,inv):
    return review_record(db,actor,inv,sha256(f'nkt-inventory:{inv.id}:{inv.version}'.encode()).hexdigest()[:32],'conference',summary(db,inv),'Conferência arquivada; não altera stock.')

@nkt.command
def create(db,actor,key,title,mode,selected=None):
    permission(db,actor);key=request_key(key);title=bounded_text(title,'Identificação do inventário')
    old=db.scalar(select(Inventory).where(Inventory.request_key==key))
    if old:
        if old.created_by!=actor.id or old.title!=title or old.mode!=mode or (mode=='partial' and json.loads(old.scope)!=sorted(set(selected or []))):raise ReceivingError('Pedido repetido com outro âmbito.')
        return old.id
    if mode not in ('complete','partial'):raise ReceivingError('Selecionar inventário completo ou parcial.',400)
    catalog=groups(db);scope=sorted(ident for ident,g in catalog.items() if g['physical']) if mode=='complete' else sorted(set(selected or []))
    if not 1<=len(scope)<=500 or any(type(i) is not int or i not in catalog or not catalog[i]['physical'] for i in scope):raise ReceivingError('Selecionar 1–500 cargas fisicamente registadas, sem inferir cargas fora do âmbito.',400)
    inv=Inventory(request_key=key,title=title,mode=mode,scope=nkt.dump(scope),opening=nkt.dump({str(i):catalog[i] for i in scope}),created_by=actor.id);db.add(inv);db.flush()
    for ident in scope:
        g=catalog[ident]
        if g['excluded']:
            db.add(Check(inventory_id=inv.id,entry_id=ident,state='excluded',full_peg=False,snapshot=nkt.dump(g),reason='Exclusão inicial: '+g['excluded'],actor=actor.username))
        if g['excluded']=='legacy_unmapped':incident(db,actor,inv,None,'unmapped_load',dict(entry_id=ident,reason='Carga/contentor não comprovado; não inventar a identidade da carga.'))
    db.flush();conference_pdf(db,actor,inv);loading.audit(db,actor,'INVENTARIO_ABRIR',dict(inventory=inv.id,scope=scope,mode=mode,opening=json.loads(inv.opening)));return inv.id

@nkt.command
def record(db,actor,ident,version,state,entry_id=None,communicated=None,reason='',full_peg=False):
    inv=get_inventory(db,actor,ident);open_inventory(inv,version);reason=bounded_text(reason,'Observação da conferência',2000);code=communicated or None;pallet=None
    try:
        if state=='located':pallet=resolve(db,communicated,full=full_peg);entry_id=pallet.entry_id
        if type(entry_id) is not int or entry_id not in json.loads(inv.scope):raise IdentificationError('Carga fora do âmbito selecionado; não alterar o âmbito por inferência.','out_of_scope')
        g=groups(db)[entry_id]
        if state=='excluded':
            if not g['excluded']:raise ReceivingError('Carga elegível não pode ser excluída sem motivo de stock/reserva.')
        elif state in ('located','not_located'):
            if g['excluded']:raise IdentificationError('Carga excluída; rever exclusão/investigação sem ajustar stock.','excluded_load',[dict(entry_id=entry_id,reason=g['excluded'])])
            if pallet and next(p for p in g['pallets'] if p['id']==pallet.id)['state'] not in ('available','reserved'):raise IdentificationError('PEG não está fisicamente registado; investigar, sem localizar a carga por inferência.','not_physical')
        else:raise ReceivingError('Estado de conferência inválido.',400)
    except IdentificationError as error:
        report=incident(db,actor,inv,code,error.kind,dict(message=str(error),candidates=error.candidates,reason=reason));inv.version+=1;db.flush();conference_pdf(db,actor,inv);return dict(incident=report,message=str(error))
    row=Check(inventory_id=inv.id,entry_id=entry_id,state=state,communicated=code,full_peg=bool(full_peg),pallet_id=pallet.id if pallet else None,snapshot=nkt.dump(g),reason=reason,actor=actor.username);db.add(row);inv.version+=1;db.flush();conference_pdf(db,actor,inv)
    loading.audit(db,actor,'INVENTARIO_CONFERIR',dict(inventory=inv.id,check=row.id,entry_id=entry_id,state=state,communicated=code,full_peg=bool(full_peg),resolved=pallet.id if pallet else None,snapshot=g,reason=reason));return dict(check=row.id,entry=entry_id)

@nkt.command
def report_unknown(db,actor,ident,version,communicated,note):
    inv=get_inventory(db,actor,ident)
    if inv.status=='draft':open_inventory(inv,version)
    note=bounded_text(note,'Material desconhecido / ocorrência',2000);code=bounded_text(communicated,'Identificação comunicada') if communicated else None
    result=incident(db,actor,inv,code,'unknown_material',dict(note=note))
    if inv.status=='draft':inv.version+=1;db.flush();conference_pdf(db,actor,inv)
    return result

@nkt.command
def resolve_incident(db,actor,ident,reason,known_peg=None):
    if actor.role!='admin':raise ReceivingError('Resolver ocorrências exige Administrador.',403)
    original=db.get(Incident,ident)
    if not original or original.resolves_id:raise ReceivingError('Ocorrência original inexistente.',404)
    inv=get_inventory(db,actor,original.inventory_id,True);reason=bounded_text(reason,'Decisão administrativa',2000)
    old=db.scalar(select(Incident.id).where(Incident.resolves_id==ident))
    if old:return old
    pallet=resolve(db,known_peg,full=True) if known_peg else None
    result=incident(db,actor,inv,known_peg or None,'resolved',dict(reason=reason,pallet_id=pallet.id if pallet else None,original=ident),resolves=ident)
    if inv.status=='draft':inv.version+=1;db.flush();conference_pdf(db,actor,inv)
    return result

@nkt.command
def review(db,actor,ident,version,key,reason):
    inv=get_inventory(db,actor,ident,True);open_inventory(inv,version);reason=bounded_text(reason,'Motivo da aprovação',2000);payload=summary(db,inv)
    if payload['blocking']:raise ReceivingError('Inventário incompleto ou desatualizado: '+' '.join(payload['blocking']))
    return review_record(db,actor,inv,key,'confirm',payload,reason).id

def operation(db,actor,inv,rev,key,kind,payload,snapshot,original=None):
    op=Op(inventory_id=inv.id,inventory_review_id=rev.id,request_key=request_key(key),kind=kind,payload=nkt.dump(payload),snapshot=nkt.dump(snapshot),corrects_id=original,actor=actor.username)
    db.add(op);db.flush()
    for row in snapshot['events']:db.add(Event(operation_id=op.id,**row))
    db.flush();return op

@nkt.command
def confirm(db,actor,ident,review_id,digest,key,accepted):
    inv=get_inventory(db,actor,ident,True);request=dict(review=review_id,digest=digest);key=request_key(key)
    if not accepted:raise ReceivingError('Administrador deve aceitar expressamente todos os descontos revistos.',403)
    old=db.scalar(select(Op).where(Op.request_key==key))
    if old:
        if old.kind!='inventory_missing' or old.inventory_id!=ident or old.payload!=nkt.dump(request):raise ReceivingError('Chave repetida com efeitos diferentes.')
        return old.id
    rev=db.get(Review,review_id)
    if not rev or rev.inventory_id!=ident or rev.kind!='confirm' or rev.digest!=digest or nkt.digest(rev.payload)!=digest:raise ReceivingError('Revisão/digest inválido.')
    if inv.status=='confirmed':
        existing=db.scalar(select(Op).where(Op.inventory_id==ident,Op.kind=='inventory_missing'))
        if existing and existing.inventory_review_id==review_id:return existing.id
        raise ReceivingError('Inventário já confirmado; não descontar novamente.')
    open_inventory(inv,rev.version);fresh=summary(db,inv)
    if fresh['blocking'] or nkt.dump(fresh)!=rev.payload:raise ReceivingError('Stock/reservas/conferências mudaram; nova conferência e revisão obrigatórias.')
    changes=[loading.event(p['id'],p['event'],'missing') for row in fresh['groups'] if row['decision']=='not_located' and not row['source']['excluded'] for p in row['source']['pallets'] if p['state']=='available']
    snapshot=dict(inventory_id=ident,review=review_id,report=fresh,events=changes)
    op=operation(db,actor,inv,rev,key,'inventory_missing',request,snapshot);inv.status='confirmed';db.flush();document(db,actor,inv,snapshot,'result',operation=op.id,reason=rev.reason)
    loading.audit(db,actor,'INVENTARIO_AJUSTAR_FALTA',dict(operation=op.id,reason=rev.reason,**snapshot));return op.id

def restoration(db,inv,original,communicated,all_load,requested,reason,full_peg=False):
    if original.kind!='inventory_missing' or original.inventory_id!=inv.id:raise ReceivingError('Referenciar o ajuste original deste inventário.')
    pallet=resolve(db,communicated,full=full_peg);scope=json.loads(inv.scope)
    if pallet.entry_id not in scope:raise ReceivingError('Carga encontrada fora do inventário original.')
    original_ids={e['pallet_id'] for e in json.loads(original.snapshot)['events']};catalog=groups(db);g=catalog[pallet.entry_id]
    if g['reserved']:raise ReceivingError('Carga com reservas ativas; reconciliação explícita antes de recuperar stock.')
    if pallet.id not in original_ids:raise ReceivingError('O PEG comunicado não pertence ao ajuste original.')
    requested_ids={i['pallet_id'] for i in loading.identify(db,requested)} if not all_load else original_ids & {p['id'] for p in g['pallets']}
    if not requested_ids.issubset({p['id'] for p in g['pallets']}) or pallet.id not in requested_ids:raise ReceivingError('Restituição deve incluir o PEG comunicado e apenas a carga identificada.')
    selected=[p for p in g['pallets'] if p['id'] in requested_ids]
    if not selected or len(selected)>60 or any(p['id'] not in original_ids or p['state']!='missing' or p['operation']!=original.id or p['id'] not in {x['id'] for x in g['pallets']} for p in selected):raise ReceivingError('PEG já recuperado/movimentado ou fora da carga; restituição bloqueada.')
    from .warehouse_settings import occupied
    from .physical_locations import ensure_exclusive
    effects=[];byloc={}
    for p in selected:
        loc=db.scalar(select(Location).where(Location.codigo==p['local'],Location.active.is_(True)))
        if not loc:raise ReceivingError('Localização original ausente/inativa; reconciliação necessária.')
        ensure_exclusive(db,[loc.codigo]);used=sum(q for _,q in occupied(db,[loc.codigo]));byloc[loc.codigo]=byloc.get(loc.codigo,0)+1
        if loc.capacidade is not None and used+byloc[loc.codigo]>loc.capacidade:raise ReceivingError('Capacidade insuficiente para a restituição comprovada.')
        effects.append(dict(pallet=p,capacity=loc.capacidade,occupied=used))
    return dict(inventory_id=inv.id,original=original.id,communicated=communicated,resolved=pallet.id,all_load=all_load,full_peg=bool(full_peg),reason=reason,source=g,effects=effects)

@nkt.command
def review_restore(db,actor,ident,original_id,key,communicated,all_load,requested,reason,full_peg=False):
    inv=get_inventory(db,actor,ident,True)
    if inv.status!='confirmed':raise ReceivingError('Confirmar o inventário original antes de compensar.')
    reason=bounded_text(reason,'Motivo e prova de recuperação',2000);original=db.get(Op,original_id)
    if not original:raise ReceivingError('Ajuste original inexistente.',404)
    try:payload=restoration(db,inv,original,communicated,all_load,requested,reason,full_peg)
    except IdentificationError as error:
        report=incident(db,actor,inv,communicated,error.kind,dict(message=str(error),candidates=error.candidates,reason=reason))
        return dict(incident=report)
    return review_record(db,actor,inv,key,'restore',payload,reason,original_id).id

@nkt.command
def apply_restore(db,actor,review_id,digest,key,accepted):
    if actor.role!='admin' or not accepted:raise ReceivingError('Administrador deve aceitar a restituição comprovada.',403)
    rev=db.get(Review,review_id)
    if not rev or rev.kind!='restore':raise ReceivingError('Revisão de restituição inexistente.',404)
    inv=get_inventory(db,actor,rev.inventory_id,True);key=request_key(key);request=dict(review=review_id,digest=digest)
    old=db.scalar(select(Op).where(Op.inventory_review_id==review_id))
    if old:
        if old.payload!=nkt.dump(request):raise ReceivingError('Restituição já aplicada com outro digest.')
        return old.id
    if rev.digest!=digest or nkt.digest(rev.payload)!=digest:raise ReceivingError('Digest inválido.')
    payload=json.loads(rev.payload);original=db.get(Op,rev.original_id)
    fresh=restoration(db,inv,original,payload['communicated'],payload['all_load'],[e['pallet']['peg'] for e in payload['effects']],rev.reason,payload.get('full_peg',False))
    if nkt.dump(fresh)!=rev.payload:raise ReceivingError('Estado/ocupação mudaram desde a revisão; reconferir antes de repor stock.')
    changes=[loading.event(e['pallet']['id'],e['pallet']['event'],'available') for e in fresh['effects']];snapshot=dict(inventory_id=inv.id,review=review_id,report=fresh,events=changes)
    op=operation(db,actor,inv,rev,key,'inventory_restore',request,snapshot,original.id);document(db,actor,inv,snapshot,'restored',operation=op.id,reason=rev.reason);loading.audit(db,actor,'INVENTARIO_COMPENSAR',dict(operation=op.id,**snapshot));return op.id
