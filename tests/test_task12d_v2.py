from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import scripts.run_task12d_v2 as runner  # noqa: E402
import scripts.finalize_task12d_v2 as finalizer  # noqa: E402
import scripts.validate_dataset_v2 as generator_gate  # noqa: E402
from generate_dataset_v2 import build_generation_plan  # noqa: E402
from core.pilot_v2 import (  # noqa: E402
    TASK12D_PLAN_SCHEMA_VERSION,
    load_pilot_plan,
)
from core.task12d_v2 import (  # noqa: E402
    TASK12D_CASE_COUNT,
    classify_task12d_roots,
    next_task12d_attempt_dir,
    require_task12d_coverage,
)


CONFIG = REPO_ROOT / "configs" / "task12d_fullchain_v2.json"


def test_task12d_plan_freezes_three_case_fullchain_coverage() -> None:
    plan = load_pilot_plan(CONFIG)
    coverage = require_task12d_coverage(plan)

    assert plan["schema_version"] == TASK12D_PLAN_SCHEMA_VERSION
    assert coverage["status"] == "pass"
    assert coverage["case_count"] == TASK12D_CASE_COUNT
    assert coverage["purpose"].endswith("no prevalence claim")
    assert all(coverage["gates"].values())
    assert coverage["maximum_diameters_mm"] == [20.0, 55.0, 90.0]
    assert len(set(coverage["rr_by_case"].values())) == 3


def test_task12d_split_is_frozen_before_generation() -> None:
    plan = load_pilot_plan(CONFIG)
    split, generation = build_generation_plan(
        dataset_id=plan["dataset_id"],
        dataset_version=plan["dataset_version"],
        dataset_role=plan["dataset_role"],
        profile_id="population_tare_hcc_nopvi_v2",
        case_count=3,
        family_size=1,
        global_seed=plan["global_seed"],
        ratios=plan["split_ratios"],
    )
    configured = [(case["case_id"], case["case_family_id"]) for case in plan["cases"]]
    planned = [
        (case["case_id"], case["case_family_id"]) for case in generation["entries"]
    ]
    assert configured == planned
    assert {entry["split"] for entry in generation["entries"]} == {
        "train",
        "val",
        "test",
    }
    assert generation["split_plan_sha256"] == split.sha256


def test_task12d_roots_and_attempts_are_fail_closed(tmp_path: Path) -> None:
    output = tmp_path / "task12d"
    work = tmp_path / "task12d_work"
    assert classify_task12d_roots(output, work, resume=False) == "fresh"
    with pytest.raises(RuntimeError, match="requires existing"):
        classify_task12d_roots(output, work, resume=True)
    output.mkdir()
    with pytest.raises(RuntimeError, match="inconsistent"):
        classify_task12d_roots(output, work, resume=True)
    work.mkdir()
    with pytest.raises(FileExistsError, match="--resume"):
        classify_task12d_roots(output, work, resume=False)
    assert classify_task12d_roots(output, work, resume=True) == "resume"

    first = next_task12d_attempt_dir(work, "case_00001")
    first.mkdir(parents=True)
    assert first.name == "attempt_001"
    assert next_task12d_attempt_dir(work, "case_00001").name == "attempt_002"


def test_task12d_runtime_resume_requires_exact_document(tmp_path: Path) -> None:
    output = tmp_path / "task12d"
    output.mkdir()
    expected = {
        "schema_version": "pars_v2_task12d_runtime_v1",
        "python_runtime": {"binding_sha256": "a" * 64},
    }
    runner._load_or_write_runtime(output, expected, "fresh")
    runner._load_or_write_runtime(output, expected, "resume")
    with pytest.raises(RuntimeError, match="resume is forbidden"):
        runner._load_or_write_runtime(
            output,
            {
                **expected,
                "python_runtime": {"binding_sha256": "b" * 64},
            },
            "resume",
        )


def test_task12d_runner_has_resume_and_batch_controls() -> None:
    args = runner._parser().parse_args(["--resume", "--max-cases", "1"])
    assert args.resume is True
    assert args.max_cases == 1
    assert args.output_root.name == "pars_v2_task12d3"


def test_task12d_preflight_and_fixture_cannot_launch_simind() -> None:
    preflight = (REPO_ROOT / "scripts" / "preflight_task12d_v2.py").read_text(
        encoding="utf-8"
    )
    assert "run_simind_case" not in preflight
    assert "SimindRunSpec" not in preflight


def test_task12d_finalize_defaults_to_separate_qa_root() -> None:
    args = finalizer._parser().parse_args([])
    assert args.dataset_root.name == "pars_v2_task12d3"
    assert args.qa_root.name == "pars_v2_task12d3_qa"
    assert args.qa_root != args.dataset_root
    assert "preflight_byte_identity" in generator_gate.TASK12D_REQUIRED_ARTIFACTS


def test_task12d_config_rejects_old_purpose(tmp_path: Path) -> None:
    plan = json.loads(CONFIG.read_text(encoding="utf-8"))
    plan["execution"]["purpose"] = "deterministic_smoke_only"
    path = tmp_path / "wrong.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="purpose label"):
        load_pilot_plan(path)
