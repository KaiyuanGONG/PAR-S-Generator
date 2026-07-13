from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.liver_geometry import GridSpecV2  # noqa: E402
from core.schemas_v2 import TumorTargetV2  # noqa: E402
from core.tumor_generator_v2 import (  # noqa: E402
    TumorRasterizationRejectedError,
    rasterize_tumor_at_center,
)


def _target(dmax_mm: float, *, stress: bool = False) -> TumorTargetV2:
    confluent = dmax_mm > 100.0
    return TumorTargetV2(
        lesion_id=f"lesion_{dmax_mm:g}",
        dmax_mm=dmax_mm,
        axis_ratios=(0.82, 0.91),
        lobe="right",
        morphology="lobulated_confluent" if confluent else "smooth_nodular",
        orientation_deg_zyx=(23.0, -17.0, 31.0),
        primitive_count=3 if confluent else 1,
        evidence_types={"dmax": "stress_test" if stress else "engineering_prior"},
    )


@pytest.mark.parametrize("dmax_mm", [10.0, 20.0, 40.0, 60.0, 100.0, 200.0])
def test_required_main_population_diameters_are_physical_and_complete(dmax_mm: float) -> None:
    grid = GridSpecV2()
    voxel_center = tuple(float(value) for value in grid.affine_4x4[:3, :3] @ np.array((64, 64, 64)) + grid.affine_4x4[:3, 3])
    raster = rasterize_tumor_at_center(_target(dmax_mm), voxel_center, grid)

    tolerance_mm = max(0.75 * grid.voxel_size_mm, 0.03 * dmax_mm)
    assert raster.metrics.recist_3d_mm == pytest.approx(dmax_mm, abs=tolerance_mm)
    assert raster.dmax_error_mm <= tolerance_mm
    assert raster.mask.dtype == bool
    assert raster.mask.shape == grid.shape
    assert raster.grid_boundary_clear
    assert raster.connected


def test_215_mm_is_stress_only_and_remains_uncropped() -> None:
    grid = GridSpecV2()
    with pytest.raises(ValueError, match="stress_test"):
        rasterize_tumor_at_center(_target(215.0), (0.0, 0.0, 0.0), grid)

    stress = rasterize_tumor_at_center(_target(215.0, stress=True), (0.0, 0.0, 0.0), grid)
    assert stress.metrics.recist_3d_mm == pytest.approx(215.0, abs=max(0.75 * 4.42, 6.45))
    assert stress.grid_boundary_clear


def test_dmax_above_100_mm_requires_one_connected_confluent_instance() -> None:
    target = _target(120.0)
    with pytest.raises(ValueError, match="lobulated_confluent"):
        rasterize_tumor_at_center(
            replace(target, morphology="smooth_nodular", primitive_count=1),
            (0.0, 0.0, 0.0),
            GridSpecV2(),
        )

    raster = rasterize_tumor_at_center(target, (0.0, 0.0, 0.0), GridSpecV2())
    assert raster.primitive_count == 3
    assert raster.primitive_overlap_voxels > 0
    assert raster.primitive_overlap_fraction > 0.0
    assert raster.connected


def test_local_crop_padding_cannot_rescale_or_change_the_tumor() -> None:
    grid = GridSpecV2()
    target = _target(60.0)
    small = rasterize_tumor_at_center(target, (13.0, -7.0, 21.0), grid, padding_voxels=2)
    large = rasterize_tumor_at_center(target, (13.0, -7.0, 21.0), grid, padding_voxels=9)

    assert np.array_equal(small.mask, large.mask)
    assert small.metrics.recist_3d_mm == large.metrics.recist_3d_mm
    assert small.metrics.volume_ml == large.metrics.volume_ml


def test_grid_boundary_is_rejected_instead_of_returning_a_cropped_mask() -> None:
    target = _target(100.0)
    with pytest.raises(TumorRasterizationRejectedError) as error:
        rasterize_tumor_at_center(target, (275.0, 0.0, 0.0), GridSpecV2())
    assert error.value.rejection.reason_code == "grid_boundary_clipped"
