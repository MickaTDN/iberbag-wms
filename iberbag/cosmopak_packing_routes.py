"""Administrative screens using the same online commands intended for future PWA."""
import json,secrets
from fastapi import APIRouter,Depends,Request,HTTPException
from fastapi.responses import Response
from sqlalchemy import select,func
from .app import database,render,context,redirect
from .cosmopak_routes import access
from .receiving_routes import receiving_form,integer,failure
from .valmet_import_upload import named_upload_form
from .database import CosmoPacking as Packing,CosmoPackingState as State,CosmoPackingPick as Pick,CosmoPackingDocument as Document,PhysicalStagingPosition,CosmoState
from . import cosmopak_packings as service
router=APIRouter()
OFFICE={'map','confirm','substitute','reopen_prepare','reopen_load','review_dispatch','approve'}

@router.get('/cosmopak/packings')
def index(request:Request,db=Depends(database)):
    user,sess=access(request,db)
    rows=db.execute(select(Packing,State).join(State,State.packing_id==Packing.id).order_by(Packing.id.desc()).limit(100)).all()
    return render(request,'cosmopak_packings.html',context(request,user,sess,rows=rows,key=secrets.token_hex(16)))

@router.post('/cosmopak/packings/upload')
def upload(request:Request,data:dict=Depends(named_upload_form),db=Depends(database)):
    user,sess=access(request,db,data,True);actor=user.id;db.rollback()
    try:
        ident=service.preview(db,actor,data['request_key'],data['excel'],data['source_name'],reauthorize=lambda:access(request,db,data,True))
        return redirect(f'/cosmopak/packings/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.get('/cosmopak/packings/{ident}')
def detail(ident:int,request:Request,sort:str='excel',history_page:int=1,docs_page:int=1,db=Depends(database)):
    user,sess=access(request,db);src=db.get(Packing,ident)
    if not src:raise HTTPException(404,'PACKING inexistente neste contexto.')
    h=service.header(db,ident)
    data=json.loads(h.final_snapshot) if h.stage=='shipped' else (service.snapshot(db,ident) if h.stage not in ('draft','reviewed') else dict(items=[]))
    rows=data['items']
    from collections import Counter
    totals=Counter(requested=len(rows),prepared=sum(r['pick']['prepared'] for r in rows),loaded=sum(r['pick']['loaded'] for r in rows),matched=sum(r['pallet'] is not None for r in rows))
    if sort=='local':rows=sorted(rows,key=lambda r:((r.get('material') or {}).get('local') or '',r['line']['source_row'],r['line']['ordinal']))
    from .database import CosmoPackingLine,CosmoPackingAction
    lines=db.scalars(select(CosmoPackingLine).where(CosmoPackingLine.mapping_id==h.mapping_id,CosmoPackingLine.packing_id==ident).order_by(CosmoPackingLine.source_row,CosmoPackingLine.ordinal)).all() if h.mapping_id else []
    history_page=max(1,history_page);docs_page=max(1,docs_page)
    history_total=db.scalar(select(func.count()).select_from(CosmoPackingAction).where(CosmoPackingAction.packing_id==ident))
    docs_total=db.scalar(select(func.count()).select_from(Document).where(Document.packing_id==ident))
    return render(request,'cosmopak_packing.html',context(request,user,sess,src=src,h=h,source=json.loads(src.report),rows=rows,lines=lines,keys=[secrets.token_hex(16) for _ in range(12)],pick_keys={r['pick']['id']:[secrets.token_hex(16) for _ in range(3)] for r in rows},docs=db.scalars(select(Document).where(Document.packing_id==ident).order_by(Document.id.desc()).offset((docs_page-1)*100).limit(100)).all(),columns=list('ABCDEFGHIJKLMNOPQRSTUVWXYZ'),totals=totals,mapping_result=json.loads(db.get(CosmoPackingAction,h.mapping_id).payload) if h.mapping_id else None,history=db.scalars(select(CosmoPackingAction).where(CosmoPackingAction.packing_id==ident).order_by(CosmoPackingAction.id.desc()).offset((history_page-1)*100).limit(100)).all(),history_page=history_page,history_pages=max(1,(history_total+99)//100),docs_page=docs_page,docs_pages=max(1,(docs_total+99)//100)))

@router.post('/cosmopak/packings/{ident}/{action}')
def command(ident:int,action:str,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=access(request,db,data,action in OFFICE or action=='document')
    try:
        payload={}
        if action=='map':
            mapping={f:data.get(f) for f in ('owner','article','lot','boxes','pallets')};mapping.update(first_row=integer(data.get('first_row')),one_row_one_pallet=data.get('one_row_one_pallet')=='yes')
            payload=dict(mapping=mapping,reference=data.get('reference'),reference_cell=data.get('reference_cell'))
        elif action in ('confirm','approve'):payload=dict(accepted=data.get('accepted')=='yes',**({'review_id':integer(data.get('review_id'))} if action=='approve' else {}))
        elif action=='review_dispatch':payload=dict(dispatched_on=data.get('dispatched_on'))
        if action=='document':
            doc=service.document(db,user.id,data.get('request_key'),ident,data.get('kind'))
            return redirect(f'/cosmopak/documents/{doc}.pdf')
        service.command(db,user.id,data.get('request_key'),ident,action,integer(data.get('version')),payload,data.get('reason'))
        return redirect(f'/cosmopak/packings/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/cosmopak/packing-picks/{ident}/{action}')
def pick(ident:int,action:str,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=access(request,db,data,action=='substitute');row=db.get(Pick,ident)
    if not row:raise HTTPException(404,'Linha inexistente.')
    packing=row.packing_id
    try:
        payload=dict(lot=data.get('lot'),boxes=data.get('boxes'),checked=data.get('checked')=='yes',physical=data.get('physical')=='yes') if action in ('prepare','load','return_stage') else {}
        service.command(db,user.id,data.get('request_key'),packing,action,integer(data.get('version')),payload,data.get('reason'),ident,integer(data.get('pallet_id')) if action=='substitute' else None)
        return redirect(f'/cosmopak/packings/{packing}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/cosmopak/pallets/{ident}/return-rack')
def return_rack(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=access(request,db,data)
    state=db.scalar(select(CosmoState).where(CosmoState.pallet_id==ident))
    pos=db.scalar(select(PhysicalStagingPosition).where(PhysicalStagingPosition.unit_kind=='cosmopak',PhysicalStagingPosition.unit_id==ident))
    if not state or not pos:raise HTTPException(404,'Presença física não comprovada.')
    try:
        payload=dict(local=(data.get('local') or '').strip().upper(),lot=data.get('lot'),boxes=data.get('boxes'),checked=data.get('checked')=='yes',physical=data.get('physical')=='yes')
        service.command(db,user.id,data.get('request_key'),pos.group_id,'return_rack',integer(data.get('version')),payload,data.get('reason'),pallet_id=ident)
        return redirect('/cosmopak/stock')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.get('/cosmopak/documents/{ident}.pdf')
def document(ident:int,request:Request,db=Depends(database)):
    access(request,db);doc=db.get(Document,ident)
    if not doc:raise HTTPException(404,'Documento inexistente neste contexto.')
    return Response(doc.pdf,media_type='application/pdf',headers={'Content-Disposition':f'inline; filename="packing-{doc.packing_id}-{doc.id}.pdf"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

@router.get('/cosmopak/packings/{ident}/source.xlsx')
def original(ident:int,request:Request,db=Depends(database)):
    access(request,db,admin=True);src=db.get(Packing,ident)
    if not src:raise HTTPException(404,'Origem inexistente.')
    return Response(src.source_excel,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':f'attachment; filename="packing-origem-{ident}.xlsx"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
