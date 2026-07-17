"""Freeze and package the formal Linux 500-main plus 50-negative campaign."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
import shutil
import sys
import tarfile
import uuid
from pathlib import Path
from typing import Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from build_task12f_linux50_bundle import (  # noqa: E402
    _git_commit_and_clean,
    _prepare_preflight,
    _read_object,
    _replace_directory_with_retry,
    _resolve,
)
from core.provenance import sha256_json  # noqa: E402
from task13_formal550_runtime import patch_runtime_contract  # noqa: E402


patch_runtime_contract()

from task12f_linux50_common import (  # noqa: E402
    BUNDLE_SCHEMA,
    PLAN_SCHEMA,
    atomic_write_json,
    sha256_file,
    validate_bundle,
)


DEFAULT_CONFIG = REPO_ROOT / "configs" / "task13_formal550_v1.json"
DEFAULT_PREFLIGHT = Path(r"D:\PFE-U\PAR\outputs\task13_formal550_preflight_v1")
DEFAULT_UPLOAD = Path(r"D:\PFE-U\PAR\outputs\task13_formal550_upload_v1")
BUNDLE_NAME = "pars_v2_task13_formal550_bundle_v1"
PLAN_FILENAME = "TASK13_PLAN.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--preflight-root", type=Path, default=DEFAULT_PREFLIGHT)
    parser.add_argument("--upload-root", type=Path, default=DEFAULT_UPLOAD)
    parser.add_argument("--local-max-parallel", type=int)
    parser.add_argument("--resume", action="store_true")
    return parser


def _role_config(config: Mapping[str, object], role: str) -> dict[str, object]:
    datasets = config["datasets"]
    paths = config["paths"]
    if not isinstance(datasets, Mapping) or not isinstance(paths, Mapping):
        raise ValueError("formal datasets/paths must be objects")
    profile_key = "main_profile" if role == "main" else "negative_profile"
    generation_key = (
        "main_profile" if role == "main" else "negative_generation_profile"
    )
    result: dict[str, object] = {
        "dataset": dict(datasets[role]),
        "paths": {
            "profile": paths[profile_key],
            "generation_profile": paths[generation_key],
            "scanner": paths["scanner"],
            "evidence_registry": paths["evidence_registry"],
            "smc": paths["smc"],
            "simind_ini": paths["simind_ini"],
            "task12g_release": paths["task12g_release"],
            "release_acceptance_key": "task12g_release",
        },
        "execution": dict(config["execution"]),
        "linux_runtime": dict(config["linux_runtime"]),
        "nodes": dict(config["nodes"]),
        "required_coverage": list(config["required_coverage"][role]),
        "frozen_evidence": dict(config["frozen_evidence"]),
    }
    if role == "main":
        result["challenge_design"] = dict(config["challenge_design"])
    return result


def _copy_or_link(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def _role_cases(
    *,
    role: str,
    role_config: Mapping[str, object],
    preflight_root: Path,
    report: Mapping[str, object],
    generation_plan: Mapping[str, object],
    staging: Path,
) -> list[dict[str, object]]:
    entries = {str(item["case_id"]): item for item in generation_plan["entries"]}
    dataset = role_config["dataset"]
    if not isinstance(dataset, Mapping):
        raise ValueError("dataset record must be an object")
    cases: list[dict[str, object]] = []
    for summary in report["cases"]:
        case_id = str(summary["case_id"])
        source = preflight_root / "cases" / case_id
        destination = staging / "inputs" / role / case_id
        for name in (
            f"{case_id}_act_av.bin",
            f"{case_id}_atn_av.bin",
            "CASE_PREFLIGHT.json",
        ):
            _copy_or_link(source / name, destination / name)
        entry = entries[case_id]
        cases.append(
            {
                "case_id": case_id,
                "case_family_id": entry["case_family_id"],
                "dataset_id": dataset["dataset_id"],
                "dataset_version": dataset["dataset_version"],
                "dataset_role": role,
                "profile_id": entry["profile_id"],
                "split": entry["split"],
                "population_weight": float(entry["population_weight"]),
                "rr_seed": int(summary["rr_seed"]),
                "nn_multiplier": int(role_config["execution"]["nn_multiplier"]),
                "inputs": {
                    "source_relative_path": (
                        f"inputs/{role}/{case_id}/{case_id}_act_av.bin"
                    ),
                    "source_sha256": summary["source_sha256"],
                    "density_relative_path": (
                        f"inputs/{role}/{case_id}/{case_id}_atn_av.bin"
                    ),
                    "density_sha256": summary["density_sha256"],
                    "case_preflight_relative_path": (
                        f"inputs/{role}/{case_id}/CASE_PREFLIGHT.json"
                    ),
                    "case_preflight_sha256": sha256_file(
                        destination / "CASE_PREFLIGHT.json"
                    ),
                    "array_manifest_sha256": sha256_json(summary["array_manifest"]),
                },
            }
        )
    return cases


def _validate_frozen_assignment(cases: list[dict[str, object]]) -> None:
    if len(cases) != 550:
        raise RuntimeError("formal campaign must contain exactly 550 cases")
    ids = [str(item["case_id"]) for item in cases]
    if len(set(ids)) != 550:
        raise RuntimeError("formal case IDs are not unique")
    rr = [int(item["rr_seed"]) for item in cases]
    if len(set(rr)) != 550:
        raise RuntimeError("formal /RR values are not unique across both roles")
    role_counts = Counter(str(item["dataset_role"]) for item in cases)
    if role_counts != Counter({"main": 500, "negative": 50}):
        raise RuntimeError(f"formal role counts drifted: {role_counts}")
    main_splits = Counter(
        str(item["split"]) for item in cases if item["dataset_role"] == "main"
    )
    negative_splits = Counter(
        str(item["split"])
        for item in cases
        if item["dataset_role"] == "negative"
    )
    if main_splits != Counter({"train": 400, "val": 50, "test": 50}):
        raise RuntimeError(f"main split counts drifted: {main_splits}")
    if negative_splits != Counter({"test": 50}):
        raise RuntimeError(f"negative split policy drifted: {negative_splits}")


def _build_bundle(
    *,
    config: Mapping[str, object],
    config_path: Path,
    preflight_root: Path,
    role_configs: Mapping[str, Mapping[str, object]],
    reports: Mapping[str, Mapping[str, object]],
    generation_plans: Mapping[str, Mapping[str, object]],
    upload_root: Path,
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
        _copy_or_link(config_path, staging / "config" / config_path.name)
        release = _resolve(config["paths"]["task12g_release"])
        _copy_or_link(
            release,
            staging / "evidence" / "task12g_manual_acceptance.json",
        )
        for role in ("main", "negative"):
            role_root = preflight_root / role
            for name in ("PREFLIGHT.json", "SPLIT_PLAN.json", "GENERATION_PLAN.json"):
                _copy_or_link(
                    role_root / name,
                    staging / "plans" / role / name,
                )
        _copy_or_link(_resolve(config["paths"]["smc"]), staging / "runtime" / "ge870_czt.smc")
        _copy_or_link(
            _resolve(config["paths"]["simind_ini"]),
            staging / "runtime" / "simind.ini",
        )
        for script_name in (
            "task12f_linux50_common.py",
            "task13_formal550_runtime.py",
            "preflight_task12f_linux50_remote.py",
            "preflight_task13_formal550_remote.py",
            "run_task12f_linux50_worker.py",
            "run_task13_formal550_worker.py",
            "finalize_task12f_linux50_master.py",
            "finalize_task13_formal550_master.py",
            "run_task13_formal550_node.sh",
            "launch_task13_formal550_screen.sh",
        ):
            _copy_or_link(
                REPO_ROOT / "scripts" / script_name,
                staging / "scripts" / script_name,
            )
        for module_name in (
            "__init__.py",
            "simind_exec.py",
            "simind_postprocess.py",
            "smc_parser.py",
        ):
            _copy_or_link(
                REPO_ROOT / "src" / "core" / module_name,
                staging / "src" / "core" / module_name,
            )

        cases: list[dict[str, object]] = []
        for role in ("main", "negative"):
            cases.extend(
                _role_cases(
                    role=role,
                    role_config=role_configs[role],
                    preflight_root=preflight_root / role,
                    report=reports[role],
                    generation_plan=generation_plans[role],
                    staging=staging,
                )
            )
        _validate_frozen_assignment(cases)
        nodes = [str(value) for value in config["nodes"]["expected"]]
        for index, case in enumerate(cases):
            case["node_id"] = nodes[index % len(nodes)]
        node_counts = Counter(str(case["node_id"]) for case in cases)
        if node_counts != Counter({"cnc5": 184, "cnc7": 183, "cnc8": 183}):
            raise RuntimeError(f"formal node assignment drifted: {node_counts}")
        binding_values = {
            str(reports[role]["generator_source"]["binding_sha256"])
            for role in ("main", "negative")
        }
        if len(binding_values) != 1:
            raise RuntimeError("role preflights bind different Generator sources")
        plan = {
            "schema_version": PLAN_SCHEMA,
            "dataset": dict(config["campaign"]),
            "datasets": dict(config["datasets"]),
            "release_evidence_relative_path": "evidence/task12g_manual_acceptance.json",
            "release_evidence_sha256": sha256_file(release),
            "release_flag": config["frozen_evidence"]["release_flag"],
            "preflight": {
                role: {
                    "relative_path": f"plans/{role}/PREFLIGHT.json",
                    "sha256": sha256_file(preflight_root / role / "PREFLIGHT.json"),
                    "split_plan_sha256": reports[role]["split_plan_sha256"],
                    "generation_plan_sha256": reports[role]["generation_plan_sha256"],
                }
                for role in ("main", "negative")
            },
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
                "generator_source_binding_sha256": next(iter(binding_values)),
                "generator_worktree_clean": True,
            },
        }
        atomic_write_json(staging / PLAN_FILENAME, plan)
        files = [
            {
                "relative_path": path.relative_to(staging).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in sorted(staging.rglob("*"), key=lambda item: item.as_posix())
            if path.is_file()
        ]
        manifest = {
            "schema_version": BUNDLE_SCHEMA,
            "status": "complete",
            "plan_relative_path": PLAN_FILENAME,
            "plan_sha256": sha256_file(staging / PLAN_FILENAME),
            "case_count": 550,
            "role_case_counts": {"main": 500, "negative": 50},
            "node_case_counts": dict(node_counts),
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
        f"{archive_sha}  {archive.name}\n",
        encoding="utf-8",
        newline="\n",
    )
    return final_root, archive, archive_sha


def main() -> int:
    args = _parser().parse_args()
    config_path = args.config.resolve()
    config = _read_object(config_path)
    if config.get("schema_version") != "pars_v2_task13_formal550_config_v1":
        raise ValueError("invalid Task 13 formal config schema")
    if int(config["campaign"]["case_count"]) != 550:
        raise ValueError("formal campaign count must be 550")
    commit = _git_commit_and_clean()
    preflight_root = args.preflight_root.resolve()
    role_configs = {
        role: _role_config(config, role) for role in ("main", "negative")
    }
    reports: dict[str, Mapping[str, object]] = {}
    generation_plans: dict[str, Mapping[str, object]] = {}
    for role in ("main", "negative"):
        report, generation_plan = _prepare_preflight(
            role_configs[role],
            config_path,
            preflight_root / role,
            commit=commit,
            resume=args.resume,
            local_max_parallel=args.local_max_parallel,
        )
        reports[role] = report
        generation_plans[role] = generation_plan
    bundle, archive, archive_sha = _build_bundle(
        config=config,
        config_path=config_path,
        preflight_root=preflight_root,
        role_configs=role_configs,
        reports=reports,
        generation_plans=generation_plans,
        upload_root=args.upload_root.resolve(),
        commit=commit,
        resume=args.resume,
    )
    print(
        json.dumps(
            {
                "status": "complete",
                "case_count": 550,
                "role_case_counts": {"main": 500, "negative": 50},
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
