from __future__ import annotations

import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.population_sampler import sample_liver_target, sample_patient  # noqa: E402
from core.schemas_v2 import load_evidence_registry, load_profile  # noqa: E402
from core.tumor_generator_v2 import sample_tumor_case_target  # noqa: E402


@pytest.fixture(scope="module")
def main_profile():
    registry = load_evidence_registry(REPO_ROOT / "configs" / "evidence_registry_v2.json")
    return load_profile(REPO_ROOT / "configs" / "population_tare_hcc_nopvi_v2.json", registry)


def _sample_cases(profile, count: int, seed: int):
    rng = np.random.default_rng(seed)
    result = []
    for index in range(count):
        patient = sample_patient(profile, rng, case_id=f"tumor_stat_{index:05d}")
        liver = sample_liver_target(patient, profile, rng)
        result.append((liver, sample_tumor_case_target(patient, liver, profile, rng)))
    return result


def test_10000_targets_preserve_literature_strata_marginals(main_profile) -> None:
    cases = _sample_cases(main_profile, 10000, 713042)
    count_bins = Counter(target.strata.count_bin for _, target in cases)
    dmax_bins = Counter(target.strata.dmax_bin for _, target in cases)
    lobe_extents = Counter(target.strata.lobe_extent for _, target in cases)

    for name, expected in main_profile.value("tumor_count_bins").items():
        assert count_bins[name] / len(cases) == pytest.approx(expected, abs=0.018)
    for name, expected in main_profile.value("dmax_bins").items():
        assert dmax_bins[name] / len(cases) == pytest.approx(expected, abs=0.018)
    for name, expected in main_profile.value("lobe_distribution").items():
        assert lobe_extents[name] / len(cases) == pytest.approx(expected, abs=0.018)


def test_conditional_sampling_obeys_count_dmax_lobe_and_morphology_contract(main_profile) -> None:
    cases = _sample_cases(main_profile, 2500, 90431)
    for liver, case in cases:
        count = len(case.targets)
        assert count == case.requested_count
        assert count == 1 if case.strata.count_bin == "1" else True
        if case.strata.count_bin == "2-5":
            assert 2 <= count <= 5
        if case.strata.count_bin == ">5":
            assert 6 <= count <= 20
        if case.strata.dmax_bin == "10-<80_mm":
            assert 10.0 <= case.dmax_mm < 80.0
        else:
            assert 80.0 <= case.dmax_mm <= 200.0
        lobes = {target.lobe for target in case.targets}
        assert (len(lobes) == 2) == (case.strata.lobe_extent == "bilobar")
        if count == 1:
            assert case.strata.lobe_extent == "unilobar"
        assert all(0.70 <= value <= 1.0 for target in case.targets for value in target.axis_ratios)
        assert all(
            target.morphology == "lobulated_confluent"
            for target in case.targets
            if target.dmax_mm > 100.0
        )
        assert all(target.dmax_mm <= case.dmax_mm for target in case.targets)

        analytic_ml = sum(
            math.pi
            / 6.0
            * target.dmax_mm**3
            * target.axis_ratios[0]
            * target.axis_ratios[1]
            / 1000.0
            for target in case.targets
        )
        assert analytic_ml / liver.volume_ml <= case.burden_fraction_max


def test_within_bin_rules_are_explicit_engineering_assumptions(main_profile) -> None:
    cases = _sample_cases(main_profile, 1000, 12977)
    multi = [case for _, case in cases if case.strata.count_bin != "1"]
    singles = [case for _, case in cases if case.strata.count_bin == "1"]

    assert multi and singles
    assert all(case.within_bin_assumption for case in multi + singles)
    assert all(
        case.targets[0].evidence_types["count_within_bin"] == "literature_population"
        for case in singles
    )
    assert all(case.evidence_types["count_bin"] == "literature_population" for _, case in cases)
    assert all(case.evidence_types["conditional_geometry"] == "engineering_prior" for _, case in cases)
    assert all(
        target.evidence_types["dmax_bin"] == "literature_population"
        and target.evidence_types["dmax_within_bin"] == "engineering_prior"
        for _, case in cases
        for target in case.targets
    )


def test_tumor_target_sampling_is_exactly_reproducible(main_profile) -> None:
    first = _sample_cases(main_profile, 30, 20260713)
    second = _sample_cases(main_profile, 30, 20260713)
    assert first == second
