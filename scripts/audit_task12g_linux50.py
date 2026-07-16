#!/usr/bin/env python
"""Independently audit and render the frozen 50-case Task 12G Linux dataset."""

from __future__ import annotations

import argparse
import io
import json
import math
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg", force=True)

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import ListedColormap


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from core.case_writer_v2 import (  # noqa: E402
    DatasetContractV2,
    DatasetFreezeRecordV2,
    freeze_dataset,
    load_case_record_v2,
)
from core.provenance import (  # noqa: E402
    atomic_write_bytes,
    atomic_write_json,
    sha256_file,
)
from core.task12g_acceptance import (  # noqa: E402
    MISMATCH_CHALLENGE_SEMANTICS,
    ensure_qa_root_outside_dataset,
    group_case_ids,
    partition_population_and_challenges,
    select_focus_cases,
)


SCHEMA_VERSION = "pars_v2_task12g_linux50_generator_gate_v1"
VISUAL_REGISTRY_SCHEMA = "pars_v2_task12g_linux50_visual_artifacts_v1"
EXPECTED_CASE_COUNT = 50
EXPECTED_DATASET_ID = "PAR-S-TARE-HCC-NoPVI-SYN-v2-linux50"
EXPECTED_DATASET_VERSION = "2.0.0-linux50-v2"
EXPECTED_SPLITS = Counter({"train": 40, "val": 5, "test": 5})
EXPECTED_MANIFEST_SHA256 = (
    "d44a77f4604bc1df192c6af0674341c704d4e069d1665e1fe159832adfeae722"
)
EXPECTED_PROJECTION_SHAPE = (60, 128, 128)
EXPECTED_CASE_IDS = tuple(f"case_{index:05d}" for index in range(EXPECTED_CASE_COUNT))
DEFAULT_DATASET_ROOT = Path(r"D:\PFE-U\PAR\outputs\pars_v2_linux50_v2")
DEFAULT_QA_ROOT = Path(r"D:\PFE-U\PAR\outputs\pars_v2_linux50_v2_qa")


class Task12GAuditError(RuntimeError):
    """Raised when frozen evidence or the read-only QA contract fails."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--qa-root", type=Path, default=DEFAULT_QA_ROOT)
    return parser


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Task12GAuditError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise Task12GAuditError(f"{label} must contain a JSON object")
    return value


def _artifact(dataset_root: Path, record: Any, name: str) -> Path:
    try:
        relative = record.artifacts[name].relative_path
    except KeyError as exc:
        raise Task12GAuditError(
            f"{record.case_id}: missing required artifact {name}"
        ) from exc
    path = (dataset_root / relative).resolve()
    try:
        path.relative_to(dataset_root)
    except ValueError as exc:
        raise Task12GAuditError(
            f"{record.case_id}: artifact {name} escapes dataset root"
        ) from exc
    return path


def validate_frozen_dataset(
    dataset_root: str | Path,
) -> tuple[DatasetFreezeRecordV2, tuple[Any, ...]]:
    """Verify the immutable 50-case dataset and idempotent freeze contract."""

    root = Path(dataset_root).resolve()
    marker_path = root / "DATASET_COMPLETE.json"
    try:
        marker = DatasetFreezeRecordV2.from_dict(
            _read_json(marker_path, "DATASET_COMPLETE.json")
        )
    except Exception as exc:
        if isinstance(exc, Task12GAuditError):
            raise
        raise Task12GAuditError(f"invalid dataset completion marker: {exc}") from exc
    if marker.case_count != EXPECTED_CASE_COUNT:
        raise Task12GAuditError("audit requires exactly 50 formally complete cases")
    if (
        marker.dataset_id != EXPECTED_DATASET_ID
        or marker.dataset_version != EXPECTED_DATASET_VERSION
        or marker.dataset_role != "main"
    ):
        raise Task12GAuditError("frozen dataset identity is not Task 12G Linux50 v2")
    if Counter(marker.split_counts) != EXPECTED_SPLITS:
        raise Task12GAuditError(
            f"frozen split counts differ from 40/5/5: {dict(marker.split_counts)}"
        )
    if marker.manifest_sha256 != EXPECTED_MANIFEST_SHA256:
        raise Task12GAuditError("frozen manifest digest is not the accepted Linux50 digest")

    manifest_path = (root / marker.manifest_relative_path).resolve()
    try:
        manifest_path.relative_to(root)
    except ValueError as exc:
        raise Task12GAuditError("manifest path escapes dataset root") from exc
    if not manifest_path.is_file():
        raise Task12GAuditError("frozen manifest is missing")
    if sha256_file(manifest_path) != marker.manifest_sha256:
        raise Task12GAuditError("manifest SHA-256 differs from DATASET_COMPLETE.json")
    lines = manifest_path.read_text(encoding="utf-8").splitlines()
    if len(lines) != EXPECTED_CASE_COUNT or any(not line.strip() for line in lines):
        raise Task12GAuditError("manifest must contain exactly 50 non-blank records")
    try:
        manifest_ids = tuple(str(json.loads(line)["case_id"]) for line in lines)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise Task12GAuditError(f"manifest case IDs are invalid: {exc}") from exc
    if manifest_ids != EXPECTED_CASE_IDS:
        raise Task12GAuditError("manifest case IDs are not the canonical case_00000..00049 set")

    records = tuple(
        load_case_record_v2(
            root / "cases" / case_id / "case_record.json",
            dataset_root=root,
            verify_hashes=True,
        )
        for case_id in manifest_ids
    )
    if tuple(record.case_id for record in records) != EXPECTED_CASE_IDS:
        raise Task12GAuditError("case records do not preserve manifest order")
    contract = DatasetContractV2(
        output_root=root,
        dataset_id=marker.dataset_id,
        dataset_version=marker.dataset_version,
        dataset_role=marker.dataset_role,
        expected_case_ids=EXPECTED_CASE_IDS,
        allowed_profile_ids=tuple(sorted({record.profile_id for record in records})),
        split_plan_sha256=marker.split_plan_sha256,
        required_artifact_names=tuple(marker.required_artifact_names),
    )
    if freeze_dataset(records, contract) != marker:
        raise Task12GAuditError("idempotent dataset freeze re-audit changed the marker")
    return marker, records


def _projection_metrics(
    projection: np.ndarray,
    *,
    outer_width: int = 8,
) -> dict[str, Any]:
    """Return absolute and support metrics without changing projection scale."""

    values = np.asarray(projection)
    if values.ndim != 3 or any(size <= 0 for size in values.shape):
        raise Task12GAuditError(f"unexpected projection shape {values.shape}")
    if not isinstance(outer_width, int) or isinstance(outer_width, bool) or outer_width < 0:
        raise ValueError("outer_width must be a non-negative integer")
    if outer_width * 2 >= min(values.shape[1:]):
        raise ValueError("outer_width is too large for detector dimensions")
    if not np.isfinite(values).all() or np.any(values < 0):
        raise Task12GAuditError("projection contains non-finite or negative bins")
    per_view = np.asarray(values.sum(axis=(1, 2), dtype=np.float64), dtype=np.float64)
    if np.any(per_view <= 0):
        raise Task12GAuditError("every projection view must have non-zero weight")
    total = float(per_view.sum(dtype=np.float64))
    if not math.isfinite(total) or total <= 0:
        raise Task12GAuditError("projection total must be positive and finite")
    positive_fraction = (values > 0).mean(axis=(1, 2), dtype=np.float64)
    outer = np.zeros(values.shape[1:], dtype=bool)
    if outer_width:
        outer[:outer_width, :] = True
        outer[-outer_width:, :] = True
        outer[:, :outer_width] = True
        outer[:, -outer_width:] = True
    outer_fraction = (
        float(values[:, outer].sum(dtype=np.float64) / total) if outer_width else 0.0
    )
    detector_y, detector_x = np.indices(values.shape[1:], dtype=np.float64)
    centroid_y = (
        (values * detector_y[None]).sum(axis=(1, 2), dtype=np.float64) / per_view
    )
    centroid_x = (
        (values * detector_x[None]).sum(axis=(1, 2), dtype=np.float64) / per_view
    )
    mean_view = float(per_view.mean())
    return {
        "projection_weight_sum": total,
        "view_sum_cv": float(per_view.std() / mean_view),
        "view_sum_ratio": float(per_view.max() / per_view.min()),
        "minimum_positive_bin_fraction_per_view": float(positive_fraction.min()),
        "outer_8px_count_fraction": outer_fraction,
        "detector_centroid_y_range_px": [
            float(centroid_y.min()),
            float(centroid_y.max()),
        ],
        "detector_centroid_x_range_px": [
            float(centroid_x.min()),
            float(centroid_x.max()),
        ],
        "per_view": per_view,
        "per_view_over_mean": per_view / mean_view,
        "sinogram": values.sum(axis=1, dtype=np.float64),
    }


def _direction_labels() -> dict[str, dict[str, str]]:
    return {
        "axial": {"horizontal": "L_to_R", "vertical": "P_to_A"},
        "coronal": {"horizontal": "L_to_R", "vertical": "I_to_S"},
        "sagittal": {"horizontal": "P_to_A", "vertical": "I_to_S"},
        "anterior": {
            "view": "A_to_P",
            "horizontal": "L_to_R",
            "vertical": "I_to_S",
        },
    }


def _selected_center(instances: np.ndarray) -> tuple[int, int, int]:
    values = np.asarray(instances)
    labels, counts = np.unique(values[values > 0], return_counts=True)
    if not len(labels):
        raise Task12GAuditError("tumor instance mask is empty")
    label = labels[int(np.argmax(counts))]
    center = np.rint(np.argwhere(values == label).mean(axis=0)).astype(int)
    return tuple(int(item) for item in center)


def _plane(array: np.ndarray, plane: str, center: tuple[int, int, int]) -> np.ndarray:
    z_index, y_index, x_index = center
    if plane == "axial":
        return np.asarray(array[z_index])
    if plane == "coronal":
        return np.asarray(array[:, y_index, :])
    if plane == "sagittal":
        return np.asarray(array[:, :, x_index])
    raise ValueError(f"unknown plane: {plane}")


def _crop_2d(mask: np.ndarray, padding: int = 4) -> tuple[slice, slice]:
    coordinates = np.argwhere(mask)
    if not len(coordinates):
        return slice(None), slice(None)
    lower = np.maximum(coordinates.min(axis=0) - padding, 0)
    upper = np.minimum(coordinates.max(axis=0) + padding + 1, mask.shape)
    return (
        slice(int(lower[0]), int(upper[0])),
        slice(int(lower[1]), int(upper[1])),
    )


def _axis_direction_labels(axis: Any, plane: str) -> None:
    labels = _direction_labels()[plane]
    horizontal = labels["horizontal"]
    vertical = labels["vertical"]
    horizontal_text = {
        "L_to_R": "L  ← horizontal →  R",
        "P_to_A": "P  ← horizontal →  A",
    }[horizontal]
    vertical_text = {
        "P_to_A": "P  ← vertical →  A",
        "I_to_S": "F/I  ← vertical →  H/S",
    }[vertical]
    axis.set_xlabel(horizontal_text, fontsize=7)
    axis.set_ylabel(vertical_text, fontsize=7)


def _draw_overlay(
    axis: Any,
    background: np.ndarray,
    liver: np.ndarray,
    tumor: np.ndarray,
    perfusion: np.ndarray,
    *,
    title: str,
    plane: str,
) -> None:
    crop = _crop_2d(liver | tumor | perfusion)
    background_crop = np.asarray(background)[crop]
    liver_crop = np.asarray(liver, dtype=bool)[crop]
    tumor_crop = np.asarray(tumor, dtype=bool)[crop]
    perfusion_crop = np.asarray(perfusion, dtype=bool)[crop]
    foreground = background_crop[background_crop > 0]
    if foreground.size:
        lower, upper = np.percentile(foreground, (1.0, 99.5))
        if not upper > lower:
            upper = lower + 1e-6
    else:
        lower = float(background_crop.min(initial=0.0))
        upper = float(background_crop.max(initial=1.0))
        if not upper > lower:
            upper = lower + 1e-6
    axis.imshow(
        background_crop,
        cmap="gray",
        origin="lower",
        interpolation="nearest",
        vmin=lower,
        vmax=upper,
    )
    if tumor_crop.any():
        axis.imshow(
            np.ma.masked_where(~tumor_crop, tumor_crop),
            cmap=ListedColormap(("#d1495b",)),
            origin="lower",
            interpolation="nearest",
            alpha=0.68,
            vmin=1,
            vmax=1,
        )
    if liver_crop.any() and not liver_crop.all():
        axis.contour(
            liver_crop,
            levels=(0.5,),
            colors=("#2a9d8f",),
            linewidths=(1.0,),
        )
    if perfusion_crop.any() and not perfusion_crop.all():
        axis.contour(
            perfusion_crop,
            levels=(0.5,),
            colors=("#277da1",),
            linewidths=(0.9,),
            linestyles=("--",),
        )
    axis.set_title(title, fontsize=8)
    axis.set_xticks([])
    axis.set_yticks([])
    _axis_direction_labels(axis, plane)


def _draw_scalar(
    axis: Any,
    values: np.ndarray,
    mask: np.ndarray,
    *,
    title: str,
    plane: str,
    cmap: str,
) -> None:
    crop = _crop_2d(mask)
    values_crop = np.asarray(values)[crop]
    mask_crop = np.asarray(mask, dtype=bool)[crop]
    shown = values_crop[mask_crop]
    if shown.size:
        lower, upper = np.percentile(shown, (1.0, 99.5))
        if not upper > lower:
            upper = lower + 1e-6
    else:
        lower, upper = 0.0, 1.0
    axis.imshow(
        values_crop,
        cmap=cmap,
        origin="lower",
        interpolation="nearest",
        vmin=lower,
        vmax=upper,
    )
    axis.set_title(title, fontsize=8)
    axis.set_xticks([])
    axis.set_yticks([])
    _axis_direction_labels(axis, plane)


def _anterior_projection(mask_zyx: np.ndarray) -> np.ndarray:
    mask = np.asarray(mask_zyx, dtype=bool)
    if mask.ndim != 3:
        raise ValueError("anterior projection requires a 3D ZYX mask")
    return mask.any(axis=1)


def _png_bytes(figure: Any, *, dpi: int = 145) -> bytes:
    buffer = io.BytesIO()
    try:
        figure.savefig(buffer, format="png", dpi=dpi, facecolor="white")
    finally:
        plt.close(figure)
    return buffer.getvalue()


def _render_case_board(
    output: Path,
    case_id: str,
    metadata: Mapping[str, Any],
    arrays: Mapping[str, np.ndarray],
    projection: np.ndarray,
    metrics: Mapping[str, Any],
    *,
    center: tuple[int, int, int],
) -> str:
    """Render a nine-panel official board without mutating input arrays."""

    figure, axes = plt.subplots(3, 3, figsize=(15, 14), constrained_layout=True)
    liver = np.asarray(arrays["liver_mask"], dtype=bool)
    tumor = np.asarray(arrays["tumor_union_mask"], dtype=bool)
    perfusion = np.asarray(arrays["perfusion_mask"], dtype=bool)
    mu_input = np.asarray(arrays["mu_input_140kev"])
    mu_true = np.asarray(arrays["mu_true_140kev"])
    activity = np.asarray(arrays["activity_relative"])
    for axis, plane in zip(axes[0], ("axial", "coronal", "sagittal")):
        _draw_overlay(
            axis,
            _plane(mu_input, plane, center),
            _plane(liver, plane, center),
            _plane(tumor, plane, center),
            _plane(perfusion, plane, center),
            title=f"{plane.title()} anatomy @ ZYX={center}",
            plane=plane,
        )
    _draw_scalar(
        axes[1, 0],
        _plane(mu_true, "axial", center),
        _plane(liver | np.asarray(arrays["body_mask"], dtype=bool), "axial", center),
        title="μ true at 140 keV (cm⁻¹)",
        plane="axial",
        cmap="gray",
    )
    _draw_scalar(
        axes[1, 1],
        _plane(mu_input, "axial", center),
        _plane(liver | np.asarray(arrays["body_mask"], dtype=bool), "axial", center),
        title="μ input at 140 keV (cm⁻¹)",
        plane="axial",
        cmap="gray",
    )
    _draw_scalar(
        axes[1, 2],
        np.log1p(_plane(activity, "axial", center)),
        _plane(liver, "axial", center),
        title="log1p activity relative",
        plane="axial",
        cmap="viridis",
    )

    anterior_axis = axes[2, 0]
    anterior_liver = _anterior_projection(liver)
    anterior_tumor = _anterior_projection(tumor)
    anterior_perfusion = _anterior_projection(perfusion)
    crop = _crop_2d(anterior_liver | anterior_tumor | anterior_perfusion)
    liver_crop = anterior_liver[crop]
    tumor_crop = anterior_tumor[crop]
    perfusion_crop = anterior_perfusion[crop]
    anterior_axis.imshow(
        np.ma.masked_where(~liver_crop, liver_crop),
        cmap=ListedColormap(("#e9c46a",)),
        origin="lower",
        interpolation="nearest",
        vmin=1,
        vmax=1,
    )
    if tumor_crop.any():
        anterior_axis.imshow(
            np.ma.masked_where(~tumor_crop, tumor_crop),
            cmap=ListedColormap(("#d1495b",)),
            origin="lower",
            interpolation="nearest",
            alpha=0.85,
            vmin=1,
            vmax=1,
        )
    if perfusion_crop.any() and not perfusion_crop.all():
        anterior_axis.contour(
            perfusion_crop,
            levels=(0.5,),
            colors=("#277da1",),
            linewidths=(0.9,),
            linestyles=("--",),
        )
    anterior_axis.set_title("Anterior anatomical view (A → P)", fontsize=8)
    anterior_axis.set_xlabel("L  ← horizontal →  R", fontsize=7)
    anterior_axis.set_ylabel("F/I  ← vertical →  H/S", fontsize=7)
    anterior_axis.set_xticks([])
    anterior_axis.set_yticks([])

    sinogram_axis = axes[2, 1]
    sinogram_axis.imshow(
        np.log1p(np.asarray(metrics["sinogram"]).T),
        cmap="magma",
        origin="lower",
        aspect="auto",
    )
    sinogram_axis.set_title("SIMIND sinogram (log1p, absolute input retained)", fontsize=8)
    sinogram_axis.set_xlabel("view")
    sinogram_axis.set_ylabel("detector u")

    curve_axis = axes[2, 2]
    curve_axis.plot(
        np.asarray(metrics["per_view"]),
        color="#264653",
        linewidth=1.2,
        label="absolute per-view weight",
    )
    shape_axis = curve_axis.twinx()
    shape_axis.plot(
        np.asarray(metrics["per_view_over_mean"]),
        color="#e76f51",
        linewidth=1.0,
        linestyle="--",
        label="per-view / case mean (shape only)",
    )
    curve_axis.set_title(
        "Projection view totals\n"
        f"sum={float(metrics['projection_weight_sum']):.3f}, "
        f"CV={float(metrics['view_sum_cv']):.3f}",
        fontsize=8,
    )
    curve_axis.set_xlabel("view")
    curve_axis.set_ylabel("absolute weight", color="#264653")
    shape_axis.set_ylabel("/ mean", color="#e76f51")
    curve_axis.grid(alpha=0.22)

    tumors = metadata["actual_metrics"]["tumors"]
    lesions = tumors["lesions"]
    dmax = max(float(item["recist_3d_mm"]) for item in lesions)
    tnr_median = float(np.median([float(item["tnr_mean"]) for item in lesions]))
    figure.suptitle(
        f"{case_id} | {metadata['split']} | "
        f"{metadata['patient']['liver_morphology']} | "
        f"{metadata['activity']['injection_territory']} | "
        f"challenge={metadata['activity']['mismatch_challenge']}\n"
        f"lesions={len(lesions)}, Dmax={dmax:.1f} mm, "
        f"TNRmean median={tnr_median:.2f}, "
        f"liver={float(metadata['actual_metrics']['liver']['volume_ml']):.1f} mL",
        fontsize=12,
        fontweight="bold",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(output, _png_bytes(figure))
    return sha256_file(output)


def _numeric_summary(values: Iterable[float]) -> dict[str, float]:
    array = np.asarray(tuple(values), dtype=np.float64)
    if not len(array) or not np.isfinite(array).all():
        raise Task12GAuditError("summary values must be non-empty and finite")
    return {
        "min": float(array.min()),
        "p25": float(np.percentile(array, 25)),
        "median": float(np.median(array)),
        "mean": float(array.mean()),
        "p75": float(np.percentile(array, 75)),
        "max": float(array.max()),
    }


def _case_row(
    record: Any,
    metadata: Mapping[str, Any],
    projection_metrics: Mapping[str, Any],
) -> dict[str, Any]:
    liver = metadata["actual_metrics"]["liver"]
    tumors = metadata["actual_metrics"]["tumors"]
    activity = metadata["activity"]
    lesions = [
        {
            "instance_id": int(item["instance_id"]),
            "recist_3d_mm": float(item["recist_3d_mm"]),
            "volume_ml": float(item["volume_ml"]),
            "tnr_mean": float(item["tnr_mean"]),
            "tnr_max": float(item["tnr_max"]),
            "necrotic_fraction": float(item["necrotic_fraction"]),
            "morphology": str(item["morphology"]),
            "liver_region_proxy": int(item["liver_region_proxy"]),
        }
        for item in tumors["lesions"]
    ]
    if not lesions:
        raise Task12GAuditError(f"{record.case_id}: tumor lesion list is empty")
    mismatch = bool(activity["mismatch_challenge"])
    return {
        "case_id": record.case_id,
        "split": record.split,
        "population_weight": float(record.population_weight),
        "sampling_probability": float(record.sampling_probability),
        "sex": str(metadata["patient"]["sex"]),
        "age_years": float(metadata["patient"]["age_years"]),
        "bmi": float(metadata["patient"]["bmi"]),
        "liver_morphology": str(metadata["patient"]["liver_morphology"]),
        "liver_volume_ml": float(liver["volume_ml"]),
        "liver_extent_si_mm": float(liver["extent_mm_zyx"][0]),
        "liver_extent_ap_mm": float(liver["extent_mm_zyx"][1]),
        "liver_extent_lr_mm": float(liver["extent_mm_zyx"][2]),
        "left_fraction": float(liver["left_fraction"]),
        "s1_3_to_s4_8_ratio": float(liver["s1_3_to_s4_8_ratio"]),
        "surface_roughness": float(liver["surface_roughness"]),
        "liver_sphericity": float(liver["sphericity"]),
        "tumor_count": len(lesions),
        "dmax_mm": max(item["recist_3d_mm"] for item in lesions),
        "lobe_extent": str(tumors["lobe_extent"]),
        "tumor_fraction_liver": float(tumors["tumor_union_fraction_liver"]),
        "tumor_fraction_perfused": float(tumors["tumor_union_fraction_perfused"]),
        "tnr_mean_median": float(np.median([item["tnr_mean"] for item in lesions])),
        "tnr_mean_maximum": max(item["tnr_mean"] for item in lesions),
        "tnr_max_maximum": max(item["tnr_max"] for item in lesions),
        "necrotic_fraction_max": max(item["necrotic_fraction"] for item in lesions),
        "injection_territory": str(activity["injection_territory"]),
        "activity_pattern": str(activity["activity_pattern"]),
        "mismatch_challenge": mismatch,
        "mismatch_semantics": (
            "coverage_challenge_not_prevalence" if mismatch else "population_case"
        ),
        "injection_tumor_coverage_fraction": float(
            activity["injection_tumor_coverage_fraction"]
        ),
        "perfusion_fraction_liver": (
            float(activity["perfused_volume_ml"]) / float(liver["volume_ml"])
        ),
        "projection_weight_sum": float(projection_metrics["projection_weight_sum"]),
        "view_sum_cv": float(projection_metrics["view_sum_cv"]),
        "view_sum_ratio": float(projection_metrics["view_sum_ratio"]),
        "minimum_positive_bin_fraction_per_view": float(
            projection_metrics["minimum_positive_bin_fraction_per_view"]
        ),
        "outer_8px_count_fraction": float(
            projection_metrics["outer_8px_count_fraction"]
        ),
        "detector_centroid_y_range_px": list(
            projection_metrics["detector_centroid_y_range_px"]
        ),
        "detector_centroid_x_range_px": list(
            projection_metrics["detector_centroid_x_range_px"]
        ),
        "lesions": lesions,
    }


def _case_audit(
    dataset_root: Path,
    record: Any,
    board_path: Path,
) -> dict[str, Any]:
    metadata = _read_json(
        _artifact(dataset_root, record, "metadata_json"),
        f"{record.case_id} metadata",
    )
    with np.load(
        _artifact(dataset_root, record, "phantom_npz"),
        allow_pickle=False,
    ) as archive:
        arrays = {name: archive[name].copy() for name in archive.files}
    provenance = _read_json(
        _artifact(dataset_root, record, "simind_run_provenance"),
        f"{record.case_id} SIMIND provenance",
    )
    shape_raw = provenance.get("expected_shape")
    if not isinstance(shape_raw, list) or tuple(shape_raw) != EXPECTED_PROJECTION_SHAPE:
        raise Task12GAuditError(
            f"{record.case_id}: projection shape is not {EXPECTED_PROJECTION_SHAPE}"
        )
    projection_map = np.memmap(
        _artifact(dataset_root, record, "projection_a00"),
        dtype="<f4",
        mode="r",
        shape=EXPECTED_PROJECTION_SHAPE,
    )
    try:
        projection = np.asarray(projection_map)
        metrics = _projection_metrics(projection)
        row = _case_row(record, metadata, metrics)
        float_keys = (
            "activity_probability",
            "activity_relative",
            "mu_input_140kev",
            "mu_true_140kev",
            "simind_source_weights",
        )
        arrays_finite = all(np.isfinite(arrays[name]).all() for name in float_keys)
        arrays_nonnegative = all(np.all(arrays[name] >= 0) for name in float_keys)
        liver_mask = arrays["liver_mask"].astype(bool)
        tumor_mask = arrays["tumor_union_mask"].astype(bool)
        body_mask = arrays["body_mask"].astype(bool)
        containment = not np.any(tumor_mask & ~liver_mask)
        probability_sum = float(
            arrays["activity_probability"].sum(dtype=np.float64)
        )
        source_sum = float(
            arrays["simind_source_weights"].sum(dtype=np.float64)
        )
        expected_source_sum = float(
            metadata["physics"]["base_histories_per_projection"]
        )
        mu_difference = float(
            np.abs(
                arrays["mu_input_140kev"] - arrays["mu_true_140kev"]
            )[body_mask].mean()
        )
        stored_projection = metadata["simulation"]["projection_stats"]
        stored_per_view = np.asarray(
            stored_projection["projection_per_view_weight_sum"],
            dtype=np.float64,
        )
        projection_binding = (
            math.isclose(
                float(stored_projection["projection_weight_sum"]),
                metrics["projection_weight_sum"],
                rel_tol=1e-9,
                abs_tol=1e-5,
            )
            and stored_per_view.shape == np.asarray(metrics["per_view"]).shape
            and np.allclose(
                stored_per_view,
                np.asarray(metrics["per_view"]),
                rtol=1e-9,
                atol=1e-5,
            )
        )
        quality = metadata["quality_control"]
        shape_quality = quality["liver_shape_quality"]
        detector_guard = 4.0
        detector_upper = EXPECTED_PROJECTION_SHAPE[1] - 1 - detector_guard
        detector_ok = all(
            detector_guard <= float(value) <= detector_upper
            for bounds in (
                metrics["detector_centroid_y_range_px"],
                metrics["detector_centroid_x_range_px"],
            )
            for value in bounds
        )
        gates = {
            "artifact_hashes_and_idempotent_freeze": True,
            "arrays_finite": arrays_finite,
            "arrays_nonnegative": arrays_nonnegative,
            "complete_tumor_containment": (
                containment and quality["complete_tumor_containment"] is True
            ),
            "activity_probability_normalized": math.isclose(
                probability_sum,
                1.0,
                rel_tol=0.0,
                abs_tol=2e-6,
            ),
            "simind_source_sum_matches_base_histories": math.isclose(
                source_sum,
                expected_source_sum,
                rel_tol=2e-6,
                abs_tol=0.02,
            ),
            "mu_true_input_separated": mu_difference > 1e-5,
            "liver_shape_quality": (
                shape_quality["status"] == "pass"
                and all(bool(value) for value in shape_quality["gates"].values())
            ),
            "torso_anatomy_quality": quality["torso_anatomy"]["passed"] is True,
            "metadata_quality_status": (
                quality["status"] == "pass" and quality["failed_gates"] == []
            ),
            "projection_metadata_binding": projection_binding,
            "projection_view_cv": metrics["view_sum_cv"] <= 1.5,
            "projection_view_ratio": metrics["view_sum_ratio"] <= 50.0,
            "projection_positive_support": (
                metrics["minimum_positive_bin_fraction_per_view"] >= 0.001
            ),
            "projection_outer_support": (
                metrics["outer_8px_count_fraction"] <= 0.01
            ),
            "projection_centroid_guard_band": detector_ok,
        }
        center = _selected_center(arrays["tumor_instance_mask"])
        board_sha = _render_case_board(
            board_path,
            record.case_id,
            metadata,
            arrays,
            projection,
            metrics,
            center=center,
        )
    finally:
        del projection_map
    row.update(
        {
            "status": "pass" if all(gates.values()) else "fail",
            "gates": gates,
            "activity_probability_sum": probability_sum,
            "simind_source_weight_sum": source_sum,
            "mean_absolute_mu_input_true_difference_cm1": mu_difference,
            "selected_center_zyx": list(center),
            "visual": {
                "path": str(board_path.resolve()),
                "sha256": board_sha,
            },
        }
    )
    return row


def _render_categorical_statistics(
    rows: Sequence[Mapping[str, Any]],
    output: Path,
) -> str:
    figure, axes = plt.subplots(2, 3, figsize=(15, 9), constrained_layout=True)
    definitions = (
        ("split", "Frozen split"),
        ("sex", "Sex"),
        ("liver_morphology", "Liver morphology"),
        ("lobe_extent", "Tumor lobe extent"),
        ("injection_territory", "Injection territory"),
        ("mismatch_challenge", "Challenge label (not prevalence)"),
    )
    colors = ("#4c78a8", "#f28e2b", "#59a14f", "#e15759", "#b07aa1", "#76b7b2")
    for axis, (field, title), color in zip(axes.flat, definitions, colors):
        counts = Counter(str(row[field]) for row in rows)
        labels = list(counts)
        values = [counts[label] for label in labels]
        axis.bar(labels, values, color=color, edgecolor="#333333", linewidth=0.5)
        axis.set_title(title)
        axis.set_ylabel("cases")
        axis.tick_params(axis="x", rotation=25)
        for index, value in enumerate(values):
            axis.text(index, value, str(value), ha="center", va="bottom", fontsize=8)
        axis.grid(axis="y", alpha=0.2)
    figure.suptitle("Task 12G Linux50 categorical coverage", fontsize=15, fontweight="bold")
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(output, _png_bytes(figure))
    return sha256_file(output)


def _render_liver_statistics(
    rows: Sequence[Mapping[str, Any]],
    output: Path,
) -> str:
    figure, axes = plt.subplots(2, 3, figsize=(15, 9), constrained_layout=True)
    definitions = (
        ("liver_volume_ml", "Liver volume", "mL"),
        ("liver_extent_si_mm", "SI extent", "mm"),
        ("liver_extent_ap_mm", "AP extent", "mm"),
        ("liver_extent_lr_mm", "LR extent", "mm"),
        ("left_fraction", "Anatomical left fraction", "fraction"),
        ("s1_3_to_s4_8_ratio", "SI–III / SIV–VIII proxy", "ratio"),
    )
    for axis, (field, title, unit) in zip(axes.flat, definitions):
        population = [
            float(row[field]) for row in rows if not row["mismatch_challenge"]
        ]
        challenge = [
            float(row[field]) for row in rows if row["mismatch_challenge"]
        ]
        axis.hist(
            population,
            bins=min(10, max(4, int(math.sqrt(len(population))))),
            color="#4c78a8",
            alpha=0.75,
            label=f"population n={len(population)}",
        )
        for value in challenge:
            axis.axvline(value, color="#e15759", linewidth=1.3, alpha=0.9)
        axis.set_title(title)
        axis.set_xlabel(unit)
        axis.set_ylabel("cases")
        axis.grid(axis="y", alpha=0.2)
    axes[0, 0].legend(frameon=False, fontsize=8)
    figure.suptitle(
        "Task 12G Linux50 liver distributions\nred lines are coverage challenges, not prevalence",
        fontsize=14,
        fontweight="bold",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(output, _png_bytes(figure))
    return sha256_file(output)


def _render_tumor_activity_statistics(
    rows: Sequence[Mapping[str, Any]],
    output: Path,
) -> str:
    figure, axes = plt.subplots(2, 3, figsize=(15, 9), constrained_layout=True)
    definitions = (
        ("tumor_count", "Lesion count", "count"),
        ("dmax_mm", "Maximum lesion RECIST", "mm"),
        ("tumor_fraction_liver", "Tumor / liver volume", "fraction"),
        ("tnr_mean_median", "Median lesion TNRmean", "ratio"),
        ("tnr_max_maximum", "Maximum lesion TNRmax", "ratio"),
        ("necrotic_fraction_max", "Maximum necrotic fraction", "fraction"),
    )
    for axis, (field, title, unit) in zip(axes.flat, definitions):
        population = [
            float(row[field]) for row in rows if not row["mismatch_challenge"]
        ]
        challenge = [
            float(row[field]) for row in rows if row["mismatch_challenge"]
        ]
        axis.hist(
            population,
            bins=min(10, max(4, int(math.sqrt(len(population))))),
            color="#59a14f",
            alpha=0.75,
        )
        for value in challenge:
            axis.axvline(value, color="#e15759", linewidth=1.3, alpha=0.9)
        axis.set_title(title)
        axis.set_xlabel(unit)
        axis.set_ylabel("cases")
        axis.grid(axis="y", alpha=0.2)
    figure.suptitle(
        "Task 12G Linux50 tumor and activity distributions\nred lines are coverage challenges, not prevalence",
        fontsize=14,
        fontweight="bold",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(output, _png_bytes(figure))
    return sha256_file(output)


def _render_projection_statistics(
    rows: Sequence[Mapping[str, Any]],
    output: Path,
) -> str:
    figure, axes = plt.subplots(2, 3, figsize=(15, 9), constrained_layout=True)
    definitions = (
        ("projection_weight_sum", "Projection total weight", "absolute weight"),
        ("view_sum_cv", "Per-view coefficient of variation", "CV"),
        ("view_sum_ratio", "Per-view max/min ratio", "ratio"),
        (
            "minimum_positive_bin_fraction_per_view",
            "Minimum positive detector support",
            "fraction",
        ),
        ("outer_8px_count_fraction", "Outer 8-pixel weight", "fraction"),
        (
            "injection_tumor_coverage_fraction",
            "Injection tumor coverage",
            "fraction",
        ),
    )
    for axis, (field, title, unit) in zip(axes.flat, definitions):
        values = [float(row[field]) for row in rows]
        colors = [
            "#e15759" if row["mismatch_challenge"] else "#f28e2b"
            for row in rows
        ]
        axis.scatter(range(len(rows)), values, c=colors, s=22, edgecolors="none")
        axis.set_title(title)
        axis.set_xlabel("manifest case index")
        axis.set_ylabel(unit)
        axis.grid(alpha=0.2)
    figure.suptitle(
        "Task 12G Linux50 absolute projection and support metrics\nred=coverage challenge",
        fontsize=14,
        fontweight="bold",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(output, _png_bytes(figure))
    return sha256_file(output)


def _aggregate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    continuous_fields = (
        "age_years",
        "bmi",
        "liver_volume_ml",
        "liver_extent_si_mm",
        "liver_extent_ap_mm",
        "liver_extent_lr_mm",
        "left_fraction",
        "s1_3_to_s4_8_ratio",
        "surface_roughness",
        "tumor_count",
        "dmax_mm",
        "tumor_fraction_liver",
        "tnr_mean_median",
        "tnr_max_maximum",
        "necrotic_fraction_max",
        "injection_tumor_coverage_fraction",
        "projection_weight_sum",
        "view_sum_cv",
        "view_sum_ratio",
        "minimum_positive_bin_fraction_per_view",
        "outer_8px_count_fraction",
    )
    categorical_fields = (
        "split",
        "sex",
        "liver_morphology",
        "lobe_extent",
        "injection_territory",
        "mismatch_challenge",
    )
    partition = partition_population_and_challenges(rows)
    return {
        "case_count": len(rows),
        "population_case_count": partition["population_count"],
        "challenge_case_count": partition["challenge_count"],
        "challenge_case_ids": partition["challenge_case_ids"],
        "challenge_semantics": partition["challenge_semantics"],
        "categorical_counts": {
            field: dict(Counter(str(row[field]) for row in rows))
            for field in categorical_fields
        },
        "continuous_all_cases": {
            field: _numeric_summary(float(row[field]) for row in rows)
            for field in continuous_fields
        },
        "continuous_population_only": {
            field: _numeric_summary(
                float(row[field])
                for row in rows
                if not row["mismatch_challenge"]
            )
            for field in continuous_fields
        },
    }


def _markdown(report: Mapping[str, Any]) -> str:
    aggregate = report["aggregate_statistics"]
    lines = [
        "# Task 12G Linux50 Generator/statistical/visual gate",
        "",
        f"- Status: **{str(report['status']).upper()}**",
        f"- Dataset: `{report['dataset_id']}` / `{report['dataset_version']}`",
        f"- Cases: `{report['case_count']}`",
        f"- Manifest SHA-256: `{report['manifest_sha256']}`",
        f"- Population/challenge: `{aggregate['population_case_count']}` / "
        f"`{aggregate['challenge_case_count']}`",
        f"- Challenge semantics: `{aggregate['challenge_semantics']}`",
        f"- Automatic case gates: `{report['passed_case_count']}/{report['case_count']}`",
        "- 500-case generation: **NOT APPROVED**",
        "",
        "## Global gates",
        "",
        "| Gate | Status |",
        "|---|---:|",
    ]
    lines.extend(
        f"| `{name}` | **{'PASS' if passed else 'FAIL'}** |"
        for name, passed in report["global_gates"].items()
    )
    lines.extend(
        [
            "",
            "The three mismatch cases are zero-population-weight coverage challenges. "
            "Their 3/50 count is not a clinical prevalence estimate.",
            "",
            "## Evidence",
            "",
        ]
    )
    for name, artifact in report["visual_artifacts"]["statistics"].items():
        lines.append(f"- {name}: `{artifact['path']}` (`{artifact['sha256']}`)")
    lines.extend(
        [
            f"- Per-case boards: `{report['visual_artifacts']['case_board_root']}`",
            f"- Case metrics JSONL: `{report['case_metrics_path']}`",
            "",
        ]
    )
    return "\n".join(lines)


def audit_task12g(
    dataset_root: str | Path,
    qa_root: str | Path,
) -> dict[str, Any]:
    """Run the independent frozen-data audit and render official evidence."""

    try:
        root, output = ensure_qa_root_outside_dataset(dataset_root, qa_root)
    except ValueError as exc:
        raise Task12GAuditError(str(exc)) from exc
    marker, records = validate_frozen_dataset(root)
    output.mkdir(parents=True, exist_ok=True)
    case_root = output / "cases"
    statistics_root = output / "statistics"
    case_root.mkdir(parents=True, exist_ok=True)
    statistics_root.mkdir(parents=True, exist_ok=True)

    case_rows: list[dict[str, Any]] = []
    for record in records:
        row = _case_audit(root, record, case_root / f"{record.case_id}.png")
        case_rows.append(row)
        print(
            json.dumps(
                {"case_id": record.case_id, "status": row["status"]},
                ensure_ascii=False,
            ),
            flush=True,
        )

    partition = partition_population_and_challenges(case_rows)
    failed_ids = [row["case_id"] for row in case_rows if row["status"] != "pass"]
    focus_cases = select_focus_cases(case_rows, failed_case_ids=failed_ids)
    groups = group_case_ids([row["case_id"] for row in case_rows], group_size=10)
    aggregate = _aggregate(case_rows)

    statistics_paths = {
        "categorical_coverage": statistics_root / "categorical_coverage.png",
        "liver_distributions": statistics_root / "liver_distributions.png",
        "tumor_activity_distributions": statistics_root
        / "tumor_activity_distributions.png",
        "projection_metrics": statistics_root / "projection_metrics.png",
    }
    statistic_artifacts = {
        "categorical_coverage": {
            "path": str(statistics_paths["categorical_coverage"].resolve()),
            "sha256": _render_categorical_statistics(
                case_rows,
                statistics_paths["categorical_coverage"],
            ),
        },
        "liver_distributions": {
            "path": str(statistics_paths["liver_distributions"].resolve()),
            "sha256": _render_liver_statistics(
                case_rows,
                statistics_paths["liver_distributions"],
            ),
        },
        "tumor_activity_distributions": {
            "path": str(
                statistics_paths["tumor_activity_distributions"].resolve()
            ),
            "sha256": _render_tumor_activity_statistics(
                case_rows,
                statistics_paths["tumor_activity_distributions"],
            ),
        },
        "projection_metrics": {
            "path": str(statistics_paths["projection_metrics"].resolve()),
            "sha256": _render_projection_statistics(
                case_rows,
                statistics_paths["projection_metrics"],
            ),
        },
    }
    case_metrics_path = output / "case_metrics.jsonl"
    case_metrics_payload = "".join(
        json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
        for row in case_rows
    ).encode("utf-8")
    atomic_write_bytes(case_metrics_path, case_metrics_payload)

    visual_registry = {
        "schema_version": VISUAL_REGISTRY_SCHEMA,
        "dataset_id": marker.dataset_id,
        "manifest_sha256": marker.manifest_sha256,
        "case_board_root": str(case_root.resolve()),
        "case_boards": {
            row["case_id"]: row["visual"] for row in case_rows
        },
        "case_groups": groups,
        "focus_cases": focus_cases,
        "statistics": statistic_artifacts,
        "direction_labels": _direction_labels(),
        "authority": "official_visual_evidence_generated_by_independent_gate",
    }
    visual_registry_path = output / "visual_artifacts.json"
    atomic_write_json(visual_registry_path, visual_registry)

    all_cases_pass = all(row["status"] == "pass" for row in case_rows)
    global_gates = {
        "frozen_manifest_and_all_artifact_hashes": True,
        "dataset_identity_and_manifest_sha256": (
            marker.dataset_id == EXPECTED_DATASET_ID
            and marker.dataset_version == EXPECTED_DATASET_VERSION
            and marker.manifest_sha256 == EXPECTED_MANIFEST_SHA256
        ),
        "exactly_50_canonical_cases": (
            [row["case_id"] for row in case_rows] == list(EXPECTED_CASE_IDS)
        ),
        "split_40_5_5": Counter(row["split"] for row in case_rows)
        == EXPECTED_SPLITS,
        "challenge_semantics_exact": (
            partition["challenge_case_ids"] == list(EXPECTED_CASE_IDS[:3])
            and partition["challenge_semantics"] == MISMATCH_CHALLENGE_SEMANTICS
        ),
        "all_case_automatic_gates": all_cases_pass,
        "all_50_case_boards_rendered": len(visual_registry["case_boards"])
        == EXPECTED_CASE_COUNT,
    }
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "pass" if all(global_gates.values()) else "fail",
        "generated_utc": _utc_now(),
        "dataset_root": str(root),
        "qa_root": str(output),
        "dataset_id": marker.dataset_id,
        "dataset_version": marker.dataset_version,
        "dataset_role": marker.dataset_role,
        "case_count": len(case_rows),
        "passed_case_count": sum(row["status"] == "pass" for row in case_rows),
        "manifest_sha256": marker.manifest_sha256,
        "completion_marker_sha256": sha256_file(root / "DATASET_COMPLETE.json"),
        "global_gates": global_gates,
        "aggregate_statistics": aggregate,
        "focus_cases": focus_cases,
        "case_groups": groups,
        "failed_case_ids": failed_ids,
        "case_metrics_path": str(case_metrics_path.resolve()),
        "case_metrics_sha256": sha256_file(case_metrics_path),
        "visual_artifacts": {
            "path": str(visual_registry_path.resolve()),
            "sha256": sha256_file(visual_registry_path),
            "case_board_root": str(case_root.resolve()),
            "statistics": statistic_artifacts,
        },
        "projection_coordinate_contract_id": marker.projection_coordinate_contract_id,
        "loader_transform_id": marker.loader_transform_id,
        "absolute_projection_scale_retained": True,
        "manual_review_required": True,
        "manual_review_status": "pending",
        "go_for_500_case_generation": False,
        "next_action": (
            "run PAR-S_2 manifest loader and Task 12B projection gates, then "
            "review the read-only Notebook"
        ),
    }
    atomic_write_json(output / "generator_gate.json", report)
    atomic_write_bytes(
        output / "generator_gate.md",
        (_markdown(report) + "\n").encode("utf-8"),
    )
    return report


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = audit_task12g(args.dataset_root, args.qa_root)
    except Exception as exc:
        print(
            json.dumps(
                {"status": "fail", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "status": report["status"],
                "case_count": report["case_count"],
                "passed_case_count": report["passed_case_count"],
                "generator_gate": str(
                    (Path(report["qa_root"]) / "generator_gate.json").resolve()
                ),
                "go_for_500_case_generation": False,
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
