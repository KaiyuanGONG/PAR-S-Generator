from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = REPO_ROOT / "docs" / "audit" / "v1_baseline_snapshot.json"


def test_v1_generator_snapshot_captures_dirty_state_and_runtime() -> None:
    assert SNAPSHOT_PATH.is_file(), f"Missing V1 Generator snapshot: {SNAPSHOT_PATH}"
    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    assert snapshot["schema_version"] == "pars_generator_v1_baseline_snapshot_v1"
    assert snapshot["source_commit"] == "b19feb9c0dbd206961986da6bb38f212aedc3143"
    assert snapshot["source_worktree_state"]["branch"] == "master"
    assert len(snapshot["source_worktree_state"]["entries"]) == 11

    tests = snapshot["known_baseline_tests"]
    assert tests["collected"] == 24
    assert tests["passed"] == 23
    assert tests["failed"] == 1
    assert tests["status"] == "known_baseline_failure"
    assert tests["failure_id"] == "tests/test_ui_smoke.py::test_tumor_single_value_maps_to_min_max"
    assert tests["scope_decision"] == "record_and_continue_without_ui_changes"

    runtime = snapshot["runtime_artifacts"]
    assert runtime["installed_simind_exe"]["sha256"] == runtime["bundled_simind_exe"]["sha256"]
    for artifact in runtime.values():
        assert artifact["size_bytes"] > 0
        assert len(artifact["sha256"]) == 64

