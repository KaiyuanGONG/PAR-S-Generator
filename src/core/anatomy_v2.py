from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping

import numpy as np
from scipy import ndimage

from .attenuation_model_v2 import AttenuationAnatomyV2
from .liver_geometry import GridSpecV2, LiverGeometryV2
from .schemas_v2 import PatientSampleV2


FORMAL_TORSO_SHAPE_V2 = (128, 128, 128)
AXIS_ORDER_V2 = "ZYX"
ORIENTATION_CODE_V2 = "SAR"


@dataclass(frozen=True)
class TorsoAnatomyQCV2:
    passed: bool
    failed_gates: tuple[str, ...]
    metrics: Mapping[str, object] = field(default_factory=dict)
    limits: Mapping[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "failed_gates": list(self.failed_gates),
            "metrics": dict(self.metrics),
            "limits": dict(self.limits),
        }


@dataclass(frozen=True)
class TorsoAnatomyMetadataV2:
    schema_version: str
    model_id: str
    axis_order: str
    orientation_code: str
    reference_phase: str
    affine_4x4: tuple[tuple[float, ...], ...]
    voxel_size_mm: float
    patient_habitus: Mapping[str, object]
    design_parameters: Mapping[str, object]
    actual_metrics: Mapping[str, object]
    tissue_priority: tuple[str, ...]
    body_mask_semantics: str
    qc: TorsoAnatomyQCV2

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "model_id": self.model_id,
            "axis_order": self.axis_order,
            "orientation_code": self.orientation_code,
            "reference_phase": self.reference_phase,
            "affine_4x4": [list(row) for row in self.affine_4x4],
            "voxel_size_mm": self.voxel_size_mm,
            "patient_habitus": dict(self.patient_habitus),
            "design_parameters": dict(self.design_parameters),
            "actual_metrics": dict(self.actual_metrics),
            "tissue_priority": list(self.tissue_priority),
            "body_mask_semantics": self.body_mask_semantics,
            "qc": self.qc.as_dict(),
        }


@dataclass(frozen=True)
class TorsoAnatomyBuildV2:
    anatomy: AttenuationAnatomyV2
    metadata: TorsoAnatomyMetadataV2


class TorsoAnatomyRejectedError(RuntimeError):
    def __init__(self, qc: TorsoAnatomyQCV2) -> None:
        self.qc = qc
        super().__init__(f"V2 torso anatomy failed QC gates: {list(qc.failed_gates)}")


def _validate_inputs(
    liver: LiverGeometryV2,
    grid: GridSpecV2,
    patient: PatientSampleV2,
) -> np.ndarray:
    if not isinstance(liver, LiverGeometryV2):
        raise TypeError("liver must be LiverGeometryV2")
    if not isinstance(grid, GridSpecV2):
        raise TypeError("grid must be GridSpecV2")
    if not isinstance(patient, PatientSampleV2):
        raise TypeError("patient must be PatientSampleV2")
    if grid.shape != FORMAL_TORSO_SHAPE_V2:
        raise ValueError("formal V2 torso anatomy requires a 128x128x128 grid")

    liver_mask = np.asarray(liver.mask)
    if liver_mask.shape != grid.shape or liver_mask.dtype != np.bool_:
        raise ValueError("liver.mask must be a 128x128x128 boolean array")
    if not liver_mask.any():
        raise ValueError("liver.mask must not be empty")
    affine = np.asarray(liver.affine_4x4, dtype=np.float64)
    if affine.shape != (4, 4) or not np.isfinite(affine).all():
        raise ValueError("liver.affine_4x4 must be a finite 4x4 matrix")
    if not np.allclose(affine, grid.affine_4x4, rtol=0.0, atol=1e-8):
        raise ValueError("liver affine must exactly match the supplied SAR/ZYX grid")

    numeric = {
        "age_years": patient.age_years,
        "height_cm": patient.height_cm,
        "weight_kg": patient.weight_kg,
        "bmi": patient.bmi,
    }
    if any(
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        for value in numeric.values()
    ):
        raise ValueError("patient habitus values must be finite numbers")
    if patient.sex not in {"male", "female"}:
        raise ValueError("patient.sex must be male or female")
    if not 18.0 <= float(patient.age_years) <= 100.0:
        raise ValueError("formal torso anatomy requires adult age within [18, 100] years")
    if not 140.0 <= float(patient.height_cm) <= 205.0:
        raise ValueError("patient.height_cm must be within [140, 205]")
    if not 14.0 <= float(patient.bmi) <= 50.0:
        raise ValueError("patient.bmi must be within [14, 50]")
    if not 35.0 <= float(patient.weight_kg) <= 220.0:
        raise ValueError("patient.weight_kg must be within [35, 220]")
    expected_weight = float(patient.bmi) * (float(patient.height_cm) / 100.0) ** 2
    if abs(float(patient.weight_kg) - expected_weight) / expected_weight > 0.03:
        raise ValueError("patient weight, height and BMI are internally inconsistent")

    if any(np.any(liver_mask[index]) for index in (0, -1)):
        raise ValueError("liver must not touch the superior/inferior grid boundary")
    if any(np.any(liver_mask[:, index, :]) for index in (0, -1)):
        raise ValueError("liver must not touch the anterior/posterior grid boundary")
    if any(np.any(liver_mask[:, :, index]) for index in (0, -1)):
        raise ValueError("liver must not touch the left/right grid boundary")
    return liver_mask


def _world_axes(grid: GridSpecV2) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    affine = grid.affine_4x4
    spacing = float(grid.voxel_size_mm)
    s = affine[0, 3] + np.arange(grid.shape[0], dtype=np.float32) * spacing
    a = affine[1, 3] + np.arange(grid.shape[1], dtype=np.float32) * spacing
    r = affine[2, 3] + np.arange(grid.shape[2], dtype=np.float32) * spacing
    return s[:, None, None], a[None, :, None], r[None, None, :]


def _clamp(value: float, lower: float, upper: float) -> float:
    return float(min(max(value, lower), upper))


def _habitus_dimensions(patient: PatientSampleV2, grid: GridSpecV2) -> dict[str, float]:
    height_scale = float(patient.height_cm) / 172.0
    bmi_delta = float(patient.bmi) - 25.0
    sex_lr_base = 392.0 if patient.sex == "male" else 366.0
    sex_ap_base = 268.0 if patient.sex == "male" else 252.0
    fov_si = grid.shape[0] * float(grid.voxel_size_mm)
    si_mm = _clamp(3.0 * float(patient.height_cm), 470.0, fov_si - 4.0 * grid.voxel_size_mm)
    lr_mm = _clamp(sex_lr_base * height_scale**0.35 + 4.0 * bmi_delta, 325.0, 500.0)
    ap_mm = _clamp(sex_ap_base * height_scale**0.20 + 6.0 * bmi_delta, 215.0, 400.0)
    fat_thickness_mm = _clamp(10.0 + 1.25 * (float(patient.bmi) - 18.0), 8.0, 42.0)
    return {
        "body_si_mm": si_mm,
        "body_ap_mm": ap_mm,
        "body_lr_mm": lr_mm,
        "subcutaneous_fat_thickness_mm": fat_thickness_mm,
    }


def _mask_metrics(mask: np.ndarray, grid: GridSpecV2) -> dict[str, object]:
    coordinates = np.argwhere(mask)
    if coordinates.size == 0:
        return {
            "voxel_count": 0,
            "volume_ml": 0.0,
            "centroid_world_mm_sar": None,
            "extent_mm_zyx": (0.0, 0.0, 0.0),
        }
    centroid_index = coordinates.mean(axis=0)
    world = grid.affine_4x4[:3, :3] @ centroid_index + grid.affine_4x4[:3, 3]
    extents = (coordinates.max(axis=0) - coordinates.min(axis=0) + 1) * float(
        grid.voxel_size_mm
    )
    return {
        "voxel_count": int(coordinates.shape[0]),
        "volume_ml": float(coordinates.shape[0] * grid.voxel_volume_ml),
        "centroid_world_mm_sar": tuple(float(value) for value in world),
        "extent_mm_zyx": tuple(float(value) for value in extents),
    }


def _build_masks(
    liver_mask: np.ndarray,
    grid: GridSpecV2,
    patient: PatientSampleV2,
) -> tuple[AttenuationAnatomyV2, dict[str, object], dict[str, float]]:
    s, a, r = _world_axes(grid)
    design = _habitus_dimensions(patient, grid)
    body_si = design["body_si_mm"]
    body_ap = design["body_ap_mm"]
    body_lr = design["body_lr_mm"]

    # The formal V2 grid uses world components (S, A, R): positive S is superior,
    # positive A anterior and positive R the patient's right.  A high-order S term
    # gives a cropped torso rather than an ellipsoidal whole-body phantom.
    body_center_s = 0.0
    body_center_a = -4.0 + 0.10 * (float(patient.bmi) - 25.0)
    body_center_r = 0.0
    body = (
        (np.abs((s - body_center_s) / (0.5 * body_si)) ** 7.0)
        + (np.abs((a - body_center_a) / (0.5 * body_ap)) ** 2.35)
        + (np.abs((r - body_center_r) / (0.5 * body_lr)) ** 2.35)
        <= 1.0
    )

    # The sampled liver is authoritative.  A small deterministic soft-tissue
    # collar makes containment exact even for population-tail liver positions.
    collar_iterations = max(1, int(math.ceil(8.0 / float(grid.voxel_size_mm))))
    liver_collar = ndimage.binary_dilation(liver_mask, iterations=collar_iterations)
    body = ndimage.binary_fill_holes(
        ndimage.binary_closing(body | liver_collar, iterations=1)
    ).astype(bool, copy=False)

    lung_center_s = 72.0 + 0.03 * (float(patient.height_cm) - 172.0) * 10.0
    lung_center_a = 18.0
    lung_offset_r = 0.215 * body_lr
    lung_si_radius = _clamp(0.255 * body_si, 112.0, 140.0)
    lung_ap_radius = _clamp(0.255 * body_ap, 58.0, 90.0)
    lung_lr_radius = _clamp(0.165 * body_lr, 52.0, 78.0)
    left_lung_raw = (
        ((s - lung_center_s) / lung_si_radius) ** 2
        + ((a - lung_center_a) / lung_ap_radius) ** 2
        + ((r + lung_offset_r) / lung_lr_radius) ** 2
        <= 1.0
    )
    right_lung_raw = (
        ((s - (lung_center_s - 5.0)) / (1.03 * lung_si_radius)) ** 2
        + ((a - lung_center_a) / lung_ap_radius) ** 2
        + ((r - lung_offset_r) / lung_lr_radius) ** 2
        <= 1.0
    )
    left_lung = left_lung_raw & body & ~liver_mask
    right_lung = right_lung_raw & body & ~liver_mask
    lung = left_lung | right_lung

    spine_center_a = -0.31 * body_ap
    spine_ap_radius = _clamp(15.0 + 0.20 * (float(patient.bmi) - 25.0), 13.0, 21.0)
    spine_lr_radius = _clamp(19.0 + 0.10 * (float(patient.height_cm) - 172.0), 16.0, 25.0)
    spine_half_si = 0.42 * body_si
    spine = (
        (np.abs(s + 5.0) <= spine_half_si)
        & (((a - spine_center_a) / spine_ap_radius) ** 2 + (r / spine_lr_radius) ** 2 <= 1.0)
    )
    bone = spine & body & ~liver_mask & ~lung

    distance_inside_mm = ndimage.distance_transform_edt(body) * float(grid.voxel_size_mm)
    fat = (
        body
        & (distance_inside_mm <= design["subcutaneous_fat_thickness_mm"])
        & ~liver_mask
        & ~lung
        & ~bone
    )

    anatomy = AttenuationAnatomyV2(
        body_mask=body.astype(bool, copy=False),
        liver_mask=liver_mask.copy(),
        lung_mask=lung.astype(bool, copy=False),
        bone_mask=bone.astype(bool, copy=False),
        fat_mask=fat.astype(bool, copy=False),
        affine_4x4=grid.affine_4x4.copy(),
    )
    auxiliaries: dict[str, object] = {
        "left_lung_mask": left_lung,
        "right_lung_mask": right_lung,
        "distance_inside_body_mm": distance_inside_mm,
    }
    return anatomy, auxiliaries, design


def _evaluate_qc(
    anatomy: AttenuationAnatomyV2,
    auxiliaries: Mapping[str, object],
    design: Mapping[str, float],
    grid: GridSpecV2,
) -> tuple[TorsoAnatomyQCV2, dict[str, object]]:
    body = anatomy.body_mask
    compartments = {
        "liver": anatomy.liver_mask,
        "lung": anatomy.lung_mask,
        "bone": anatomy.bone_mask,
        "fat": anatomy.fat_mask,
    }
    metrics_by_tissue = {
        name: _mask_metrics(mask, grid)
        for name, mask in {"body": body, **compartments}.items()
    }
    left_metrics = _mask_metrics(np.asarray(auxiliaries["left_lung_mask"]), grid)
    right_metrics = _mask_metrics(np.asarray(auxiliaries["right_lung_mask"]), grid)
    body_components = int(ndimage.label(body)[1])
    lung_components = int(ndimage.label(anatomy.lung_mask)[1])
    outside_voxels = {
        name: int(np.count_nonzero(mask & ~body)) for name, mask in compartments.items()
    }
    overlap_voxels: dict[str, int] = {}
    names = tuple(compartments)
    for index, first in enumerate(names):
        for second in names[index + 1 :]:
            overlap_voxels[f"{first}__{second}"] = int(
                np.count_nonzero(compartments[first] & compartments[second])
            )

    body_extent = tuple(float(value) for value in metrics_by_tissue["body"]["extent_mm_zyx"])
    body_volume = float(metrics_by_tissue["body"]["volume_ml"])
    fat_fraction = float(metrics_by_tissue["fat"]["volume_ml"]) / body_volume
    liver_centroid = metrics_by_tissue["liver"]["centroid_world_mm_sar"]
    lung_centroid = metrics_by_tissue["lung"]["centroid_world_mm_sar"]
    bone_centroid = metrics_by_tissue["bone"]["centroid_world_mm_sar"]
    left_centroid = left_metrics["centroid_world_mm_sar"]
    right_centroid = right_metrics["centroid_world_mm_sar"]

    limits: dict[str, object] = {
        "body_extent_mm_zyx": ((440.0, 570.0), (200.0, 420.0), (300.0, 520.0)),
        "body_volume_ml": (20_000.0, 80_000.0),
        "total_lung_volume_ml": (1_500.0, 8_000.0),
        "fat_fraction_of_body": (0.02, 0.50),
        "lung_superior_to_liver_mm_min": 55.0,
        "spine_posterior_to_liver_mm_min": 45.0,
        "required_lung_components": 2,
    }
    numeric_metrics: dict[str, object] = {
        "body_extent_mm_zyx": body_extent,
        "body_volume_ml": body_volume,
        "total_lung_volume_ml": float(metrics_by_tissue["lung"]["volume_ml"]),
        "left_lung_volume_ml": float(left_metrics["volume_ml"]),
        "right_lung_volume_ml": float(right_metrics["volume_ml"]),
        "fat_fraction_of_body": fat_fraction,
        "body_connected_components": body_components,
        "lung_connected_components": lung_components,
        "outside_body_voxels": outside_voxels,
        "compartment_overlap_voxels": overlap_voxels,
        "lung_minus_liver_centroid_s_mm": float(lung_centroid[0] - liver_centroid[0]),
        "liver_minus_spine_centroid_a_mm": float(liver_centroid[1] - bone_centroid[1]),
        "left_lung_centroid_r_mm": float(left_centroid[2]),
        "right_lung_centroid_r_mm": float(right_centroid[2]),
    }

    failures: list[str] = []
    if not np.allclose(anatomy.affine_4x4, grid.affine_4x4, rtol=0.0, atol=1e-8):
        failures.append("affine_mismatch")
    if body_components != 1:
        failures.append("body_not_single_component")
    if lung_components != 2:
        failures.append("lungs_not_two_components")
    if any(outside_voxels.values()):
        failures.append("compartment_outside_body")
    if any(overlap_voxels.values()):
        failures.append("compartment_overlap")
    if not all(lower <= value <= upper for value, (lower, upper) in zip(body_extent, limits["body_extent_mm_zyx"])):
        failures.append("adult_body_extent")
    if not limits["body_volume_ml"][0] <= body_volume <= limits["body_volume_ml"][1]:
        failures.append("adult_body_volume")
    lung_volume = float(metrics_by_tissue["lung"]["volume_ml"])
    if not limits["total_lung_volume_ml"][0] <= lung_volume <= limits["total_lung_volume_ml"][1]:
        failures.append("adult_lung_volume")
    if not limits["fat_fraction_of_body"][0] <= fat_fraction <= limits["fat_fraction_of_body"][1]:
        failures.append("fat_shell_fraction")
    if numeric_metrics["lung_minus_liver_centroid_s_mm"] < limits["lung_superior_to_liver_mm_min"]:
        failures.append("lungs_not_superior_to_liver")
    if numeric_metrics["liver_minus_spine_centroid_a_mm"] < limits["spine_posterior_to_liver_mm_min"]:
        failures.append("spine_not_posterior_to_liver")
    if not numeric_metrics["left_lung_centroid_r_mm"] < 0.0 < numeric_metrics["right_lung_centroid_r_mm"]:
        failures.append("lung_laterality_inconsistent_with_sar")

    qc = TorsoAnatomyQCV2(
        passed=not failures,
        failed_gates=tuple(failures),
        metrics=numeric_metrics,
        limits=limits,
    )
    actual = {
        "tissues": metrics_by_tissue,
        "left_lung": left_metrics,
        "right_lung": right_metrics,
        "qc_metrics": numeric_metrics,
        "requested_body_extent_mm_zyx": (
            float(design["body_si_mm"]),
            float(design["body_ap_mm"]),
            float(design["body_lr_mm"]),
        ),
    }
    return qc, actual


def build_torso_anatomy_v2(
    liver: LiverGeometryV2,
    grid: GridSpecV2,
    patient: PatientSampleV2,
) -> TorsoAnatomyBuildV2:
    """Build the deterministic formal 128^3 attenuation anatomy and its QC record.

    `body_mask` retains the established AttenuationAnatomyV2 meaning: it is the
    containing patient envelope.  Liver, lung, bone and fat are pairwise-disjoint
    tissue compartments inside that envelope; remaining body voxels are soft tissue.
    """
    liver_mask = _validate_inputs(liver, grid, patient)
    anatomy, auxiliaries, design = _build_masks(liver_mask, grid, patient)
    qc, actual = _evaluate_qc(anatomy, auxiliaries, design, grid)
    metadata = TorsoAnatomyMetadataV2(
        schema_version="pars_torso_anatomy_v2",
        model_id="deterministic_habitus_conditioned_torso_v2",
        axis_order=AXIS_ORDER_V2,
        orientation_code=ORIENTATION_CODE_V2,
        reference_phase="end_expiration",
        affine_4x4=tuple(
            tuple(float(value) for value in row) for row in grid.affine_4x4
        ),
        voxel_size_mm=float(grid.voxel_size_mm),
        patient_habitus={
            "case_id": patient.case_id,
            "sex": patient.sex,
            "age_years": float(patient.age_years),
            "height_cm": float(patient.height_cm),
            "weight_kg": float(patient.weight_kg),
            "bmi": float(patient.bmi),
        },
        design_parameters={
            **design,
            "coordinate_meaning": {"Z": "superior", "Y": "anterior", "X": "right"},
            "lung_model": "paired_superior_ellipsoids",
            "bone_model": "posterior_vertebral_column_proxy",
            "fat_model": "subcutaneous_distance_shell",
            "randomness": "none",
        },
        actual_metrics=actual,
        tissue_priority=("liver", "lung", "bone", "fat", "soft_tissue", "outside_air"),
        body_mask_semantics="containing patient envelope; unassigned body voxels are soft tissue",
        qc=qc,
    )
    if not qc.passed:
        raise TorsoAnatomyRejectedError(qc)
    return TorsoAnatomyBuildV2(anatomy=anatomy, metadata=metadata)


def build_attenuation_anatomy_v2(
    liver: LiverGeometryV2,
    grid: GridSpecV2,
    patient: PatientSampleV2,
) -> AttenuationAnatomyV2:
    """Return only the formal anatomy for direct use by generate_attenuation_maps."""
    return build_torso_anatomy_v2(liver, grid, patient).anatomy
