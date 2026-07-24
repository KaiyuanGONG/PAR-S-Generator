from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tarfile

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import finalize_task13_formal550_local as finalizer

from core import pilot_v2


def test_completed_metadata_supports_tumor_negative_case() -> None:
    tumor_union = np.zeros((4, 4, 4), dtype=bool)
    perfusion = np.zeros_like(tumor_union)
    perfusion[1:3, 1:3, 1:3] = True

    coverage, fraction_perfused = pilot_v2._tumor_perfusion_fractions(
        tumor_union,
        perfusion,
    )

    assert coverage == 1.0
    assert fraction_perfused == 0.0


def _write_result_archive(path: Path, *, member_name: str) -> None:
    payload = b'{"status":"pass"}\n'
    with tarfile.open(path, "w:gz") as stream:
        member = tarfile.TarInfo(member_name)
        member.size = len(payload)
        stream.addfile(member, io.BytesIO(payload))


def _write_sidecar(archive: Path, digest: str) -> Path:
    sidecar = Path(f"{archive}.sha256")
    sidecar.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    return sidecar


def test_task13_local_defaults_keep_download_staging_output_and_work_separate() -> None:
    roots = {
        finalizer.DEFAULT_DOWNLOAD_ROOT,
        finalizer.DEFAULT_STAGING_ROOT,
        finalizer.DEFAULT_OUTPUT_ROOT,
        finalizer.DEFAULT_WORK_ROOT,
    }

    assert len(roots) == 4
    assert finalizer.DEFAULT_ARCHIVE.parent == finalizer.DEFAULT_DOWNLOAD_ROOT
    assert finalizer.DEFAULT_RESULTS_ROOT == (
        finalizer.DEFAULT_STAGING_ROOT / "task13_formal550_results"
    )


def test_stage_results_archive_verifies_hash_and_extracts_atomically(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "task13_formal550_results.tar.gz"
    _write_result_archive(
        archive,
        member_name="task13_formal550_results/TASK13_FORMAL550_MASTER.json",
    )
    digest = finalizer.sha256_file(archive)
    sidecar = _write_sidecar(archive, digest)
    staging = tmp_path / "staging"

    results = finalizer.stage_results_archive(
        archive,
        sidecar,
        staging,
        resume=False,
    )

    assert results == staging / "task13_formal550_results"
    assert (results / "TASK13_FORMAL550_MASTER.json").is_file()
    marker = finalizer.read_json(staging / "STAGING_COMPLETE.json")
    assert marker["status"] == "complete"
    assert marker["archive_sha256"] == digest


def test_stage_results_archive_rejects_sha_mismatch(tmp_path: Path) -> None:
    archive = tmp_path / "task13_formal550_results.tar.gz"
    _write_result_archive(
        archive,
        member_name="task13_formal550_results/TASK13_FORMAL550_MASTER.json",
    )
    sidecar = _write_sidecar(archive, "0" * 64)

    with pytest.raises(finalizer.Formal550LocalError, match="SHA-256 mismatch"):
        finalizer.stage_results_archive(
            archive,
            sidecar,
            tmp_path / "staging",
            resume=False,
        )


def test_stage_results_archive_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "task13_formal550_results.tar.gz"
    _write_result_archive(archive, member_name="../escape.json")
    sidecar = _write_sidecar(archive, finalizer.sha256_file(archive))

    with pytest.raises(ValueError, match="unsafe archive member"):
        finalizer.stage_results_archive(
            archive,
            sidecar,
            tmp_path / "staging",
            resume=False,
        )


def test_task13_local_finalizer_never_launches_simind() -> None:
    source = (REPO_ROOT / "scripts" / "finalize_task13_formal550_local.py").read_text(
        encoding="utf-8"
    )

    assert "run_simind" not in source
    assert "subprocess.run" not in source


def _formal_entries(role: str, count: int) -> list[dict[str, object]]:
    prefix = "case" if role == "main" else "negative"
    entries: list[dict[str, object]] = []
    for index in range(count):
        if role == "main":
            split = "train" if index < 400 else "val" if index < 450 else "test"
            weight = 1.0
            probability = 1.0 / 500.0
        else:
            split = "test"
            weight = 0.0
            probability = 1.0 / 50.0
        entries.append(
            {
                "case_id": f"{prefix}_{index:05d}",
                "case_family_id": f"{prefix}_family_{index:05d}",
                "profile_id": (
                    "population_tare_hcc_nopvi_v2"
                    if role == "main"
                    else "negative_control_v2"
                ),
                "split": split,
                "population_weight": weight,
                "sampling_probability": probability,
                "mismatch_challenge": False,
            }
        )
    return entries


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _role_dataset(role: str) -> dict[str, object]:
    return {
        "dataset_id": (
            "PAR-S-TARE-HCC-NoPVI-SYN-v2"
            if role == "main"
            else "PAR-S-TARE-HCC-NoPVI-NEG-v2"
        ),
        "dataset_version": "2.0.0",
        "dataset_role": role,
        "case_count": 500 if role == "main" else 50,
    }


def _formal_input_roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Write the smallest complete immutable Task13 contract fixture."""

    bundle = tmp_path / "bundle"
    preflight = tmp_path / "preflight"
    results = tmp_path / "results"
    roles = {"main": _formal_entries("main", 500), "negative": _formal_entries("negative", 50)}
    plan_preflight: dict[str, object] = {}
    plan_cases: list[dict[str, object]] = []
    master_cases: list[dict[str, object]] = []
    for role, entries in roles.items():
        role_root = preflight / role
        generation = {"schema_version": "pars_generation_plan_v2", **_role_dataset(role), "entries": entries}
        split = {"schema_version": "pars_split_plan_v2", "role": role}
        _write_json(role_root / "GENERATION_PLAN.json", generation)
        _write_json(role_root / "SPLIT_PLAN.json", split)
        report = {
            "schema_version": "pars_v2_task12f_linux50_preflight_v2",
            "status": "pass",
            "case_count": len(entries),
            "simind_launched": False,
            "generation_plan_sha256": finalizer.sha256_file(role_root / "GENERATION_PLAN.json"),
            "split_plan_sha256": finalizer.sha256_file(role_root / "SPLIT_PLAN.json"),
            "cases": entries,
        }
        _write_json(role_root / "PREFLIGHT.json", report)
        for name in ("GENERATION_PLAN.json", "SPLIT_PLAN.json", "PREFLIGHT.json"):
            destination = bundle / "plans" / role / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((role_root / name).read_bytes())
        plan_preflight[role] = {
            "relative_path": f"plans/{role}/PREFLIGHT.json",
            "sha256": finalizer.sha256_file(role_root / "PREFLIGHT.json"),
            "generation_plan_sha256": report["generation_plan_sha256"],
            "split_plan_sha256": report["split_plan_sha256"],
        }
        plan_cases.extend({**entry, **_role_dataset(role)} for entry in entries)
        master_cases.extend({"case_id": entry["case_id"], "dataset_role": role} for entry in entries)
    plan = {
        "schema_version": "pars_v2_task13_formal550_plan_v1",
        "dataset": {"dataset_id": "PAR-S-V2-FORMAL550", "dataset_version": "2.0.0", "case_count": 550},
        "datasets": {role: _role_dataset(role) for role in roles},
        "preflight": plan_preflight,
        "cases": plan_cases,
    }
    _write_json(bundle / "TASK13_PLAN.json", plan)
    manifest = {
        "schema_version": "pars_v2_task13_formal550_bundle_v1",
        "status": "complete",
        "plan_relative_path": "TASK13_PLAN.json",
        "plan_sha256": finalizer.sha256_file(bundle / "TASK13_PLAN.json"),
        "files": [
            {
                "relative_path": path.relative_to(bundle).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": finalizer.sha256_file(path),
            }
            for path in sorted(bundle.rglob("*"))
            if path.is_file()
        ],
    }
    _write_json(bundle / "BUNDLE_MANIFEST.json", manifest)
    bundle_sha = finalizer.sha256_file(bundle / "BUNDLE_MANIFEST.json")
    _write_json(results / "REMOTE_PREFLIGHT.json", {
        "schema_version": "pars_v2_task13_formal550_remote_preflight_v1",
        "status": "pass",
        "bundle_manifest_sha256": bundle_sha,
    })
    _write_json(results / "TASK13_FORMAL550_MASTER.json", {
        "schema_version": "pars_v2_task13_formal550_master_v1",
        "status": "pass",
        "bundle_manifest_sha256": bundle_sha,
        "dataset": plan["dataset"],
        "case_count": 550,
        "role_case_counts": {"main": 500, "negative": 50},
        "cases": master_cases,
        "go_for_local_case_writer_and_dataset_freeze": False,
    })
    return results, bundle, preflight


def test_validate_role_entries_freezes_main_and_negative_contracts() -> None:
    main = finalizer._validate_role_entries(
        "main",
        _formal_entries("main", 500),
        {
            "dataset_id": "PAR-S-TARE-HCC-NoPVI-SYN-v2",
            "dataset_version": "2.0.0",
            "dataset_role": "main",
            "case_count": 500,
        },
    )
    negative = finalizer._validate_role_entries(
        "negative",
        _formal_entries("negative", 50),
        {
            "dataset_id": "PAR-S-TARE-HCC-NoPVI-NEG-v2",
            "dataset_version": "2.0.0",
            "dataset_role": "negative",
            "case_count": 50,
        },
    )

    assert main[0] == "case_00000"
    assert main[-1] == "case_00499"
    assert negative[0] == "negative_00000"
    assert negative[-1] == "negative_00049"


def test_validate_role_entries_rejects_negative_weight_or_split_drift() -> None:
    entries = _formal_entries("negative", 50)
    entries[0]["population_weight"] = 1.0

    with pytest.raises(finalizer.Formal550LocalError, match="negative policy"):
        finalizer._validate_role_entries(
            "negative",
            entries,
            {
                "dataset_id": "PAR-S-TARE-HCC-NoPVI-NEG-v2",
                "dataset_version": "2.0.0",
                "dataset_role": "negative",
                "case_count": 50,
            },
        )


def test_validate_formal_inputs_binds_master_bundle_and_both_role_contracts(
    tmp_path: Path,
) -> None:
    results, bundle, preflight = _formal_input_roots(tmp_path)

    contracts = finalizer.validate_formal_inputs(results, bundle, preflight)

    assert set(contracts) == {"main", "negative"}
    assert isinstance(contracts["main"], finalizer.RoleContract)
    assert contracts["main"].expected_case_ids == (
        "case_00000",
        *[f"case_{index:05d}" for index in range(1, 500)],
    )
    assert contracts["negative"].expected_case_ids[-1] == "negative_00049"


def test_validate_formal_inputs_rejects_master_bundle_binding_drift(
    tmp_path: Path,
) -> None:
    results, bundle, preflight = _formal_input_roots(tmp_path)
    master_path = results / "TASK13_FORMAL550_MASTER.json"
    master = finalizer.read_json(master_path)
    _write_json(master_path, {**master, "bundle_manifest_sha256": "0" * 64})

    with pytest.raises(finalizer.Formal550LocalError, match="master.*bundle"):
        finalizer.validate_formal_inputs(results, bundle, preflight)


def test_validate_formal_inputs_rejects_local_preflight_byte_drift(
    tmp_path: Path,
) -> None:
    results, bundle, preflight = _formal_input_roots(tmp_path)
    _write_json(preflight / "main" / "SPLIT_PLAN.json", {
        "schema_version": "pars_split_plan_v2",
        "role": "main",
        "drift": True,
    })

    with pytest.raises(finalizer.Formal550LocalError, match="uploaded/local main split"):
        finalizer.validate_formal_inputs(results, bundle, preflight)


def test_cli_accepts_validate_only_resume_and_bounded_max_cases() -> None:
    args = finalizer._parser().parse_args(
        ["--validate-only", "--resume", "--max-cases", "17"]
    )

    assert args.validate_only is True
    assert args.resume is True
    assert args.max_cases == 17
