"""Run one Task 12F node shard with bounded parallel SIMIND processes."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import scipy
import skimage


SCRIPT_ROOT = Path(__file__).resolve().parent
BUNDLE_ROOT_DEFAULT = SCRIPT_ROOT.parent
sys.path.insert(0, str(SCRIPT_ROOT))
sys.path.insert(0, str(BUNDLE_ROOT_DEFAULT / "src"))

from task12f_linux50_common import (  # noqa: E402
    CASE_MARKER_FILENAME,
    CASE_SCHEMA,
    NODE_FAILED_SCHEMA,
    NODE_COMPLETE_SCHEMA,
    QUARTET_EXTENSIONS,
    REMOTE_PREFLIGHT_SCHEMA,
    atomic_write_json,
    cases_for_node,
    directory_manifest,
    load_plan,
    read_json,
    sha256_file,
    validate_bundle,
    validate_case_id,
    validate_node,
)
from core.simind_exec import SimindRunSpec, run_simind_case  # noqa: E402


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _event(**values: object) -> None:
    print(json.dumps(values, ensure_ascii=False), flush=True)


def _read_optional(path: str) -> str | None:
    candidate = Path(path)
    try:
        return candidate.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _cpu_quota_equivalent() -> float:
    quota = _read_optional("/sys/fs/cgroup/cpu,cpuacct/cpu.cfs_quota_us")
    period = _read_optional("/sys/fs/cgroup/cpu,cpuacct/cpu.cfs_period_us")
    if quota is not None and period is not None and int(quota) > 0:
        return int(quota) / int(period)
    cpu_max = _read_optional("/sys/fs/cgroup/cpu.max")
    if cpu_max:
        quota_text, period_text = cpu_max.split()
        if quota_text != "max":
            return int(quota_text) / int(period_text)
    return float(os.cpu_count() or 1)


def _memory_limit_bytes() -> int:
    for name in (
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",
        "/sys/fs/cgroup/memory.max",
    ):
        value = _read_optional(name)
        if value and value != "max":
            return int(value)
    return 2**63 - 1


def _validate_environment(plan: Mapping[str, Any]) -> Mapping[str, object]:
    expected = plan["linux_runtime"]
    versions = {
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "scikit-image": skimage.__version__,
    }
    if sys.version.split()[0] != expected["python_version"]:
        raise RuntimeError("Python version differs from the frozen Linux runtime")
    if versions != dict(expected["critical_packages"]):
        raise RuntimeError(f"critical package versions differ: {versions}")
    cpu_quota = _cpu_quota_equivalent()
    memory = _memory_limit_bytes()
    if cpu_quota < float(expected["minimum_cpu_quota_equivalent"]):
        raise RuntimeError(f"CPU quota too small: {cpu_quota}")
    if memory < int(expected["minimum_memory_bytes"]):
        raise RuntimeError(f"memory limit too small: {memory}")
    return {
        "python": sys.version.split()[0],
        "python_executable": str(Path(sys.executable).resolve()),
        "critical_packages": versions,
        "cpu_quota_equivalent": cpu_quota,
        "memory_limit_bytes": memory,
    }


def _runtime_fingerprint(
    plan: Mapping[str, Any], simind_exe: Path, smc_dir: Path
) -> Mapping[str, object]:
    expected = plan["linux_runtime"]
    simind_sha = sha256_file(simind_exe)
    if simind_sha != expected["simind_sha256"]:
        raise RuntimeError("Linux SIMIND binary hash mismatch")
    records, smc_manifest_sha = directory_manifest(smc_dir)
    if len(records) != int(expected["smc_dir_file_count"]):
        raise RuntimeError("SMC_DIR file count mismatch")
    if smc_manifest_sha != expected["smc_dir_manifest_sha256"]:
        raise RuntimeError("SMC_DIR manifest hash mismatch")
    return {
        "simind_executable": str(simind_exe.resolve()),
        "simind_sha256": simind_sha,
        "smc_dir": str(smc_dir.resolve()),
        "smc_dir_file_count": len(records),
        "smc_dir_manifest_sha256": smc_manifest_sha,
        "environment": _validate_environment(plan),
    }


def _validate_existing(
    final_dir: Path, case_id: str, bundle_manifest_sha256: str
) -> Mapping[str, Any]:
    document = read_json(final_dir / CASE_MARKER_FILENAME)
    if (
        document.get("schema_version") != CASE_SCHEMA
        or document.get("status") != "complete"
        or document.get("case_id") != case_id
        or document.get("bundle_manifest_sha256") != bundle_manifest_sha256
    ):
        raise ValueError(f"invalid completed case marker: {case_id}")
    artifacts = document.get("output_artifacts")
    if not isinstance(artifacts, Mapping):
        raise ValueError(f"{case_id} artifact records missing")
    for extension in QUARTET_EXTENSIONS:
        path = final_dir / f"{case_id}.{extension}"
        record = artifacts.get(extension)
        if not path.is_file() or not isinstance(record, Mapping):
            raise FileNotFoundError(f"{case_id}.{extension}")
        if path.stat().st_size != int(record.get("size_bytes", -1)):
            raise ValueError(f"{case_id}.{extension} size drift")
        if sha256_file(path) != record.get("sha256"):
            raise ValueError(f"{case_id}.{extension} hash drift")
    provenance = read_json(final_dir / "run_provenance.json")
    if provenance.get("status") != "complete":
        raise ValueError(f"{case_id} SIMIND provenance is incomplete")
    return document


def _run_case(
    *,
    bundle_root: Path,
    shared_node_root: Path,
    local_root: Path,
    simind_exe: Path,
    smc_dir: Path,
    plan: Mapping[str, Any],
    runtime_fingerprint: Mapping[str, object],
    bundle_manifest_sha256: str,
    case: Mapping[str, Any],
    resume: bool,
) -> Mapping[str, Any]:
    case_id = validate_case_id(case.get("case_id"))
    final_dir = shared_node_root / "cases" / case_id
    if final_dir.exists():
        if not resume:
            raise FileExistsError(f"completed case exists; use --resume: {case_id}")
        result = _validate_existing(final_dir, case_id, bundle_manifest_sha256)
        _event(event="case_reused", node_id=shared_node_root.name, case_id=case_id)
        return result

    inputs = case.get("inputs")
    runtime = plan["runtime"]
    if not isinstance(inputs, Mapping) or not isinstance(runtime, Mapping):
        raise ValueError(f"{case_id} plan records are malformed")
    source = bundle_root / str(inputs["source_relative_path"])
    density = bundle_root / str(inputs["density_relative_path"])
    smc = bundle_root / str(runtime["smc_relative_path"])
    ini = bundle_root / str(runtime["simind_ini_relative_path"])
    for label, path, expected in (
        ("source", source, inputs["source_sha256"]),
        ("density", density, inputs["density_sha256"]),
        ("smc", smc, runtime["smc_sha256"]),
        ("simind.ini", ini, runtime["simind_ini_sha256"]),
    ):
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"{case_id} {label} hash mismatch")

    case_local_parent = local_root / case_id
    case_local_parent.mkdir(parents=True, exist_ok=True)
    case_local_root = case_local_parent / f"attempt-{uuid.uuid4().hex}"
    _event(event="case_started", node_id=shared_node_root.name, case_id=case_id)
    started = time.monotonic()
    result = run_simind_case(
        SimindRunSpec(
            case_id=case_id,
            simind_exe=simind_exe,
            smc_file=smc,
            simind_ini=ini,
            source_bin=source,
            density_bin=density,
            output_root=case_local_root,
            rr_seed=int(case["rr_seed"]),
            nn_multiplier=int(case["nn_multiplier"]),
            timeout_seconds=float(plan["execution"]["timeout_seconds"]),
            environment_overrides={"SMC_DIR": str(smc_dir.resolve()) + os.sep},
        )
    )
    if not result.success or result.final_dir is None:
        raise RuntimeError(
            f"{case_id} SIMIND failed; diagnostics={result.failure_dir}; {result.error}"
        )
    local_final = result.final_dir
    provenance = read_json(local_final / "run_provenance.json")
    artifacts: dict[str, object] = {}
    for extension in QUARTET_EXTENSIONS:
        path = local_final / f"{case_id}.{extension}"
        artifacts[extension] = {
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    marker = {
        "schema_version": CASE_SCHEMA,
        "status": "complete",
        "case_id": case_id,
        "node_id": shared_node_root.name,
        "hostname": socket.gethostname(),
        "bundle_manifest_sha256": bundle_manifest_sha256,
        "case_plan": dict(case),
        "runtime_fingerprint": dict(runtime_fingerprint),
        "simind_provenance_sha256": sha256_file(local_final / "run_provenance.json"),
        "projection_audit": provenance["completion_audit"],
        "output_artifacts": artifacts,
        "elapsed_seconds": time.monotonic() - started,
        "finished_utc": _utc_now(),
    }
    atomic_write_json(local_final / CASE_MARKER_FILENAME, marker)
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    publishing = final_dir.parent / f".{case_id}.{uuid.uuid4().hex}.tmp"
    shutil.copytree(local_final, publishing)
    try:
        os.replace(publishing, final_dir)
    finally:
        if publishing.exists():
            shutil.rmtree(publishing, ignore_errors=True)
    verified = _validate_existing(final_dir, case_id, bundle_manifest_sha256)
    shutil.rmtree(case_local_root, ignore_errors=True)
    _event(
        event="case_complete",
        node_id=shared_node_root.name,
        case_id=case_id,
        elapsed_seconds=marker["elapsed_seconds"],
    )
    return verified


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--shared-root", type=Path, required=True)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--simind-exe", type=Path, required=True)
    parser.add_argument("--smc-dir", type=Path, required=True)
    parser.add_argument("--local-root", type=Path, required=True)
    parser.add_argument("--max-parallel", type=int, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    bundle_root = args.bundle_root.resolve()
    shared_root = args.shared_root.resolve()
    manifest = validate_bundle(bundle_root)
    plan = load_plan(bundle_root)
    validate_node(plan, args.node_id, socket.gethostname())
    remote_preflight = read_json(shared_root / "REMOTE_PREFLIGHT.json")
    if (
        remote_preflight.get("schema_version") != REMOTE_PREFLIGHT_SCHEMA
        or remote_preflight.get("status") != "pass"
        or remote_preflight.get("bundle_manifest_sha256")
        != sha256_file(bundle_root / "BUNDLE_MANIFEST.json")
    ):
        raise RuntimeError("passing bundle-bound remote preflight is required")
    expected_parallel = int(
        plan["execution"]["requested_parallel_by_node"][args.node_id]
    )
    maximum = int(plan["execution"]["maximum_parallel_per_node"])
    if args.max_parallel != expected_parallel or not 1 <= args.max_parallel <= maximum:
        raise ValueError(
            f"{args.node_id} max_parallel must equal frozen value {expected_parallel}"
        )
    if shutil.disk_usage(shared_root.parent).free < int(
        plan["linux_runtime"]["minimum_shared_free_bytes"]
    ):
        raise RuntimeError("shared filesystem free space is below the frozen minimum")
    runtime_fingerprint = _runtime_fingerprint(
        plan, args.simind_exe.resolve(), args.smc_dir.resolve()
    )
    bundle_sha = sha256_file(bundle_root / "BUNDLE_MANIFEST.json")
    assigned = cases_for_node(plan, args.node_id)
    node_root = shared_root / "nodes" / args.node_id
    node_root.mkdir(parents=True, exist_ok=True)
    args.local_root.mkdir(parents=True, exist_ok=True)
    _event(
        event="worker_started",
        node_id=args.node_id,
        case_count=len(assigned),
        max_parallel=args.max_parallel,
    )
    completed: list[Mapping[str, Any]] = []
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=args.max_parallel) as executor:
        futures = {
            executor.submit(
                _run_case,
                bundle_root=bundle_root,
                shared_node_root=node_root,
                local_root=args.local_root,
                simind_exe=args.simind_exe.resolve(),
                smc_dir=args.smc_dir.resolve(),
                plan=plan,
                runtime_fingerprint=runtime_fingerprint,
                bundle_manifest_sha256=bundle_sha,
                case=case,
                resume=args.resume,
            ): str(case["case_id"])
            for case in assigned
        }
        for future in as_completed(futures):
            case_id = futures[future]
            try:
                completed.append(future.result())
            except Exception as exc:
                failures.append(
                    {
                        "case_id": case_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                _event(
                    event="case_failed",
                    node_id=args.node_id,
                    case_id=case_id,
                    error=failures[-1]["error"],
                )
    if failures:
        atomic_write_json(
            node_root / "NODE_FAILED.json",
            {
                "schema_version": NODE_FAILED_SCHEMA,
                "status": "failed",
                "node_id": args.node_id,
                "failures": failures,
                "completed_count": len(completed),
                "failed_utc": _utc_now(),
            },
        )
        return 1
    completion = {
        "schema_version": NODE_COMPLETE_SCHEMA,
        "status": "complete",
        "node_id": args.node_id,
        "hostname": socket.gethostname(),
        "bundle_manifest_sha256": bundle_sha,
        "max_parallel": args.max_parallel,
        "runtime_fingerprint": dict(runtime_fingerprint),
        "case_ids": sorted(str(item["case_id"]) for item in completed),
        "case_count": len(completed),
        "finished_utc": _utc_now(),
    }
    atomic_write_json(node_root / "NODE_COMPLETE.json", completion)
    _event(event="worker_complete", node_id=args.node_id, case_count=len(completed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
