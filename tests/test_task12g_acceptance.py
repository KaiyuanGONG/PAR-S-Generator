from __future__ import annotations

from pathlib import Path

import pytest

from src.core.task12g_acceptance import (
    build_automatic_summary,
    ensure_qa_root_outside_dataset,
    gate_evidence_rows,
    group_case_ids,
    partition_population_and_challenges,
    select_focus_cases,
)


def test_qa_root_must_be_outside_frozen_dataset(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()

    with pytest.raises(ValueError, match="outside"):
        ensure_qa_root_outside_dataset(dataset, dataset / "qa")


def test_fifty_cases_are_grouped_exactly_once() -> None:
    ids = [f"case_{index:05d}" for index in range(50)]

    groups = group_case_ids(ids, group_size=10)

    assert [len(group) for group in groups] == [10, 10, 10, 10, 10]
    assert [case_id for group in groups for case_id in group] == ids


def test_group_case_ids_rejects_duplicates() -> None:
    with pytest.raises(ValueError, match="unique"):
        group_case_ids(["case_00000", "case_00000"])


def test_challenges_are_separate_from_population() -> None:
    rows = [
        {
            "case_id": "case_00000",
            "mismatch_challenge": True,
            "population_weight": 0.0,
        },
        {
            "case_id": "case_00001",
            "mismatch_challenge": True,
            "population_weight": 0.0,
        },
        {
            "case_id": "case_00002",
            "mismatch_challenge": True,
            "population_weight": 0.0,
        },
        {
            "case_id": "case_00003",
            "mismatch_challenge": False,
            "population_weight": 1.0,
        },
    ]

    partition = partition_population_and_challenges(rows)

    assert [row["case_id"] for row in partition["challenge"]] == [
        "case_00000",
        "case_00001",
        "case_00002",
    ]
    assert [row["case_id"] for row in partition["population"]] == ["case_00003"]
    assert partition["challenge_semantics"] == (
        "zero_population_weight_coverage_challenges_not_prevalence"
    )


def test_challenge_partition_rejects_wrong_frozen_case_set() -> None:
    rows = [
        {
            "case_id": "case_00000",
            "mismatch_challenge": True,
            "population_weight": 0.0,
        },
        {
            "case_id": "case_00001",
            "mismatch_challenge": True,
            "population_weight": 0.0,
        },
        {
            "case_id": "case_00003",
            "mismatch_challenge": True,
            "population_weight": 0.0,
        },
    ]

    with pytest.raises(ValueError, match="frozen mismatch challenge"):
        partition_population_and_challenges(rows)


def test_focus_selection_deduplicates_reasons() -> None:
    rows = [
        {
            "case_id": "case_00000",
            "mismatch_challenge": True,
            "liver_volume_ml": 1000.0,
            "dmax_mm": 20.0,
            "tumor_fraction_liver": 0.01,
            "projection_weight_sum": 100.0,
        },
        {
            "case_id": "case_00001",
            "mismatch_challenge": True,
            "liver_volume_ml": 1500.0,
            "dmax_mm": 60.0,
            "tumor_fraction_liver": 0.10,
            "projection_weight_sum": 500.0,
        },
        {
            "case_id": "case_00002",
            "mismatch_challenge": True,
            "liver_volume_ml": 1750.0,
            "dmax_mm": 80.0,
            "tumor_fraction_liver": 0.15,
            "projection_weight_sum": 700.0,
        },
        {
            "case_id": "case_00003",
            "mismatch_challenge": False,
            "liver_volume_ml": 2000.0,
            "dmax_mm": 100.0,
            "tumor_fraction_liver": 0.20,
            "projection_weight_sum": 900.0,
        },
    ]

    focus = select_focus_cases(rows, failed_case_ids=["case_00000"])

    case0 = next(item for item in focus if item["case_id"] == "case_00000")
    assert "mismatch_challenge" in case0["reasons"]
    assert "automatic_gate_attention" in case0["reasons"]
    assert "minimum_liver_volume" in case0["reasons"]
    assert len({item["case_id"] for item in focus}) == len(focus)


def test_gate_rows_preserve_formal_status() -> None:
    rows = gate_evidence_rows(
        [
            {
                "gate_id": "clinical_projection_quality_gate_v1",
                "blocking": True,
                "report": {"schema_version": "quality_v1", "status": "fail"},
                "path": Path("quality.json"),
                "sha256": "a" * 64,
                "meaning": "full-physics projection quality",
            }
        ]
    )

    assert rows[0]["status"] == "fail"
    assert rows[0]["blocking"] is True


def test_gate_rows_reject_invalid_digest() -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        gate_evidence_rows(
            [
                {
                    "gate_id": "gate",
                    "blocking": True,
                    "report": {"schema_version": "v1", "status": "pass"},
                    "path": Path("gate.json"),
                    "sha256": "not-a-digest",
                }
            ]
        )


def test_automatic_summary_never_releases_500() -> None:
    summary = build_automatic_summary(
        dataset_id="dataset",
        manifest_sha256="b" * 64,
        gate_rows=[{"blocking": True, "status": "pass"}],
        focus_cases=[],
    )

    assert summary["automatic_gate_passed"] is True
    assert summary["status"] == "pass_awaiting_manual_review"
    assert summary["go_for_500_case_generation"] is False
    assert summary["manual_review_status"] == "pending"


def test_automatic_summary_cannot_hide_blocking_failure() -> None:
    summary = build_automatic_summary(
        dataset_id="dataset",
        manifest_sha256="b" * 64,
        gate_rows=[
            {"blocking": True, "status": "pass"},
            {"blocking": True, "status": "fail"},
            {"blocking": False, "status": "diagnostic_nonunique"},
        ],
        focus_cases=[],
    )

    assert summary["automatic_gate_passed"] is False
    assert summary["status"] == "fail"
    assert summary["go_for_500_case_generation"] is False
