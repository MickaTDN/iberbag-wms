"""Administrative command-line operations."""
import argparse, getpass, sys
from sqlalchemy import select
from .database import init_db, SessionLocal, User, Audit
from .security import set_password
from .importer import analyze, import_stock
from .capacities import export_csv


def main():
    parser = argparse.ArgumentParser(description='Iberbag WMS — ferramentas do servidor')
    commands = parser.add_subparsers(dest='command', required=True)
    upgrade = commands.add_parser('upgrade-db', help='rever/aplicar atualização de esquema com backup verificado')
    mode = upgrade.add_mutually_exclusive_group(required=True)
    mode.add_argument('--preview', action='store_true')
    mode.add_argument('--apply', action='store_true')
    upgrade.add_argument('--backup-folder')
    commands.add_parser('prepare-db', help='preparar base NOVA com migrações versionadas')
    commands.add_parser('init-db', help='criar as tabelas')
    commands.add_parser('create-admin', help='criar o primeiro administrador')
    imp = commands.add_parser('import-excel', help='validar/importar o ficheiro XLSM (nunca executa VBA)')
    imp.add_argument('ficheiro')
    imp.add_argument('--apply', action='store_true', help='gravar dados após a análise; uma única vez')
    capex = commands.add_parser('export-capacities', help='exportar ocupação e capacidades fixas (apenas consulta)')
    capex.add_argument('ficheiro')
    capim = commands.add_parser('import-capacities', help='desativado: capacidades fixas na versão 2.2')
    capim.add_argument('ficheiro')
    capim.add_argument('--apply', action='store_true', help='confirmar gravação')
    args = parser.parse_args()
    if args.command == 'upgrade-db':
        import json
        from .database import engine
        from .schema_upgrade import preview, apply_upgrade
        if args.apply and not args.backup_folder:
            parser.error('--apply exige --backup-folder')
        try:
            report = preview(engine) if args.preview else apply_upgrade(engine, args.backup_folder)
        except Exception:
            raise SystemExit('Atualização recusada. Verificar revisão, ligação, backup e relatório de restauro; parar o servidor e repetir a pré-visualização.')
        print(json.dumps(report, ensure_ascii=False, indent=2)); return
    if args.command == 'prepare-db':
        from .migrations import prepare_database
        prepare_database()
        print('Base preparada e versão verificada.'); return
    init_db()
    if args.command == 'init-db':
        print('Tabelas criadas.'); return
    if args.command == 'export-capacities':
        with SessionLocal() as db: total=export_csv(db,args.ficheiro)
        print(f'{total} localizações exportadas para {args.ficheiro}.'); return
    if args.command == 'import-capacities':
        raise SystemExit('Capacidades fixas no Iberbag WMS: 1 palete por posição, Chão sem limite. Não é necessário importar capacidades.')
    if args.command == 'create-admin':
        u = input('Nome de utilizador: ').strip()
        name = input('Nome a apresentar: ').strip() or u
        password = getpass.getpass('Palavra-passe (mínimo 12 caracteres): ')
        confirm = getpass.getpass('Confirmar palavra-passe: ')
        if not u or not u.replace('_', '').replace('-', '').isalnum() or len(u) > 60:
            raise SystemExit('Nome de utilizador inválido.')
        if password != confirm:
            raise SystemExit('As palavras-passe não coincidem.')
        with SessionLocal() as db:
            if db.scalar(select(User.id).where(User.username == u)):
                raise SystemExit('Já existe esse nome de utilizador.')
            user = User(username=u, display_name=name[:120], role='admin', active=True)
            try: set_password(user, password)
            except ValueError as err: raise SystemExit(str(err)) from err
            db.add(user); db.add(Audit(username=u, action='CRIAR_ADMIN', details='Criado por consola.')); db.commit()
        print('Administrador criado.'); return
    if args.command == 'import-excel':
        report = analyze(args.ficheiro)
        print(f'STOCK: {report.input_rows} linhas, {report.pallets} paletes, {len(report.groups)} combinações.')
        print(f'Linhas ignoradas: {report.ignored}. Total geral excluído: {report.skipped_totals}.')
        print(f'Paletes com campos incompletos: {report.missing_fields}.')
        print(f'Cargas sem data de entrada: {len(report.loads_without_date)}.')
        from collections import Counter
        from .location_rules import capacity_for, is_unassigned
        by_location = Counter()
        for (art, lot, load, loc), qty in report.groups.items():
            by_location[loc] += qty
        excess = [(loc, n) for loc, n in by_location.items() if capacity_for(loc) == 1 and n > 1]
        unlocated = sum(n for loc, n in by_location.items() if is_unassigned(loc))
        print(f'Posições ocupadas por mais de 1 palete: {len(excess)}; paletes sem localização: {unlocated}.')
        for loc, n in excess[:10]: print(f'  A CONFERIR: {loc}: {n} paletes (capacidade 1).')
        for problem in report.problems[:20]: print('  ATENÇÃO:', problem)
        if report.loads_without_date:
            print('  ATENÇÃO: FIFO ainda não pode ser certificado. Cargas sem data (amostra):', ', '.join(report.loads_without_date[:10]))
        if not args.apply:
            print('Validação concluída; não foram escritos dados. Acrescente --apply para importar.'); return
        if report.ignored:
            raise SystemExit('Importação não permitida: existem linhas com quantidades inválidas.')
        with SessionLocal() as db:
            try: import_stock(db, report)
            except ValueError as err: raise SystemExit(str(err)) from err
        print('Importação concluída com sucesso. Consulte os avisos antes de usar operacionalmente.')

if __name__ == '__main__': main()
