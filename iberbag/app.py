"""Iberbag WMS: office preparations, incoming allocation, inventory and warehouse consultation."""
import hmac, os, re
from contextlib import asynccontextmanager
from datetime import timedelta
from urllib.parse import parse_qs
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, desc, or_, delete, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from pathlib import Path
from .database import (SessionLocal, init_db, User, LoginSession, FailedLogin, Stock,
                       Carga, Location, Audit, now_utc)
from .location_rules import capacity_for, is_floor, is_unassigned
from .database import Corridor
from .warehouse_settings import (WarehouseError, add_corridor, update_corridor, remove_corridor,
                                 add_location, remove_location, rename_location, corridor_locations)
from .security import (verify_password, set_password, token_hash, new_session,
                       get_session, too_many_failures, log_failed_login, clear_old_attempts)

@asynccontextmanager
async def lifespan(_app):
    init_db()
    yield

app = FastAPI(title='Iberbag WMS', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
BASE = Path(__file__).parent
app.mount('/static', StaticFiles(directory=BASE / 'static'), name='static')
templates = Jinja2Templates(directory=BASE / 'templates')
COOKIE_SECURE = os.environ.get('WMS_COOKIE_SECURE', '0') == '1'

def database():
    with SessionLocal() as db:
        yield db

def identity(request: Request, db: Session):
    # Serialize administrative writes across workers before reading permissions/state.
    # Transaction-scoped lock is released by commit/rollback/session close.
    if request.method == 'POST' and db.bind.dialect.name == 'postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(72834001)'))
    session = get_session(db, request.cookies.get('wms_session'))
    return (session.user, session) if session else (None, None)

def render(request, template, context, status=200):
    return templates.TemplateResponse(request, template, context, status_code=status,
                                      headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                                               'Referrer-Policy': 'same-origin', 'X-Frame-Options': 'DENY',
                                               'Content-Security-Policy': "default-src 'self'; script-src 'none'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})

def redirect(to):
    return RedirectResponse(to, status_code=303)

def unauthorized(user, require_admin=False):
    return user is None or (require_admin and user.role != 'admin')

def context(request, user, sess, **kwargs):
    return {'request': request, 'user': user, 'csrf': sess.csrf_token if sess else '', **kwargs}

async def form_body(request: Request):
    if request.headers.get('content-type', '').split(';')[0] != 'application/x-www-form-urlencoded':
        return {}
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 8192:
            raise HTTPException(status_code=413, detail='Formulário demasiado grande.')
    return {k: vals[0] for k, vals in parse_qs(body.decode('utf-8', 'replace'), keep_blank_values=True).items()}

def valid_csrf(form, sess):
    return bool(sess and hmac.compare_digest(form.get('csrf', ''), sess.csrf_token))

def forbidden(request, user, sess, msg='Operação não autorizada.'):
    return render(request, 'message.html', context(request, user, sess, title='Acesso negado', message=msg), 403)

@app.get('/login', response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(database)):
    user, sess = identity(request, db)
    if user: return redirect('/')
    return render(request, 'login.html', context(request, None, None))

@app.post('/login')
async def login(request: Request, db: Session = Depends(database)):
    data = await form_body(request)
    username = data.get('username', '').strip()[:60]
    password = data.get('password', '')[:250]
    ip = (request.client.host if request.client else 'unknown')[:64]
    if not username or not password:
        return render(request, 'login.html', context(request, None, None, error='Preenche o utilizador e a palavra-passe.'), 400)
    if too_many_failures(db, username, ip):
        return render(request, 'login.html', context(request, None, None, error='Demasiadas tentativas. Tenta novamente daqui a 15 minutos.'), 429)
    user = db.scalar(select(User).where(User.username == username, User.active.is_(True)))
    if not verify_password(user, password):
        log_failed_login(db, username, ip); db.commit()
        return render(request, 'login.html', context(request, None, None, error='Credenciais incorretas.'), 401)
    db.execute(delete(FailedLogin).where(FailedLogin.username == username))
    token = new_session(db, user)
    db.add(Audit(username=user.username, action='LOGIN', details='Sessão iniciada.'))
    db.commit()
    resp = redirect('/')
    resp.set_cookie('wms_session', token, max_age=8 * 3600, httponly=True, secure=COOKIE_SECURE, samesite='strict', path='/')
    return resp

@app.post('/logout')
async def logout(request: Request, db: Session = Depends(database)):
    user, sess = identity(request, db)
    form = await form_body(request)
    if user and not valid_csrf(form, sess):
        return forbidden(request, user, sess, 'Pedido inválido.')
    if sess:
        db.delete(sess); db.commit()
    resp = redirect('/login')
    resp.delete_cookie('wms_session', path='/')
    return resp

@app.get('/', response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(database)):
    user, sess = identity(request, db)
    if unauthorized(user): return redirect('/login')
    stats = db.execute(select(func.coalesce(func.sum(Stock.quantidade),0),func.count(func.distinct(Stock.artigo)),
                              func.count(func.distinct(Stock.lote)), func.count(func.distinct(Stock.local))).where(Stock.quantidade > 0)).first()
    occupied = db.execute(select(Stock.local, func.sum(Stock.quantidade)).where(Stock.quantidade > 0).group_by(Stock.local)).all()
    exceeding_locations = [loc for loc, total in occupied if capacity_for(loc) is not None and total > 1]
    unidentified_pallets = sum(total for loc, total in occupied if is_unassigned(loc))
    missing_dates = db.scalar(select(func.count()).select_from(Carga).where(Carga.entrada.is_(None))) or 0
    return render(request, 'dashboard.html', context(request, user, sess, stats=stats,
                  exceeding_locations=exceeding_locations, unidentified_pallets=unidentified_pallets,
                  missing_dates=missing_dates))

@app.get('/stock', response_class=HTMLResponse)
def stock_page(request: Request, artigo: str='', lote: str='', local: str='', page: int=1,
               db: Session=Depends(database)):
    user, sess = identity(request, db)
    if unauthorized(user): return redirect('/login')
    page = max(1, min(page, 10000)); filters = {'artigo': artigo.strip()[:100], 'lote': lote.strip()[:100], 'local': local.strip()[:100]}
    query = select(Stock).where(Stock.quantidade > 0)
    count_query = select(func.count(), func.coalesce(func.sum(Stock.quantidade),0)).select_from(Stock).where(Stock.quantidade > 0)
    for key, value in filters.items():
        if value:
            field = getattr(Stock, key)
            # literal partial search, not wildcards typed by the end user
            pattern = '%' + value.replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%'
            condition = field.ilike(pattern, escape='\\')
            query = query.where(condition); count_query = count_query.where(condition)
    count, pallet_count = db.execute(count_query).one()
    rows = db.scalars(query.order_by(Stock.artigo,Stock.lote,Stock.local,Stock.id)
                      .offset((page-1)*50).limit(50)).all()
    return render(request, 'stock.html', context(request, user, sess, rows=rows, filters=filters, page=page,
                  count=count, pallet_count=pallet_count, total_pages=max(1, (count+49)//50)))

@app.get('/stock/{stock_id}', response_class=HTMLResponse)
def stock_details(stock_id: int, request: Request, db: Session=Depends(database)):
    user, sess = identity(request, db)
    if unauthorized(user): return redirect('/login')
    item = db.get(Stock, stock_id)
    if item is None:
        return render(request, 'message.html',
                      context(request, user, sess, title='Registo inexistente',
                              message='Esta palete ou grupo já não consta no stock.'), 404)
    carga = db.scalar(select(Carga).where(Carga.codigo == item.carga))
    from .database import Putaway
    events = db.scalars(select(Putaway).where(Putaway.stock_id == item.id, Putaway.status == 'confirmed').order_by(Putaway.id)).all()
    from .database import DispatchOrder, DispatchPick, DispatchLine
    dispatches = db.execute(select(DispatchPick, DispatchOrder).join(DispatchLine, DispatchLine.id == DispatchPick.line_id).join(DispatchOrder, DispatchOrder.id == DispatchLine.order_id).where(DispatchPick.stock_id == item.id, DispatchOrder.status == 'confirmed').order_by(DispatchOrder.id)).all()
    from .database import StockTransfer
    transfers = db.scalars(select(StockTransfer).where(or_(StockTransfer.source_stock_id == item.id, StockTransfer.destination_stock_id == item.id), StockTransfer.status == 'confirmed').order_by(StockTransfer.id)).all()
    return render(request, 'stock_detail.html', context(request, user, sess, item=item, carga=carga, events=events, dispatches=dispatches, transfers=transfers))

@app.get('/locations')
def old_locations_redirect(request: Request, db: Session=Depends(database)):
    """Backward-compatible navigation, no longer an independent operator menu."""
    user, sess = identity(request, db)
    if unauthorized(user): return redirect('/login')
    return redirect('/settings/locations' if user.role == 'admin' else '/map')


def settings_error(request, user, sess, message, status=409):
    return render(request, 'message.html', context(request, user, sess,
                  title='Não foi possível alterar', message=message), status)

@app.get('/settings', response_class=HTMLResponse)
def settings_page(request: Request, db: Session=Depends(database)):
    """Landing page for current and future settings subsections."""
    user, sess = identity(request, db)
    if unauthorized(user, True): return forbidden(request, user, sess)
    return render(request, 'settings_home.html', context(request, user, sess))

@app.get('/settings/locations', response_class=HTMLResponse)
def location_settings_page(request: Request, db: Session=Depends(database)):
    user, sess = identity(request, db)
    if unauthorized(user, True): return forbidden(request, user, sess)
    corridors = db.scalars(select(Corridor).order_by(Corridor.codigo)).all()
    current_count = db.scalar(select(func.count()).select_from(Location)) or 0
    return render(request, 'settings.html', context(request, user, sess,
                  corridors=corridors, current_count=current_count))

@app.get('/map', response_class=HTMLResponse)
def warehouse_map(request: Request, corredor: str='', andar: int=1,
                  db: Session=Depends(database)):
    """Read-only, server-rendered map of the configured physical positions.

    The catalogue defines whether a position exists; the grouped stock
    determines occupancy. Never infer the catalogue from current stock.
    """
    user, sess = identity(request, db)
    if unauthorized(user): return redirect('/login')
    corridors = db.scalars(select(Corridor).order_by(Corridor.codigo)).all()
    if not corridors:
        return render(request, 'warehouse_map.html', context(request, user, sess,
                      corridors=[], current=None, selected_floor=None,
                      slots=[], free=0, used=0, anomalies=0, removed=0,
                      floor_stock=0, floor_pallets=0, floor_name='Chão',
                      unassigned=0, special_stock=[]))
    current = next((c for c in corridors if c.codigo == corredor.strip().upper()), corridors[0])
    selected_floor = andar if 1 <= andar <= current.andares else 1
    catalogue = db.scalars(select(Location).where(Location.codigo.like(f'{current.codigo}%'))).all()
    configured = {loc.codigo for loc in catalogue}
    inactive = {loc.codigo for loc in catalogue if not loc.active}
    # Restrict by the full 4-character code in Python, rather than matching
    # arbitrary historical names or strings that happen to share a prefix.
    codes = [f'{current.codigo}{selected_floor}{i:02d}' for i in range(1, current.posicoes+1)]
    quantities = dict(db.execute(select(Stock.local, func.sum(Stock.quantidade))
                                 .where(Stock.local.in_(codes))
                                 .group_by(Stock.local)).all())
    slots = []
    free = used = anomalies = removed = 0
    for code in codes:
        qty = int(quantities.get(code, 0))
        exists = code in configured
        if not exists:
            status = 'anomaly' if qty else 'removed'
            if qty: anomalies += 1
            else: removed += 1
        elif code in inactive and qty == 0:
            status = 'inactive'
            removed += 1
        elif qty > 1:
            status = 'anomaly'
            anomalies += 1
        elif qty == 1:
            status = 'used'
            used += 1
        else:
            status = 'free'
            free += 1
        slots.append({'code': code, 'qty': qty, 'status': status,
                      'configured': exists})
    # Floor and missing locations are not silently treated as rack positions.
    from .location_rules import is_floor, is_unassigned
    all_stock = db.execute(select(Stock.local, func.sum(Stock.quantidade)).where(Stock.quantidade > 0).group_by(Stock.local)).all()
    floor_pallets = sum(int(qty) for code,qty in all_stock if is_floor(code))
    unassigned = sum(int(qty) for code,qty in all_stock if is_unassigned(code))
    return render(request, 'warehouse_map.html', context(request, user, sess,
                  corridors=corridors, current=current, selected_floor=selected_floor,
                  slots=slots, free=free, used=used, anomalies=anomalies,
                  removed=removed, floor_pallets=floor_pallets,
                  unassigned=unassigned))

@app.get('/settings/corridors/{code}', response_class=HTMLResponse)
def corridor_page(code: str, request: Request, db: Session=Depends(database)):
    user, sess = identity(request, db)
    if unauthorized(user, True): return forbidden(request, user, sess)
    code = code.upper()
    corridor = db.scalar(select(Corridor).where(Corridor.codigo == code))
    if not corridor:
        return settings_error(request, user, sess, 'Corredor não encontrado.', 404)
    locations = corridor_locations(db, code)
    count = {loc: qty for loc, qty in db.execute(select(Stock.local, func.sum(Stock.quantidade))
                     .where(Stock.local.in_(list(locations) or ['!']))
                     .group_by(Stock.local)).all()}
    entries = [dict(code=f'{code}{floor}{pos:02d}', floor=floor, pos=pos,
                    present=f'{code}{floor}{pos:02d}' in locations,
                    active=locations[f'{code}{floor}{pos:02d}'].active if f'{code}{floor}{pos:02d}' in locations else False,
                    occupied=count.get(f'{code}{floor}{pos:02d}', 0))
               for floor in range(1, corridor.andares+1) for pos in range(1, corridor.posicoes+1)]
    return render(request, 'corridor.html', context(request, user, sess, corridor=corridor,
                   entries=entries, count=count, floor_range=range(1,corridor.andares+1)))

async def admin_form(request, db):
    user, sess = identity(request, db)
    if unauthorized(user, True): return user, sess, None, forbidden(request,user,sess)
    data = await form_body(request)
    if not valid_csrf(data, sess): return user, sess, None, forbidden(request,user,sess,'Pedido inválido (CSRF).')
    return user, sess, data, None

@app.post('/settings/corridors')
async def create_corridor(request: Request, db: Session=Depends(database)):
    user, sess, data, error = await admin_form(request, db)
    if error: return error
    try:
        code = add_corridor(db, user.username, data.get('codigo'), data.get('andares'), data.get('posicoes'))
    except WarehouseError as e:
        db.rollback(); return settings_error(request, user, sess, str(e))
    return redirect(f'/settings/corridors/{code}')

@app.post('/settings/corridors/{code}/update')
async def edit_corridor(code: str, request: Request, db: Session=Depends(database)):
    user, sess, data, error = await admin_form(request, db)
    if error: return error
    try:
        update_corridor(db,user.username,code.upper(),data.get('andares'),data.get('posicoes'))
    except WarehouseError as e:
        db.rollback(); return settings_error(request,user,sess,str(e))
    return redirect(f'/settings/corridors/{code.upper()}')

@app.post('/settings/corridors/{code}/delete')
async def delete_corridor(code: str, request: Request, db: Session=Depends(database)):
    user, sess, data, error = await admin_form(request, db)
    if error: return error
    try: remove_corridor(db,user.username,code.upper())
    except WarehouseError as e:
        db.rollback(); return settings_error(request,user,sess,str(e))
    return redirect('/settings/locations')

@app.post('/settings/locations')
async def create_location(request: Request, db: Session=Depends(database)):
    user, sess, data, error = await admin_form(request, db)
    if error: return error
    try: code = add_location(db,user.username,data.get('codigo'))
    except WarehouseError as e:
        db.rollback(); return settings_error(request,user,sess,str(e))
    return redirect(f'/settings/corridors/{code[0]}')

@app.post('/settings/locations/{code}/delete')
async def delete_location(code: str, request: Request, db: Session=Depends(database)):
    user, sess, data, error = await admin_form(request, db)
    if error: return error
    try: remove_location(db,user.username,code)
    except WarehouseError as e:
        db.rollback(); return settings_error(request,user,sess,str(e))
    return redirect(f'/settings/corridors/{code[0].upper()}')

@app.post('/settings/locations/{code}/rename')
async def edit_location(code: str, request: Request, db: Session=Depends(database)):
    user, sess, data, error = await admin_form(request, db)
    if error: return error
    try: newcode = rename_location(db,user.username,code,data.get('novo_codigo'))
    except WarehouseError as e:
        db.rollback(); return settings_error(request,user,sess,str(e))
    return redirect(f'/settings/corridors/{newcode[0]}')

@app.get('/users',response_class=HTMLResponse)
def users_page(request: Request, db: Session=Depends(database)):
    user, sess = identity(request, db)
    if unauthorized(user, True): return forbidden(request, user, sess)
    users = db.scalars(select(User).order_by(User.username)).all()
    return render(request, 'users.html', context(request,user,sess,users=users))

@app.post('/users')
async def users_create(request: Request, db: Session=Depends(database)):
    user, sess = identity(request, db)
    if unauthorized(user, True): return forbidden(request,user,sess)
    data = await form_body(request)
    if not valid_csrf(data, sess):return forbidden(request,user,sess,'Pedido inválido (CSRF).')
    name = data.get('username','').strip()
    fullname = data.get('display_name','').strip()[:120]
    role = data.get('role','')
    password = data.get('password','')
    if not (name and re.fullmatch(r'[A-Za-z0-9_-]{1,60}',name) and fullname and role in ('admin','operador')):
        return render(request,'message.html',context(request,user,sess,title='Dados inválidos',message='Confirma os dados do utilizador.'),400)
    new = User(username=name,display_name=fullname,role=role,active=True)
    try:set_password(new,password)
    except ValueError as err:return render(request,'message.html',context(request,user,sess,title='Palavra-passe inválida',message=str(err)),400)
    db.add(new)
    db.add(Audit(username=user.username,action='CRIAR_UTILIZADOR',details=f'{name} ({role})'))
    try:db.commit()
    except IntegrityError:
        db.rollback()
        return render(request,'message.html',context(request,user,sess,title='Utilizador existente',message='Este nome já existe.'),409)
    return redirect('/users')

@app.post('/users/{uid}/toggle')
async def users_toggle(uid: int, request: Request, db: Session=Depends(database)):
    user, sess = identity(request, db)
    if unauthorized(user, True): return forbidden(request, user, sess)
    data = await form_body(request)
    if not valid_csrf(data,sess):return forbidden(request,user,sess,'Pedido inválido (CSRF).')
    target = db.get(User, uid)
    if not target: return forbidden(request,user,sess,'Utilizador inexistente.')
    if target.id == user.id:
        return forbidden(request,user,sess,'Não podes desativar a tua própria conta.')
    if target.active and target.role == 'admin':
        active_admins = db.scalar(select(func.count()).select_from(User).where(User.active.is_(True), User.role == 'admin')) or 0
        if active_admins <= 1:return forbidden(request,user,sess,'É necessário manter um administrador ativo.')
    target.active = not target.active
    db.add(Audit(username=user.username,action='ALTERAR_UTILIZADOR', details=f'{target.username}: ativo={target.active}'))
    # Remove any active sessions when deactivating an account.
    if not target.active:db.execute(delete(LoginSession).where(LoginSession.user_id==target.id))
    db.commit()
    return redirect('/users')

@app.post('/users/{uid}/reset-password')
async def users_reset_password(uid: int, request: Request, db: Session=Depends(database)):
    user, sess = identity(request, db)
    if unauthorized(user, True): return forbidden(request, user, sess)
    data = await form_body(request)
    if not valid_csrf(data,sess):return forbidden(request,user,sess,'Pedido inválido (CSRF).')
    target = db.get(User,uid)
    if not target:return forbidden(request,user,sess,'Utilizador inexistente.')
    try: set_password(target,data.get('password',''))
    except ValueError as err:
        return render(request,'message.html',context(request,user,sess,title='Palavra-passe inválida',message=str(err)),400)
    db.execute(delete(LoginSession).where(LoginSession.user_id==target.id))
    db.add(Audit(username=user.username,action='ALTERAR_PALAVRA_PASSE', details=f'Conta {target.username}. Sessões encerradas.'))
    db.commit()
    return redirect('/users')

@app.get('/audit',response_class=HTMLResponse)
def audit_page(request: Request, db: Session=Depends(database)):
    user, sess = identity(request, db)
    if unauthorized(user, True): return forbidden(request,user,sess)
    events = db.scalars(select(Audit).order_by(desc(Audit.id)).limit(150)).all()
    return render(request,'audit.html',context(request,user,sess,events=events))

@app.get('/health')
def health(db: Session = Depends(database)):
    from sqlalchemy.exc import SQLAlchemyError
    try:
        db.execute(select(User.id).limit(1))
    except SQLAlchemyError:
        return JSONResponse({'status': 'unavailable'}, status_code=503)
    return {'status': 'ok', 'application': 'Iberbag WMS', 'version':'2.3-prototipo'}

# Imported after shared rendering/auth helpers are defined.
from .receiving_routes import router as receiving_router
app.include_router(receiving_router)

from .dispatch_routes import router as dispatch_router
app.include_router(dispatch_router)

from .transfer_routes import router as transfer_router
app.include_router(transfer_router)

from .inventory_routes import router as inventory_router
app.include_router(inventory_router)

from .commercial_routes import router as commercial_router
app.include_router(commercial_router)
