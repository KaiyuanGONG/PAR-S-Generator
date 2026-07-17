"""Bind the proven Task 12F runtime engine to Task 13 formal schemas."""

from __future__ import annotations


def patch_runtime_contract() -> None:
    import task12f_linux50_common as common

    common.BUNDLE_SCHEMA = "pars_v2_task13_formal550_bundle_v1"
    common.PLAN_SCHEMA = "pars_v2_task13_formal550_plan_v1"
    common.NODE_COMPLETE_SCHEMA = "pars_v2_task13_formal550_node_complete_v1"
    common.MASTER_SCHEMA = "pars_v2_task13_formal550_master_v1"
    common.CASE_SCHEMA = "pars_v2_task13_formal550_case_v1"
    common.REMOTE_PREFLIGHT_SCHEMA = "pars_v2_task13_formal550_remote_preflight_v1"
    common.CASE_MARKER_FILENAME = "TASK13_CASE.json"
    common.NODE_FAILED_SCHEMA = "pars_v2_task13_formal550_node_failed_v1"
    common.MASTER_FILENAME = "TASK13_FORMAL550_MASTER.json"
    common.RESULT_ARCHIVE_NAME = "task13_formal550_results.tar.gz"
    common.RESULT_ARCHIVE_ROOT = "task13_formal550_results"
