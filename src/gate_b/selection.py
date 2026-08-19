"""Deterministic Gate B case feature extraction and selection freeze."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import numpy as np


class GateBSelectionError(RuntimeError):
    """Raised when parent identity or deterministic coverage fails closed."""


# Analytic selection features do not need sub-micrometre precision.  Rounding
# before selection and serialization removes reduction-order representation
# drift observed between qualified NumPy 1.x and 2.x environments.
CANONICAL_FLOAT_DECIMALS = 7


def _canonicalize_numeric(value: Any) -> Any:
    if isinstance(value, (float, np.floating)):
        rounded = round(float(value), CANONICAL_FLOAT_DECIMALS)
        return 0.0 if rounded == 0.0 else rounded
    if isinstance(value, dict):
        return {key: _canonicalize_numeric(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonicalize_numeric(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_canonicalize_numeric(item) for item in value)
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise GateBSelectionError(f"JSON object required: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with Path(path).open("r", encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise GateBSelectionError(f"JSONL object required at {path}:{number}")
            rows.append(value)
    return rows


def _manifest_inventory(parent_root: Path, config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    manifest_path = parent_root / "dataset_manifest.json"
    expected_sha = config["parent"]["dataset_manifest_sha256"]
    observed_sha = sha256_file(manifest_path)
    if observed_sha != expected_sha:
        raise GateBSelectionError(
            f"Gate A manifest SHA mismatch: expected {expected_sha}, got {observed_sha}"
        )
    manifest = _read_json(manifest_path)
    if manifest.get("dataset_id") != config["parent"]["dataset_id"]:
        raise GateBSelectionError("Gate A dataset identity differs from selection config")
    if manifest.get("case_count") != config["parent"]["case_count"]:
        raise GateBSelectionError("Gate A case count differs from selection config")
    if manifest.get("gate_a_report_status") != "passed":
        raise GateBSelectionError("Gate A report status is not passed")
    records = manifest.get("files")
    if not isinstance(records, list) or len(records) != 307:
        raise GateBSelectionError("Gate A manifest must contain exactly 307 frozen files")
    inventory: dict[str, dict[str, Any]] = {}
    for record in records:
        relative = str(record.get("path", ""))
        if not relative or relative in inventory or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise GateBSelectionError(f"unsafe or duplicate Gate A manifest path: {relative!r}")
        path = parent_root / Path(relative)
        if not path.is_file():
            raise GateBSelectionError(f"Gate A manifest member missing: {relative}")
        if path.stat().st_size != int(record["bytes"]):
            raise GateBSelectionError(f"Gate A manifest size mismatch: {relative}")
        if sha256_file(path) != record["sha256"]:
            raise GateBSelectionError(f"Gate A manifest SHA mismatch: {relative}")
        inventory[relative] = dict(record)
    report = _read_json(parent_root / "gate_a_report.json")
    if sha256_file(parent_root / "gate_a_report.json") != config["parent"]["gate_a_report_sha256"]:
        raise GateBSelectionError("Gate A report SHA differs from selection config")
    if report.get("status") != "passed" or report.get("implementation_commit") != config["parent"]["commit"]:
        raise GateBSelectionError("Gate A report commit/status identity differs")
    summary = report.get("qc_summary", {})
    if summary.get("lesion_count") != config["parent"]["lesion_count"]:
        raise GateBSelectionError("Gate A lesion count differs")
    if summary.get("lesion_distributions", {}).get("placement_strata") != config["parent"]["placement_strata"]:
        raise GateBSelectionError("Gate A lesion placement strata differ")
    return inventory


def _case_paths(parent_root: Path, case_id: str) -> tuple[Path, Path, Path]:
    return (
        parent_root / "phantom" / f"{case_id}.npz",
        parent_root / "phantom" / f"{case_id}_meta.json",
        parent_root / "qc" / f"{case_id}_phantom_qc.json",
    )


def _inventory_record(
    inventory: dict[str, dict[str, Any]], parent_root: Path, path: Path
) -> dict[str, Any]:
    relative = path.relative_to(parent_root).as_posix()
    if relative not in inventory:
        raise GateBSelectionError(f"parent artifact absent from Gate A manifest: {relative}")
    return inventory[relative]


def _activity_geometry(
    activity: np.ndarray,
    *,
    voxel_size_mm: float,
    detector_i: int,
    detector_j: int,
    detector_pitch_mm: float,
    views: int,
    start_angle_degrees: float,
    angle_step_degrees: float,
) -> dict[str, Any]:
    support = activity > 0.0
    indices = np.argwhere(support)
    if indices.size == 0:
        raise GateBSelectionError("activity support is empty")
    weights = activity[support].astype(np.float64)
    mass = float(np.sum(weights, dtype=np.float64))
    if not math.isfinite(mass) or mass <= 0.0:
        raise GateBSelectionError("activity mass must be finite and positive")
    center = (np.asarray(activity.shape, dtype=np.float64) - 1.0) / 2.0
    coordinates = (indices.astype(np.float64) - center[None, :]) * voxel_size_mm
    centroid_zyx = np.sum(coordinates * weights[:, None], axis=0) / mass
    z = coordinates[:, 0]
    y = coordinates[:, 1]
    x = coordinates[:, 2]
    half_i = detector_i * detector_pitch_mm / 2.0
    half_j = detector_j * detector_pitch_mm / 2.0
    view_rows = []
    worst_i_usage = 0.0
    worst_margin = float("inf")
    for view in range(views):
        angle = start_angle_degrees + view * angle_step_degrees
        radians = math.radians(angle)
        u = x * math.cos(radians) + y * math.sin(radians)
        u_min = float(np.min(u))
        u_max = float(np.max(u))
        z_min = float(np.min(z))
        z_max = float(np.max(z))
        u_abs = max(abs(u_min), abs(u_max))
        z_abs = max(abs(z_min), abs(z_max))
        margin_i = half_i - u_abs
        margin_j = half_j - z_abs
        worst_i_usage = max(worst_i_usage, u_abs / half_i)
        worst_margin = min(worst_margin, margin_i, margin_j)
        centroid_u = float(np.sum(u * weights, dtype=np.float64) / mass)
        view_rows.append(
            {
                "view": view,
                "angle_degrees": angle,
                "bbox_u_mm": [u_min, u_max],
                "bbox_v_mm": [z_min, z_max],
                "centroid_u_mm": centroid_u,
                "centroid_v_mm": float(centroid_zyx[0]),
                "native_margin_u_mm": margin_i,
                "native_margin_v_mm": margin_j,
            }
        )
    right_mass = float(np.sum(activity[:, :, center[2] + 0.5 <= np.arange(activity.shape[2])], dtype=np.float64))
    left_mass = mass - right_mass
    lr_imbalance = abs(right_mass - left_mass) / mass
    centroid_radial = math.hypot(float(centroid_zyx[1]), float(centroid_zyx[2]))
    directional = max(centroid_radial / half_i, lr_imbalance)
    z_usage = max(abs(float(np.min(z))), abs(float(np.max(z)))) / half_j
    return {
        "activity_mass": mass,
        "support_voxel_count": int(indices.shape[0]),
        "support_nonzero_fraction": float(indices.shape[0] / activity.size),
        "centroid_mm_zyx": [float(value) for value in centroid_zyx],
        "left_right_mass_imbalance": lr_imbalance,
        "directional_asymmetry": directional,
        "fov_pressure_ratio": max(worst_i_usage, z_usage),
        "native_fov_min_margin_mm": worst_margin,
        "native_aperture_passed": worst_margin >= 0.0,
        "per_view": view_rows,
    }


def _attenuation_geometry(mu: np.ndarray, voxel_size_cm: float) -> dict[str, float]:
    if not np.isfinite(mu).all() or np.any(mu < 0.0):
        raise GateBSelectionError("mu_map must be finite and nonnegative")
    x_paths = np.sum(mu, axis=2, dtype=np.float64) * voxel_size_cm
    y_paths = np.sum(mu, axis=1, dtype=np.float64) * voxel_size_cm
    positive = mu[mu > 0.0]
    return {
        "attenuation_path_burden": float(max(np.max(x_paths), np.max(y_paths))),
        "attenuation_mean_positive_mu": float(np.mean(positive, dtype=np.float64)) if positive.size else 0.0,
        "attenuation_nonzero_fraction": float(positive.size / mu.size),
        "attenuation_mass": float(np.sum(mu, dtype=np.float64)),
    }


def _candidate_row(
    parent_root: Path,
    case_record: dict[str, Any],
    inventory: dict[str, dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    case_id = str(case_record["case_id"])
    npz_path, meta_path, qc_path = _case_paths(parent_root, case_id)
    parent_records = {
        "npz": _inventory_record(inventory, parent_root, npz_path),
        "metadata": _inventory_record(inventory, parent_root, meta_path),
        "qc": _inventory_record(inventory, parent_root, qc_path),
    }
    metadata = _read_json(meta_path)
    qc = _read_json(qc_path)
    metadata_case = metadata.get("case_id")
    metadata_identity_matches = metadata_case in {
        case_id,
        int(case_id.rsplit("_", 1)[1]),
    }
    if not metadata_identity_matches or qc.get("status") != "passed":
        raise GateBSelectionError(f"parent case identity/QC failed: {case_id}")
    with np.load(npz_path) as payload:
        required = {"activity", "mu_map", "liver_mask", "left_mask", "right_mask", "tumor_masks"}
        if set(payload.files) != required:
            raise GateBSelectionError(f"parent NPZ keys differ: {case_id}")
        activity = np.asarray(payload["activity"], dtype=np.float32)
        mu = np.asarray(payload["mu_map"], dtype=np.float32)
    if activity.shape != (128, 128, 128) or mu.shape != activity.shape:
        raise GateBSelectionError(f"parent array shape differs: {case_id}")
    if not np.isfinite(activity).all() or np.any(activity < 0.0):
        raise GateBSelectionError(f"parent activity invalid: {case_id}")
    physics = config["physics"]
    activity_geometry = _activity_geometry(
        activity,
        voxel_size_mm=float(physics["voxel_size_mm"]),
        detector_i=int(physics["index_100_detector_i"]),
        detector_j=int(physics["index_101_detector_j"]),
        detector_pitch_mm=float(physics["native_detector_pitch_mm"]),
        views=int(physics["projection_shape"][0]),
        start_angle_degrees=float(physics["start_angle_degrees"]),
        angle_step_degrees=float(physics["angle_step_degrees"]),
    )
    attenuation = _attenuation_geometry(mu, float(physics["voxel_size_mm"]) / 10.0)
    tumors = qc["metrics"].get("tumors", [])
    diameters = [float(row["effective_diameter_mm"]) for row in tumors]
    margins = [float(row["surface_margin_mm"]) for row in tumors]
    tnrs = [float(row["tnr_from_saved_activity"]) for row in tumors]
    size_layers = sorted(
        {
            f"{int(row['sampled_size_bin_mm'][0])}_{int(row['sampled_size_bin_mm'][1])}"
            for row in tumors
        }
    )
    torso = metadata["v2"]["torso"]
    torso_metrics = torso["actual_metrics"]
    body_extent = torso_metrics["qc_metrics"]["body_extent_mm_zyx"]
    perfusion_labels = {
        "Left Only": "Left",
        "Right Only": "Right",
        "Tumor Only": "Tumor-only",
        "Whole Liver": "Whole",
    }
    perfusion = perfusion_labels.get(metadata["perfusion_mode"])
    if perfusion is None:
        raise GateBSelectionError(
            f"unknown master perfusion label for {case_id}: {metadata['perfusion_mode']!r}"
        )
    return {
        "case_id": case_id,
        "case_number": int(case_id.rsplit("_", 1)[1]),
        "pilot_only": True,
        "parent_split_ignored": case_record.get("split"),
        "morphology": qc["v2"]["morphology"],
        "caudate_enabled": bool(qc["v2"]["caudate_enabled"]),
        "perfusion": perfusion,
        "parent_perfusion_label": metadata["perfusion_mode"],
        "actual_liver_volume_ml": float(qc["v2"]["actual_volume_ml"]),
        "actual_left_fraction": float(qc["v2"]["actual_left_fraction"]),
        "torso_body_volume_ml": float(torso_metrics["tissues"]["body"]["volume_ml"]),
        "torso_body_si_mm": float(body_extent[0]),
        "torso_body_ap_mm": float(body_extent[1]),
        "torso_body_lr_mm": float(body_extent[2]),
        "n_tumors": int(qc["metrics"]["n_tumors"]),
        "minimum_lesion_diameter_mm": min(diameters),
        "mean_lesion_diameter_mm": float(np.mean(diameters)),
        "maximum_lesion_diameter_mm": max(diameters),
        "minimum_surface_margin_mm": min(margins),
        "minimum_realized_tnr": min(tnrs),
        "mean_realized_tnr": float(np.mean(tnrs)),
        "maximum_realized_tnr": max(tnrs),
        "lesion_size_layers": size_layers,
        "activity_fov_pressure_ratio": activity_geometry["fov_pressure_ratio"],
        "activity_native_fov_min_margin_mm": activity_geometry["native_fov_min_margin_mm"],
        "activity_directional_asymmetry": activity_geometry["directional_asymmetry"],
        "activity_centroid_mm_zyx": activity_geometry["centroid_mm_zyx"],
        "activity_left_right_mass_imbalance": activity_geometry["left_right_mass_imbalance"],
        "activity_support_nonzero_fraction": activity_geometry["support_nonzero_fraction"],
        "activity_mass": activity_geometry["activity_mass"],
        **attenuation,
        "preflight_native_aperture_passed": activity_geometry["native_aperture_passed"],
        "preflight_per_view": activity_geometry["per_view"],
        "parent": {
            role: {
                "relative_path": record["path"],
                "bytes": int(record["bytes"]),
                "sha256": record["sha256"],
            }
            for role, record in parent_records.items()
        },
    }


def build_candidate_table(parent_root: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    parent_root = Path(parent_root).resolve()
    inventory = _manifest_inventory(parent_root, config)
    cases = sorted(_read_jsonl(parent_root / "cases.jsonl"), key=lambda row: row["case_id"])
    if len(cases) != 100 or len({row["case_id"] for row in cases}) != 100:
        raise GateBSelectionError("Gate A case ledger must contain 100 unique cases")
    candidates = [
        _canonicalize_numeric(_candidate_row(parent_root, case, inventory, config))
        for case in cases
    ]
    if not all(row["preflight_native_aperture_passed"] for row in candidates):
        failed = [row["case_id"] for row in candidates if not row["preflight_native_aperture_passed"]]
        raise GateBSelectionError(f"parent activity exceeds native aperture: {failed}")
    return candidates


def _quantile(rows: list[dict[str, Any]], field: str, q: float) -> float:
    return float(np.quantile(np.asarray([row[field] for row in rows], dtype=np.float64), q))


def _thresholds(rows: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, float]:
    quantiles = config["selection"]["quantiles"]
    low = float(quantiles["low"])
    high = float(quantiles["high"])
    return {
        "liver_low": _quantile(rows, "actual_liver_volume_ml", low),
        "liver_high": _quantile(rows, "actual_liver_volume_ml", high),
        "left_low": _quantile(rows, "actual_left_fraction", low),
        "left_high": _quantile(rows, "actual_left_fraction", high),
        "fov_high": _quantile(rows, "activity_fov_pressure_ratio", high),
        "attenuation_high": _quantile(rows, "attenuation_path_burden", high),
        "tnr_low": _quantile(rows, "minimum_realized_tnr", low),
        "tnr_high": _quantile(rows, "maximum_realized_tnr", high),
        "asymmetry_sentinel": _quantile(
            rows,
            "activity_directional_asymmetry",
            float(quantiles["asymmetry_sentinel_minimum"]),
        ),
        "population_max_lesion": max(row["maximum_lesion_diameter_mm"] for row in rows),
        "population_min_margin": min(row["minimum_surface_margin_mm"] for row in rows),
    }


def _labels(row: dict[str, Any], thresholds: dict[str, float], config: dict[str, Any]) -> set[str]:
    absolute = config["selection"]["absolute_thresholds"]
    labels = {
        f"morphology:{row['morphology']}",
        f"caudate:{'on' if row['caudate_enabled'] else 'off'}",
        f"perfusion:{row['perfusion']}",
        f"tumor_count:{row['n_tumors']}",
    }
    volume = row["actual_liver_volume_ml"]
    if volume <= thresholds["liver_low"]:
        labels.add("liver_volume:low")
    elif volume >= thresholds["liver_high"]:
        labels.add("liver_volume:high")
    else:
        labels.add("liver_volume:middle")
    if volume > float(absolute["large_liver_ml"]):
        labels.add("liver_volume:gt1900")
    if volume >= float(absolute["near_or_above_2200_liver_ml"]):
        labels.add("liver_volume:ge2200")
    if row["actual_left_fraction"] <= thresholds["left_low"]:
        labels.add("left_fraction:low")
    if row["actual_left_fraction"] >= thresholds["left_high"]:
        labels.add("left_fraction:high")
    if row["activity_fov_pressure_ratio"] >= thresholds["fov_high"]:
        labels.add("pressure:fov_high")
    if row["attenuation_path_burden"] >= thresholds["attenuation_high"]:
        labels.add("pressure:attenuation_high")
    labels.update(f"lesion_size:{layer}" for layer in row["lesion_size_layers"])
    if math.isclose(row["maximum_lesion_diameter_mm"], thresholds["population_max_lesion"], rel_tol=0.0, abs_tol=1e-12):
        labels.add("lesion:max_population")
    if math.isclose(row["minimum_surface_margin_mm"], thresholds["population_min_margin"], rel_tol=0.0, abs_tol=1e-12):
        labels.add("margin:min_population")
    if row["minimum_realized_tnr"] <= thresholds["tnr_low"]:
        labels.add("tnr:low")
    if row["maximum_realized_tnr"] >= thresholds["tnr_high"]:
        labels.add("tnr:high")
    return labels


def _standardized_matrix(
    rows: list[dict[str, Any]], feature_names: list[str]
) -> tuple[np.ndarray, dict[str, dict[str, float]]]:
    matrix = np.asarray([[row[name] for name in feature_names] for row in rows], dtype=np.float64)
    if not np.isfinite(matrix).all():
        raise GateBSelectionError("selection feature matrix contains nonfinite values")
    median = np.median(matrix, axis=0)
    scale = np.std(matrix, axis=0, ddof=0)
    scale[scale == 0.0] = 1.0
    standardized = (matrix - median) / scale
    stats = {
        name: {"median": float(center), "population_std": float(width)}
        for name, center, width in zip(feature_names, median, scale)
    }
    return standardized, stats


def _percentile_rank(rows: list[dict[str, Any]], field: str) -> dict[str, float]:
    ordered = sorted(rows, key=lambda row: (row[field], row["case_id"]))
    denominator = max(len(ordered) - 1, 1)
    return {row["case_id"]: index / denominator for index, row in enumerate(ordered)}


def _sentinel_index(rows: list[dict[str, Any]], thresholds: dict[str, float]) -> tuple[int, dict[str, float]]:
    fov_rank = _percentile_rank(rows, "activity_fov_pressure_ratio")
    attenuation_rank = _percentile_rank(rows, "attenuation_path_burden")
    asymmetry_rank = _percentile_rank(rows, "activity_directional_asymmetry")
    eligible = [
        index
        for index, row in enumerate(rows)
        if row["activity_directional_asymmetry"] >= thresholds["asymmetry_sentinel"]
    ]
    if not eligible:
        raise GateBSelectionError("no direction-identifiable sentinel candidate")
    scores = {
        row["case_id"]: (
            fov_rank[row["case_id"]]
            + attenuation_rank[row["case_id"]]
            + asymmetry_rank[row["case_id"]]
        )
        for row in rows
    }
    selected = sorted(
        eligible,
        key=lambda index: (-scores[rows[index]["case_id"]], rows[index]["case_id"]),
    )[0]
    return selected, {
        "fov_percentile": fov_rank[rows[selected]["case_id"]],
        "attenuation_percentile": attenuation_rank[rows[selected]["case_id"]],
        "asymmetry_percentile": asymmetry_rank[rows[selected]["case_id"]],
        "composite": scores[rows[selected]["case_id"]],
    }


def _minimum_distance(index: int, selected: list[int], matrix: np.ndarray) -> float:
    if not selected:
        return float("inf")
    return float(np.min(np.linalg.norm(matrix[index] - matrix[np.asarray(selected)], axis=1)))


def _select_indices(
    rows: list[dict[str, Any]],
    matrix: np.ndarray,
    labels: list[set[str]],
    config: dict[str, Any],
    sentinel_index: int,
) -> tuple[list[int], list[dict[str, Any]], dict[str, int]]:
    count = int(config["selection"]["count"])
    quotas = {str(key): int(value) for key, value in config["selection"]["quotas"].items()}
    selected = [sentinel_index]
    observed = Counter(labels[sentinel_index])
    trace = [
        {
            "rank": 1,
            "case_id": rows[sentinel_index]["case_id"],
            "phase": "pre_frozen_sentinel",
            "coverage_gain": sum(min(observed[key], quota) for key, quota in quotas.items()),
            "minimum_standardized_distance": None,
        }
    ]
    while any(observed[key] < quota for key, quota in quotas.items()):
        if len(selected) >= count:
            missing = {key: quota - observed[key] for key, quota in quotas.items() if observed[key] < quota}
            raise GateBSelectionError(f"selection quotas cannot fit in {count} cases: {missing}")
        candidates = []
        for index, row in enumerate(rows):
            if index in selected:
                continue
            gain = sum(
                1
                for label in labels[index]
                if label in quotas and observed[label] < quotas[label]
            )
            if gain <= 0:
                continue
            candidates.append((index, gain, _minimum_distance(index, selected, matrix)))
        if not candidates:
            missing = {key: quota - observed[key] for key, quota in quotas.items() if observed[key] < quota}
            raise GateBSelectionError(f"no candidate can cover remaining quotas: {missing}")
        maximum_gain = max(item[1] for item in candidates)
        gain_candidates = [item for item in candidates if item[1] == maximum_gain]
        maximum_distance = max(item[2] for item in gain_candidates)
        finalists = [item for item in gain_candidates if math.isclose(item[2], maximum_distance, rel_tol=0.0, abs_tol=1e-12)]
        index, gain, distance = sorted(finalists, key=lambda item: rows[item[0]]["case_id"])[0]
        selected.append(index)
        observed.update(labels[index])
        trace.append(
            {
                "rank": len(selected),
                "case_id": rows[index]["case_id"],
                "phase": "quota_constrained_maximin",
                "coverage_gain": gain,
                "minimum_standardized_distance": distance,
            }
        )
    while len(selected) < count:
        candidates = [
            (index, _minimum_distance(index, selected, matrix))
            for index in range(len(rows))
            if index not in selected
        ]
        maximum_distance = max(item[1] for item in candidates)
        finalists = [item for item in candidates if math.isclose(item[1], maximum_distance, rel_tol=0.0, abs_tol=1e-12)]
        index, distance = sorted(finalists, key=lambda item: rows[item[0]]["case_id"])[0]
        selected.append(index)
        observed.update(labels[index])
        trace.append(
            {
                "rank": len(selected),
                "case_id": rows[index]["case_id"],
                "phase": "farthest_first_fill",
                "coverage_gain": 0,
                "minimum_standardized_distance": distance,
            }
        )
    coverage = {key: observed[key] for key in quotas}
    if any(coverage[key] < quota for key, quota in quotas.items()):
        raise GateBSelectionError("final selection does not satisfy configured quotas")
    return selected, trace, coverage


def _flatten_candidate(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: json.dumps(value, sort_keys=True, separators=(",", ":"))
        if isinstance(value, (list, dict))
        else value
        for key, value in row.items()
        if key != "preflight_per_view"
    }


def _write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    materialized = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(materialized[0]) if materialized else []
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(materialized)


def _markdown(result: dict[str, Any]) -> str:
    selected = result["selected"]
    lines = [
        "# Gate B frozen positive pilot selection",
        "",
        f"- Parent: `{result['parent']['dataset_id']}` at `{result['parent']['commit']}`",
        f"- Status: **{result['status']}**",
        f"- Sentinel: `{result['sentinel']['case_id']}`",
        "- Scope: `pilot_only`; parent train/val/test labels are ignored.",
        "- Selection: category quotas followed by standardized maximin/farthest-first with ascending case-ID ties.",
        "",
        "| Rank | Case | Sentinel | Morphology | Caudate | Perfusion | Liver mL | Left fraction | Tumors | Diameter min–max mm | Margin min mm | TNR min–max | FOV pressure | Attenuation burden |",
        "|---:|---|:---:|---|:---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for record in selected:
        row = record["candidate"]
        lines.append(
            "| {rank} | `{case}` | {sentinel} | {morphology} | {caudate} | {perfusion} | {volume:.1f} | {left:.4f} | {tumors} | {dmin:.1f}–{dmax:.1f} | {margin:.2f} | {tnrmin:.2f}–{tnrmax:.2f} | {fov:.4f} | {atten:.4f} |".format(
                rank=record["rank"],
                case=row["case_id"],
                sentinel="yes" if record["sentinel"] else "",
                morphology=row["morphology"],
                caudate="on" if row["caudate_enabled"] else "off",
                perfusion=row["perfusion"],
                volume=row["actual_liver_volume_ml"],
                left=row["actual_left_fraction"],
                tumors=row["n_tumors"],
                dmin=row["minimum_lesion_diameter_mm"],
                dmax=row["maximum_lesion_diameter_mm"],
                margin=row["minimum_surface_margin_mm"],
                tnrmin=row["minimum_realized_tnr"],
                tnrmax=row["maximum_realized_tnr"],
                fov=row["activity_fov_pressure_ratio"],
                atten=row["attenuation_path_burden"],
            )
        )
    lines.extend(
        [
            "",
            "## Frozen scope disclosure",
            "",
            "All 329 parent lesions are central master lesions. The hybrid transfers V2 patient/liver/torso/attenuation anatomy only; lesion generation, activity, and perfusion remain the master 1–5 lesion, 10–60 mm implementation rather than the complete V2 TARE-HCC tumor population.",
            "",
            "These ten cases must not enter Formal550, E-CAL, training, validation, sealed test, or negative-control datasets.",
            "",
        ]
    )
    return "\n".join(lines)


def freeze_selection(
    *,
    parent_root: Path,
    config_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    config_path = Path(config_path).resolve()
    config = _read_json(config_path)
    rows = build_candidate_table(Path(parent_root), config)
    thresholds = _thresholds(rows, config)
    labels = [_labels(row, thresholds, config) for row in rows]
    feature_names = list(config["selection"]["standardized_features"])
    matrix, feature_stats = _standardized_matrix(rows, feature_names)
    sentinel_index, sentinel_score = _sentinel_index(rows, thresholds)
    selected_indices, trace, coverage = _select_indices(
        rows, matrix, labels, config, sentinel_index
    )
    selection_code = Path(__file__).resolve()
    selected = []
    for rank, index in enumerate(selected_indices, 1):
        candidate = {key: value for key, value in rows[index].items() if key != "preflight_per_view"}
        selected.append(
            {
                "rank": rank,
                "case_id": rows[index]["case_id"],
                "sentinel": index == sentinel_index,
                "pilot_only": True,
                "labels": sorted(labels[index]),
                "standardized_features": {
                    name: float(value)
                    for name, value in zip(feature_names, matrix[index])
                },
                "candidate": candidate,
                "preflight_per_view": rows[index]["preflight_per_view"],
            }
        )
    quotas = config["selection"]["quotas"]
    result = _canonicalize_numeric({
        "schema_version": "pars_gate_b_selection_v1",
        "status": "frozen",
        "pilot_only": True,
        "parent": {
            **config["parent"],
            "root_read_only": str(Path(parent_root).resolve()),
        },
        "selection_algorithm": {
            "name": config["selection"]["algorithm"],
            "tie_break": config["selection"]["tie_break"],
            "config_path": config_path.as_posix(),
            "config_sha256": sha256_file(config_path),
            "code_path": selection_code.as_posix(),
            "code_sha256": sha256_file(selection_code),
            "standardized_features": feature_names,
            "feature_standardization": feature_stats,
            "thresholds": thresholds,
            "quotas": quotas,
            "coverage": coverage,
            "trace": trace,
        },
        "candidate_count": len(rows),
        "selected_count": len(selected),
        "sentinel": {
            "case_id": rows[sentinel_index]["case_id"],
            "selection_rank": selected_indices.index(sentinel_index) + 1,
            "pressure_score": sentinel_score,
            "frozen_before_simind": True,
        },
        "selected_case_ids_in_order": [record["case_id"] for record in selected],
        "selected": selected,
        "claim_boundary": config["claim_boundary"],
    })
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "selection.json").write_bytes(canonical_json_bytes(result))
    (output_dir / "selection.md").write_text(_markdown(result), encoding="utf-8", newline="\n")
    candidate_payload = _canonicalize_numeric({
        "schema_version": "pars_gate_b_candidate_features_v1",
        "pilot_only": True,
        "parent": result["parent"],
        "selection_config_sha256": result["selection_algorithm"]["config_sha256"],
        "selection_code_sha256": result["selection_algorithm"]["code_sha256"],
        "thresholds": thresholds,
        "candidates": rows,
    })
    (output_dir / "candidate_features.json").write_bytes(canonical_json_bytes(candidate_payload))
    _write_csv(output_dir / "candidate_features.csv", (_flatten_candidate(row) for row in rows))
    _write_csv(
        output_dir / "selection.csv",
        (
            {
                "rank": record["rank"],
                "case_id": record["case_id"],
                "sentinel": record["sentinel"],
                "pilot_only": True,
                **_flatten_candidate(record["candidate"]),
            }
            for record in selected
        ),
    )
    return result
