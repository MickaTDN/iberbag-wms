"""Global catalogue editing is administrator-only; operations retain scoped sessions."""
from fastapi import Request, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .app import app, database, identity, unauthorized, forbidden, form_body, valid_csrf, context, render, redirect, COOKIE_SECURE
from .database import Client, Warehouse, StorageContext, Location, Stock, Audit
from . import contexts

@app.post('/context')
async def select_context(request: Request, db: Session=Depends(database)):
    user, sess = identity(request, db)
    form = await form_body(request)
    if unauthorized(user) or not valid_csrf(form,sess): return forbidden(request,user,sess)
    try:
        ident = int(form.get('selected_context',''))
        contexts.require_context(db, ident)
    except ValueError as error:
        raise HTTPException(400, 'Contexto inválido ou inativo.') from error
    response = redirect('/')
    response.set_cookie('ddn_context',str(ident),httponly=True,secure=COOKIE_SECURE,samesite='strict',path='/')
    return response

@app.get('/settings/clients')
def clients_page(request: Request, db: Session=Depends(database)):
    user,sess = identity(request,db)
    if unauthorized(user,True): return forbidden(request,user,sess)
    return render(request,'clients.html',context(request,user,sess,
        owners=db.scalars(select(Client).order_by(Client.name)).all(),
        warehouses=db.scalars(select(Warehouse).order_by(Warehouse.name)).all(),
        locations=db.scalars(select(Location).order_by(Location.codigo)).all()))

@app.post('/settings/clients')
async def client_create(request: Request, db: Session=Depends(database)):
    user,sess=identity(request,db,require_admin=True); form=await form_body(request)
    if unauthorized(user,True) or not valid_csrf(form,sess): return forbidden(request,user,sess)
    try:
        contexts.create_client(db,user.id,form.get('name'),int(form.get('warehouse','')),form.get('area'),form.get('pallet_operations')=='yes')
    except (ValueError,IntegrityError) as error:
        db.rollback(); raise HTTPException(409, 'Nome, área ou configuração inválida/já existente.') from error
    return redirect('/settings/clients')

@app.post('/settings/clients/{ident}')
async def client_edit(ident: int, request: Request, db: Session=Depends(database)):
    user,sess=identity(request,db,require_admin=True); form=await form_body(request)
    if unauthorized(user,True) or not valid_csrf(form,sess): return forbidden(request,user,sess)
    owner=db.get(Client,ident)
    if not owner: raise HTTPException(404,'Cliente inexistente.')
    try:
        owner.name=contexts.name(form.get('name'))
        # Keep historical area assignments and workflow capabilities immutable here.
        db.add(Audit(username=user.username,action='CONFIGURAR_CLIENTE',details=f'Cliente {ident}; nome atualizado.'))
        db.commit()
    except (ValueError,IntegrityError) as error:
        db.rollback(); raise HTTPException(409,'Nome inválido ou já existente.') from error
    return redirect('/settings/clients')

@app.post('/settings/warehouses')
async def warehouse_create(request: Request, db: Session=Depends(database)):
    user,sess=identity(request,db,require_admin=True); form=await form_body(request)
    if unauthorized(user,True) or not valid_csrf(form,sess): return forbidden(request,user,sess)
    try:
        db.add(Warehouse(name=contexts.name(form.get('name')),active=True))
        db.add(Audit(username=user.username,action='CRIAR_ARMAZEM',details='Novo armazém configurado.'))
        db.commit()
    except (ValueError,IntegrityError) as error:
        db.rollback(); raise HTTPException(409,'Nome inválido ou já existente.') from error
    return redirect('/settings/clients')

@app.post('/settings/contexts')
async def context_create(request: Request, db: Session=Depends(database)):
    user,sess=identity(request,db,require_admin=True); form=await form_body(request)
    if unauthorized(user,True) or not valid_csrf(form,sess): return forbidden(request,user,sess)
    try:
        owner=db.get(Client,int(form.get('client',''))); wh=db.get(Warehouse,int(form.get('warehouse','')))
        if not owner or not wh: raise ValueError()
        item=StorageContext(client_id=owner.id,warehouse_id=wh.id,area=contexts.name(form.get('area')),active=True)
        db.add(item); db.flush()
        db.execute(Location.__table__.insert().values(context_id=item.id,codigo='Chão',capacidade=None,active=True))
        db.add(Audit(username=user.username,action='CRIAR_CONTEXTO',details=f'Contexto {item.id}; cliente {owner.id}; armazém {wh.id}.'))
        db.commit()
    except (ValueError,IntegrityError) as error:
        db.rollback(); raise HTTPException(409,'Cliente, armazém ou área inválidos/já existentes.') from error
    return redirect('/settings/clients')

@app.post('/settings/capacity')
async def capacity_edit(request: Request, db: Session=Depends(database)):
    user,sess=identity(request,db,require_admin=True); form=await form_body(request)
    if unauthorized(user,True) or not valid_csrf(form,sess): return forbidden(request,user,sess)
    from .warehouse_settings import set_capacity, WarehouseError
    try:
        set_capacity(db,user.username,form.get('location',''),form.get('capacity',''))
    except WarehouseError as error:
        raise HTTPException(409,str(error)) from error
    return redirect('/settings/clients')
