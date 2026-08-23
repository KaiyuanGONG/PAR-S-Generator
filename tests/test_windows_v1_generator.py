import numpy as np

from core.phantom_generator import PhantomGenerator, PreviewOverrides
from core.windows_v1 import WindowsV1Config


def _generator(*, mode="positive_only", positive=1, negative=0, lesions=None, seed=24680):
    config = WindowsV1Config.from_dict(
        {
            "cohort": {
                "mode": mode,
                "positive_cases": positive,
                "negative_cases": negative,
            },
            "lesions": lesions
            or {
                "tumor_count_min": 1,
                "tumor_count_max": 1,
                "territory_policy": "whole_liver",
            },
            "seed": seed,
        }
    )
    return PhantomGenerator(config.to_phantom_config())


def test_windows_v1_positive_discards_upstream_activity_and_applies_limited_authority():
    result = _generator(
        lesions={
            "tumor_count_min": 1,
            "tumor_count_max": 1,
            "tnr_min": 3.0,
            "tnr_max": 4.0,
            "territory_policy": "whole_liver",
        }
    ).generate_one(1, overrides=PreviewOverrides(exact_tumor_count=1, tumor_mode="ellipsoid"))

    limited = result.v2_metadata["limited_activity"]
    assert result.v2_metadata["adapters"]["activity"] == "hybrid_v2_limited_activity_v1_sole_authority"
    assert limited["upstream_activity_and_perfusion"] == "discarded_not_persisted"
    assert limited["selected_territory"] == "whole_liver"
    assert result.perfusion_mode == "whole_liver"
    assert result.activity.dtype == np.float32
    assert result.activity.flags.c_contiguous
    assert np.all(result.activity[~result.liver_mask] == 0)
    assert abs(float(np.sum(result.activity, dtype=np.float64)) - 80_000.0) < 0.05
    assert result.n_tumors == 1
    record = result.tumor_metadata[0]
    assert 3.0 <= record["target_ring_tnr"] <= 4.0
    assert abs(record["actual_ring_tnr"] - record["target_ring_tnr"]) / record["target_ring_tnr"] <= 0.02
    assert not {"perfusion_region", "target_contrast", "tnr_local", "tnr_global"} & set(record)


def test_windows_v1_true_negative_is_generated_directly_with_nonzero_background():
    result = _generator(
        mode="true_negative_only",
        positive=0,
        negative=1,
        lesions={"territory_policy": "right_lobar"},
    ).generate_one(2, overrides=PreviewOverrides(exact_tumor_count=0))

    limited = result.v2_metadata["limited_activity"]
    assert result.n_tumors == 0
    assert result.tumor_masks == []
    assert result.tumor_metadata == []
    assert limited["contract"]["is_true_negative"] is True
    assert limited["selected_territory"] == "right_lobar"
    assert float(np.sum(result.activity, dtype=np.float64)) > 0


def test_windows_v1_generation_replays_bitwise_for_same_case_and_seed():
    generator = _generator(seed=13579)
    first = generator.generate_one(1, overrides=PreviewOverrides(exact_tumor_count=1))
    second = generator.generate_one(1, overrides=PreviewOverrides(exact_tumor_count=1))

    assert np.array_equal(first.activity, second.activity)
    assert np.array_equal(first.mu_map, second.mu_map)
    assert np.array_equal(first.tumor_masks[0], second.tumor_masks[0])
    assert first.v2_metadata["limited_activity"] == second.v2_metadata["limited_activity"]
