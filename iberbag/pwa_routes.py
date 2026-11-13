"""Read-only operator projections; no mobile stock commands in PR38."""
from pathlib import Path
from datetime import datetime,timezone
from fastapi import APIRouter,Depends,Request,HTTPException
from fastapi.responses import FileResponse,JSONResponse,RedirectResponse
from sqlalchemy import select,func
from . import database as m
from .app import database,identity
from .contexts import available,require_context
router=APIRouter()
ROOT=Path(__file__).with_name('pwa')
CSP="default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; worker-src 'self'; manifest-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"

def secure(request):
    from .app import COOKIE_SECURE
    if request.url.scheme!='https' or not COOKIE_SECURE:raise HTTPException(503,'PWA exige HTTPS e sessões Secure. Contactar o escritório.')

def actor(request,db):
    secure(request);user,sess=identity(request,db)
    if not user:raise HTTPException(401,'Iniciar sessão novamente.')
    return user,sess

def allowed(row):return row[1].id in (1,3) and row[2].id==1

@router.get('/pwa/api/session')
def session(request:Request,db=Depends(database)):
    u,s=actor(request,db)
    return JSONResponse(dict(user=u.id,name=u.display_name,csrf=s.csrf_token,current=m.context_id(db),contexts=[dict(id=c.id,label=o.name+' / '+w.name) for c,o,w in available(db) if allowed((c,o,w))]),headers={'Cache-Control':'no-store'})

@router.get('/pwa/api/tasks')
def tasks(request:Request,context_id:int,db=Depends(database)):
    u,s=actor(request,db)
    row=require_context(db)
    if context_id!=m.context_id(db) or not allowed(row):raise HTTPException(403,'Selecionar explicitamente um contexto autorizado.')
    items=[]
    def add(kind,ref,ident,version,lines,status):items.append(dict(kind=kind,reference=ref,id=ident,version=version,lines=lines,status=status))
    if row[1].id==3:
        for rec in db.scalars(select(m.CosmoReception).where(m.CosmoReception.status!='approved').order_by(m.CosmoReception.id)):
            lines=[dict(article=p.article,lot=p.lot,quantity=1,boxes=b.actual_boxes,classification='INCOMPLETA' if p.incomplete else 'COMPLETA',location=b.local,status=b.status) for p,b in db.execute(select(m.CosmoPallet,m.CosmoState).join(m.CosmoState,m.CosmoState.pallet_id==m.CosmoPallet.id).where(m.CosmoPallet.reception_id==rec.id).order_by(m.CosmoPallet.source_row,m.CosmoPallet.ordinal))]
            add('Receção',rec.guide or str(rec.id),rec.id,rec.version,lines,rec.status)
        for h in db.scalars(select(m.CosmoPackingState).where(m.CosmoPackingState.stage.in_(('preparing','prepared','loading','load_closed'))).order_by(m.CosmoPackingState.packing_id)):
            from .cosmopak_packings import snapshot
            lines=[dict(article=r['line']['article'],lot=r['line']['lot'],quantity=1,boxes=r['line']['boxes'],classification=('INCOMPLETA' if r['pallet']['incomplete'] else 'COMPLETA') if r['pallet'] else 'EM FALTA',location=(r['material'] or {}).get('local'),status=(('No camião — carregada' if r['pick']['loaded'] else 'No camião — conferência pendente') if (r['physical'] or {}).get('phase')=='loading' else 'Ocorrência por conferir' if r['pick']['reason'] else 'Preparada' if r['pick']['prepared'] else 'Por preparar')) for r in snapshot(db,h.packing_id)['items']]
            add('PACKING',h.reference,h.packing_id,h.version,lines,h.stage)
    else:
        from .receiving import remaining
        for rec in db.scalars(select(m.Receipt).where(m.Receipt.status=='confirmed').order_by(m.Receipt.id)):
            lines=[dict(article=l.artigo,lot=l.lote,quantity=remaining(db,l),location=None,status='Aguarda arrumação') for l in db.scalars(select(m.ReceiptLine).where(m.ReceiptLine.receipt_id==rec.id)) if remaining(db,l)>0]
            if lines:add('Arrumação',rec.code,rec.id,rec.version,lines,'Pendente')
        for order in db.scalars(select(m.DispatchOrder).where(m.DispatchOrder.office.is_(True),m.DispatchOrder.status=='confirmed').order_by(m.DispatchOrder.id)):
            if db.get(m.PreparationCompletion,order.id) or db.get(m.PreparationCompensation,order.id):continue
            lines=[]
            for pick in db.scalars(select(m.DispatchPick).join(m.DispatchLine,m.DispatchLine.id==m.DispatchPick.line_id).where(m.DispatchLine.order_id==order.id)):
                stock=db.get(m.Stock,pick.stock_id)
                if stock:lines.append(dict(article=stock.artigo,lot=stock.lote,quantity=pick.quantidade,location=pick.local,status='SOBREPOSTAS — retirar juntas' if stock.stacked else order.status))
            add('Recolha',order.reference,order.id,order.version,lines,order.status)
        for incident,pick,line in db.execute(select(m.StockIncident,m.DispatchPick,m.DispatchLine).join(m.DispatchPick,m.DispatchPick.id==m.StockIncident.pick_id).join(m.DispatchLine,m.DispatchLine.id==m.DispatchPick.line_id).where(m.StockIncident.status=='open').order_by(m.StockIncident.id)):
            order=db.get(m.DispatchOrder,line.order_id)
            add('Ocorrência',order.reference,incident.id,0,[dict(article=line.artigo,lot=line.lote,quantity=incident.quantidade,location=pick.local,status='Ocorrência por conferir no escritório')],'Pendente')
    return JSONResponse(dict(user=u.id,context=context_id,synced_at=datetime.now(timezone.utc).isoformat(),tasks=items),headers={'Cache-Control':'no-store'})

@router.get('/pwa')
@router.get('/pwa/')
def entry(request:Request):
    secure(request)
    if request.url.path=='/pwa':return RedirectResponse('/pwa/',status_code=303)
    return asset('index.html',request)

@router.get('/pwa/{filename}')
def asset(filename:str,request:Request):
    secure(request)
    if filename not in ('index.html','app.js','vault.js','app.css','sw.js','manifest.webmanifest','icon.svg','icon-192.png','icon-512.png'):raise HTTPException(404)
    media={'js':'text/javascript','html':'text/html','css':'text/css','webmanifest':'application/manifest+json','svg':'image/svg+xml','png':'image/png'}
    return FileResponse(ROOT/filename,media_type=media[filename.rsplit('.',1)[-1]],headers={'Cache-Control':'no-cache','Content-Security-Policy':CSP,'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','X-Frame-Options':'DENY'})
