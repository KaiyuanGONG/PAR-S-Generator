"""Run local projection gates on a downloaded Task 12E Linux result archive."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from task12e_linux_common import (  # noqa: E402
    MASTER_SCHEMA,
    atomic_write_json,
    read_json,
    safe_extract_tar,
    sha256_file,
    validate_bundle,
)


DEFAULT_BUNDLE_ROOT = Path(
    r"D:\PFE-U\PAR\outputs\task12e_linux_upload\pars_v2_task12e_linux_bundle_v1"
)
DEFAULT_RESULTS_ARCHIVE = Path(
    r"D:\PFE-U\PAR\outputs\task12e_linux_download\task12e_linux_results.tar.gz"
)
DEFAULT_QA_ROOT = Path(r"D:\PFE-U\PAR\outputs\task12e_linux_qa")
DEFAULT_PARS2_ROOT = Path(r"D:\PFE-U\PAR\.worktrees\PAR-S_2-task12")
DEFAULT_WINDOWS_ROOT = Path(r"D:\PFE-U\PAR\outputs\pars_v2_task12d3")
FROZEN_TRANSFORM_ID = "simind_v8_xcat_v1_views_forward_roll000_det_v_flip_det_u_keep"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _git_value(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _validate_local_source_bindings(
    manifest: Mapping[str, Any], plan: Mapping[str, Any], pars2_root: Path
) -> None:
    source_binding = manifest.get("source_binding")
    if manifest.get("formal_eligible") is not True or not isinstance(
        source_binding, Mapping
    ):
        raise ValueError("Task 12E bundle is not a formal clean-worktree build")
    if source_binding.get("worktree_clean") is not True:
        raise ValueError("Task 12E bundle source binding is not clean")
    expected_pars2 = str(plan.get("pars2_required_commit", ""))
    actual_pars2 = _git_value(pars2_root, "rev-parse", "HEAD")
    if actual_pars2 != expected_pars2:
        raise ValueError(
            f"PAR-S_2 commit mismatch: expected={expected_pars2} actual={actual_pars2}"
        )
    status = _git_value(pars2_root, "status", "--short")
    if status:
        raise ValueError("PAR-S_2 worktree must be clean for the Task 12E finalizer")


def _descriptor(
    *,
    plan: Mapping[str, Any],
    bundle_root: Path,
    result_root: Path,
    fixture_group: str,
) -> dict[str, object]:
    canonical = str(plan["canonical_projection_node"])
    cases: list[dict[str, str]] = []
    for case in plan["cases"]:
        if case["fixture_group"] != fixture_group:
            continue
        case_id = str(case["case_id"])
        inputs = case["inputs"]
        cases.append(
            {
                "case_id": case_id,
                "phantom_npz": str(
                    (bundle_root / str(inputs["phantom_relative_path"])).resolve()
                ),
                "projection_a00": str(
                    (
                        result_root
                        / "nodes"
                        / canonical
                        / case_id
                        / f"{case_id}.a00"
                    ).resolve()
                ),
                "projection_mhd": str(
                    (
                        result_root
                        / "nodes"
                        / canonical
                        / case_id
                        / f"{case_id}.mhd"
                    ).resolve()
                ),
            }
        )
    if len(cases) != 3:
        raise ValueError(f"{fixture_group} descriptor requires three cases")
    return {
        "schema_version": "pars_projection_alignment_cases_v1",
        "projection_coordinates": plan["projection_coordinates"],
        "cases": cases,
    }


def _run_search(
    *,
    python: str,
    pars2_root: Path,
    descriptor: Path,
    output: Path,
    role: str,
) -> dict[str, object]:
    command = [
        python,
        str(pars2_root / "scripts" / "search_projection_transform.py"),
        str(descriptor),
        "--output",
        str(output),
        "--device",
        "auto",
        "--minimum-score-margin",
        "0.005",
        "--minimum-bootstrap-top1-frequency",
        "0.95",
        "--minimum-case-top1-frequency",
        "1.0",
        "--report-role",
        role,
    ]
    completed = subprocess.run(
        command,
        cwd=pars2_root,
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    if not output.is_file():
        raise RuntimeError(
            f"projection search did not write {output}; exit={completed.returncode}; "
            f"stderr={completed.stderr[-2000:]}"
        )
    return {
        "command": command,
        "return_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "report": read_json(output),
    }


def _raw_projection_audit(path: Path) -> dict[str, object]:
    projection = np.memmap(path, dtype="<f4", mode="r", shape=(60, 128, 128))
    values = np.asarray(projection, dtype=np.float64)
    if not np.isfinite(values).all() or np.any(values < 0):
        raise ValueError(f"invalid projection values: {path}")
    view_sums = values.sum(axis=(1, 2), dtype=np.float64)
    if np.any(view_sums <= 0):
        raise ValueError(f"zero projection view: {path}")
    positive = values > 0
    positive_fraction = positive.mean(axis=(1, 2))
    outer = np.zeros((128, 128), dtype=bool)
    outer[:8, :] = True
    outer[-8:, :] = True
    outer[:, :8] = True
    outer[:, -8:] = True
    outer_fraction = float(values[:, outer].sum() / values.sum())
    ratio = float(view_sums.max() / view_sums.min())
    cv = float(view_sums.std() / view_sums.mean())
    failures: list[str] = []
    if float(positive_fraction.min()) < 0.001:
        failures.append("positive_bin_fraction")
    if outer_fraction > 0.01:
        failures.append("outer_8px_count_fraction")
    if ratio > 50:
        failures.append("view_sum_ratio")
    if cv > 1.5:
        failures.append("view_sum_coefficient_of_variation")
    return {
        "status": "pass" if not failures else "fail",
        "failures": failures,
        "projection_weight_sum": float(values.sum()),
        "minimum_positive_bin_fraction_per_view": float(positive_fraction.min()),
        "outer_8px_count_fraction": outer_fraction,
        "view_sum_ratio": ratio,
        "view_sum_coefficient_of_variation": cv,
        "sha256": sha256_file(path),
    }


def _windows_rows(root: Path) -> dict[str, Mapping[str, Any]]:
    rows: dict[str, Mapping[str, Any]] = {}
    for line in (root / "case_manifest.jsonl").read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        rows[str(value["case_id"])] = value
    return rows


def _platform_diagnostic(
    *,
    plan: Mapping[str, Any],
    result_root: Path,
    windows_root: Path,
) -> list[dict[str, object]]:
    canonical = str(plan["canonical_projection_node"])
    windows = _windows_rows(windows_root)
    reports: list[dict[str, object]] = []
    for case_id in plan["clinical_case_ids"]:
        case_id = str(case_id)
        linux_path = result_root / "nodes" / canonical / case_id / f"{case_id}.a00"
        record = windows[case_id]["artifacts"]["projection_a00"]
        windows_path = windows_root / str(record["relative_path"])
        linux = np.memmap(linux_path, dtype="<f4", mode="r", shape=(60, 128, 128))
        win = np.memmap(windows_path, dtype="<f4", mode="r", shape=(60, 128, 128))
        difference = np.asarray(linux, dtype=np.float64) - np.asarray(win, dtype=np.float64)
        linux_flat = np.asarray(linux, dtype=np.float64).ravel()
        win_flat = np.asarray(win, dtype=np.float64).ravel()
        correlation = float(np.corrcoef(linux_flat, win_flat)[0, 1])
        reports.append(
            {
                "case_id": case_id,
                "byte_identical": sha256_file(linux_path) == sha256_file(windows_path),
                "linux_sha256": sha256_file(linux_path),
                "windows_sha256": sha256_file(windows_path),
                "mean_absolute_difference": float(np.mean(np.abs(difference))),
                "maximum_absolute_difference": float(np.max(np.abs(difference))),
                "projection_sum_relative_difference": float(
                    abs(linux_flat.sum() - win_flat.sum()) / win_flat.sum()
                ),
                "correlation": correlation,
                "blocking": False,
            }
        )
    return reports


def _validate_results_archive_hash(path: Path) -> str:
    sidecar = path.with_name(f"{path.name}.sha256")
    actual = sha256_file(path)
    if sidecar.is_file():
        expected = sidecar.read_text(encoding="utf-8").split()[0].lower()
        if actual != expected:
            raise ValueError("downloaded Task 12E result archive hash mismatch")
    return actual


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, default=DEFAULT_BUNDLE_ROOT)
    parser.add_argument("--results-archive", type=Path, default=DEFAULT_RESULTS_ARCHIVE)
    parser.add_argument("--qa-root", type=Path, default=DEFAULT_QA_ROOT)
    parser.add_argument("--pars2-root", type=Path, default=DEFAULT_PARS2_ROOT)
    parser.add_argument("--windows-root", type=Path, default=DEFAULT_WINDOWS_ROOT)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    bundle_root = args.bundle_root.resolve()
    manifest = validate_bundle(bundle_root)
    plan = read_json(bundle_root / str(manifest["plan_relative_path"]))
    pars2_root = args.pars2_root.resolve()
    _validate_local_source_bindings(manifest, plan, pars2_root)
    archive = args.results_archive.resolve()
    archive_sha = _validate_results_archive_hash(archive)
    qa_root = args.qa_root.resolve()
    extract_root = qa_root / "extracted"
    if qa_root.exists() and not args.resume:
        raise FileExistsError(f"QA root exists; use --resume: {qa_root}")
    qa_root.mkdir(parents=True, exist_ok=True)
    if not extract_root.exists():
        safe_extract_tar(archive, extract_root)
    result_root = extract_root / "task12e_linux_results"
    master = read_json(result_root / "TASK12E_LINUX_MASTER.json")
    if master.get("schema_version") != MASTER_SCHEMA or master.get("status") != "pass":
        raise ValueError("downloaded Linux master gate is not passing")
    if master.get("bundle_manifest_sha256") != sha256_file(
        bundle_root / "BUNDLE_MANIFEST.json"
    ):
        raise ValueError("downloaded Linux result bundle binding mismatch")

    coordinate_descriptor = qa_root / "linux_coordinate_descriptor.json"
    clinical_descriptor = qa_root / "linux_clinical_descriptor.json"
    atomic_write_json(
        coordinate_descriptor,
        _descriptor(
            plan=plan,
            bundle_root=bundle_root,
            result_root=result_root,
            fixture_group="coordinate",
        ),
    )
    atomic_write_json(
        clinical_descriptor,
        _descriptor(
            plan=plan,
            bundle_root=bundle_root,
            result_root=result_root,
            fixture_group="clinical",
        ),
    )
    coordinate_output = qa_root / "linux_projection_coordinate_report.json"
    clinical_output = qa_root / "linux_clinical_alignment_exploratory.json"
    coordinate_stage = _run_search(
        python=sys.executable,
        pars2_root=pars2_root,
        descriptor=coordinate_descriptor,
        output=coordinate_output,
        role="projection-coordinate-gate",
    )
    clinical_stage = _run_search(
        python=sys.executable,
        pars2_root=pars2_root,
        descriptor=clinical_descriptor,
        output=clinical_output,
        role="clinical-exploratory",
    )
    coordinate_report = coordinate_stage["report"]
    clinical_report = clinical_stage["report"]
    coordinate_pass = bool(coordinate_report.get("freeze_gate", {}).get("passed"))
    coordinate_transform = coordinate_report.get("decision", {}).get(
        "preferred", {}
    ).get("transform_id")
    if coordinate_transform != FROZEN_TRANSFORM_ID:
        coordinate_pass = False

    metrics = clinical_report.get("decision", {}).get("frozen_transform_metrics", {})
    thresholds = plan["projection_gates"]
    clinical_failures: list[str] = []
    comparisons = (
        ("normalized_correlation", float(metrics.get("normalized_correlation", -math.inf)), ">=", float(thresholds["clinical_minimum_normalized_correlation"])),
        ("scale_fit_nrmse", float(metrics.get("scale_fit_nrmse", math.inf)), "<=", float(thresholds["clinical_maximum_scale_fit_nrmse"])),
        ("composite_score", float(metrics.get("composite_score", -math.inf)), ">=", float(thresholds["clinical_minimum_composite_score"])),
        ("centroid_rmse_pixels", float(metrics.get("centroid_rmse_pixels", math.inf)), "<=", float(thresholds["clinical_maximum_centroid_rmse_pixels"])),
    )
    for name, value, operator, threshold in comparisons:
        if (operator == ">=" and value < threshold) or (
            operator == "<=" and value > threshold
        ):
            clinical_failures.append(name)
    canonical = str(plan["canonical_projection_node"])
    raw_audits = [
        {
            "case_id": case_id,
            **_raw_projection_audit(
                result_root
                / "nodes"
                / canonical
                / str(case_id)
                / f"{case_id}.a00"
            ),
        }
        for case_id in plan["clinical_case_ids"]
    ]
    if any(item["status"] != "pass" for item in raw_audits):
        clinical_failures.append("raw_projection_engineering_audit")
    clinical_pass = not clinical_failures
    platform_diagnostic = _platform_diagnostic(
        plan=plan,
        result_root=result_root,
        windows_root=args.windows_root.resolve(),
    )
    automatic_pass = coordinate_pass and clinical_pass
    document = {
        "schema_version": "pars_v2_task12e_linux_complete_v1",
        "status": "pass_awaiting_manual_review" if automatic_pass else "fail",
        "generated_utc": _utc_now(),
        "automatic_gate_passed": automatic_pass,
        "manual_review_required": True,
        "manual_review_status": "pending",
        "go_for_50_case_generation": False,
        "bundle_manifest_sha256": sha256_file(bundle_root / "BUNDLE_MANIFEST.json"),
        "results_archive_sha256": archive_sha,
        "linux_master_sha256": sha256_file(
            result_root / "TASK12E_LINUX_MASTER.json"
        ),
        "gates": {
            "linux_environment_gate": "pass",
            "three_node_byte_gate": "pass",
            "linux_projection_coordinate_gate_v2": "pass" if coordinate_pass else "fail",
            "linux_clinical_projection_quality_gate_v1": "pass" if clinical_pass else "fail",
        },
        "coordinate": {
            "preferred_transform_id": coordinate_transform,
            "freeze_gate": coordinate_report.get("freeze_gate"),
            "report_sha256": sha256_file(coordinate_output),
            "stage_return_code": coordinate_stage["return_code"],
        },
        "clinical": {
            "metrics": metrics,
            "failures": clinical_failures,
            "raw_case_audits": raw_audits,
            "report_sha256": sha256_file(clinical_output),
            "exploratory_uniqueness": clinical_report.get("freeze_gate"),
            "stage_return_code": clinical_stage["return_code"],
        },
        "windows_linux_diagnostic_nonblocking": platform_diagnostic,
        "next_action": (
            "manual review and Task 12E acceptance before 50 cases"
            if automatic_pass
            else "repair failed Linux homologation gate"
        ),
    }
    atomic_write_json(qa_root / "TASK12E_COMPLETE.json", document)
    print(
        json.dumps(
            {
                "status": document["status"],
                "automatic_gate_passed": automatic_pass,
                "go_for_50_case_generation": False,
                "output": str(qa_root / "TASK12E_COMPLETE.json"),
            }
        )
    )
    return 0 if automatic_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
