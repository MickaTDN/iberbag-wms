"""Common physical catalogue/occupancy with opt-in sharing and historical claims.

Claims remain immutable historical assignments. Explicitly reconciled Iberbag/
Cosmopak positions use one common identity/capacity and physical event ledger.
"""
from sqlalchemy import select

# Stable identities assigned by published ddn_0001, not display names or
# StorageContext IDs. FKs prevent replacing these referenced owners/warehouse;
# contexts are resolved below rather than assuming context IDs equal owner IDs.
SHARING_WAREHOUSE_ID = 1
SHARING_OWNER_IDS = (1, 3)

EXEMPT = {'', 'chão', 'chao', '(não indicado)'}


def physical_code(value):
    code = (value or '').strip(' ').lower()
    return None if code in EXEMPT else code


def expected_claims(data):
    """Virtual metadata for legacy reconciliation; never writes the source."""
    warehouses = {r['id']: r['warehouse_id'] for r in data['storage_contexts']}
    claimed = {}
    for table, field in (('locations', 'codigo'), ('stock', 'local')):
        for row in data[table]:
            code = physical_code(row[field])
            if code is None:
                continue
            ctx = row.get('context_id', 1)
            key = (warehouses[ctx], code)
            if key in claimed and claimed[key] != ctx:
                raise ValueError('Código físico atribuído a contextos diferentes no mesmo armazém. Partilha bloqueada; reconciliar antes de atualizar.')
            claimed[key] = ctx
    return [dict(warehouse_id=wh, code=code, context_id=ctx)
            for (wh, code), ctx in sorted(claimed.items())]


def ensure_exclusive(db, codes):
    """Read the global registry through Connection, deliberately outside ORM scope."""
    from .database import PhysicalLocationClaim, StorageContext, context_id
    ctx = context_id(db)
    wanted = {key for value in codes if (key := physical_code(value)) is not None}
    if not wanted:
        return
    wh = db.connection().scalar(select(StorageContext.warehouse_id).where(StorageContext.id == ctx))
    if wh is None:
        raise ValueError('Contexto inexistente.')
    if _schema(db):
        from .database import PhysicalLocation,PhysicalLocationAccess
        p=PhysicalLocation.__table__;a=PhysicalLocationAccess.__table__
        allowed=set(db.connection().scalars(select(p.c.code).join(a,a.c.physical_id==p.c.id).where(p.c.warehouse_id==wh,a.c.context_id==ctx,p.c.code.in_(wanted))))
        wanted-=allowed
    if not wanted:return
    conflict = db.connection().scalar(select(PhysicalLocationClaim.code).where(
        PhysicalLocationClaim.warehouse_id == wh, PhysicalLocationClaim.code.in_(wanted),
        PhysicalLocationClaim.context_id != ctx).limit(1))
    if conflict:
        raise ValueError('Código físico já atribuído a outro contexto neste armazém. Partilha apenas após ativação administrativa das posições Iberbag/Cosmopak compatíveis.')


def claim_sqlite(db, record):
    """SQLite consultation fixtures only; operational transactions remain forbidden."""
    from .database import PhysicalLocationClaim, StorageContext, Location
    code = physical_code(record.codigo if isinstance(record, Location) else record.local)
    if code is None:
        return
    ensure_exclusive(db, [code])
    ctx = record.context_id
    wh = db.connection().scalar(select(StorageContext.warehouse_id).where(StorageContext.id == ctx))
    table = PhysicalLocationClaim.__table__
    if not db.connection().scalar(select(table.c.context_id).where(table.c.warehouse_id == wh, table.c.code == code)):
        db.connection().execute(table.insert().values(warehouse_id=wh, code=code, context_id=ctx))


def _schema(db):
    from sqlalchemy import inspect
    if '_physical_shared_schema' not in db.info:
        db.info['_physical_shared_schema']=inspect(db.connection()).has_table('physical_locations')
    return db.info['_physical_shared_schema']


def shared_location(db, local):
    from .database import PhysicalLocation,StorageContext,context_id
    code=physical_code(local)
    if code is None or not _schema(db):return None
    p=PhysicalLocation.__table__;c=StorageContext.__table__
    return db.connection().execute(select(p).join(c,c.c.warehouse_id==p.c.warehouse_id).where(c.c.id==context_id(db),p.c.code==code)).mappings().first()


def common_state(db, local):
    from .database import PhysicalLocationState
    p=shared_location(db,local)
    if not p:return None
    t=PhysicalLocationState.__table__
    h=db.connection().execute(select(t).where(t.c.physical_id==p['id'])).mappings().one()
    return dict(p,**{k:v for k,v in h.items() if k!='physical_id'})


def physical_statuses(db):
    """Common occupancy only; never returns another owner's articles/lots/loads."""
    from .database import PhysicalLocation,PhysicalLocationAccess,PhysicalLocationState,context_id
    from sqlalchemy import case
    if not _schema(db):return {}
    p=PhysicalLocation.__table__;a=PhysicalLocationAccess.__table__;h=PhysicalLocationState.__table__
    return {row['label']:dict(row) for row in db.connection().execute(select(p.c.id,p.c.label,h.c.state,h.c.context_id,h.c.version,case((h.c.context_id==context_id(db),h.c.source_kind),else_=None).label('source_kind'),case((h.c.context_id==context_id(db),h.c.source_id),else_=None).label('source_id'),case((h.c.context_id==context_id(db),h.c.stock_id),else_=None).label('stock_id'))
        .join(a,a.c.physical_id==p.c.id).join(h,h.c.physical_id==p.c.id).where(a.c.context_id==context_id(db))).mappings()}


def require_free(db, local, qty, pending=None):
    from .database import context_id
    from .receiving import ReceivingError
    h=common_state(db,local)
    if h:
        own_pending=pending and h['context_id']==context_id(db) and (h['source_kind'],h['source_id'])==pending and h['state'].startswith('pending_')
        if qty!=1 or (h['state']!='free' and not own_pending):
            raise ReceivingError('Posição física partilhada ocupada ou com atribuição pendente. O desconto contabilístico não a liberta.')
    return h


def _emit(db, actor, local, kind, ref_id, stock_id=None, reason='Movimento físico confirmado', ctx=None):
    from .database import PhysicalLocationEvent,context_id,now_utc
    from .receiving import ReceivingError
    h=shared_location(db,local)
    if not h:return
    ctx=context_id(db) if ctx is None else ctx
    t=PhysicalLocationEvent.__table__
    old=db.connection().execute(select(t).where(t.c.physical_id==h['id'],t.c.context_id==ctx,t.c.kind==kind,t.c.ref_id==ref_id)).mappings().first()
    if old:
        if old['stock_id']!=stock_id:raise ReceivingError('Movimento físico já registado com outra identidade.')
        return old['id']
    ident=db.connection().scalar(t.insert().values(context_id=ctx,physical_id=h['id'],kind=kind,ref_id=ref_id,
        stock_id=stock_id,actor_id=actor.id,reason=reason,created_at=now_utc()).returning(t.c.id))
    from .database import LocationClock
    # Trigger updates both owners' clocks; avoid returning stale ORM clock values.
    for value in list(db.identity_map.values()):
        if isinstance(value,LocationClock):db.expire(value)
    return ident


def pending_line_quantity(db,line_id,exclude=None):
    from .database import PhysicalLocationState,Putaway,context_id
    from sqlalchemy import func
    if not _schema(db):return 0
    h=PhysicalLocationState.__table__;p=Putaway.__table__
    q=select(func.coalesce(func.sum(p.c.quantidade),0)).join(h,(h.c.source_id==p.c.id)&(h.c.context_id==p.c.context_id)).where(
        h.c.state=='pending_putaway',p.c.line_id==line_id,p.c.context_id==context_id(db))
    if exclude is not None:q=q.where(p.c.id!=exclude)
    return db.connection().scalar(q)


def putaway_hold(db,actor,record):
    _emit(db,actor,record.local,'putaway_hold',record.id)


def putaway_commit(db,actor,record):
    _emit(db,actor,record.local,'putaway_commit',record.id,record.stock_id)


def transfer_hold(db,actor,record):
    _emit(db,actor,record.destination_local,'transfer_hold',record.id)


def transfer_commit(db,actor,record):
    _emit(db,actor,record.source_local,'transfer_out',record.id,record.source_stock_id)
    _emit(db,actor,record.destination_local,'transfer_in',record.id,record.destination_stock_id)


def dispatch_confirm(db,actor,order,plan):
    for pick in plan:
        _emit(db,actor,pick.local,'dispatch_pending' if order.office else 'dispatch_release',order.id,pick.stock_id)


def dispatch_release(db,actor,order_id,plan,reason):
    from .database import context_id
    for pick in plan:
        h=common_state(db,pick.local)
        if h and h['state']=='awaiting_collection' and h['context_id']==context_id(db) and h['source_id']==order_id and h['stock_id']==pick.stock_id:
            _emit(db,actor,pick.local,'dispatch_release',order_id,pick.stock_id,reason)


def inventory_hold(db,actor,inv,all_scopes,changes):
    from .database import context_id
    wanted={r['scope'].local for r in changes}
    held=[]
    for scope in all_scopes:
        h=common_state(db,scope.local)
        if not h:continue
        own=h['context_id']==context_id(db)
        if scope.local not in wanted and not (own and h['state'] in ('occupied','awaiting_collection')):continue
        _emit(db,actor,scope.local,'inventory_hold',inv.id,reason='Contagem submetida e revalidada para aprovação')
        held.append(scope.local)
    return held


def inventory_commit(db,actor,inv,held,reason):
    for local in held:_emit(db,actor,local,'inventory_commit',inv.id,reason=reason)


# Administrative catalogue commands use the same transaction/authentication wrapper,
# without enabling any client-specific operational workflow.
from .receiving import operation,ReceivingError,required_note,request_key


def activation_snapshot(db, value):
    from sqlalchemy import text
    from .database import PhysicalLocationClaim,Location,Stock,StorageContext,Client,PhysicalLocation
    from .transfer import json_value
    import json
    code=physical_code(value)
    if code is None:raise ReceivingError('Chão é lógico e ilimitado; não é uma estante partilhada.',400)
    con=db.connection();c=StorageContext.__table__;u=Client.__table__;l=Location.__table__;s=Stock.__table__;claim=PhysicalLocationClaim.__table__
    contexts=[dict(r) for r in con.execute(select(c).join(u,u.c.id==c.c.client_id).where(c.c.warehouse_id==SHARING_WAREHOUSE_ID,c.c.client_id.in_(SHARING_OWNER_IDS),c.c.active.is_(True),u.c.active.is_(True)).order_by(c.c.client_id)).mappings()]
    if len(contexts)!=2:raise ReceivingError('São necessários os contextos ativos Iberbag/Ovar e Cosmopak/Ovar.')
    ids=[r['id'] for r in contexts]
    owner=con.scalar(select(claim.c.context_id).where(claim.c.warehouse_id==SHARING_WAREHOUSE_ID,claim.c.code==code))
    if owner not in ids:raise ReceivingError('Código não atribuído a Iberbag/Cosmopak em Ovar. Reconciliar o catálogo antes da partilha.')
    from sqlalchemy import func
    locations=[dict(r) for r in con.execute(select(l).join(c,c.c.id==l.c.context_id).where(c.c.warehouse_id==SHARING_WAREHOUSE_ID,func.lower(func.trim(l.c.codigo))==code).order_by(l.c.id)).mappings()]
    stock=[dict(r) for r in con.execute(select(s).join(c,c.c.id==s.c.context_id).where(c.c.warehouse_id==SHARING_WAREHOUSE_ID,func.lower(func.trim(s.c.local))==code).order_by(s.c.id)).mappings()]
    original=[r for r in locations if r['context_id']==owner]
    if len(original)!=1:raise ReceivingError('Catálogo original ausente/ambíguo. Requer reconciliação.')
    label=original[0]['codigo']
    if label!=label.upper() or any(r['context_id'] not in ids or r['codigo']!=label or r['capacidade']!=1 or not r['active'] for r in locations):
        raise ReceivingError('Capacidade histórica diferente de 1, aliases ou posição inativa. Reconciliar; não será alterada automaticamente.')
    if sum(r['quantidade'] for r in stock)>1 or any(r['context_id'] not in ids or r['local']!=label or r['stacked'] or r['quantidade']>1 for r in stock):
        raise ReceivingError('Stock/identidade física incompatível. Reconciliar antes de ativar a partilha.')
    pending=con.execute(text('''SELECT o.id FROM dispatch_orders o JOIN dispatch_lines d ON d.order_id=o.id JOIN dispatch_picks k ON k.line_id=d.id JOIN storage_contexts c ON c.id=o.context_id
        WHERE c.warehouse_id=:warehouse AND lower(btrim(k.local))=:code AND (o.status IN ('ready','checked','blocked') OR (o.status='confirmed' AND o.office
        AND NOT EXISTS(SELECT 1 FROM preparation_completions x WHERE x.order_id=o.id) AND NOT EXISTS(SELECT 1 FROM preparation_compensations x WHERE x.order_id=o.id)))
        UNION ALL SELECT p.id FROM putaways p JOIN storage_contexts c ON c.id=p.context_id WHERE c.warehouse_id=:warehouse AND lower(btrim(p.local))=:code AND p.status='draft'
        UNION ALL SELECT t.id FROM stock_transfers t JOIN storage_contexts c ON c.id=t.context_id WHERE c.warehouse_id=:warehouse AND (lower(btrim(t.source_local))=:code OR lower(btrim(t.destination_local))=:code) AND t.status='draft'
        UNION ALL SELECT i.id FROM stock_incidents i LEFT JOIN dispatch_picks k ON k.id=i.pick_id LEFT JOIN inventory_scopes sc ON sc.id=i.scope_id JOIN storage_contexts c ON c.id=i.context_id
        WHERE c.warehouse_id=:warehouse AND i.status='open' AND (lower(btrim(k.local))=:code OR lower(btrim(sc.local))=:code)'''),{'code':code,'warehouse':SHARING_WAREHOUSE_ID}).all()
    if pending:raise ReceivingError('Há reservas, recolhas, arrumações, transferências ou ocorrências pendentes. Reconciliar fisicamente antes de ativar.')
    if con.scalar(select(PhysicalLocation.id).where(PhysicalLocation.warehouse_id==SHARING_WAREHOUSE_ID,PhysicalLocation.code==code)):
        raise ReceivingError('Esta posição já tem gestão física comum.')
    value=dict(contexts=contexts,claim=owner,locations=locations,stock=stock)
    return code,label,owner,next(i for i in ids if i!=owner),json.dumps(value,default=json_value,sort_keys=True,ensure_ascii=False)


@operation(operational=False)
def review_sharing(db,actor,key,code,reason,physical_proof=False):
    from .database import PhysicalSharingReview,Audit
    if actor.role!='admin':raise ReceivingError('Apenas Administrador pode ativar partilha.',403)
    reason=required_note(reason,'Conferência física e reconciliação')
    if physical_proof is not True:raise ReceivingError('Confirmar fisicamente código, capacidade e ocupação antes de rever.',400)
    key=request_key(key)
    old=db.scalar(select(PhysicalSharingReview).where(PhysicalSharingReview.request_key==key))
    if old:
        if old.code!=physical_code(code) or old.reason!=reason:raise ReceivingError('Chave usada com outra conferência.')
        return old.id
    code,label,source,other,snapshot=activation_snapshot(db,code)
    row=PhysicalSharingReview(request_key=key,code=code,label=label,source_context_id=source,other_context_id=other,expected=snapshot,reason=reason,created_by=actor.id)
    db.add(row);db.flush();db.add(Audit(username=actor.username,action='REVER_PARTILHA_FISICA',details=f'Posição Ovar/{label}; revisão {row.id}; {reason}'))
    return row.id


@operation(operational=False)
def confirm_sharing(db,actor,ident):
    from .database import PhysicalSharingReview,PhysicalLocation,PhysicalLocationAccess,Location,Stock,Audit,now_utc
    if actor.role!='admin':raise ReceivingError('Apenas Administrador pode ativar partilha.',403)
    r=db.get(PhysicalSharingReview,ident)
    if not r:raise ReceivingError('Revisão inexistente.',404)
    if r.status=='confirmed':return r.id
    code,label,source,other,snapshot=activation_snapshot(db,r.code)
    if snapshot!=r.expected:raise ReceivingError('Catálogo/stock mudou desde a conferência. Reconciliar e criar nova revisão.')
    r.status='confirmed';r.confirmed_by=actor.id;r.confirmed_at=now_utc();db.flush()
    p=PhysicalLocation(warehouse_id=SHARING_WAREHOUSE_ID,code=code,label=label,capacity=1,review_id=r.id);db.add(p);db.flush()
    a=PhysicalLocationAccess.__table__;l=Location.__table__
    db.connection().execute(a.insert(),[dict(physical_id=p.id,context_id=i) for i in (source,other)])
    for ctx in (source,other):
        if not db.connection().scalar(select(l.c.id).where(l.c.context_id==ctx,l.c.codigo==label)):
            db.connection().execute(l.insert().values(context_id=ctx,codigo=label,capacidade=1,active=True))
    s=Stock.__table__
    present=db.connection().execute(select(s).where(s.c.context_id.in_((source,other)),s.c.local==label,s.c.quantidade>0)).mappings().first()
    _emit(db,actor,label,'activate',r.id,present['id'] if present else None,r.reason,ctx=present['context_id'] if present else source)
    db.add(Audit(username=actor.username,action='ATIVAR_PARTILHA_FISICA',details=f'Ovar/{label}; capacidade comum 1; contextos {source}/{other}; revisão {r.id}'))
    return r.id


@operation
def cancel_putaway(db,actor,ident,reason,physically_empty=False):
    from .database import Putaway,Audit
    if actor.role!='admin':raise ReceivingError('Apenas Administrador pode libertar atribuições pendentes.',403)
    reason=required_note(reason,'Prova de posição fisicamente vazia')
    if physically_empty is not True:raise ReceivingError('Confirmar posição fisicamente vazia; não libertar por omissão.',400)
    row=db.get(Putaway,ident)
    if not row or not shared_location(db,row.local):raise ReceivingError('Arrumação partilhada inexistente.',404)
    if row.status=='cancelled':return row.id
    if row.status!='draft':raise ReceivingError('Arrumação confirmada não pode ser cancelada.')
    row.status='cancelled';db.flush();_emit(db,actor,row.local,'putaway_cancel',row.id,reason=reason)
    db.add(Audit(username=actor.username,action='CANCELAR_ATRIBUICAO_FISICA',details=f'Arrumação {row.id}; posição {row.local}; {reason}'))
    return row.id


def validate_shared_snapshot(data):
    """Reconcile the physical projection against immutable events and owner stock.

    Pure validation: never invent occupancy or repair a backup during restoration.
    """
    import json
    masters=data.get('physical_locations',[])
    if not masters:return
    contexts={r['id']:r for r in data['storage_contexts']}
    states={r['physical_id']:r for r in data['physical_location_state']}
    reviews={r['id']:r for r in data['physical_sharing_reviews']}
    claims={(r['warehouse_id'],r['code']):r['context_id'] for r in data['physical_location_claims']}
    for master in masters:
        def invalid():raise ValueError('Ocupação física comum divergente do catálogo, livro ou stock; exigir reconciliação, sem corrigir automaticamente.')
        ids={r['context_id'] for r in data['physical_location_access'] if r['physical_id']==master['id']}
        review=reviews.get(master['review_id']);state=states.get(master['id'])
        if len(ids)!=2 or master['warehouse_id']!=SHARING_WAREHOUSE_ID or master['capacity']!=1 or physical_code(master['label'])!=master['code'] or master['code'] is None:invalid()
        if any(i not in contexts or contexts[i]['warehouse_id']!=SHARING_WAREHOUSE_ID for i in ids) or {contexts[i]['client_id'] for i in ids}!=set(SHARING_OWNER_IDS):invalid()
        if not review or review['status']!='confirmed' or {review['source_context_id'],review['other_context_id']}!=ids or claims.get((SHARING_WAREHOUSE_ID,master['code'])) not in ids:invalid()
        for ctx in ids:
            bindings=[r for r in data['locations'] if r['context_id']==ctx and physical_code(r['codigo'])==master['code']]
            if len(bindings)!=1 or bindings[0]['codigo']!=master['label'] or bindings[0]['capacidade']!=1 or not bindings[0]['active']:invalid()
        events=[r for r in data['physical_location_events'] if r['physical_id']==master['id']]
        if not state or not events:invalid()
        latest=max(events,key=lambda r:r['id'])
        try:expected=json.loads(latest['after_state'])
        except (ValueError,TypeError):invalid()
        if expected!=state or state['version']!=len(events):invalid()
        goods=[r for r in data['stock'] if contexts[r['context_id']]['warehouse_id']==SHARING_WAREHOUSE_ID and physical_code(r['local'])==master['code'] and r['quantidade']>0]
        if sum(r['quantidade'] for r in goods)>1 or any(r['context_id'] not in ids or r['stacked'] or r['local']!=master['label'] for r in goods):invalid()
        if state['state']=='free':
            if goods or state['context_id'] is not None or state['stock_id'] is not None:invalid()
        else:
            if state['context_id'] not in ids:invalid()
            if state['state']=='occupied':
                if len(goods)!=1 or goods[0]['id']!=state['stock_id'] or goods[0]['context_id']!=state['context_id']:invalid()
            elif state['state'] in ('pending_putaway','pending_transfer','awaiting_collection') and goods:invalid()
            if state['state']=='pending_putaway':
                if state['source_kind']=='cosmopak':
                    pending=next((r for r in data.get('cosmo_states',[]) if r['pallet_id']==state['source_id'] and r['context_id']==state['context_id']),None)
                    if not pending or pending['status'] not in ('placed','blocked') or pending['local']!=master['label']:invalid()
                    continue
                pending=next((r for r in data['putaways'] if r['id']==state['source_id'] and r['context_id']==state['context_id']),None)
                if not pending or pending['status']!='draft' or pending['local']!=master['label'] or pending['quantidade']!=1 or pending['stacked']:invalid()
            if state['state']=='awaiting_collection':
                order=next((r for r in data['dispatch_orders'] if r['id']==state['source_id'] and r['context_id']==state['context_id']),None)
                if not order or order['status']!='confirmed' or not order['office'] or any(r['order_id']==order['id'] for r in data['preparation_completions']+data['preparation_compensations']):invalid()
