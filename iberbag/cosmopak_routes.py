"""Office UI and reusable authenticated services for future online PWA calls."""
import json,secrets
from fastapi import APIRouter,Request,Depends,HTTPException
from fastapi.responses import Response
from sqlalchemy import select,func
from .app import database,identity,render,context,redirect
from .receiving_routes import form_actor,receiving_form,failure,integer
from .valmet_import_upload import named_upload_form
from . import cosmopak as service,physical_locations
from .database import Receipt
from .database import CosmoReception as Reception,CosmoPallet as Pallet,CosmoState as State,CosmoReview as Review,CosmoOperation as Operation
router=APIRouter()

def access(request,db,data=None,admin=False):
    if not service.enabled(db):raise HTTPException(403,'Contexto Cosmopak/Ovar obrigatório.')
    user,sess=form_actor(request,db,data,require_admin=admin,operational=False) if data is not None else identity(request,db)
    if not user:raise HTTPException(403,'Autenticação obrigatória.')
    if not service.enabled(db) or (admin and user.role!='admin'):raise HTTPException(403,'Contexto/permissão Cosmopak inválidos.')
    return user,sess

@router.get('/cosmopak/receptions')
def index(request:Request,db=Depends(database)):
    user,sess=access(request,db)
    return render(request,'cosmopak_receptions.html',context(request,user,sess,key=secrets.token_hex(16),rows=db.scalars(select(Reception).order_by(Reception.id.desc()).limit(100)).all()))

@router.post('/cosmopak/receptions/upload')
def upload(request:Request,data:dict=Depends(named_upload_form),db=Depends(database)):
    user,sess=access(request,db,data,True);actor=user.id;db.rollback()
    try:
        ident=service.preview_file(db,actor,data['request_key'],data['excel'],data['source_name'],reauthorize=lambda:access(request,db,data,True))
        return redirect(f'/cosmopak/receptions/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.get('/cosmopak/receptions/{ident}')
def detail(ident:int,request:Request,history_page:int=1,db=Depends(database)):
    user,sess=access(request,db)
    try:rec=service.record(db,ident)
    except service.ReceivingError as error:raise HTTPException(error.status,str(error))
    reviews=db.scalars(select(Review).where(Review.reception_id==ident).order_by(Review.id.desc())).all()
    last=next((r for r in reviews if r.kind=='review'),None)
    units=service.pallets(db,ident)
    summary=dict(accepted=sum(s.decision=='accept' and s.status in ('placed','approved') for p,s in units),boxes=sum(s.actual_boxes or 0 for p,s in units if s.decision=='accept' and s.status in ('placed','approved')),missing=sum(s.status=='missing' for p,s in units),unresolved=sum(s.decision=='unresolved' or s.status in ('planned','blocked') for p,s in units))
    ids=[p.id for p,s in units]
    history_page=max(1,min(history_page,10000))
    history_total=db.scalar(select(func.count(Operation.id)).where(Operation.pallet_id.in_(ids)))
    operations=db.scalars(select(Operation).where(Operation.pallet_id.in_(ids)).order_by(Operation.id.desc()).offset((history_page-1)*100).limit(100)).all()
    return render(request,'cosmopak_reception.html',context(request,user,sess,rec=rec,source=json.loads(rec.report),pallets=units,summary=summary,receipt=db.get(Receipt,rec.receipt_id) if rec.receipt_id else None,pallet_keys={p.id:[secrets.token_hex(16),secrets.token_hex(16)] for p,s in units},reviews=reviews,operations=operations,history_page=history_page,history_pages=max(1,(history_total+99)//100),review=last,keys=[secrets.token_hex(16) for _ in range(5)],places=physical_locations.physical_statuses(db)))

@router.post('/cosmopak/receptions/{ident}/{action}')
def revise(ident:int,action:str,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=access(request,db,data,action!='unloaded')
    try:
        key=data.get('request_key');version=integer(data.get('version'));reason=data.get('reason')
        if action=='metadata':service.metadata(db,user.id,key,ident,version,data.get('guide'),data.get('arrived_on'),data.get('legacy_code'),reason)
        elif action=='unloaded':service.finish_unloading(db,user.id,key,ident,version,reason)
        elif action=='review':service.review(db,user.id,key,ident,version,reason)
        elif action=='approve':service.approve(db,user.id,key,ident,integer(data.get('review_id')),data.get('accepted')=='yes')
        else:raise HTTPException(404,'Operação inexistente.')
        return redirect(f'/cosmopak/receptions/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.get('/cosmopak/pallets/{ident}')
def pallet_detail(ident:int,request:Request,db=Depends(database)):
    access(request,db)
    row=db.get(Pallet,ident)
    if not row:raise HTTPException(404,'Palete inexistente neste contexto.')
    return redirect(f'/cosmopak/receptions/{row.reception_id}')

@router.post('/cosmopak/pallets/{ident}')
def pallet(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=access(request,db,data,data.get('kind')=='decide')
    row=db.get(Pallet,ident)
    if not row:raise HTTPException(404,'Palete inexistente neste contexto.')
    reception=row.reception_id
    try:
        service.change_pallet(db,user.id,data.get('request_key'),ident,integer(data.get('version')),data.get('kind'),data.get('local'),data.get('actual_boxes'),data.get('reason'),data.get('vacated')=='yes',data.get('decision'))
        return redirect(f'/cosmopak/receptions/{reception}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.get('/cosmopak/stock')
def stock(request:Request,article:str='',lot:str='',local:str='',db=Depends(database)):
    user,sess=access(request,db)
    query=select(Pallet,State).join(State,State.pallet_id==Pallet.id).where(State.status.in_(['placed','blocked','approved']))
    for column,value in ((Pallet.article,article),(Pallet.lot,lot),(State.local,local)):
        if value:query=query.where(column.contains(value[:120],autoescape=True))
    rows=db.execute(query.order_by(State.local,Pallet.id).limit(1000)).all()
    return render(request,'cosmopak_stock.html',context(request,user,sess,rows=rows,article=article,lot=lot,local=local))

@router.get('/cosmopak/receptions/{ident}/source.xlsx')
def source(ident:int,request:Request,db=Depends(database)):
    access(request,db,admin=True)
    try:rec=service.record(db,ident)
    except service.ReceivingError as error:raise HTTPException(error.status,str(error))
    return Response(rec.source_excel,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':f'attachment; filename="cosmopak-origem-{ident}.xlsx"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
