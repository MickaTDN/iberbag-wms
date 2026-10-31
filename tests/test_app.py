from conftest import login_data
"""Version 2.3 functional tests with an isolated, temporary SQLite database."""
import os
from datetime import datetime
from pathlib import Path
import tempfile
import secrets

PASSWORD = secrets.token_urlsafe(24)

from fastapi.testclient import TestClient
from sqlalchemy import select, func, delete
from iberbag.app import app
from iberbag.database import SessionLocal, init_db, User, Stock, Carga, Location, Audit, Corridor, AppConfig
from iberbag.security import set_password
from iberbag.location_rules import capacity_for, is_floor


def create_user(db, name, role):
    u = User(username=name, display_name=name, role=role, active=True)
    set_password(u, PASSWORD)
    db.add(u)
    db.commit()
    return u


def setup_module():
    init_db()
    with SessionLocal() as db:
        create_user(db,'admin','admin')
        create_user(db,'operador','operador')
        db.add_all([
            Stock(artigo='ART001',lote='LOT001',carga='CARGA-20',local='A101',quantidade=1),
            Stock(artigo='ART001',lote='LOT002',carga='CARGA-21',local='A101',quantidade=1),
            Stock(artigo='ART002',lote='LOT003',carga='CARGA-22',local='B201',quantidade=1),
            Stock(artigo='ART003',lote='LOT004',carga='CARGA-23',local='Chão',quantidade=7),
            Carga(codigo='CARGA-20', entrada=datetime(2026,10,5), funcionario='Ana'),
            Carga(codigo='CARGA-21'), Carga(codigo='CARGA-22'), Carga(codigo='CARGA-23'),
        ])
        db.commit()


def login(client, name):
    response=client.post('/login',data=login_data(client, {'username':name,'password':PASSWORD}),follow_redirects=False)
    assert response.status_code == 303


def csrf(client):
    with SessionLocal() as db:
        from iberbag.security import get_session
        return get_session(db,client.cookies.get('wms_session')).csrf_token


def test_search_fields_and_row_details():
    with TestClient(app) as client:
        assert client.get('/stock',follow_redirects=False).status_code==303
        login(client,'operador')
        res=client.get('/stock?artigo=ART001&local=A101')
        assert res.status_code==200
        assert 'LOT001' in res.text and 'LOT002' in res.text
        assert 'LOT003' not in res.text
        assert 'name="carga"' not in res.text
        assert '<th>Carga</th>' not in res.text
        assert 'Data de entrada</th>' not in res.text
        assert 'CARGA-20' not in res.text
        assert '2 paletes' in res.text
        assert 'Ver detalhes' in res.text
        with SessionLocal() as db:
            stock_id=db.scalar(select(Stock.id).where(Stock.carga=='CARGA-20'))
        detail=client.get(f'/stock/{stock_id}')
        assert detail.status_code==200
        assert 'CARGA-20' in detail.text
        assert '05/10/2026' in detail.text
        assert 'Ana' in detail.text
        assert 'Quem arrumou' in detail.text
        assert client.get('/stock/999999').status_code==404
        assert client.get('/users').status_code==403


def test_vba_layout_and_admin_settings():
    assert capacity_for('A101')==1
    assert capacity_for('CHÃO') is None
    assert capacity_for('Chao') is None
    assert capacity_for('(não indicado)') is None
    with SessionLocal() as db:
        assert db.scalar(select(Corridor.andares).where(Corridor.codigo=='L'))==5
        assert db.scalar(select(Corridor.andares).where(Corridor.codigo=='N'))==4
        assert db.scalar(select(Location.id).where(Location.codigo=='C214')) is not None
        assert db.scalar(select(Location.id).where(Location.codigo=='M527')) is not None
        assert db.scalar(select(Location.id).where(Location.codigo=='A128')) is None
        assert db.scalar(select(func.count()).select_from(Corridor)) == 19
        assert db.scalar(select(func.count()).select_from(Location)) == 2107
    with TestClient(app) as client:
        login(client, 'admin')
        page=client.get('/settings')
        assert 'Localizações' in page.text and '/settings/locations' in page.text
        assert 'Utilizadores' in page.text
        assert 'Registo de atividade' in page.text
        settings_loc=client.get('/settings/locations')
        assert 'Corredores configurados' in settings_loc.text
        assert 'Corredor L' in settings_loc.text
        assert '27 posições' in settings_loc.text
        assert '/locations' not in client.get('/').text
        assert client.get('/locations',follow_redirects=False).headers['location']=='/settings/locations'
        cl=client.get('/settings/corridors/C')
        assert 'C214' in cl.text
        assert 'C228' not in cl.text
        assert client.post('/settings/locations/A101/delete',data={'csrf':csrf(client)}).status_code==409
        assert client.post('/settings/corridors/A/delete',data={'csrf':csrf(client)}).status_code==409
        assert client.post('/settings/locations/A105/delete',data={'csrf':csrf(client)},follow_redirects=False).status_code==303
        with SessionLocal() as db:
            assert db.scalar(select(Location.id).where(Location.codigo=='A105')) is None
        # A deleted position must never reappear merely because the server restarts.
        init_db()
        with SessionLocal() as db:
            assert db.scalar(select(Location.id).where(Location.codigo=='A105')) is None
        assert client.post('/settings/locations',data={'csrf':csrf(client),'codigo':'A105'},follow_redirects=False).status_code==303
        with SessionLocal() as db:
            assert db.scalar(select(Location.id).where(Location.codigo=='A105')) is not None


def test_configured_capacity_and_stock_are_preserved():
    with SessionLocal() as db:
        a=db.scalar(select(Location).where(Location.codigo=='A101'))
        a.capacidade=19
        db.commit()
    from iberbag.database import sync_location_policy
    with SessionLocal() as db:
        sync_location_policy(db)
    with SessionLocal() as db:
        assert db.scalar(select(Location.capacidade).where(Location.codigo=='A101'))==19
        assert db.scalar(select(Stock.quantidade).where(Stock.carga=='CARGA-20'))==1
        assert db.scalar(select(Stock.quantidade).where(Stock.carga=='CARGA-21'))==1
        assert db.scalar(select(Audit.id).where(Audit.action=='REGRA_CAPACIDADE')) is None



def test_export_capacities_readonly(tmp_path):
    from iberbag.capacities import export_csv
    outfile=tmp_path/'capacity.csv'
    with SessionLocal() as db:
        assert export_csv(db,outfile)==2107
    body=outfile.read_text(encoding='utf-8-sig')
    assert 'Chão;7;' in body
    assert 'A101;2;1' in body


def test_configure_corridors_and_location_guardrails():
    with TestClient(app) as client:
        login(client,'admin')
        tok=csrf(client)
        new=client.post('/settings/corridors',data={'csrf':tok,'codigo':'T','andares':'2','posicoes':'7'},follow_redirects=False)
        assert new.status_code==303
        assert new.headers['location']=='/settings/corridors/T'
        with SessionLocal() as db:
            assert db.scalar(select(Location.id).where(Location.codigo=='T207')) is not None
            assert db.scalar(select(Location.id).where(Location.codigo=='T208')) is None
        assert client.post('/settings/locations',data={'csrf':tok,'codigo':'T208'}).status_code==409
        assert client.post('/settings/corridors/T/update',data={'csrf':tok,'andares':'3','posicoes':'9'},follow_redirects=False).status_code==303
        assert client.post('/settings/locations/T309/delete',data={'csrf':tok},follow_redirects=False).status_code==303
        assert client.post('/settings/locations/T308/rename',data={'csrf':tok,'novo_codigo':'T309'},follow_redirects=False).status_code==303
        with SessionLocal() as db:
            assert db.scalar(select(Location.id).where(Location.codigo=='T308')) is None
            assert db.scalar(select(Location.id).where(Location.codigo=='T309')) is not None
        assert client.post('/settings/corridors/T/delete',data={'csrf':tok},follow_redirects=False).status_code==303
        with SessionLocal() as db:
            assert db.scalar(select(Corridor.id).where(Corridor.codigo=='T')) is None
            assert db.scalar(select(Location.id).where(Location.codigo=='T309')) is None
        # Deleting an occupied corridor or shrinking past an occupied location is forbidden.
        assert client.post('/settings/corridors/B/update',data={'csrf':tok,'andares':'1','posicoes':'3'}).status_code==409
        assert client.post('/settings/locations/Ch%C3%A3o/delete',data={'csrf':tok}).status_code==409
    with TestClient(app) as client:
        login(client,'operador')
        assert client.get('/settings').status_code==403
        assert client.get('/settings/locations').status_code==403
        assert client.get('/settings/corridors/A').status_code==403
        assert client.get('/locations',follow_redirects=False).headers['location']=='/map'
        assert client.post('/settings/locations',data={'codigo':'A105'}).status_code==403

def test_zz_permissions_and_rate_limit():
    with TestClient(app) as client:
        login(client,'operador')
        assert client.post('/settings/locations/B201/delete',data={'csrf':csrf(client)}).status_code==403
        assert client.post('/users',data={}).status_code==403
    with TestClient(app) as client:
        login(client,'admin')
        payload={'csrf':csrf(client),'username':'newop','display_name':'Novo Operador',
                 'role':'operador','password':secrets.token_urlsafe(24)}
        assert client.post('/users',data=payload,follow_redirects=False).status_code==303
        assert 'newop' in client.get('/users').text
    with TestClient(app) as client:
        for _ in range(8):
            assert client.post('/login',data=login_data(client, {'username':'none','password':secrets.token_urlsafe(24)})).status_code==401
        assert client.post('/login',data=login_data(client, {'username':'none','password':secrets.token_urlsafe(24)})).status_code==429
    # Keep subsequent tests independent of the intentional rate-limit test.
    from iberbag.database import FailedLogin
    with SessionLocal() as db:
        db.execute(delete(FailedLogin))
        db.commit()



def test_map_occupancy_accessible_to_operator_and_settings_restricted():
    with TestClient(app) as client:
        assert client.get('/map', follow_redirects=False).status_code == 303
        login(client, 'operador')
        response=client.get('/map?corredor=A&andar=1')
        assert response.status_code == 200
        assert 'Mapa do armazém' in response.text
        assert 'A101' in response.text
        assert '2 paletes' in response.text  # old inconsistency, marked and retained
        assert 'Verificar' in response.text
        assert 'A102' in response.text
        assert 'Livre' in response.text
        assert 'Chão' in response.text and '7' in response.text
        assert '/stock?local=A101' in response.text
        assert '/settings/locations' not in response.text  # no admin actions
        assert client.get('/settings/locations').status_code == 403


def test_map_floors_and_missing_catalogue_positions():
    with TestClient(app) as client:
        login(client, 'admin')
        x=client.get('/map?corredor=L&andar=5')
        assert x.status_code == 200
        assert 'L501' in x.text and 'L527' in x.text
        assert 'L601' not in x.text
        assert 'Andar 5' in x.text
        assert 'Gerir localizações' in x.text
        y=client.get('/map?corredor=A&andar=500')
        assert 'A101' in y.text  # invalid floor safely defaults to first
        # Deleting an empty location excludes it from the map; no stock changes.
        t=csrf(client)
        assert client.post('/settings/locations/A109/delete', data={'csrf':t}, follow_redirects=False).status_code == 303
        q=client.get('/map?corredor=A&andar=1')
        assert 'A109' in q.text
        assert 'Indisponível' in q.text
        assert 'Não configurada' in q.text
        assert client.post('/settings/locations',data={'csrf':t,'codigo':'A109'},follow_redirects=False).status_code==303


def test_map_queries_are_read_only():
    with SessionLocal() as db:
        before=(db.scalar(select(func.sum(Stock.quantidade))), db.scalar(select(func.count()).select_from(Stock)),
                db.scalar(select(func.count()).select_from(Audit)))
    with TestClient(app) as client:
        login(client,'operador')
        for q in ['/map', '/map?corredor=N&andar=4', '/map?corredor=C&andar=-1',
                  '/map?corredor=A%27%20OR%201%3D1&andar=1']:
            assert client.get(q).status_code == 200
    with SessionLocal() as db:
        after=(db.scalar(select(func.sum(Stock.quantidade))), db.scalar(select(func.count()).select_from(Stock)),
               db.scalar(select(func.count()).select_from(Audit)))
    # Login adds an audit record, but opening the map never writes to the DB.
    assert before[:2] == after[:2]
    assert after[2] == before[2] + 1
