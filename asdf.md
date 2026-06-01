# Audit report

**Totals — blocking: 0 · high: 9 · medium: 67 · low: 668**

## Files with findings

| File | Role | Blocking | High | Medium | Low |
| --- | --- | --- | --- | --- | --- |
| `cyclone/utils/data_splitting.py` | production | 0 | 0 | 2 | 43 |
| `cyclone/database/typed_labels.py` | production | 0 | 0 | 1 | 37 |
| `cyclone/label_resolution.py` | production | 0 | 0 | 1 | 26 |
| `tools/worker_contracts.py` | production | 0 | 0 | 22 | 5 |
| `cyclone/database/migrations/spans/labelset_ops.py` | production | 0 | 0 | 1 | 24 |
| `cyclone/database/queries/labels.py` | production | 0 | 0 | 5 | 16 |
| `cyclone/services/csv_processing.py` | production | 0 | 2 | 0 | 13 |
| `alembic/manual_migrations/populate_ds_type_default_datacolumn.py` | script | 0 | 0 | 0 | 13 |
| `alembic/manual_migrations/label_spans.py` | script | 0 | 0 | 0 | 11 |
| `cyclone/database/migrations/spans/datacolumn_ops.py` | production | 0 | 0 | 0 | 10 |
| `tests/utils/test_demux.py` | test | 0 | 0 | 1 | 8 |
| `tests/utils/test_span_source_loader.py` | test | 0 | 0 | 0 | 9 |
| `cyclone/database/labels.py` | production | 0 | 0 | 1 | 7 |
| `cyclone/worker_contracts.py` | production | 0 | 0 | 8 | 0 |
| `cyclone/celery_tasks/export_task.py` | production | 0 | 0 | 0 | 7 |
| `cyclone/database/datafiles.py` | production | 0 | 0 | 0 | 7 |
| `cyclone/database/utils.py` | production | 0 | 0 | 4 | 3 |
| `alembic/versions_legacy/c09a8fe14dee_add_reviewer_permissions.py` | production | 0 | 2 | 0 | 4 |
| `scripts/export.py` | script | 0 | 0 | 1 | 5 |
| `tests/celery_tasks/file_pipeline/test_process.py` | test | 0 | 0 | 0 | 6 |
| `alembic/manual_migrations/populate_target_names.py` | script | 0 | 0 | 0 | 5 |
| `alembic/manual_migrations/span_migration.py` | script | 0 | 0 | 0 | 5 |
| `cyclone/celery_tasks/file_pipeline/process.py` | production | 0 | 0 | 0 | 5 |
| `cyclone/celery_tasks/workflows/load_data/muxation.py` | production | 0 | 1 | 2 | 2 |
| `cyclone/celery_tasks/workflows/load_data/prediction_builder.py` | production | 0 | 0 | 0 | 5 |
| `cyclone/database/queries/target_names.py` | production | 0 | 0 | 0 | 5 |
| `cyclone/services/labelsets.py` | production | 0 | 0 | 0 | 5 |
| `tests/celery_tasks/workflows/test_load_data.py` | test | 0 | 0 | 0 | 5 |
| `tests/conftest.py` | test_support | 0 | 0 | 4 | 1 |
| `alembic/manual_migrations/datarow.py` | script | 0 | 0 | 0 | 4 |
| `alembic/manual_migrations/example_audit_migration.py` | script | 0 | 0 | 0 | 4 |
| `alembic/manual_migrations/find_bad_span_ds.py` | script | 0 | 2 | 0 | 2 |
| `alembic/versions_legacy/0ac73f2fabc3_xlsm_xlsb_file_types.py` | production | 0 | 0 | 0 | 4 |
| `alembic/versions_legacy/1372cc725083_add_empty_password_protected_failuretype.py` | production | 0 | 0 | 0 | 4 |
| `alembic/versions_legacy/151933fc0092_add_labelset_tasktype_rationalized_.py` | production | 0 | 0 | 0 | 4 |
| `alembic/versions_legacy/208cd1005ec8_add_new_filetypes.py` | production | 0 | 0 | 0 | 4 |
| `alembic/versions_legacy/21546ee7a1f4_add_limit_failures.py` | production | 0 | 0 | 0 | 4 |
| `alembic/versions_legacy/26912a096f60_image_filetypes.py` | production | 0 | 0 | 0 | 4 |
| `alembic/versions_legacy/284776505ea5_add_csv_filefailuretypes.py` | production | 0 | 0 | 0 | 4 |
| `alembic/versions_legacy/4625fee6fca6_add_staged_status.py` | production | 0 | 0 | 0 | 4 |
| `alembic/versions_legacy/4ec4f6fc5054_add_genai_task_and_model_type.py` | production | 0 | 0 | 0 | 4 |
| `alembic/versions_legacy/58dfe0fecd92_add_genai_classification.py` | production | 0 | 0 | 0 | 4 |
| `alembic/versions_legacy/62d381354744_add_unknown_filetype_enum.py` | production | 0 | 0 | 0 | 4 |
| `alembic/versions_legacy/67cace5e7a2c_add_new_file_types.py` | production | 0 | 0 | 0 | 4 |
| `alembic/versions_legacy/68203aa36035_add_summarization_task_type_and_.py` | production | 0 | 0 | 0 | 4 |
| `alembic/versions_legacy/72e0dc61b71b_add_labelset_status_poll_for_updates.py` | production | 0 | 0 | 0 | 4 |
| `alembic/versions_legacy/83b234dc54b1_add_object_detection_task_type.py` | production | 0 | 0 | 0 | 4 |
| `alembic/versions_legacy/90edd6ea1dec_add_standard_v2.py` | production | 0 | 0 | 0 | 4 |
| `alembic/versions_legacy/95b38783aa48_add_processed_status.py` | production | 0 | 0 | 0 | 4 |
| `alembic/versions_legacy/a523f41ed78b_upload_labelset_type.py` | production | 0 | 0 | 0 | 4 |
| `alembic/versions_legacy/a91072f40ec9_add_form_extraction_enum.py` | production | 0 | 0 | 0 | 4 |
| `alembic/versions_legacy/ab375d7e7897_adding_eml_and_msg_file_types.py` | production | 0 | 0 | 0 | 4 |
| `alembic/versions_legacy/bd8091e76e50_datafile_columns_dataset_type.py` | production | 0 | 0 | 0 | 4 |
| `alembic/versions_legacy/f11454b07f01_spans.py` | production | 0 | 0 | 0 | 4 |
| `alembic/versions_legacy/fc8747d855fb_add_pptx_filetypes.py` | production | 0 | 0 | 0 | 4 |
| `cyclone/celery_tasks/workflows/copy_labels.py` | production | 0 | 0 | 0 | 4 |
| `cyclone/database/queries/datasets.py` | production | 0 | 0 | 1 | 3 |
| `cyclone/database/queries/datasetusers.py` | production | 0 | 0 | 0 | 4 |
| `cyclone/services/file_processing.py` | production | 0 | 0 | 0 | 4 |
| `cyclone/utils/type_inference.py` | production | 0 | 0 | 1 | 3 |
| `scripts/copy_perms.py` | script | 0 | 0 | 0 | 4 |
| `tests/celery_tasks/workflows/test_copy_labels.py` | test | 0 | 0 | 0 | 4 |
| `tests/routes/labelset/test_create_and_list_labelsets.py` | test | 0 | 0 | 3 | 1 |
| `alembic/env.py` | production | 0 | 0 | 0 | 3 |
| `alembic/manual_migrations/delete_dataset.py` | script | 0 | 0 | 0 | 3 |
| `alembic/manual_migrations/fix_active_labels.py` | script | 0 | 0 | 0 | 3 |
| `alembic/manual_migrations/perms_migration.py` | script | 0 | 0 | 0 | 3 |
| `alembic/manual_migrations/populate_subset_rowids.py` | script | 0 | 0 | 0 | 3 |
| `alembic/versions/0d2a9c81277c_init.py` | production | 0 | 0 | 0 | 3 |
| `alembic/versions_legacy/2e9274def356_remove_datasetuser_label_cascade.py` | production | 0 | 0 | 0 | 3 |
| `cyclone/celery_tasks/workflows/add_examples.py` | production | 0 | 0 | 0 | 3 |
| `cyclone/celery_tasks/workflows/save_predictions.py` | production | 0 | 0 | 0 | 3 |
| `cyclone/celery_tasks/workflows/split_data.py` | production | 0 | 0 | 0 | 3 |
| `cyclone/database/labelsets.py` | production | 0 | 0 | 0 | 3 |
| `cyclone/database/migrations/spans/external_ops.py` | production | 0 | 0 | 0 | 3 |
| `cyclone/database/queries/examples/examples.py` | production | 0 | 0 | 0 | 3 |
| `cyclone/database/queries/filtered_examples.py` | production | 0 | 0 | 1 | 2 |
| `cyclone/database/queries/labelsets.py` | production | 0 | 0 | 0 | 3 |
| `tests/celery_tasks/workflows/conftest.py` | test_support | 0 | 1 | 0 | 2 |
| `tests/celery_tasks/workflows/test_split_data.py` | test | 0 | 0 | 0 | 3 |
| `tests/database/migrations/test_manual_migrations_async.py` | test | 0 | 0 | 0 | 3 |
| `tests/routes/example/test_label_examples.py` | test | 0 | 0 | 0 | 3 |
| `alembic/manual_migrations/populate_ocr_used.py` | script | 0 | 0 | 0 | 2 |
| `alembic/manual_migrations/reindex_rows_in_dataset.py` | script | 0 | 0 | 0 | 2 |
| `alembic/manual_migrations/reverse_span_migration.py` | script | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/05a29c2567cf_added_number_of_labeled_points_to_.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/160c69329d8b_add_labelinstance.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/1e017eb89d4c_add_frozenlabelset.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/1e36c3e3ea40_datarow.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/22cac615366b_audit_datapoints.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/28aa119b10ff_change_num_labelers_default.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/2f4fed877dcf_add_target_type.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/335687920bf7_dataset_delete_status.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/4256adf7f691_subset_no_cascade.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/4a3004b1ca94_add_datafiles.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/4c8fdcdf0d04_add_default_subset_id.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/4c8fdcdf0d05_default_subset_id_migration.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/515df41c6591_adding_counting_columns.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/51715468df06_add_indices.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/5560e59c7914_add_targetname_mapping_to_frozenlabelset.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/58eb1b060798_add_link_to_exports.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/5b34295c0780_more_counts_for_labels.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/5f73e3294dbb_add_new_image_datatype.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/637da133f222_add_offset_to_df_page.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/65b901abc724_add_fkey_indexing.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/6eed0d4724a2_migrate_export_links.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/75f111379f0d_add_file_info.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/7a3518051316_add_indexes.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/7d55fe936c49_filetype_enum_for_datafile.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/7d69eafc422f_add_subsets.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/803fd223d497_add_subset_id_to_featurecolumn.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/808d4ded59ee_target_names.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/8953a9ee518b_add_example_spangroup_assoc_table.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/8c662b67987c_add_scores_to_label.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/8c9a4c4a53ba_datafile_deleted_flag.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/8cbedb0e8568_remove_classes.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/8fb40ff7b381_change_target_name_position_to_nullable.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/a051419e65a1_add_ocr_used_on_datacolumn.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/a5a4f96c6bc3_auditing_labels.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/aa2dcaf6570c_add_example_spangroup_id_index.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/ac9d37ce5f2b_add_task_type.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/b0956d938e1e_add_original_datafile_col.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/b14b336d4a87_userpermission_table.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/b1acd2e5e67d_.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/b611dd1b8620_swap_foreign_key_for_label.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/b77ab4cb9556_add_celery_task_id_to_datafile.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/b8f1a1fe7f96_initial_schema.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/bac24e2f954d_default_row_count.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/bcaf02152e43_dataset_error_info.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/c4e2a2ec9cb2_add_index_to_label_row_index.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/d0d98d994ccf_add_labelsetpoint_row_index_index.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/db4b89c74c67_export_column_ids_and_subset_ids.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/f02afee5edc6_add_labelset_type.py` | production | 0 | 0 | 0 | 2 |
| `alembic/versions_legacy/f2e71f22fbaa_added_datafile_page_object_to_support_.py` | production | 0 | 0 | 0 | 2 |
| `cyclone/celery_tasks/file_pipeline/download.py` | production | 0 | 0 | 0 | 2 |
| `cyclone/celery_tasks/workflows/bundle_docs.py` | production | 0 | 0 | 0 | 2 |
| `cyclone/celery_tasks/workflows/load_data/load_data_task.py` | production | 0 | 0 | 0 | 2 |
| `cyclone/celery_tasks/workflows/load_data/load_predictions_task.py` | production | 0 | 0 | 0 | 2 |
| `cyclone/celery_tasks/workflows/load_data/load_tables_task.py` | production | 0 | 0 | 0 | 2 |
| `cyclone/celery_tasks/workflows/load_data/spansource_loader.py` | production | 0 | 0 | 1 | 1 |
| `cyclone/config.py` | production | 0 | 0 | 0 | 2 |
| `cyclone/database/datapoints.py` | production | 0 | 0 | 0 | 2 |
| `cyclone/database/datasetusers.py` | production | 0 | 0 | 0 | 2 |
| `cyclone/database/migrations/remove_deleting_users.py` | script | 0 | 0 | 0 | 2 |
| `cyclone/database/migrations/targetname_utils.py` | production | 0 | 0 | 0 | 2 |
| `cyclone/database/models/datasetuser.py` | production | 0 | 0 | 0 | 2 |
| `cyclone/database/queries/add_data.py` | production | 0 | 0 | 0 | 2 |
| `cyclone/database/queries/datarow.py` | production | 0 | 0 | 0 | 2 |
| `cyclone/database/queries/examples/list_examples.py` | production | 0 | 0 | 0 | 2 |
| `cyclone/database/queries/training_data.py` | production | 0 | 0 | 0 | 2 |
| `cyclone/http_contracts.py` | production | 0 | 0 | 2 | 0 |
| `cyclone/services/api_exports.py` | production | 0 | 0 | 0 | 2 |
| `tests/celery_tasks/file_pipeline/test_file_failure.py` | test | 0 | 0 | 0 | 2 |
| `tests/celery_tasks/workflows/test_add_examples.py` | test | 0 | 0 | 0 | 2 |
| `tests/database/migrations/test_spans_migration.py` | test | 0 | 0 | 1 | 1 |
| `tests/queries/test_examples.py` | test | 0 | 0 | 0 | 2 |
| `tests/queries/test_featurecolumns.py` | test | 0 | 0 | 0 | 2 |
| `tests/routes/dataset/test_add_data_csv.py` | test | 0 | 0 | 0 | 2 |
| `tests/routes/dataset/test_add_data_files.py` | test | 0 | 0 | 0 | 2 |
| `tests/routes/dataset/test_pipeline.py` | test | 0 | 0 | 0 | 2 |
| `tests/routes/labelset/test_target_names.py` | test | 0 | 0 | 2 | 0 |
| `tests/utils/test_load_data_utils.py` | test | 0 | 0 | 0 | 2 |
| `tools/validate_contracts.py` | script | 0 | 0 | 1 | 1 |
| `alembic/manual_migrations/create_frozen_labelsets.py` | script | 0 | 0 | 0 | 1 |
| `alembic/manual_migrations/rm_label_dsuser_fkey.py` | script | 0 | 0 | 0 | 1 |
| `cyclone/celery_tasks/file_pipeline/extract.py` | production | 0 | 0 | 0 | 1 |
| `cyclone/celery_tasks/file_pipeline/file_failure.py` | production | 0 | 0 | 0 | 1 |
| `cyclone/celery_tasks/task_utils/utils.py` | production | 0 | 0 | 0 | 1 |
| `cyclone/database/exports.py` | production | 0 | 0 | 0 | 1 |
| `cyclone/database/migrations/create_frozen_labelsets.py` | production | 0 | 0 | 0 | 1 |
| `cyclone/database/models/dataset.py` | production | 0 | 0 | 0 | 1 |
| `cyclone/database/models/labelset.py` | production | 0 | 0 | 0 | 1 |
| `cyclone/database/queries/datafiles.py` | production | 0 | 0 | 0 | 1 |
| `cyclone/database/queries/exports.py` | production | 0 | 0 | 0 | 1 |
| `cyclone/database/queries/spangroups.py` | production | 0 | 0 | 0 | 1 |
| `cyclone/database/queries/subsets.py` | production | 0 | 0 | 0 | 1 |
| `cyclone/dependencies/permissions.py` | production | 0 | 1 | 0 | 0 |
| `cyclone/routes/models/examples.py` | production | 0 | 0 | 0 | 1 |
| `cyclone/services/datafiles.py` | production | 0 | 0 | 0 | 1 |
| `cyclone/services/datasets.py` | production | 0 | 0 | 0 | 1 |
| `cyclone/services/permissions.py` | production | 0 | 0 | 0 | 1 |
| `cyclone/services/search.py` | production | 0 | 0 | 0 | 1 |
| `cyclone/utils/name.py` | production | 0 | 0 | 0 | 1 |
| `scripts/check_example_and_labels_assoc.py` | script | 0 | 0 | 0 | 1 |
| `scripts/check_spangroups.py` | script | 0 | 0 | 0 | 1 |
| `scripts/freeze_dataset.py` | script | 0 | 0 | 0 | 1 |
| `tests/celery_tasks/workflows/test_agent_bundler.py` | test | 0 | 0 | 0 | 1 |
| `tests/celery_tasks/workflows/test_save_predictions.py` | test | 0 | 0 | 0 | 1 |
| `tests/contracts/http/test_contract_edges.py` | test | 0 | 0 | 0 | 1 |
| `tests/database/migrations/test_remove_deleting_users.py` | test | 0 | 0 | 0 | 1 |
| `tests/fixtures/dataset.py` | test_support | 0 | 0 | 0 | 1 |
| `tests/routes/dataset/test_delete_dataset.py` | test | 0 | 0 | 0 | 1 |
| `tests/routes/dataset/test_get_labelsets_info.py` | test | 0 | 0 | 0 | 1 |
| `tests/routes/example/test_example_contexts.py` | test | 0 | 0 | 0 | 1 |
| `tests/routes/example/test_example_tasks.py` | test | 0 | 0 | 1 | 0 |
| `tests/routes/labelset/test_compare_labelset.py` | test | 0 | 0 | 0 | 1 |
| `tests/routes/labelset/test_copy_labelset.py` | test | 0 | 0 | 0 | 1 |
| `tests/routes/labelset/test_frozen_labelsets.py` | test | 0 | 0 | 0 | 1 |
| `tests/routes/labelset/test_labelset_stats.py` | test | 0 | 0 | 0 | 1 |
| `tests/routes/permissions/test_datasets_route_permissions.py` | test | 0 | 0 | 0 | 1 |
| `tests/routes/permissions/test_rainbow_permissions.py` | test | 0 | 0 | 0 | 1 |
| `tests/services/test_csv_processing.py` | test | 0 | 0 | 0 | 1 |

### `cyclone/utils/data_splitting.py`

- 🔎 **medium** `PY-SEC-INSECURE-RANDOM` (L129) — `random.choice(...)` is not cryptographically secure; unsafe for tokens/keys
- 🔎 **medium** `PY-CORRECT-SWALLOWED-EXCEPTION` (L444) — exception silently swallowed (no log, re-raise, or handling)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L23) — `label_binarize` missing type hints (labels, classes, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L32) — `_format_target` missing type hints (target, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L45) — `split_data` missing type hints (data, test_split, seed, task_type)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L90) — `__init__` missing type hints (targets, exclude_from_metrics_flags, target_keys)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L101) — `group_by_keys` missing type hints (keys, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L111) — `extract_classes` missing type hints (targets, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L115) — `binarize` missing type hints (targets, classes, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L118) — `select_idx` missing type hints (force_set, idxs_by_class, target_idx, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L137) — `clean_idx` missing type hints (idxs_by_class, target_idx, idx, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L140) — `update_conditions` missing type hints (conditions, idx, target_idx, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L152) — `select_examples_for_condition` missing type hints (conditions, target_idx, min_test, idxs_by_class, force_set, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L173) — `order_for_selection` missing type hints (idxs_by_class, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L176) — `populate_idx_by_class` missing type hints (min_examples)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L193) — `check_classes` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L197) — `resolve_rare_classes` missing type hints (idxs_by_class, min_examples, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L217) — `handle_skipped_class` missing type hints (target_idx, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L220) — `reorganize_shared_idxs` missing type hints (train_set, test_set, -> return)
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L284) — `__call__` cyclomatic complexity 19 (> 10)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L284) — `__call__` missing type hints (test_size, min_test, min_train, seed, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L413) — `extract_classes` missing type hints (targets, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L417) — `binarize` missing type hints (targets, classes, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L422) — `check_classes` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L429) — `handle_skipped_class` missing type hints (target_idx, idxs_by_class, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L436) — `clean_idx` missing type hints (idxs_by_class, target_idx, idx, idxs_removed_by_class, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L447) — `update_conditions` missing type hints (conditions, idx, target_idx, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L457) — `select_examples_for_condition` missing type hints (conditions, target_idx, min_cond, idxs_by_class, force_set, idxs_removed_by_class, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L490) — `order_for_selection` missing type hints (idxs_by_class, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L502) — `populate_idx_by_class` missing type hints (min_examples, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L518) — `__init__` missing type hints (targets)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L522) — `check_classes` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L526) — `handle_skipped_class` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L529) — `populate_idx_by_class` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L532) — `update_conditions` missing type hints (conditions, idx, target_idx, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L542) — `select_examples_for_condition` missing type hints (conditions, target_idx, min_cond, idxs_by_class, force_set, idxs_removed_by_class, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L575) — `__call__` missing type hints (test_size, min_test, min_train, seed, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L582) — `__init__` missing type hints (targets)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L593) — `get_class` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L601) — `__init__` missing type hints (targets)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L612) — `__call__` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L637) — `unique_counts` missing type hints (Y, multilabel, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L647) — `strip_rare_classes` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L666) — `strip_extraction_label` missing type hints (target_list, rare_classes, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L670) — `strip_multilabel_label` missing type hints (target_list, rare_classes, -> return)

### `cyclone/database/typed_labels.py`

- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L25) — `add_page_num` takes dict[str, Any] `span`; accept a typed model
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L25) — `add_page_num` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L42) — `meta_parser` missing type hints (cls, target, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L66) — `format_prediction` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L73) — `io_to_db` missing type hints (pred, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L80) — `convert_to_old` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L87) — `convert_new` missing type hints (label, df, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L101) — `io_to_db` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L119) — `format_prediction` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L130) — `format_prediction_new` missing type hints (class_confidences, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L135) — `matches_format` missing type hints (target, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L139) — `convert_new` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L148) — `io_to_db` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L164) — `format_prediction` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L178) — `format_prediction_new` missing type hints (target, class_confidences, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L183) — `matches_format` missing type hints (target, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L187) — `convert_new` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L201) — `io_to_db` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L223) — `format_prediction` missing type hints (target, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L240) — `format_prediction_new` missing type hints (target, class_confidences, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L244) — `matches_format` missing type hints (target, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L248) — `convert_new` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L280) — `convert_to_old` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L304) — `io_to_db` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L326) — `format_prediction` missing type hints (target, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L343) — `format_prediction_new` missing type hints (target, class_confidences, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L347) — `matches_format` missing type hints (target, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L351) — `convert_new` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L381) — `io_to_db` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L403) — `format_prediction` missing type hints (target, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L420) — `format_prediction_new` missing type hints (target, class_confidences, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L424) — `matches_format` missing type hints (target, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L428) — `convert_new` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L464) — `io_to_db` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L479) — `format_prediction` missing type hints (target, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L493) — `format_prediction_new` missing type hints (target, class_confidences, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L498) — `matches_format` missing type hints (target, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L503) — `convert_new` missing type hints (-> return)

### `cyclone/label_resolution.py`

- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L66) — `_get_meta` returns dict[str, Any]; return a typed model
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L33) — `group_by_fields` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L45) — `resolution` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L46) — `wrapped_resolution` missing type hints (fn, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L53) — `prediction_resolution` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L54) — `wrapped_resolution` missing type hints (fn, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L73) — `_get_confidences` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L89) — `get_spans` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L169) — `unanimous` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L183) — `majority_vote_with_ties` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L203) — `majority_vote_without_ties` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L222) — `resolve_predictions` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L236) — `filter_class_counter` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L251) — `all_labels` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L257) — `unanimous` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L267) — `majority_vote_with_ties` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L277) — `majority_vote_without_ties` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L284) — `resolve_predictions` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L294) — `resolutions` missing type hints (task_types, resolution_types, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L295) — `compound_decorator` missing type hints (fn, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L309) — `all_resolutions_token` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L325) — `all_resolutions_spatial` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L339) — `resolve_predictions_spatial` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L355) — `resolve_predictions_annotation` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L401) — `resolve_predictions` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L446) — `resolve_predictions` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L485) — `resolve_predictions` missing type hints (-> return)

### `tools/worker_contracts.py`

- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L32) — `load_json` returns dict[str, Any]; return a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L36) — `load_manifest` returns dict[str, Any]; return a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L43) — `load_schema` returns dict[str, Any]; return a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L50) — `normalize_schema_node` returns dict[str, Any]; return a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L79) — `task_identity` takes dict[str, Any] `task`; accept a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L83) — `queue_value` takes dict[str, Any] `task`; accept a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L91) — `validator_errors` takes dict[str, Any] `manifest`; accept a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L91) — `validator_errors` takes dict[str, Any] `schema`; accept a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L103) — `duplicate_identities` takes dict[str, Any] `manifest`; accept a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L117) — `validate_manifest` returns dict[str, Any]; return a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L117) — `validate_manifest` takes dict[str, Any] `manifest`; accept a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L171) — `diff_schema` takes dict[str, Any] `base`; accept a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L171) — `diff_schema` takes dict[str, Any] `head`; accept a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L234) — `diff_retry_policy` takes dict[str, Any] `base`; accept a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L234) — `diff_retry_policy` takes dict[str, Any] `head`; accept a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L268) — `diff_idempotency_policy` takes dict[str, Any] `base`; accept a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L268) — `diff_idempotency_policy` takes dict[str, Any] `head`; accept a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L296) — `diff_task` takes dict[str, Any] `base`; accept a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L296) — `diff_task` takes dict[str, Any] `head`; accept a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L387) — `build_diff_payload` returns dict[str, Any]; return a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L387) — `build_diff_payload` takes dict[str, Any] `base_manifest`; accept a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L387) — `build_diff_payload` takes dict[str, Any] `head_manifest`; accept a typed model
- 🔎 **low** `PY-OOP-FREE-FN-ORCHESTRATOR` (L20) — 5 free functions thread `path` between them; use a coordinator class
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L50) — `normalize_schema_node` cyclomatic complexity 11 (> 10)
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L171) — `diff_schema` cyclomatic complexity 14 (> 10)
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L296) — `diff_task` cyclomatic complexity 13 (> 10)
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L387) — `build_diff_payload` cyclomatic complexity 12 (> 10)

### `cyclone/database/migrations/spans/labelset_ops.py`

- 🔎 **medium** `PY-CORRECT-SWALLOWED-EXCEPTION` (L59) — exception silently swallowed (no log, re-raise, or handling)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L53) — `add_page_num` missing type hints (span, df)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L75) — `get_spans_from_col` missing type hints (col_id)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L101) — `ls_copy_name` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L105) — `copy_upload_labels` missing type hints (source_ls, dest_ls, user_id, dsuser_id, tn_mapping, -> return)
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L183) — `copy_target_names` cyclomatic complexity 11 (> 10)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L228) — `__init__` missing type hints (ds, col_info, mgs, _log)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L248) — `initialize` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L258) — `_get_dr_to_df` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L265) — `_get_datafiles` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L274) — `_get_ls_copies` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L295) — `dfs` missing type hints (datarow_id, -> return)
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L301) — `adjust_upload_lsets` cyclomatic complexity 16 (> 10)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L301) — `adjust_upload_lsets` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L345) — `copy_upload_labelset` missing type hints (ls, dc_id, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L378) — `adjust_prediction_lsets` missing type hints (-> return)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L392) — await inside a loop; if independent, gather them concurrently
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L416) — `move_preds_to_lset` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L470) — `make_examples_and_labels` missing type hints (-> return)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L483) — await inside a loop; if independent, gather them concurrently
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L490) — await inside a loop; if independent, gather them concurrently
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L541) — `subset_spans_for_ls` cyclomatic complexity 12 (> 10)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L541) — `subset_spans_for_ls` missing type hints (ls, -> return)
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L572) — `update_labels` cyclomatic complexity 16 (> 10)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L572) — `update_labels` missing type hints (-> return)

### `cyclone/database/queries/labels.py`

- 🔎 **medium** `PY-CORRECT-SWALLOWED-EXCEPTION` (L75) — exception silently swallowed (no log, re-raise, or handling)
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L112) — `format_new_labels_for_db` returns dict[str, Any]; return a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L112) — `format_new_labels_for_db` takes dict[str, Any] `label`; accept a typed model
- 🔧 **medium** `PY-CORRECT-BROAD-EXCEPT` (L263) — except Exception with no re-raise swallows all errors
- 🔧 **medium** `PY-CORRECT-BROAD-EXCEPT` (L1026) — except Exception with no re-raise swallows all errors
- 🔧 **low** `PY-STYLE-FILE-SIZE` (L1) — file is 1312 lines (> 800); split into a package
- 🔎 **low** `PY-OOP-FREE-FN-ORCHESTRATOR` (L80) — 8 free functions thread `example_ids` between them; use a coordinator class
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L112) — `format_new_labels_for_db` cyclomatic complexity 16 (> 10)
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L171) — `validate_labels_for_labelset` cyclomatic complexity 31 (> 10)
- 🔎 **low** `PY-OOP-LONG-PARAM-LIST` (L171) — `validate_labels_for_labelset` takes 7 parameters (> 6); group into an object
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L171) — `validate_labels_for_labelset` missing type hints (ignore_failed, reset_page_nums, user_id, logger)
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L314) — `get_row_by_target` cyclomatic complexity 13 (> 10)
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L384) — `_get_and_validate_datapoints` cyclomatic complexity 20 (> 10)
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L480) — `_validate_label_spans` cyclomatic complexity 13 (> 10)
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L517) — `update_labels_logic` cyclomatic complexity 42 (> 10)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L517) — `update_labels_logic` missing type hints (first_labels)
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L788) — `create_spans_for_instances` cyclomatic complexity 17 (> 10)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L802) — await inside a loop; if independent, gather them concurrently
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L917) — `copy_labels` cyclomatic complexity 11 (> 10)
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L988) — `get_labels_by_subsets` cyclomatic complexity 15 (> 10)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L1306) — await inside a loop; if independent, gather them concurrently

### `cyclone/services/csv_processing.py`

- 🔎 **high** `PY-ASYNC-UNLOCKED-LAZY-INIT` (L104) — check-then-set lazy init of `self._existing_dcols` without a lock (race)
- 🔎 **high** `PY-ASYNC-UNLOCKED-LAZY-INIT` (L112) — check-then-set lazy init of `self._existing_lsets` without a lock (race)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L90) — `load_csv` missing type hints (-> return)
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L180) — `create_cols_from_csv` cyclomatic complexity 14 (> 10)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L180) — `create_cols_from_csv` missing type hints (-> return)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L189) — await inside a loop; if independent, gather them concurrently
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L251) — await inside a loop; if independent, gather them concurrently
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L271) — `update_ls` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L303) — `get_target_names_for_labelset` missing type hints (-> return)
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L394) — `create_points_and_labels` cyclomatic complexity 11 (> 10)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L394) — `create_points_and_labels` missing type hints (row_ids, starting_row_idx, -> return)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L397) — await inside a loop; if independent, gather them concurrently
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L423) — await inside a loop; if independent, gather them concurrently
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L445) — `check_type_info` cyclomatic complexity 34 (> 10)
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L579) — `_is_valid_url_col` cyclomatic complexity 12 (> 10)

### `alembic/manual_migrations/populate_ds_type_default_datacolumn.py`

- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L35) — `get_dataset_type` cyclomatic complexity 12 (> 10)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L35) — `get_dataset_type` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L66) — `get_default_dc_image` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L72) — `get_default_dc_document` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L78) — `get_default_dc_text` missing type hints (-> return)
- 🔎 **low** `PY-OOP-FREE-FN-ORCHESTRATOR` (L89) — 6 free functions thread `db_session` between them; use a coordinator class
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L89) — `get_type_inference_csv` missing type hints (-> return)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L92) — await inside a loop; if independent, gather them concurrently
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L127) — `get_task_type_for_labelset` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L148) — `update_type_info` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L219) — `main` missing type hints (-> return)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L235) — await inside a loop; if independent, gather them concurrently
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L274) — `run_main` missing type hints (-> return)

### `alembic/manual_migrations/label_spans.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L33) — `_page_num_for_span` missing type hints (span, df, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L45) — `convert_class` missing type hints (label, df, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L55) — `convert_class_multi` missing type hints (label, df, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L63) — `convert_annot` missing type hints (label, df, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L77) — `convert_ratclass` missing type hints (label, df, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L84) — `convert_objdet` missing type hints (label, df, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L90) — `convert_formextr` missing type hints (label, df, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L106) — `main` missing type hints (-> return)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L119) — await inside a loop; if independent, gather them concurrently
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L129) — await inside a loop; if independent, gather them concurrently
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L143) — `run_main` missing type hints (-> return)

### `cyclone/database/migrations/spans/datacolumn_ops.py`

- 🔎 **low** `PY-OOP-FREE-FN-ORCHESTRATOR` (L24) — 7 free functions thread `db_session` between them; use a coordinator class
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L24) — `_new_span_ids` missing type hints (-> return)
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L28) — `update_default_datacol` cyclomatic complexity 11 (> 10)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L28) — `update_default_datacol` missing type hints (ds, col_info)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L68) — `add_spans_to_cols` missing type hints (ds, -> return)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L74) — await inside a loop; if independent, gather them concurrently
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L88) — `add_blank_spans` missing type hints (col)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L121) — `add_text_spans` missing type hints (col)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L158) — `add_from_file` missing type hints (col)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L227) — `unreject_dps` missing type hints (ds, _log, -> return)

### `tests/utils/test_demux.py`

- 🔧 **medium** `PY-CORRECT-BROAD-EXCEPT` (L100) — except Exception with no re-raise swallows all errors
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L11) — async `test_empty_stream` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L47) — async `faulty` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L87) — async `faulty` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L91) — async `normal` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L107) — async `test_atexit_register` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L114) — async `gen` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L130) — async `faulty_stream` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L135) — async `normal_stream` has no await/async-with/async-for; make it sync

### `tests/utils/test_span_source_loader.py`

- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L111) — async `_empty_spans_label_load` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L239) — async `mock_generator` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L248) — async `mock_process` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L294) — async `failing_generator` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L339) — async `mock_loader` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L505) — async `mock_stream` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L546) — async `fake_scoped_session_ctx` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L554) — async `mock_to_thread` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L719) — async `fake_load_datapage` has no await/async-with/async-for; make it sync

### `cyclone/database/labels.py`

- 🔎 **medium** `PY-CORRECT-SWALLOWED-EXCEPTION` (L249) — exception silently swallowed (no log, re-raise, or handling)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L65) — `_resolve_meta` missing type hints (tname_to_id, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L164) — `targets` missing type hints (-> return)
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L167) — `scoped_targets` cyclomatic complexity 17 (> 10)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L167) — `scoped_targets` missing type hints (spangroup)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L213) — `_target_v1` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L235) — `targets` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L313) — `_labelname_to_str` missing type hints (class_name, -> return)

### `cyclone/worker_contracts.py`

- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L145) — `_io_descriptor_schema` returns dict[str, Any]; return a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L161) — `_surface_schema` returns dict[str, Any]; return a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L175) — `_config_schema` returns dict[str, Any]; return a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L187) — `_normalize_retry` returns dict[str, Any]; return a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L206) — `_normalize_idempotency` returns dict[str, Any]; return a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L220) — `_task_contract` returns dict[str, Any]; return a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L263) — `build_manifest` returns dict[str, Any]; return a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L275) — `write_manifest_artifacts` takes dict[str, Any] `manifest`; accept a typed model

### `cyclone/celery_tasks/export_task.py`

- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L36) — `create_export` cyclomatic complexity 21 (> 10)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L185) — `_add_values` missing type hints (df, datacolumn_names, dp_ids_to_dps, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L196) — `_add_spans` missing type hints (df, id_colname, main_col_name, df_info, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L207) — `_point_val` missing type hints (-> return)
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L224) — `_generate_columns` cyclomatic complexity 28 (> 10)
- 🔎 **low** `PY-OOP-LONG-PARAM-LIST` (L224) — `_generate_columns` takes 8 parameters (> 6); group into an object
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L224) — `_generate_columns` missing type hints (-> return)

### `cyclone/database/datafiles.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L88) — `page_ids` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L92) — `num_pages` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L96) — `num_pages` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L104) — `has_pages` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L108) — `has_pages` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L115) — `status_meta` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L120) — `csv_type_info` missing type hints (-> return)

### `cyclone/database/utils.py`

- 🔧 **medium** `PY-CORRECT-BROAD-EXCEPT` (L15) — except Exception with no re-raise swallows all errors
- 🔎 **medium** `PY-CORRECT-SWALLOWED-EXCEPTION` (L15) — exception silently swallowed (no log, re-raise, or handling)
- 🔧 **medium** `PY-CORRECT-BROAD-EXCEPT` (L21) — except Exception with no re-raise swallows all errors
- 🔧 **medium** `PY-CORRECT-BROAD-EXCEPT` (L31) — except Exception with no re-raise swallows all errors
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L12) — `encode` missing type hints (data, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L27) — `decode` missing type hints (b_data, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L40) — `int_array_param` missing type hints (-> return)

### `alembic/versions_legacy/c09a8fe14dee_add_reviewer_permissions.py`

- 🔎 **high** `PY-SEC-SQL-STRING-BUILD` (L85) — SQL built from a caller-supplied value passed to .execute(); injection risk
- 🔎 **high** `PY-SEC-SQL-STRING-BUILD` (L96) — SQL built from a caller-supplied value passed to .execute(); injection risk
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L78) — `convert_enum_array` missing type hints (table, column, enum_name, values, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L109) — `migrate` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L140) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L149) — `downgrade` missing type hints (-> return)

### `scripts/export.py`

- 🔧 **medium** `PY-STYLE-INLINE-IMPORT` (L147) — inline import; move to module top
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L21) — `get_datacolumn_ids` missing type hints (dataset_id, db_session, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L28) — `get_labelset_ids` missing type hints (dataset_id, db_session, -> return)
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L35) — `create_export` cyclomatic complexity 19 (> 10)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L35) — `create_export` missing type hints (db_session, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L146) — `main` missing type hints (-> return)

### `tests/celery_tasks/file_pipeline/test_process.py`

- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L71) — async `_fake_extract` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L461) — async `run_csv_pipeline` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L541) — async `run_csv_pipeline` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L618) — async `run_csv_pipeline` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L692) — async `load_csv` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L710) — async `_wfv2_launch` has no await/async-with/async-for; make it sync

### `alembic/manual_migrations/populate_target_names.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L58) — `create_question_cache` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L92) — `create_mg_target_cache` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L121) — `get_labelsets_to_migrate` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L145) — `add_targets` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L180) — `main` missing type hints (-> return)

### `alembic/manual_migrations/span_migration.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L32) — `_log` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L37) — `get_ds` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L51) — `migrate_dataset` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L86) — `main` missing type hints (-> return)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L116) — await inside a loop; if independent, gather them concurrently

### `cyclone/celery_tasks/file_pipeline/process.py`

- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L71) — `complete_process_file` cyclomatic complexity 19 (> 10)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L71) — `complete_process_file` missing type hints (-> return)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L112) — await inside a loop; if independent, gather them concurrently
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L189) — await inside a loop; if independent, gather them concurrently
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L202) — `process_csv_task` missing type hints (context, -> return)

### `cyclone/celery_tasks/workflows/load_data/muxation.py`

- 🔧 **high** `PY-ASYNC-DANGLING-TASK` (L72) — `asyncio.create_task(...)` result is discarded; the task may be GC'd mid-flight
- 🔎 **medium** `PY-CORRECT-SWALLOWED-EXCEPTION` (L52) — exception silently swallowed (no log, re-raise, or handling)
- 🔎 **medium** `PY-CORRECT-SWALLOWED-EXCEPTION` (L77) — exception silently swallowed (no log, re-raise, or handling)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L49) — await inside a loop; if independent, gather them concurrently
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L59) — await inside a loop; if independent, gather them concurrently

### `cyclone/celery_tasks/workflows/load_data/prediction_builder.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L43) — `__init__` missing type hints (include_deleted_targets)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L57) — await inside a loop; if independent, gather them concurrently
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L131) — `__init__` missing type hints (include_deleted_targets)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L173) — `__init__` missing type hints (include_deleted_targets)
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L190) — async `_prediction_batches` has no await/async-with/async-for; make it sync

### `cyclone/database/queries/target_names.py`

- 🔎 **low** `PY-OOP-FREE-FN-ORCHESTRATOR` (L25) — 10 free functions thread `db_session` between them; use a coordinator class
- 🔎 **low** `PY-OOP-LONG-PARAM-LIST` (L25) — `create_target_names` takes 7 parameters (> 6); group into an object
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L105) — `add_target_names_if_not_existing` cyclomatic complexity 14 (> 10)
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L225) — `copy_target_names` cyclomatic complexity 13 (> 10)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L307) — await inside a loop; if independent, gather them concurrently

### `cyclone/services/labelsets.py`

- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L396) — `update_target_positions` cyclomatic complexity 13 (> 10)
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L438) — `add_labelset_targets` cyclomatic complexity 14 (> 10)
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L528) — `deactivate_target_names` cyclomatic complexity 22 (> 10)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L639) — await inside a loop; if independent, gather them concurrently
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L698) — `update_labelset` cyclomatic complexity 11 (> 10)

### `tests/celery_tasks/workflows/test_load_data.py`

- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L59) — async `load` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L67) — async `iter_by_pkeys` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L148) — await inside a loop; if independent, gather them concurrently
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L266) — await inside a loop; if independent, gather them concurrently
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L382) — await inside a loop; if independent, gather them concurrently

### `tests/conftest.py`

- 🔧 **medium** `PY-CORRECT-BROAD-EXCEPT` (L52) — except Exception with no re-raise swallows all errors
- 🔎 **medium** `PY-CORRECT-SWALLOWED-EXCEPTION` (L52) — exception silently swallowed (no log, re-raise, or handling)
- 🔧 **medium** `PY-CORRECT-BROAD-EXCEPT` (L54) — except Exception with no re-raise swallows all errors
- 🔎 **medium** `PY-CORRECT-SWALLOWED-EXCEPTION` (L54) — exception silently swallowed (no log, re-raise, or handling)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L48) — await inside a loop; if independent, gather them concurrently

### `alembic/manual_migrations/datarow.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L23) — `update_table` missing type hints (cls, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L40) — `update_labelset_related_obj` missing type hints (cls, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L57) — `migrate_dataset` missing type hints (-> return)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L123) — await inside a loop; if independent, gather them concurrently

### `alembic/manual_migrations/example_audit_migration.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L21) — `update_examples_audit` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L51) — `process_labelsets` missing type hints (-> return)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L54) — await inside a loop; if independent, gather them concurrently
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L61) — `main` missing type hints (-> return)

### `alembic/manual_migrations/find_bad_span_ds.py`

- 🔧 **high** `PY-ASYNC-SYNC-IO` (L78) — sync `open(...)` blocks the event loop inside async `main`
- 🔧 **high** `PY-ASYNC-SYNC-IO` (L79) — sync `f.write(...)` blocks the event loop inside async `main`
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L19) — `main` missing type hints (db_session, write, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L82) — `run_main` missing type hints (write, -> return)

### `alembic/versions_legacy/0ac73f2fabc3_xlsm_xlsb_file_types.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L13) — `add_enum_values` missing type hints (table, column, enum_name, existing_values, values_to_add, additional_columns, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L37) — `remove_emum_values` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L80) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L99) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/1372cc725083_add_empty_password_protected_failuretype.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L13) — `add_enum_values` missing type hints (table, column, enum_name, existing_values, values_to_add, additional_columns, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L37) — `remove_emum_values` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L48) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L75) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/151933fc0092_add_labelset_tasktype_rationalized_.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L13) — `add_enum_values` missing type hints (table, column, enum_name, existing_values, values_to_add, additional_columns, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L37) — `remove_emum_values` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L48) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L65) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/208cd1005ec8_add_new_filetypes.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L13) — `add_enum_values` missing type hints (table, column, enum_name, existing_values, values_to_add, additional_columns, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L37) — `remove_emum_values` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L113) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L132) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/21546ee7a1f4_add_limit_failures.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L13) — `add_enum_values` missing type hints (table, column, enum_name, existing_values, values_to_add, additional_columns, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L37) — `remove_emum_values` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L48) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L71) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/26912a096f60_image_filetypes.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L13) — `add_enum_values` missing type hints (table, column, enum_name, existing_values, values_to_add, additional_columns, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L37) — `remove_emum_values` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L48) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L58) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/284776505ea5_add_csv_filefailuretypes.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L13) — `add_enum_values` missing type hints (table, column, enum_name, existing_values, values_to_add, additional_columns, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L37) — `remove_emum_values` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L48) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L73) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/4625fee6fca6_add_staged_status.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L14) — `add_enum_values` missing type hints (table, column, enum_name, existing_values, values_to_add, additional_columns, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L38) — `remove_emum_values` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L49) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L61) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/4ec4f6fc5054_add_genai_task_and_model_type.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L13) — `add_enum_values` missing type hints (table, column, enum_name, existing_values, values_to_add, additional_columns, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L37) — `remove_emum_values` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L48) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L68) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/58dfe0fecd92_add_genai_classification.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L13) — `add_enum_values` missing type hints (table, column, enum_name, existing_values, values_to_add, additional_columns, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L37) — `remove_emum_values` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L48) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L69) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/62d381354744_add_unknown_filetype_enum.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L13) — `add_enum_values` missing type hints (table, column, enum_name, existing_values, values_to_add, additional_columns, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L37) — `remove_emum_values` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L62) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L72) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/67cace5e7a2c_add_new_file_types.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L13) — `add_enum_values` missing type hints (table, column, enum_name, existing_values, values_to_add, additional_columns, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L37) — `remove_emum_values` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L64) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L74) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/68203aa36035_add_summarization_task_type_and_.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L13) — `add_enum_values` missing type hints (table, column, enum_name, existing_values, values_to_add, additional_columns, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L37) — `remove_emum_values` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L48) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L83) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/72e0dc61b71b_add_labelset_status_poll_for_updates.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L14) — `add_enum_values` missing type hints (table, column, enum_name, existing_values, values_to_add, additional_columns, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L38) — `remove_emum_values` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L49) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L61) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/83b234dc54b1_add_object_detection_task_type.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L13) — `add_enum_values` missing type hints (table, column, enum_name, existing_values, values_to_add, additional_columns, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L37) — `remove_emum_values` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L48) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L58) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/90edd6ea1dec_add_standard_v2.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L14) — `add_enum_values` missing type hints (table, column, enum_name, existing_values, values_to_add, additional_columns, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L38) — `remove_emum_values` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L67) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L71) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/95b38783aa48_add_processed_status.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L13) — `add_enum_values` missing type hints (table, column, enum_name, existing_values, values_to_add, additional_columns, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L37) — `remove_emum_values` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L48) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L58) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/a523f41ed78b_upload_labelset_type.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L14) — `add_enum_values` missing type hints (table, column, enum_name, existing_values, values_to_add, additional_columns, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L38) — `remove_emum_values` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L67) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L84) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/a91072f40ec9_add_form_extraction_enum.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L13) — `add_enum_values` missing type hints (table, column, enum_name, existing_values, values_to_add, additional_columns, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L37) — `remove_emum_values` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L48) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L64) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/ab375d7e7897_adding_eml_and_msg_file_types.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L13) — `add_enum_values` missing type hints (table, column, enum_name, existing_values, values_to_add, additional_columns, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L37) — `remove_emum_values` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L68) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L78) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/bd8091e76e50_datafile_columns_dataset_type.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L19) — `add_enum_values` missing type hints (table, column, enum_name, existing_values, values_to_add, additional_columns, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L43) — `remove_emum_values` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L76) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L133) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/f11454b07f01_spans.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L16) — `add_enum_values` missing type hints (table, column, enum_name, existing_values, values_to_add, additional_columns, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L40) — `remove_emum_values` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L61) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L210) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/fc8747d855fb_add_pptx_filetypes.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L13) — `add_enum_values` missing type hints (table, column, enum_name, existing_values, values_to_add, additional_columns, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L37) — `remove_emum_values` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L51) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L61) — `downgrade` missing type hints (-> return)

### `cyclone/celery_tasks/workflows/copy_labels.py`

- 🔎 **low** `PY-OOP-LONG-PARAM-LIST` (L25) — `create_fields_and_links` takes 8 parameters (> 6); group into an object
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L120) — `copy_labels` cyclomatic complexity 15 (> 10)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L120) — `copy_labels` missing type hints (context)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L214) — await inside a loop; if independent, gather them concurrently

### `cyclone/database/queries/datasets.py`

- 🔧 **medium** `PY-STYLE-INLINE-IMPORT` (L198) — inline import; move to module top
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L103) — `list_datasets_query` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L131) — `list_all_datasets_query` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L160) — `list_dataset_ids_query` missing type hints (-> return)

### `cyclone/database/queries/datasetusers.py`

- 🔎 **low** `PY-OOP-FREE-FN-ORCHESTRATOR` (L16) — 14 free functions thread `db_session` between them; use a coordinator class
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L161) — `update_user_perms` cyclomatic complexity 23 (> 10)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L387) — await inside a loop; if independent, gather them concurrently
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L393) — await inside a loop; if independent, gather them concurrently

### `cyclone/services/file_processing.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L84) — `js_launch` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L94) — `wfv2_launch` missing type hints (-> return)
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L151) — async `download` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L229) — async `process` has no await/async-with/async-for; make it sync

### `cyclone/utils/type_inference.py`

- 🔎 **medium** `PY-CORRECT-SWALLOWED-EXCEPTION` (L14) — exception silently swallowed (no log, re-raise, or handling)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L7) — `json_str` missing type hints (x, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L11) — `maybe_json_str` missing type hints (x, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L21) — `convert_labelset_type` missing type hints (arr, ttype, -> return)

### `scripts/copy_perms.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L11) — `main` missing type hints (source_user_id, target_user_id, -> return)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L22) — await inside a loop; if independent, gather them concurrently
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L33) — await inside a loop; if independent, gather them concurrently
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L56) — `add_to_dataset` missing type hints (user_id, dataset_id, -> return)

### `tests/celery_tasks/workflows/test_copy_labels.py`

- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L33) — async `post` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L41) — async `make_request` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L49) — async `get` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L57) — async `_fake_model_group_meta` has no await/async-with/async-for; make it sync

### `tests/routes/labelset/test_create_and_list_labelsets.py`

- 🔧 **medium** `PY-STYLE-INLINE-IMPORT` (L236) — inline import; move to module top
- 🔧 **medium** `PY-STYLE-INLINE-IMPORT` (L283) — inline import; move to module top
- 🔧 **medium** `PY-STYLE-INLINE-IMPORT` (L341) — inline import; move to module top
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L79) — async `mock_create_target_names` has no await/async-with/async-for; make it sync

### `alembic/env.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L39) — `run_migrations_offline` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L62) — `run_migration` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L73) — `run_migrations_online` missing type hints (-> return)

### `alembic/manual_migrations/delete_dataset.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L50) — `clean_app` missing type hints (-> return)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L79) — await inside a loop; if independent, gather them concurrently
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L97) — `main` missing type hints (-> return)

### `alembic/manual_migrations/fix_active_labels.py`

- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L19) — `main` cyclomatic complexity 11 (> 10)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L19) — `main` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L81) — `run_main` missing type hints (-> return)

### `alembic/manual_migrations/perms_migration.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L26) — `get_users` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L43) — `main` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L61) — `run_main` missing type hints (-> return)

### `alembic/manual_migrations/populate_subset_rowids.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L15) — `migrate_dataset` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L66) — `migrate` missing type hints (-> return)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L75) — await inside a loop; if independent, gather them concurrently

### `alembic/versions/0d2a9c81277c_init.py`

- 🔧 **low** `PY-STYLE-FILE-SIZE` (L1) — file is 1513 lines (> 800); split into a package
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L21) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L1257) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/2e9274def356_remove_datasetuser_label_cascade.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `safe_drop_fk_constraint` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L27) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L42) — `downgrade` missing type hints (-> return)

### `cyclone/celery_tasks/workflows/add_examples.py`

- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L48) — `add_examples` cyclomatic complexity 37 (> 10)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L48) — `add_examples` missing type hints (context, -> return)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L100) — await inside a loop; if independent, gather them concurrently

### `cyclone/celery_tasks/workflows/save_predictions.py`

- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L47) — await inside a loop; if independent, gather them concurrently
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L118) — `save_predictions` missing type hints (-> return)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L168) — await inside a loop; if independent, gather them concurrently

### `cyclone/celery_tasks/workflows/split_data.py`

- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L64) — `split_training_data` cyclomatic complexity 22 (> 10)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L64) — `split_training_data` missing type hints (-> return)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L111) — await inside a loop; if independent, gather them concurrently

### `cyclone/database/labelsets.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L30) — `targetname_ids` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L34) — `targets_are_subset` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L81) — `targetname_ids` missing type hints (-> return)

### `cyclone/database/migrations/spans/external_ops.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L12) — `gather_usage` missing type hints (ds, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L83) — `update_services` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L117) — `_update_services` missing type hints (mg_updates, q_updates, comp_updates, -> return)

### `cyclone/database/queries/examples/examples.py`

- 🔧 **low** `PY-STYLE-FILE-SIZE` (L1) — file is 1028 lines (> 800); split into a package
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L59) — `_unnest_int_array_subquery` missing type hints (-> return)
- 🔎 **low** `PY-OOP-FREE-FN-ORCHESTRATOR` (L90) — 15 free functions thread `db_session` between them; use a coordinator class

### `cyclone/database/queries/filtered_examples.py`

- 🔎 **medium** `PY-SEC-INSECURE-RANDOM` (L178) — `random.random(...)` is not cryptographically secure; unsafe for tokens/keys
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L19) — `query_examples_with_predictions` cyclomatic complexity 18 (> 10)
- 🔎 **low** `PY-OOP-LONG-PARAM-LIST` (L19) — `query_examples_with_predictions` takes 10 parameters (> 6); group into an object

### `cyclone/database/queries/labelsets.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L34) — `labelset_update_status` missing type hints (status)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L213) — await inside a loop; if independent, gather them concurrently
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L512) — `check_labelset_permission` missing type hints (permission)

### `tests/celery_tasks/workflows/conftest.py`

- 🔧 **high** `PY-ASYNC-SYNC-IO` (L30) — sync `open(...)` blocks the event loop inside async `load_data_mock_data`
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L144) — await inside a loop; if independent, gather them concurrently
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L147) — await inside a loop; if independent, gather them concurrently

### `tests/celery_tasks/workflows/test_split_data.py`

- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L158) — await inside a loop; if independent, gather them concurrently
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L162) — await inside a loop; if independent, gather them concurrently
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L166) — await inside a loop; if independent, gather them concurrently

### `tests/database/migrations/test_manual_migrations_async.py`

- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L85) — async `execute` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L542) — async `_scoped_session_ctx` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L546) — async `_app_scoped_session` has no await/async-with/async-for; make it sync

### `tests/routes/example/test_label_examples.py`

- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L181) — await inside a loop; if independent, gather them concurrently
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L230) — await inside a loop; if independent, gather them concurrently
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L255) — await inside a loop; if independent, gather them concurrently

### `alembic/manual_migrations/populate_ocr_used.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L18) — `main` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L58) — `run_main` missing type hints (-> return)

### `alembic/manual_migrations/reindex_rows_in_dataset.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L10) — `main` missing type hints (-> return)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L29) — await inside a loop; if independent, gather them concurrently

### `alembic/manual_migrations/reverse_span_migration.py`

- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L18) — `main` cyclomatic complexity 11 (> 10)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L18) — `main` missing type hints (-> return)

### `alembic/versions_legacy/05a29c2567cf_added_number_of_labeled_points_to_.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L28) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/160c69329d8b_add_labelinstance.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L85) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/1e017eb89d4c_add_frozenlabelset.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L43) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/1e36c3e3ea40_datarow.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L102) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/22cac615366b_audit_datapoints.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L31) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/28aa119b10ff_change_num_labelers_default.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L31) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/2f4fed877dcf_add_target_type.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L33) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L41) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/335687920bf7_dataset_delete_status.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L30) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L47) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/4256adf7f691_subset_no_cascade.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L32) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/4a3004b1ca94_add_datafiles.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L65) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/4c8fdcdf0d04_add_default_subset_id.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L21) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L35) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/4c8fdcdf0d05_default_subset_id_migration.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L42) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L104) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/515df41c6591_adding_counting_columns.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L47) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L80) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/51715468df06_add_indices.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L30) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L80) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/5560e59c7914_add_targetname_mapping_to_frozenlabelset.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L21) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L32) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/58eb1b060798_add_link_to_exports.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L24) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/5b34295c0780_more_counts_for_labels.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L31) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/5f73e3294dbb_add_new_image_datatype.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L34) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L50) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/637da133f222_add_offset_to_df_page.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L19) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L28) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/65b901abc724_add_fkey_indexing.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L19) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L37) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/6eed0d4724a2_migrate_export_links.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L33) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L57) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/75f111379f0d_add_file_info.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L28) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/7a3518051316_add_indexes.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L18) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L45) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/7d55fe936c49_filetype_enum_for_datafile.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L23) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L29) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/7d69eafc422f_add_subsets.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L36) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/803fd223d497_add_subset_id_to_featurecolumn.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L41) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/808d4ded59ee_target_names.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L39) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/8953a9ee518b_add_example_spangroup_assoc_table.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L90) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/8c662b67987c_add_scores_to_label.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L29) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/8c9a4c4a53ba_datafile_deleted_flag.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L30) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/8cbedb0e8568_remove_classes.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L26) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/8fb40ff7b381_change_target_name_position_to_nullable.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L21) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L29) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/a051419e65a1_add_ocr_used_on_datacolumn.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L24) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/a5a4f96c6bc3_auditing_labels.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L21) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L42) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/aa2dcaf6570c_add_example_spangroup_id_index.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L18) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L24) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/ac9d37ce5f2b_add_task_type.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L29) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L50) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/b0956d938e1e_add_original_datafile_col.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L35) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/b14b336d4a87_userpermission_table.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L68) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/b1acd2e5e67d_.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L26) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/b611dd1b8620_swap_foreign_key_for_label.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L42) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L71) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/b77ab4cb9556_add_celery_task_id_to_datafile.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L32) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/b8f1a1fe7f96_initial_schema.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L235) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/bac24e2f954d_default_row_count.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L26) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/bcaf02152e43_dataset_error_info.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L26) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/c4e2a2ec9cb2_add_index_to_label_row_index.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L26) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/d0d98d994ccf_add_labelsetpoint_row_index_index.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L28) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/db4b89c74c67_export_column_ids_and_subset_ids.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L31) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/f02afee5edc6_add_labelset_type.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L23) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L52) — `downgrade` missing type hints (-> return)

### `alembic/versions_legacy/f2e71f22fbaa_added_datafile_page_object_to_support_.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `upgrade` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L49) — `downgrade` missing type hints (-> return)

### `cyclone/celery_tasks/file_pipeline/download.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L35) — `complete_download_file` missing type hints (context, -> return)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L45) — await inside a loop; if independent, gather them concurrently

### `cyclone/celery_tasks/workflows/bundle_docs.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L23) — `bundle_docs` missing type hints (-> return)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L32) — await inside a loop; if independent, gather them concurrently

### `cyclone/celery_tasks/workflows/load_data/load_data_task.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L67) — `load_data` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L151) — `load_examples_by_ids` missing type hints (context, -> return)

### `cyclone/celery_tasks/workflows/load_data/load_predictions_task.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L28) — `raw_predictions_to_labelgroup` missing type hints (context, -> return)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L38) — await inside a loop; if independent, gather them concurrently

### `cyclone/celery_tasks/workflows/load_data/load_tables_task.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L11) — `load_tables` missing type hints (context, -> return)
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L14) — await inside a loop; if independent, gather them concurrently

### `cyclone/celery_tasks/workflows/load_data/spansource_loader.py`

- 🔎 **medium** `PY-CORRECT-SWALLOWED-EXCEPTION` (L461) — exception silently swallowed (no log, re-raise, or handling)
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L138) — `_load_datapage` cyclomatic complexity 17 (> 10)

### `cyclone/config.py`

- 🔎 **low** `PY-OOP-THIN-WRAPPER` (L89) — thin wrapper `EXPORT_PATH` forwards its args verbatim; call the underlying directly
- 🔎 **low** `PY-OOP-THIN-WRAPPER` (L94) — thin wrapper `EXPORT_LINK` forwards its args verbatim; call the underlying directly

### `cyclone/database/datapoints.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L56) — `value` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L60) — `value` missing type hints (set_value, -> return)

### `cyclone/database/datasetusers.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L9) — `check_permission` missing type hints (self, user_id, dataset, db_session, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L47) — `permissions` missing type hints (-> return)

### `cyclone/database/migrations/remove_deleting_users.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L12) — `main` missing type hints (-> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L40) — `run_main` missing type hints (-> return)

### `cyclone/database/migrations/targetname_utils.py`

- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L9) — `get_targets_by_labelset` cyclomatic complexity 11 (> 10)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L9) — `get_targets_by_labelset` missing type hints (quiet, -> return)

### `cyclone/database/models/datasetuser.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `default_label_count` missing type hints (value, -> return)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L26) — `permissions_to_str` missing type hints (value, -> return)

### `cyclone/database/queries/add_data.py`

- 🔎 **low** `PY-OOP-LONG-PARAM-LIST` (L21) — `create_labels_from_csv` takes 7 parameters (> 6); group into an object
- 🔎 **low** `PY-OOP-LONG-PARAM-LIST` (L58) — `create_datapoints_single_column` takes 8 parameters (> 6); group into an object

### `cyclone/database/queries/datarow.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L39) — `_process_row` missing type hints (column_index, size, row, -> return)
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L51) — `get_rows_by_datarow_ids` cyclomatic complexity 13 (> 10)

### `cyclone/database/queries/examples/list_examples.py`

- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L129) — `list_examples_query` cyclomatic complexity 23 (> 10)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L129) — `list_examples_query` missing type hints (-> return)

### `cyclone/database/queries/training_data.py`

- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L366) — `calculate_data_duplicates` cyclomatic complexity 21 (> 10)
- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L366) — `calculate_data_duplicates` missing type hints (task_type)

### `cyclone/http_contracts.py`

- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L15) — `build_openapi_contract` returns dict[str, Any]; return a typed model
- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L19) — `write_openapi_artifacts` takes dict[str, Any] `schema`; accept a typed model

### `cyclone/services/api_exports.py`

- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L31) — `create_export` cyclomatic complexity 11 (> 10)
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L113) — `list_exports` cyclomatic complexity 13 (> 10)

### `tests/celery_tasks/file_pipeline/test_file_failure.py`

- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L41) — async `err_task` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L53) — async `_scoped_session_ctx` has no await/async-with/async-for; make it sync

### `tests/celery_tasks/workflows/test_add_examples.py`

- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L112) — await inside a loop; if independent, gather them concurrently
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L186) — await inside a loop; if independent, gather them concurrently

### `tests/database/migrations/test_spans_migration.py`

- 🔧 **medium** `PY-STYLE-INLINE-IMPORT` (L449) — inline import; move to module top
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L502) — await inside a loop; if independent, gather them concurrently

### `tests/queries/test_examples.py`

- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L160) — await inside a loop; if independent, gather them concurrently
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L206) — await inside a loop; if independent, gather them concurrently

### `tests/queries/test_featurecolumns.py`

- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L34) — async `execute` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L61) — await inside a loop; if independent, gather them concurrently

### `tests/routes/dataset/test_add_data_csv.py`

- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L32) — async `launch_mock` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L211) — async `fake_update` has no await/async-with/async-for; make it sync

### `tests/routes/dataset/test_add_data_files.py`

- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L37) — async `launch_mock` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L185) — async `fake_update` has no await/async-with/async-for; make it sync

### `tests/routes/dataset/test_pipeline.py`

- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L86) — async `fake_launch` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L506) — async `fake_add_datafiles` has no await/async-with/async-for; make it sync

### `tests/routes/labelset/test_target_names.py`

- 🔧 **medium** `PY-STYLE-INLINE-IMPORT` (L78) — inline import; move to module top
- 🔧 **medium** `PY-STYLE-INLINE-IMPORT` (L80) — inline import; move to module top

### `tests/utils/test_load_data_utils.py`

- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L285) — async `test_raw_prediction_builder_prediction_batches` has no await/async-with/async-for; make it sync
- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L373) — async `test_raw_prediction_builder_dataset_type_reset_page_nums` has no await/async-with/async-for; make it sync

### `tools/validate_contracts.py`

- 🔧 **medium** `PY-TYPING-UNTYPED-DICT` (L63) — `write_summary` takes dict[str, Any] `payload`; accept a typed model
- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L127) — `main` cyclomatic complexity 16 (> 10)

### `alembic/manual_migrations/create_frozen_labelsets.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L8) — `main` missing type hints (-> return)

### `alembic/manual_migrations/rm_label_dsuser_fkey.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L13) — `main` missing type hints (-> return)

### `cyclone/celery_tasks/file_pipeline/extract.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L22) — `complete_extract_file` missing type hints (context, -> return)

### `cyclone/celery_tasks/file_pipeline/file_failure.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L20) — `file_failure_errback` missing type hints (-> return)

### `cyclone/celery_tasks/task_utils/utils.py`

- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L17) — await inside a loop; if independent, gather them concurrently

### `cyclone/database/exports.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L41) — `labelset_id` missing type hints (-> return)

### `cyclone/database/migrations/create_frozen_labelsets.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L56) — `create_frozen_labelsets` missing type hints (-> return)

### `cyclone/database/models/dataset.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L33) — `permissions_to_str` missing type hints (value, -> return)

### `cyclone/database/models/labelset.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L22) — `permissions_to_str` missing type hints (v, -> return)

### `cyclone/database/queries/datafiles.py`

- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L343) — await inside a loop; if independent, gather them concurrently

### `cyclone/database/queries/exports.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L55) — `get_exports_query` missing type hints (-> return)

### `cyclone/database/queries/spangroups.py`

- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L126) — `adjust_child_spans` cyclomatic complexity 12 (> 10)

### `cyclone/database/queries/subsets.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L220) — `get_row_ids_for_subsets_query` missing type hints (-> return)

### `cyclone/dependencies/permissions.py`

- 🔎 **high** `PY-ASYNC-UNLOCKED-LAZY-INIT` (L31) — check-then-set lazy init of `self._token` without a lock (race)

### `cyclone/routes/models/examples.py`

- 🔎 **low** `PY-OOP-FLAT-FIELD-MODEL` (L17) — flat model `ExampleFilters` has 12 fields; compose sub-models

### `cyclone/services/datafiles.py`

- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L38) — `list_datafiles` cyclomatic complexity 14 (> 10)

### `cyclone/services/datasets.py`

- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L101) — `list_datasets` cyclomatic complexity 16 (> 10)

### `cyclone/services/permissions.py`

- 🔎 **low** `PY-OOP-HIGH-COMPLEXITY` (L141) — `check_dataset_permissions` cyclomatic complexity 14 (> 10)

### `cyclone/services/search.py`

- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L146) — async `get_mapped_texts` has no await/async-with/async-for; make it sync

### `cyclone/utils/name.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L4) — `match_and_increment` missing type hints (name_to_match, names, -> return)

### `scripts/check_example_and_labels_assoc.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L25) — `main` missing type hints (-> return)

### `scripts/check_spangroups.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L6) — `main` missing type hints (-> return)

### `scripts/freeze_dataset.py`

- 🔧 **low** `PY-TYPING-MISSING-HINTS` (L14) — `main` missing type hints (-> return)

### `tests/celery_tasks/workflows/test_agent_bundler.py`

- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L34) — await inside a loop; if independent, gather them concurrently

### `tests/celery_tasks/workflows/test_save_predictions.py`

- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L108) — async `mock_predict` has no await/async-with/async-for; make it sync

### `tests/contracts/http/test_contract_edges.py`

- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L11) — async `cleanup` has no await/async-with/async-for; make it sync

### `tests/database/migrations/test_remove_deleting_users.py`

- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L53) — async `_scoped_session_ctx` has no await/async-with/async-for; make it sync

### `tests/fixtures/dataset.py`

- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L223) — await inside a loop; if independent, gather them concurrently

### `tests/routes/dataset/test_delete_dataset.py`

- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L112) — async `expire_permissions` has no await/async-with/async-for; make it sync

### `tests/routes/dataset/test_get_labelsets_info.py`

- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L87) — async `get` has no await/async-with/async-for; make it sync

### `tests/routes/example/test_example_contexts.py`

- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L28) — await inside a loop; if independent, gather them concurrently

### `tests/routes/example/test_example_tasks.py`

- 🔧 **medium** `PY-STYLE-INLINE-IMPORT` (L186) — inline import; move to module top

### `tests/routes/labelset/test_compare_labelset.py`

- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L68) — async `mock_get_labelset_permissions` has no await/async-with/async-for; make it sync

### `tests/routes/labelset/test_copy_labelset.py`

- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L97) — await inside a loop; if independent, gather them concurrently

### `tests/routes/labelset/test_frozen_labelsets.py`

- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L64) — async `mock_get_labelset_permissions` has no await/async-with/async-for; make it sync

### `tests/routes/labelset/test_labelset_stats.py`

- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L172) — async `mock_load_hashed_training_data` has no await/async-with/async-for; make it sync

### `tests/routes/permissions/test_datasets_route_permissions.py`

- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L9) — async `cleanup` has no await/async-with/async-for; make it sync

### `tests/routes/permissions/test_rainbow_permissions.py`

- 🔎 **low** `PY-ASYNC-SEQUENTIAL-AWAITS` (L129) — await inside a loop; if independent, gather them concurrently

### `tests/services/test_csv_processing.py`

- 🔎 **low** `PY-ASYNC-NO-AWAIT-BODY` (L330) — async `fake_create_labels_from_csv` has no await/async-with/async-for; make it sync
