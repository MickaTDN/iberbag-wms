"""Admin-only upload/reconciliation; the bounded HTTP body precedes all locks."""
import json,secrets
from fastapi import APIRouter,Request,Depends,HTTPException
from sqlalchemy import select
from .app import database,render,context,redirect
from .valmet_routes import access
from .receiving_routes import receiving_form,failure,integer
from .database import ValmetImportDraft as Draft,ValmetImportRevision as Revision,ValmetImportConfirmation as Confirmation
from . import valmet_import as service
from .valmet_import_upload import upload_form
router=APIRouter()

def allowed(request,db,data=None):
    user,sess=access(request,db,data)
    if user.role!='admin':raise HTTPException(403,'Importação histórica apenas para Administrador.')
    return user,sess

@router.get('/valmet/imports')
def index(request:Request,db=Depends(database)):
    user,sess=allowed(request,db)
    return render(request,'valmet_imports.html',context(request,user,sess,key=secrets.token_hex(16),drafts=db.scalars(select(Draft).order_by(Draft.id.desc()).limit(100)).all()))

@router.post('/valmet/imports/upload')
def upload(request:Request,data:dict=Depends(upload_form),db=Depends(database)):
    user,sess=allowed(request,db,data);actor=user.id;db.rollback()
    try:
        ident=service.preview_file(db,actor,data['request_key'],data['excel'],reauthorize=lambda:allowed(request,db,data))
        return redirect(f'/valmet/imports/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.get('/valmet/imports/{ident}')
def detail(ident:int,request:Request,revision:int|None=None,db=Depends(database)):
    user,sess=allowed(request,db);draft=db.get(Draft,ident)
    if not draft:raise HTTPException(404,'Prévia inexistente neste contexto.')
    revisions=db.scalars(select(Revision).where(Revision.draft_id==ident).order_by(Revision.id.desc())).all()
    current=next((r for r in revisions if r.id==revision),None) if revision else (revisions[0] if revisions else None)
    if revision and not current:raise HTTPException(404,'Revisão não pertence à prévia.')
    original=json.loads(draft.report);original.pop('source_sheets',None)
    return render(request,'valmet_import.html',context(request,user,sess,draft=draft,original=original,revision=current,revisions=revisions,
        plan=json.loads(current.plan) if current else None,key=secrets.token_hex(16),confirmation=db.scalar(select(Confirmation).where(Confirmation.revision_id.in_([r.id for r in revisions])))))

@router.post('/valmet/imports/{ident}/reconcile')
def reconcile(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data)
    try:
        try:decisions=json.loads(data.get('decisions','{}'))
        except (ValueError,RecursionError):raise service.ReceivingError('Decisões JSON inválidas.',400)
        revision=service.reconcile(db,user.id,ident,data.get('key'),decisions,data.get('reason'))
        return redirect(f'/valmet/imports/{ident}?revision={revision}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/valmet/imports/{ident}/confirm')
def confirm(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data)
    try:
        revision=db.get(Revision,integer(data.get('revision')))
        if not revision or revision.draft_id!=ident:raise service.ReceivingError('Revisão inválida.',404)
        service.confirm(db,user.id,revision.id,data.get('key'),data.get('digest'),data.get('accepted')=='yes')
        return redirect(f'/valmet/imports/{ident}?revision={revision.id}')
    except service.ReceivingError as error:return failure(request,user,sess,error)
