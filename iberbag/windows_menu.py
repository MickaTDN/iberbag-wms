"""Assisted operations for the Windows test installation, with no stored passwords."""
import os
import subprocess
import sys


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--isolated-ensemble', action='store_true')
    isolated = parser.parse_args().isolated_ensemble
    print('DDN WMS — ensaio de instalação Windows; usar apenas dados fictícios.')
    name = 'ddn_wms_ensaio_test' if isolated else (input('Nome da base de TESTE [iberbag_ensaio_test]: ').strip() or 'iberbag_ensaio_test')
    if not name.endswith('_test') or not name.replace('_', '').isalnum():
        raise SystemExit('Usar letras, números, _ e nome terminado em _test.')
    env = {k:v for k,v in os.environ.items() if not k.startswith(('WMS_', 'PG'))}
    env['WMS_DB_NAME'] = name
    # The assisted test menu must not accidentally inherit a production URL.
    env.pop('WMS_DATABASE_URL', None)
    env['WMS_DB_HOST'] = '127.0.0.1'
    port = int(os.environ.get('DDN_TEST_PG_PORT', '55433')) if isolated else int(input('Porta PostgreSQL [55433]: ').strip() or '55433')
    http_port = int(os.environ.get('DDN_TEST_HTTP_PORT', '8100')) if isolated else 8000
    if not 1024 <= port <= 65535 or not 1024 <= http_port <= 65535 or port == http_port:
        raise SystemExit('Portas inválidas.')
    if isolated and (port in (5432,8000,55432,8765,8443) or http_port in (5432,8000,55432,8765,8443)):
        raise SystemExit('Portas reservadas; configurar recursos DDN próprios.')
    env['WMS_DB_PORT'] = str(port)
    env['WMS_HTTP_PORT'] = str(http_port)
    env['WMS_DB_USER'] = 'ddn_wms_runtime' if isolated else (input('Utilizador PostgreSQL [iberbag_app]: ').strip() or 'iberbag_app')
    options = {
        '1': ['-m', 'iberbag.demo'],
        '2': ['-m', 'iberbag.transfer', 'preview'],
        '3': ['-m', 'iberbag.transfer', 'apply'],
        '4': ['-m', 'iberbag.transfer', 'reconcile'],
        '5': ['-m', 'iberbag.server', 'serve'],
        '6': ['-m', 'iberbag.backup', 'backup'],
        '7': ['-m', 'iberbag.backup', 'restore-test'],
        '8': ['-m', 'iberbag.server', 'prepare'],
        '9': ['-m', 'iberbag.server', 'admin'],
        '10': ['-m', 'iberbag.server', 'upgrade-preview'],
        '11': ['-m', 'iberbag.server', 'upgrade-apply'],
    }
    while True:
        print('''
1 Criar SQLite fictício
2 Pré-visualizar migração e guardar cópia SQLite
3 Aplicar migração na base PostgreSQL VAZIA
4 Reconciliar novamente
5 Iniciar servidor (Ctrl+C para parar)
6 Criar backup PostgreSQL
7 Ensaiar restauro numa OUTRA base VAZIA
8 Preparar base SEM migração (instalação vazia)
9 Criar administrador (instalação vazia)
10 Pré-visualizar junção de segurança/multicliente (ddn_security_0001)
11 Aplicar atualização com backup/restauro verificado
0 Sair
''')
        choice = input('Opção: ').strip()
        if choice == '0':
            return
        if choice not in options:
            continue
        args = list(options[choice])
        current = dict(env)
        if isolated and choice in ('3','7','8','9','10','11'):
            print('Requer conta de manutenção DDN; não usar conta ou cluster GVA.')
            if input('Instância DDN dedicada comprovada? Escreva DDN ISOLADO: ') != 'DDN ISOLADO':
                continue
            current['WMS_DB_USER'] = 'ddn_wms_owner'
        if choice in ('2', '3', '4'):
            if choice == '2':
                args += ['--source', input('Ficheiro SQLite FICTÍCIO: ').strip().strip('"')]
            args += ['--bundle', input('Pasta do ensaio (nova na pré-visualização): ').strip().strip('"'), '--test-data-only']
        if choice == '11':
            args += ['--backup-folder', input('Pasta do backup com relatório de restauro reconciliado: ').strip().strip('"')]
            if input('Servidor parado e pré-visualização revista? Escreva ATUALIZAR TESTE: ') != 'ATUALIZAR TESTE':
                continue
        if choice in ('6', '7'):
            args += ['--folder', input('Pasta do backup (nova ao criar): ').strip().strip('"')]
        if choice == '7':
            target = input('OUTRA base vazia para restauro, terminada em _test: ').strip()
            if (target == name or not target.endswith('_test') or not target.replace('_', '').isalnum()
                    or (isolated and not target.startswith('ddn_wms_restore_'))):
                print('Destino inválido; não foi executado restauro.')
                continue
            current['WMS_DB_NAME'] = target
        if choice == '3' and input('Leu preview.json e confirma dados fictícios? Escreva MIGRAR TESTE: ') != 'MIGRAR TESTE':
            continue
        if choice == '8':
            print('Não usar esta opção antes de migrar: a migração exige uma base sem tabelas.')
            if input('Criar instalação vazia? Escreva VAZIA: ') != 'VAZIA':
                continue
        try:
            code = subprocess.call([sys.executable, *args], env=current)
            print('Concluído.' if code == 0 else 'Operação falhou. Leia a mensagem acima; não repetir sem corrigir a causa.')
        except KeyboardInterrupt:
            print('\nServidor parado.')


if __name__ == '__main__':
    main()
