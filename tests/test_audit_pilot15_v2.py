from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from audit_pilot15_v2 import (  # noqa: E402
    Pilot15AuditError,
    _anterior_projection,
    _numeric_summary,
    _outside_dataset,
    _projection_metrics,
    _zyx_to_xyz,
)


def test_projection_metrics_detect_centered_nonnegative_support() -> None:
    projection = np.zeros((60, 128, 128), dtype=np.float32)
    projection[:, 32:96, 32:96] = 1.0
    metrics = _projection_metrics(projection)
    assert metrics["projection_weight_sum"] == pytest.approx(60 * 64 * 64)
    assert metrics["view_sum_cv"] == pytest.approx(0.0)
    assert metrics["view_sum_ratio"] == pytest.approx(1.0)
    assert metrics["outer_8px_count_fraction"] == pytest.approx(0.0)
    assert metrics["minimum_positive_bin_fraction_per_view"] == pytest.approx(0.25)
    assert metrics["detector_centroid_x_range_px"] == pytest.approx([63.5, 63.5])
    assert metrics["detector_centroid_y_range_px"] == pytest.approx([63.5, 63.5])


def test_projection_metrics_reject_invalid_shape_and_negative_bins() -> None:
    with pytest.raises(Pilot15AuditError, match="shape"):
        _projection_metrics(np.ones((3, 4, 5), dtype=np.float32))
    projection = np.ones((60, 128, 128), dtype=np.float32)
    projection[0, 0, 0] = -1.0
    with pytest.raises(Pilot15AuditError, match="negative"):
        _projection_metrics(projection)


def test_output_must_be_outside_immutable_dataset(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    root.mkdir()
    with pytest.raises(Pilot15AuditError, match="outside"):
        _outside_dataset(root / "qa", root, "QA")
    accepted = _outside_dataset(tmp_path / "qa", root, "QA")
    assert accepted == (tmp_path / "qa").resolve()


def test_numeric_summary_is_deterministic() -> None:
    summary = _numeric_summary([1.0, 2.0, 3.0, 4.0])
    assert summary == {
        "min": 1.0,
        "p25": 1.75,
        "median": 2.5,
        "mean": 2.5,
        "p75": 3.25,
        "max": 4.0,
    }


def test_anatomical_coordinate_mapping_is_zyx_to_xyz() -> None:
    points_zyx = np.asarray([[3.0, 2.0, 4.0], [7.0, 6.0, 8.0]])
    assert _zyx_to_xyz(points_zyx).tolist() == [
        [4.0, 2.0, 3.0],
        [8.0, 6.0, 7.0],
    ]


def test_anterior_projection_preserves_si_vertical_and_lr_horizontal() -> None:
    mask = np.zeros((6, 5, 7), dtype=bool)
    mask[3, 2, 4] = True
    projection = _anterior_projection(mask)
    assert projection.shape == (6, 7)
    assert projection[3, 4]
    assert projection.sum() == 1
