"""Build all three Task-12 phantoms and SIMIND inputs without running SIMIND."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.liver_geometry import GridSpecV2  # noqa: E402
from core.pilot_v2 import (  # noqa: E402
    load_pilot_plan,
    prepare_pilot_case,
    resolve_plan_path,
    validate_boundary_rejections,
)
from core.provenance import atomic_write_json, sha256_file  # noqa: E402
from core.schemas_v2 import load_evidence_registry, load_profile  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "configs" / "pilot3_v2.json",
    )
    parser.add_argument("--work-root", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.work_root.exists():
        raise FileExistsError(f"preflight work root already exists: {args.work_root}")
    plan = load_pilot_plan(args.config)
    registry_path = resolve_plan_path(
        REPO_ROOT, plan["evidence_registry_path"], "evidence_registry_path"
    )
    profile_path = resolve_plan_path(REPO_ROOT, plan["profile_path"], "profile_path")
    scanner_path = resolve_plan_path(REPO_ROOT, plan["scanner_path"], "scanner_path")
    registry = load_evidence_registry(registry_path)
    profile = load_profile(profile_path, registry)
    scanner = load_profile(scanner_path, registry)
    execution = plan["execution"]
    grid = GridSpecV2(
        shape=tuple(int(value) for value in scanner.value("matrix")),
        voxel_size_mm=float(scanner.value("voxel_size_mm")),
    )
    summaries = []
    for case in plan["cases"]:
        prepared = prepare_pilot_case(
            case,
            profile,
            grid,
            global_seed=int(plan["global_seed"]),
            base_histories=int(execution["base_histories_per_projection"]),
            work_dir=args.work_root / str(case["case_id"]),
        )
        summaries.append(
            {
                "case_id": prepared.case_id,
                "rr_seed": prepared.seeds.simind,
                "liver_fit_attempt": prepared.liver_fit_attempt,
                "liver_volume_ml": prepared.liver.actual_metrics["volume_ml"],
                "liver_extent_mm_zyx": prepared.liver.actual_metrics[
                    "extent_mm_zyx"
                ],
                "lesion_recist_3d_mm": [
                    metric.recist_3d_mm for metric in prepared.tumors.lesion_metrics
                ],
                "tumor_fraction_liver": prepared.tumors.tumor_to_liver_fraction,
                "injection_territory": prepared.activity.injection_territory,
                "mismatch_challenge": prepared.activity.mismatch_challenge,
                "injection_tumor_coverage_fraction": (
                    prepared.activity.injection_tumor_coverage_fraction
                ),
                "source_sha256": sha256_file(prepared.source_bin),
                "density_sha256": sha256_file(prepared.density_bin),
                "status": "pass",
            }
        )
    report = {
        "schema_version": "pars_v2_pilot3_preflight_v1",
        "status": "pass",
        "case_count": len(summaries),
        "cases": summaries,
        "boundary_gates": validate_boundary_rejections(plan, profile),
    }
    atomic_write_json(args.work_root / "PREFLIGHT.json", report)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
