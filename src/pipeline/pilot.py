"""Deterministic representative-case selection for protocol pilots."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from pipeline.contracts import atomic_write_json, read_jsonl


FEATURE_NAMES = (
    "liver_volume_ml",
    "left_ratio",
    "n_tumors",
    "mean_lesion_diameter_mm",
    "mean_lesion_tnr",
    "minimum_surface_margin_mm",
)


def _features(case: dict) -> list[float]:
    qc_path = Path(case["qc"]["phantom"]["path"])
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    metrics = qc["metrics"]
    tumors = metrics.get("tumors", [])
    diameters = [float(row["effective_diameter_mm"]) for row in tumors]
    tnrs = [
        float(row["tnr_from_saved_activity"])
        for row in tumors
        if row.get("tnr_from_saved_activity") is not None
    ]
    margins = [float(row["surface_margin_mm"]) for row in tumors]
    return [
        float(metrics["liver_volume_ml"]),
        float(metrics["left_ratio"]),
        float(metrics["n_tumors"]),
        float(np.mean(diameters)) if diameters else 0.0,
        float(np.mean(tnrs)) if tnrs else 0.0,
        float(np.min(margins)) if margins else 0.0,
    ]


def select_representative_cases(cases: list[dict], count: int) -> dict:
    """Select a deterministic standardized-feature maximin subset."""
    ordered = sorted(cases, key=lambda row: row["case_id"])
    if count < 1 or count > len(ordered):
        raise ValueError("count must be between one and the available case count")
    matrix = np.asarray([_features(case) for case in ordered], dtype=np.float64)
    if not np.isfinite(matrix).all():
        raise ValueError("pilot feature matrix contains non-finite values")
    center = np.median(matrix, axis=0)
    scale = np.std(matrix, axis=0, ddof=0)
    scale[scale == 0] = 1.0
    standardized = (matrix - center) / scale

    selected = [int(np.argmin(np.linalg.norm(standardized, axis=1)))]
    while len(selected) < count:
        distances = np.min(
            np.linalg.norm(
                standardized[:, None, :] - standardized[np.asarray(selected)][None, :, :],
                axis=2,
            ),
            axis=1,
        )
        distances[np.asarray(selected)] = -np.inf
        maximum = float(np.max(distances))
        candidates = np.flatnonzero(np.isclose(distances, maximum, rtol=0, atol=1e-12))
        selected.append(int(candidates[0]))

    records = []
    for rank, index in enumerate(selected, 1):
        case = ordered[index]
        records.append(
            {
                "selection_rank": rank,
                "case_id": case["case_id"],
                "case_number": int(case["case_id"].rsplit("_", 1)[1]),
                "split_in_population_run": case["split"],
                "features": {
                    name: float(value)
                    for name, value in zip(FEATURE_NAMES, matrix[index])
                },
                "standardized_features": {
                    name: float(value)
                    for name, value in zip(FEATURE_NAMES, standardized[index])
                },
            }
        )
    return {
        "method": "deterministic_standardized_feature_maximin",
        "population_case_count": len(ordered),
        "selected_case_count": count,
        "feature_names": list(FEATURE_NAMES),
        "selected": records,
        "claim_boundary": "Representative protocol-pilot coverage, not clinical sampling.",
    }


def select_from_run(run_root: Path, count: int, output_path: Path | None = None) -> dict:
    run_root = Path(run_root)
    cases = read_jsonl(run_root / "cases.jsonl")
    result = select_representative_cases(cases, count)
    result["source_population_run_id"] = run_root.name
    result["source_population_run"] = str(run_root.resolve())
    destination = output_path or run_root / "qc" / "pilot_selection.json"
    atomic_write_json(destination, result)
    return result
