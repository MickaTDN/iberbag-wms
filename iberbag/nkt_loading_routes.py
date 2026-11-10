"""Office NKT forms; authenticated bounded POSTs precede all writer locks."""
import json,secrets
from fastapi import APIRouter,Request,Depends,HTTPException
from fastapi.responses import Response
from sqlalchemy import select,or_
from .app import database,render,context,redirect
from .nkt_routes import access
from .receiving_routes import receiving_form,form_actor,failure,integer
from . import nkt_loading as service
from .database import NktPreparation as Prep,NktPlanRevision as Rev,NktOperation as Op,NktCorrection as Fix,NktDocument as Doc
router=APIRouter()

def allowed(request,db,data=None,admin=False):
    user,sess=form_actor(request,db,data,require_admin=True,operational=False) if admin and data is not None else access(request,db,data)
    if not service.nkt.enabled(db) or (admin and user.role!='admin'):raise HTTPException(403,'Operação NKT não autorizada.')
    return user,sess

def before(data):return integer(data['before']) if data.get('before') else None

@router.get('/nkt/preparations')
def index(request:Request,db=Depends(database)):
    user,sess=allowed(request,db);rows=[]
    for prep in db.scalars(select(Prep).order_by(Prep.id.desc()).limit(200)):
        rev=service.latest(db,prep)
        if rev:rows.append((prep,json.loads(rev.payload)))
    rows.sort(key=lambda pair:(pair[1]['order'],pair[1]['planned_on'],pair[0].id))
    return render(request,'nkt_preparations.html',context(request,user,sess,rows=rows))

@router.get('/nkt/preparations/new')
def new(request:Request,db=Depends(database)):
    user,sess=allowed(request,db)
    return render(request,'nkt_preparation_form.html',context(request,user,sess,prep=None,values={},key=secrets.token_hex(16)))

@router.get('/nkt/preparations/{ident}/edit')
def edit(ident:int,request:Request,db=Depends(database)):
    user,sess=allowed(request,db)
    try:
        prep=service.get_preparation(db,user,ident)
        if prep.status in ('loaded','cancelled'):raise service.ReceivingError('Preparação encerrada.')
        return render(request,'nkt_preparation_form.html',context(request,user,sess,prep=prep,values=json.loads(service.latest(db,prep).payload),key=prep.request_key))
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/nkt/preparations/review')
def review(request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data)
    try:
        ident=service.save(db,user.id,data.get('key'),data.get('reference'),data.get('planned_on'),data.get('order'),data.get('pegs'),data.get('reason'),integer(data['id']) if data.get('id') else None,integer(data['version']) if data.get('version') else None)
        return redirect(f'/nkt/preparations/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.get('/nkt/preparations/{ident}')
def detail(ident:int,request:Request,db=Depends(database)):
    user,sess=allowed(request,db);prep=db.get(Prep,ident)
    if not prep:raise HTTPException(404,'Preparação NKT inexistente neste contexto.')
    revisions=db.scalars(select(Rev).where(Rev.preparation_id==ident).order_by(Rev.version.desc())).all();rev=revisions[0];plan=json.loads(rev.payload)
    operations=db.scalars(select(Op).where(Op.preparation_id==ident).order_by(Op.id)).all();fixes=db.scalars(select(Fix).where(Fix.preparation_id==ident).order_by(Fix.id)).all()
    docs=db.scalars(select(Doc).where(or_(Doc.revision_id.in_([r.id for r in revisions]),Doc.operation_id.in_([o.id for o in operations]),Doc.correction_id.in_([f.id for f in fixes]))).order_by(Doc.id)).all()
    return render(request,'nkt_preparation.html',context(request,user,sess,prep=prep,revision=rev,plan=plan,revisions=revisions,operations=operations,fixes=fixes,documents=docs,states=service.states(db,[i['pallet_id'] for i in plan['items']]),last=service.last_operation(db,ident),keys={k:secrets.token_hex(16) for k in ('reserve','ship','cancel','correction')},operation_data={o.id:json.loads(o.snapshot) for o in operations}))

@router.post('/nkt/preparations/{ident}/reserve')
def reserve(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data)
    try:
        service.reserve(db,user.id,ident,integer(data.get('version')),data.get('digest'),data.get('key'),data.get('accepted')=='yes');return redirect(f'/nkt/preparations/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/nkt/preparations/{ident}/ship')
def ship(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data)
    try:
        service.ship(db,user.id,ident,integer(data.get('version')),before(data),data.get('key'),data.get('pegs'),data.get('loaded_on'),data.get('gt'));return redirect(f'/nkt/preparations/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/nkt/preparations/{ident}/cancel')
def cancel(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data)
    try:
        service.cancel(db,user.id,ident,integer(data.get('version')),before(data),data.get('key'),data.get('reason'));return redirect(f'/nkt/preparations/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/nkt/preparations/{ident}/corrections/review')
def correction_review(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data,True)
    try:
        fix=service.review_correction(db,user.id,ident,integer(data.get('original')),data.get('key'),data.get('kind'),data.get('pegs') if data.get('kind')=='restore' else '',data.get('loaded_on') or None,data.get('gt') or None,data.get('reason'))
        return redirect(f'/nkt/corrections/{fix}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.get('/nkt/corrections/{ident}')
def correction_detail(ident:int,request:Request,db=Depends(database)):
    user,sess=allowed(request,db,admin=True);fix=db.get(Fix,ident)
    if not fix:raise HTTPException(404,'Correção inexistente neste contexto.')
    return render(request,'nkt_correction.html',context(request,user,sess,fix=fix,values=json.loads(fix.payload),effects=json.loads(fix.snapshot),original=json.loads(db.get(Op,fix.operation_id).snapshot),key=secrets.token_hex(16),applied=db.scalar(select(Op.id).where(Op.correction_id==ident))))

@router.post('/nkt/corrections/{ident}/apply')
def correction_apply(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data,True)
    try:
        service.apply_correction(db,user.id,ident,data.get('digest'),data.get('accepted')=='yes',data.get('key'));return redirect(f'/nkt/corrections/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.get('/nkt/documents/{ident}.pdf')
def pdf(ident:int,request:Request,db=Depends(database)):
    allowed(request,db);doc=db.get(Doc,ident)
    if not doc:raise HTTPException(404,'Documento inexistente neste contexto.')
    from hashlib import sha256
    if sha256(doc.pdf).hexdigest()!=doc.digest:raise HTTPException(409,'Integridade do documento inválida; recuperar arquivo verificado.')
    return Response(doc.pdf,media_type='application/pdf',headers={'Content-Disposition':f'inline; filename="NKT-documento-{ident}.pdf"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
