"""Bind the proven Task 12F runtime engine to Task 13 formal schemas."""

from __future__ import annotations


_OVERRIDES = {
    "BUNDLE_SCHEMA": "pars_v2_task13_formal550_bundle_v1",
    "PLAN_SCHEMA": "pars_v2_task13_formal550_plan_v1",
    "NODE_COMPLETE_SCHEMA": "pars_v2_task13_formal550_node_complete_v1",
    "MASTER_SCHEMA": "pars_v2_task13_formal550_master_v1",
    "CASE_SCHEMA": "pars_v2_task13_formal550_case_v1",
    "REMOTE_PREFLIGHT_SCHEMA": "pars_v2_task13_formal550_remote_preflight_v1",
    "CASE_MARKER_FILENAME": "TASK13_CASE.json",
    "NODE_FAILED_SCHEMA": "pars_v2_task13_formal550_node_failed_v1",
    "MASTER_FILENAME": "TASK13_FORMAL550_MASTER.json",
    "RESULT_ARCHIVE_NAME": "task13_formal550_results.tar.gz",
    "RESULT_ARCHIVE_ROOT": "task13_formal550_results",
}


def patch_runtime_contract() -> dict[str, object]:
    import task12f_linux50_common as common

    previous = {name: getattr(common, name) for name in _OVERRIDES}
    for name, value in _OVERRIDES.items():
        setattr(common, name, value)
    return previous


def restore_runtime_contract(previous: dict[str, object]) -> None:
    import task12f_linux50_common as common

    if set(previous) != set(_OVERRIDES):
        raise ValueError("incomplete Task 13 runtime-contract snapshot")
    for name, value in previous.items():
        setattr(common, name, value)
