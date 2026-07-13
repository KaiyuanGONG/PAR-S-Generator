from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field, replace
from typing import Mapping, Sequence

import numpy as np
from scipy import ndimage

from .liver_geometry import GridSpecV2, LiverGeometryV2
from .measurements import (
    LesionMetricsV2,
    _feret_diameter_mm,
    measure_lesions,
    signed_distance_mm,
)
from .schemas_v2 import LiverTargetV2, PatientSampleV2, PopulationProfileV2, TumorTargetV2


COUNT_BINS = ("1", "2-5", ">5")
DMAX_BINS = ("10-<80_mm", "80-200_mm")
LOBE_EXTENTS = ("unilobar", "bilobar")
TUMOR_LOBES = ("left", "right")
TUMOR_MORPHOLOGIES = ("smooth_nodular", "lobulated_confluent")


@dataclass(frozen=True)
class TumorStrataV2:
    count_bin: str
    dmax_bin: str
    lobe_extent: str


@dataclass(frozen=True)
class TumorCaseTargetV2:
    case_id: str
    strata: TumorStrataV2
    targets: tuple[TumorTargetV2, ...]
    burden_fraction_max: float
    dmax_tolerance_voxels: float
    placement_attempts_per_lesion: int
    instance_gap_mm: float
    subcapsular_clearance_max_mm: float
    sampling_attempts: int
    rejected_reason_counts: Mapping[str, int] = field(default_factory=dict)
    evidence_types: Mapping[str, str] = field(default_factory=dict)

    @property
    def dmax_mm(self) -> float:
        return max(target.dmax_mm for target in self.targets)

    @property
    def requested_count(self) -> int:
        return len(self.targets)

    @property
    def within_bin_assumption(self) -> bool:
        return any(target.within_bin_assumption for target in self.targets)


@dataclass(frozen=True)
class TumorRejectionV2:
    reason_code: str
    lesion_id: str | None
    details: Mapping[str, object] = field(default_factory=dict)


class TumorTargetSamplingRejectedError(RuntimeError):
    def __init__(self, rejection: TumorRejectionV2) -> None:
        self.rejection = rejection
        super().__init__(f"tumor target sampling rejected: {rejection.reason_code}")


class TumorRasterizationRejectedError(RuntimeError):
    def __init__(self, rejection: TumorRejectionV2) -> None:
        self.rejection = rejection
        super().__init__(
            f"tumor rasterization rejected for {rejection.lesion_id}: "
            f"{rejection.reason_code}"
        )


class TumorPlacementRejectedError(RuntimeError):
    def __init__(self, rejection: TumorRejectionV2) -> None:
        self.rejection = rejection
        super().__init__(
            f"tumor placement rejected for {rejection.lesion_id}: "
            f"{rejection.reason_code}"
        )


@dataclass(frozen=True)
class TumorRasterV2:
    mask: np.ndarray
    metrics: LesionMetricsV2 | None
    fitted_scale: float
    dmax_error_mm: float
    primitive_count: int
    primitive_sum_voxels: int
    primitive_overlap_voxels: int
    primitive_overlap_fraction: float
    connected: bool
    grid_boundary_clear: bool


@dataclass(frozen=True)
class TumorPlacementRecordV2:
    instance_id: int
    target: TumorTargetV2
    center_world_mm: tuple[float, float, float]
    metrics: LesionMetricsV2 | None
    capsule_clearance_mm: float
    complete_containment: bool
    assigned_lobe: str
    primitive_count: int
    primitive_overlap_voxels: int
    primitive_overlap_fraction: float


@dataclass(frozen=True)
class TumorGeometryV2:
    instance_mask: np.ndarray
    placements: tuple[TumorPlacementRecordV2, ...]
    lesion_metrics: tuple[LesionMetricsV2, ...]
    tumor_union_volume_ml: float
    liver_volume_ml: float
    tumor_to_liver_fraction: float
    requested_lobe_extent: str
    realized_lobe_extent: str
    target_count: int
    realized_count: int
    status: str = "pass"
    containment_definition: str = "complete_primitive_union_inside_liver_without_clipping"
    lobe_assignment_definition: str = "primitive_center_in_couinaud_side_proxy"


def _model(profile: PopulationProfileV2, name: str) -> Mapping[str, object]:
    value = profile.value(name)
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return value


def _categorical(rng: np.random.Generator, values: Mapping[str, float]) -> str:
    names = tuple(values)
    probabilities = np.asarray([float(values[name]) for name in names], dtype=np.float64)
    probabilities /= probabilities.sum()
    return str(rng.choice(names, p=probabilities))


def _liver_context(liver: LiverTargetV2 | LiverGeometryV2) -> tuple[float, float]:
    if isinstance(liver, LiverTargetV2):
        return float(liver.volume_ml), float(liver.left_fraction)
    if isinstance(liver, LiverGeometryV2):
        return (
            float(liver.actual_metrics["volume_ml"]),
            float(liver.actual_metrics["left_fraction"]),
        )
    raise TypeError("liver must be LiverTargetV2 or LiverGeometryV2")


def _sample_count(
    count_bin: str,
    profile: PopulationProfileV2,
    rng: np.random.Generator,
) -> tuple[int, bool]:
    if count_bin == "1":
        return 1, False
    if count_bin == "2-5":
        model = _model(profile, "within_2_5_count_model")
        return int(rng.integers(int(model["minimum"]), int(model["maximum"]) + 1)), True
    if count_bin == ">5":
        model = _model(profile, "within_gt5_count_model")
        value = int(model["minimum"]) + int(rng.geometric(float(model["success_probability"]))) - 1
        return min(value, int(model["maximum"])), True
    raise ValueError(f"unsupported count bin {count_bin!r}")


def _dmax_interval(dmax_bin: str, profile: PopulationProfileV2) -> tuple[float, float]:
    lower = float(profile.value("dmax_min_mm"))
    upper = float(profile.value("dmax_max_mm"))
    if dmax_bin == "10-<80_mm":
        return lower, min(80.0, upper)
    if dmax_bin == "80-200_mm":
        return max(80.0, lower), upper
    raise ValueError(f"unsupported Dmax bin {dmax_bin!r}")


def _sample_dmax(
    dmax_bin: str,
    profile: PopulationProfileV2,
    rng: np.random.Generator,
) -> float:
    lower, upper = _dmax_interval(dmax_bin, profile)
    model = _model(profile, "tumor_geometry_model")
    median = float(model["dmax_lognormal_median_mm"])
    sigma = float(model["dmax_lognormal_sigma"])
    for _ in range(128):
        value = float(rng.lognormal(math.log(median), sigma))
        if lower <= value <= upper and not (dmax_bin == "10-<80_mm" and value >= 80.0):
            return value
    return float(rng.uniform(lower, np.nextafter(upper, lower)))


def _analytic_ellipsoid_volume_ml(diameter_mm: float, ratios: tuple[float, float]) -> float:
    return math.pi / 6.0 * diameter_mm**3 * ratios[0] * ratios[1] / 1000.0


def _sample_lobes(
    count: int,
    lobe_extent: str,
    left_fraction: float,
    rng: np.random.Generator,
) -> list[str]:
    if lobe_extent == "unilobar":
        chosen = "left" if rng.random() < left_fraction else "right"
        return [chosen] * count
    if lobe_extent != "bilobar" or count < 2:
        raise ValueError("bilobar tumor targets require at least two lesions")
    first = "left" if rng.random() < left_fraction else "right"
    lobes = [first, "right" if first == "left" else "left"]
    lobes.extend(
        "left" if rng.random() < left_fraction else "right"
        for _ in range(count - 2)
    )
    return lobes


def _sample_target_set(
    patient: PatientSampleV2,
    liver_volume_ml: float,
    left_fraction: float,
    profile: PopulationProfileV2,
    rng: np.random.Generator,
    strata: TumorStrataV2,
) -> tuple[TumorTargetV2, ...] | None:
    count, _ = _sample_count(strata.count_bin, profile, rng)
    if strata.lobe_extent == "bilobar" and count < 2:
        return None
    dmax = _sample_dmax(strata.dmax_bin, profile, rng)
    axis_lower, axis_upper = map(float, profile.value("axis_ratio_range"))
    geometry_model = _model(profile, "tumor_geometry_model")
    beta_a, beta_b = map(float, geometry_model["secondary_diameter_beta_shape"])
    confluent_threshold = float(profile.value("confluent_required_above_mm"))
    confluent_probability = float(geometry_model["lobulated_probability_below_threshold"])
    primitive_min, primitive_max = map(
        int, geometry_model["confluent_primitive_count_range"]
    )
    subcapsular_probability = float(geometry_model["subcapsular_probability"])
    lobes = _sample_lobes(count, strata.lobe_extent, left_fraction, rng)
    diameters = [dmax]
    if count > 1:
        fraction_floor = float(profile.value("dmax_min_mm")) / dmax
        for _ in range(count - 1):
            fraction = fraction_floor + (1.0 - fraction_floor) * float(
                rng.beta(beta_a, beta_b)
            )
            diameters.append(max(float(profile.value("dmax_min_mm")), dmax * fraction))
    targets: list[TumorTargetV2] = []
    analytic_by_lobe = {"left": 0.0, "right": 0.0}
    for index, (diameter, lobe) in enumerate(zip(diameters, lobes), start=1):
        ratios = tuple(float(value) for value in rng.uniform(axis_lower, axis_upper, size=2))
        morphology = (
            "lobulated_confluent"
            if diameter > confluent_threshold or rng.random() < confluent_probability
            else "smooth_nodular"
        )
        primitive_count = (
            int(rng.integers(primitive_min, primitive_max + 1))
            if morphology == "lobulated_confluent"
            else 1
        )
        analytic_by_lobe[lobe] += _analytic_ellipsoid_volume_ml(diameter, ratios)
        targets.append(
            TumorTargetV2(
                lesion_id=f"{patient.case_id}_lesion_{index:02d}",
                dmax_mm=float(diameter),
                axis_ratios=ratios,
                lobe=lobe,
                morphology=morphology,
                orientation_deg_zyx=tuple(
                    float(value) for value in rng.uniform(-180.0, 180.0, size=3)
                ),
                subcapsular=bool(rng.random() < subcapsular_probability),
                primitive_count=primitive_count,
                target_rank=index,
                count_bin=strata.count_bin,
                dmax_bin=strata.dmax_bin,
                within_bin_assumption=True,
                evidence_types={
                    "count_bin": profile.parameters["tumor_count_bins"].source_type,
                    "count_within_bin": (
                        profile.parameters["within_gt5_count_model"].source_type
                        if strata.count_bin == ">5"
                        else profile.parameters["within_2_5_count_model"].source_type
                        if strata.count_bin == "2-5"
                        else profile.parameters["tumor_count_bins"].source_type
                    ),
                    "dmax_bin": profile.parameters["dmax_bins"].source_type,
                    "dmax_within_bin": profile.parameters["tumor_geometry_model"].source_type,
                    "axis_ratios": profile.parameters["axis_ratio_range"].source_type,
                    "morphology": profile.parameters["morphology_set"].source_type,
                    "placement_model": profile.parameters["tumor_geometry_model"].source_type,
                },
            )
        )
    burden_max = float(profile.value("tumor_burden_fraction_max"))
    if sum(analytic_by_lobe.values()) > burden_max * liver_volume_ml:
        return None
    lobe_volumes = {
        "left": left_fraction * liver_volume_ml,
        "right": (1.0 - left_fraction) * liver_volume_ml,
    }
    if any(analytic_by_lobe[lobe] > 0.95 * lobe_volumes[lobe] for lobe in TUMOR_LOBES):
        return None
    return tuple(targets)


def sample_tumor_case_target(
    patient: PatientSampleV2,
    liver: LiverTargetV2 | LiverGeometryV2,
    profile: PopulationProfileV2,
    rng: np.random.Generator,
    *,
    fixed_strata: TumorStrataV2 | None = None,
    max_within_stratum_attempts: int = 64,
) -> TumorCaseTargetV2:
    """Sample patient-level tumor strata first, then feasible within-stratum lesions."""
    if not isinstance(rng, np.random.Generator):
        raise TypeError("rng must be numpy.random.Generator")
    if (
        not isinstance(max_within_stratum_attempts, int)
        or isinstance(max_within_stratum_attempts, bool)
        or max_within_stratum_attempts < 1
    ):
        raise ValueError("max_within_stratum_attempts must be a positive integer")
    liver_volume_ml, left_fraction = _liver_context(liver)
    count_probabilities = profile.value("tumor_count_bins")
    dmax_probabilities = profile.value("dmax_bins")
    lobe_probabilities = profile.value("lobe_distribution")
    if fixed_strata is None:
        count_bin = _categorical(rng, count_probabilities)
        dmax_bin = _categorical(rng, dmax_probabilities)
        if count_bin == "1":
            lobe_extent = "unilobar"
        else:
            multi_probability = 1.0 - float(count_probabilities["1"])
            bilobar_given_multi = float(lobe_probabilities["bilobar"]) / multi_probability
            if not 0.0 <= bilobar_given_multi <= 1.0:
                raise ValueError("lobe and count marginals are not jointly feasible")
            lobe_extent = "bilobar" if rng.random() < bilobar_given_multi else "unilobar"
        strata = TumorStrataV2(count_bin, dmax_bin, lobe_extent)
    else:
        strata = fixed_strata
    if strata.count_bin not in COUNT_BINS:
        raise ValueError("invalid fixed count bin")
    if strata.dmax_bin not in DMAX_BINS:
        raise ValueError("invalid fixed Dmax bin")
    if strata.lobe_extent not in LOBE_EXTENTS:
        raise ValueError("invalid fixed lobe extent")
    rejected = {"analytic_burden_or_lobe_capacity": 0}
    for attempt in range(1, max_within_stratum_attempts + 1):
        targets = _sample_target_set(
            patient,
            liver_volume_ml,
            left_fraction,
            profile,
            rng,
            strata,
        )
        if targets is None:
            rejected["analytic_burden_or_lobe_capacity"] += 1
            continue
        geometry_model = _model(profile, "tumor_geometry_model")
        return TumorCaseTargetV2(
            case_id=patient.case_id,
            strata=strata,
            targets=targets,
            burden_fraction_max=float(profile.value("tumor_burden_fraction_max")),
            dmax_tolerance_voxels=float(geometry_model["dmax_tolerance_voxels"]),
            placement_attempts_per_lesion=int(
                geometry_model["placement_attempts_per_lesion"]
            ),
            instance_gap_mm=float(geometry_model["instance_gap_mm"]),
            subcapsular_clearance_max_mm=float(
                geometry_model["subcapsular_clearance_max_mm"]
            ),
            sampling_attempts=attempt,
            rejected_reason_counts={name: count for name, count in rejected.items() if count},
            evidence_types={
                "count_bin": profile.parameters["tumor_count_bins"].source_type,
                "dmax_bin": profile.parameters["dmax_bins"].source_type,
                "lobe_extent": profile.parameters["lobe_distribution"].source_type,
                "conditional_geometry": profile.parameters["tumor_geometry_model"].source_type,
                "burden_gate": profile.parameters["tumor_burden_fraction_max"].source_type,
            },
        )
    raise TumorTargetSamplingRejectedError(
        TumorRejectionV2(
            reason_code="within_stratum_feasibility_exhausted",
            lesion_id=None,
            details={
                "case_id": patient.case_id,
                "strata": strata.__dict__,
                "max_attempts": max_within_stratum_attempts,
                "reason_counts": rejected,
            },
        )
    )


def sample_tumor_targets(
    patient: PatientSampleV2,
    liver: LiverTargetV2 | LiverGeometryV2,
    profile: PopulationProfileV2,
    rng: np.random.Generator,
) -> list[TumorTargetV2]:
    """Plan interface; use sample_tumor_case_target when provenance is required."""
    return list(sample_tumor_case_target(patient, liver, profile, rng).targets)


def _validate_target(target: TumorTargetV2) -> None:
    if not isinstance(target.lesion_id, str) or not target.lesion_id.strip():
        raise ValueError("lesion_id must be non-empty")
    if not math.isfinite(target.dmax_mm) or not 10.0 <= target.dmax_mm <= 215.0:
        raise ValueError("Dmax must be within [10, 215] mm")
    if target.dmax_mm > 200.0 and target.evidence_types.get("dmax") != "stress_test":
        raise ValueError("Dmax above 200 mm is permitted only for stress_test targets")
    if target.lobe not in TUMOR_LOBES:
        raise ValueError("tumor lobe must be left or right")
    if target.morphology not in TUMOR_MORPHOLOGIES:
        raise ValueError("unsupported tumor morphology")
    if target.dmax_mm > 100.0 and target.morphology != "lobulated_confluent":
        raise ValueError("Dmax above 100 mm requires lobulated_confluent morphology")
    if len(target.axis_ratios) != 2 or any(
        not math.isfinite(value) or not 0.70 <= value <= 1.0
        for value in target.axis_ratios
    ):
        raise ValueError("axis ratios must contain two values within [0.70, 1.0]")
    if len(target.orientation_deg_zyx) != 3 or not all(
        math.isfinite(value) for value in target.orientation_deg_zyx
    ):
        raise ValueError("orientation must contain three finite angles")
    if target.morphology == "smooth_nodular" and target.primitive_count != 1:
        raise ValueError("smooth_nodular targets require exactly one primitive")
    if target.morphology == "lobulated_confluent" and not 2 <= target.primitive_count <= 4:
        raise ValueError("lobulated_confluent targets require 2-4 primitives")


def _rotation_matrix(angles_deg_zyx: tuple[float, float, float]) -> np.ndarray:
    a, b, c = np.radians(np.asarray(angles_deg_zyx, dtype=np.float64))
    rz = np.array(
        (
            (math.cos(a), -math.sin(a), 0.0),
            (math.sin(a), math.cos(a), 0.0),
            (0.0, 0.0, 1.0),
        )
    )
    ry = np.array(
        (
            (math.cos(b), 0.0, math.sin(b)),
            (0.0, 1.0, 0.0),
            (-math.sin(b), 0.0, math.cos(b)),
        )
    )
    rx = np.array(
        (
            (1.0, 0.0, 0.0),
            (0.0, math.cos(c), -math.sin(c)),
            (0.0, math.sin(c), math.cos(c)),
        )
    )
    return rz @ ry @ rx


def _crop_geometry(
    center_world_mm: np.ndarray,
    target: TumorTargetV2,
    grid: GridSpecV2,
    padding_voxels: int,
) -> tuple[tuple[slice, slice, slice], np.ndarray, np.ndarray]:
    affine = grid.affine_4x4
    linear = affine[:3, :3]
    center_index = np.linalg.solve(linear, center_world_mm - affine[:3, 3])
    spacing = np.linalg.norm(linear, axis=0)
    radius_mm = 0.75 * target.dmax_mm * 1.30 + padding_voxels * float(spacing.max())
    radius_vox = np.ceil(radius_mm / spacing).astype(int)
    lower = np.maximum(np.floor(center_index).astype(int) - radius_vox, 0)
    upper = np.minimum(np.ceil(center_index).astype(int) + radius_vox + 1, grid.shape)
    slices = tuple(slice(int(lower[axis]), int(upper[axis])) for axis in range(3))
    shape = tuple(int(upper[axis] - lower[axis]) for axis in range(3))
    local_indices = np.indices(shape, dtype=np.float64)
    for axis in range(3):
        local_indices[axis] += lower[axis]
    flat = np.moveaxis(local_indices, 0, -1)
    world = flat @ linear.T + affine[:3, 3]
    crop_affine = affine.copy()
    crop_affine[:3, 3] = lower @ linear.T + affine[:3, 3]
    return slices, world - center_world_mm, crop_affine


def _primitive_definition(
    target: TumorTargetV2,
    scale: float,
) -> tuple[tuple[np.ndarray, np.ndarray], ...]:
    diameter = float(target.dmax_mm) * scale
    ratio_a, ratio_b = map(float, target.axis_ratios)
    if target.morphology == "smooth_nodular":
        radii = np.array((0.50, 0.50 * ratio_a, 0.50 * ratio_b)) * diameter
        return ((np.zeros(3), radii),)
    positions = np.linspace(-0.20, 0.20, target.primitive_count)
    primitives = []
    for index, position in enumerate(positions):
        transverse = (0.045 if index % 2 == 0 else -0.045) * diameter
        center = np.array((position * diameter, transverse, -0.5 * transverse))
        modulation = 1.0 + 0.04 * math.sin(index + 1.0)
        radii = np.array(
            (0.30 * modulation, 0.47 * ratio_a / modulation, 0.47 * ratio_b)
        ) * diameter
        primitives.append((center, radii))
    return tuple(primitives)


def _render_crop(
    delta_world: np.ndarray,
    target: TumorTargetV2,
    scale: float,
) -> tuple[np.ndarray, tuple[np.ndarray, ...]]:
    local = delta_world @ _rotation_matrix(target.orientation_deg_zyx)
    primitives = []
    for center, radii in _primitive_definition(target, scale):
        field = np.sum(((local - center) / radii) ** 2, axis=-1)
        primitives.append(field <= 1.0)
    return np.logical_or.reduce(primitives), tuple(primitives)


def _touches_clipped_grid_boundary(
    mask: np.ndarray,
    slices: tuple[slice, slice, slice],
    grid_shape: tuple[int, int, int],
) -> bool:
    for axis, section in enumerate(slices):
        if section.start == 0 and np.any(np.take(mask, 0, axis=axis)):
            return True
        if section.stop == grid_shape[axis] and np.any(np.take(mask, -1, axis=axis)):
            return True
    return False


def _measure_crop(mask: np.ndarray, affine_4x4: np.ndarray) -> LesionMetricsV2:
    measured = measure_lesions(mask.astype(np.uint16), affine_4x4)
    if len(measured) != 1:
        raise ValueError("tumor raster must contain exactly one connected instance label")
    return measured[0]


def rasterize_tumor_at_center(
    target: TumorTargetV2,
    center_world_mm: Sequence[float],
    grid: GridSpecV2,
    *,
    padding_voxels: int = 3,
    dmax_tolerance_voxels: float = 0.75,
    measure_full_metrics: bool = True,
) -> TumorRasterV2:
    """Rasterize a complete physical-mm primitive union without liver clipping."""
    _validate_target(target)
    if not isinstance(grid, GridSpecV2):
        raise TypeError("grid must be GridSpecV2")
    if not isinstance(padding_voxels, int) or isinstance(padding_voxels, bool) or padding_voxels < 1:
        raise ValueError("padding_voxels must be a positive integer")
    if not math.isfinite(dmax_tolerance_voxels) or dmax_tolerance_voxels <= 0:
        raise ValueError("dmax_tolerance_voxels must be positive and finite")
    center = np.asarray(center_world_mm, dtype=np.float64)
    if center.shape != (3,) or not np.isfinite(center).all():
        raise ValueError("center_world_mm must contain three finite coordinates")

    slices, delta_world, crop_affine = _crop_geometry(center, target, grid, padding_voxels)
    low, high = 0.25, 1.30
    best: tuple[float, np.ndarray, tuple[np.ndarray, ...], float] | None = None
    if not isinstance(measure_full_metrics, bool):
        raise TypeError("measure_full_metrics must be boolean")
    for _ in range(8):
        scale = 0.5 * (low + high)
        mask, primitive_masks = _render_crop(delta_world, target, scale)
        if not mask.any():
            low = scale
            continue
        diameter_mm = _feret_diameter_mm(mask, crop_affine)
        candidate = (scale, mask, primitive_masks, diameter_mm)
        if best is None or abs(diameter_mm - target.dmax_mm) < abs(
            best[3] - target.dmax_mm
        ):
            best = candidate
        if diameter_mm < target.dmax_mm:
            low = scale
        else:
            high = scale
    if best is None:
        raise TumorRasterizationRejectedError(
            TumorRejectionV2("empty_raster", target.lesion_id)
        )
    scale, crop_mask, primitive_masks, actual_diameter_mm = best
    metrics = _measure_crop(crop_mask, crop_affine) if measure_full_metrics else None
    error_mm = abs(float(actual_diameter_mm) - float(target.dmax_mm))
    tolerance_mm = max(
        float(dmax_tolerance_voxels) * float(grid.voxel_size_mm),
        0.03 * float(target.dmax_mm),
    )
    if error_mm > tolerance_mm:
        raise TumorRasterizationRejectedError(
            TumorRejectionV2(
                "dmax_tolerance_failed",
                target.lesion_id,
                {
                    "target_mm": float(target.dmax_mm),
                    "actual_mm": float(actual_diameter_mm),
                    "tolerance_mm": tolerance_mm,
                },
            )
        )
    boundary_clear = not _touches_clipped_grid_boundary(crop_mask, slices, grid.shape)
    if not boundary_clear:
        raise TumorRasterizationRejectedError(
            TumorRejectionV2("grid_boundary_clipped", target.lesion_id)
        )

    primitive_sum = int(sum(np.count_nonzero(item) for item in primitive_masks))
    union_voxels = int(np.count_nonzero(crop_mask))
    overlap_voxels = primitive_sum - union_voxels
    overlap_fraction = overlap_voxels / primitive_sum if primitive_sum else 0.0
    connected = ndimage.label(crop_mask, structure=np.ones((3, 3, 3), dtype=np.uint8))[1] == 1
    if not connected:
        raise TumorRasterizationRejectedError(
            TumorRejectionV2("disconnected_raster", target.lesion_id)
        )
    if target.morphology == "lobulated_confluent" and overlap_voxels <= 0:
        raise TumorRasterizationRejectedError(
            TumorRejectionV2(
                "confluent_primitives_not_connected",
                target.lesion_id,
                {"overlap_voxels": overlap_voxels, "connected": connected},
            )
        )

    full_mask = np.zeros(grid.shape, dtype=bool)
    full_mask[slices] = crop_mask
    return TumorRasterV2(
        mask=full_mask,
        metrics=metrics,
        fitted_scale=float(scale),
        dmax_error_mm=error_mm,
        primitive_count=len(primitive_masks),
        primitive_sum_voxels=primitive_sum,
        primitive_overlap_voxels=overlap_voxels,
        primitive_overlap_fraction=float(overlap_fraction),
        connected=bool(connected),
        grid_boundary_clear=boundary_clear,
    )


def _stable_seed(*parts: object) -> int:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big", signed=False)


def _candidate_indices(
    lobe_mask: np.ndarray,
    lobe_signed_distance: np.ndarray,
    liver_signed_distance: np.ndarray,
    target: TumorTargetV2,
    grid: GridSpecV2,
    maximum: int,
) -> np.ndarray:
    spacing = float(grid.voxel_size_mm)
    indices = np.argwhere(lobe_mask)
    lobe_depths = lobe_signed_distance[tuple(indices.T)]
    liver_depths = liver_signed_distance[tuple(indices.T)]
    desired_depth = 0.50 * float(target.dmax_mm) + 0.5 * spacing
    if target.subcapsular:
        safe = lobe_depths >= desired_depth - spacing
        if safe.any():
            indices = indices[safe]
            liver_depths = liver_depths[safe]
        order = np.argsort(np.abs(liver_depths - desired_depth), kind="stable")
    else:
        safe = lobe_depths >= desired_depth
        if safe.any():
            indices = indices[safe]
            lobe_depths = lobe_depths[safe]
        order = np.argsort(-lobe_depths, kind="stable")
    pool_size = min(len(order), max(maximum * 8, maximum))
    pool = indices[order[:pool_size]].copy()
    rng = np.random.default_rng(
        _stable_seed(
            target.lesion_id,
            f"{target.dmax_mm:.8f}",
            target.orientation_deg_zyx,
            target.subcapsular,
            grid.shape,
        )
    )
    rng.shuffle(pool)
    return pool[:maximum]


def _world_from_index(index_zyx: np.ndarray, affine_4x4: np.ndarray) -> np.ndarray:
    return index_zyx @ affine_4x4[:3, :3].T + affine_4x4[:3, 3]


def _normalize_case_target(
    targets: Sequence[TumorTargetV2] | TumorCaseTargetV2,
) -> TumorCaseTargetV2:
    if isinstance(targets, TumorCaseTargetV2):
        return targets
    items = tuple(targets)
    if not items:
        raise ValueError("at least one tumor target is required")
    lobes = {item.lobe for item in items}
    return TumorCaseTargetV2(
        case_id="ad_hoc",
        strata=TumorStrataV2(
            "1" if len(items) == 1 else ("2-5" if len(items) <= 5 else ">5"),
            "10-<80_mm" if max(item.dmax_mm for item in items) < 80.0 else "80-200_mm",
            "bilobar" if len(lobes) > 1 else "unilobar",
        ),
        targets=items,
        burden_fraction_max=0.70,
        dmax_tolerance_voxels=0.75,
        placement_attempts_per_lesion=192,
        instance_gap_mm=0.0,
        subcapsular_clearance_max_mm=5.0,
        sampling_attempts=1,
        evidence_types={"wrapper": "engineering_default"},
    )


def place_and_rasterize_tumors(
    targets: Sequence[TumorTargetV2] | TumorCaseTargetV2,
    liver: LiverGeometryV2,
    grid: GridSpecV2,
) -> TumorGeometryV2:
    """Place complete tumor instances; reject instead of clipping any primitive."""
    case_target = _normalize_case_target(targets)
    if not isinstance(liver, LiverGeometryV2):
        raise TypeError("liver must be LiverGeometryV2")
    if not isinstance(grid, GridSpecV2):
        raise TypeError("grid must be GridSpecV2")
    liver_mask = np.asarray(liver.mask, dtype=bool)
    region_labels = np.asarray(liver.region_labels)
    if liver_mask.shape != grid.shape or region_labels.shape != grid.shape:
        raise ValueError("liver masks must match grid shape")
    if not np.allclose(liver.affine_4x4, grid.affine_4x4, atol=1e-8):
        raise ValueError("liver affine must match grid affine")
    if not np.array_equal(region_labels > 0, liver_mask):
        raise ValueError("region_labels must exactly partition liver.mask")

    lobe_masks = {
        "left": np.isin(region_labels, (1, 2, 3)),
        "right": np.isin(region_labels, (4, 5)),
    }
    liver_sdf = signed_distance_mm(liver_mask, grid.affine_4x4)
    lobe_sdf = {
        lobe: signed_distance_mm(mask, grid.affine_4x4)
        for lobe, mask in lobe_masks.items()
    }
    instance_mask = np.zeros(grid.shape, dtype=np.uint16)
    records: list[TumorPlacementRecordV2] = []
    voxel_volume_ml = float(grid.voxel_volume_ml)
    liver_volume_ml = float(np.count_nonzero(liver_mask) * voxel_volume_ml)

    for instance_id, target in enumerate(case_target.targets, start=1):
        _validate_target(target)
        reference_index = np.asarray(grid.shape, dtype=int) // 2
        reference_center = _world_from_index(reference_index, grid.affine_4x4)
        try:
            template = rasterize_tumor_at_center(
                target,
                reference_center,
                grid,
                dmax_tolerance_voxels=case_target.dmax_tolerance_voxels,
                measure_full_metrics=False,
            )
        except TumorRasterizationRejectedError as error:
            raise TumorPlacementRejectedError(
                TumorRejectionV2(
                    "template_rasterization_failed",
                    target.lesion_id,
                    {
                        "raster_reason": error.rejection.reason_code,
                        **dict(error.rejection.details),
                    },
                )
            ) from error
        template_offsets = np.argwhere(template.mask) - reference_index
        candidates = _candidate_indices(
            lobe_masks[target.lobe],
            lobe_sdf[target.lobe],
            liver_sdf,
            target,
            grid,
            case_target.placement_attempts_per_lesion,
        )
        if len(candidates) == 0:
            raise TumorPlacementRejectedError(
                TumorRejectionV2("no_candidate_centers", target.lesion_id)
            )
        rejected_counts: dict[str, int] = {}
        accepted: tuple[np.ndarray, TumorRasterV2, float] | None = None
        for index_zyx in candidates:
            center = _world_from_index(index_zyx, grid.affine_4x4)
            translated_indices = template_offsets + index_zyx
            if np.any(translated_indices < 0) or np.any(
                translated_indices >= np.asarray(grid.shape)
            ):
                rejected_counts["grid_boundary_clipped"] = (
                    rejected_counts.get("grid_boundary_clipped", 0) + 1
                )
                continue
            translated_mask = np.zeros(grid.shape, dtype=bool)
            translated_mask[tuple(translated_indices.T)] = True
            raster = replace(template, mask=translated_mask)
            if np.any(raster.mask & ~liver_mask):
                rejected_counts["incomplete_liver_containment"] = (
                    rejected_counts.get("incomplete_liver_containment", 0) + 1
                )
                continue
            if np.any(raster.mask & (instance_mask > 0)):
                rejected_counts["instance_overlap"] = rejected_counts.get("instance_overlap", 0) + 1
                continue
            if case_target.instance_gap_mm > 0.0 and np.any(instance_mask):
                distance_from_existing = ndimage.distance_transform_edt(
                    instance_mask == 0,
                    sampling=(grid.voxel_size_mm,) * 3,
                )
                if float(distance_from_existing[raster.mask].min()) <= case_target.instance_gap_mm:
                    rejected_counts["instance_gap"] = rejected_counts.get("instance_gap", 0) + 1
                    continue
            capsule_clearance = max(
                0.0,
                float(liver_sdf[raster.mask].min()) - 0.5 * float(grid.voxel_size_mm),
            )
            if target.subcapsular and (
                capsule_clearance > case_target.subcapsular_clearance_max_mm
            ):
                rejected_counts["subcapsular_clearance"] = (
                    rejected_counts.get("subcapsular_clearance", 0) + 1
                )
                continue
            proposed_voxels = int(np.count_nonzero(instance_mask)) + int(
                np.count_nonzero(raster.mask)
            )
            proposed_burden = proposed_voxels * voxel_volume_ml / liver_volume_ml
            if proposed_burden > case_target.burden_fraction_max:
                rejected_counts["tumor_burden"] = rejected_counts.get("tumor_burden", 0) + 1
                continue
            accepted = (center, raster, capsule_clearance)
            break
        if accepted is None:
            raise TumorPlacementRejectedError(
                TumorRejectionV2(
                    "placement_attempts_exhausted",
                    target.lesion_id,
                    {
                        "attempts": int(len(candidates)),
                        "reason_counts": rejected_counts,
                        "lobe": target.lobe,
                        "dmax_mm": float(target.dmax_mm),
                    },
                )
            )
        center, raster, capsule_clearance = accepted
        instance_mask[raster.mask] = instance_id
        records.append(
            TumorPlacementRecordV2(
                instance_id=instance_id,
                target=target,
                center_world_mm=tuple(float(value) for value in center),
                metrics=raster.metrics,
                capsule_clearance_mm=float(capsule_clearance),
                complete_containment=True,
                assigned_lobe=target.lobe,
                primitive_count=raster.primitive_count,
                primitive_overlap_voxels=raster.primitive_overlap_voxels,
                primitive_overlap_fraction=raster.primitive_overlap_fraction,
            )
        )

    lesion_metrics = tuple(measure_lesions(instance_mask, grid.affine_4x4))
    realized_ids = tuple(metric.instance_id for metric in lesion_metrics)
    if realized_ids != tuple(range(1, len(case_target.targets) + 1)):
        raise RuntimeError("realized tumor instance IDs do not match targets")
    metrics_by_id = {metric.instance_id: metric for metric in lesion_metrics}
    records = [
        replace(record, metrics=metrics_by_id[record.instance_id])
        for record in records
    ]
    if np.any((instance_mask > 0) & ~liver_mask):
        raise RuntimeError("complete-containment invariant violated")
    tumor_volume_ml = float(np.count_nonzero(instance_mask) * voxel_volume_ml)
    realized_lobes = {record.assigned_lobe for record in records}
    realized_lobe_extent = "bilobar" if len(realized_lobes) > 1 else "unilobar"
    if realized_lobe_extent != case_target.strata.lobe_extent:
        raise RuntimeError("realized lobe extent differs from sampled stratum")
    return TumorGeometryV2(
        instance_mask=instance_mask,
        placements=tuple(records),
        lesion_metrics=lesion_metrics,
        tumor_union_volume_ml=tumor_volume_ml,
        liver_volume_ml=liver_volume_ml,
        tumor_to_liver_fraction=tumor_volume_ml / liver_volume_ml,
        requested_lobe_extent=case_target.strata.lobe_extent,
        realized_lobe_extent=realized_lobe_extent,
        target_count=len(case_target.targets),
        realized_count=len(lesion_metrics),
    )
