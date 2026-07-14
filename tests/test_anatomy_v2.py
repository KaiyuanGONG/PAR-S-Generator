from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from scipy import ndimage


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.anatomy_v2 import (  # noqa: E402
    TorsoAnatomyRejectedError,
    build_attenuation_anatomy_v2,
    build_torso_anatomy_v2,
)
from core.attenuation_model_v2 import AttenuationAnatomyV2  # noqa: E402
from core.liver_geometry import GridSpecV2, LiverGeometryV2  # noqa: E402
from core.schemas_v2 import PatientSampleV2  # noqa: E402


def _patient(*, bmi: float = 26.4, sex: str = "male") -> PatientSampleV2:
    height = 174.0 if sex == "male" else 162.0
    return PatientSampleV2(
        case_id=f"anatomy_{sex}_{bmi:.1f}",
        sex=sex,
        age_years=66.0,
        height_cm=height,
        weight_kg=bmi * (height / 100.0) ** 2,
        bmi=bmi,
        liver_morphology="cirrhotic",
        evidence_types={},
    )


def _liver(grid: GridSpecV2 | None = None) -> LiverGeometryV2:
    grid = grid or GridSpecV2()
    s = grid.affine_4x4[0, 3] + np.arange(grid.shape[0])[:, None, None] * grid.voxel_size_mm
    a = grid.affine_4x4[1, 3] + np.arange(grid.shape[1])[None, :, None] * grid.voxel_size_mm
    r = grid.affine_4x4[2, 3] + np.arange(grid.shape[2])[None, None, :] * grid.voxel_size_mm
    mask = (
        ((s + 45.0) / 78.0) ** 2
        + ((a - 15.0) / 66.0) ** 2
        + ((r - 35.0) / 102.0) ** 2
        <= 1.0
    )
    labels = np.zeros(grid.shape, dtype=np.uint8)
    labels[mask] = 1
    return LiverGeometryV2(
        mask=mask.astype(bool),
        region_labels=labels,
        affine_4x4=grid.affine_4x4,
        primitive_masks={},
        target_metrics={},
        actual_metrics={},
        continuous_parameters={},
        evidence_types={},
    )


def _centroid(mask: np.ndarray, grid: GridSpecV2) -> np.ndarray:
    index = np.argwhere(mask).mean(axis=0)
    return grid.affine_4x4[:3, :3] @ index + grid.affine_4x4[:3, 3]


def test_formal_builder_returns_contained_disjoint_128_cube_anatomy() -> None:
    grid = GridSpecV2()
    liver = _liver(grid)
    result = build_torso_anatomy_v2(liver, grid, _patient())
    anatomy = result.anatomy

    assert isinstance(anatomy, AttenuationAnatomyV2)
    assert anatomy.body_mask.shape == (128, 128, 128)
    assert np.array_equal(anatomy.affine_4x4, grid.affine_4x4)
    assert np.array_equal(anatomy.liver_mask, liver.mask)
    assert ndimage.label(anatomy.body_mask)[1] == 1
    assert ndimage.label(anatomy.lung_mask)[1] == 2

    compartments = (
        anatomy.liver_mask,
        anatomy.lung_mask,
        anatomy.bone_mask,
        anatomy.fat_mask,
    )
    assert all(mask.dtype == np.bool_ for mask in (anatomy.body_mask, *compartments))
    assert all(np.all(mask <= anatomy.body_mask) for mask in compartments)
    for index, first in enumerate(compartments):
        for second in compartments[index + 1 :]:
            assert not np.any(first & second)
    soft_tissue = anatomy.body_mask & ~np.logical_or.reduce(compartments)
    assert soft_tissue.any()
    assert result.metadata.qc.passed
    assert result.metadata.qc.failed_gates == ()


def test_sar_orientation_places_lungs_superior_and_spine_posterior() -> None:
    grid = GridSpecV2()
    result = build_torso_anatomy_v2(_liver(grid), grid, _patient())
    anatomy = result.anatomy
    liver_center = _centroid(anatomy.liver_mask, grid)
    lung_center = _centroid(anatomy.lung_mask, grid)
    spine_center = _centroid(anatomy.bone_mask, grid)

    assert result.metadata.axis_order == "ZYX"
    assert result.metadata.orientation_code == "SAR"
    assert lung_center[0] > liver_center[0] + 55.0
    assert spine_center[1] < liver_center[1] - 45.0
    assert result.metadata.qc.metrics["left_lung_centroid_r_mm"] < 0.0
    assert result.metadata.qc.metrics["right_lung_centroid_r_mm"] > 0.0


def test_body_is_adult_sized_and_fat_is_a_surface_shell() -> None:
    grid = GridSpecV2()
    result = build_torso_anatomy_v2(_liver(grid), grid, _patient())
    anatomy = result.anatomy
    body_extent = result.metadata.actual_metrics["tissues"]["body"]["extent_mm_zyx"]
    assert 440.0 <= body_extent[0] <= 570.0
    assert 200.0 <= body_extent[1] <= 420.0
    assert 300.0 <= body_extent[2] <= 520.0

    distance_mm = ndimage.distance_transform_edt(anatomy.body_mask) * grid.voxel_size_mm
    thickness = result.metadata.design_parameters["subcutaneous_fat_thickness_mm"]
    assert anatomy.fat_mask.any()
    assert np.all(distance_mm[anatomy.fat_mask] <= thickness + 1e-6)


def test_builder_is_bitwise_deterministic_and_plain_adapter_matches() -> None:
    grid = GridSpecV2()
    liver = _liver(grid)
    patient = _patient()
    first = build_torso_anatomy_v2(liver, grid, patient)
    second = build_torso_anatomy_v2(liver, grid, patient)
    for name in ("body_mask", "liver_mask", "lung_mask", "bone_mask", "fat_mask"):
        assert np.array_equal(getattr(first.anatomy, name), getattr(second.anatomy, name))
    assert first.metadata.as_dict() == second.metadata.as_dict()
    direct = build_attenuation_anatomy_v2(liver, grid, patient)
    assert np.array_equal(direct.body_mask, first.anatomy.body_mask)


def test_patient_habitus_controls_body_and_fat_without_randomness() -> None:
    grid = GridSpecV2()
    liver = _liver(grid)
    lean = build_torso_anatomy_v2(liver, grid, _patient(bmi=20.0, sex="female"))
    large = build_torso_anatomy_v2(liver, grid, _patient(bmi=38.0, sex="female"))

    lean_extent = lean.metadata.actual_metrics["tissues"]["body"]["extent_mm_zyx"]
    large_extent = large.metadata.actual_metrics["tissues"]["body"]["extent_mm_zyx"]
    assert large_extent[1] > lean_extent[1]
    assert large_extent[2] > lean_extent[2]
    assert (
        large.metadata.design_parameters["subcutaneous_fat_thickness_mm"]
        > lean.metadata.design_parameters["subcutaneous_fat_thickness_mm"]
    )
    assert large.metadata.qc.metrics["fat_fraction_of_body"] > lean.metadata.qc.metrics["fat_fraction_of_body"]


def test_malformed_grid_affine_and_habitus_fail_closed() -> None:
    grid = GridSpecV2()
    liver = _liver(grid)
    with pytest.raises(ValueError, match="128x128x128"):
        build_torso_anatomy_v2(_liver(GridSpecV2(shape=(96, 96, 96))), GridSpecV2(shape=(96, 96, 96)), _patient())
    bad_affine = np.asarray(liver.affine_4x4).copy()
    bad_affine[2, 3] += 1.0
    with pytest.raises(ValueError, match="affine"):
        build_torso_anatomy_v2(replace(liver, affine_4x4=bad_affine), grid, _patient())
    inconsistent = replace(_patient(), weight_kg=40.0)
    with pytest.raises(ValueError, match="internally inconsistent"):
        build_torso_anatomy_v2(liver, grid, inconsistent)


def test_qc_failure_is_a_hard_rejection() -> None:
    grid = GridSpecV2(voxel_size_mm=2.5)
    liver = _liver(grid)
    with pytest.raises(TorsoAnatomyRejectedError, match="adult_body_extent") as exc:
        build_torso_anatomy_v2(liver, grid, _patient())
    assert not exc.value.qc.passed
    assert "adult_body_extent" in exc.value.qc.failed_gates
