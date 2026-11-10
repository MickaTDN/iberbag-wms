"""Controlled office reception, one existing NKT entry per explicit container."""
import json
from datetime import date,datetime
from hashlib import sha256
from sqlalchemy import select,func
from . import nkt
from .nkt_excel import analyze,expand,MAX_PALLETS,MAX_CONTAINERS
from .database import User,NktReceiving as Receiving,NktReceivingReview as Review,NktContainer as Container,NktEntry,Location,Carga,Audit,StorageContext,LoadNumber
from .contexts import require_context
from .receiving import ReceivingError,request_key

def office(db,actor,admin=False):
    ctx,owner,warehouse=require_context(db)
    if warehouse.name!='Escapães' or (admin and actor.role!='admin'):raise ReceivingError('Receção NKT exige Escapães; Excel exige Administrador.',403)
    return ctx,owner

def bounded_text(value,label,maximum=120):
    if not isinstance(value,str) or not value.strip() or value!=value.strip() or len(value)>maximum or any(ord(c)<32 for c in value):raise ReceivingError(label+': indicar texto comprovado, sem espaços exteriores ou controlos.',400)
    return value

@nkt.command
def create(db,actor,key,source_name,report,source_digest=None):
    ctx,owner=office(db,actor,source_digest is not None);key=request_key(key)
    source_name=bounded_text(source_name,'Origem',240)
    if source_digest and (report.get('sha256')!=source_digest or len(source_digest)!=64):raise ReceivingError('Digest da origem incompatível.',400)
    report_text=nkt.dump(report)
    if len(report_text.encode())>1024*1024:raise ReceivingError('Relatório excede 1 MiB; dividir a listagem antes da prévia.',400)
    old=db.scalar(select(Receiving).where(Receiving.request_key==key))
    if old:
        if old.created_by!=actor.id or old.report!=report_text or old.source_digest!=source_digest:raise ReceivingError('Pedido repetido com origem diferente.')
        return old.id
    if source_digest:
        # Owner-wide duplicate detection, never return an ID from another context.
        row=db.connection().execute(select(Receiving.__table__).where(Receiving.__table__.c.client_id==owner.id,Receiving.__table__.c.source_digest==source_digest)).mappings().first()
        if row:
            if row['context_id']!=ctx.id:raise ReceivingError('Ficheiro já registado noutro contexto NKT.')
            return row['id']
    rec=Receiving(client_id=owner.id,request_key=key,source_name=source_name,source_digest=source_digest,report=report_text,created_by=actor.id)
    db.add(rec);db.flush();db.add(Audit(username=actor.username,action='NKT_ORIGEM',details=nkt.dump(dict(receiving=rec.id,sha256=source_digest,source=source_name,report=report))));return rec.id

def preview_file(db,actor,key,content,name,reauthorize=None):
    # Parse outside the global lock; recheck both direct callers and HTTP callbacks.
    if db.bind.dialect.name!='postgresql':raise ReceivingError('Operações NKT exigem PostgreSQL.',503)
    user=db.get(User,actor)
    if not user or not user.active or user.role!='admin' or not nkt.enabled(db):raise ReceivingError('Excel NKT exige Administrador ativo.',403)
    office(db,user,True);request_key(key);db.rollback()
    try:report=analyze(content)
    except ValueError as error:raise ReceivingError(str(error),400) from error
    if reauthorize:reauthorize()
    return create(db,actor,key,name,report,report['sha256'])

def plan_rows(source,rows):
    if not isinstance(rows,list) or not 1<=len(rows)<=300:raise ReceivingError('Indicar 1–300 linhas de distribuição.',400)
    source_rows={r['ref']:r for r in source.get('records',[])}
    excluded={p.casefold() for r in source.get('excluded',[]) for p in r.get('pegs',[])}
    seen_refs=set();seen_pegs=set();groups={};by_ref={}
    for index,row in enumerate(rows):
        if not isinstance(row,dict):raise ReceivingError('Distribuição inválida.',400)
        ref=row.get('ref') or f'Manual!{index+1}'
        if source_rows and ref not in source_rows:raise ReceivingError('Linha de origem inexistente; não remover referências Excel.',400)
        seen_refs.add(ref)
        name=bounded_text(row.get('container'),'Contentor');material=bounded_text(row.get('material'),'Material',500)
        day=row.get('received_on')
        try:
            if not isinstance(day,str) or date.fromisoformat(day).isoformat()!=day:raise ValueError()
        except (ValueError,TypeError):raise ReceivingError('Data efetiva de chegada obrigatória (AAAA-MM-DD), sem data inventada.',400)
        if row.get('first') or row.get('last'):
            if row.get('pegs'):raise ReceivingError('Usar PEG individuais ou intervalo, nunca ambos.',400)
            try:pegs=expand(row.get('first'),row.get('last'))
            except ValueError as error:raise ReceivingError(str(error),400) from error
        else:
            pegs=row.get('pegs')
            if isinstance(pegs,str):pegs=pegs.splitlines()
        # Reuse all PEG/raw-text validation and per-container technical limit.
        nkt.payload(day,'',[dict(peg=p,local='Chão') for p in pegs] if isinstance(pegs,list) else [])
        group=groups.setdefault(name.casefold(),dict(container=name,received_on=day,items=[],intervals=[]))
        if group['container']!=name or group['received_on']!=day:raise ReceivingError('Contentor repetido com grafia/data contraditória.')
        first=row.get('first');last=row.get('last')
        if not first and ref in source_rows and source_rows[ref].get('initial') and pegs==source_rows[ref]['pegs']:
            first=source_rows[ref]['initial'];last=source_rows[ref]['final']
        if first:group['intervals'].append(dict(ref=ref,first=first,last=last))
        by_ref.setdefault(ref,set()).update(pegs)
        for peg in pegs:
            if peg.casefold() in excluded:raise ReceivingError('CQ — Não Vai não pode ser recebido.')
            if peg.casefold() in seen_pegs:raise ReceivingError('PEG repetido na distribuição, incluindo maiúsculas/minúsculas.')
            seen_pegs.add(peg.casefold());group['items'].append(dict(peg=peg,material=material,ref=ref))
        if len(group['items'])>60:raise ReceivingError('Limite técnico de 60 paletes por contentor; não implica uma quantidade comercial fixa.',400)
    for ref,record in source_rows.items():
        if record['pegs'] and by_ref.get(ref)!=set(record['pegs']):raise ReceivingError('A distribuição deve preservar todos os PEG originais da linha '+ref+'.')
    if source_rows and seen_refs!=set(source_rows):raise ReceivingError('Todas as linhas elegíveis exigem distribuição explícita; não omitir paletes.')
    if len(groups)>MAX_CONTAINERS or len(seen_pegs)>MAX_PALLETS:raise ReceivingError('Limites técnicos: 20 contentores e 300 paletes por receção.',400)
    return list(groups.values())

def check(db,groups,create_floor=False):
    floor=db.scalar(select(Location).where(Location.codigo=='Chão'))
    if floor is None and create_floor:
        db.add(Location(codigo='Chão',capacidade=None,active=True));db.flush()
    elif floor is not None and (not floor.active or floor.capacidade is not None):raise ReceivingError('Catálogo Chão inativo/inconsistente; requer resolução administrativa.')
    if floor is None and not create_floor:
        ctx,owner,wh=require_context(db)
        issued=set(db.connection().scalars(select(nkt.NktPallet.__table__.c.peg).where(nkt.NktPallet.__table__.c.client_id==owner.id)))
        if {p.casefold() for p in issued}&{i['peg'].casefold() for g in groups for i in g['items']}:raise ReceivingError('PEG já emitido; nunca reutilizável.')
    else:
        for group in groups:nkt.validate(db,dict(rows=[dict(peg=i['peg'],local='Chão') for i in group['items']]))

@nkt.command
def review(db,actor,ident,version,rows,reason):
    rec=db.get(Receiving,ident)
    if not rec:raise ReceivingError('Receção inexistente neste contexto.',404)
    office(db,actor,rec.source_digest is not None)
    if rec.created_by!=actor.id or rec.status!='draft':raise ReceivingError('Só o autor revê a receção em rascunho.',403)
    reason=bounded_text(reason,'Evidência/decisão',2000)
    groups=plan_rows(json.loads(rec.report),rows);encoded=nkt.dump(groups)
    current=db.scalar(select(Review).where(Review.receiving_id==ident,Review.version==rec.version))
    if current and rec.version==version+1 and current.payload==encoded and current.reason==reason:return current.id
    if rec.version!=version:raise ReceivingError('Revisão desatualizada; recarregar e conferir.')
    check(db,groups);rec.version+=1;db.flush()
    revision=Review(receiving_id=ident,version=rec.version,payload=encoded,digest=nkt.digest(encoded),reason=reason,actor=actor.username);db.add(revision);db.flush()
    db.add(Audit(username=actor.username,action='NKT_DISTRIBUIR',details=nkt.dump(dict(receiving=ident,review=revision.id,version=rec.version,reason=reason,plan=groups))));return revision.id

def load_reference(db,entry,group):
    ctx,owner,wh=require_context(db);day=date.fromisoformat(group['received_on'])
    if day.year<2027:return f'NKT-E{entry}',None,None
    issued=db.connection().scalar(select(func.max(Container.__table__.c.number)).where(Container.__table__.c.client_id==owner.id,Container.__table__.c.year==day.year)) or 0
    issued=max(issued,db.scalar(select(func.max(LoadNumber.number)).where(LoadNumber.client_id==owner.id,LoadNumber.year==day.year)) or 0)
    contexts=select(StorageContext.id).where(StorageContext.client_id==owner.id)
    codes=set(db.connection().scalars(select(Carga.__table__.c.codigo).where(Carga.__table__.c.context_id.in_(contexts))))
    number=issued+1
    while f'{day.year%100:02d}-{number:03d}' in codes:number+=1
    if number>2147483647:raise ReceivingError('Sequência anual esgotada; requer revisão administrativa.')
    return f'{day.year%100:02d}-{number:03d}',day.year,number

@nkt.command
def confirm(db,actor,ident,review_id,expected_digest,accepted):
    rec=db.get(Receiving,ident);revision=db.get(Review,review_id)
    if not rec or not revision or revision.receiving_id!=ident:raise ReceivingError('Receção/revisão inexistente neste contexto.',404)
    office(db,actor,rec.source_digest is not None)
    if rec.created_by!=actor.id or not accepted:raise ReceivingError('O autor deve aceitar explicitamente a distribuição revista.',403)
    if rec.version!=revision.version or revision.digest!=expected_digest or nkt.digest(revision.payload)!=expected_digest:raise ReceivingError('Revisão/digest desatualizados.')
    if rec.status=='confirmed':return ident
    groups=json.loads(revision.payload);check(db,groups,create_floor=True)
    for group in groups:
        key=sha256(f'nkt:{ident}:{revision.version}:{group["container"]}'.encode()).hexdigest()[:32]
        entry=nkt.save.__wrapped__(db,actor,key,group['received_on'],f'Receção {ident}; contentor {group["container"]}',[dict(peg=i['peg'],local='Chão') for i in group['items']])
        record=db.get(NktEntry,entry);nkt.confirm.__wrapped__(db,actor,entry,record.version,nkt.digest(record.payload))
        code,year,number=load_reference(db,entry,group)
        carga=Carga(codigo=code,entrada=datetime.combine(date.fromisoformat(group['received_on']),datetime.min.time()),funcionario=actor.username);db.add(carga);db.flush()
        db.add(Container(receiving_id=ident,review_id=review_id,entry_id=entry,carga_id=carga.id,client_id=rec.client_id,name=group['container'],payload=nkt.dump(group),year=year,number=number,actor=actor.username));db.flush()
    rec.status='confirmed';db.flush()
    db.add(Audit(username=actor.username,action='NKT_RECEBER_CONTENTORES',details=nkt.dump(dict(receiving=ident,review=review_id,digest=expected_digest,containers=len(groups),paletes=sum(len(g['items']) for g in groups)))));return ident

def metadata(db,entry_ids):
    return {c.entry_id:dict(json.loads(c.payload),receiving=c.receiving_id,carga=db.get(Carga,c.carga_id).codigo) for c in db.scalars(select(Container).where(Container.entry_id.in_(entry_ids)))}
