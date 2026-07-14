from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import scripts.run_pilot15_v2 as runner  # noqa: E402
from generate_dataset_v2 import build_generation_plan  # noqa: E402
from core.pilot15_v2 import (  # noqa: E402
    PILOT15_CASE_COUNT,
    classify_run_root,
    next_attempt_dir,
    require_pilot15_coverage,
)
from core.pilot_v2 import load_pilot_plan  # noqa: E402
from core.schemas_v2 import load_evidence_registry, load_profile  # noqa: E402


def _profile():
    registry = load_evidence_registry(REPO_ROOT / "configs" / "evidence_registry_v2.json")
    return load_profile(
        REPO_ROOT / "configs" / "population_tare_hcc_nopvi_v2.json",
        registry,
    )


def test_pilot15_plan_freezes_stratified_coverage_and_unique_rr() -> None:
    plan = load_pilot_plan(REPO_ROOT / "configs" / "pilot15_v2.json")
    report = require_pilot15_coverage(plan, _profile())

    assert report["status"] == "pass"
    assert report["case_count"] == PILOT15_CASE_COUNT
    assert report["prevalence_claim"] is False
    assert report["counts"] == {
        "sex": {"female": 7, "male": 8},
        "liver_morphology": {"cirrhotic": 9, "normal": 6},
        "injection_territory": {
            "left_lobar": 4,
            "right_lobar": 4,
            "sector_proxy": 3,
            "whole_liver": 4,
        },
        "lesion_count": {"1": 11, "2": 2, "3": 1, "4": 1},
        "mismatch_true": 5,
        "heterogeneous_true": 11,
    }
    rr_values = list(report["rr_by_case"].values())
    assert len(set(rr_values)) == 15
    assert all(1 <= value <= 10_007 for value in rr_values)


def test_pilot15_split_is_frozen_before_generation() -> None:
    plan = load_pilot_plan(REPO_ROOT / "configs" / "pilot15_v2.json")
    split, generation = build_generation_plan(
        dataset_id=plan["dataset_id"],
        dataset_version=plan["dataset_version"],
        dataset_role=plan["dataset_role"],
        profile_id="population_tare_hcc_nopvi_v2",
        case_count=15,
        family_size=1,
        global_seed=plan["global_seed"],
        ratios=plan["split_ratios"],
    )
    configured = [
        (case["case_id"], case["case_family_id"]) for case in plan["cases"]
    ]
    planned = [
        (case["case_id"], case["case_family_id"])
        for case in generation["entries"]
    ]
    assert configured == planned
    counts = {
        name: sum(item["split"] == name for item in generation["entries"])
        for name in ("train", "val", "test")
    }
    assert counts == {"train": 9, "val": 3, "test": 3}
    assert generation["split_plan_sha256"] == split.sha256


def test_pilot15_plan_rejects_wrong_count_and_purpose(tmp_path: Path) -> None:
    plan = load_pilot_plan(REPO_ROOT / "configs" / "pilot15_v2.json")
    plan["cases"] = plan["cases"][:-1]
    wrong_count = tmp_path / "wrong_count.json"
    wrong_count.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly 15"):
        load_pilot_plan(wrong_count)

    plan = load_pilot_plan(REPO_ROOT / "configs" / "pilot15_v2.json")
    plan["execution"]["purpose"] = "quick_untracked_run"
    wrong_purpose = tmp_path / "wrong_purpose.json"
    wrong_purpose.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="purpose label"):
        load_pilot_plan(wrong_purpose)


def test_resume_root_state_is_fail_closed(tmp_path: Path) -> None:
    output = tmp_path / "pilot15"
    work = tmp_path / "pilot15_work"
    assert classify_run_root(output, work, resume=False) == "fresh"
    with pytest.raises(RuntimeError, match="requires an existing"):
        classify_run_root(output, work, resume=True)

    output.mkdir()
    with pytest.raises(RuntimeError, match="inconsistent"):
        classify_run_root(output, work, resume=True)
    work.mkdir()
    with pytest.raises(FileExistsError, match="--resume"):
        classify_run_root(output, work, resume=False)
    assert classify_run_root(output, work, resume=True) == "resume"


def test_resume_allocates_new_input_attempt_without_overwrite(tmp_path: Path) -> None:
    work = tmp_path / "pilot15_work"
    first = next_attempt_dir(work, "case_00004")
    assert first.name == "attempt_001"
    first.mkdir(parents=True)
    (first.parent / "attempt_002").mkdir()
    (first.parent / "diagnostic_notes").mkdir()
    assert next_attempt_dir(work, "case_00004").name == "attempt_003"


def test_runtime_resume_requires_exact_binding(tmp_path: Path) -> None:
    output = tmp_path / "pilot15"
    output.mkdir()
    expected = {
        "schema_version": "pars_v2_pilot15_runtime_v1",
        "pilot_plan_sha256": "a" * 64,
    }
    runner._load_or_write_runtime(output, expected, "fresh")
    runner._load_or_write_runtime(output, expected, "resume")
    with pytest.raises(RuntimeError, match="binding changed"):
        runner._load_or_write_runtime(
            output,
            {**expected, "pilot_plan_sha256": "b" * 64},
            "resume",
        )


def test_runner_exposes_explicit_resume_and_safe_batch_limit() -> None:
    args = runner._parser().parse_args(["--resume", "--max-cases", "2"])
    assert args.resume is True
    assert args.max_cases == 2


def test_preflight_script_has_no_simind_launch_entrypoint() -> None:
    source = (REPO_ROOT / "scripts" / "preflight_pilot15_v2.py").read_text(
        encoding="utf-8"
    )
    assert "run_simind_case" not in source
    assert "SimindRunSpec" not in source
