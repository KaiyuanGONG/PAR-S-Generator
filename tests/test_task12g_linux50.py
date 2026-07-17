from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import finalize_task12g_linux50_local as finalizer  # noqa: E402


def test_task12g_defaults_use_separate_local_roots() -> None:
    args = finalizer._parser().parse_args([])

    assert args.preflight_root.name == "task12f_linux50_preflight_v2"
    assert args.bundle_root.name == "pars_v2_task12f_linux50_bundle_v2"
    assert args.results_root.name == "task12f_linux50_results"
    assert args.output_root.name == "pars_v2_linux50_v2"
    assert args.work_root.name == "pars_v2_linux50_v2_work"
    assert args.validate_only is False
    assert args.output_root != args.results_root
    assert args.output_root != args.preflight_root


def test_task12g_never_launches_simind() -> None:
    source = (
        REPO_ROOT / "scripts" / "finalize_task12g_linux50_local.py"
    ).read_text(encoding="utf-8")

    assert "run_simind_case" not in source
    assert "SimindRunSpec" not in source


def test_task12g_freezes_full_local_and_remote_evidence_set() -> None:
    required = set(finalizer.REQUIRED_ARTIFACTS)

    assert {
        "phantom_npz",
        "metadata_json",
        "projection_a00",
        "projection_mhd",
        "projection_res",
        "projection_spe",
        "simind_run_provenance",
        "simind_source_bin",
        "simind_density_bin",
        "pilot_plan",
        "pilot_runtime",
        "pilot_preflight",
        "pilot_input_bundle",
        "preflight_byte_identity",
        "generation_plan",
        "split_plan",
        "task12f_bundle_manifest",
        "task12f_execution_plan",
        "task12f_case_preflight",
        "task12f_remote_preflight",
        "task12f_node_complete",
        "task12f_case_marker",
        "task12f_master",
        "population_profile",
        "scanner_config",
        "evidence_registry",
        "task12e_acceptance",
        "simind_smc_snapshot",
        "simind_ini_snapshot",
    } <= required


def test_frozen_source_validation_ignores_new_finalizer_but_rejects_drift(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    source = repo / "src" / "core" / "production_v2.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"frozen-generation-source")
    source_files = [
        {
            "path": "src/core/production_v2.py",
            "size_bytes": source.stat().st_size,
            "sha256": finalizer.sha256_file(source),
        }
    ]
    binding = {
        "git_commit": "a" * 40,
        "source_manifest_sha256": finalizer.canonical_json_sha256(source_files),
        "source_files": source_files,
    }

    observed = finalizer._validate_frozen_source_files(repo, binding)
    assert observed["file_count"] == 1
    assert observed["frozen_git_commit"] == "a" * 40

    (repo / "scripts").mkdir()
    (repo / "scripts" / "finalize_task12g_linux50_local.py").write_text(
        "new local finalizer", encoding="utf-8"
    )
    finalizer._validate_frozen_source_files(repo, binding)

    source.write_bytes(b"drifted-generation-source")
    with pytest.raises(RuntimeError, match="frozen generation source drift"):
        finalizer._validate_frozen_source_files(repo, binding)


def test_downloaded_result_is_wrapped_without_execution(tmp_path: Path) -> None:
    case_id = "case_00000"
    case_dir = tmp_path / case_id
    case_dir.mkdir()
    provenance = {
        "schema_version": "pars_simind_run_v2",
        "status": "complete",
        "case_id": case_id,
        "exit_code": 0,
        "expected_shape": [60, 128, 128],
        "command": ["simind", "smc", case_id, "/NN:1", "/RR:7"],
        "started_utc": "2026-07-15T00:00:00Z",
        "finished_utc": "2026-07-15T00:01:00Z",
        "completion_audit": {
            "sha256": {
                "a00": "a" * 64,
                "mhd": "b" * 64,
                "res": "c" * 64,
                "spe": "d" * 64,
            }
        },
    }
    (case_dir / "run_provenance.json").write_text(
        json.dumps(provenance), encoding="utf-8"
    )

    result = finalizer._completed_downloaded_result(case_dir, case_id)

    assert result.success is True
    assert result.exit_code == 0
    assert result.expected_shape == (60, 128, 128)
    assert result.final_dir == case_dir.resolve()
    assert result.output_hashes == provenance["completion_audit"]["sha256"]


def test_downloaded_result_rejects_incomplete_provenance(tmp_path: Path) -> None:
    case_id = "case_00000"
    case_dir = tmp_path / case_id
    case_dir.mkdir()
    (case_dir / "run_provenance.json").write_text(
        json.dumps(
            {
                "schema_version": "pars_simind_run_v2",
                "status": "failed",
                "case_id": case_id,
                "exit_code": 1,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="completed Linux SIMIND provenance"):
        finalizer._completed_downloaded_result(case_dir, case_id)


def test_runtime_allows_unrelated_pip_distribution_removal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    python = {
        "executable": "python.exe",
        "executable_sha256": "a" * 64,
        "version": "3.11.14",
        "prefix": str(Path(sys.prefix).resolve()),
    }
    modules = [
        {
            "name": "numpy",
            "version": "2.4.3",
            "module_file_sha256": "b" * 64,
        },
        {
            "name": "scipy",
            "version": "1.17.1",
            "module_file_sha256": "c" * 64,
        },
        {
            "name": "skimage",
            "version": "0.26.0",
            "module_file_sha256": "d" * 64,
        },
    ]
    frozen = {
        "python": python,
        "critical_modules": modules,
        "python_distributions": [
            {"name": "numpy", "version": "2.4.3"},
            {"name": "pypdf", "version": "6.11.0"},
        ],
        "python_distributions_sha256": "e" * 64,
        "conda": {
            "resolved_prefix": str(Path(sys.prefix).resolve()),
            "records_sha256": "f" * 64,
            "history_sha256": "0" * 64,
        },
    }
    observed = {
        "python": python,
        "critical_modules": modules,
        "python_distributions": [{"name": "numpy", "version": "2.4.3"}],
        "python_distributions_sha256": "1" * 64,
    }
    monkeypatch.setattr(finalizer, "capture_python_runtime", lambda: observed)
    monkeypatch.setattr(
        finalizer,
        "_conda_record_binding",
        lambda _prefix: dict(frozen["conda"]),
    )

    audit = finalizer._validate_python_runtime(frozen)

    assert audit["status"] == "pass"
    assert audit["noncritical_distribution_drift"]["removed"] == {
        "pypdf": "6.11.0"
    }


def test_semantic_summary_comparison_uses_json_container_semantics() -> None:
    observed = {
        "case_id": "case_00000",
        "liver_extent_mm_zyx": (176.8, 167.96, 225.42),
    }
    frozen = {
        "case_id": "case_00000",
        "liver_extent_mm_zyx": [176.8, 167.96, 225.42],
    }

    assert finalizer._semantic_summaries_equal(observed, frozen) is True


def test_zero_case_failed_resume_can_refresh_only_finalizer_hash(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    work = tmp_path / "work"
    output.mkdir()
    work.mkdir()
    existing = {
        "schema_version": finalizer.TASK12G_RUNTIME_SCHEMA,
        "status": "bound",
        "finalizer_git_commit": "a" * 40,
        "finalizer_sha256": "b" * 64,
        "dataset": {"case_count": 50},
    }
    expected = {
        **existing,
        "finalizer_sha256": "c" * 64,
    }
    finalizer.atomic_write_json(output / "PILOT_RUNTIME.json", existing)
    finalizer.atomic_write_json(
        work / "PROGRESS.json",
        {
            "schema_version": finalizer.TASK12G_PROGRESS_SCHEMA,
            "status": "failed",
            "completed_case_ids": [],
            "completed_count": 0,
            "total_count": 50,
            "remaining_count": 50,
            "case_summaries": [],
            "go_for_500_case_generation": False,
        },
    )

    path = finalizer._load_or_write_runtime(
        output,
        work,
        expected,
        "resume",
    )

    assert json.loads(path.read_text(encoding="utf-8")) == expected


def test_resume_cannot_refresh_runtime_after_formal_case_exists(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    work = tmp_path / "work"
    (output / "cases" / "case_00000").mkdir(parents=True)
    work.mkdir()
    existing = {
        "schema_version": finalizer.TASK12G_RUNTIME_SCHEMA,
        "status": "bound",
        "finalizer_git_commit": "a" * 40,
        "finalizer_sha256": "b" * 64,
    }
    expected = {
        **existing,
        "finalizer_sha256": "c" * 64,
    }
    finalizer.atomic_write_json(output / "PILOT_RUNTIME.json", existing)

    with pytest.raises(RuntimeError, match="runtime binding changed"):
        finalizer._load_or_write_runtime(
            output,
            work,
            expected,
            "resume",
        )


def test_resume_ignores_only_informational_distribution_drift(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    work = tmp_path / "work"
    (output / "cases" / "case_00000").mkdir(parents=True)
    work.mkdir()
    stable_runtime = {
        "status": "pass",
        "python": {
            "executable": "python.exe",
            "executable_sha256": "a" * 64,
            "version": "3.11.14",
            "prefix": "SPECT",
        },
        "critical_modules": [
            {
                "name": "numpy",
                "version": "2.4.3",
                "module_file_sha256": "b" * 64,
            }
        ],
        "conda": {
            "resolved_prefix": "SPECT",
            "records_sha256": "c" * 64,
            "history_sha256": "d" * 64,
        },
    }
    existing = {
        "schema_version": finalizer.TASK12G_RUNTIME_SCHEMA,
        "status": "bound",
        "finalizer_git_commit": "a" * 40,
        "finalizer_sha256": "e" * 64,
        "local_python_runtime": {
            **stable_runtime,
            "python_distributions_sha256": "f" * 64,
            "noncritical_distribution_drift": {
                "exact_match": True,
                "added": {},
                "removed": {},
                "changed": {},
            },
        },
    }
    expected = {
        **existing,
        "local_python_runtime": {
            **stable_runtime,
            "python_distributions_sha256": "0" * 64,
            "noncritical_distribution_drift": {
                "exact_match": False,
                "added": {},
                "removed": {"pypdf": "6.11.0"},
                "changed": {},
            },
        },
    }
    runtime_path = output / "PILOT_RUNTIME.json"
    finalizer.atomic_write_json(runtime_path, existing)

    path = finalizer._load_or_write_runtime(
        output,
        work,
        expected,
        "resume",
    )

    assert path == runtime_path
    assert json.loads(path.read_text(encoding="utf-8")) == existing


def test_frozen_dataset_can_be_reaudited_by_newer_finalizer(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    work = tmp_path / "work"
    output.mkdir()
    work.mkdir()
    existing = {
        "schema_version": finalizer.TASK12G_RUNTIME_SCHEMA,
        "status": "bound",
        "finalizer_git_commit": "a" * 40,
        "finalizer_sha256": "b" * 64,
        "dataset": {"case_count": 50},
    }
    expected = {
        **existing,
        "finalizer_sha256": "c" * 64,
    }
    runtime_path = output / "PILOT_RUNTIME.json"
    finalizer.atomic_write_json(runtime_path, existing)
    finalizer.atomic_write_json(
        output / finalizer.DATASET_COMPLETE_FILENAME,
        {"status": "complete"},
    )

    path = finalizer._load_or_write_runtime(
        output,
        work,
        expected,
        "resume",
    )

    assert path == runtime_path
    assert json.loads(path.read_text(encoding="utf-8")) == existing
