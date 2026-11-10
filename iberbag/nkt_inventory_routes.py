"""NKT office load conference. Same session/CSRF/context preflight as loading."""
import json,secrets
from fastapi import APIRouter,Request,Depends,HTTPException
from sqlalchemy import select,or_
from .app import database,render,context,redirect
from .nkt_loading_routes import allowed
from .receiving_routes import receiving_form,failure,integer
from . import nkt_inventory as service
from .database import NktInventory as Inventory,NktInventoryReview as Review,NktInventoryIncident as Incident,NktInventoryCheck as Check,NktOperation as Op,NktDocument as Document
router=APIRouter()

@router.get('/nkt/inventories')
def index(request:Request,db=Depends(database)):
    user,sess=allowed(request,db)
    return render(request,'nkt_inventories.html',context(request,user,sess,key=secrets.token_hex(16),inventories=db.scalars(select(Inventory).order_by(Inventory.id.desc()).limit(200)).all(),catalog=service.groups(db)))

@router.post('/nkt/inventories/new')
def create(request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data)
    try:
        selected=[integer(k[6:]) for k,v in data.items() if k.startswith('entry_') and v=='yes']
        ident=service.create(db,user.id,data.get('key'),data.get('title'),data.get('mode'),selected)
        return redirect(f'/nkt/inventories/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.get('/nkt/inventories/{ident}')
def detail(ident:int,request:Request,review:int|None=None,db=Depends(database)):
    user,sess=allowed(request,db);inv=db.get(Inventory,ident)
    if not inv:raise HTTPException(404,'Inventário inexistente neste contexto.')
    revisions=db.scalars(select(Review).where(Review.inventory_id==ident).order_by(Review.id.desc())).all()
    selected=next((r for r in revisions if r.id==review),None) if review else (revisions[0] if revisions else None)
    if review and not selected:raise HTTPException(404,'Revisão não pertence a este inventário.')
    operations=db.scalars(select(Op).where(Op.inventory_id==ident).order_by(Op.id)).all();original=next((o for o in operations if o.kind=='inventory_missing'),None)
    report=json.loads(original.snapshot)['report'] if original else service.summary(db,inv)
    incidents=db.scalars(select(Incident).where(Incident.inventory_id==ident).order_by(Incident.id)).all();resolved={i.resolves_id for i in incidents if i.resolves_id}
    docs=db.scalars(select(Document).where(or_(Document.inventory_review_id.in_([r.id for r in revisions]),Document.operation_id.in_([o.id for o in operations]))).order_by(Document.id)).all()
    return render(request,'nkt_inventory.html',context(request,user,sess,inv=inv,report=report,revision=selected,revisions=revisions,revision_data=json.loads(selected.payload) if selected else {},operations=operations,original=original,applied=next((o.id for o in operations if selected and o.inventory_review_id==selected.id),None),incidents=incidents,resolved=resolved,documents=docs,checks=db.scalars(select(Check).where(Check.inventory_id==ident).order_by(Check.id)).all(),keys={k:secrets.token_hex(16) for k in ('review','confirm','restore','apply')}))

@router.post('/nkt/inventories/{ident}/check')
def check(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data)
    try:
        service.record(db,user.id,ident,integer(data.get('version')),data.get('state'),integer(data['entry']) if data.get('entry') else None,data.get('peg') or None,data.get('reason'),data.get('full_peg')=='yes')
        return redirect(f'/nkt/inventories/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/nkt/inventories/{ident}/incident')
def report(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data)
    try:
        service.report_unknown(db,user.id,ident,integer(data.get('version')),data.get('peg'),data.get('note'));return redirect(f'/nkt/inventories/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/nkt/inventory-incidents/{ident}/resolve')
def resolve(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data,True)
    try:
        row=db.get(Incident,ident)
        if not row:raise service.ReceivingError('Ocorrência inexistente.',404)
        target=row.inventory_id;service.resolve_incident(db,user.id,ident,data.get('reason'),data.get('peg'));return redirect(f'/nkt/inventories/{target}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/nkt/inventories/{ident}/review')
def review(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data,True)
    try:
        rev=service.review(db,user.id,ident,integer(data.get('version')),data.get('key'),data.get('reason'));return redirect(f'/nkt/inventories/{ident}?review={rev}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/nkt/inventories/{ident}/confirm')
def confirm(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data,True)
    try:
        service.confirm(db,user.id,ident,integer(data.get('review')),data.get('digest'),data.get('key'),data.get('accepted')=='yes');return redirect(f'/nkt/inventories/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/nkt/inventories/{ident}/restore/review')
def restore_review(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data,True)
    try:
        result=service.review_restore(db,user.id,ident,integer(data.get('original')),data.get('key'),data.get('peg'),data.get('all_load')=='yes',data.get('pegs'),data.get('reason'),data.get('full_peg')=='yes')
        return redirect(f'/nkt/inventories/{ident}'+(f'?review={result}' if isinstance(result,int) else ''))
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/nkt/inventory-reviews/{ident}/restore')
def restore(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data,True)
    try:
        row=db.get(Review,ident)
        if not row:raise service.ReceivingError('Revisão inexistente.',404)
        target=row.inventory_id;service.apply_restore(db,user.id,ident,data.get('digest'),data.get('key'),data.get('accepted')=='yes');return redirect(f'/nkt/inventories/{target}?review={ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)
