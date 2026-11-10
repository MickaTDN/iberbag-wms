"""Valmet physical receipts; no picking, transfers or inventory adjustments."""
from functools import wraps
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
import hashlib
from sqlalchemy import select, text, func, or_, and_
from sqlalchemy.exc import DBAPIError
from .database import (ValmetOwner, ValmetEntry, ValmetUnit, ValmetNumber,
    ValmetIdentification, ValmetItem, Receipt, Carga, Stock, Location, User, Audit, now_utc,
    require_write_isolation)
from .contexts import require_context
from .receiving import ReceivingError, required, quantity, request_key

KINDS = {'lab':'Bobines LAB', 'paletes':'Paletes', 'fio':'Paletes Fio', 'prateleiras':'Prateleiras',
         'rolos':'ROLOS', 'caixas':'Caixas', 'vazias':'Caixas Vazias',
         'pecas':'Caixas com Peças', 'vasilhame':'Vasilhame'}
GROUPED = {'paletes','fio','prateleiras','vasilhame'}
BRANDS = ('Filva','Wetekam','Filkemp','Nextrusion','Monosuisse','Trofil','Hahl','Durafiver')

def enabled(db):
    ctx, owner, wh = require_context(db)
    return db.get(ValmetOwner, owner.id) is not None

def command(fn):
    @wraps(fn)
    def run(db, actor_id, *args, **kwargs):
        try:
            if db.bind.dialect.name != 'postgresql':
                raise ReceivingError('Operações Valmet exigem PostgreSQL.',503)
            require_write_isolation(db)
            db.execute(text('SELECT pg_advisory_xact_lock(72834001)'))
            db.expire_all()
            actor = db.get(User,actor_id)
            if not actor or not actor.active or actor.role not in ('admin','operador'):
                raise ReceivingError('Operação não autorizada.',403)
            if not enabled(db):raise ReceivingError('Processo Valmet indisponível neste contexto.',403)
            result=fn(db,actor,*args,**kwargs)
            db.commit();return result
        except DBAPIError as error:
            db.rollback();raise ReceivingError('Conflito: verificar N.º Armazém, localização ou revisão. Nada foi duplicado.') from error
        except Exception:
            db.rollback();raise
    return run

def optional(value,limit=120):
    value=(value or '').strip()
    if len(value)>limit or any(ord(c)<32 and c not in '\n\r\t' for c in value):
        raise ReceivingError('Texto inválido ou demasiado longo.',400)
    return value

def composition(kind, physical, total, items):
    if len(items)>100:raise ReceivingError('Demasiadas linhas de composição.',400)
    result=[(required(a,'Artigo') if (a or '').strip() else None,required(l,'Lote'),quantity(q) if q not in (None,'') else None) for a,l,q in items]
    if kind!='vasilhame' and not result:
        if kind=='vazias':raise ReceivingError('Caixas Vazias: indicar lote; artigo pode ser desconhecido.',400)
        raise ReceivingError('Indicar lote e composição; usar Aguarda identificação Valmet se a identificação operacional for insuficiente.',400)
    if kind=='paletes' and physical==1:
        # Several lots may be present on ONE physical pallet. These are
        # associations, not disjoint extra pallets. Never add their counts.
        if any(q not in (None,1) for a,l,q in result):
            raise ReceivingError('Uma palete física: indicar presença (vazio ou 1) por lote, sem multiplicar paletes.',400)
    elif kind in ('paletes','fio','prateleiras','rolos','pecas'):
        expected=physical if kind in ('paletes','fio','prateleiras') else total
        if expected is None or any(q is None for a,l,q in result) or sum(q for a,l,q in result)!=expected:
            raise ReceivingError('As quantidades identificadas não correspondem ao total físico/material registado.',400)
    elif total is not None and (any(q is None for a,l,q in result) or sum(q for a,l,q in result)!=total):
        raise ReceivingError('A composição diverge do total de material.',400)
    return result

def payload(received_on,code,rows):
    try: day=date.fromisoformat(received_on)
    except (ValueError,TypeError):raise ReceivingError('Data efetiva de entrada inválida.',400)
    code=required(code,'Referência histórica') if day.year<2027 else 'Automática na confirmação'
    return json.dumps(dict(received_on=day.isoformat(),code=code,rows=clean_rows(rows)),ensure_ascii=False,sort_keys=True)

def clean_rows(rows, *, historical_numbers=False):
    if not 1<=len(rows)<=60:raise ReceivingError('Indicar 1–60 grupos/unidades.',400)
    cleaned=[]
    for row in rows:
        kind=row.get('kind')
        if kind not in KINDS:raise ReceivingError('Tipo de mercadoria inválido.',400)
        physical=quantity(row.get('physical_quantity',''))
        number=optional(row.get('number')).upper() or None
        if kind not in GROUPED and (physical!=1 or not number):
            raise ReceivingError('Cada unidade indivisível exige uma palete/caixa e N.º Armazém manual.',400)
        if kind in GROUPED and number and not historical_numbers:raise ReceivingError('N.º Armazém destina-se às unidades individualizadas.',400)
        total=quantity(row['content_total']) if row.get('content_total') not in (None,'') else None
        m2=None
        if row.get('material_m2') not in (None,''):
            try:
                m2=Decimal(str(row['material_m2']).replace(',','.'))
                if kind!='paletes' or not m2.is_finite() or m2<0 or m2>Decimal('999999999999999.999') or m2.as_tuple().exponent < -3:raise InvalidOperation
            except (InvalidOperation,ValueError):raise ReceivingError('m²: valor decimal não negativo, até três casas, apenas em Paletes.',400)
        pending=row.get('pending',False)
        if not isinstance(pending,bool):raise ReceivingError('Estado de identificação inválido.',400)
        items=row.get('items',[])
        roll_rows=None
        if row.get('rolls') is not None:
            if kind!='rolos':raise ReceivingError('Identidades de rolo apenas em ROLOS.')
            from .valmet_rolls import clean_rows,material
            roll_rows=clean_rows(row['rolls'])
            expected=material(roll_rows)
            if items:
                actual={}
                for a,l,q in items:actual[l]=actual.get(l,0)+quantity(q)
                if actual!=expected:raise ReceivingError('Composição agregada diverge dos rolos individuais.')
            else:
                counts={}
                for r in roll_rows:
                    pair=(r['article'],r['lot']);counts[pair]=counts.get(pair,0)+1
                items=[(a,l,q) for (a,l),q in counts.items()]
            if total is not None and total!=len(roll_rows):raise ReceivingError('Total de rolos diverge das identidades.')
            total=len(roll_rows);pending=False
        if kind=='vazias':
            # Lot + manual warehouse number identify an empty box, even if its
            # article is unknown. This alone must not make it unavailable.
            pending=False
        elif pending:
            # Missing article alone is not an operational identity failure.
            # Keep pending only if the lot/composition/totals are insufficient.
            try:composition(kind,physical,total,items)
            except ReceivingError:pass
            else:pending=False
        if pending:
            items=[(optional(a) or None,optional(l) or None,quantity(q) if q not in ('',None) else None) for a,l,q in items]
            bound=physical if kind in ('paletes','fio','prateleiras') else total
            too_many=(any(q not in (None,1) for a,l,q in items) if kind=='paletes' and physical==1
                      else bound is not None and sum(q or 0 for a,l,q in items)>bound)
            if len(items)>100 or too_many:
                raise ReceivingError('Identificação parcial excede o total físico/material.',400)
        else:items=composition(kind,physical,total,items)
        brand=optional(row.get('brand')) or None
        if kind=='vasilhame' and brand not in BRANDS:raise ReceivingError('Selecionar referência de vasilhame válida.',400)
        cleaned.append(dict(kind=kind,physical_quantity=physical,number=number,
            local='Chão' if kind=='vazias' else (optional(row.get('local')).upper() or None),content_total=total,
            material_m2=str(m2) if m2 is not None else None, supplier=optional(row.get('supplier')),
            guide=optional(row.get('guide')),notes=optional(row.get('notes'),2000),brand=brand,pending=pending,items=items,rolls=roll_rows))
    return cleaned

@command
def save_entry(db,actor,key,received_on,code,rows,entry_id=None,version=None):
    key=request_key(key);data=payload(received_on,code,rows)
    entry=db.get(ValmetEntry,entry_id) if entry_id is not None else db.scalar(select(ValmetEntry).where(ValmetEntry.request_key==key))
    if entry:
        if entry.created_by!=actor.id:raise ReceivingError('Só o autor pode rever/confirmar este rascunho.',403)
        if entry_id is None:
            if entry.payload!=data:raise ReceivingError('Pedido repetido com dados diferentes.')
            return entry.id
        if entry.status!='draft' or entry.version!=version or entry.request_key!=key:raise ReceivingError('Revisão desatualizada.')
        entry.payload=data;entry.version+=1
    else:
        if entry_id is not None:raise ReceivingError('Entrada inexistente.',404)
        entry=ValmetEntry(request_key=key,payload=data,created_by=actor.id);db.add(entry)
    db.flush()
    db.add(Audit(username=actor.username,action='VALMET_RASCUNHO',details=f'Entrada {entry.id}; revisão {entry.version}; {data}'))
    return entry.id

def add_identification(db,actor,unit,key,items,total,notes):
    items=composition(unit.kind,unit.physical_quantity,total,items)
    if unit.content_total is not None and total!=unit.content_total:raise ReceivingError('O total de material original diverge; requer reconciliação, sem ajuste automático.')
    identification=ValmetIdentification(unit_id=unit.id,request_key=key,actor=actor.username,content_total=total,notes=notes)
    db.add(identification);db.flush()
    for a,l,q in items:db.add(ValmetItem(unit_id=unit.id,identification_id=identification.id,article=a,lot=l,quantity=q))
    db.flush()

@command
def confirm_entry(db,actor,ident,version):
    entry=db.get(ValmetEntry,ident)
    if not entry:raise ReceivingError('Entrada inexistente.',404)
    if entry.created_by!=actor.id:raise ReceivingError('Só o autor confirma.',403)
    if entry.version!=version:raise ReceivingError('Revisão desatualizada.')
    if entry.status=='confirmed':return entry.id
    original=json.loads(entry.payload)
    # Drafts saved by a previous application version must pass today's rules;
    # confirmed history is returned above, never rewritten.
    data=json.loads(payload(original['received_on'],original['code'],original['rows']))
    receipt=Receipt(request_key=entry.request_key,payload_hash=hashlib.sha256(entry.payload.encode()).hexdigest(),
        code=data['code'],received_on=datetime.combine(date.fromisoformat(data['received_on']),datetime.min.time()),created_by=actor.id)
    db.add(receipt);db.flush()
    from .load_numbering import allocate
    receipt.code=allocate(db,receipt)
    if db.scalar(select(Carga.id).where(Carga.codigo==receipt.code)) or db.scalar(select(Stock.id).where(Stock.carga==receipt.code)):
        raise ReceivingError('Carga histórica já existente; não anexar movimentos.')
    carga=Carga(codigo=receipt.code,entrada=receipt.received_on);db.add(carga);db.flush()
    for index,row in enumerate(data['rows']):
        values={k:v for k,v in row.items() if k not in ('items','pending','rolls')}
        if values['local']:
            loc=db.scalar(select(Location).where(func.lower(Location.codigo)==values['local'].lower()))
            if not loc or not loc.active:raise ReceivingError('Localização inexistente ou inativa; configurar em Definições.')
            values['local']=loc.codigo
        unit=ValmetUnit(entry_id=entry.id,**values);db.add(unit);db.flush()
        if not row['pending']:add_identification(db,actor,unit,f'{entry.request_key}:{index}',row['items'],row['content_total'],'Identificada na receção')
        else:
            for a,l,q in row['items']:db.add(ValmetItem(unit_id=unit.id,article=a,lot=l,quantity=q))
        if row.get('rolls'):
            from .valmet_rolls import register
            register(db,actor,unit.id,hashlib.sha256(f'{entry.request_key}:rolls:{index}'.encode()).hexdigest()[:32],row['rolls'],'Receção individual','Rolos e classificações registados explicitamente na receção')
    receipt.carga_id=carga.id;receipt.receiver=actor.username;receipt.status='confirmed';receipt.confirmed_at=now_utc()
    entry.receipt_id=receipt.id;entry.status='confirmed'
    db.add(Audit(username=actor.username,action='VALMET_CONFIRMAR',details=f'Entrada {entry.id}; carga {receipt.code}; receção {receipt.id}.'))
    return entry.id

@command
def identify(db,actor,unit_id,key,items,total=None,notes=''):
    key=request_key(key);notes=required(notes,'Comprovação / observação')
    unit=db.get(ValmetUnit,unit_id)
    if not unit:raise ReceivingError('Unidade inexistente.',404)
    total=quantity(total) if total not in ('',None) else None
    existing=db.scalar(select(ValmetIdentification).where(ValmetIdentification.unit_id==unit.id))
    cleaned=composition(unit.kind,unit.physical_quantity,total,items)
    if existing:
        previous=[(i.article,i.lot,i.quantity) for i in db.scalars(select(ValmetItem).where(ValmetItem.identification_id==existing.id).order_by(ValmetItem.id))]
        if existing.request_key==key and previous==cleaned and existing.content_total==total and existing.notes==notes:return unit.id
        raise ReceivingError('Identificação já concluída; não substituir histórico.')
    add_identification(db,actor,unit,key,cleaned,total,notes)
    db.add(Audit(username=actor.username,action='VALMET_IDENTIFICAR',details=f'Unidade {unit.id}; entrada original {unit.entry_id}; {json.dumps(cleaned,ensure_ascii=False)}; {notes}'))
    return unit.id

def stock(db,filters=None,page=1):
    from .database import ValmetPosition
    from . import valmet_balances as balances
    filters=filters or {};query=select(ValmetUnit,ValmetPosition).join(ValmetPosition,ValmetPosition.unit_id==ValmetUnit.id).where(ValmetPosition.physical_quantity>0)
    if filters.get('entry_id') is not None:query=query.where(ValmetUnit.entry_id==filters['entry_id'])
    for field,column in [('number',ValmetUnit.number),('kind',ValmetUnit.kind),('local',ValmetPosition.local),('guide',ValmetUnit.guide)]:
        term=filters.get(field,'').strip()
        if term:query=query.where(func.coalesce(column,'Por localizar').icontains(term,autoescape=True))
    note=filters.get('notes','').strip()
    if note:query=query.where(or_(ValmetUnit.notes.icontains(note,autoescape=True),
        select(ValmetIdentification.id).where(ValmetIdentification.unit_id==ValmetUnit.id,
            ValmetIdentification.notes.icontains(note,autoescape=True)).exists()))
    composition_filters=[]
    for field,column in [('article',ValmetItem.article),('lot',ValmetItem.lot)]:
        term=filters.get(field,'').strip()
        if term:composition_filters.append(column.icontains(term,autoescape=True))
    if composition_filters:
        if db.bind.dialect.name=='postgresql':
            from sqlalchemy import cast
            from sqlalchemy.dialects.postgresql import JSONB
            composition_filters.append(or_(ValmetUnit.kind=='vasilhame',ValmetItem.identification_id.is_(None),cast(ValmetPosition.materials,JSONB).op('?')(ValmetItem.lot)))
        legacy=select(ValmetItem.id).where(ValmetItem.unit_id==ValmetUnit.id,
            *composition_filters,or_(ValmetItem.identification_id.is_not(None),
            ~select(ValmetIdentification.id).where(ValmetIdentification.unit_id==ValmetUnit.id).correlate(ValmetUnit).exists())).exists()
        from sqlalchemy import inspect
        if inspect(db.bind).has_table('valmet_rolls'):
            from .database import ValmetRoll,ValmetRollState
            terms=[]
            for field,col in [('article',ValmetRoll.article),('lot',ValmetRoll.lot)]:
                term=filters.get(field,'').strip()
                if term:terms.append(col.icontains(term,autoescape=True))
            registered=select(ValmetRoll.id).where(ValmetRoll.unit_id==ValmetUnit.id).exists()
            current=select(ValmetRoll.id).join(ValmetRollState,ValmetRollState.id==ValmetRoll.id).where(ValmetRoll.unit_id==ValmetUnit.id,ValmetRollState.present.is_(True),*terms).exists()
            query=query.where(or_(and_(ValmetUnit.kind=='rolos',registered,current),and_(or_(ValmetUnit.kind!='rolos',~registered),legacy)))
        else:query=query.where(legacy)
    count=db.scalar(select(func.count()).select_from(query.subquery()))
    units=db.execute(query.order_by(ValmetUnit.id,ValmetPosition.id).offset((page-1)*100).limit(100)).all()
    from .valmet_planning import reserved_physical,source
    eligible_units={unit['id'] for unit in source(db,unit_ids=[u.id for u,z in units])}
    result=[]
    for unit,pos in units:
        identification=db.scalar(select(ValmetIdentification).where(ValmetIdentification.unit_id==unit.id))
        items=db.scalars(select(ValmetItem).where(ValmetItem.unit_id==unit.id,
            ValmetItem.identification_id==identification.id if identification else ValmetItem.identification_id.is_(None)).order_by(ValmetItem.id)).all()
        current=balances.view(pos,unit);reserved=reserved_physical(db,current)
        result.append((current,identification,items,max(0,pos.physical_quantity-reserved) if current.id in eligible_units else 0))
    return result,count
