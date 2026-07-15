"""Validate the uploaded Task 12F bundle and shared run root without SIMIND."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import scipy
import skimage


SCRIPT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_ROOT))

from task12f_linux50_common import (  # noqa: E402
    REMOTE_PREFLIGHT_SCHEMA,
    atomic_write_json,
    cases_for_node,
    directory_manifest,
    load_plan,
    read_json,
    sha256_file,
    validate_bundle,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--shared-root", type=Path, required=True)
    parser.add_argument("--environment-prefix", type=Path, required=True)
    parser.add_argument("--simind-exe", type=Path, required=True)
    parser.add_argument("--smc-dir", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    bundle = args.bundle_root.resolve()
    shared = args.shared_root.resolve()
    validate_bundle(bundle)
    plan = load_plan(bundle)
    bundle_sha = sha256_file(bundle / "BUNDLE_MANIFEST.json")
    marker = shared / "REMOTE_PREFLIGHT.json"
    if marker.is_file():
        if not args.resume:
            raise FileExistsError(f"remote preflight exists; use --resume: {marker}")
        existing = read_json(marker)
        if (
            existing.get("schema_version") != REMOTE_PREFLIGHT_SCHEMA
            or existing.get("status") != "pass"
            or existing.get("bundle_manifest_sha256") != bundle_sha
        ):
            raise RuntimeError("existing remote preflight is invalid")
        print(json.dumps({"status": "pass", "reused": True, "marker": str(marker)}))
        return 0
    if shared.exists() and any(shared.iterdir()):
        raise RuntimeError("non-empty shared root has no valid remote preflight marker")
    shared.mkdir(parents=True, exist_ok=True)

    expected = plan["linux_runtime"]
    prefix = args.environment_prefix.resolve()
    if Path(sys.prefix).resolve() != prefix:
        raise RuntimeError(
            f"wrong Python prefix: expected={prefix} actual={Path(sys.prefix).resolve()}"
        )
    versions = {
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "scikit-image": skimage.__version__,
    }
    if sys.version.split()[0] != expected["python_version"]:
        raise RuntimeError("Python version mismatch")
    if versions != dict(expected["critical_packages"]):
        raise RuntimeError(f"critical package mismatch: {versions}")
    simind = args.simind_exe.resolve()
    if not simind.is_file() or sha256_file(simind) != expected["simind_sha256"]:
        raise RuntimeError("Linux SIMIND hash mismatch")
    smc_records, smc_sha = directory_manifest(args.smc_dir.resolve())
    if len(smc_records) != int(expected["smc_dir_file_count"]):
        raise RuntimeError("SMC_DIR file count mismatch")
    if smc_sha != expected["smc_dir_manifest_sha256"]:
        raise RuntimeError("SMC_DIR manifest mismatch")
    if shutil.disk_usage(shared).free < int(expected["minimum_shared_free_bytes"]):
        raise RuntimeError("shared filesystem free space is below the frozen minimum")

    acceptance = read_json(bundle / "evidence" / "task12e_manual_acceptance.json")
    if acceptance.get("release", {}).get("go_for_50_case_generation") is not True:
        raise RuntimeError("bundled Task 12E acceptance does not release 50 cases")
    cases = plan.get("cases")
    if not isinstance(cases, list) or len(cases) != 50:
        raise RuntimeError("Task 12F plan must contain exactly 50 cases")
    rr_values = [int(case["rr_seed"]) for case in cases]
    if len(set(rr_values)) != 50:
        raise RuntimeError("Task 12F /RR values are not unique")
    expected_counts = {"cnc5": 17, "cnc7": 17, "cnc8": 16}
    observed_counts = {
        node: len(cases_for_node(plan, node)) for node in plan["expected_nodes"]
    }
    if observed_counts != expected_counts:
        raise RuntimeError(f"node assignment mismatch: {observed_counts}")

    document = {
        "schema_version": REMOTE_PREFLIGHT_SCHEMA,
        "status": "pass",
        "generated_utc": _utc_now(),
        "bundle_manifest_sha256": bundle_sha,
        "case_count": 50,
        "case_count_by_node": observed_counts,
        "rr_unique": True,
        "environment": {
            "prefix": str(prefix),
            "python": sys.version.split()[0],
            "critical_packages": versions,
        },
        "simind_sha256": sha256_file(simind),
        "smc_dir_file_count": len(smc_records),
        "smc_dir_manifest_sha256": smc_sha,
        "shared_free_bytes": shutil.disk_usage(shared).free,
        "simind_launched": False,
    }
    atomic_write_json(marker, document)
    print(json.dumps({"status": "pass", "marker": str(marker), "case_count": 50}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
