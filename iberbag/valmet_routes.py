"""Office Valmet screens; all writes use the shared bounded-body/CSRF protocol."""
import json
import secrets
from fastapi import APIRouter, Request, Depends, HTTPException
from sqlalchemy import select
from .app import database, identity, render, context, redirect
from .receiving_routes import receiving_form, form_actor, failure, integer
from .database import ValmetEntry, ValmetUnit, ValmetIdentification, Receipt
from . import valmet as service

router=APIRouter()

def access(request,db,data=None):
    user,sess=(form_actor(request,db,data,operational=False) if data is not None else identity(request,db))
    if not user:raise HTTPException(403,'Iniciar sessão.')
    if not service.enabled(db):raise HTTPException(403,'Processo Valmet indisponível neste contexto.')
    return user,sess

def rows(data):
    result=[]
    for i in range(8):
        prefix=f'u{i}_'
        if not data.get(prefix+'kind'):continue
        row={key:data.get(prefix+key,'') for key in ('kind','physical_quantity','number','local','material_m2','content_total','supplier','guide','notes','brand')}
        row['pending']=data.get(prefix+'pending')=='yes'
        row['items']=items(data,prefix)
        result.append(row)
    return result

def items(data,prefix=''):
    lines=[]
    for j in range(6):
        row=tuple(data.get(prefix+f'{field}{j}','').strip() for field in ('article','lot','amount'))
        if any(row):lines.append(row)
    return lines

@router.get('/valmet/entries')
def entries(request:Request,db=Depends(database)):
    user,sess=access(request,db)
    entries=db.scalars(select(ValmetEntry).order_by(ValmetEntry.id.desc()).limit(100)).all()
    return render(request,'valmet_entries.html',context(request,user,sess,entries=entries))

@router.get('/valmet/entries/new')
def new(request:Request,db=Depends(database)):
    user,sess=access(request,db)
    return render(request,'valmet_entry_form.html',context(request,user,sess,entry=None,values={},rows=[],key=secrets.token_hex(16),kinds=service.KINDS,brands=service.BRANDS))

@router.get('/valmet/entries/{ident}/edit')
def edit(ident:int,request:Request,db=Depends(database)):
    user,sess=access(request,db);entry=db.get(ValmetEntry,ident)
    if not entry or entry.status!='draft' or entry.created_by!=user.id:raise HTTPException(403,'Rascunho indisponível.')
    values=json.loads(entry.payload)
    return render(request,'valmet_entry_form.html',context(request,user,sess,entry=entry,values=values,rows=values['rows'],key=entry.request_key,kinds=service.KINDS,brands=service.BRANDS))

@router.post('/valmet/entries/review')
def review(request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=access(request,db,data)
    try:
        ident=service.save_entry(db,user.id,data.get('request_key'),data.get('received_on'),data.get('code'),rows(data),
            integer(data['id']) if data.get('id') else None,integer(data['version']) if data.get('version') else None)
        return redirect(f'/valmet/entries/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.get('/valmet/entries/{ident}')
def detail(ident:int,request:Request,db=Depends(database)):
    user,sess=access(request,db);entry=db.get(ValmetEntry,ident)
    if not entry:raise HTTPException(404,'Entrada inexistente.')
    receipt=db.get(Receipt,entry.receipt_id) if entry.receipt_id else None
    current_rows=service.stock(db,{'entry_id':entry.id})[0] if receipt else []
    return render(request,'valmet_entry.html',context(request,user,sess,entry=entry,receipt=receipt,current_rows=current_rows,values=json.loads(entry.payload),kinds=service.KINDS))

@router.post('/valmet/entries/{ident}/confirm')
def confirm(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=access(request,db,data)
    try:
        service.confirm_entry(db,user.id,ident,integer(data.get('version')))
        return redirect(f'/valmet/entries/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.get('/valmet/stock')
def stock(request:Request,page:int=1,db=Depends(database)):
    user,sess=access(request,db)
    filters={key:request.query_params.get(key,'')[:120] for key in ('article','lot','number','kind','local','guide','notes')}
    page=max(1,min(page,10000));rows,total=service.stock(db,filters,page)
    from .valmet_planning import stock_details
    return render(request,'valmet_stock.html',context(request,user,sess,rows=rows,total=total,page=page,filters=filters,kinds=service.KINDS,details=stock_details(db,rows)))

@router.get('/valmet/units/{ident}/identify')
def identification(ident:int,request:Request,db=Depends(database)):
    user,sess=access(request,db);unit=db.get(ValmetUnit,ident)
    if not unit:raise HTTPException(404,'Unidade inexistente.')
    if db.scalar(select(ValmetIdentification.id).where(ValmetIdentification.unit_id==ident)):raise HTTPException(409,'Já identificada.')
    return render(request,'valmet_identify.html',context(request,user,sess,unit=unit,key=secrets.token_hex(16),kinds=service.KINDS))

@router.post('/valmet/units/{ident}/identify')
def identify(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=access(request,db,data)
    try:
        service.identify(db,user.id,ident,data.get('request_key'),items(data),data.get('content_total'),data.get('notes'))
        return redirect('/valmet/stock')
    except service.ReceivingError as error:return failure(request,user,sess,error)
