"""Validate, write, and freeze downloaded Task13 Formal550 Linux results."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
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
        },
        "negative": {
            "dataset_id": "PAR-S-TARE-HCC-NoPVI-NEG-v2",
            "dataset_version": "2.0.0",
            "dataset_role": "negative",
            "case_count": 50,
        },
    }
    return datasets[role]


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
    if (
        generation.get("schema_version") != "pars_generation_plan_v2"
        or split.get("schema_version") != "pars_split_plan_v2"
        or report.get("schema_version") != "pars_v2_task12f_linux50_preflight_v2"
        or report.get("status") != "pass"
        or report.get("simind_launched") is not False
    ):
        raise Formal550LocalError(f"{role} local preflight schema/status mismatch")
    dataset = _role_dataset(role)
    if any(generation.get(key) != value for key, value in dataset.items()):
        raise Formal550LocalError(f"{role} generation dataset identity mismatch")
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
    if not isinstance(campaign, Mapping) or campaign != {
        "dataset_id": "PAR-S-V2-FORMAL550",
        "dataset_version": "2.0.0",
        "case_count": 550,
    }:
        raise Formal550LocalError("Task13 campaign identity mismatch")
    expected_ids = {
        role: contract.expected_case_ids for role, contract in contracts.items()
    }
    planned_datasets = plan.get("datasets")
    if not isinstance(planned_datasets, Mapping) or any(
        planned_datasets.get(role) != _role_dataset(role)
        for role in ("main", "negative")
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
    print(
        json.dumps(
            {
                "status": "validated",
                "results_root": str(results),
                "role_case_counts": {
                    role: len(contract.expected_case_ids)
                    for role, contract in contracts.items()
                },
                "validate_only": args.validate_only,
                "next_action": "local case writing is outside this archive-validation layer",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
