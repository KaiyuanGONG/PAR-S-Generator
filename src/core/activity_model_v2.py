from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping

import numpy as np
from scipy import ndimage

from .liver_geometry import LiverGeometryV2
from .schemas_v2 import ActivityTargetV2, PatientSampleV2, PopulationProfileV2
from .tumor_generator_v2 import TumorGeometryV2


INJECTION_TERRITORIES = ("whole_liver", "right_lobar", "left_lobar", "sector_proxy")
ACTIVITY_PATTERNS = (
    "physiologic_heterogeneous",
    "tumor_dominant_low_background",
    "extreme_low_uptake",
)
LEFT_REGION_LABELS = (1, 2, 3)
RIGHT_REGION_LABELS = (4, 5)


@dataclass(frozen=True)
class LesionActivityMetricsV2:
    instance_id: int
    target_tnr_mean: float
    actual_tnr_mean: float
    actual_tnr_max: float
    background_mean: float
    coverage_fraction: float
    heterogeneous: bool
    necrotic: bool
    necrotic_fraction: float
    necrotic_core_mean: float | None
    viable_rim_mean: float
    dmax_mm: float
    evidence_types: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ActivityFieldV2:
    activity_relative: np.ndarray
    activity_probability: np.ndarray
    perfusion_mask: np.ndarray
    necrosis_mask: np.ndarray
    lesion_metrics: tuple[LesionActivityMetricsV2, ...]
    injection_territory: str
    sector_proxy_label: int | None
    activity_pattern: str
    perfused_volume_ml: float
    injection_tumor_coverage_fraction: float
    mismatch_challenge: bool
    within_patient_correlation_assumption: str
    evidence_types: Mapping[str, str] = field(default_factory=dict)
    background_field_definition: str = "isotropic_low_frequency_no_fixed_axis_gradient"
    status: str = "pass"


def _model(profile: PopulationProfileV2) -> Mapping[str, object]:
    value = profile.value("activity_model_v2")
    if not isinstance(value, Mapping):
        raise TypeError("activity_model_v2 must be a mapping")
    return value


def _validate_inputs(
    patient: PatientSampleV2,
    liver: LiverGeometryV2,
    tumors: TumorGeometryV2,
) -> None:
    if not isinstance(patient, PatientSampleV2):
        raise TypeError("patient must be PatientSampleV2")
    if not isinstance(liver, LiverGeometryV2):
        raise TypeError("liver must be LiverGeometryV2")
    if not isinstance(tumors, TumorGeometryV2):
        raise TypeError("tumors must be TumorGeometryV2")
    if liver.mask.shape != tumors.instance_mask.shape:
        raise ValueError("liver and tumor masks must have the same shape")
    if np.any((tumors.instance_mask > 0) & ~liver.mask):
        raise ValueError("tumors must be completely contained in liver")


def _territory_mask(
    liver: LiverGeometryV2,
    territory: str,
    sector_proxy_label: int | None,
) -> np.ndarray:
    if territory not in INJECTION_TERRITORIES:
        raise ValueError(f"injection_territory must be one of {INJECTION_TERRITORIES}")
    labels = np.asarray(liver.region_labels)
    if territory == "whole_liver":
        if sector_proxy_label is not None:
            raise ValueError("sector_proxy_label is only valid for sector_proxy")
        return np.asarray(liver.mask, dtype=bool)
    if territory == "right_lobar":
        if sector_proxy_label is not None:
            raise ValueError("sector_proxy_label is only valid for sector_proxy")
        return np.isin(labels, RIGHT_REGION_LABELS)
    if territory == "left_lobar":
        if sector_proxy_label is not None:
            raise ValueError("sector_proxy_label is only valid for sector_proxy")
        return np.isin(labels, LEFT_REGION_LABELS)
    if sector_proxy_label not in (1, 2, 3, 4, 5):
        raise ValueError("sector_proxy requires sector_proxy_label within [1, 5]")
    result = labels == int(sector_proxy_label)
    if not result.any():
        raise ValueError("selected sector_proxy_label is empty")
    return result


def _coverage(tumor_mask: np.ndarray, territory_mask: np.ndarray) -> float:
    count = int(np.count_nonzero(tumor_mask))
    if count == 0:
        return 0.0
    return float(np.count_nonzero(tumor_mask & territory_mask) / count)


def _sample_truncated_normal(
    rng: np.random.Generator,
    mean: float,
    sd: float,
    lower: float,
    upper: float,
) -> float:
    for _ in range(256):
        value = float(rng.normal(mean, sd))
        if lower <= value <= upper:
            return value
    raise RuntimeError("truncated normal sampling exhausted")


def _sample_lesion_tnrs(
    profile: PopulationProfileV2,
    instance_ids: tuple[int, ...],
    rng: np.random.Generator,
) -> dict[int, float]:
    mean = float(profile.value("tnr_mean_reference"))
    sd = float(profile.value("tnr_mean_sd"))
    lower, upper = map(float, profile.value("tnr_mean_range"))
    log_variance = math.log1p((sd / mean) ** 2)
    log_sd = math.sqrt(log_variance)
    log_mean = math.log(mean) - 0.5 * log_variance
    shared_fraction = float(_model(profile)["within_patient_shared_variance_fraction"])
    if not 0.0 <= shared_fraction <= 1.0:
        raise ValueError("within_patient_shared_variance_fraction must be within [0, 1]")
    shared_z = float(rng.normal())
    result: dict[int, float] = {}
    for instance_id in instance_ids:
        for _ in range(256):
            latent = math.sqrt(shared_fraction) * shared_z + math.sqrt(
                1.0 - shared_fraction
            ) * float(rng.normal())
            value = math.exp(log_mean + log_sd * latent)
            if lower <= value <= upper:
                result[instance_id] = float(value)
                break
        else:
            result[instance_id] = _sample_truncated_normal(rng, mean, sd, lower, upper)
    return result


def _candidate_territories(
    liver: LiverGeometryV2,
    tumor_union: np.ndarray,
) -> list[tuple[str, int | None, float]]:
    result: list[tuple[str, int | None, float]] = []
    for territory in INJECTION_TERRITORIES[:-1]:
        mask = _territory_mask(liver, territory, None)
        result.append((territory, None, _coverage(tumor_union, mask)))
    for label in (1, 2, 3, 4, 5):
        mask = np.asarray(liver.region_labels) == label
        if mask.any():
            result.append(("sector_proxy", label, _coverage(tumor_union, mask)))
    return result


def necrosis_probability_for_diameter(
    dmax_mm: float,
    profile: PopulationProfileV2,
) -> float:
    if not math.isfinite(dmax_mm) or dmax_mm <= 0.0:
        raise ValueError("dmax_mm must be positive and finite")
    model = _model(profile)
    midpoint = float(model["necrosis_probability_midpoint_mm"])
    scale_mm = float(model["necrosis_probability_scale_mm"])
    if scale_mm <= 0.0:
        raise ValueError("necrosis_probability_scale_mm must be positive")
    return float(1.0 / (1.0 + math.exp(-(dmax_mm - midpoint) / scale_mm)))


def sample_activity_target(
    patient: PatientSampleV2,
    liver: LiverGeometryV2,
    tumors: TumorGeometryV2,
    profile: PopulationProfileV2,
    rng: np.random.Generator,
    *,
    injection_territory: str | None = None,
    sector_proxy_label: int | None = None,
    activity_pattern: str = "physiologic_heterogeneous",
    mismatch_challenge: bool = False,
) -> ActivityTargetV2:
    """Sample lesion-level HCC uptake while keeping injection territory non-population."""
    _validate_inputs(patient, liver, tumors)
    if not isinstance(rng, np.random.Generator):
        raise TypeError("rng must be numpy.random.Generator")
    if activity_pattern not in ACTIVITY_PATTERNS:
        raise ValueError(f"activity_pattern must be one of {ACTIVITY_PATTERNS}")

    tumor_union = tumors.instance_mask > 0
    candidates = _candidate_territories(liver, tumor_union)
    if injection_territory is not None:
        territory_mask = _territory_mask(liver, injection_territory, sector_proxy_label)
        chosen = (injection_territory, sector_proxy_label, _coverage(tumor_union, territory_mask))
        if mismatch_challenge != (chosen[2] < 1.0):
            raise ValueError("mismatch_challenge must exactly describe incomplete tumor coverage")
    else:
        desired = profile.value("injection_territories")
        if not isinstance(desired, Mapping):
            raise TypeError("injection_territories must be a probability mapping")
        eligible = [item for item in candidates if (item[2] < 1.0) == mismatch_challenge]
        if not eligible:
            raise ValueError("no injection territory satisfies requested mismatch state")
        sector_count = sum(item[0] == "sector_proxy" for item in eligible)
        weights = []
        for territory, _, _ in eligible:
            weight = float(desired[territory])
            if territory == "sector_proxy":
                weight /= max(sector_count, 1)
            weights.append(weight)
        probabilities = np.asarray(weights, dtype=np.float64)
        probabilities /= probabilities.sum()
        chosen = eligible[int(rng.choice(len(eligible), p=probabilities))]

    instance_ids = tuple(metric.instance_id for metric in tumors.lesion_metrics)
    lesion_tnrs = _sample_lesion_tnrs(profile, instance_ids, rng)
    heterogeneous_fraction = float(profile.value("heterogeneous_fraction"))
    lesion_heterogeneous = {
        instance_id: bool(rng.random() < heterogeneous_fraction)
        for instance_id in instance_ids
    }
    return ActivityTargetV2(
        injection_territory=chosen[0],
        sector_proxy_label=chosen[1],
        activity_pattern=activity_pattern,
        tnr_mean=float(np.mean(tuple(lesion_tnrs.values()))),
        heterogeneous=any(lesion_heterogeneous.values()),
        mismatch_challenge=mismatch_challenge,
        lesion_tnr_means=lesion_tnrs,
        lesion_heterogeneous=lesion_heterogeneous,
        within_patient_correlation_assumption=(
            "unknown_in_literature_shared_case_effect_plus_lesion_residual_engineering_prior"
        ),
        evidence_types={
            "tnr_mean": profile.parameters["tnr_mean_reference"].source_type,
            "heterogeneous": profile.parameters["heterogeneous_fraction"].source_type,
            "injection_territory": profile.parameters["injection_territories"].source_type,
            "within_patient_correlation": profile.parameters["activity_model_v2"].source_type,
            "activity_pattern": (
                "stress_test"
                if activity_pattern != "physiologic_heterogeneous"
                else profile.parameters["activity_patterns"].source_type
            ),
        },
    )


def _standardized_low_frequency_field(
    shape: tuple[int, int, int],
    support: np.ndarray,
    sigma_voxels: float,
    rng: np.random.Generator,
) -> np.ndarray:
    noise = rng.normal(size=shape).astype(np.float32)
    smooth = ndimage.gaussian_filter(noise, sigma=sigma_voxels, mode="reflect")
    values = smooth[support]
    if values.size == 0:
        raise ValueError("low-frequency field support is empty")
    sd = float(values.std(ddof=0))
    if sd <= 1e-8:
        return np.zeros(shape, dtype=np.float32)
    return ((smooth - float(values.mean())) / sd).astype(np.float32)


def _lesion_crop(mask: np.ndarray, padding: int = 2) -> tuple[tuple[slice, slice, slice], np.ndarray]:
    indices = np.argwhere(mask)
    lower = np.maximum(indices.min(axis=0) - padding, 0)
    upper = np.minimum(indices.max(axis=0) + padding + 1, mask.shape)
    slices = tuple(slice(int(lo), int(hi)) for lo, hi in zip(lower, upper))
    return slices, mask[slices]


def _necrotic_core(
    lesion: np.ndarray,
    desired_fraction: float,
    voxel_size_mm: float,
) -> np.ndarray:
    distances = ndimage.distance_transform_edt(
        lesion,
        sampling=(voxel_size_mm,) * 3,
    )
    values = distances[lesion]
    if values.size < 8 or desired_fraction <= 0.0:
        return np.zeros_like(lesion, dtype=bool)
    threshold = float(np.quantile(values, 1.0 - desired_fraction))
    core = lesion & (distances >= threshold)
    if np.count_nonzero(core) >= np.count_nonzero(lesion):
        return np.zeros_like(lesion, dtype=bool)
    return core


def _target_maps(
    target: ActivityTargetV2,
    instance_ids: tuple[int, ...],
) -> tuple[dict[int, float], dict[int, bool]]:
    if target.lesion_tnr_means:
        if set(target.lesion_tnr_means) != set(instance_ids):
            raise ValueError("lesion_tnr_means must contain every realized instance ID exactly once")
        tnr = {int(key): float(value) for key, value in target.lesion_tnr_means.items()}
    else:
        tnr = {instance_id: float(target.tnr_mean) for instance_id in instance_ids}
    if target.lesion_heterogeneous:
        if set(target.lesion_heterogeneous) != set(instance_ids):
            raise ValueError(
                "lesion_heterogeneous must contain every realized instance ID exactly once"
            )
        heterogeneous = {
            int(key): bool(value) for key, value in target.lesion_heterogeneous.items()
        }
    else:
        heterogeneous = {instance_id: bool(target.heterogeneous) for instance_id in instance_ids}
    if any(not math.isfinite(value) or value <= 0.0 for value in tnr.values()):
        raise ValueError("all target TNR means must be positive and finite")
    return tnr, heterogeneous


def generate_activity_field(
    patient: PatientSampleV2,
    liver: LiverGeometryV2,
    tumors: TumorGeometryV2,
    target: ActivityTargetV2,
    profile: PopulationProfileV2,
    rng: np.random.Generator,
) -> ActivityFieldV2:
    """Generate clean relative uptake and a normalized source probability field."""
    _validate_inputs(patient, liver, tumors)
    if not isinstance(target, ActivityTargetV2):
        raise TypeError("target must be ActivityTargetV2")
    if not isinstance(rng, np.random.Generator):
        raise TypeError("rng must be numpy.random.Generator")
    if target.activity_pattern not in ACTIVITY_PATTERNS:
        raise ValueError(f"activity_pattern must be one of {ACTIVITY_PATTERNS}")
    if (
        target.activity_pattern != "physiologic_heterogeneous"
        and target.evidence_types.get("activity_pattern") != "stress_test"
    ):
        raise ValueError("challenge activity patterns must be explicitly marked stress_test")

    liver_mask = np.asarray(liver.mask, dtype=bool)
    tumor_instances = np.asarray(tumors.instance_mask)
    tumor_union = tumor_instances > 0
    territory = _territory_mask(
        liver,
        target.injection_territory,
        target.sector_proxy_label,
    )
    territory &= liver_mask
    union_coverage = _coverage(tumor_union, territory)
    incomplete = union_coverage < 1.0
    if incomplete != bool(target.mismatch_challenge):
        raise ValueError(
            "mismatch_challenge must be true exactly when injection territory incompletely covers tumors"
        )

    model = _model(profile)
    affine = np.asarray(liver.affine_4x4, dtype=np.float64)
    voxel_spacing = np.linalg.norm(affine[:3, :3], axis=0)
    if not np.allclose(voxel_spacing, voxel_spacing[0], atol=1e-6):
        raise ValueError("activity model currently requires isotropic voxel spacing")
    voxel_size_mm = float(voxel_spacing[0])
    residual = float(model["residual_outside_territory_fraction"])
    if not 0.0 <= residual < 1.0:
        raise ValueError("residual_outside_territory_fraction must be within [0, 1)")

    background_log_sd = float(model["background_log_sd"])
    background_sigma = float(model["background_correlation_length_mm"]) / voxel_size_mm
    background_z = _standardized_low_frequency_field(
        liver_mask.shape,
        liver_mask,
        background_sigma,
        rng,
    )
    background = np.exp(
        background_log_sd * background_z - 0.5 * background_log_sd**2
    ).astype(np.float32)
    activity = np.zeros(liver_mask.shape, dtype=np.float32)
    activity[liver_mask] = residual * background[liver_mask]
    activity[territory] = background[territory]
    if target.activity_pattern == "tumor_dominant_low_background":
        activity[liver_mask] *= 0.10

    normal_reference = territory & liver_mask & ~tumor_union
    if not normal_reference.any():
        normal_reference = liver_mask & ~tumor_union
    if not normal_reference.any():
        raise ValueError("normal-liver background reference is empty")
    background_mean = float(activity[normal_reference].mean())
    if not math.isfinite(background_mean) or background_mean <= 0.0:
        raise RuntimeError("normal-liver background mean is invalid")

    instance_ids = tuple(metric.instance_id for metric in tumors.lesion_metrics)
    target_tnrs, heterogeneous_map = _target_maps(target, instance_ids)
    placement_by_id = {record.instance_id: record for record in tumors.placements}
    metrics: list[LesionActivityMetricsV2] = []
    necrosis_instances = np.zeros(liver_mask.shape, dtype=np.uint16)
    tumor_log_sd = float(model["tumor_log_sd"])
    tumor_sigma = float(model["tumor_correlation_length_mm"]) / voxel_size_mm
    necrosis_lower, necrosis_upper = map(float, model["necrosis_fraction_range"])
    core_uptake_lower, core_uptake_upper = map(
        float, model["necrotic_core_uptake_fraction_range"]
    )

    for instance_id in instance_ids:
        lesion_mask = tumor_instances == instance_id
        slices, lesion_crop = _lesion_crop(lesion_mask)
        heterogeneous = heterogeneous_map[instance_id]
        if heterogeneous:
            local_z = _standardized_low_frequency_field(
                lesion_crop.shape,
                lesion_crop,
                tumor_sigma,
                rng,
            )
            local_field = np.exp(
                tumor_log_sd * local_z - 0.5 * tumor_log_sd**2
            ).astype(np.float32)
        else:
            local_field = np.ones(lesion_crop.shape, dtype=np.float32)
        local_field[~lesion_crop] = 0.0

        dmax_mm = float(placement_by_id[instance_id].target.dmax_mm)
        necrosis_probability = necrosis_probability_for_diameter(dmax_mm, profile)
        necrotic = bool(heterogeneous and rng.random() < necrosis_probability)
        core = np.zeros_like(lesion_crop, dtype=bool)
        if necrotic:
            desired_fraction = (
                necrosis_lower
                + (necrosis_upper - necrosis_lower)
                * necrosis_probability
                * float(rng.uniform(0.65, 1.0))
            )
            core = _necrotic_core(lesion_crop, desired_fraction, voxel_size_mm)
            necrotic = bool(core.any())
        if necrotic:
            rim = lesion_crop & ~core
            rim_mean_raw = float(local_field[rim].mean())
            core_fraction = float(rng.uniform(core_uptake_lower, core_uptake_upper))
            local_field[core] = np.minimum(
                local_field[core] * core_fraction,
                0.80 * rim_mean_raw,
            )
            local_necrosis = necrosis_instances[slices]
            local_necrosis[core] = instance_id
            necrosis_instances[slices] = local_necrosis
        else:
            rim = lesion_crop

        raw_mean = float(local_field[lesion_crop].mean())
        desired_mean = target_tnrs[instance_id] * background_mean
        local_values = local_field * (desired_mean / raw_mean)
        local_territory = territory[slices]
        local_values[lesion_crop & ~local_territory] *= residual
        destination = activity[slices]
        destination[lesion_crop] = local_values[lesion_crop]
        activity[slices] = destination

        lesion_values = activity[lesion_mask]
        actual_tnr_mean = float(lesion_values.mean() / background_mean)
        actual_tnr_max = max(
            actual_tnr_mean,
            float(lesion_values.max() / background_mean),
        )
        coverage_fraction = _coverage(lesion_mask, territory)
        necrotic_fraction = float(np.count_nonzero(core) / np.count_nonzero(lesion_crop))
        core_mean = float(local_values[core].mean()) if necrotic else None
        rim_mean = float(local_values[rim].mean())
        if necrotic and not core_mean < rim_mean:
            raise RuntimeError("necrotic core uptake must be below viable rim uptake")
        metrics.append(
            LesionActivityMetricsV2(
                instance_id=instance_id,
                target_tnr_mean=target_tnrs[instance_id],
                actual_tnr_mean=actual_tnr_mean,
                actual_tnr_max=actual_tnr_max,
                background_mean=background_mean,
                coverage_fraction=coverage_fraction,
                heterogeneous=heterogeneous,
                necrotic=necrotic,
                necrotic_fraction=necrotic_fraction,
                necrotic_core_mean=core_mean,
                viable_rim_mean=rim_mean,
                dmax_mm=dmax_mm,
                evidence_types={
                    "target_tnr_mean": target.evidence_types.get(
                        "tnr_mean", "unspecified"
                    ),
                    "heterogeneous": target.evidence_types.get(
                        "heterogeneous", "unspecified"
                    ),
                    "necrosis_mapping": profile.parameters[
                        "activity_model_v2"
                    ].source_type,
                },
            )
        )

    activity[~liver_mask] = 0.0
    if not np.isfinite(activity).all() or np.any(activity < 0.0):
        raise RuntimeError("activity_relative must be finite and non-negative")
    total = float(activity.sum(dtype=np.float64))
    if total <= 0.0:
        raise RuntimeError("activity_relative has zero sum")
    probability = (activity.astype(np.float64) / total).astype(np.float32)
    probability /= probability.sum(dtype=np.float64)
    if not math.isclose(float(probability.sum(dtype=np.float64)), 1.0, abs_tol=2e-7):
        raise RuntimeError("activity_probability normalization failed")

    voxel_volume_ml = abs(float(np.linalg.det(affine[:3, :3]))) / 1000.0
    return ActivityFieldV2(
        activity_relative=activity.astype(np.float32, copy=False),
        activity_probability=probability,
        perfusion_mask=territory.astype(np.uint8),
        necrosis_mask=necrosis_instances,
        lesion_metrics=tuple(metrics),
        injection_territory=target.injection_territory,
        sector_proxy_label=target.sector_proxy_label,
        activity_pattern=target.activity_pattern,
        perfused_volume_ml=float(np.count_nonzero(territory) * voxel_volume_ml),
        injection_tumor_coverage_fraction=union_coverage,
        mismatch_challenge=target.mismatch_challenge,
        within_patient_correlation_assumption=(
            target.within_patient_correlation_assumption
        ),
        evidence_types={
            "tnr_mean": target.evidence_types.get("tnr_mean", "unspecified"),
            "heterogeneous": target.evidence_types.get(
                "heterogeneous", "unspecified"
            ),
            "injection_territory": target.evidence_types.get(
                "injection_territory", "unspecified"
            ),
            "activity_field": profile.parameters["activity_model_v2"].source_type,
        },
    )
