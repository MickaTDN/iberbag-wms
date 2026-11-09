"""Office stock control: evidence first, immutable movements, no projection DML."""
import json
import re
import hashlib
from collections import defaultdict
from sqlalchemy import select,func,text,or_
from . import valmet as v,valmet_planning as p,valmet_balances as b
from .database import (context_id,ValmetUnit as Unit,ValmetPosition as Position,ValmetItem,ValmetIdentification,Location,Audit,
    ValmetInventory as Inventory,ValmetCount as Count,ValmetControlDraft as Draft,ValmetControlMove as Move,
    ValmetInventoryDecision as Decision,ValmetControlDocument as Document,ValmetShipmentLine as ShipmentLine,ValmetOccurrence)
from .receiving import ReceivingError,request_key

def note(value,label):
    value=v.optional(value,2000)
    if not value.strip():raise ReceivingError(label+' obrigatório.',400)
    return value

def number(value,unknown=False):
    if value in ('',None) and unknown:return None
    if isinstance(value,bool) or not re.fullmatch(r'[0-9]+',str(value).strip()):raise ReceivingError('Indicar quantidade inteira; vazio é desconhecido, não zero.',400)
    value=int(value)
    if not 0<=value<=2147483647:raise ReceivingError('Quantidade fora do limite.',400)
    return value

def materials(value,unknown=False):
    if value in ('',None) and unknown:return None
    if isinstance(value,str):
        if len(value)>16000:raise ReceivingError('Composição demasiado longa.',400)
        def pairs(items):
            result={}
            for k,q in items:
                if k in result:raise ReceivingError('Lote repetido na composição.',400)
                result[k]=q
            return result
        try:value=json.loads(value,object_pairs_hook=pairs)
        except (ValueError,TypeError):raise ReceivingError('Composição JSON inválida. Exemplo: {"LOTE-A": 2}.',400)
    if not isinstance(value,dict) or len(value)>100:raise ReceivingError('Indicar até 100 lotes/referências.',400)
    return {v.required(k,'Lote / referência'):number(q,unknown=True) for k,q in value.items()}

def canonical(content):return {k:q for k,q in content.items() if q is None or q>0}
def individual(unit):return unit.kind in ('rolos','lab','caixas','pecas','vazias') or (unit.kind=='paletes' and unit.physical_quantity==1)
def admin(actor):
    if actor.role!='admin':raise ReceivingError('Só Administrador pode aprovar ou ajustar stock.',403)
def permission(record,actor):
    if not record:raise ReceivingError('Registo inexistente neste contexto.',404)
    if actor.role!='admin' and actor.id!=record.created_by:raise ReceivingError('Sem permissão para alterar este registo.',403)
def journal(db,actor,action,details):db.add(Audit(username=actor.username,action='VALMET_CONTROLO_'+action,details=p.dump(details)))

def reference(db,posid,target,inventory=None):
    result=db.scalar(text('SELECT valmet_control_reference(:pid,:ctx,:dest,:inv)'),dict(pid=posid,ctx=context_id(db),dest=target,inv=inventory))
    if result is None:raise ReceivingError('Posição inexistente neste contexto.',404)
    return result

def destination(db,value,default=None):
    value=v.optional(value) or default
    if value is None:return None
    loc=db.scalar(select(Location).where(func.lower(Location.codigo)==value.lower(),Location.active.is_(True)))
    if not loc:raise ReceivingError('Destino desconhecido/inativo; configurar em Definições.')
    return loc.codigo

def known(db,unit):
    rows=db.scalars(select(ValmetItem).where(ValmetItem.unit_id==unit.id,ValmetItem.identification_id.is_not(None))).all()
    return p.materials_for(unit,rows)

def lookup(db,filters=None,empty=False):
    """Physical, blocked and unknown material are visible; no availability shortcut."""
    filters=filters or {};query=select(Position,Unit).join(Unit,Unit.id==Position.unit_id)
    for key,col in [('local',Position.local),('number',Unit.number),('kind',Unit.kind)]:
        term=filters.get(key,'').strip()
        if term:query=query.where(func.coalesce(col,'Por localizar').icontains(term,autoescape=True))
    for key,col in [('lot',ValmetItem.lot),('article',ValmetItem.article)]:
        term=filters.get(key,'').strip()
        if term:
            condition=select(ValmetItem.id).where(ValmetItem.unit_id==Unit.id,col.icontains(term,autoescape=True)).exists()
            if key=='lot':condition=or_(condition,Unit.brand==term)
            query=query.where(condition)
    candidates=db.execute(query.order_by(Unit.id,Position.id).limit(10001)).all()
    if len(candidates)>10000:raise ReceivingError('Âmbito superior a 10000 posições; restringir filtros.')
    active_units={u.id for z,u in candidates if z.physical_quantity>0 or z.uncertain}
    return [(z,u) for z,u in candidates if z.physical_quantity>0 or z.uncertain or (empty and z.root and u.id not in active_units)]

def scope_data(mode,local='',kind='',lot='',number_filter=''):
    if mode not in ('partial','complete'):raise ReceivingError('Tipo de inventário inválido.')
    if kind and kind not in v.KINDS:raise ReceivingError('Categoria inválida.')
    values=dict(mode=mode,local=v.optional(local),kind=kind,lot=v.optional(lot),number=v.optional(number_filter).upper())
    if mode=='complete' and any(values[k] for k in ('local','kind','lot','number')):raise ReceivingError('Inventário completo abrange todo o contexto; usar parcial para filtros.')
    return values

def targets(db,scope):
    rows=lookup(db,scope,empty=bool(scope['number']));result={}
    locations=db.scalars(select(Location).where(Location.active.is_(True)).order_by(Location.codigo)).all()
    # Scope calculation runs for each saved count: batch composition/identity
    # reads so a full inventory does not issue two queries per physical bucket.
    unit_ids={u.id for z,u in rows};items=defaultdict(list)
    if unit_ids:
        for item in db.scalars(select(ValmetItem).where(ValmetItem.unit_id.in_(unit_ids),ValmetItem.identification_id.is_not(None))):items[item.unit_id].append(item)
        identified=set(db.scalars(select(ValmetIdentification.unit_id).where(ValmetIdentification.unit_id.in_(unit_ids))))
    else:identified=set()
    for z,u in rows:
        original=p.materials_for(u,items[u.id]);current=json.loads(z.materials);keys=sorted(set(current)|set(original))
        if scope['lot']:
            keys=sorted(k for k in set(current)|set(original) if scope['lot'].casefold() in k.casefold())
        expected={k:current.get(k,None if z.uncertain else 0) for k in keys}
        physical=z.physical_quantity if individual(u) or not scope['lot'] else sum(q or 0 for q in expected.values())
        result[f'P:{z.id}']=dict(key=f'P:{z.id}',position=z.id,unit=u.id,kind=u.kind,number=u.number,local=z.local,
            physical=physical,materials=expected,partial=bool(scope['lot']),keys=keys,source=json.loads(z.materials),source_physical=z.physical_quantity,
            individual=individual(u),grouped=not individual(u),
            blocked=z.uncertain or u.legacy_unnumbered,legacy=u.legacy_unnumbered,identified=u.id in identified)
    if scope['mode']=='complete':places=[l.codigo for l in locations]
    elif scope['local']:places=[l.codigo for l in locations if scope['local'].casefold() in l.codigo.casefold()]
    else:places=sorted({z.local for z,u in rows if z.local is not None})
    if any(z.local is None for z,u in rows):places.append(None)
    for loc in places:
        key='L:'+(loc or 'Por localizar');result[key]=dict(key=key,position=None,local=loc,kind='location',number=None,physical=None,materials=None)
    return result

def initial_reference(db,scope,ident):
    selected=targets(db,scope)
    position_ids=[t['position'] for t in selected.values() if t['position']]
    positions={str(pid):state for pid,state in db.execute(text('SELECT id,valmet_control_position_state(id,:ctx,:inv) FROM valmet_positions WHERE context_id=:ctx AND id=ANY(:ids)'),dict(ctx=context_id(db),inv=ident,ids=position_ids))} if position_ids else {}
    catalog={l.codigo:dict(id=l.id,context_id=l.context_id,codigo=l.codigo,capacidade=l.capacidade,active=l.active) for l in db.scalars(select(Location))}
    return dict(targets=selected,positions=positions,catalogues=catalog)

def inventory_targets(db,record):
    initial=json.loads(record.reference)['targets'];current=targets(db,json.loads(record.scope))
    handled={(m.unit_id,json.loads(d.payload)['target']) for m,d in db.execute(select(Move,Draft).join(Draft,Draft.id==Move.draft_id).where(Draft.inventory_id==record.id))}
    return dict(initial,**{k:t for k,t in current.items() if k not in initial and (t.get('unit'),t.get('local')) not in handled})

def latest_counts(db,ident):
    result={}
    for c in db.scalars(select(Count).where(Count.inventory_id==ident).order_by(Count.id)):result[c.target]=c
    return result

@v.command
def create_inventory(db,actor,key,mode,local='',kind='',lot='',number_filter=''):
    key=request_key(key);scope=scope_data(mode,local,kind,lot,number_filter);encoded=p.dump(scope)
    existing=db.scalar(select(Inventory).where(Inventory.request_key==key))
    if existing:
        permission(existing,actor)
        if existing.scope!=encoded:raise ReceivingError('Chave repetida com âmbito diferente.')
        return existing.id
    record=Inventory(request_key=key,scope=encoded,reference='{}',created_by=actor.id);db.add(record);db.flush()
    snapshot=initial_reference(db,scope,record.id)
    if not snapshot['targets']:raise ReceivingError('Âmbito sem unidades/localizações; escolher localizações do catálogo.')
    record.reference=p.dump(snapshot);journal(db,actor,'INVENTARIO',dict(id=record.id,scope=scope,reference=snapshot));return record.id

def project_count(target,baseline,physical,content,local):
    if physical is None or content is None:return None
    if physical!=0 or target['partial']:
        if any(k not in content for k in target['keys']):return None
    previous=baseline['position'];unit_kind=target['kind'];before=previous['materials']
    result=dict(before)
    if target['partial']:
        if target['grouped'] and (any(q is None for q in before.values()) or sum(before.values())!=previous['physical']):return None
        if physical==0 and target['individual'] and any(k not in target['keys'] for k in before):
            raise ReceivingError('Contagem por lote não declara vazia a unidade multilote: contar a unidade completa.')
        if any(k not in target['keys'] for k in content):raise ReceivingError('Composição fora do lote selecionado.')
        if any(k not in content for k in target['keys']):return None
        for k,q in content.items():
            if q==0:result.pop(k,None)
            else:result[k]=q
        is_group=unit_kind in ('paletes','fio','prateleiras','vasilhame') and target.get('grouped',target['source_physical']!=1 or unit_kind!='paletes')
        if is_group:
            if sum(q or 0 for q in content.values())!=physical:raise ReceivingError('Paletes contadas divergem das quantidades por lote.')
            total=previous['physical']-sum(before.get(k,0) or 0 for k in target['keys'])+physical
            if local!=previous['local']:
                remaining={k:q for k,q in before.items() if k not in target['keys']}
                return dict(physical=physical,materials=canonical(content),target=local,source_remaining=dict(physical=sum(remaining.values()),materials=remaining),scope_keys=target['keys'])
        else:total=physical
    else:result=canonical(content);total=physical
    if physical==0 and not target['partial']:result={}
    return dict(physical=total,materials=canonical(result),target=local)

@v.command
def save_count(db,actor,ident,version,key,target_key,kind,physical=None,content=None,observed=None,evidence='',recount=False,roll_ids=None):
    record=db.get(Inventory,ident)
    if not record:raise ReceivingError('Inventário inexistente.',404)
    key=request_key(key)
    intent=dict(version=version,kind=kind,physical=number(physical,unknown=True),materials=materials(content,unknown=True),observed=v.optional(observed),evidence=v.optional(evidence,2000),recount=bool(recount),roll_ids=roll_ids)
    existing=db.scalar(select(Count).where(Count.request_key==key))
    if existing:
        if existing.inventory_id!=ident or existing.target!=target_key or json.loads(existing.payload).get('request')!=intent:raise ReceivingError('Chave repetida para outra contagem/dados diferentes.')
        return existing.id
    if record.status=='closed' or record.version!=version:raise ReceivingError('Inventário encerrado ou versão desatualizada.')
    if kind not in ('count','unknown','omit'):raise ReceivingError('Estado de contagem inválido.')
    if kind=='omit':admin(actor);note(evidence,'Motivo da omissão resolvida')
    if kind=='unknown':note(evidence,'Observação da ocorrência desconhecida')
    allowed=inventory_targets(db,record);target=allowed.get(target_key)
    if target is None:
        if not target_key.startswith('U:') or kind not in ('unknown','omit'):raise ReceivingError('Alvo fora do âmbito do inventário.')
        target=dict(key=target_key,position=None,kind='unknown',local=None)
    physical=number(physical,unknown=True);content=materials(content,unknown=True)
    loc=destination(db,observed,default=target.get('local'))
    raw=json.loads(record.reference)
    if recount:
        note(evidence,'Comprovação da nova conferência')
        pos=target.get('position')
        ref=reference(db,pos,loc,record.id) if pos else {}
        if pos:
            state=ref['position'];expected={k:state['materials'].get(k,0) for k in target.get('keys',[])} if target.get('partial') else state['materials']
            target=dict(target,source=state['materials'],source_physical=state['physical'],materials=expected,physical=sum(q or 0 for q in expected.values()) if target['partial'] and target['grouped'] else state['physical'])
    elif target.get('position'):
        state=raw['positions'].get(str(target['position']))
        ref=dict(position=state,source_catalogue=raw['catalogues'].get(state['local']) if state else None,target_catalogue=raw['catalogues'].get(loc))
    else:ref={}
    after=None
    if kind=='count' and target.get('position') and ref.get('position'):
        if physical==0:content={} if not target.get('partial') else content
        if target['kind']=='vasilhame' and physical is not None:
            brand=db.get(Unit,target['unit']).brand;content={brand:physical}
        after=project_count(target,ref,physical,content,loc)
    if kind=='count' and target.get('kind')=='rolos':
        if roll_ids is None:after=None
        else:
            from .valmet_rolls import present,material
            if len(roll_ids)!=len(set(roll_ids)):raise ReceivingError('IDs repetidos na contagem.')
            current={r['id']:r for r in present(db,target['unit'])}
            if not set(roll_ids)<=current.keys():raise ReceivingError('Rolos desconhecidos/ausentes: registar ocorrência e reconciliar administrativamente.')
            counted=material([current[i] for i in roll_ids])
            other=[r for r in current.values() if target.get('partial') and r['lot'] not in target['keys']]
            if canonical(content or {})!=counted or physical!=(1 if roll_ids or other else 0):raise ReceivingError('Contagem física/composição não corresponde aos IDs indicados.')
    complete=kind=='omit' or (kind=='count' and ((target['kind']=='location' and physical is not None) or (after is not None and all(q is not None for q in content.values()) and (physical==0 or target['identified'] and not target['legacy']))))
    payload=dict(physical=physical,materials=content,observed=loc,evidence=v.optional(evidence,2000),complete=complete,after=after,target=target,recount=bool(recount),request=intent,roll_ids=roll_ids)
    record.version+=1;record.status='draft'
    count=Count(inventory_id=ident,target=target_key,request_key=key,version=record.version,kind=kind,payload=p.dump(payload),reference=p.dump(ref),actor=actor.username)
    db.add(count);db.flush();journal(db,actor,'CONTAGEM',dict(inventory=ident,count=count.id,version=record.version,payload=payload,reference=ref));return count.id

@v.command
def submit(db,actor,ident,version):
    record=db.get(Inventory,ident)
    if not record:raise ReceivingError('Inventário inexistente.',404)
    if record.status=='closed' or record.version!=version:raise ReceivingError('Versão desatualizada/encerrada.')
    if record.status!='submitted':record.status='submitted';record.version+=1;journal(db,actor,'SUBMETER',dict(inventory=ident,version=record.version))
    return ident

def unresolved(db,unitid):
    rows=db.scalars(select(ValmetOccurrence).where(ValmetOccurrence.unit_id==unitid)).all();resolved={o.resolves_id for o in rows if o.resolves_id}
    return [o.id for o in rows if o.kind!='resolved' and o.id not in resolved]

def allocate_returns(db,unit,previous,after,physical,lineids):
    need={k:max(0,q-(previous.get(k,0) or 0)) for k,q in after.items() if q is not None}
    if any(previous.get(k)=='unknown' or previous.get(k,None) is None for k in after if k in previous):raise ReceivingError('Reconciliar material desconhecido antes de compensar expedição.')
    need={k:q for k,q in need.items() if q};result=[];used=defaultdict(lambda:dict(physical=0,materials=defaultdict(int)))
    for draft in db.scalars(select(Draft).join(Move,Move.draft_id==Draft.id).where(Move.unit_id==unit.id)):
        for claim in json.loads(draft.payload).get('returns',[]):
            used[claim['line']]['physical']+=claim['physical']
            for k,q in claim['materials'].items():used[claim['line']]['materials'][k]+=q
    for ident in sorted(set(lineids)):
        line=db.get(ShipmentLine,ident)
        if not line or db.get(Position,line.position_id).unit_id!=unit.id:raise ReceivingError('Expedição de outra unidade/contexto.')
        amount=min(physical,max(0,line.physical_quantity-used[ident]['physical']));physical-=amount;mat={}
        for k,q in json.loads(line.materials).items():
            value=min(need.get(k,0),max(0,q-used[ident]['materials'][k]))
            if value:mat[k]=value;need[k]-=value
        if amount or mat:result.append(dict(line=ident,physical=amount,materials=mat))
    if physical or any(need.values()) or not result:raise ReceivingError('Reposição ultrapassa o material expedido ainda não compensado.')
    return result

def validate_after(db,pos,unit,physical,content,proven):
    current=json.loads(pos.materials);ident=db.scalar(select(ValmetIdentification.id).where(ValmetIdentification.unit_id==unit.id))
    allowed=set(known(db,unit))|set(current)
    if unit.kind!='lab' and any(q is None for q in content.values()):raise ReceivingError('Indicar quantidades comprovadas; conteúdo desconhecido fica pendente.')
    if any(k not in allowed for k in content):raise ReceivingError('Artigo/lote/referência sem origem rastreável; usar identificação/entrada regular.')
    increase=physical>pos.physical_quantity or any(q is not None and (current.get(k) is None or q>current[k]) for k,q in content.items())
    if increase and (not proven or not ident or unit.legacy_unnumbered or (unit.kind in ('rolos','lab','caixas','pecas','vazias') and not unit.number)):
        raise ReceivingError('Diferença positiva exige identidade física e contagem comprovadas; manter pendente.')
    if physical==0:
        if content:raise ReceivingError('Unidade ausente não pode conservar conteúdo contado positivo.')
    if individual(unit) and not unit.legacy_unnumbered and physical>1:raise ReceivingError('Não duplicar a identidade física/N.º Armazém.')
    if unit.kind in ('paletes','fio','prateleiras','vasilhame') and not (unit.kind=='paletes' and unit.physical_quantity==1) and ident:
        if any(q is None for q in content.values()) or sum(content.values())!=physical:raise ReceivingError('As quantidades por lote não correspondem às paletes físicas.')
    if unit.kind in ('caixas','vazias') or (unit.kind=='paletes' and unit.physical_quantity==1):
        if any(q!=physical for q in content.values()):raise ReceivingError('Uma unidade física multilote conta uma vez; indicar presença por lote.')

def make_draft(db,actor,key,kind,posid,target,physical,content,reason,evidence,proven=False,clear=False,located=False,basis='count',lineids=(),resolves=(),inventory=None,count=None,baseline=None,ident=None,version=None,counted_keys=None,source_remaining=None,scope_keys=(),roll_change=None):
    if kind=='adjustment':admin(actor)
    key=request_key(key);pos=db.get(Position,posid)
    if not pos:raise ReceivingError('Posição inexistente.',404)
    unit=db.get(Unit,pos.unit_id);physical=number(physical);rawcontent=materials(content);counted_keys=list(rawcontent) if counted_keys is None else counted_keys;content=canonical(rawcontent)
    target=destination(db,target,default=pos.local if kind=='adjustment' else None)
    reason=note(reason,'Motivo');evidence=note(evidence,'Evidência')
    current=json.loads(pos.materials)
    remaining=source_remaining or dict(physical=0,materials={})
    total_physical=physical+remaining['physical'];combined=dict(content,**remaining['materials'])
    if kind=='transfer':
        if pos.local is None and (actor.role!='admin' or not located):raise ReceivingError('Origem Por localizar exige prova explícita do Administrador.',403)
        if individual(unit):
            if physical!=pos.physical_quantity or content!=current:raise ReceivingError('Transferir a unidade física inteira, com toda a composição.')
        elif sum(content.values())!=physical:raise ReceivingError('Composição física da transferência divergente.')
        if physical==0:raise ReceivingError('Transferência sem quantidade.')
    else:validate_after(db,pos,unit,total_physical,combined,proven)
    if basis not in ('count','return'):raise ReceivingError('Natureza do ajuste inválida.')
    returns=allocate_returns(db,unit,current,combined,max(0,total_physical-pos.physical_quantity),lineids) if basis=='return' else []
    for lid in lineids:
        line=db.get(ShipmentLine,lid)
        if not line or db.get(Position,line.position_id).unit_id!=unit.id:raise ReceivingError('Referência de saída fora da unidade/contexto.')
    uncertain=(pos.uncertain and not clear) or (not db.scalar(select(ValmetIdentification.id).where(ValmetIdentification.unit_id==unit.id)))
    payload=dict(target=target,physical=physical,materials=content,reason=reason,evidence=evidence,count_proven=bool(proven),uncertain=bool(uncertain),
        located_proof=bool(located),basis=basis,returns=returns,related_lines=list(sorted(set(lineids))),resolves=list(sorted(set(resolves))),counted_keys=counted_keys)
    payload.update(source_remaining=remaining,scope_keys=list(scope_keys))
    if kind=='adjustment' and unit.kind=='rolos':
        from .valmet_rolls import adjustment
        payload['roll_change']=adjustment(db,pos,physical,content,roll_change)
    data=p.dump(payload);snapshot=baseline or reference(db,posid,target,inventory)
    existing=db.get(Draft,ident) if ident else db.scalar(select(Draft).where(Draft.request_key==key))
    if existing:
        permission(existing,actor)
        if not ident:
            if existing.payload!=data or existing.position_id!=posid or existing.kind!=kind:raise ReceivingError('Chave repetida com revisão diferente.')
            return existing.id
        if existing.status!='draft' or existing.version!=version or existing.request_key!=key:raise ReceivingError('Revisão desatualizada/confirmada.')
        existing.payload=data;existing.reference=p.dump(snapshot);existing.version+=1;draft=existing
    else:
        draft=Draft(request_key=key,kind=kind,position_id=posid,payload=data,reference=p.dump(snapshot),created_by=actor.id,inventory_id=inventory,count_id=count)
        db.add(draft);db.flush()
    journal(db,actor,'REVISAO',dict(draft=draft.id,version=draft.version,kind=kind,reference=snapshot,payload=payload));return draft.id

@v.command
def save_transfer(db,actor,key,posid,target,physical,lot,reason,evidence,located=False,ident=None,version=None):
    pos=db.get(Position,posid)
    if not pos:raise ReceivingError('Posição inexistente.',404)
    unit=db.get(Unit,pos.unit_id);content=json.loads(pos.materials)
    if not individual(unit):
        lot=v.required(lot,'Lote / referência')
        if lot not in content:raise ReceivingError('Lote não encontrado na origem.')
        content={lot:number(physical)}
    return make_draft(db,actor,key,'transfer',posid,target,physical,content,reason,evidence,located=located,ident=ident,version=version)

@v.command
def save_adjustment(db,actor,key,posid,target,physical,content,reason,evidence,proven=False,clear=False,basis='count',lineids=(),resolves=(),ident=None,version=None,roll_change=None):
    return make_draft(db,actor,key,'adjustment',posid,target,physical,content,reason,evidence,proven,clear,basis=basis,lineids=lineids,resolves=resolves,ident=ident,version=version,roll_change=roll_change)

def apply_draft(db,actor,record,version):
    permission(record,actor)
    if record.kind=='adjustment':admin(actor)
    existing=db.scalar(select(Move).where(Move.draft_id==record.id))
    if existing:return existing.id
    if record.status!='draft' or record.version!=version:raise ReceivingError('Revisão desatualizada/confirmada.')
    payload=json.loads(record.payload);current=reference(db,record.position_id,payload['target'],record.inventory_id)
    if current!=json.loads(record.reference):raise ReceivingError('Stock, reservas ou movimentos mudaram; suspender e fazer nova revisão/conferência.')
    pos=db.get(Position,record.position_id)
    move=Move(draft_id=record.id,unit_id=pos.unit_id,snapshot=p.dump(dict(kind=record.kind,before=current,operation=payload)),actor=actor.username)
    db.add(move);db.flush();record.status='confirmed';db.flush()
    journal(db,actor,'MOVIMENTO',dict(move=move.id,draft=record.id,version=version,snapshot=json.loads(move.snapshot)));return move.id

@v.command
def confirm(db,actor,ident,version):return apply_draft(db,actor,db.get(Draft,ident),version)

@v.command
def decide(db,actor,ident,version,count_id,key,action,reason,evidence,proven=False,clear=False,basis='count',lineids=(),resolves=()):
    admin(actor);record=db.get(Inventory,ident);permission(record,actor);key=request_key(key)
    reason=note(reason,'Motivo da decisão');evidence=note(evidence,'Evidência da decisão')
    existing=db.scalar(select(Decision).where(Decision.request_key==key))
    if existing:
        if existing.inventory_id!=ident or existing.count_id!=count_id or existing.kind!=action or existing.reason!=reason+' · '+evidence:raise ReceivingError('Chave de decisão repetida com dados diferentes.')
        return existing.id
    if record.status!='submitted' or record.version!=version:raise ReceivingError('Submeter a versão atual das contagens antes de decidir.')
    count=db.get(Count,count_id)
    if not count or count.inventory_id!=ident or latest_counts(db,ident).get(count.target).id!=count.id:raise ReceivingError('Contagem desatualizada/inexistente.')
    if action not in ('approve','defer','omit'):raise ReceivingError('Decisão inválida.')
    data=json.loads(count.payload);moveid=None
    last=db.scalar(select(Decision).where(Decision.count_id==count.id,Decision.kind.in_(('approve','omit'))).order_by(Decision.id.desc()))
    if last:
        if last.kind!=action:raise ReceivingError('Contagem já decidida; corrigir por nova revisão auditada.')
        return last.id
    if action=='approve':
        if count.kind!='count' or not data['complete']:raise ReceivingError('Contagem insuficiente: esclarecer identificação/quantidades ou resolver omissão explicitamente.')
        if data['target'].get('position'):
            keep_ids=data.get('roll_ids')
            if keep_ids is not None and data['target'].get('partial'):
                from .valmet_rolls import present
                keep_ids=sorted(set(keep_ids)|{r['id'] for r in present(db,data['target']['unit']) if r['lot'] not in data['target']['keys']})
            after=data['after'];before=json.loads(count.reference)
            if before!=reference(db,data['target']['position'],after['target'],ident):raise ReceivingError('Movimentos posteriores à referência; reconferir a parte afetada.')
            old=before['position']
            if after['physical']!=old['physical'] or canonical(after['materials'])!=old['materials'] or after['target']!=old['local'] or clear or resolves:
                draftid=make_draft(db,actor,key,'adjustment',data['target']['position'],after['target'],after['physical'],after['materials'],reason,evidence,proven,clear,
                    basis=basis,lineids=lineids,resolves=resolves,inventory=ident,count=count.id,baseline=before,counted_keys=list(data['materials'] or {}),source_remaining=after.get('source_remaining'),scope_keys=after.get('scope_keys',()),roll_change=dict(keep=keep_ids,restore=[],add=[]) if keep_ids is not None else None)
                moveid=apply_draft(db,actor,db.get(Draft,draftid),1)
    decision=Decision(inventory_id=ident,count_id=count.id,move_id=moveid,request_key=key,kind=action,reason=reason+' · '+evidence,actor=actor.username)
    db.add(decision);db.flush();record.version+=1;journal(db,actor,'DECISAO',dict(inventory=ident,count=count.id,decision=decision.id,action=action,move=moveid));return decision.id

@v.command
def close_inventory(db,actor,ident,version):
    admin(actor);record=db.get(Inventory,ident);permission(record,actor)
    if record.status=='closed':return ident
    if record.status!='submitted' or record.version!=version:raise ReceivingError('Submeter a versão atual antes de concluir.')
    counts=latest_counts(db,ident);decisions=db.scalars(select(Decision).where(Decision.inventory_id==ident,Decision.kind.in_(('approve','omit')))).all()
    approved={d.count_id for d in decisions}
    selected=inventory_targets(db,record)
    for key in selected:
        c=counts.get(key)
        if not c or c.id not in approved:raise ReceivingError('Âmbito ainda não contado/reconciliado; zero não é ausência de contagem.')
    if any(c.id not in approved for key,c in counts.items() if key.startswith('U:')):raise ReceivingError('Ocorrências desconhecidas ainda sem decisão explícita.')
    # A location total is an explicit count, never a blank-as-zero shortcut.
    # Cross-check the selected physical scope, except omissions explicitly resolved
    # by an administrator (whose quantities must not be guessed).
    final={d.count_id:d.kind for d in decisions};totals=defaultdict(int);omitted=set()
    for key,count in counts.items():
        data=json.loads(count.payload);target=data['target']
        if target['kind']=='location':continue
        if final.get(count.id)=='approve' and target.get('position'):
            before=json.loads(count.reference)['position']
            current=reference(db,target['position'],data['after']['target'],ident)['position']
            if before['epoch']!=current['epoch']:raise ReceivingError('Movimentos/reservas posteriores à contagem aprovada; reconferir antes de concluir o inventário.')
        loc=data.get('observed') or target.get('local')
        if final.get(count.id)=='omit':omitted.update((loc,target.get('local')))
        elif data['physical'] is not None:totals[loc]+=data['physical']
    for key,target in selected.items():
        if target['kind']!='location':continue
        count=counts[key];data=json.loads(count.payload);loc=target.get('local')
        if final[count.id]=='approve' and loc not in omitted and data['physical']!=totals[loc]:
            raise ReceivingError('Total físico da localização diverge das unidades contadas no âmbito; rever a contagem, sem presumir zero.')
    record.version+=1;record.status='closed';journal(db,actor,'CONCLUIR',dict(inventory=ident,version=record.version));return ident

def inventory_summary(db,record):
    counts=latest_counts(db,record.id);decisions=list(db.scalars(select(Decision).where(Decision.inventory_id==record.id).order_by(Decision.id)))
    selected=inventory_targets(db,record)
    for key,count in counts.items():
        if key not in selected:selected[key]=json.loads(count.payload)['target']
    return [dict(target=json.loads(counts[key].payload)['target'] if key in counts else t,count=json.loads(counts[key].payload) if key in counts else None,count_id=counts[key].id if key in counts else None,
        decisions=[dict(id=d.id,kind=d.kind,reason=d.reason,actor=d.actor,move=d.move_id) for d in decisions if key in counts and d.count_id==counts[key].id]) for key,t in selected.items()]

@v.command
def issue_document(db,actor,ident,version,kind):
    from hashlib import sha256
    from .database import now_utc
    from .valmet_control_pdf import render_pdf
    record=db.get(Inventory,ident)
    if not record:raise ReceivingError('Inventário inexistente.',404)
    if kind not in ('count','differences'):raise ReceivingError('Documento inválido.')
    existing=db.scalar(select(Document).where(Document.inventory_id==ident,Document.version==version,Document.kind==kind))
    if existing:return existing.id
    if record.version!=version:raise ReceivingError('Revisão desatualizada; consultar documento já arquivado ou gerar a revisão atual.')
    snapshot=dict(scope=json.loads(record.scope),status=record.status,actor=actor.username,rows=inventory_summary(db,record))
    if sum(1+len(row['target'].get('materials') or {}) for row in snapshot['rows'])>25000:raise ReceivingError('Documento demasiado extenso; usar folhas de inventários parciais. O inventário continua guardado.')
    created=now_utc();pdf=render_pdf(ident,version,kind,snapshot,created)
    doc=Document(inventory_id=ident,version=version,kind=kind,snapshot=p.dump(snapshot),pdf=pdf,digest=sha256(pdf).hexdigest(),actor=actor.username,created_at=created)
    db.add(doc);db.flush();journal(db,actor,'DOCUMENTO',dict(document=doc.id,inventory=ident,version=version,digest=doc.digest));return doc.id
