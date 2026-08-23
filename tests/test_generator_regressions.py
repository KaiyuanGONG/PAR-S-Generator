import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.phantom_generator import (
    Geometry3D,
    PhantomConfig,
    PhantomGenerator,
    PreviewOverrides,
)


def test_superellipsoid_respects_requested_axes():
    mask = Geometry3D.create_superellipsoid(
        (64, 64, 64), (32, 32, 32), radius_vox=5.0, p=2.0, elong=1.2
    )
    coords = np.argwhere(mask)
    spans = coords.max(axis=0) - coords.min(axis=0) + 1

    assert spans[1] <= 11
    assert spans[2] <= 11
    assert spans[0] <= 13
    assert spans[1] >= 9
    assert spans[2] >= 9


def test_central_lesions_are_unclipped_nonoverlapping_and_measured():
    cfg = PhantomConfig(
        global_seed=101,
        tumor_count_min=3,
        tumor_count_max=3,
        tumor_mode_policy="ellipsoid",
        subcapsular_fraction=0.0,
        tumor_min_liver_margin_mm=4.42,
    )
    result = PhantomGenerator(cfg).generate_one(1)

    assert result.n_tumors == 3
    assert len(result.tumor_metadata) == 3
    assert len(result.tumor_diameters_mm) == 3
    assert len(result.tumor_nominal_diameters_mm) == 3
    for index, mask in enumerate(result.tumor_masks):
        assert not np.any(mask & ~result.liver_mask)
        assert result.tumor_metadata[index]["effective_diameter_mm"] == result.tumor_diameters_mm[index]
        assert result.tumor_metadata[index]["surface_margin_mm"] >= 4.42 - 1e-6
        assert result.tumor_metadata[index]["placement_stratum"] == "central"
        for other in result.tumor_masks[index + 1 :]:
            assert not np.any(mask & other)


def test_subcapsular_is_explicit_without_clipping():
    cfg = PhantomConfig(
        global_seed=211,
        tumor_count_min=1,
        tumor_count_max=1,
        tumor_mode_policy="ellipsoid",
        subcapsular_fraction=1.0,
    )
    result = PhantomGenerator(cfg).generate_one(1)

    assert result.n_tumors == 1
    assert result.tumor_metadata[0]["placement_stratum"] == "subcapsular"
    assert not np.any(result.tumor_masks[0] & ~result.liver_mask)


def test_cantlie_solver_records_convergence():
    cfg = PhantomConfig(tumor_count_min=0, tumor_count_max=0)
    results = [PhantomGenerator(cfg).generate_one(case_id, seed=300 + case_id) for case_id in range(1, 4)]

    for result in results:
        assert result.cantlie_converged
        assert result.cantlie_abs_error <= cfg.cantlie_tolerance
        assert abs(result.left_ratio - cfg.target_left_ratio) <= cfg.cantlie_tolerance
        assert "initial_offset_range" in result.cantlie_search_evidence
        assert "expanded_offset_range" in result.cantlie_search_evidence
        assert isinstance(result.cantlie_search_evidence["hit_expansion_limit"], bool)


def test_saved_metadata_separates_nominal_and_measured_diameter(tmp_path):
    cfg = PhantomConfig(global_seed=412, tumor_count_min=1, tumor_count_max=1)
    result = PhantomGenerator(cfg).generate_one(
        1,
        overrides=PreviewOverrides(exact_tumor_count=1, tumor_mode="ellipsoid"),
    )
    result.save(tmp_path)

    payload = json.loads((tmp_path / "case_0001_meta.json").read_text(encoding="utf-8"))
    assert payload["tumor_diameters_mm"] == result.tumor_diameters_mm
    assert payload["tumor_nominal_diameters_mm"] == result.tumor_nominal_diameters_mm
    assert payload["tumors"][0]["effective_diameter_mm"] == result.tumor_diameters_mm[0]
    sampled_low, sampled_high = payload["tumors"][0]["sampled_size_bin_mm"]
    assert sampled_low <= result.tumor_diameters_mm[0] <= sampled_high
    assert payload["cantlie"]["converged"] is True
    assert "hit_expansion_limit" in payload["cantlie"]["search"]
    assert payload["attenuation_contract"]["status"] == (
        "verified_type7_mu_times_voxel_v10_current_h2o_protocol"
    )


def test_multilesion_layout_keeps_presampled_size_strata():
    """Regression for the former last-lesion trap and small-lesion bias."""
    cfg = PhantomConfig()
    result = PhantomGenerator(cfg).generate_one(case_id=4, seed=46)

    assert result.n_tumors == 5
    sampled = [row["sampled_size_bin_mm"] for row in result.tumor_metadata]
    measured = [row["effective_diameter_mm"] for row in result.tumor_metadata]
    assert sampled.count([40.0, 60.0]) == 1
    assert sampled.count([20.0, 40.0]) == 3
    assert sampled.count([10.0, 20.0]) == 1
    for bounds, diameter in zip(sampled, measured):
        assert bounds[0] <= diameter <= bounds[1]
