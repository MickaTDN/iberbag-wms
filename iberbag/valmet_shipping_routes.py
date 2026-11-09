"""Office-only collection exceptions, optional printing and final loads."""
import json
from fastapi import APIRouter,Request,Depends,HTTPException
from fastapi.responses import Response
from sqlalchemy import select,func,cast
from sqlalchemy.dialects.postgresql import JSONB
from .app import database,render,context,redirect
from .receiving_routes import receiving_form,integer,failure
from .valmet_routes import access
from .database import ValmetShipment as Shipment,ValmetShipmentLine as Line,ValmetPosition,ValmetUnit,ValmetDocument as Document,ValmetPreparation,ValmetOccurrence
from . import valmet_shipping as s,valmet as v

router=APIRouter()

def office_actor(request,db,data,ident):
    user,sess=access(request,db,data)
    try:s.permission(db.get(ValmetPreparation,ident),user)
    except v.ReceivingError as error:raise HTTPException(error.status,str(error))
    return user,sess

@router.post('/valmet/preparations/{ident}/ready')
def ready(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=office_actor(request,db,data,ident)
    try:
        s.ready(db,user.id,ident,integer(data.get('version')));return redirect(f'/valmet/preparations/{ident}')
    except v.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/valmet/preparations/{ident}/document')
def document(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=office_actor(request,db,data,ident)
    try:
        doc=s.issue_document(db,user.id,ident,integer(data.get('version')));return redirect(f'/valmet/documents/{doc}.pdf')
    except v.ReceivingError as error:return failure(request,user,sess,error)

@router.get('/valmet/documents/{ident}.pdf')
def pdf(ident:int,request:Request,db=Depends(database)):
    access(request,db);doc=db.get(Document,ident)
    if not doc:raise HTTPException(404,'Documento inexistente neste contexto.')
    return Response(content=bytes(doc.pdf),media_type='application/pdf',headers={'Content-Disposition':f'inline; filename="valmet-{doc.id}.pdf"','Cache-Control':'private, no-store'})

@router.post('/valmet/preparations/{ident}/exception')
def report(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=office_actor(request,db,data,ident)
    try:
        s.report(db,user.id,ident,integer(data.get('version')),integer(data.get('unit')),data.get('kind'),data.get('note'),data.get('lot',''),data.get('quantity'),data.get('observed',''),data.get('key'))
        return redirect(f'/valmet/preparations/{ident}')
    except v.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/valmet/occurrences/{ident}/resolve')
def resolve(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=access(request,db,data)
    original=db.get(ValmetOccurrence,ident)
    if not original:raise HTTPException(404,'Ocorrência inexistente.')
    try:s.permission(db.get(ValmetPreparation,original.preparation_id),user)
    except v.ReceivingError as error:raise HTTPException(error.status,str(error))
    if original.kind=='missing' and user.role!='admin':raise HTTPException(403,'Só Administrador resolve material não encontrado.')
    try:
        s.resolve(db,user.id,ident,data.get('note'));return redirect('/valmet/preparations')
    except v.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/valmet/preparations/{ident}/location-proof')
def relocate(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=office_actor(request,db,data,ident)
    try:
        s.relocate(db,user.id,ident,integer(data.get('version')),integer(data.get('unit')),data.get('target',''),data.get('quantity'),data.get('note'),data.get('lot',''),data.get('key'))
        return redirect(f'/valmet/preparations/{ident}')
    except v.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/valmet/preparations/{ident}/ship')
def ship(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=office_actor(request,db,data,ident)
    try:
        lines=None
        if data.get('partial')=='yes':
            lines=[(integer(data.get(f'unit{i}')),data.get(f'lot{i}'),data.get(f'q{i}')) for i in range(100)
                if data.get(f'pick{i}')=='yes' and data.get(f'q{i}') not in ('','0',None)]
        pallets=[integer(key[4:]) for key,value in data.items() if key.startswith('lab_') and value=='yes']
        roll_ids=None
        if data.get('specific_rolls')=='yes':
            roll_ids={}
            for name,value in data.items():
                if name.startswith('shiproll_') and value=='yes':
                    _,unit,rid=name.split('_');roll_ids.setdefault(integer(unit),[]).append(integer(rid))
        ident=s.ship(db,user.id,ident,integer(data.get('version')),data.get('key'),data.get('day'),
            [r.strip() for r in data.get('references','').splitlines() if r.strip()],lines,data.get('close')=='yes',data.get('extra_ack')=='yes',data.get('unknown_ack')=='yes',pallets,roll_ids)
        return redirect(f'/valmet/shipments/{ident}')
    except v.ReceivingError as error:return failure(request,user,sess,error)

@router.get('/valmet/shipments')
def history(request:Request,page:int=1,db=Depends(database)):
    user,sess=access(request,db);page=max(1,min(page,10000))
    filters={k:request.query_params.get(k,'')[:120].strip() for k in ('lot','number','reference','document','date')}
    query=select(Shipment)
    if filters['reference']:
        ref=(cast(Shipment.snapshot,JSONB)['reference'].astext if db.bind.dialect.name=='postgresql' else func.json_extract(Shipment.snapshot,'$.reference'))
        query=query.where(ref.icontains(filters['reference'],autoescape=True))
    if filters['document']:query=query.where(Shipment.references.icontains(filters['document'],autoescape=True))
    if filters['date']:
        from datetime import date,datetime
        try:day=date.fromisoformat(filters['date'])
        except ValueError:raise HTTPException(400,'Data inválida.')
        query=query.where(Shipment.loaded_on==datetime.combine(day,datetime.min.time()))
    if filters['lot'] or filters['number']:
        line=select(Line.id).join(ValmetPosition,ValmetPosition.id==Line.position_id).join(ValmetUnit,ValmetUnit.id==ValmetPosition.unit_id).where(Line.shipment_id==Shipment.id)
        if filters['lot']:
            criterion=(cast(Line.materials,JSONB).op('?')(filters['lot']) if db.bind.dialect.name=='postgresql' else
                func.json_type(Line.materials,'$.'+json.dumps(filters['lot'])).is_not(None))
            line=line.where(criterion)
        if filters['number']:line=line.where(ValmetUnit.number.icontains(filters['number'],autoescape=True))
        query=query.where(line.exists())
    total=db.scalar(select(func.count()).select_from(query.subquery()))
    records=db.scalars(query.order_by(Shipment.loaded_on.desc(),Shipment.id.desc()).offset((page-1)*50).limit(50)).all()
    return render(request,'valmet_shipments.html',context(request,user,sess,records=records,page=page,total=total,filters=filters))

@router.get('/valmet/shipments/{ident}')
def detail(ident:int,request:Request,db=Depends(database)):
    user,sess=access(request,db);record=db.get(Shipment,ident)
    if not record:raise HTTPException(404,'Expedição inexistente neste contexto.')
    return render(request,'valmet_shipment.html',context(request,user,sess,record=record,snapshot=json.loads(record.snapshot),references=json.loads(record.references),kinds=v.KINDS))
