from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping

import numpy as np
from scipy import ndimage


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.population_sampler import sample_liver_target, sample_patient  # noqa: E402
from core.liver_geometry import (  # noqa: E402
    GridSpecV2,
    fit_liver_geometry,
    shape_coordinates_for_target,
)
from core.schemas_v2 import (  # noqa: E402
    LiverTargetV2,
    PatientSampleV2,
    PopulationProfileV2,
    load_evidence_registry,
    load_profile,
)


def load_main_profile(repo_root: Path = REPO_ROOT) -> PopulationProfileV2:
    registry = load_evidence_registry(repo_root / "configs" / "evidence_registry_v2.json")
    return load_profile(repo_root / "configs" / "population_tare_hcc_nopvi_v2.json", registry)


@dataclass(frozen=True)
class RepresentativeTargetV2:
    patient: PatientSampleV2
    target: LiverTargetV2
    selection_role: str
    features: Mapping[str, float]
    selection_thresholds: Mapping[str, float]


def make_controlled_cirrhotic_target(
    normal_target: LiverTargetV2,
    cirrhotic_reference: LiverTargetV2,
) -> LiverTargetV2:
    """Hold global size/position fixed while applying the cirrhotic phenotype."""
    return replace(
        normal_target,
        morphology="cirrhotic",
        left_fraction=cirrhotic_reference.left_fraction,
        s1_3_to_s4_8_ratio=cirrhotic_reference.s1_3_to_s4_8_ratio,
        caudate_fraction=cirrhotic_reference.caudate_fraction,
        surface_roughness_target=cirrhotic_reference.surface_roughness_target,
        surface_field_amplitude=cirrhotic_reference.surface_field_amplitude,
        caudate_enabled=True,
        evidence_types=dict(cirrhotic_reference.evidence_types),
    )


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
    bbox_fill = volume * 1000.0 / (si * ap * lr)
    left = np.array([target.left_fraction for target in targets])
    segment_ratio = np.array([target.s1_3_to_s4_8_ratio for target in targets])
    caudate = np.array([target.caudate_fraction for target in targets])
    roughness = np.array([target.surface_roughness_target for target in targets])
    normal_selector = is_cirrhotic == 0
    cirrhotic_selector = is_cirrhotic == 1

    expected_male = float(profile.value("male_fraction_auxiliary"))
    expected_cirrhosis = float(profile.value("cirrhosis_prevalence"))
    volume_reference = profile.value("liver_volume_reference_ml")
    extent_reference = profile.value("liver_extent_reference_mm_zyx")
    left_reference = profile.value("left_liver_fraction_reference")
    geometry_model = profile.value("liver_geometry_model")
    reference_extents = np.asarray(extent_reference["mean_at_profile_volume"], dtype=np.float64)
    reference_bbox_fill = float(
        float(volume_reference["mean"]) * 1000.0 / np.prod(reference_extents)
    )
    configured_bbox_range = np.asarray(
        geometry_model["bbox_fill_fraction_range"], dtype=np.float64
    )
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
        "extent_means": bool(
            np.all(np.abs(np.asarray((si.mean(), ap.mean(), lr.mean())) / reference_extents - 1.0) <= 0.05)
        ),
        "bbox_fill_center": abs(float(np.median(bbox_fill)) - reference_bbox_fill) <= 0.02,
        "bbox_fill_variation": float(bbox_fill.std(ddof=0)) >= 0.008,
        "bbox_fill_support": bool(
            bbox_fill.min() >= configured_bbox_range[0] - 1e-12
            and bbox_fill.max() <= configured_bbox_range[1] + 1e-12
        ),
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
        "schema_version": "pars_task3_population_validation_v2",
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
            "liver_extent_mm_zyx": {
                **extent_reference,
                "source_type": profile.parameters["liver_extent_reference_mm_zyx"].source_type,
            },
            "bbox_fill_fraction": {
                "reference_at_profile_mean": reference_bbox_fill,
                "configured_engineering_support": configured_bbox_range.tolist(),
                "source_type": "derived_literature_plus_engineering_joint_prior",
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
            "bbox_fill_fraction": _describe(bbox_fill),
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
    candidate_count: int = 8192,
) -> list[RepresentativeTargetV2]:
    """Select centre, quantile-edge, and fixed production-stress geometry cases."""
    if candidate_count < 1000:
        raise ValueError("candidate_count must be >= 1000")
    rng = np.random.default_rng(seed)
    candidates: list[tuple[PatientSampleV2, LiverTargetV2, dict[str, float]]] = []
    for index in range(candidate_count):
        patient = sample_patient(profile, rng, case_id=f"task3_voxel_candidate_{index:05d}")
        target = sample_liver_target(patient, profile, rng)
        shape_u, shape_v = shape_coordinates_for_target(target)
        candidates.append(
            (
                patient,
                target,
                {
                    "volume_ml": float(target.volume_ml),
                    "si_mm": float(target.si_mm),
                    "ap_mm": float(target.ap_mm),
                    "lr_mm": float(target.lr_mm),
                    "left_fraction": float(target.left_fraction),
                    "shape_u": float(shape_u),
                    "shape_v": float(shape_v),
                },
            )
        )

    feature_names = tuple(candidates[0][2])
    arrays = {
        name: np.asarray([candidate[2][name] for candidate in candidates], dtype=np.float64)
        for name in feature_names
    }
    quantiles = {
        name: {
            "p05": float(np.quantile(values, 0.05)),
            "p10": float(np.quantile(values, 0.10)),
            "p50": float(np.quantile(values, 0.50)),
            "p90": float(np.quantile(values, 0.90)),
            "p95": float(np.quantile(values, 0.95)),
        }
        for name, values in arrays.items()
    }
    robust_scales = {
        name: max(float(np.quantile(values, 0.75) - np.quantile(values, 0.25)), 1e-9)
        for name, values in arrays.items()
    }
    used_ids: set[str] = set()
    selected: list[RepresentativeTargetV2] = []

    def choose(
        role: str,
        stratum: tuple[str, bool],
        predicate,
        score,
        thresholds: Mapping[str, float],
    ) -> None:
        eligible = [
            candidate
            for candidate in candidates
            if candidate[0].case_id not in used_ids
            and (candidate[1].morphology, bool(candidate[1].caudate_enabled)) == stratum
            and predicate(candidate[2])
        ]
        if not eligible:
            raise RuntimeError(f"no eligible candidate for representative role {role}")
        patient, target, features = min(eligible, key=lambda candidate: score(candidate[2]))
        used_ids.add(patient.case_id)
        selected.append(
            RepresentativeTargetV2(
                patient=patient,
                target=target,
                selection_role=role,
                features=dict(features),
                selection_thresholds=dict(thresholds),
            )
        )

    strata = (
        ("normal", False),
        ("normal", True),
        ("cirrhotic", False),
        ("cirrhotic", True),
    )
    centre_features = ("volume_ml", "si_mm", "ap_mm", "lr_mm", "left_fraction")
    for morphology, caudate_enabled in strata:
        stratum_candidates = [
            candidate
            for candidate in candidates
            if (candidate[1].morphology, bool(candidate[1].caudate_enabled))
            == (morphology, caudate_enabled)
        ]
        medians = {
            name: float(np.median([candidate[2][name] for candidate in stratum_candidates]))
            for name in centre_features
        }
        choose(
            f"centre-{morphology}-caudate-{'on' if caudate_enabled else 'off'}",
            (morphology, caudate_enabled),
            lambda _: True,
            lambda features, medians=medians: sum(
                ((features[name] - medians[name]) / robust_scales[name]) ** 2
                for name in centre_features
            )
            + (features["shape_u"] / robust_scales["shape_u"]) ** 2
            + (features["shape_v"] / robust_scales["shape_v"]) ** 2,
            medians,
        )

    size_features = ("volume_ml", "si_mm", "ap_mm", "lr_mm")
    low_size_thresholds = {name: quantiles[name]["p10"] for name in size_features}
    high_size_thresholds = {name: quantiles[name]["p90"] for name in size_features}
    choose(
        "joint-size-p10",
        ("normal", True),
        lambda features: all(features[name] <= low_size_thresholds[name] for name in size_features),
        lambda features: sum(
            ((features[name] - low_size_thresholds[name]) / robust_scales[name]) ** 2
            for name in size_features
        ),
        low_size_thresholds,
    )
    choose(
        "joint-size-p90",
        ("cirrhotic", True),
        lambda features: all(features[name] >= high_size_thresholds[name] for name in size_features),
        lambda features: sum(
            ((features[name] - high_size_thresholds[name]) / robust_scales[name]) ** 2
            for name in size_features
        ),
        high_size_thresholds,
    )

    edge_specs = (
        ("left-p05", ("normal", False), "left_fraction", "p05", -1),
        ("left-p95", ("cirrhotic", False), "left_fraction", "p95", 1),
        ("shape-u-p05", ("normal", True), "shape_u", "p05", -1),
        ("shape-u-p95", ("cirrhotic", True), "shape_u", "p95", 1),
        ("shape-v-p05", ("normal", False), "shape_v", "p05", -1),
        ("shape-v-p95", ("cirrhotic", False), "shape_v", "p95", 1),
    )
    for role, stratum, feature_name, quantile_name, direction in edge_specs:
        threshold = quantiles[feature_name][quantile_name]
        choose(
            role,
            stratum,
            (
                (lambda features, name=feature_name, value=threshold: features[name] <= value)
                if direction < 0
                else (lambda features, name=feature_name, value=threshold: features[name] >= value)
            ),
            lambda features, name=feature_name, value=threshold: abs(features[name] - value),
            {f"{feature_name}_{quantile_name}": threshold},
        )

    stress_rng = np.random.default_rng(987_654)
    stress_roles = {
        8: "stress-cirrhotic-caudate-upper",
        11: "stress-cirrhotic-left-upper",
    }
    for index in range(max(stress_roles) + 1):
        patient = sample_patient(
            profile,
            stress_rng,
            case_id=f"task3_stress_seed987654_{index:05d}",
        )
        target = sample_liver_target(patient, profile, stress_rng)
        if index not in stress_roles:
            continue
        shape_u, shape_v = shape_coordinates_for_target(target)
        selected.append(
            RepresentativeTargetV2(
                patient=patient,
                target=target,
                selection_role=stress_roles[index],
                features={
                    "volume_ml": float(target.volume_ml),
                    "si_mm": float(target.si_mm),
                    "ap_mm": float(target.ap_mm),
                    "lr_mm": float(target.lr_mm),
                    "left_fraction": float(target.left_fraction),
                    "shape_u": float(shape_u),
                    "shape_v": float(shape_v),
                },
                selection_thresholds={
                    "stress_seed": 987_654.0,
                    "sequence_index": float(index),
                },
            )
        )

    if len(selected) != 14:
        raise RuntimeError(f"representative selector produced {len(selected)} rather than 14 cases")
    return selected


def build_voxel_validation(
    profile: PopulationProfileV2,
    *,
    seed: int = 20_260_714,
    grid: GridSpecV2 | None = None,
) -> dict:
    grid = grid or GridSpecV2()
    selected = select_representative_targets(profile, seed=seed)
    rows = []
    geometries_by_role = {}
    for representative in selected:
        patient = representative.patient
        target = representative.target
        geometry = fit_liver_geometry(target, grid)
        geometries_by_role[representative.selection_role] = geometry
        actual = geometry.actual_metrics
        target_extents = np.asarray((target.si_mm, target.ap_mm, target.lr_mm))
        actual_extents = np.asarray(actual["extent_mm_zyx"])
        target_centroid = np.asarray(target.centroid_mm)
        actual_centroid = np.asarray(actual["centroid_world_mm"])
        shape_quality = dict(actual["shape_quality"])
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
            "shape_quality": shape_quality["status"] == "pass"
            and all(shape_quality["gates"].values()),
            "shape_coordinates": bool(
                np.allclose(
                    (
                        geometry.continuous_parameters["shape_variation_coordinate"],
                        geometry.continuous_parameters["shape_transverse_coordinate"],
                    ),
                    (representative.features["shape_u"], representative.features["shape_v"]),
                    rtol=0.0,
                    atol=1e-12,
                )
            ),
        }
        gates = {name: bool(value) for name, value in gates.items()}
        rows.append(
            {
                "case_id": patient.case_id,
                "selection_role": representative.selection_role,
                "selection_features": dict(representative.features),
                "selection_thresholds": dict(representative.selection_thresholds),
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
                    "shape_quality": shape_quality,
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
    representative_by_role = {
        representative.selection_role: representative for representative in selected
    }
    normal_centre = representative_by_role["centre-normal-caudate-on"]
    cirrhotic_centre = representative_by_role["centre-cirrhotic-caudate-on"]
    controlled_cirrhotic_target = make_controlled_cirrhotic_target(
        normal_centre.target,
        cirrhotic_centre.target,
    )
    controlled_normal_geometry = geometries_by_role["centre-normal-caudate-on"]
    controlled_cirrhotic_geometry = fit_liver_geometry(controlled_cirrhotic_target, grid)
    controlled_normal_roughness = float(
        controlled_normal_geometry.actual_metrics["surface_roughness"]
    )
    controlled_cirrhotic_roughness = float(
        controlled_cirrhotic_geometry.actual_metrics["surface_roughness"]
    )
    controlled_roughness_delta = (
        controlled_cirrhotic_roughness - controlled_normal_roughness
    )
    controlled_pair_gates = {
        "shape_quality": controlled_cirrhotic_geometry.actual_metrics["shape_quality"]["status"]
        == "pass",
        "volume": abs(
            float(controlled_cirrhotic_geometry.actual_metrics["volume_ml"])
            / controlled_cirrhotic_target.volume_ml
            - 1.0
        )
        <= 0.04,
        "roughness_delta": controlled_roughness_delta >= 0.01,
    }
    expected_roles = {
        "centre-normal-caudate-off",
        "centre-normal-caudate-on",
        "centre-cirrhotic-caudate-off",
        "centre-cirrhotic-caudate-on",
        "joint-size-p10",
        "joint-size-p90",
        "left-p05",
        "left-p95",
        "shape-u-p05",
        "shape-u-p95",
        "shape-v-p05",
        "shape-v-p95",
        "stress-cirrhotic-caudate-upper",
        "stress-cirrhotic-left-upper",
    }
    by_role = {row["selection_role"]: row for row in rows}
    core_rows = [row for row in rows if not row["selection_role"].startswith("stress-")]
    stratum_counts = {
        f"{morphology}_caudate_{'on' if enabled else 'off'}": sum(
            row["morphology"] == morphology and row["caudate_enabled"] is enabled
            for row in core_rows
        )
        for morphology in ("normal", "cirrhotic")
        for enabled in (False, True)
    }
    size_names = ("volume_ml", "si_mm", "ap_mm", "lr_mm")
    low_size = by_role.get("joint-size-p10")
    high_size = by_role.get("joint-size-p90")
    edge_roles = (
        ("left-p05", "left_fraction", "left_fraction_p05", -1),
        ("left-p95", "left_fraction", "left_fraction_p95", 1),
        ("shape-u-p05", "shape_u", "shape_u_p05", -1),
        ("shape-u-p95", "shape_u", "shape_u_p95", 1),
        ("shape-v-p05", "shape_v", "shape_v_p05", -1),
        ("shape-v-p95", "shape_v", "shape_v_p95", 1),
    )
    edge_coverage = all(
        role in by_role
        and (
            by_role[role]["selection_features"][feature]
            <= by_role[role]["selection_thresholds"][threshold]
            if direction < 0
            else by_role[role]["selection_features"][feature]
            >= by_role[role]["selection_thresholds"][threshold]
        )
        for role, feature, threshold, direction in edge_roles
    )
    joint_size_coverage = bool(
        low_size
        and high_size
        and all(
            low_size["selection_features"][name] <= low_size["selection_thresholds"][name]
            and high_size["selection_features"][name] >= high_size["selection_thresholds"][name]
            for name in size_names
        )
    )
    aggregate_gates = {
        "all_cases": all(row["status"] == "pass" for row in rows),
        "all_shape_quality": all(
            row["actual"]["shape_quality"]["status"] == "pass" for row in rows
        ),
        "selection_case_count": len(rows) == 14,
        "selection_unique_cases": len({row["case_id"] for row in rows}) == 14,
        "selection_roles_complete": set(by_role) == expected_roles,
        "selection_strata_balanced": all(count == 3 for count in stratum_counts.values()),
        "selection_joint_size_edges": joint_size_coverage,
        "selection_left_and_shape_edges": edge_coverage,
        "fixed_production_stress_edges": all(
            by_role[role]["status"] == "pass"
            for role in (
                "stress-cirrhotic-caudate-upper",
                "stress-cirrhotic-left-upper",
            )
        ),
        "shape_coordinates_consistent": all(row["gates"]["shape_coordinates"] for row in rows),
        "controlled_cirrhotic_roughness": all(controlled_pair_gates.values()),
    }
    aggregate_gates = {name: bool(value) for name, value in aggregate_gates.items()}
    return {
        "schema_version": "pars_task3_voxel_validation_v2",
        "profile_id": profile.profile_id,
        "selection_role": "coverage_qa_not_population_prevalence",
        "seed": int(seed),
        "grid": {"shape": list(grid.shape), "voxel_size_mm": float(grid.voxel_size_mm)},
        "case_count": len(rows),
        "status": "pass" if all(aggregate_gates.values()) else "fail",
        "aggregate": {
            "normal_surface_roughness_mean": float(np.mean(normal_roughness)),
            "cirrhotic_surface_roughness_mean": float(np.mean(cirrhotic_roughness)),
            "minimum_lobe_overlap_fraction": float(
                min(row["actual"]["shape_quality"]["lobe_overlap_fraction"] for row in rows)
            ),
            "maximum_bbox_fill_absolute_error": float(
                max(row["actual"]["shape_quality"]["bbox_fill_absolute_error"] for row in rows)
            ),
            "selection_stratum_counts": stratum_counts,
            "independent_coverage_roughness_difference": float(
                np.mean(cirrhotic_roughness) - np.mean(normal_roughness)
            ),
            "controlled_morphology_pair": {
                "normal_case_id": normal_centre.patient.case_id,
                "fixed_target_volume_ml": float(normal_centre.target.volume_ml),
                "fixed_target_extent_mm_zyx": [
                    float(normal_centre.target.si_mm),
                    float(normal_centre.target.ap_mm),
                    float(normal_centre.target.lr_mm),
                ],
                "normal_surface_roughness": controlled_normal_roughness,
                "cirrhotic_surface_roughness": controlled_cirrhotic_roughness,
                "roughness_delta": controlled_roughness_delta,
                "gates": {name: bool(value) for name, value in controlled_pair_gates.items()},
            },
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
        "| 角色 | 形态 | 尾状叶 | 体积误差 | 最大三径误差 | 几何左叶误差 | 腰比 | 渐薄比 | 尾状叶外显 | fossa | 结果 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in report["cases"]:
        lines.append(
            f"| `{row['selection_role']}` | {row['morphology']} | {row['caudate_enabled']} | "
            f"{row['errors']['volume_relative_pct']:.4f}% | {row['errors']['maximum_extent_mm']:.2f} mm | "
            f"{row['actual']['shape_quality']['geometric_left_fraction_error']:+.4f} | "
            f"{row['actual']['shape_quality']['central_waist_ratio']:.4f} | "
            f"{row['actual']['shape_quality']['left_lateral_to_medial_area_ratio']:.4f} | "
            f"{row['actual']['shape_quality']['caudate_outer_fraction']:.4f} | "
            f"{row['actual']['shape_quality']['fossa_removed_fraction']:.4f} | "
            f"{row['status'].upper()} |"
        )
    lines.extend(
        [
            "",
            "## 聚合方向性",
            "",
            f"- 正常肝粗糙度均值: `{report['aggregate']['normal_surface_roughness_mean']:.4f}`",
            f"- 肝硬化粗糙度均值: `{report['aggregate']['cirrhotic_surface_roughness_mean']:.4f}`",
            f"- 独立覆盖集差值（仅描述）: `{report['aggregate']['independent_coverage_roughness_difference']:+.4f}`",
            f"- 受控配对粗糙度差值（门禁）: `{report['aggregate']['controlled_morphology_pair']['roughness_delta']:+.4f}`",
            "",
            "上述形态阈值是用于拒绝明显不自然构造的工程 QA 门禁，待真实肝脏 mask 校准；并非文献直接给出的生理解剖阈值。",
            "",
            "本报告验证固定目标的直接拟合（`shape_seed=None`），不把生产重试当作通过条件。pilot 前仍须用固定 `liver_seed` 运行真实生产小样本，并报告 first-pass rate、attempt histogram 与各失败门禁频次。",
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
        f"| SI/AP/LR 均值 (mm) | {observed['extent_si_mm']['mean']:.1f}/{observed['extent_ap_mm']['mean']:.1f}/{observed['extent_lr_mm']['mean']:.1f} | "
        f"{'/'.join(f'{value:.1f}' for value in report['expected']['liver_extent_mm_zyx']['mean_at_profile_volume'])} |",
        f"| 体积/外接框占比中位数 | {observed['bbox_fill_fraction']['median']:.4f} | {report['expected']['bbox_fill_fraction']['reference_at_profile_mean']:.4f} |",
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
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    markdown_path.write_text(_markdown_report(report), encoding="utf-8", newline="\n")
    outputs = {"population_status": report["status"], "json": str(json_path), "markdown": str(markdown_path)}
    overall_pass = report["status"] == "pass"
    if not args.skip_voxel:
        voxel_report = build_voxel_validation(profile, seed=args.voxel_seed)
        voxel_json_path = args.output_dir / "task3_liver_v2_voxel_gate.json"
        voxel_markdown_path = args.output_dir / "task3_liver_v2_voxel_gate.md"
        voxel_json_path.write_text(
            json.dumps(voxel_report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        voxel_markdown_path.write_text(
            _voxel_markdown(voxel_report), encoding="utf-8", newline="\n"
        )
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
