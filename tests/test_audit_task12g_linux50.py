from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import matplotlib.pyplot as plt
import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from audit_task12g_linux50 import (  # noqa: E402
    Task12GAuditError,
    _case_row,
    _direction_labels,
    _projection_metrics,
    _render_case_board,
    audit_task12g,
    validate_frozen_dataset,
)


def _metadata() -> dict[str, object]:
    return {
        "case_id": "case_00000",
        "split": "val",
        "patient": {
            "sex": "female",
            "age_years": 72.0,
            "bmi": 27.0,
            "liver_morphology": "cirrhotic",
        },
        "actual_metrics": {
            "liver": {
                "volume_ml": 1500.0,
                "extent_mm_zyx": [170.0, 160.0, 220.0],
                "left_fraction": 0.42,
                "s1_3_to_s4_8_ratio": 0.55,
                "surface_roughness": 0.25,
                "sphericity": 0.72,
            },
            "tumors": {
                "realized_count": 2,
                "lobe_extent": "bilobar",
                "tumor_union_fraction_liver": 0.12,
                "tumor_union_fraction_perfused": 0.20,
                "lesions": [
                    {
                        "instance_id": 1,
                        "recist_3d_mm": 20.0,
                        "volume_ml": 2.0,
                        "tnr_mean": 2.0,
                        "tnr_max": 4.0,
                        "necrotic_fraction": 0.0,
                        "morphology": "smooth_nodular",
                        "liver_region_proxy": 2,
                    },
                    {
                        "instance_id": 2,
                        "recist_3d_mm": 80.0,
                        "volume_ml": 100.0,
                        "tnr_mean": 6.0,
                        "tnr_max": 10.0,
                        "necrotic_fraction": 0.3,
                        "morphology": "lobulated_confluent",
                        "liver_region_proxy": 5,
                    },
                ],
            },
        },
        "activity": {
            "activity_pattern": "physiologic_heterogeneous",
            "injection_territory": "right_lobar",
            "injection_tumor_coverage_fraction": 0.6,
            "mismatch_challenge": True,
            "perfused_volume_ml": 900.0,
        },
        "quality_control": {
            "status": "pass",
            "failed_gates": [],
            "complete_tumor_containment": True,
            "liver_shape_quality": {"status": "pass", "gates": {"connected": True}},
            "torso_anatomy": {"passed": True},
        },
        "simulation": {
            "status": "complete",
            "projection_stats": {
                "projection_weight_sum": 96.0,
                "projection_per_view_weight_sum": [16.0, 32.0, 48.0],
            },
        },
    }


def _projection() -> np.ndarray:
    projection = np.zeros((3, 8, 8), dtype=np.float32)
    for view, weight in enumerate((1.0, 2.0, 3.0)):
        projection[view, 2:6, 2:6] = weight
    return projection


def _arrays() -> dict[str, np.ndarray]:
    liver = np.zeros((8, 8, 8), dtype=np.uint8)
    liver[1:7, 1:7, 1:7] = 1
    tumor = np.zeros_like(liver)
    tumor[3:5, 3:5, 3:5] = 1
    instances = tumor.astype(np.uint16)
    activity = tumor.astype(np.float32) * 5.0 + liver.astype(np.float32)
    return {
        "activity_probability": activity / activity.sum(dtype=np.float64),
        "activity_relative": activity,
        "body_mask": liver.copy(),
        "liver_mask": liver,
        "liver_region_proxy": liver.copy(),
        "mu_input_140kev": liver.astype(np.float32) * 0.16,
        "mu_true_140kev": liver.astype(np.float32) * 0.15,
        "perfusion_mask": liver.copy(),
        "simind_source_weights": activity / activity.sum(dtype=np.float64) * 80000.0,
        "tumor_instance_mask": instances,
        "tumor_union_mask": tumor,
    }


def test_projection_metrics_preserve_absolute_total_and_shape_curve() -> None:
    projection = _projection()

    metrics = _projection_metrics(projection, outer_width=1)

    assert metrics["projection_weight_sum"] == pytest.approx(96.0)
    assert metrics["per_view"].tolist() == pytest.approx([16.0, 32.0, 48.0])
    assert metrics["per_view_over_mean"].tolist() == pytest.approx([0.5, 1.0, 1.5])
    assert metrics["view_sum_cv"] == pytest.approx(np.std([16, 32, 48]) / 32)
    assert metrics["view_sum_ratio"] == pytest.approx(3.0)
    assert metrics["minimum_positive_bin_fraction_per_view"] == pytest.approx(0.25)
    assert metrics["outer_8px_count_fraction"] == pytest.approx(0.0)
    assert metrics["detector_centroid_y_range_px"] == pytest.approx([3.5, 3.5])
    assert metrics["detector_centroid_x_range_px"] == pytest.approx([3.5, 3.5])
    assert metrics["sinogram"].shape == (3, 8)


def test_projection_metrics_reject_empty_view() -> None:
    projection = _projection()
    projection[1] = 0.0

    with pytest.raises(Task12GAuditError, match="non-zero weight"):
        _projection_metrics(projection, outer_width=1)


def test_case_row_extracts_tnr_necrosis_and_challenge_semantics() -> None:
    record = SimpleNamespace(
        case_id="case_00000",
        split="val",
        population_weight=0.0,
        sampling_probability=0.0,
    )

    row = _case_row(record, _metadata(), _projection_metrics(_projection(), outer_width=1))

    assert row["dmax_mm"] == 80.0
    assert row["tnr_mean_median"] == pytest.approx(4.0)
    assert row["tnr_max_maximum"] == pytest.approx(10.0)
    assert row["necrotic_fraction_max"] == pytest.approx(0.3)
    assert row["mismatch_semantics"] == "coverage_challenge_not_prevalence"
    assert len(row["lesions"]) == 2


def test_direction_labels_follow_frozen_zyx_sar_contract() -> None:
    labels = _direction_labels()

    assert labels["axial"] == {"horizontal": "L_to_R", "vertical": "P_to_A"}
    assert labels["coronal"] == {"horizontal": "L_to_R", "vertical": "I_to_S"}
    assert labels["sagittal"] == {"horizontal": "P_to_A", "vertical": "I_to_S"}
    assert labels["anterior"] == {
        "view": "A_to_P",
        "horizontal": "L_to_R",
        "vertical": "I_to_S",
    }


def test_case_board_is_atomic_read_only_and_closes_figure(tmp_path: Path) -> None:
    arrays = _arrays()
    before = {name: array.copy() for name, array in arrays.items()}
    projection = _projection()
    metrics = _projection_metrics(projection, outer_width=1)
    output = tmp_path / "case_00000.png"

    digest = _render_case_board(
        output,
        "case_00000",
        _metadata(),
        arrays,
        projection,
        metrics,
        center=(4, 4, 4),
    )

    assert output.is_file()
    assert len(digest) == 64
    assert plt.get_fignums() == []
    for name, expected in before.items():
        assert np.array_equal(arrays[name], expected)


def test_validate_frozen_dataset_rejects_wrong_expected_count(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "DATASET_COMPLETE.json").write_text(
        json.dumps(
            {
                "schema_version": "pars_dataset_freeze_v2",
                "status": "complete",
                "case_count": 2,
                "dataset_id": "wrong",
                "dataset_version": "wrong",
                "dataset_role": "main",
                "manifest_relative_path": "case_manifest.jsonl",
                "manifest_sha256": "a" * 64,
                "contract_sha256": "b" * 64,
                "split_plan_sha256": "c" * 64,
                "required_artifact_names": [],
                "projection_coordinate_contract_id": "pars_simind_v8_xcat_zyx_sar_v1",
                "loader_transform_id": (
                    "simind_v8_xcat_v1_views_forward_roll000_"
                    "det_v_flip_det_u_keep"
                ),
                "split_counts": {"train": 2, "val": 0, "test": 0},
                "frozen_utc": "2026-07-16T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(Task12GAuditError, match="exactly 50"):
        validate_frozen_dataset(root)


def test_audit_rejects_qa_root_inside_dataset_before_reading_marker(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    root.mkdir()

    with pytest.raises(Task12GAuditError, match="outside"):
        audit_task12g(root, root / "qa")
