from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.liver_geometry import GridSpecV2  # noqa: E402
from core.phantom_generator import PhantomConfig, PhantomGenerator  # noqa: E402
from core.population_sampler import sample_liver_target, sample_patient  # noqa: E402
from core.schemas_v2 import (  # noqa: E402
    TumorTargetV2,
    load_evidence_registry,
    load_profile,
)
from core.tumor_generator_v2 import (  # noqa: E402
    rasterize_tumor_at_center,
    sample_tumor_case_target,
)


def load_main_profile():
    registry = load_evidence_registry(REPO_ROOT / "configs" / "evidence_registry_v2.json")
    return load_profile(REPO_ROOT / "configs" / "population_tare_hcc_nopvi_v2.json", registry)


def _describe(values) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    q05, q50, q95 = np.quantile(array, (0.05, 0.50, 0.95))
    return {
        "mean": float(array.mean()),
        "sd": float(array.std(ddof=0)),
        "min": float(array.min()),
        "p05": float(q05),
        "median": float(q50),
        "p95": float(q95),
        "max": float(array.max()),
    }


def _fraction_tolerance(expected: float, sample_count: int) -> float:
    return max(0.02, 4.0 * math.sqrt(expected * (1.0 - expected) / sample_count))


def build_target_statistics(profile, *, sample_count: int, seed: int) -> dict:
    rng = np.random.default_rng(seed)
    cases = []
    for index in range(sample_count):
        patient = sample_patient(profile, rng, case_id=f"task4_stat_{index:05d}")
        liver = sample_liver_target(patient, profile, rng)
        cases.append((liver, sample_tumor_case_target(patient, liver, profile, rng)))

    count_bins = Counter(case.strata.count_bin for _, case in cases)
    dmax_bins = Counter(case.strata.dmax_bin for _, case in cases)
    lobe_extents = Counter(case.strata.lobe_extent for _, case in cases)
    expected_groups = {
        "count_bins": profile.value("tumor_count_bins"),
        "dmax_bins": profile.value("dmax_bins"),
        "lobe_extents": profile.value("lobe_distribution"),
    }
    observed_groups = {
        "count_bins": {name: count_bins[name] / sample_count for name in count_bins},
        "dmax_bins": {name: dmax_bins[name] / sample_count for name in dmax_bins},
        "lobe_extents": {name: lobe_extents[name] / sample_count for name in lobe_extents},
    }
    gates: dict[str, bool] = {}
    for group_name, expected in expected_groups.items():
        for name, probability in expected.items():
            gates[f"{group_name}.{name}"] = abs(
                observed_groups[group_name].get(name, 0.0) - float(probability)
            ) <= _fraction_tolerance(float(probability), sample_count)

    counts = [case.requested_count for _, case in cases]
    dmax_values = [case.dmax_mm for _, case in cases]
    gates.update(
        {
            "single_count_is_exact": all(
                case.requested_count == 1
                for _, case in cases
                if case.strata.count_bin == "1"
            ),
            "two_to_five_support": all(
                2 <= case.requested_count <= 5
                for _, case in cases
                if case.strata.count_bin == "2-5"
            ),
            "greater_than_five_support": all(
                6 <= case.requested_count <= 20
                for _, case in cases
                if case.strata.count_bin == ">5"
            ),
            "dmax_support": all(10.0 <= case.dmax_mm <= 200.0 for _, case in cases),
            "dmax_bin_support": all(
                (10.0 <= case.dmax_mm < 80.0)
                if case.strata.dmax_bin == "10-<80_mm"
                else (80.0 <= case.dmax_mm <= 200.0)
                for _, case in cases
            ),
            "lobe_extent_exact": all(
                (len({target.lobe for target in case.targets}) == 2)
                == (case.strata.lobe_extent == "bilobar")
                for _, case in cases
            ),
            "large_is_confluent": all(
                target.morphology == "lobulated_confluent"
                for _, case in cases
                for target in case.targets
                if target.dmax_mm > 100.0
            ),
            "analytic_burden_gate": all(
                sum(
                    math.pi
                    / 6.0
                    * target.dmax_mm**3
                    * target.axis_ratios[0]
                    * target.axis_ratios[1]
                    / 1000.0
                    for target in case.targets
                )
                / liver.volume_ml
                <= case.burden_fraction_max
                for liver, case in cases
            ),
            "population_vs_within_bin_evidence": all(
                case.evidence_types["count_bin"] == "literature_population"
                and case.evidence_types["conditional_geometry"] == "engineering_prior"
                for _, case in cases
            ),
        }
    )
    return {
        "sample_count": sample_count,
        "seed": seed,
        "status": "pass" if all(gates.values()) else "fail",
        "expected": expected_groups,
        "observed": observed_groups,
        "descriptive": {
            "lesion_count": _describe(counts),
            "patient_dmax_mm": _describe(dmax_values),
            "subcapsular_fraction": float(
                np.mean([target.subcapsular for _, case in cases for target in case.targets])
            ),
            "confluent_fraction": float(
                np.mean(
                    [
                        target.morphology == "lobulated_confluent"
                        for _, case in cases
                        for target in case.targets
                    ]
                )
            ),
        },
        "gates": gates,
    }


def _diameter_target(dmax_mm: float, *, stress: bool = False) -> TumorTargetV2:
    confluent = dmax_mm > 100.0
    return TumorTargetV2(
        lesion_id=f"required_{dmax_mm:g}_mm",
        dmax_mm=dmax_mm,
        axis_ratios=(0.82, 0.91),
        lobe="right",
        morphology="lobulated_confluent" if confluent else "smooth_nodular",
        orientation_deg_zyx=(23.0, -17.0, 31.0),
        primitive_count=3 if confluent else 1,
        evidence_types={"dmax": "stress_test" if stress else "engineering_prior"},
    )


def build_raster_gate(grid: GridSpecV2) -> dict:
    center_index = np.asarray(grid.shape) // 2
    center = center_index @ grid.affine_4x4[:3, :3].T + grid.affine_4x4[:3, 3]
    rows = []
    for dmax_mm in (10.0, 20.0, 40.0, 60.0, 100.0, 200.0, 215.0):
        raster = rasterize_tumor_at_center(
            _diameter_target(dmax_mm, stress=dmax_mm > 200.0),
            center,
            grid,
        )
        tolerance_mm = max(0.75 * grid.voxel_size_mm, 0.03 * dmax_mm)
        row_gates = {
            "dmax": raster.dmax_error_mm <= tolerance_mm,
            "connected": raster.connected,
            "grid_boundary_clear": raster.grid_boundary_clear,
            "confluent_overlap": dmax_mm <= 100.0 or raster.primitive_overlap_voxels > 0,
        }
        rows.append(
            {
                "target_dmax_mm": dmax_mm,
                "actual_recist_mm": raster.metrics.recist_3d_mm,
                "error_mm": raster.dmax_error_mm,
                "tolerance_mm": tolerance_mm,
                "voxel_count": raster.metrics.voxel_count,
                "volume_ml": raster.metrics.volume_ml,
                "primitive_count": raster.primitive_count,
                "primitive_overlap_voxels": raster.primitive_overlap_voxels,
                "status": "pass" if all(row_gates.values()) else "fail",
                "gates": row_gates,
            }
        )
    return {
        "status": "pass" if all(row["status"] == "pass" for row in rows) else "fail",
        "grid": {"shape": list(grid.shape), "voxel_size_mm": grid.voxel_size_mm},
        "cases": rows,
    }


def build_end_to_end_gate(
    profile,
    grid: GridSpecV2,
    *,
    case_count: int,
    seed: int,
) -> tuple[dict, dict[str, np.ndarray]]:
    generator = PhantomGenerator(
        PhantomConfig(volume_shape=grid.shape, voxel_size_mm=grid.voxel_size_mm)
    )
    rows = []
    arrays: dict[str, np.ndarray] = {}
    rejection_counts: Counter[str] = Counter()
    for index in range(case_count):
        rng = np.random.default_rng(seed + index)
        case_id = f"task4_e2e_{index:03d}"
        liver_case = generator.generate_liver_v2(
            profile,
            rng,
            case_id=case_id,
            liver_seed=seed + 10_000 + index,
            max_shape_attempts=8,
        )
        tumor_case = generator.generate_tumors_v2(
            liver_case.patient,
            liver_case.geometry,
            profile,
            rng,
            tumor_seed=seed + 20_000 + index,
            max_target_attempts=12,
        )
        for rejected in tumor_case.sampling_provenance.rejected_attempts:
            rejection_counts[rejected.reason_code] += 1
        target_by_id = {
            placement.instance_id: placement.target
            for placement in tumor_case.geometry.placements
        }
        dmax_errors = [
            abs(metric.recist_3d_mm - target_by_id[metric.instance_id].dmax_mm)
            for metric in tumor_case.geometry.lesion_metrics
        ]
        tolerances = [
            max(
                tumor_case.target.dmax_tolerance_voxels * grid.voxel_size_mm,
                0.03 * target_by_id[metric.instance_id].dmax_mm,
            )
            for metric in tumor_case.geometry.lesion_metrics
        ]
        row_gates = {
            "complete_liver_containment": not np.any(
                (tumor_case.geometry.instance_mask > 0) & ~liver_case.geometry.mask
            ),
            "instance_count": tumor_case.geometry.realized_count
            == tumor_case.geometry.target_count,
            "dmax": all(error <= tolerance for error, tolerance in zip(dmax_errors, tolerances)),
            "burden": tumor_case.geometry.tumor_to_liver_fraction
            <= tumor_case.target.burden_fraction_max,
            "lobe_extent": tumor_case.geometry.realized_lobe_extent
            == tumor_case.target.strata.lobe_extent,
            "positive_instance_ids": tuple(
                int(value)
                for value in np.unique(tumor_case.geometry.instance_mask)
                if value > 0
            )
            == tuple(range(1, tumor_case.geometry.target_count + 1)),
        }
        rows.append(
            {
                "case_id": case_id,
                "liver_morphology": liver_case.patient.liver_morphology,
                "strata": tumor_case.target.strata.__dict__,
                "target_count": tumor_case.target.requested_count,
                "patient_dmax_mm": tumor_case.target.dmax_mm,
                "actual_dmax_error_max_mm": max(dmax_errors),
                "tumor_to_liver_fraction": tumor_case.geometry.tumor_to_liver_fraction,
                "accepted_attempt_index": tumor_case.sampling_provenance.accepted_attempt_index,
                "rejected_reasons": [
                    rejected.reason_code
                    for rejected in tumor_case.sampling_provenance.rejected_attempts
                ],
                "status": "pass" if all(row_gates.values()) else "fail",
                "gates": row_gates,
            }
        )
        arrays[f"liver_{index}"] = liver_case.geometry.mask.astype(np.uint8)
        arrays[f"regions_{index}"] = liver_case.geometry.region_labels.astype(np.uint8)
        arrays[f"tumors_{index}"] = tumor_case.geometry.instance_mask.astype(np.uint16)
        arrays[f"affine_{index}"] = liver_case.geometry.affine_4x4.astype(np.float64)

    attempts = [row["accepted_attempt_index"] for row in rows]
    aggregate_gates = {
        "all_cases_pass": all(row["status"] == "pass" for row in rows),
        "at_least_one_multifocal": any(row["target_count"] > 1 for row in rows),
        "retry_chain_bounded": max(attempts) <= 12,
    }
    report = {
        "status": "pass" if all(aggregate_gates.values()) else "fail",
        "case_count": case_count,
        "seed": seed,
        "first_pass_fraction": float(np.mean(np.asarray(attempts) == 1)),
        "attempt_histogram": {
            str(name): count for name, count in sorted(Counter(attempts).items())
        },
        "rejection_reason_counts": dict(sorted(rejection_counts.items())),
        "gates": aggregate_gates,
        "cases": rows,
    }
    arrays["metadata_json"] = np.asarray(json.dumps(report, ensure_ascii=False))
    return report, arrays


def _markdown(report: dict) -> str:
    target = report["target_statistics"]
    raster = report["raster_gate"]
    endpoint = report["end_to_end_gate"]
    lines = [
        "# PAR-S V2 Task 4 肿瘤目标、栅格与完整放置报告",
        "",
        f"- Profile: `{report['profile_id']}`",
        f"- 无体素目标样本: **{target['sample_count']:,}**",
        f"- 真实肝脏端到端病例: **{endpoint['case_count']}**",
        f"- 总门禁: **{report['status'].upper()}**",
        "",
        "## 文献分层边际",
        "",
        "| 分层 | 类别 | 目标 | 观察 |",
        "|---|---|---:|---:|",
    ]
    for group in ("count_bins", "dmax_bins", "lobe_extents"):
        for name, expected in target["expected"][group].items():
            lines.append(
                f"| {group} | `{name}` | {float(expected):.4f} | "
                f"{target['observed'][group][name]:.4f} |"
            )
    lines.extend(
        [
            "",
            "数量层、Dmax 层和单/双叶层为 `literature_population`；层内具体数量、截断对数正态 Dmax、次级病灶比例、亚包膜概率和形态混合均为 `engineering_prior`，没有伪装成文献 prevalence。",
            "",
            "## 必测直径栅格门禁",
            "",
            "| 目标 (mm) | 实测 RECIST (mm) | 误差 (mm) | 容差 (mm) | primitive | 结果 |",
            "|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in raster["cases"]:
        lines.append(
            f"| {row['target_dmax_mm']:.0f} | {row['actual_recist_mm']:.2f} | "
            f"{row['error_mm']:.2f} | {row['tolerance_mm']:.2f} | "
            f"{row['primitive_count']} | {row['status'].upper()} |"
        )
    lines.extend(
        [
            "",
            "## 真实 Task 3 肝脏上的端到端门禁",
            "",
            f"- first-pass fraction: `{endpoint['first_pass_fraction']:.3f}`",
            f"- attempt histogram: `{endpoint['attempt_histogram']}`",
            f"- rejection reasons: `{endpoint['rejection_reason_counts']}`",
            "- 该端到端集合是几何/重试链 smoke gate，不用 3 例 first-pass 比例推断 500 例生产通过率。",
            "",
            "| 病例 | 肝形态 | 数量层 | Dmax 层 | 单/双叶 | 病灶数 | Dmax | 负荷 | 尝试 | 结果 |",
            "|---|---|---|---|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in endpoint["cases"]:
        strata = row["strata"]
        lines.append(
            f"| `{row['case_id']}` | {row['liver_morphology']} | {strata['count_bin']} | "
            f"{strata['dmax_bin']} | {strata['lobe_extent']} | {row['target_count']} | "
            f"{row['patient_dmax_mm']:.1f} | {row['tumor_to_liver_fraction']:.3f} | "
            f"{row['accepted_attempt_index']} | {row['status'].upper()} |"
        )
    lines.extend(
        [
            "",
            "完整 containment 是对未裁切的完整 primitive union 逐体素检查；不同病灶实例在接受前检查零重叠。融合病灶允许自身 primitive 受控重叠，但输出单一 instance label。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Task 4 V2 tumor generation.")
    parser.add_argument("--sample-count", type=int, default=10_000)
    parser.add_argument("--endpoint-count", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20_260_713)
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "docs" / "reports")
    args = parser.parse_args()
    if args.sample_count < 100:
        raise ValueError("sample-count must be >= 100")
    if args.endpoint_count < 1:
        raise ValueError("endpoint-count must be >= 1")

    profile = load_main_profile()
    grid = GridSpecV2()
    target_statistics = build_target_statistics(
        profile, sample_count=args.sample_count, seed=args.seed
    )
    raster_gate = build_raster_gate(grid)
    end_to_end_gate, arrays = build_end_to_end_gate(
        profile,
        grid,
        case_count=args.endpoint_count,
        seed=args.seed + 1000,
    )
    components = (target_statistics, raster_gate, end_to_end_gate)
    report = {
        "schema_version": "pars_task4_tumor_validation_v2",
        "profile_id": profile.profile_id,
        "status": "pass" if all(item["status"] == "pass" for item in components) else "fail",
        "target_statistics": target_statistics,
        "raster_gate": raster_gate,
        "end_to_end_gate": end_to_end_gate,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "task4_tumor_v2_validation.json"
    markdown_path = args.output_dir / "task4_tumor_v2_validation.md"
    examples_path = args.output_dir / "task4_tumor_v2_examples.npz"
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    markdown_path.write_text(_markdown(report), encoding="utf-8", newline="\n")
    np.savez_compressed(examples_path, **arrays)
    print(
        json.dumps(
            {
                "status": report["status"],
                "json": str(json_path),
                "markdown": str(markdown_path),
                "examples": str(examples_path),
            }
        )
    )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
