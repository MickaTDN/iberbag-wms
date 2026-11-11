"""Authenticated office inventory forms; existing bounded CSRF/context preflight."""
import json,secrets
from hashlib import sha256
from fastapi import APIRouter,Request,Depends,HTTPException,Response
from sqlalchemy import select,or_
from .app import database,render,context,redirect
from .alcobre_routes import allowed
from .receiving_routes import receiving_form,failure,integer
from . import alcobre_inventory as service,database as m
from .alcobre_inventory_pdf import LABELS,ACTIONS
router=APIRouter()

@router.get('/alcobre/inventories')
def index(request:Request,db=Depends(database)):
    user,sess=allowed(request,db)
    return render(request,'alcobre_inventories.html',context(request,user,sess,key=secrets.token_hex(16),inventories=db.scalars(select(m.AlcobreInventory).order_by(m.AlcobreInventory.id.desc()).limit(200)).all(),zones=db.scalars(select(m.AlcobreZone).where(m.AlcobreZone.active.is_(True))).all()))

@router.post('/alcobre/inventories/new')
def create(request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data)
    try:
        ident=service.create(db,user.id,data.get('key'),data.get('title'),data.get('mode'),data.get('locations','').splitlines(),data.get('zones','').splitlines())
        return redirect(f'/alcobre/inventories/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.get('/alcobre/inventories/{ident}')
def detail(ident:int,request:Request,db=Depends(database)):
    user,sess=allowed(request,db);inv=db.get(m.AlcobreInventory,ident)
    if not inv:raise HTTPException(404,'Inventário inexistente neste contexto.')
    reviews=db.scalars(select(m.AlcobreInventoryReview).where(m.AlcobreInventoryReview.inventory_id==ident).order_by(m.AlcobreInventoryReview.id.desc())).all()
    op=db.scalar(select(m.AlcobreInventoryOperation).where(m.AlcobreInventoryOperation.inventory_id==ident))
    report=json.loads(op.snapshot) if op else service.summary(db,inv)
    docs=db.scalars(select(m.AlcobreInventoryDocument).where(or_(m.AlcobreInventoryDocument.review_id.in_([r.id for r in reviews]),m.AlcobreInventoryDocument.operation_id==op.id if op else m.AlcobreInventoryDocument.id<0)).order_by(m.AlcobreInventoryDocument.id)).all()
    return render(request,'alcobre_inventory.html',context(request,user,sess,inv=inv,report=report,reviews=reviews,operation=op,documents=docs,labels=LABELS,actions=ACTIONS,keys={n:secrets.token_hex(16) for n in ('record','review','confirm')}))

@router.post('/alcobre/inventories/{ident}/record')
def record(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data)
    try:
        service.record(db,user.id,ident,integer(data.get('version')),data.get('result'),data.get('evidence'),data.get('number') or None,data.get('local') or None,integer(data['entry_id']) if data.get('entry_id') else None,integer(data['resolves']) if data.get('resolves') else None)
        return redirect(f'/alcobre/inventories/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/alcobre/inventories/{ident}/review')
def review(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data,True)
    try:
        service.review(db,user.id,ident,integer(data.get('version')),data.get('key'),data.get('reason'))
        return redirect(f'/alcobre/inventories/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/alcobre/inventories/{ident}/confirm')
def confirm(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data,True)
    try:
        service.confirm(db,user.id,ident,integer(data.get('review')),data.get('digest'),data.get('key'),data.get('accepted')=='yes')
        return redirect(f'/alcobre/inventories/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.get('/alcobre/inventory-documents/{ident}.pdf')
def document(ident:int,request:Request,db=Depends(database)):
    allowed(request,db);doc=db.get(m.AlcobreInventoryDocument,ident)
    if not doc:raise HTTPException(404,'Documento inexistente neste contexto.')
    if sha256(doc.pdf).hexdigest()!=doc.digest:raise HTTPException(409,'Integridade do documento inválida.')
    return Response(doc.pdf,media_type='application/pdf',headers={'Content-Disposition':f'inline; filename="inventario-alcobre-{ident}.pdf"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox; default-src 'none'"})
