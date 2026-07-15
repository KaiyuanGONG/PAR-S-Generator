"""Read-only statistical and visual acceptance audit for the frozen V2 pilot15."""

from __future__ import annotations

import argparse
import io
import json
import math
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import matplotlib

matplotlib.use("Agg", force=True)

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import ListedColormap
from matplotlib.patches import Patch
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from skimage.measure import marching_cubes


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.case_writer_v2 import (  # noqa: E402
    DatasetContractV2,
    DatasetFreezeRecordV2,
    freeze_dataset,
    load_case_record_v2,
)
from core.provenance import atomic_write_bytes, atomic_write_json, sha256_file  # noqa: E402


SCHEMA_VERSION = "pars_v2_pilot15_acceptance_audit_v1"
EXPECTED_CASE_COUNT = 15
PROJECTION_SHAPE = (60, 128, 128)
MANUAL_REVIEW_SCHEMA = "pars_v2_pilot15_manual_visual_review_v1"


class Pilot15AuditError(RuntimeError):
    """Raised when frozen bytes or acceptance evidence fail closed."""


@dataclass(frozen=True)
class CaseOverview:
    case_id: str
    split: str
    sex: str
    morphology: str
    injection: str
    mismatch: bool
    bmi: float
    liver_volume_ml: float
    left_fraction: float
    roughness: float
    tumor_count: int
    recist_mm: tuple[float, ...]
    projection_sum: float
    selected_zyx: tuple[int, int, int]
    axial_mu: np.ndarray
    axial_liver: np.ndarray
    axial_tumor: np.ndarray
    axial_perfusion: np.ndarray
    liver_small: np.ndarray
    tumor_small: np.ndarray
    projection_sinogram: np.ndarray
    projection_per_view: np.ndarray


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Pilot15AuditError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise Pilot15AuditError(f"{path} must contain a JSON object")
    return value


def _outside_dataset(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return resolved
    raise Pilot15AuditError(f"{label} must be outside the immutable dataset root")


def _artifact(root: Path, record: Any, name: str) -> Path:
    try:
        relative = record.artifacts[name].relative_path
    except KeyError as exc:
        raise Pilot15AuditError(f"{record.case_id}: missing artifact {name}") from exc
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise Pilot15AuditError(f"{record.case_id}: unsafe artifact path {name}") from exc
    return path


def validate_frozen_dataset(root: Path) -> tuple[DatasetFreezeRecordV2, tuple[Any, ...]]:
    marker = DatasetFreezeRecordV2.from_dict(_read_json(root / "DATASET_COMPLETE.json"))
    if marker.status != "complete" or marker.case_count != EXPECTED_CASE_COUNT:
        raise Pilot15AuditError("audit requires exactly 15 formally complete frozen cases")
    manifest_path = (root / marker.manifest_relative_path).resolve()
    if sha256_file(manifest_path) != marker.manifest_sha256:
        raise Pilot15AuditError("case manifest differs from DATASET_COMPLETE.json")
    lines = manifest_path.read_text(encoding="utf-8").splitlines()
    if len(lines) != EXPECTED_CASE_COUNT or any(not line.strip() for line in lines):
        raise Pilot15AuditError("frozen manifest must contain exactly 15 non-blank records")
    records = tuple(
        load_case_record_v2(
            root / "cases" / str(json.loads(line)["case_id"]) / "case_record.json",
            dataset_root=root,
            verify_hashes=True,
        )
        for line in lines
    )
    ids = tuple(record.case_id for record in records)
    if ids != tuple(sorted(ids)) or len(set(ids)) != EXPECTED_CASE_COUNT:
        raise Pilot15AuditError("case IDs must be unique and canonically sorted")
    contract = DatasetContractV2(
        output_root=root,
        dataset_id=marker.dataset_id,
        dataset_version=marker.dataset_version,
        dataset_role=marker.dataset_role,
        expected_case_ids=ids,
        allowed_profile_ids=tuple(sorted({record.profile_id for record in records})),
        split_plan_sha256=marker.split_plan_sha256,
        required_artifact_names=tuple(marker.required_artifact_names),
    )
    if freeze_dataset(records, contract) != marker:
        raise Pilot15AuditError("idempotent read-only freeze re-audit changed the marker")
    return marker, records


def _projection_metrics(projection: np.ndarray) -> dict[str, Any]:
    if projection.shape != PROJECTION_SHAPE:
        raise Pilot15AuditError(f"unexpected projection shape {projection.shape}")
    if not np.isfinite(projection).all() or np.any(projection < 0):
        raise Pilot15AuditError("projection contains non-finite or negative bins")
    per_view = projection.sum(axis=(1, 2), dtype=np.float64)
    positive = projection > 0
    positive_fraction = positive.mean(axis=(1, 2), dtype=np.float64)
    outer = np.zeros(projection.shape[1:], dtype=bool)
    outer[:8, :] = True
    outer[-8:, :] = True
    outer[:, :8] = True
    outer[:, -8:] = True
    total = float(per_view.sum())
    outer_fraction = float(projection[:, outer].sum(dtype=np.float64) / total)
    yy, xx = np.indices(projection.shape[1:], dtype=np.float64)
    view_safe = np.maximum(per_view, np.finfo(np.float64).tiny)
    cy = (projection * yy[None]).sum(axis=(1, 2), dtype=np.float64) / view_safe
    cx = (projection * xx[None]).sum(axis=(1, 2), dtype=np.float64) / view_safe
    positive_views = per_view[per_view > 0]
    ratio = float(positive_views.max() / positive_views.min())
    return {
        "projection_weight_sum": total,
        "view_sum_cv": float(per_view.std() / per_view.mean()),
        "view_sum_ratio": ratio,
        "minimum_positive_bin_fraction_per_view": float(positive_fraction.min()),
        "outer_8px_count_fraction": outer_fraction,
        "detector_centroid_y_range_px": [float(cy.min()), float(cy.max())],
        "detector_centroid_x_range_px": [float(cx.min()), float(cx.max())],
        "per_view": per_view,
        "sinogram": projection.sum(axis=1, dtype=np.float64),
    }


def _selected_center(instances: np.ndarray) -> tuple[int, int, int]:
    labels, counts = np.unique(instances[instances > 0], return_counts=True)
    if not len(labels):
        raise Pilot15AuditError("tumor instance mask is empty")
    label = labels[int(np.argmax(counts))]
    center = np.rint(np.argwhere(instances == label).mean(axis=0)).astype(int)
    return tuple(int(value) for value in center)


def _crop(mask: np.ndarray, padding: int = 4) -> tuple[slice, slice]:
    indices = np.argwhere(mask)
    if not len(indices):
        return slice(None), slice(None)
    lo = np.maximum(indices.min(axis=0) - padding, 0)
    hi = np.minimum(indices.max(axis=0) + padding + 1, mask.shape)
    return slice(int(lo[0]), int(hi[0])), slice(int(lo[1]), int(hi[1]))


def _plane(array: np.ndarray, name: str, center: tuple[int, int, int]) -> np.ndarray:
    z, y, x = center
    if name == "axial":
        return array[z]
    if name == "coronal":
        return array[:, y, :]
    if name == "sagittal":
        return array[:, :, x]
    raise ValueError(name)


def _draw_anatomy(
    axis: Any,
    background: np.ndarray,
    liver: np.ndarray,
    tumor: np.ndarray,
    perfusion: np.ndarray,
    *,
    title: str,
) -> None:
    crop = _crop(liver | tumor | perfusion)
    background = background[crop]
    liver = liver[crop]
    tumor = tumor[crop]
    perfusion = perfusion[crop]
    foreground = background[background > 0]
    if foreground.size:
        vmin, vmax = np.percentile(foreground, (1, 99.5))
    else:
        vmin, vmax = float(background.min()), float(background.max()) + 1e-6
    axis.imshow(background, cmap="gray", origin="lower", vmin=vmin, vmax=vmax)
    if tumor.any():
        axis.imshow(
            np.ma.masked_where(~tumor, tumor),
            cmap=ListedColormap(("#ff476f",)),
            origin="lower",
            interpolation="nearest",
            alpha=0.66,
            vmin=1,
            vmax=1,
        )
    if liver.any() and not liver.all():
        axis.contour(liver, levels=(0.5,), colors=("#45d483",), linewidths=(1.0,))
    if perfusion.any() and not perfusion.all():
        axis.contour(
            perfusion,
            levels=(0.5,),
            colors=("#00b8e6",),
            linewidths=(0.9,),
            linestyles=("--",),
        )
    axis.set_title(title, fontsize=8)
    axis.set_xticks([])
    axis.set_yticks([])


def _draw_surface(axis: Any, liver: np.ndarray, tumor: np.ndarray, title: str) -> None:
    for mask, color, alpha in ((liver, "#c6a15b", 0.22), (tumor, "#d83b5d", 0.95)):
        if not mask.any():
            continue
        try:
            vertices, faces, _, _ = marching_cubes(
                np.pad(mask.astype(np.uint8), 1), 0.5, step_size=2
            )
            vertices -= 1.0
        except (RuntimeError, ValueError):
            center = np.argwhere(mask).mean(axis=0)
            axis.scatter(
                center[0], center[1], center[2], color=color, s=22, alpha=alpha
            )
            continue
        mesh = Poly3DCollection(vertices[faces], alpha=alpha, linewidths=0.0)
        mesh.set_facecolor(color)
        axis.add_collection3d(mesh)
    coordinates = np.argwhere(liver | tumor)
    lower = np.maximum(coordinates.min(axis=0) - 2, 0)
    upper = np.minimum(coordinates.max(axis=0) + 3, liver.shape)
    axis.set_xlim(float(lower[0]), float(upper[0]))
    axis.set_ylim(float(lower[1]), float(upper[1]))
    axis.set_zlim(float(lower[2]), float(upper[2]))
    axis.set_box_aspect(np.maximum(upper - lower, 1))
    axis.view_init(elev=24, azim=-55)
    axis.set_title(title, fontsize=8)
    axis.set_axis_off()


def _png_bytes(figure: Any, *, dpi: int = 140) -> bytes:
    buffer = io.BytesIO()
    try:
        figure.savefig(buffer, format="png", dpi=dpi, facecolor="white")
    finally:
        plt.close(figure)
    return buffer.getvalue()


def _render_case(
    output: Path,
    case_id: str,
    metadata: Mapping[str, Any],
    arrays: Mapping[str, np.ndarray],
    projection: np.ndarray,
    metrics: Mapping[str, Any],
    center: tuple[int, int, int],
) -> str:
    figure = plt.figure(figsize=(18, 9), constrained_layout=True)
    grid = figure.add_gridspec(2, 4)
    liver = arrays["liver_mask"].astype(bool)
    tumor = arrays["tumor_union_mask"].astype(bool)
    perfusion = arrays["perfusion_mask"].astype(bool)
    mu_input = arrays["mu_input_140kev"]
    for index, plane in enumerate(("axial", "coronal", "sagittal")):
        axis = figure.add_subplot(grid[0, index])
        _draw_anatomy(
            axis,
            _plane(mu_input, plane, center),
            _plane(liver, plane, center),
            _plane(tumor, plane, center),
            _plane(perfusion, plane, center),
            title=f"{plane} @ ZYX={center}",
        )
    surface_axis = figure.add_subplot(grid[0, 3], projection="3d")
    _draw_surface(surface_axis, liver[::2, ::2, ::2], tumor[::2, ::2, ::2], "3D liver / tumor")

    activity_axis = figure.add_subplot(grid[1, 0])
    activity = _plane(arrays["activity_relative"], "axial", center)
    activity_axis.imshow(np.log1p(activity), cmap="viridis", origin="lower")
    activity_axis.contour(_plane(liver, "axial", center), levels=(0.5,), colors=("white",), linewidths=(0.7,))
    activity_axis.set_title("Axial log1p activity", fontsize=8)
    activity_axis.set_axis_off()

    difference_axis = figure.add_subplot(grid[1, 1])
    difference = _plane(
        arrays["mu_input_140kev"] - arrays["mu_true_140kev"], "axial", center
    )
    limit = max(float(np.percentile(np.abs(difference), 99.5)), 1e-6)
    difference_axis.imshow(difference, cmap="coolwarm", origin="lower", vmin=-limit, vmax=limit)
    difference_axis.set_title("mu_input - mu_true (cm^-1)", fontsize=8)
    difference_axis.set_axis_off()

    sinogram_axis = figure.add_subplot(grid[1, 2])
    sinogram_axis.imshow(np.log1p(metrics["sinogram"].T), cmap="magma", origin="lower", aspect="auto")
    sinogram_axis.set_title("SIMIND sinogram (log1p)", fontsize=8)
    sinogram_axis.set_xlabel("view")
    sinogram_axis.set_ylabel("detector u")

    curve_axis = figure.add_subplot(grid[1, 3])
    curve_axis.plot(metrics["per_view"], color="#345995", linewidth=1.3)
    curve_axis.set_title(
        f"Per-view counts | CV={metrics['view_sum_cv']:.3f} | outer={metrics['outer_8px_count_fraction']:.3g}",
        fontsize=8,
    )
    curve_axis.set_xlabel("view")
    curve_axis.grid(alpha=0.25)
    lesions = metadata["actual_metrics"]["tumors"]["lesions"]
    recist = "/".join(f"{float(item['recist_3d_mm']):.1f}" for item in lesions)
    figure.suptitle(
        f"{case_id} | {metadata['split']} | {metadata['patient']['liver_morphology']} | "
        f"RECIST {recist} mm | {metadata['activity']['injection_territory']} | "
        f"mismatch={metadata['activity']['mismatch_challenge']}",
        fontsize=13,
        fontweight="bold",
    )
    payload = _png_bytes(figure)
    atomic_write_bytes(output, payload)
    return sha256_file(output)


def _numeric_summary(values: Iterable[float]) -> dict[str, float]:
    array = np.asarray(tuple(values), dtype=np.float64)
    if not len(array) or not np.isfinite(array).all():
        raise Pilot15AuditError("summary values must be non-empty and finite")
    return {
        "min": float(array.min()),
        "p25": float(np.percentile(array, 25)),
        "median": float(np.median(array)),
        "mean": float(array.mean()),
        "p75": float(np.percentile(array, 75)),
        "max": float(array.max()),
    }


def _case_audit(
    root: Path,
    record: Any,
    case_output: Path,
) -> tuple[dict[str, Any], CaseOverview, str]:
    metadata = _read_json(_artifact(root, record, "metadata_json"))
    with np.load(_artifact(root, record, "phantom_npz"), allow_pickle=False) as archive:
        arrays = {key: archive[key].copy() for key in archive.files}
    provenance = _read_json(_artifact(root, record, "simind_run_provenance"))
    shape = tuple(int(value) for value in provenance.get("expected_shape", ()))
    if shape != PROJECTION_SHAPE:
        raise Pilot15AuditError(f"{record.case_id}: projection provenance shape is {shape}")
    projection_map = np.memmap(
        _artifact(root, record, "projection_a00"), dtype="<f4", mode="r", shape=shape
    )
    projection = np.asarray(projection_map)
    pmetrics = _projection_metrics(projection)

    liver = arrays["liver_mask"].astype(bool)
    tumor = arrays["tumor_union_mask"].astype(bool)
    instances = arrays["tumor_instance_mask"]
    body = arrays["body_mask"].astype(bool)
    float_keys = (
        "activity_probability",
        "activity_relative",
        "mu_input_140kev",
        "mu_true_140kev",
        "simind_source_weights",
    )
    arrays_finite = all(np.isfinite(arrays[key]).all() for key in float_keys)
    arrays_nonnegative = all(np.all(arrays[key] >= 0) for key in float_keys)
    containment = not np.any(tumor & ~liver)
    probability_sum = float(arrays["activity_probability"].sum(dtype=np.float64))
    source_sum = float(arrays["simind_source_weights"].sum(dtype=np.float64))
    mu_mean_abs_difference = float(
        np.abs(arrays["mu_input_140kev"] - arrays["mu_true_140kev"])[body].mean()
    )
    liver_metrics = metadata["actual_metrics"]["liver"]
    tumor_metrics = metadata["actual_metrics"]["tumors"]
    quality = metadata["quality_control"]
    shape_gates = quality["liver_shape_quality"]["gates"]
    stored_projection = metadata["simulation"]["projection_stats"]
    projection_binding = math.isclose(
        pmetrics["projection_weight_sum"],
        float(stored_projection["projection_weight_sum"]),
        rel_tol=1e-9,
        abs_tol=1e-5,
    ) and np.allclose(
        pmetrics["per_view"],
        np.asarray(stored_projection["projection_per_view_weight_sum"], dtype=np.float64),
        rtol=1e-9,
        atol=1e-5,
    )
    detector_guard = 4.0
    detector_ok = all(
        detector_guard <= value <= PROJECTION_SHAPE[1] - 1 - detector_guard
        for bounds in (
            pmetrics["detector_centroid_y_range_px"],
            pmetrics["detector_centroid_x_range_px"],
        )
        for value in bounds
    )
    gates = {
        "artifact_hashes": True,
        "arrays_finite": arrays_finite,
        "arrays_nonnegative": arrays_nonnegative,
        "complete_tumor_containment": containment and bool(quality["complete_tumor_containment"]),
        "activity_probability_normalized": math.isclose(probability_sum, 1.0, rel_tol=0, abs_tol=2e-6),
        "simind_source_sum_80000": math.isclose(source_sum, 80000.0, rel_tol=0, abs_tol=0.02),
        "mu_true_input_separated": mu_mean_abs_difference > 1e-5,
        "liver_shape_quality": quality["liver_shape_quality"]["status"] == "pass" and all(shape_gates.values()),
        "torso_anatomy_quality": bool(quality["torso_anatomy"]["passed"]),
        "metadata_quality_status": quality["status"] == "pass" and not quality["failed_gates"],
        "projection_metadata_binding": projection_binding,
        "projection_view_cv": pmetrics["view_sum_cv"] <= 1.5,
        "projection_view_ratio": pmetrics["view_sum_ratio"] <= 50.0,
        "projection_positive_support": pmetrics["minimum_positive_bin_fraction_per_view"] >= 0.001,
        "projection_outer_support": pmetrics["outer_8px_count_fraction"] <= 0.01,
        "projection_centroid_guard_band": detector_ok,
    }
    status = "pass" if all(gates.values()) else "fail"
    center = _selected_center(instances)
    png_sha = _render_case(
        case_output,
        record.case_id,
        metadata,
        arrays,
        projection,
        pmetrics,
        center,
    )
    lesion_rows = [
        {
            "recist_3d_mm": float(item["recist_3d_mm"]),
            "volume_ml": float(item["volume_ml"]),
            "sphericity": float(item["sphericity"]),
            "lobe": item.get("lobe"),
            "morphology": item.get("morphology"),
            "subcapsular": item.get("subcapsular"),
        }
        for item in tumor_metrics["lesions"]
    ]
    case_report = {
        "case_id": record.case_id,
        "split": record.split,
        "status": status,
        "patient": {
            "sex": metadata["patient"]["sex"],
            "age_years": float(metadata["patient"]["age_years"]),
            "bmi": float(metadata["patient"]["bmi"]),
            "liver_morphology": metadata["patient"]["liver_morphology"],
        },
        "liver": {
            "volume_ml": float(liver_metrics["volume_ml"]),
            "extent_mm_zyx": [float(value) for value in liver_metrics["extent_mm_zyx"]],
            "left_fraction": float(liver_metrics["left_fraction"]),
            "s1_3_to_s4_8_ratio": float(liver_metrics["s1_3_to_s4_8_ratio"]),
            "centroid_world_mm": [float(value) for value in liver_metrics["centroid_world_mm"]],
            "sphericity": float(liver_metrics["sphericity"]),
            "surface_roughness": float(liver_metrics["surface_roughness"]),
            "caudate_fraction": float(liver_metrics["caudate_fraction"]),
            "central_waist_ratio": float(liver_metrics["shape_quality"]["central_waist_ratio"]),
        },
        "tumors": {
            "count": len(lesion_rows),
            "lobe_extent": tumor_metrics["lobe_extent"],
            "tumor_fraction_liver": float(tumor_metrics["tumor_union_fraction_liver"]),
            "lesions": lesion_rows,
        },
        "activity": {
            "injection_territory": metadata["activity"]["injection_territory"],
            "mismatch_challenge": bool(metadata["activity"]["mismatch_challenge"]),
            "injection_tumor_coverage_fraction": float(metadata["activity"]["injection_tumor_coverage_fraction"]),
            "perfusion_fraction_liver": float(metadata["activity"]["perfused_volume_ml"]) / float(liver_metrics["volume_ml"]),
            "activity_probability_sum": probability_sum,
            "simind_source_weight_sum": source_sum,
        },
        "attenuation": {"mean_absolute_mu_input_true_difference_cm1": mu_mean_abs_difference},
        "projection": {key: value for key, value in pmetrics.items() if key not in {"per_view", "sinogram"}},
        "gates": gates,
        "visual": {"path": str(case_output), "sha256": png_sha},
    }
    axial_index = center[0]
    overview = CaseOverview(
        case_id=record.case_id,
        split=record.split,
        sex=str(metadata["patient"]["sex"]),
        morphology=str(metadata["patient"]["liver_morphology"]),
        injection=str(metadata["activity"]["injection_territory"]),
        mismatch=bool(metadata["activity"]["mismatch_challenge"]),
        bmi=float(metadata["patient"]["bmi"]),
        liver_volume_ml=float(liver_metrics["volume_ml"]),
        left_fraction=float(liver_metrics["left_fraction"]),
        roughness=float(liver_metrics["surface_roughness"]),
        tumor_count=len(lesion_rows),
        recist_mm=tuple(item["recist_3d_mm"] for item in lesion_rows),
        projection_sum=pmetrics["projection_weight_sum"],
        selected_zyx=center,
        axial_mu=arrays["mu_input_140kev"][axial_index].copy(),
        axial_liver=liver[axial_index].copy(),
        axial_tumor=tumor[axial_index].copy(),
        axial_perfusion=arrays["perfusion_mask"][axial_index].astype(bool).copy(),
        liver_small=liver[::2, ::2, ::2].copy(),
        tumor_small=tumor[::2, ::2, ::2].copy(),
        projection_sinogram=pmetrics["sinogram"].copy(),
        projection_per_view=pmetrics["per_view"].copy(),
    )
    del projection_map
    return case_report, overview, png_sha


def _render_contact_sheet(cases: Sequence[CaseOverview], output: Path) -> str:
    figure, axes = plt.subplots(3, 5, figsize=(18, 11), constrained_layout=True)
    for axis, case in zip(axes.flat, cases):
        _draw_anatomy(
            axis,
            case.axial_mu,
            case.axial_liver,
            case.axial_tumor,
            case.axial_perfusion,
            title=(
                f"{case.case_id} | {case.split} | {case.morphology}\n"
                f"Dmax {max(case.recist_mm):.1f} mm | {case.injection} | mismatch={case.mismatch}"
            ),
        )
    figure.suptitle("PAR-S V2 pilot15: tumor-centred axial acceptance contact sheet", fontsize=15, fontweight="bold")
    figure.legend(
        handles=(
            Patch(facecolor="#ff476f", alpha=0.66, label="tumor"),
            Patch(facecolor="#45d483", label="liver contour"),
            Patch(facecolor="#00b8e6", label="perfusion contour"),
        ),
        loc="outside lower center",
        ncols=3,
        frameon=False,
    )
    atomic_write_bytes(output, _png_bytes(figure, dpi=150))
    return sha256_file(output)


def _render_3d_sheet(cases: Sequence[CaseOverview], output: Path) -> str:
    figure = plt.figure(figsize=(18, 11), constrained_layout=True)
    for index, case in enumerate(cases, start=1):
        axis = figure.add_subplot(3, 5, index, projection="3d")
        _draw_surface(
            axis,
            case.liver_small,
            case.tumor_small,
            f"{case.case_id} | {case.morphology} | Dmax {max(case.recist_mm):.1f} mm",
        )
    figure.suptitle("PAR-S V2 pilot15: 3D liver/tumor morphology overview", fontsize=15, fontweight="bold")
    atomic_write_bytes(output, _png_bytes(figure, dpi=150))
    return sha256_file(output)


def _render_statistics(cases: Sequence[CaseOverview], output: Path) -> str:
    ids = [case.case_id.removeprefix("case_") for case in cases]
    colors = ["#8c6bb1" if case.morphology == "cirrhotic" else "#2ca25f" for case in cases]
    figure, axes = plt.subplots(2, 3, figsize=(18, 10), constrained_layout=True)
    axes[0, 0].bar(ids, [case.liver_volume_ml for case in cases], color=colors)
    axes[0, 0].set_title("Liver volume by case")
    axes[0, 0].set_ylabel("mL")
    axes[0, 0].tick_params(axis="x", rotation=60)

    for index, case in enumerate(cases):
        axes[0, 1].scatter([index] * len(case.recist_mm), case.recist_mm, color="#d1495b", s=28)
    axes[0, 1].set_xticks(range(len(cases)), ids, rotation=60)
    axes[0, 1].set_title("All lesion RECIST diameters")
    axes[0, 1].set_ylabel("mm")

    axes[0, 2].bar(ids, [case.left_fraction for case in cases], color="#4c78a8")
    axes[0, 2].set_title("Anatomical left-liver fraction")
    axes[0, 2].tick_params(axis="x", rotation=60)

    axes[1, 0].bar(ids, [case.roughness for case in cases], color=colors)
    axes[1, 0].set_title("Surface roughness")
    axes[1, 0].tick_params(axis="x", rotation=60)

    axes[1, 1].bar(ids, [case.projection_sum for case in cases], color="#f28e2b")
    axes[1, 1].set_title("SIMIND projection weight sum")
    axes[1, 1].tick_params(axis="x", rotation=60)

    categories = Counter(case.injection for case in cases)
    labels = list(categories)
    axes[1, 2].bar(labels, [categories[label] for label in labels], color="#59a14f")
    axes[1, 2].set_title("Injection territory coverage")
    axes[1, 2].tick_params(axis="x", rotation=25)
    for axis in axes.flat:
        axis.grid(axis="y", alpha=0.22)
    figure.suptitle("PAR-S V2 pilot15 statistical acceptance overview", fontsize=15, fontweight="bold")
    atomic_write_bytes(output, _png_bytes(figure, dpi=150))
    return sha256_file(output)


def _render_projection_sheet(cases: Sequence[CaseOverview], output: Path) -> str:
    figure, axes = plt.subplots(5, 6, figsize=(20, 15), constrained_layout=True)
    for case_index, case in enumerate(cases):
        row = case_index // 3
        column = (case_index % 3) * 2
        sinogram_axis = axes[row, column]
        curve_axis = axes[row, column + 1]
        sinogram_axis.imshow(
            np.log1p(case.projection_sinogram.T),
            cmap="magma",
            origin="lower",
            aspect="auto",
        )
        sinogram_axis.set_title(f"{case.case_id} sinogram", fontsize=9)
        sinogram_axis.set_xlabel("view", fontsize=7)
        sinogram_axis.set_ylabel("detector u", fontsize=7)
        normalized = case.projection_per_view / case.projection_per_view.mean()
        curve_axis.plot(normalized, color="#345995", linewidth=1.15)
        curve_axis.axhline(1.0, color="#999999", linewidth=0.7, linestyle="--")
        curve_axis.set_title(
            f"{case.case_id} per-view / mean", fontsize=9
        )
        curve_axis.set_xlabel("view", fontsize=7)
        curve_axis.set_ylim(bottom=0)
        curve_axis.grid(alpha=0.22)
    figure.suptitle(
        "PAR-S V2 pilot15: complete SIMIND projection visual acceptance sheet",
        fontsize=15,
        fontweight="bold",
    )
    atomic_write_bytes(output, _png_bytes(figure, dpi=145))
    return sha256_file(output)


def _aggregate(cases: Sequence[dict[str, Any]]) -> dict[str, Any]:
    lesions = [lesion for case in cases for lesion in case["tumors"]["lesions"]]
    return {
        "split_counts": dict(sorted(Counter(case["split"] for case in cases).items())),
        "sex_counts": dict(sorted(Counter(case["patient"]["sex"] for case in cases).items())),
        "morphology_counts": dict(sorted(Counter(case["patient"]["liver_morphology"] for case in cases).items())),
        "injection_territory_counts": dict(sorted(Counter(case["activity"]["injection_territory"] for case in cases).items())),
        "mismatch_counts": dict(sorted(Counter(str(case["activity"]["mismatch_challenge"]).lower() for case in cases).items())),
        "tumor_count_distribution": dict(sorted(Counter(str(case["tumors"]["count"]) for case in cases).items())),
        "total_lesion_count": len(lesions),
        "bmi": _numeric_summary(case["patient"]["bmi"] for case in cases),
        "liver_volume_ml": _numeric_summary(case["liver"]["volume_ml"] for case in cases),
        "liver_extent_si_mm": _numeric_summary(case["liver"]["extent_mm_zyx"][0] for case in cases),
        "liver_extent_ap_mm": _numeric_summary(case["liver"]["extent_mm_zyx"][1] for case in cases),
        "liver_extent_lr_mm": _numeric_summary(case["liver"]["extent_mm_zyx"][2] for case in cases),
        "left_fraction": _numeric_summary(case["liver"]["left_fraction"] for case in cases),
        "s1_3_to_s4_8_ratio": _numeric_summary(case["liver"]["s1_3_to_s4_8_ratio"] for case in cases),
        "surface_roughness": _numeric_summary(case["liver"]["surface_roughness"] for case in cases),
        "liver_sphericity": _numeric_summary(case["liver"]["sphericity"] for case in cases),
        "lesion_recist_3d_mm": _numeric_summary(lesion["recist_3d_mm"] for lesion in lesions),
        "lesion_volume_ml": _numeric_summary(lesion["volume_ml"] for lesion in lesions),
        "tumor_fraction_liver": _numeric_summary(case["tumors"]["tumor_fraction_liver"] for case in cases),
        "perfusion_fraction_liver": _numeric_summary(case["activity"]["perfusion_fraction_liver"] for case in cases),
        "mu_input_true_mean_abs_difference_cm1": _numeric_summary(case["attenuation"]["mean_absolute_mu_input_true_difference_cm1"] for case in cases),
        "projection_weight_sum": _numeric_summary(case["projection"]["projection_weight_sum"] for case in cases),
        "projection_view_cv": _numeric_summary(case["projection"]["view_sum_cv"] for case in cases),
        "projection_outer_8px_fraction": _numeric_summary(case["projection"]["outer_8px_count_fraction"] for case in cases),
    }


def _markdown(report: Mapping[str, Any]) -> str:
    aggregate = report["aggregate_statistics"]
    lines = [
        "# PAR-S V2 15 例冻结数据统计与视觉验收",
        "",
        f"- 自动验收：**{str(report['status']).upper()}**",
        f"- 数据集：`{report['dataset_id']}` / `{report['dataset_version']}`",
        f"- Manifest SHA-256：`{report['manifest_sha256']}`",
        f"- 病例 / 病灶：{report['case_count']} / {aggregate['total_lesion_count']}",
        f"- Split：{aggregate['split_counts']}",
        f"- 正常 / 肝硬化：{aggregate['morphology_counts']}",
        f"- 人工视觉审核：**{report['manual_review']['status'].upper()}**",
        "",
        "## 核心统计",
        "",
        "| 指标 | Min | Median | Mean | Max |",
        "|---|---:|---:|---:|---:|",
    ]
    for label, key in (
        ("BMI", "bmi"),
        ("肝体积 (mL)", "liver_volume_ml"),
        ("SI 径 (mm)", "liver_extent_si_mm"),
        ("AP 径 (mm)", "liver_extent_ap_mm"),
        ("LR 径 (mm)", "liver_extent_lr_mm"),
        ("左叶比例", "left_fraction"),
        ("SI-III / SIV-VIII", "s1_3_to_s4_8_ratio"),
        ("表面粗糙度", "surface_roughness"),
        ("病灶 RECIST (mm)", "lesion_recist_3d_mm"),
        ("肿瘤/肝体积分数", "tumor_fraction_liver"),
        ("投影权重和", "projection_weight_sum"),
    ):
        value = aggregate[key]
        lines.append(
            f"| {label} | {value['min']:.4g} | {value['median']:.4g} | {value['mean']:.4g} | {value['max']:.4g} |"
        )
    lines.extend(
        [
            "",
            "## 逐例自动门禁",
            "",
            "| Case | Split | 性别 | 肝形态 | 肝体积 mL | 病灶数 | RECIST mm | 注射区 | Mismatch | 自动门禁 |",
            "|---|---|---|---|---:|---:|---|---|---|---|",
        ]
    )
    for case in report["cases"]:
        recist = ", ".join(f"{item['recist_3d_mm']:.1f}" for item in case["tumors"]["lesions"])
        lines.append(
            f"| {case['case_id']} | {case['split']} | {case['patient']['sex']} | "
            f"{case['patient']['liver_morphology']} | {case['liver']['volume_ml']:.1f} | "
            f"{case['tumors']['count']} | {recist} | {case['activity']['injection_territory']} | "
            f"{case['activity']['mismatch_challenge']} | {case['status'].upper()} |"
        )
    lines.extend(
        [
            "",
            "## 解释与限制",
            "",
            "- 自动 PASS 证明冻结字节、哈希、payload、containment、形态元数据门禁、衰减图分离和投影工程质量内部一致。",
            "- 自动门禁不能代替对肝轮廓解剖观感、肿瘤形态、灌注边界和投影视觉伪影的人工判断。",
            "- pilot15 v1 未冻结实际 Python/Conda 环境，也未证明 preflight 与最终生成源图逐字节一致；因此本轮不得批准 50 例扩展。",
            "- 480-transform 临床 alignment 搜索是探索性诊断，不要求唯一 top-1；专用坐标 fixture 才是阻断坐标门禁。",
            "",
        ]
    )
    return "\n".join(lines)


def _manual_checklist(marker: DatasetFreezeRecordV2, cases: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": MANUAL_REVIEW_SCHEMA,
        "status": "pending",
        "dataset_id": marker.dataset_id,
        "dataset_version": marker.dataset_version,
        "manifest_sha256": marker.manifest_sha256,
        "review_scope": [
            "liver_contour_continuity_and_anatomical_plausibility",
            "normal_vs_cirrhotic_morphology_plausibility",
            "tumor_shape_size_lobe_and_full_containment",
            "perfusion_territory_and_mismatch_plausibility",
            "mu_input_degradation_without_structural_corruption",
            "projection_support_no_clipping_and_smooth_view_evolution",
        ],
        "cases": [
            {
                "case_id": case["case_id"],
                "visual_path": case["visual"]["path"],
                "expected": {
                    "liver_morphology": case["patient"]["liver_morphology"],
                    "lesion_recist_mm": [item["recist_3d_mm"] for item in case["tumors"]["lesions"]],
                    "injection_territory": case["activity"]["injection_territory"],
                    "mismatch_challenge": case["activity"]["mismatch_challenge"],
                },
                "review_status": "pending",
                "reviewer_notes": "",
            }
            for case in cases
        ],
        "release_constraint": "manual approval alone cannot release 50 cases until runtime environment binding is fixed",
    }


def audit_pilot15(dataset_root: Path, output_dir: Path) -> dict[str, Any]:
    root = Path(dataset_root).resolve()
    if not root.is_dir():
        raise Pilot15AuditError(f"dataset root does not exist: {root}")
    output = _outside_dataset(Path(output_dir), root, "output directory")
    output.mkdir(parents=True, exist_ok=True)
    case_dir = output / "cases"
    case_dir.mkdir(parents=True, exist_ok=True)
    marker, records = validate_frozen_dataset(root)
    case_reports: list[dict[str, Any]] = []
    overviews: list[CaseOverview] = []
    for record in records:
        case_report, overview, _ = _case_audit(
            root, record, case_dir / f"{record.case_id}.png"
        )
        case_reports.append(case_report)
        overviews.append(overview)
        print(json.dumps({"case_id": record.case_id, "status": case_report["status"]}), flush=True)

    contact = output / "pilot15_contact_sheet.png"
    three_d = output / "pilot15_3d_overview.png"
    statistics = output / "pilot15_statistics_overview.png"
    projections = output / "pilot15_projection_overview.png"
    visual_artifacts = {
        "contact_sheet": {"path": str(contact), "sha256": _render_contact_sheet(overviews, contact)},
        "three_d_overview": {"path": str(three_d), "sha256": _render_3d_sheet(overviews, three_d)},
        "statistics_overview": {"path": str(statistics), "sha256": _render_statistics(overviews, statistics)},
        "projection_overview": {"path": str(projections), "sha256": _render_projection_sheet(overviews, projections)},
    }
    all_case_gates = all(case["status"] == "pass" for case in case_reports)
    global_gates = {
        "frozen_manifest_and_all_artifact_hashes": True,
        "exactly_15_cases": len(case_reports) == EXPECTED_CASE_COUNT,
        "split_9_3_3": Counter(case["split"] for case in case_reports) == Counter({"train": 9, "val": 3, "test": 3}),
        "all_case_automatic_gates": all_case_gates,
        "normal_and_cirrhotic_coverage": {case["patient"]["liver_morphology"] for case in case_reports} == {"normal", "cirrhotic"},
        "all_injection_territories_covered": {case["activity"]["injection_territory"] for case in case_reports} == {"whole_liver", "right_lobar", "left_lobar", "sector_proxy"},
        "matched_and_mismatch_covered": {case["activity"]["mismatch_challenge"] for case in case_reports} == {False, True},
    }
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "pass" if all(global_gates.values()) else "fail",
        "dataset_root": str(root),
        "dataset_id": marker.dataset_id,
        "dataset_version": marker.dataset_version,
        "case_count": marker.case_count,
        "manifest_sha256": marker.manifest_sha256,
        "contract_sha256": marker.contract_sha256,
        "global_gates": global_gates,
        "aggregate_statistics": _aggregate(case_reports),
        "visual_artifacts": visual_artifacts,
        "cases": case_reports,
        "manual_review": {
            "status": "pending",
            "checklist_path": str(output / "pilot15_manual_review.json"),
        },
        "runtime_environment_binding": {
            "status": "not_frozen_in_pilot15_v1",
            "preflight_byte_identity": "not_established",
            "impact": "blocks 50-case expansion but does not invalidate internal frozen-byte QA",
        },
        "go_for_50_case_pilot": False,
    }
    atomic_write_json(output / "pilot15_statistics.json", report)
    atomic_write_bytes(output / "pilot15_statistics.md", _markdown(report).encode("utf-8"))
    atomic_write_json(output / "pilot15_manual_review.json", _manual_checklist(marker, case_reports))
    return report


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = audit_pilot15(args.dataset_root, args.output_dir)
    except Exception as exc:
        print(json.dumps({"status": "fail", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1
    print(
        json.dumps(
            {
                "status": report["status"],
                "case_count": report["case_count"],
                "output_dir": str(args.output_dir.resolve()),
                "manual_review": report["manual_review"]["status"],
                "go_for_50_case_pilot": report["go_for_50_case_pilot"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
