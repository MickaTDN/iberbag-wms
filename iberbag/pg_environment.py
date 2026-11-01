"""Explicit libpq transport configuration, independent of inherited PG* variables."""
import os
from sqlalchemy.engine import make_url

# Existing libpq names remain internal; only WMS_PG_* is explicit configuration.
TLS = {
    'sslmode': 'PGSSLMODE', 'sslrootcert': 'PGSSLROOTCERT',
    'sslcert': 'PGSSLCERT', 'sslkey': 'PGSSLKEY', 'sslcrl': 'PGSSLCRL',
    'sslcrldir': 'PGSSLCRLDIR', 'ssl_min_protocol_version': 'PGSSLMINPROTOCOLVERSION',
    'ssl_max_protocol_version': 'PGSSLMAXPROTOCOLVERSION',
    'channel_binding': 'PGCHANNELBINDING', 'sslnegotiation': 'PGSSLNEGOTIATION',
}


def tls_options(url=None, source=None, *, allow_url_options=False):
    source = os.environ if source is None else source
    values = {key: source['WMS_PG_' + key.upper()] for key in TLS if source.get('WMS_PG_' + key.upper())}
    if url is not None:
        url = make_url(url)
        if url.drivername != 'postgresql+psycopg':
            raise ValueError('Ligação de manutenção exige PostgreSQL/psycopg.')
        for key, value in url.query.items():
            if key not in TLS:
                if not allow_url_options:
                    raise ValueError('Parâmetros de ligação personalizados não são aceites; apenas opções TLS explícitas.')
                continue
            if key in values and values[key] != value:
                raise ValueError('Configuração TLS PostgreSQL contraditória entre ligação e WMS_PG_*.')
            values[key] = value
    for key, value in values.items():
        if not isinstance(value, str) or not value or len(value) > 4096 or any(ord(c) < 32 for c in value):
            raise ValueError('Configuração TLS PostgreSQL inválida; não foram registados valores.')
    enums = {'sslmode': {'disable','allow','prefer','require','verify-ca','verify-full'},
             'channel_binding': {'disable','prefer','require'},
             'sslnegotiation': {'postgres','direct'},
             'ssl_min_protocol_version': {'TLSv1.2','TLSv1.3'},
             'ssl_max_protocol_version': {'TLSv1.2','TLSv1.3'}}
    if any(k in values and values[k] not in valid for k, valid in enums.items()):
        raise ValueError('Opção TLS PostgreSQL inválida ou protocolo obsoleto.')
    if values.get('ssl_min_protocol_version') == 'TLSv1.3' and values.get('ssl_max_protocol_version') == 'TLSv1.2':
        raise ValueError('Intervalo TLS PostgreSQL inválido.')
    # Never silently turn a legacy certificate-verifying connection into prefer.
    inherited_tls = {k for k, v in source.items() if v and (k.startswith('PGSSL') or k == 'PGCHANNELBINDING')}
    explicit_tls = {TLS[key]:value for key,value in values.items()}
    if any(source[env] != explicit_tls.get(env) for env in inherited_tls):
        raise ValueError('TLS PostgreSQL herdado: configurar explicitamente WMS_PG_* ou opções TLS na ligação.')
    return explicit_tls


def client_environment(url, source=None):
    """Secrets only in the child's environment, never in argv or reports."""
    source = os.environ if source is None else source
    tls = tls_options(url, source)
    env = {k: v for k, v in source.items() if not k.upper().startswith(('PG', 'WMS_'))}
    env.update(tls)
    env['PGPASSFILE'] = os.devnull
    env['PGCONNECT_TIMEOUT'] = '10'
    env['PGPASSWORD'] = make_url(url).password or ''
    return env


def configure_process(url=None, *, allow_url_options=False):
    """Call before engine/client connection creation in a dedicated WMS process."""
    tls = tls_options(url, allow_url_options=allow_url_options)
    for name in list(os.environ):
        if name.upper().startswith('PG'):
            os.environ.pop(name)
    os.environ.update(tls)
    os.environ['PGPASSFILE'] = os.devnull
    os.environ['PGCONNECT_TIMEOUT'] = '10'


def isolated_test_environment(source=None):
    """No production URL, credentials, route, service or TLS file enters tests."""
    source = os.environ if source is None else source
    return {k: v for k, v in source.items() if not k.upper().startswith(('PG', 'WMS_'))}
