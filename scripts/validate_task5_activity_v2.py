from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.activity_model_v2 import (  # noqa: E402
    _sample_lesion_tnrs,
    necrosis_probability_for_diameter,
)
from core.schemas_v2 import load_evidence_registry, load_profile  # noqa: E402


def _describe(values: np.ndarray) -> dict[str, float]:
    q05, q50, q95 = np.quantile(values, (0.05, 0.50, 0.95))
    return {
        "mean": float(values.mean()),
        "sd": float(values.std(ddof=0)),
        "min": float(values.min()),
        "p05": float(q05),
        "median": float(q50),
        "p95": float(q95),
        "max": float(values.max()),
    }


def build_report(*, lesion_count: int, seed: int) -> dict:
    registry = load_evidence_registry(REPO_ROOT / "configs" / "evidence_registry_v2.json")
    profile = load_profile(
        REPO_ROOT / "configs" / "population_tare_hcc_nopvi_v2.json",
        registry,
    )
    lesions_per_patient = 4
    patient_count = int(np.ceil(lesion_count / lesions_per_patient))
    rng = np.random.default_rng(seed)
    rows = []
    heterogeneous = []
    for _ in range(patient_count):
        sampled = _sample_lesion_tnrs(
            profile,
            tuple(range(1, lesions_per_patient + 1)),
            rng,
        )
        rows.append([sampled[index] for index in range(1, lesions_per_patient + 1)])
        heterogeneous.extend(
            rng.random(lesions_per_patient) < float(profile.value("heterogeneous_fraction"))
        )
    matrix = np.asarray(rows, dtype=np.float64)
    flat = matrix.ravel()[:lesion_count]
    heterogeneous_array = np.asarray(heterogeneous[:lesion_count], dtype=np.float64)
    reference_mean = float(profile.value("tnr_mean_reference"))
    reference_sd = float(profile.value("tnr_mean_sd"))
    lower, upper = map(float, profile.value("tnr_mean_range"))
    expected_heterogeneous = float(profile.value("heterogeneous_fraction"))
    off_diagonal = np.corrcoef(matrix, rowvar=False)
    within_patient_correlation = float(
        off_diagonal[np.triu_indices(lesions_per_patient, k=1)].mean()
    )
    diameters = (20.0, 40.0, 60.0, 80.0, 100.0, 120.0, 160.0)
    necrosis_curve = {
        f"{diameter:g}": necrosis_probability_for_diameter(diameter, profile)
        for diameter in diameters
    }
    gates = {
        "tnr_support": bool(flat.min() >= lower and flat.max() <= upper),
        "tnr_mean": abs(float(flat.mean()) / reference_mean - 1.0) <= 0.08,
        "tnr_sd": abs(float(flat.std(ddof=0)) / reference_sd - 1.0) <= 0.20,
        "heterogeneous_fraction": abs(float(heterogeneous_array.mean()) - expected_heterogeneous)
        <= 0.025,
        "necrosis_probability_increases_with_size": all(
            later > earlier
            for earlier, later in zip(necrosis_curve.values(), tuple(necrosis_curve.values())[1:])
        ),
        "tnr_evidence_is_hcc_lesion_level": profile.parameters[
            "tnr_mean_reference"
        ].source_type
        == "literature_population",
        "injection_territory_is_not_population": profile.parameters[
            "injection_territories"
        ].source_type
        == "coverage_sampling",
        "within_patient_model_is_engineering": profile.parameters[
            "activity_model_v2"
        ].source_type
        == "engineering_prior",
    }
    return {
        "schema_version": "pars_task5_activity_validation_v2",
        "profile_id": profile.profile_id,
        "seed": seed,
        "lesion_count": lesion_count,
        "synthetic_patient_groups_for_dependence_audit": patient_count,
        "status": "pass" if all(gates.values()) else "fail",
        "expected": {
            "tnr_mean": reference_mean,
            "tnr_sd": reference_sd,
            "tnr_range": [lower, upper],
            "heterogeneous_fraction": expected_heterogeneous,
            "evidence_scope": "HCC lesion-level marginal; 59 patients, 77 lesions",
            "within_patient_correlation": "not_reported",
        },
        "observed": {
            "tnr_mean": _describe(flat),
            "heterogeneous_fraction": float(heterogeneous_array.mean()),
            "engineering_within_patient_correlation": within_patient_correlation,
            "necrosis_probability_by_dmax_mm": necrosis_curve,
        },
        "challenge_separation": {
            "population_activity_pattern": "physiologic_heterogeneous",
            "stress_activity_patterns": [
                "tumor_dominant_low_background",
                "extreme_low_uptake",
            ],
            "mismatch_challenge_population_weight": 0,
        },
        "gates": gates,
    }


def _markdown(report: dict) -> str:
    observed = report["observed"]
    expected = report["expected"]
    lines = [
        "# PAR-S V2 Task 5 活度目标统计审计",
        "",
        f"- Profile: `{report['profile_id']}`",
        f"- 病灶级目标数: **{report['lesion_count']:,}**",
        f"- 总门禁: **{report['status'].upper()}**",
        "",
        "| 指标 | 目标/证据 | 观察 |",
        "|---|---:|---:|",
        f"| TNR mean | {expected['tnr_mean']:.3f} | {observed['tnr_mean']['mean']:.3f} |",
        f"| TNR SD | {expected['tnr_sd']:.3f} | {observed['tnr_mean']['sd']:.3f} |",
        f"| 异质病灶比例 | {expected['heterogeneous_fraction']:.3f} | {observed['heterogeneous_fraction']:.3f} |",
        f"| 患者内相关 | 文献未报告 | {observed['engineering_within_patient_correlation']:.3f}（工程模型） |",
        "",
        "## 尺寸相关坏死工程函数",
        "",
        "| Dmax (mm) | 坏死概率 |",
        "|---:|---:|",
    ]
    lines.extend(
        f"| {diameter} | {probability:.4f} |"
        for diameter, probability in observed["necrosis_probability_by_dmax_mm"].items()
    )
    lines.extend(
        [
            "",
            "TNR 与总体异质性是 HCC 病灶级文献边际；患者内相关结构、低频场和尺寸→坏死映射是显式 `engineering_prior`。注射区域为 `coverage_sampling`，不是疾病 prevalence。",
            "",
            "`tumor_dominant_low_background`、`extreme_low_uptake` 与 territory mismatch 仅属于 population-weight-zero challenge，不进入上述主统计。",
            "",
            "## 自动门禁",
            "",
        ]
    )
    lines.extend(f"- [{'x' if passed else ' '}] `{name}`" for name, passed in report["gates"].items())
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Task 5 activity targets.")
    parser.add_argument("--lesion-count", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=20_260_714)
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "docs" / "reports")
    args = parser.parse_args()
    report = build_report(lesion_count=args.lesion_count, seed=args.seed)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "task5_activity_v2_validation.json"
    markdown_path = args.output_dir / "task5_activity_v2_validation.md"
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    markdown_path.write_text(_markdown(report), encoding="utf-8", newline="\n")
    print(json.dumps({"status": report["status"], "json": str(json_path), "markdown": str(markdown_path)}))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
