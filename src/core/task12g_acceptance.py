"""Pure evidence-shaping helpers for the Task 12G Linux50 acceptance review."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any


MISMATCH_CHALLENGE_CASE_IDS = (
    "case_00000",
    "case_00001",
    "case_00002",
)
MISMATCH_CHALLENGE_SEMANTICS = (
    "zero_population_weight_coverage_challenges_not_prevalence"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def ensure_qa_root_outside_dataset(
    dataset_root: str | Path,
    qa_root: str | Path,
) -> tuple[Path, Path]:
    """Resolve the two roots and reject any QA output inside frozen data."""

    dataset = Path(dataset_root).resolve()
    qa = Path(qa_root).resolve()
    if qa == dataset or dataset in qa.parents:
        raise ValueError("QA root must resolve outside the frozen dataset root")
    return dataset, qa


def group_case_ids(
    case_ids: Sequence[str],
    group_size: int = 10,
) -> list[list[str]]:
    """Return stable, non-overlapping groups while preserving manifest order."""

    if not isinstance(group_size, int) or isinstance(group_size, bool) or group_size <= 0:
        raise ValueError("group_size must be positive")
    ids = list(case_ids)
    if not all(isinstance(case_id, str) and case_id for case_id in ids):
        raise ValueError("case IDs must be non-empty strings")
    if len(ids) != len(set(ids)):
        raise ValueError("case IDs must be unique")
    return [
        ids[start : start + group_size]
        for start in range(0, len(ids), group_size)
    ]


def _finite_number(value: object, context: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        raise ValueError(f"{context} must be finite")
    return float(value)


def partition_population_and_challenges(
    case_rows: Sequence[Mapping[str, object]],
) -> dict[str, Any]:
    """Separate frozen coverage challenges from population-weighted cases."""

    population: list[dict[str, object]] = []
    challenge: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw_row in case_rows:
        row = dict(raw_row)
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("case row lacks a valid case_id")
        if case_id in seen:
            raise ValueError(f"duplicate case row: {case_id}")
        seen.add(case_id)
        mismatch = row.get("mismatch_challenge")
        if not isinstance(mismatch, bool):
            raise ValueError(f"{case_id} mismatch_challenge must be boolean")
        population_weight = _finite_number(
            row.get("population_weight"),
            f"{case_id} population_weight",
        )
        if mismatch:
            if population_weight != 0.0:
                raise ValueError(
                    f"{case_id} mismatch challenge must have zero population weight"
                )
            challenge.append(row)
        else:
            if population_weight <= 0.0:
                raise ValueError(
                    f"{case_id} population case must have positive population weight"
                )
            population.append(row)
    observed_challenges = tuple(row["case_id"] for row in challenge)
    if observed_challenges != MISMATCH_CHALLENGE_CASE_IDS:
        raise ValueError(
            "frozen mismatch challenge case set/order changed: "
            f"expected={MISMATCH_CHALLENGE_CASE_IDS}, observed={observed_challenges}"
        )
    return {
        "population": population,
        "challenge": challenge,
        "population_count": len(population),
        "challenge_count": len(challenge),
        "challenge_case_ids": list(MISMATCH_CHALLENGE_CASE_IDS),
        "challenge_semantics": MISMATCH_CHALLENGE_SEMANTICS,
    }


def select_focus_cases(
    case_rows: Sequence[Mapping[str, object]],
    *,
    failed_case_ids: Iterable[str] = (),
) -> list[dict[str, object]]:
    """Select challenge, extreme, and gate-attention cases with deduplicated reasons."""

    rows = [dict(row) for row in case_rows]
    if not rows:
        return []
    by_id: dict[str, dict[str, object]] = {}
    reasons: dict[str, list[str]] = {}
    for row in rows:
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("case row lacks a valid case_id")
        if case_id in by_id:
            raise ValueError(f"duplicate case row: {case_id}")
        by_id[case_id] = row
        reasons[case_id] = []

    def add_reason(case_id: str, reason: str) -> None:
        if reason not in reasons[case_id]:
            reasons[case_id].append(reason)

    for row in rows:
        if row.get("mismatch_challenge") is True:
            add_reason(str(row["case_id"]), "mismatch_challenge")

    for case_id in failed_case_ids:
        if case_id not in by_id:
            raise ValueError(f"failed case ID is absent from case rows: {case_id}")
        add_reason(case_id, "automatic_gate_attention")

    metrics = (
        ("liver_volume_ml", "liver_volume"),
        ("dmax_mm", "dmax"),
        ("tumor_fraction_liver", "tumor_burden"),
        ("projection_weight_sum", "projection_total"),
    )
    for field, label in metrics:
        values = [
            (_finite_number(row.get(field), f"{row['case_id']} {field}"), str(row["case_id"]))
            for row in rows
        ]
        minimum = min(values, key=lambda item: (item[0], item[1]))
        maximum = max(values, key=lambda item: (item[0], item[1]))
        add_reason(minimum[1], f"minimum_{label}")
        add_reason(maximum[1], f"maximum_{label}")

    return [
        {
            "case_id": case_id,
            "reasons": case_reasons,
        }
        for case_id, case_reasons in reasons.items()
        if case_reasons
    ]


def gate_evidence_rows(
    reports: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Shape formal reports for display without changing their authority."""

    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in reports:
        gate_id = item.get("gate_id")
        if not isinstance(gate_id, str) or not gate_id:
            raise ValueError("gate evidence lacks a valid gate_id")
        if gate_id in seen:
            raise ValueError(f"duplicate gate evidence: {gate_id}")
        seen.add(gate_id)
        blocking = item.get("blocking")
        if not isinstance(blocking, bool):
            raise ValueError(f"{gate_id} blocking must be boolean")
        report = item.get("report")
        if not isinstance(report, Mapping):
            raise ValueError(f"{gate_id} report must be a mapping")
        status = report.get("status")
        schema = report.get("schema_version")
        if not isinstance(status, str) or not status:
            raise ValueError(f"{gate_id} report lacks a status")
        if not isinstance(schema, str) or not schema:
            raise ValueError(f"{gate_id} report lacks a schema_version")
        digest = item.get("sha256")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ValueError(f"{gate_id} evidence SHA-256 is invalid")
        path = item.get("path")
        if not isinstance(path, (str, Path)):
            raise ValueError(f"{gate_id} evidence path is invalid")
        rows.append(
            {
                "gate_id": gate_id,
                "schema_version": schema,
                "status": status,
                "blocking": blocking,
                "path": str(Path(path).resolve()),
                "sha256": digest,
                "meaning": str(item.get("meaning", "")),
            }
        )
    return rows


def build_automatic_summary(
    *,
    dataset_id: str,
    manifest_sha256: str,
    gate_rows: Sequence[Mapping[str, object]],
    focus_cases: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Build the automatic result while keeping manual/500 release closed."""

    if not isinstance(dataset_id, str) or not dataset_id:
        raise ValueError("dataset_id must be a non-empty string")
    if not _SHA256.fullmatch(manifest_sha256):
        raise ValueError("manifest_sha256 must be a lowercase SHA-256 digest")
    blocking_rows = [row for row in gate_rows if row.get("blocking") is True]
    automatic_pass = bool(blocking_rows) and all(
        row.get("status") == "pass" for row in blocking_rows
    )
    return {
        "schema_version": "pars_v2_task12g_automatic_acceptance_v1",
        "status": "pass_awaiting_manual_review" if automatic_pass else "fail",
        "dataset_id": dataset_id,
        "manifest_sha256": manifest_sha256,
        "automatic_gate_passed": automatic_pass,
        "gate_rows": [dict(row) for row in gate_rows],
        "focus_cases": [dict(row) for row in focus_cases],
        "manual_review_required": True,
        "manual_review_status": "pending",
        "go_for_500_case_generation": False,
        "next_action": (
            "review the read-only Notebook and create a separate evidence-bound "
            "manual approval or rejection"
        ),
    }
