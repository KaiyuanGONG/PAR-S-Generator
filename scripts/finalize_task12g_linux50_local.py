"""Write and freeze the downloaded Task 12F Linux-50 cohort as PAR-S V2 cases."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import uuid
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from finalize_task12f_linux50_master import _validate_case  # noqa: E402
from freeze_dataset_v2 import load_generation_plan  # noqa: E402
from run_pilot15_v2 import _summary_from_record  # noqa: E402
from task12f_linux50_common import (  # noqa: E402
    MASTER_SCHEMA,
    NODE_COMPLETE_SCHEMA,
    REMOTE_PREFLIGHT_SCHEMA,
    cases_for_node,
    load_plan,
    read_json,
    sha256_file,
    validate_bundle,
)

from core.case_writer_v2 import (  # noqa: E402
    DATASET_COMPLETE_FILENAME,
    CasePayloadV2,
    DatasetContractV2,
    freeze_dataset,
    load_case_record_v2,
    write_case_v2,
)
from core.liver_geometry import GridSpecV2  # noqa: E402
from core.pilot_v2 import (  # noqa: E402
    build_completed_metadata,
    simind_extra_artifacts,
)
from core.production_v2 import (  # noqa: E402
    prepare_population_case,
    summarize_prepared_population_case,
)
from core.provenance import atomic_write_json, sha256_json  # noqa: E402
from core.reproducibility_v2 import (  # noqa: E402
    canonical_json_sha256,
    capture_python_runtime,
    load_and_validate_preflight_input_bundle,
    prove_preflight_byte_identity,
)
from core.schemas_v2 import (  # noqa: E402
    FROZEN_LOADER_TRANSFORM_ID,
    PROJECTION_COORDINATE_CONTRACT_ID,
    load_evidence_registry,
    load_profile,
)
from core.simind_exec import SimindRunResult  # noqa: E402


TASK12G_RUNTIME_SCHEMA = "pars_v2_task12g_linux50_runtime_v1"
TASK12G_PROGRESS_SCHEMA = "pars_v2_task12g_linux50_progress_v1"
TASK12G_GENERATION_GATE_SCHEMA = "pars_v2_task12g_linux50_generation_gate_v1"
EXPECTED_CASE_COUNT = 50

DEFAULT_PREFLIGHT_ROOT = Path(
    r"D:\PFE-U\PAR\outputs\task12f_linux50_preflight_v2"
)
DEFAULT_BUNDLE_ROOT = Path(
    r"D:\PFE-U\PAR\outputs\task12f_linux50_upload_v2"
    r"\pars_v2_task12f_linux50_bundle_v2"
)
DEFAULT_RESULTS_ROOT = Path(
    r"D:\PFE-U\PAR\outputs\task12g_linux50_staging_v1"
    r"\task12f_linux50_results"
)
DEFAULT_OUTPUT_ROOT = Path(r"D:\PFE-U\PAR\outputs\pars_v2_linux50_v2")
DEFAULT_WORK_ROOT = Path(r"D:\PFE-U\PAR\outputs\pars_v2_linux50_v2_work")

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
)


@dataclass(frozen=True)
class DownloadedCaseV2:
    case_id: str
    node_id: str
    case_dir: Path
    case_marker_path: Path
    node_complete_path: Path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preflight-root",
        type=Path,
        default=DEFAULT_PREFLIGHT_ROOT,
    )
    parser.add_argument("--bundle-root", type=Path, default=DEFAULT_BUNDLE_ROOT)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Audit every frozen local/downloaded binding without writing output roots.",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--max-cases",
        type=int,
        help="Optional safe batch limit; a partial batch exits 3 and resumes with --resume.",
    )
    return parser


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must contain a JSON object: {path}")
    return value


def _safe_repo_path(repo_root: Path, relative_value: object, label: str) -> Path:
    if not isinstance(relative_value, str) or not relative_value:
        raise RuntimeError(f"{label} must be a non-empty relative path")
    relative = Path(relative_value)
    if relative.is_absolute():
        raise RuntimeError(f"{label} must be relative to the Generator repository")
    root = repo_root.resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"{label} escapes the Generator repository") from exc
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    return path


def _validate_frozen_source_files(
    repo_root: Path,
    source_binding: Mapping[str, object],
) -> dict[str, object]:
    """Verify every preflight-bound generation file while allowing new finalizer files."""

    raw_files = source_binding.get("source_files")
    if not isinstance(raw_files, list) or not raw_files:
        raise RuntimeError("frozen Generator source binding has no source files")
    root = Path(repo_root).resolve()
    drifted: list[str] = []
    observed: list[dict[str, object]] = []
    for raw in raw_files:
        if not isinstance(raw, Mapping):
            raise RuntimeError("frozen Generator source file record is invalid")
        relative_value = raw.get("path")
        if not isinstance(relative_value, str) or not relative_value:
            raise RuntimeError("frozen Generator source path is invalid")
        relative = Path(relative_value)
        if relative.is_absolute():
            raise RuntimeError(f"frozen Generator source path is absolute: {relative}")
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise RuntimeError(
                f"frozen Generator source path escapes repository: {relative}"
            ) from exc
        if not path.is_file():
            drifted.append(f"{relative.as_posix()}:missing")
            continue
        record = {
            "path": relative.as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        observed.append(record)
        if (
            record["size_bytes"] != raw.get("size_bytes")
            or record["sha256"] != raw.get("sha256")
        ):
            drifted.append(relative.as_posix())
    if drifted:
        raise RuntimeError(f"frozen generation source drift: {sorted(drifted)}")
    observed.sort(key=lambda item: str(item["path"]))
    expected_manifest = source_binding.get("source_manifest_sha256")
    if expected_manifest is not None and canonical_json_sha256(observed) != expected_manifest:
        raise RuntimeError("frozen generation source manifest hash mismatch")
    return {
        "status": "pass",
        "file_count": len(observed),
        "source_manifest_sha256": canonical_json_sha256(observed),
        "frozen_git_commit": str(source_binding.get("git_commit", "")),
    }


def _conda_record_binding(prefix: Path) -> dict[str, object]:
    metadata_root = prefix / "conda-meta"
    if not metadata_root.is_dir():
        raise RuntimeError(f"active Python prefix is not a Conda environment: {prefix}")
    records: list[dict[str, object]] = []
    for path in sorted(metadata_root.glob("*.json"), key=lambda item: item.name.casefold()):
        value = _read_object(path, "Conda package record")
        records.append(
            {
                "name": str(value.get("name", "")),
                "version": str(value.get("version", "")),
                "build": str(value.get("build", "")),
                "build_number": int(value.get("build_number", 0)),
                "subdir": str(value.get("subdir", "")),
            }
        )
    history = metadata_root / "history"
    return {
        "resolved_prefix": str(prefix),
        "records_sha256": canonical_json_sha256(records),
        "history_sha256": sha256_file(history) if history.is_file() else None,
    }


def _validate_python_runtime(
    frozen_runtime: Mapping[str, object],
) -> dict[str, object]:
    """Require the exact deterministic runtime and report unrelated pip drift."""

    observed = capture_python_runtime()
    frozen_python = frozen_runtime.get("python")
    observed_python = observed.get("python")
    if not isinstance(frozen_python, Mapping) or not isinstance(
        observed_python, Mapping
    ):
        raise RuntimeError("preflight Python runtime binding is malformed")
    frozen_modules = frozen_runtime.get("critical_modules")
    observed_modules = observed.get("critical_modules")
    if not isinstance(frozen_modules, list) or not isinstance(observed_modules, list):
        raise RuntimeError("preflight critical-module binding is malformed")
    frozen_distributions = frozen_runtime.get("python_distributions")
    observed_distributions = observed.get("python_distributions")
    if not isinstance(frozen_distributions, list) or not isinstance(
        observed_distributions, list
    ):
        raise RuntimeError("preflight Python distribution binding is malformed")
    frozen_by_name = {
        str(item.get("name")): item
        for item in frozen_modules
        if isinstance(item, Mapping)
    }
    observed_by_name = {
        str(item.get("name")): item
        for item in observed_modules
        if isinstance(item, Mapping)
    }
    drifted: list[str] = []
    for field in ("executable", "executable_sha256", "version", "prefix"):
        if observed_python.get(field) != frozen_python.get(field):
            drifted.append(f"python.{field}")
    if set(frozen_by_name) != set(observed_by_name):
        drifted.append("critical_modules.names")
    else:
        for name in sorted(frozen_by_name):
            for field in ("version", "module_file_sha256"):
                if observed_by_name[name].get(field) != frozen_by_name[name].get(
                    field
                ):
                    drifted.append(f"critical_modules.{name}.{field}")
    prefix = Path(sys.prefix).resolve()
    conda = _conda_record_binding(prefix)
    frozen_conda = frozen_runtime.get("conda")
    if not isinstance(frozen_conda, Mapping):
        drifted.append("conda")
    else:
        for field in ("resolved_prefix", "records_sha256", "history_sha256"):
            if conda.get(field) != frozen_conda.get(field):
                drifted.append(f"conda.{field}")
    if drifted:
        raise RuntimeError(f"preflight Python runtime drift: {sorted(set(drifted))}")
    def versions_by_name(
        values: list[object],
    ) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for item in values:
            if not isinstance(item, Mapping):
                raise RuntimeError("Python distribution record is invalid")
            name = str(item.get("name", ""))
            version = str(item.get("version", ""))
            if not name or not version:
                raise RuntimeError("Python distribution record is incomplete")
            result.setdefault(name, []).append(version)
        return {name: sorted(versions) for name, versions in result.items()}

    def compact(versions: list[str]) -> str | list[str]:
        return versions[0] if len(versions) == 1 else versions

    frozen_distribution_map = versions_by_name(frozen_distributions)
    observed_distribution_map = versions_by_name(observed_distributions)
    distribution_drift = {
        "exact_match": observed.get("python_distributions_sha256")
        == frozen_runtime.get("python_distributions_sha256"),
        "frozen_sha256": frozen_runtime.get("python_distributions_sha256"),
        "observed_sha256": observed.get("python_distributions_sha256"),
        "added": {
            name: compact(observed_distribution_map[name])
            for name in sorted(
                observed_distribution_map.keys() - frozen_distribution_map.keys()
            )
        },
        "removed": {
            name: compact(frozen_distribution_map[name])
            for name in sorted(
                frozen_distribution_map.keys() - observed_distribution_map.keys()
            )
        },
        "changed": {
            name: {
                "frozen": frozen_distribution_map[name],
                "observed": observed_distribution_map[name],
            }
            for name in sorted(
                frozen_distribution_map.keys() & observed_distribution_map.keys()
            )
            if frozen_distribution_map[name] != observed_distribution_map[name]
        },
        "gate_semantics": (
            "informational_only_when_conda_records_and_critical_module_bytes_match"
        ),
    }
    return {
        "status": "pass",
        "python": {
            field: observed_python[field]
            for field in ("executable", "executable_sha256", "version", "prefix")
        },
        "critical_modules": [
            {
                field: observed_by_name[name][field]
                for field in ("name", "version", "module_file_sha256")
            }
            for name in sorted(observed_by_name)
        ],
        "python_distributions_sha256": observed["python_distributions_sha256"],
        "noncritical_distribution_drift": distribution_drift,
        "conda": conda,
    }


def _validate_split_plan(path: Path) -> dict[str, Any]:
    value = _read_object(path, "split plan")
    if value.get("schema_version") != "pars_split_plan_v2":
        raise RuntimeError("SPLIT_PLAN.json schema mismatch")
    content = {key: item for key, item in value.items() if key != "sha256"}
    if set(value) != set(content) | {"sha256"}:
        raise RuntimeError("SPLIT_PLAN.json has unexpected fields")
    if value.get("sha256") != sha256_json(content):
        raise RuntimeError("SPLIT_PLAN.json SHA-256 mismatch")
    return value


def _validate_configs(
    *,
    repo_root: Path,
    bundle_root: Path,
    preflight: Mapping[str, object],
    task_plan: Mapping[str, object],
) -> dict[str, Path]:
    config_path = bundle_root / "config" / "task12f_linux50_v2.json"
    config = _read_object(config_path, "Task 12F config")
    if sha256_file(config_path) != preflight.get("config_sha256"):
        raise RuntimeError("Task 12F config differs from local preflight")
    paths = config.get("paths")
    if not isinstance(paths, Mapping):
        raise RuntimeError("Task 12F config paths are missing")
    resolved = {
        "config_path": config_path,
        "profile_path": _safe_repo_path(
            repo_root, paths.get("profile"), "population profile"
        ),
        "scanner_path": _safe_repo_path(
            repo_root, paths.get("scanner"), "scanner config"
        ),
        "evidence_registry_path": _safe_repo_path(
            repo_root, paths.get("evidence_registry"), "evidence registry"
        ),
        "smc_path": bundle_root / str(task_plan["runtime"]["smc_relative_path"]),
        "simind_ini_path": bundle_root
        / str(task_plan["runtime"]["simind_ini_relative_path"]),
        "task12e_acceptance_path": bundle_root
        / "evidence"
        / "task12e_manual_acceptance.json",
    }
    expected_hashes = {
        "profile_path": preflight.get("profile_sha256"),
        "scanner_path": preflight.get("scanner_sha256"),
        "evidence_registry_path": preflight.get("evidence_registry_sha256"),
        "smc_path": task_plan["runtime"]["smc_sha256"],
        "simind_ini_path": task_plan["runtime"]["simind_ini_sha256"],
        "task12e_acceptance_path": task_plan["task12e_acceptance_sha256"],
    }
    drifted = []
    for name, path in resolved.items():
        if name == "config_path":
            continue
        if not path.is_file() or sha256_file(path) != expected_hashes[name]:
            drifted.append(name)
    if drifted:
        raise RuntimeError(f"Task 12G config/runtime bytes drifted: {drifted}")
    return resolved


def _validate_frozen_plans(
    *,
    preflight_root: Path,
    bundle_root: Path,
    preflight: Mapping[str, object],
    task_plan: Mapping[str, object],
    config: Mapping[str, object],
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    list[dict[str, Any]],
    dict[str, Mapping[str, object]],
]:
    generation_path = preflight_root / "GENERATION_PLAN.json"
    split_path = preflight_root / "SPLIT_PLAN.json"
    generation = load_generation_plan(preflight_root)
    split = _validate_split_plan(split_path)
    for name, local, bundled in (
        ("generation", generation_path, bundle_root / "plans" / "GENERATION_PLAN.json"),
        ("split", split_path, bundle_root / "plans" / "SPLIT_PLAN.json"),
    ):
        if not bundled.is_file() or local.read_bytes() != bundled.read_bytes():
            raise RuntimeError(f"local and uploaded {name} plans differ")
    if (
        generation.get("sha256") != preflight.get("generation_plan_sha256")
        or generation.get("sha256") != task_plan.get("generation_plan_sha256")
    ):
        raise RuntimeError("generation-plan binding mismatch")
    if (
        split.get("sha256") != preflight.get("split_plan_sha256")
        or split.get("sha256") != task_plan.get("split_plan_sha256")
        or generation.get("split_plan_sha256") != split.get("sha256")
    ):
        raise RuntimeError("split-plan binding mismatch")
    dataset = task_plan.get("dataset")
    config_dataset = config.get("dataset")
    if not isinstance(dataset, Mapping) or dataset != config_dataset:
        raise RuntimeError("Task 12F dataset config/plan mismatch")
    expected_identity = {
        "dataset_id": dataset["dataset_id"],
        "dataset_version": dataset["dataset_version"],
        "dataset_role": dataset["dataset_role"],
        "case_count": EXPECTED_CASE_COUNT,
        "family_size": 1,
        "global_seed": dataset["global_seed"],
    }
    if any(generation.get(key) != value for key, value in expected_identity.items()):
        raise RuntimeError("generation plan dataset identity mismatch")
    entries_raw = generation.get("entries")
    task_cases_raw = task_plan.get("cases")
    preflight_cases_raw = preflight.get("cases")
    if (
        not isinstance(entries_raw, list)
        or not isinstance(task_cases_raw, list)
        or not isinstance(preflight_cases_raw, list)
        or len(entries_raw) != EXPECTED_CASE_COUNT
        or len(task_cases_raw) != EXPECTED_CASE_COUNT
        or len(preflight_cases_raw) != EXPECTED_CASE_COUNT
    ):
        raise RuntimeError("Task 12G requires the exact frozen 50 cases")
    entries = [dict(item) for item in entries_raw if isinstance(item, Mapping)]
    task_cases = {
        str(item["case_id"]): item
        for item in task_cases_raw
        if isinstance(item, Mapping)
    }
    summaries = {
        str(item["case_id"]): item
        for item in preflight_cases_raw
        if isinstance(item, Mapping)
    }
    expected_ids = [f"case_{index:05d}" for index in range(EXPECTED_CASE_COUNT)]
    observed_ids = [str(item.get("case_id")) for item in entries]
    if (
        len(entries) != EXPECTED_CASE_COUNT
        or observed_ids != expected_ids
        or set(task_cases) != set(expected_ids)
        or set(summaries) != set(expected_ids)
    ):
        raise RuntimeError("Task 12G case identities/order mismatch")
    for entry in entries:
        case_id = str(entry["case_id"])
        task_case = task_cases[case_id]
        summary = summaries[case_id]
        task_inputs = task_case.get("inputs")
        if not isinstance(task_inputs, Mapping):
            raise RuntimeError(f"{case_id}: Task 12F input binding is missing")
        comparisons = {
            "case_family_id": entry["case_family_id"],
            "split": entry["split"],
        }
        if any(task_case.get(key) != value for key, value in comparisons.items()):
            raise RuntimeError(f"{case_id}: Task 12F execution-plan mismatch")
        for key in (
            "case_family_id",
            "split",
            "population_weight",
            "sampling_probability",
            "mismatch_challenge",
        ):
            if summary.get(key) != entry.get(key):
                raise RuntimeError(f"{case_id}: preflight/generation-plan {key} mismatch")
        if int(summary.get("rr_seed", -1)) != int(task_case.get("rr_seed", -2)):
            raise RuntimeError(f"{case_id}: preflight/execution-plan /RR mismatch")
        case_preflight = preflight_root / "cases" / case_id / "CASE_PREFLIGHT.json"
        if (
            task_inputs.get("source_sha256") != summary.get("source_sha256")
            or task_inputs.get("density_sha256") != summary.get("density_sha256")
            or task_inputs.get("array_manifest_sha256")
            != sha256_json(summary["array_manifest"])
            or not case_preflight.is_file()
            or task_inputs.get("case_preflight_sha256")
            != sha256_file(case_preflight)
        ):
            raise RuntimeError(f"{case_id}: uploaded/local preflight input binding mismatch")
    challenges = [item for item in entries if item.get("mismatch_challenge") is True]
    population = [item for item in entries if item.get("mismatch_challenge") is False]
    if [str(item["case_id"]) for item in challenges] != [
        "case_00000",
        "case_00001",
        "case_00002",
    ]:
        raise RuntimeError("Task 12G mismatch challenge identities drifted")
    if Counter(str(item["split"]) for item in entries) != {
        "train": 40,
        "val": 5,
        "test": 5,
    }:
        raise RuntimeError("Task 12G split counts must remain 40/5/5")
    if any(
        float(item["population_weight"]) != 0.0
        or float(item["sampling_probability"]) != 0.0
        for item in challenges
    ):
        raise RuntimeError("Task 12G challenges must remain zero-weight coverage cases")
    if not math.isclose(
        sum(float(item["sampling_probability"]) for item in population),
        1.0,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise RuntimeError("Task 12G population sampling probabilities do not sum to 1")
    return generation, split, entries, summaries


def _validate_downloaded_results(
    *,
    results_root: Path,
    task_plan: Mapping[str, object],
    bundle_manifest_sha256: str,
) -> tuple[dict[str, DownloadedCaseV2], Mapping[str, object]]:
    root = results_root.resolve()
    remote_preflight = read_json(root / "REMOTE_PREFLIGHT.json")
    if (
        remote_preflight.get("schema_version") != REMOTE_PREFLIGHT_SCHEMA
        or remote_preflight.get("status") != "pass"
        or remote_preflight.get("bundle_manifest_sha256")
        != bundle_manifest_sha256
    ):
        raise RuntimeError("downloaded REMOTE_PREFLIGHT.json is invalid")
    master_path = root / "TASK12F_LINUX50_MASTER.json"
    master = read_json(master_path)
    if (
        master.get("schema_version") != MASTER_SCHEMA
        or master.get("status") != "pass"
        or master.get("bundle_manifest_sha256") != bundle_manifest_sha256
        or int(master.get("case_count", -1)) != EXPECTED_CASE_COUNT
        or master.get("dataset") != task_plan.get("dataset")
    ):
        raise RuntimeError("downloaded Task 12F master is invalid")
    expected_simind_sha = str(task_plan["linux_runtime"]["simind_sha256"])
    downloaded: dict[str, DownloadedCaseV2] = {}
    results: list[Mapping[str, object]] = []
    node_counts: dict[str, int] = {}
    runtime_fingerprints: dict[str, Mapping[str, object]] = {}
    for node_value in task_plan["expected_nodes"]:
        node_id = str(node_value)
        node_root = root / "nodes" / node_id
        failed = node_root / "NODE_FAILED.json"
        if failed.exists():
            raise RuntimeError(f"downloaded results retain NODE_FAILED.json: {failed}")
        node_complete_path = node_root / "NODE_COMPLETE.json"
        node_complete = read_json(node_complete_path)
        assigned = cases_for_node(task_plan, node_id)
        expected_ids = sorted(str(case["case_id"]) for case in assigned)
        if (
            node_complete.get("schema_version") != NODE_COMPLETE_SCHEMA
            or node_complete.get("status") != "complete"
            or node_complete.get("bundle_manifest_sha256")
            != bundle_manifest_sha256
            or node_complete.get("case_ids") != expected_ids
            or int(node_complete.get("case_count", -1)) != len(expected_ids)
        ):
            raise RuntimeError(f"downloaded node completion mismatch: {node_id}")
        expected_parallel = int(
            task_plan["execution"]["requested_parallel_by_node"][node_id]
        )
        if int(node_complete.get("max_parallel", -1)) != expected_parallel:
            raise RuntimeError(f"downloaded node parallelism mismatch: {node_id}")
        runtime = node_complete.get("runtime_fingerprint")
        if not isinstance(runtime, Mapping):
            raise RuntimeError(f"downloaded node runtime missing: {node_id}")
        runtime_fingerprints[node_id] = runtime
        node_counts[node_id] = len(expected_ids)
        for case in assigned:
            case_id = str(case["case_id"])
            if case_id in downloaded:
                raise RuntimeError(f"duplicate downloaded Task 12F case: {case_id}")
            case_dir = node_root / "cases" / case_id
            result = _validate_case(
                case_dir=case_dir,
                case=case,
                node_id=node_id,
                bundle_sha=bundle_manifest_sha256,
                expected_simind_sha=expected_simind_sha,
            )
            for suffix, expected_sha in (
                ("act_av.bin", case["inputs"]["source_sha256"]),
                ("atn_av.bin", case["inputs"]["density_sha256"]),
            ):
                path = case_dir / f"{case_id}_{suffix}"
                if not path.is_file() or sha256_file(path) != expected_sha:
                    raise RuntimeError(
                        f"{case_id}: downloaded retained input hash mismatch: {suffix}"
                    )
            downloaded[case_id] = DownloadedCaseV2(
                case_id=case_id,
                node_id=node_id,
                case_dir=case_dir,
                case_marker_path=case_dir / "TASK12F_CASE.json",
                node_complete_path=node_complete_path,
            )
            results.append(result)
    results.sort(key=lambda item: str(item["case_id"]))
    expected_ids = {f"case_{index:05d}" for index in range(EXPECTED_CASE_COUNT)}
    if set(downloaded) != expected_ids:
        raise RuntimeError("downloaded result set is not the exact frozen 50 cases")
    if master.get("node_case_counts") != node_counts:
        raise RuntimeError("downloaded master node counts mismatch")
    if master.get("runtime_fingerprints") != runtime_fingerprints:
        raise RuntimeError("downloaded master runtime fingerprints mismatch")
    if master.get("cases") != results:
        raise RuntimeError("downloaded master case audit records mismatch")
    expected_projection_summary = {
        "minimum": min(float(item["projection_sum"]) for item in results),
        "maximum": max(float(item["projection_sum"]) for item in results),
        "mean": sum(float(item["projection_sum"]) for item in results)
        / EXPECTED_CASE_COUNT,
    }
    if master.get("projection_sum_summary") != expected_projection_summary:
        raise RuntimeError("downloaded master projection summary mismatch")
    if master.get("go_for_local_case_writer_and_dataset_freeze") is not False:
        raise RuntimeError("downloaded master local-freeze authority field is invalid")
    return downloaded, master


def _completed_downloaded_result(case_dir: Path, case_id: str) -> SimindRunResult:
    """Wrap already downloaded bytes as a completed result without execution."""

    final_dir = Path(case_dir).resolve()
    provenance = _read_object(
        final_dir / "run_provenance.json", f"{case_id} Linux SIMIND provenance"
    )
    if (
        provenance.get("schema_version") != "pars_simind_run_v2"
        or provenance.get("status") != "complete"
        or provenance.get("case_id") != case_id
        or provenance.get("exit_code") != 0
        or provenance.get("expected_shape") != [60, 128, 128]
    ):
        raise RuntimeError(f"{case_id}: completed Linux SIMIND provenance is required")
    completion = provenance.get("completion_audit")
    hashes = completion.get("sha256") if isinstance(completion, Mapping) else None
    if not isinstance(hashes, Mapping):
        raise RuntimeError(f"{case_id}: Linux SIMIND completion hashes are missing")
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


def _classify_roots(
    output_root: Path,
    work_root: Path,
    *,
    resume: bool,
) -> str:
    output_exists = output_root.exists()
    work_exists = work_root.exists()
    if output_exists != work_exists:
        raise RuntimeError("Task 12G output/work roots are inconsistent; audit required")
    if not output_exists:
        if resume:
            raise RuntimeError("--resume requires existing Task 12G roots")
        return "fresh"
    if not resume:
        raise FileExistsError("Task 12G roots already exist; use --resume after audit")
    return "resume"


def _next_attempt_dir(work_root: Path, case_id: str) -> Path:
    parent = work_root / "inputs" / case_id
    if not parent.exists():
        return parent / "attempt_001"
    indices = []
    for path in parent.iterdir():
        if not path.is_dir() or not path.name.startswith("attempt_"):
            continue
        try:
            indices.append(int(path.name.removeprefix("attempt_")))
        except ValueError:
            continue
    return parent / f"attempt_{max(indices, default=0) + 1:03d}"


def _copy_immutable(source: Path, destination: Path) -> None:
    source_bytes = source.read_bytes()
    if destination.exists():
        if not destination.is_file() or destination.read_bytes() != source_bytes:
            raise RuntimeError(f"immutable output file drift: {destination}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(source_bytes)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _load_records(
    output_root: Path,
    expected_case_ids: Sequence[str],
) -> list[object]:
    cases_root = output_root / "cases"
    if not cases_root.exists():
        return []
    expected = set(expected_case_ids)
    observed = {path.name for path in cases_root.iterdir() if path.is_dir()}
    unexpected = observed - expected
    if unexpected:
        raise RuntimeError(f"Task 12G contains unexpected cases: {sorted(unexpected)}")
    return [
        load_case_record_v2(
            cases_root / case_id / "case_record.json",
            dataset_root=output_root,
            verify_hashes=True,
        )
        for case_id in expected_case_ids
        if case_id in observed
    ]


def _git_head(repo_root: Path) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={repo_root.resolve().as_posix()}",
            "rev-parse",
            "HEAD",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _runtime_document(
    *,
    preflight_root: Path,
    bundle_root: Path,
    results_root: Path,
    output_root: Path,
    work_root: Path,
    preflight: Mapping[str, object],
    task_plan: Mapping[str, object],
    generation: Mapping[str, object],
    split: Mapping[str, object],
    frozen_source_audit: Mapping[str, object],
    python_runtime_audit: Mapping[str, object],
    master: Mapping[str, object],
) -> dict[str, object]:
    return {
        "schema_version": TASK12G_RUNTIME_SCHEMA,
        "status": "bound",
        "generator_repository": str(REPO_ROOT.resolve()),
        "finalizer_git_commit": _git_head(REPO_ROOT),
        "finalizer_sha256": sha256_file(Path(__file__)),
        "preflight_root": str(preflight_root),
        "bundle_root": str(bundle_root),
        "results_root": str(results_root),
        "output_root": str(output_root),
        "work_root": str(work_root),
        "task12f_config_sha256": preflight["config_sha256"],
        "task12f_preflight_sha256": sha256_file(preflight_root / "PREFLIGHT.json"),
        "task12f_input_bundle": dict(preflight["input_bundle"]),
        "task12f_bundle_manifest_sha256": sha256_file(
            bundle_root / "BUNDLE_MANIFEST.json"
        ),
        "task12f_execution_plan_sha256": sha256_file(
            bundle_root / "TASK12F_PLAN.json"
        ),
        "task12f_master_sha256": sha256_file(
            results_root / "TASK12F_LINUX50_MASTER.json"
        ),
        "generation_plan_sha256": generation["sha256"],
        "split_plan_sha256": split["sha256"],
        "dataset": dict(task_plan["dataset"]),
        "linux_runtime": dict(task_plan["linux_runtime"]),
        "frozen_generator_source": dict(frozen_source_audit),
        "local_python_runtime": dict(python_runtime_audit),
        "remote_runtime_fingerprints": dict(master["runtime_fingerprints"]),
        "projection_coordinate_contract_id": PROJECTION_COORDINATE_CONTRACT_ID,
        "loader_transform_id": FROZEN_LOADER_TRANSFORM_ID,
        "projection_scale_policy": "retain_absolute_linux_projection_scale",
        "simind_execution": "forbidden_use_downloaded_linux_outputs_only",
        "preflight_to_writer_contract": {
            "comparison": "source_density_size_sha256_and_all_array_semantic_bytes",
            "mismatch_action": "fail_before_case_write",
        },
        "resume_contract": {
            "completed_case_action": "verify_hashes_and_skip",
            "failed_attempt_action": "retain_and_allocate_new_attempt",
            "runtime_drift_action": "forbid_resume",
            "progress_schema": TASK12G_PROGRESS_SCHEMA,
        },
        "go_for_500_case_generation": False,
    }


def _load_or_write_runtime(
    output_root: Path,
    work_root: Path,
    runtime: Mapping[str, object],
    state: str,
) -> Path:
    def stable_binding(document: Mapping[str, object]) -> dict[str, object]:
        stable = json.loads(json.dumps(document))
        local_runtime = stable.get("local_python_runtime")
        if isinstance(local_runtime, dict):
            local_runtime.pop("python_distributions_sha256", None)
            local_runtime.pop("noncritical_distribution_drift", None)
        return stable

    path = output_root / "PILOT_RUNTIME.json"
    if state == "fresh":
        atomic_write_json(path, dict(runtime))
        return path
    existing = _read_object(path, "Task 12G runtime")
    expected = dict(runtime)
    if existing == expected:
        return path
    existing_bound = stable_binding(existing)
    expected_bound = stable_binding(expected)
    if existing_bound == expected_bound:
        return path
    cases_root = output_root / "cases"
    formal_cases = (
        [item for item in cases_root.iterdir() if item.is_dir()]
        if cases_root.is_dir()
        else []
    )
    progress_path = work_root / "PROGRESS.json"
    progress = (
        _read_object(progress_path, "Task 12G progress")
        if progress_path.is_file()
        else {}
    )
    allowed_refresh_fields = {"finalizer_git_commit", "finalizer_sha256"}
    existing_stable = {
        key: value
        for key, value in existing_bound.items()
        if key not in allowed_refresh_fields
    }
    expected_stable = {
        key: value
        for key, value in expected_bound.items()
        if key not in allowed_refresh_fields
    }
    completion_path = output_root / DATASET_COMPLETE_FILENAME
    frozen_dataset = False
    if completion_path.is_file():
        completion = _read_object(completion_path, "Task 12G dataset completion marker")
        frozen_dataset = completion.get("status") == "complete"
    if frozen_dataset and existing_stable == expected_stable:
        return path
    zero_case_failed_recovery = (
        not formal_cases
        and not completion_path.exists()
        and progress.get("schema_version") == TASK12G_PROGRESS_SCHEMA
        and progress.get("status") == "failed"
        and progress.get("completed_case_ids") == []
        and int(progress.get("completed_count", -1)) == 0
        and existing_stable == expected_stable
    )
    if not zero_case_failed_recovery:
        raise RuntimeError("Task 12G runtime binding changed; resume is forbidden")
    atomic_write_json(path, expected)
    return path


def _progress(
    work_root: Path,
    *,
    status: str,
    records: Sequence[object],
    summaries: Sequence[Mapping[str, object]],
    current_case_id: str | None = None,
    error: str | None = None,
    dataset_complete: object | None = None,
) -> None:
    document: dict[str, object] = {
        "schema_version": TASK12G_PROGRESS_SCHEMA,
        "status": status,
        "completed_case_ids": [record.case_id for record in records],
        "completed_count": len(records),
        "total_count": EXPECTED_CASE_COUNT,
        "remaining_count": EXPECTED_CASE_COUNT - len(records),
        "case_summaries": list(summaries),
        "go_for_500_case_generation": False,
    }
    if current_case_id is not None:
        document["current_case_id"] = current_case_id
    if error is not None:
        document["error"] = error
    if dataset_complete is not None:
        document["dataset_complete"] = dataset_complete
    atomic_write_json(work_root / "PROGRESS.json", document)


def _bound_summary(
    prepared: object,
    entry: Mapping[str, object],
) -> dict[str, object]:
    summary = summarize_prepared_population_case(prepared)
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


def _semantic_summaries_equal(
    observed: Mapping[str, object],
    frozen: Mapping[str, object],
) -> bool:
    """Compare the JSON contract, where tuples and arrays have the same meaning."""

    return sha256_json(observed) == sha256_json(frozen)


def _case_artifacts(
    *,
    prepared: object,
    result: SimindRunResult,
    byte_identity_path: Path,
    downloaded: DownloadedCaseV2,
    preflight_root: Path,
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
        raise RuntimeError(f"{downloaded.case_id}: SIMIND snapshots are missing")
    artifacts = simind_extra_artifacts(prepared, result)
    artifacts.update(
        {
            "pilot_plan": paths["config_path"],
            "pilot_runtime": runtime_path,
            "pilot_preflight": preflight_root / "PREFLIGHT.json",
            "pilot_input_bundle": preflight_root / "INPUT_BUNDLE.json",
            "preflight_byte_identity": byte_identity_path,
            "generation_plan": preflight_root / "GENERATION_PLAN.json",
            "split_plan": preflight_root / "SPLIT_PLAN.json",
            "task12f_bundle_manifest": bundle_root / "BUNDLE_MANIFEST.json",
            "task12f_execution_plan": bundle_root / "TASK12F_PLAN.json",
            "task12f_case_preflight": preflight_root
            / "cases"
            / downloaded.case_id
            / "CASE_PREFLIGHT.json",
            "task12f_remote_preflight": results_root / "REMOTE_PREFLIGHT.json",
            "task12f_node_complete": downloaded.node_complete_path,
            "task12f_case_marker": downloaded.case_marker_path,
            "task12f_master": results_root / "TASK12F_LINUX50_MASTER.json",
            "population_profile": paths["profile_path"],
            "scanner_config": paths["scanner_path"],
            "evidence_registry": paths["evidence_registry_path"],
            "task12e_acceptance": paths["task12e_acceptance_path"],
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
        raise RuntimeError(
            f"{downloaded.case_id}: Task 12G artifact set is not exact"
        )
    return artifacts


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.max_cases is not None and args.max_cases <= 0:
        raise ValueError("--max-cases must be a positive integer")
    preflight_root = args.preflight_root.resolve()
    bundle_root = args.bundle_root.resolve()
    results_root = args.results_root.resolve()
    output_root = args.output_root.resolve()
    work_root = args.work_root.resolve()

    validate_bundle(bundle_root)
    task_plan = load_plan(bundle_root)
    preflight = _read_object(preflight_root / "PREFLIGHT.json", "Task 12F preflight")
    if (
        preflight.get("schema_version") != "pars_v2_task12f_linux50_preflight_v2"
        or preflight.get("status") != "pass"
        or int(preflight.get("case_count", -1)) != EXPECTED_CASE_COUNT
        or preflight.get("simind_launched") is not False
        or preflight.get("formal_runner_eligible") is not True
    ):
        raise RuntimeError("passing local Task 12F v2 preflight is required")
    source_binding = preflight.get("generator_source")
    frozen_python = preflight.get("python_runtime")
    if not isinstance(source_binding, Mapping) or not isinstance(
        frozen_python, Mapping
    ):
        raise RuntimeError("Task 12F preflight source/runtime bindings are missing")
    task_source = task_plan.get("source_binding")
    if (
        not isinstance(task_source, Mapping)
        or task_source.get("generator_git_commit")
        != preflight.get("generator_git_commit")
        or task_source.get("generator_git_commit")
        != source_binding.get("git_commit")
        or task_source.get("generator_source_binding_sha256")
        != source_binding.get("binding_sha256")
        or task_source.get("generator_worktree_clean") is not True
        or task_plan.get("preflight_sha256")
        != sha256_file(preflight_root / "PREFLIGHT.json")
    ):
        raise RuntimeError("uploaded Task 12F plan/preflight/source binding mismatch")
    frozen_source_audit = _validate_frozen_source_files(REPO_ROOT, source_binding)
    python_runtime_audit = _validate_python_runtime(frozen_python)
    paths = _validate_configs(
        repo_root=REPO_ROOT,
        bundle_root=bundle_root,
        preflight=preflight,
        task_plan=task_plan,
    )
    config = _read_object(paths["config_path"], "Task 12F config")
    generation, split, entries, preflight_summaries = _validate_frozen_plans(
        preflight_root=preflight_root,
        bundle_root=bundle_root,
        preflight=preflight,
        task_plan=task_plan,
        config=config,
    )
    expected_ids = [str(entry["case_id"]) for entry in entries]
    input_reference = preflight.get("input_bundle")
    if not isinstance(input_reference, Mapping):
        raise RuntimeError("Task 12F preflight input bundle binding is missing")
    preflight_inputs = load_and_validate_preflight_input_bundle(
        preflight_root / "PREFLIGHT.json",
        input_reference,
        expected_case_ids=expected_ids,
        case_summaries=list(preflight_summaries.values()),
    )
    bundle_sha = sha256_file(bundle_root / "BUNDLE_MANIFEST.json")
    downloaded, master = _validate_downloaded_results(
        results_root=results_root,
        task_plan=task_plan,
        bundle_manifest_sha256=bundle_sha,
    )
    if args.validate_only:
        print(
            json.dumps(
                {
                    "status": "pass",
                    "mode": "validate_only",
                    "case_count": len(downloaded),
                    "split_counts": dict(
                        Counter(str(entry["split"]) for entry in entries)
                    ),
                    "bundle_manifest_sha256": bundle_sha,
                    "task12f_master_sha256": sha256_file(
                        results_root / "TASK12F_LINUX50_MASTER.json"
                    ),
                    "generation_plan_sha256": generation["sha256"],
                    "split_plan_sha256": split["sha256"],
                    "frozen_generator_source": frozen_source_audit,
                    "local_python_runtime": python_runtime_audit,
                    "go_for_500_case_generation": False,
                },
                ensure_ascii=False,
            )
        )
        return 0

    state = _classify_roots(output_root, work_root, resume=args.resume)
    if state == "fresh":
        output_root.mkdir(parents=True, exist_ok=False)
        work_root.mkdir(parents=True, exist_ok=False)
    _copy_immutable(
        preflight_root / "GENERATION_PLAN.json",
        output_root / "GENERATION_PLAN.json",
    )
    _copy_immutable(
        preflight_root / "SPLIT_PLAN.json",
        output_root / "SPLIT_PLAN.json",
    )
    runtime_document = _runtime_document(
        preflight_root=preflight_root,
        bundle_root=bundle_root,
        results_root=results_root,
        output_root=output_root,
        work_root=work_root,
        preflight=preflight,
        task_plan=task_plan,
        generation=generation,
        split=split,
        frozen_source_audit=frozen_source_audit,
        python_runtime_audit=python_runtime_audit,
        master=master,
    )
    runtime_path = _load_or_write_runtime(
        output_root,
        work_root,
        runtime_document,
        state,
    )
    runtime_document = _read_object(runtime_path, "Task 12G bound runtime")

    registry = load_evidence_registry(paths["evidence_registry_path"])
    profile = load_profile(paths["profile_path"], registry)
    scanner = load_profile(paths["scanner_path"], registry)
    if profile.profile_id != generation["profile_id"]:
        raise RuntimeError("Task 12G population profile ID mismatch")
    grid = GridSpecV2(
        shape=tuple(int(value) for value in scanner.value("matrix")),
        voxel_size_mm=float(scanner.value("voxel_size_mm")),
    )
    records = _load_records(output_root, expected_ids)
    records.sort(key=lambda item: item.case_id)
    summaries = [_summary_from_record(record, output_root) for record in records]
    contract = DatasetContractV2(
        output_root=output_root,
        dataset_id=str(generation["dataset_id"]),
        dataset_version=str(generation["dataset_version"]),
        dataset_role=str(generation["dataset_role"]),
        expected_case_ids=tuple(expected_ids),
        allowed_profile_ids=(profile.profile_id,),
        split_plan_sha256=str(split["sha256"]),
        required_artifact_names=REQUIRED_ARTIFACTS,
    )
    if (output_root / DATASET_COMPLETE_FILENAME).exists():
        frozen = freeze_dataset(records, contract)
        _progress(
            work_root,
            status="complete",
            records=records,
            summaries=summaries,
            dataset_complete=frozen.to_dict(),
        )
        print(
            json.dumps(
                {
                    "status": "already_complete",
                    "case_count": len(records),
                    "manifest_sha256": frozen.manifest_sha256,
                },
                ensure_ascii=False,
            )
        )
        return 0

    completed_ids = {record.case_id for record in records}
    pending = [entry for entry in entries if str(entry["case_id"]) not in completed_ids]
    processed_this_run = 0
    base_histories = int(task_plan["execution"]["base_histories_per_projection"])
    task_cases = {
        str(item["case_id"]): item
        for item in task_plan["cases"]
        if isinstance(item, Mapping)
    }
    for entry in pending:
        if args.max_cases is not None and processed_this_run >= args.max_cases:
            _progress(
                work_root,
                status="paused",
                records=records,
                summaries=summaries,
            )
            print(
                json.dumps(
                    {"status": "paused", "completed_count": len(records)},
                    ensure_ascii=False,
                )
            )
            return 3
        case_id = str(entry["case_id"])
        _progress(
            work_root,
            status="running",
            records=records,
            summaries=summaries,
            current_case_id=case_id,
        )
        try:
            attempt_dir = _next_attempt_dir(work_root, case_id)
            prepared = prepare_population_case(
                case_id,
                profile,
                grid,
                global_seed=int(generation["global_seed"]),
                base_histories=base_histories,
                work_dir=attempt_dir,
                mismatch_challenge=bool(entry["mismatch_challenge"]),
            )
            regenerated_summary = _bound_summary(prepared, entry)
            if not _semantic_summaries_equal(
                regenerated_summary,
                preflight_summaries[case_id],
            ):
                raise RuntimeError(
                    f"{case_id}: regenerated semantic summary differs from frozen "
                    f"preflight; observed_sha256={sha256_json(regenerated_summary)}; "
                    f"frozen_sha256={sha256_json(preflight_summaries[case_id])}"
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
                raise RuntimeError(f"{case_id}: frozen /RR or /NN mismatch")
            downloaded_case = downloaded[case_id]
            result = _completed_downloaded_result(
                downloaded_case.case_dir,
                case_id,
            )
            metadata = build_completed_metadata(
                prepared,
                profile_path=paths["profile_path"],
                scanner_path=paths["scanner_path"],
                evidence_registry_path=paths["evidence_registry_path"],
                simind_ini_path=paths["simind_ini_path"],
                scanner=scanner,
                result=result,
                runtime_binding=runtime_document,
            )
            artifacts = _case_artifacts(
                prepared=prepared,
                result=result,
                byte_identity_path=byte_identity_path,
                downloaded=downloaded_case,
                preflight_root=preflight_root,
                bundle_root=bundle_root,
                results_root=results_root,
                runtime_path=runtime_path,
                paths=paths,
            )
            record = write_case_v2(
                CasePayloadV2(
                    case_id=case_id,
                    case_family_id=str(entry["case_family_id"]),
                    profile_id=profile.profile_id,
                    dataset_id=str(generation["dataset_id"]),
                    dataset_version=str(generation["dataset_version"]),
                    dataset_role=str(generation["dataset_role"]),
                    split=str(entry["split"]),
                    population_weight=float(entry["population_weight"]),
                    sampling_probability=float(entry["sampling_probability"]),
                    arrays=prepared.arrays,
                    metadata=metadata,
                    extra_artifacts=artifacts,
                ),
                output_root,
                resume=state == "resume",
            )
            records.append(record)
            records.sort(key=lambda item: item.case_id)
            summaries = [_summary_from_record(item, output_root) for item in records]
            processed_this_run += 1
            _progress(
                work_root,
                status="running",
                records=records,
                summaries=summaries,
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            _progress(
                work_root,
                status="failed",
                records=records,
                summaries=summaries,
                current_case_id=case_id,
                error=error,
            )
            print(
                json.dumps(
                    {"status": "failed", "case_id": case_id, "error": error},
                    ensure_ascii=False,
                )
            )
            return 1

    split_counts = dict(Counter(record.split for record in records))
    challenge_ids = [
        str(entry["case_id"])
        for entry in entries
        if entry["mismatch_challenge"] is True
    ]
    generation_gate = {
        "schema_version": TASK12G_GENERATION_GATE_SCHEMA,
        "status": "ready_for_dataset_freeze",
        "case_count": len(records),
        "split_counts": split_counts,
        "mismatch_challenge_case_ids": challenge_ids,
        "mismatch_challenge_semantics": (
            "zero_population_weight_coverage_challenges_not_prevalence"
        ),
        "required_artifacts": list(REQUIRED_ARTIFACTS),
        "projection_coordinate_contract_id": PROJECTION_COORDINATE_CONTRACT_ID,
        "loader_transform_id": FROZEN_LOADER_TRANSFORM_ID,
        "absolute_projection_scale_retained": True,
        "linux_only": True,
        "go_for_500_case_generation": False,
        "next_action": (
            "run Task 12G artifact/statistical/visual/projection gates and manual review"
        ),
    }
    atomic_write_json(
        output_root / "TASK12G_GENERATION_GATE.json",
        generation_gate,
    )
    frozen = freeze_dataset(records, contract)
    _progress(
        work_root,
        status="complete",
        records=records,
        summaries=summaries,
        dataset_complete=frozen.to_dict(),
    )
    print(
        json.dumps(
            {
                "status": "complete",
                "output_root": str(output_root),
                "work_root": str(work_root),
                "case_count": len(records),
                "split_counts": split_counts,
                "manifest_sha256": frozen.manifest_sha256,
                "go_for_500_case_generation": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
