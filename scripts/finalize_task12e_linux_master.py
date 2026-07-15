"""Validate three immutable Linux node shards and build the Task 12E result archive."""

from __future__ import annotations

import argparse
import json
import os
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from task12e_linux_common import (
    ENVIRONMENT_SCHEMA,
    MASTER_SCHEMA,
    NODE_COMPLETE_SCHEMA,
    QUARTET_EXTENSIONS,
    atomic_write_json,
    node_case_specs,
    normalized_res_sha256,
    read_json,
    sha256_file,
    validate_bundle,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _verify_case(
    shared_root: Path,
    node_id: str,
    case_id: str,
    bundle_manifest_sha256: str,
) -> Mapping[str, Any]:
    case_dir = shared_root / "nodes" / node_id / case_id
    provenance_path = case_dir / "run_provenance.json"
    provenance = read_json(provenance_path)
    if provenance.get("status") != "complete" or provenance.get("case_id") != case_id:
        raise ValueError(f"invalid provenance for {node_id}/{case_id}")
    if provenance.get("bundle_manifest_sha256") != bundle_manifest_sha256:
        raise ValueError(f"bundle binding mismatch for {node_id}/{case_id}")
    artifacts = provenance.get("output_artifacts")
    if not isinstance(artifacts, Mapping):
        raise ValueError(f"missing output_artifacts for {node_id}/{case_id}")
    for extension in QUARTET_EXTENSIONS:
        path = case_dir / f"{case_id}.{extension}"
        record = artifacts.get(extension)
        if not path.is_file() or not isinstance(record, Mapping):
            raise FileNotFoundError(f"missing {node_id}/{case_id}.{extension}")
        if path.stat().st_size != int(record.get("size_bytes", -1)):
            raise ValueError(f"size mismatch {node_id}/{case_id}.{extension}")
        if sha256_file(path) != record.get("sha256"):
            raise ValueError(f"hash mismatch {node_id}/{case_id}.{extension}")
    if normalized_res_sha256(case_dir / f"{case_id}.res") != artifacts["res"].get(
        "normalized_sha256"
    ):
        raise ValueError(f"normalized RES mismatch for {node_id}/{case_id}")
    return provenance


def _build_archive(shared_root: Path, master_dir: Path) -> tuple[Path, str, int]:
    archive_path = master_dir / "task12e_linux_results.tar.gz"
    with tarfile.open(archive_path, "w:gz", compresslevel=6) as stream:
        stream.add(
            master_dir / "TASK12E_LINUX_MASTER.json",
            arcname="task12e_linux_results/TASK12E_LINUX_MASTER.json",
        )
        stream.add(
            shared_root / "LINUX_ENVIRONMENT.json",
            arcname="task12e_linux_results/LINUX_ENVIRONMENT.json",
        )
        stream.add(
            shared_root / "nodes",
            arcname="task12e_linux_results/nodes",
        )
    return archive_path, sha256_file(archive_path), archive_path.stat().st_size


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--shared-root", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    bundle_root = args.bundle_root.expanduser().resolve()
    shared_root = args.shared_root.expanduser().resolve()
    manifest = validate_bundle(bundle_root)
    bundle_manifest_sha256 = sha256_file(bundle_root / "BUNDLE_MANIFEST.json")
    plan = read_json(bundle_root / str(manifest["plan_relative_path"]))
    environment_path = shared_root / "LINUX_ENVIRONMENT.json"
    environment = read_json(environment_path)
    if environment.get("schema_version") != ENVIRONMENT_SCHEMA or environment.get(
        "status"
    ) != "pass":
        raise ValueError("Linux environment capture is not passing")
    expected_nodes = plan.get("expected_nodes")
    if not isinstance(expected_nodes, list) or len(expected_nodes) != 3:
        raise ValueError("Task 12E requires exactly three expected nodes")
    canonical = str(plan.get("canonical_projection_node"))
    master_dir = shared_root / "master"
    completion_path = master_dir / "TASK12E_LINUX_MASTER.json"
    if completion_path.exists():
        if not args.resume:
            raise FileExistsError(f"master completion exists; use --resume: {completion_path}")
        existing = read_json(completion_path)
        if existing.get("schema_version") != MASTER_SCHEMA or existing.get("status") != "pass":
            raise ValueError("existing master completion is invalid")
        archive_path = master_dir / "task12e_linux_results.tar.gz"
        if not archive_path.is_file():
            archive_path, archive_sha, archive_size = _build_archive(shared_root, master_dir)
            atomic_write_json(
                master_dir / "RESULT_ARCHIVE.json",
                {
                    "schema_version": "pars_v2_task12e_linux_result_archive_v1",
                    "archive": archive_path.name,
                    "sha256": archive_sha,
                    "size_bytes": archive_size,
                },
            )
        print(json.dumps({"status": "pass", "reused": True, "master": str(completion_path)}))
        return 0

    node_documents: dict[str, Mapping[str, Any]] = {}
    provenance_by_node: dict[str, dict[str, Mapping[str, Any]]] = {}
    for node in expected_nodes:
        node_id = str(node)
        completion = shared_root / "nodes" / node_id / "NODE_COMPLETE.json"
        document = read_json(completion)
        if document.get("schema_version") != NODE_COMPLETE_SCHEMA or document.get(
            "status"
        ) != "complete":
            raise ValueError(f"node {node_id} is not complete")
        if document.get("bundle_manifest_sha256") != bundle_manifest_sha256:
            raise ValueError(f"node {node_id} bundle binding mismatch")
        expected_cases = {
            str(case["case_id"]) for case in node_case_specs(plan, node_id)
        }
        observed_cases = {
            str(case.get("case_id"))
            for case in document.get("cases", [])
            if isinstance(case, Mapping)
        }
        if observed_cases != expected_cases:
            raise ValueError(f"node {node_id} case coverage mismatch")
        node_documents[node_id] = document
        provenance_by_node[node_id] = {
            case_id: _verify_case(
                shared_root, node_id, case_id, bundle_manifest_sha256
            )
            for case_id in sorted(expected_cases)
        }

    runtime_fingerprints = {
        node: document.get("runtime_fingerprint")
        for node, document in node_documents.items()
    }
    expected_simind = plan.get("expected_linux_simind_sha256")
    environment_capture_sha256 = sha256_file(environment_path)
    dependency_sets: list[Mapping[str, Any]] = []
    for node, fingerprint in runtime_fingerprints.items():
        if not isinstance(fingerprint, Mapping):
            raise ValueError(f"node {node} runtime fingerprint missing")
        if fingerprint.get("simind_sha256") != expected_simind:
            raise ValueError(f"node {node} SIMIND hash mismatch")
        if fingerprint.get("environment_capture_sha256") != environment_capture_sha256:
            raise ValueError(f"node {node} environment capture binding mismatch")
        dependencies = fingerprint.get("dependency_hashes")
        if not isinstance(dependencies, Mapping):
            raise ValueError(f"node {node} dependency hashes missing")
        dependency_sets.append(dependencies)
    if any(value != dependency_sets[0] for value in dependency_sets[1:]):
        raise ValueError("Linux nodes do not share identical dynamic dependency hashes")

    clinical_ids = plan.get("clinical_case_ids")
    if not isinstance(clinical_ids, list) or len(clinical_ids) != 3:
        raise ValueError("bound clinical case ids must contain three cases")
    cross_node_cases: list[dict[str, object]] = []
    for raw_case_id in clinical_ids:
        case_id = str(raw_case_id)
        hashes_by_extension: dict[str, dict[str, str]] = {}
        for extension in ("a00", "mhd", "spe"):
            values = {
                node: str(
                    provenance_by_node[node][case_id]["output_artifacts"][extension][
                        "sha256"
                    ]
                )
                for node in expected_nodes
            }
            if len(set(values.values())) != 1:
                raise ValueError(f"cross-node {case_id}.{extension} bytes differ")
            hashes_by_extension[extension] = values
        normalized_res = {
            node: str(
                provenance_by_node[node][case_id]["output_artifacts"]["res"][
                    "normalized_sha256"
                ]
            )
            for node in expected_nodes
        }
        if len(set(normalized_res.values())) != 1:
            raise ValueError(f"cross-node normalized {case_id}.res differs")
        cross_node_cases.append(
            {
                "case_id": case_id,
                "exact_hashes_by_extension": hashes_by_extension,
                "normalized_res_sha256_by_node": normalized_res,
                "status": "pass",
            }
        )

    coordinate_ids = plan.get("coordinate_case_ids")
    if not isinstance(coordinate_ids, list) or len(coordinate_ids) != 3:
        raise ValueError("bound coordinate case ids must contain three cases")
    for case_id in coordinate_ids:
        if str(case_id) not in provenance_by_node[canonical]:
            raise ValueError(f"canonical coordinate fixture missing: {case_id}")

    master = {
        "schema_version": MASTER_SCHEMA,
        "status": "pass",
        "generated_utc": _utc_now(),
        "bundle_manifest_sha256": bundle_manifest_sha256,
        "environment_capture_sha256": environment_capture_sha256,
        "expected_linux_simind_sha256": expected_simind,
        "expected_nodes": expected_nodes,
        "canonical_projection_node": canonical,
        "node_completion_sha256": {
            node: sha256_file(shared_root / "nodes" / node / "NODE_COMPLETE.json")
            for node in expected_nodes
        },
        "runtime_dependency_hashes": dependency_sets[0],
        "cross_node_byte_gate": {
            "status": "pass",
            "cases": cross_node_cases,
            "exact_artifacts": ["a00", "mhd", "spe"],
            "res_policy": "dynamic runtime lines ignored",
        },
        "coordinate_fixture_gate": {
            "status": "complete_awaiting_local_coordinate_search",
            "node_id": canonical,
            "case_ids": coordinate_ids,
        },
        "go_for_50_case_generation": False,
        "next_action": "download archive and run local Task 12E projection finalizer",
    }
    master_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(completion_path, master)
    archive_path, archive_sha, archive_size = _build_archive(shared_root, master_dir)
    archive_document = {
        "schema_version": "pars_v2_task12e_linux_result_archive_v1",
        "archive": archive_path.name,
        "sha256": archive_sha,
        "size_bytes": archive_size,
    }
    atomic_write_json(master_dir / "RESULT_ARCHIVE.json", archive_document)
    (master_dir / f"{archive_path.name}.sha256").write_text(
        f"{archive_sha}  {archive_path.name}\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "pass",
                "master": str(completion_path),
                "archive": str(archive_path),
                "archive_sha256": archive_sha,
                "go_for_50_case_generation": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
