"""NKT office manual/email/Excel reception; body preflight before writer locks."""
import json,secrets
from fastapi import APIRouter,Request,Depends,HTTPException
from sqlalchemy import select
from .app import database,render,context,redirect
from .nkt_routes import access
from .valmet_import_upload import named_upload_form
from .receiving_routes import receiving_form,failure,integer,form_actor
from .database import NktReceiving as Receiving,NktReceivingReview as Review,NktContainer as Container
from . import nkt_receiving as service
router=APIRouter()

def allowed(request,db,data=None,admin=False):
    user,sess=form_actor(request,db,data,require_admin=True,operational=False) if admin and data is not None else access(request,db,data)
    if not service.nkt.enabled(db):raise HTTPException(403,'Contexto NKT obrigatório.')
    if admin and user.role!='admin':raise HTTPException(403,'Excel NKT exige Administrador.')
    return user,sess

@router.get('/nkt/receivings')
def index(request:Request,db=Depends(database)):
    user,sess=allowed(request,db)
    return render(request,'nkt_receivings.html',context(request,user,sess,key=secrets.token_hex(16),receivings=db.scalars(select(Receiving).order_by(Receiving.id.desc()).limit(100)).all()))

@router.post('/nkt/receivings/manual')
def manual(request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data)
    try:
        ident=service.create(db,user.id,data.get('request_key'),'Manual/e-mail',dict(records=[],excluded=[],warnings=[]))
        return redirect(f'/nkt/receivings/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/nkt/receivings/upload')
def upload(request:Request,data:dict=Depends(named_upload_form),db=Depends(database)):
    user,sess=allowed(request,db,data,True);actor=user.id;db.rollback()
    try:
        ident=service.preview_file(db,actor,data['request_key'],data['excel'],data['source_name'],reauthorize=lambda:allowed(request,db,data,True))
        return redirect(f'/nkt/receivings/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.get('/nkt/receivings/{ident}')
def detail(ident:int,request:Request,db=Depends(database)):
    user,sess=allowed(request,db);rec=db.get(Receiving,ident)
    if not rec:raise HTTPException(404,'Receção inexistente neste contexto.')
    revisions=db.scalars(select(Review).where(Review.receiving_id==ident).order_by(Review.version.desc())).all()
    revision=revisions[0] if revisions else None;source=json.loads(rec.report)
    if revision:
        # One editable row per provenance/material, never merge away a source line.
        rows=[]
        for group in json.loads(revision.payload):
            members={}
            for item in group['items']:members.setdefault((item['ref'],item['material']),[]).append(item['peg'])
            rows.extend(dict(ref=ref,material=material,container=group['container'],received_on=group['received_on'],pegs=pegs) for (ref,material),pegs in members.items())
    else:rows=list(source['records'])
    rows+= [dict() for _ in range(min(20,300-len(rows)))]
    return render(request,'nkt_receiving.html',context(request,user,sess,rec=rec,source=source,rows=rows,revisions=revisions,revision=revision,plan=json.loads(revision.payload) if revision else [],containers=db.scalars(select(Container).where(Container.receiving_id==ident)).all()))

@router.post('/nkt/receivings/{ident}/review')
def review(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data)
    try:
        count=integer(data.get('row_count'))
        if not 1<=count<=300:raise service.ReceivingError('Número de linhas inválido.',400)
        expected={'csrf','context_id','version','row_count','reason'}|{f'{field}{i}' for i in range(count) for field in ('ref','container','material','received_on','pegs','first','last')}
        if set(data)-expected:raise service.ReceivingError('Campos de distribuição desconhecidos; recarregar o formulário.',400)
        rows=[]
        for i in range(count):
            row={name:data.get(f'{name}{i}','') for name in ('ref','container','material','received_on','pegs','first','last')}
            if any(row.values()):rows.append(row)
        service.review(db,user.id,ident,integer(data.get('version')),rows,data.get('reason'))
        return redirect(f'/nkt/receivings/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/nkt/receivings/{ident}/confirm')
def confirm(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data)
    try:
        service.confirm(db,user.id,ident,integer(data.get('review')),data.get('digest'),data.get('accepted')=='yes')
        return redirect(f'/nkt/receivings/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)
