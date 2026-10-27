"""Browser form headers and hostile origins, without logging request secrets."""
import logging
import re
import secrets

import pytest
from fastapi.testclient import TestClient

from iberbag_demo.app import create_app
from iberbag_demo import simulation as sim


ORIGIN='http://127.0.0.1:54237'


@pytest.fixture
def local(tmp_path):
    app=create_app(tmp_path,54237,secrets.token_hex(32))
    with TestClient(app,base_url=ORIGIN,client=('127.0.0.1',1234)) as client:
        response=client.get('/dispatches/new')
        assert response.headers['referrer-policy']=='same-origin'
        csrf=re.search('name="csrf" value="([^"]+)"',response.text)[1]
        yield client,app.state.store,csrf


@pytest.mark.parametrize('headers',[
    {'Origin':ORIGIN,'Referer':ORIGIN+'/dispatches/new'},
    {'Origin':ORIGIN},
    {'Referer':ORIGIN+'/dispatches/new?channel=normal'},
    {},  # Technical clients may omit both. Every UI mutation still requires CSRF.
])
def test_local_form_headers_confirm_and_preserve_csrf(local,headers):
    client,store,csrf=local
    before=store.snapshot()
    fields=dict(key='a'*32,customer='CLIENTE DEMO TESTE',channel='normal',reference='ORIGEM FICTÍCIA',lote_0='LOTE-DEMO-A',quantidade_0='2')
    assert client.post('/dispatches/save',data=fields,headers=headers).status_code==403
    assert store.snapshot()==before
    response=client.post('/dispatches/save',data=dict(fields,csrf=csrf),headers=headers)
    assert response.status_code==200
    oid=store.snapshot()['orders'][-1]['id']
    confirmed=client.post(f'/dispatches/{oid}/confirm',data={'csrf':csrf},headers=headers)
    assert confirmed.status_code==200 and store.snapshot()['orders'][-1]['status']=='confirmed'


@pytest.mark.parametrize('headers',[
    {'Origin':'null'},
    {'Origin':'null','Referer':ORIGIN+'/dispatches/new'},
    {'Origin':'https://external.invalid','Referer':ORIGIN+'/dispatches/new'},
    {'Origin':ORIGIN,'Referer':'https://external.invalid/form'},
    {'Referer':'https://external.invalid/form'},
    {'Origin':'http://localhost:54237'},
    {'Origin':'http://127.0.0.1:54238'},
    {'Origin':'https://127.0.0.1:54237'},
    {'Origin':ORIGIN+'/'},
    {'Origin':ORIGIN+'.external.invalid'},
    {'Origin':ORIGIN+' https://external.invalid'},
    {'Origin':''},
    {'Referer':ORIGIN+'@external.invalid/form'},
    {'Referer':'http://user:password@127.0.0.1:54237/form'},
    {'Referer':ORIGIN+'\\@external.invalid/form'},
    {'Referer':'//127.0.0.1:54237/form'},
    {'Referer':'http://[invalid/form'},
    {'Referer':'null'},
    {'Host':'localhost:54237'},
    {'Host':'127.0.0.1:54238'},
    {'Host':'external.invalid','X-Forwarded-Host':'127.0.0.1:54237'},
    [('Origin',ORIGIN),('Origin','https://external.invalid')],
    [('Origin',ORIGIN),('Origin',ORIGIN)],
    [('Referer',ORIGIN+'/'),('Referer','https://external.invalid')],
    [('Host','127.0.0.1:54237'),('Host','external.invalid')],
])
def test_foreign_null_malformed_and_ambiguous_headers_never_change_state(local,headers):
    client,store,csrf=local;before=store.snapshot()
    response=client.post('/demo/reset',data=dict(csrf=csrf,confirm='REPOR'),headers=headers)
    assert response.status_code==403
    assert store.snapshot()==before
    assert 'access-control-allow-origin' not in response.headers


def test_origin_diagnostics_never_record_raw_headers_or_secrets(local,caplog):
    client,store,csrf=local
    caplog.set_level(logging.WARNING,logger='iberbag_demo.security')
    secret=secrets.token_hex(32)
    response=client.post('/demo/reset',data=dict(csrf=csrf,confirm='REPOR'),headers={
        'Origin':'null','Referer':ORIGIN+'/private?token='+secret,
        'Cookie':'iberbag_demo='+secret,'X-Demo-Control':secret,
    })
    assert response.status_code==403
    assert 'Host=local; Origin=null; Referer=local' in caplog.text
    assert secret not in caplog.text and csrf not in caplog.text and '/private' not in caplog.text
    caplog.clear()
    client.post('/demo/reset',data=dict(csrf=csrf,confirm='REPOR'),headers={
        'Host':secret+'.invalid','Origin':'https://'+secret+'.invalid',
        'Referer':'https://user:'+secret+'@external.invalid/?token='+secret,
    })
    assert 'Host=externo-ou-invalido; Origin=externo-ou-invalido; Referer=externo-ou-invalido' in caplog.text
    assert secret not in caplog.text


def test_all_process_confirmations_with_browser_headers(local):
    client,store,csrf=local
    client.headers.update({'Origin':ORIGIN,'Referer':ORIGIN+'/dispatches'})

    def post(path,**fields):
        response=client.post(path,data=dict(csrf=csrf,**fields))
        assert response.status_code==200,response.text
        return response

    def preparation(key,channel,lot,qty):
        post('/dispatches/save',key=key*32,customer='CLIENTE DEMO TESTE',channel=channel,reference='DEMO BROWSER',
             lote_0=lot,quantidade_0=qty,destination_0=store.snapshot()['destinations'][0] if channel=='mercadona' else '')
        oid=store.snapshot()['orders'][-1]['id']
        before=sum(r['quantidade'] for r in store.snapshot()['stock'])
        post(f'/dispatches/{oid}/confirm')
        assert store.snapshot()['orders'][-1]['status']=='confirmed'
        assert sum(r['quantidade'] for r in store.snapshot()['stock'])==before-int(qty)
        pdf=client.get(f'/documents/order-{oid}.pdf').content
        assert pdf.startswith(b'%PDF')
        state=store.snapshot();post(f'/dispatches/{oid}/confirm')
        assert store.snapshot()==state and client.get(f'/documents/order-{oid}.pdf').content==pdf

    preparation('a','normal','LOTE-DEMO-A','1')
    preparation('b','mercadona','LOTE-DEMO-C','1')
    fields=dict(key='c'*32,code='DEMO-ORIGIN-ENTRADA',day='2025-02-01',artigo_0='ART-DEMO-SACO',lote_0='LOTE-DEMO-B',quantidade_0='4')
    before=store.snapshot();post('/receipts/review',**fields);assert store.snapshot()==before
    post('/receipts/confirm',**fields);rid=store.snapshot()['receipts'][-1]['id']
    assert store.snapshot()['receipts'][-1]['lines'][0]['remaining']==1
    post('/putaway',key='d'*32,receipt_id=rid,line=0,local='A104',quantidade=1)
    sid=next(s['id'] for s in store.snapshot()['stock'] if s['local']=='A104' and s['quantidade'])
    post('/transfers',key='e'*32,stock_id=sid,local='A105',quantidade=1)
    assert sim.occupied(store.snapshot(),'A104')==0 and sim.occupied(store.snapshot(),'A105')==1
    post('/inventory/create',key='f'*32,kind='partial',locations='A105 A106')
    iid=store.snapshot()['inventories'][-1]['id']
    for location in ('A105','A106'):post(f'/inventory/{iid}/count',local=location,empty=1)
    post(f'/inventory/{iid}/submit');assert sim.occupied(store.snapshot(),'A105')==1
    post(f'/inventory/{iid}/approve',reason='Conferência fictícia de origem local')
    assert sim.occupied(store.snapshot(),'A105')==0
    for name in (f'receipt-{rid}',f'inventory-{iid}'):
        pdf=client.get(f'/documents/{name}.pdf').content
        assert pdf.startswith(b'%PDF') and client.get(f'/documents/{name}.pdf').content==pdf
