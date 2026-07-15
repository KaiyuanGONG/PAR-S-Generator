"""Run or resume one isolated Task 12E Linux node shard using only frozen inputs."""

from __future__ import annotations

import argparse
import array
import json
import math
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from task12e_linux_common import (
    ENVIRONMENT_SCHEMA,
    EXPECTED_A00_BYTES,
    EXPECTED_PROJECTION_SHAPE,
    NODE_COMPLETE_SCHEMA,
    QUARTET_EXTENSIONS,
    atomic_write_json,
    node_case_specs,
    normalized_res_sha256,
    read_json,
    sha256_file,
    validate_bundle,
    validate_case_id,
    validate_node_id,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _dependency_hashes(executable: Path) -> tuple[str, dict[str, str]]:
    completed = subprocess.run(
        ["ldd", str(executable)], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise RuntimeError(f"ldd failed: {completed.stderr.strip()}")
    paths: set[Path] = set()
    for line in completed.stdout.splitlines():
        match = re.search(r"=>\s+(/\S+)", line)
        if match is None:
            match = re.match(r"\s*(/\S+)\s+\(", line)
        if match is not None:
            path = Path(match.group(1)).resolve()
            if path.is_file():
                paths.add(path)
    return completed.stdout, {str(path): sha256_file(path) for path in sorted(paths)}


def _read_optional(path: str) -> str | None:
    candidate = Path(path)
    if not candidate.is_file():
        return None
    return candidate.read_text(encoding="utf-8", errors="replace").strip()


def _resource_snapshot() -> dict[str, object]:
    return {
        "cpu_count": os.cpu_count(),
        "cpu_max_v2": _read_optional("/sys/fs/cgroup/cpu.max"),
        "cpu_quota_v1": _read_optional(
            "/sys/fs/cgroup/cpu,cpuacct/cpu.cfs_quota_us"
        ),
        "cpu_period_v1": _read_optional(
            "/sys/fs/cgroup/cpu,cpuacct/cpu.cfs_period_us"
        ),
        "cpuset_v1": _read_optional("/sys/fs/cgroup/cpuset/cpuset.cpus"),
        "memory_max_v2": _read_optional("/sys/fs/cgroup/memory.max"),
        "memory_limit_v1": _read_optional(
            "/sys/fs/cgroup/memory/memory.limit_in_bytes"
        ),
    }


def _runtime_fingerprint(simind_exe: Path, environment_path: Path) -> dict[str, object]:
    ldd_output, dependency_hashes = _dependency_hashes(simind_exe)
    environment = read_json(environment_path)
    if environment.get("schema_version") != ENVIRONMENT_SCHEMA or environment.get(
        "status"
    ) != "pass":
        raise ValueError("LINUX_ENVIRONMENT.json is not a passing Task 12E capture")
    return {
        "hostname": socket.gethostname(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "libc": list(platform.libc_ver()),
        },
        "python_executable": str(Path(sys.executable).resolve()),
        "python_version": ".".join(str(value) for value in sys.version_info[:3]),
        "simind_executable": str(simind_exe),
        "simind_sha256": sha256_file(simind_exe),
        "ldd_output": ldd_output,
        "dependency_hashes": dependency_hashes,
        "environment_capture_sha256": sha256_file(environment_path),
        "resources": _resource_snapshot(),
    }


def _validate_minimum_resources(
    plan: Mapping[str, Any], fingerprint: Mapping[str, Any]
) -> None:
    minimum = plan.get("minimum_resources_per_node")
    resources = fingerprint.get("resources")
    if not isinstance(minimum, Mapping) or not isinstance(resources, Mapping):
        raise ValueError("resource contract is missing")
    cpu_count = float(resources.get("cpu_count") or 0)
    quota = resources.get("cpu_quota_v1")
    period = resources.get("cpu_period_v1")
    if quota not in (None, "", "-1") and period not in (None, "", "0"):
        cpu_count = min(cpu_count, float(str(quota)) / float(str(period)))
    cpu_max_v2 = resources.get("cpu_max_v2")
    if cpu_max_v2 not in (None, ""):
        fields = str(cpu_max_v2).split()
        if len(fields) == 2 and fields[0] != "max" and fields[1] != "0":
            cpu_count = min(cpu_count, float(fields[0]) / float(fields[1]))
    if cpu_count < float(minimum.get("cpu_quota_equivalent", 0)):
        raise ValueError(f"effective CPU quota {cpu_count:g} is below the bound minimum")
    memory_value = resources.get("memory_limit_v1") or resources.get("memory_max_v2")
    if memory_value not in (None, "", "max") and int(str(memory_value)) < int(
        minimum.get("memory_bytes", 0)
    ):
        raise ValueError("effective memory limit is below the bound minimum")


def _audit_a00(path: Path) -> dict[str, object]:
    if path.stat().st_size != EXPECTED_A00_BYTES:
        raise ValueError(
            f"{path.name} size {path.stat().st_size} != {EXPECTED_A00_BYTES}"
        )
    values = array.array("f")
    with path.open("rb") as stream:
        values.fromfile(stream, EXPECTED_A00_BYTES // 4)
    if sys.byteorder != "little":
        values.byteswap()
    finite = all(math.isfinite(float(value)) for value in values)
    nonnegative = all(float(value) >= 0 for value in values)
    if not finite or not nonnegative:
        raise ValueError(f"{path.name} contains non-finite or negative values")
    return {
        "shape_vvu": list(EXPECTED_PROJECTION_SHAPE),
        "size_bytes": path.stat().st_size,
        "finite": finite,
        "nonnegative": nonnegative,
        "sum": math.fsum(float(value) for value in values),
        "sha256": sha256_file(path),
    }


def _bounded_parallelism(
    plan: Mapping[str, Any], requested: int, case_count: int
) -> int:
    execution = plan.get("execution")
    if not isinstance(execution, Mapping):
        raise ValueError("bound execution contract missing")
    bound = int(execution.get("maximum_parallel_per_node", 0))
    if requested < 1 or requested > bound:
        raise ValueError(
            f"max_parallel must be within 1..{bound}; received {requested}"
        )
    if case_count < 1:
        raise ValueError("node case count must be positive")
    return min(requested, case_count)


def _execute_cases_concurrently(
    cases: tuple[Mapping[str, Any], ...],
    max_parallel: int,
    execute: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    completed: list[Mapping[str, Any]] = []
    with ThreadPoolExecutor(
        max_workers=max_parallel,
        thread_name_prefix="task12e-case",
    ) as executor:
        futures = [executor.submit(execute, case) for case in cases]
        for future in as_completed(futures):
            completed.append(future.result())
    return completed


def _validate_completed_case(
    case_dir: Path, case_id: str, bundle_manifest_sha256: str
) -> Mapping[str, Any]:
    provenance_path = case_dir / "run_provenance.json"
    provenance = read_json(provenance_path)
    if provenance.get("status") != "complete" or provenance.get("case_id") != case_id:
        raise ValueError(f"invalid completed provenance for {case_id}")
    if provenance.get("bundle_manifest_sha256") != bundle_manifest_sha256:
        raise ValueError(f"completed provenance bundle binding drift for {case_id}")
    artifacts = provenance.get("output_artifacts")
    if not isinstance(artifacts, Mapping):
        raise ValueError(f"{case_id} output_artifacts missing")
    for extension in QUARTET_EXTENSIONS:
        path = case_dir / f"{case_id}.{extension}"
        record = artifacts.get(extension)
        if not path.is_file() or not isinstance(record, Mapping):
            raise ValueError(f"{case_id}.{extension} missing")
        if path.stat().st_size != int(record.get("size_bytes", -1)):
            raise ValueError(f"{case_id}.{extension} size drift")
        if sha256_file(path) != record.get("sha256"):
            raise ValueError(f"{case_id}.{extension} hash drift")
    return provenance


def _run_case(
    *,
    bundle_root: Path,
    shared_node_root: Path,
    local_root: Path,
    simind_exe: Path,
    runtime: Mapping[str, Any],
    runtime_fingerprint: Mapping[str, Any],
    bundle_manifest_sha256: str,
    case: Mapping[str, Any],
    resume: bool,
) -> Mapping[str, Any]:
    case_id = validate_case_id(case.get("case_id"))
    final_dir = shared_node_root / case_id
    if final_dir.exists():
        if not resume:
            raise FileExistsError(f"completed case exists; use --resume: {final_dir}")
        return _validate_completed_case(final_dir, case_id, bundle_manifest_sha256)

    input_record = case.get("inputs")
    if not isinstance(input_record, Mapping):
        raise ValueError(f"{case_id} inputs missing")
    source = bundle_root / str(input_record.get("source_relative_path"))
    density = bundle_root / str(input_record.get("density_relative_path"))
    smc = bundle_root / str(runtime.get("smc_relative_path"))
    ini = bundle_root / str(runtime.get("simind_ini_relative_path"))
    for name, path, expected_hash in (
        ("source", source, input_record.get("source_sha256")),
        ("density", density, input_record.get("density_sha256")),
        ("smc", smc, runtime.get("smc_sha256")),
        ("simind.ini", ini, runtime.get("simind_ini_sha256")),
    ):
        if not path.is_file() or sha256_file(path) != expected_hash:
            raise ValueError(f"{case_id} {name} input hash mismatch")

    local_root.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix=f"{case_id}-", dir=local_root))
    started = _utc_now()
    started_monotonic = time.monotonic()
    try:
        local_smc = work_dir / smc.name
        local_ini = work_dir / "simind.ini"
        local_source = work_dir / f"{case_id}_act_av.bin"
        local_density = work_dir / f"{case_id}_atn_av.bin"
        shutil.copy2(smc, local_smc)
        shutil.copy2(ini, local_ini)
        shutil.copy2(source, local_source)
        shutil.copy2(density, local_density)
        simind_data = simind_exe.parent / "smc_dir"
        if simind_data.is_dir():
            try:
                (work_dir / "smc_dir").symlink_to(simind_data, target_is_directory=True)
            except OSError:
                pass
        command = [
            str(simind_exe),
            local_smc.stem,
            case_id,
            f"/FS:{case_id}",
            f"/FD:{case_id}",
            f"/NN:{int(case.get('nn_multiplier'))}",
            f"/RR:{int(case.get('rr_seed'))}",
        ]
        completed = subprocess.run(
            command,
            cwd=work_dir,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=float(runtime.get("timeout_seconds", 7200)),
            check=False,
        )
        (work_dir / "stdout.log").write_text(completed.stdout, encoding="utf-8")
        (work_dir / "stderr.log").write_text(completed.stderr, encoding="utf-8")
        if completed.returncode != 0:
            raise RuntimeError(
                f"{case_id} SIMIND exit={completed.returncode}: {completed.stderr[-1000:]}"
            )
        output_artifacts: dict[str, object] = {}
        for extension in QUARTET_EXTENSIONS:
            path = work_dir / f"{case_id}.{extension}"
            if not path.is_file() or path.stat().st_size <= 0:
                raise FileNotFoundError(f"missing SIMIND output: {path}")
            output_artifacts[extension] = {
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        a00_audit = _audit_a00(work_dir / f"{case_id}.a00")
        output_artifacts["res"]["normalized_sha256"] = normalized_res_sha256(
            work_dir / f"{case_id}.res"
        )
        provenance: dict[str, object] = {
            "schema_version": "pars_v2_task12e_linux_run_provenance_v2",
            "status": "complete",
            "case_id": case_id,
            "bundle_manifest_sha256": bundle_manifest_sha256,
            "fixture_group": case.get("fixture_group"),
            "node_id": shared_node_root.name,
            "hostname": socket.gethostname(),
            "started_utc": started,
            "finished_utc": _utc_now(),
            "elapsed_seconds": time.monotonic() - started_monotonic,
            "command": command,
            "exit_code": completed.returncode,
            "rr_seed": int(case.get("rr_seed")),
            "nn_multiplier": int(case.get("nn_multiplier")),
            "inputs": {
                "source_sha256": sha256_file(local_source),
                "density_sha256": sha256_file(local_density),
                "smc_sha256": sha256_file(local_smc),
                "simind_ini_sha256": sha256_file(local_ini),
            },
            "runtime_fingerprint": runtime_fingerprint,
            "a00_audit": a00_audit,
            "output_artifacts": output_artifacts,
        }
        atomic_write_json(work_dir / "run_provenance.json", provenance)

        shared_node_root.mkdir(parents=True, exist_ok=True)
        publish_dir = shared_node_root / f".{case_id}.tmp-{uuid.uuid4().hex}"
        publish_dir.mkdir(parents=False, exist_ok=False)
        try:
            for extension in QUARTET_EXTENSIONS:
                shutil.copy2(work_dir / f"{case_id}.{extension}", publish_dir)
            shutil.copy2(work_dir / "stdout.log", publish_dir)
            shutil.copy2(work_dir / "stderr.log", publish_dir)
            shutil.copy2(work_dir / "run_provenance.json", publish_dir)
            os.replace(publish_dir, final_dir)
        finally:
            if publish_dir.exists():
                shutil.rmtree(publish_dir, ignore_errors=True)
        return _validate_completed_case(final_dir, case_id, bundle_manifest_sha256)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--shared-root", type=Path, required=True)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--simind-exe", type=Path, required=True)
    parser.add_argument("--local-root", type=Path)
    parser.add_argument("--max-parallel", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    bundle_root = args.bundle_root.resolve()
    manifest = validate_bundle(bundle_root)
    bundle_manifest_sha256 = sha256_file(bundle_root / "BUNDLE_MANIFEST.json")
    plan = read_json(bundle_root / str(manifest["plan_relative_path"]))
    hostname = socket.gethostname()
    validate_node_id(plan, args.node_id, hostname)
    simind_exe = args.simind_exe.expanduser().resolve()
    expected_simind = plan.get("expected_linux_simind_sha256")
    if not simind_exe.is_file() or sha256_file(simind_exe) != expected_simind:
        raise ValueError("Linux SIMIND binary hash does not match the bound plan")
    shared_root = args.shared_root.expanduser().resolve()
    environment_path = shared_root / "LINUX_ENVIRONMENT.json"
    runtime_fingerprint = _runtime_fingerprint(simind_exe, environment_path)
    _validate_minimum_resources(plan, runtime_fingerprint)
    if runtime_fingerprint["simind_sha256"] != expected_simind:
        raise ValueError("runtime SIMIND hash drift")
    runtime = plan.get("runtime")
    if not isinstance(runtime, Mapping):
        raise ValueError("bound runtime missing")
    local_root = (
        args.local_root.expanduser().resolve()
        if args.local_root
        else Path(f"/tmp/pars_v2_task12e_{args.node_id}")
    )
    shared_node_root = shared_root / "nodes" / args.node_id
    completion_path = shared_node_root / "NODE_COMPLETE.json"
    if completion_path.exists():
        if not args.resume:
            raise FileExistsError(f"node completion exists; use --resume: {completion_path}")
        existing = read_json(completion_path)
        if existing.get("schema_version") != NODE_COMPLETE_SCHEMA or existing.get(
            "status"
        ) != "complete":
            raise ValueError("existing node completion is invalid")
        if existing.get("bundle_manifest_sha256") != bundle_manifest_sha256:
            raise ValueError("existing node completion bundle binding drift")
        existing_fingerprint = existing.get("runtime_fingerprint")
        if not isinstance(existing_fingerprint, Mapping):
            raise ValueError("existing node runtime fingerprint is missing")
        for key in ("simind_sha256", "environment_capture_sha256"):
            if existing_fingerprint.get(key) != runtime_fingerprint.get(key):
                raise ValueError(f"existing node runtime {key} drift")
        for case in node_case_specs(plan, args.node_id):
            case_id = validate_case_id(case.get("case_id"))
            _validate_completed_case(
                shared_node_root / case_id,
                case_id,
                bundle_manifest_sha256,
            )
        print(json.dumps({"status": "complete", "node_id": args.node_id, "reused": True}))
        return 0

    cases = node_case_specs(plan, args.node_id)
    actual_parallel = _bounded_parallelism(plan, args.max_parallel, len(cases))
    print(
        json.dumps(
            {
                "event": "worker_started",
                "node_id": args.node_id,
                "case_count": len(cases),
                "max_parallel": actual_parallel,
            }
        ),
        flush=True,
    )

    def execute(case: Mapping[str, Any]) -> Mapping[str, Any]:
        case_id = validate_case_id(case.get("case_id"))
        print(
            json.dumps(
                {
                    "event": "case_started",
                    "node_id": args.node_id,
                    "case_id": case_id,
                    "resume_candidate": (shared_node_root / case_id).exists(),
                }
            ),
            flush=True,
        )
        provenance = _run_case(
            bundle_root=bundle_root,
            shared_node_root=shared_node_root,
            local_root=local_root,
            simind_exe=simind_exe,
            runtime=runtime,
            runtime_fingerprint=runtime_fingerprint,
            bundle_manifest_sha256=bundle_manifest_sha256,
            case=case,
            resume=args.resume,
        )
        print(
            json.dumps(
                {
                    "event": "case_complete",
                    "node_id": args.node_id,
                    "case_id": case_id,
                    "elapsed_seconds": provenance["elapsed_seconds"],
                }
            ),
            flush=True,
        )
        return provenance

    provenances = _execute_cases_concurrently(cases, actual_parallel, execute)

    results = [
        {
            "case_id": provenance["case_id"],
            "fixture_group": provenance["fixture_group"],
            "elapsed_seconds": provenance["elapsed_seconds"],
            "run_provenance_relative_path": (
                f"nodes/{args.node_id}/{provenance['case_id']}/run_provenance.json"
            ),
            "run_provenance_sha256": sha256_file(
                shared_node_root / str(provenance["case_id"]) / "run_provenance.json"
            ),
            "output_artifacts": provenance["output_artifacts"],
        }
        for provenance in sorted(provenances, key=lambda item: str(item["case_id"]))
    ]
    completion = {
        "schema_version": NODE_COMPLETE_SCHEMA,
        "status": "complete",
        "node_id": args.node_id,
        "hostname": hostname,
        "bundle_manifest_sha256": bundle_manifest_sha256,
        "runtime_fingerprint": runtime_fingerprint,
        "max_parallel": actual_parallel,
        "case_count": len(results),
        "cases": results,
        "completed_utc": _utc_now(),
    }
    atomic_write_json(completion_path, completion)
    print(
        json.dumps(
            {
                "status": "complete",
                "node_id": args.node_id,
                "case_count": len(results),
                "completion": str(completion_path),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
