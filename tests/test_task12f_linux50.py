from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from task12f_linux50_common import (  # noqa: E402
    BUNDLE_SCHEMA,
    PLAN_SCHEMA,
    atomic_write_json,
    cases_for_node,
    sha256_file,
    validate_bundle,
)
import build_task12f_linux50_bundle as task12f_builder  # noqa: E402
from build_task12f_linux50_bundle import _replace_directory_with_retry  # noqa: E402
from core.liver_geometry import GridSpecV2  # noqa: E402
from core.phantom_generator import TumorTargetRetryExhaustedError  # noqa: E402
from core.production_v2 import (  # noqa: E402
    population_coverage,
    prepare_population_case,
    summarize_prepared_population_case,
)
from core.schemas_v2 import load_evidence_registry, load_profile  # noqa: E402


def test_preflight_pool_failure_is_case_bound_and_pickle_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(**_: object) -> object:
        raise TumorTargetRetryExhaustedError("case_00009", 32, ())

    monkeypatch.setattr(task12f_builder, "_prepare_case_job", fail)
    with pytest.raises(RuntimeError, match="case_00009.*exhausted 32") as caught:
        task12f_builder._prepare_case_job_for_pool(case_id="case_00009")
    restored = pickle.loads(pickle.dumps(caught.value))
    assert str(restored) == str(caught.value)


def test_negative_role_requires_zero_weight_without_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    summary = {
        "case_id": "negative_00000",
        "status": "pass",
        "failures": [],
        "mismatch_challenge": False,
        "population_weight": 0.0,
    }

    class Prepared:
        pass

    monkeypatch.setattr(task12f_builder, "load_evidence_registry", lambda _: object())
    monkeypatch.setattr(task12f_builder, "load_profile", lambda *_: object())
    monkeypatch.setattr(task12f_builder, "prepare_negative_case", lambda *_, **__: Prepared())
    monkeypatch.setattr(
        task12f_builder, "summarize_prepared_negative_case", lambda _: dict(summary)
    )
    staging = tmp_path / "staging"
    final = tmp_path / "final"
    observed = task12f_builder._prepare_case_job(
        case_id="negative_00000",
        entry={
            "case_family_id": "negative_family_00000",
            "split": "test",
            "profile_id": "negative_control_v2",
            "population_weight": 0.0,
            "sampling_probability": 0.0,
            "mismatch_challenge": False,
        },
        generation_profile_path="profile.json",
        dataset_role="negative",
        registry_path="registry.json",
        grid_shape=(16, 16, 16),
        voxel_size_mm=1.0,
        global_seed=1,
        base_histories=1,
        max_tumor_attempts=64,
        staging_path=str(staging),
        final_path=str(final),
    )
    assert observed["population_weight"] == 0.0
    assert observed["mismatch_challenge"] is False
    assert (final / "CASE_PREFLIGHT.json").is_file()


def test_task12f_config_freezes_linux_only_50_case_release() -> None:
    config = json.loads(
        (REPO_ROOT / "configs" / "task12f_linux50_v2.json").read_text(
            encoding="utf-8"
        )
    )
    assert config["dataset"]["case_count"] == 50
    assert config["dataset"]["dataset_version"] == "2.0.0-linux50-v2"
    assert config["dataset"]["split_ratios"] == {
        "train": 0.8,
        "val": 0.1,
        "test": 0.1,
    }
    assert config["execution"]["requested_parallel_by_node"] == {
        "cnc5": 17,
        "cnc7": 17,
        "cnc8": 16,
    }
    assert config["challenge_design"]["mismatch_cases_per_split"] == {
        "train": 1,
        "val": 1,
        "test": 1,
    }
    acceptance = REPO_ROOT / config["paths"]["task12e_acceptance"]
    assert sha256_file(acceptance) == config["frozen_evidence"][
        "task12e_acceptance_sha256"
    ]
    assert config["frozen_evidence"]["windows_cases_forbidden"] is True


def test_task12f_round_robin_assignment_is_17_17_16() -> None:
    nodes = ["cnc5", "cnc7", "cnc8"]
    cases = [
        {"case_id": f"case_{index:05d}", "node_id": nodes[index % 3]}
        for index in range(50)
    ]
    plan = {"cases": cases}
    assert {node: len(cases_for_node(plan, node)) for node in nodes} == {
        "cnc5": 17,
        "cnc7": 17,
        "cnc8": 16,
    }


def test_task12f_freezes_one_zero_weight_mismatch_challenge_per_split() -> None:
    entries = [
        {
            "case_id": f"case_{index:05d}",
            "split": split,
            "population_weight": 1.0,
            "sampling_probability": 1.0 / 6.0,
        }
        for index, split in enumerate(("train", "train", "val", "val", "test", "test"))
    ]
    generation_plan = {
        "schema_version": "pars_generation_plan_v2",
        "case_count": 6,
        "entries": entries,
        "sha256": "stale",
    }
    updated = task12f_builder._apply_mismatch_challenge_design(
        generation_plan,
        {"mismatch_cases_per_split": {"train": 1, "val": 1, "test": 1}},
    )
    challenges = [entry for entry in updated["entries"] if entry["mismatch_challenge"]]
    population = [entry for entry in updated["entries"] if not entry["mismatch_challenge"]]
    assert {entry["split"] for entry in challenges} == {"train", "val", "test"}
    assert all(entry["population_weight"] == 0.0 for entry in challenges)
    assert all(entry["sampling_probability"] == 0.0 for entry in challenges)
    assert all(entry["challenge_labels"] == ["perfusion_mismatch"] for entry in challenges)
    assert all(entry["population_weight"] == 1.0 for entry in population)
    assert all(entry["sampling_probability"] == pytest.approx(1.0 / 3.0) for entry in population)
    assert updated["sha256"] != "stale"


def test_task12f_bundle_validation_binds_every_file(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    plan = {"schema_version": PLAN_SCHEMA, "cases": []}
    atomic_write_json(root / "TASK12F_PLAN.json", plan)
    payload = root / "payload.bin"
    payload.write_bytes(b"frozen")
    atomic_write_json(
        root / "BUNDLE_MANIFEST.json",
        {
            "schema_version": BUNDLE_SCHEMA,
            "status": "complete",
            "plan_relative_path": "TASK12F_PLAN.json",
            "plan_sha256": sha256_file(root / "TASK12F_PLAN.json"),
            "files": [
                {
                    "relative_path": "TASK12F_PLAN.json",
                    "size_bytes": (root / "TASK12F_PLAN.json").stat().st_size,
                    "sha256": sha256_file(root / "TASK12F_PLAN.json"),
                },
                {
                    "relative_path": "payload.bin",
                    "size_bytes": payload.stat().st_size,
                    "sha256": sha256_file(payload),
                },
            ],
        },
    )
    assert validate_bundle(root)["status"] == "complete"
    payload.write_bytes(b"drift")
    try:
        validate_bundle(root)
    except ValueError as exc:
        assert "mismatch" in str(exc)
    else:
        raise AssertionError("bundle drift was accepted")


def test_task12f_directory_publish_retries_transient_windows_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    (source / "payload.txt").write_text("frozen", encoding="utf-8")
    real_replace = __import__("os").replace
    attempts = 0

    def flaky_replace(first: Path, second: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("transient Windows directory lock")
        real_replace(first, second)

    monkeypatch.setattr(
        "build_task12f_linux50_bundle.os.replace", flaky_replace
    )
    monkeypatch.setattr(
        "build_task12f_linux50_bundle.time.sleep", lambda _seconds: None
    )
    _replace_directory_with_retry(source, destination)
    assert attempts == 3
    assert (destination / "payload.txt").read_text(encoding="utf-8") == "frozen"


def test_population_case_preparation_passes_strict_preflight(tmp_path: Path) -> None:
    registry = load_evidence_registry(REPO_ROOT / "configs" / "evidence_registry_v2.json")
    profile = load_profile(
        REPO_ROOT / "configs" / "population_tare_hcc_nopvi_v2.json",
        registry,
    )
    prepared = prepare_population_case(
        "case_00000",
        profile,
        GridSpecV2(),
        global_seed=20260714,
        base_histories=80000,
        work_dir=tmp_path / "case_00000",
        mismatch_challenge=True,
    )
    summary = summarize_prepared_population_case(prepared)
    assert summary["status"] == "pass"
    assert summary["source_weight_sum"] == pytest.approx(80000, abs=0.1)
    assert summary["array_manifest"]
    assert "mismatch:true" in population_coverage(summary)
    assert summary["injection_tumor_coverage_fraction"] < 1.0


def test_task12f_launchers_freeze_threads_and_screen() -> None:
    node = (REPO_ROOT / "scripts" / "run_task12f_linux50_node.sh").read_text(
        encoding="utf-8"
    )
    launcher = (
        REPO_ROOT / "scripts" / "launch_task12f_linux50_screen.sh"
    ).read_text(encoding="utf-8")
    assert "OMP_NUM_THREADS=1" in node
    assert "OPENBLAS_NUM_THREADS=1" in node
    assert "screen -dmS" in launcher
    assert "screen exited during startup" in launcher
