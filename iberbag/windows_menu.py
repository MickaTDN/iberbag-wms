"""Assisted operations for the Windows test installation, with no stored passwords."""
import os
import subprocess
import sys


def main():
    print('IBERBAG WMS — ensaio de instalação Windows; usar apenas dados fictícios.')
    name = input('Nome da base de TESTE [iberbag_ensaio_test]: ').strip() or 'iberbag_ensaio_test'
    if not name.endswith('_test') or not name.replace('_', '').isalnum():
        raise SystemExit('Usar letras, números, _ e nome terminado em _test.')
    env = {k:v for k,v in os.environ.items() if not k.startswith(('WMS_', 'PG'))}
    env['WMS_DB_NAME'] = name
    # The assisted test menu must not accidentally inherit a production URL.
    env.pop('WMS_DATABASE_URL', None)
    env['WMS_DB_HOST'] = '127.0.0.1'
    env['WMS_DB_PORT'] = '5432'
    env['WMS_DB_USER'] = input('Utilizador PostgreSQL [iberbag_app]: ').strip() or 'iberbag_app'
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
10 Pré-visualizar atualização 0001–0006 para 0007
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
            if target == name or not target.endswith('_test') or not target.replace('_', '').isalnum():
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
