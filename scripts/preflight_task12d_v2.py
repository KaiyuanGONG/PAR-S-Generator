"""Build the three-case Task-12D frozen input bundle without launching SIMIND."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from generate_dataset_v2 import build_generation_plan  # noqa: E402
from preflight_pilot15_v2 import (  # noqa: E402
    _case_summary,
    _git_state,
    _resolve_paths,
    _runtime_preflight,
)

from core.liver_geometry import GridSpecV2  # noqa: E402
from core.pilot_v2 import (  # noqa: E402
    load_pilot_plan,
    prepare_pilot_case,
    validate_boundary_rejections,
)
from core.provenance import (
    atomic_write_bytes,
    atomic_write_json,
    sha256_file,
)  # noqa: E402
from core.reproducibility_v2 import (  # noqa: E402
    capture_generator_source_binding,
    capture_python_runtime,
    write_preflight_input_bundle,
)
from core.schemas_v2 import load_evidence_registry, load_profile  # noqa: E402
from core.task12d_v2 import (  # noqa: E402
    TASK12D_CASE_COUNT,
    TASK12D_COVERAGE_LABEL,
    TASK12D_PREFLIGHT_SCHEMA,
    require_task12d_coverage,
)


DEFAULT_WORK_ROOT = Path(r"D:\PFE-U\PAR\outputs\pars_v2_task12d3_preflight")
DEFAULT_SIMIND_EXE = Path(r"D:\PFE-U\PAR-S-Generator\simind\simind.exe")
DEFAULT_SMC_DIR = Path(r"C:\simind\smc_dir")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "configs" / "task12d_fullchain_v2.json",
    )
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    parser.add_argument("--simind-exe", type=Path, default=DEFAULT_SIMIND_EXE)
    parser.add_argument("--smc-dir", type=Path, default=DEFAULT_SMC_DIR)
    parser.add_argument("--allow-dirty", action="store_true")
    return parser


def _markdown(report: Mapping[str, object]) -> str:
    lines = [
        "# PAR-S V2 Task 12D preflight",
        "",
        f"- Status: **{str(report['status']).upper()}**",
        f"- Formal runner eligible: `{report.get('formal_runner_eligible')}`",
        "- SIMIND launched: `false`",
        f"- Cases: `{report.get('case_count')}`",
        f"- Generator commit: `{report.get('generator_git_commit')}`",
        f"- Runtime binding: `{report.get('python_runtime', {}).get('binding_sha256')}`",
        f"- Source binding: `{report.get('generator_source', {}).get('binding_sha256')}`",
        f"- Input bundle: `{report.get('input_bundle', {}).get('manifest_sha256')}`",
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
    lines.extend(
        [
            "",
            "This preflight freezes the only source/density bytes eligible for Task 12D. It never invokes SIMIND.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.work_root.exists():
        raise FileExistsError(
            f"Task 12D preflight root already exists: {args.work_root}"
        )
    commit, clean, dirty_status = _git_state()
    if not clean and not args.allow_dirty:
        raise RuntimeError(
            "formal Task 12D preflight requires a clean Generator worktree"
        )
    plan = load_pilot_plan(args.config)
    coverage = require_task12d_coverage(plan)
    paths = _resolve_paths(plan)
    registry = load_evidence_registry(paths["evidence_registry_path"])
    profile = load_profile(paths["profile_path"], registry)
    scanner = load_profile(paths["scanner_path"], registry)
    runtime = _runtime_preflight(args, plan, paths)
    python_runtime = capture_python_runtime()
    generator_source = capture_generator_source_binding(REPO_ROOT)
    if generator_source["git_commit"] != commit:
        raise RuntimeError("Task 12D source binding commit changed during preflight")
    if bool(generator_source["worktree_clean"]) != clean:
        raise RuntimeError(
            "Task 12D source binding cleanliness changed during preflight"
        )
    split_plan, generation_plan = build_generation_plan(
        dataset_id=str(plan["dataset_id"]),
        dataset_version=str(plan["dataset_version"]),
        dataset_role=str(plan["dataset_role"]),
        profile_id=profile.profile_id,
        case_count=TASK12D_CASE_COUNT,
        family_size=1,
        global_seed=int(plan["global_seed"]),
        ratios={key: float(value) for key, value in plan["split_ratios"].items()},
    )
    configured = [
        (str(case["case_id"]), str(case["case_family_id"])) for case in plan["cases"]
    ]
    planned = [
        (str(item["case_id"]), str(item["case_family_id"]))
        for item in generation_plan["entries"]
    ]
    if configured != planned:
        raise RuntimeError("Task 12D cases disagree with the immutable generation plan")

    args.work_root.mkdir(parents=True, exist_ok=False)
    report: dict[str, object] = {
        "schema_version": TASK12D_PREFLIGHT_SCHEMA,
        "status": "running",
        "formal_runner_eligible": False,
        "generator_git_commit": commit,
        "generator_worktree_clean": clean,
        "generator_dirty_status": dirty_status,
        "task12d_plan_path": str(args.config.resolve()),
        "task12d_plan_sha256": sha256_file(args.config),
        "profile_sha256": sha256_file(paths["profile_path"]),
        "scanner_sha256": sha256_file(paths["scanner_path"]),
        "evidence_registry_sha256": sha256_file(paths["evidence_registry_path"]),
        "split_plan_sha256": split_plan.sha256,
        "generation_plan_sha256": generation_plan["sha256"],
        "coverage": coverage,
        "runtime": runtime,
        "python_runtime": python_runtime,
        "generator_source": generator_source,
        "boundary_gates": validate_boundary_rejections(plan, profile),
        "case_count": TASK12D_CASE_COUNT,
        "cases": [],
        "simind_launched": False,
    }
    atomic_write_json(args.work_root / "PROGRESS.json", report)
    execution = plan["execution"]
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
                coverage_label=TASK12D_COVERAGE_LABEL,
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
        report["input_bundle"] = write_preflight_input_bundle(
            args.work_root,
            summaries,
        )
        report["actual_gates"] = [
            {
                "name": "all_three_case_preflights_pass",
                "status": "pass" if len(summaries) == TASK12D_CASE_COUNT else "fail",
                "observed": len(summaries),
                "required": TASK12D_CASE_COUNT,
            }
        ]
        report["status"] = "pass"
        report["formal_runner_eligible"] = clean and bool(
            generator_source["worktree_clean"]
        )
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
    return (
        0
        if report["formal_runner_eligible"]
        else (0 if args.allow_dirty and report["status"] == "pass" else 1)
    )


if __name__ == "__main__":
    raise SystemExit(main())
