"""Administrative opt-in reconciliation and common occupancy, using existing forms."""
import secrets
from fastapi import APIRouter,Depends,Request,HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from .app import database,identity,context,render,redirect
from .receiving_routes import receiving_form,form_actor,failure,integer
from .database import PhysicalLocation,PhysicalLocationState,PhysicalSharingReview,PhysicalLocationEvent,User
from . import physical_locations as service
router=APIRouter()


@router.get('/settings/shared-locations')
def index(request:Request,db:Session=Depends(database)):
    user,sess=identity(request,db)
    if not user:return redirect('/login')
    if user.role!='admin':raise HTTPException(403,'Apenas Administrador.')
    p=PhysicalLocation.__table__;h=PhysicalLocationState.__table__
    rows=db.connection().execute(select(p,h.c.state,h.c.context_id,h.c.version).join(h,h.c.physical_id==p.c.id).order_by(p.c.label)).mappings().all()
    return render(request,'physical_settings.html',context(request,user,sess,rows=rows,review=None,request_key=secrets.token_hex(16)))


@router.post('/settings/shared-locations/review')
def review(request:Request,data:dict=Depends(receiving_form),db:Session=Depends(database)):
    user,sess=form_actor(request,db,data,require_admin=True,operational=False)
    try:
        ident=service.review_sharing(db,user.id,data.get('request_key'),data.get('code'),data.get('reason'),data.get('physical_proof')=='1')
        return redirect(f'/settings/shared-locations/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)


@router.get('/settings/shared-locations/{ident}')
def detail(ident:int,request:Request,db:Session=Depends(database)):
    user,sess=identity(request,db)
    if not user:return redirect('/login')
    if user.role!='admin':raise HTTPException(403,'Apenas Administrador.')
    row=db.get(PhysicalSharingReview,ident)
    if not row:raise HTTPException(404,'Conferência inexistente.')
    import json
    return render(request,'physical_settings.html',context(request,user,sess,rows=[],review=row,snapshot=json.loads(row.expected)))


@router.post('/settings/shared-locations/{ident}/confirm')
def confirm(ident:int,request:Request,data:dict=Depends(receiving_form),db:Session=Depends(database)):
    user,sess=form_actor(request,db,data,require_admin=True,operational=False)
    try:
        service.confirm_sharing(db,user.id,ident)
        return redirect(f'/settings/shared-locations/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)


@router.post('/putaway/{ident}/cancel-physical-assignment')
def cancel(ident:int,request:Request,data:dict=Depends(receiving_form),db:Session=Depends(database)):
    user,sess=form_actor(request,db,data,require_admin=True)
    try:
        service.cancel_putaway(db,user.id,ident,data.get('reason'),data.get('physically_empty')=='1')
        return redirect(f'/putaway/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)


@router.get('/physical-occupancy')
def occupancy(request:Request,db:Session=Depends(database)):
    user,sess=identity(request,db)
    if not user:return redirect('/login')
    states=service.physical_statuses(db)
    # Events are context-scoped. No other owner's stock or documents are disclosed.
    events=db.scalars(select(PhysicalLocationEvent).order_by(PhysicalLocationEvent.id.desc()).limit(100)).all()
    return render(request,'physical_occupancy.html',context(request,user,sess,states=states,events=events,staging=service.staging_status(db)))
