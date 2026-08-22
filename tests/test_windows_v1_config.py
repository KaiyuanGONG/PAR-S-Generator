import math

import pytest

from core.windows_v1 import (
    GENERATION_PROFILE,
    MAX_SAFE_INTEGER,
    WindowsV1Config,
    WindowsV1ConfigError,
)
from pipeline.runner import PipelineConfig


def test_default_profile_compiles_only_locked_hybrid_v2_contract():
    config = WindowsV1Config.from_dict(
        {
            "schema_version": "windows_v1",
            "generation_profile": GENERATION_PROFILE,
            "cohort": {"mode": "positive_only", "positive_cases": 2, "negative_cases": 0},
        }
    )

    phantom = config.to_phantom_config()

    assert phantom.n_cases == 2
    assert phantom.anatomy_model == "v2_population"
    assert phantom.activity_model == "limited_v1"
    assert phantom.volume_shape == (128, 128, 128)
    assert phantom.voxel_size_mm == 4.42
    assert phantom.total_counts == 80_000.0
    assert phantom.residual_bg == 0.05
    assert phantom.gradient_gain == 0.08
    assert phantom.tumor_count_min == 1
    assert phantom.tumor_count_max == 5
    assert phantom.tumor_size_bins_mm == [[10.0, 20.0], [20.0, 40.0], [40.0, 60.0]]
    assert phantom.tumor_probs == [0.45, 0.40, 0.15]


def test_windows_v1_is_expectation_only_and_rejects_legacy_observation_output():
    windows = WindowsV1Config.from_dict({})
    config = PipelineConfig.for_windows_v1(run_id="expectation-only", windows_v1=windows)
    assert config.create_poisson_observation is False
    assert config.observation_policy == "fixed_scale"

    with pytest.raises(ValueError, match="offline observation"):
        PipelineConfig.for_windows_v1(
            run_id="invalid-observation",
            windows_v1=windows,
            create_poisson_observation=True,
            observation_policy="empirical_total_counts",
            observation_protocol_status="empirical_protocol_matching",
        )


@pytest.mark.parametrize(
    ("cohort", "roles"),
    [
        ({"mode": "positive_only", "positive_cases": 3, "negative_cases": 0}, ["positive"] * 3),
        ({"mode": "true_negative_only", "positive_cases": 0, "negative_cases": 2}, ["true_negative"] * 2),
        (
            {"mode": "mixed", "positive_cases": 2, "negative_cases": 2},
            ["positive", "positive", "true_negative", "true_negative"],
        ),
    ],
)
def test_cohort_modes_materialize_explicit_case_roles(cohort, roles):
    config = WindowsV1Config.from_dict({"cohort": cohort})
    assert config.case_roles() == roles


def test_generation_case_count_has_no_product_cap():
    config = WindowsV1Config.from_dict(
        {"cohort": {"mode": "positive_only", "positive_cases": 1_000_001, "negative_cases": 0}}
    )
    assert config.total_cases == 1_000_001


@pytest.mark.parametrize(
    "cohort",
    [
        {"mode": "positive_only", "positive_cases": 0, "negative_cases": 0},
        {"mode": "positive_only", "positive_cases": 1, "negative_cases": 1},
        {"mode": "true_negative_only", "positive_cases": 1, "negative_cases": 1},
        {"mode": "mixed", "positive_cases": 1, "negative_cases": 0},
    ],
)
def test_invalid_cohort_combinations_fail_closed(cohort):
    with pytest.raises(WindowsV1ConfigError, match="cohort"):
        WindowsV1Config.from_dict({"cohort": cohort})


def test_size_weights_are_normalized_and_both_forms_are_serialized():
    config = WindowsV1Config.from_dict(
        {"lesions": {"size_band_weights": [2.0, 1.0, 1.0]}}
    )
    assert config.lesions.normalized_size_band_weights == (0.5, 0.25, 0.25)
    payload = config.to_dict()
    assert payload["lesions"]["size_band_weights"] == [2.0, 1.0, 1.0]
    assert payload["lesions"]["normalized_size_band_weights"] == [0.5, 0.25, 0.25]


@pytest.mark.parametrize(
    "lesions",
    [
        {"tumor_count_min": 0},
        {"tumor_count_max": 6},
        {"tumor_count_min": 4, "tumor_count_max": 3},
        {"size_band_weights": [0.0, 0.0, 0.0]},
        {"size_band_weights": [0.5, -0.1, 0.6]},
        {"tnr_min": 1.99},
        {"tnr_max": 8.01},
        {"tnr_min": 7.0, "tnr_max": 3.0},
        {"tnr_min": math.nan},
        {"territory_policy": "sector_proxy"},
    ],
)
def test_unsupported_lesion_controls_are_rejected(lesions):
    with pytest.raises(WindowsV1ConfigError):
        WindowsV1Config.from_dict({"lesions": lesions})


@pytest.mark.parametrize("seed", [-1, MAX_SAFE_INTEGER + 1, True])
def test_seed_must_be_a_json_safe_nonnegative_integer(seed):
    with pytest.raises(WindowsV1ConfigError, match="seed"):
        WindowsV1Config.from_dict({"seed": seed})


@pytest.mark.parametrize(
    "payload",
    [
        {"unexpected": 1},
        {"cohort": {"mode": "positive_only", "positive_cases": 1, "negative_cases": 0, "extra": 1}},
        {"lesions": {"extra": 1}},
        {"schema_version": "legacy"},
        {"generation_profile": "legacy_master"},
        {"runtime_backend": "linux"},
    ],
)
def test_unknown_or_non_authoritative_profile_fields_are_rejected(payload):
    with pytest.raises(WindowsV1ConfigError):
        WindowsV1Config.from_dict(payload)
