from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.population_sampler import sample_liver_target, sample_patient  # noqa: E402
from core.liver_geometry import GridSpecV2, fit_liver_geometry  # noqa: E402
from core.schemas_v2 import PopulationProfileV2, load_evidence_registry, load_profile  # noqa: E402


def load_main_profile(repo_root: Path = REPO_ROOT) -> PopulationProfileV2:
    registry = load_evidence_registry(repo_root / "configs" / "evidence_registry_v2.json")
    return load_profile(repo_root / "configs" / "population_tare_hcc_nopvi_v2.json", registry)


def _describe(values: np.ndarray) -> dict[str, float]:
    values = np.asarray(values, dtype=np.float64)
    q05, q25, q50, q75, q95 = np.quantile(values, (0.05, 0.25, 0.50, 0.75, 0.95))
    return {
        "mean": float(values.mean()),
        "sd": float(values.std(ddof=0)),
        "min": float(values.min()),
        "p05": float(q05),
        "p25": float(q25),
        "median": float(q50),
        "p75": float(q75),
        "p95": float(q95),
        "max": float(values.max()),
    }


def _fraction_tolerance(expected: float, sample_count: int) -> float:
    standard_error = math.sqrt(expected * (1.0 - expected) / sample_count)
    return max(0.025, 4.0 * standard_error)


def build_population_statistics(
    profile: PopulationProfileV2,
    *,
    sample_count: int = 10_000,
    seed: int = 20_260_713,
) -> dict:
    if not isinstance(sample_count, int) or isinstance(sample_count, bool) or sample_count < 100:
        raise ValueError("sample_count must be an integer >= 100")
    rng = np.random.default_rng(seed)
    patients = []
    targets = []
    for index in range(sample_count):
        patient = sample_patient(profile, rng, case_id=f"task3_stat_{index:05d}")
        patients.append(patient)
        targets.append(sample_liver_target(patient, profile, rng))

    sex_male = np.fromiter((patient.sex == "male" for patient in patients), dtype=np.float64)
    is_cirrhotic = np.fromiter(
        (patient.liver_morphology == "cirrhotic" for patient in patients), dtype=np.float64
    )
    age = np.array([patient.age_years for patient in patients])
    height = np.array([patient.height_cm for patient in patients])
    weight = np.array([patient.weight_kg for patient in patients])
    bmi = np.array([patient.bmi for patient in patients])
    volume = np.array([target.volume_ml for target in targets])
    si = np.array([target.si_mm for target in targets])
    ap = np.array([target.ap_mm for target in targets])
    lr = np.array([target.lr_mm for target in targets])
    left = np.array([target.left_fraction for target in targets])
    segment_ratio = np.array([target.s1_3_to_s4_8_ratio for target in targets])
    caudate = np.array([target.caudate_fraction for target in targets])
    roughness = np.array([target.surface_roughness_target for target in targets])
    normal_selector = is_cirrhotic == 0
    cirrhotic_selector = is_cirrhotic == 1

    expected_male = float(profile.value("male_fraction_auxiliary"))
    expected_cirrhosis = float(profile.value("cirrhosis_prevalence"))
    volume_reference = profile.value("liver_volume_reference_ml")
    left_reference = profile.value("left_liver_fraction_reference")
    male_fraction = float(sex_male.mean())
    cirrhosis_fraction = float(is_cirrhotic.mean())
    height_weight_correlation = float(np.corrcoef(height, weight)[0, 1])
    weight_volume_correlation = float(np.corrcoef(weight, volume)[0, 1])
    age_volume_correlation = float(np.corrcoef(age, volume)[0, 1])
    banned_upper_limit = 14.0 * weight + 979.0
    banned_used = bool(np.allclose(volume, banned_upper_limit, rtol=0.0, atol=1e-6))
    slope, intercept = np.polyfit(weight, volume, 1)

    normal_left = left[normal_selector]
    normal_ratio = segment_ratio[normal_selector]
    cirrhotic_ratio = segment_ratio[cirrhotic_selector]
    normal_caudate = caudate[normal_selector]
    cirrhotic_caudate = caudate[cirrhotic_selector]
    normal_roughness = roughness[normal_selector]
    cirrhotic_roughness = roughness[cirrhotic_selector]
    gates = {
        "male_fraction": abs(male_fraction - expected_male)
        <= _fraction_tolerance(expected_male, sample_count),
        "cirrhosis_fraction": abs(cirrhosis_fraction - expected_cirrhosis)
        <= _fraction_tolerance(expected_cirrhosis, sample_count),
        "height_weight_correlation": height_weight_correlation > 0.45,
        "weight_volume_correlation": weight_volume_correlation > 0.30,
        "volume_mean": abs(volume.mean() / float(volume_reference["mean"]) - 1.0) <= 0.04,
        "volume_sd": abs(volume.std(ddof=0) / float(volume_reference["sd"]) - 1.0) <= 0.18,
        "normal_left_median": abs(np.median(normal_left) - float(left_reference["median"])) <= 0.02,
        "normal_left_variation": normal_left.std(ddof=0) > 0.035,
        "normal_left_support": bool(
            normal_left.min() >= float(left_reference["range"][0])
            and normal_left.max() <= float(left_reference["range"][1])
        ),
        "cirrhotic_segment_direction": cirrhotic_ratio.mean() > normal_ratio.mean(),
        "cirrhotic_caudate_direction": cirrhotic_caudate.mean() > normal_caudate.mean(),
        "cirrhotic_roughness_direction": cirrhotic_roughness.mean() > normal_roughness.mean(),
        "banned_upper_limit_not_used": not banned_used,
    }
    gates = {name: bool(value) for name, value in gates.items()}
    return {
        "schema_version": "pars_task3_population_validation_v1",
        "profile_id": profile.profile_id,
        "seed": int(seed),
        "sample_count": int(sample_count),
        "status": "pass" if all(gates.values()) else "fail",
        "expected": {
            "male_fraction": {
                "value": expected_male,
                "source_type": profile.parameters["male_fraction_auxiliary"].source_type,
                "scope": "full_tare_cohort_auxiliary",
            },
            "cirrhosis_fraction": {
                "value": expected_cirrhosis,
                "source_type": profile.parameters["cirrhosis_prevalence"].source_type,
            },
            "liver_volume_ml": {
                **volume_reference,
                "source_type": profile.parameters["liver_volume_reference_ml"].source_type,
            },
            "normal_left_fraction": {
                **left_reference,
                "source_type": profile.parameters["left_liver_fraction_reference"].source_type,
            },
        },
        "observed": {
            "male_fraction": male_fraction,
            "cirrhosis_fraction": cirrhosis_fraction,
            "age_years": _describe(age),
            "height_cm": _describe(height),
            "weight_kg": _describe(weight),
            "bmi": _describe(bmi),
            "liver_volume_ml": _describe(volume),
            "extent_si_mm": _describe(si),
            "extent_ap_mm": _describe(ap),
            "extent_lr_mm": _describe(lr),
            "left_fraction_all": _describe(left),
            "left_fraction_normal": _describe(normal_left),
            "segment_ratio_normal": _describe(normal_ratio),
            "segment_ratio_cirrhotic": _describe(cirrhotic_ratio),
            "caudate_fraction_normal": _describe(normal_caudate),
            "caudate_fraction_cirrhotic": _describe(cirrhotic_caudate),
            "roughness_target_normal": _describe(normal_roughness),
            "roughness_target_cirrhotic": _describe(cirrhotic_roughness),
        },
        "correlations": {
            "height_weight": height_weight_correlation,
            "weight_liver_volume": weight_volume_correlation,
            "age_liver_volume": age_volume_correlation,
        },
        "checks": {
            "banned_upper_limit_equation_used": banned_used,
            "fitted_weight_volume_slope_ml_per_kg": float(slope),
            "fitted_weight_volume_intercept_ml": float(intercept),
            "normal_count": int(normal_selector.sum()),
            "cirrhotic_count": int(cirrhotic_selector.sum()),
        },
        "gates": gates,
    }


def select_representative_targets(
    profile: PopulationProfileV2,
    *,
    seed: int = 20_260_714,
    normal_with_caudate: int = 2,
    normal_without_caudate: int = 1,
    cirrhotic_with_caudate: int = 3,
) -> list[tuple[object, object]]:
    """Select real profile samples for morphology coverage, not prevalence estimation."""
    requested = {
        ("normal", True): normal_with_caudate,
        ("normal", False): normal_without_caudate,
        ("cirrhotic", True): cirrhotic_with_caudate,
    }
    selected: dict[tuple[str, bool], list[tuple[object, object]]] = {key: [] for key in requested}
    rng = np.random.default_rng(seed)
    for index in range(20_000):
        patient = sample_patient(profile, rng, case_id=f"task3_voxel_candidate_{index:05d}")
        target = sample_liver_target(patient, profile, rng)
        key = (target.morphology, bool(target.caudate_enabled))
        if key in requested and len(selected[key]) < requested[key]:
            selected[key].append((patient, target))
        if all(len(selected[key]) == count for key, count in requested.items()):
            break
    else:
        raise RuntimeError("could not select requested representative target strata")
    ordered = []
    for key in (("normal", False), ("normal", True), ("cirrhotic", True)):
        ordered.extend(selected[key])
    return ordered


def build_voxel_validation(
    profile: PopulationProfileV2,
    *,
    seed: int = 20_260_714,
    grid: GridSpecV2 | None = None,
) -> dict:
    grid = grid or GridSpecV2()
    selected = select_representative_targets(profile, seed=seed)
    rows = []
    for index, (patient, target) in enumerate(selected):
        geometry = fit_liver_geometry(target, grid)
        actual = geometry.actual_metrics
        target_extents = np.asarray((target.si_mm, target.ap_mm, target.lr_mm))
        actual_extents = np.asarray(actual["extent_mm_zyx"])
        target_centroid = np.asarray(target.centroid_mm)
        actual_centroid = np.asarray(actual["centroid_world_mm"])
        gates = {
            "volume": abs(float(actual["volume_ml"]) / target.volume_ml - 1.0) <= 0.04,
            "extents": bool(
                np.max(np.abs(actual_extents - target_extents)) <= 2.5 * grid.voxel_size_mm
            ),
            "centroid": bool(
                np.max(np.abs(actual_centroid - target_centroid)) <= 1.5 * grid.voxel_size_mm
            ),
            "left_fraction": abs(float(actual["left_fraction"]) - target.left_fraction) <= 0.025,
            "region_cover": bool(np.array_equal(geometry.region_labels > 0, geometry.mask)),
            "connected": ndimage.label(geometry.mask)[1] == 1,
        }
        gates = {name: bool(value) for name, value in gates.items()}
        rows.append(
            {
                "case_id": patient.case_id,
                "morphology": target.morphology,
                "caudate_enabled": bool(target.caudate_enabled),
                "target": {
                    "volume_ml": float(target.volume_ml),
                    "extent_mm_zyx": [float(value) for value in target_extents],
                    "centroid_world_mm": [float(value) for value in target_centroid],
                    "left_fraction": float(target.left_fraction),
                    "s1_3_to_s4_8_ratio": float(target.s1_3_to_s4_8_ratio),
                    "caudate_fraction": float(target.caudate_fraction),
                    "surface_roughness": float(target.surface_roughness_target),
                },
                "actual": {
                    "volume_ml": float(actual["volume_ml"]),
                    "extent_mm_zyx": [float(value) for value in actual_extents],
                    "centroid_world_mm": [float(value) for value in actual_centroid],
                    "left_fraction": float(actual["left_fraction"]),
                    "s1_3_to_s4_8_ratio": float(actual["s1_3_to_s4_8_ratio"]),
                    "caudate_fraction": float(actual["caudate_fraction"]),
                    "surface_roughness": float(actual["surface_roughness"]),
                    "sphericity": float(actual["sphericity"]),
                },
                "errors": {
                    "volume_relative_pct": 100.0 * (float(actual["volume_ml"]) / target.volume_ml - 1.0),
                    "maximum_extent_mm": float(np.max(np.abs(actual_extents - target_extents))),
                    "maximum_centroid_mm": float(np.max(np.abs(actual_centroid - target_centroid))),
                    "left_fraction": float(actual["left_fraction"] - target.left_fraction),
                },
                "gates": gates,
                "status": "pass" if all(gates.values()) else "fail",
            }
        )
    normal_roughness = [row["actual"]["surface_roughness"] for row in rows if row["morphology"] == "normal"]
    cirrhotic_roughness = [
        row["actual"]["surface_roughness"] for row in rows if row["morphology"] == "cirrhotic"
    ]
    aggregate_gates = {
        "all_cases": all(row["status"] == "pass" for row in rows),
        "cirrhotic_roughness_direction": float(np.mean(cirrhotic_roughness))
        > float(np.mean(normal_roughness)),
    }
    aggregate_gates = {name: bool(value) for name, value in aggregate_gates.items()}
    return {
        "schema_version": "pars_task3_voxel_validation_v1",
        "profile_id": profile.profile_id,
        "selection_role": "coverage_qa_not_population_prevalence",
        "seed": int(seed),
        "grid": {"shape": list(grid.shape), "voxel_size_mm": float(grid.voxel_size_mm)},
        "case_count": len(rows),
        "status": "pass" if all(aggregate_gates.values()) else "fail",
        "aggregate": {
            "normal_surface_roughness_mean": float(np.mean(normal_roughness)),
            "cirrhotic_surface_roughness_mean": float(np.mean(cirrhotic_roughness)),
        },
        "gates": aggregate_gates,
        "cases": rows,
    }


def _voxel_markdown(report: dict) -> str:
    lines = [
        "# PAR-S V2 Task 3 体素几何门禁",
        "",
        f"- 选择角色: `{report['selection_role']}`",
        f"- 网格: `{report['grid']['shape']}` @ `{report['grid']['voxel_size_mm']} mm`",
        f"- 病例数: **{report['case_count']}**",
        f"- 总门禁: **{report['status'].upper()}**",
        "",
        "| Case | 形态 | 尾状叶 | 体积误差 | 最大三径误差 | 最大质心误差 | 粗糙度 | 结果 |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in report["cases"]:
        lines.append(
            f"| `{row['case_id']}` | {row['morphology']} | {row['caudate_enabled']} | "
            f"{row['errors']['volume_relative_pct']:.4f}% | {row['errors']['maximum_extent_mm']:.2f} mm | "
            f"{row['errors']['maximum_centroid_mm']:.2f} mm | {row['actual']['surface_roughness']:.4f} | "
            f"{row['status'].upper()} |"
        )
    lines.extend(
        [
            "",
            "## 聚合方向性",
            "",
            f"- 正常肝粗糙度均值: `{report['aggregate']['normal_surface_roughness_mean']:.4f}`",
            f"- 肝硬化粗糙度均值: `{report['aggregate']['cirrhotic_surface_roughness_mean']:.4f}`",
            "",
        ]
    )
    return "\n".join(lines)


def _markdown_report(report: dict) -> str:
    observed = report["observed"]
    correlations = report["correlations"]
    lines = [
        "# PAR-S V2 Task 3 患者与肝脏目标采样统计报告",
        "",
        f"- Profile: `{report['profile_id']}`",
        f"- Seed: `{report['seed']}`",
        f"- 无体素样本数: **{report['sample_count']:,}**",
        f"- 总门禁: **{report['status'].upper()}**",
        "",
        "## 关键分布",
        "",
        "| 指标 | 观察值 | 目标/参考 |",
        "|---|---:|---:|",
        f"| 男性比例 | {observed['male_fraction']:.4f} | {report['expected']['male_fraction']['value']:.4f} |",
        f"| 肝硬化比例 | {observed['cirrhosis_fraction']:.4f} | {report['expected']['cirrhosis_fraction']['value']:.4f} |",
        f"| 肝体积均值 (mL) | {observed['liver_volume_ml']['mean']:.1f} | {report['expected']['liver_volume_ml']['mean']:.1f} |",
        f"| 肝体积 SD (mL) | {observed['liver_volume_ml']['sd']:.1f} | {report['expected']['liver_volume_ml']['sd']:.1f} |",
        f"| 正常肝左叶比例中位数 | {observed['left_fraction_normal']['median']:.4f} | {report['expected']['normal_left_fraction']['median']:.4f} |",
        f"| 身高–体重相关 | {correlations['height_weight']:.4f} | > 0.45 |",
        f"| 体重–肝体积相关 | {correlations['weight_liver_volume']:.4f} | > 0.30 |",
        "",
        "## 肝硬化方向性",
        "",
        "| 指标 | 正常 | 肝硬化 |",
        "|---|---:|---:|",
        f"| S1–3/S4–8 proxy 均值 | {observed['segment_ratio_normal']['mean']:.4f} | {observed['segment_ratio_cirrhotic']['mean']:.4f} |",
        f"| 尾状叶比例均值 | {observed['caudate_fraction_normal']['mean']:.4f} | {observed['caudate_fraction_cirrhotic']['mean']:.4f} |",
        f"| 粗糙度目标均值 | {observed['roughness_target_normal']['mean']:.4f} | {observed['roughness_target_cirrhotic']['mean']:.4f} |",
        "",
        "## 自动门禁",
        "",
    ]
    lines.extend(f"- [{'x' if passed else ' '}] `{name}`" for name, passed in report["gates"].items())
    lines.extend(
        [
            "",
            "## 证据语义",
            "",
            "年龄中位数和男性比例仅作为完整 TARE 队列的辅助边际；联合分布形状、肝体积条件模型、尾状叶出现率和连续表面场均保持 `engineering_prior`，不输出为 No-PVI prevalence。",
            "",
            "`14×weight+979` 仅是已禁止的肝大上限式，本报告明确检查生成体积未使用该式。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Task 3 patient and liver target sampling.")
    parser.add_argument("--sample-count", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=20_260_713)
    parser.add_argument("--voxel-seed", type=int, default=20_260_714)
    parser.add_argument("--skip-voxel", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "docs" / "reports")
    args = parser.parse_args()

    profile = load_main_profile(REPO_ROOT)
    report = build_population_statistics(profile, sample_count=args.sample_count, seed=args.seed)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "task3_liver_v2_statistics.json"
    markdown_path = args.output_dir / "task3_liver_v2_statistics.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown_report(report), encoding="utf-8")
    outputs = {"population_status": report["status"], "json": str(json_path), "markdown": str(markdown_path)}
    overall_pass = report["status"] == "pass"
    if not args.skip_voxel:
        voxel_report = build_voxel_validation(profile, seed=args.voxel_seed)
        voxel_json_path = args.output_dir / "task3_liver_v2_voxel_gate.json"
        voxel_markdown_path = args.output_dir / "task3_liver_v2_voxel_gate.md"
        voxel_json_path.write_text(json.dumps(voxel_report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        voxel_markdown_path.write_text(_voxel_markdown(voxel_report), encoding="utf-8")
        outputs.update(
            {
                "voxel_status": voxel_report["status"],
                "voxel_json": str(voxel_json_path),
                "voxel_markdown": str(voxel_markdown_path),
            }
        )
        overall_pass = overall_pass and voxel_report["status"] == "pass"
    print(json.dumps(outputs))
    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
