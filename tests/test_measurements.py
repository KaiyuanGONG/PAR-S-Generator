from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.measurements import (  # noqa: E402
    measure_lesions,
    measure_liver,
    measure_path_lengths,
    signed_distance_mm,
)


def affine_zyx(spacing=(1.0, 1.0, 1.0), origin=(0.0, 0.0, 0.0)) -> np.ndarray:
    affine = np.eye(4, dtype=np.float64)
    affine[:3, :3] = np.diag(spacing)
    affine[:3, 3] = origin
    return affine


def ellipsoid_mask(shape, center, radii) -> np.ndarray:
    zz, yy, xx = np.indices(shape, dtype=np.float64)
    return (
        ((zz - center[0]) / radii[0]) ** 2
        + ((yy - center[1]) / radii[1]) ** 2
        + ((xx - center[2]) / radii[2]) ** 2
    ) <= 1.0


def test_sphere_measurements_match_analytic_values_with_voxel_tolerance() -> None:
    radius_mm = 20.0
    mask = ellipsoid_mask((64, 64, 64), (31, 32, 33), (radius_mm,) * 3)
    metrics = measure_liver(mask, affine_zyx())
    analytic_volume_ml = 4.0 / 3.0 * math.pi * radius_mm**3 / 1000.0

    assert metrics.voxel_count == int(mask.sum())
    assert metrics.volume_ml == pytest.approx(analytic_volume_ml, rel=0.04)
    assert metrics.centroid_world_mm == pytest.approx((31.0, 32.0, 33.0), abs=0.05)
    assert metrics.equivalent_diameter_mm == pytest.approx(2 * radius_mm, abs=1.0)
    assert metrics.recist_3d_mm == pytest.approx(2 * radius_mm, abs=1.5)
    assert metrics.principal_axes_mm == pytest.approx((41.0, 41.0, 41.0), abs=1.5)
    assert 0.90 <= metrics.sphericity <= 1.0
    assert 0.0 <= metrics.surface_roughness <= 0.08


def test_ellipsoid_principal_axes_and_world_centroid_are_physical() -> None:
    radii = (24.0, 16.0, 10.0)
    center = (42, 39, 45)
    origin = (100.0, -50.0, 12.0)
    mask = ellipsoid_mask((96, 96, 96), center, radii)
    metrics = measure_liver(mask, affine_zyx(origin=origin))
    expected_centroid = tuple(origin[index] + center[index] for index in range(3))

    assert metrics.centroid_world_mm == pytest.approx(expected_centroid, abs=0.05)
    assert metrics.principal_axes_mm == pytest.approx((49.0, 33.0, 21.0), abs=1.5)
    assert metrics.recist_3d_mm == pytest.approx(49.0, abs=1.5)
    analytic_volume_ml = 4.0 / 3.0 * math.pi * np.prod(radii) / 1000.0
    assert metrics.volume_ml == pytest.approx(analytic_volume_ml, rel=0.05)


def test_measure_lesions_uses_instance_mask_and_returns_sorted_ids() -> None:
    instances = np.zeros((72, 72, 72), dtype=np.uint16)
    instances[ellipsoid_mask(instances.shape, (22, 24, 25), (6, 6, 6))] = 2
    instances[ellipsoid_mask(instances.shape, (50, 48, 46), (10, 8, 7))] = 7

    lesions = measure_lesions(instances, affine_zyx(spacing=(2.0, 2.0, 2.0)))

    assert [lesion.instance_id for lesion in lesions] == [2, 7]
    assert lesions[0].volume_ml < lesions[1].volume_ml
    assert lesions[0].centroid_world_mm == pytest.approx((44.0, 48.0, 50.0), abs=0.1)
    assert lesions[1].recist_3d_mm > lesions[0].recist_3d_mm


def test_signed_distance_is_positive_inside_and_negative_outside() -> None:
    mask = np.zeros((21, 21, 21), dtype=bool)
    mask[5:16, 5:16, 5:16] = True
    distance = signed_distance_mm(mask, affine_zyx(spacing=(2.0, 2.0, 2.0)))

    assert distance[10, 10, 10] > 10.0
    assert distance[5, 10, 10] == pytest.approx(2.0)
    assert distance[4, 10, 10] == pytest.approx(-2.0)
    assert distance[0, 0, 0] < distance[4, 10, 10]


def test_lobulated_union_is_rougher_than_single_smooth_ellipsoid() -> None:
    shape = (80, 80, 80)
    smooth = ellipsoid_mask(shape, (40, 40, 40), (18, 14, 12))
    lobulated = smooth.copy()
    lobulated |= ellipsoid_mask(shape, (40, 55, 40), (10, 10, 10))
    lobulated |= ellipsoid_mask(shape, (40, 27, 47), (8, 9, 8))

    smooth_metrics = measure_liver(smooth, affine_zyx())
    lobulated_metrics = measure_liver(lobulated, affine_zyx())

    assert lobulated_metrics.surface_roughness > smooth_metrics.surface_roughness
    assert lobulated_metrics.sphericity < smooth_metrics.sphericity


def test_path_lengths_report_every_view_and_known_axis_aligned_depth() -> None:
    body = np.zeros((48, 64, 72), dtype=bool)
    liver = np.zeros_like(body)
    body[14:34, 17:47, 16:56] = True
    liver[18:30, 24:40, 26:46] = True

    paths = measure_path_lengths(
        body,
        liver,
        affine_zyx(spacing=(2.0, 2.0, 2.0)),
        views=60,
    )

    assert len(paths.angles_deg) == 60
    assert paths.angles_deg[:3] == pytest.approx((90.0, 96.0, 102.0))
    assert paths.body[0].p05_mm == pytest.approx(60.0)
    assert paths.body[0].p50_mm == pytest.approx(60.0)
    assert paths.body[0].p95_mm == pytest.approx(60.0)
    assert paths.liver[0].p50_mm == pytest.approx(32.0)
    assert all(item.mean_mm > 0 for item in paths.body)
    assert all(item.mean_mm > 0 for item in paths.liver)


def test_measurements_reject_empty_masks_and_sheared_affines() -> None:
    with pytest.raises(ValueError, match="empty"):
        measure_liver(np.zeros((8, 8, 8), dtype=bool), affine_zyx())

    sheared = affine_zyx()
    sheared[0, 1] = 0.2
    with pytest.raises(ValueError, match="orthogonal"):
        measure_liver(np.ones((8, 8, 8), dtype=bool), sheared)
