"""Prepare, freeze and package all inputs for the 50-case Linux production pilot."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path
from typing import Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from generate_dataset_v2 import (  # noqa: E402
    build_generation_plan,
    write_generation_plan,
)
from task12f_linux50_common import (  # noqa: E402
    BUNDLE_SCHEMA,
    PLAN_SCHEMA,
    atomic_write_json,
    sha256_file,
    validate_bundle,
)
from core.case_writer_v2 import write_split_plan  # noqa: E402
from core.liver_geometry import GridSpecV2  # noqa: E402
from core.production_v2 import (  # noqa: E402
    prepare_negative_case,
    population_coverage,
    prepare_population_case,
    summarize_prepared_negative_case,
    summarize_prepared_population_case,
)
from core.provenance import sha256_json  # noqa: E402
from core.reproducibility_v2 import (  # noqa: E402
    capture_generator_source_binding,
    capture_python_runtime,
    write_preflight_input_bundle,
)
from core.schemas_v2 import load_evidence_registry, load_profile  # noqa: E402


DEFAULT_CONFIG = REPO_ROOT / "configs" / "task12f_linux50_v2.json"
DEFAULT_PREFLIGHT = Path(r"D:\PFE-U\PAR\outputs\task12f_linux50_preflight_v2")
DEFAULT_UPLOAD = Path(r"D:\PFE-U\PAR\outputs\task12f_linux50_upload_v2")
BUNDLE_NAME = "pars_v2_task12f_linux50_bundle_v2"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--preflight-root", type=Path, default=DEFAULT_PREFLIGHT)
    parser.add_argument("--upload-root", type=Path, default=DEFAULT_UPLOAD)
    parser.add_argument("--local-max-parallel", type=int)
    parser.add_argument("--resume", action="store_true")
    return parser


def _read_object(path: Path) -> Mapping[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must contain an object")
    return value


def _resolve(relative: object) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError("configured repository path must be a non-empty string")
    path = (REPO_ROOT / relative).resolve()
    try:
        path.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"configured path escapes repository: {relative}") from exc
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _git_commit_and_clean() -> str:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if status:
        raise RuntimeError("formal Task 12F bundle requires a clean Generator worktree")
    return commit


def _replace_directory_with_retry(
    source: Path,
    destination: Path,
    *,
    attempts: int = 8,
    delay_seconds: float = 0.25,
) -> None:
    """Publish a completed directory despite transient Windows file locks."""

    if attempts < 1 or delay_seconds < 0:
        raise ValueError("atomic directory retry settings are invalid")
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if destination.exists() or attempt + 1 >= attempts:
                raise
            time.sleep(delay_seconds * (2**attempt))
    raise AssertionError("atomic directory retry loop terminated unexpectedly")


def _apply_mismatch_challenge_design(
    generation_plan: Mapping[str, object],
    design: Mapping[str, object],
) -> dict[str, object]:
    """Freeze explicit zero-population-weight mismatch cases after split planning."""

    raw_counts = design.get("mismatch_cases_per_split")
    raw_entries = generation_plan.get("entries")
    if not isinstance(raw_counts, Mapping) or not isinstance(raw_entries, list):
        raise ValueError("mismatch challenge design is malformed")
    expected_splits = ("train", "val", "test")
    counts = {split: int(raw_counts.get(split, -1)) for split in expected_splits}
    if set(raw_counts) != set(expected_splits) or any(value < 0 for value in counts.values()):
        raise ValueError("mismatch challenge counts must cover train/val/test")

    selected: set[str] = set()
    for split in expected_splits:
        candidates = sorted(
            str(entry["case_id"])
            for entry in raw_entries
            if isinstance(entry, Mapping) and entry.get("split") == split
        )
        if len(candidates) < counts[split]:
            raise ValueError(f"not enough {split} cases for mismatch challenges")
        selected.update(candidates[: counts[split]])
    population_count = len(raw_entries) - len(selected)
    if population_count < 1:
        raise ValueError("mismatch design leaves no population-weighted cases")

    entries = []
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, Mapping):
            raise ValueError("generation plan entry must be an object")
        entry = dict(raw_entry)
        challenge = str(entry["case_id"]) in selected
        entry["mismatch_challenge"] = challenge
        entry["challenge_labels"] = ["perfusion_mismatch"] if challenge else []
        entry["population_weight"] = 0.0 if challenge else 1.0
        entry["sampling_probability"] = 0.0 if challenge else 1.0 / population_count
        entries.append(entry)

    content = {
        key: value for key, value in generation_plan.items() if key != "sha256"
    }
    content["entries"] = entries
    return {**content, "sha256": sha256_json(content)}


def _validate_case_dir(
    case_root: Path,
    case_id: str,
    *,
    expected_mismatch_challenge: bool,
) -> Mapping[str, object]:
    document = _read_object(case_root / "CASE_PREFLIGHT.json")
    if document.get("status") != "pass" or document.get("case_id") != case_id:
        raise ValueError(f"invalid completed preflight for {case_id}")
    if bool(document.get("mismatch_challenge")) != expected_mismatch_challenge:
        raise ValueError(f"{case_id} mismatch challenge policy drifted")
    for name, suffix, key in (
        ("source", "_act_av.bin", "source_sha256"),
        ("density", "_atn_av.bin", "density_sha256"),
    ):
        path = case_root / f"{case_id}{suffix}"
        if not path.is_file() or sha256_file(path) != document.get(key):
            raise ValueError(f"{case_id} {name} preflight bytes drifted")
    return document


def _prepare_case_job(
    *,
    case_id: str,
    entry: Mapping[str, object],
    generation_profile_path: str,
    dataset_role: str,
    registry_path: str,
    grid_shape: tuple[int, int, int],
    voxel_size_mm: float,
    global_seed: int,
    base_histories: int,
    staging_path: str,
    final_path: str,
) -> Mapping[str, object]:
    """Process-isolated case preparation used by the Windows preflight."""

    registry = load_evidence_registry(Path(registry_path))
    profile = load_profile(Path(generation_profile_path), registry)
    staging = Path(staging_path)
    grid = GridSpecV2(shape=grid_shape, voxel_size_mm=voxel_size_mm)
    if dataset_role == "negative":
        prepared = prepare_negative_case(
            case_id,
            profile,
            grid,
            global_seed=global_seed,
            base_histories=base_histories,
            work_dir=staging,
        )
        raw = summarize_prepared_negative_case(prepared)
    elif dataset_role == "main":
        prepared = prepare_population_case(
            case_id,
            profile,
            grid,
            global_seed=global_seed,
            base_histories=base_histories,
            work_dir=staging,
            mismatch_challenge=bool(entry["mismatch_challenge"]),
        )
        raw = summarize_prepared_population_case(prepared)
    else:
        raise ValueError(f"unsupported dataset role: {dataset_role}")
    summary = {
        **raw,
        "case_family_id": entry["case_family_id"],
        "split": entry["split"],
        "profile_id": entry["profile_id"],
        "population_weight": entry["population_weight"],
        "sampling_probability": entry["sampling_probability"],
    }
    if summary["status"] != "pass":
        raise RuntimeError(f"{case_id}: {summary['failures']}")
    expected_mismatch = bool(entry.get("mismatch_challenge", False))
    if bool(summary["mismatch_challenge"]) != expected_mismatch:
        raise RuntimeError(f"{case_id}: mismatch challenge policy was not realized")
    if expected_mismatch != (float(summary["population_weight"]) == 0.0):
        raise RuntimeError(f"{case_id}: mismatch challenge population weight is invalid")
    atomic_write_json(staging / "CASE_PREFLIGHT.json", summary)
    final = Path(final_path)
    final.parent.mkdir(parents=True, exist_ok=True)
    _replace_directory_with_retry(staging, final)
    return summary


def _prepare_preflight(
    config: Mapping[str, object],
    config_path: Path,
    preflight_root: Path,
    *,
    commit: str,
    resume: bool,
    local_max_parallel: int | None,
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    dataset = config["dataset"]
    paths = config["paths"]
    execution = config["execution"]
    if not all(isinstance(item, Mapping) for item in (dataset, paths, execution)):
        raise ValueError("Task 12F dataset/paths/execution records are malformed")
    release_key = str(paths.get("release_acceptance_key", "task12e_acceptance"))
    acceptance_path = _resolve(paths[release_key])
    expected_acceptance = config["frozen_evidence"].get(
        "release_acceptance_sha256",
        config["frozen_evidence"].get("task12e_acceptance_sha256"),
    )
    if sha256_file(acceptance_path) != expected_acceptance:
        raise RuntimeError("Task 12E acceptance hash changed")
    acceptance = _read_object(acceptance_path)
    release_flag = str(
        config["frozen_evidence"].get(
            "release_flag", "go_for_50_case_generation"
        )
    )
    if acceptance.get("release", {}).get(release_flag) is not True:
        raise RuntimeError(f"release evidence does not set {release_flag}=true")

    registry_path = _resolve(paths["evidence_registry"])
    profile_path = _resolve(paths["profile"])
    generation_profile_path = _resolve(paths.get("generation_profile", paths["profile"]))
    scanner_path = _resolve(paths["scanner"])
    registry = load_evidence_registry(registry_path)
    profile = load_profile(profile_path, registry)
    generation_profile = load_profile(generation_profile_path, registry)
    scanner = load_profile(scanner_path, registry)
    case_count = int(dataset["case_count"])
    split_plan, generation_plan = build_generation_plan(
        dataset_id=str(dataset["dataset_id"]),
        dataset_version=str(dataset["dataset_version"]),
        dataset_role=str(dataset["dataset_role"]),
        profile_id=profile.profile_id,
        case_count=case_count,
        family_size=int(dataset["family_size"]),
        global_seed=int(dataset["global_seed"]),
        ratios={
            key: float(value)
            for key, value in dataset["split_ratios"].items()
        },
    )
    dataset_role = str(dataset["dataset_role"])
    challenge_design = config.get("challenge_design")
    if dataset_role == "main":
        if not isinstance(challenge_design, Mapping):
            raise ValueError("main dataset challenge design is missing")
        generation_plan = _apply_mismatch_challenge_design(
            generation_plan,
            challenge_design,
        )
    elif dataset_role == "negative":
        entries = []
        for raw_entry in generation_plan["entries"]:
            entry = dict(raw_entry)
            entry.update(
                {
                    "mismatch_challenge": False,
                    "challenge_labels": [],
                    "population_weight": 0.0,
                }
            )
            entries.append(entry)
        content = {key: value for key, value in generation_plan.items() if key != "sha256"}
        content["entries"] = entries
        generation_plan = {**content, "sha256": sha256_json(content)}
    else:
        raise ValueError(f"unsupported dataset role: {dataset_role}")
    if preflight_root.exists() and not resume:
        raise FileExistsError(
            f"preflight root exists; use --resume: {preflight_root}"
        )
    preflight_root.mkdir(parents=True, exist_ok=True)
    write_split_plan(split_plan, preflight_root)
    write_generation_plan(generation_plan, preflight_root)
    grid = GridSpecV2(
        shape=tuple(int(value) for value in scanner.value("matrix")),
        voxel_size_mm=float(scanner.value("voxel_size_mm")),
    )
    progress_path = preflight_root / "PROGRESS.json"
    requested_local_parallel = int(
        execution["local_preflight_max_parallel"]
        if local_max_parallel is None
        else local_max_parallel
    )
    if not 1 <= requested_local_parallel <= 8:
        raise ValueError("local preflight parallelism must be within 1..8")
    summaries_by_id: dict[str, Mapping[str, object]] = {}
    pending_jobs: list[tuple[Mapping[str, object], Path, Path]] = []
    for entry in generation_plan["entries"]:
        case_id = str(entry["case_id"])
        case_root = preflight_root / "cases" / case_id
        if case_root.is_dir():
            summaries_by_id[case_id] = _validate_case_dir(
                case_root,
                case_id,
                expected_mismatch_challenge=bool(entry["mismatch_challenge"]),
            )
        else:
            staging = preflight_root / ".staging" / f"{case_id}.{uuid.uuid4().hex}"
            staging.parent.mkdir(parents=True, exist_ok=True)
            pending_jobs.append((entry, staging, case_root))

    def write_progress() -> None:
        atomic_write_json(
            progress_path,
            {
                "schema_version": "pars_v2_task12f_linux50_preflight_progress_v2",
                "status": "running",
                "completed_count": len(summaries_by_id),
                "total_count": case_count,
                "completed_case_ids": sorted(summaries_by_id),
                "local_max_parallel": requested_local_parallel,
            },
        )
    write_progress()
    if pending_jobs:
        with ProcessPoolExecutor(max_workers=requested_local_parallel) as executor:
            jobs = iter(pending_jobs)
            futures: dict[object, str] = {}

            def submit_next() -> bool:
                try:
                    entry, staging, case_root = next(jobs)
                except StopIteration:
                    return False
                future = executor.submit(
                    _prepare_case_job,
                    case_id=str(entry["case_id"]),
                    entry=dict(entry),
                    generation_profile_path=str(generation_profile_path),
                    dataset_role=dataset_role,
                    registry_path=str(registry_path),
                    grid_shape=tuple(int(value) for value in grid.shape),
                    voxel_size_mm=float(grid.voxel_size_mm),
                    global_seed=int(dataset["global_seed"]),
                    base_histories=int(execution["base_histories_per_projection"]),
                    staging_path=str(staging),
                    final_path=str(case_root),
                )
                futures[future] = str(entry["case_id"])
                return True

            for _ in range(min(len(pending_jobs), requested_local_parallel * 2)):
                submit_next()
            try:
                while futures:
                    completed, _ = wait(
                        tuple(futures), return_when=FIRST_COMPLETED
                    )
                    for future in completed:
                        case_id = futures.pop(future)
                        summaries_by_id[case_id] = future.result()
                        write_progress()
                        submit_next()
            except BaseException:
                for future in futures:
                    future.cancel()
                raise
    summaries = [
        summaries_by_id[str(entry["case_id"])]
        for entry in generation_plan["entries"]
    ]

    if dataset_role == "negative":
        observed_coverage = sorted(
            {
                label
                for summary in summaries
                for label in (
                    f"sex:{summary['patient']['sex']}",
                    f"morphology:{summary['patient']['liver_morphology']}",
                    f"territory:{summary['injection_territory']}",
                    "tumor_count:0",
                )
            }
        )
    else:
        observed_coverage = sorted(
            {
                label
                for summary in summaries
                for label in population_coverage(summary)
            }
        )
    required_coverage = [str(value) for value in config["required_coverage"]]
    missing = sorted(set(required_coverage) - set(observed_coverage))
    if missing:
        raise RuntimeError(f"deterministic cohort misses coverage: {missing}")
    input_bundle = write_preflight_input_bundle(preflight_root, summaries)
    report = {
        "schema_version": "pars_v2_task12f_linux50_preflight_v2",
        "status": "pass",
        "formal_runner_eligible": True,
        "generator_git_commit": commit,
        "generator_source": capture_generator_source_binding(REPO_ROOT),
        "python_runtime": capture_python_runtime(),
        "config_sha256": sha256_file(config_path),
        "profile_sha256": sha256_file(profile_path),
        "generation_profile_id": generation_profile.profile_id,
        "generation_profile_sha256": sha256_file(generation_profile_path),
        "scanner_sha256": sha256_file(scanner_path),
        "evidence_registry_sha256": sha256_file(registry_path),
        "release_acceptance_sha256": sha256_file(acceptance_path),
        "release_flag": release_flag,
        "task12e_acceptance_sha256": sha256_file(acceptance_path),
        "split_plan_sha256": split_plan.sha256,
        "generation_plan_sha256": generation_plan["sha256"],
        "case_count": case_count,
        "coverage": {
            "required": required_coverage,
            "observed": observed_coverage,
            "missing": [],
        },
        "input_bundle": input_bundle,
        "cases": summaries,
        "simind_launched": False,
    }
    atomic_write_json(preflight_root / "PREFLIGHT.json", report)
    atomic_write_json(
        progress_path,
        {
            "schema_version": "pars_v2_task12f_linux50_preflight_progress_v2",
            "status": "complete",
            "completed_count": case_count,
            "total_count": case_count,
            "preflight_sha256": sha256_file(preflight_root / "PREFLIGHT.json"),
        },
    )
    return report, generation_plan


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _build_bundle(
    config: Mapping[str, object],
    config_path: Path,
    preflight_root: Path,
    report: Mapping[str, object],
    generation_plan: Mapping[str, object],
    upload_root: Path,
    *,
    commit: str,
    resume: bool,
) -> tuple[Path, Path, str]:
    final_root = upload_root / BUNDLE_NAME
    archive = upload_root / f"{BUNDLE_NAME}.tar.gz"
    if final_root.is_dir():
        if not resume:
            raise FileExistsError(f"bundle exists; use --resume: {final_root}")
        validate_bundle(final_root)
        if not archive.is_file():
            with tarfile.open(archive, "w:gz", compresslevel=6) as stream:
                stream.add(final_root, arcname=BUNDLE_NAME)
        return final_root, archive, sha256_file(archive)

    upload_root.mkdir(parents=True, exist_ok=True)
    staging = upload_root / f".{BUNDLE_NAME}.{uuid.uuid4().hex}.tmp"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        configured_paths = config["paths"]
        _copy_file(config_path, staging / "config" / config_path.name)
        _copy_file(
            preflight_root / "PREFLIGHT.json", staging / "evidence" / "PREFLIGHT.json"
        )
        _copy_file(
            _resolve(configured_paths["task12e_acceptance"]),
            staging / "evidence" / "task12e_manual_acceptance.json",
        )
        _copy_file(
            preflight_root / "SPLIT_PLAN.json", staging / "plans" / "SPLIT_PLAN.json"
        )
        _copy_file(
            preflight_root / "GENERATION_PLAN.json",
            staging / "plans" / "GENERATION_PLAN.json",
        )
        _copy_file(
            _resolve(configured_paths["smc"]), staging / "runtime" / "ge870_czt.smc"
        )
        _copy_file(
            _resolve(configured_paths["simind_ini"]), staging / "runtime" / "simind.ini"
        )
        for script_name in (
            "task12f_linux50_common.py",
            "preflight_task12f_linux50_remote.py",
            "run_task12f_linux50_worker.py",
            "run_task12f_linux50_node.sh",
            "launch_task12f_linux50_screen.sh",
            "finalize_task12f_linux50_master.py",
        ):
            _copy_file(REPO_ROOT / "scripts" / script_name, staging / "scripts" / script_name)
        for module_name in (
            "__init__.py",
            "simind_exec.py",
            "simind_postprocess.py",
            "smc_parser.py",
        ):
            _copy_file(REPO_ROOT / "src" / "core" / module_name, staging / "src" / "core" / module_name)

        entries = {str(item["case_id"]): item for item in generation_plan["entries"]}
        nodes = [str(value) for value in config["nodes"]["expected"]]
        cases = []
        for index, summary in enumerate(report["cases"]):
            case_id = str(summary["case_id"])
            node_id = nodes[index % len(nodes)]
            case_source = preflight_root / "cases" / case_id
            destination = staging / "inputs" / case_id
            for name in (
                f"{case_id}_act_av.bin",
                f"{case_id}_atn_av.bin",
                "CASE_PREFLIGHT.json",
            ):
                _copy_file(case_source / name, destination / name)
            entry = entries[case_id]
            cases.append(
                {
                    "case_id": case_id,
                    "case_family_id": entry["case_family_id"],
                    "split": entry["split"],
                    "node_id": node_id,
                    "rr_seed": int(summary["rr_seed"]),
                    "nn_multiplier": int(config["execution"]["nn_multiplier"]),
                    "inputs": {
                        "source_relative_path": f"inputs/{case_id}/{case_id}_act_av.bin",
                        "source_sha256": summary["source_sha256"],
                        "density_relative_path": f"inputs/{case_id}/{case_id}_atn_av.bin",
                        "density_sha256": summary["density_sha256"],
                        "case_preflight_relative_path": f"inputs/{case_id}/CASE_PREFLIGHT.json",
                        "case_preflight_sha256": sha256_file(destination / "CASE_PREFLIGHT.json"),
                        "array_manifest_sha256": sha256_json(summary["array_manifest"]),
                    },
                }
            )
        rr_values = [int(case["rr_seed"]) for case in cases]
        if len(set(rr_values)) != len(rr_values):
            raise RuntimeError("Task 12F deterministic cohort contains duplicate /RR seeds")
        plan = {
            "schema_version": PLAN_SCHEMA,
            "dataset": dict(config["dataset"]),
            "profile_id": "population_tare_hcc_nopvi_v2",
            "split_plan_sha256": report["split_plan_sha256"],
            "generation_plan_sha256": report["generation_plan_sha256"],
            "preflight_sha256": sha256_file(preflight_root / "PREFLIGHT.json"),
            "task12e_acceptance_sha256": report["task12e_acceptance_sha256"],
            "expected_nodes": nodes,
            "hostname_prefix_by_node": dict(config["nodes"]["hostname_prefix_by_node"]),
            "execution": dict(config["execution"]),
            "linux_runtime": dict(config["linux_runtime"]),
            "runtime": {
                "smc_relative_path": "runtime/ge870_czt.smc",
                "smc_sha256": sha256_file(staging / "runtime" / "ge870_czt.smc"),
                "simind_ini_relative_path": "runtime/simind.ini",
                "simind_ini_sha256": sha256_file(staging / "runtime" / "simind.ini"),
            },
            "cases": cases,
            "source_binding": {
                "generator_git_commit": commit,
                "generator_source_binding_sha256": report["generator_source"]["binding_sha256"],
                "generator_worktree_clean": True,
            },
        }
        atomic_write_json(staging / "TASK12F_PLAN.json", plan)
        files = []
        for path in sorted(staging.rglob("*"), key=lambda item: item.as_posix()):
            if path.is_file():
                files.append(
                    {
                        "relative_path": path.relative_to(staging).as_posix(),
                        "size_bytes": path.stat().st_size,
                        "sha256": sha256_file(path),
                    }
                )
        manifest = {
            "schema_version": BUNDLE_SCHEMA,
            "status": "complete",
            "plan_relative_path": "TASK12F_PLAN.json",
            "plan_sha256": sha256_file(staging / "TASK12F_PLAN.json"),
            "case_count": len(cases),
            "source_binding": plan["source_binding"],
            "files": files,
        }
        atomic_write_json(staging / "BUNDLE_MANIFEST.json", manifest)
        _replace_directory_with_retry(staging, final_root)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    validate_bundle(final_root)
    with tarfile.open(archive, "w:gz", compresslevel=6) as stream:
        stream.add(final_root, arcname=BUNDLE_NAME)
    archive_sha = sha256_file(archive)
    (upload_root / f"{archive.name}.sha256").write_text(
        f"{archive_sha}  {archive.name}\n", encoding="utf-8", newline="\n"
    )
    return final_root, archive, archive_sha


def main() -> int:
    args = _parser().parse_args()
    config_path = args.config.resolve()
    config = _read_object(config_path)
    if config.get("schema_version") != "pars_v2_task12f_linux50_config_v2":
        raise ValueError("invalid Task 12F config schema")
    commit = _git_commit_and_clean()
    preflight_root = args.preflight_root.resolve()
    try:
        report, generation_plan = _prepare_preflight(
            config,
            config_path,
            preflight_root,
            commit=commit,
            resume=args.resume,
            local_max_parallel=args.local_max_parallel,
        )
    except Exception as exc:
        preflight_root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            preflight_root / "PROGRESS.json",
            {
                "schema_version": "pars_v2_task12f_linux50_preflight_progress_v2",
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        raise
    bundle, archive, archive_sha = _build_bundle(
        config,
        config_path,
        preflight_root,
        report,
        generation_plan,
        args.upload_root.resolve(),
        commit=commit,
        resume=args.resume,
    )
    print(
        json.dumps(
            {
                "status": "complete",
                "case_count": 50,
                "bundle_root": str(bundle),
                "archive": str(archive),
                "archive_sha256": archive_sha,
                "generator_git_commit": commit,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
