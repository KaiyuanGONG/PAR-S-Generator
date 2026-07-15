"""Run one fail-closed Linux SIMIND fixture before releasing any node worker."""

from __future__ import annotations

import argparse
import array
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from task12e_linux_common import (
    EXPECTED_A00_BYTES,
    EXPECTED_PROJECTION_SHAPE,
    QUARTET_EXTENSIONS,
    SMOKE_SCHEMA,
    atomic_write_json,
    directory_manifest,
    read_json,
    sha256_file,
    validate_bundle,
    validate_case_id,
    validate_node_id,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _audit_a00(path: Path) -> dict[str, object]:
    if path.stat().st_size != EXPECTED_A00_BYTES:
        raise ValueError(
            f"projection size {path.stat().st_size} != {EXPECTED_A00_BYTES}"
        )
    values = array.array("f")
    with path.open("rb") as stream:
        values.fromfile(stream, EXPECTED_A00_BYTES // 4)
    if sys.byteorder != "little":
        values.byteswap()
    if not all(math.isfinite(float(value)) for value in values):
        raise ValueError("projection contains non-finite values")
    if not all(float(value) >= 0 for value in values):
        raise ValueError("projection contains negative values")
    return {
        "shape_vvu": list(EXPECTED_PROJECTION_SHAPE),
        "size_bytes": path.stat().st_size,
        "minimum": min(float(value) for value in values),
        "maximum": max(float(value) for value in values),
        "sum": math.fsum(float(value) for value in values),
        "sha256": sha256_file(path),
    }


def _case_spec(plan: Mapping[str, Any], case_id: str) -> Mapping[str, Any]:
    cases = plan.get("cases")
    if not isinstance(cases, list):
        raise ValueError("bound plan cases are missing")
    matches = [
        case
        for case in cases
        if isinstance(case, Mapping) and case.get("case_id") == case_id
    ]
    if len(matches) != 1:
        raise ValueError(f"smoke case must resolve exactly once: {case_id}")
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--shared-root", type=Path, required=True)
    parser.add_argument("--simind-exe", type=Path, required=True)
    parser.add_argument("--smc-dir", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--case-id", default="coord_spots_001")
    parser.add_argument("--development-allow-noncanonical-host", action="store_true")
    args = parser.parse_args()

    bundle_root = args.bundle_root.expanduser().resolve()
    manifest = validate_bundle(bundle_root)
    bundle_manifest_sha256 = sha256_file(bundle_root / "BUNDLE_MANIFEST.json")
    plan = read_json(bundle_root / str(manifest["plan_relative_path"]))
    canonical_node = str(plan.get("canonical_projection_node"))
    hostname = socket.gethostname()
    canonical_hostname_verified = True
    try:
        validate_node_id(plan, canonical_node, hostname)
    except ValueError:
        if not args.development_allow_noncanonical_host:
            raise
        canonical_hostname_verified = False
    runtime = plan.get("runtime")
    if not isinstance(runtime, Mapping):
        raise ValueError("bound runtime contract is missing")
    simind_runtime = runtime.get("linux_simind_runtime")
    if not isinstance(simind_runtime, Mapping):
        raise ValueError("bound Linux SIMIND runtime contract is missing")

    simind_exe = args.simind_exe.expanduser().resolve()
    expected_simind = str(plan.get("expected_linux_simind_sha256"))
    if not simind_exe.is_file() or sha256_file(simind_exe) != expected_simind:
        raise ValueError("Linux SIMIND binary hash mismatch")
    smc_dir = args.smc_dir.expanduser().resolve()
    smc_records, smc_manifest_sha256 = directory_manifest(smc_dir)
    if len(smc_records) != int(simind_runtime.get("smc_dir_file_count", -1)):
        raise ValueError("SMC_DIR file-count mismatch")
    if sum(int(item["size_bytes"]) for item in smc_records) != int(
        simind_runtime.get("smc_dir_total_size_bytes", -1)
    ):
        raise ValueError("SMC_DIR total-size mismatch")
    if smc_manifest_sha256 != simind_runtime.get("smc_dir_manifest_sha256"):
        raise ValueError("SMC_DIR content-manifest mismatch")

    case_id = validate_case_id(args.case_id)
    case = _case_spec(plan, case_id)
    inputs = case.get("inputs")
    if not isinstance(inputs, Mapping):
        raise ValueError("smoke fixture inputs are missing")
    work_root = args.work_root.expanduser().resolve()
    if work_root.exists():
        raise FileExistsError(f"smoke work root already exists: {work_root}")
    work_root.mkdir(parents=True)
    shared_root = args.shared_root.expanduser().resolve()
    completion_path = shared_root / "LINUX_SMOKE_COMPLETE.json"
    if completion_path.exists():
        raise FileExistsError(f"smoke completion already exists: {completion_path}")

    smc = bundle_root / str(runtime.get("smc_relative_path"))
    ini = bundle_root / str(runtime.get("simind_ini_relative_path"))
    source = bundle_root / str(inputs.get("source_relative_path"))
    density = bundle_root / str(inputs.get("density_relative_path"))
    copies = (
        (smc, work_root / smc.name),
        (ini, work_root / "simind.ini"),
        (source, work_root / f"{case_id}_act_av.bin"),
        (density, work_root / f"{case_id}_atn_av.bin"),
    )
    expected_hashes = (
        (smc, runtime.get("smc_sha256"), "smc"),
        (ini, runtime.get("simind_ini_sha256"), "simind.ini"),
        (source, inputs.get("source_sha256"), "source"),
        (density, inputs.get("density_sha256"), "density"),
    )
    for path, expected_hash, label in expected_hashes:
        if not path.is_file() or sha256_file(path) != expected_hash:
            raise ValueError(f"smoke {label} input hash mismatch")
    for origin, destination in copies:
        if not origin.is_file():
            raise FileNotFoundError(origin)
        shutil.copy2(origin, destination)

    command = [
        str(simind_exe),
        smc.stem,
        case_id,
        f"/FS:{case_id}",
        f"/FD:{case_id}",
        f"/NN:{int(case.get('nn_multiplier'))}",
        f"/RR:{int(case.get('rr_seed'))}",
    ]
    environment = os.environ.copy()
    environment["SMC_DIR"] = f"{smc_dir}{os.sep}"
    started_utc = _utc_now()
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=work_root,
        env=environment,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=float(runtime.get("timeout_seconds", 7200)),
        check=False,
    )
    (work_root / "stdout.log").write_text(completed.stdout, encoding="utf-8")
    (work_root / "stderr.log").write_text(completed.stderr, encoding="utf-8")
    observed_files = sorted(path.name for path in work_root.iterdir() if path.is_file())
    if completed.returncode != 0:
        raise RuntimeError(
            f"smoke SIMIND exit={completed.returncode}; work_root={work_root}"
        )
    artifacts: dict[str, dict[str, object]] = {}
    for extension in QUARTET_EXTENSIONS:
        path = work_root / f"{case_id}.{extension}"
        if not path.is_file() or path.stat().st_size <= 0:
            raise FileNotFoundError(
                f"smoke output missing: {path}; observed={observed_files}; "
                f"work_root={work_root}"
            )
        artifacts[extension] = {
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    a00_audit = _audit_a00(work_root / f"{case_id}.a00")
    document = {
        "schema_version": SMOKE_SCHEMA,
        "status": "pass",
        "case_id": case_id,
        "hostname": hostname,
        "canonical_node_id": canonical_node,
        "canonical_hostname_verified": canonical_hostname_verified,
        "development_override": args.development_allow_noncanonical_host,
        "started_utc": started_utc,
        "finished_utc": _utc_now(),
        "elapsed_seconds": time.monotonic() - started,
        "bundle_manifest_sha256": bundle_manifest_sha256,
        "simind_executable": str(simind_exe),
        "simind_sha256": expected_simind,
        "smc_dir": str(smc_dir),
        "smc_dir_environment_value": environment["SMC_DIR"],
        "smc_dir_file_count": len(smc_records),
        "smc_dir_manifest_sha256": smc_manifest_sha256,
        "command": command,
        "return_code": completed.returncode,
        "observed_files": observed_files,
        "artifacts": artifacts,
        "a00_audit": a00_audit,
        "work_root": str(work_root),
    }
    atomic_write_json(completion_path, document)
    atomic_write_json(work_root / "LINUX_SMOKE_COMPLETE.json", document)
    print(
        json.dumps(
            {
                "status": "pass",
                "case_id": case_id,
                "elapsed_seconds": document["elapsed_seconds"],
                "output": str(completion_path),
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
