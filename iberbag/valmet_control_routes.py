"""Office Valmet control; bounded authenticated forms precede transactional locks."""
import json
import secrets
from fastapi import APIRouter,Request,Depends,HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from .app import database,render,context,redirect
from .receiving_routes import receiving_form,integer,failure
from .valmet_routes import access
from . import valmet_control as c,valmet as v
from .database import (ValmetInventory as Inventory,ValmetCount as Count,ValmetControlDraft as Draft,ValmetControlMove as Move,
    ValmetControlDocument as Document,ValmetPosition as Position,ValmetUnit as Unit,ValmetShipmentLine as Line,Location)

router=APIRouter()

def authorized(request,db,data=None,admin=False):
    user,sess=access(request,db,data)
    if admin and user.role!='admin':raise HTTPException(403,'Só Administrador pode aprovar ou ajustar stock.')
    return user,sess

def found(db,model,ident):
    row=db.get(model,ident)
    if not row:raise HTTPException(404,'Registo inexistente neste contexto.')
    return row

def ids(data,name):
    values=data.get(name,'').replace(',',' ').split()
    if len(values)>100:raise v.ReceivingError('Até 100 referências por operação.')
    return [integer(x) for x in values]

@router.get('/valmet/inventories')
def inventories(request:Request,db=Depends(database)):
    user,sess=authorized(request,db)
    records=db.scalars(select(Inventory).order_by(Inventory.id.desc()).limit(200)).all()
    return render(request,'valmet_inventories.html',context(request,user,sess,records=records,kinds=v.KINDS,key=secrets.token_hex(16)))

@router.post('/valmet/inventories')
def create(request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=authorized(request,db,data)
    try:
        ident=c.create_inventory(db,user.id,data.get('key'),data.get('mode'),data.get('local',''),data.get('kind',''),data.get('lot',''),data.get('number',''))
        return redirect(f'/valmet/inventories/{ident}')
    except v.ReceivingError as error:return failure(request,user,sess,error)

@router.get('/valmet/inventories/{ident}')
def inventory(ident:int,request:Request,db=Depends(database)):
    user,sess=authorized(request,db);record=found(db,Inventory,ident)
    return render(request,'valmet_inventory.html',context(request,user,sess,record=record,scope=json.loads(record.scope),rows=[dict(row,key=secrets.token_hex(16)) for row in c.inventory_summary(db,record)],kinds=v.KINDS,
        counts=db.scalars(select(Count).where(Count.inventory_id==ident).order_by(Count.id.desc())).all(),
        documents=db.scalars(select(Document).where(Document.inventory_id==ident).order_by(Document.id.desc())).all(),key=secrets.token_hex(16)))

@router.get('/valmet/inventories/{ident}/count')
def count_form(ident:int,request:Request,target:str='',db=Depends(database)):
    user,sess=authorized(request,db);record=found(db,Inventory,ident);selected=c.inventory_targets(db,record).get(target)
    if target and selected is None:raise HTTPException(404,'Alvo fora do âmbito.')
    return render(request,'valmet_count_form.html',context(request,user,sess,record=record,target=selected or dict(key='U:'+secrets.token_hex(16),kind='unknown',materials={}),key=secrets.token_hex(16),kinds=v.KINDS))

@router.post('/valmet/inventories/{ident}/count')
def save_count(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=authorized(request,db,data)
    try:
        content={data[f'lot{i}']:c.number(data.get(f'q{i}'),unknown=True) for i in range(100) if data.get(f'lot{i}')}
        c.save_count(db,user.id,ident,integer(data.get('version')),data.get('key'),data.get('target',''),data.get('kind'),data.get('physical'),content or None,data.get('observed',''),data.get('evidence',''),data.get('recount')=='yes')
        return redirect(f'/valmet/inventories/{ident}')
    except v.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/valmet/inventories/{ident}/submit')
def submit(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=authorized(request,db,data)
    try:c.submit(db,user.id,ident,integer(data.get('version')));return redirect(f'/valmet/inventories/{ident}')
    except v.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/valmet/inventories/{ident}/decide')
def decide(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=authorized(request,db,data,admin=True)
    try:
        c.decide(db,user.id,ident,integer(data.get('version')),integer(data.get('count')),data.get('key'),data.get('action'),data.get('reason'),data.get('evidence'),data.get('proven')=='yes',data.get('clear')=='yes',data.get('basis','count'),ids(data,'lines'),ids(data,'resolves'))
        return redirect(f'/valmet/inventories/{ident}')
    except v.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/valmet/inventories/{ident}/close')
def close(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=authorized(request,db,data,admin=True)
    try:c.close_inventory(db,user.id,ident,integer(data.get('version')));return redirect(f'/valmet/inventories/{ident}')
    except v.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/valmet/inventories/{ident}/document')
def document(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=authorized(request,db,data)
    try:
        doc=c.issue_document(db,user.id,ident,integer(data.get('version')),data.get('kind'));return redirect(f'/valmet/control-documents/{doc}.pdf')
    except v.ReceivingError as error:return failure(request,user,sess,error)

@router.get('/valmet/control-documents/{ident}.pdf')
def pdf(ident:int,request:Request,db=Depends(database)):
    authorized(request,db);doc=found(db,Document,ident)
    return Response(bytes(doc.pdf),media_type='application/pdf',headers={'Content-Disposition':f'inline; filename="valmet-inventario-{doc.id}.pdf"','Cache-Control':'private, no-store'})

@router.get('/valmet/transfers')
@router.get('/valmet/adjustments')
def controls(request:Request,db=Depends(database)):
    kind='adjustment' if request.url.path.endswith('adjustments') else 'transfer'
    user,sess=authorized(request,db,admin=kind=='adjustment')
    filters={key:request.query_params.get(key,'')[:120] for key in ('local','number','kind','lot','article')}
    records=db.scalars(select(Draft).where(Draft.kind==kind).order_by(Draft.id.desc()).limit(200)).all()
    return render(request,'valmet_controls.html',context(request,user,sess,kind=kind,filters=filters,records=records,rows=c.lookup(db,filters,empty=kind=='adjustment'),kinds=v.KINDS))

@router.get('/valmet/controls/new')
def control_form(request:Request,position:int,kind:str,db=Depends(database)):
    if kind not in ('transfer','adjustment'):raise HTTPException(400,'Operação inválida.')
    user,sess=authorized(request,db,admin=kind=='adjustment');pos=found(db,Position,position);unit=found(db,Unit,pos.unit_id)
    return render(request,'valmet_control_form.html',context(request,user,sess,kind=kind,pos=pos,unit=unit,material=dict.fromkeys(c.known(db,unit),None) | json.loads(pos.materials),
        locations=db.scalars(select(Location).where(Location.active.is_(True)).order_by(Location.codigo)).all(),
        unresolved=c.unresolved(db,unit.id),lines=db.scalars(select(Line).join(Position,Position.id==Line.position_id).where(Position.unit_id==unit.id)).all(),key=secrets.token_hex(16),kinds=v.KINDS))

@router.post('/valmet/controls/review')
def review(request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    kind=data.get('kind')
    if kind not in ('transfer','adjustment'):raise HTTPException(400,'Operação inválida.')
    user,sess=authorized(request,db,data,admin=kind=='adjustment')
    try:
        args=(db,user.id,data.get('key'),integer(data.get('position')),data.get('target'),data.get('physical'))
        if kind=='transfer':ident=c.save_transfer(*args,data.get('lot'),data.get('reason'),data.get('evidence'),data.get('located')=='yes')
        else:
            content={data[f'lot{i}']:c.number(data.get(f'q{i}'),unknown=True) for i in range(100) if data.get(f'lot{i}')}
            if c.number(data.get('physical'))==0:content={}
            ident=c.save_adjustment(*args,content,data.get('reason'),data.get('evidence'),data.get('proven')=='yes',data.get('clear')=='yes',data.get('basis','count'),ids(data,'lines'),ids(data,'resolves'))
        return redirect(f'/valmet/controls/{ident}')
    except v.ReceivingError as error:return failure(request,user,sess,error)

@router.get('/valmet/controls/{ident}')
def control(ident:int,request:Request,db=Depends(database)):
    user,sess=authorized(request,db);record=found(db,Draft,ident)
    if record.kind=='adjustment' and user.role!='admin':raise HTTPException(403,'Só Administrador.')
    pos=found(db,Position,record.position_id)
    return render(request,'valmet_control.html',context(request,user,sess,record=record,before=json.loads(record.reference),operation=json.loads(record.payload),unit=found(db,Unit,pos.unit_id),kinds=v.KINDS,
        moves=db.scalars(select(Move).where(Move.draft_id==ident)).all()))

@router.post('/valmet/controls/{ident}/confirm')
def confirm(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=authorized(request,db,data);record=found(db,Draft,ident)
    try:
        c.permission(record,user)
        if record.kind=='adjustment':c.admin(user)
        c.confirm(db,user.id,ident,integer(data.get('version')));return redirect(f'/valmet/controls/{ident}')
    except v.ReceivingError as error:return failure(request,user,sess,error)
