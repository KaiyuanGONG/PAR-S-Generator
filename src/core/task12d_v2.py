"""Coverage and resume contracts for the three-case Task-12D full-chain gate."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

from .case_writer_v2 import CaseRecordV2, load_case_record_v2
from .pilot_v2 import TASK12D_PLAN_SCHEMA_VERSION
from .seeds import SeedBundle


TASK12D_CASE_COUNT = 3
TASK12D_COVERAGE_LABEL = "task12d_runtime_bound_fullchain_coverage"
TASK12D_PREFLIGHT_SCHEMA = "pars_v2_task12d_preflight_v1"
TASK12D_RUNTIME_SCHEMA = "pars_v2_task12d_runtime_v1"
TASK12D_PROGRESS_SCHEMA = "pars_v2_task12d_progress_v1"
TASK12D_GENERATION_GATE_SCHEMA = "pars_v2_task12d_generation_gate_v1"


def require_task12d_coverage(plan: Mapping[str, object]) -> dict[str, object]:
    if plan.get("schema_version") != TASK12D_PLAN_SCHEMA_VERSION:
        raise ValueError("Task 12D requires its versioned plan schema")
    cases = plan.get("cases")
    if not isinstance(cases, list) or len(cases) != TASK12D_CASE_COUNT:
        raise ValueError("Task 12D requires exactly three cases")
    if not all(isinstance(case, Mapping) for case in cases):
        raise ValueError("Task 12D cases must be objects")
    case_ids = [str(case["case_id"]) for case in cases]
    family_ids = [str(case["case_family_id"]) for case in cases]
    if case_ids != [f"case_{index:05d}" for index in range(TASK12D_CASE_COUNT)]:
        raise ValueError("Task 12D case IDs/order are not frozen")
    if family_ids != [f"family_{index:05d}" for index in range(TASK12D_CASE_COUNT)]:
        raise ValueError("Task 12D family IDs/order are not frozen")

    morphologies: set[str] = set()
    injections: set[str] = set()
    lobe_extents: set[str] = set()
    mismatches: set[bool] = set()
    maximum_diameters: list[float] = []
    for case in cases:
        patient = case.get("patient")
        lesions = case.get("lesions")
        if (
            not isinstance(patient, Mapping)
            or not isinstance(lesions, list)
            or not lesions
        ):
            raise ValueError("Task 12D patient/lesion coverage is invalid")
        morphologies.add(str(patient.get("liver_morphology")))
        injections.add(str(case.get("injection_territory")))
        mismatches.add(bool(case.get("mismatch_challenge")))
        lobes = {
            str(lesion.get("lobe")) for lesion in lesions if isinstance(lesion, Mapping)
        }
        if len(lobes) not in {1, 2}:
            raise ValueError("Task 12D lesion lobe coverage is invalid")
        lobe_extents.add("bilobar" if len(lobes) == 2 else "unilobar")
        maximum_diameters.append(
            max(
                float(lesion["dmax_mm"])
                for lesion in lesions
                if isinstance(lesion, Mapping)
            )
        )

    expected_size_bands = (
        maximum_diameters[0] <= 25.0
        and 40.0 <= maximum_diameters[1] <= 70.0
        and maximum_diameters[2] >= 80.0
    )
    rr_by_case = {
        case_id: SeedBundle.from_case(int(plan["global_seed"]), case_id).simind
        for case_id in case_ids
    }
    gates = {
        "normal_and_cirrhotic": morphologies == {"normal", "cirrhotic"},
        "three_injection_territories": injections
        == {"whole_liver", "right_lobar", "left_lobar"},
        "unilobar_and_bilobar": lobe_extents == {"unilobar", "bilobar"},
        "matched_and_mismatch": mismatches == {False, True},
        "small_medium_large": expected_size_bands,
        "unique_practical_rr": len(set(rr_by_case.values())) == TASK12D_CASE_COUNT
        and all(1 <= value <= 10_007 for value in rr_by_case.values()),
    }
    if not all(gates.values()):
        raise ValueError(
            f"Task 12D coverage gates failed: "
            f"{sorted(name for name, passed in gates.items() if not passed)}"
        )
    return {
        "schema_version": "pars_v2_task12d_coverage_v1",
        "status": "pass",
        "purpose": "full-chain engineering verification; no prevalence claim",
        "case_count": TASK12D_CASE_COUNT,
        "case_ids": case_ids,
        "rr_by_case": rr_by_case,
        "maximum_diameters_mm": maximum_diameters,
        "gates": gates,
    }


def classify_task12d_roots(output_root: Path, work_root: Path, *, resume: bool) -> str:
    output_exists = output_root.exists()
    work_exists = work_root.exists()
    if output_exists != work_exists:
        raise RuntimeError(
            "Task 12D output/work roots are inconsistent; audit required"
        )
    if not output_exists:
        if resume:
            raise RuntimeError("--resume requires existing Task 12D roots")
        return "fresh"
    if not resume:
        raise FileExistsError("Task 12D roots already exist; use --resume after audit")
    return "resume"


def next_task12d_attempt_dir(work_root: Path, case_id: str) -> Path:
    parent = work_root / "inputs" / case_id
    if not parent.exists():
        return parent / "attempt_001"
    indices = []
    for path in parent.iterdir():
        if not path.is_dir() or not path.name.startswith("attempt_"):
            continue
        try:
            indices.append(int(path.name.removeprefix("attempt_")))
        except ValueError:
            continue
    return parent / f"attempt_{max(indices, default=0) + 1:03d}"


def load_task12d_records(
    output_root: Path,
    expected_case_ids: Sequence[str],
) -> list[CaseRecordV2]:
    cases_root = output_root / "cases"
    if not cases_root.exists():
        return []
    expected = set(expected_case_ids)
    observed = {path.name for path in cases_root.iterdir() if path.is_dir()}
    unexpected = observed - expected
    if unexpected:
        raise RuntimeError(f"Task 12D has unexpected cases: {sorted(unexpected)}")
    return [
        load_case_record_v2(
            cases_root / case_id / "case_record.json",
            dataset_root=output_root,
            verify_hashes=True,
        )
        for case_id in expected_case_ids
        if case_id in observed
    ]
