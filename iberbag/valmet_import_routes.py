"""Admin-only upload/reconciliation; the bounded HTTP body precedes all locks."""
import json,secrets
from email import policy
from email.parser import BytesParser
from fastapi import APIRouter,Request,Depends,HTTPException
from sqlalchemy import select
from .app import database,render,context,redirect
from .valmet_routes import access
from .receiving_routes import receiving_form,failure,integer
from .database import ValmetImportDraft as Draft,ValmetImportRevision as Revision,ValmetImportConfirmation as Confirmation
from . import valmet_import as service
router=APIRouter()

def allowed(request,db,data=None):
    user,sess=access(request,db,data)
    if user.role!='admin':raise HTTPException(403,'Importação histórica apenas para Administrador.')
    return user,sess

async def upload_form(request:Request):
    content_type=request.headers.get('content-type','')
    if not content_type.startswith('multipart/form-data;') or len(content_type)>200 or '\r' in content_type or '\n' in content_type:raise HTTPException(400,'Selecionar um ficheiro Excel.')
    body=getattr(request.state,'wms_body',b'')
    if not body:raise HTTPException(400,'Ficheiro ausente.')
    try:
        envelope=BytesParser(policy=policy.default).parsebytes(b'Content-Type: '+content_type.encode('ascii')+b'\r\n\r\n')
        boundary=envelope.get_boundary()
        if not boundary or not 1<=len(boundary)<=120 or '\r' in boundary or '\n' in boundary:raise ValueError()
        parts=body.split(b'--'+boundary.encode('ascii'))
        if len(parts)!=6 or parts[0] or parts[-1] not in (b'--',b'--\r\n'):raise ValueError()
        data={}
        for raw in parts[1:-1]:
            if not raw.startswith(b'\r\n') or not raw.endswith(b'\r\n'):raise ValueError()
            header,sep,payload=raw[2:-2].partition(b'\r\n\r\n')
            if not sep or len(header)>4096:raise ValueError()
            part=BytesParser(policy=policy.default).parsebytes(header+b'\r\n\r\n')
            name=part.get_param('name',header='content-disposition')
            if part.defects or part.get_content_maintype()=='multipart' or part.get_content_disposition()!='form-data' or name not in ('excel','csrf','context_id','request_key') or name in data or part.get('Content-Transfer-Encoding'):raise ValueError()
            if name=='excel':data[name]=payload
            else:
                if len(payload)>200:raise ValueError()
                data[name]=payload.decode('utf-8','strict')
        if set(data)!= {'excel','csrf','context_id','request_key'}:raise ValueError()
        return data
    except (ValueError,UnicodeError,TypeError):raise HTTPException(400,'Upload inválido; selecionar novamente o Excel.')

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
