"""Authenticated server-side FIFO order forms; no editable stock/carga selection."""
import secrets
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy import select, func, or_
from sqlalchemy.orm import Session
from .app import database, identity, render, context, redirect
from .receiving_routes import receiving_form, form_actor, failure, integer
from .database import DispatchOrder, DispatchDocument, PreparationCompletion, PreparationCompensation, Stock
from . import dispatching as service

router = APIRouter()


def visible(db, ident, user):
    order = db.get(DispatchOrder, ident)
    if order is None or (order.status == 'draft' and order.created_by != user.id and user.role != 'admin'):
        raise HTTPException(404, 'Pedido não encontrado.')
    return order


def lot_options(db):
    from .lots import resolve
    options=[]
    for lote in db.scalars(select(Stock.lote).distinct().order_by(Stock.lote).limit(500)):
        try:
            artigo=resolve(db,lote)
            total=service.availability(db,artigo,lote)['total']
            label=f'{artigo} · {total} palete(s)'
        except service.DispatchError:
            label='Reconciliação administrativa necessária'
        options.append(dict(lote=lote,label=label))
    return options


@router.get('/dispatches')
def index(request: Request, page: int = 1, db: Session = Depends(database)):
    user, sess = identity(request, db)
    if not user: return redirect('/login')
    query = select(DispatchOrder).where(or_(DispatchOrder.status != 'draft', DispatchOrder.created_by == user.id))
    count = db.scalar(select(func.count()).select_from(query.subquery()))
    page = max(1, min(page, 100000))
    orders = db.scalars(query.order_by(DispatchOrder.id.desc()).offset((page - 1) * 50).limit(50)).all()
    return render(request, 'dispatches.html', context(request, user, sess, orders=orders,
        page=page, total_pages=max(1, (count + 49) // 50)))


@router.get('/dispatches/new')
def new(request: Request, db: Session = Depends(database)):
    user, sess = identity(request, db)
    if not user: return redirect('/login')
    return render(request, 'dispatch_form.html', context(request, user, sess, order=None, lines=[], request_key=secrets.token_hex(16),lot_options=lot_options(db)))


@router.post('/dispatches/save')
def save(request: Request, data: dict = Depends(receiving_form), db: Session = Depends(database)):
    user, sess = form_actor(request, db, data)
    try:
        rows = [(data.get(f'lote_{i}', ''), data.get(f'quantidade_{i}', '')) for i in range(20)]
        rows = [row for row in rows if any(v.strip() for v in row)]
        ident = service.save_preparation(db, user.id, data.get('request_key'), data.get('reference') or 'Preparação de escritório', rows,
            integer(data['order_id']) if data.get('order_id') else None,
            integer(data['version']) if data.get('version') else None)
        return redirect(f'/dispatches/{ident}')
    except service.DispatchError as error: return failure(request, user, sess, error)


@router.get('/dispatches/{ident}/edit')
def edit(ident: int, request: Request, db: Session = Depends(database)):
    user, sess = identity(request, db)
    if not user: return redirect('/login')
    order = visible(db, ident, user)
    if not order.office: raise HTTPException(409,'Pedido legado: cancelar administrativamente e recriar uma preparação por lote.')
    if order.status != 'draft' or order.created_by != user.id: raise HTTPException(403)
    return render(request, 'dispatch_form.html', context(request, user, sess, order=order,
        lines=service.lines(db, ident), request_key=order.request_key,lot_options=lot_options(db)))


@router.get('/dispatches/{ident}')
def detail(ident: int, request: Request, db: Session = Depends(database)):
    user, sess = identity(request, db)
    if not user: return redirect('/login')
    order = visible(db, ident, user)
    lines = service.lines(db, ident)
    return render(request, 'office_detail.html' if order.office else 'dispatch_detail.html', context(request, user, sess, order=order, lines=lines,
        incident_key=secrets.token_hex(16), document=db.get(DispatchDocument,order.id), completion=db.get(PreparationCompletion,order.id), compensation=db.get(PreparationCompensation,order.id),
        picks=service.picks(db, ident), by_line={l.id: l for l in lines},
        available={l.id: service.availability(db, l.artigo, l.lote) for l in lines}))


@router.post('/dispatches/{ident}/review')
def review(ident: int, request: Request, data: dict = Depends(receiving_form), db: Session = Depends(database)):
    user, sess = form_actor(request, db, data)
    try:
        order = visible(db,ident,user)
        if not order.office: raise HTTPException(409,'Pedido legado: cancelar administrativamente e recriar a preparação.')
        method = service.review_preparation
        method(db, user.id, ident, integer(data.get('version')))
        return redirect(f'/dispatches/{ident}')
    except service.DispatchError as error: return failure(request, user, sess, error)


@router.post('/dispatches/{ident}/check')
def check(ident: int, request: Request, data: dict = Depends(receiving_form), db: Session = Depends(database)):
    user, sess = form_actor(request, db, data)
    raise HTTPException(409,'A confirmação da preparação é feita no escritório; não existe segundo passo de recolha. Resolver pedidos legados administrativamente.')


@router.post('/dispatches/{ident}/confirm')
def confirm(ident: int, request: Request, data: dict = Depends(receiving_form), db: Session = Depends(database)):
    user, sess = form_actor(request, db, data)
    try:
        order = visible(db,ident,user)
        if not order.office: raise HTTPException(409,'Pedido legado: resolver/cancelar administrativamente; não retomar recolha antiga.')
        method = service.confirm_preparation
        method(db, user.id, ident)
        return redirect(f'/dispatches/{ident}')
    except service.DispatchError as error: return failure(request, user, sess, error)


@router.get('/dispatches/{ident}/document.pdf')
def document_pdf(ident: int, request: Request, db: Session = Depends(database)):
    from fastapi.responses import Response
    user, sess = identity(request,db)
    if not user: return redirect('/login')
    order = visible(db,ident,user)
    doc = db.get(DispatchDocument,ident)
    if order.status!='confirmed' or doc is None: raise HTTPException(404,'Documento não disponível para este pedido.')
    return Response(doc.pdf,media_type='application/pdf',headers={'Content-Disposition':f'inline; filename="preparacao-{ident}.pdf"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


@router.post('/dispatches/{ident}/cancel')
def cancel(ident: int, request: Request, data: dict = Depends(receiving_form), db: Session = Depends(database)):
    user,sess=form_actor(request,db,data)
    try:
        service.cancel_order(db,user.id,ident,data.get('reason'),data.get('returned')=='1')
        return redirect(f'/dispatches/{ident}')
    except service.DispatchError as error: return failure(request,user,sess,error)


@router.post('/dispatches/{ident}/archive-document')
def archive_legacy(ident:int,request:Request,data:dict=Depends(receiving_form),db:Session=Depends(database)):
    user,sess=form_actor(request,db,data)
    try:
        service.archive_legacy_document(db,user.id,ident)
        return redirect(f'/dispatches/{ident}')
    except service.DispatchError as error:return failure(request,user,sess,error)
