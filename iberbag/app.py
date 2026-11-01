"""DDN WMS: office preparations, incoming allocation, inventory and warehouse consultation."""
import hmac, os, re, secrets
from contextlib import asynccontextmanager
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
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
from .location_rules import is_floor, is_unassigned
from .database import Corridor
from .warehouse_settings import (WarehouseError, add_corridor, update_corridor, remove_corridor,
                                 add_location, remove_location, rename_location, corridor_locations)
from .security import (verify_password, set_password, token_hash, new_session,
                       get_session, too_many_failures, log_failed_login, clear_old_attempts)

@asynccontextmanager
async def lifespan(_app):
    init_db()
    yield

app = FastAPI(title='DDN WMS', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
from .http_body import BoundedPostBody
app.add_middleware(BoundedPostBody)
BASE = Path(__file__).parent
app.mount('/static', StaticFiles(directory=BASE / 'static'), name='static')
templates = Jinja2Templates(directory=BASE / 'templates')
COOKIE_SECURE = os.environ.get('WMS_COOKIE_SECURE', '0') == '1'

def database(request: Request):
    from .contexts import require_context, available
    try:
        ident = int(request.cookies.get('ddn_context', '1'))
        if ident < 1: raise ValueError()
    except ValueError:
        raise HTTPException(400, 'Contexto cliente/armazém inválido.')
    with SessionLocal(info={'ddn_context': ident}) as db:
        try:
            ctx, owner, warehouse = require_context(db)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        request.state.ddn_context = ident
        request.state.ddn_contexts = available(db)
        yield db

def identity(request: Request, db: Session, *, data=None, require_admin=False, operational=False):
    session = get_session(db, request.cookies.get('wms_session'))
    if not session or session.user.role not in ('admin','operador'):
        return None, None
    if request.method == 'POST':
        # Middleware has finished receiving the bounded body before any DB work.
        form = parsed_form(request) if data is None else data
        validate_form_context(request, form)
        if not valid_csrf(form, session) or (require_admin and session.user.role != 'admin'):
            return session.user, session  # Existing handlers render their denial.
        from .contexts import require_context
        try:
            require_context(db, operational=operational)
            if db.bind.dialect.name == 'postgresql':
                from .database import require_write_isolation
                require_write_isolation(db)
                db.execute(text('SELECT pg_advisory_xact_lock(72834001)'))
                db.expire_all()
                session = get_session(db, request.cookies.get('wms_session'))
                if (not session or not valid_csrf(form, session)
                        or (require_admin and session.user.role != 'admin')):
                    db.rollback()
                    raise HTTPException(403, 'Sessão ou permissões alteradas. Reabrir o formulário.')
                require_context(db, operational=operational)
                validate_form_context(request, form)
        except ValueError as error:
            db.rollback()
            raise HTTPException(409, str(error)) from error
    return session.user, session

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
    return {'request': request, 'user': user, 'csrf': sess.csrf_token if sess else '',
            'active_context_id': getattr(request.state, 'ddn_context', 1),
            'ddn_contexts': getattr(request.state, 'ddn_contexts', []), **kwargs}

def parsed_form(request: Request):
    if request.headers.get('content-type', '').split(';')[0] != 'application/x-www-form-urlencoded':
        return {}
    body = getattr(request.state, 'wms_body', None)
    if body is None:
        raise HTTPException(400, 'Corpo do formulário não validado.')
    if len(body) > 8192:
        raise HTTPException(status_code=413, detail='Formulário demasiado grande.')
    fields = parse_qs(body.decode('utf-8', 'replace'), keep_blank_values=True)
    if any(len(vals) != 1 for vals in fields.values()):
        raise HTTPException(400, 'Campos repetidos no formulário.')
    form = {k: vals[0] for k, vals in fields.items()}
    validate_form_context(request, form)
    return form


def validate_form_context(request, form):
    if 'csrf' in form:
        current = str(getattr(request.state, 'ddn_context', 1))
        # Old default-context forms remain compatible. New forms always include
        # context_id: a tab opened before switching cannot write in a new context.
        if form.get('context_id', '1') != current:
            raise HTTPException(409, 'O contexto mudou noutra página. Reabrir o formulário no cliente/armazém correto.')


async def form_body(request: Request):
    return parsed_form(request)


def valid_csrf(form, sess):
    value = form.get('csrf', '')
    return bool(sess and isinstance(value, str) and value.isascii()
                and hmac.compare_digest(value, sess.csrf_token))

def forbidden(request, user, sess, msg='Operação não autorizada.'):
    return render(request, 'message.html', context(request, user, sess, title='Acesso negado', message=msg), 403)

def login_response(request, error=None, status=200):
    token = secrets.token_hex(32)
    response = render(request, 'login.html', context(request, None, None, error=error, login_csrf=token), status)
    response.set_cookie('__Host-wms_login' if COOKIE_SECURE else 'wms_login', token,
                        max_age=600, httponly=True, secure=COOKIE_SECURE, samesite='strict', path='/')
    return response


def valid_login_origin(request):
    expected = str(request.base_url).rstrip('/')
    for name in ('origin', 'referer'):
        values = request.headers.getlist(name)
        if len(values) > 1: return False
        if not values: continue
        value = values[0]
        if name == 'origin':
            if value != expected: return False
        else:
            try:
                parts = urlsplit(value)
                if parts.username is not None or parts.password is not None or '\\' in value or any(ord(c) <= 32 for c in value): return False
                if f'{parts.scheme}://{parts.netloc}' != expected: return False
            except ValueError: return False
    return True


@app.get('/login', response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(database)):
    user, sess = identity(request, db)
    if user: return redirect('/')
    return login_response(request)

@app.post('/login')
async def login(request: Request, db: Session = Depends(database)):
    data = await form_body(request)
    submitted = data.get('login_csrf', '')
    cookie = request.cookies.get('__Host-wms_login' if COOKIE_SECURE else 'wms_login', '')
    if not (valid_login_origin(request) and re.fullmatch(r'[a-f0-9]{64}', submitted) and re.fullmatch(r'[a-f0-9]{64}', cookie) and hmac.compare_digest(submitted, cookie)):
        return login_response(request, 'Formulário de início de sessão inválido. Volte a tentar.', 403)
    username = data.get('username', '').strip()[:60]
    password = data.get('password', '')[:250]
    ip = (request.client.host if request.client else 'unknown')[:64]
    if not username or not password:
        return login_response(request, 'Preenche o utilizador e a palavra-passe.', 400)
    if too_many_failures(db, username, ip):
        return login_response(request, 'Demasiadas tentativas. Tenta novamente daqui a 15 minutos.', 429)
    user = db.scalar(select(User).where(User.username == username, User.active.is_(True)))
    if not verify_password(user, password) or user.role not in ('admin','operador'):
        log_failed_login(db, username, ip); db.commit()
        return login_response(request, 'Credenciais incorretas.', 401)
    if db.bind.dialect.name == 'postgresql':
        # Login has no session yet: credentials/challenge/body are validated
        # first. Serialize session creation with account revocation/reset too.
        ident, verified = user.id, (user.password_salt, user.password_hash)
        from .database import require_write_isolation
        try:
            require_write_isolation(db)
        except ValueError:
            db.rollback()
            return login_response(request, 'Configuração transacional incompatível. Contactar o administrador.', 503)
        db.execute(text('SELECT pg_advisory_xact_lock(72834001)'))
        db.expire_all()
        user = db.get(User, ident)
        if (not user or not user.active or user.username != username
                or user.role not in ('admin','operador')
                or (user.password_salt, user.password_hash) != verified):
            db.rollback()
            return login_response(request, 'Credenciais ou permissões alteradas. Volte a tentar.', 401)
        from .contexts import require_context
        try:
            require_context(db)
        except ValueError:
            db.rollback()
            return login_response(request, 'Contexto alterado ou inativo. Reabrir o acesso no contexto correto.', 409)
    db.execute(delete(FailedLogin).where(FailedLogin.username == username))
    token = new_session(db, user)
    db.add(Audit(username=user.username, action='LOGIN', details='Sessão iniciada.'))
    db.commit()
    resp = redirect('/')
    resp.delete_cookie('__Host-wms_login' if COOKIE_SECURE else 'wms_login', path='/', secure=COOKIE_SECURE, samesite='strict')
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
    capacities = dict(db.execute(select(Location.codigo, Location.capacidade)).all())
    exceeding_locations = [loc for loc, total in occupied if capacities.get(loc) is not None and total > capacities[loc]]
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
        totals = db.execute(select(Stock.local, func.sum(Stock.quantidade)).where(
            Stock.quantidade > 0).group_by(Stock.local)).all()
        return render(request, 'warehouse_map.html', context(request, user, sess,
                      corridors=[], current=None, selected_floor=None,
                      slots=[], free=0, used=0, anomalies=0, removed=0,
                      floor_pallets=sum(q for code,q in totals if is_floor(code)),
                      unassigned=sum(q for code,q in totals if is_unassigned(code))))
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
    capacities = {loc.codigo: loc.capacidade for loc in catalogue}
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
        elif capacities.get(code) is not None and qty > capacities[code]:
            status = 'anomaly'
            anomalies += 1
        elif qty > 0:
            status = 'used'
            used += 1
        else:
            status = 'free'
            free += 1
        slots.append({'code': code, 'qty': qty, 'status': status,
                      'configured': exists})
    # Floor and missing locations are not silently treated as rack positions.
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
    user, sess = identity(request, db, require_admin=True)
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
    user, sess = identity(request, db, require_admin=True)
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
    user, sess = identity(request, db, require_admin=True)
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
    user, sess = identity(request, db, require_admin=True)
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
    return {'status': 'ok', 'application': 'DDN WMS', 'version':'2.3-prototipo'}

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

from . import context_routes  # noqa: E402,F401
