"""Office evidence capture, never automatic reconstruction from aggregate totals."""
import secrets,json
from sqlalchemy import select
from fastapi import APIRouter,Request,Depends,HTTPException
from .app import database,render,context,redirect
from .valmet_routes import access
from .receiving_routes import receiving_form,failure,integer
from .database import ValmetUnit,ValmetPosition,ValmetRoll,ValmetRollState,ValmetRollEvent
from . import valmet_rolls as r,valmet as v
router=APIRouter()

@router.get('/valmet/units/{ident}/rolls')
def detail(ident:int,request:Request,db=Depends(database)):
    user,sess=access(request,db);unit=db.get(ValmetUnit,ident)
    if not unit or unit.kind!='rolos':raise HTTPException(404,'Palete ROLOS inexistente neste contexto.')
    rows=db.execute(select(ValmetRoll,ValmetRollState).join(ValmetRollState,ValmetRollState.id==ValmetRoll.id).where(ValmetRoll.unit_id==ident).order_by(ValmetRoll.id)).all()
    return render(request,'valmet_rolls.html',context(request,user,sess,unit=unit,rows=rows,key=secrets.token_hex(16),
        positions=db.scalars(select(ValmetPosition).where(ValmetPosition.unit_id==ident)).all(),
        events=db.scalars(select(ValmetRollEvent).where(ValmetRollEvent.unit_id==ident).order_by(ValmetRollEvent.id)).all()))

@router.post('/valmet/units/{ident}/rolls')
def proof(ident:int,request:Request,data:dict=Depends(receiving_form),db=Depends(database)):
    user,sess=access(request,db,data)
    if user.role!='admin':raise HTTPException(403,'Só Administrador pode reconciliar classificações/identidades.')
    try:
        if data.get('confirmed')!='yes':raise v.ReceivingError('Confirmar explicitamente a comprovação individual, sem inferir pelos totais.')
        if data.get('mode')=='register':r.reconcile(db,user.id,ident,data.get('key'),r.parse_lines(data.get('rows','')),data.get('reason'),data.get('evidence'))
        elif data.get('mode')=='classify':
            flags={integer(name[5:]):value=='X' for name,value in data.items() if name.startswith('flag_') and value in ('X','-')}
            r.classify(db,user.id,ident,data.get('key'),flags,data.get('reason'),data.get('evidence'))
        else:raise v.ReceivingError('Operação inválida.')
        return redirect(f'/valmet/units/{ident}/rolls')
    except v.ReceivingError as error:return failure(request,user,sess,error)
