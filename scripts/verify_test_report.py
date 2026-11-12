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
REQUIRED_POSTGRES |= {
    'test_valmet_shipping.test_shipping_without_pdf_or_individual_checks_and_repeated_confirmation',
    'test_valmet_shipping.test_shipping_partial_groups_multiple_loads_keep_remaining_reservations',
    'test_valmet_shipping.test_shipping_whole_multilot_units_only_once',
    'test_valmet_shipping.test_shipping_rolls_remainders_and_indivisibility',
    'test_valmet_shipping.test_shipping_lab_partial_keeps_pallet_and_unknown_saldo_block',
    'test_valmet_shipping.test_shipping_vasilhame_all_brands_no_pdf_or_destination',
    'test_valmet_shipping.test_shipping_piece_optimization_excess_actuals_and_close_partial',
    'test_valmet_shipping.test_shipping_pdf_is_optional_a4_immutable_and_current_revision',
    'test_valmet_shipping.test_shipping_missing_block_substitution_and_admin_resolution',
    'test_valmet_shipping.test_shipping_location_proof_splits_groups_and_preserves_original_entry',
    'test_valmet_shipping.test_shipping_location_capacity_failure_rolls_back_and_direct_observation',
    'test_valmet_shipping.test_shipping_concurrency_and_runtime_projection_protection',
    'test_valmet_shipping.test_shipping_invalid_refs_over_quantity_and_rollback',
    'test_valmet_shipping.test_shipping_backup_restores_ledger_positions_documents_and_history',
    'test_valmet_shipping.test_shipping_pdf_and_reference_rules_on_windows_sqlite_without_writes',
    'test_valmet_shipping.test_shipping_http_permissions_csrf_context_documents_and_history',
    'test_valmet_shipping.test_shipping_edit_after_collection_no_mandatory_pdf_and_real_shortage',
    'test_valmet_shipping.test_shipping_same_owner_other_warehouse_cannot_read_documents_or_debit',
    'test_valmet_shipping.test_shipping_migrate_ddn7_preserves_old_legacy_box_reservations_but_blocks_loading',
}
REQUIRED_POSTGRES |= {
    'test_valmet_shipping.test_shipping_definer_functions_refuse_temp_trigger_spoofing',
    'test_valmet_shipping.test_shipping_confirmed_contents_cannot_recreate_debited_stock',
    'test_valmet_shipping.test_shipping_races_new_reservation_without_resurrecting_original_quantity',
}
REQUIRED_POSTGRES |= {
    'test_valmet_shipping.test_shipping_lab_pallet_leaves_only_by_explicit_choice_and_number_never_reused',
}
REQUIRED_POSTGRES |= {
    'test_valmet_shipping.test_shipping_unknown_lab_return_location_preserves_block_and_idempotence',
}
REQUIRED_POSTGRES |= {
    'test_valmet_shipping.test_shipping_sqlite_refuses_every_operational_write',
}
REQUIRED_POSTGRES |= {
    'test_valmet_control.test_control_partial_zero_unknown_revisions_and_counts_do_not_change_stock',
    'test_valmet_control.test_control_complete_includes_floor_and_uncounted_locations_cannot_close',
    'test_valmet_control.test_control_lot_scope_preserves_other_materials_and_partial_location_proof',
    'test_valmet_control.test_control_category_counts_and_positive_or_content_corrections',
    'test_valmet_control.test_control_unknown_material_found_elsewhere_stays_pending_without_fake_identity',
    'test_valmet_control.test_control_lab_unknown_needs_explicit_complete_evidence_and_admin_clear',
    'test_valmet_control.test_control_transfer_partial_groups_atomic_capacity_and_history',
    'test_valmet_control.test_control_transfer_never_splits_individual_identity_even_separable_rolls',
    'test_valmet_control.test_control_reserved_ready_and_unresolved_occurrences_block_transfers_and_adjustments',
    'test_valmet_control.test_control_adjustment_admin_only_reason_identity_and_no_fictitious_lots',
    'test_valmet_control.test_control_proven_shipment_compensation_never_duplicates_or_rewrites',
    'test_valmet_control.test_control_inventory_stale_after_movements_partial_reconciliation_and_recount',
    'test_valmet_control.test_control_parsers_keep_unknown_and_zero_separate_without_postgres',
    'test_valmet_control.test_control_pdfs_keep_headers_unknowns_versions_and_physical_counts_on_sqlite',
    'test_valmet_control.test_control_sqlite_rejects_all_operational_commands',
    'test_valmet_control.test_control_capacity_floor_unknown_vasilhame_and_rollback',
    'test_valmet_control.test_control_occurrences_require_explicit_resolution_and_no_silent_reservations',
    'test_valmet_control.test_control_pending_identification_after_count_never_refills_original_stock',
    'test_valmet_control.test_control_concurrent_confirmation_never_doubles_movements_or_capacity',
    'test_valmet_control.test_control_simultaneous_inventories_reservation_aba_and_new_receipts',
    'test_valmet_control.test_control_failure_after_projection_rolls_back_entire_command',
    'test_valmet_control.test_control_pdf_archived_without_movements_backup_restores_all_contexts',
    'test_valmet_control.test_control_http_runtime_csrf_roles_context_forms_and_document_isolation',
    'test_valmet_control.test_control_runtime_guards_immutable_ledgers_and_definer_spoofing',
    'test_valmet_control.test_control_migrate_ddn8_preserves_shipments_reservations_documents_and_positions',
    'test_valmet_control.test_control_case_insensitive_lot_scope_and_multilot_pallet_counts_once',
    'test_valmet_control.test_control_lab_unknown_and_legacy_empty_boxes_cannot_become_available_or_transfer',
    'test_valmet_control.test_control_legacy_empty_box_identity_not_invented',
    'test_valmet_control.test_control_multiple_shipment_compensations_bounded_and_reserved_returns_blocked',
}
REQUIRED_POSTGRES.add('test_valmet_control.test_control_location_totals_cannot_hide_material_or_treat_blank_as_zero')
REQUIRED_WINDOWS = {
    'test_private_file_security.test_windows_refuses_permissive_backup_file',
    'test_private_file_security.test_windows_refuses_moved_permissive_file',
    'test_private_file_security.test_windows_refuses_different_file_owner',
    'test_private_file_security.test_windows_refuses_different_folder_owner',
    'test_private_file_security.test_windows_accepts_only_safe_inheritance_and_private_temporary',
    'test_private_file_security.test_windows_refuses_junctions_and_hardlinked_files',
    'test_security_hardening.test_private_directory_restricts_inheritance_and_refuses_links',
}

REQUIRED_WINDOWS.add('test_valmet_shipping.test_shipping_pdf_and_reference_rules_on_windows_sqlite_without_writes')
REQUIRED_WINDOWS.add('test_valmet_shipping.test_shipping_sqlite_refuses_every_operational_write')

REQUIRED_WINDOWS |= {
    'test_valmet_control.test_control_parsers_keep_unknown_and_zero_separate_without_postgres',
    'test_valmet_control.test_control_pdfs_keep_headers_unknowns_versions_and_physical_counts_on_sqlite',
    'test_valmet_control.test_control_sqlite_rejects_all_operational_commands',
}

REQUIRED_POSTGRES |= {
    'test_valmet_preview.test_grouping_repeated_lots_one_pallet_originals_and_divisibility',
    'test_valmet_preview.test_unmarked_rolls_not_divisible_and_contradictory_markings',
    'test_valmet_preview.test_numbered_multilot_units_keep_historic_numbers',
    'test_valmet_preview.test_blank_quantities_never_zero',
    'test_valmet_preview.test_ambiguous_invalid_quantities_block',
    'test_valmet_preview.test_identity_catalogue_capacity_context_and_no_mutation',
    'test_valmet_preview.test_contradictory_duplicates_and_missing_fields',
    'test_valmet_preview.test_safe_xlsx_read_only_and_formula_rejection',
    'test_valmet_preview.test_preview_never_uses_database_and_detects_capacity_and_missing_sheets',
    'test_valmet_preview.test_duplicate_numbers_across_categories_and_uppercase_sheet_names',
}
REQUIRED_WINDOWS |= {
    'test_valmet_preview.test_grouping_repeated_lots_one_pallet_originals_and_divisibility',
    'test_valmet_preview.test_unmarked_rolls_not_divisible_and_contradictory_markings',
    'test_valmet_preview.test_numbered_multilot_units_keep_historic_numbers',
    'test_valmet_preview.test_blank_quantities_never_zero',
    'test_valmet_preview.test_ambiguous_invalid_quantities_block',
    'test_valmet_preview.test_identity_catalogue_capacity_context_and_no_mutation',
    'test_valmet_preview.test_contradictory_duplicates_and_missing_fields',
    'test_valmet_preview.test_safe_xlsx_read_only_and_formula_rejection',
    'test_valmet_preview.test_preview_never_uses_database_and_detects_capacity_and_missing_sheets',
    'test_valmet_preview.test_duplicate_numbers_across_categories_and_uppercase_sheet_names',
}

REQUIRED_POSTGRES.add('test_valmet_preview.test_current_eight_layouts_mixed_rolls_and_legitimate_identical_rows')
REQUIRED_WINDOWS.add('test_valmet_preview.test_current_eight_layouts_mixed_rolls_and_legitimate_identical_rows')
REQUIRED_POSTGRES.add('test_valmet_preview.test_current_layout_xlsx_original_bytes_and_no_write')
REQUIRED_WINDOWS.add('test_valmet_preview.test_current_layout_xlsx_original_bytes_and_no_write')
REQUIRED_POSTGRES.add('test_valmet_preview.test_invalid_roll_markings_still_block')
REQUIRED_WINDOWS.add('test_valmet_preview.test_invalid_roll_markings_still_block')

REQUIRED_POSTGRES |= {
    'test_valmet_rolls.test_individual_rolls_select_bundles_and_release_last_pallet',
    'test_valmet_rolls.test_mixed_multilot_reserves_disjoint_ids_and_never_splits_nondivisible_set',
    'test_valmet_rolls.test_roll_selection_rejects_unrequested_rolls_outside_mandatory_bundle',
    'test_valmet_rolls.test_roll_selection_keeps_explicit_acceptance_of_mandatory_excess',
    'test_valmet_rolls.test_legacy_policy_never_creates_individual_identity_and_proof_is_required',
    'test_valmet_rolls.test_rolls_concurrent_reservations_and_idempotent_shipments',
    'test_valmet_rolls.test_rolls_partial_loading_explicit_ids_cancellation_and_rollback',
    'test_valmet_rolls.test_rolls_inventory_zero_unknown_transfer_adjustment_and_returns',
    'test_valmet_rolls.test_rolls_runtime_projection_immutable_permissions_context_and_archived_documents',
    'test_valmet_rolls.test_rolls_backup_restore_preserves_ids_classes_holds_and_pdf',
    'test_valmet_rolls.test_rolls_ddn9_upgrade_never_backfills_legacy_or_changes_pdfs',
    'test_valmet_rolls.test_rolls_admin_historical_difference_reconciliation_and_no_fake_ids',
    'test_valmet_rolls.test_rolls_parsers_and_sqlite_never_operate_without_postgres',
}
REQUIRED_WINDOWS.add('test_valmet_rolls.test_rolls_parsers_and_sqlite_never_operate_without_postgres')

REQUIRED_POSTGRES.add('test_valmet_rolls.test_rolls_stock_article_search_uses_remaining_identities_not_original_totals')

REQUIRED_POSTGRES.add('test_valmet_rolls.test_rolls_postgres_rejects_forged_partial_bundle_and_temp_writer_trigger')

REQUIRED_POSTGRES |= {
    'test_valmet_import.test_historical_import_all_layouts_official_stock_rolls_and_idempotency',
    'test_valmet_import.test_historical_import_blocking_unknowns_and_catalogue',
    'test_valmet_import.test_historical_import_reconciliation_provenance_unknown_not_zero',
    'test_valmet_import.test_historical_import_stale_reservations_failure_rollback_and_concurrency',
    'test_valmet_import.test_historical_import_permissions_runtime_isolation_csrf_and_books',
    'test_valmet_import.test_historical_import_backup_restore_and_altered_file_no_second_baseline',
    'test_valmet_import.test_historical_import_multilot_unknown_presence_and_existing_identity_cannot_be_renamed',
    'test_valmet_import.test_historical_import_integrates_inventory_transfer_adjustment_and_archived_pdf',
    'test_valmet_import.test_historical_import_ddn10_upgrade_preserves_existing_stock_reservations_and_pdf',
    'test_valmet_import.test_historical_upload_parser_bounds_and_sqlite_never_imports',
    'test_valmet_import.test_historical_invalid_decisions_never_write_stock',
    'test_valmet_import.test_historical_latest_revision_digest_and_capacity_change_are_revalidated',
}
REQUIRED_WINDOWS.add('test_valmet_import.test_historical_upload_parser_bounds_and_sqlite_never_imports')

REQUIRED_POSTGRES |= {'test_valmet_integrated.test_integrated_valmet_office_lifecycle_and_reconciled_recovery',
    'test_valmet_integrated.test_integrated_valmet_failed_shipment_does_not_change_restorable_books'}

REQUIRED_POSTGRES |= {
    'test_nkt.test_nkt_individual_identity_review_history_capacity_and_never_reuse',
    'test_nkt.test_nkt_invalid_identity_never_becomes_stock',
    'test_nkt.test_nkt_concurrent_identity_capacity_and_atomic_rollback',
    'test_nkt.test_nkt_owner_wide_identity_contexts_and_failed_confirmation',
    'test_nkt.test_nkt_server_permissions_csrf_context_and_revision',
    'test_nkt.test_nkt_backup_restore_and_prior_head_upgrade',
    'test_nkt.test_nkt_sqlite_writes_disabled_and_original_text_preserved',
}
REQUIRED_WINDOWS.add('test_nkt.test_nkt_sqlite_writes_disabled_and_original_text_preserved')

REQUIRED_POSTGRES |= {
    'test_nkt_receiving.test_nkt_excel_explicit_containers_preserve_all_pegs_and_cq',
    'test_nkt_receiving.test_nkt_invalid_ranges_never_invent_identifiers',
    'test_nkt_receiving.test_nkt_excel_ambiguous_missing_material_numeric_peg_and_formulas',
    'test_nkt_receiving.test_nkt_excel_split_requires_explicit_full_distribution_and_cq_exclusion',
    'test_nkt_receiving.test_nkt_containers_atomic_entries_material_dates_and_numbering',
    'test_nkt_receiving.test_nkt_receiving_review_changes_missing_date_and_duplicate_cases',
    'test_nkt_receiving.test_nkt_receiving_concurrent_batches_and_midway_rollback',
    'test_nkt_receiving.test_nkt_receiving_runtime_permissions_http_csrf_and_wrong_context',
    'test_nkt_receiving.test_nkt_receiving_encrypted_restore_and_prior_head_upgrade',
    'test_nkt_receiving.test_nkt_excel_zip_xml_layout_limits_and_sqlite_never_receive',
    'test_nkt_receiving.test_nkt_repeated_concurrent_files_altered_listing_and_source_immutability',
    'test_nkt_receiving.test_nkt_upload_parsing_never_holds_writer_lock',
    'test_nkt_receiving.test_nkt_manual_mixed_materials_pre2027_and_floor_catalogue',
}
REQUIRED_WINDOWS |= {
    'test_nkt_receiving.test_nkt_excel_explicit_containers_preserve_all_pegs_and_cq',
    'test_nkt_receiving.test_nkt_invalid_ranges_never_invent_identifiers',
    'test_nkt_receiving.test_nkt_excel_ambiguous_missing_material_numeric_peg_and_formulas',
    'test_nkt_receiving.test_nkt_excel_split_requires_explicit_full_distribution_and_cq_exclusion',
    'test_nkt_receiving.test_nkt_excel_zip_xml_layout_limits_and_sqlite_never_receive',
}

REQUIRED_POSTGRES |= {
    'test_nkt_loading.test_nkt_explicit_selection_reservations_and_no_stock_deduction',
    'test_nkt_loading.test_nkt_reservations_concurrent_case_duplicates',
    'test_nkt_loading.test_nkt_schedule_revision_preserves_pegs_holds_and_immutable_pdf',
    'test_nkt_loading.test_nkt_partial_shipments_distinct_dates_gt_and_idempotence',
    'test_nkt_loading.test_nkt_partial_cancellation_releases_only_unshipped_and_reprint',
    'test_nkt_loading.test_nkt_administrative_compensation_and_gt_correction_preserve_original',
    'test_nkt_loading.test_nkt_correction_revalidates_capacity_and_later_operations',
    'test_nkt_loading.test_nkt_loading_atomic_failure_stale_versions_and_concurrent_confirmation',
    'test_nkt_loading.test_nkt_loading_http_permissions_csrf_context_and_pdf_reprints',
    'test_nkt_loading.test_nkt_loading_prior_head_upgrade_encrypted_restore_and_original_metadata',
    'test_nkt_loading.test_nkt_loading_sqlite_never_writes_and_pdf_keeps_unknown_material',
    'test_nkt_loading.test_nkt_loading_direct_runtime_cannot_forge_reservations_or_capacity',
    'test_nkt_loading.test_nkt_loading_validation_and_draft_cancel_keep_identity',
}
REQUIRED_WINDOWS.add('test_nkt_loading.test_nkt_loading_sqlite_never_writes_and_pdf_keeps_unknown_material')

REQUIRED_POSTGRES |= {'test_nkt_loading.test_nkt_a4_one_load_per_page_and_no_silent_text_truncation','test_nkt_loading.test_nkt_cancel_pending_revision_then_compensate_without_reviving_reservations'}
REQUIRED_WINDOWS.add('test_nkt_loading.test_nkt_a4_one_load_per_page_and_no_silent_text_truncation')

REQUIRED_POSTGRES |= {
    'test_nkt_inventory.test_nkt_inventory_load_conference_and_atomic_missing',
    'test_nkt_inventory.test_nkt_inventory_full_suffix_resolves_one_peg_entire_load_and_ambiguity',
    'test_nkt_inventory.test_nkt_inventory_partial_unverified_is_not_missing_and_outside_scope_untouched',
    'test_nkt_inventory.test_nkt_inventory_reserved_loads_excluded_wholly_even_when_partially_reserved',
    'test_nkt_inventory.test_nkt_inventory_rejects_reserve_cancel_net_zero_movement_after_conference',
    'test_nkt_inventory.test_nkt_inventory_complete_refuses_new_arrivals_and_shipments_need_reconference',
    'test_nkt_inventory.test_nkt_inventory_revisions_unknown_material_and_legacy_loads_remain_blocked',
    'test_nkt_inventory.test_nkt_inventory_found_load_restored_without_new_identity_and_double_restore',
    'test_nkt_inventory.test_nkt_inventory_partial_recovery_and_reserved_load_or_later_movements_block',
    'test_nkt_inventory.test_nkt_inventory_concurrent_confirmations_rollback_and_stale_other_inventory',
    'test_nkt_inventory.test_nkt_inventory_runtime_immutable_permission_csrf_isolation_and_pdfs',
    'test_nkt_inventory.test_nkt_inventory_runtime_direct_cannot_discount_reserved_or_forge_restore',
    'test_nkt_inventory.test_nkt_inventory_ddn14_upgrade_native_archive_and_encrypted_recovery',
    'test_nkt_inventory.test_nkt_inventory_a4_pdf_unknown_and_zero_vs_unverified_and_sqlite_refusal',
    'test_nkt_inventory.test_nkt_inventory_recovery_revalidates_capacity_and_unknown_peg_is_pending',
    'test_nkt_inventory.test_nkt_inventory_nkt_new_preparation_form_remains_usable',
    'test_nkt_inventory.test_nkt_inventory_four_character_full_peg_requires_explicit_disambiguation',
}
REQUIRED_WINDOWS.add('test_nkt_inventory.test_nkt_inventory_a4_pdf_unknown_and_zero_vs_unverified_and_sqlite_refusal')
REQUIRED_POSTGRES |= {
    'test_nkt_integrated.test_integrated_nkt_receipt_loading_inventory_compensation_and_encrypted_recovery',
    'test_nkt_integrated.test_integrated_nkt_inventory_failure_preserves_shipments_reservations_and_documents',
    'test_nkt_integrated.test_integrated_nkt_inventory_and_reservation_race_never_adjusts_reserved_load',
}

REQUIRED_POSTGRES |= {
    'test_alcobre.test_alcobre_original_pdf_survives_importers_role_change',
    'test_alcobre.test_alcobre_admin_completes_inactive_authors_draft_without_reassigning_history',
    'test_alcobre.test_alcobre_upgrade_refuses_legacy_grouped_stock_without_hiding_or_converting_it',
    'test_alcobre.test_alcobre_pdf_never_reads_right_aligned_neighbor_as_bobine',
    'test_alcobre.test_alcobre_pdf_admission_limit_blocks_without_starting_another_parser',
    'test_alcobre.test_alcobre_pdf_column_only_original_numbers_and_sqlite_refusal',
    'test_alcobre.test_alcobre_pdf_ambiguous_layouts_duplicates_and_malformed_never_automatic',
    'test_alcobre.test_alcobre_pdf_direct_putaway_preserves_one_identity_and_source',
    'test_alcobre.test_alcobre_partial_identification_conserves_physical_pending_and_known_locations',
    'test_alcobre.test_alcobre_concurrent_confirmation_and_pending_reference_are_atomic',
    'test_alcobre.test_alcobre_invalid_numbers_capacity_zone_and_context_never_create_stock',
    'test_alcobre.test_alcobre_confirmation_rollback_and_runtime_immutable_books',
    'test_alcobre.test_alcobre_pdf_unreliable_corrections_and_repeated_numbers_require_proof',
    'test_alcobre.test_alcobre_http_upload_permissions_csrf_metadata_and_context',
    'test_alcobre.test_alcobre_ddn15_upgrade_encrypted_restore_and_original_histories',
    'test_alcobre.test_alcobre_administrator_configures_zones_and_corrects_only_empty_positions',
    'test_alcobre.test_alcobre_unknown_material_requires_proved_positive_whole_quantity',
    'test_alcobre.test_alcobre_pdf_parsing_releases_lock_and_revalidates_actor',
    'test_alcobre.test_alcobre_runtime_refuses_null_or_excess_identification_review',
}
REQUIRED_WINDOWS |= {
    'test_alcobre.test_alcobre_pdf_never_reads_right_aligned_neighbor_as_bobine',
    'test_alcobre.test_alcobre_pdf_admission_limit_blocks_without_starting_another_parser',
    'test_alcobre.test_alcobre_pdf_column_only_original_numbers_and_sqlite_refusal',
    'test_alcobre.test_alcobre_pdf_ambiguous_layouts_duplicates_and_malformed_never_automatic',
}

REQUIRED_POSTGRES |= {
    'test_alcobre_dispatch.test_alcobre_outgoing_mixed_guide_reserves_only_exact_available_and_confirms_once',
    'test_alcobre_dispatch.test_alcobre_departure_reuses_position_keeps_original_identity_and_location',
    'test_alcobre_dispatch.test_alcobre_uncollected_bobine_is_exception_blocked_not_shipped_or_free',
    'test_alcobre_dispatch.test_alcobre_cancel_before_expedition_releases_reservations_preserves_books',
    'test_alcobre_dispatch.test_alcobre_two_guides_concurrent_reservation_never_overlap',
    'test_alcobre_dispatch.test_alcobre_revision_releases_only_removed_numbers_and_archives_old_pdf',
    'test_alcobre_dispatch.test_alcobre_stock_changes_between_review_prepare_and_confirmation_require_revision',
    'test_alcobre_dispatch.test_alcobre_full_confirmation_rollback_and_retry',
    'test_alcobre_dispatch.test_alcobre_duplicate_and_ambiguous_pdf_cannot_be_prepared_silently',
    'test_alcobre_dispatch.test_alcobre_pdf_archive_navigation_permissions_csrf_and_context',
    'test_alcobre_dispatch.test_alcobre_runtime_cannot_forge_expedition_or_incomplete_preparation',
    'test_alcobre_dispatch.test_alcobre_a4_full_rows_totals_and_sqlite_operational_refusal',
    'test_alcobre_dispatch.test_alcobre_ddn16_upgrade_encrypted_restore_and_guide_history',
    'test_alcobre_dispatch.test_alcobre_complete_sem_stock_guide_confirms_exceptions_without_events',
    'test_alcobre_dispatch.test_alcobre_concurrent_whole_guide_confirmations_discount_once',
    'test_alcobre_dispatch.test_alcobre_confirmation_cannot_repoint_active_revision',
}
REQUIRED_POSTGRES |= {
    'test_alcobre_dispatch.test_alcobre_noncollected_present_releases_only_reservation_preserves_position_and_history',
    'test_alcobre_dispatch.test_alcobre_noncollected_requires_explicit_classification_physical_proof_and_current_location',
    'test_alcobre_dispatch.test_alcobre_runtime_rejects_unproved_release_and_forged_classification',
    'test_alcobre_dispatch.test_alcobre_confirm_present_concurrent_with_other_guide_never_silently_reassigns',
    'test_alcobre_dispatch.test_alcobre_http_whole_guide_requires_classification_and_explicit_presence',
}
REQUIRED_WINDOWS.add('test_alcobre_dispatch.test_alcobre_a4_full_rows_totals_and_sqlite_operational_refusal')

REQUIRED_POSTGRES |= {
    'test_alcobre_inventory.test_alcobre_inventory_missing_recovery_and_position_correction_keep_identity',
    'test_alcobre_inventory.test_alcobre_inventory_blocked_found_and_stock_dispatch_use_corrected_location',
    'test_alcobre_inventory.test_alcobre_inventory_complete_unverified_empty_pending_are_distinct',
    'test_alcobre_inventory.test_alcobre_inventory_unknown_incident_does_not_create_stock_and_blocks_location',
    'test_alcobre_inventory.test_alcobre_inventory_reserved_or_shipped_never_adjusted_and_stale_counts_block',
    'test_alcobre_inventory.test_alcobre_inventory_pdf_sqlite_is_read_only_and_absence_not_unknown',
    'test_alcobre_inventory.test_alcobre_inventory_concurrent_confirmations_and_conflicting_inventories',
    'test_alcobre_inventory.test_alcobre_inventory_movements_after_count_require_new_conference',
    'test_alcobre_inventory.test_alcobre_inventory_failure_rolls_back_event_location_document_and_audit',
    'test_alcobre_inventory.test_alcobre_inventory_permissions_csrf_context_and_archived_pdf',
    'test_alcobre_inventory.test_alcobre_inventory_sql_runtime_rejects_forged_corrections',
    'test_alcobre_inventory.test_alcobre_inventory_empty_unknown_and_contradictory_counts_do_not_become_stock',
    'test_alcobre_inventory.test_alcobre_inventory_operator_can_count_own_but_not_approve',
    'test_alcobre_inventory.test_alcobre_inventory_ddn17_upgrade_and_encrypted_recovery_preserve_guide_books',
}
REQUIRED_POSTGRES |= {'test_alcobre_inventory.test_alcobre_inventory_incident_after_reservation_blocks_shipping_until_decision','test_alcobre_inventory.test_alcobre_inventory_concurrent_counts_use_versions_without_lost_history'}
REQUIRED_POSTGRES |= {'test_alcobre_inventory.test_alcobre_inventory_empty_count_invalid_after_unknown_resolved_elsewhere','test_alcobre_inventory.test_alcobre_inventory_catalog_change_shows_blocking_without_unsafe_confirmation'}
REQUIRED_POSTGRES.add('test_alcobre_inventory.test_alcobre_inventory_unlabelled_found_material_never_gets_invented_identity')
REQUIRED_WINDOWS.add('test_alcobre_inventory.test_alcobre_inventory_pdf_sqlite_is_read_only_and_absence_not_unknown')

REQUIRED_POSTGRES |= {
    'test_iberbag_stacked.test_stacked_pure_flags_pdf_and_sqlite_write_guard',
    'test_iberbag_stacked.test_stacked_partial_putaway_separate_balances_documents_and_idempotence',
    'test_iberbag_stacked.test_stacked_floor_only_and_idempotent_key_payload',
    'test_iberbag_stacked.test_stacked_fifo_each_type_unknown_dates_and_no_fallback',
    'test_iberbag_stacked.test_stacked_shortage_changed_after_review_rollback_and_concurrent_preparations',
    'test_iberbag_stacked.test_stacked_pdf_failure_rolls_back_stock_confirmation_and_archive',
    'test_iberbag_stacked.test_stacked_inventory_preserves_type_admin_adjustment_and_transfer_guards',
    'test_iberbag_stacked.test_stacked_inventory_stale_reservation_and_compensation_type',
    'test_iberbag_stacked.test_stacked_restricted_runtime_sql_guards_context_permissions_and_csrf',
    'test_iberbag_stacked.test_stacked_restricted_runtime_rejects_cross_type_pick_and_adjustment',
    'test_iberbag_stacked.test_stacked_upgrade18_encrypted_backup_restore_and_historical_documents',
    'test_iberbag_stacked.test_stacked_concurrent_mixed_putaway_never_exceeds_guide_units',
    'test_iberbag_stacked.test_stacked_mixed_lines_and_typed_reservations_do_not_mix',
    'test_iberbag_stacked.test_stacked_new_office_review_blocks_legacy_shortage_without_losing_pendentes',
    'test_iberbag_stacked.test_stacked_inventory_cannot_invent_pair_type_from_normal_stock',
    'test_iberbag_stacked.test_stacked_inventory_never_converts_pairs_into_normal_units',
    'test_iberbag_stacked.test_stacked_restricted_runtime_forbids_balanced_type_conversion',
    'test_iberbag_stacked.test_stacked_independent_positive_adjustment_missing_and_proven_pair_recovery',
}
REQUIRED_WINDOWS.add('test_iberbag_stacked.test_stacked_pure_flags_pdf_and_sqlite_write_guard')
REQUIRED_POSTGRES |= {
    'test_alcobre_integrated.test_integrated_alcobre_receipts_dispatch_inventory_and_full_encrypted_recovery',
    'test_alcobre_integrated.test_integrated_alcobre_review_stale_after_location_correction_or_receipt_putaway',
    'test_alcobre_integrated.test_integrated_alcobre_failures_between_ledger_document_and_audit_are_atomic',
    'test_alcobre_integrated.test_integrated_alcobre_a4_inventory_keeps_every_long_number_and_unknown_quantity',
    'test_alcobre_integrated.test_integrated_alcobre_two_authenticated_users_cannot_adjust_a_reserved_bobine',
}
REQUIRED_WINDOWS.add('test_alcobre_integrated.test_integrated_alcobre_a4_inventory_keeps_every_long_number_and_unknown_quantity')

REQUIRED_POSTGRES |= {
    'test_shared_locations.test_shared_activation_is_opt_in_one_identity_and_preserves_claim_ids',
    'test_shared_locations.test_shared_seed_identities_use_resolved_contexts_and_reject_name_impostors',
    'test_shared_locations.test_shared_pending_putaway_cannot_be_double_assigned_and_admin_cancel_requires_proof',
    'test_shared_locations.test_shared_accounting_dispatch_does_not_release_then_physical_completion_allows_cosmopak',
    'test_shared_locations.test_shared_concurrent_owners_exactly_one_pending_assignment',
    'test_shared_locations.test_shared_activation_rejects_unreconciled_catalogue_and_stale_review',
    'test_shared_locations.test_shared_inventory_missing_and_recovery_do_not_overwrite_foreign_occupancy',
    'test_shared_locations.test_shared_transfer_releases_only_confirmed_source_and_keeps_both_contexts_isolated',
    'test_shared_locations.test_shared_sql_projection_and_events_are_protected_and_forms_enforce_admin_csrf',
    'test_shared_locations.test_shared_rollback_does_not_leave_pending_claim_or_partial_stock',
    'test_shared_locations.test_shared_sqlite_never_activates_operational_sharing',
    'test_shared_locations.test_shared_migration19_and_encrypted_restore_preserve_history_and_pending_occupancy',
    'test_shared_locations.test_shared_inventory_compensation_keeps_physical_position_and_original_pdf',
    'test_shared_locations.test_shared_completed_order_replay_cannot_release_another_pallet',
    'test_shared_locations.test_shared_restored_function_acl_cannot_use_temporary_writer_trigger',
    'test_shared_locations.test_shared_runtime_concurrent_assignment_and_physical_release_rollback',
    'test_shared_locations.test_shared_snapshot_rejects_divergent_projection_or_double_physical_stock',
}
REQUIRED_WINDOWS.add('test_shared_locations.test_shared_sqlite_never_activates_operational_sharing')

REQUIRED_POSTGRES |= {
    'test_cosmopak.test_cosmo_pending_identity_never_matches_a_legacy_putaway_with_same_id',
    'test_cosmopak.test_cosmo_runtime_cannot_commit_stock_before_integral_approval',
    'test_cosmopak.test_cosmo_restored_function_acls_cannot_forge_projection_writers',
    'test_cosmopak.test_cosmo_generic_divergence_without_box_difference_still_blocks_until_decision',
    'test_cosmopak.test_cosmo_numeric_precision_and_repeated_huge_strings_are_rejected',
    'test_cosmopak.test_cosmo_excel_selected_sheet_box_counts_and_original_identity',
    'test_cosmopak.test_cosmo_excel_missing_ambiguous_and_invalid_quantities',
    'test_cosmopak.test_cosmo_excel_formula_xml_zip_limits_and_sqlite_write_prohibition',
    'test_cosmopak.test_cosmo_planned_pallets_individual_partial_stock_and_atomic_approval',
    'test_cosmopak.test_cosmo_missing_and_divergent_boxes_need_explicit_decisions',
    'test_cosmopak.test_cosmo_correction_requires_physical_vacancy_and_missing_never_releases',
    'test_cosmopak.test_cosmo_concurrent_owners_or_trucks_get_one_position',
    'test_cosmopak.test_cosmo_approval_rollback_versions_numbering_and_multiple_trucks',
    'test_cosmopak.test_cosmo_revisions_replays_and_context_permissions',
    'test_cosmopak.test_cosmo_runtime_http_csrf_authorization_and_projection_guards',
    'test_cosmopak.test_cosmo_migration20_and_encrypted_restore_preserve_all_owners_and_pending',
    'test_cosmopak.test_cosmo_approval_concurrency_is_idempotent_and_invalid_previews_never_stock',
    'test_cosmopak.test_cosmo_iberbag_accounting_release_waits_for_physical_completion',
    'test_cosmopak.test_cosmo_all_missing_and_historical_reference_never_invent_stock_or_dates',
    'test_cosmopak.test_cosmo_location_correction_preserves_observed_boxes_and_conflict_rolls_back',
}
REQUIRED_WINDOWS |= {
    'test_cosmopak.test_cosmo_numeric_precision_and_repeated_huge_strings_are_rejected',
    'test_cosmopak.test_cosmo_excel_selected_sheet_box_counts_and_original_identity',
    'test_cosmopak.test_cosmo_excel_missing_ambiguous_and_invalid_quantities',
    'test_cosmopak.test_cosmo_excel_formula_xml_zip_limits_and_sqlite_write_prohibition',
}

REQUIRED_POSTGRES |= {'test_cosmopak_packings.test_packing_runtime_http_permissions_csrf_isolation_and_archive', 'test_cosmopak_packings.test_packing_partial_releases_reservation_not_physical_presence_and_regroups', 'test_cosmopak_packings.test_packing_excel_numeric_identifiers_safe_formats_and_limits', 'test_cosmopak_packings.test_packing_sql_rejects_forged_source_multiplicity_and_temporary_writers', 'test_cosmopak_packings.test_packing_concurrent_exclusive_reservations_and_stale_flags', 'test_cosmopak_packings.test_packing_missing_exact_boxes_no_pooling_and_returns_require_free_rack', 'test_cosmopak_packings.test_packing_rollback_between_movement_output_audit_and_commit', 'test_cosmopak_packings.test_packing_documents_frozen_and_encrypted_restore_reconciles_all_books', 'test_cosmopak_packings.test_packing_migration21_preserves_identity_stock_and_verified_recovery', 'test_cosmopak_packings.test_packing_equivalent_substitution_is_explicit_scoped_and_unreserved', 'test_cosmopak_packings.test_packing_toggles_reopen_and_physical_returns_never_invent_movements', 'test_cosmopak_packings.test_packing_a4_complete_report_and_sqlite_refuses_commands', 'test_cosmopak_packings.test_packing_receipt_reservation_stage_load_and_office_dispatch', 'test_cosmopak_packings.test_packing_explicit_mapping_preserves_order_filters_and_blocks_ambiguity'}
REQUIRED_WINDOWS |= {'test_cosmopak_packings.test_packing_a4_complete_report_and_sqlite_refuses_commands', 'test_cosmopak_packings.test_packing_excel_numeric_identifiers_safe_formats_and_limits', 'test_cosmopak_packings.test_packing_explicit_mapping_preserves_order_filters_and_blocks_ambiguity'}

REQUIRED_POSTGRES.add('test_cosmopak_packings.test_packing_two_operators_complete_distinct_rows_and_dispatch_failure_rolls_back')

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
    parser.add_argument('--selection',type=Path,help='Explicit partial PostgreSQL proof; requires final aggregate gate.')
    args=parser.parse_args()
    try:
        if args.selection and args.backend!='postgres':raise ValueError('Partição apenas PostgreSQL.')
        result=verify(args.report,args.backend,required=set() if args.selection else None)
        if args.selection:
            from scripts.verify_postgres_shards import validate_selection
            selection=json.loads(args.selection.read_text(encoding='utf-8'))
            validate_selection(selection,result['counts'].get('passed',0))
            result.update(report_scope='shard',selection=selection)
    except (OSError,ValueError,TypeError,ET.ParseError):
        raise SystemExit('Relatório ausente ou inválido; execução não comprovada.')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('backend','counts','missing_required','valid')}))
    if os.getenv('GITHUB_ACTIONS')=='true':
        for name in result['missing_required']:
            print(f'::error title=DDN required test not proved::{name}')
        scope=' shard' if args.selection else ''
        print(f"::notice title=DDN {args.backend}{scope} evidence::passed={result['counts'].get('passed',0)}; skipped={result['counts'].get('skipped',0)}; required={len(result['required_passed'])}; original={len(result['original_critical_passed'])}; valid={result['valid']}")
    if not result['valid']:raise SystemExit('Testes obrigatórios não comprovados; revisão recusada.')


if __name__=='__main__':main()
