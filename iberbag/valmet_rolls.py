"""Proven individual material identities inside an indivisible physical pallet."""
import json
from collections import Counter
from sqlalchemy import select,func
from . import valmet as v
from .database import ValmetRoll as Roll,ValmetRollState as State,ValmetRollEvent as Event,ValmetUnit,ValmetPosition,Audit
from .receiving import ReceivingError,request_key

def dump(data):return json.dumps(data,sort_keys=True,ensure_ascii=False)
def clean_rows(rows):
    if not isinstance(rows,list) or not 1<=len(rows)<=500:raise ReceivingError('Indicar 1–500 rolos individuais comprovados.')
    clean=[]
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('divisible'),bool):raise ReceivingError('Cada rolo exige classificação individual X / Não divisível comprovada.')
        clean.append(dict(article=v.optional(row.get('article')) or None,lot=v.required(row.get('lot'),'Lote'),divisible=row['divisible']))
    return clean

def parse_lines(value):
    if not value or not value.strip():return None
    rows=[]
    for line in value.splitlines():
        if not line.strip():continue
        fields=[s.strip() for s in line.split('|')]
        if len(fields)!=3 or fields[2].upper() not in ('X','-'):raise ReceivingError('Usar uma linha por rolo: artigo (ou vazio) | lote | X ou -.')
        rows.append(dict(article=fields[0] or None,lot=fields[1],divisible=fields[2].upper()=='X'))
    return clean_rows(rows)

def present(db,uid):
    return [dict(id=r.id,article=r.article,lot=r.lot,divisible=s.divisible) for r,s in db.execute(select(Roll,State).join(State,State.id==Roll.id).where(Roll.unit_id==uid,State.present.is_(True)).order_by(Roll.id))]

def material(rows):return dict(Counter(r['lot'] for r in rows))
def event_version(db,uid):return db.scalar(select(func.max(Event.id)).where(Event.unit_id==uid)) or 0

def register(db,actor,uid,key,rows,reason,evidence):
    key=request_key(key);rows=clean_rows(rows);reason=v.required(reason,'Motivo');evidence=v.required(evidence,'Comprovação')
    payload=dump(dict(rolls=rows));existing=db.scalar(select(Event).where(Event.request_key==key))
    if existing:
        if existing.unit_id!=uid or existing.payload!=payload or existing.reason!=reason or existing.evidence!=evidence:raise ReceivingError('Identificador repetido com dados diferentes.')
        return existing.id
    unit=db.get(ValmetUnit,uid)
    if not unit or unit.kind!='rolos':raise ReceivingError('Palete ROLOS inexistente neste contexto.',404)
    if not unit.number or unit.legacy_unnumbered:raise ReceivingError('N.º Armazém/identidade física insuficiente; não gerar identidades de rolo fictícias.')
    if db.scalar(select(Roll.id).where(Roll.unit_id==uid).limit(1)):raise ReceivingError('Identidades já definidas; nunca reconstruir nem substituir IDs.')
    positions=db.scalars(select(ValmetPosition).where(ValmetPosition.unit_id==uid,ValmetPosition.physical_quantity>0)).all()
    if len(positions)!=1 or positions[0].uncertain or json.loads(positions[0].materials)!=material(rows):raise ReceivingError('Rolos comprovados não correspondem ao saldo atual; reconciliar quantidades/identificação primeiro.')
    event=Event(unit_id=uid,request_key=key,action='register',payload=payload,reason=reason,evidence=evidence,actor=actor.username)
    db.add(event);db.flush();db.add(Audit(username=actor.username,action='VALMET_ROLOS_IDENTIFICAR',details=dump(dict(unit=uid,event=event.id,rows=rows,reason=reason,evidence=evidence))))
    return event.id

@v.command
def reconcile(db,actor,uid,key,rows,reason,evidence):
    if actor.role!='admin':raise ReceivingError('Só Administrador pode reconciliar identidades históricas.',403)
    return register(db,actor,uid,key,rows,reason,evidence)

@v.command
def classify(db,actor,uid,key,flags,reason,evidence,expected_version=None):
    if actor.role!='admin':raise ReceivingError('Só Administrador pode corrigir classificações comprovadas.',403)
    if not isinstance(flags,dict) or not flags or any(not isinstance(value,bool) for value in flags.values()):raise ReceivingError('Classificação explícita por ID obrigatório.')
    current={r['id'] for r in present(db,uid)}
    if not set(flags)<=current:raise ReceivingError('ID ausente ou de outro contexto/palete.')
    payload=dump(dict(ids=sorted(flags),flags=[dict(id=k,divisible=v) for k,v in sorted(flags.items())]))
    key=request_key(key);reason=v.required(reason,'Motivo');evidence=v.required(evidence,'Comprovação')
    existing=db.scalar(select(Event).where(Event.request_key==key))
    if existing:
        if existing.unit_id!=uid or existing.payload!=payload or existing.reason!=reason or existing.evidence!=evidence:raise ReceivingError('Identificador repetido com outra classificação.')
        return existing.id
    if expected_version is not None and event_version(db,uid)!=expected_version:raise ReceivingError('Classificação mudou desde a revisão; voltar a conferir os IDs.')
    record=Event(unit_id=uid,request_key=key,action='classify',payload=payload,reason=reason,evidence=evidence,actor=actor.username);db.add(record);db.flush()
    db.add(Audit(username=actor.username,action='VALMET_ROLOS_CLASSIFICAR',details=dump(dict(event=record.id,unit=uid,flags=flags,reason=reason,evidence=evidence))))
    return record.id

def selection(unit,ids):
    ids=list(ids)
    if not ids or len(ids)!=len(set(ids)):raise ReceivingError('Selecionar IDs distintos de rolos.')
    available={r['id']:r for r in unit['rolls']};allrows=unit['all_rolls']
    if not set(ids)<=available.keys():raise ReceivingError('Rolo reservado, expedido ou de outra palete/contexto.')
    bundle={r['id'] for r in allrows if not r['divisible']}
    if set(ids)&bundle:ids=sorted(set(ids)|bundle)
    if not set(ids)<=available.keys():raise ReceivingError('Conjunto não divisível já reservado; não pode ser fracionado.')
    chosen=[available[i] for i in sorted(ids)]
    return dict(unit=unit,physical=1,materials=material(chosen),exclusive=False,roll_ids=sorted(ids))

def take(unit,chosen,lot,amount):
    used=set(chosen.get('roll_ids',[])) if chosen else set();ids=[]
    # Stable IDs, divisibles first; no FIFO commercial rule is introduced.
    for row in sorted(unit['rolls'],key=lambda r:(not r['divisible'],r['id'])):
        if row['lot']==lot and row['id'] not in used:
            ids.append(row['id'])
            candidate=selection(unit,sorted(used|set(ids)))
            if candidate['materials'].get(lot,0)-(chosen or {}).get('materials',{}).get(lot,0)>=amount:return candidate
    raise ReceivingError('Quantidade de rolos indisponível.')

def pick_shipment(selection_row,chosen,explicit=None):
    unit=selection_row['unit'];reserved=set(selection_row.get('roll_ids',[]));available={r['id']:r for r in unit['all_rolls']}
    if explicit is None:
        ids=[]
        for lot,q in chosen.items():
            candidates=sorted((r for r in available.values() if r['id'] in reserved and r['lot']==lot),key=lambda r:(not r['divisible'],r['id']))
            ids.extend(r['id'] for r in candidates[:q])
    else:ids=list(explicit)
    if len(ids)!=len(set(ids)) or not set(ids)<=reserved or not set(ids)<=available.keys() or material([available[i] for i in ids])!=chosen:raise ReceivingError('IDs efetivamente carregados divergem das quantidades/reserva.')
    bundle={r['id'] for r in available.values() if not r['divisible']}
    if set(ids)&bundle and not bundle<=set(ids):raise ReceivingError('Carregar todo o conjunto não divisível, incluindo os lotes adicionais.')
    return sorted(ids)


def adjustment(db,pos,physical,content,change):
    """Validate human-proven identities, never distribute aggregate counts."""
    from .database import ValmetRollState
    if not isinstance(change,dict):raise ReceivingError('Ajuste ROLOS exige IDs conservados/repostos e novos rolos comprovados, não apenas totais.')
    keep=change.get('keep',[]);restore=change.get('restore',[]);new=change.get('add',[])
    if any(not isinstance(i,int) or isinstance(i,bool) for i in keep+restore) or len(keep+restore)!=len(set(keep+restore)):raise ReceivingError('IDs de rolos inválidos/repetidos.')
    rows=[]
    for ident,wanted in [(i,True) for i in keep]+[(i,False) for i in restore]:
        roll=db.get(Roll,ident);state=db.get(State,ident)
        if not roll or not state or roll.unit_id!=pos.unit_id or state.present!=wanted:raise ReceivingError('ID ausente, já em stock ou de outra palete/contexto.')
        rows.append(dict(id=roll.id,lot=roll.lot,article=roll.article,divisible=state.divisible))
    if new:
        if db.scalar(select(Roll.id).where(Roll.unit_id==pos.unit_id).limit(1)) and not present(db,pos.unit_id):raise ReceivingError('Palete individualizada ausente: repor os IDs originais comprovados, sem reconstruir identidades.')
        new=clean_rows(new);rows+=new
    if not db.scalar(select(Roll.id).where(Roll.unit_id==pos.unit_id).limit(1)) and pos.physical_quantity>0 and physical>0 and not new:raise ReceivingError('Reconciliar identidades históricas antes de ajustar o remanescente.')
    if material(rows)!=content or physical!=(1 if rows else 0):raise ReceivingError('IDs individuais divergem do saldo físico/composição propostos.')
    return dict(keep=sorted(keep),restore=sorted(restore),add=new,removed=sorted({r['id'] for r in present(db,pos.unit_id)}-set(keep)))
