from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import core.tumor_generator_v2 as tumor_module  # noqa: E402
from core.liver_geometry import GridSpecV2, LiverGeometryV2  # noqa: E402
from core.measurements import measure_liver  # noqa: E402
from core.phantom_generator import PhantomConfig, PhantomGenerator  # noqa: E402
from core.schemas_v2 import PatientSampleV2, TumorTargetV2  # noqa: E402
from core.tumor_generator_v2 import (  # noqa: E402
    TumorCaseTargetV2,
    TumorPlacementRejectedError,
    TumorRejectionV2,
    TumorStrataV2,
    place_and_rasterize_tumors,
)


def _synthetic_liver(grid: GridSpecV2 | None = None) -> LiverGeometryV2:
    grid = grid or GridSpecV2()
    affine = grid.affine_4x4
    indices = np.indices(grid.shape, dtype=np.float64)
    world = np.moveaxis(indices, 0, -1) @ affine[:3, :3].T + affine[:3, 3]
    mask = (
        (world[..., 0] / 92.0) ** 2
        + (world[..., 1] / 72.0) ** 2
        + (world[..., 2] / 108.0) ** 2
        <= 1.0
    )
    labels = np.zeros(grid.shape, dtype=np.uint8)
    labels[mask & (world[..., 2] < 0.0)] = 2
    labels[mask & (world[..., 2] >= 0.0)] = 4
    measured = measure_liver(mask, affine)
    actual = measured.__dict__ | {"left_fraction": float(np.mean(world[mask, 2] < 0.0))}
    return LiverGeometryV2(
        mask=mask,
        region_labels=labels,
        affine_4x4=affine,
        primitive_masks={},
        target_metrics={},
        actual_metrics=actual,
        continuous_parameters={},
        evidence_types={"fixture": "engineering_prior"},
    )


def _tumor(
    lesion_id: str,
    lobe: str,
    dmax_mm: float = 32.0,
    *,
    morphology: str = "smooth_nodular",
    subcapsular: bool = False,
) -> TumorTargetV2:
    return TumorTargetV2(
        lesion_id=lesion_id,
        dmax_mm=dmax_mm,
        axis_ratios=(0.82, 0.90),
        lobe=lobe,
        morphology=morphology,
        orientation_deg_zyx=(15.0, -11.0, 27.0),
        subcapsular=subcapsular,
        primitive_count=3 if morphology == "lobulated_confluent" else 1,
        count_bin="2-5",
        dmax_bin="10-<80_mm",
    )


def _case(*targets: TumorTargetV2, attempts: int = 96, burden: float = 0.70) -> TumorCaseTargetV2:
    lobes = {target.lobe for target in targets}
    return TumorCaseTargetV2(
        case_id="placement_fixture",
        strata=TumorStrataV2(
            "1" if len(targets) == 1 else "2-5",
            "10-<80_mm" if max(target.dmax_mm for target in targets) < 80 else "80-200_mm",
            "bilobar" if len(lobes) > 1 else "unilobar",
        ),
        targets=tuple(targets),
        burden_fraction_max=burden,
        dmax_tolerance_voxels=0.75,
        placement_attempts_per_lesion=attempts,
        instance_gap_mm=0.0,
        subcapsular_clearance_max_mm=5.0,
        sampling_attempts=1,
    )


def test_bilobar_instances_are_fully_contained_nonoverlapping_and_measured() -> None:
    grid = GridSpecV2()
    liver = _synthetic_liver(grid)
    case = _case(_tumor("right_1", "right", 36.0), _tumor("left_1", "left", 28.0))

    geometry = place_and_rasterize_tumors(case, liver, grid)

    assert geometry.target_count == geometry.realized_count == 2
    assert geometry.requested_lobe_extent == geometry.realized_lobe_extent == "bilobar"
    assert set(np.unique(geometry.instance_mask)) == {0, 1, 2}
    assert not np.any((geometry.instance_mask > 0) & ~liver.mask)
    assert all(record.complete_containment for record in geometry.placements)
    for record in geometry.placements:
        center_index = np.rint(
            np.linalg.solve(
                grid.affine_4x4[:3, :3],
                np.asarray(record.center_world_mm) - grid.affine_4x4[:3, 3],
            )
        ).astype(int)
        center_label = int(liver.region_labels[tuple(center_index)])
        assert center_label in ((1, 2, 3) if record.assigned_lobe == "left" else (4, 5))
        tolerance = max(0.75 * grid.voxel_size_mm, 0.03 * record.target.dmax_mm)
        assert record.metrics.recist_3d_mm == pytest.approx(record.target.dmax_mm, abs=tolerance)
    assert 0.0 < geometry.tumor_to_liver_fraction <= 0.70


def test_subcapsular_placement_uses_a_clearance_gate_not_mask_cropping() -> None:
    grid = GridSpecV2()
    liver = _synthetic_liver(grid)
    target = _tumor("subcapsular", "right", 24.0, subcapsular=True)

    geometry = place_and_rasterize_tumors(_case(target, attempts=192), liver, grid)

    record = geometry.placements[0]
    assert record.capsule_clearance_mm <= 5.0
    assert record.complete_containment
    assert np.count_nonzero(geometry.instance_mask) == record.metrics.voxel_count


def test_overlapping_confluent_primitives_form_one_instance_label() -> None:
    grid = GridSpecV2()
    liver = _synthetic_liver(grid)
    target = _tumor("confluent", "right", 56.0, morphology="lobulated_confluent")

    geometry = place_and_rasterize_tumors(_case(target), liver, grid)

    assert set(np.unique(geometry.instance_mask)) == {0, 1}
    assert geometry.realized_count == 1
    assert geometry.placements[0].primitive_count == 3
    assert geometry.placements[0].primitive_overlap_voxels > 0


def test_impossible_or_excessive_burden_is_structurally_rejected() -> None:
    grid = GridSpecV2()
    liver = _synthetic_liver(grid)
    target = _tumor("burden", "right", 60.0)
    with pytest.raises(TumorPlacementRejectedError) as error:
        place_and_rasterize_tumors(_case(target, attempts=4, burden=0.0001), liver, grid)
    assert error.value.rejection.reason_code == "placement_attempts_exhausted"
    assert error.value.rejection.details["reason_counts"]["tumor_burden"] > 0


def test_production_retry_keeps_strata_and_records_rejection(monkeypatch) -> None:
    grid = GridSpecV2()
    liver = _synthetic_liver(grid)
    patient = PatientSampleV2(
        case_id="retry_case",
        sex="male",
        age_years=66.0,
        height_cm=174.0,
        weight_kg=80.0,
        bmi=26.4,
        liver_morphology="normal",
    )
    target = _case(_tumor("retry_lesion", "right", 28.0))
    fixed_seen = []
    placement_calls = 0

    def fake_sample(patient_arg, liver_arg, profile_arg, rng_arg, *, fixed_strata=None, **kwargs):
        fixed_seen.append(fixed_strata)
        return target

    real_place = tumor_module.place_and_rasterize_tumors

    def fail_once(target_arg, liver_arg, grid_arg):
        nonlocal placement_calls
        placement_calls += 1
        if placement_calls == 1:
            raise TumorPlacementRejectedError(
                TumorRejectionV2("placement_attempts_exhausted", "retry_lesion")
            )
        return real_place(target_arg, liver_arg, grid_arg)

    monkeypatch.setattr(tumor_module, "sample_tumor_case_target", fake_sample)
    monkeypatch.setattr(tumor_module, "place_and_rasterize_tumors", fail_once)
    generator = PhantomGenerator(PhantomConfig())
    result = generator.generate_tumors_v2(
        patient,
        liver,
        object(),
        np.random.default_rng(7),
        tumor_seed=12345,
        max_target_attempts=3,
    )

    assert fixed_seen == [None, target.strata]
    assert result.target.strata == target.strata
    assert result.sampling_provenance.accepted_attempt_index == 2
    assert len(result.sampling_provenance.rejected_attempts) == 1
    assert result.sampling_provenance.rejected_attempts[0].reason_code == "placement_attempts_exhausted"
