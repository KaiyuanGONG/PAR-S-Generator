"""Validate, write, and freeze downloaded Task13 Formal550 Linux results."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, replace
import json
import math
import os
import re
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from task12f_linux50_common import (  # noqa: E402
    atomic_write_json,
    read_json,
    safe_extract_tar,
    sha256_file,
    validate_bundle,
)
from task13_formal550_runtime import patch_runtime_contract, restore_runtime_contract  # noqa: E402
from core.case_writer_v2 import (  # noqa: E402
    DATASET_COMPLETE_FILENAME,
    CasePayloadV2,
    DatasetContractV2,
    freeze_dataset,
    load_case_record_v2,
    write_case_v2,
)
from core.liver_geometry import GridSpecV2  # noqa: E402
from core.pilot_v2 import build_completed_metadata, simind_extra_artifacts  # noqa: E402
from core.production_v2 import (  # noqa: E402
    prepare_negative_case,
    prepare_population_case,
    summarize_prepared_negative_case,
    summarize_prepared_population_case,
)
from core.provenance import sha256_json  # noqa: E402
from core.reproducibility_v2 import (  # noqa: E402
    load_and_validate_preflight_input_bundle,
    prove_preflight_byte_identity,
)
from core.schemas_v2 import load_evidence_registry, load_profile  # noqa: E402
from core.simind_exec import SimindRunResult  # noqa: E402
from core.simind_postprocess import audit_simind_completion  # noqa: E402


STAGING_SCHEMA = "pars_v2_task13_formal550_staging_v1"
RESULT_ARCHIVE_NAME = "task13_formal550_results.tar.gz"
RESULT_ARCHIVE_ROOT = "task13_formal550_results"

DEFAULT_DOWNLOAD_ROOT = Path(
    r"D:\PFE-U\PAR\outputs\task13_formal550_download_v1"
)
DEFAULT_ARCHIVE = DEFAULT_DOWNLOAD_ROOT / RESULT_ARCHIVE_NAME
DEFAULT_SIDECAR = Path(f"{DEFAULT_ARCHIVE}.sha256")
DEFAULT_STAGING_ROOT = Path(
    r"D:\PFE-U\PAR\outputs\task13_formal550_staging_v1"
)
DEFAULT_RESULTS_ROOT = DEFAULT_STAGING_ROOT / RESULT_ARCHIVE_ROOT
DEFAULT_PREFLIGHT_ROOT = Path(
    r"D:\PFE-U\PAR\outputs\task13_formal550_preflight_v1"
)
DEFAULT_BUNDLE_ROOT = Path(
    r"D:\PFE-U\PAR\outputs\task13_formal550_upload_v1"
    r"\pars_v2_task13_formal550_bundle_v1"
)
DEFAULT_OUTPUT_ROOT = Path(r"D:\PFE-U\PAR\outputs\pars_v2_formal550_v1")
DEFAULT_WORK_ROOT = Path(r"D:\PFE-U\PAR\outputs\pars_v2_formal550_v1_work")

_SHA256 = re.compile(r"[0-9a-f]{64}")
ROLE_PROGRESS_SCHEMA = "pars_v2_task13_formal550_role_progress_v1"
CAMPAIGN_COMPLETE_SCHEMA = "pars_v2_task13_formal550_complete_v1"
CAMPAIGN_COMPLETE_FILENAME = "FORMAL550_COMPLETE.json"
GENERATION_PLAN_KEYS = frozenset(
    {
        "case_count",
        "dataset_id",
        "dataset_role",
        "dataset_version",
        "entries",
        "family_size",
        "global_seed",
        "profile_id",
        "schema_version",
        "sha256",
        "split_plan_sha256",
    }
)
SPLIT_PLAN_KEYS = frozenset(
    {
        "dataset_id",
        "family_seeds",
        "family_to_split",
        "global_seed",
        "profile_id",
        "ratios",
        "schema_version",
        "sha256",
    }
)

REQUIRED_ARTIFACTS = (
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


class Formal550LocalError(RuntimeError):
    """Raised when the immutable Formal550 local contract is violated."""


@dataclass(frozen=True)
class RoleContract:
    """Immutable role-specific inputs that the later case writer may consume."""

    role: str
    preflight_root: Path
    generation: Mapping[str, object]
    split: Mapping[str, object]
    entries: tuple[Mapping[str, object], ...]
    summaries: Mapping[str, Mapping[str, object]]
    expected_case_ids: tuple[str, ...]


@dataclass(frozen=True)
class DownloadedCaseV2:
    case_id: str
    node_id: str
    case_dir: Path
    case_marker_path: Path
    node_complete_path: Path


def _dataset_contract(
    role_contract: RoleContract,
    output_root: Path,
) -> DatasetContractV2:
    generation = role_contract.generation
    return DatasetContractV2(
        output_root=Path(output_root),
        dataset_id=str(generation["dataset_id"]),
        dataset_version=str(generation["dataset_version"]),
        dataset_role=role_contract.role,
        expected_case_ids=role_contract.expected_case_ids,
        allowed_profile_ids=(str(generation["profile_id"]),),
        split_plan_sha256=str(role_contract.split["sha256"]),
        required_artifact_names=REQUIRED_ARTIFACTS,
    )


def _prepare_role_case(
    *,
    role: str,
    case_id: str,
    entry: Mapping[str, object],
    profile: object,
    grid: object,
    global_seed: int,
    base_histories: int,
    work_dir: Path,
    max_tumor_attempts: int = 32,
) -> object:
    common = {
        "global_seed": global_seed,
        "base_histories": base_histories,
        "work_dir": work_dir,
    }
    if role == "main":
        return prepare_population_case(
            case_id,
            profile,
            grid,
            mismatch_challenge=bool(entry.get("mismatch_challenge", False)),
            max_tumor_attempts=max_tumor_attempts,
            **common,
        )
    if role == "negative":
        return prepare_negative_case(case_id, profile, grid, **common)
    raise ValueError(f"unknown formal role: {role}")


def _write_role_progress(
    work_root: Path,
    *,
    role: str,
    status: str,
    records: Sequence[object],
    total_count: int,
    current_case_id: str | None = None,
    error: str | None = None,
    dataset_complete: Mapping[str, object] | None = None,
) -> Path:
    path = Path(work_root) / role / "PROGRESS.json"
    completed = [str(record.case_id) for record in records]
    document: dict[str, object] = {
        "schema_version": ROLE_PROGRESS_SCHEMA,
        "status": status,
        "role": role,
        "completed_case_ids": completed,
        "completed_count": len(completed),
        "total_count": total_count,
        "remaining_count": total_count - len(completed),
    }
    if current_case_id is not None:
        document["current_case_id"] = current_case_id
    if error is not None:
        document["error"] = error
    if dataset_complete is not None:
        document["dataset_complete"] = dict(dataset_complete)
    atomic_write_json(path, document)
    return path


def _campaign_complete_document(
    main_marker: object,
    negative_marker: object,
) -> dict[str, object]:
    return {
        "schema_version": CAMPAIGN_COMPLETE_SCHEMA,
        "status": "complete",
        "campaign": {
            "dataset_id": "PAR-S-V2-FORMAL550",
            "dataset_version": "2.0.0",
        },
        "case_count": 550,
        "role_case_counts": {"main": 500, "negative": 50},
        "datasets": {
            "main": {
                "relative_root": "main",
                "manifest_sha256": main_marker.manifest_sha256,
            },
            "negative": {
                "relative_root": "negative",
                "manifest_sha256": negative_marker.manifest_sha256,
            },
        },
    }


def _write_campaign_complete(
    output_root: Path,
    main_marker: object,
    negative_marker: object,
) -> Path:
    path = Path(output_root) / CAMPAIGN_COMPLETE_FILENAME
    document = _campaign_complete_document(main_marker, negative_marker)
    if path.exists():
        if read_json(path) != document:
            raise Formal550LocalError("existing campaign marker drift")
        return path
    atomic_write_json(path, document)
    return path


def _load_role_records(
    output_root: Path,
    expected_case_ids: Sequence[str],
) -> list[object]:
    root = Path(output_root)
    cases_root = root / "cases"
    if not cases_root.exists():
        return []
    expected = set(expected_case_ids)
    observed = {path.name for path in cases_root.iterdir() if path.is_dir()}
    unexpected = observed - expected
    if unexpected:
        raise Formal550LocalError(
            f"role dataset contains unexpected cases: {sorted(unexpected)}"
        )
    return [
        load_case_record_v2(
            cases_root / case_id / "case_record.json",
            dataset_root=root,
            verify_hashes=True,
        )
        for case_id in expected_case_ids
        if case_id in observed
    ]


def _validate_role_entries(
    role: str,
    entries: Sequence[Mapping[str, object]],
    dataset: Mapping[str, object],
) -> tuple[str, ...]:
    """Validate the exact role-specific identity, order, split, and weights."""

    contracts = {
        "main": {
            "count": 500,
            "prefix": "case",
            "dataset_id": "PAR-S-TARE-HCC-NoPVI-SYN-v2",
            "profile_id": "population_tare_hcc_nopvi_v2",
            "splits": Counter({"train": 400, "val": 50, "test": 50}),
        },
        "negative": {
            "count": 50,
            "prefix": "negative",
            "dataset_id": "PAR-S-TARE-HCC-NoPVI-NEG-v2",
            "profile_id": "negative_control_v2",
            "splits": Counter({"test": 50}),
        },
    }
    if role not in contracts:
        raise ValueError(f"unknown formal role: {role}")
    expected = contracts[role]
    count = int(expected["count"])
    if (
        dataset.get("dataset_id") != expected["dataset_id"]
        or dataset.get("dataset_version") != "2.0.0"
        or dataset.get("dataset_role") != role
        or int(dataset.get("case_count", -1)) != count
    ):
        raise Formal550LocalError(f"{role} dataset identity mismatch")
    ids = tuple(str(item.get("case_id")) for item in entries)
    expected_ids = tuple(
        f"{expected['prefix']}_{index:05d}" for index in range(count)
    )
    if ids != expected_ids:
        raise Formal550LocalError(f"{role} case identity/order mismatch")
    if Counter(str(item.get("split")) for item in entries) != expected["splits"]:
        raise Formal550LocalError(f"{role} split policy mismatch")
    if any(item.get("profile_id") != expected["profile_id"] for item in entries):
        raise Formal550LocalError(f"{role} profile/challenge policy mismatch")
    if role == "main" and any(
        item.get("mismatch_challenge") is not False for item in entries
    ):
        raise Formal550LocalError("main policy forbids mismatch challenges")
    if role == "negative":
        if any(
            float(item.get("population_weight", -1.0)) != 0.0
            for item in entries
        ) or not math.isclose(
            sum(float(item.get("sampling_probability", -1.0)) for item in entries),
            1.0,
            rel_tol=0.0,
            abs_tol=1e-12,
        ) or any(float(item.get("sampling_probability", -1.0)) <= 0.0 for item in entries):
            raise Formal550LocalError(
                "negative policy requires zero population weights and normalized sampling"
            )
    else:
        if any(
            float(item.get("population_weight", -1.0)) != 1.0
            or float(item.get("sampling_probability", 0.0)) <= 0.0
            for item in entries
        ) or not math.isclose(
            sum(float(item["sampling_probability"]) for item in entries),
            1.0,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise Formal550LocalError("main policy requires normalized population weights")
    return ids


def read_sha256_sidecar(sidecar: Path, archive_name: str) -> str:
    try:
        fields = sidecar.read_text(encoding="ascii").strip().split()
    except OSError as exc:
        raise Formal550LocalError(f"cannot read archive sidecar: {sidecar}") from exc
    if (
        len(fields) != 2
        or _SHA256.fullmatch(fields[0]) is None
        or fields[1] != archive_name
    ):
        raise Formal550LocalError("archive sidecar does not match the expected format")
    return fields[0]


def _read_sidecar(sidecar: Path, archive_name: str) -> str:
    """Backward-compatible private spelling for the sidecar parser."""

    return read_sha256_sidecar(sidecar, archive_name)


def _reuse_staging(staging_root: Path, archive_sha256: str) -> Path:
    marker_path = staging_root / "STAGING_COMPLETE.json"
    if not marker_path.is_file():
        raise Formal550LocalError("staging root exists without a completion marker")
    marker = read_json(marker_path)
    if (
        marker.get("schema_version") != STAGING_SCHEMA
        or marker.get("status") != "complete"
        or marker.get("archive_sha256") != archive_sha256
        or marker.get("results_relative_path") != RESULT_ARCHIVE_ROOT
    ):
        raise Formal550LocalError("existing staging marker does not match the archive")
    results = staging_root / RESULT_ARCHIVE_ROOT
    if not (results / "TASK13_FORMAL550_MASTER.json").is_file():
        raise Formal550LocalError("existing staging root lacks the Task13 master")
    return results


def stage_results_archive(
    archive: Path,
    sidecar: Path,
    staging_root: Path,
    *,
    resume: bool,
) -> Path:
    """Verify and atomically extract the immutable downloaded result archive."""

    archive = Path(archive).resolve()
    sidecar = Path(sidecar).resolve()
    staging_root = Path(staging_root).resolve()
    if not archive.is_file():
        raise Formal550LocalError(f"result archive does not exist: {archive}")
    expected_sha = read_sha256_sidecar(sidecar, archive.name)
    observed_sha = sha256_file(archive)
    if observed_sha != expected_sha:
        raise Formal550LocalError(
            "downloaded result archive SHA-256 mismatch: "
            f"expected={expected_sha} observed={observed_sha}"
        )
    if staging_root.exists():
        if not resume:
            raise FileExistsError(f"staging root exists; use --resume: {staging_root}")
        return _reuse_staging(staging_root, observed_sha)

    staging_root.parent.mkdir(parents=True, exist_ok=True)
    temporary = staging_root.parent / f".{staging_root.name}.{uuid.uuid4().hex}.tmp"
    try:
        safe_extract_tar(archive, temporary)
        results = temporary / RESULT_ARCHIVE_ROOT
        if not (results / "TASK13_FORMAL550_MASTER.json").is_file():
            raise Formal550LocalError("result archive lacks the Task13 master")
        unexpected = sorted(
            path.name for path in temporary.iterdir() if path.name != RESULT_ARCHIVE_ROOT
        )
        if unexpected:
            raise Formal550LocalError(
                f"result archive has unexpected top-level entries: {unexpected}"
            )
        atomic_write_json(
            temporary / "STAGING_COMPLETE.json",
            {
                "schema_version": STAGING_SCHEMA,
                "status": "complete",
                "archive_name": archive.name,
                "archive_sha256": observed_sha,
                "results_relative_path": RESULT_ARCHIVE_ROOT,
            },
        )
        os.replace(temporary, staging_root)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
    return staging_root / RESULT_ARCHIVE_ROOT


def _read_object(path: Path, label: str) -> Mapping[str, object]:
    try:
        value = read_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise Formal550LocalError(f"cannot read {label}: {path}") from exc
    return value


def _formal_bundle(bundle_root: Path) -> tuple[Mapping[str, object], Mapping[str, object]]:
    """Validate the immutable upload bundle under the Task13 runtime schemas."""

    previous = patch_runtime_contract()
    try:
        manifest = validate_bundle(bundle_root)
    except (OSError, ValueError) as exc:
        raise Formal550LocalError("immutable Task13 upload bundle is invalid") from exc
    finally:
        restore_runtime_contract(previous)
    plan = _read_object(bundle_root / str(manifest["plan_relative_path"]), "Task13 plan")
    if plan.get("schema_version") != "pars_v2_task13_formal550_plan_v1":
        raise Formal550LocalError("Task13 plan schema mismatch")
    return manifest, plan


def _role_dataset(role: str) -> Mapping[str, object]:
    datasets = {
        "main": {
            "dataset_id": "PAR-S-TARE-HCC-NoPVI-SYN-v2",
            "dataset_version": "2.0.0",
            "dataset_role": "main",
            "case_count": 500,
            "family_size": 1,
            "global_seed": 20260718,
            "split_ratios": {"train": 0.8, "val": 0.1, "test": 0.1},
        },
        "negative": {
            "dataset_id": "PAR-S-TARE-HCC-NoPVI-NEG-v2",
            "dataset_version": "2.0.0",
            "dataset_role": "negative",
            "case_count": 50,
            "family_size": 1,
            "global_seed": 20260718,
            "split_ratios": {"train": 0.0, "val": 0.0, "test": 1.0},
        },
    }
    return datasets[role]


def _exact_contract_value(observed: object, expected: object) -> bool:
    """Compare frozen JSON values without Python's numeric type coercion."""

    if type(observed) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(observed) == set(expected) and all(
            _exact_contract_value(observed[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(observed) == len(expected) and all(
            _exact_contract_value(observed_item, expected_item)
            for observed_item, expected_item in zip(observed, expected)
        )
    return observed == expected


def _validate_role_contract(
    *,
    role: str,
    preflight_root: Path,
    bundle_root: Path,
    plan: Mapping[str, object],
) -> RoleContract:
    generation_path = preflight_root / "GENERATION_PLAN.json"
    split_path = preflight_root / "SPLIT_PLAN.json"
    report_path = preflight_root / "PREFLIGHT.json"
    generation = _read_object(generation_path, f"{role} generation plan")
    split = _read_object(split_path, f"{role} split plan")
    report = _read_object(report_path, f"{role} preflight")
    for name, local, label in (
        ("GENERATION_PLAN.json", generation_path, "generation plan"),
        ("SPLIT_PLAN.json", split_path, "split plan"),
        ("PREFLIGHT.json", report_path, "preflight"),
    ):
        bundled = bundle_root / "plans" / role / name
        if not bundled.is_file() or local.read_bytes() != bundled.read_bytes():
            raise Formal550LocalError(f"uploaded/local {role} {label} bytes differ")
    if set(generation) != GENERATION_PLAN_KEYS:
        raise Formal550LocalError(f"{role} generation plan keys mismatch")
    if set(split) != SPLIT_PLAN_KEYS:
        raise Formal550LocalError(f"{role} split plan keys mismatch")
    if (
        generation.get("schema_version") != "pars_generation_plan_v2"
        or split.get("schema_version") != "pars_split_plan_v2"
        or report.get("schema_version") != "pars_v2_task12f_linux50_preflight_v2"
        or report.get("status") != "pass"
        or report.get("simind_launched") is not False
    ):
        raise Formal550LocalError(f"{role} local preflight schema/status mismatch")
    dataset = _role_dataset(role)
    expected_generation_dataset = {
        key: value for key, value in dataset.items() if key != "split_ratios"
    }
    observed_generation_dataset = {
        key: generation.get(key) for key in expected_generation_dataset
    }
    if not _exact_contract_value(
        observed_generation_dataset, expected_generation_dataset
    ):
        raise Formal550LocalError(f"{role} generation dataset identity mismatch")
    expected_split_dataset = {
        "dataset_id": dataset["dataset_id"],
        "global_seed": dataset["global_seed"],
        "ratios": dataset["split_ratios"],
    }
    observed_split_dataset = {
        key: split.get(key) for key in expected_split_dataset
    }
    if not _exact_contract_value(observed_split_dataset, expected_split_dataset):
        raise Formal550LocalError(f"{role} split dataset identity mismatch")
    raw_entries = generation.get("entries")
    raw_summaries = report.get("cases")
    if not isinstance(raw_entries, list) or not all(
        isinstance(item, Mapping) for item in raw_entries
    ):
        raise Formal550LocalError(f"{role} generation entries are malformed")
    if not isinstance(raw_summaries, list) or not all(
        isinstance(item, Mapping) for item in raw_summaries
    ):
        raise Formal550LocalError(f"{role} preflight summaries are malformed")
    entries = tuple(dict(item) for item in raw_entries)
    expected_case_ids = _validate_role_entries(role, entries, dataset)
    summaries = {str(item.get("case_id")): dict(item) for item in raw_summaries}
    if tuple(summaries) != expected_case_ids:
        raise Formal550LocalError(f"{role} preflight case identities/order mismatch")
    for entry in entries:
        case_id = str(entry["case_id"])
        summary = summaries[case_id]
        for key in (
            "case_family_id",
            "profile_id",
            "split",
            "population_weight",
            "sampling_probability",
        ):
            if summary.get(key) != entry.get(key):
                raise Formal550LocalError(f"{case_id} preflight/generation mismatch")
    plan_preflights = plan.get("preflight")
    if not isinstance(plan_preflights, Mapping) or not isinstance(
        plan_preflights.get(role), Mapping
    ):
        raise Formal550LocalError(f"Task13 plan lacks {role} preflight binding")
    binding = plan_preflights[role]
    if binding.get("relative_path") != f"plans/{role}/PREFLIGHT.json":
        raise Formal550LocalError(f"Task13 plan {role} preflight path mismatch")
    expected_bindings = {
        "sha256": sha256_file(report_path),
        "generation_plan_sha256": report.get("generation_plan_sha256"),
        "split_plan_sha256": report.get("split_plan_sha256"),
    }
    if any(binding.get(key) != value for key, value in expected_bindings.items()):
        raise Formal550LocalError(f"Task13 plan {role} preflight binding mismatch")
    if int(report.get("case_count", -1)) != len(entries):
        raise Formal550LocalError(f"{role} preflight case count mismatch")
    return RoleContract(
        role=role,
        preflight_root=preflight_root,
        generation=generation,
        split=split,
        entries=entries,
        summaries=summaries,
        expected_case_ids=expected_case_ids,
    )


def _switch(command: object, prefix: str) -> int:
    if not isinstance(command, list):
        raise Formal550LocalError("SIMIND command must be a list")
    values = [
        str(value)[len(prefix) :]
        for value in command
        if str(value).startswith(prefix)
    ]
    if len(values) != 1:
        raise Formal550LocalError(f"SIMIND command requires exactly one {prefix} switch")
    try:
        return int(values[0])
    except ValueError as exc:
        raise Formal550LocalError(f"SIMIND command has invalid {prefix} switch") from exc


def _validate_downloaded_case(
    *,
    case_dir: Path,
    case: Mapping[str, object],
    node_id: str,
    bundle_sha: str,
    expected_simind_sha: str,
) -> Mapping[str, object]:
    """Fail closed on every downloaded Task13 SIMIND case and its quartet."""

    case_id = str(case.get("case_id"))
    marker_path = case_dir / "TASK13_CASE.json"
    marker = _read_object(marker_path, f"{case_id} Task13 case marker")
    if (
        marker.get("schema_version") != "pars_v2_task13_formal550_case_v1"
        or marker.get("status") != "complete"
        or marker.get("case_id") != case_id
        or marker.get("node_id") != node_id
        or marker.get("bundle_manifest_sha256") != bundle_sha
    ):
        raise Formal550LocalError(f"invalid downloaded Task13 marker for {case_id}")
    artifacts = marker.get("output_artifacts")
    if not isinstance(artifacts, Mapping):
        raise Formal550LocalError(f"{case_id} artifact records are missing")
    for extension in ("a00", "mhd", "res", "spe"):
        path = case_dir / f"{case_id}.{extension}"
        record = artifacts.get(extension)
        if not path.is_file() or not isinstance(record, Mapping):
            raise Formal550LocalError(f"{case_id}.{extension} is missing")
        if path.stat().st_size != int(record.get("size_bytes", -1)):
            raise Formal550LocalError(f"{case_id}.{extension} size mismatch")
        if sha256_file(path) != record.get("sha256"):
            raise Formal550LocalError(f"{case_id}.{extension} hash mismatch")
    provenance_path = case_dir / "run_provenance.json"
    provenance = _read_object(provenance_path, f"{case_id} SIMIND provenance")
    if marker.get("simind_provenance_sha256") != sha256_file(provenance_path):
        raise Formal550LocalError(f"{case_id} SIMIND provenance hash mismatch")
    if (
        provenance.get("schema_version") != "pars_simind_run_v2"
        or provenance.get("status") != "complete"
        or provenance.get("exit_code") != 0
        or provenance.get("binary_sha256") != expected_simind_sha
        or provenance.get("rr_seed") != case.get("rr_seed")
        or provenance.get("nn_multiplier") != case.get("nn_multiplier")
    ):
        raise Formal550LocalError(f"{case_id} SIMIND provenance contract mismatch")
    if (
        _switch(provenance.get("command"), "/RR:") != int(case["rr_seed"])
        or _switch(provenance.get("command"), "/NN:") != int(case["nn_multiplier"])
    ):
        raise Formal550LocalError(f"{case_id} SIMIND command binding mismatch")
    inputs = provenance.get("inputs")
    planned_inputs = case.get("inputs")
    if not isinstance(inputs, Mapping) or not isinstance(planned_inputs, Mapping):
        raise Formal550LocalError(f"{case_id} SIMIND input binding is missing")
    if (
        inputs.get("source_sha256") != planned_inputs.get("source_sha256")
        or inputs.get("density_sha256") != planned_inputs.get("density_sha256")
    ):
        raise Formal550LocalError(f"{case_id} SIMIND input binding mismatch")
    try:
        audit = audit_simind_completion(
            case_dir / case_id,
            expected_shape=(60, 128, 128),
            exit_code=0,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        raise Formal550LocalError(f"{case_id} SIMIND quartet validation failed") from exc
    return {
        "case_id": case_id,
        "node_id": node_id,
        "dataset_id": case.get("dataset_id"),
        "dataset_role": case.get("dataset_role"),
        "split": case.get("split"),
        "rr_seed": int(case["rr_seed"]),
        "projection_sum": audit.projection_sum,
        "a00_sha256": audit.sha256["a00"],
        "case_marker_sha256": sha256_file(marker_path),
        "simind_provenance_sha256": sha256_file(provenance_path),
        "status": "pass",
    }


def _validate_downloaded_results(
    *,
    results_root: Path,
    plan: Mapping[str, object],
    bundle_sha: str,
    master: Mapping[str, object],
) -> None:
    """Validate every assigned node and every completed Task13 case exactly once."""

    nodes = plan["expected_nodes"]
    plan_cases = plan["cases"]
    execution = plan.get("execution")
    runtime = plan.get("linux_runtime")
    if not isinstance(execution, Mapping) or not isinstance(runtime, Mapping):
        raise Formal550LocalError("Task13 execution/runtime contract is malformed")
    parallel = execution.get("requested_parallel_by_node")
    if not isinstance(parallel, Mapping):
        raise Formal550LocalError("Task13 node parallelism contract is missing")
    expected_simind_sha = runtime.get("simind_sha256")
    if not isinstance(expected_simind_sha, str):
        raise Formal550LocalError("Task13 SIMIND binary binding is missing")
    results: list[Mapping[str, object]] = []
    node_counts: dict[str, int] = {}
    runtime_fingerprints: dict[str, Mapping[str, object]] = {}
    observed_case_ids: set[str] = set()
    for node_id in nodes:
        node_root = results_root / "nodes" / node_id
        if not node_root.is_dir():
            raise Formal550LocalError(f"missing node results: {node_id}")
        if (node_root / "NODE_FAILED.json").exists():
            raise Formal550LocalError(f"downloaded results retain NODE_FAILED.json: {node_id}")
        node_complete = _read_object(
            node_root / "NODE_COMPLETE.json", f"{node_id} node completion"
        )
        assigned = [case for case in plan_cases if case.get("node_id") == node_id]
        expected_ids = sorted(str(case["case_id"]) for case in assigned)
        if (
            node_complete.get("schema_version")
            != "pars_v2_task13_formal550_node_complete_v1"
            or node_complete.get("status") != "complete"
            or node_complete.get("bundle_manifest_sha256") != bundle_sha
            or node_complete.get("case_ids") != expected_ids
            or int(node_complete.get("case_count", -1)) != len(expected_ids)
            or node_complete.get("max_parallel") != parallel.get(node_id)
        ):
            raise Formal550LocalError(f"downloaded node completion mismatch: {node_id}")
        fingerprint = node_complete.get("runtime_fingerprint")
        if not isinstance(fingerprint, Mapping):
            raise Formal550LocalError(f"downloaded node runtime is missing: {node_id}")
        runtime_fingerprints[node_id] = fingerprint
        node_counts[node_id] = len(expected_ids)
        for case in assigned:
            case_id = str(case["case_id"])
            if case_id in observed_case_ids:
                raise Formal550LocalError(f"duplicate downloaded Task13 case: {case_id}")
            observed_case_ids.add(case_id)
            results.append(
                _validate_downloaded_case(
                    case_dir=node_root / "cases" / case_id,
                    case=case,
                    node_id=node_id,
                    bundle_sha=bundle_sha,
                    expected_simind_sha=expected_simind_sha,
                )
            )
    results.sort(key=lambda item: str(item["case_id"]))
    planned_ids = {str(case["case_id"]) for case in plan_cases}
    if observed_case_ids != planned_ids or len(observed_case_ids) != 550:
        raise Formal550LocalError("downloaded result set is not the exact frozen 550 cases")
    if (
        master.get("node_case_counts") != node_counts
        or master.get("runtime_fingerprints") != runtime_fingerprints
        or master.get("cases") != results
    ):
        raise Formal550LocalError("downloaded Task13 master case/node binding mismatch")
    projection_summary = {
        "minimum": min(float(item["projection_sum"]) for item in results),
        "maximum": max(float(item["projection_sum"]) for item in results),
        "mean": sum(float(item["projection_sum"]) for item in results) / 550,
    }
    if master.get("projection_sum_summary") != projection_summary:
        raise Formal550LocalError("downloaded Task13 projection summary mismatch")


def validate_formal_inputs(
    results_root: Path,
    bundle_root: Path,
    preflight_root: Path,
) -> Mapping[str, RoleContract]:
    """Bind downloaded results to the immutable Task13 bundle and both preflights.

    This intentionally stops before any local case writing or SIMIND execution.
    """

    results_root = Path(results_root).resolve()
    bundle_root = Path(bundle_root).resolve()
    preflight_root = Path(preflight_root).resolve()
    _, plan = _formal_bundle(bundle_root)
    contracts = {
        role: _validate_role_contract(
            role=role,
            preflight_root=preflight_root / role,
            bundle_root=bundle_root,
            plan=plan,
        )
        for role in ("main", "negative")
    }
    campaign = plan.get("dataset")
    expected_campaign = {
        "dataset_id": "PAR-S-V2-FORMAL550",
        "dataset_version": "2.0.0",
        "case_count": 550,
    }
    if not isinstance(campaign, Mapping) or not _exact_contract_value(
        campaign, expected_campaign
    ):
        raise Formal550LocalError("Task13 campaign identity mismatch")
    expected_ids = {
        role: contract.expected_case_ids for role, contract in contracts.items()
    }
    planned_datasets = plan.get("datasets")
    if (
        not isinstance(planned_datasets, Mapping)
        or set(planned_datasets) != {"main", "negative"}
        or any(
            not _exact_contract_value(
                planned_datasets.get(role), _role_dataset(role)
            )
            for role in ("main", "negative")
        )
    ):
        raise Formal550LocalError("Task13 plan role dataset bindings mismatch")
    plan_cases = plan.get("cases")
    if not isinstance(plan_cases, list) or not all(
        isinstance(item, Mapping) for item in plan_cases
    ):
        raise Formal550LocalError("Task13 plan cases are malformed")
    for role, ids in expected_ids.items():
        observed = tuple(
            str(item.get("case_id"))
            for item in plan_cases
            if item.get("dataset_role") == role
        )
        if observed != ids:
            raise Formal550LocalError(f"Task13 plan {role} case binding mismatch")
    if len(plan_cases) != 550:
        raise Formal550LocalError("Task13 plan case count mismatch")
    if any(
        not isinstance(case.get("rr_seed"), int)
        or isinstance(case.get("rr_seed"), bool)
        for case in plan_cases
    ):
        raise Formal550LocalError("Task13 plan /RR seeds are malformed")
    rr_seeds = [int(case["rr_seed"]) for case in plan_cases]
    if len(set(rr_seeds)) != 550:
        raise Formal550LocalError("Task13 requires exactly 550 unique /RR seeds")
    nodes = plan.get("expected_nodes")
    if nodes != ["cnc5", "cnc7", "cnc8"]:
        raise Formal550LocalError("Task13 expected-node contract mismatch")
    if any(case.get("node_id") not in nodes for case in plan_cases):
        raise Formal550LocalError("Task13 plan case/node binding mismatch")
    bundle_sha = sha256_file(bundle_root / "BUNDLE_MANIFEST.json")
    remote = _read_object(results_root / "REMOTE_PREFLIGHT.json", "remote preflight")
    master = _read_object(results_root / "TASK13_FORMAL550_MASTER.json", "Task13 master")
    if (
        remote.get("schema_version") != "pars_v2_task13_formal550_remote_preflight_v1"
        or remote.get("status") != "pass"
        or remote.get("bundle_manifest_sha256") != bundle_sha
        or master.get("schema_version") != "pars_v2_task13_formal550_master_v1"
        or master.get("status") != "pass"
        or master.get("bundle_manifest_sha256") != bundle_sha
    ):
        raise Formal550LocalError("downloaded master/remote-preflight bundle binding mismatch")
    if (
        master.get("dataset") != campaign
        or int(master.get("case_count", -1)) != 550
        or master.get("role_case_counts") != {"main": 500, "negative": 50}
        or master.get("go_for_local_case_writer_and_dataset_freeze") is not False
    ):
        raise Formal550LocalError("downloaded Task13 master contract mismatch")
    raw_master_cases = master.get("cases")
    if not isinstance(raw_master_cases, list) or not all(
        isinstance(item, Mapping) for item in raw_master_cases
    ):
        raise Formal550LocalError("downloaded Task13 master cases are malformed")
    for role, ids in expected_ids.items():
        observed = tuple(
            str(item.get("case_id"))
            for item in raw_master_cases
            if item.get("dataset_role") == role
        )
        if observed != ids:
            raise Formal550LocalError(f"downloaded Task13 master {role} case binding mismatch")
    if len(raw_master_cases) != 550:
        raise Formal550LocalError("downloaded Task13 master case count mismatch")
    _validate_downloaded_results(
        results_root=results_root,
        plan=plan,
        bundle_sha=bundle_sha,
        master=master,
    )
    return contracts


def _safe_repo_path(relative_value: object, label: str) -> Path:
    if not isinstance(relative_value, str) or not relative_value:
        raise Formal550LocalError(f"{label} must be a non-empty relative path")
    relative = Path(relative_value)
    if relative.is_absolute():
        raise Formal550LocalError(f"{label} must be repository-relative")
    root = REPO_ROOT.resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise Formal550LocalError(f"{label} escapes the repository") from exc
    if not path.is_file():
        raise Formal550LocalError(f"{label} not found: {path}")
    return path


def _copy_immutable(source: Path, destination: Path) -> None:
    payload = Path(source).read_bytes()
    destination = Path(destination)
    if destination.exists():
        if not destination.is_file() or destination.read_bytes() != payload:
            raise Formal550LocalError(f"immutable output file drift: {destination}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _next_attempt_dir(work_root: Path, case_id: str) -> Path:
    parent = Path(work_root) / "inputs" / case_id
    if not parent.exists():
        return parent / "attempt_001"
    indices: list[int] = []
    for path in parent.iterdir():
        if not path.is_dir() or not path.name.startswith("attempt_"):
            continue
        try:
            indices.append(int(path.name.removeprefix("attempt_")))
        except ValueError:
            continue
    return parent / f"attempt_{max(indices, default=0) + 1:03d}"


def _completed_downloaded_result(case_dir: Path, case_id: str) -> SimindRunResult:
    final_dir = Path(case_dir).resolve()
    provenance = _read_object(
        final_dir / "run_provenance.json",
        f"{case_id} Linux SIMIND provenance",
    )
    if (
        provenance.get("schema_version") != "pars_simind_run_v2"
        or provenance.get("status") != "complete"
        or provenance.get("case_id") != case_id
        or provenance.get("exit_code") != 0
        or provenance.get("expected_shape") != [60, 128, 128]
    ):
        raise Formal550LocalError(
            f"{case_id}: completed Linux SIMIND provenance is required"
        )
    completion = provenance.get("completion_audit")
    hashes = completion.get("sha256") if isinstance(completion, Mapping) else None
    if not isinstance(hashes, Mapping):
        raise Formal550LocalError(
            f"{case_id}: Linux SIMIND completion hashes are missing"
        )
    return SimindRunResult(
        case_id=case_id,
        success=True,
        exit_code=0,
        command=tuple(str(value) for value in provenance.get("command", [])),
        expected_shape=(60, 128, 128),
        started_utc=str(provenance.get("started_utc", "")),
        finished_utc=str(provenance.get("finished_utc", "")),
        final_dir=final_dir,
        output_hashes={str(key): str(value) for key, value in hashes.items()},
    )


def _downloaded_cases(
    results_root: Path,
    plan: Mapping[str, object],
) -> dict[str, DownloadedCaseV2]:
    raw_cases = plan.get("cases")
    if not isinstance(raw_cases, list):
        raise Formal550LocalError("Task13 plan cases are malformed")
    downloaded: dict[str, DownloadedCaseV2] = {}
    for raw in raw_cases:
        if not isinstance(raw, Mapping):
            raise Formal550LocalError("Task13 plan case is malformed")
        case_id = str(raw["case_id"])
        node_id = str(raw["node_id"])
        node_root = Path(results_root) / "nodes" / node_id
        downloaded[case_id] = DownloadedCaseV2(
            case_id=case_id,
            node_id=node_id,
            case_dir=node_root / "cases" / case_id,
            case_marker_path=node_root / "cases" / case_id / "TASK13_CASE.json",
            node_complete_path=node_root / "NODE_COMPLETE.json",
        )
    if len(downloaded) != 550:
        raise Formal550LocalError("Task13 downloaded case index is not exactly 550")
    return downloaded


def _bound_summary(
    role: str,
    prepared: object,
    entry: Mapping[str, object],
) -> dict[str, object]:
    if role == "main":
        summary = summarize_prepared_population_case(prepared)
    elif role == "negative":
        summary = summarize_prepared_negative_case(prepared)
    else:
        raise ValueError(f"unknown formal role: {role}")
    summary.update(
        {
            "case_family_id": str(entry["case_family_id"]),
            "profile_id": str(entry["profile_id"]),
            "split": str(entry["split"]),
            "population_weight": float(entry["population_weight"]),
            "sampling_probability": float(entry["sampling_probability"]),
        }
    )
    return summary


def _role_paths(
    *,
    bundle_root: Path,
    contracts: Mapping[str, RoleContract],
) -> tuple[dict[str, Path], dict[str, dict[str, Path]]]:
    config_path = Path(bundle_root) / "config" / "task13_formal550_v1.json"
    config = _read_object(config_path, "Task13 formal config")
    if config.get("schema_version") != "pars_v2_task13_formal550_config_v1":
        raise Formal550LocalError("Task13 formal config schema mismatch")
    raw_paths = config.get("paths")
    if not isinstance(raw_paths, Mapping):
        raise Formal550LocalError("Task13 formal config paths are missing")
    common = {
        "config": config_path,
        "scanner": _safe_repo_path(raw_paths.get("scanner"), "scanner config"),
        "evidence_registry": _safe_repo_path(
            raw_paths.get("evidence_registry"), "evidence registry"
        ),
        "task12g_acceptance": _safe_repo_path(
            raw_paths.get("task12g_release"), "Task12G acceptance"
        ),
        "smc": Path(bundle_root) / "runtime" / "ge870_czt.smc",
        "simind_ini": Path(bundle_root) / "runtime" / "simind.ini",
    }
    role_paths = {
        "main": {
            "profile": _safe_repo_path(
                raw_paths.get("main_profile"), "main population profile"
            ),
            "generation_profile": _safe_repo_path(
                raw_paths.get("main_profile"), "main generation profile"
            ),
        },
        "negative": {
            "profile": _safe_repo_path(
                raw_paths.get("negative_profile"), "negative semantic profile"
            ),
            "generation_profile": _safe_repo_path(
                raw_paths.get("negative_generation_profile"),
                "negative generation profile",
            ),
        },
    }
    plan = _read_object(Path(bundle_root) / "TASK13_PLAN.json", "Task13 plan")
    runtime = plan.get("runtime")
    if not isinstance(runtime, Mapping):
        raise Formal550LocalError("Task13 runtime paths are missing")
    expected_common = {
        "smc": runtime.get("smc_sha256"),
        "simind_ini": runtime.get("simind_ini_sha256"),
        "task12g_acceptance": plan.get("release_evidence_sha256"),
    }
    for name, digest in expected_common.items():
        path = common[name]
        if not path.is_file() or sha256_file(path) != digest:
            raise Formal550LocalError(f"Task13 {name} bytes drifted")
    for role, contract in contracts.items():
        report = _read_object(
            contract.preflight_root / "PREFLIGHT.json", f"{role} preflight"
        )
        expected = {
            "config": report.get("config_sha256"),
            "profile": report.get("profile_sha256"),
            "generation_profile": report.get("generation_profile_sha256"),
            "scanner": report.get("scanner_sha256"),
            "evidence_registry": report.get("evidence_registry_sha256"),
            "task12g_acceptance": report.get("release_acceptance_sha256"),
        }
        observed_paths = {**common, **role_paths[role]}
        drifted = [
            name
            for name, digest in expected.items()
            if sha256_file(observed_paths[name]) != digest
        ]
        if drifted:
            raise Formal550LocalError(
                f"Task13 {role} local config bytes drifted: {drifted}"
            )
    return common, role_paths


def _role_runtime_document(
    *,
    role: str,
    contract: RoleContract,
    output_root: Path,
    work_root: Path,
    bundle_root: Path,
    results_root: Path,
    paths: Mapping[str, Path],
) -> dict[str, object]:
    return {
        "schema_version": "pars_v2_task13_formal550_role_runtime_v1",
        "status": "bound",
        "role": role,
        "dataset": {
            key: contract.generation[key]
            for key in ("dataset_id", "dataset_version", "dataset_role")
        },
        "output_root": str(Path(output_root).resolve()),
        "work_root": str(Path(work_root).resolve()),
        "bundle_manifest_sha256": sha256_file(
            Path(bundle_root) / "BUNDLE_MANIFEST.json"
        ),
        "task13_plan_sha256": sha256_file(Path(bundle_root) / "TASK13_PLAN.json"),
        "task13_master_sha256": sha256_file(
            Path(results_root) / "TASK13_FORMAL550_MASTER.json"
        ),
        "preflight_sha256": sha256_file(
            contract.preflight_root / "PREFLIGHT.json"
        ),
        "generation_plan_sha256": contract.generation["sha256"],
        "split_plan_sha256": contract.split["sha256"],
        "config_hashes": {
            name: sha256_file(path)
            for name, path in sorted(paths.items())
            if name
            in {
                "config",
                "profile",
                "generation_profile",
                "scanner",
                "evidence_registry",
                "task12g_acceptance",
                "smc",
                "simind_ini",
            }
        },
        "simind_execution": "forbidden_use_downloaded_linux_outputs_only",
        "resume_contract": "verify_hashes_and_skip_completed_cases",
    }


def _load_or_write_role_runtime(
    output_root: Path,
    runtime: Mapping[str, object],
) -> Path:
    path = Path(output_root) / "FORMAL_RUNTIME.json"
    if path.exists():
        if _read_object(path, "Task13 role runtime") != dict(runtime):
            raise Formal550LocalError("Task13 role runtime binding changed")
        return path
    atomic_write_json(path, dict(runtime))
    return path


def _revalidate_frozen_role(
    *,
    role: str,
    contract: RoleContract,
    role_output: Path,
    runtime_document: Mapping[str, object],
) -> object:
    """Read-only revalidation of an already frozen role dataset."""

    root = Path(role_output)
    marker_path = root / DATASET_COMPLETE_FILENAME
    if not marker_path.is_file():
        raise Formal550LocalError(f"frozen {role} completion marker is missing")
    for name in ("GENERATION_PLAN.json", "SPLIT_PLAN.json"):
        source = contract.preflight_root / name
        destination = root / name
        if (
            not destination.is_file()
            or not source.is_file()
            or destination.read_bytes() != source.read_bytes()
        ):
            raise Formal550LocalError(f"frozen {role} {name} is missing or drifted")
    runtime_path = root / "FORMAL_RUNTIME.json"
    if (
        not runtime_path.is_file()
        or _read_object(runtime_path, f"frozen {role} runtime")
        != dict(runtime_document)
    ):
        raise Formal550LocalError(
            f"frozen {role} FORMAL_RUNTIME.json is missing or drifted"
        )
    records = _load_role_records(root, contract.expected_case_ids)
    records.sort(key=lambda item: item.case_id)
    return freeze_dataset(records, _dataset_contract(contract, root))


def _freeze_and_revalidate_role(
    *,
    role: str,
    records: Sequence[object],
    dataset_contract: DatasetContractV2,
    contract: RoleContract,
    role_output: Path,
    runtime_document: Mapping[str, object],
) -> object:
    """Freeze once, then require the same read-only frozen-role audit to pass."""

    freeze_dataset(records, dataset_contract)
    return _revalidate_frozen_role(
        role=role,
        contract=contract,
        role_output=role_output,
        runtime_document=runtime_document,
    )


def _case_artifacts(
    *,
    prepared: object,
    result: SimindRunResult,
    byte_identity_path: Path,
    downloaded: DownloadedCaseV2,
    contract: RoleContract,
    bundle_root: Path,
    results_root: Path,
    runtime_path: Path,
    paths: Mapping[str, Path],
) -> dict[str, Path]:
    provenance = _read_object(
        downloaded.case_dir / "run_provenance.json",
        f"{downloaded.case_id} SIMIND provenance",
    )
    smc_record = provenance.get("smc")
    ini_record = provenance.get("simind_ini")
    if not isinstance(smc_record, Mapping) or not isinstance(ini_record, Mapping):
        raise Formal550LocalError(
            f"{downloaded.case_id}: SIMIND snapshots are missing"
        )
    artifacts = simind_extra_artifacts(prepared, result)
    artifacts.update(
        {
            "formal_config": paths["config"],
            "formal_runtime": runtime_path,
            "role_preflight": contract.preflight_root / "PREFLIGHT.json",
            "role_input_bundle": contract.preflight_root / "INPUT_BUNDLE.json",
            "preflight_byte_identity": byte_identity_path,
            "generation_plan": contract.preflight_root / "GENERATION_PLAN.json",
            "split_plan": contract.preflight_root / "SPLIT_PLAN.json",
            "task13_bundle_manifest": Path(bundle_root) / "BUNDLE_MANIFEST.json",
            "task13_execution_plan": Path(bundle_root) / "TASK13_PLAN.json",
            "task13_case_preflight": contract.preflight_root
            / "cases"
            / downloaded.case_id
            / "CASE_PREFLIGHT.json",
            "task13_remote_preflight": Path(results_root) / "REMOTE_PREFLIGHT.json",
            "task13_node_complete": downloaded.node_complete_path,
            "task13_case_marker": downloaded.case_marker_path,
            "task13_master": Path(results_root) / "TASK13_FORMAL550_MASTER.json",
            "population_profile": paths["profile"],
            "generation_profile": paths["generation_profile"],
            "scanner_config": paths["scanner"],
            "evidence_registry": paths["evidence_registry"],
            "task12g_acceptance": paths["task12g_acceptance"],
            "simind_smc_snapshot": downloaded.case_dir
            / str(smc_record["source_name"]),
            "simind_ini_snapshot": downloaded.case_dir
            / str(ini_record["source_name"]),
        }
    )
    if set(artifacts) != set(REQUIRED_ARTIFACTS) - {
        "phantom_npz",
        "metadata_json",
    }:
        raise Formal550LocalError(
            f"{downloaded.case_id}: Task13 artifact set is not exact"
        )
    return artifacts


def _initialize_campaign_roots(
    output_root: Path,
    work_root: Path,
    *,
    resume: bool,
) -> None:
    output_exists = Path(output_root).exists()
    work_exists = Path(work_root).exists()
    if output_exists != work_exists:
        raise Formal550LocalError("Task13 output/work roots are inconsistent")
    if output_exists:
        if not resume:
            raise FileExistsError("Task13 roots already exist; use --resume")
        return
    output_root = Path(output_root)
    work_root = Path(work_root)
    output_root.mkdir(parents=True, exist_ok=False)
    try:
        work_root.mkdir(parents=True, exist_ok=False)
    except Exception:
        output_root.rmdir()
        raise


def _finalize_role(
    *,
    role: str,
    contract: RoleContract,
    output_root: Path,
    work_root: Path,
    bundle_root: Path,
    results_root: Path,
    plan: Mapping[str, object],
    downloaded: Mapping[str, DownloadedCaseV2],
    common_paths: Mapping[str, Path],
    role_paths: Mapping[str, Path],
    max_cases: int | None,
) -> tuple[object | None, int]:
    role_output = Path(output_root) / role
    role_work = Path(work_root) / role
    paths = {**common_paths, **role_paths}
    runtime_document = _role_runtime_document(
        role=role,
        contract=contract,
        output_root=role_output,
        work_root=role_work,
        bundle_root=bundle_root,
        results_root=results_root,
        paths=paths,
    )
    dataset_contract = _dataset_contract(contract, role_output)
    marker_path = role_output / DATASET_COMPLETE_FILENAME
    if marker_path.exists():
        frozen = _revalidate_frozen_role(
            role=role,
            contract=contract,
            role_output=role_output,
            runtime_document=runtime_document,
        )
        records = _load_role_records(role_output, contract.expected_case_ids)
        records.sort(key=lambda item: item.case_id)
        _write_role_progress(
            work_root,
            role=role,
            status="complete",
            records=records,
            total_count=len(contract.expected_case_ids),
            dataset_complete=frozen.to_dict(),
        )
        return frozen, 0
    role_output.mkdir(parents=True, exist_ok=True)
    role_work.mkdir(parents=True, exist_ok=True)
    _copy_immutable(
        contract.preflight_root / "GENERATION_PLAN.json",
        role_output / "GENERATION_PLAN.json",
    )
    _copy_immutable(
        contract.preflight_root / "SPLIT_PLAN.json",
        role_output / "SPLIT_PLAN.json",
    )
    runtime_path = _load_or_write_role_runtime(role_output, runtime_document)
    runtime_document = _read_object(runtime_path, f"{role} formal runtime")
    registry = load_evidence_registry(paths["evidence_registry"])
    profile = load_profile(paths["generation_profile"], registry)
    scanner = load_profile(paths["scanner"], registry)
    grid = GridSpecV2(
        shape=tuple(int(value) for value in scanner.value("matrix")),
        voxel_size_mm=float(scanner.value("voxel_size_mm")),
    )
    report = _read_object(contract.preflight_root / "PREFLIGHT.json", f"{role} preflight")
    input_reference = report.get("input_bundle")
    if not isinstance(input_reference, Mapping):
        raise Formal550LocalError(f"{role} preflight input bundle binding is missing")
    preflight_inputs = load_and_validate_preflight_input_bundle(
        contract.preflight_root / "PREFLIGHT.json",
        input_reference,
        expected_case_ids=list(contract.expected_case_ids),
        case_summaries=[
            contract.summaries[case_id] for case_id in contract.expected_case_ids
        ],
    )
    records = _load_role_records(role_output, contract.expected_case_ids)
    records.sort(key=lambda item: item.case_id)
    task_cases = {
        str(item["case_id"]): item
        for item in plan["cases"]
        if isinstance(item, Mapping)
    }
    completed_ids = {record.case_id for record in records}
    pending = [
        entry
        for entry in contract.entries
        if str(entry["case_id"]) not in completed_ids
    ]
    processed = 0
    execution = plan["execution"]
    base_histories = int(execution["base_histories_per_projection"])
    max_tumor_attempts = int(execution["max_tumor_target_attempts"])
    for entry in pending:
        if max_cases is not None and processed >= max_cases:
            _write_role_progress(
                work_root,
                role=role,
                status="paused",
                records=records,
                total_count=len(contract.expected_case_ids),
            )
            return None, processed
        case_id = str(entry["case_id"])
        _write_role_progress(
            work_root,
            role=role,
            status="running",
            records=records,
            total_count=len(contract.expected_case_ids),
            current_case_id=case_id,
        )
        try:
            attempt_dir = _next_attempt_dir(role_work, case_id)
            prepared = _prepare_role_case(
                role=role,
                case_id=case_id,
                entry=entry,
                profile=profile,
                grid=grid,
                global_seed=int(contract.generation["global_seed"]),
                base_histories=base_histories,
                work_dir=attempt_dir,
                max_tumor_attempts=max_tumor_attempts,
            )
            regenerated = _bound_summary(role, prepared, entry)
            frozen_summary = contract.summaries[case_id]
            if sha256_json(regenerated) != sha256_json(frozen_summary):
                raise Formal550LocalError(
                    f"{case_id}: regenerated semantic summary differs from frozen "
                    f"preflight; observed_sha256={sha256_json(regenerated)}; "
                    f"frozen_sha256={sha256_json(frozen_summary)}"
                )
            byte_identity_path = attempt_dir / "PREFLIGHT_BYTE_IDENTITY.json"
            prove_preflight_byte_identity(
                generated_source=prepared.source_bin,
                generated_density=prepared.density_bin,
                frozen=preflight_inputs[case_id],
                generated_arrays=prepared.arrays,
                evidence_path=byte_identity_path,
            )
            prepared = replace(
                prepared,
                source_bin=preflight_inputs[case_id].source_path,
                density_bin=preflight_inputs[case_id].density_path,
            )
            task_case = task_cases[case_id]
            if (
                prepared.seeds.simind != int(task_case["rr_seed"])
                or int(task_case["nn_multiplier"]) != 1
            ):
                raise Formal550LocalError(f"{case_id}: frozen /RR or /NN mismatch")
            downloaded_case = downloaded[case_id]
            result = _completed_downloaded_result(
                downloaded_case.case_dir,
                case_id,
            )
            metadata = build_completed_metadata(
                prepared,
                profile_path=paths["profile"],
                scanner_path=paths["scanner"],
                evidence_registry_path=paths["evidence_registry"],
                simind_ini_path=paths["simind_ini"],
                scanner=scanner,
                result=result,
                runtime_binding=runtime_document,
            )
            artifacts = _case_artifacts(
                prepared=prepared,
                result=result,
                byte_identity_path=byte_identity_path,
                downloaded=downloaded_case,
                contract=contract,
                bundle_root=bundle_root,
                results_root=results_root,
                runtime_path=runtime_path,
                paths=paths,
            )
            record = write_case_v2(
                CasePayloadV2(
                    case_id=case_id,
                    case_family_id=str(entry["case_family_id"]),
                    profile_id=str(entry["profile_id"]),
                    dataset_id=str(contract.generation["dataset_id"]),
                    dataset_version=str(contract.generation["dataset_version"]),
                    dataset_role=role,
                    split=str(entry["split"]),
                    population_weight=float(entry["population_weight"]),
                    sampling_probability=float(entry["sampling_probability"]),
                    arrays=prepared.arrays,
                    metadata=metadata,
                    extra_artifacts=artifacts,
                ),
                role_output,
                resume=True,
            )
            records.append(record)
            records.sort(key=lambda item: item.case_id)
            processed += 1
            _write_role_progress(
                work_root,
                role=role,
                status="running",
                records=records,
                total_count=len(contract.expected_case_ids),
            )
        except Exception as exc:
            _write_role_progress(
                work_root,
                role=role,
                status="failed",
                records=records,
                total_count=len(contract.expected_case_ids),
                current_case_id=case_id,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
    try:
        frozen = _freeze_and_revalidate_role(
            role=role,
            records=records,
            dataset_contract=dataset_contract,
            contract=contract,
            role_output=role_output,
            runtime_document=runtime_document,
        )
    except Exception as exc:
        _write_role_progress(
            work_root,
            role=role,
            status="failed",
            records=records,
            total_count=len(contract.expected_case_ids),
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    revalidated_records = _load_role_records(
        role_output, contract.expected_case_ids
    )
    revalidated_records.sort(key=lambda item: item.case_id)
    _write_role_progress(
        work_root,
        role=role,
        status="complete",
        records=revalidated_records,
        total_count=len(contract.expected_case_ids),
        dataset_complete=frozen.to_dict(),
    )
    return frozen, processed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--sidecar", type=Path, default=DEFAULT_SIDECAR)
    parser.add_argument("--staging-root", type=Path, default=DEFAULT_STAGING_ROOT)
    parser.add_argument("--preflight-root", type=Path, default=DEFAULT_PREFLIGHT_ROOT)
    parser.add_argument("--bundle-root", type=Path, default=DEFAULT_BUNDLE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-cases", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.max_cases is not None and not 1 <= args.max_cases <= 550:
        raise ValueError("--max-cases must be within 1..550")
    results = stage_results_archive(
        args.archive,
        args.sidecar,
        args.staging_root,
        resume=args.resume,
    )
    contracts = validate_formal_inputs(results, args.bundle_root, args.preflight_root)
    if args.validate_only:
        print(
            json.dumps(
                {
                    "status": "validated",
                    "results_root": str(results),
                    "role_case_counts": {
                        role: len(contract.expected_case_ids)
                        for role, contract in contracts.items()
                    },
                    "validate_only": True,
                    "next_action": "run without --validate-only to write role datasets",
                },
                ensure_ascii=False,
            )
        )
        return 0
    output_root = args.output_root.resolve()
    work_root = args.work_root.resolve()
    bundle_root = args.bundle_root.resolve()
    _initialize_campaign_roots(output_root, work_root, resume=args.resume)
    _, plan = _formal_bundle(bundle_root)
    downloaded = _downloaded_cases(results, plan)
    common_paths, role_paths = _role_paths(
        bundle_root=bundle_root,
        contracts=contracts,
    )
    markers: dict[str, object] = {}
    processed = 0
    try:
        for role in ("main", "negative"):
            remaining = (
                None
                if args.max_cases is None
                else max(args.max_cases - processed, 0)
            )
            marker, role_processed = _finalize_role(
                role=role,
                contract=contracts[role],
                output_root=output_root,
                work_root=work_root,
                bundle_root=bundle_root,
                results_root=results,
                plan=plan,
                downloaded=downloaded,
                common_paths=common_paths,
                role_paths=role_paths[role],
                max_cases=remaining,
            )
            processed += role_processed
            if marker is None:
                print(
                    json.dumps(
                        {
                            "status": "paused",
                            "role": role,
                            "processed_this_run": processed,
                        },
                        ensure_ascii=False,
                    )
                )
                return 3
            markers[role] = marker
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                },
                ensure_ascii=False,
            )
        )
        return 1
    campaign_path = _write_campaign_complete(
        output_root,
        markers["main"],
        markers["negative"],
    )
    print(
        json.dumps(
            {
                "status": "complete",
                "results_root": str(results),
                "output_root": str(output_root),
                "work_root": str(work_root),
                "campaign_marker": str(campaign_path),
                "role_case_counts": {
                    role: len(contract.expected_case_ids)
                    for role, contract in contracts.items()
                },
                "processed_this_run": processed,
                "manifest_sha256": {
                    role: markers[role].manifest_sha256
                    for role in ("main", "negative")
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
