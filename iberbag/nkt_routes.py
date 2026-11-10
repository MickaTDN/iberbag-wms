"""Office NKT forms, shared session/CSRF/context preflight."""
import secrets,json
from fastapi import APIRouter,Request,Depends,HTTPException
from sqlalchemy import select
from .app import database,identity,render,context,redirect
from .receiving_routes import receiving_form,form_actor,failure,integer
from .database import NktEntry,NktReview,Location
from . import nkt as service
router=APIRouter()
def access(request,db,data=None):
    user,sess=form_actor(request,db,data,operational=False) if data is not None else identity(request,db)
    if not user or not service.enabled(db):raise HTTPException(403,'Processo NKT indisponível neste contexto.')
    return user,sess

@router.get('/nkt/entries')
def entries(request:Request,db=Depends(database)):
    user,sess=access(request,db)
    return render(request,'nkt_entries.html',context(request,user,sess,entries=db.scalars(select(NktEntry).order_by(NktEntry.id.desc()).limit(100)).all()))
@router.get('/nkt/entries/new')
def new(request:Request,db=Depends(database)):
    user,sess=access(request,db)
    return render(request,'nkt_form.html',context(request,user,sess,key=secrets.token_hex(16),entry=None,values={},places=db.scalars(select(Location).where(Location.active.is_(True))).all()))
@router.get('/nkt/entries/{ident}/edit')
def edit(ident:int,request:Request,db=Depends(database)):
    user,sess=access(request,db);entry=db.get(NktEntry,ident)
    if not entry or entry.status!='draft' or entry.created_by!=user.id:raise HTTPException(403,'Rascunho indisponível.')
    return render(request,'nkt_form.html',context(request,user,sess,key=entry.request_key,entry=entry,values=json.loads(entry.payload),places=db.scalars(select(Location).where(Location.active.is_(True))).all()))
@router.post('/nkt/entries/review')
def review(request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=access(request,db,data)
    try:
        rows=[dict(peg=data.get(f'peg{i}',''),local=data.get(f'local{i}','')) for i in range(20) if data.get(f'peg{i}') or data.get(f'local{i}')]
        ident=service.save(db,user.id,data.get('key'),data.get('received_on'),data.get('notes',''),rows,integer(data['id']) if data.get('id') else None,integer(data['version']) if data.get('version') else None)
        return redirect(f'/nkt/entries/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)
@router.get('/nkt/entries/{ident}')
def detail(ident:int,request:Request,db=Depends(database)):
    user,sess=access(request,db);entry=db.get(NktEntry,ident)
    if not entry:raise HTTPException(404,'Entrada NKT inexistente neste contexto.')
    reviews=db.scalars(select(NktReview).where(NktReview.entry_id==ident).order_by(NktReview.version)).all()
    return render(request,'nkt_entry.html',context(request,user,sess,entry=entry,values=json.loads(entry.payload),digest=service.digest(entry.payload),reviews=reviews))
@router.post('/nkt/entries/{ident}/confirm')
def confirm(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=access(request,db,data)
    try:
        if data.get('accepted')!='yes':raise service.ReceivingError('Rever e aceitar explicitamente a entrada.')
        service.confirm(db,user.id,ident,integer(data.get('version')),data.get('digest'))
        return redirect(f'/nkt/entries/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)
@router.get('/nkt/stock')
def stock(request:Request,peg:str='',local:str='',page:int=1,db=Depends(database)):
    user,sess=access(request,db);page=max(1,min(page,10000));peg=peg[:120];local=local[:120]
    rows,total=service.stock(db,peg,local,page)
    return render(request,'nkt_stock.html',context(request,user,sess,rows=rows,total=total,peg=peg,local=local,page=page))
