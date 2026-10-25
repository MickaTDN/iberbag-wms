"""Server-rendered operational forms; synchronous handlers keep DB locks off the event loop."""
import secrets
from urllib.parse import parse_qs
from fastapi import APIRouter, Request, Depends, HTTPException
from sqlalchemy import select, func, or_
from sqlalchemy.orm import Session
from .app import database, identity, render, context, redirect, valid_csrf, forbidden
from .database import Receipt, ReceiptLine, Putaway
from . import receiving as service

router = APIRouter()


async def receiving_form(request: Request):
    if request.headers.get('content-type', '').split(';')[0] != 'application/x-www-form-urlencoded':
        raise HTTPException(400, 'Formulário inválido.')
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 65536:
            raise HTTPException(413, 'Formulário demasiado grande.')
    try:
        fields = parse_qs(body.decode('utf-8', 'strict'), keep_blank_values=True)
    except UnicodeDecodeError:
        raise HTTPException(400, 'Codificação do formulário inválida.')
    if any(len(values) != 1 for values in fields.values()):
        raise HTTPException(400, 'Campos repetidos no formulário.')
    return {key: values[0] for key, values in fields.items()}


def form_actor(request, db, data):
    user, sess = identity(request, db)
    if not user or user.role not in ('admin', 'operador') or not valid_csrf(data, sess):
        raise HTTPException(403, 'Operação não autorizada ou sessão/formulário inválido.')
    return user, sess


def failure(request, user, sess, error):
    return render(request, 'message.html', context(request, user, sess,
        title='Operação não confirmada', message=str(error)), error.status)


def integer(value):
    try:
        return int(value)
    except (ValueError, TypeError):
        raise service.ReceivingError('Identificador ou revisão inválidos.', 400)


@router.get('/receipts')
def receipts(request: Request, page: int = 1, db: Session = Depends(database)):
    user, sess = identity(request, db)
    if not user:
        return redirect('/login')
    page = max(1, page)
    query = select(Receipt).where(or_(Receipt.status == 'confirmed', Receipt.created_by == user.id))
    count = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(Receipt.id.desc()).offset((page - 1) * 50).limit(50)).all()
    return render(request, 'receipts.html', context(request, user, sess, rows=rows,
        page=page, total_pages=max(1, (count + 49) // 50)))


@router.get('/receipts/new')
def new_receipt(request: Request, db: Session = Depends(database)):
    user, sess = identity(request, db)
    if not user:
        return redirect('/login')
    return render(request, 'receipt_form.html', context(request, user, sess, receipt=None,
        lines=[], request_key=secrets.token_hex(16)))


@router.get('/receipts/{receipt_id}/edit')
def edit_receipt(receipt_id: int, request: Request, db: Session = Depends(database)):
    user, sess = identity(request, db)
    if not user:
        return redirect('/login')
    receipt = db.get(Receipt, receipt_id)
    if not receipt or receipt.created_by != user.id or receipt.status != 'draft':
        return forbidden(request, user, sess)
    lines = db.scalars(select(ReceiptLine).where(ReceiptLine.receipt_id == receipt.id).order_by(ReceiptLine.id)).all()
    return render(request, 'receipt_form.html', context(request, user, sess, receipt=receipt,
        lines=lines, request_key=receipt.request_key))


@router.post('/receipts/review')
def review_receipt(request: Request, data: dict = Depends(receiving_form), db: Session = Depends(database)):
    user, sess = form_actor(request, db, data)
    try:
        rows = [(data.get(f'artigo_{i}', ''), data.get(f'lote_{i}', ''), data.get(f'quantidade_{i}', '')) for i in range(20)]
        rows = [row for row in rows if any(value.strip() for value in row)]
        receipt_id = service.save_receipt(db, user.id, data.get('request_key'), data.get('code'),
            data.get('received_on'), rows, integer(data['receipt_id']) if data.get('receipt_id') else None,
            integer(data['version']) if data.get('version') else None)
        return redirect(f'/receipts/{receipt_id}')
    except service.ReceivingError as error:
        return failure(request, user, sess, error)


@router.get('/receipts/{receipt_id}')
def receipt_detail(receipt_id: int, request: Request, db: Session = Depends(database)):
    user, sess = identity(request, db)
    if not user:
        return redirect('/login')
    receipt = db.get(Receipt, receipt_id)
    if not receipt or (receipt.status == 'draft' and receipt.created_by != user.id):
        raise HTTPException(404, 'Entrada não encontrada.')
    lines = db.scalars(select(ReceiptLine).where(ReceiptLine.receipt_id == receipt.id).order_by(ReceiptLine.id)).all()
    events = db.scalars(select(Putaway).join(ReceiptLine).where(ReceiptLine.receipt_id == receipt.id,
        Putaway.status == 'confirmed').order_by(Putaway.id)).all()
    return render(request, 'receipt_detail.html', context(request, user, sess, receipt=receipt,
        lines=lines, pending={line.id: service.remaining(db, line) for line in lines}, events=events))


@router.post('/receipts/{receipt_id}/confirm')
def confirm_receipt(receipt_id: int, request: Request, data: dict = Depends(receiving_form), db: Session = Depends(database)):
    user, sess = form_actor(request, db, data)
    try:
        service.confirm_receipt(db, user.id, receipt_id, integer(data.get('version')))
        return redirect(f'/receipts/{receipt_id}')
    except service.ReceivingError as error:
        return failure(request, user, sess, error)


@router.get('/putaway')
def pending_putaway(request: Request, page: int = 1, db: Session = Depends(database)):
    user, sess = identity(request, db)
    if not user:
        return redirect('/login')
    totals = select(Putaway.line_id, func.sum(Putaway.quantidade).label('placed')).where(
        Putaway.status == 'confirmed').group_by(Putaway.line_id).subquery()
    balance = ReceiptLine.quantidade - func.coalesce(totals.c.placed, 0)
    query = select(ReceiptLine, Receipt, balance.label('pending')).join(Receipt).outerjoin(
        totals, totals.c.line_id == ReceiptLine.id).where(Receipt.status == 'confirmed', balance > 0)
    count = db.scalar(select(func.count()).select_from(query.subquery()))
    page = max(1, page)
    rows = db.execute(query.order_by(Receipt.received_on, ReceiptLine.id).offset((page - 1) * 50).limit(50)).all()
    return render(request, 'putaway.html', context(request, user, sess, rows=rows, page=page,
        total_pages=max(1, (count + 49) // 50), request_keys={line.id: secrets.token_hex(16) for line, receipt, balance in rows}))


@router.post('/putaway/review')
def review_putaway(request: Request, data: dict = Depends(receiving_form), db: Session = Depends(database)):
    user, sess = form_actor(request, db, data)
    try:
        operation = service.review_putaway(db, user.id, data.get('request_key'), integer(data.get('line_id')),
            data.get('local'), data.get('quantidade'))
        return redirect(f'/putaway/{operation}')
    except service.ReceivingError as error:
        return failure(request, user, sess, error)


@router.get('/putaway/{operation_id}')
def putaway_detail(operation_id: int, request: Request, db: Session = Depends(database)):
    user, sess = identity(request, db)
    if not user:
        return redirect('/login')
    operation = db.get(Putaway, operation_id)
    if not operation or (operation.status == 'draft' and operation.created_by != user.id):
        raise HTTPException(404, 'Arrumação não encontrada.')
    line = db.get(ReceiptLine, operation.line_id)
    receipt = db.get(Receipt, line.receipt_id)
    return render(request, 'putaway_detail.html', context(request, user, sess, operation=operation,
        line=line, receipt=receipt))


@router.post('/putaway/{operation_id}/confirm')
def confirm_putaway(operation_id: int, request: Request, data: dict = Depends(receiving_form), db: Session = Depends(database)):
    user, sess = form_actor(request, db, data)
    try:
        service.confirm_putaway(db, user.id, operation_id)
        return redirect(f'/putaway/{operation_id}')
    except service.ReceivingError as error:
        return failure(request, user, sess, error)
