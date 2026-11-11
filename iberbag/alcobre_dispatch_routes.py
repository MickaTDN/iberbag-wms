"""Office-only Alcobre guides; bounded PDF parsing, CSRF and context preflight."""
import json,secrets
from hashlib import sha256
from fastapi import APIRouter,Request,Depends,HTTPException,Response
from sqlalchemy import select,or_
from .app import database,render,context,redirect
from .alcobre_routes import allowed
from .receiving_routes import receiving_form,failure,integer
from .valmet_import_upload import alcobre_upload_form
from . import alcobre_dispatch as service,database as m
router=APIRouter()

@router.get('/alcobre/dispatches')
def index(request:Request,db=Depends(database)):
    user,sess=allowed(request,db)
    return render(request,'alcobre_dispatches.html',context(request,user,sess,key=secrets.token_hex(16),guides=db.scalars(select(m.AlcobreDispatch).order_by(m.AlcobreDispatch.id.desc()).limit(200)).all()))

@router.post('/alcobre/dispatches/upload')
def upload(request:Request,data:dict=Depends(alcobre_upload_form),db=Depends(database)):
    user,sess=allowed(request,db,data,True);actor=user.id;db.rollback()
    try:
        ident=service.preview_file(db,actor,data['request_key'],data['pdf'],data['source_name'],reauthorize=lambda:allowed(request,db,data,True))
        return redirect(f'/alcobre/dispatches/{ident}')
    except (service.ReceivingError,ValueError) as error:return failure(request,user,sess,error if isinstance(error,service.ReceivingError) else service.ReceivingError(str(error),400))

@router.get('/alcobre/dispatches/{ident}')
def detail(ident:int,request:Request,review:int|None=None,db=Depends(database)):
    user,sess=allowed(request,db);guide=db.get(m.AlcobreDispatch,ident)
    if not guide:raise HTTPException(404,'Guia inexistente neste contexto.')
    revisions=db.scalars(select(m.AlcobreDispatchRevision).where(m.AlcobreDispatchRevision.dispatch_id==ident).order_by(m.AlcobreDispatchRevision.id.desc())).all()
    selected=next((r for r in revisions if r.id==review),None) if review else (revisions[0] if revisions else None)
    if review and not selected:raise HTTPException(404,'Revisão fora desta guia.')
    operations=db.scalars(select(m.AlcobreDispatchOperation).where(m.AlcobreDispatchOperation.dispatch_id==ident).order_by(m.AlcobreDispatchOperation.id)).all()
    terminal=next((o for o in operations if o.kind=='confirm'),None)
    plan=json.loads(terminal.snapshot)['plan'] if terminal else (json.loads(selected.payload) if selected else None)
    docs=db.scalars(select(m.AlcobreDispatchDocument).where(or_(m.AlcobreDispatchDocument.revision_id.in_([r.id for r in revisions]),m.AlcobreDispatchDocument.operation_id.in_([o.id for o in operations]))).order_by(m.AlcobreDispatchDocument.id)).all()
    return render(request,'alcobre_dispatch.html',context(request,user,sess,guide=guide,source=json.loads(guide.extraction),revision=selected,revisions=revisions,plan=plan,operations=operations,documents=docs,before=operations[-1].id if operations else None,keys={name:secrets.token_hex(16) for name in ('review','prepare','confirm','cancel')},reasons=service.REASONS))

@router.post('/alcobre/dispatches/{ident}/review')
def review(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data)
    try:
        revision=service.review(db,user.id,ident,integer(data.get('version')),data.get('key'),data.get('numbers',''),data.get('reason'),data.get('manual')=='yes',data.get('reference'))
        return redirect(f'/alcobre/dispatches/{ident}?review={revision}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/alcobre/dispatches/{ident}/prepare')
def prepare(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data)
    try:
        service.prepare(db,user.id,ident,integer(data.get('version')),data.get('digest'),data.get('key'),data.get('accepted')=='yes')
        return redirect(f'/alcobre/dispatches/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/alcobre/dispatches/{ident}/confirm')
def confirm(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data)
    try:
        guide=db.get(m.AlcobreDispatch,ident)
        if not guide:raise service.ReceivingError('Guia inexistente.',404)
        rev=service.latest(db,guide)
        if not rev:raise service.ReceivingError('Rever e preparar a guia primeiro.')
        rows=json.loads(rev.payload)['items'];selected=[];exceptions={}
        for i,row in enumerate(rows):
            if data.get(f'collected{i}')=='yes':selected.append(row['requested'])
            if data.get(f'exception{i}'):exceptions[row['requested']]=data[f'exception{i}']
        service.confirm(db,user.id,ident,integer(data.get('version')),integer(data.get('before')),data.get('key'),selected,exceptions,data.get('loaded_on'),data.get('accepted')=='yes')
        return redirect(f'/alcobre/dispatches/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/alcobre/dispatches/{ident}/cancel')
def cancel(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data)
    try:
        service.cancel(db,user.id,ident,integer(data.get('version')),integer(data['before']) if data.get('before') else None,data.get('key'),data.get('reason'))
        return redirect(f'/alcobre/dispatches/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.get('/alcobre/dispatches/{ident}/source.pdf')
def source(ident:int,request:Request,db=Depends(database)):
    allowed(request,db);guide=db.get(m.AlcobreDispatch,ident)
    if not guide:raise HTTPException(404,'Guia inexistente neste contexto.')
    if sha256(guide.source_pdf).hexdigest()!=guide.source_digest:raise HTTPException(409,'Integridade do original inválida.')
    return Response(guide.source_pdf,media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="guia-saida-alcobre-{ident}.pdf"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox; default-src 'none'"})

@router.get('/alcobre/dispatch-documents/{ident}.pdf')
def document(ident:int,request:Request,db=Depends(database)):
    allowed(request,db);doc=db.get(m.AlcobreDispatchDocument,ident)
    if not doc:raise HTTPException(404,'Documento inexistente neste contexto.')
    if sha256(doc.pdf).hexdigest()!=doc.digest:raise HTTPException(409,'Integridade do documento inválida.')
    return Response(doc.pdf,media_type='application/pdf',headers={'Content-Disposition':f'inline; filename="recolha-alcobre-{ident}.pdf"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox; default-src 'none'"})
