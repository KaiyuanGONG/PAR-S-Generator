from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tarfile
import ast
from types import SimpleNamespace

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


def test_resume_cli_revalidates_a_tampered_staging_tree(tmp_path: Path) -> None:
    results, bundle, preflight = _formal_input_roots(tmp_path)
    archive = tmp_path / "task13_formal550_results.tar.gz"
    with tarfile.open(archive, "w:gz") as stream:
        stream.add(results, arcname="task13_formal550_results")
    sidecar = _write_sidecar(archive, finalizer.sha256_file(archive))
    staging = tmp_path / "staging"
    staged_results = finalizer.stage_results_archive(
        archive, sidecar, staging, resume=False
    )
    master_path = staged_results / "TASK13_FORMAL550_MASTER.json"
    _write_json(master_path, {
        **finalizer.read_json(master_path),
        "bundle_manifest_sha256": "0" * 64,
    })

    with pytest.raises(finalizer.Formal550LocalError, match="master.*bundle"):
        finalizer.main([
            "--archive", str(archive),
            "--sidecar", str(sidecar),
            "--staging-root", str(staging),
            "--bundle-root", str(bundle),
            "--preflight-root", str(preflight),
            "--resume",
            "--validate-only",
        ])


def test_task13_local_finalizer_never_launches_simind() -> None:
    source = (REPO_ROOT / "scripts" / "finalize_task13_formal550_local.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "run_simind" not in source
    assert "subprocess.run" not in source
    assert calls.isdisjoint({"run_simind_case", "run_simind"})


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
        "family_size": 1,
        "global_seed": 20260718,
        "split_ratios": (
            {"train": 0.8, "val": 0.1, "test": 0.1}
            if role == "main"
            else {"train": 0.0, "val": 0.0, "test": 1.0}
        ),
    }


def _generation_dataset(role: str) -> dict[str, object]:
    return {
        key: value
        for key, value in _role_dataset(role).items()
        if key != "split_ratios"
    }


def _case_dataset_identity(role: str) -> dict[str, object]:
    return {
        key: value
        for key, value in _role_dataset(role).items()
        if key not in {"family_size", "global_seed", "split_ratios"}
    }


def _refresh_bundle_binding(results: Path, bundle: Path) -> None:
    plan_path = bundle / "TASK13_PLAN.json"
    manifest = finalizer.read_json(bundle / "BUNDLE_MANIFEST.json")
    _write_json(bundle / "BUNDLE_MANIFEST.json", {
        **manifest,
        "plan_sha256": finalizer.sha256_file(plan_path),
        "files": [
            {
                "relative_path": path.relative_to(bundle).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": finalizer.sha256_file(path),
            }
            for path in sorted(bundle.rglob("*"))
            if path.is_file() and path.name != "BUNDLE_MANIFEST.json"
        ],
    })
    bundle_sha = finalizer.sha256_file(bundle / "BUNDLE_MANIFEST.json")
    for name in ("REMOTE_PREFLIGHT.json", "TASK13_FORMAL550_MASTER.json"):
        path = results / name
        _write_json(path, {
            **finalizer.read_json(path),
            "bundle_manifest_sha256": bundle_sha,
        })


def _rewrite_bundled_preflight_file(
    *,
    results: Path,
    bundle: Path,
    preflight: Path,
    role: str,
    name: str,
    value: object,
) -> None:
    _write_json(preflight / role / name, value)
    _write_json(bundle / "plans" / role / name, value)
    _refresh_bundle_binding(results, bundle)


def _write_completed_quartet_case(case_dir: Path, case_id: str) -> dict[str, object]:
    case_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "a00": case_dir / f"{case_id}.a00",
        "mhd": case_dir / f"{case_id}.mhd",
        "res": case_dir / f"{case_id}.res",
        "spe": case_dir / f"{case_id}.spe",
    }
    paths["a00"].write_bytes(b"\0" * (60 * 128 * 128 * 4))
    paths["mhd"].write_text(
        "\n".join((
            "ObjectType = Image",
            "BinaryData = True",
            "BinaryDataByteOrderMSB = False",
            "CompressedData = False",
            "NDims = 3",
            "DimSize = 128 128 60",
            "ElementType = MET_FLOAT",
            f"ElementDataFile = {case_id}.a00",
            "",
        )),
        encoding="ascii",
    )
    paths["res"].write_text("SIMIND completed\n", encoding="utf-8")
    paths["spe"].write_text("spectrum\n", encoding="utf-8")
    return {
        suffix: {
            "size_bytes": path.stat().st_size,
            "sha256": finalizer.sha256_file(path),
        }
        for suffix, path in paths.items()
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
        generation = {
            "schema_version": "pars_generation_plan_v2",
            **_generation_dataset(role),
            "profile_id": (
                "population_tare_hcc_nopvi_v2"
                if role == "main"
                else "negative_control_v2"
            ),
            "sha256": "a" * 64,
            "split_plan_sha256": "b" * 64,
            "entries": entries,
        }
        split = {
            "schema_version": "pars_split_plan_v2",
            "dataset_id": _role_dataset(role)["dataset_id"],
            "family_seeds": {},
            "family_to_split": {},
            "global_seed": _role_dataset(role)["global_seed"],
            "profile_id": (
                "population_tare_hcc_nopvi_v2"
                if role == "main"
                else "negative_control_v2"
            ),
            "ratios": _role_dataset(role)["split_ratios"],
            "sha256": "c" * 64,
        }
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
        plan_cases.extend(
            {
                **entry,
                **_case_dataset_identity(role),
                "node_id": ("cnc5", "cnc7", "cnc8")[len(plan_cases) % 3],
                "rr_seed": len(plan_cases) + 1000,
                "nn_multiplier": 1,
                "inputs": {
                    "source_sha256": "1" * 64,
                    "density_sha256": "2" * 64,
                },
            }
            for entry in entries
        )
        master_cases.extend({"case_id": entry["case_id"], "dataset_role": role} for entry in entries)
    plan = {
        "schema_version": "pars_v2_task13_formal550_plan_v1",
        "dataset": {"dataset_id": "PAR-S-V2-FORMAL550", "dataset_version": "2.0.0", "case_count": 550},
        "datasets": {role: _role_dataset(role) for role in roles},
        "preflight": plan_preflight,
        "expected_nodes": ["cnc5", "cnc7", "cnc8"],
        "execution": {
            "requested_parallel_by_node": {"cnc5": 17, "cnc7": 17, "cnc8": 16},
        },
        "linux_runtime": {"simind_sha256": "a" * 64},
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


def test_role_contracts_bind_the_immutable_bundle_and_both_preflights(
    tmp_path: Path,
) -> None:
    results, bundle, preflight = _formal_input_roots(tmp_path)

    _, plan = finalizer._formal_bundle(bundle)
    contracts = {
        role: finalizer._validate_role_contract(
            role=role,
            preflight_root=preflight / role,
            bundle_root=bundle,
            plan=plan,
        )
        for role in ("main", "negative")
    }

    assert set(contracts) == {"main", "negative"}
    assert isinstance(contracts["main"], finalizer.RoleContract)
    assert contracts["main"].expected_case_ids == (
        "case_00000",
        *[f"case_{index:05d}" for index in range(1, 500)],
    )
    assert contracts["negative"].expected_case_ids[-1] == "negative_00049"


def test_validate_formal_inputs_accepts_full_frozen_role_dataset_bindings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    results, bundle, preflight = _formal_input_roots(tmp_path)
    monkeypatch.setattr(finalizer, "_validate_downloaded_results", lambda **_: None)

    contracts = finalizer.validate_formal_inputs(results, bundle, preflight)

    assert set(contracts) == {"main", "negative"}


@pytest.mark.parametrize(
    ("role", "field", "wrong_value"),
    (
        ("main", "family_size", 2),
        ("negative", "global_seed", 20260719),
        ("main", "split_ratios", {"train": 0.7, "val": 0.2, "test": 0.1}),
        ("negative", "unexpected_field", True),
        ("main", "family_size", True),
        ("negative", "global_seed", 20260718.0),
        ("negative", "split_ratios", {"train": False, "val": False, "test": True}),
        ("negative", "split_ratios", {"train": 0, "val": 0, "test": 1}),
        ("negative", "case_count", 50.0),
    ),
)
def test_validate_formal_inputs_rejects_task13_role_dataset_frozen_field_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    role: str,
    field: str,
    wrong_value: object,
) -> None:
    results, bundle, preflight = _formal_input_roots(tmp_path)
    plan_path = bundle / "TASK13_PLAN.json"
    plan = finalizer.read_json(plan_path)
    datasets = {key: dict(value) for key, value in plan["datasets"].items()}
    datasets[role][field] = wrong_value
    _write_json(plan_path, {**plan, "datasets": datasets})
    _refresh_bundle_binding(results, bundle)
    monkeypatch.setattr(finalizer, "_validate_downloaded_results", lambda **_: None)

    with pytest.raises(
        finalizer.Formal550LocalError,
        match="Task13 plan role dataset bindings mismatch",
    ):
        finalizer.validate_formal_inputs(results, bundle, preflight)


@pytest.mark.parametrize(
    ("role", "name", "field", "wrong_value"),
    (
        ("main", "GENERATION_PLAN.json", "family_size", 2),
        ("negative", "GENERATION_PLAN.json", "global_seed", 20260719),
        (
            "main",
            "SPLIT_PLAN.json",
            "ratios",
            {"train": 0.7, "val": 0.2, "test": 0.1},
        ),
        ("main", "GENERATION_PLAN.json", "family_size", True),
        ("negative", "GENERATION_PLAN.json", "global_seed", 20260718.0),
        ("negative", "GENERATION_PLAN.json", "case_count", 50.0),
        (
            "negative",
            "SPLIT_PLAN.json",
            "ratios",
            {"train": False, "val": False, "test": True},
        ),
        (
            "negative",
            "SPLIT_PLAN.json",
            "ratios",
            {"train": 0, "val": 0, "test": 1},
        ),
    ),
)
def test_validate_formal_inputs_rejects_preflight_frozen_field_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    role: str,
    name: str,
    field: str,
    wrong_value: object,
) -> None:
    results, bundle, preflight = _formal_input_roots(tmp_path)
    path = preflight / role / name
    value = finalizer.read_json(path)
    _rewrite_bundled_preflight_file(
        results=results,
        bundle=bundle,
        preflight=preflight,
        role=role,
        name=name,
        value={**value, field: wrong_value},
    )
    monkeypatch.setattr(finalizer, "_validate_downloaded_results", lambda **_: None)

    with pytest.raises(finalizer.Formal550LocalError, match=f"{role} .* mismatch"):
        finalizer.validate_formal_inputs(results, bundle, preflight)


def test_validate_formal_inputs_rejects_extra_task13_dataset_role(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    results, bundle, preflight = _formal_input_roots(tmp_path)
    plan_path = bundle / "TASK13_PLAN.json"
    plan = finalizer.read_json(plan_path)
    datasets = {key: dict(value) for key, value in plan["datasets"].items()}
    datasets["shadow"] = dict(datasets["main"])
    _write_json(plan_path, {**plan, "datasets": datasets})
    _refresh_bundle_binding(results, bundle)
    monkeypatch.setattr(finalizer, "_validate_downloaded_results", lambda **_: None)

    with pytest.raises(
        finalizer.Formal550LocalError,
        match="Task13 plan role dataset bindings mismatch",
    ):
        finalizer.validate_formal_inputs(results, bundle, preflight)


@pytest.mark.parametrize(
    ("role", "name", "extra_fields"),
    (
        ("main", "GENERATION_PLAN.json", {"unexpected_field": True}),
        ("negative", "SPLIT_PLAN.json", {"unexpected_field": True}),
        (
            "main",
            "GENERATION_PLAN.json",
            {"split_ratios": {"train": 0.8, "val": 0.1, "test": 0.1}},
        ),
        ("negative", "SPLIT_PLAN.json", {"family_size": 1}),
        ("main", "SPLIT_PLAN.json", {"dataset_role": "main"}),
    ),
)
def test_validate_formal_inputs_rejects_extra_or_misplaced_preflight_keys(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    role: str,
    name: str,
    extra_fields: dict[str, object],
) -> None:
    results, bundle, preflight = _formal_input_roots(tmp_path)
    path = preflight / role / name
    value = finalizer.read_json(path)
    _rewrite_bundled_preflight_file(
        results=results,
        bundle=bundle,
        preflight=preflight,
        role=role,
        name=name,
        value={**value, **extra_fields},
    )
    monkeypatch.setattr(finalizer, "_validate_downloaded_results", lambda **_: None)

    with pytest.raises(finalizer.Formal550LocalError, match=f"{role} .* mismatch"):
        finalizer.validate_formal_inputs(results, bundle, preflight)


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


def test_validate_formal_inputs_rejects_missing_required_node(
    tmp_path: Path,
) -> None:
    results, bundle, preflight = _formal_input_roots(tmp_path)

    with pytest.raises(finalizer.Formal550LocalError, match="missing node results: cnc5"):
        finalizer.validate_formal_inputs(results, bundle, preflight)


def test_validate_formal_inputs_rejects_duplicate_rr_seed(
    tmp_path: Path,
) -> None:
    results, bundle, preflight = _formal_input_roots(tmp_path)
    plan_path = bundle / "TASK13_PLAN.json"
    plan = finalizer.read_json(plan_path)
    cases = list(plan["cases"])
    cases[1] = {**cases[1], "rr_seed": cases[0]["rr_seed"]}
    _write_json(plan_path, {**plan, "cases": cases})
    _refresh_bundle_binding(results, bundle)

    with pytest.raises(finalizer.Formal550LocalError, match="unique /RR"):
        finalizer.validate_formal_inputs(results, bundle, preflight)


def test_validate_formal_inputs_rejects_missing_rr_seed(
    tmp_path: Path,
) -> None:
    results, bundle, preflight = _formal_input_roots(tmp_path)
    plan_path = bundle / "TASK13_PLAN.json"
    plan = finalizer.read_json(plan_path)
    cases = list(plan["cases"])
    cases[0] = {key: value for key, value in cases[0].items() if key != "rr_seed"}
    _write_json(plan_path, {**plan, "cases": cases})
    _refresh_bundle_binding(results, bundle)

    with pytest.raises(finalizer.Formal550LocalError, match="/RR seeds are malformed"):
        finalizer.validate_formal_inputs(results, bundle, preflight)


def test_downloaded_case_validator_rejects_tampered_quartet_bytes(tmp_path: Path) -> None:
    case_id = "case_00000"
    case_dir = tmp_path / case_id
    artifacts = _write_completed_quartet_case(case_dir, case_id)
    provenance = {
        "schema_version": "pars_simind_run_v2",
        "status": "complete",
        "exit_code": 0,
        "binary_sha256": "a" * 64,
        "rr_seed": 1000,
        "nn_multiplier": 1,
        "command": ["/RR:1000", "/NN:1"],
        "inputs": {"source_sha256": "1" * 64, "density_sha256": "2" * 64},
    }
    _write_json(case_dir / "run_provenance.json", provenance)
    _write_json(case_dir / "TASK13_CASE.json", {
        "schema_version": "pars_v2_task13_formal550_case_v1",
        "status": "complete",
        "case_id": case_id,
        "node_id": "cnc5",
        "bundle_manifest_sha256": "b" * 64,
        "simind_provenance_sha256": finalizer.sha256_file(
            case_dir / "run_provenance.json"
        ),
        "output_artifacts": artifacts,
    })
    (case_dir / f"{case_id}.spe").write_text("tampered\n", encoding="utf-8")
    case = {
        "case_id": case_id,
        "node_id": "cnc5",
        "dataset_id": "PAR-S-TARE-HCC-NoPVI-SYN-v2",
        "dataset_role": "main",
        "split": "train",
        "rr_seed": 1000,
        "nn_multiplier": 1,
        "inputs": {"source_sha256": "1" * 64, "density_sha256": "2" * 64},
    }

    with pytest.raises(finalizer.Formal550LocalError, match="spe hash mismatch"):
        finalizer._validate_downloaded_case(
            case_dir=case_dir,
            case=case,
            node_id="cnc5",
            bundle_sha="b" * 64,
            expected_simind_sha="a" * 64,
        )


def test_cli_accepts_validate_only_resume_and_bounded_max_cases() -> None:
    args = finalizer._parser().parse_args(
        ["--validate-only", "--resume", "--max-cases", "17"]
    )

    assert args.validate_only is True
    assert args.resume is True
    assert args.max_cases == 17


def _minimal_role_contract(
    tmp_path: Path,
    *,
    role: str,
    case_id: str,
) -> finalizer.RoleContract:
    dataset_id = (
        "PAR-S-TARE-HCC-NoPVI-SYN-v2"
        if role == "main"
        else "PAR-S-TARE-HCC-NoPVI-NEG-v2"
    )
    profile_id = (
        "population_tare_hcc_nopvi_v2"
        if role == "main"
        else "negative_control_v2"
    )
    return finalizer.RoleContract(
        role=role,
        preflight_root=tmp_path / "preflight" / role,
        generation={
            "dataset_id": dataset_id,
            "dataset_version": "2.0.0",
            "dataset_role": role,
            "profile_id": profile_id,
            "sha256": "a" * 64,
        },
        split={"sha256": "b" * 64},
        entries=(
            {
                "case_id": case_id,
                "case_family_id": f"{case_id}_family",
                "profile_id": profile_id,
                "split": "train" if role == "main" else "test",
                "population_weight": 1.0 if role == "main" else 0.0,
                "sampling_probability": 1.0,
                "mismatch_challenge": False,
            },
        ),
        summaries={case_id: {"case_id": case_id}},
        expected_case_ids=(case_id,),
    )


def test_task13_required_artifacts_are_exact() -> None:
    assert finalizer.REQUIRED_ARTIFACTS == (
        "phantom_npz",
        "metadata_json",
        "projection_a00",
        "projection_mhd",
        "projection_res",
        "projection_spe",
        "simind_run_provenance",
        "simind_source_bin",
        "simind_density_bin",
        "formal_config",
        "formal_runtime",
        "role_preflight",
        "role_input_bundle",
        "preflight_byte_identity",
        "generation_plan",
        "split_plan",
        "task13_bundle_manifest",
        "task13_execution_plan",
        "task13_case_preflight",
        "task13_remote_preflight",
        "task13_node_complete",
        "task13_case_marker",
        "task13_master",
        "population_profile",
        "generation_profile",
        "scanner_config",
        "evidence_registry",
        "task12g_acceptance",
        "simind_smc_snapshot",
        "simind_ini_snapshot",
    )


def test_role_dataset_contracts_are_independent(tmp_path: Path) -> None:
    main = _minimal_role_contract(tmp_path, role="main", case_id="case_00000")
    negative = _minimal_role_contract(
        tmp_path, role="negative", case_id="negative_00000"
    )

    main_contract = finalizer._dataset_contract(main, tmp_path / "output" / "main")
    negative_contract = finalizer._dataset_contract(
        negative, tmp_path / "output" / "negative"
    )

    assert main_contract.output_root == tmp_path / "output" / "main"
    assert main_contract.dataset_role == "main"
    assert main_contract.allowed_profile_ids == ("population_tare_hcc_nopvi_v2",)
    assert main_contract.expected_case_ids == ("case_00000",)
    assert negative_contract.output_root == tmp_path / "output" / "negative"
    assert negative_contract.dataset_role == "negative"
    assert negative_contract.allowed_profile_ids == ("negative_control_v2",)
    assert negative_contract.expected_case_ids == ("negative_00000",)
    assert main_contract.required_artifact_names == finalizer.REQUIRED_ARTIFACTS
    assert negative_contract.required_artifact_names == finalizer.REQUIRED_ARTIFACTS


def test_role_preparation_dispatches_main_and_negative(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    def population(case_id: str, *_: object, **__: object) -> object:
        calls.append(("main", case_id))
        return object()

    def negative(case_id: str, *_: object, **__: object) -> object:
        calls.append(("negative", case_id))
        return object()

    monkeypatch.setattr(finalizer, "prepare_population_case", population)
    monkeypatch.setattr(finalizer, "prepare_negative_case", negative)
    common = {
        "profile": object(),
        "grid": object(),
        "global_seed": 7,
        "base_histories": 80_000,
    }

    finalizer._prepare_role_case(
        role="main",
        case_id="case_00000",
        entry={"mismatch_challenge": False},
        work_dir=tmp_path / "main",
        **common,
    )
    finalizer._prepare_role_case(
        role="negative",
        case_id="negative_00000",
        entry={"mismatch_challenge": False},
        work_dir=tmp_path / "negative",
        **common,
    )

    assert calls == [("main", "case_00000"), ("negative", "negative_00000")]


def test_role_progress_is_independent(tmp_path: Path) -> None:
    main_record = SimpleNamespace(case_id="case_00000")
    negative_record = SimpleNamespace(case_id="negative_00000")

    main_path = finalizer._write_role_progress(
        tmp_path,
        role="main",
        status="running",
        records=[main_record],
        total_count=500,
    )
    negative_path = finalizer._write_role_progress(
        tmp_path,
        role="negative",
        status="paused",
        records=[negative_record],
        total_count=50,
    )

    assert main_path == tmp_path / "main" / "PROGRESS.json"
    assert negative_path == tmp_path / "negative" / "PROGRESS.json"
    assert finalizer.read_json(main_path)["completed_case_ids"] == ["case_00000"]
    negative_progress = finalizer.read_json(negative_path)
    assert negative_progress["completed_case_ids"] == ["negative_00000"]
    assert negative_progress["remaining_count"] == 49


def test_campaign_marker_binds_both_role_manifest_hashes() -> None:
    marker = finalizer._campaign_complete_document(
        SimpleNamespace(manifest_sha256="1" * 64),
        SimpleNamespace(manifest_sha256="2" * 64),
    )

    assert marker == {
        "schema_version": "pars_v2_task13_formal550_complete_v1",
        "status": "complete",
        "campaign": {
            "dataset_id": "PAR-S-V2-FORMAL550",
            "dataset_version": "2.0.0",
        },
        "case_count": 550,
        "role_case_counts": {"main": 500, "negative": 50},
        "datasets": {
            "main": {"relative_root": "main", "manifest_sha256": "1" * 64},
            "negative": {
                "relative_root": "negative",
                "manifest_sha256": "2" * 64,
            },
        },
    }


def test_campaign_marker_is_idempotent_but_never_overwritten(tmp_path: Path) -> None:
    main = SimpleNamespace(manifest_sha256="1" * 64)
    negative = SimpleNamespace(manifest_sha256="2" * 64)

    path = finalizer._write_campaign_complete(tmp_path, main, negative)
    first = path.read_bytes()
    assert finalizer._write_campaign_complete(tmp_path, main, negative) == path
    assert path.read_bytes() == first

    with pytest.raises(finalizer.Formal550LocalError, match="campaign marker drift"):
        finalizer._write_campaign_complete(
            tmp_path,
            SimpleNamespace(manifest_sha256="3" * 64),
            negative,
        )


def test_resume_loads_completed_cases_with_hash_verification(
    monkeypatch,
    tmp_path: Path,
) -> None:
    cases = tmp_path / "cases"
    (cases / "case_00000").mkdir(parents=True)
    observed: list[tuple[Path, Path, bool]] = []

    def load(path: Path, *, dataset_root: Path, verify_hashes: bool) -> object:
        observed.append((path, dataset_root, verify_hashes))
        return SimpleNamespace(case_id=path.parent.name)

    monkeypatch.setattr(finalizer, "load_case_record_v2", load)
    records = finalizer._load_role_records(
        tmp_path,
        ("case_00000", "case_00001"),
    )

    assert [record.case_id for record in records] == ["case_00000"]
    assert observed == [
        (cases / "case_00000" / "case_record.json", tmp_path, True)
    ]


@pytest.mark.parametrize(
    "missing_name",
    ["GENERATION_PLAN.json", "SPLIT_PLAN.json", "FORMAL_RUNTIME.json"],
)
def test_frozen_role_resume_never_repairs_missing_root_artifacts(
    monkeypatch,
    tmp_path: Path,
    missing_name: str,
) -> None:
    contract = _minimal_role_contract(
        tmp_path, role="main", case_id="case_00000"
    )
    contract.preflight_root.mkdir(parents=True)
    _write_json(
        contract.preflight_root / "GENERATION_PLAN.json",
        contract.generation,
    )
    _write_json(contract.preflight_root / "SPLIT_PLAN.json", contract.split)
    role_output = tmp_path / "output" / "main"
    role_output.mkdir(parents=True)
    _write_json(role_output / "DATASET_COMPLETE.json", {"status": "complete"})
    _write_json(role_output / "GENERATION_PLAN.json", contract.generation)
    _write_json(role_output / "SPLIT_PLAN.json", contract.split)
    runtime = {"schema_version": "runtime", "status": "bound"}
    _write_json(role_output / "FORMAL_RUNTIME.json", runtime)
    (role_output / missing_name).unlink()
    monkeypatch.setattr(
        finalizer,
        "_load_role_records",
        lambda *_: pytest.fail("case loading must follow frozen root-file validation"),
    )

    with pytest.raises(finalizer.Formal550LocalError, match="frozen main"):
        finalizer._revalidate_frozen_role(
            role="main",
            contract=contract,
            role_output=role_output,
            runtime_document=runtime,
        )

    assert not (role_output / missing_name).exists()


def test_new_role_freeze_requires_immediate_idempotent_revalidation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    contract = _minimal_role_contract(
        tmp_path, role="main", case_id="case_00000"
    )
    dataset_contract = SimpleNamespace(output_root=tmp_path / "output" / "main")
    record = SimpleNamespace(case_id="case_00000")
    calls: list[str] = []

    monkeypatch.setattr(
        finalizer,
        "freeze_dataset",
        lambda records, frozen_contract: calls.append("initial_freeze")
        or SimpleNamespace(manifest_sha256="1" * 64),
    )

    def reject_revalidation(**_: object) -> object:
        calls.append("post_freeze_revalidation")
        raise finalizer.Formal550LocalError("post-freeze revalidation failed")

    monkeypatch.setattr(finalizer, "_revalidate_frozen_role", reject_revalidation)

    with pytest.raises(
        finalizer.Formal550LocalError, match="post-freeze revalidation failed"
    ):
        finalizer._freeze_and_revalidate_role(
            role="main",
            records=[record],
            dataset_contract=dataset_contract,
            contract=contract,
            role_output=dataset_contract.output_root,
            runtime_document={"status": "bound"},
        )

    assert calls == ["initial_freeze", "post_freeze_revalidation"]


def test_non_file_completion_marker_is_frozen_state_and_never_mutated(
    monkeypatch,
    tmp_path: Path,
) -> None:
    contract = _minimal_role_contract(
        tmp_path, role="main", case_id="case_00000"
    )
    role_output = tmp_path / "output" / "main"
    (role_output / "DATASET_COMPLETE.json").mkdir(parents=True)
    monkeypatch.setattr(
        finalizer,
        "_role_runtime_document",
        lambda **_: {"status": "bound"},
    )
    monkeypatch.setattr(
        finalizer,
        "_copy_immutable",
        lambda *_: pytest.fail("frozen role root must not be mutated"),
    )

    with pytest.raises(
        finalizer.Formal550LocalError, match="completion marker is missing"
    ):
        finalizer._finalize_role(
            role="main",
            contract=contract,
            output_root=tmp_path / "output",
            work_root=tmp_path / "work",
            bundle_root=tmp_path / "bundle",
            results_root=tmp_path / "results",
            plan={},
            downloaded={},
            common_paths={},
            role_paths={},
            max_cases=None,
        )

    assert (role_output / "DATASET_COMPLETE.json").is_dir()


def _patch_minimal_main_inputs(
    monkeypatch,
    tmp_path: Path,
) -> dict[str, finalizer.RoleContract]:
    contracts = {
        "main": _minimal_role_contract(
            tmp_path, role="main", case_id="case_00000"
        ),
        "negative": _minimal_role_contract(
            tmp_path, role="negative", case_id="negative_00000"
        ),
    }
    monkeypatch.setattr(
        finalizer,
        "stage_results_archive",
        lambda *_args, **_kwargs: tmp_path / "results",
    )
    monkeypatch.setattr(
        finalizer,
        "validate_formal_inputs",
        lambda *_args, **_kwargs: contracts,
    )
    monkeypatch.setattr(finalizer, "_formal_bundle", lambda *_: ({}, {"cases": []}))
    monkeypatch.setattr(finalizer, "_downloaded_cases", lambda *_: {})
    monkeypatch.setattr(
        finalizer,
        "_role_paths",
        lambda **_: ({}, {"main": {}, "negative": {}}),
    )
    return contracts


def test_campaign_marker_not_written_when_post_freeze_revalidation_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _patch_minimal_main_inputs(monkeypatch, tmp_path)
    writes: list[object] = []

    def finalize_role(*, role: str, **_: object) -> tuple[object, int]:
        if role == "negative":
            raise finalizer.Formal550LocalError(
                "negative post-freeze revalidation failed"
            )
        return SimpleNamespace(manifest_sha256="1" * 64), 1

    monkeypatch.setattr(finalizer, "_finalize_role", finalize_role)
    monkeypatch.setattr(
        finalizer,
        "_write_campaign_complete",
        lambda *_: writes.append(object()),
    )

    result = finalizer.main(
        [
            "--archive",
            str(tmp_path / "archive.tar.gz"),
            "--sidecar",
            str(tmp_path / "archive.sha256"),
            "--staging-root",
            str(tmp_path / "staging"),
            "--preflight-root",
            str(tmp_path / "preflight"),
            "--bundle-root",
            str(tmp_path / "bundle"),
            "--output-root",
            str(tmp_path / "output"),
            "--work-root",
            str(tmp_path / "work"),
        ]
    )

    assert result == 1
    assert writes == []
    assert not (tmp_path / "output" / "FORMAL550_COMPLETE.json").exists()


def test_bounded_run_resume_lifecycle_is_independent_and_exact(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _patch_minimal_main_inputs(monkeypatch, tmp_path)
    loaded: list[tuple[str, bool]] = []
    prepared: list[str] = []
    freeze_passes: dict[str, int] = {"main": 0, "negative": 0}

    def load(path: Path, *, dataset_root: Path, verify_hashes: bool) -> object:
        loaded.append((path.parent.name, verify_hashes))
        return SimpleNamespace(case_id=path.parent.name)

    monkeypatch.setattr(finalizer, "load_case_record_v2", load)

    def finalize_role(
        *,
        role: str,
        contract: finalizer.RoleContract,
        output_root: Path,
        work_root: Path,
        max_cases: int | None,
        **_: object,
    ) -> tuple[object | None, int]:
        role_output = Path(output_root) / role
        cases_root = role_output / "cases"
        cases_root.mkdir(parents=True, exist_ok=True)
        records = finalizer._load_role_records(
            role_output, contract.expected_case_ids
        )
        if not records and role == "main" and max_cases == 1:
            case_id = contract.expected_case_ids[0]
            prepared.append(case_id)
            case_root = cases_root / case_id
            case_root.mkdir()
            (case_root / "case_record.json").write_text("{}", encoding="utf-8")
            record = SimpleNamespace(case_id=case_id)
            finalizer._write_role_progress(
                work_root,
                role=role,
                status="paused",
                records=[record],
                total_count=2,
            )
            return None, 1
        if not records:
            case_id = contract.expected_case_ids[0]
            prepared.append(case_id)
            case_root = cases_root / case_id
            case_root.mkdir(exist_ok=True)
            (case_root / "case_record.json").write_text("{}", encoding="utf-8")
            records = [SimpleNamespace(case_id=case_id)]
        for _pass in range(2):
            freeze_passes[role] += 1
        marker = SimpleNamespace(
            manifest_sha256=("1" if role == "main" else "2") * 64
        )
        finalizer._write_role_progress(
            work_root,
            role=role,
            status="complete",
            records=records,
            total_count=len(records),
            dataset_complete={"manifest_sha256": marker.manifest_sha256},
        )
        return marker, 0 if role == "main" else 1

    monkeypatch.setattr(finalizer, "_finalize_role", finalize_role)
    common_args = [
        "--archive",
        str(tmp_path / "archive.tar.gz"),
        "--sidecar",
        str(tmp_path / "archive.sha256"),
        "--staging-root",
        str(tmp_path / "staging"),
        "--preflight-root",
        str(tmp_path / "preflight"),
        "--bundle-root",
        str(tmp_path / "bundle"),
        "--output-root",
        str(tmp_path / "output"),
        "--work-root",
        str(tmp_path / "work"),
    ]

    assert finalizer.main([*common_args, "--max-cases", "1"]) == 3
    assert not (tmp_path / "output" / "FORMAL550_COMPLETE.json").exists()
    assert (tmp_path / "work" / "main" / "PROGRESS.json").is_file()
    assert not (tmp_path / "work" / "negative" / "PROGRESS.json").exists()

    assert finalizer.main([*common_args, "--resume"]) == 0
    assert loaded == [("case_00000", True)]
    assert prepared == ["case_00000", "negative_00000"]
    assert freeze_passes == {"main": 2, "negative": 2}
    assert finalizer.read_json(
        tmp_path / "output" / "FORMAL550_COMPLETE.json"
    ) == {
        "schema_version": "pars_v2_task13_formal550_complete_v1",
        "status": "complete",
        "campaign": {
            "dataset_id": "PAR-S-V2-FORMAL550",
            "dataset_version": "2.0.0",
        },
        "case_count": 550,
        "role_case_counts": {"main": 500, "negative": 50},
        "datasets": {
            "main": {"relative_root": "main", "manifest_sha256": "1" * 64},
            "negative": {
                "relative_root": "negative",
                "manifest_sha256": "2" * 64,
            },
        },
    }
