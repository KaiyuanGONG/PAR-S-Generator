from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from scipy import ndimage


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.liver_geometry import GridSpecV2, fit_liver_geometry  # noqa: E402
from core.phantom_generator import PhantomConfig, PhantomGenerator  # noqa: E402
from core.schemas_v2 import LiverTargetV2, load_evidence_registry, load_profile  # noqa: E402


def _target(morphology: str = "normal") -> LiverTargetV2:
    cirrhotic = morphology == "cirrhotic"
    return LiverTargetV2(
        volume_ml=1533.0,
        lr_mm=260.0,
        ap_mm=150.0,
        si_mm=155.0,
        left_fraction=0.42 if cirrhotic else 0.31,
        centroid_mm=(-45.0, 15.0, 35.0),
        morphology=morphology,
        s1_3_to_s4_8_ratio=0.56 if cirrhotic else 0.29,
        caudate_fraction=0.055 if cirrhotic else 0.020,
        surface_roughness_target=0.273 if cirrhotic else 0.256,
        surface_field_amplitude=0.40 if cirrhotic else 0.06,
        caudate_enabled=True,
        evidence_types={
            "volume_reference": "literature_population",
            "volume_model": "engineering_prior",
            "morphology": "engineering_prior",
            "segment_proxy": "literature_population",
        },
    )


def test_normal_liver_hits_volume_axes_centroid_and_left_fraction_targets() -> None:
    grid = GridSpecV2(shape=(128, 128, 128), voxel_size_mm=4.42)
    target = _target("normal")

    geometry = fit_liver_geometry(target, grid)

    assert geometry.mask.dtype == bool
    assert geometry.mask.shape == grid.shape
    assert geometry.target_metrics["volume_ml"] == target.volume_ml
    assert geometry.actual_metrics["volume_ml"] == pytest.approx(target.volume_ml, rel=0.04)
    assert geometry.actual_metrics["extent_mm_zyx"] == pytest.approx(
        (target.si_mm, target.ap_mm, target.lr_mm), abs=2.5 * grid.voxel_size_mm
    )
    assert geometry.actual_metrics["centroid_world_mm"] == pytest.approx(
        target.centroid_mm, abs=1.5 * grid.voxel_size_mm
    )
    assert geometry.actual_metrics["left_fraction"] == pytest.approx(target.left_fraction, abs=0.025)
    assert np.array_equal(geometry.region_labels > 0, geometry.mask)
    assert geometry.continuous_parameters["surface_field_kind"] == "analytic_low_frequency"


def test_cirrhotic_geometry_has_directionally_correct_proxy_changes() -> None:
    grid = GridSpecV2(shape=(128, 128, 128), voxel_size_mm=4.42)
    normal = fit_liver_geometry(_target("normal"), grid)
    cirrhotic = fit_liver_geometry(_target("cirrhotic"), grid)

    assert cirrhotic.actual_metrics["s1_3_to_s4_8_ratio"] > normal.actual_metrics["s1_3_to_s4_8_ratio"]
    assert cirrhotic.actual_metrics["caudate_fraction"] > normal.actual_metrics["caudate_fraction"]
    assert cirrhotic.actual_metrics["surface_roughness"] > normal.actual_metrics["surface_roughness"] + 0.01
    assert cirrhotic.actual_metrics["volume_ml"] == pytest.approx(_target("cirrhotic").volume_ml, rel=0.04)
    assert ndimage.label(cirrhotic.mask)[1] == 1


def test_optional_caudate_primitive_can_be_disabled() -> None:
    grid = GridSpecV2(shape=(96, 96, 96), voxel_size_mm=4.42)
    target = replace(
        _target("normal"),
        volume_ml=1200.0,
        lr_mm=235.0,
        ap_mm=138.0,
        si_mm=145.0,
        caudate_enabled=False,
        caudate_fraction=0.0,
    )

    geometry = fit_liver_geometry(target, grid)

    assert not geometry.primitive_masks["caudate"].any()
    assert geometry.actual_metrics["caudate_fraction"] == 0.0
    assert geometry.continuous_parameters["caudate_enabled"] is False


def test_v2_liver_path_is_exposed_without_changing_v1_generation() -> None:
    registry = load_evidence_registry(REPO_ROOT / "configs" / "evidence_registry_v2.json")
    profile = load_profile(REPO_ROOT / "configs" / "population_tare_hcc_nopvi_v2.json", registry)
    generator = PhantomGenerator(PhantomConfig(volume_shape=(96, 96, 96), voxel_size_mm=4.42))

    case = generator.generate_liver_v2(profile, np.random.default_rng(123), case_id="v2_case_0001")

    assert case.patient.case_id == "v2_case_0001"
    assert case.target.morphology == case.patient.liver_morphology
    assert case.geometry.mask.shape == (96, 96, 96)
    assert callable(generator.generate_one)


def test_grid_and_target_validation_fail_before_rasterisation() -> None:
    with pytest.raises(ValueError, match="voxel_size_mm"):
        GridSpecV2(shape=(128, 128, 128), voxel_size_mm=0.0)

    grid = GridSpecV2(shape=(64, 64, 64), voxel_size_mm=4.42)
    with pytest.raises(ValueError, match="volume"):
        fit_liver_geometry(replace(_target(), volume_ml=-1.0), grid)
    with pytest.raises(ValueError, match="fit inside"):
        fit_liver_geometry(replace(_target(), lr_mm=400.0), grid)
