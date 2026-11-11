"""Alcobre office forms; shared bounded body, authenticated context and CSRF."""
import json
import secrets
from hashlib import sha256
from fastapi import APIRouter, Request, Depends, HTTPException, Response
from sqlalchemy import select
from .app import database, identity, render, context, redirect
from .receiving_routes import receiving_form, form_actor, failure, integer
from .valmet_import_upload import alcobre_upload_form
from . import alcobre as service, database as m

router=APIRouter()

def allowed(request,db,data=None,admin=False):
    user,sess=form_actor(request,db,data,require_admin=admin,operational=False) if data is not None else identity(request,db)
    if not user or (admin and user.role!='admin') or not service.enabled(db): raise HTTPException(403,'Processo Alcobre indisponível neste contexto.')
    return user,sess

@router.get('/alcobre/entries')
def entries(request:Request,db=Depends(database)):
    user,sess=allowed(request,db)
    return render(request,'alcobre_entries.html',context(request,user,sess,key=secrets.token_hex(16),entries=db.scalars(select(m.AlcobreEntry).order_by(m.AlcobreEntry.id.desc()).limit(100)).all()))

@router.post('/alcobre/entries/new')
def create(request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data)
    try:
        ident=service.create(db,user.id,data.get('key'),data.get('mode'),data.get('reference'),data.get('received_on'))
        return redirect(f'/alcobre/entries/{ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/alcobre/entries/upload')
def upload(request:Request,data:dict=Depends(alcobre_upload_form),db=Depends(database)):
    user,sess=allowed(request,db,data,True);actor=user.id;db.rollback()
    try:
        ident=service.preview_file(db,actor,data['request_key'],data['pdf'],data['source_name'],reauthorize=lambda:allowed(request,db,data,True))
        return redirect(f'/alcobre/entries/{ident}')
    except (service.ReceivingError,ValueError) as error:
        return failure(request,user,sess,error if isinstance(error,service.ReceivingError) else service.ReceivingError(str(error),400))

@router.get('/alcobre/entries/{ident}')
def detail(ident:int,request:Request,review:int|None=None,db=Depends(database)):
    user,sess=allowed(request,db);entry=db.get(m.AlcobreEntry,ident)
    if not entry:raise HTTPException(404,'Receção inexistente neste contexto.')
    revisions=db.scalars(select(m.AlcobreReview).where(m.AlcobreReview.entry_id==ident).order_by(m.AlcobreReview.id.desc())).all()
    revision=next((r for r in revisions if r.id==review),None) if review else (revisions[0] if revisions else None)
    if review and not revision:raise HTTPException(404,'Revisão fora desta receção.')
    units=db.execute(select(m.AlcobreBobine,m.AlcobrePlacement).outerjoin(m.AlcobrePlacement,m.AlcobrePlacement.bobine_id==m.AlcobreBobine.id).where(m.AlcobreBobine.entry_id==ident).order_by(m.AlcobreBobine.id)).all()
    from .alcobre_dispatch import states
    current=states(db)
    source=json.loads(entry.extraction)
    return render(request,'alcobre_entry.html',context(request,user,sess,entry=entry,source=source,revisions=revisions,revision=revision,plan=json.loads(revision.payload) if revision else None,
        states=current,key=secrets.token_hex(16),confirm_key=secrets.token_hex(16),units=units,remaining=entry.quantity-len(units) if entry.quantity is not None else None,
        actions=db.scalars(select(m.AlcobreAction).where(m.AlcobreAction.entry_id==ident).order_by(m.AlcobreAction.id)).all()))

@router.post('/alcobre/entries/{ident}/review')
def review(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data)
    try:
        numbers=data.get('numbers','').splitlines();places=data.get('places','').splitlines()
        if len(places)>len(numbers):raise service.ReceivingError('Mais localizações do que números; rever as correspondências.',400)
        rows=[dict(number=n,local=places[i] if i<len(places) and places[i] else None) for i,n in enumerate(numbers)]
        rid=service.review(db,user.id,ident,integer(data.get('version')),data.get('key'),data.get('kind'),rows,data.get('quantity'),data.get('reason'),data.get('manual')=='yes',data.get('reference'),data.get('received_on'))
        return redirect(f'/alcobre/entries/{ident}?review={rid}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/alcobre/reviews/{ident}/confirm')
def confirm(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data)
    try:
        revision=db.get(m.AlcobreReview,ident)
        if not revision:raise service.ReceivingError('Revisão inexistente.',404)
        target=revision.entry_id;service.confirm(db,user.id,ident,data.get('digest'),data.get('key'),data.get('accepted')=='yes')
        return redirect(f'/alcobre/entries/{target}?review={ident}')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.get('/alcobre/entries/{ident}/source.pdf')
def source(ident:int,request:Request,db=Depends(database)):
    allowed(request,db);entry=db.get(m.AlcobreEntry,ident)
    if not entry or not entry.source_pdf:raise HTTPException(404,'Guia inexistente neste contexto.')
    if sha256(entry.source_pdf).hexdigest()!=entry.source_digest:raise HTTPException(409,'Integridade da guia inválida.')
    return Response(entry.source_pdf,media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="guia-alcobre-{ident}.pdf"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox; default-src 'none'"})

@router.get('/alcobre/stock')
def stock(request:Request,number:str='',local:str='',state:str='all',page:int=1,db=Depends(database)):
    user,sess=allowed(request,db);page=max(1,min(page,10000));number=number[:120];local=local[:120]
    try:rows,total,pending=service.stock(db,number,local,state,page)
    except service.ReceivingError as error:return failure(request,user,sess,error)
    from .alcobre_dispatch import states
    from .alcobre_inventory import incident_locations
    return render(request,'alcobre_stock.html',context(request,user,sess,rows=rows,total=total,pending=pending,number=number,local=local,state=state,page=page,states=states(db),inventory_incidents=incident_locations(db)))

@router.get('/alcobre/settings')
def settings(request:Request,db=Depends(database)):
    user,sess=allowed(request,db,admin=True)
    return render(request,'alcobre_settings.html',context(request,user,sess,zones=db.scalars(select(m.AlcobreZone).order_by(m.AlcobreZone.code)).all(),slots=db.scalars(select(m.AlcobreSlot).order_by(m.AlcobreSlot.zone_id,m.AlcobreSlot.position)).all()))

@router.post('/alcobre/settings/zones')
def zone(request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data,True)
    try:
        service.configure_zone(db,user.id,data.get('code'),data.get('positions'),data.get('active')=='yes',integer(data['id']) if data.get('id') else None)
        return redirect('/alcobre/settings')
    except service.ReceivingError as error:return failure(request,user,sess,error)

@router.post('/alcobre/settings/slots')
def slot(request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=allowed(request,db,data,True)
    try:
        service.configure_slot(db,user.id,integer(data.get('zone')),data.get('position'),data.get('code'))
        return redirect('/alcobre/settings')
    except service.ReceivingError as error:return failure(request,user,sess,error)
