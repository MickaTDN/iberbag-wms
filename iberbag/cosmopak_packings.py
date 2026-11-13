"""Office PACKINGs, exclusive individual picks, physical conference and dispatch."""
import hashlib,json,secrets
from datetime import date,datetime
from sqlalchemy import select,text,func
from .database import (CosmoPacking as Packing,CosmoPackingState as State,CosmoPackingLine as Line,
 CosmoPackingPick as Pick,CosmoPackingAction as Action,CosmoPackingDocument as Document,
 CosmoState,Audit,context_id,now_utc)
from .receiving import operation,ReceivingError,request_key,required_note
from . import cosmopak as receipts,cosmopak_packing_excel as excel


def header(db,ident):
    obj=db.scalar(select(State).where(State.packing_id==ident))
    if not obj:raise ReceivingError('PACKING inexistente neste contexto.',404)
    return obj


def snapshot(db,ident):
    value=db.scalar(text('SELECT ddn_packing_snapshot(:ident,:ctx)'),{'ident':ident,'ctx':context_id(db)})
    if not value:raise ReceivingError('PACKING inexistente.',404)
    return value if isinstance(value,dict) else json.loads(value)


def preview(db,actor,key,content,filename,reauthorize=None):
    if db.in_transaction():db.rollback()
    try:report=excel.analyze(content)
    except ValueError as error:raise ReceivingError(str(error),400) from error
    if reauthorize:reauthorize()
    return store(db,actor,key,content,filename,report)


@operation(operational=False)
def store(db,actor,key,content,filename,report):
    receipts.guard(db,actor,True);key=request_key(key)
    if not isinstance(filename,str) or not 1<=len(filename)<=240 or any(ord(c)<32 or c in '/\\' for c in filename):raise ReceivingError('Nome de ficheiro inválido.',400)
    old=receipts.previous(db,Packing,key,source_sha256=hashlib.sha256(content).hexdigest(),source_name=filename)
    if old:return old.id
    obj=Packing(request_key=key,source_name=filename,source_sha256=report['sha256'],source_excel=content,report=receipts.dump(report),created_by=actor.id)
    db.add(obj);db.flush();db.add(Audit(username=actor.username,action='COSMO_PACKING_PREVIA',details=f'PACKING interno {obj.id}; origem {obj.source_sha256}; sem reservas.'))
    return obj.id


@operation(operational=False)
def command(db,actor,key,ident,kind,version,payload=None,reason='',pick_id=None,pallet_id=None):
    receipts.guard(db,actor);key=request_key(key);reason=required_note(reason,'Motivo / confirmação física')
    office={'map','confirm','substitute','reopen_prepare','reopen_load','review_dispatch','approve'}
    if kind in office:receipts.guard(db,actor,True)
    data=payload or {};head=header(db,ident)
    if kind=='map':
        packing=db.get(Packing,ident)
        try:data=excel.map_rows(json.loads(packing.report),data.get('mapping'),data.get('reference'),data.get('reference_cell') or None,reason)
        except ValueError as error:raise ReceivingError(str(error),400) from error
    if kind=='review_dispatch':
        try:data={'dispatched_on':date.fromisoformat(data.get('dispatched_on')).isoformat()}
        except (ValueError,TypeError):raise ReceivingError('Indicar data real, sem a presumir.',400)
    if kind in ('prepare','load','return_stage','return_rack'):
        try:data['boxes']=excel.integer(data.get('boxes'))
        except ValueError as error:raise ReceivingError(str(error),400) from error
    fields=dict(packing_id=ident,kind=kind,pick_id=pick_id,pallet_id=pallet_id,expected_version=version,payload=receipts.dump(data),reason=reason)
    old=receipts.previous(db,Action,key,**fields)
    if old:return old.id
    obj=Action(request_key=key,actor_id=actor.id,created_at=now_utc(),**fields);db.add(obj);db.flush();db.expire_all()
    db.add(Audit(username=actor.username,action='COSMO_PACKING_'+kind.upper(),details=f'PACKING {ident}; comando {obj.id}; {reason}'))
    return obj.id


@operation(operational=False)
def document(db,actor,key,ident,kind):
    receipts.guard(db,actor,True);key=request_key(key)
    old=receipts.previous(db,Document,key,packing_id=ident,kind=kind)
    if old:return old.id
    h=header(db,ident)
    if kind=='dispatch':
        if h.stage!='shipped':raise ReceivingError('Saída ainda não aprovada.')
        data=json.loads(h.final_snapshot)
    elif kind=='stage':
        if h.stage in ('draft','reviewed','shipped'):raise ReceivingError('Gerar identificação só numa preparação confirmada; depois, usar o arquivo.')
        data=snapshot(db,ident)
    else:raise ReceivingError('Tipo de documento inválido.',400)
    from .cosmopak_packing_pdf import render_pdf
    created=now_utc();pdf=render_pdf(data,kind,actor.username,created)
    doc=Document(packing_id=ident,request_key=key,kind=kind,snapshot=receipts.dump(data),pdf=pdf,digest=hashlib.sha256(pdf).hexdigest(),actor_id=actor.id,created_at=created)
    db.add(doc);db.flush();db.add(Audit(username=actor.username,action='COSMO_PACKING_PDF',details=f'Documento {doc.id}; sem movimento de stock.'))
    return doc.id


def validate_snapshot(data):
    """Cross-book recovery proof, with no repairs or synthetic physical identity."""
    from collections import Counter
    from .transfer import json_value
    def normalized(row):
        # PostgreSQL JSON omits trailing fractional-second zeroes; compare
        # instants without weakening any non-temporal book/projection fields.
        row=json.loads(json.dumps(row,default=json_value))
        for field in ('prepared_at','loaded_at','dispatched_on'):
            if row.get(field) is not None:row[field]=datetime.fromisoformat(row[field]).isoformat(timespec='microseconds')
        return row
    for src in data.get('cosmo_packings',[]):
        if hashlib.sha256(src['source_excel']).hexdigest()!=src['source_sha256'] or excel.analyze(src['source_excel'])!=json.loads(src['report']):raise ValueError('Origem PACKING divergente.')
    if data.get('physical_staging_areas')!=[dict(id=1,warehouse_id=1,label='Zona de preparação')]:raise ValueError('Catálogo da zona física lógica divergente.')
    actions={r['id']:r for r in data.get('cosmo_packing_actions',[])}
    states={r['packing_id']:r for r in data.get('cosmo_packing_states',[])}
    ledger={r['action_id']:r for r in data.get('cosmo_packing_ledger',[])}
    if set(actions)!=set(ledger):raise ValueError('Comando PACKING sem resultado integral.')
    active=Counter()
    for pick in data.get('cosmo_packing_picks',[]):
        if pick['active'] and pick['pallet_id'] is not None:active[(pick['context_id'],pick['pallet_id'])]+=1
        if pick['loaded'] and not pick['prepared']:raise ValueError('Carregada sem preparação.')
    if any(q>1 for q in active.values()):raise ValueError('Reserva individual duplicada.')
    for position in data.get('physical_staging_positions',[]):
        events=[r for r in data['physical_staging_events'] if (r['context_id'],r['unit_kind'],r['unit_id'])==(position['context_id'],position['unit_kind'],position['unit_id'])]
        if not events or json.loads(max(events,key=lambda r:r['id'])['after_state'])!=position or position['version']!=len(events):raise ValueError('Zona física divergente do livro comum.')
    for doc in data.get('cosmo_packing_documents',[]):
        if hashlib.sha256(doc['pdf']).hexdigest()!=doc['digest']:raise ValueError('PDF PACKING alterado.')
        if doc['kind']=='dispatch' and json.loads(doc['snapshot'])!=json.loads(states[doc['packing_id']]['final_snapshot']):raise ValueError('PDF divergente da seleção expedida.')

    pick_kinds={'substitute','prepare','unprepare','exception','load','unload','return_stage'}
    lines={r['id']:r for r in data.get('cosmo_packing_lines',[])}
    sources={r['id']:r for r in data.get('cosmo_packings',[])}
    moves={(r['context_id'],r['action_id'],r['pallet_id']):r for r in data.get('cosmo_packing_moves',[])}
    events={(r['context_id'],r['source_id'],r['unit_id']):r for r in data.get('physical_staging_events',[])}
    if set(moves)!=set(events):raise ValueError('Movimento sem prova física comum integral.')
    for key,move in moves.items():
        event=events[key];action=actions.get(move['action_id'])
        before=json.loads(move['before_state']);after=json.loads(move['after_state'])
        if not action or action['kind'] not in ('prepare','load','return_stage','return_rack','approve') or action['context_id']!=move['context_id'] or before['pallet_id']!=move['pallet_id'] or after['pallet_id']!=move['pallet_id'] or after['version']!=before['version']+1 or event['group_id']!=action['packing_id'] or event['unit_kind']!='cosmopak' or event['group_kind']!='cosmo_packing' or event['source_kind']!='cosmo_packing':raise ValueError('Origem/identidade de movimento PACKING divergente.')
    for head in states.values():
        commands=sorted((r for r in actions.values() if r['packing_id']==head['packing_id']),key=lambda r:r['id'])
        if head['version']!=sum(r['kind']!='return_rack' for r in commands):raise ValueError('Versão PACKING divergente do livro.')
        outputs=[ledger[a['id']] for a in commands if a['kind'] not in pick_kinds|{'return_rack'}]
        if outputs:
            expected=json.loads(outputs[-1]['after_state']);expected['version']=head['version']
            if normalized(expected)!=normalized(head):raise ValueError('Estado PACKING divergente do livro.')
        elif head['stage']!='draft' or head['version']!=0:raise ValueError('PACKING sem origem de estado.')
        for a in commands:
            if a['context_id']!=head['context_id']:raise ValueError('Comando PACKING fora do contexto.')
            if a['kind']=='map':
                payload=json.loads(a['payload']);report=json.loads(sources[head['packing_id']]['report'])
                rebuilt=excel.map_rows(report,payload['mapping'],payload['reference'],payload['reference_cell'],payload['reason'])
                if payload!=rebuilt:raise ValueError('Mapeamento PACKING divergente da origem.')
                saved=[{k:l[k] for k in ('source_row','ordinal','article','lot','boxes')} for l in sorted(lines.values(),key=lambda l:(l['source_row'],l['ordinal'])) if l['mapping_id']==a['id']]
                if saved!=payload['lines']:raise ValueError('Linhas PACKING alteradas/omitidas.')
        current=[p for p in data.get('cosmo_packing_picks',[]) if p['packing_id']==head['packing_id']]
        if head['stage'] not in ('draft','reviewed'):
            expected_lines={l['id'] for l in lines.values() if l['mapping_id']==head['mapping_id']}
            if {p['line_id'] for p in current}!=expected_lines or len(current)!=len(expected_lines):raise ValueError('Reservas não abrangem as linhas confirmadas.')
        for pick in current:
            chain=[json.loads(ledger[a['id']]['after_state']) for a in commands if a['kind'] in pick_kinds and a['pick_id']==pick['id']]
            if chain:
                expected=chain[-1]
                if head['stage']=='shipped':expected=dict(expected,active=False,version=expected['version']+1)
                if normalized(expected)!=normalized(pick):raise ValueError('Conferência/reserva divergente do livro.')
            elif pick['prepared'] or pick['loaded'] or pick['version']!=(1 if head['stage']=='shipped' else 0):raise ValueError('Conferência sem comando comprovado.')
        if head['stage']=='shipped':
            frozen=json.loads(head['final_snapshot']);items={r['pick']['id']:r for r in frozen['items']}
            if set(items)!={p['id'] for p in current} or any(p['active'] for p in current):raise ValueError('Arquivo de saída incompleto ou reserva abandonada.')
            approval=next(a for a in commands if a['kind']=='approve')
            shipped={m['pallet_id'] for m in moves.values() if m['action_id']==approval['id']}
            if shipped!={r['pick']['pallet_id'] for r in items.values() if r['pick']['loaded']}:raise ValueError('Expedição divergente das conferências arquivadas.')
            for pick in current:
                old=items[pick['id']]['pick']
                if normalized(pick)!=normalized(dict(old,active=False,version=old['version']+1)):raise ValueError('Saída alterada após aprovação.')
