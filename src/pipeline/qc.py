"""Quality-control primitives for phantom and projection artifacts.

All functions are pure/read-only except for callers choosing to persist the
returned dictionaries.  The canonical projection orientation is the one
validated for newly generated SIMIND data and is recorded in every QC result.
The historical PAR-S_2 transform remains a separate legacy contract.
"""

from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path
from typing import Any

import numpy as np
from scipy.ndimage import binary_erosion, distance_transform_edt

from pipeline.contracts import CANONICAL_PROJECTION_TRANSFORM, sha256_file


DEFAULT_PROJECTION_SHAPE = (60, 128, 128)


def _sha256_array(array: np.ndarray) -> str:
    values = np.ascontiguousarray(array)
    digest = hashlib.sha256()
    digest.update(str(values.dtype).encode("ascii"))
    digest.update(json.dumps(list(values.shape), separators=(",", ":")).encode("ascii"))
    digest.update(values.tobytes(order="C"))
    return digest.hexdigest()


def load_projection(
    path: Path,
    shape: tuple[int, int, int] = DEFAULT_PROJECTION_SHAPE,
    *,
    canonical: bool = True,
) -> np.ndarray:
    """Load one SIMIND ``.a00`` float32 projection with an explicit contract."""
    path = Path(path)
    expected_bytes = int(np.prod(shape)) * np.dtype(np.float32).itemsize
    actual_bytes = path.stat().st_size
    if actual_bytes != expected_bytes:
        raise ValueError(
            f"{path.name}: expected {expected_bytes} bytes for float32 {shape}, "
            f"found {actual_bytes}"
        )
    raw = np.fromfile(path, dtype=np.float32).reshape(shape)
    return raw[:, ::-1, :] if canonical else raw


def _completion_marker(res_path: Path) -> bool:
    if not res_path.exists():
        return False
    text = res_path.read_text(encoding="utf-8", errors="replace")
    return "Simulation stopped.:" in text or "Simulation stopped" in text


def _parse_res_effective(text: str) -> dict[str, Any]:
    """Extract a small semantic snapshot of values SIMIND actually reported.

    The report remains the source of truth.  Missing labels stay absent rather
    than being back-filled from the requested SMC configuration.
    """
    labels = {
        "photon_energy_kev": r"PhotonEnergy\.*:\s*([-+0-9.Ee]+)",
        "upper_energy_window_kev": r"UpperEneWindowTresh\.*:\s*([-+0-9.Ee]+)",
        "lower_energy_window_kev": r"LowerEneWindowTresh\.*:\s*([-+0-9.Ee]+)",
        "activity_time_value": r"Activity\.*:\s*([-+0-9.Ee]+)",
        "photons_per_projection": r"PhotonsPerProj\.*:\s*([-+0-9.Ee]+)",
        "input_pixel_size_i_cm": r"PixelSize\s+I\.*:\s*([-+0-9.Ee]+)",
        "input_pixel_size_j_cm": r"PixelSize\s+J\.*:\s*([-+0-9.Ee]+)",
        "nn_scaling_factor": r"NN ScalingFactor\.*:\s*([-+0-9.Ee]+)",
        "detector_matrix_i": r"Number detectors\s+I:\s*([-+0-9.Ee]+)",
        "detector_matrix_j": r"Number Detectors\s+J:\s*([-+0-9.Ee]+)",
        "detector_pitch_cm": r"Anode element pitch:\s*([-+0-9.Ee]+)",
        "projection_count": r"Nr of Projections\.*:\s*([-+0-9.Ee]+)",
        "rotation_step_deg": r"RotationAngle\.*:\s*([-+0-9.Ee]+)",
        "sensitivity_cps_per_mbq": r"Sensitivity Cps/MBq:\s*([-+0-9.Ee]+)",
    }
    effective: dict[str, Any] = {"source": "SIMIND .res"}
    for key, pattern in labels.items():
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        value = float(match.group(1))
        effective[key] = int(value) if key in {
            "photons_per_projection", "detector_matrix_i", "detector_matrix_j", "projection_count"
        } else value
    command = re.search(r"(?im)^\s*Command:\s*(.+?)\s*$", text)
    if command:
        effective["command"] = command.group(1)
    return effective


def validate_projection_artifacts(
    a00_path: Path,
    *,
    shape: tuple[int, int, int] = DEFAULT_PROJECTION_SHAPE,
    require_res: bool = True,
    require_mhd: bool = False,
    expected_command_tokens: tuple[str, ...] = (),
    res_path: Path | None = None,
    mhd_path: Path | None = None,
) -> dict[str, Any]:
    """Return strong, machine-readable completion and numerical QC evidence."""
    a00_path = Path(a00_path)
    res_path = Path(res_path) if res_path is not None else a00_path.with_suffix(".res")
    mhd_path = Path(mhd_path) if mhd_path is not None else a00_path.with_suffix(".mhd")
    failures: list[str] = []
    projection: np.ndarray | None = None

    if not a00_path.exists():
        failures.append("missing_a00")
    else:
        try:
            projection = load_projection(a00_path, shape=shape, canonical=True)
        except (OSError, ValueError) as exc:
            failures.append(f"invalid_a00:{exc}")

    marker = _completion_marker(res_path)
    res_text = res_path.read_text(encoding="utf-8", errors="replace") if res_path.exists() else ""
    res_effective = _parse_res_effective(res_text) if res_text else {"source": "SIMIND .res", "available": False}
    if require_res and not res_path.exists():
        failures.append("missing_res")
    elif require_res and not marker:
        failures.append("missing_simulation_stopped_marker")
    missing_tokens = [token for token in expected_command_tokens if token not in res_text]
    if missing_tokens:
        failures.append("res_command_mismatch:" + ",".join(missing_tokens))

    mhd_evidence: dict[str, Any] = {"exists": mhd_path.exists()}
    if require_mhd and not mhd_path.exists():
        failures.append("missing_mhd")
    if mhd_path.exists():
        mhd_text = mhd_path.read_text(encoding="utf-8", errors="replace")
        fields = {}
        for line in mhd_text.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                fields[key.strip()] = value.strip()
        try:
            dimensions = [int(value) for value in fields.get("DimSize", "").split()]
        except ValueError:
            dimensions = []
        expected_dimensions = [shape[2], shape[1], shape[0]]
        if dimensions != expected_dimensions:
            failures.append(f"mhd_dim_mismatch:{dimensions}")
        if fields.get("ElementType") != "MET_FLOAT":
            failures.append(f"mhd_element_type:{fields.get('ElementType')}")
        mhd_evidence = {
            "exists": True,
            "dim_size": dimensions,
            "element_type": fields.get("ElementType"),
            "element_data_file": fields.get("ElementDataFile"),
        }

    metrics: dict[str, Any] = {}
    if projection is not None:
        finite = bool(np.isfinite(projection).all())
        nonnegative = bool(np.all(projection >= 0))
        if not finite:
            failures.append("nonfinite_projection")
        if not nonnegative:
            failures.append("negative_projection")
        positive = projection[projection > 0]
        row_col_support = np.any(projection > 0, axis=0)
        view_sums = np.sum(projection, axis=(1, 2), dtype=np.float64)
        view_sum_mean = float(np.mean(view_sums, dtype=np.float64))
        metrics = {
            "shape": list(projection.shape),
            "dtype": str(projection.dtype),
            "sum": float(np.sum(projection, dtype=np.float64)),
            "mean": float(np.mean(projection, dtype=np.float64)),
            "max": float(np.max(projection)),
            "nonzero_fraction": float(np.count_nonzero(projection) / projection.size),
            "noninteger_positive_fraction": (
                float(np.mean(positive != np.floor(positive))) if positive.size else 0.0
            ),
            "view_sum_min": float(np.min(view_sums)),
            "view_sum_median": float(np.median(view_sums)),
            "view_sum_max": float(np.max(view_sums)),
            "angular_cv": (
                float(np.std(view_sums, dtype=np.float64) / view_sum_mean)
                if view_sum_mean > 0.0
                else 0.0
            ),
            "support_rows": np.flatnonzero(np.any(row_col_support, axis=1)).tolist(),
            "support_cols": np.flatnonzero(np.any(row_col_support, axis=0)).tolist(),
        }

    checksums = {}
    for label, artifact in (("a00", a00_path), ("res", res_path), ("mhd", mhd_path)):
        if artifact.exists():
            checksums[label] = sha256_file(artifact)

    return {
        "status": "passed" if not failures else "failed",
        "failures": failures,
        "a00": str(a00_path.resolve()),
        "res": str(res_path.resolve()),
        "res_completion_marker": marker,
        "res_expected_command_tokens": list(expected_command_tokens),
        "res_command_tokens_matched": not missing_tokens,
        "res_effective": res_effective,
        "mhd": mhd_evidence,
        "canonical_transform": CANONICAL_PROJECTION_TRANSFORM,
        "metrics": metrics,
        "sha256": checksums,
    }


def _validate_v2_case_metadata(
    v2: dict[str, Any],
    *,
    liver: np.ndarray,
    left: np.ndarray,
    mu_map: np.ndarray,
    voxel_mm: float,
) -> tuple[dict[str, Any], list[str]]:
    failures: list[str] = []
    profile = v2.get("profile", {})
    patient = v2.get("patient", {})
    liver_record = v2.get("liver", {})
    target = liver_record.get("target", {})
    actual = liver_record.get("actual", {})
    shape_quality = actual.get("shape_quality", {})
    torso_qc = v2.get("torso", {}).get("qc", {})
    attenuation = v2.get("attenuation", {})
    contracts = v2.get("contracts", {})
    seeds = v2.get("seeds", {})
    child_seeds = seeds.get("child_seeds", {})

    if v2.get("schema_version") != "pars_hybrid_v2_master_gate_a_v1":
        failures.append("v2_schema_version")
    if profile.get("role") != "population" or profile.get("population_claim") is not True:
        failures.append("v2_population_profile_contract")
    for key in ("sha256", "evidence_registry_sha256"):
        value = profile.get(key)
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            failures.append(f"v2_profile_{key}")
    morphology = patient.get("liver_morphology")
    if morphology not in {"normal", "cirrhotic"}:
        failures.append("v2_liver_morphology")
    if shape_quality.get("status") != "pass" or not shape_quality.get("gates"):
        failures.append("v2_liver_shape_quality")
    elif not all(bool(value) for value in shape_quality["gates"].values()):
        failures.append("v2_liver_shape_gate")
    if torso_qc.get("passed") is not True or torso_qc.get("failed_gates"):
        failures.append("v2_torso_qc")
    if attenuation.get("physical_map_key") != "mu_true_140kev":
        failures.append("v2_physical_mu_key")
    if attenuation.get("master_npz_key") != "mu_map":
        failures.append("v2_master_mu_key")
    if attenuation.get("ct_like_map_saved_to_npz") is not False:
        failures.append("v2_ct_like_map_leaked_to_npz")
    if attenuation.get("mu_true_sha256") != _sha256_array(mu_map):
        failures.append("v2_mu_true_hash_mismatch")
    if contracts.get("tumor_generator_v2_imported") is not False:
        failures.append("v2_tumor_generator_exclusion")
    if contracts.get("lesion_generator") != "master_f423_physical_measured_strata_v1":
        failures.append("v2_master_lesion_contract")
    if set(child_seeds) != {"patient", "liver", "tumor", "activity", "mu", "simind"}:
        failures.append("v2_seed_tree")
    elif len({int(value) for value in child_seeds.values()}) != len(child_seeds):
        failures.append("v2_seed_tree_collision")

    liver_voxels = int(liver.sum())
    measured_volume_ml = float(liver_voxels * voxel_mm**3 / 1000.0)
    measured_left_fraction = float(left.sum() / max(liver_voxels, 1))
    target_volume_ml = float(target.get("volume_ml", np.nan))
    actual_volume_ml = float(actual.get("volume_ml", np.nan))
    target_left_fraction = float(target.get("left_fraction", np.nan))
    actual_left_fraction = float(actual.get("left_fraction", np.nan))
    volume_error_ml = actual_volume_ml - target_volume_ml
    left_error = actual_left_fraction - target_left_fraction
    if not np.isclose(actual_volume_ml, measured_volume_ml, rtol=0.0, atol=1e-9):
        failures.append("v2_actual_volume_not_from_saved_mask")
    if not np.isclose(actual_left_fraction, measured_left_fraction, rtol=0.0, atol=1e-12):
        failures.append("v2_actual_left_fraction_not_from_saved_masks")
    if not np.isfinite(volume_error_ml) or not np.isfinite(left_error):
        failures.append("v2_target_actual_metrics_missing")
    elif abs(left_error) > (1.0 / max(liver_voxels, 1)) + 1e-12:
        failures.append("v2_left_fraction_quantization")
    region_counts = actual.get("region_voxel_counts", {})
    if not isinstance(region_counts, dict) or sum(int(value) for value in region_counts.values()) != liver_voxels:
        failures.append("v2_region_proxy_coverage")

    compact = {
        "schema_version": v2.get("schema_version"),
        "profile_id": profile.get("profile_id"),
        "profile_sha256": profile.get("sha256"),
        "evidence_registry_id": profile.get("evidence_registry_id"),
        "evidence_registry_sha256": profile.get("evidence_registry_sha256"),
        "cirrhosis_prevalence": profile.get("cirrhosis_prevalence"),
        "liver_volume_range_ml": profile.get("liver_volume_range_ml"),
        "left_fraction_reference": profile.get("left_fraction_reference"),
        "morphology": morphology,
        "caudate_enabled": bool(target.get("caudate_enabled")),
        "target_volume_ml": target_volume_ml,
        "actual_volume_ml": actual_volume_ml,
        "volume_error_ml": volume_error_ml,
        "volume_relative_error": volume_error_ml / target_volume_ml if target_volume_ml else None,
        "target_left_fraction": target_left_fraction,
        "actual_left_fraction": actual_left_fraction,
        "left_fraction_error": left_error,
        "target_s1_3_to_s4_8_ratio": target.get("s1_3_to_s4_8_ratio"),
        "actual_s1_3_to_s4_8_ratio": actual.get("s1_3_to_s4_8_ratio"),
        "target_caudate_fraction": target.get("caudate_fraction"),
        "actual_caudate_fraction": actual.get("caudate_fraction"),
        "shape_quality_status": shape_quality.get("status"),
        "shape_gates": shape_quality.get("gates", {}),
        "torso_qc_passed": torso_qc.get("passed"),
        "accepted_shape_attempt": liver_record.get("sampling_provenance", {}).get("accepted_attempt_index"),
        "rejected_shape_attempt_count": len(liver_record.get("sampling_provenance", {}).get("rejected_attempts", [])),
        "case_seed": seeds.get("case_seed"),
        "child_seeds": child_seeds,
        "mu_true_sha256": attenuation.get("mu_true_sha256"),
        "mu_input_sha256": attenuation.get("mu_input_sha256"),
    }
    return compact, failures


def phantom_qc(npz_path: Path, meta_path: Path | None = None) -> dict[str, Any]:
    """Validate one saved phantom and derive geometry/activity evidence."""
    npz_path = Path(npz_path)
    meta_path = meta_path or npz_path.with_name(f"{npz_path.stem}_meta.json")
    failures: list[str] = []
    warnings: list[str] = []
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    voxel_mm = float(meta.get("voxel_size_mm", 4.42))

    required = {"activity", "mu_map", "liver_mask", "left_mask", "right_mask", "tumor_masks"}
    with np.load(npz_path) as payload:
        missing = sorted(required - set(payload.files))
        if missing:
            return {
                "status": "failed",
                "failures": [f"missing_arrays:{','.join(missing)}"],
                "warnings": [],
                "npz": str(npz_path.resolve()),
            }
        activity = np.asarray(payload["activity"])
        mu_map = np.asarray(payload["mu_map"])
        liver = np.asarray(payload["liver_mask"], dtype=bool)
        left = np.asarray(payload["left_mask"], dtype=bool)
        right = np.asarray(payload["right_mask"], dtype=bool)
        tumors = np.asarray(payload["tumor_masks"], dtype=bool)

    shape = activity.shape
    if activity.ndim != 3 or mu_map.shape != shape or liver.shape != shape:
        failures.append("incompatible_volume_shapes")
    if tumors.ndim != 4 or tuple(tumors.shape[1:]) != tuple(shape):
        failures.append("invalid_tumor_mask_shape")
    if activity.dtype != np.float32:
        warnings.append(f"activity_dtype:{activity.dtype}")
    if mu_map.dtype != np.float32:
        warnings.append(f"mu_map_dtype:{mu_map.dtype}")
    if not np.isfinite(activity).all() or np.any(activity < 0):
        failures.append("invalid_activity_values")
    if not np.isfinite(mu_map).all() or np.any(mu_map < 0):
        failures.append("invalid_mu_values")
    if np.any(left & right) or not np.array_equal(left | right, liver):
        failures.append("invalid_lobe_partition")

    tumor_records: list[dict[str, Any]] = []
    surface = liver & ~binary_erosion(liver, border_value=0)
    distance_to_surface = distance_transform_edt(~surface) * voxel_mm
    occupied = np.zeros(shape, dtype=bool)
    all_tumors = np.any(tumors, axis=0) if len(tumors) else np.zeros(shape, dtype=bool)
    normal_liver = liver & ~all_tumors
    liver_mean = float(activity[normal_liver].mean()) if np.any(normal_liver) else 0.0
    for index, tumor in enumerate(tumors):
        saved_tumor = (
            meta.get("tumors", [])[index]
            if index < len(meta.get("tumors", []))
            else {}
        )
        volume_vox = int(tumor.sum())
        overlap_vox = int((occupied & tumor).sum())
        outside_vox = int((tumor & ~liver).sum())
        occupied |= tumor
        volume_mm3 = volume_vox * voxel_mm**3
        effective_diameter = (6.0 * volume_mm3 / np.pi) ** (1.0 / 3.0) if volume_vox else 0.0
        tumor_mean = float(activity[tumor].mean()) if volume_vox else 0.0
        left_overlap = int((tumor & left).sum())
        right_overlap = int((tumor & right).sum())
        local_lobe = left if left_overlap >= right_overlap else right
        local_background = local_lobe & ~all_tumors
        local_mean = float(activity[local_background].mean()) if np.any(local_background) else 0.0
        tumor_records.append(
            {
                "index": index,
                "volume_vox": volume_vox,
                "effective_diameter_mm": float(effective_diameter),
                "outside_liver_vox": outside_vox,
                "overlap_previous_vox": overlap_vox,
                "surface_margin_mm": float(distance_to_surface[tumor].min()) if volume_vox else 0.0,
                "tnr_from_saved_activity": tumor_mean / liver_mean if liver_mean > 0 else None,
                "tnr_local_from_saved_activity": tumor_mean / local_mean if local_mean > 0 else None,
                "nominal_diameter_mm": saved_tumor.get("nominal_diameter_mm"),
                "target_contrast": saved_tumor.get("target_contrast"),
                "mode": saved_tumor.get("mode"),
                "placement_stratum": saved_tumor.get("placement_stratum"),
                "perfusion_region": saved_tumor.get("perfusion_region"),
                "sampled_size_bin_mm": saved_tumor.get("sampled_size_bin_mm"),
            }
        )
        if volume_vox == 0 or outside_vox or overlap_vox:
            failures.append(f"invalid_tumor_{index}")

    attenuation = meta.get("attenuation_contract", {})
    if not str(attenuation.get("status", "unrecorded")).startswith("verified"):
        warnings.append("attenuation_contract_not_verified_by_simind_ict")

    cantlie = meta.get("cantlie", {})
    if cantlie and cantlie.get("converged") is not True:
        failures.append("cantlie_not_converged")
    if cantlie.get("search", {}).get("expanded_beyond_initial_range"):
        warnings.append("cantlie_search_range_expanded")

    left_ratio = float(left.sum() / max(int(liver.sum()), 1))
    v2_qc = None
    if "v2" in meta:
        if not isinstance(meta["v2"], dict):
            failures.append("v2_metadata_not_object")
        else:
            v2_qc, v2_failures = _validate_v2_case_metadata(
                meta["v2"],
                liver=liver,
                left=left,
                mu_map=mu_map,
                voxel_mm=voxel_mm,
            )
            failures.extend(v2_failures)
    result = {
        "status": "passed" if not failures else "failed",
        "failures": sorted(set(failures)),
        "warnings": sorted(set(warnings)),
        "npz": str(npz_path.resolve()),
        "meta": str(Path(meta_path).resolve()) if Path(meta_path).exists() else None,
        "sha256": {
            "npz": sha256_file(npz_path),
            **({"meta": sha256_file(Path(meta_path))} if Path(meta_path).exists() else {}),
        },
        "metrics": {
            "shape": list(shape),
            "activity_dtype": str(activity.dtype),
            "mu_dtype": str(mu_map.dtype),
            "activity_sum": float(np.sum(activity, dtype=np.float64)),
            "mu_min": float(np.min(mu_map)),
            "mu_max": float(np.max(mu_map)),
            "liver_volume_ml": float(liver.sum() * voxel_mm**3 / 1000.0),
            "left_ratio": left_ratio,
            "n_tumors": int(len(tumors)),
            "tumors": tumor_records,
        },
        "attenuation_contract": attenuation or {"status": "unrecorded"},
        "cantlie": cantlie or {"status": "unrecorded"},
        "v2": v2_qc,
    }
    return result


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    array = np.asarray(values, dtype=np.float64)
    array = array[np.isfinite(array)]
    if not array.size:
        return {"count": 0, "min": None, "q25": None, "median": None, "q75": None, "max": None, "mean": None, "std": None}
    return {
        "count": int(array.size),
        "min": float(array.min()),
        "q25": float(np.quantile(array, 0.25)),
        "median": float(np.median(array)),
        "q75": float(np.quantile(array, 0.75)),
        "max": float(array.max()),
        "mean": float(array.mean()),
        "std": float(array.std(ddof=0)),
    }


def summarize_phantom_population(qc_records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate case-level QC without inventing clinical prevalence claims."""
    case_metrics = [record.get("metrics", {}) for record in qc_records]
    tumors = [
        tumor
        for metrics in case_metrics
        for tumor in metrics.get("tumors", [])
    ]
    diameters = [float(row["effective_diameter_mm"]) for row in tumors]
    nominal = [
        float(row["nominal_diameter_mm"])
        for row in tumors
        if row.get("nominal_diameter_mm") is not None
    ]
    diameter_ratios = [
        float(row["effective_diameter_mm"]) / float(row["nominal_diameter_mm"])
        for row in tumors
        if row.get("nominal_diameter_mm") not in {None, 0}
    ]
    diameter_bins = {
        "10_to_lt20_mm": sum(10 <= value < 20 for value in diameters),
        "20_to_lt40_mm": sum(20 <= value < 40 for value in diameters),
        "40_to_60_mm": sum(40 <= value <= 60 for value in diameters),
        "outside_10_to_60_mm": sum(not (10 <= value <= 60) for value in diameters),
    }
    placement_counts: dict[str, int] = {}
    mode_counts: dict[str, int] = {}
    sampled_bin_counts: dict[str, int] = {}
    perfusion_region_counts: dict[str, int] = {}
    for row in tumors:
        placement = str(row.get("placement_stratum") or "unrecorded")
        mode = str(row.get("mode") or "unrecorded")
        sampled_bin = row.get("sampled_size_bin_mm")
        sampled_key = (
            f"{float(sampled_bin[0]):g}_to_{float(sampled_bin[1]):g}_mm"
            if isinstance(sampled_bin, (list, tuple)) and len(sampled_bin) == 2
            else "unrecorded"
        )
        perfusion_region = str(row.get("perfusion_region") or "unrecorded")
        placement_counts[placement] = placement_counts.get(placement, 0) + 1
        mode_counts[mode] = mode_counts.get(mode, 0) + 1
        sampled_bin_counts[sampled_key] = sampled_bin_counts.get(sampled_key, 0) + 1
        perfusion_region_counts[perfusion_region] = perfusion_region_counts.get(perfusion_region, 0) + 1
    tumor_count_distribution: dict[str, int] = {}
    for metrics in case_metrics:
        key = str(int(metrics.get("n_tumors", 0)))
        tumor_count_distribution[key] = tumor_count_distribution.get(key, 0) + 1
    passed_cases = sum(record.get("status") == "passed" for record in qc_records)
    outside_voxels = sum(
        int(row.get("outside_liver_vox", 0)) for row in tumors
    )
    overlap_voxels = sum(
        int(row.get("overlap_previous_vox", 0)) for row in tumors
    )
    v2_rows = [
        record["v2"]
        for record in qc_records
        if isinstance(record.get("v2"), dict)
    ]
    morphology_counts: dict[str, int] = {}
    for row in v2_rows:
        morphology = str(row.get("morphology") or "unrecorded")
        morphology_counts[morphology] = morphology_counts.get(morphology, 0) + 1

    def v2_values(name: str, *, morphology: str | None = None) -> list[float]:
        return [
            float(row.get(name, np.nan))
            for row in v2_rows
            if morphology is None or row.get("morphology") == morphology
        ]

    v2_population = {
        "case_count": len(v2_rows),
        "profile_identities": sorted(
            {
                f"{row.get('profile_id')}@{row.get('profile_sha256')}"
                for row in v2_rows
            }
        ),
        "evidence_registry_identities": sorted(
            {
                f"{row.get('evidence_registry_id')}@{row.get('evidence_registry_sha256')}"
                for row in v2_rows
            }
        ),
        "profile_contracts": sorted(
            {
                json.dumps(
                    {
                        "cirrhosis_prevalence": row.get("cirrhosis_prevalence"),
                        "liver_volume_range_ml": row.get("liver_volume_range_ml"),
                        "left_fraction_reference": row.get("left_fraction_reference"),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                for row in v2_rows
            }
        ),
        "morphology_counts": morphology_counts,
        "caudate_enabled_count": sum(bool(row.get("caudate_enabled")) for row in v2_rows),
        "large_volume_above_legacy_1900_ml_count": sum(
            float(row.get("actual_volume_ml", 0.0)) > 1900.0 for row in v2_rows
        ),
        "target_volume_ml": _distribution(v2_values("target_volume_ml")),
        "actual_volume_ml": _distribution(v2_values("actual_volume_ml")),
        "volume_error_ml": _distribution(v2_values("volume_error_ml")),
        "volume_relative_error": _distribution(v2_values("volume_relative_error")),
        "target_left_fraction": _distribution(v2_values("target_left_fraction")),
        "actual_left_fraction": _distribution(v2_values("actual_left_fraction")),
        "left_fraction_error": _distribution(v2_values("left_fraction_error")),
        "target_s1_3_to_s4_8_ratio_by_morphology": {
            morphology: _distribution(
                v2_values("target_s1_3_to_s4_8_ratio", morphology=morphology)
            )
            for morphology in ("normal", "cirrhotic")
        },
        "actual_s1_3_to_s4_8_ratio_by_morphology": {
            morphology: _distribution(
                v2_values("actual_s1_3_to_s4_8_ratio", morphology=morphology)
            )
            for morphology in ("normal", "cirrhotic")
        },
        "target_caudate_fraction": _distribution(v2_values("target_caudate_fraction")),
        "actual_caudate_fraction": _distribution(v2_values("actual_caudate_fraction")),
        "accepted_shape_attempt": _distribution(v2_values("accepted_shape_attempt")),
        "rejected_shape_attempt_count": _distribution(v2_values("rejected_shape_attempt_count")),
        "all_shape_quality_passed": all(
            row.get("shape_quality_status") == "pass"
            and bool(row.get("shape_gates"))
            and all(bool(value) for value in row["shape_gates"].values())
            for row in v2_rows
        ),
        "all_torso_qc_passed": all(row.get("torso_qc_passed") is True for row in v2_rows),
        "child_seed_unique_counts": {
            namespace: len(
                {
                    int(row.get("child_seeds", {}).get(namespace, -1))
                    for row in v2_rows
                }
            )
            for namespace in ("patient", "liver", "tumor", "activity", "mu", "simind")
        },
    }
    return {
        "status": (
            "passed"
            if passed_cases == len(qc_records) and not outside_voxels and not overlap_voxels
            else "failed"
        ),
        "case_count": len(qc_records),
        "passed_case_count": passed_cases,
        "failed_case_count": len(qc_records) - passed_cases,
        "lesion_count": len(tumors),
        "containment_outside_voxels": outside_voxels,
        "overlap_voxels": overlap_voxels,
        "case_distributions": {
            "liver_volume_ml": _distribution(
                [float(row.get("liver_volume_ml", np.nan)) for row in case_metrics]
            ),
            "left_ratio": _distribution(
                [float(row.get("left_ratio", np.nan)) for row in case_metrics]
            ),
            "tumor_count": tumor_count_distribution,
            "activity_sum": _distribution(
                [float(row.get("activity_sum", np.nan)) for row in case_metrics]
            ),
        },
        "lesion_distributions": {
            "effective_diameter_mm": _distribution(diameters),
            "nominal_diameter_mm": _distribution(nominal),
            "effective_to_nominal_ratio": _distribution(diameter_ratios),
            "surface_margin_mm": _distribution(
                [float(row.get("surface_margin_mm", np.nan)) for row in tumors]
            ),
            "central_surface_margin_mm": _distribution(
                [
                    float(row.get("surface_margin_mm", np.nan))
                    for row in tumors
                    if row.get("placement_stratum") == "central"
                ]
            ),
            "tnr_from_saved_activity": _distribution(
                [
                    float(row.get("tnr_from_saved_activity", np.nan))
                    for row in tumors
                    if row.get("tnr_from_saved_activity") is not None
                ]
            ),
            "tnr_local_from_saved_activity": _distribution(
                [
                    float(row.get("tnr_local_from_saved_activity", np.nan))
                    for row in tumors
                    if row.get("tnr_local_from_saved_activity") is not None
                ]
            ),
            "target_contrast": _distribution(
                [
                    float(row.get("target_contrast", np.nan))
                    for row in tumors
                    if row.get("target_contrast") is not None
                ]
            ),
            "diameter_bins": diameter_bins,
            "sampled_size_bins": sampled_bin_counts,
            "placement_strata": placement_counts,
            "perfusion_regions": perfusion_region_counts,
            "morphology_modes": mode_counts,
        },
        "v2_population": v2_population,
        "claim_boundary": (
            "Generated-population QC for the current synthetic liver protocol; "
            "not a clinical prevalence distribution."
        ),
    }


def assess_stage3_phantom_population(
    summary: dict[str, Any],
    *,
    size_bins_mm: list[list[float]],
    size_probabilities: list[float],
    tumor_count_min: int,
    tumor_count_max: int,
    mode_probabilities: dict[str, float],
    target_left_ratio: float,
    target_contrast_range: tuple[float, float],
    central_margin_mm: float,
) -> dict[str, Any]:
    """Apply declared Stage-3 population gates to a 100-case phantom QC run.

    These are generator-contract checks, not tests of clinical prevalence.
    Proportion tolerances deliberately cover ordinary finite-sample variation
    while still detecting the large acceptance bias found in the legacy loop.
    """
    case_count = int(summary.get("case_count", 0))
    lesion_count = int(summary.get("lesion_count", 0))
    lesion = summary.get("lesion_distributions", {})
    case = summary.get("case_distributions", {})
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, **evidence: Any) -> None:
        checks.append({"name": name, "status": "passed" if passed else "failed", **evidence})

    add("case_count_100", case_count == 100, observed=case_count, expected=100)
    add(
        "all_case_qc_passed",
        int(summary.get("passed_case_count", 0)) == case_count,
        passed_case_count=int(summary.get("passed_case_count", 0)),
    )
    add(
        "lesion_containment_and_nonoverlap",
        int(summary.get("containment_outside_voxels", 0)) == 0
        and int(summary.get("overlap_voxels", 0)) == 0,
        outside_voxels=int(summary.get("containment_outside_voxels", 0)),
        overlap_voxels=int(summary.get("overlap_voxels", 0)),
    )

    liver_volume = case.get("liver_volume_ml", {})
    liver_min, liver_max = liver_volume.get("min"), liver_volume.get("max")
    add(
        "liver_volume_design_envelope_ml",
        liver_min is not None and liver_max is not None
        and float(liver_min) >= 900.0 and float(liver_max) <= 1900.0,
        observed_min=liver_min,
        observed_max=liver_max,
        envelope=[900.0, 1900.0],
    )
    left_ratio = case.get("left_ratio", {})
    left_min, left_max = left_ratio.get("min"), left_ratio.get("max")
    left_tolerance = 0.006
    add(
        "cantlie_left_ratio",
        left_min is not None and left_max is not None
        and float(left_min) >= target_left_ratio - left_tolerance
        and float(left_max) <= target_left_ratio + left_tolerance,
        observed_min=left_min,
        observed_max=left_max,
        target=target_left_ratio,
        tolerance=left_tolerance,
    )

    sampled_counts = lesion.get("sampled_size_bins", {})
    bin_rows = []
    bin_passed = lesion_count > 0 and len(size_bins_mm) == len(size_probabilities)
    for bounds, expected in zip(size_bins_mm, size_probabilities):
        key = f"{float(bounds[0]):g}_to_{float(bounds[1]):g}_mm"
        observed_count = int(sampled_counts.get(key, 0))
        observed = observed_count / lesion_count if lesion_count else 0.0
        tolerance = 0.08 if float(expected) <= 0.2 else 0.10
        passed = abs(observed - float(expected)) <= tolerance
        bin_passed = bin_passed and passed
        bin_rows.append(
            {
                "bin_mm": [float(bounds[0]), float(bounds[1])],
                "count": observed_count,
                "observed_fraction": observed,
                "expected_fraction": float(expected),
                "tolerance": tolerance,
                "status": "passed" if passed else "failed",
            }
        )
    add("sampled_size_strata", bin_passed, strata=bin_rows)
    add(
        "effective_diameters_match_declared_strata",
        int(lesion.get("diameter_bins", {}).get("outside_10_to_60_mm", -1)) == 0,
        observed=lesion.get("diameter_bins", {}),
    )

    mode_counts = lesion.get("morphology_modes", {})
    mode_rows = []
    mode_passed = lesion_count > 0
    for mode, expected in mode_probabilities.items():
        observed_count = int(mode_counts.get(mode, 0))
        observed = observed_count / lesion_count if lesion_count else 0.0
        passed = abs(observed - float(expected)) <= 0.10
        mode_passed = mode_passed and passed
        mode_rows.append(
            {
                "mode": mode,
                "count": observed_count,
                "observed_fraction": observed,
                "expected_fraction": float(expected),
                "tolerance": 0.10,
                "status": "passed" if passed else "failed",
            }
        )
    add("morphology_mode_distribution", mode_passed, modes=mode_rows)

    tumor_counts = case.get("tumor_count", {})
    count_values = list(range(int(tumor_count_min), int(tumor_count_max) + 1))
    expected_count_fraction = 1.0 / len(count_values)
    count_rows = []
    count_passed = bool(count_values) and case_count > 0
    for value in count_values:
        observed_count = int(tumor_counts.get(str(value), 0))
        observed = observed_count / case_count if case_count else 0.0
        passed = abs(observed - expected_count_fraction) <= 0.10
        count_passed = count_passed and passed
        count_rows.append(
            {
                "tumor_count": value,
                "case_count": observed_count,
                "observed_fraction": observed,
                "expected_fraction": expected_count_fraction,
                "tolerance": 0.10,
                "status": "passed" if passed else "failed",
            }
        )
    add("uniform_tumor_count_distribution", count_passed, counts=count_rows)

    target_contrast = lesion.get("target_contrast", {})
    contrast_min, contrast_max = target_contrast.get("min"), target_contrast.get("max")
    add(
        "sampled_target_contrast_range",
        contrast_min is not None and contrast_max is not None
        and float(contrast_min) >= float(target_contrast_range[0])
        and float(contrast_max) <= float(target_contrast_range[1]),
        observed_min=contrast_min,
        observed_max=contrast_max,
        configured_range=list(target_contrast_range),
    )
    placement = lesion.get("placement_strata", {})
    central_margin = lesion.get("central_surface_margin_mm", {})
    margin_min = central_margin.get("min")
    add(
        "central_lesion_surface_margin",
        margin_min is not None and float(margin_min) + 1e-6 >= central_margin_mm,
        observed_min_mm=margin_min,
        configured_min_mm=central_margin_mm,
    )
    fallback_count = int(placement.get("capacity_fallback_margin_relaxed", 0))
    fallback_fraction = fallback_count / lesion_count if lesion_count else 0.0
    add(
        "capacity_fallback_is_bounded_and_explicit",
        fallback_fraction <= 0.05,
        lesion_count=fallback_count,
        lesion_fraction=fallback_fraction,
        maximum_fraction=0.05,
        interpretation=(
            "Fallback lesions remain fully contained and non-overlapping but do not carry "
            "the configured central surface-margin guarantee."
        ),
    )

    return {
        "status": "passed" if all(row["status"] == "passed" for row in checks) else "failed",
        "enforced": True,
        "scope": (
            "100-case generated-population contract for the current liver and GE 870 CZT protocol; "
            "not a clinical prevalence claim"
        ),
        "checks": checks,
    }


def assess_gate_a_v2_population(
    summary: dict[str, Any],
    *,
    size_bins_mm: list[list[float]],
    size_probabilities: list[float],
    tumor_count_min: int,
    tumor_count_max: int,
    mode_probabilities: dict[str, float],
    target_contrast_range: tuple[float, float],
    central_margin_mm: float,
) -> dict[str, Any]:
    """Apply Gate A without substituting legacy liver thresholds for V2 semantics."""
    master = assess_stage3_phantom_population(
        summary,
        size_bins_mm=size_bins_mm,
        size_probabilities=size_probabilities,
        tumor_count_min=tumor_count_min,
        tumor_count_max=tumor_count_max,
        mode_probabilities=mode_probabilities,
        target_left_ratio=0.0,
        target_contrast_range=target_contrast_range,
        central_margin_mm=central_margin_mm,
    )
    excluded_legacy_anatomy_checks = {
        "liver_volume_design_envelope_ml",
        "cantlie_left_ratio",
    }
    checks = [
        row
        for row in master["checks"]
        if row["name"] not in excluded_legacy_anatomy_checks
    ]

    def add(name: str, passed: bool, **evidence: Any) -> None:
        checks.append({"name": name, "status": "passed" if passed else "failed", **evidence})

    case_count = int(summary.get("case_count", 0))
    v2 = summary.get("v2_population", {})
    v2_count = int(v2.get("case_count", 0))
    add(
        "v2_metadata_complete",
        case_count == 100 and v2_count == case_count,
        case_count=case_count,
        v2_case_count=v2_count,
    )
    profile_identities = v2.get("profile_identities", [])
    evidence_identities = v2.get("evidence_registry_identities", [])
    profile_contracts = v2.get("profile_contracts", [])
    add(
        "single_traceable_v2_profile",
        len(profile_identities) == 1
        and "None@" not in str(profile_identities[0])
        and len(evidence_identities) == 1
        and "None@" not in str(evidence_identities[0])
        and len(profile_contracts) == 1,
        profiles=profile_identities,
        evidence_registries=evidence_identities,
        profile_contracts=profile_contracts,
    )
    contract = json.loads(profile_contracts[0]) if len(profile_contracts) == 1 else {}
    volume_range = contract.get("liver_volume_range_ml", [])
    target_volume = v2.get("target_volume_ml", {})
    target_min, target_max = target_volume.get("min"), target_volume.get("max")
    volume_range_valid = (
        isinstance(volume_range, list)
        and len(volume_range) == 2
        and float(volume_range[0]) < float(volume_range[1])
    )
    add(
        "v2_target_volume_within_profile_range",
        volume_range_valid
        and target_min is not None
        and target_max is not None
        and float(target_min) >= float(volume_range[0])
        and float(target_max) <= float(volume_range[1]),
        observed_min_ml=target_min,
        observed_max_ml=target_max,
        profile_range_ml=volume_range,
    )
    add(
        "v2_large_volume_tail_exercised",
        int(v2.get("large_volume_above_legacy_1900_ml_count", 0)) > 0,
        comparator="frozen legacy design-envelope upper bound, not a medical threshold",
        observed_count=int(v2.get("large_volume_above_legacy_1900_ml_count", 0)),
        comparator_ml=1900.0,
    )
    morphology_counts = v2.get("morphology_counts", {})
    add(
        "v2_normal_and_cirrhotic_semantics_exercised",
        int(morphology_counts.get("normal", 0)) > 0
        and int(morphology_counts.get("cirrhotic", 0)) > 0
        and sum(int(value) for value in morphology_counts.values()) == case_count,
        observed=morphology_counts,
        configured_cirrhosis_prevalence=contract.get("cirrhosis_prevalence"),
        interpretation="traceability only; no fitted clinical-prevalence tolerance is asserted",
    )
    add(
        "v2_caudate_semantics_exercised",
        int(v2.get("caudate_enabled_count", 0)) > 0,
        caudate_enabled_count=int(v2.get("caudate_enabled_count", 0)),
    )
    target_left = v2.get("target_left_fraction", {})
    left_min, left_max = target_left.get("min"), target_left.get("max")
    add(
        "v2_left_fraction_is_sampled_not_forced_to_legacy_0p35",
        left_min is not None
        and left_max is not None
        and float(left_min) < float(left_max)
        and not (
            np.isclose(float(left_min), 0.35, rtol=0.0, atol=1e-12)
            and np.isclose(float(left_max), 0.35, rtol=0.0, atol=1e-12)
        ),
        observed_min=left_min,
        observed_max=left_max,
        source_reference=contract.get("left_fraction_reference"),
        rejected_substitution="legacy fixed target_left_ratio=0.35",
    )
    left_error = v2.get("left_fraction_error", {})
    volume_error = v2.get("volume_relative_error", {})
    add(
        "v2_target_actual_metrics_complete",
        int(left_error.get("count", 0)) == case_count
        and int(volume_error.get("count", 0)) == case_count,
        left_fraction_error=left_error,
        volume_relative_error=volume_error,
    )
    add(
        "v2_source_shape_and_torso_hard_qc",
        v2.get("all_shape_quality_passed") is True
        and v2.get("all_torso_qc_passed") is True,
        all_shape_quality_passed=v2.get("all_shape_quality_passed"),
        all_torso_qc_passed=v2.get("all_torso_qc_passed"),
    )
    seed_counts = v2.get("child_seed_unique_counts", {})
    add(
        "v2_child_seeds_unique_per_namespace",
        bool(seed_counts)
        and all(int(seed_counts.get(name, 0)) == case_count for name in (
            "patient", "liver", "tumor", "activity", "mu", "simind"
        )),
        unique_counts=seed_counts,
        expected_per_namespace=case_count,
    )

    return {
        "status": "passed" if all(row["status"] == "passed" for row in checks) else "failed",
        "enforced": True,
        "scope": (
            "Gate A 100-case anatomy-only CPU pilot for the evidence-backed V2 population/liver "
            "adapter and frozen master lesion/NPZ/QC contracts; not a clinical prevalence claim"
        ),
        "legacy_anatomy_checks_not_applied": sorted(excluded_legacy_anatomy_checks),
        "checks": checks,
    }
