"""Explicit historical baseline. No automatic file discovery or production import."""
from collections import defaultdict
from copy import deepcopy
from hashlib import sha256
import json,math
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import BadZipFile
from xml.etree.ElementTree import ParseError
from defusedxml.common import DefusedXmlException
from types import SimpleNamespace
from sqlalchemy import select,func,text
from . import valmet as v,valmet_preview as preview,valmet_rolls as rolls
from . import database as m
from .migrations import REVISION
from .transfer import fingerprint
from .contexts import require_context
from .physical_locations import physical_code
from .receiving import ReceivingError,request_key

MAX_REPORT=32*1024*1024
MAX_UNITS=2000

def dump(data):return json.dumps(data,ensure_ascii=False,sort_keys=True,allow_nan=False)
def admin(db,actor):
    if not actor or not actor.active or actor.role!='admin' or not v.enabled(db):raise ReceivingError('Só Administrador no contexto Valmet.',403)
    if db.bind.dialect.name!='postgresql':raise ReceivingError('Importação exige PostgreSQL.',503)
    if db.scalars(text('SELECT version_num FROM alembic_version')).all()!=[REVISION]:raise ReceivingError('Atualizar e verificar a versão da base antes de importar.',503)

def snapshot(db):
    ctx,owner,wh=require_context(db)
    places=db.scalars(select(m.Location).order_by(m.Location.codigo)).all()
    claims={c.code:c.context_id for c in db.scalars(select(m.PhysicalLocationClaim).where(m.PhysicalLocationClaim.warehouse_id==wh.id))}
    used=defaultdict(int)
    for model,col,qty in ((m.Stock,m.Stock.local,m.Stock.quantidade),(m.ValmetPosition,m.ValmetPosition.local,m.ValmetPosition.physical_quantity)):
        for loc,q in db.execute(select(col,func.sum(qty)).group_by(col)):used[loc]+=q
    cat=dict(context_id=ctx.id,is_valmet=True,issued_numbers=sorted(db.scalars(select(m.ValmetNumber.number).where(m.ValmetNumber.client_id==owner.id))),
        locations={p.codigo:dict(active=p.active,capacity=p.capacidade,occupied=used[p.codigo],exclusive=physical_code(p.codigo) is None or claims.get(physical_code(p.codigo))==ctx.id) for p in places})
    state=dict(catalogue=cat,context=[ctx.id,owner.id,wh.id,ctx.active,owner.active,wh.active],claims=sorted(claims.items()))
    for model in (m.ValmetUnit,m.ValmetIdentification,m.ValmetItem,m.ValmetShipment,m.ValmetControlMove,m.ValmetRollEvent,m.ValmetCount,m.ValmetInventoryDecision):
        state[model.__tablename__]=list(db.execute(select(func.count(model.id),func.max(model.id))).one())
    for model,cols in ((m.ValmetPosition,('id','unit_id','local','physical_quantity','materials','uncertain')),(m.ValmetPreparation,('id','version','status','active_revision')),(m.ValmetInventory,('id','version','status'))):
        state[model.__tablename__]=[list(row) for row in db.execute(select(*(getattr(model,k) for k in cols)).order_by(model.id))]
    state['stocks']=[list(row) for row in db.execute(select(m.Stock.id,m.Stock.artigo,m.Stock.lote,m.Stock.carga,m.Stock.local,m.Stock.quantidade).order_by(m.Stock.id))]
    state['baseline']=db.scalar(select(m.ValmetImportConfirmation.id))
    return cat,fingerprint(state)

def preview_file(db,actor_id,key,content,*,reauthorize=None):
    actor=db.get(m.User,actor_id);admin(db,actor);key=request_key(key)
    if not isinstance(content,bytes) or not content or len(content)>preview.MAX_FILE:raise ReceivingError('Excel vazio ou demasiado grande.',400)
    digest=sha256(content).hexdigest()
    existing=db.scalar(select(m.ValmetImportDraft).where(m.ValmetImportDraft.request_key==key))
    if existing:
        if existing.digest!=digest:raise ReceivingError('Pedido de prévia repetido com outro ficheiro.')
        return existing.id
    cat,token=snapshot(db);scope=m.context_id(db);db.rollback() # no write lock during ZIP/XML analysis
    try:
        with TemporaryDirectory(prefix='ddn-valmet-preview-') as folder:
            from .private_files import private_folder
            private_folder(folder)
            path=Path(folder)/'source.xlsx';path.write_bytes(content)
            report=preview.analyze(path,scope,cat,include_source=True)
    except (ValueError,KeyError,TypeError,BadZipFile,ParseError,DefusedXmlException) as error:raise ReceivingError('Excel recusado: '+str(error),400) from error
    if reauthorize:reauthorize()
    report['reference']=token
    return save_preview(db,actor_id,key,digest,dump(report))

@v.command
def save_preview(db,actor,key,digest,report):
    admin(db,actor)
    if len(report.encode())>MAX_REPORT:raise ReceivingError('Relatório demasiado grande; resolver o âmbito sem truncagem.',400)
    existing=db.scalar(select(m.ValmetImportDraft).where(m.ValmetImportDraft.request_key==key))
    if existing:
        if existing.digest!=digest:raise ReceivingError('Pedido repetido com outro ficheiro.')
        return existing.id
    rec=m.ValmetImportDraft(request_key=key,digest=digest,report=report,actor=actor.username);db.add(rec);db.flush()
    db.add(m.Audit(username=actor.username,action='VALMET_IMPORT_PREVIA',details=dump(dict(draft=rec.id,sha256=digest))))
    return rec.id

def corrected(source,decisions):
    allowed={'rows','historical_numbers','m2_totals','warnings'}
    if not isinstance(decisions,dict) or set(decisions)-allowed:raise ReceivingError('Decisões de reconciliação inválidas.',400)
    for name in ('historical_numbers','warnings'):
        values=decisions.get(name,[])
        if not isinstance(values,list) or any(not isinstance(x,str) for x in values):raise ReceivingError('Lista de confirmações inválida.',400)
    if not isinstance(decisions.get('m2_totals',{}),dict):raise ReceivingError('Totais m² por referência obrigatórios.',400)
    changes=decisions.get('rows',{})
    if not isinstance(changes,dict):raise ReceivingError('Correções por referência Folha!linha.',400)
    data=deepcopy(source);found=set()
    for sheet,rows in data.items():
        header=rows[0][1]
        for line,raw in rows[1:]:
            ref=f'{sheet}!{line}'
            if ref not in changes:continue
            fields=changes[ref]
            if not isinstance(fields,dict) or set(fields)-set(preview.ALIASES):raise ReceivingError('Campo de correção inválido.',400)
            for field,value in fields.items():
                if value is not None and (isinstance(value,bool) or not isinstance(value,(str,int,float)) or (isinstance(value,float) and not math.isfinite(value))):raise ReceivingError('Correção literal inválida.',400)
                indexes=[i for i,h in enumerate(header) if preview.key(h) in {preview.key(a) for a in preview.ALIASES[field]}]
                if len(indexes)>1:raise ReceivingError('Cabeçalho ambíguo; corrigir uma cópia do Excel.',400)
                if not indexes:header.append(preview.ALIASES[field][0]);indexes=[len(header)-1]
                index=indexes[0]
                raw.extend([None]*max(0,index+1-len(raw)));raw[index]=value
            found.add(ref)
    if set(changes)!=found:raise ReceivingError('Referência de linha inexistente.',400)
    return data

def build_plan(db,draft,decisions,reason):
    original=json.loads(draft.report);cat,reference=snapshot(db)
    # Issued identities cannot be laundered by renaming conflicting Excel rows.
    raw=preview.analyze_rows(original['source_sheets'],m.context_id(db),cat)
    report=preview.analyze_rows(corrected(original['source_sheets'],decisions),m.context_id(db),cat)
    issues=list(report['issues']);historical=set(decisions.get('historical_numbers',[]));anchors={u['refs'][0] for u in report['units']}
    if not (historical|set(decisions.get('m2_totals',{})))<=anchors:raise ReceivingError('Confirmação de identidade sem unidade correspondente.',400)
    for issue in raw['issues']:
        if issue['code']=='NUMERO_JA_EMITIDO' and issue not in issues:issues.append(issue)
    if db.scalar(select(m.ValmetImportConfirmation.id)):issues.append(dict(code='BASE_HISTORICA_JA_CONFIRMADA',severity='error',refs=[]))
    if db.scalar(select(m.ValmetPosition.id).where(m.ValmetPosition.physical_quantity>0).limit(1)):
        issues.append(dict(code='STOCK_EXISTENTE_EXIGE_PROVA_DE_NAO_DUPLICACAO',severity='warning',refs=[]))
    original_cells={f'{sheet}!{line}':dict(sheet=sheet,row=line,cells=cells) for sheet,lines in original['source_sheets'].items() for line,cells in lines[1:]}
    rows=[];origins=[];totals=defaultdict(int);materials=defaultdict(int)
    for u in report['units']:
        anchor=u['refs'][0]
        try:
            if u['number'] and u['kind'] in v.GROUPED:
                if anchor not in historical:raise ReceivingError('Confirmar representação e preservação do N.º Armazém histórico.')
                if u['physical']!=1:raise ReceivingError('Número individual exige uma única unidade física comprovada.')
                issues=[i for i in issues if not (i['code']=='IDENTIDADE_HISTORICA_EXIGE_ADAPTACAO_MODELO' and set(i['refs'])<=set(u['refs']))]
            items=[]
            for c in u['contents']:
                if u['kind']=='vasilhame':continue
                q=c['quantity']
                if u['kind'] in ('paletes','prateleiras','fio'):q=c['physical_quantity']
                if q is None and u['number'] and u['kind'] in ('prateleiras','fio') and len(u['contents'])==1:q=1
                items.append((c['article'],c['lot'],q))
            m2=[c['m2'] for c in u['contents'] if c['m2'] is not None]
            if len(m2)>1 and anchor not in decisions.get('m2_totals',{}):raise ReceivingError('Confirmar total m²; múltiplas declarações não são somadas automaticamente.')
            row=dict(kind=u['kind'],number=u['number'],local=u['local'],physical_quantity=u['physical'],items=items,
                content_total=sum(c['quantity'] for c in u['contents']) if u['kind'] in ('pecas','rolos') and all(c['quantity'] is not None for c in u['contents']) else None,
                material_m2=decisions.get('m2_totals',{}).get(anchor,m2[0] if m2 else None),brand=u['brand'],notes='\n'.join(c['ref']+': '+c['notes'] for c in u['contents'] if c['notes']),supplier='',guide='',pending=False)
            if u['kind']=='rolos':row['items']=[];row['rolls']=[dict(article=c['article'],lot=c['lot'],divisible=c['divisible']) for c in u['contents']]
            clean=v.clean_rows([row],historical_numbers=True)[0]
            if clean['local']:
                matches=[loc for loc in cat['locations'] if loc.casefold()==clean['local'].casefold()]
                if len(matches)!=1:raise ReceivingError('Localização sem correspondência única no catálogo.')
                clean['local']=matches[0]
            rows.append(clean);origins.append(dict(refs=u['refs'],originals=[original_cells[ref] for ref in u['refs']],corrected_cells=u['originals'],inferred=dict(physical='Presença única por N.º Armazém' if u['number'] else 'Quantidade literal de paletes',location=[c['local_source'] for c in u['contents']])))
            totals[u['kind']]+=clean['physical_quantity']
            from .valmet_planning import materials_for
            for lot,q in materials_for(SimpleNamespace(**clean),[SimpleNamespace(article=a,lot=l,quantity=q) for a,l,q in clean['items']]).items():materials[(clean['kind'],lot,clean['local'])]+=q or 0
        except (ReceivingError,TypeError,ValueError) as error:issues.append(dict(code='MODELO_INCOMPATIVEL: '+str(error),severity='error',refs=u['refs']))
    if not rows or len(report['units'])>MAX_UNITS:issues.append(dict(code='AMBITO_VAZIO_OU_EXCESSIVO',severity='error',refs=[]))
    warnings={i['code'] for i in issues if i['severity']=='warning'}
    if not warnings<=set(decisions.get('warnings',[])):issues.append(dict(code='AVISOS_EXIGEM_ACEITACAO_EXPLICITA',severity='error',refs=[]))
    return dict(source_digest=draft.digest,reference=reference,decisions=decisions,reason=reason,report=report,issues=issues,
        blocking=sum(i['severity']=='error' for i in issues),acknowledged=warnings<=set(decisions.get('warnings',[])),
        rows=rows,origins=origins,existing_positions=[list(row) for row in db.execute(select(m.ValmetPosition.id,m.ValmetPosition.unit_id,m.ValmetPosition.local,m.ValmetPosition.physical_quantity,m.ValmetPosition.materials).where(m.ValmetPosition.physical_quantity>0).order_by(m.ValmetPosition.id))],physical=dict(totals),materials=[dict(kind=k,lot=l,local=loc,quantity=q) for (k,l,loc),q in materials.items()])

@v.command
def reconcile(db,actor,ident,key,decisions,reason):
    admin(db,actor);reason=v.optional(reason,2000)
    if not reason:raise ReceivingError('Motivo e comprovação da reconciliação obrigatórios.',400)
    key=request_key(key);draft=db.get(m.ValmetImportDraft,ident)
    if not draft:raise ReceivingError('Prévia inexistente neste contexto.',404)
    existing=db.scalar(select(m.ValmetImportRevision).where(m.ValmetImportRevision.request_key==key))
    if existing:
        old=json.loads(existing.plan)
        if existing.draft_id!=ident or old['decisions']!=decisions or old['reason']!=reason:raise ReceivingError('Revisão repetida com dados diferentes.')
        return existing.id
    plan=build_plan(db,draft,decisions,reason);data=dump(plan)
    if len(data.encode())>MAX_REPORT:raise ReceivingError('Plano demasiado grande; não truncado.',400)
    rec=m.ValmetImportRevision(draft_id=ident,request_key=key,plan=data,digest=fingerprint(plan),actor=actor.username);db.add(rec);db.flush()
    db.add(m.Audit(username=actor.username,action='VALMET_IMPORT_RECONCILIAR',details=dump(dict(revision=rec.id,sha256=draft.digest,decisions=decisions,reason=reason))))
    return rec.id

@v.command
def confirm(db,actor,ident,key,expected_digest,accepted=False):
    admin(db,actor);key=request_key(key)
    rec=db.get(m.ValmetImportRevision,ident)
    if not rec:raise ReceivingError('Revisão inexistente neste contexto.',404)
    if rec.digest!=expected_digest or not accepted:raise ReceivingError('Rever e confirmar explicitamente o plano/digest.')
    draft=db.get(m.ValmetImportDraft,rec.draft_id);ctx,owner,wh=require_context(db)
    existing=db.scalar(select(m.ValmetImportConfirmation).where(m.ValmetImportConfirmation.digest==draft.digest))
    if existing:return existing.id
    if db.scalar(select(m.ValmetImportConfirmation.id)):raise ReceivingError('Base histórica já confirmada; não importar outro ficheiro incremental.')
    if db.scalar(select(func.max(m.ValmetImportRevision.id)).where(m.ValmetImportRevision.draft_id==draft.id))!=rec.id:raise ReceivingError('Revisão desatualizada; rever a última reconciliação.')
    plan=json.loads(rec.plan)
    if plan['blocking']:raise ReceivingError('Resolver todos os bloqueios e aceitar avisos antes de confirmar.')
    if snapshot(db)[1]!=plan['reference']:raise ReceivingError('Stock, reservas, catálogo ou contexto mudaram; reconciliar novamente.')
    fresh=build_plan(db,draft,plan['decisions'],plan['reason'])
    if fingerprint(fresh)!=rec.digest:raise ReceivingError('Plano mudou; nova reconciliação obrigatória.')
    entry=m.ValmetEntry(request_key=key,payload=dump(dict(code='Importação histórica '+draft.digest,received_on=None,rows=plan['rows'])),created_by=actor.id)
    db.add(entry);db.flush()
    for index,row in enumerate(plan['rows']):
        values={k:v for k,v in row.items() if k not in ('items','pending','rolls')}
        unit=m.ValmetUnit(entry_id=entry.id,**values);db.add(unit);db.flush()
        v.add_identification(db,actor,unit,sha256(f'import:{rec.id}:{index}'.encode()).hexdigest()[:32],row['items'],row['content_total'],'Importação histórica comprovada; '+', '.join(plan['origins'][index]['refs']))
        if row.get('rolls'):rolls.register(db,actor,unit.id,sha256(f'import-rolls:{rec.id}:{index}'.encode()).hexdigest()[:32],row['rolls'],'Importação histórica comprovada',f'Prévia {draft.id}; revisão {rec.id}; SHA-256 {draft.digest}')
    record=m.ValmetImportConfirmation(revision_id=rec.id,entry_id=entry.id,client_id=owner.id,digest=draft.digest,request_key=key,actor=actor.username)
    db.add(record);db.flush();entry.status='imported';db.flush()
    db.add(m.Audit(username=actor.username,action='VALMET_IMPORT_CONFIRMAR',details=dump(dict(confirmation=record.id,entry=entry.id,revision=rec.id,sha256=draft.digest,physical=plan['physical']))))
    return record.id
