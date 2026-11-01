"""Office inventory, administrative adjustments and incident resolution forms."""
import csv
import json
from collections import defaultdict
from io import StringIO
import secrets
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from .app import database, identity, context, render, redirect
from .receiving_routes import receiving_form, form_actor, failure, integer
from . import inventorying as service
from .database import (PhysicalInventory, InventoryScope, InventoryCount, StockIncident, StockAdjustment,
                       DispatchPick, DispatchLine, DispatchOrder)

router=APIRouter()


@router.get('/inventory')
def index(request:Request,page:int=1,db:Session=Depends(database)):
    user,sess=identity(request,db)
    if not user:return redirect('/login')
    page=max(1,page)
    rows=db.scalars(select(PhysicalInventory).order_by(PhysicalInventory.id.desc()).offset((page-1)*50).limit(50)).all()
    return render(request,'inventory_index.html',context(request,user,sess,rows=rows,page=page,request_key=secrets.token_hex(16)))


@router.post('/inventory/create')
def create(request:Request,data:dict=Depends(receiving_form),db:Session=Depends(database)):
    user,sess=form_actor(request,db,data)
    try:
        ident=service.create_inventory(db,user.id,data.get('request_key'),data.get('reference'),data.get('kind'),
            [code.strip() for code in data.get('locations','').splitlines() if code.strip()])
        return redirect(f'/inventory/{ident}')
    except service.InventoryError as error:return failure(request,user,sess,error)


@router.get('/inventory/incidents')
def incidents(request:Request,page:int=1,db:Session=Depends(database)):
    user,sess=identity(request,db)
    if not user:return redirect('/login')
    page=max(1,page)
    cases=db.scalars(select(StockIncident).order_by(StockIncident.id.desc()).offset((page-1)*50).limit(50)).all()
    rows=[]
    for case in cases:
        pick=db.get(DispatchPick,case.pick_id) if case.pick_id else None
        line=db.get(DispatchLine,pick.line_id) if pick else None
        scope=db.get(InventoryScope,case.scope_id) if case.scope_id else None
        rows.append(dict(case=case,pick=pick,line=line,scope=scope))
    pending=db.scalars(select(DispatchOrder).where(DispatchOrder.status.in_(('draft','ready','checked','blocked'))).order_by(DispatchOrder.id)).all()
    return render(request,'incidents.html',context(request,user,sess,rows=rows,pending=pending,page=page))


@router.post('/inventory/incidents')
def report(request:Request,data:dict=Depends(receiving_form),db:Session=Depends(database)):
    user,sess=form_actor(request,db,data)
    try:
        service.report_shortage(db,user.id,data.get('request_key'),integer(data.get('pick_id')),data.get('quantidade'),data.get('reason'),data.get('observation',''))
        return redirect('/inventory/incidents')
    except service.InventoryError as error:return failure(request,user,sess,error)


@router.post('/inventory/incidents/{ident}/resolve')
def resolve_incident(ident:int,request:Request,data:dict=Depends(receiving_form),db:Session=Depends(database)):
    user,sess=form_actor(request,db,data,require_admin=True)
    try:
        service.resolve_incident(db,user.id,ident,data.get('reason'),integer(data['inventory_id']) if data.get('inventory_id') else None)
        return redirect('/inventory/incidents')
    except service.InventoryError as error:return failure(request,user,sess,error)


@router.post('/inventory/lots/resolve')
def resolve_lot(request:Request,data:dict=Depends(receiving_form),db:Session=Depends(database)):
    user,sess=form_actor(request,db,data,require_admin=True)
    try:
        service.resolve_lot(db,user.id,data.get('lote'),data.get('artigo'),data.get('reason'))
        return redirect('/inventory/incidents')
    except service.InventoryError as error:return failure(request,user,sess,error)


@router.post('/inventory/preparations/{ident}/complete')
def complete(ident:int,request:Request,data:dict=Depends(receiving_form),db:Session=Depends(database)):
    user,sess=form_actor(request,db,data,require_admin=True)
    try:
        service.close_preparation(db,user.id,ident,data.get('reason'))
        return redirect(f'/dispatches/{ident}')
    except service.InventoryError as error:return failure(request,user,sess,error)


@router.get('/inventory/adjustments')
def adjustments(request:Request,page:int=1,db:Session=Depends(database)):
    user,sess=identity(request,db)
    if not user:return redirect('/login')
    rows=db.scalars(select(StockAdjustment).order_by(StockAdjustment.id.desc()).offset((max(1,page)-1)*50).limit(50)).all()
    return render(request,'adjustments.html',context(request,user,sess,rows=rows,page=max(1,page)))


@router.get('/inventory/scope/{ident}')
def scope(ident:int,request:Request,db:Session=Depends(database)):
    import json
    user,sess=identity(request,db)
    if not user:return redirect('/login')
    item=db.get(InventoryScope,ident)
    if not item:raise HTTPException(404)
    rows=service.counts(db,item.id)
    output=StringIO(); writer=csv.writer(output,delimiter=';',lineterminator='\n')
    for row in rows:writer.writerow([row.artigo or '',row.lote or '',row.carga or '',row.quantidade])
    return render(request,'inventory_scope.html',context(request,user,sess,scope=item,
        inv=db.get(PhysicalInventory,item.inventory_id),baseline=json.loads(item.baseline),physical=output.getvalue(),
        stale=service.clock(db,item.local)!=item.clock,pending=any(row.pending for row in rows)))


@router.post('/inventory/scope/{ident}')
def count(ident:int,request:Request,data:dict=Depends(receiving_form),db:Session=Depends(database)):
    user,sess=form_actor(request,db,data)
    try:
        rows=[]
        for row in csv.reader(StringIO(data.get('physical','')),delimiter=';'):
            if not any(value.strip() for value in row):continue
            if len(row)!=4:raise service.InventoryError('Cada linha deve ter artigo;lote;carga;paletes.',400)
            rows.append(tuple(value.strip() for value in row))
        inv=service.save_count(db,user.id,ident,integer(data.get('version')),rows,data.get('note',''),data.get('empty')=='1')
        return redirect(f'/inventory/{inv}')
    except (csv.Error,service.InventoryError) as error:
        if isinstance(error,csv.Error):error=service.InventoryError('Formato de contagem inválido.',400)
        return failure(request,user,sess,error)


@router.get('/inventory/{ident}')
def detail(ident:int,request:Request,page:int=1,db:Session=Depends(database)):
    user,sess=identity(request,db)
    if not user:return redirect('/login')
    inv=db.get(PhysicalInventory,ident)
    if not inv:raise HTTPException(404)
    all_scopes=service.scopes(db,ident); page=max(1,page)
    grouped=defaultdict(list)
    for row in db.scalars(select(InventoryCount).join(InventoryScope).where(InventoryScope.inventory_id==ident)):
        grouped[row.scope_id].append(row)
    totals={scope.id:dict(system=sum(r['quantidade'] for r in json.loads(scope.baseline)),physical=sum(r.quantidade for r in grouped[scope.id]) if scope.counted else None,pending=any(r.pending for r in grouped[scope.id])) for scope in all_scopes}
    summary=dict(system=sum(row['system'] for row in totals.values()),physical=sum(row['physical'] for row in totals.values()) if all(s.counted for s in all_scopes) else None)
    return render(request,'inventory_detail.html',context(request,user,sess,inv=inv,
        scopes=all_scopes[(page-1)*50:page*50],page=page,totals=totals,summary=summary,total_pages=max(1,(len(all_scopes)+49)//50),
        uncounted=sum(not scope.counted for scope in all_scopes),differences=service.differences(db,ident),
        pending_rows=[(scope,row) for scope in all_scopes for row in grouped[scope.id] if row.pending]))


@router.post('/inventory/{ident}/submit')
def submit(ident:int,request:Request,data:dict=Depends(receiving_form),db:Session=Depends(database)):
    user,sess=form_actor(request,db,data)
    try:
        service.submit_inventory(db,user.id,ident)
        return redirect(f'/inventory/{ident}')
    except service.InventoryError as error:return failure(request,user,sess,error)


@router.post('/inventory/{ident}/approve')
def approve(ident:int,request:Request,data:dict=Depends(receiving_form),db:Session=Depends(database)):
    user,sess=form_actor(request,db,data,require_admin=True)
    try:
        service.approve_inventory(db,user.id,ident,data.get('reason'),integer(data['compensate_order']) if data.get('compensate_order') else None)
        return redirect(f'/inventory/{ident}')
    except service.InventoryError as error:return failure(request,user,sess,error)
