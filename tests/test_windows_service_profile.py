"""No real credentials or database; validate the service boundary before imports."""
import base64,os
import pytest
from iberbag.windows_service import configure
from scripts.audit_windows_components import audit


def profile():return dict(database='ddn_wms_ensaio_test',user='ddn_wms_runtime',postgres=55433,http=8100,https=8447)


def test_service_profile_clears_inherited_targets_and_keeps_credentials_out_of_arguments(monkeypatch):
    from sqlalchemy.engine import make_url
    original=dict(os.environ)
    monkeypatch.setenv('PGHOSTADDR','192.0.2.50');monkeypatch.setenv('WMS_DATABASE_URL','postgresql://fictional/production')
    try:
        configure(profile(),base64.b64encode(b'FICTITIOUS @ complex / value').decode())
        url=make_url(os.environ['WMS_DATABASE_URL'])
        assert url.host=='127.0.0.1' and url.database=='ddn_wms_ensaio_test' and url.port==55433
        assert url.password=='FICTITIOUS @ complex / value' and 'PGHOSTADDR' not in os.environ
        assert os.environ['WMS_COOKIE_SECURE']=='1' and os.environ['WMS_HTTP_PORT']=='8100'
    finally:os.environ.clear();os.environ.update(original)


@pytest.mark.parametrize('change',[{'database':'gva_test'},{'user':'postgres'},{'postgres':55432},{'https':8443},{'http':55433},{'postgres':True}])
def test_service_profile_rejects_other_targets(change):
    p=profile();p.update(change)
    with pytest.raises(ValueError):configure(p,base64.b64encode(b'FICTITIOUS').decode())


@pytest.mark.parametrize('encoded',['','invalid***','A'*16385,base64.b64encode(b'bad\0value').decode()])
def test_service_profile_refuses_missing_malformed_or_oversized_credential(encoded):
    with pytest.raises((ValueError,UnicodeError)):configure(profile(),encoded)


def test_windows_component_advisory_gate_refuses_every_applicable_issue():
    assert audit(lambda url:[])==set()
    with pytest.raises(ValueError):audit(lambda url:[{'ghsa_id':'GHSA-4f7c-pmjv-c25w'}])
    with pytest.raises(ValueError):audit(lambda url:[{'ghsa_id':'GHSA-UNREVIEWED'}])
