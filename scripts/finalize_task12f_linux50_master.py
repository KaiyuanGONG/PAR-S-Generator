"""Validate all three Task 12F shards and package the 50 Linux projections."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


SCRIPT_ROOT = Path(__file__).resolve().parent
BUNDLE_ROOT_DEFAULT = SCRIPT_ROOT.parent
sys.path.insert(0, str(SCRIPT_ROOT))
sys.path.insert(0, str(BUNDLE_ROOT_DEFAULT / "src"))

from task12f_linux50_common import (  # noqa: E402
    CASE_SCHEMA,
    MASTER_SCHEMA,
    NODE_COMPLETE_SCHEMA,
    QUARTET_EXTENSIONS,
    REMOTE_PREFLIGHT_SCHEMA,
    atomic_write_json,
    cases_for_node,
    load_plan,
    read_json,
    sha256_file,
    validate_bundle,
)
from core.simind_postprocess import audit_simind_completion  # noqa: E402


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _switch(command: object, prefix: str) -> int:
    if not isinstance(command, list):
        raise ValueError("SIMIND command must be a list")
    values = [str(value)[len(prefix) :] for value in command if str(value).startswith(prefix)]
    if len(values) != 1:
        raise ValueError(f"SIMIND command requires exactly one {prefix} switch")
    return int(values[0])


def _validate_case(
    *,
    case_dir: Path,
    case: Mapping[str, Any],
    node_id: str,
    bundle_sha: str,
    expected_simind_sha: str,
) -> Mapping[str, object]:
    case_id = str(case["case_id"])
    marker = read_json(case_dir / "TASK12F_CASE.json")
    if (
        marker.get("schema_version") != CASE_SCHEMA
        or marker.get("status") != "complete"
        or marker.get("case_id") != case_id
        or marker.get("node_id") != node_id
        or marker.get("bundle_manifest_sha256") != bundle_sha
    ):
        raise ValueError(f"invalid Task 12F marker for {case_id}")
    artifacts = marker.get("output_artifacts")
    if not isinstance(artifacts, Mapping):
        raise ValueError(f"{case_id} artifact records missing")
    for extension in QUARTET_EXTENSIONS:
        path = case_dir / f"{case_id}.{extension}"
        record = artifacts.get(extension)
        if not path.is_file() or not isinstance(record, Mapping):
            raise FileNotFoundError(path)
        if path.stat().st_size != int(record.get("size_bytes", -1)):
            raise ValueError(f"{case_id}.{extension} size mismatch")
        if sha256_file(path) != record.get("sha256"):
            raise ValueError(f"{case_id}.{extension} hash mismatch")
    provenance_path = case_dir / "run_provenance.json"
    provenance = read_json(provenance_path)
    if marker.get("simind_provenance_sha256") != sha256_file(provenance_path):
        raise ValueError(f"{case_id} SIMIND provenance hash mismatch")
    if provenance.get("status") != "complete" or provenance.get("exit_code") != 0:
        raise ValueError(f"{case_id} SIMIND provenance is incomplete")
    if provenance.get("binary_sha256") != expected_simind_sha:
        raise ValueError(f"{case_id} Linux SIMIND hash mismatch")
    if int(provenance.get("rr_seed", -1)) != int(case["rr_seed"]):
        raise ValueError(f"{case_id} /RR provenance mismatch")
    if int(provenance.get("nn_multiplier", -1)) != int(case["nn_multiplier"]):
        raise ValueError(f"{case_id} /NN provenance mismatch")
    if _switch(provenance.get("command"), "/RR:") != int(case["rr_seed"]):
        raise ValueError(f"{case_id} /RR command mismatch")
    if _switch(provenance.get("command"), "/NN:") != int(case["nn_multiplier"]):
        raise ValueError(f"{case_id} /NN command mismatch")
    inputs = provenance.get("inputs")
    if not isinstance(inputs, Mapping):
        raise ValueError(f"{case_id} input provenance missing")
    expected_inputs = case["inputs"]
    if inputs.get("source_sha256") != expected_inputs["source_sha256"]:
        raise ValueError(f"{case_id} source binding mismatch")
    if inputs.get("density_sha256") != expected_inputs["density_sha256"]:
        raise ValueError(f"{case_id} density binding mismatch")
    audit = audit_simind_completion(
        case_dir / case_id,
        expected_shape=(60, 128, 128),
        exit_code=0,
    )
    return {
        "case_id": case_id,
        "node_id": node_id,
        "split": case["split"],
        "rr_seed": int(case["rr_seed"]),
        "projection_sum": audit.projection_sum,
        "a00_sha256": audit.sha256["a00"],
        "case_marker_sha256": sha256_file(case_dir / "TASK12F_CASE.json"),
        "simind_provenance_sha256": sha256_file(provenance_path),
        "status": "pass",
    }


def _archive(shared_root: Path, master_dir: Path) -> tuple[Path, str]:
    path = master_dir / "task12f_linux50_results.tar.gz"
    with tarfile.open(path, "w:gz", compresslevel=6) as stream:
        stream.add(
            master_dir / "TASK12F_LINUX50_MASTER.json",
            arcname="task12f_linux50_results/TASK12F_LINUX50_MASTER.json",
        )
        stream.add(
            shared_root / "REMOTE_PREFLIGHT.json",
            arcname="task12f_linux50_results/REMOTE_PREFLIGHT.json",
        )
        stream.add(
            shared_root / "nodes",
            arcname="task12f_linux50_results/nodes",
        )
    return path, sha256_file(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--shared-root", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    bundle = args.bundle_root.resolve()
    shared = args.shared_root.resolve()
    validate_bundle(bundle)
    plan = load_plan(bundle)
    bundle_sha = sha256_file(bundle / "BUNDLE_MANIFEST.json")
    preflight = read_json(shared / "REMOTE_PREFLIGHT.json")
    if (
        preflight.get("schema_version") != REMOTE_PREFLIGHT_SCHEMA
        or preflight.get("status") != "pass"
        or preflight.get("bundle_manifest_sha256") != bundle_sha
    ):
        raise RuntimeError("remote preflight is not passing")
    master_dir = shared / "master"
    completion = master_dir / "TASK12F_LINUX50_MASTER.json"
    if completion.exists():
        if not args.resume:
            raise FileExistsError(f"master exists; use --resume: {completion}")
        existing = read_json(completion)
        if existing.get("schema_version") != MASTER_SCHEMA or existing.get("status") != "pass":
            raise RuntimeError("existing master is invalid")
        archive_path = master_dir / "task12f_linux50_results.tar.gz"
        print(json.dumps({"status": "pass", "reused": True, "archive": str(archive_path)}))
        return 0

    results = []
    observed_ids: set[str] = set()
    runtime_hashes: dict[str, Mapping[str, object]] = {}
    for node_id in plan["expected_nodes"]:
        node_root = shared / "nodes" / str(node_id)
        node_complete_path = node_root / "NODE_COMPLETE.json"
        node_complete = read_json(node_complete_path)
        expected_cases = cases_for_node(plan, str(node_id))
        expected_ids = sorted(str(case["case_id"]) for case in expected_cases)
        if (
            node_complete.get("schema_version") != NODE_COMPLETE_SCHEMA
            or node_complete.get("status") != "complete"
            or node_complete.get("bundle_manifest_sha256") != bundle_sha
            or node_complete.get("case_ids") != expected_ids
            or int(node_complete.get("case_count", -1)) != len(expected_ids)
        ):
            raise ValueError(f"node completion mismatch: {node_id}")
        expected_parallel = int(plan["execution"]["requested_parallel_by_node"][node_id])
        if int(node_complete.get("max_parallel", -1)) != expected_parallel:
            raise ValueError(f"node parallelism mismatch: {node_id}")
        runtime = node_complete.get("runtime_fingerprint")
        if not isinstance(runtime, Mapping):
            raise ValueError(f"node runtime fingerprint missing: {node_id}")
        runtime_hashes[str(node_id)] = runtime
        for case in expected_cases:
            case_id = str(case["case_id"])
            if case_id in observed_ids:
                raise ValueError(f"duplicate case result: {case_id}")
            observed_ids.add(case_id)
            results.append(
                _validate_case(
                    case_dir=node_root / "cases" / case_id,
                    case=case,
                    node_id=str(node_id),
                    bundle_sha=bundle_sha,
                    expected_simind_sha=str(plan["linux_runtime"]["simind_sha256"]),
                )
            )
    planned_ids = {str(case["case_id"]) for case in plan["cases"]}
    if observed_ids != planned_ids or len(observed_ids) != 50:
        raise ValueError("master result set is not the exact frozen 50 cases")
    results.sort(key=lambda item: str(item["case_id"]))
    master = {
        "schema_version": MASTER_SCHEMA,
        "status": "pass",
        "generated_utc": _utc_now(),
        "bundle_manifest_sha256": bundle_sha,
        "dataset": dict(plan["dataset"]),
        "case_count": 50,
        "node_case_counts": {
            str(node): len(cases_for_node(plan, str(node)))
            for node in plan["expected_nodes"]
        },
        "runtime_fingerprints": runtime_hashes,
        "projection_sum_summary": {
            "minimum": min(float(item["projection_sum"]) for item in results),
            "maximum": max(float(item["projection_sum"]) for item in results),
            "mean": sum(float(item["projection_sum"]) for item in results) / 50.0,
        },
        "cases": results,
        "go_for_local_case_writer_and_dataset_freeze": False,
        "next_action": "download archive and run the bound local case-writer/freeze gate",
    }
    master_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(completion, master)
    archive_path, archive_sha = _archive(shared, master_dir)
    (master_dir / f"{archive_path.name}.sha256").write_text(
        f"{archive_sha}  {archive_path.name}\n", encoding="utf-8", newline="\n"
    )
    print(
        json.dumps(
            {
                "status": "pass",
                "master": str(completion),
                "archive": str(archive_path),
                "archive_sha256": archive_sha,
                "case_count": 50,
                "go_for_local_case_writer_and_dataset_freeze": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
