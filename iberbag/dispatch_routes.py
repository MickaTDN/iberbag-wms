"""Authenticated server-side FIFO order forms; no editable stock/carga selection."""
import secrets
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy import select, func, or_
from sqlalchemy.orm import Session
from .app import database, identity, render, context, redirect
from .receiving_routes import receiving_form, form_actor, failure, integer
from .database import DispatchOrder
from . import dispatching as service

router = APIRouter()


def visible(db, ident, user):
    order = db.get(DispatchOrder, ident)
    if order is None or (order.status == 'draft' and order.created_by != user.id):
        raise HTTPException(404, 'Pedido não encontrado.')
    return order


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
    return render(request, 'dispatch_form.html', context(request, user, sess, order=None, lines=[], request_key=secrets.token_hex(16)))


@router.post('/dispatches/save')
def save(request: Request, data: dict = Depends(receiving_form), db: Session = Depends(database)):
    user, sess = form_actor(request, db, data)
    try:
        # Only article, lot and quantities are accepted; no user-selected stock or load.
        rows = [(data.get(f'artigo_{i}', ''), data.get(f'lote_{i}', ''), data.get(f'quantidade_{i}', '')) for i in range(20)]
        rows = [row for row in rows if any(v.strip() for v in row)]
        ident = service.save_order(db, user.id, data.get('request_key'), data.get('reference'), rows,
            integer(data['order_id']) if data.get('order_id') else None,
            integer(data['version']) if data.get('version') else None)
        return redirect(f'/dispatches/{ident}')
    except service.DispatchError as error: return failure(request, user, sess, error)


@router.get('/dispatches/{ident}/edit')
def edit(ident: int, request: Request, db: Session = Depends(database)):
    user, sess = identity(request, db)
    if not user: return redirect('/login')
    order = visible(db, ident, user)
    if order.status != 'draft' or order.created_by != user.id: raise HTTPException(403)
    return render(request, 'dispatch_form.html', context(request, user, sess, order=order,
        lines=service.lines(db, ident), request_key=order.request_key))


@router.get('/dispatches/{ident}')
def detail(ident: int, request: Request, db: Session = Depends(database)):
    user, sess = identity(request, db)
    if not user: return redirect('/login')
    order = visible(db, ident, user)
    lines = service.lines(db, ident)
    return render(request, 'dispatch_detail.html', context(request, user, sess, order=order, lines=lines,
        picks=service.picks(db, ident), by_line={l.id: l for l in lines},
        available={l.id: service.availability(db, l.artigo, l.lote) for l in lines}))


@router.post('/dispatches/{ident}/review')
def review(ident: int, request: Request, data: dict = Depends(receiving_form), db: Session = Depends(database)):
    user, sess = form_actor(request, db, data)
    try:
        service.review_order(db, user.id, ident, integer(data.get('version')))
        return redirect(f'/dispatches/{ident}')
    except service.DispatchError as error: return failure(request, user, sess, error)


@router.post('/dispatches/{ident}/check')
def check(ident: int, request: Request, data: dict = Depends(receiving_form), db: Session = Depends(database)):
    user, sess = form_actor(request, db, data)
    try:
        values = {integer(key[7:]): value for key, value in data.items() if key.startswith('actual_')}
        service.check_order(db, user.id, ident, values, data.get('note', ''))
        return redirect(f'/dispatches/{ident}')
    except service.DispatchError as error: return failure(request, user, sess, error)


@router.post('/dispatches/{ident}/confirm')
def confirm(ident: int, request: Request, data: dict = Depends(receiving_form), db: Session = Depends(database)):
    user, sess = form_actor(request, db, data)
    try:
        service.confirm_order(db, user.id, ident)
        return redirect(f'/dispatches/{ident}')
    except service.DispatchError as error: return failure(request, user, sess, error)
