"""Frozen coverage and resumability primitives for the Task-12 15-case pilot."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Mapping, Sequence

from .case_writer_v2 import CaseRecordV2, load_case_record_v2
from .pilot_v2 import PILOT15_PLAN_SCHEMA_VERSION
from .schemas_v2 import PopulationProfileV2
from .seeds import SeedBundle


PILOT15_CASE_COUNT = 15
PILOT15_PROGRESS_SCHEMA = "pars_v2_pilot15_progress_v1"
PILOT15_RUNTIME_SCHEMA = "pars_v2_pilot15_runtime_v1"
PILOT15_PREFLIGHT_SCHEMA = "pars_v2_pilot15_preflight_v1"
PILOT15_GATE_SCHEMA = "pars_v2_pilot15_gate_v1"
PILOT15_COVERAGE_LABEL = "task12_fixed_visual_physics_qa_coverage"


def _dmax_band(value: float) -> str:
    if value < 20.0:
        return "10-<20_mm"
    if value < 40.0:
        return "20-<40_mm"
    if value < 60.0:
        return "40-<60_mm"
    if value < 80.0:
        return "60-<80_mm"
    if value <= 100.0:
        return "80-100_mm"
    return ">100-200_mm"


def _bmi_band(value: float) -> str:
    if value < 22.0:
        return "lean_coverage"
    if value < 27.5:
        return "mid_coverage"
    if value < 32.0:
        return "high_coverage"
    return "very_high_coverage"


def _gate(name: str, passed: bool, observed: object, required: object) -> dict[str, object]:
    return {
        "name": name,
        "status": "pass" if passed else "fail",
        "observed": observed,
        "required": required,
    }


def pilot15_coverage_report(
    plan: Mapping[str, object],
    profile: PopulationProfileV2,
) -> dict[str, object]:
    """Validate deterministic QA coverage without treating it as prevalence."""

    if plan.get("schema_version") != PILOT15_PLAN_SCHEMA_VERSION:
        raise ValueError("pilot15 coverage requires the pilot15 plan schema")
    raw_cases = plan.get("cases")
    if not isinstance(raw_cases, list) or len(raw_cases) != PILOT15_CASE_COUNT:
        raise ValueError("pilot15 coverage requires exactly 15 cases")
    cases: list[Mapping[str, object]] = []
    for index, value in enumerate(raw_cases):
        if not isinstance(value, Mapping):
            raise ValueError(f"pilot15 case {index} must be an object")
        cases.append(value)

    expected_case_ids = [f"case_{index:05d}" for index in range(PILOT15_CASE_COUNT)]
    expected_family_ids = [
        f"family_{index:05d}" for index in range(PILOT15_CASE_COUNT)
    ]
    case_ids = [str(case.get("case_id")) for case in cases]
    family_ids = [str(case.get("case_family_id")) for case in cases]
    sexes: list[str] = []
    morphologies: list[str] = []
    bmi_bands: list[str] = []
    injections: list[str] = []
    lesion_counts: list[int] = []
    dmax_bands: list[str] = []
    lobe_extents: list[str] = []
    lesion_morphologies: list[str] = []
    subcapsular_values: list[bool] = []
    mismatch_values: list[bool] = []
    heterogeneous_values: list[bool] = []
    tnr_values: list[float] = []

    for case in cases:
        patient = case.get("patient")
        lesions = case.get("lesions")
        if not isinstance(patient, Mapping):
            raise ValueError(f"{case.get('case_id')}: patient must be an object")
        if not isinstance(lesions, list) or not lesions:
            raise ValueError(f"{case.get('case_id')}: lesions must be non-empty")
        sex = str(patient.get("sex"))
        height = float(patient.get("height_cm"))
        weight = float(patient.get("weight_kg"))
        bmi = weight / (height / 100.0) ** 2
        if not math.isfinite(bmi):
            raise ValueError("pilot15 patient BMI is non-finite")
        sexes.append(sex)
        morphologies.append(str(patient.get("liver_morphology")))
        bmi_bands.append(_bmi_band(bmi))
        injections.append(str(case.get("injection_territory")))
        lesion_counts.append(len(lesions))
        lesion_lobes: set[str] = set()
        for lesion in lesions:
            if not isinstance(lesion, Mapping):
                raise ValueError("pilot15 lesion must be an object")
            diameter = float(lesion.get("dmax_mm"))
            if not 10.0 <= diameter <= 200.0:
                raise ValueError("pilot15 lesion diameter must be within 10--200 mm")
            dmax_bands.append(_dmax_band(diameter))
            lesion_lobes.add(str(lesion.get("lobe")))
            lesion_morphologies.append(str(lesion.get("morphology")))
            subcapsular_values.append(bool(lesion.get("subcapsular")))
        lobe_extents.append("bilobar" if len(lesion_lobes) > 1 else "unilobar")
        mismatch_values.append(bool(case.get("mismatch_challenge")))
        heterogeneous_values.append(bool(case.get("heterogeneous")))
        tnr_values.append(float(case.get("tnr_mean")))

    expected_injections = set(profile.value("injection_territories"))
    tnr_range = tuple(float(value) for value in profile.value("tnr_mean_range"))
    rr_values = [
        SeedBundle.from_case(int(plan["global_seed"]), case_id).simind
        for case_id in case_ids
    ]
    gates = [
        _gate("case_ids_exact", case_ids == expected_case_ids, case_ids, expected_case_ids),
        _gate(
            "family_ids_exact",
            family_ids == expected_family_ids,
            family_ids,
            expected_family_ids,
        ),
        _gate("sex_coverage", set(sexes) == {"male", "female"}, sorted(set(sexes)), ["female", "male"]),
        _gate(
            "liver_morphology_coverage",
            set(morphologies) == {"normal", "cirrhotic"},
            sorted(set(morphologies)),
            ["cirrhotic", "normal"],
        ),
        _gate(
            "bmi_band_coverage",
            set(bmi_bands)
            == {"lean_coverage", "mid_coverage", "high_coverage", "very_high_coverage"},
            sorted(set(bmi_bands)),
            ["lean_coverage", "mid_coverage", "high_coverage", "very_high_coverage"],
        ),
        _gate(
            "injection_territory_coverage",
            set(injections) == expected_injections,
            sorted(set(injections)),
            sorted(expected_injections),
        ),
        _gate(
            "lesion_count_coverage",
            any(value == 1 for value in lesion_counts)
            and any(2 <= value <= 5 for value in lesion_counts),
            sorted(set(lesion_counts)),
            "both 1 and 2--5",
        ),
        _gate(
            "dmax_band_coverage",
            set(dmax_bands)
            == {
                "10-<20_mm",
                "20-<40_mm",
                "40-<60_mm",
                "60-<80_mm",
                "80-100_mm",
                ">100-200_mm",
            },
            sorted(set(dmax_bands)),
            "all six visual-QA diameter bands",
        ),
        _gate(
            "lobe_extent_coverage",
            set(lobe_extents) == {"unilobar", "bilobar"},
            sorted(set(lobe_extents)),
            ["bilobar", "unilobar"],
        ),
        _gate(
            "lesion_morphology_coverage",
            set(lesion_morphologies) == {"smooth_nodular", "lobulated_confluent"},
            sorted(set(lesion_morphologies)),
            ["lobulated_confluent", "smooth_nodular"],
        ),
        _gate(
            "subcapsular_coverage",
            set(subcapsular_values) == {False, True},
            sorted(set(subcapsular_values)),
            [False, True],
        ),
        _gate(
            "mismatch_coverage",
            set(mismatch_values) == {False, True} and sum(mismatch_values) >= 3,
            {"values": sorted(set(mismatch_values)), "true_count": sum(mismatch_values)},
            "both values and at least 3 challenge cases",
        ),
        _gate(
            "heterogeneity_coverage",
            set(heterogeneous_values) == {False, True}
            and sum(heterogeneous_values) >= 10,
            {
                "values": sorted(set(heterogeneous_values)),
                "true_count": sum(heterogeneous_values),
            },
            "both values and at least 10 heterogeneous cases",
        ),
        _gate(
            "tnr_range_and_stress_coverage",
            all(tnr_range[0] <= value <= tnr_range[1] for value in tnr_values)
            and min(tnr_values) <= 1.0
            and max(tnr_values) >= 4.0,
            {"minimum": min(tnr_values), "maximum": max(tnr_values)},
            {"profile_range": list(tnr_range), "qa_minimum_at_most": 1.0, "qa_maximum_at_least": 4.0},
        ),
        _gate(
            "simind_rr_unique_practical",
            len(set(rr_values)) == PILOT15_CASE_COUNT
            and all(1 <= value <= 10_007 for value in rr_values),
            rr_values,
            "15 unique values in [1,10007]",
        ),
    ]
    status = "pass" if all(item["status"] == "pass" for item in gates) else "fail"
    return {
        "schema_version": "pars_v2_pilot15_coverage_v1",
        "status": status,
        "case_count": len(cases),
        "prevalence_claim": False,
        "purpose": "deterministic visual and physics QA coverage",
        "gates": gates,
        "counts": {
            "sex": {value: sexes.count(value) for value in sorted(set(sexes))},
            "liver_morphology": {
                value: morphologies.count(value) for value in sorted(set(morphologies))
            },
            "injection_territory": {
                value: injections.count(value) for value in sorted(set(injections))
            },
            "lesion_count": {
                str(value): lesion_counts.count(value) for value in sorted(set(lesion_counts))
            },
            "mismatch_true": sum(mismatch_values),
            "heterogeneous_true": sum(heterogeneous_values),
        },
        "rr_by_case": dict(zip(case_ids, rr_values)),
    }


def require_pilot15_coverage(
    plan: Mapping[str, object],
    profile: PopulationProfileV2,
) -> dict[str, object]:
    report = pilot15_coverage_report(plan, profile)
    if report["status"] != "pass":
        failed = [
            str(item["name"])
            for item in report["gates"]
            if item["status"] != "pass"
        ]
        raise ValueError(f"pilot15 coverage gates failed: {failed}")
    return report


def classify_run_root(
    output_root: Path,
    work_root: Path,
    *,
    resume: bool,
) -> str:
    output_exists = output_root.exists()
    work_exists = work_root.exists()
    if output_exists != work_exists:
        raise RuntimeError("pilot15 output/work roots are inconsistent; manual audit required")
    if not output_exists:
        if resume:
            raise RuntimeError("--resume requires an existing pilot15 output/work root")
        return "fresh"
    if not resume:
        raise FileExistsError("pilot15 roots already exist; use --resume after auditing PROGRESS.json")
    return "resume"


def next_attempt_dir(work_root: Path, case_id: str) -> Path:
    parent = work_root / "inputs" / case_id
    if not parent.exists():
        return parent / "attempt_001"
    indices = []
    for path in parent.iterdir():
        if path.is_dir() and path.name.startswith("attempt_"):
            try:
                indices.append(int(path.name.removeprefix("attempt_")))
            except ValueError:
                continue
    return parent / f"attempt_{(max(indices, default=0) + 1):03d}"


def load_completed_records(
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
        raise RuntimeError(f"pilot15 contains unexpected formal case directories: {sorted(unexpected)}")
    return [
        load_case_record_v2(
            cases_root / case_id / "case_record.json",
            dataset_root=output_root,
            verify_hashes=True,
        )
        for case_id in expected_case_ids
        if case_id in observed
    ]
