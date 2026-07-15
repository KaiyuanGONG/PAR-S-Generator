from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.activity_model_v2 import (  # noqa: E402
    generate_activity_field,
    necrosis_probability_for_diameter,
    sample_activity_target,
)
import core.activity_model_v2 as activity_model_v2  # noqa: E402
from core.liver_geometry import GridSpecV2, LiverGeometryV2  # noqa: E402
from core.measurements import measure_lesions  # noqa: E402
from core.phantom_generator import PhantomConfig, PhantomGenerator  # noqa: E402
from core.schemas_v2 import (  # noqa: E402
    ActivityTargetV2,
    PatientSampleV2,
    TumorTargetV2,
    load_evidence_registry,
    load_profile,
)
from core.tumor_generator_v2 import (  # noqa: E402
    TumorGeometryV2,
    TumorPlacementRecordV2,
)


@pytest.fixture(scope="module")
def profile():
    registry = load_evidence_registry(REPO_ROOT / "configs" / "evidence_registry_v2.json")
    return load_profile(REPO_ROOT / "configs" / "population_tare_hcc_nopvi_v2.json", registry)


def _patient(case_id: str = "activity_fixture") -> PatientSampleV2:
    return PatientSampleV2(
        case_id=case_id,
        sex="male",
        age_years=66.0,
        height_cm=174.0,
        weight_kg=80.0,
        bmi=26.4,
        liver_morphology="cirrhotic",
    )


def _liver(shape: tuple[int, int, int] = (64, 64, 64)) -> tuple[GridSpecV2, LiverGeometryV2]:
    grid = GridSpecV2(shape=shape)
    indices = np.indices(shape, dtype=np.float32)
    center = 0.5 * (np.asarray(shape, dtype=np.float32) - 1.0)
    radii = np.asarray(shape, dtype=np.float32) * np.array((0.38, 0.34, 0.42))
    normalized = sum(((indices[axis] - center[axis]) / radii[axis]) ** 2 for axis in range(3))
    mask = normalized <= 1.0
    labels = np.zeros(shape, dtype=np.uint8)
    x_left = indices[2] < center[2]
    y_anterior = indices[1] < center[1]
    labels[mask & x_left & y_anterior] = 2
    labels[mask & x_left & ~y_anterior] = 3
    labels[mask & ~x_left & y_anterior] = 4
    labels[mask & ~x_left & ~y_anterior] = 5
    left_fraction = float(np.count_nonzero(mask & x_left) / np.count_nonzero(mask))
    geometry = LiverGeometryV2(
        mask=mask,
        region_labels=labels,
        affine_4x4=grid.affine_4x4,
        primitive_masks={},
        target_metrics={},
        actual_metrics={
            "volume_ml": float(np.count_nonzero(mask) * grid.voxel_volume_ml),
            "left_fraction": left_fraction,
        },
        continuous_parameters={},
        evidence_types={"fixture": "engineering_prior"},
    )
    return grid, geometry


def _tumors(
    liver: LiverGeometryV2,
    grid: GridSpecV2,
    specifications: tuple[tuple[str, float, tuple[int, int, int], int], ...],
) -> TumorGeometryV2:
    instances = np.zeros(grid.shape, dtype=np.uint16)
    placements = []
    indices = np.indices(grid.shape)
    for instance_id, (lobe, dmax_mm, center, radius_voxels) in enumerate(specifications, start=1):
        squared = sum((indices[axis] - center[axis]) ** 2 for axis in range(3))
        lesion = squared <= radius_voxels**2
        assert not np.any(lesion & ~liver.mask)
        assert not np.any(lesion & (instances > 0))
        instances[lesion] = instance_id
        target = TumorTargetV2(
            lesion_id=f"activity_lesion_{instance_id}",
            dmax_mm=dmax_mm,
            axis_ratios=(0.9, 0.9),
            lobe=lobe,
            morphology="lobulated_confluent" if dmax_mm > 100 else "smooth_nodular",
            primitive_count=3 if dmax_mm > 100 else 1,
        )
        placements.append((instance_id, target, center))
    measured = tuple(measure_lesions(instances, grid.affine_4x4))
    metrics_by_id = {metric.instance_id: metric for metric in measured}
    affine = grid.affine_4x4
    records = tuple(
        TumorPlacementRecordV2(
            instance_id=instance_id,
            target=target,
            center_world_mm=tuple(
                float(value)
                for value in np.asarray(center) @ affine[:3, :3].T + affine[:3, 3]
            ),
            metrics=metrics_by_id[instance_id],
            capsule_clearance_mm=5.0,
            complete_containment=True,
            assigned_lobe=target.lobe,
            primitive_count=target.primitive_count,
            primitive_overlap_voxels=1 if target.primitive_count > 1 else 0,
            primitive_overlap_fraction=0.01 if target.primitive_count > 1 else 0.0,
        )
        for instance_id, target, center in placements
    )
    tumor_volume = float(np.count_nonzero(instances) * grid.voxel_volume_ml)
    liver_volume = float(np.count_nonzero(liver.mask) * grid.voxel_volume_ml)
    lobes = {record.assigned_lobe for record in records}
    extent = "bilobar" if len(lobes) > 1 else "unilobar"
    return TumorGeometryV2(
        instance_mask=instances,
        placements=records,
        lesion_metrics=measured,
        tumor_union_volume_ml=tumor_volume,
        liver_volume_ml=liver_volume,
        tumor_to_liver_fraction=tumor_volume / liver_volume,
        requested_lobe_extent=extent,
        realized_lobe_extent=extent,
        target_count=len(records),
        realized_count=len(records),
    )


def _target(
    tumors: TumorGeometryV2,
    *,
    territory: str = "whole_liver",
    sector: int | None = None,
    mismatch: bool = False,
    heterogeneous: bool = True,
    dflt_tnr: float = 2.2,
    pattern: str = "physiologic_heterogeneous",
    stress: bool = False,
) -> ActivityTargetV2:
    ids = tuple(metric.instance_id for metric in tumors.lesion_metrics)
    return ActivityTargetV2(
        injection_territory=territory,
        sector_proxy_label=sector,
        activity_pattern=pattern,
        tnr_mean=dflt_tnr,
        heterogeneous=heterogeneous,
        mismatch_challenge=mismatch,
        lesion_tnr_means={instance_id: dflt_tnr + 0.2 * (instance_id - 1) for instance_id in ids},
        lesion_heterogeneous={instance_id: heterogeneous for instance_id in ids},
        evidence_types={
            "tnr_mean": "literature_population",
            "heterogeneous": "literature_population",
            "injection_territory": "coverage_sampling",
            "activity_pattern": "stress_test" if stress else "engineering_prior",
        },
    )


def test_activity_field_measures_tnr_and_probability_from_actual_voxels(profile) -> None:
    grid, liver = _liver()
    tumors = _tumors(
        liver,
        grid,
        (("right", 36.0, (32, 28, 43), 4), ("left", 28.0, (32, 36, 21), 3)),
    )
    target = _target(tumors, heterogeneous=False, dflt_tnr=2.0)

    field = generate_activity_field(_patient(), liver, tumors, target, profile, np.random.default_rng(11))

    assert field.activity_relative.dtype == np.float32
    assert field.activity_probability.dtype == np.float32
    assert field.activity_probability.sum(dtype=np.float64) == pytest.approx(1.0, abs=2e-7)
    assert np.all(field.activity_relative[~liver.mask] == 0.0)
    assert field.injection_tumor_coverage_fraction == 1.0
    for metric in field.lesion_metrics:
        assert metric.actual_tnr_mean == pytest.approx(metric.target_tnr_mean, rel=1e-5)
        assert metric.actual_tnr_max >= metric.actual_tnr_mean
        assert metric.evidence_types["target_tnr_mean"] == "literature_population"


def test_injection_territory_and_activity_pattern_are_distinct_concepts(profile) -> None:
    grid, liver = _liver()
    tumors = _tumors(liver, grid, (("right", 40.0, (32, 28, 42), 4),))

    invalid = _target(tumors, territory="tumor_dominant_low_background")
    with pytest.raises(ValueError, match="injection_territory"):
        generate_activity_field(_patient(), liver, tumors, invalid, profile, np.random.default_rng(2))

    challenge = _target(
        tumors,
        pattern="tumor_dominant_low_background",
        stress=True,
    )
    field = generate_activity_field(
        _patient(), liver, tumors, challenge, profile, np.random.default_rng(3)
    )
    assert field.injection_territory == "whole_liver"
    assert field.activity_pattern == "tumor_dominant_low_background"


def test_incomplete_territory_requires_explicit_mismatch_and_records_coverage(profile) -> None:
    grid, liver = _liver()
    tumors = _tumors(
        liver,
        grid,
        (("right", 32.0, (32, 28, 43), 3), ("left", 32.0, (32, 36, 21), 3)),
    )
    with pytest.raises(ValueError, match="mismatch_challenge"):
        generate_activity_field(
            _patient(),
            liver,
            tumors,
            _target(tumors, territory="right_lobar", mismatch=False),
            profile,
            np.random.default_rng(4),
        )

    field = generate_activity_field(
        _patient(),
        liver,
        tumors,
        _target(tumors, territory="right_lobar", mismatch=True),
        profile,
        np.random.default_rng(4),
    )
    assert field.mismatch_challenge
    assert 0.0 < field.injection_tumor_coverage_fraction < 1.0
    assert any(metric.coverage_fraction < 1.0 for metric in field.lesion_metrics)

    sampled_challenge = sample_activity_target(
        _patient(),
        liver,
        tumors,
        profile,
        np.random.default_rng(41),
        injection_territory="right_lobar",
        mismatch_challenge=True,
        activity_pattern="tumor_dominant_low_background",
    )
    assert sampled_challenge.evidence_types["activity_pattern"] == "stress_test"


def test_necrosis_mapping_is_size_increasing_and_core_is_colder(profile) -> None:
    assert necrosis_probability_for_diameter(120.0, profile) > necrosis_probability_for_diameter(
        20.0, profile
    )
    grid, liver = _liver()
    tumors = _tumors(liver, grid, (("right", 120.0, (32, 32, 36), 12),))
    target = _target(tumors, heterogeneous=True, dflt_tnr=2.5)
    generated = None
    for seed in range(10):
        candidate = generate_activity_field(
            _patient(), liver, tumors, target, profile, np.random.default_rng(seed)
        )
        if candidate.lesion_metrics[0].necrotic:
            generated = candidate
            break
    assert generated is not None
    metric = generated.lesion_metrics[0]
    assert 0.0 < metric.necrotic_fraction < 0.5
    assert metric.necrotic_core_mean < metric.viable_rim_mean


def test_heterogeneity_exponentiates_only_inside_lesion_support(
    profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    grid, liver = _liver()
    tumors = _tumors(liver, grid, (("right", 36.0, (32, 28, 43), 4),))

    def extreme_padding(
        shape: tuple[int, int, int],
        support: np.ndarray,
        _sigma_voxels: float,
        _rng: np.random.Generator,
    ) -> np.ndarray:
        values = np.zeros(shape, dtype=np.float32)
        values[~support] = 1_000.0
        return values

    monkeypatch.setattr(
        activity_model_v2,
        "_standardized_low_frequency_field",
        extreme_padding,
    )
    with np.errstate(over="raise"):
        field = generate_activity_field(
            _patient(),
            liver,
            tumors,
            _target(tumors, heterogeneous=True),
            profile,
            np.random.default_rng(17),
        )
    assert np.isfinite(field.activity_relative).all()


def test_background_exponentiates_only_inside_liver_support(
    profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    grid, liver = _liver()
    tumors = _tumors(liver, grid, (("right", 36.0, (32, 28, 43), 4),))

    def extreme_padding(
        shape: tuple[int, int, int],
        support: np.ndarray,
        _sigma_voxels: float,
        _rng: np.random.Generator,
    ) -> np.ndarray:
        values = np.zeros(shape, dtype=np.float32)
        values[~support] = 2_000.0
        return values

    monkeypatch.setattr(
        activity_model_v2,
        "_standardized_low_frequency_field",
        extreme_padding,
    )
    with np.errstate(over="raise"):
        field = generate_activity_field(
            _patient(),
            liver,
            tumors,
            _target(tumors, heterogeneous=False),
            profile,
            np.random.default_rng(23),
        )
    assert np.isfinite(field.activity_relative).all()


def test_target_sampling_preserves_lesion_level_evidence_and_unknown_correlation(profile) -> None:
    grid, liver = _liver((32, 32, 32))
    tumors = _tumors(liver, grid, (("right", 28.0, (16, 15, 21), 2),))
    rng = np.random.default_rng(20260714)
    targets = [
        sample_activity_target(_patient(), liver, tumors, profile, rng)
        for _ in range(2500)
    ]
    tnr = np.array([target.lesion_tnr_means[1] for target in targets])
    heterogeneous = np.array([target.lesion_heterogeneous[1] for target in targets])

    assert tnr.min() >= profile.value("tnr_mean_range")[0]
    assert tnr.max() <= profile.value("tnr_mean_range")[1]
    assert tnr.mean() == pytest.approx(profile.value("tnr_mean_reference"), rel=0.08)
    assert heterogeneous.mean() == pytest.approx(profile.value("heterogeneous_fraction"), abs=0.035)
    assert all(target.evidence_types["tnr_mean"] == "literature_population" for target in targets)
    assert all(
        target.evidence_types["injection_territory"] == "coverage_sampling"
        for target in targets
    )
    assert all("unknown_in_literature" in target.within_patient_correlation_assumption for target in targets)


def test_phantom_generator_v2_activity_adapter_does_not_touch_v1(profile) -> None:
    grid, liver = _liver()
    tumors = _tumors(liver, grid, (("right", 36.0, (32, 28, 43), 4),))
    generator = PhantomGenerator(PhantomConfig())
    result = generator.generate_activity_v2(
        _patient(),
        liver,
        tumors,
        profile,
        np.random.default_rng(91),
        target=_target(tumors, heterogeneous=False),
    )
    assert result.field.status == "pass"
    assert result.tumors is tumors
