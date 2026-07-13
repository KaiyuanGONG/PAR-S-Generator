from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.population_sampler import sample_liver_target, sample_patient  # noqa: E402
from core.schemas_v2 import load_evidence_registry, load_profile  # noqa: E402


@pytest.fixture(scope="module")
def main_profile():
    registry = load_evidence_registry(REPO_ROOT / "configs" / "evidence_registry_v2.json")
    return load_profile(REPO_ROOT / "configs" / "population_tare_hcc_nopvi_v2.json", registry)


def test_patient_and_liver_sampling_are_exactly_reproducible(main_profile) -> None:
    first_rng = np.random.default_rng(20260713)
    second_rng = np.random.default_rng(20260713)

    first = []
    second = []
    for index in range(12):
        patient_a = sample_patient(main_profile, first_rng, case_id=f"case_{index:04d}")
        target_a = sample_liver_target(patient_a, main_profile, first_rng)
        first.append((patient_a, target_a))

        patient_b = sample_patient(main_profile, second_rng, case_id=f"case_{index:04d}")
        target_b = sample_liver_target(patient_b, main_profile, second_rng)
        second.append((patient_b, target_b))

    assert first == second


def test_5000_samples_follow_profile_marginals_and_joint_structure(main_profile) -> None:
    rng = np.random.default_rng(41023)
    patients = []
    targets = []
    for index in range(5000):
        patient = sample_patient(main_profile, rng, case_id=f"stat_{index:05d}")
        patients.append(patient)
        targets.append(sample_liver_target(patient, main_profile, rng))

    male_fraction = np.mean([patient.sex == "male" for patient in patients])
    cirrhotic_fraction = np.mean([patient.liver_morphology == "cirrhotic" for patient in patients])
    heights = np.array([patient.height_cm for patient in patients])
    weights = np.array([patient.weight_kg for patient in patients])
    volumes = np.array([target.volume_ml for target in targets])

    assert male_fraction == pytest.approx(main_profile.value("male_fraction_auxiliary"), abs=0.025)
    assert cirrhotic_fraction == pytest.approx(main_profile.value("cirrhosis_prevalence"), abs=0.025)
    assert np.corrcoef(heights, weights)[0, 1] > 0.45
    assert np.corrcoef(weights, volumes)[0, 1] > 0.30

    reference = main_profile.value("liver_volume_reference_ml")
    assert volumes.mean() == pytest.approx(reference["mean"], rel=0.04)
    assert volumes.std(ddof=0) == pytest.approx(reference["sd"], rel=0.18)


def test_left_fraction_varies_and_cirrhosis_changes_segment_targets(main_profile) -> None:
    rng = np.random.default_rng(9981)
    targets = []
    for index in range(3000):
        patient = sample_patient(main_profile, rng, case_id=f"left_{index:05d}")
        targets.append(sample_liver_target(patient, main_profile, rng))

    normal = [target for target in targets if target.morphology == "normal"]
    cirrhotic = [target for target in targets if target.morphology == "cirrhotic"]
    reference = main_profile.value("left_liver_fraction_reference")

    normal_left = np.array([target.left_fraction for target in normal])
    assert normal_left.std(ddof=0) > 0.035
    assert normal_left.min() >= reference["range"][0]
    assert normal_left.max() <= reference["range"][1]
    assert np.median(normal_left) == pytest.approx(reference["median"], abs=0.02)

    assert np.mean([target.left_fraction for target in cirrhotic]) > normal_left.mean()
    assert np.mean([target.s1_3_to_s4_8_ratio for target in cirrhotic]) > np.mean(
        [target.s1_3_to_s4_8_ratio for target in normal]
    )
    assert np.mean([target.caudate_fraction for target in cirrhotic]) > np.mean(
        [target.caudate_fraction for target in normal]
    )


def test_volume_model_is_not_the_banned_upper_limit_equation_and_keeps_evidence_types(main_profile) -> None:
    rng = np.random.default_rng(7331)
    patients = []
    targets = []
    for index in range(1000):
        patient = sample_patient(main_profile, rng, case_id=f"model_{index:04d}")
        patients.append(patient)
        targets.append(sample_liver_target(patient, main_profile, rng))

    weights = np.array([patient.weight_kg for patient in patients])
    volumes = np.array([target.volume_ml for target in targets])
    banned_upper_limit = 14.0 * weights + 979.0
    slope, intercept = np.polyfit(weights, volumes, 1)

    assert not np.allclose(volumes, banned_upper_limit, rtol=0.0, atol=1e-6)
    assert abs(slope - 14.0) > 1.0 or abs(intercept - 979.0) > 100.0
    assert patients[0].evidence_types["sex"] == main_profile.parameters["male_fraction_auxiliary"].source_type
    assert patients[0].evidence_types["joint_sampling_model"] == "engineering_prior"
    assert targets[0].evidence_types["volume_reference"] == main_profile.parameters[
        "liver_volume_reference_ml"
    ].source_type
    assert targets[0].evidence_types["volume_model"] == "engineering_prior"

