"""Build one immutable upload bundle for Task 12E Linux homologation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from task12e_linux_common import (  # noqa: E402
    BUNDLE_SCHEMA,
    PLAN_SCHEMA,
    atomic_write_json,
    read_json,
    sha256_file,
    validate_bundle,
)


DEFAULT_TASK12D_ROOT = Path(r"D:\PFE-U\PAR\outputs\pars_v2_task12d3")
DEFAULT_TASK12D_WORK = Path(r"D:\PFE-U\PAR\outputs\pars_v2_task12d3_work")
DEFAULT_TASK12D_QA = Path(r"D:\PFE-U\PAR\outputs\pars_v2_task12d3_qa")
DEFAULT_COORDINATE_ROOT = Path(r"D:\PFE-U\PAR\outputs\projection_coordinate_fixtures_v2")
DEFAULT_OUTPUT_ROOT = Path(r"D:\PFE-U\PAR\outputs\task12e_linux_upload_v2")
BUNDLE_NAME = "pars_v2_task12e_linux_bundle_v2"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _git_value(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _source_binding(allow_dirty: bool) -> dict[str, object]:
    status = _git_value("status", "--short")
    if status and not allow_dirty:
        raise RuntimeError("formal Task 12E bundle requires a clean Generator worktree")
    return {
        "git_commit": _git_value("rev-parse", "HEAD"),
        "git_tree": _git_value("rev-parse", "HEAD^{tree}"),
        "worktree_clean": not bool(status),
        "dirty_status": status,
        "formal_eligible": not bool(status) and not allow_dirty,
    }


def _copy(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _verify_task12d_bindings(
    *,
    acceptance: Mapping[str, Any],
    task12d_root: Path,
    task12d_work: Path,
    task12d_qa: Path,
) -> Mapping[str, Any]:
    bindings = acceptance.get("evidence_bindings")
    if not isinstance(bindings, Mapping):
        raise ValueError("Task 12D acceptance evidence bindings are missing")
    paths = {
        "dataset_complete_sha256": task12d_root / "DATASET_COMPLETE.json",
        "dataset_manifest_sha256": task12d_root / "case_manifest.jsonl",
        "progress_sha256": task12d_work / "PROGRESS.json",
        "task12b_gate_summary_sha256": task12d_qa / "task12b_gate_summary.json",
        "task12d_complete_sha256": task12d_qa / "TASK12D_COMPLETE.json",
    }
    for key, path in paths.items():
        if not path.is_file() or sha256_file(path) != bindings.get(key):
            raise ValueError(f"Task 12D accepted evidence binding mismatch: {key}")
    return bindings


def _manifest_rows(dataset_root: Path) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for line in (dataset_root / "case_manifest.jsonl").read_text(
        encoding="utf-8"
    ).splitlines():
        value = json.loads(line)
        if not isinstance(value, Mapping):
            raise ValueError("case manifest rows must be objects")
        result[str(value["case_id"])] = value
    return result


def _artifact_path(
    dataset_root: Path, row: Mapping[str, Any], artifact_name: str
) -> tuple[Path, Mapping[str, Any]]:
    artifacts = row.get("artifacts")
    if not isinstance(artifacts, Mapping) or not isinstance(
        artifacts.get(artifact_name), Mapping
    ):
        raise ValueError(f"{row.get('case_id')} missing {artifact_name}")
    record = artifacts[artifact_name]
    return dataset_root / str(record["relative_path"]), record


def _file_records(root: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.name == "BUNDLE_MANIFEST.json":
            continue
        records.append(
            {
                "relative_path": path.relative_to(root).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return records


def _content_sha(records: list[dict[str, object]]) -> str:
    payload = (json.dumps(records, sort_keys=True) + "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_bundle(
    *,
    task12d_root: Path,
    task12d_work: Path,
    task12d_qa: Path,
    coordinate_root: Path,
    output_root: Path,
    allow_dirty: bool,
) -> dict[str, object]:
    binding = _source_binding(allow_dirty)
    if output_root.exists():
        raise FileExistsError(f"Task 12E output root already exists: {output_root}")
    output_root.parent.mkdir(parents=True, exist_ok=True)
    staging = output_root.parent / f".{output_root.name}.tmp-{uuid.uuid4().hex}"
    bundle_root = staging / BUNDLE_NAME
    bundle_root.mkdir(parents=True, exist_ok=False)
    try:
        config = read_json(REPO_ROOT / "configs" / "task12e_linux_homologation_v2.json")
        acceptance = read_json(REPO_ROOT / "docs" / "reports" / "task12d_manual_acceptance.json")
        if acceptance.get("release", {}).get("go_for_task12e_linux_homologation") is not True:
            raise ValueError("Task 12D acceptance does not release Task 12E")
        acceptance_bindings = _verify_task12d_bindings(
            acceptance=acceptance,
            task12d_root=task12d_root,
            task12d_work=task12d_work,
            task12d_qa=task12d_qa,
        )
        expected_nodes = [str(value) for value in config["expected_nodes"]]
        canonical_node = str(config["canonical_projection_node"])

        _copy(
            REPO_ROOT / "docs" / "reports" / "task12d_manual_acceptance.json",
            bundle_root / "evidence" / "task12d_manual_acceptance.json",
        )
        _copy(
            REPO_ROOT
            / "docs"
            / "reports"
            / "task12e_v1_environment_preflight_failure.json",
            bundle_root
            / "evidence"
            / "task12e_v1_environment_preflight_failure.json",
        )
        _copy(
            task12d_work / "PROGRESS.json",
            bundle_root / "evidence" / "task12d_progress.json",
        )
        _copy(
            task12d_qa / "TASK12D_COMPLETE.json",
            bundle_root / "evidence" / "task12d_complete.json",
        )
        _copy(
            task12d_qa / "task12b_gate_summary.json",
            bundle_root / "evidence" / "task12b_gate_summary.json",
        )
        _copy(
            REPO_ROOT / "configs" / "task12e_linux_environment.yml",
            bundle_root / "environment" / "task12e_linux_environment.yml",
        )
        for script_name in (
            "task12e_linux_common.py",
            "capture_task12e_linux_environment.py",
            "run_task12e_linux_worker.py",
            "finalize_task12e_linux_master.py",
            "prepare_task12e_linux_environment.sh",
            "launch_task12e_linux_screen.sh",
            "run_task12e_linux_node.sh",
        ):
            _copy(
                REPO_ROOT / "scripts" / script_name,
                bundle_root / "scripts" / script_name,
            )
        smc_source = REPO_ROOT / "simind" / "ge870_czt.smc"
        ini_source = REPO_ROOT / "configs" / "simind_v2.ini"
        _copy(smc_source, bundle_root / "runtime" / "ge870_czt.smc")
        _copy(ini_source, bundle_root / "runtime" / "simind.ini")

        rows = _manifest_rows(task12d_root)
        clinical_cases: list[dict[str, object]] = []
        for case_id in config["fixture_execution"]["clinical_cases_on_every_node"]:
            case_id = str(case_id)
            row = rows[case_id]
            destination = bundle_root / "fixtures" / "clinical" / case_id
            source_path, source_record = _artifact_path(
                task12d_root, row, "simind_source_bin"
            )
            density_path, density_record = _artifact_path(
                task12d_root, row, "simind_density_bin"
            )
            phantom_path, phantom_record = _artifact_path(
                task12d_root, row, "phantom_npz"
            )
            metadata_path, metadata_record = _artifact_path(
                task12d_root, row, "metadata_json"
            )
            _copy(source_path, destination / f"{case_id}_act_av.bin")
            _copy(density_path, destination / f"{case_id}_atn_av.bin")
            _copy(phantom_path, destination / "phantom.npz")
            _copy(metadata_path, destination / "metadata.json")
            metadata = read_json(metadata_path)
            physics = metadata.get("physics")
            if not isinstance(physics, Mapping):
                raise ValueError(f"{case_id} physics metadata missing")
            artifacts = row["artifacts"]
            clinical_cases.append(
                {
                    "case_id": case_id,
                    "fixture_group": "clinical",
                    "nodes": expected_nodes,
                    "nn_multiplier": int(physics["nn_multiplier"]),
                    "rr_seed": int(physics["rr_seed"]),
                    "inputs": {
                        "source_relative_path": f"fixtures/clinical/{case_id}/{case_id}_act_av.bin",
                        "source_sha256": source_record["sha256"],
                        "density_relative_path": f"fixtures/clinical/{case_id}/{case_id}_atn_av.bin",
                        "density_sha256": density_record["sha256"],
                        "phantom_relative_path": f"fixtures/clinical/{case_id}/phantom.npz",
                        "phantom_sha256": phantom_record["sha256"],
                        "metadata_relative_path": f"fixtures/clinical/{case_id}/metadata.json",
                        "metadata_sha256": metadata_record["sha256"],
                    },
                    "windows_reference": {
                        extension: artifacts[f"projection_{extension}"]["sha256"]
                        for extension in ("a00", "mhd", "res", "spe")
                    },
                }
            )

        coordinate_complete = read_json(coordinate_root / "COMPLETE.json")
        coordinate_by_id = {
            str(item["case_id"]): item for item in coordinate_complete["cases"]
        }
        coordinate_cases: list[dict[str, object]] = []
        for case_id in config["fixture_execution"][
            "coordinate_cases_on_canonical_node"
        ]:
            case_id = str(case_id)
            item = coordinate_by_id[case_id]
            destination = bundle_root / "fixtures" / "coordinate" / case_id
            source_path = coordinate_root / str(item["source_bin"])
            density_path = coordinate_root / str(item["density_bin"])
            phantom_path = coordinate_root / str(item["phantom_npz"])
            _copy(source_path, destination / f"{case_id}_act_av.bin")
            _copy(density_path, destination / f"{case_id}_atn_av.bin")
            _copy(phantom_path, destination / "phantom.npz")
            coordinate_cases.append(
                {
                    "case_id": case_id,
                    "fixture_group": "coordinate",
                    "nodes": [canonical_node],
                    "nn_multiplier": int(item["nn_multiplier"]),
                    "rr_seed": int(item["rr_seed"]),
                    "inputs": {
                        "source_relative_path": f"fixtures/coordinate/{case_id}/{case_id}_act_av.bin",
                        "source_sha256": item["source_bin_sha256"],
                        "density_relative_path": f"fixtures/coordinate/{case_id}/{case_id}_atn_av.bin",
                        "density_sha256": item["density_bin_sha256"],
                        "phantom_relative_path": f"fixtures/coordinate/{case_id}/phantom.npz",
                        "phantom_sha256": item["phantom_npz_sha256"],
                    },
                    "windows_reference": dict(item["quartet_sha256"]),
                }
            )
        coordinate_descriptor = read_json(
            coordinate_root / "projection_alignment_cases_v1.json"
        )
        _copy(
            coordinate_root / "projection_alignment_cases_v1.json",
            bundle_root / "evidence" / "windows_coordinate_descriptor.json",
        )

        bound_plan: dict[str, object] = {
            "schema_version": PLAN_SCHEMA,
            "purpose": config["purpose"],
            "canonical_production_platform": config[
                "canonical_production_platform"
            ],
            "expected_linux_simind_sha256": config[
                "expected_linux_simind_sha256"
            ],
            "expected_nodes": expected_nodes,
            "canonical_projection_node": canonical_node,
            "hostname_prefix_by_node": config["hostname_prefix_by_node"],
            "minimum_resources_per_node": config["minimum_resources_per_node"],
            "environment": config["environment"],
            "runtime": {
                **dict(config["runtime"]),
                "smc_sha256": sha256_file(smc_source),
                "simind_ini_sha256": sha256_file(ini_source),
            },
            "projection_coordinates": coordinate_descriptor[
                "projection_coordinates"
            ],
            "projection_gates": config["projection_gates"],
            "clinical_case_ids": [item["case_id"] for item in clinical_cases],
            "coordinate_case_ids": [
                item["case_id"] for item in coordinate_cases
            ],
            "execution": {
                "maximum_parallel_per_node": int(
                    config["fixture_execution"]["initial_max_parallel_per_node"]
                ),
                "requested_parallel_by_node": config["fixture_execution"][
                    "requested_parallel_by_node"
                ],
            },
            "cases": clinical_cases + coordinate_cases,
            "source_binding": binding,
            "task12d_acceptance_sha256": sha256_file(
                REPO_ROOT / "docs" / "reports" / "task12d_manual_acceptance.json"
            ),
            "task12e_v1_failure_sha256": sha256_file(
                REPO_ROOT
                / "docs"
                / "reports"
                / "task12e_v1_environment_preflight_failure.json"
            ),
            "pars2_required_commit": acceptance_bindings["pars2_git_commit"],
        }
        plan_path = bundle_root / "TASK12E_PLAN.json"
        atomic_write_json(plan_path, bound_plan)
        records = _file_records(bundle_root)
        manifest = {
            "schema_version": BUNDLE_SCHEMA,
            "status": "complete",
            "created_utc": _utc_now(),
            "formal_eligible": binding["formal_eligible"],
            "source_binding": binding,
            "plan_relative_path": "TASK12E_PLAN.json",
            "plan_sha256": sha256_file(plan_path),
            "file_count": len(records),
            "content_sha256": _content_sha(records),
            "files": records,
        }
        atomic_write_json(bundle_root / "BUNDLE_MANIFEST.json", manifest)
        validate_bundle(bundle_root)
        os.replace(staging, output_root)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    final_bundle = output_root / BUNDLE_NAME
    archive_path = output_root / f"{BUNDLE_NAME}.tar.gz"
    with tarfile.open(archive_path, "w:gz", compresslevel=6) as stream:
        stream.add(final_bundle, arcname=BUNDLE_NAME)
    archive_sha = sha256_file(archive_path)
    (output_root / f"{archive_path.name}.sha256").write_text(
        f"{archive_sha}  {archive_path.name}\n", encoding="utf-8"
    )
    result = {
        "schema_version": "pars_v2_task12e_linux_bundle_build_v2",
        "status": "complete",
        "formal_eligible": binding["formal_eligible"],
        "bundle_root": str(final_bundle),
        "bundle_manifest_sha256": sha256_file(
            final_bundle / "BUNDLE_MANIFEST.json"
        ),
        "archive": str(archive_path),
        "archive_sha256": archive_sha,
        "archive_size_bytes": archive_path.stat().st_size,
    }
    atomic_write_json(output_root / "BUILD_COMPLETE.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task12d-root", type=Path, default=DEFAULT_TASK12D_ROOT)
    parser.add_argument("--task12d-work", type=Path, default=DEFAULT_TASK12D_WORK)
    parser.add_argument("--task12d-qa", type=Path, default=DEFAULT_TASK12D_QA)
    parser.add_argument("--coordinate-root", type=Path, default=DEFAULT_COORDINATE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()
    result = build_bundle(
        task12d_root=args.task12d_root.resolve(),
        task12d_work=args.task12d_work.resolve(),
        task12d_qa=args.task12d_qa.resolve(),
        coordinate_root=args.coordinate_root.resolve(),
        output_root=args.output_root.resolve(),
        allow_dirty=args.allow_dirty,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
