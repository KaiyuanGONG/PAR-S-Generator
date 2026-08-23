"""Validate and freeze one finalized Windows v1 run as compact JSON evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


EXPECTED_PROFILE = "hybrid_v2_limited_activity_v1"
EXPECTED_BACKEND = "windows_native"
EXPECTED_TRANSFORM = "raw[:,::-1,:]"
EXPECTED_VOLUME_BYTES = 128**3 * 4
EXPECTED_PROJECTION_BYTES = 60 * 128 * 128 * 4


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def checked_artifact(
    run_root: Path,
    relative_path: str,
    expected_sha256: str,
    *,
    expected_bytes: int | None = None,
) -> dict[str, Any]:
    root = run_root.resolve()
    path = (root / relative_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"Artifact escapes run root: {relative_path}") from exc
    if not path.is_file():
        raise RuntimeError(f"Missing artifact: {relative_path}")
    size = path.stat().st_size
    if expected_bytes is not None and size != expected_bytes:
        raise RuntimeError(f"Unexpected artifact size for {relative_path}: {size}")
    actual_sha256 = sha256_file(path)
    if actual_sha256 != expected_sha256:
        raise RuntimeError(f"Artifact hash mismatch: {relative_path}")
    return {"path": relative_path, "bytes": size, "sha256": actual_sha256}


def freeze_run(
    run_root: Path,
    *,
    source_commit: str,
    config_path: Path | None = None,
) -> dict[str, Any]:
    run_root = run_root.resolve()
    run = read_json(run_root / "run.json")
    manifest_path = run_root / "dataset_manifest.json"
    manifest = read_json(manifest_path)
    manifest_sha256 = sha256_file(manifest_path)

    if not run.get("finalized"):
        raise RuntimeError("Windows v1 freeze requires a finalized run")
    if run.get("package_sha256") != manifest_sha256:
        raise RuntimeError("run.json package hash does not match dataset_manifest.json")
    if manifest.get("schema_version") != "windows_v1":
        raise RuntimeError("Not a Windows v1 manifest")
    if manifest.get("generation_profile") != EXPECTED_PROFILE:
        raise RuntimeError("Unexpected generation profile")
    if manifest.get("runtime_backend") != EXPECTED_BACKEND:
        raise RuntimeError("Unexpected runtime backend")
    if "observation" in run.get("stages", {}):
        raise RuntimeError("Windows v1 must not enter an observation stage")
    if manifest.get("observation_contract", {}).get("enabled") is not False:
        raise RuntimeError("Windows v1 observation output must be disabled")

    inventory = manifest.get("files", [])
    for entry in inventory:
        checked = checked_artifact(
            run_root,
            entry["path"],
            entry["sha256"],
            expected_bytes=int(entry["bytes"]),
        )
        if checked["bytes"] != entry["bytes"]:
            raise RuntimeError(f"Inventory size mismatch: {entry['path']}")

    cases = read_jsonl(run_root / "cases.jsonl")
    jobs = {item["case_id"]: item for item in read_json(run_root / "logs" / "simind_jobs.json")}
    frozen_cases: list[dict[str, Any]] = []
    for case in cases:
        case_id = case["case_id"]
        if "observation" in case or "observation" in case.get("qc", {}):
            raise RuntimeError(f"Windows v1 case contains observation output: {case_id}")
        projection_qc_ref = case.get("qc", {}).get("projection", {})
        if projection_qc_ref.get("status") != "passed":
            raise RuntimeError(f"Projection QC did not pass: {case_id}")
        projection_qc_artifact = checked_artifact(
            run_root,
            projection_qc_ref["relpath"],
            projection_qc_ref["sha256"],
        )
        projection_qc = read_json(run_root / projection_qc_ref["relpath"])
        runtime_hashes = projection_qc.get("runtime_hashes", {})
        if not (
            runtime_hashes.get("before")
            == runtime_hashes.get("after")
            == runtime_hashes.get("expected")
        ):
            raise RuntimeError(f"Runtime hashes drifted: {case_id}")
        if projection_qc.get("canonical_transform") != EXPECTED_TRANSFORM:
            raise RuntimeError(f"Unexpected projection transform: {case_id}")
        if projection_qc.get("exit_code") != 0 or projection_qc.get("failures"):
            raise RuntimeError(f"SIMIND or projection QC failed: {case_id}")

        phantom = case["phantom"]
        simind_input = case["simind_input"]
        expectation = case["expectation"]
        job = jobs[case_id]
        command = job["command"]
        required_tokens = {
            "/NN:10",
            f"/RR:{expectation['rr_seed']}",
            "/IN:x21,100x/25:1704/100:160/101:208",
        }
        if not required_tokens.issubset(command):
            raise RuntimeError(f"SIMIND command contract mismatch: {case_id}")

        frozen_cases.append(
            {
                "case_id": case_id,
                "case_role": case["case_role"],
                "split_role": case["split_role"],
                "seed": case["seed"],
                "rr_seed": expectation["rr_seed"],
                "command": command,
                "phantom_npz": checked_artifact(
                    run_root, phantom["npz_relpath"], phantom["npz_sha256"]
                ),
                "phantom_meta": checked_artifact(
                    run_root, phantom["meta_relpath"], phantom["meta_sha256"]
                ),
                "act": checked_artifact(
                    run_root,
                    simind_input["activity_relpath"],
                    simind_input["activity_sha256"],
                    expected_bytes=EXPECTED_VOLUME_BYTES,
                ),
                "atn": checked_artifact(
                    run_root,
                    simind_input["attenuation_relpath"],
                    simind_input["attenuation_sha256"],
                    expected_bytes=EXPECTED_VOLUME_BYTES,
                ),
                "expectation_a00": checked_artifact(
                    run_root,
                    expectation["a00_relpath"],
                    expectation["a00_sha256"],
                    expected_bytes=EXPECTED_PROJECTION_BYTES,
                ),
                "expectation_res": checked_artifact(
                    run_root, expectation["res_relpath"], expectation["res_sha256"]
                ),
                "projection_qc_artifact": projection_qc_artifact,
                "projection_qc": {
                    "status": projection_qc["status"],
                    "canonical_transform": projection_qc["canonical_transform"],
                    "res_command_tokens_matched": projection_qc["res_command_tokens_matched"],
                    "res_completion_marker": projection_qc["res_completion_marker"],
                    "metrics": projection_qc["metrics"],
                    "runtime_hashes": runtime_hashes,
                },
            }
        )

    config_evidence = None
    if config_path is not None:
        config_path = config_path.resolve()
        config_evidence = {
            "path": str(config_path),
            "bytes": config_path.stat().st_size,
            "sha256": sha256_file(config_path),
        }

    return {
        "evidence_schema": "windows_v1_behavior_freeze_v1",
        "source_commit": source_commit,
        "run_id": run["run_id"],
        "run_root": str(run_root),
        "finalized_utc": run["finalized_utc"],
        "dataset_manifest_sha256": manifest_sha256,
        "effective_config_sha256": manifest["effective_config_sha256"],
        "config_file": config_evidence,
        "schema_version": manifest["schema_version"],
        "generation_profile": manifest["generation_profile"],
        "runtime_backend": manifest["runtime_backend"],
        "windows_v1": manifest["windows_v1"],
        "scientific_authority": manifest["scientific_authority"],
        "windows_runtime": manifest["windows_runtime"],
        "windows_platform": manifest["windows_platform"],
        "software_sha256": run["provenance"]["software_sha256"],
        "inventory_file_count": len(inventory),
        "stages": {name: stage["status"] for name, stage in run["stages"].items()},
        "cases": frozen_cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    evidence = freeze_run(
        args.run_root,
        source_commit=args.source_commit,
        config_path=args.config,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
