import json
import secrets
from fastapi import APIRouter,Request,Depends,HTTPException
from sqlalchemy import select
from .app import database,render,context,redirect
from .receiving_routes import receiving_form,failure,integer
from .valmet_routes import access
from .database import (ValmetPreparation as Prep,ValmetPlanRevision as Rev,ValmetUnit,ValmetPosition,ValmetOccurrence,ValmetDocument)
from . import valmet as v,valmet_planning as p

router=APIRouter()

@router.get('/valmet/preparations')
def listing(request:Request,db=Depends(database)):
    user,sess=access(request,db)
    records=db.scalars(select(Prep).order_by(Prep.id.desc()).limit(100)).all()
    return render(request,'valmet_preparations.html',context(request,user,sess,records=records))

@router.get('/valmet/preparations/new')
def new(request:Request,db=Depends(database)):
    user,sess=access(request,db)
    return render(request,'valmet_preparation_form.html',context(request,user,sess,record=None,data={},kinds=v.KINDS,brands=v.BRANDS,key=secrets.token_hex(16)))

@router.get('/valmet/preparations/{ident}/edit')
def edit(ident:int,request:Request,db=Depends(database)):
    user,sess=access(request,db);record=db.get(Prep,ident)
    try:p.access(record,user)
    except v.ReceivingError as error:return failure(request,user,sess,error)
    return render(request,'valmet_preparation_form.html',context(request,user,sess,record=record,data=json.loads(record.payload),kinds=v.KINDS,brands=v.BRANDS,key=record.request_key))

@router.post('/valmet/preparations/save')
def save(request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=access(request,db,data)
    try:
        lines=[(data.get(f'kind{i}'),data.get(f'lot{i}'),data.get(f'q{i}')) for i in range(20)
            if any(data.get(f'{field}{i}') for field in ('kind','lot','q'))]
        ident=p.save(db,user.id,data.get('key'),data.get('reference'),data.get('day'),data.get('destination'),lines,
            integer(data['id']) if data.get('id') else None,integer(data['version']) if data.get('version') else None)
        return redirect(f'/valmet/preparations/{ident}')
    except v.ReceivingError as e:return failure(request,user,sess,e)

@router.get('/valmet/preparations/{ident}')
def detail(ident:int,request:Request,candidate_number:str='',candidate_local:str='',db=Depends(database)):
    user,sess=access(request,db);record=db.get(Prep,ident)
    if not record:raise HTTPException(404,'Preparação inexistente.')
    revisions=db.scalars(select(Rev).where(Rev.preparation_id==ident).order_by(Rev.id.desc())).all()
    # Previous records are preparations/reservations, never labelled as dispatches.
    previous=db.scalars(select(Prep).where(Prep.id!=ident).order_by(Prep.id.desc()).limit(100)).all()
    try:candidates=p.source(db,ident)
    except v.ReceivingError as error:return failure(request,user,sess,error)
    data=json.loads(record.payload);requested={(line['kind'],line['lot']) for line in data['lines']}
    candidate_number=candidate_number.strip()[:120];candidate_local=candidate_local.strip()[:120]
    candidates=[dict(u,available={lot:q for lot,q in u['available'].items()
        if (u['kind'],lot) in requested and (q is None or q>0)}) for u in candidates
        if candidate_number.casefold() in (u['number'] or '').casefold()
        and candidate_local.casefold() in (u['local'] or 'Por localizar').casefold()]
    candidates=[u for u in candidates if u['available']]
    from . import valmet_shipping as shipping,valmet_balances as balances
    loads=shipping.shipments(db,ident)
    occurrences=db.scalars(select(ValmetOccurrence).where(ValmetOccurrence.preparation_id==ident).order_by(ValmetOccurrence.id)).all()
    documents=db.scalars(select(ValmetDocument).where(ValmetDocument.preparation_id==ident).order_by(ValmetDocument.id)).all()
    seen={s['unit'].get('origin_id',s['unit']['id']) for r in revisions if r.action=='reserve' for s in json.loads(r.snapshot)['selections']}
    positions=[balances.view(z,u) for z,u in db.execute(select(ValmetPosition,ValmetUnit).join(ValmetUnit,ValmetUnit.id==ValmetPosition.unit_id).where(ValmetUnit.id.in_(seen),ValmetPosition.physical_quantity>0))]
    return render(request,'valmet_preparation.html',context(request,user,sess,record=record,data=data,
        plan=json.loads(record.review) if record.review else None,revisions=revisions,previous=previous,
        candidates=candidates,candidate_number=candidate_number,candidate_local=candidate_local,
        candidate_count=sum(len(u['available']) for u in candidates),kinds=v.KINDS,review_key=p.review_key(record),
        active_plan=json.loads(db.get(Rev,record.active_revision).snapshot) if record.active_revision else None,
        loads=loads,occurrences=occurrences,documents=documents,document_versions={d.id:db.get(Rev,d.revision_id).version for d in documents},positions=positions,shipping_key=secrets.token_hex(16)))

@router.post('/valmet/preparations/{ident}/review')
def review(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=access(request,db,data)
    try:
        manual=None
        if data.get('manual')=='yes':
            manual=[]
            for i in range(100):
                if data.get(f'pick{i}')=='yes':manual.append((integer(data.get(f'uid{i}')),data.get(f'lot{i}'),data.get(f'q{i}')))
        roll_selection={} if data.get('individual')=='yes' else None
        if roll_selection is not None:
            for name,value in data.items():
                if name.startswith('rollpick_') and value=='yes':
                    _,unit,rid=name.split('_');roll_selection.setdefault(integer(unit),[]).append(integer(rid))
            if not roll_selection:raise v.ReceivingError('Escolher pelo menos um rolo.')
        p.review(db,user.id,ident,integer(data.get('version')),manual,roll_selection)
        return redirect(f'/valmet/preparations/{ident}')
    except v.ReceivingError as e:return failure(request,user,sess,e)

@router.post('/valmet/preparations/{ident}/confirm')
def confirm(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=access(request,db,data)
    try:
        if not data.get('review_key'):raise v.ReceivingError('Abrir a revisão antes de confirmar.',400)
        p.confirm(db,user.id,ident,integer(data.get('version')),data.get('extras')=='yes',data.get('unlocated')=='yes',data.get('uncontrolled')=='yes',data['review_key'])
        return redirect(f'/valmet/preparations/{ident}')
    except v.ReceivingError as e:return failure(request,user,sess,e)

@router.post('/valmet/preparations/{ident}/cancel')
def cancel(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=access(request,db,data)
    try:
        p.cancel(db,user.id,ident,integer(data.get('version')),data.get('reason'))
        return redirect(f'/valmet/preparations/{ident}')
    except v.ReceivingError as e:return failure(request,user,sess,e)

@router.post('/valmet/units/{ident}/handling')
def handling(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    from .receiving_routes import form_actor
    user,sess=form_actor(request,db,data,require_admin=True,operational=False)
    if not v.enabled(db):raise HTTPException(403,'Contexto Valmet obrigatório.')
    try:
        p.configure(db,user.id,ident,data.get('separation'),data.get('reconciled')=='yes',data.get('unlocated')=='yes',data.get('note'))
        return redirect('/valmet/stock')
    except v.ReceivingError as e:return failure(request,user,sess,e)
