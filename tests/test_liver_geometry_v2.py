from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from scipy import ndimage


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.liver_geometry import (  # noqa: E402
    GridSpecV2,
    _central_waist_ratio,
    _constructive_template,
    _left_lobe_taper,
    fit_liver_geometry,
)
from core.hybrid_v2_adapter import HybridV2Adapter  # noqa: E402
from core.phantom_generator import PhantomConfig, PhantomGenerator  # noqa: E402
from core.schemas_v2 import LiverTargetV2, load_evidence_registry, load_profile  # noqa: E402


def _target(morphology: str = "normal") -> LiverTargetV2:
    cirrhotic = morphology == "cirrhotic"
    return LiverTargetV2(
        volume_ml=1533.0,
        lr_mm=200.0,
        ap_mm=160.0,
        si_mm=174.5,
        left_fraction=0.42 if cirrhotic else 0.31,
        centroid_mm=(-45.0, 15.0, 35.0),
        morphology=morphology,
        s1_3_to_s4_8_ratio=0.56 if cirrhotic else 0.29,
        caudate_fraction=0.055 if cirrhotic else 0.020,
        surface_roughness_target=0.273 if cirrhotic else 0.256,
        surface_field_amplitude=0.18 if cirrhotic else 0.04,
        caudate_enabled=True,
        evidence_types={
            "volume_reference": "literature_population",
            "volume_model": "engineering_prior",
            "morphology": "engineering_prior",
            "segment_proxy": "literature_population",
        },
    )


def test_profile_reference_extents_are_scaled_from_ship_mri_population() -> None:
    registry = load_evidence_registry(REPO_ROOT / "configs" / "evidence_registry_v2.json")
    profile = load_profile(REPO_ROOT / "configs" / "population_tare_hcc_nopvi_v2.json", registry)
    reference = profile.value("liver_extent_reference_mm_zyx")
    extents = np.asarray(reference["mean_at_profile_volume"], dtype=np.float64)
    mean_volume_ml = float(profile.value("liver_volume_reference_ml")["mean"])
    bbox_fill = mean_volume_ml * 1000.0 / float(np.prod(extents))

    assert reference["healthy_sample_size"] == 886
    assert reference["source_mean_mm_zyx"] == pytest.approx((172.0, 158.0, 197.0))
    assert reference["source_sd_mm_zyx"] == pytest.approx((20.0, 19.0, 23.0))
    assert extents == pytest.approx((174.5, 160.0, 200.0), abs=0.6)
    assert bbox_fill == pytest.approx(0.274, abs=0.01)
    assert profile.parameters["liver_extent_reference_mm_zyx"].source_type == "literature_population"


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
    assert geometry.continuous_parameters["surface_field_kind"] == "analytic_multiscale"


def test_cirrhotic_geometry_has_directionally_correct_proxy_changes() -> None:
    grid = GridSpecV2(shape=(128, 128, 128), voxel_size_mm=4.42)
    normal = fit_liver_geometry(_target("normal"), grid)
    cirrhotic = fit_liver_geometry(_target("cirrhotic"), grid)

    assert cirrhotic.actual_metrics["s1_3_to_s4_8_ratio"] > normal.actual_metrics["s1_3_to_s4_8_ratio"]
    assert cirrhotic.actual_metrics["caudate_fraction"] > normal.actual_metrics["caudate_fraction"]
    assert cirrhotic.actual_metrics["surface_roughness"] > normal.actual_metrics["surface_roughness"] + 0.01
    assert cirrhotic.actual_metrics["volume_ml"] == pytest.approx(_target("cirrhotic").volume_ml, rel=0.04)
    assert ndimage.label(cirrhotic.mask)[1] == 1


@pytest.mark.parametrize("morphology", ["normal", "cirrhotic"])
def test_composed_liver_uses_overlapping_rotated_lobes_dome_and_open_fossa(
    morphology: str,
) -> None:
    geometry = fit_liver_geometry(_target(morphology), GridSpecV2())
    primitives = geometry.primitive_masks

    assert {
        "right",
        "left",
        "dome_envelope",
        "dome_removed",
        "fossa_cutout",
        "fossa_removed",
        "caudate",
    } <= set(primitives)
    assert "bridge" not in primitives

    lobe_union = primitives["right"] | primitives["left"]
    overlap_fraction = float((primitives["right"] & primitives["left"]).sum() / lobe_union.sum())
    assert overlap_fraction >= 0.05
    assert primitives["dome_removed"].any()
    assert primitives["fossa_removed"].any()
    assert not np.any(primitives["fossa_removed"] & geometry.mask)
    assert np.any(ndimage.binary_dilation(primitives["fossa_removed"]) & geometry.mask)

    parameters = geometry.continuous_parameters
    assert abs(float(parameters["right_rotation_xz_deg"])) >= 5.0
    assert abs(float(parameters["left_rotation_xz_deg"])) >= 5.0
    assert parameters["connection_kind"] == "natural_lobe_overlap"
    assert parameters["component_policy"] == "reject_not_keep_largest"
    assert parameters["constructive_source"] == "population_anchored_continuous_csg"
    assert parameters["shape_family"] == "asymmetric_wedge_with_continuous_variation"


@pytest.mark.parametrize("morphology", ["normal", "cirrhotic"])
def test_complete_composed_liver_passes_shape_plausibility_gate(morphology: str) -> None:
    geometry = fit_liver_geometry(_target(morphology), GridSpecV2())

    quality = geometry.actual_metrics["shape_quality"]

    assert quality["status"] == "pass"
    assert all(quality["gates"].values())
    assert quality["lobe_overlap_fraction"] >= 0.05
    assert 0.01 <= quality["fossa_removed_fraction"] <= 0.12
    assert 0.01 <= quality["dome_removed_fraction"] <= 0.35
    assert min(quality["single_component_slice_fraction_zyx"]) >= 0.80
    assert quality["gates"]["no_dumbbell_waist"] is True
    assert quality["central_waist_ratio"] >= 0.55
    assert quality["gates"]["left_lobe_tapers_laterally"] is True
    assert quality["left_lateral_to_medial_area_ratio"] <= 0.65
    assert quality["left_rising_step_fraction"] >= 0.70
    assert quality["left_maximum_rise_fraction"] <= 0.30
    assert quality["gates"]["geometric_left_fraction_tracks_target"] is True
    assert quality["gates"]["caudate_changes_outer_geometry"] is True
    assert quality["gates"]["caudate_outer_is_s1"] is True
    assert quality["caudate_surface_voxels"] >= 8
    outer_only = geometry.primitive_masks["caudate_outer"] & ~(
        geometry.primitive_masks["right"] | geometry.primitive_masks["left"]
    )
    assert np.all(~outer_only | (geometry.region_labels == 1))
    assert quality["caudate_outer_fraction"] <= quality["caudate_outer_fraction_upper"]
    assert all(
        cut["mouth_fraction"] >= 0.05 and cut["mouth_area_mm2_proxy"] >= 50.0
        for cut in quality["cut_mouths"].values()
    )


def test_shape_metrics_reject_a_thin_dumbbell_and_abrupt_left_lobe_step() -> None:
    zz, yy, xx = np.ogrid[:21, :21, :31]
    right = (zz - 10) ** 2 + (yy - 10) ** 2 + (xx - 8) ** 2 <= 25
    left = (zz - 10) ** 2 + (yy - 10) ** 2 + (xx - 22) ** 2 <= 25
    bridge = np.zeros_like(right)
    bridge[10, 10, 13:18] = True
    mask = right | left | bridge

    assert _central_waist_ratio(mask, right | bridge, left | bridge) < 0.55

    abrupt_left = np.zeros((9, 9, 16), dtype=bool)
    abrupt_left[4, 4, :5] = True
    abrupt_left[3:6, 3:6, 5:] = True
    taper_ratio, rise_fraction, maximum_rise, peak_position = _left_lobe_taper(abrupt_left)

    assert taper_ratio <= 0.65
    assert rise_fraction >= 0.70
    assert peak_position >= 0.30
    assert maximum_rise > 0.30


def test_shape_family_varies_continuously_without_copying_the_frozen_v1_template() -> None:
    grid = GridSpecV2()
    targets = [
        replace(_target(), centroid_mm=(-45.0, 15.0, 35.0 + shift))
        for shift in (-8.0, 0.0, 8.0)
    ]

    geometries = [fit_liver_geometry(target, grid) for target in targets]
    coordinates = [
        float(geometry.continuous_parameters["shape_variation_coordinate"])
        for geometry in geometries
    ]
    left_radii = [
        tuple(geometry.continuous_parameters["left_radii_normalized_zyx"])
        for geometry in geometries
    ]

    assert len(set(round(value, 6) for value in coordinates)) == len(coordinates)
    assert len(set(left_radii)) == len(left_radii)
    assert all(-1.0 <= value <= 1.0 for value in coordinates)
    assert all(
        "legacy" not in str(geometry.continuous_parameters).lower()
        for geometry in geometries
    )


def test_high_left_fraction_mapping_does_not_saturate_at_point_45() -> None:
    targets = [replace(_target("cirrhotic"), left_fraction=value) for value in (0.45, 0.50, 0.55)]
    templates = [_constructive_template(target) for target in targets]

    assert [template.left_response_coordinate for template in templates] == sorted(
        template.left_response_coordinate for template in templates
    )
    assert all(
        later.left_radii[2] > earlier.left_radii[2]
        and later.right_radii[2] < earlier.right_radii[2]
        for earlier, later in zip(templates, templates[1:])
    )


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
    assert not geometry.primitive_masks["caudate_outer"].any()
    assert geometry.actual_metrics["caudate_fraction"] == 0.0
    assert geometry.actual_metrics["shape_quality"]["caudate_outer_fraction"] == 0.0
    assert geometry.continuous_parameters["caudate_enabled"] is False


def test_v2_liver_path_is_exposed_without_changing_v1_generation() -> None:
    adapter = HybridV2Adapter(
        profile_path=REPO_ROOT / "configs" / "population_tare_hcc_nopvi_v2.json",
        evidence_registry_path=REPO_ROOT / "configs" / "evidence_registry_v2.json",
        volume_shape=(128, 128, 128),
        voxel_size_mm=4.42,
        max_shape_attempts=16,
    )
    case = adapter.generate(case_id="case_0001", global_seed=123)
    legacy = PhantomGenerator(PhantomConfig()).generate_one(1, seed=123)

    assert case.patient.case_id == "case_0001"
    assert case.target.morphology == case.patient.liver_morphology
    assert case.geometry.mask.shape == (128, 128, 128)
    assert case.sampling_provenance.accepted_attempt_index >= 1
    assert case.metadata["contracts"]["tumor_generator_v2_imported"] is False
    assert legacy.v2_metadata is None


def test_grid_and_target_validation_fail_before_rasterisation() -> None:
    with pytest.raises(ValueError, match="voxel_size_mm"):
        GridSpecV2(shape=(128, 128, 128), voxel_size_mm=0.0)

    grid = GridSpecV2(shape=(64, 64, 64), voxel_size_mm=4.42)
    with pytest.raises(ValueError, match="volume"):
        fit_liver_geometry(replace(_target(), volume_ml=-1.0), grid)
    with pytest.raises(ValueError, match="fit inside"):
        fit_liver_geometry(replace(_target(), lr_mm=400.0), grid)
