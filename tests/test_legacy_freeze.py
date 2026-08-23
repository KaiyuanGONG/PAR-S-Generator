from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.contracts import read_jsonl, sha256_file


def test_frozen_legacy_manifest_is_complete_and_self_consistent():
    root = Path("manifests/legacy-v1-weighted-mc")
    if not root.exists():
        pytest.fail("The required 500-case legacy freeze is missing")

    run = json.loads((root / "run.json").read_text(encoding="utf-8"))
    cases = read_jsonl(root / "cases.jsonl")
    splits = json.loads((root / "splits.json").read_text(encoding="utf-8"))["splits"]
    qc = json.loads((root / "qc_summary.json").read_text(encoding="utf-8"))
    inventory = [line for line in (root / "file_inventory.sha256").read_text(encoding="utf-8").splitlines() if line]

    assert run["freeze_mode"] == "read_only_checksum_reference"
    assert run["case_count"] == len(cases) == 500
    assert len(splits["train"]) == 400
    assert len(splits["val"]) == 50
    assert len(splits["test"]) == 50
    assert len(set(splits["train"] + splits["val"] + splits["test"])) == 500
    assert len(inventory) == 3000
    assert qc["all_projection_artifacts_strong_qc_passed"] is True
    assert qc["actual_to_nominal_diameter_ratio_by_mode"]["ellipsoid"]["mean"] == pytest.approx(
        1.4875708741099403
    )
    assert run["cases_manifest_sha256"] == sha256_file(root / "cases.jsonl")
    assert run["file_inventory_sha256"] == sha256_file(root / "file_inventory.sha256")
