import copy

import numpy as np
import pytest

from core.limited_activity import (
    LimitedActivityError,
    build_limited_activity,
    derive_domain_seed,
    verify_limited_activity,
)


LIVER = np.zeros((15, 15, 15), dtype=bool)
LIVER[2:13, 2:13, 2:13] = True
LEFT = LIVER.copy()
LEFT[:, :, 8:] = False
RIGHT = LIVER.copy()
RIGHT[:, :, :8] = False
LEFT_TUMOR = np.zeros_like(LIVER)
LEFT_TUMOR[7, 7, 5] = True
RIGHT_TUMOR = np.zeros_like(LIVER)
RIGHT_TUMOR[7, 7, 10] = True
TUMOR = np.zeros_like(LIVER)
TUMOR[6:9, 6:9, 9:12] = True


def test_bilobar_tumors_force_whole_liver_without_changing_masks():
    """Removing either containment check would select an invalid lobar territory."""
    liver_before = LIVER.copy()
    left_before = LEFT.copy()
    right_before = RIGHT.copy()
    out = build_limited_activity(
        liver_mask=LIVER,
        left_mask=LEFT,
        right_mask=RIGHT,
        tumor_masks=[LEFT_TUMOR, RIGHT_TUMOR],
        tumor_records=[{"effective_diameter_mm": 12.0}, {"effective_diameter_mm": 12.0}],
        activity_seed=1234,
        residual_bg=0.05,
        gradient_gain=0.10,
        total_counts=80_000.0,
    )

    assert out.selected_territory == "whole_liver"
    assert out.contract["coverage_fraction"] == 1.0
    assert out.contract["conditional_weights"] == {"whole_liver": 1.0}
    assert np.array_equal(LIVER, liver_before)
    assert np.array_equal(LEFT, left_before)
    assert np.array_equal(RIGHT, right_before)


def test_activity_seed_domains_do_not_depend_on_tumor_rng():
    """Collapsing domains or indices would couple territory and lesion contrast."""
    assert derive_domain_seed(1234, "territory") != derive_domain_seed(1234, "tnr", 0)
    assert derive_domain_seed(1234, "tnr", 0) != derive_domain_seed(1234, "tnr", 1)


def test_contract_records_adapter_source_sha():
    """Omitting adapter provenance would make persisted activity metadata unverifiable."""
    out = build_limited_activity(
        liver_mask=LIVER,
        left_mask=LEFT,
        right_mask=RIGHT,
        tumor_masks=[],
        activity_seed=1234,
        residual_bg=0.05,
        gradient_gain=0.10,
        total_counts=80_000.0,
    )
    assert len(out.contract["adapter_source_sha256"]) == 64


def build_fixture(*, target_tnrs=None, tumor_masks=None, tumor_fills_territory=False):
    tumors = [TUMOR] if tumor_masks is None else tumor_masks
    if tumor_fills_territory:
        tumors = [LIVER.copy()]
    records = [{"effective_diameter_mm": 12.0} for _ in tumors]
    return build_limited_activity(
        liver_mask=LIVER,
        left_mask=LEFT,
        right_mask=RIGHT,
        tumor_masks=tumors,
        tumor_records=records,
        activity_seed=5678,
        residual_bg=0.05,
        gradient_gain=0.10,
        total_counts=80_000.0,
        target_tnrs=target_tnrs,
    )


def verify_out(out, **overrides):
    """Exercise verifier inputs as persisted arrays and metadata, not its output object."""
    params = {
        "liver_mask": LIVER,
        "left_mask": LEFT,
        "right_mask": RIGHT,
        "tumor_masks": [TUMOR],
        "tumor_records": out.tumor_records,
        "activity": out.activity,
        "perfusion_mask": out.perfusion_mask,
        "selected_territory": out.selected_territory,
        "contract": out.contract,
        "total_counts": 80_000.0,
    }
    params.update(overrides)
    return verify_limited_activity(**params)


@pytest.mark.parametrize("target", [2.0, 3.5, 8.0])
def test_realized_ring_tnr_matches_target(target):
    """Setting lesion values before normalization must preserve each ring ratio."""
    out = build_fixture(target_tnrs=[target])
    actual = out.tumor_records[0]["actual_ring_tnr"]
    assert 2.0 <= actual <= 8.0
    assert abs(actual - target) / target <= 0.02
    verify_out(out)


def test_built_activity_is_c_contiguous_float32():
    """Returning float64 or a strided view breaks the Gate C NPZ activity contract."""
    out = build_fixture()
    assert out.activity.dtype == np.float32
    assert out.activity.flags.c_contiguous


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("candidates", ["right_lobar", "whole_liver", "left_lobar"]),
        ("raw_weights", {"whole_liver": 0.5, "right_lobar": 0.25, "left_lobar": 0.25}),
        ("feasible_candidates", []),
        ("conditional_weights", {"whole_liver": 1.0}),
        ("selected_territory", "left_lobar"),
        ("adapter_source_sha256", "0" * 64),
        ("coverage_fraction", 0.5),
        ("mismatch_challenge", True),
        ("background_ring_definition", "not the required Euclidean ring"),
        ("is_true_negative", True),
    ],
)
def test_verifier_rejects_each_tampered_selection_contract_field(field, replacement):
    """Trusting any selection metadata field would permit a falsified activity contract."""
    out = build_fixture()
    contract = copy.deepcopy(out.contract)
    if field == "selected_territory":
        replacement = next(name for name in ("whole_liver", "right_lobar", "left_lobar") if name != out.selected_territory)
    contract[field] = replacement
    with pytest.raises(LimitedActivityError):
        verify_out(out, contract=contract)


def test_verifier_rejects_contract_total_that_disagrees_with_authoritative_argument():
    """A metadata total differing from the requested frozen total must not be accepted."""
    out = build_fixture()
    contract = {**out.contract, "total_counts": 80_001.0}
    with pytest.raises(LimitedActivityError, match="total"):
        verify_out(out, contract=contract)


def test_verifier_rejects_contract_total_drift_below_a_relative_count_tolerance():
    """A 0.05 metadata drift must not be hidden by a count-scaled tolerance."""
    out = build_fixture()
    contract = {**out.contract, "total_counts": 80_000.05}
    with pytest.raises(LimitedActivityError, match="contract total"):
        verify_out(out, contract=contract, total_counts=80_000.0)


def test_verifier_rejects_representable_five_hundredths_activity_drift():
    """An unrelated persisted-voxel change of about 0.05 must fail total validation."""
    out = build_fixture()
    bad_activity = out.activity.copy()
    voxel = tuple(np.argwhere(out.perfusion_mask & ~TUMOR)[0])
    previous = bad_activity[voxel]
    bad_activity[voxel] = np.float32(previous + np.float32(0.05))
    assert float(bad_activity[voxel] - previous) >= 0.049
    with pytest.raises(LimitedActivityError, match="activity total"):
        verify_out(out, activity=bad_activity)


def test_float32_total_bound_accepts_legitimate_formal_sized_activity():
    """Cast-roundoff validation must still admit a real 128-cubed formal-shape output."""
    shape = (128, 128, 128)
    liver = np.zeros(shape, dtype=bool)
    liver[16:112, 16:112, 16:112] = True
    left = liver.copy()
    left[:, :, 64:] = False
    right = liver.copy()
    right[:, :, :64] = False
    tumor = np.zeros(shape, dtype=bool)
    tumor[60:64, 60:64, 80:84] = True
    out = build_limited_activity(
        liver_mask=liver,
        left_mask=left,
        right_mask=right,
        tumor_masks=[tumor],
        tumor_records=[{"effective_diameter_mm": 12.0}],
        activity_seed=2468,
        residual_bg=0.05,
        gradient_gain=0.10,
        total_counts=80_000.0,
        target_tnrs=[3.5],
    )
    assert abs(float(out.activity.astype(np.float64).sum()) - 80_000.0) < 0.05


def test_verifier_rejects_wrong_authoritative_total_even_when_metadata_matches_it():
    """An authoritative call argument cannot be rewritten by matching metadata alone."""
    out = build_fixture()
    contract = {**out.contract, "total_counts": 80_001.0}
    with pytest.raises(LimitedActivityError, match="total"):
        verify_out(out, contract=contract, total_counts=80_001.0)


def test_positive_requires_effective_diameter_field():
    """Allowing a positive record without the frozen field loses its scope evidence."""
    with pytest.raises(LimitedActivityError, match="effective_diameter_mm"):
        build_limited_activity(
            liver_mask=LIVER,
            left_mask=LEFT,
            right_mask=RIGHT,
            tumor_masks=[TUMOR],
            tumor_records=[{}],
            activity_seed=5678,
            residual_bg=0.05,
            gradient_gain=0.10,
            total_counts=80_000.0,
        )


def test_verifier_rejects_conflicting_optional_diameter_alias():
    """An alias that contradicts the authoritative diameter invalidates lesion metadata."""
    out = build_fixture()
    records = [dict(out.tumor_records[0], diameter_mm=13.0)]
    with pytest.raises(LimitedActivityError, match="diameter"):
        verify_out(out, tumor_records=records)


@pytest.mark.parametrize(
    ("tumors", "records", "message"),
    [
        ([TUMOR] * 6, [{"effective_diameter_mm": 12.0}] * 6, "1-5"),
        ([np.zeros_like(TUMOR)], [{"effective_diameter_mm": 12.0}], "nonempty"),
        ([TUMOR, TUMOR], [{"effective_diameter_mm": 12.0}] * 2, "overlap"),
    ],
)
def test_build_rejects_invalid_positive_tumor_geometry(tumors, records, message):
    """Accepting unsupported counts, empty masks, or overlap breaks positive-case semantics."""
    with pytest.raises(LimitedActivityError, match=message):
        build_limited_activity(
            liver_mask=LIVER,
            left_mask=LEFT,
            right_mask=RIGHT,
            tumor_masks=tumors,
            tumor_records=records,
            activity_seed=5678,
            residual_bg=0.05,
            gradient_gain=0.10,
            total_counts=80_000.0,
        )


def test_empty_in_territory_ring_fails_without_reseed():
    """Accepting an empty ring would silently manufacture a lesion contrast."""
    with pytest.raises(LimitedActivityError, match="background ring"):
        build_fixture(tumor_fills_territory=True)


def test_true_negative_keeps_nonzero_liver_activity():
    """Dropping background for a zero-tumor case would create a false cold liver."""
    out = build_fixture(tumor_masks=[])
    assert out.contract["coverage_fraction"] == 1.0
    assert float(out.activity[LIVER].sum()) > 0
    assert out.contract["is_true_negative"] is True
    verify_limited_activity(
        liver_mask=LIVER,
        left_mask=LEFT,
        right_mask=RIGHT,
        tumor_masks=[],
        tumor_records=[],
        activity=out.activity,
        perfusion_mask=out.perfusion_mask,
        selected_territory=out.selected_territory,
        contract=out.contract,
        total_counts=80_000.0,
    )


def test_exact_territory_policy_selects_requested_feasible_lobe():
    out = build_limited_activity(
        liver_mask=LIVER,
        left_mask=LEFT,
        right_mask=RIGHT,
        tumor_masks=[RIGHT_TUMOR],
        tumor_records=[{"effective_diameter_mm": 12.0}],
        activity_seed=1234,
        residual_bg=0.05,
        gradient_gain=0.10,
        total_counts=80_000.0,
        territory_policy="right_lobar",
    )
    assert out.selected_territory == "right_lobar"
    assert out.contract["territory_policy"] == "right_lobar"


def test_exact_territory_policy_rejects_an_infeasible_lobe():
    with pytest.raises(LimitedActivityError, match="not feasible"):
        build_limited_activity(
            liver_mask=LIVER,
            left_mask=LEFT,
            right_mask=RIGHT,
            tumor_masks=[LEFT_TUMOR],
            tumor_records=[{"effective_diameter_mm": 12.0}],
            activity_seed=1234,
            residual_bg=0.05,
            gradient_gain=0.10,
            total_counts=80_000.0,
            territory_policy="right_lobar",
        )


def test_verifier_rejects_one_uncovered_tumor_voxel():
    """A verifier that only checks aggregate coverage would miss a perfusion hole."""
    out = build_fixture()
    bad_mask = out.perfusion_mask.copy()
    bad_mask[tuple(np.argwhere(TUMOR)[0])] = False
    with pytest.raises(LimitedActivityError, match="coverage"):
        verify_out(out, perfusion_mask=bad_mask)


def test_verifier_rejects_extra_perfusion_voxel():
    """A verifier that accepts supersets would allow a metadata/array territory mismatch."""
    out = build_fixture()
    bad_mask = out.perfusion_mask.copy()
    extra = np.argwhere(LIVER & ~out.perfusion_mask)[0]
    bad_mask[tuple(extra)] = True
    with pytest.raises(LimitedActivityError, match="coverage"):
        verify_out(out, perfusion_mask=bad_mask)


@pytest.mark.parametrize("factor", [0.5, 4.0])
def test_verifier_rejects_cold_or_hot_ring_tnr(factor):
    """Recomputing TNR from activity catches arrays that disagree with lesion metadata."""
    out = build_fixture(target_tnrs=[3.5])
    bad_activity = out.activity.copy()
    bad_activity[TUMOR] *= factor
    bad_total = float(np.sum(bad_activity, dtype=np.float64))
    bad_contract = {**out.contract, "total_counts": bad_total}
    with pytest.raises(LimitedActivityError, match="ring TNR"):
        verify_out(out, activity=bad_activity, contract=bad_contract, total_counts=bad_total)


@pytest.mark.parametrize("bad_value", [np.nan, np.inf, -1.0])
def test_verifier_rejects_nonfinite_or_negative_activity(bad_value):
    """Skipping numeric validation would permit invalid activity arrays into packaging."""
    out = build_fixture()
    bad_activity = out.activity.copy()
    bad_activity[tuple(np.argwhere(out.perfusion_mask)[0])] = bad_value
    with pytest.raises(LimitedActivityError, match="finite and non-negative"):
        verify_out(out, activity=bad_activity)


def test_verifier_rejects_out_of_scope_diameter():
    """Removing the diameter boundary would admit unsupported formal-scope lesions."""
    out = build_fixture()
    records = [dict(out.tumor_records[0], effective_diameter_mm=9.9)]
    with pytest.raises(LimitedActivityError, match="diameter"):
        verify_out(out, tumor_records=records)


def test_verifier_rejects_tnr_metadata_that_disagrees_with_arrays():
    """Trusting recorded ratios instead of arrays would make metadata tampering invisible."""
    out = build_fixture()
    records = [dict(out.tumor_records[0], actual_ring_tnr=2.0)]
    with pytest.raises(LimitedActivityError, match="metadata"):
        verify_out(out, tumor_records=records)


def test_verifier_rejects_negative_metadata_with_tumors():
    """A negative label alongside tumor arrays violates true-negative semantics."""
    out = build_fixture()
    contract = {**out.contract, "is_true_negative": True}
    with pytest.raises(LimitedActivityError, match="negative semantics"):
        verify_out(out, contract=contract)
