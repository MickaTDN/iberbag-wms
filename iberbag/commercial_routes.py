"""Authenticated office screens for commercial pending and documentary compositions."""
import json
import secrets
from fastapi import APIRouter, Request, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from .app import database,identity,render,context,redirect
from .receiving_routes import receiving_form,form_actor,failure,integer
from .database import MercadonaDestination,AllocationCollection,IncompleteComposition
from . import commercial as service
from .receiving import ReceivingError

router=APIRouter()


@router.get('/pending')
def pending(request:Request,page:int=1,customer:str='',lote:str='',reference:str='',state:str='all',db:Session=Depends(database)):
    user,sess=identity(request,db)
    if not user:return redirect('/login')
    rows=[]
    for item,line,order,info in service.pending_rows(db):
        done=service.satisfied(db,item.id);balance=item.quantidade-done
        if customer and customer.casefold() not in info.customer.casefold():continue
        if lote and lote!=line.lote:continue
        if reference and reference.casefold() not in order.reference.casefold():continue
        if state=='open' and balance==0:continue
        rows.append(dict(item=item,line=line,order=order,info=info,done=done,balance=balance))
    total_pages=max(1,(len(rows)+49)//50);page=max(1,min(page,total_pages))
    return render(request,'customer_pending.html',context(request,user,sess,rows=rows[(page-1)*50:page*50],
        page=page,total_pages=total_pages,customer=customer,lote=lote,reference=reference,state=state))


@router.get('/pending/allocations')
def allocations(request:Request,page:int=1,db:Session=Depends(database)):
    user,sess=identity(request,db)
    if not user:return redirect('/login')
    rows=list(reversed(service.allocations(db)));total_pages=max(1,(len(rows)+49)//50);page=max(1,min(page,total_pages))
    selected=rows[(page-1)*50:page*50]
    return render(request,'customer_allocations.html',context(request,user,sess,rows=selected,page=page,total_pages=total_pages,
        collections={item.id:db.get(AllocationCollection,item.id) for item,*_ in selected}))


@router.post('/pending/allocations/{ident}/collect')
def collection(ident:int,request:Request,data:dict=Depends(receiving_form),db:Session=Depends(database)):
    user,sess=form_actor(request,db,data,require_admin=True)
    try:
        service.record_collection(db,user.id,ident,data.get('reason'))
        return redirect('/pending/allocations')
    except ReceivingError as error:return failure(request,user,sess,error)


@router.get('/settings/mercadona')
def destinations(request:Request,db:Session=Depends(database)):
    user,sess=identity(request,db)
    if not user:return redirect('/login')
    if user.role!='admin':raise HTTPException(403,'Só Administrador pode configurar destinos.')
    rows=db.scalars(select(MercadonaDestination).order_by(MercadonaDestination.sort_order,MercadonaDestination.name)).all()
    return render(request,'mercadona_destinations.html',context(request,user,sess,rows=rows))


@router.post('/settings/mercadona')
def destination_save(request:Request,data:dict=Depends(receiving_form),db:Session=Depends(database)):
    user,sess=form_actor(request,db,data,require_admin=True)
    try:
        service.save_destination(db,user.id,data.get('name'),data.get('sort_order'),data.get('active')=='1',data.get('highlighted')=='1',
            integer(data['ident']) if data.get('ident') else None)
        return redirect('/settings/mercadona')
    except ReceivingError as error:return failure(request,user,sess,error)


@router.get('/compositions')
def compositions(request:Request,page:int=1,db:Session=Depends(database)):
    user,sess=identity(request,db)
    if not user:return redirect('/login')
    page=max(1,min(page,100000))
    rows=db.scalars(select(IncompleteComposition).order_by(IncompleteComposition.id.desc()).offset((page-1)*50).limit(51)).all()
    return render(request,'compositions.html',context(request,user,sess,rows=rows[:50],more=len(rows)>50,page=page,
        components={r.id:json.loads(r.components) for r in rows},request_key=secrets.token_hex(16)))


@router.post('/compositions')
def composition_save(request:Request,data:dict=Depends(receiving_form),db:Session=Depends(database)):
    user,sess=form_actor(request,db,data)
    try:
        rows=[]
        for raw in data.get('components','').splitlines():
            if not raw.strip():continue
            parts=raw.split(';')
            if len(parts)!=2:raise ReceivingError('Usar medida;caixas, uma medida por linha.',400)
            rows.append(tuple(p.strip() for p in parts))
        service.save_composition(db,user.id,data.get('request_key'),data.get('reference'),rows,data.get('observation'),
            integer(data['receipt_line_id']) if data.get('receipt_line_id') else None)
        return redirect('/compositions')
    except ReceivingError as error:return failure(request,user,sess,error)
