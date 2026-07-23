"""Deterministic assembly helpers for the three-case PAR-S V2 smoke pilot."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from .activity_model_v2 import ActivityFieldV2, generate_activity_field
from .anatomy_v2 import TorsoAnatomyBuildV2, build_torso_anatomy_v2
from .attenuation_model_v2 import (
    AttenuationDegradationMetadataV2,
    generate_attenuation_maps,
)
from .interfile_writer import write_attenuation_map_v2, write_voxel_source
from .liver_geometry import (
    GridSpecV2,
    LiverGeometryV2,
    LiverShapeRejectedError,
    fit_liver_geometry,
)
from .measurements import PathLengthMetricsV2, measure_path_lengths
from .population_sampler import sample_liver_target
from .provenance import sha256_file
from .schemas_v2 import (
    ActivityTargetV2,
    FROZEN_PROJECTION_COORDINATES_V1,
    PatientSampleV2,
    PopulationProfileV2,
    TumorTargetV2,
)
from .seeds import SeedBundle
from .simind_exec import SimindRunResult
from .simind_postprocess import audit_simind_completion
from .tumor_generator_v2 import (
    TumorCaseTargetV2,
    TumorGeometryV2,
    TumorStrataV2,
    place_and_rasterize_tumors,
    rasterize_tumor_at_center,
)


PILOT_PLAN_SCHEMA_VERSION = "pars_v2_pilot3_plan_v1"
PILOT15_PLAN_SCHEMA_VERSION = "pars_v2_pilot15_plan_v1"
TASK12D_PLAN_SCHEMA_VERSION = "pars_v2_task12d_plan_v1"
PILOT_GATE_SCHEMA_VERSION = "pars_v2_pilot3_gate_v1"
_PLAN_CASE_COUNTS = {
    PILOT_PLAN_SCHEMA_VERSION: 3,
    PILOT15_PLAN_SCHEMA_VERSION: 15,
    TASK12D_PLAN_SCHEMA_VERSION: 3,
}
_PLAN_PURPOSES = {
    PILOT_PLAN_SCHEMA_VERSION: (
        "deterministic_smoke_only_pending_task12_clinical_count_benchmark"
    ),
    PILOT15_PLAN_SCHEMA_VERSION: "visual_physics_qa_before_statistical_pilot",
    TASK12D_PLAN_SCHEMA_VERSION: (
        "runtime_bound_fullchain_verification_before_50_case_expansion"
    ),
}
_SIMIND_VERSION = re.compile(r"SIMIND Monte Carlo Simulation Program\s+V([0-9.]+)")
_MAX_INT63 = 2**63 - 1


@dataclass(frozen=True)
class PreparedPilotCaseV2:
    case_id: str
    patient: PatientSampleV2
    seeds: SeedBundle
    liver: LiverGeometryV2
    liver_fit_attempt: int
    tumor_target: TumorCaseTargetV2
    tumors: TumorGeometryV2
    activity_target: ActivityTargetV2
    activity: ActivityFieldV2
    anatomy: TorsoAnatomyBuildV2
    attenuation_metadata: AttenuationDegradationMetadataV2
    base_histories_per_projection: int
    arrays: Mapping[str, np.ndarray]
    source_bin: Path
    density_bin: Path


def load_pilot_plan(path: str | Path) -> dict[str, object]:
    plan_path = Path(path)
    try:
        raw = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read pilot plan: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") not in _PLAN_CASE_COUNTS:
        raise ValueError("pilot plan has an invalid schema_version")
    schema_version = str(raw["schema_version"])
    expected_count = _PLAN_CASE_COUNTS[schema_version]
    required = {
        "dataset_id",
        "dataset_version",
        "dataset_role",
        "profile_path",
        "scanner_path",
        "evidence_registry_path",
        "smc_path",
        "simind_ini_path",
        "expected_simind_binary_sha256",
        "global_seed",
        "split_ratios",
        "execution",
        "cases",
        "boundary_gates",
    }
    missing = sorted(required - set(raw))
    if missing:
        raise ValueError(f"pilot plan missing required fields: {missing}")
    cases = raw["cases"]
    if not isinstance(cases, list) or len(cases) != expected_count:
        raise ValueError(f"pilot plan must contain exactly {expected_count} cases")
    case_ids = [case.get("case_id") for case in cases if isinstance(case, dict)]
    family_ids = [case.get("case_family_id") for case in cases if isinstance(case, dict)]
    if len(case_ids) != expected_count or len(set(case_ids)) != expected_count:
        raise ValueError(f"pilot case IDs must be {expected_count} unique strings")
    if len(family_ids) != expected_count or len(set(family_ids)) != expected_count:
        raise ValueError(f"pilot family IDs must be {expected_count} unique strings")
    execution = raw["execution"]
    if not isinstance(execution, dict):
        raise ValueError("pilot execution must be an object")
    if execution.get("purpose") != _PLAN_PURPOSES[schema_version]:
        raise ValueError("pilot /NN=1 purpose label is missing or mismatched")
    if execution.get("nn_multiplier") != 1:
        raise ValueError("Task-12 pilot plans must use frozen /NN=1")
    if (
        execution.get("rr_allocator") != "affine_permutation_mod_10007_v1"
        or execution.get("rr_maximum") != 10_007
    ):
        raise ValueError("pilot plan must freeze the practical collision-free /RR allocator")
    return raw


def resolve_plan_path(repo_root: Path, value: object, name: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty relative path")
    relative = Path(value)
    if relative.is_absolute():
        raise ValueError(f"{name} must be relative to the Generator repository")
    path = (Path(repo_root) / relative).resolve()
    try:
        path.relative_to(Path(repo_root).resolve())
    except ValueError as exc:
        raise ValueError(f"{name} escapes the Generator repository") from exc
    if not path.is_file():
        raise FileNotFoundError(f"{name} not found: {path}")
    return path


def validate_boundary_rejections(
    plan: Mapping[str, object],
    profile: PopulationProfileV2,
) -> list[dict[str, object]]:
    """Prove why the requested 200/215 mm boundaries cannot be full pilot cases."""

    gates = plan.get("boundary_gates")
    if not isinstance(gates, list) or len(gates) != 2:
        raise ValueError("pilot boundary_gates must contain 200 and 215 mm")
    axis_range = profile.value("axis_ratio_range")
    liver_model = profile.value("liver_geometry_model")
    if not isinstance(axis_range, Sequence) or len(axis_range) != 2:
        raise ValueError("axis_ratio_range must contain lower/upper bounds")
    if not isinstance(liver_model, Mapping):
        raise ValueError("liver_geometry_model must be an object")
    max_liver_volume_ml = float(liver_model["volume_range_ml"][1])
    max_burden = float(profile.value("tumor_burden_fraction_max"))
    maximum_population_tumor_ml = max_liver_volume_ml * max_burden
    grid = GridSpecV2()
    center_index = np.asarray(grid.shape, dtype=np.float64) // 2
    center_world = (
        center_index @ grid.affine_4x4[:3, :3].T + grid.affine_4x4[:3, 3]
    )
    results: list[dict[str, object]] = []
    diameters = []
    for gate in gates:
        if not isinstance(gate, Mapping):
            raise ValueError("boundary gate entries must be objects")
        diameter = float(gate["dmax_mm"])
        diameters.append(diameter)
        axis_ratios = gate.get("axis_ratios")
        orientation = gate.get("orientation_deg_zyx")
        if (
            not isinstance(axis_ratios, Sequence)
            or len(axis_ratios) != 2
            or not isinstance(orientation, Sequence)
            or len(orientation) != 3
        ):
            raise ValueError("boundary gates must freeze axis ratios and orientation")
        expected = str(gate.get("expected_result"))
        if expected != "structural_rejection":
            raise ValueError("boundary gates must explicitly expect structural_rejection")
        if diameter not in {200.0, 215.0}:
            raise ValueError("boundary gate diameters must be exactly 200 and 215 mm")
        target = TumorTargetV2(
            lesion_id=f"boundary_{int(diameter)}mm",
            dmax_mm=diameter,
            axis_ratios=(float(axis_ratios[0]), float(axis_ratios[1])),
            lobe="right",
            morphology=str(gate.get("morphology")),
            orientation_deg_zyx=tuple(float(value) for value in orientation),
            primitive_count=int(gate.get("primitive_count", 0)),
            evidence_types={
                "dmax": "stress_test" if diameter > 200.0 else "coverage_boundary"
            },
        )
        raster = rasterize_tumor_at_center(target, center_world, grid)
        if raster.metrics is None:
            raise RuntimeError("boundary rasterization did not return actual metrics")
        actual_rasterized_volume_ml = float(raster.metrics.volume_ml)
        passed = actual_rasterized_volume_ml > maximum_population_tumor_ml
        rule = "actual_raster_exceeds_profile_maximum_tumor_burden"
        if not passed:
            raise ValueError(f"boundary structural rejection proof failed for {diameter:g} mm")
        results.append(
            {
                "dmax_mm": diameter,
                "expected_result": expected,
                "observed_result": "actual_raster_exceeds_burden_gate",
                "gate": rule,
                "axis_ratios": [float(value) for value in axis_ratios],
                "morphology": target.morphology,
                "primitive_count": target.primitive_count,
                "actual_recist_3d_mm": raster.metrics.recist_3d_mm,
                "actual_rasterized_volume_ml": actual_rasterized_volume_ml,
                "maximum_liver_volume_ml": max_liver_volume_ml,
                "maximum_population_tumor_volume_ml": maximum_population_tumor_ml,
                "passed": True,
            }
        )
    if sorted(diameters) != [200.0, 215.0]:
        raise ValueError("boundary gate diameters must be exactly 200 and 215 mm")
    return results


def _fixed_patient(
    case: Mapping[str, object],
    coverage_label: str,
) -> PatientSampleV2:
    raw = case.get("patient")
    if not isinstance(raw, Mapping):
        raise ValueError("case.patient must be an object")
    height_cm = float(raw["height_cm"])
    weight_kg = float(raw["weight_kg"])
    bmi = weight_kg / (height_cm / 100.0) ** 2
    morphology = str(raw["liver_morphology"])
    if morphology not in {"normal", "cirrhotic"}:
        raise ValueError("pilot liver_morphology must be normal or cirrhotic")
    return PatientSampleV2(
        case_id=str(case["case_id"]),
        sex=str(raw["sex"]),
        age_years=float(raw["age_years"]),
        height_cm=height_cm,
        weight_kg=weight_kg,
        bmi=bmi,
        liver_morphology=morphology,
        evidence_types={
            "phenotype": coverage_label,
            "morphology": coverage_label,
        },
    )


def _fit_liver(
    patient: PatientSampleV2,
    profile: PopulationProfileV2,
    grid: GridSpecV2,
    seeds: SeedBundle,
    *,
    max_attempts: int = 16,
) -> tuple[LiverGeometryV2, int]:
    target = sample_liver_target(
        patient,
        profile,
        np.random.default_rng(seeds.liver),
    )
    failures: list[str] = []
    for attempt in range(1, max_attempts + 1):
        shape_seed = (seeds.liver - 1 + attempt - 1) % _MAX_INT63 + 1
        try:
            return fit_liver_geometry(target, grid, shape_seed=shape_seed), attempt
        except LiverShapeRejectedError as exc:
            failures.append(",".join(exc.failed_gates))
    raise RuntimeError(
        f"{patient.case_id}: liver fit exhausted {max_attempts} attempts; "
        f"failures={failures}"
    )


def _tumor_case_target(
    case: Mapping[str, object],
    profile: PopulationProfileV2,
    seeds: SeedBundle,
    coverage_label: str,
) -> TumorCaseTargetV2:
    raw_lesions = case.get("lesions")
    if not isinstance(raw_lesions, list) or not raw_lesions:
        raise ValueError("pilot case requires a non-empty lesions list")
    rng = np.random.default_rng(seeds.tumor)
    targets = []
    for index, raw in enumerate(raw_lesions, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError("pilot lesion entries must be objects")
        axis = raw["axis_ratios"]
        if not isinstance(axis, Sequence) or len(axis) != 2:
            raise ValueError("pilot axis_ratios must contain two values")
        diameter = float(raw["dmax_mm"])
        targets.append(
            TumorTargetV2(
                lesion_id=f"{case['case_id']}_lesion_{index:02d}",
                dmax_mm=diameter,
                axis_ratios=(float(axis[0]), float(axis[1])),
                lobe=str(raw["lobe"]),
                morphology=str(raw["morphology"]),
                orientation_deg_zyx=tuple(
                    float(value) for value in rng.uniform(-20.0, 20.0, size=3)
                ),
                subcapsular=bool(raw["subcapsular"]),
                primitive_count=int(raw["primitive_count"]),
                target_rank=index,
                count_bin="1" if len(raw_lesions) == 1 else "2-5",
                dmax_bin="10-<80_mm" if diameter < 80.0 else "80-200_mm",
                within_bin_assumption=False,
                evidence_types={
                    "diameter": coverage_label,
                    "morphology": coverage_label,
                    "orientation": "engineering_seeded",
                },
            )
        )
    lobes = {target.lobe for target in targets}
    geometry_model = profile.value("tumor_geometry_model")
    if not isinstance(geometry_model, Mapping):
        raise ValueError("tumor_geometry_model must be an object")
    maximum_diameter = max(target.dmax_mm for target in targets)
    return TumorCaseTargetV2(
        case_id=str(case["case_id"]),
        strata=TumorStrataV2(
            count_bin="1" if len(targets) == 1 else "2-5",
            dmax_bin="10-<80_mm" if maximum_diameter < 80.0 else "80-200_mm",
            lobe_extent="bilobar" if len(lobes) > 1 else "unilobar",
        ),
        targets=tuple(targets),
        burden_fraction_max=float(profile.value("tumor_burden_fraction_max")),
        dmax_tolerance_voxels=float(geometry_model["dmax_tolerance_voxels"]),
        placement_attempts_per_lesion=int(geometry_model["placement_attempts_per_lesion"]),
        instance_gap_mm=float(geometry_model["instance_gap_mm"]),
        subcapsular_clearance_max_mm=float(
            geometry_model["subcapsular_clearance_max_mm"]
        ),
        sampling_attempts=1,
        evidence_types={"case_target": coverage_label},
    )


def _activity_target(
    case: Mapping[str, object],
    tumors: TumorGeometryV2,
    coverage_label: str,
) -> ActivityTargetV2:
    tnr = float(case["tnr_mean"])
    heterogeneous = bool(case["heterogeneous"])
    instance_ids = tuple(metric.instance_id for metric in tumors.lesion_metrics)
    return ActivityTargetV2(
        injection_territory=str(case["injection_territory"]),
        activity_pattern=str(case["activity_pattern"]),
        tnr_mean=tnr,
        heterogeneous=heterogeneous,
        mismatch_challenge=bool(case["mismatch_challenge"]),
        sector_proxy_label=(
            None
            if case.get("sector_proxy_label") is None
            else int(case["sector_proxy_label"])
        ),
        lesion_tnr_means={instance_id: tnr for instance_id in instance_ids},
        lesion_heterogeneous={
            instance_id: heterogeneous for instance_id in instance_ids
        },
        within_patient_correlation_assumption=(
            "task12_fixed_case_mean_shared_across_lesions"
        ),
        evidence_types={
            "tnr_mean": coverage_label,
            "heterogeneous": coverage_label,
            "injection_territory": coverage_label,
            "activity_pattern": "literature_anchored_population",
        },
    )


def prepare_pilot_case(
    case: Mapping[str, object],
    profile: PopulationProfileV2,
    grid: GridSpecV2,
    *,
    global_seed: int,
    base_histories: int,
    work_dir: Path,
    coverage_label: str = "task12_fixed_smoke_coverage",
) -> PreparedPilotCaseV2:
    """Create one complete phantom and exact SIMIND inputs, but do not run SIMIND."""

    case_id = str(case["case_id"])
    seeds = SeedBundle.from_case(global_seed, case_id)
    patient = _fixed_patient(case, coverage_label)
    liver, fit_attempt = _fit_liver(patient, profile, grid, seeds)
    tumor_target = _tumor_case_target(case, profile, seeds, coverage_label)
    tumors = place_and_rasterize_tumors(tumor_target, liver, grid)
    activity_target = _activity_target(case, tumors, coverage_label)
    activity = generate_activity_field(
        patient,
        liver,
        tumors,
        activity_target,
        profile,
        np.random.default_rng(seeds.activity),
    )
    anatomy = build_torso_anatomy_v2(liver, grid, patient)
    mu_true, mu_input, attenuation_metadata = generate_attenuation_maps(
        anatomy.anatomy,
        profile,
        np.random.default_rng(seeds.mu),
    )

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=False)
    stem = work_dir / case_id
    source = write_voxel_source(
        activity.activity_probability,
        stem,
        base_histories=base_histories,
    )
    density = write_attenuation_map_v2(
        mu_true,
        stem,
        semantic_key="mu_true_140kev",
    )
    source_weights = np.fromfile(source.path, dtype="<f4").reshape(grid.shape).copy()
    arrays = {
        "activity_relative": np.asarray(activity.activity_relative, dtype=np.float32),
        "activity_probability": np.asarray(activity.activity_probability, dtype=np.float32),
        "simind_source_weights": np.asarray(source_weights, dtype=np.float32),
        "mu_true_140kev": np.asarray(mu_true, dtype=np.float32),
        "mu_input_140kev": np.asarray(mu_input, dtype=np.float32),
        "body_mask": np.asarray(anatomy.anatomy.body_mask, dtype=np.uint8),
        "liver_mask": np.asarray(liver.mask, dtype=np.uint8),
        "liver_region_proxy": np.asarray(liver.region_labels, dtype=np.uint8),
        "tumor_instance_mask": np.asarray(tumors.instance_mask, dtype=np.uint16),
        "tumor_union_mask": np.asarray(tumors.instance_mask > 0, dtype=np.uint8),
        "perfusion_mask": np.asarray(activity.perfusion_mask, dtype=np.uint8),
    }
    return PreparedPilotCaseV2(
        case_id=case_id,
        patient=patient,
        seeds=seeds,
        liver=liver,
        liver_fit_attempt=fit_attempt,
        tumor_target=tumor_target,
        tumors=tumors,
        activity_target=activity_target,
        activity=activity,
        anatomy=anatomy,
        attenuation_metadata=attenuation_metadata,
        base_histories_per_projection=base_histories,
        arrays=arrays,
        source_bin=source.path,
        density_bin=density.path,
    )


def _normalized_liver_coordinate(
    center_world_mm: Sequence[float],
    liver_mask: np.ndarray,
    affine_4x4: np.ndarray,
) -> tuple[tuple[float, float, float], tuple[int, int, int]]:
    inverse = np.linalg.inv(np.asarray(affine_4x4, dtype=np.float64))
    homogeneous = np.append(np.asarray(center_world_mm, dtype=np.float64), 1.0)
    index = (inverse @ homogeneous)[:3]
    rounded = np.rint(index).astype(int)
    indices = np.argwhere(liver_mask)
    lower = indices.min(axis=0).astype(np.float64)
    upper = indices.max(axis=0).astype(np.float64)
    denominator = np.maximum(upper - lower, 1.0)
    normalized = np.clip((index - lower) / denominator, 0.0, 1.0)
    return tuple(float(value) for value in normalized), tuple(int(value) for value in rounded)


def _lesion_documents(prepared: PreparedPilotCaseV2) -> list[dict[str, object]]:
    placement_by_id = {
        placement.instance_id: placement for placement in prepared.tumors.placements
    }
    activity_by_id = {
        metric.instance_id: metric for metric in prepared.activity.lesion_metrics
    }
    documents = []
    for metric in prepared.tumors.lesion_metrics:
        placement = placement_by_id[metric.instance_id]
        activity = activity_by_id[metric.instance_id]
        normalized, index = _normalized_liver_coordinate(
            placement.center_world_mm,
            prepared.liver.mask,
            prepared.liver.affine_4x4,
        )
        region = int(prepared.liver.region_labels[index])
        if region not in {1, 2, 3, 4, 5}:
            raise RuntimeError("tumor center is not in a liver region proxy")
        documents.append(
            {
                "instance_id": metric.instance_id,
                "center_world_mm": list(placement.center_world_mm),
                "normalized_liver_coordinate_zyx": list(normalized),
                "liver_region_proxy": region,
                "capsule_clearance_mm": placement.capsule_clearance_mm,
                "recist_3d_mm": metric.recist_3d_mm,
                "principal_axes_mm": list(metric.principal_axes_mm),
                "equivalent_diameter_mm": metric.equivalent_diameter_mm,
                "volume_ml": metric.volume_ml,
                "sphericity": metric.sphericity,
                "morphology": placement.target.morphology,
                "necrotic_fraction": activity.necrotic_fraction,
                "tnr_mean": activity.actual_tnr_mean,
                "tnr_max": activity.actual_tnr_max,
            }
        )
    return documents


def _simind_version(res_path: Path) -> str:
    text = Path(res_path).read_text(encoding="utf-8", errors="replace")
    match = _SIMIND_VERSION.search(text)
    if match is None:
        raise RuntimeError("SIMIND version could not be parsed from .res")
    return f"SIMIND V{match.group(1)}"


def _path_length_document(paths: PathLengthMetricsV2) -> dict[str, object]:
    """Convert immutable measurement tuples to the JSON payload contract.

    ``dataclasses.asdict`` intentionally preserves tuple containers, while the
    V2 case payload requires one JSON-array record per view.  Normalize here,
    before the strict writer validates the in-memory payload.
    """

    return {
        "angles_deg": [float(value) for value in paths.angles_deg],
        "body": [asdict(value) for value in paths.body],
        "liver": [asdict(value) for value in paths.liver],
        "support_definition": paths.support_definition,
    }


def _tumor_perfusion_fractions(
    tumor_union: np.ndarray,
    perfusion: np.ndarray,
) -> tuple[float, float]:
    """Return stable tumor/perfusion overlap fractions, including controls."""

    tumor = np.asarray(tumor_union, dtype=bool)
    perfused = np.asarray(perfusion, dtype=bool)
    if tumor.shape != perfused.shape:
        raise ValueError("tumor and perfusion masks must have the same shape")
    intersection = int(np.count_nonzero(tumor & perfused))
    tumor_voxels = int(np.count_nonzero(tumor))
    perfusion_voxels = int(np.count_nonzero(perfused))
    tumor_coverage = intersection / tumor_voxels if tumor_voxels else 1.0
    tumor_fraction_perfused = (
        intersection / perfusion_voxels if perfusion_voxels else 0.0
    )
    return float(tumor_coverage), float(tumor_fraction_perfused)


def build_completed_metadata(
    prepared: PreparedPilotCaseV2,
    *,
    profile_path: Path,
    scanner_path: Path,
    evidence_registry_path: Path,
    simind_ini_path: Path,
    scanner: PopulationProfileV2,
    result: SimindRunResult,
    runtime_binding: Mapping[str, object],
) -> dict[str, object]:
    """Join actual geometry/activity with audited SIMIND bytes into strict metadata."""

    if not result.success or result.final_dir is None or result.exit_code != 0:
        raise RuntimeError(f"{prepared.case_id}: SIMIND did not complete: {result.error}")
    final_dir = Path(result.final_dir)
    output_stem = final_dir / prepared.case_id
    audit = audit_simind_completion(
        output_stem,
        expected_shape=result.expected_shape,
        exit_code=result.exit_code,
    )
    provenance_path = final_dir / "run_provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    if not isinstance(provenance, dict) or provenance.get("status") != "complete":
        raise RuntimeError("SIMIND provenance is not a completed V2 run")
    projections = np.memmap(
        output_stem.with_suffix(".a00"),
        dtype="<f4",
        mode="r",
        shape=result.expected_shape,
    )
    per_view = np.asarray(
        projections.sum(axis=(1, 2), dtype=np.float64), dtype=np.float64
    )
    del projections

    tumor_union = prepared.tumors.instance_mask > 0
    perfusion = prepared.activity.perfusion_mask > 0
    tumor_coverage, tumor_fraction_perfused = _tumor_perfusion_fractions(
        tumor_union,
        perfusion,
    )
    paths = measure_path_lengths(
        prepared.anatomy.anatomy.body_mask,
        prepared.liver.mask,
        prepared.liver.affine_4x4,
        views=60,
        starting_angle_deg=(
            FROZEN_PROJECTION_COORDINATES_V1.projector_starting_angle_deg
        ),
        rotation_direction=FROZEN_PROJECTION_COORDINATES_V1.rotation_direction,
    )
    seeds = prepared.seeds
    geometry = prepared.tumor_target
    anatomy_qc = asdict(prepared.anatomy.metadata.qc)
    if not anatomy_qc["passed"]:
        raise RuntimeError("torso anatomy QC did not pass")

    return {
        "seeds": {"global_seed": seeds.global_seed, **seeds.child_seeds},
        "config_hashes": {
            "evidence_registry_sha256": sha256_file(evidence_registry_path),
            "population_config_sha256": sha256_file(profile_path),
            "scanner_config_sha256": sha256_file(scanner_path),
            "simind_ini_sha256": sha256_file(simind_ini_path),
        },
        "patient": {
            "sex": prepared.patient.sex,
            "age_years": prepared.patient.age_years,
            "height_cm": prepared.patient.height_cm,
            "weight_kg": prepared.patient.weight_kg,
            "bmi": prepared.patient.bmi,
            "liver_morphology": prepared.patient.liver_morphology,
            "evidence_types": dict(prepared.patient.evidence_types),
        },
        "target_metrics": {
            "liver": dict(prepared.liver.target_metrics),
            "tumors": {
                "count_bin": geometry.strata.count_bin,
                "dmax_bin": geometry.strata.dmax_bin,
                "lobe_extent": geometry.strata.lobe_extent,
            },
        },
        "actual_metrics": {
            "liver": dict(prepared.liver.actual_metrics),
            "path_lengths": _path_length_document(paths),
            "tumors": {
                "count_bin": geometry.strata.count_bin,
                "realized_count": prepared.tumors.realized_count,
                "lobe_extent": prepared.tumors.realized_lobe_extent,
                "tumor_union_fraction_liver": prepared.tumors.tumor_to_liver_fraction,
                "tumor_union_fraction_perfused": tumor_fraction_perfused,
                "lesions": _lesion_documents(prepared),
            },
        },
        "activity": {
            "injection_territory": prepared.activity.injection_territory,
            "activity_pattern": prepared.activity.activity_pattern,
            "perfused_volume_ml": prepared.activity.perfused_volume_ml,
            "injection_tumor_coverage_fraction": tumor_coverage,
            "tumor_volume_fraction_perfused": tumor_fraction_perfused,
            "mismatch_challenge": prepared.activity.mismatch_challenge,
        },
        "spatial": {
            "affine_4x4": prepared.liver.affine_4x4.tolist(),
            "world_origin_mm": prepared.liver.affine_4x4[:3, 3].tolist(),
            "orientation_code": "SAR",
            "axis_order": "ZYX",
            "reference_phase": "end_expiration",
            "dvf_convention": "ref_to_phase",
            "dvf_units": "mm",
        },
        "acquisition": {
            "matrix": list(scanner.value("matrix")),
            "voxel_size_mm": float(scanner.value("voxel_size_mm")),
            "views": int(scanner.value("views")),
            "starting_angle_deg": float(scanner.value("starting_angle_deg")),
            "rotation_direction": str(scanner.value("rotation_direction")),
            "orbit_cm": float(scanner.value("orbit_cm")),
            "energy_window_kev": list(scanner.value("energy_window_kev")),
            "projection_coordinates": FROZEN_PROJECTION_COORDINATES_V1.to_dict(),
        },
        "physics": {
            "base_histories_per_projection": prepared.base_histories_per_projection,
            "activity_mbq": float(scanner.value("activity_mbq")),
            "time_per_projection_s": float(scanner.value("time_per_projection_s")),
            "smc_index25": float(scanner.value("smc_index25")),
            "nn_multiplier": int(provenance["nn_multiplier"]),
            "rr_seed": int(provenance["rr_seed"]),
            "hepatic_only": True,
            "lung_shunt_fraction": 0,
            "extrahepatic_uptake": False,
        },
        "simulation": {
            "status": "complete",
            "exit_code": result.exit_code,
            "command": list(provenance["command"]),
            "simind_version": _simind_version(output_stem.with_suffix(".res")),
            "binary_sha256": str(provenance["binary_sha256"]),
            "smc_snapshot_sha256": str(provenance["smc"]["sha256"]),
            "simind_ini_snapshot_sha256": str(
                provenance["simind_ini"]["sha256"]
            ),
            "input_sha256": {
                "source": str(provenance["inputs"]["source_sha256"]),
                "density": str(provenance["inputs"]["density_sha256"]),
            },
            "output_sha256": dict(audit.sha256),
            "projection_stats": {
                "view_count": audit.view_count,
                "projection_weight_sum": audit.projection_sum,
                "projection_per_view_weight_sum": per_view.tolist(),
                "finite": audit.finite,
            },
            "completion_status": "complete",
        },
        "quality_control": {
            "status": "pass",
            "failed_gates": [],
            "liver_fit_attempt": prepared.liver_fit_attempt,
            "liver_shape_quality": prepared.liver.actual_metrics["shape_quality"],
            "torso_anatomy": anatomy_qc,
            "attenuation": asdict(prepared.attenuation_metadata),
            "runtime_binding": dict(runtime_binding),
            "complete_tumor_containment": bool(
                np.all(~tumor_union | prepared.liver.mask)
            ),
        },
    }


def simind_extra_artifacts(
    prepared: PreparedPilotCaseV2,
    result: SimindRunResult,
) -> dict[str, Path]:
    if not result.success or result.final_dir is None:
        raise RuntimeError("cannot collect artifacts from a failed SIMIND run")
    stem = Path(result.final_dir) / prepared.case_id
    return {
        "projection_a00": stem.with_suffix(".a00"),
        "projection_mhd": stem.with_suffix(".mhd"),
        "projection_res": stem.with_suffix(".res"),
        "projection_spe": stem.with_suffix(".spe"),
        "simind_run_provenance": Path(result.final_dir) / "run_provenance.json",
        "simind_source_bin": prepared.source_bin,
        "simind_density_bin": prepared.density_bin,
    }
