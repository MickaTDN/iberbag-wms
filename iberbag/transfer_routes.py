"""Stock movement UI, distinct from the offline SQLite migration tool."""
import secrets
from fastapi import APIRouter, Request, Depends, HTTPException
from sqlalchemy import select, func, or_
from sqlalchemy.orm import Session
from .app import database, identity, render, context, redirect
from .receiving_routes import receiving_form, form_actor, failure, integer
from .database import Stock, StockTransfer
from . import transferring as service

router = APIRouter()


@router.get('/transfers')
def index(request: Request, page: int = 1, db: Session = Depends(database)):
    user, sess = identity(request, db)
    if not user: return redirect('/login')
    query = select(StockTransfer).where(or_(StockTransfer.status == 'confirmed', StockTransfer.created_by == user.id))
    count = db.scalar(select(func.count()).select_from(query.subquery()))
    page = max(1, min(page, 100000))
    rows = db.scalars(query.order_by(StockTransfer.id.desc()).offset((page-1)*50).limit(50)).all()
    return render(request, 'transfers.html', context(request, user, sess, rows=rows, page=page, total_pages=max(1, (count+49)//50)))


@router.get('/transfers/new')
def new(request: Request, source: str = '', db: Session = Depends(database)):
    user, sess = identity(request, db)
    if not user: return redirect('/login')
    try:
        local = service.canonical_location(db, source) if source.strip() else ''
    except service.TransferError as error: return failure(request, user, sess, error)
    rows = db.scalars(select(Stock).where(Stock.local == local, Stock.quantidade > 0).order_by(
        Stock.artigo, Stock.lote, Stock.carga, Stock.id)).all() if local else []
    return render(request, 'transfer_form.html', context(request, user, sess, source=local, rows=rows, request_key=secrets.token_hex(16)))


@router.post('/transfers/review')
def review(request: Request, data: dict = Depends(receiving_form), db: Session = Depends(database)):
    user, sess = form_actor(request, db, data)
    try:
        ident = service.review_transfer(db, user.id, data.get('request_key'), integer(data.get('stock_id')),
            data.get('destination'), data.get('quantidade'))
        return redirect(f'/transfers/{ident}')
    except service.TransferError as error: return failure(request, user, sess, error)


@router.get('/transfers/{ident}')
def detail(ident: int, request: Request, db: Session = Depends(database)):
    user, sess = identity(request, db)
    if not user: return redirect('/login')
    transfer = db.get(StockTransfer, ident)
    if transfer is None or (transfer.status == 'draft' and transfer.created_by != user.id):
        raise HTTPException(404, 'Transferência não encontrada.')
    return render(request, 'transfer_detail.html', context(request, user, sess, transfer=transfer))


@router.post('/transfers/{ident}/confirm')
def confirm(ident: int, request: Request, data: dict = Depends(receiving_form), db: Session = Depends(database)):
    user, sess = form_actor(request, db, data)
    try:
        service.confirm_transfer(db, user.id, ident)
        return redirect(f'/transfers/{ident}')
    except service.TransferError as error: return failure(request, user, sess, error)


@router.post('/settings/locations/{code}/active')
def active_location(code: str, request: Request, data: dict = Depends(receiving_form), db: Session = Depends(database)):
    user, sess = form_actor(request, db, data)
    if user.role != 'admin': raise HTTPException(403, 'Só o administrador gere o catálogo.')
    if data.get('active') not in ('0', '1'): raise HTTPException(400, 'Estado de localização inválido.')
    from .warehouse_settings import set_location_active, WarehouseError
    try:
        set_location_active(db, user.username, code, data['active'] == '1')
    except WarehouseError as error:
        return failure(request, user, sess, service.TransferError(str(error)))
    return redirect('/settings/locations')
