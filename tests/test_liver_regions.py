from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.liver_geometry import GridSpecV2  # noqa: E402
from core.liver_regions import REGION_LABELS_V2, build_liver_regions  # noqa: E402


def _ellipsoid(shape, center, radii) -> np.ndarray:
    zz, yy, xx = np.indices(shape, dtype=np.float64)
    return (
        ((zz - center[0]) / radii[0]) ** 2
        + ((yy - center[1]) / radii[1]) ** 2
        + ((xx - center[2]) / radii[2]) ** 2
    ) <= 1.0


def test_region_proxies_are_disjoint_and_exactly_cover_liver() -> None:
    grid = GridSpecV2(shape=(72, 72, 72), voxel_size_mm=2.5)
    liver = _ellipsoid(grid.shape, (36, 36, 36), (25, 19, 28))
    caudate = _ellipsoid(grid.shape, (37, 29, 36), (7, 5, 6)) & liver

    regions = build_liver_regions(
        liver,
        grid.affine_4x4,
        target_left_fraction=0.34,
        target_s1_3_to_s4_8_ratio=0.36,
        caudate_mask=caudate,
    )

    assert regions.labels.dtype == np.uint8
    assert np.array_equal(regions.labels > 0, liver)
    assert np.all(regions.labels[~liver] == 0)
    assert set(np.unique(regions.labels)) == {0, *REGION_LABELS_V2}
    for label_id in REGION_LABELS_V2:
        assert np.count_nonzero(regions.labels == label_id) > 0
    assert sum(regions.region_voxel_counts.values()) == int(liver.sum())


def test_region_targets_are_met_to_voxel_quantisation() -> None:
    grid = GridSpecV2(shape=(80, 80, 80), voxel_size_mm=3.0)
    liver = _ellipsoid(grid.shape, (40, 40, 40), (28, 21, 31))
    caudate = _ellipsoid(grid.shape, (42, 33, 40), (6, 5, 6)) & liver
    target_left = 0.41
    target_ratio = 0.58

    regions = build_liver_regions(
        liver,
        grid.affine_4x4,
        target_left_fraction=target_left,
        target_s1_3_to_s4_8_ratio=target_ratio,
        caudate_mask=caudate,
    )

    voxel_fraction = 1.0 / liver.sum()
    assert regions.left_fraction == pytest.approx(target_left, abs=2 * voxel_fraction)
    assert regions.s1_3_to_s4_8_ratio == pytest.approx(target_ratio, abs=4 * voxel_fraction)
    assert regions.caudate_fraction == pytest.approx(caudate.sum() / liver.sum(), abs=voxel_fraction)


def test_region_builder_rejects_non_proxy_inputs() -> None:
    grid = GridSpecV2(shape=(32, 32, 32), voxel_size_mm=4.42)
    liver = _ellipsoid(grid.shape, (16, 16, 16), (10, 8, 11))
    outside = np.zeros_like(liver)
    outside[0, 0, 0] = True

    with pytest.raises(ValueError, match="contained"):
        build_liver_regions(
            liver,
            grid.affine_4x4,
            target_left_fraction=0.3,
            target_s1_3_to_s4_8_ratio=0.3,
            caudate_mask=outside,
        )
    with pytest.raises(ValueError, match="left fraction"):
        build_liver_regions(
            liver,
            grid.affine_4x4,
            target_left_fraction=0.99,
            target_s1_3_to_s4_8_ratio=0.3,
        )
