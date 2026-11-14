"""Auditable, per-folder Windows rehearsal bootstrap. No privileged operations."""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
PORTS = {'postgres': 55433, 'http': 8100, 'https': 8447}
RESERVED_PORTS = {5432, 8000, 55432, 8765, 8443}


def isolated_environment():
    return {k: v for k, v in os.environ.items()
            if not k.upper().startswith(('WMS_', 'PG', 'PYTHON', 'PIP_', 'DDN_'))}


def validate(settings):
    if set(settings) != {'database', 'user', 'postgres', 'http', 'https'}:
        raise ValueError('Configuração inesperada.')
    if settings['database'] != 'ddn_wms_ensaio_test' or settings['user'] != 'ddn_wms_runtime':
        raise ValueError('Só a base e conta dedicadas de ENSAIO são permitidas.')
    ports = [settings[k] for k in PORTS]
    if any(type(p) is not int or not 1024 <= p <= 65535 for p in ports) or len(set(ports)) != 3:
        raise ValueError('Escolher três portas distintas entre 1024 e 65535.')
    # Do not silently adopt standard PostgreSQL/HTTPS endpoints used by other apps.
    if any(p in RESERVED_PORTS for p in ports):
        raise ValueError('Portas históricas reservadas; escolher portas DDN próprias.')
    return settings


def load_settings(root):
    local = root / 'DDN_LOCAL.json'
    default = root / 'DDN_ENSAIO.json'
    path = local if local.exists() else default
    if path.exists():
        return validate(json.loads(path.read_text(encoding='utf-8')))
    return validate(dict(database='ddn_wms_ensaio_test', user='ddn_wms_runtime', **PORTS))


def verify_bundle(root=ROOT):
    manifest = json.loads((root / 'MANIFEST.json').read_text(encoding='utf-8'))
    if manifest.get('mode') != 'homologacao' or not re.fullmatch('[a-f0-9]{40}', manifest.get('commit', '')):
        raise ValueError('Manifesto inválido.')
    for name, digest in manifest['files'].items():
        path = root / name
        if Path(name).is_absolute() or '\\' in name or '..' in Path(name).parts or path.is_symlink():
            raise ValueError('Caminho inseguro no pacote.')
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Integridade do pacote inválida; descarregar novamente.')
    return manifest


def probe(port):
    with socket.socket() as s:
        try:
            s.bind(('127.0.0.1', port))
            return 'livre'
        except OSError:
            return 'ocupada'


def windows_inventory():
    # Only names/states, never service command lines (may contain credentials).
    script = "Get-CimInstance Win32_Service | Select-Object Name,State | ConvertTo-Json -Compress"
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, timeout=30, check=True)
    return json.loads(result.stdout.decode('utf-8-sig'))


def check_windows11():
    if (platform.system() != 'Windows' or sys.getwindowsversion().build < 22000
            or sys.getwindowsversion().product_type != 1
            or platform.machine().lower() not in ('amd64', 'x86_64')):
        raise ValueError('Este pacote exige Windows 11 x64.')
    if sys.version_info[:2] != (3, 12):
        raise ValueError('É necessário Python 3.12 x64 aprovado pela equipa técnica.')


def install(root=ROOT):
    verify_bundle(root)
    check_windows11()
    # Refuse arbitrary directories/previous environments; never repair by deleting.
    target = root / '.ddn-venv'
    if target.exists():
        raise ValueError('Instalação existente preservada. Usar opção 4 Reparar; atualização de serviços exige revisão técnica.')
    settings = load_settings(root)
    for port in (settings[k] for k in PORTS):
        if probe(port) != 'livre':
            raise ValueError('Conflito de portas. Nada foi instalado; rever recursos DDN/GVA.')
    if input('Instalar dependências APENAS nesta pasta? Escreva INSTALAR ENSAIO: ') != 'INSTALAR ENSAIO':
        return
    env = isolated_environment()
    subprocess.run([sys.executable, '-I', '-m', 'venv', str(target)], env=env, check=True)
    python = target / 'Scripts' / 'python.exe'
    subprocess.run([str(python), '-I', '-m', 'pip', '--isolated', 'install', '--no-index',
                    '--no-cache-dir', '--find-links', str(root / 'wheels'), '-r', str(root / 'requirements-prod.lock')],
                   env=env, check=True)
    subprocess.run([str(python), '-I', '-m', 'pip', '--isolated', 'check'], env=env, check=True)
    print('Dependências instaladas. PostgreSQL/HTTPS ainda NÃO configurados; seguir LEIA-ME.')


def active_python(root):
    pointer = root / 'DDN_ACTIVE_ENV.json'
    if not pointer.exists():
        return root / '.ddn-venv' / 'Scripts' / 'python.exe'
    value = json.loads(pointer.read_text(encoding='utf-8'))
    relative = value.get('environment', '')
    if not re.fullmatch(r'\.ddn-envs/env-[a-f0-9]{32}', relative):
        raise ValueError('Referência de ambiente inválida.')
    environment = root / relative
    for path in (root / '.ddn-envs', environment, environment / 'Scripts'):
        if path.is_symlink() or path.is_junction():
            raise ValueError('Ligação inesperada no ambiente.')
    if json.loads((environment / 'DDN_COMPLETE.json').read_text()) != value:
        raise ValueError('Ambiente incompleto; reparar sem apagar o anterior.')
    if value.get('lock_sha256') != hashlib.sha256((root / 'requirements-prod.lock').read_bytes()).hexdigest():
        raise ValueError('Dependências mudaram; rever atualização antes de iniciar.')
    return environment / 'Scripts' / 'python.exe'


def repair(root=ROOT):
    """Build in its final unique path; never relocate a venv or delete failed work."""
    import uuid
    verify_bundle(root)
    check_windows11()
    parent = root / '.ddn-envs'
    if parent.is_symlink() or parent.is_junction():
        raise ValueError('Pasta de ambientes insegura.')
    if input('Criar NOVO ambiente sem apagar o anterior? Escreva REPARAR ENSAIO: ') != 'REPARAR ENSAIO':
        return
    parent.mkdir(exist_ok=True)
    relative = '.ddn-envs/env-' + uuid.uuid4().hex
    target = root / relative
    target.mkdir()  # exclusive name, final path: venv entrypoints remain valid
    env = isolated_environment()
    subprocess.run([sys.executable, '-I', '-m', 'venv', str(target)], env=env, check=True)
    python = target / 'Scripts' / 'python.exe'
    subprocess.run([str(python), '-I', '-m', 'pip', '--isolated', 'install', '--no-index',
                    '--no-cache-dir', '--find-links', str(root / 'wheels'), '-r', str(root / 'requirements-prod.lock')],
                   env=env, check=True)
    subprocess.run([str(python), '-I', '-m', 'pip', '--isolated', 'check'], env=env, check=True)
    subprocess.run([str(python), '-I', '-c', 'import psycopg,uvicorn,sqlalchemy,cryptography'], env=env, check=True)
    state = dict(environment=relative, lock_sha256=hashlib.sha256((root / 'requirements-prod.lock').read_bytes()).hexdigest())
    (target / 'DDN_COMPLETE.json').write_text(json.dumps(state), encoding='utf-8')
    temporary = root / ('DDN_ACTIVE_ENV-' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(state, stream); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, root / 'DDN_ACTIVE_ENV.json')
    print('Novo ambiente validado e selecionado. Ambientes anteriores preservados.')



def run_menu(root=ROOT):
    verify_bundle(root)
    python = active_python(root)
    if not python.is_file():
        raise ValueError('Escolha primeiro Instalar dependências.')
    settings = load_settings(root)
    env = isolated_environment()
    env.update(DDN_TEST_PG_PORT=str(settings['postgres']), DDN_TEST_HTTP_PORT=str(settings['http']))
    # Existing assistant asks credentials via getpass; none stored or passed in argv.
    subprocess.run([str(python), '-m', 'iberbag.windows_menu', '--isolated-ensemble'], cwd=root, env=env, check=True)


def main():
    print('DDN WMS — ENSAIO. Produção indisponível. Não altera serviços GVA.')
    print('1 Instalar dependências locais\n2 Verificar pacote/portas/serviços (leitura)\n3 Assistente WMS existente\n4 Reparar dependências sem apagar\n5 Procedimento técnico PostgreSQL/serviços/HTTPS\n6 Criar atalho pessoal do assistente\n0 Sair')
    choice = input('Opção: ').strip()
    try:
        if choice == '1': install()
        elif choice == '2':
            manifest = verify_bundle()
            print('Commit:', manifest['commit'])
            settings = load_settings(ROOT)
            print('Portas DDN:', {k: probe(settings[k]) for k in PORTS})
            print('Serviços existentes (só leitura):', windows_inventory())
        elif choice == '3': run_menu()
        elif choice == '4': repair()
        elif choice == '5': os.startfile(ROOT / 'docs' / 'DDN_PROVISIONAMENTO_ASSISTIDO.md')
        elif choice == '6':
            verify_bundle()
            subprocess.run(['powershell.exe', '-NoProfile', '-File', str(ROOT / 'windows' / 'atalho_assistente.ps1')], check=True)
    except Exception:
        # Avoid exception repr/tracebacks: DB URLs/subprocess errors can hold secrets.
        print('Operação recusada ou falhou. Dados preservados. Consulte LEIA-ME; não repetir sem rever a causa.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
