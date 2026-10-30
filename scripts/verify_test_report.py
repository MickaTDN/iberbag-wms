"""Require actual critical test execution; emit only names/counts, never traces."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import os
import re
import xml.etree.ElementTree as ET

REQUIRED_POSTGRES = {
    'test_ddn_contexts.test_same_article_lot_load_and_location_are_isolated',
    'test_ddn_contexts.test_ovar_stock_is_never_available_in_escapaes',
    'test_ddn_contexts.test_pending_allocations_do_not_cross_owners',
    'test_ddn_contexts.test_context_capacity_and_catalogue_are_resolved_exactly',
    'test_ddn_contexts.test_inventory_scope_clock_and_adjustments_are_isolated',
    'test_ddn_contexts.test_server_and_composite_foreign_keys_refuse_wrong_context',
    'test_ddn_contexts.test_migration_preserves_every_legacy_column_and_document',
    'test_ddn_contexts.test_backup_restore_keeps_all_contexts_and_archived_pdfs',
    'test_ddn_review.test_ovar_physical_code_cannot_be_duplicated_for_cosmopak',
    'test_ddn_review.test_deleting_location_does_not_release_physical_claim',
    'test_ddn_review.test_concurrent_physical_claims_allow_exactly_one_context',
    'test_ddn_review.test_reserved_stock_cancellation_and_movements_are_contextual',
    'test_ddn_review.test_upgrade_refuses_ambiguous_existing_physical_codes_without_changes',
    'test_ddn_review.test_ddn1_backup_restore_upgrade_preserves_legacy_data',
    'test_dispatching.test_fifo_multiple_loads_locations_and_zero_rows',
    'test_dispatching.test_concurrent_orders_or_duplicate_confirmations',
    'test_receiving.test_atomic_racing_confirmations',
    'test_transferring.test_rollback_and_immutability',
}


def verify(path, backend, required=None):
    raw = Path(path).read_bytes()
    # This XML is generated locally by pytest, never accepted from uploads/users.
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('Declarações XML não permitidas no relatório de testes.')
    root = ET.fromstring(raw)
    totals=Counter(); executed=Counter()
    for case in root.iter('testcase'):
        state='failed' if case.find('failure') is not None or case.find('error') is not None else ('skipped' if case.find('skipped') is not None else 'passed')
        totals[state]+=1
        module=case.get('classname','').split('.')[-1]
        name=case.get('name','').split('[')[0]
        key=module+'.'+name
        if re.fullmatch(r'[A-Za-z0-9_.]+',key) and state=='passed':executed[key]+=1
    required = REQUIRED_POSTGRES if required is None and backend=='postgres' else (required or set())
    missing=sorted(required-set(executed))
    valid=bool(totals['passed']) and not totals['failed'] and not missing and (backend!='postgres' or not totals['skipped'])
    commit=os.getenv('GITHUB_SHA','')
    report=dict(backend=backend,commit=commit if re.fullmatch('[a-f0-9]{40}',commit) else None,
        junit_sha256=hashlib.sha256(raw).hexdigest(),counts=dict(totals),
        required_passed=sorted(required & set(executed)),missing_required=missing,
        passed_functions=dict(sorted(executed.items())),valid=valid)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report',type=Path)
    parser.add_argument('--backend',choices=('sqlite','postgres'),required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try:result=verify(args.report,args.backend)
    except (OSError,ValueError,ET.ParseError):
        raise SystemExit('Relatório ausente ou inválido; execução não comprovada.')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('backend','counts','missing_required','valid')}))
    if os.getenv('GITHUB_ACTIONS')=='true':
        print(f"::notice title=DDN {args.backend} evidence::passed={result['counts'].get('passed',0)}; skipped={result['counts'].get('skipped',0)}; required={len(result['required_passed'])}; valid={result['valid']}")
    if not result['valid']:raise SystemExit('Testes obrigatórios não comprovados; revisão recusada.')


if __name__=='__main__':main()
