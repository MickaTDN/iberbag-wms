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
    'test_security_integration.test_mismatched_heads_refuse_startup_and_upgrade_without_adoption',
    'test_security_integration.test_encrypted_verified_upgrade_origin_matrix',
    'test_security_integration.test_encrypted_recovery_preserves_all_owners_warehouses_and_claims',
    'test_security_integration.test_upgrade_rollback_retains_original_heads_and_history',
    'test_security_integration.test_existing_pr9_encrypted_archive_uses_original_authenticated_inventory',
    'test_security_context_auth.test_login_csrf_preserves_context_and_blocks_external_and_stale_forms',
    'test_security_context_auth.test_secure_login_challenge_and_operator_admin_denial_in_other_context',
    'test_security_context_auth.test_restricted_runtime_can_write_contextual_stock_but_not_physical_claims_or_history',
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
ORIGINAL_POSTGRES = frozenset(REQUIRED_POSTGRES)
REQUIRED_POSTGRES |= {
    'test_security_regressions.test_slow_body_never_holds_global_lock',
    'test_security_regressions.test_rejected_post_does_not_wait_for_write_lock',
    'test_security_regressions.test_write_revalidates_after_waiting_for_lock',
    'test_security_regressions.test_runtime_rejects_privileges_outside_current_schema',
    'test_security_regressions.test_every_post_rejects_bad_csrf_without_waiting_for_lock',
    'test_security_regressions.test_incomplete_body_timeout_cancel_and_disconnect_release_resources',
    'test_security_regressions.test_authenticated_concurrent_forms_finish_atomically',
    'test_security_regressions.test_admin_posts_refuse_operator_before_lock',
    'test_security_regressions.test_database_error_after_lock_releases_transaction',
    'test_security_regressions.test_login_revalidates_credentials_and_context_before_session_creation',
    'test_security_regressions.test_snapshot_isolation_cannot_reuse_stale_authorization',
    'test_pg_environment.test_inherited_routes_and_credentials_never_reach_libpq',
    'test_pg_environment.test_explicit_tls_preserved_without_legacy_tls_downgrade',
    'test_pg_environment.test_pg_dump_receives_controlled_environment_and_no_password_argv',
    'test_pg_environment.test_process_policy_removes_hidden_routes_before_engine_connections',
    'test_pg_client_routing.test_pg_clients_drop_inherited_hostaddr_and_services',
    'test_private_files.test_private_outputs_are_exclusive_and_temporaries_removed_on_error',
}
REQUIRED_POSTGRES |= {
    'test_phase2a.test_owner_year_warehouse_numbering_and_history',
    'test_phase2a.test_concurrent_numbering_retry_and_rollback',
    'test_phase2a.test_legacy_collision_all_warehouses_and_unchanged_references',
    'test_phase2a.test_numbering_backup_restore_preserves_next_number',
    'test_phase2a.test_office_navigation_context_roles_csrf',
    'test_phase2a.test_prior_security_head_upgrades_without_rewriting_loads',
    'test_phase2a.test_restricted_runtime_numbers_but_cannot_rewrite_ledger',
}
REQUIRED_POSTGRES |= {
    'test_valmet.test_valmet_composition_counts_physical_units_once',
    'test_valmet.test_valmet_pending_identification_keeps_original_units_and_locations',
    'test_valmet.test_valmet_m2_vasilhame_and_unknown_location',
    'test_valmet.test_valmet_invalid_entries_rollback',
    'test_valmet.test_valmet_ids_capacity_and_confirmations_are_concurrent_safe',
    'test_valmet.test_valmet_owner_wide_manual_ids_and_context_isolation',
    'test_valmet.test_valmet_backup_restore_and_upgrade_preserve_units',
    'test_valmet.test_valmet_security_head_migration_and_recovery',
    'test_valmet.test_valmet_http_roles_csrf_and_restricted_runtime',
    'test_valmet.test_valmet_identification_race_and_audit',
    'test_valmet.test_valmet_error_after_physical_insert_rolls_back',
}
REQUIRED_POSTGRES |= {
    'test_valmet_corrections.test_normal_pallet_many_lots_one_physical_place',
    'test_valmet_corrections.test_composition_limits_do_not_change_other_categories',
    'test_valmet_corrections.test_pending_multilot_pallet_identified_without_new_stock',
    'test_valmet_corrections.test_empty_boxes_lot_number_and_automatic_floor',
    'test_valmet_corrections.test_empty_box_required_identity_not_bypassed_by_pending',
    'test_valmet_corrections.test_unavailable_floor_rolls_back_entire_receipt',
    'test_valmet_corrections.test_database_enforces_empty_floor_and_deferred_lot',
    'test_valmet_corrections.test_old_draft_without_empty_box_lot_is_revalidated',
    'test_valmet_corrections.test_empty_boxes_concurrent_repeated_number',
    'test_valmet_corrections.test_corrected_boxes_http_permissions_and_search',
    'test_valmet_corrections.test_upgrade_preserves_confirmed_old_boxes_and_verified_backup',
}
REQUIRED_POSTGRES |= {
    'test_valmet_article.test_known_lot_without_article_available_by_category',
    'test_valmet_article.test_incomplete_lot_stays_physical_and_completion_needs_no_fake_article',
    'test_valmet_article.test_article_exception_does_not_relax_physical_identity_or_totals',
    'test_valmet_article.test_unknown_article_http_authentication_and_csrf',
    'test_valmet_article.test_article_migration_and_encrypted_recovery_preserve_history',
}
REQUIRED_POSTGRES |= {
    'test_valmet_planning.test_planning_multilot_physical_shortage_and_cancel',
    'test_valmet_planning.test_planning_grouped_partial_and_shared_location',
    'test_valmet_planning.test_planning_roll_separability_and_extras',
    'test_valmet_planning.test_planning_whole_boxes_extra_lots_and_article_null',
    'test_valmet_planning.test_planning_piece_box_optimization',
    'test_valmet_planning.test_planning_two_warehouses_never_share_reservations',
    'test_valmet_planning.test_planning_duplicate_confirmation_and_partial_race',
    'test_valmet_planning.test_planning_empty_boxes_and_reconciled_legacy_groups',
    'test_valmet_planning.test_planning_lab_partial_without_physical_debit',
    'test_valmet_planning.test_planning_vasilhame_unlocated_requires_two_validations',
    'test_valmet_planning.test_planning_edit_reservations_and_rollback',
    'test_valmet_planning.test_planning_concurrent_different_lots_same_box',
    'test_valmet_planning.test_planning_permissions_context_and_insufficient_identity',
    'test_valmet_planning.test_planning_manual_review_staleness_and_snapshot',
    'test_valmet_planning.test_planning_http_runtime_csrf_context_and_balances',
    'test_valmet_planning.test_planning_immutable_audit_and_database_overbooking',
    'test_valmet_planning.test_planning_backup_restore_reconciles_all_revisions',
    'test_valmet_planning.test_planning_ddn6_migration_preserves_every_legacy_byte',
}
REQUIRED_WINDOWS = {
    'test_private_file_security.test_windows_refuses_permissive_backup_file',
    'test_private_file_security.test_windows_refuses_moved_permissive_file',
    'test_private_file_security.test_windows_refuses_different_file_owner',
    'test_private_file_security.test_windows_refuses_different_folder_owner',
    'test_private_file_security.test_windows_accepts_only_safe_inheritance_and_private_temporary',
    'test_private_file_security.test_windows_refuses_junctions_and_hardlinked_files',
    'test_security_hardening.test_private_directory_restricts_inheritance_and_refuses_links',
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
    if required is None:
        required = REQUIRED_POSTGRES if backend=='postgres' else (REQUIRED_WINDOWS if os.name=='nt' else set())
    missing=sorted(required-set(executed))
    valid=bool(totals['passed']) and not totals['failed'] and not missing and (backend!='postgres' or not totals['skipped'])
    commit=os.getenv('GITHUB_SHA','')
    report=dict(backend=backend,commit=commit if re.fullmatch('[a-f0-9]{40}',commit) else None,
        junit_sha256=hashlib.sha256(raw).hexdigest(),counts=dict(totals),
        required_passed=sorted(required & set(executed)),missing_required=missing,
        original_critical_passed=sorted(ORIGINAL_POSTGRES & set(executed)) if backend=='postgres' else [],
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
        print(f"::notice title=DDN {args.backend} evidence::passed={result['counts'].get('passed',0)}; skipped={result['counts'].get('skipped',0)}; required={len(result['required_passed'])}; original={len(result['original_critical_passed'])}; valid={result['valid']}")
    if not result['valid']:raise SystemExit('Testes obrigatórios não comprovados; revisão recusada.')


if __name__=='__main__':main()
