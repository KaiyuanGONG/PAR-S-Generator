"""Build all 15 QA phantoms and SIMIND inputs without launching SIMIND."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Mapping

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from generate_dataset_v2 import build_generation_plan  # noqa: E402

from core.liver_geometry import GridSpecV2  # noqa: E402
from core.pilot15_v2 import (  # noqa: E402
    PILOT15_CASE_COUNT,
    PILOT15_COVERAGE_LABEL,
    PILOT15_PREFLIGHT_SCHEMA,
    require_pilot15_coverage,
)
from core.pilot_v2 import (  # noqa: E402
    load_pilot_plan,
    prepare_pilot_case,
    resolve_plan_path,
    validate_boundary_rejections,
)
from core.provenance import atomic_write_bytes, atomic_write_json, sha256_file  # noqa: E402
from core.schemas_v2 import load_evidence_registry, load_profile  # noqa: E402
from core.simind_exec import build_simind_command  # noqa: E402
from core.smc_parser import parse_smc, validate_voxel_source_smc  # noqa: E402


DEFAULT_WORK_ROOT = Path(r"D:\PFE-U\PAR\outputs\pars_v2_pilot15_preflight")
DEFAULT_SIMIND_EXE = Path(r"D:\PFE-U\PAR-S-Generator\simind\simind.exe")
DEFAULT_SMC_DIR = Path(r"C:\simind\smc_dir")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "configs" / "pilot15_v2.json",
    )
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    parser.add_argument("--simind-exe", type=Path, default=DEFAULT_SIMIND_EXE)
    parser.add_argument("--smc-dir", type=Path, default=DEFAULT_SMC_DIR)
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Development-only: run geometry checks but mark the report ineligible for formal execution.",
    )
    return parser


def _git_state() -> tuple[str, bool, str]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.rstrip()
    return commit, not bool(status), status


def _resolve_paths(plan: Mapping[str, object]) -> dict[str, Path]:
    return {
        key: resolve_plan_path(REPO_ROOT, plan[key], key)
        for key in (
            "profile_path",
            "scanner_path",
            "evidence_registry_path",
            "smc_path",
            "simind_ini_path",
        )
    }


def _runtime_preflight(
    args: argparse.Namespace,
    plan: Mapping[str, object],
    paths: Mapping[str, Path],
) -> dict[str, object]:
    if not args.simind_exe.is_file():
        raise FileNotFoundError(f"SIMIND executable not found: {args.simind_exe}")
    binary_sha = sha256_file(args.simind_exe)
    if binary_sha != plan["expected_simind_binary_sha256"]:
        raise RuntimeError("SIMIND binary hash differs from the pilot15 plan")
    if not args.smc_dir.is_dir():
        raise FileNotFoundError(f"SMC_DIR does not exist: {args.smc_dir}")
    smc = validate_voxel_source_smc(parse_smc(paths["smc_path"]))
    command_probe = build_simind_command(
        executable=args.simind_exe,
        smc_stem=paths["smc_path"].stem,
        output_stem="case_00000",
        source_stem="case_00000",
        density_stem="case_00000",
        nn_multiplier=int(plan["execution"]["nn_multiplier"]),
        rr_seed=1,
    )
    return {
        "simind_executable": str(args.simind_exe.resolve()),
        "simind_binary_sha256": binary_sha,
        "smc_dir": str(args.smc_dir.resolve()),
        "smc_sha256": sha256_file(paths["smc_path"]),
        "simind_ini_sha256": sha256_file(paths["simind_ini_path"]),
        "projection_shape_vvu": [
            smc.projection_views,
            smc.image_matrix_xy[1],
            smc.image_matrix_xy[0],
        ],
        "command_probe": list(command_probe),
        "simind_launched": False,
    }


def _case_summary(prepared: object) -> dict[str, object]:
    arrays = prepared.arrays
    tumor_union = np.asarray(arrays["tumor_union_mask"], dtype=bool)
    liver = np.asarray(arrays["liver_mask"], dtype=bool)
    perfusion = np.asarray(arrays["perfusion_mask"], dtype=bool)
    mu_true = np.asarray(arrays["mu_true_140kev"], dtype=np.float32)
    mu_input = np.asarray(arrays["mu_input_140kev"], dtype=np.float32)
    source = np.asarray(arrays["simind_source_weights"], dtype=np.float64)
    tumor_count = int(np.count_nonzero(tumor_union))
    coverage = float(np.count_nonzero(tumor_union & perfusion) / tumor_count)
    mismatch_observed = coverage < 1.0
    failures: list[str] = []
    if np.any(tumor_union & ~liver):
        failures.append("tumor containment failed")
    if mismatch_observed != bool(prepared.activity.mismatch_challenge):
        failures.append("perfusion mismatch semantics failed")
    if not np.isfinite(mu_true).all() or not np.isfinite(mu_input).all():
        failures.append("attenuation contains non-finite values")
    if np.any(mu_true < 0) or np.any(mu_input < 0):
        failures.append("attenuation contains negative values")
    if np.array_equal(mu_true, mu_input):
        failures.append("mu_true and mu_input are not separated")
    if not math.isclose(
        float(source.sum()),
        float(prepared.base_histories_per_projection),
        rel_tol=2e-6,
        abs_tol=0.1,
    ):
        failures.append("source history sum mismatch")
    lesion_documents = [
        {
            "instance_id": metric.instance_id,
            "recist_3d_mm": metric.recist_3d_mm,
            "volume_ml": metric.volume_ml,
            "sphericity": metric.sphericity,
            "necrotic_fraction": next(
                item.necrotic_fraction
                for item in prepared.activity.lesion_metrics
                if item.instance_id == metric.instance_id
            ),
            "tnr_mean": next(
                item.actual_tnr_mean
                for item in prepared.activity.lesion_metrics
                if item.instance_id == metric.instance_id
            ),
        }
        for metric in prepared.tumors.lesion_metrics
    ]
    return {
        "case_id": prepared.case_id,
        "status": "pass" if not failures else "fail",
        "failures": failures,
        "rr_seed": prepared.seeds.simind,
        "liver_morphology": prepared.patient.liver_morphology,
        "bmi": prepared.patient.bmi,
        "liver_fit_attempt": prepared.liver_fit_attempt,
        "liver_volume_ml": prepared.liver.actual_metrics["volume_ml"],
        "liver_extent_mm_zyx": prepared.liver.actual_metrics["extent_mm_zyx"],
        "lesions": lesion_documents,
        "tumor_fraction_liver": prepared.tumors.tumor_to_liver_fraction,
        "injection_territory": prepared.activity.injection_territory,
        "sector_proxy_label": prepared.activity.sector_proxy_label,
        "mismatch_challenge": prepared.activity.mismatch_challenge,
        "injection_tumor_coverage_fraction": coverage,
        "source_weight_sum": float(source.sum()),
        "source_sha256": sha256_file(prepared.source_bin),
        "density_sha256": sha256_file(prepared.density_bin),
        "mu_true_input_mean_absolute_difference": float(
            np.mean(np.abs(mu_true.astype(np.float64) - mu_input.astype(np.float64)))
        ),
        "lung_component_cleanup": prepared.anatomy.metadata.actual_metrics[
            "lung_component_cleanup"
        ],
    }


def _markdown(report: Mapping[str, object]) -> str:
    lines = [
        "# PAR-S V2 pilot15 preflight",
        "",
        f"- Status: **{str(report['status']).upper()}**",
        f"- Formal runner eligible: `{report.get('formal_runner_eligible')}`",
        "- SIMIND launched: `false`",
        f"- Cases: `{report.get('case_count')}`",
        f"- Generator commit: `{report.get('generator_git_commit')}`",
        f"- Plan SHA-256: `{report.get('pilot_plan_sha256')}`",
        "",
        "| Case | Result | Liver ml | RECIST mm | Territory | Mismatch | /RR |",
        "|---|---|---:|---|---|---|---:|",
    ]
    for case in report.get("cases", []):
        recist = ", ".join(f"{item['recist_3d_mm']:.2f}" for item in case["lesions"])
        lines.append(
            f"| `{case['case_id']}` | **{str(case['status']).upper()}** | "
            f"{case['liver_volume_ml']:.2f} | {recist} | "
            f"{case['injection_territory']} | {case['mismatch_challenge']} | "
            f"{case['rr_seed']} |"
        )
    lines.extend(["", "This preflight creates phantom/SIMIND input bytes only; it never invokes SIMIND.", ""])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.work_root.exists():
        raise FileExistsError(f"preflight work root already exists: {args.work_root}")
    commit, clean, dirty_status = _git_state()
    if not clean and not args.allow_dirty:
        raise RuntimeError("formal pilot15 preflight requires a clean Generator worktree")
    plan = load_pilot_plan(args.config)
    paths = _resolve_paths(plan)
    registry = load_evidence_registry(paths["evidence_registry_path"])
    profile = load_profile(paths["profile_path"], registry)
    scanner = load_profile(paths["scanner_path"], registry)
    coverage = require_pilot15_coverage(plan, profile)
    runtime = _runtime_preflight(args, plan, paths)
    execution = plan["execution"]
    split_plan, generation_plan = build_generation_plan(
        dataset_id=str(plan["dataset_id"]),
        dataset_version=str(plan["dataset_version"]),
        dataset_role=str(plan["dataset_role"]),
        profile_id=profile.profile_id,
        case_count=PILOT15_CASE_COUNT,
        family_size=1,
        global_seed=int(plan["global_seed"]),
        ratios={key: float(value) for key, value in plan["split_ratios"].items()},
    )
    configured = [
        (str(case["case_id"]), str(case["case_family_id"]))
        for case in plan["cases"]
    ]
    planned = [
        (str(item["case_id"]), str(item["case_family_id"]))
        for item in generation_plan["entries"]
    ]
    if configured != planned:
        raise RuntimeError("pilot15 configured case identities disagree with generation plan")

    args.work_root.mkdir(parents=True, exist_ok=False)
    report: dict[str, object] = {
        "schema_version": PILOT15_PREFLIGHT_SCHEMA,
        "status": "running",
        "formal_runner_eligible": False,
        "generator_git_commit": commit,
        "generator_worktree_clean": clean,
        "generator_dirty_status": dirty_status,
        "pilot_plan_path": str(args.config.resolve()),
        "pilot_plan_sha256": sha256_file(args.config),
        "profile_sha256": sha256_file(paths["profile_path"]),
        "scanner_sha256": sha256_file(paths["scanner_path"]),
        "evidence_registry_sha256": sha256_file(paths["evidence_registry_path"]),
        "split_plan_sha256": split_plan.sha256,
        "generation_plan_sha256": generation_plan["sha256"],
        "coverage": coverage,
        "runtime": runtime,
        "boundary_gates": validate_boundary_rejections(plan, profile),
        "case_count": PILOT15_CASE_COUNT,
        "cases": [],
        "simind_launched": False,
    }
    atomic_write_json(args.work_root / "PROGRESS.json", report)
    grid = GridSpecV2(
        shape=tuple(int(value) for value in scanner.value("matrix")),
        voxel_size_mm=float(scanner.value("voxel_size_mm")),
    )
    try:
        summaries: list[dict[str, object]] = []
        for case in plan["cases"]:
            prepared = prepare_pilot_case(
                case,
                profile,
                grid,
                global_seed=int(plan["global_seed"]),
                base_histories=int(execution["base_histories_per_projection"]),
                work_dir=args.work_root / "cases" / str(case["case_id"]),
                coverage_label=PILOT15_COVERAGE_LABEL,
            )
            summary = _case_summary(prepared)
            if summary["status"] != "pass":
                raise RuntimeError(f"{prepared.case_id}: {summary['failures']}")
            summaries.append(summary)
            atomic_write_json(
                args.work_root / "cases" / prepared.case_id / "CASE_PREFLIGHT.json",
                summary,
            )
            report["cases"] = summaries
            atomic_write_json(args.work_root / "PROGRESS.json", report)
        necrotic_count = sum(
            item["necrotic_fraction"] > 0
            for case in summaries
            for item in case["lesions"]
        )
        actual_gates = [
            {
                "name": "all_case_preflights_pass",
                "status": "pass" if len(summaries) == PILOT15_CASE_COUNT else "fail",
                "observed": len(summaries),
                "required": PILOT15_CASE_COUNT,
            },
            {
                "name": "necrosis_visual_coverage",
                "status": "pass" if necrotic_count >= 1 else "fail",
                "observed_necrotic_lesions": necrotic_count,
                "required": ">=1",
            },
        ]
        report["actual_gates"] = actual_gates
        report["status"] = (
            "pass" if all(item["status"] == "pass" for item in actual_gates) else "fail"
        )
        report["formal_runner_eligible"] = report["status"] == "pass" and clean
    except Exception as exc:
        report["status"] = "fail"
        report["formal_runner_eligible"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
    atomic_write_json(args.work_root / "PREFLIGHT.json", report)
    atomic_write_bytes(
        args.work_root / "PREFLIGHT.md",
        _markdown(report).encode("utf-8"),
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["formal_runner_eligible"] else (0 if args.allow_dirty and report["status"] == "pass" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
