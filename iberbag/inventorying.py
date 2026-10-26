"""Physical counts and admin adjustments. Counts never imply stock changes without approval."""
import json
from sqlalchemy import select, func, delete, or_, text
from .database import (Stock, Location, LocationClock, PhysicalInventory, InventoryScope, InventoryCount,
    StockAdjustment, StockIncident, Carga, Audit, DispatchOrder, DispatchPick, DispatchLine,
    DispatchDocument, PreparationCompletion, PreparationCompensation, LotResolution, now_utc)
from .receiving import operation, ReceivingError as InventoryError, required, required_note, quantity, request_key, MAX_INT
from .location_rules import is_floor
from .warehouse_settings import validate_location, WarehouseError
from .dispatching import ACTIVE
from .lots import assert_lot, resolve


def clock(db, local):
    value=db.get(LocationClock,local)
    return value.version if value else 0


def touch_location(db,local):
    db.execute(text('INSERT INTO location_clocks(local,version) VALUES(:local,1) ON CONFLICT(local) DO UPDATE SET version=location_clocks.version+1'),{'local':local})
    value=db.identity_map.get((LocationClock,(local,),None))
    if value is not None: db.expire(value)


def admin(actor):
    if actor.role!='admin': raise InventoryError('Apenas Administrador pode aprovar ou resolver.',403)


def snapshot(db,local):
    rows=db.scalars(select(Stock).where(Stock.local==local,Stock.quantidade>0).order_by(Stock.id)).all()
    return [dict(artigo=s.artigo,lote=s.lote,carga=s.carga,quantidade=s.quantidade) for s in rows]


def scopes(db,inventory_id):
    return db.scalars(select(InventoryScope).where(InventoryScope.inventory_id==inventory_id).order_by(InventoryScope.local)).all()


def counts(db,scope_id):
    return db.scalars(select(InventoryCount).where(InventoryCount.scope_id==scope_id).order_by(InventoryCount.id)).all()


def check_clock(db,scope):
    if clock(db,scope.local)!=scope.clock:
        raise InventoryError(f'{scope.local}: houve movimentos/alterações desde o início. Criar nova contagem; não substituir stock recente.')


@operation
def create_inventory(db,actor,key,reference,kind,selected):
    key=request_key(key); reference=required(reference,'Referência')
    previous=db.scalar(select(PhysicalInventory).where(PhysicalInventory.request_key==key))
    if previous:
        if previous.created_by!=actor.id or previous.reference!=reference or previous.kind!=kind:
            raise InventoryError('Pedido de inventário já utilizado.')
        original={s.local for s in scopes(db,previous.id)}
        if kind=='partial' and original!=set(selected): raise InventoryError('Âmbito diferente para a mesma chave.')
        return previous.id
    if kind=='full':
        selected=set(db.scalars(select(Location.codigo).where(Location.active.is_(True))))
        selected |= set(db.scalars(select(Stock.local).where(Stock.quantidade>0)))
        floors=set(code for code in db.scalars(select(Location.codigo)) if is_floor(code))
        selected |= floors or {'Chão'}
    elif kind=='partial':
        selected=set(required(code,'Localização') for code in selected)
        if not selected: raise InventoryError('Selecionar pelo menos uma localização.',400)
        for code in selected:
            if not db.scalar(select(Location.id).where(Location.codigo==code)) and not db.scalar(select(Stock.id).where(Stock.local==code,Stock.quantidade>0)):
                raise InventoryError('Localização fora do catálogo e sem stock para conferir: '+code)
    else: raise InventoryError('Tipo de inventário inválido.',400)
    inv=PhysicalInventory(request_key=key,reference=reference,kind=kind,created_by=actor.id)
    db.add(inv); db.flush()
    for local in sorted(selected):
        db.add(InventoryScope(inventory_id=inv.id,local=local,clock=clock(db,local),baseline=json.dumps(snapshot(db,local),ensure_ascii=False)))
    db.add(Audit(username=actor.username,action='CRIAR_INVENTARIO',details=f'Inventário {inv.id}; {kind}; {len(selected)} localizações.'))
    return inv.id


@operation
def save_count(db,actor,scope_id,version,rows,note='',empty=False):
    scope=db.get(InventoryScope,scope_id)
    if scope is None: raise InventoryError('Localização de inventário inexistente.',404)
    inv=db.get(PhysicalInventory,scope.inventory_id)
    if inv.status!='draft' or scope.version!=version: raise InventoryError('Contagem submetida ou versão desatualizada.')
    check_clock(db,scope)
    if empty and rows: raise InventoryError('Uma localização vazia não pode ter grupos contados.',400)
    if not empty and not rows: raise InventoryError('Assinalar vazia ou indicar grupos. Não contada não significa zero.',400)
    if len(note)>1000: raise InventoryError('Observação demasiado longa.',400)
    entries=[]; known=set()
    for artigo,lote,carga,qty in rows:
        artigo=required(artigo,'Artigo') if artigo.strip() else None
        lote=required(lote,'Lote') if lote.strip() else None
        carga=required(carga,'Carga') if carga.strip() else None
        qty=quantity(qty); pending=not all((artigo,lote,carga))
        if not pending:
            key=(artigo,lote,carga)
            if key in known: raise InventoryError('Agrupar linhas repetidas da contagem.',400)
            known.add(key)
            try: assert_lot(db,lote,artigo)
            except InventoryError: pending=True
            if not db.scalar(select(Carga.id).where(Carga.codigo==carga)): pending=True
        if pending and not note.strip(): raise InventoryError('Identidade desconhecida/contraditória exige observação para investigação.',400)
        entries.append(dict(artigo=artigo,lote=lote,carga=carga,quantidade=qty,pending=pending))
    db.execute(delete(InventoryCount).where(InventoryCount.scope_id==scope.id))
    for row in entries: db.add(InventoryCount(scope_id=scope.id,**row))
    scope.counted=True; scope.version+=1
    scope.counted_by,scope.counted_at,scope.note=actor.username,now_utc(),note
    db.add(Audit(username=actor.username,action='CONTAGEM_FISICA',details=json.dumps(dict(inventario=inv.id,local=scope.local,versao=scope.version,vazia=empty,linhas=entries,nota=note),ensure_ascii=False)))
    return inv.id


@operation
def submit_inventory(db,actor,inventory_id):
    inv=db.get(PhysicalInventory,inventory_id)
    if inv is None: raise InventoryError('Inventário inexistente.',404)
    if inv.status in ('submitted','applied'): return inv.id
    for scope in scopes(db,inv.id):
        if not scope.counted: raise InventoryError('Falta contar '+scope.local+'. Uma localização desconhecida não é vazia.')
        check_clock(db,scope)
        pending=[row for row in counts(db,scope.id) if row.pending]
        if pending:
            db.add(StockIncident(request_key='inventory-'+str(scope.id),scope_id=scope.id,
                quantidade=sum(row.quantidade for row in pending),reason='Identidade por esclarecer: '+(scope.note or ''),actor=actor.username))
    inv.status='submitted'; inv.submitted_by,inv.submitted_at=actor.username,now_utc()
    db.add(Audit(username=actor.username,action='SUBMETER_INVENTARIO',details=f'Inventário {inv.id}; stock inalterado.'))
    return inv.id


def differences(db,inventory_id):
    result=[]
    for scope in scopes(db,inventory_id):
        baseline={(r['artigo'],r['lote'],r['carga']):r['quantidade'] for r in json.loads(scope.baseline)}
        physical_rows=counts(db,scope.id)
        uncertain=any(row.pending for row in physical_rows)
        physical={(r.artigo,r.lote,r.carga):r.quantidade for r in physical_rows if not r.pending}
        for key in sorted(set(baseline)|set(physical)):
            before=baseline.get(key,0); after=physical.get(key,0) if scope.counted and not uncertain else None
            result.append(dict(scope=scope,artigo=key[0],lote=key[1],carga=key[2],before=before,after=after,
                               delta=None if after is None else after-before))
    return result


def protect_reservations(db,artigo,lote):
    busy=db.scalar(select(DispatchOrder.id).join(DispatchLine).where(DispatchOrder.status.in_(ACTIVE),DispatchLine.artigo==artigo,DispatchLine.lote==lote).limit(1))
    if busy: raise InventoryError(f'Ajuste bloqueado pela reserva do pedido #{busy}. Cancelar/resolver administrativamente primeiro.')


def open_preparations(db,row,except_order=None):
    query=select(DispatchOrder.id).join(DispatchDocument,DispatchDocument.order_id==DispatchOrder.id).join(DispatchLine).join(DispatchPick).outerjoin(
        PreparationCompletion,PreparationCompletion.order_id==DispatchOrder.id).outerjoin(
        PreparationCompensation,PreparationCompensation.order_id==DispatchOrder.id).where(
        DispatchOrder.status=='confirmed',PreparationCompletion.order_id.is_(None),
        PreparationCompensation.order_id.is_(None),DispatchLine.artigo==row['artigo'],DispatchLine.lote==row['lote'],
        DispatchPick.carga==row['carga'],DispatchPick.local==row['scope'].local)
    if except_order is not None: query=query.where(DispatchOrder.id!=except_order)
    return db.scalar(query.limit(1))


@operation
def approve_inventory(db,actor,inventory_id,reason,compensate_order=None):
    admin(actor); reason=required_note(reason,'Motivo obrigatório')
    inv=db.get(PhysicalInventory,inventory_id)
    if inv is None: raise InventoryError('Inventário inexistente.',404)
    if inv.status=='applied':
        existing=db.scalar(select(PreparationCompensation.order_id).where(PreparationCompensation.inventory_id==inv.id))
        if existing!=compensate_order: raise InventoryError('Inventário já aplicado com outro tipo de operação.')
        return inv.id
    if inv.status!='submitted': raise InventoryError('Submeter e rever o inventário antes de aprovar.')
    all_scopes=scopes(db,inv.id)
    if inv.kind=='full':
        required_now=set(db.scalars(select(Location.codigo).where(Location.active.is_(True))))|set(db.scalars(select(Stock.local).where(Stock.quantidade>0)))
        if not required_now.issubset({s.local for s in all_scopes}): raise InventoryError('O âmbito completo mudou. Criar novo inventário.')
    for scope in all_scopes:
        check_clock(db,scope)
        rows=counts(db,scope.id)
        if not scope.counted or any(row.pending for row in rows): raise InventoryError('Contagem desconhecida ou ocorrência pendente; nova conferência identificada é necessária.')
        total=sum(row.quantidade for row in rows)
        if not is_floor(scope.local) and total>1: raise InventoryError('Capacidade física excedida em '+scope.local)
    changes=[r for r in differences(db,inv.id) if r['delta']]
    if compensate_order is not None:
        from .dispatching import picks,lines
        order=db.get(DispatchOrder,compensate_order); doc=db.get(DispatchDocument,compensate_order)
        if not order or order.status!='confirmed' or not order.office or not doc: raise InventoryError('Sem documento/versões originais para reversão segura. Exige reconciliação.')
        if db.get(PreparationCompensation,order.id): raise InventoryError('Preparação já compensada.')
        versions=json.loads(doc.stock_versions)
        pick_locations=set(p.local for p in picks(db,order.id))
        if not versions or set(versions)!=pick_locations: raise InventoryError('Sem versões comprovadas na confirmação original; exigir reconciliação administrativa.')
        if any(clock(db,local)!=value for local,value in versions.items()): raise InventoryError('Existem movimentos posteriores à preparação. Compensação automática bloqueada; reconciliar.')
        by_line={line.id:line for line in lines(db,order.id)}
        expected={}
        for pick in picks(db,order.id):
            original=db.scalar(select(Carga).where(Carga.codigo==pick.carga))
            if original is None or original.entrada!=pick.entrada: raise InventoryError('Data/carga alterada desde a preparação. Compensação exige reconciliação.')
            line=by_line[pick.line_id]; key=(line.artigo,line.lote,pick.carga,pick.local)
            expected[key]=expected.get(key,0)+pick.quantidade
        actual={(r['artigo'],r['lote'],r['carga'],r['scope'].local):r['delta'] for r in changes}
        if expected!=actual: raise InventoryError('A contagem física não comprova a restituição exata de toda a preparação.')
    for row in changes:
        protect_reservations(db,row['artigo'],row['lote'])
        if row['delta']>0:
            assert_lot(db,row['lote'],row['artigo'])
            carga=db.scalar(select(Carga).where(Carga.codigo==row['carga']))
            if carga is None or carga.entrada is None: raise InventoryError('Ajuste positivo exige carga real e data conhecida; esclarecer sem criar stock.')
            if compensate_order is None:
                from .database import Receipt,ReceiptLine
                from .receiving import remaining
                pending_lines=db.scalars(select(ReceiptLine).join(Receipt).where(Receipt.status=='confirmed',Receipt.carga_id==carga.id,ReceiptLine.artigo==row['artigo'],ReceiptLine.lote==row['lote'])).all()
                if any(remaining(db,line)>0 for line in pending_lines):
                    raise InventoryError('Há receção por arrumar desta carga/grupo. Usar arrumação e nova conferência, sem duplicar paletes por ajuste.')
            location=db.scalar(select(Location).where(Location.codigo==row['scope'].local))
            if not location or not location.active: raise InventoryError('Destino do ajuste inexistente/inativo.')
            if not is_floor(location.codigo):
                try: validate_location(db,location.codigo)
                except WarehouseError as error: raise InventoryError(str(error)) from error
            if open_preparations(db,row,compensate_order): raise InventoryError('Há preparação impressa ainda não concluída fisicamente. Não duplicar paletes já descontadas.')
        if not 0<=row['after']<=MAX_INT: raise InventoryError('Quantidade fora do limite técnico.')
    for row in changes:
        stock=db.scalar(select(Stock).where(Stock.artigo==row['artigo'],Stock.lote==row['lote'],Stock.carga==row['carga'],Stock.local==row['scope'].local).with_for_update())
        if stock is None:
            stock=Stock(artigo=row['artigo'],lote=row['lote'],carga=row['carga'],local=row['scope'].local,quantidade=row['after'])
            db.add(stock)
        else:
            if stock.quantidade!=row['before']: raise InventoryError('Saldo mudou durante a aprovação. Reconciliar novamente.')
            stock.quantidade=row['after']
        db.flush()
        db.add(StockAdjustment(inventory_id=inv.id,stock_id=stock.id,artigo=stock.artigo,lote=stock.lote,carga=stock.carga,local=stock.local,
            before=row['before'],after=row['after'],delta=row['delta'],reason=reason,actor=actor.username))
    inv.status='applied'; inv.applied_by,inv.applied_at,inv.reason=actor.username,now_utc(),reason
    if compensate_order is not None:
        db.add(PreparationCompensation(order_id=compensate_order,inventory_id=inv.id,reason=reason,actor=actor.username))
    db.add(Audit(username=actor.username,action='APROVAR_AJUSTES',details=f'Inventário {inv.id}; {len(changes)} diferenças; {reason}; compensação={compensate_order}.'))
    return inv.id


@operation
def report_shortage(db,actor,key,pick_id,qty,reason,observation=''):
    key=request_key(key); qty=quantity(qty); reason=required_note(reason,'Motivo')
    observation=(observation or '').strip()
    if len(observation)>2000: raise InventoryError('Observação demasiado longa.',400)
    old=db.scalar(select(StockIncident).where(StockIncident.request_key==key))
    if old:
        if (old.pick_id,old.quantidade,old.reason,old.actor,old.observation or '')!=(pick_id,qty,reason,actor.username,observation): raise InventoryError('Chave já utilizada com outros dados.')
        return old.id
    pick=db.get(DispatchPick,pick_id)
    if not pick: raise InventoryError('Grupo inexistente.',404)
    line=db.get(DispatchLine,pick.line_id); order=db.get(DispatchOrder,line.order_id)
    if order.status!='confirmed': raise InventoryError('Ocorrência pós-impressão exige preparação confirmada.')
    total=db.scalar(select(func.coalesce(func.sum(StockIncident.quantidade),0)).where(StockIncident.pick_id==pick.id))
    if total+qty>pick.quantidade: raise InventoryError('Faltas comunicadas excedem a quantidade dessa linha impressa.')
    incident=StockIncident(request_key=key,pick_id=pick.id,quantidade=qty,reason=reason,observation=observation,actor=actor.username)
    db.add(incident); db.flush()
    touch_location(db,pick.local)
    db.add(Audit(username=actor.username,action='DIVERGENCIA_POS_IMPRESSAO',details=f'Ocorrência {incident.id}; preparação {order.id}; stock inalterado.'))
    return incident.id


@operation
def close_preparation(db,actor,order_id,reason):
    admin(actor); reason=required_note(reason,'Comprovação física')
    order=db.get(DispatchOrder,order_id)
    if not order or order.status!='confirmed': raise InventoryError('Preparação não confirmada.')
    if db.get(PreparationCompletion,order_id): return order_id
    open_case=db.scalar(select(StockIncident.id).join(DispatchPick,DispatchPick.id==StockIncident.pick_id).join(DispatchLine).where(DispatchLine.order_id==order_id,StockIncident.status=='open'))
    if open_case: raise InventoryError('Há divergências por resolver; não marcar execução física concluída.')
    for local in set(db.scalars(select(DispatchPick.local).join(DispatchLine).where(DispatchLine.order_id==order_id))):
        touch_location(db,local)
    db.add(PreparationCompletion(order_id=order_id,reason=reason,actor=actor.username))
    db.add(Audit(username=actor.username,action='EXECUCAO_FISICA',details=f'Preparação {order_id}; {reason}; stock inalterado.'))
    return order_id


@operation
def resolve_incident(db,actor,incident_id,reason,inventory_id=None):
    admin(actor); reason=required_note(reason,'Resolução fundamentada')
    incident=db.get(StockIncident,incident_id)
    if not incident: raise InventoryError('Ocorrência inexistente.',404)
    if incident.status=='resolved': return incident.id
    if inventory_id:
        inv=db.get(PhysicalInventory,inventory_id)
        if not inv or inv.status!='applied': raise InventoryError('Referenciar inventário aprovado, não uma contagem sem tratamento.')
        if incident.pick_id:
            pick=db.get(DispatchPick,incident.pick_id)
            if not db.scalar(select(InventoryScope.id).where(InventoryScope.inventory_id==inv.id,InventoryScope.local==pick.local)):
                raise InventoryError('Inventário fora da localização afetada.')
        elif incident.scope_id:
            old=db.get(InventoryScope,incident.scope_id)
            if not db.scalar(select(InventoryScope.id).where(InventoryScope.inventory_id==inv.id,InventoryScope.local==old.local)):
                raise InventoryError('Inventário fora do âmbito da ocorrência.')
    # No-stock decision is explicit; this endpoint never changes stock.
    incident.status='resolved'; incident.resolution=reason; incident.resolved_by=actor.username
    incident.resolved_at,incident.inventory_id=now_utc(),inventory_id
    db.add(Audit(username=actor.username,action='RESOLVER_OCORRENCIA',details=f'Ocorrência {incident.id}; inventário={inventory_id}; {reason}; sem ajuste automático.'))
    return incident.id


@operation
def resolve_lot(db,actor,lote,artigo,reason):
    admin(actor); lote=required(lote,'Lote'); artigo=required(artigo,'Artigo'); reason=required_note(reason,'Motivo documentado')
    from .lots import articles
    if artigo not in articles(db,lote): raise InventoryError('O artigo deve constar do histórico documentado do lote.')
    latest=db.scalar(select(LotResolution).where(LotResolution.lote==lote).order_by(LotResolution.id.desc()).limit(1))
    if latest and latest.artigo==artigo and latest.reason==reason: return lote
    # An append-only adjudication does not release preparation while wrong positive stock exists.
    for local in set(db.scalars(select(Stock.local).where(Stock.lote==lote))): touch_location(db,local)
    db.add(LotResolution(lote=lote,artigo=artigo,reason=reason,actor=actor.username))
    db.add(Audit(username=actor.username,action='RECONCILIAR_LOTE',details=f'{lote} → {artigo}; {reason}.'))
    return lote
