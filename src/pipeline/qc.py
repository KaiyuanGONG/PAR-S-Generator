"""Quality-control primitives for phantom and projection artifacts.

All functions are pure/read-only except for callers choosing to persist the
returned dictionaries.  The canonical projection orientation is the one used
by the current PAR-S_2 data loader and is recorded in every QC result.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np
from scipy.ndimage import binary_erosion, distance_transform_edt

from pipeline.contracts import CANONICAL_PROJECTION_TRANSFORM, sha256_file


DEFAULT_PROJECTION_SHAPE = (60, 128, 128)


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
    return raw[::-1, ::-1, :] if canonical else raw


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
) -> dict[str, Any]:
    """Return strong, machine-readable completion and numerical QC evidence."""
    a00_path = Path(a00_path)
    res_path = a00_path.with_suffix(".res")
    mhd_path = a00_path.with_suffix(".mhd")
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
        volume_vox = int(tumor.sum())
        overlap_vox = int((occupied & tumor).sum())
        outside_vox = int((tumor & ~liver).sum())
        occupied |= tumor
        volume_mm3 = volume_vox * voxel_mm**3
        effective_diameter = (6.0 * volume_mm3 / np.pi) ** (1.0 / 3.0) if volume_vox else 0.0
        tumor_mean = float(activity[tumor].mean()) if volume_vox else 0.0
        tumor_records.append(
            {
                "index": index,
                "volume_vox": volume_vox,
                "effective_diameter_mm": float(effective_diameter),
                "outside_liver_vox": outside_vox,
                "overlap_previous_vox": overlap_vox,
                "surface_margin_mm": float(distance_to_surface[tumor].min()) if volume_vox else 0.0,
                "tnr_from_saved_activity": tumor_mean / liver_mean if liver_mean > 0 else None,
            }
        )
        if volume_vox == 0 or outside_vox or overlap_vox:
            failures.append(f"invalid_tumor_{index}")

    attenuation = meta.get("attenuation_contract", {})
    if attenuation.get("status", "unrecorded") != "verified_by_simind_ict":
        warnings.append("attenuation_contract_not_verified_by_simind_ict")

    cantlie = meta.get("cantlie", {})
    if cantlie and cantlie.get("converged") is not True:
        failures.append("cantlie_not_converged")
    if cantlie.get("search", {}).get("expanded_beyond_initial_range"):
        warnings.append("cantlie_search_range_expanded")

    left_ratio = float(left.sum() / max(int(liver.sum()), 1))
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
    }
    return result
