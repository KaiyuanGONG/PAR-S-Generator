"""Deterministic population-sampled preparation for formal PAR-S V2 cases."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Mapping

import numpy as np

from .activity_model_v2 import sample_activity_target
from .anatomy_v2 import build_torso_anatomy_v2
from .interfile_writer import write_attenuation_map_v2, write_voxel_source
from .liver_geometry import GridSpecV2
from .phantom_generator import PhantomConfig, PhantomGenerator
from .pilot_v2 import PreparedPilotCaseV2
from .provenance import sha256_file
from .reproducibility_v2 import array_manifest
from .schemas_v2 import PopulationProfileV2
from .seeds import SeedBundle


def prepare_population_case(
    case_id: str,
    profile: PopulationProfileV2,
    grid: GridSpecV2,
    *,
    global_seed: int,
    base_histories: int,
    work_dir: Path,
    max_liver_attempts: int = 16,
    max_tumor_attempts: int = 32,
    mismatch_challenge: bool = False,
) -> PreparedPilotCaseV2:
    """Sample and rasterize one full population case before SIMIND execution."""

    seeds = SeedBundle.from_case(global_seed, case_id)
    generator = PhantomGenerator(
        PhantomConfig(volume_shape=grid.shape, voxel_size_mm=grid.voxel_size_mm)
    )
    liver_case = generator.generate_liver_v2(
        profile,
        np.random.default_rng(seeds.patient),
        case_id=case_id,
        liver_seed=seeds.liver,
        max_shape_attempts=max_liver_attempts,
    )
    tumor_case = generator.generate_tumors_v2(
        liver_case.patient,
        liver_case.geometry,
        profile,
        np.random.default_rng(seeds.tumor),
        tumor_seed=seeds.tumor,
        max_target_attempts=max_tumor_attempts,
    )
    activity_rng = np.random.default_rng(seeds.activity)
    activity_target = sample_activity_target(
        liver_case.patient,
        liver_case.geometry,
        tumor_case.geometry,
        profile,
        activity_rng,
        mismatch_challenge=mismatch_challenge,
    )
    activity_case = generator.generate_activity_v2(
        liver_case.patient,
        liver_case.geometry,
        tumor_case.geometry,
        profile,
        activity_rng,
        target=activity_target,
    )
    anatomy = build_torso_anatomy_v2(
        liver_case.geometry,
        grid,
        liver_case.patient,
    )
    attenuation = generator.generate_attenuation_v2(
        anatomy.anatomy,
        profile,
        np.random.default_rng(seeds.mu),
    )

    destination = Path(work_dir)
    destination.mkdir(parents=True, exist_ok=False)
    stem = destination / case_id
    source = write_voxel_source(
        activity_case.field.activity_probability,
        stem,
        base_histories=base_histories,
    )
    density = write_attenuation_map_v2(
        attenuation.mu_true_140kev,
        stem,
        semantic_key="mu_true_140kev",
    )
    source_weights = np.fromfile(source.path, dtype="<f4").reshape(grid.shape).copy()
    arrays = {
        "activity_relative": np.asarray(
            activity_case.field.activity_relative, dtype=np.float32
        ),
        "activity_probability": np.asarray(
            activity_case.field.activity_probability, dtype=np.float32
        ),
        "simind_source_weights": np.asarray(source_weights, dtype=np.float32),
        "mu_true_140kev": np.asarray(
            attenuation.mu_true_140kev, dtype=np.float32
        ),
        "mu_input_140kev": np.asarray(
            attenuation.mu_input_140kev, dtype=np.float32
        ),
        "body_mask": np.asarray(anatomy.anatomy.body_mask, dtype=np.uint8),
        "liver_mask": np.asarray(liver_case.geometry.mask, dtype=np.uint8),
        "liver_region_proxy": np.asarray(
            liver_case.geometry.region_labels, dtype=np.uint8
        ),
        "tumor_instance_mask": np.asarray(
            tumor_case.geometry.instance_mask, dtype=np.uint16
        ),
        "tumor_union_mask": np.asarray(
            tumor_case.geometry.instance_mask > 0, dtype=np.uint8
        ),
        "perfusion_mask": np.asarray(
            activity_case.field.perfusion_mask, dtype=np.uint8
        ),
    }
    provenance = liver_case.sampling_provenance
    if provenance is None:
        raise RuntimeError(f"{case_id}: liver sampling provenance is missing")
    return PreparedPilotCaseV2(
        case_id=case_id,
        patient=liver_case.patient,
        seeds=seeds,
        liver=liver_case.geometry,
        liver_fit_attempt=provenance.accepted_attempt_index,
        tumor_target=tumor_case.target,
        tumors=tumor_case.geometry,
        activity_target=activity_case.target,
        activity=activity_case.field,
        anatomy=anatomy,
        attenuation_metadata=attenuation.degradation_metadata,
        base_histories_per_projection=base_histories,
        arrays=arrays,
        source_bin=source.path,
        density_bin=density.path,
    )


def summarize_prepared_population_case(
    prepared: PreparedPilotCaseV2,
) -> dict[str, object]:
    """Audit one prepared case and return its frozen pre-SIMIND record."""

    arrays = prepared.arrays
    tumor = np.asarray(arrays["tumor_union_mask"], dtype=bool)
    liver = np.asarray(arrays["liver_mask"], dtype=bool)
    perfusion = np.asarray(arrays["perfusion_mask"], dtype=bool)
    mu_true = np.asarray(arrays["mu_true_140kev"], dtype=np.float32)
    mu_input = np.asarray(arrays["mu_input_140kev"], dtype=np.float32)
    source = np.asarray(arrays["simind_source_weights"], dtype=np.float64)
    tumor_voxels = int(np.count_nonzero(tumor))
    coverage = (
        0.0
        if tumor_voxels <= 0
        else float(np.count_nonzero(tumor & perfusion) / tumor_voxels)
    )
    failures: list[str] = []
    if tumor_voxels <= 0:
        failures.append("tumor mask is empty")
    if np.any(tumor & ~liver):
        failures.append("tumor containment failed")
    if not np.isfinite(mu_true).all() or not np.isfinite(mu_input).all():
        failures.append("attenuation contains non-finite values")
    if np.any(mu_true < 0) or np.any(mu_input < 0):
        failures.append("attenuation contains negative values")
    if np.array_equal(mu_true, mu_input):
        failures.append("mu_true and mu_input are not separated")
    if not math.isclose(
        float(source.sum()),
        float(prepared.base_histories_per_projection),
        rel_tol=2e-6,
        abs_tol=0.1,
    ):
        failures.append("source history sum mismatch")
    if bool(coverage < 1.0) != bool(prepared.activity.mismatch_challenge):
        failures.append("perfusion mismatch semantics failed")

    lesion_documents = []
    activity_by_id = {
        item.instance_id: item for item in prepared.activity.lesion_metrics
    }
    for metric in prepared.tumors.lesion_metrics:
        activity = activity_by_id[metric.instance_id]
        lesion_documents.append(
            {
                "instance_id": metric.instance_id,
                "recist_3d_mm": metric.recist_3d_mm,
                "volume_ml": metric.volume_ml,
                "sphericity": metric.sphericity,
                "necrotic_fraction": activity.necrotic_fraction,
                "tnr_mean": activity.actual_tnr_mean,
            }
        )
    return {
        "case_id": prepared.case_id,
        "status": "pass" if not failures else "fail",
        "failures": failures,
        "seeds": prepared.seeds.to_dict(),
        "rr_seed": prepared.seeds.simind,
        "patient": {
            "sex": prepared.patient.sex,
            "age_years": prepared.patient.age_years,
            "height_cm": prepared.patient.height_cm,
            "weight_kg": prepared.patient.weight_kg,
            "bmi": prepared.patient.bmi,
            "liver_morphology": prepared.patient.liver_morphology,
        },
        "liver_fit_attempt": prepared.liver_fit_attempt,
        "liver_volume_ml": prepared.liver.actual_metrics["volume_ml"],
        "liver_extent_mm_zyx": prepared.liver.actual_metrics["extent_mm_zyx"],
        "tumor_strata": {
            "count_bin": prepared.tumor_target.strata.count_bin,
            "dmax_bin": prepared.tumor_target.strata.dmax_bin,
            "lobe_extent": prepared.tumor_target.strata.lobe_extent,
        },
        "realized_tumor_count": prepared.tumors.realized_count,
        "tumor_fraction_liver": prepared.tumors.tumor_to_liver_fraction,
        "lesions": lesion_documents,
        "injection_territory": prepared.activity.injection_territory,
        "mismatch_challenge": prepared.activity.mismatch_challenge,
        "injection_tumor_coverage_fraction": coverage,
        "source_weight_sum": float(source.sum()),
        "source_sha256": sha256_file(prepared.source_bin),
        "density_sha256": sha256_file(prepared.density_bin),
        "array_manifest": array_manifest(prepared.arrays),
        "mu_true_input_mean_absolute_difference": float(
            np.mean(
                np.abs(mu_true.astype(np.float64) - mu_input.astype(np.float64))
            )
        ),
    }


def population_coverage(summary: Mapping[str, object]) -> tuple[str, ...]:
    """Return compact categorical coverage labels for cohort-level gates."""

    patient = summary["patient"]
    strata = summary["tumor_strata"]
    if not isinstance(patient, Mapping) or not isinstance(strata, Mapping):
        raise TypeError("prepared summary is malformed")
    return (
        f"sex:{patient['sex']}",
        f"morphology:{patient['liver_morphology']}",
        f"count:{strata['count_bin']}",
        f"dmax:{strata['dmax_bin']}",
        f"lobe:{strata['lobe_extent']}",
        f"territory:{summary['injection_territory']}",
        f"mismatch:{str(bool(summary['mismatch_challenge'])).lower()}",
    )
