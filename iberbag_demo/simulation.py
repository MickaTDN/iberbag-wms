"""Visible warehouse results using invented data only. No operational service calls."""
import base64
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import re
from types import SimpleNamespace as R
from . import MARKER, BANNER
from .store import DemoError
from iberbag.commercial_pdf import document


def now():return datetime.now().replace(microsecond=0).isoformat(sep=' ')


def locations():
    return ['Chão']+[f'{c}{floor}{position:02d}' for c in 'ABCDEFGHIJKLMNOPQRS' for floor in range(1,(5 if c in 'LM' else 4)+1) for position in range(1,28)]


def number(value,zero=False):
    if not re.fullmatch(r'[0-9]{1,7}',str(value)) or not (0 if zero else 1)<=int(value)<=1000000:
        raise DemoError('Indicar paletes inteiras, entre '+('0' if zero else '1')+' e 1000000.')
    return int(value)


def label(value):
    value=str(value or '').strip()
    if not value or len(value)>120 or any(ord(c)<32 for c in value):raise DemoError('Preencher os campos obrigatórios (até 120 caracteres).')
    return value


def ident(state, collection):
    counters=state.setdefault('sequences',{})
    counters[collection]=max(counters.get(collection,0),max((r['id'] for r in state[collection]),default=0))+1
    return counters[collection]


def get(state, collection, key):
    row=next((r for r in state[collection] if r['id']==int(key)),None)
    if row is None:raise DemoError('Registo de demonstração não encontrado.')
    return row


def event(state,kind,detail,actor):
    state['events'].append(dict(id=ident(state,'events'),kind=kind,detail=detail,actor=actor,at=now()))


def occupied(state,local):return sum(r['quantidade'] for r in state['stock'] if r['local']==local)


def capacity(state,local,qty,extra=0):
    if local not in state['locations'] or local in state['inactive']:raise DemoError('Localização inexistente ou inativa.')
    if local!='Chão' and occupied(state,local)+qty+extra>1:raise DemoError('Cada posição normal comporta apenas uma palete. Chão não tem limite.')


def add_stock(state,artigo,lote,carga,local,qty,actor):
    row=next((s for s in state['stock'] if (s['artigo'],s['lote'],s['carga'],s['local'])==(artigo,lote,carga,local)),None)
    if row:row['quantidade']+=qty;row['arranger']=actor
    else:state['stock'].append(dict(id=ident(state,'stock'),artigo=artigo,lote=lote,carga=carga,local=local,quantidade=qty,arranger=actor))


def identity(state,lote):
    article=state['lots'].get(lote)
    if not article:raise DemoError('Lote inexistente nesta demonstração. Registar uma entrada fictícia primeiro.')
    return article


def archive(state,key,title,headers,rows,notes=()):
    content=document(BANNER+' · '+title,[BANNER,'Simulação local — sem validade operacional.']+list(notes),
        [('Registos simulados',headers,rows,'DEMONSTRAÇÃO — não utilizar no armazém.',False)],demo_watermark=True)
    state['documents'][key]=base64.b64encode(content).decode('ascii')
    return key


def fifo(state,lines):
    selected=[];used={}
    for i,line in enumerate(lines):
        identity(state,line['lote'])
        stocks=[s for s in state['stock'] if s['lote']==line['lote'] and s['quantidade']>0]
        if any(not state['loads'].get(s['carga']) for s in stocks):raise DemoError('FIFO bloqueado: falta data real de entrada. Não é uma falta comercial.')
        needed=line['quantidade']
        for s in sorted(stocks,key=lambda s:(state['loads'][s['carga']],s['carga'],s['id'])):
            free=s['quantidade']-used.get(s['id'],0)
            if not needed:break
            if free<=0:continue
            if s['local'] not in state['locations']:raise DemoError('FIFO bloqueado: localização desconhecida.')
            qty=min(needed,free);needed-=qty;used[s['id']]=used.get(s['id'],0)+qty
            selected.append(dict(line=i,stock_id=s['id'],artigo=s['artigo'],lote=s['lote'],carga=s['carga'],local=s['local'],quantidade=qty,destination=line.get('destination','')))
    return selected


def save_preparation(state,key,customer,channel,reference,rows,actor):
    previous=next((o for o in state['orders'] if o['key']==key),None)
    if previous:return previous['id']
    customer=label(customer);reference=label(reference)
    if channel not in ('normal','mercadona'):raise DemoError('Fluxo inválido.')
    values=[]
    for lote,qty,dest in rows:
        lote=label(lote);article=identity(state,lote)
        if channel=='mercadona' and dest not in state['destinations']:raise DemoError('Escolher um destino Mercadona fictício configurado.')
        values.append(dict(lote=lote,artigo=article,quantidade=number(qty),destination=dest if channel=='mercadona' else ''))
    if not 1<=len(values)<=20 or len({(r['lote'],r['destination']) for r in values})!=len(values):raise DemoError('Usar 1–20 linhas, sem repetir lote/destino.')
    plan=fifo(state,values);oid=ident(state,'orders')
    state['orders'].append(dict(id=oid,key=key,customer='Mercadona Fictícia' if channel=='mercadona' else customer,channel=channel,
        reference=reference,lines=values,picks=plan,status='draft',actor=actor,at=now(),review_version=state['stock_version']))
    return oid


def confirm_preparation(state,oid,actor):
    order=get(state,'orders',oid)
    if order['status']=='confirmed':return oid
    current=fifo(state,order['lines'])
    if current!=order['picks'] or order['review_version']!=state['stock_version']:raise DemoError('O stock mudou desde a revisão. Criar uma nova preparação simulada.')
    for pick in current:get(state,'stock',pick['stock_id'])['quantidade']-=pick['quantidade']
    for i,line in enumerate(order['lines']):
        short=line['quantidade']-sum(p['quantidade'] for p in current if p['line']==i)
        if short:state['pending'].append(dict(id=ident(state,'pending'),order_id=order['id'],customer=order['customer'],destination=line['destination'],
            reference=order['reference'],artigo=line['artigo'],lote=line['lote'],original=short,satisfied=0,at=now()))
    order.update(status='confirmed',actor=actor,at=now())
    plan=sorted(current,key=lambda p:(p['destination'],p['local'].upper(),p['stock_id'])) if order['channel']=='mercadona' else sorted(current,key=lambda p:(p['local'].upper(),p['stock_id']))
    rows=[(p['destination'] or order['customer'],p['artigo'],p['lote'],p['quantidade'],p['carga'],p['local']) for p in plan]
    for pending in state['pending']:
        if pending['order_id']==oid:rows.append((pending['destination'] or pending['customer'],pending['artigo'],pending['lote'],pending['original'],'PENDENTE','Não recolher'))
    archive(state,f'order-{oid}',f'{"Mercadona" if order["channel"]=="mercadona" else "CARGA / Preparação"} #{oid}',
        ('Cliente/destino','Artigo','Lote','Paletes','Carga','Localização'),rows,
        [order['reference'],f'Preparou: {actor} · {order["at"]}',f'Pedidas: {sum(l["quantidade"] for l in order["lines"])} · Preparadas: {sum(p["quantidade"] for p in current)}'])
    state['stock_version']+=1;event(state,'Saída',f'Preparação #{oid}: desconto simulado e faltas registadas.',actor)
    return oid


def receive(state,key,code,day,rows,actor):
    previous=next((r for r in state['receipts'] if r['key']==key),None)
    if previous:return previous['id']
    code=label(code)
    try:datetime.strptime(day,'%Y-%m-%d')
    except ValueError:raise DemoError('Indicar data de entrada válida.')
    if code in state['loads']:raise DemoError('Código de carga já utilizado na demonstração.')
    lines=[]
    for article,lote,qty in rows:
        article,lote=label(article),label(lote)
        if lote in state['lots'] and state['lots'][lote]!=article:raise DemoError('Cada lote pertence a um único artigo.')
        if any(r['lote']==lote for r in lines):raise DemoError('Agrupar cada lote numa única linha.')
        lines.append(dict(artigo=article,lote=lote,quantidade=number(qty),remaining=number(qty)))
    if not 1<=len(lines)<=20:raise DemoError('Indicar 1–20 linhas de entrada.')
    rid=ident(state,'receipts');receipt=dict(id=rid,key=key,code=code,day=day,lines=lines,actor=actor,at=now())
    state['loads'][code]=day;state['receipts'].append(receipt);doc=[]
    for index,line in enumerate(lines):
        state['lots'][line['lote']]=line['artigo']
        for p in sorted(state['pending'],key=lambda p:(p['at'],p['id'])):
            if p['lote']!=line['lote'] or p['satisfied']==p['original']:continue
            qty=min(line['remaining'],p['original']-p['satisfied'])
            if qty:
                p['satisfied']+=qty;line['remaining']-=qty
                state['allocations'].append(dict(id=ident(state,'allocations'),pending_id=p['id'],receipt_id=rid,line=index,quantidade=qty,customer=p['customer'],destination=p['destination'],lote=line['lote'],collected=False))
                doc.append((line['artigo'],line['lote'],qty,p['customer']+' / '+p['destination'],'Separar — já carregadas contabilisticamente'))
        if line['remaining']:doc.append((line['artigo'],line['lote'],line['remaining'],'Stock normal','Registar arrumação simulada'))
    archive(state,f'receipt-{rid}',f'Entrada / Arrumação · {code}',('Artigo','Lote','Paletes','Destino','Instrução'),doc,[f'Data: {day} · Recebeu: {actor}'])
    state['stock_version']+=1;event(state,'Entrada',f'{code}: atribuição simulada aos pendentes; excedente por arrumar.',actor)
    return rid


def putaway(state,rid,index,local,qty,key,actor):
    if key in state['commands']:return
    receipt=get(state,'receipts',rid)
    if not 0<=index<len(receipt['lines']):raise DemoError('Linha inválida.')
    line=receipt['lines'][index];qty=number(qty)
    if qty>line['remaining']:raise DemoError('Quantidade superior ao excedente por arrumar. Paletes de clientes não são stock livre.')
    capacity(state,local,qty);line['remaining']-=qty
    add_stock(state,line['artigo'],line['lote'],receipt['code'],local,qty,actor)
    state['commands'].append(key);state['stock_version']+=1
    event(state,'Arrumação',f'{qty} palete(s) de {receipt["code"]} em {local}.',actor)


def move(state,stock_id,local,qty,key,actor):
    if key in state['commands']:return
    stock=get(state,'stock',stock_id);qty=number(qty)
    if stock['local']==local or qty>stock['quantidade']:raise DemoError('Origem/destino ou quantidade inválidos.')
    capacity(state,local,qty);stock['quantidade']-=qty
    add_stock(state,stock['artigo'],stock['lote'],stock['carga'],local,qty,actor)
    state['commands'].append(key);state['stock_version']+=1
    event(state,'Transferência',f'{qty} palete(s) {stock["local"]} → {local}, lote {stock["lote"]}, carga {stock["carga"]}.',actor)


def create_inventory(state,kind,selected,key,actor):
    old=next((i for i in state['inventories'] if i['key']==key),None)
    if old:return old['id']
    scope=list(state['locations']) if kind=='full' else list(dict.fromkeys(selected))
    if kind not in ('full','partial') or not scope or any(l not in state['locations'] for l in scope):raise DemoError('Escolher âmbito e localizações existentes.')
    iid=ident(state,'inventories')
    state['inventories'].append(dict(id=iid,key=key,kind=kind,scope=scope,counts={},baseline=deepcopy(state['stock']),
        version=state['stock_version'],status='draft',actor=actor,at=now(),reason=''))
    return iid


def save_count(state,iid,local,rows,empty,actor):
    inv=get(state,'inventories',iid)
    if inv['status']!='draft' or local not in inv['scope']:raise DemoError('Contagem não editável ou fora do âmbito.')
    if inv['version']!=state['stock_version']:raise DemoError('Houve movimentos desde o início. Criar novo inventário simulado.')
    if not rows and not empty:raise DemoError('Não contado é desconhecido. Assinalar explicitamente vazio para contar zero.')
    if rows and empty:raise DemoError('Não combinar posição vazia com paletes contadas.')
    groups=[]
    for article,lote,carga,qty in rows:
        qty=number(qty)
        if identity(state,lote)!=article or carga not in state['loads']:raise DemoError('Identidade não comprovada: investigação ilustrativa; não inventar artigo/lote/carga.')
        if any((r['artigo'],r['lote'],r['carga'])==(article,lote,carga) for r in groups):raise DemoError('Agrupar artigo/lote/carga numa única linha.')
        groups.append(dict(artigo=article,lote=lote,carga=carga,local=local,quantidade=qty))
    inv['counts'][local]=groups


def differences(inv):
    rows=[]
    for local in inv['scope']:
        before={(r['artigo'],r['lote'],r['carga']):r['quantidade'] for r in inv['baseline'] if r['local']==local and r['quantidade']}
        after={(r['artigo'],r['lote'],r['carga']):r['quantidade'] for r in inv['counts'].get(local,[])}
        for key in sorted(set(before)|set(after)):
            observed=after.get(key,0) if local in inv['counts'] else None
            rows.append(dict(artigo=key[0],lote=key[1],carga=key[2],local=local,before=before.get(key,0),after=observed,
                delta=None if observed is None else observed-before.get(key,0)))
    return rows


def submit_inventory(state,iid,actor):
    inv=get(state,'inventories',iid)
    if inv['status']!='draft':return
    if len(inv['counts'])!=len(inv['scope']):raise DemoError('Existem localizações ainda não contadas. Nunca são interpretadas como vazias.')
    if inv['version']!=state['stock_version']:raise DemoError('Contagem desatualizada. Criar novo inventário simulado.')
    archive(state,f'inventory-{iid}',f'Inventário #{iid}',('Artigo','Lote','Carga','Localização','Sistema','Físico'),
        [(r['artigo'],r['lote'],r['carga'],r['local'],r['before'],r['after']) for r in differences(inv)],['Contagem simulada; não substitui aprovação administrativa.'])
    inv['status']='submitted';event(state,'Inventário',f'Contagem #{iid} submetida sem alterar stock.',actor)


def approve(state,iid,reason,actor):
    inv=get(state,'inventories',iid)
    if inv['status']=='applied':return
    if inv['status']!='submitted':raise DemoError('Submeter todas as contagens antes do ajuste simulado.')
    reason=label(reason)
    if inv['version']!=state['stock_version']:raise DemoError('Stock alterado durante o inventário. Recontar.')
    for local,rows in inv['counts'].items():
        if local!='Chão' and sum(r['quantidade'] for r in rows)>1:raise DemoError('Contagem excede capacidade de uma palete.')
    for d in differences(inv):
        if d['delta'] and d['delta']>0:
            # Conservative: no simulation shortcut that recreates goods already allocated/prepared.
            if any(a['lote']==d['lote'] and not a['collected'] for a in state['allocations']) or any(o['status']=='confirmed' and any(p['lote']==d['lote'] for p in o['picks']) for o in state['orders']):
                raise DemoError('Paletes já destinadas a clientes: ajuste positivo requer reconciliação, aqui apenas ilustrativa.')
            if not state['loads'].get(d['carga']):raise DemoError('Ajuste positivo exige carga real da simulação com data conhecida.')
        if d['delta']:state['adjustments'].append(dict(**d,inventory_id=iid,reason=reason,actor=actor,at=now()))
    state['stock']=[s for s in state['stock'] if s['local'] not in inv['scope']]
    for rows in inv['counts'].values():
        for r in rows:add_stock(state,r['artigo'],r['lote'],r['carga'],r['local'],r['quantidade'],actor)
    inv.update(status='applied',reason=reason);state['stock_version']+=1;event(state,'Ajuste',f'Inventário #{iid}: {reason}.',actor)


def seed():
    state=dict(marker=MARKER,version=1,stock_version=0,locations=locations(),inactive=[],lots={
        'LOTE-DEMO-A':'ART-DEMO-SACO','LOTE-DEMO-B':'ART-DEMO-SACO','LOTE-DEMO-C':'ART-DEMO-FILME','LOTE-DEMO-D':'ART-DEMO-CAIXA'},
        loads={'DEMO-ANTIGA':'2020-01-02','DEMO-NOVA':'2021-03-04','DEMO-FILME':'2022-04-01','DEMO-CAIXA':'2023-01-03'},
        stock=[],orders=[],receipts=[],pending=[],allocations=[],inventories=[],adjustments=[],events=[],documents={},commands=[],destinations=['DESTINO DEMO NORTE','DESTINO DEMO SUL'])
    actor='Escritório Fictício'
    for lot,load,local,qty in [('LOTE-DEMO-A','DEMO-ANTIGA','A101',1),('LOTE-DEMO-A','DEMO-ANTIGA','Chão',3),
        ('LOTE-DEMO-A','DEMO-NOVA','B202',1),('LOTE-DEMO-A','DEMO-NOVA','Chão',4),('LOTE-DEMO-B','DEMO-NOVA','C214',1),
        ('LOTE-DEMO-B','DEMO-NOVA','Chão',3),('LOTE-DEMO-C','DEMO-FILME','L501',1),('LOTE-DEMO-C','DEMO-FILME','Chão',5),
        ('LOTE-DEMO-D','DEMO-CAIXA','S427',1),('LOTE-DEMO-D','DEMO-CAIXA','Chão',2)]:add_stock(state,state['lots'][lot],lot,load,local,qty,actor)
    oid=save_preparation(state,'seed-order','CLIENTE DEMO ALFA','normal','ENCOMENDA DEMO INCOMPLETA',[('LOTE-DEMO-B','7','')],actor)
    confirm_preparation(state,oid,actor)
    receive(state,'seed-receipt','DEMO-RECEBIDA','2024-01-02',[('ART-DEMO-SACO','LOTE-DEMO-B','2'),('ART-DEMO-CAIXA','LOTE-DEMO-D','3')],actor)
    move(state,1,'A102',1,'seed-move',actor)
    mid=save_preparation(state,'seed-merc','Mercadona Fictícia','mercadona','MERCADONA DEMO',
        [('LOTE-DEMO-C','2','DESTINO DEMO NORTE'),('LOTE-DEMO-D','1','DESTINO DEMO SUL')],actor)
    confirm_preparation(state,mid,actor)
    oid=save_preparation(state,'seed-order-beta','CLIENTE DEMO BETA','normal','PEDIDO DEMO BETA',[('LOTE-DEMO-B','2','')],actor)
    confirm_preparation(state,oid,actor)
    iid=create_inventory(state,'partial',['B202'],'seed-adjusted',actor)
    save_count(state,iid,'B202',[],True,actor);submit_inventory(state,iid,actor)
    approve(state,iid,'Diferença fictícia confirmada em ensaio',actor)
    iid=create_inventory(state,'partial',['A102','A103'],'seed-inventory-current',actor)
    save_count(state,iid,'A102',[],True,actor)
    event(state,'Demonstração','Dados inteiramente inventados; repor livremente no menu DEMO.',actor)
    return state
