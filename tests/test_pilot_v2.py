from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.pilot_v2 import (  # noqa: E402
    _path_length_document,
    _simind_version,
    load_pilot_plan,
    resolve_plan_path,
    validate_boundary_rejections,
)
from core.measurements import PathLengthMetricsV2, PathLengthStatsV2  # noqa: E402
from core.schemas_v2 import load_evidence_registry, load_profile  # noqa: E402


def _profile():
    registry = load_evidence_registry(REPO_ROOT / "configs" / "evidence_registry_v2.json")
    return load_profile(REPO_ROOT / "configs" / "population_tare_hcc_nopvi_v2.json", registry)


def test_pilot_plan_freezes_three_cases_and_practical_rr_allocator() -> None:
    plan = load_pilot_plan(REPO_ROOT / "configs" / "pilot3_v2.json")

    assert len(plan["cases"]) == 3
    assert plan["execution"]["nn_multiplier"] == 1
    assert plan["execution"]["rr_allocator"] == "affine_permutation_mod_10007_v1"
    assert {case["size_label"] for case in plan["cases"]} == {
        "small",
        "medium",
        "large",
    }


def test_200_and_215_mm_boundaries_are_explicit_structural_rejections() -> None:
    plan = load_pilot_plan(REPO_ROOT / "configs" / "pilot3_v2.json")
    results = validate_boundary_rejections(plan, _profile())

    assert [item["dmax_mm"] for item in results] == [200.0, 215.0]
    assert all(item["passed"] for item in results)
    assert results[0]["actual_rasterized_volume_ml"] > results[0][
        "maximum_population_tumor_volume_ml"
    ]
    assert results[1]["actual_rasterized_volume_ml"] > results[1][
        "maximum_population_tumor_volume_ml"
    ]
    assert all(item["observed_result"] == "actual_raster_exceeds_burden_gate" for item in results)


def test_pilot_plan_rejects_unversioned_rr_mapping(tmp_path: Path) -> None:
    plan = load_pilot_plan(REPO_ROOT / "configs" / "pilot3_v2.json")
    plan["execution"]["rr_allocator"] = "hash_to_int31"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(plan), encoding="utf-8")

    with pytest.raises(ValueError, match="collision-free /RR"):
        load_pilot_plan(path)


def test_plan_paths_cannot_escape_repository() -> None:
    assert resolve_plan_path(
        REPO_ROOT,
        "configs/pilot3_v2.json",
        "pilot",
    ) == (REPO_ROOT / "configs" / "pilot3_v2.json").resolve()
    with pytest.raises(ValueError, match="relative path"):
        resolve_plan_path(REPO_ROOT, REPO_ROOT / "configs" / "pilot3_v2.json", "pilot")
    with pytest.raises(ValueError, match="escapes"):
        resolve_plan_path(REPO_ROOT, "../outside.json", "pilot")


def test_simind_version_is_parsed_from_actual_result_syntax(tmp_path: Path) -> None:
    path = tmp_path / "case.res"
    path.write_text(
        "SIMIND Monte Carlo Simulation Program    V8.0\n",
        encoding="ascii",
    )
    assert _simind_version(path) == "SIMIND V8.0"


def test_path_length_document_uses_json_arrays_for_every_view() -> None:
    stats = PathLengthStatsV2(mean_mm=10.0, p05_mm=5.0, p50_mm=10.0, p95_mm=15.0)
    metrics = PathLengthMetricsV2(
        angles_deg=tuple(float(index * 6) for index in range(60)),
        body=tuple(stats for _ in range(60)),
        liver=tuple(stats for _ in range(60)),
    )

    document = _path_length_document(metrics)

    assert isinstance(document["angles_deg"], list)
    assert isinstance(document["body"], list)
    assert isinstance(document["liver"], list)
    assert len(document["body"]) == 60
    assert document["body"][0] == {
        "mean_mm": 10.0,
        "p05_mm": 5.0,
        "p50_mm": 10.0,
        "p95_mm": 15.0,
    }
