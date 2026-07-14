"""Render a read-only visual QA board for a frozen three-case PAR-S V2 pilot."""

from __future__ import annotations

import argparse
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import matplotlib

matplotlib.use("Agg", force=True)

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import ListedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Patch


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.case_writer_v2 import (  # noqa: E402
    CASE_SCHEMA_VERSION,
    CasePayloadV2,
    CaseRecordV2,
    DatasetContractV2,
    DatasetFreezeError,
    DatasetFreezeRecordV2,
    freeze_dataset,
    validate_case_payload_v2,
)
from core.provenance import (  # noqa: E402
    atomic_write_bytes,
    atomic_write_json,
    resolve_relative_path,
    sha256_bytes,
    sha256_file,
)


SUMMARY_SCHEMA_VERSION = "pars_v2_pilot3_visual_summary_v1"
_METADATA_FIELDS = {
    "seeds",
    "config_hashes",
    "patient",
    "target_metrics",
    "actual_metrics",
    "activity",
    "spatial",
    "acquisition",
    "physics",
    "simulation",
    "quality_control",
}
_VISUAL_ARTIFACTS = {
    "phantom_npz",
    "metadata_json",
    "projection_a00",
    "projection_mhd",
    "projection_res",
    "projection_spe",
    "simind_run_provenance",
}


class PilotRenderError(RuntimeError):
    """Raised before output publication when frozen pilot validation fails."""


@dataclass(frozen=True)
class _CaseVisual:
    case_id: str
    split: str
    morphology: str
    recist_mm: tuple[float, ...]
    injection_territory: str
    mismatch_challenge: bool
    background: np.ndarray
    body_mask: np.ndarray
    liver_mask: np.ndarray
    tumor_instances: np.ndarray
    perfusion_mask: np.ndarray
    selected_zyx: tuple[int, int, int]
    projection_sinogram: np.ndarray
    projection_per_view: np.ndarray
    projection_sum: float


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PilotRenderError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise PilotRenderError(f"{label} must contain a JSON object")
    return value


def _read_manifest(path: Path) -> tuple[CaseRecordV2, ...]:
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PilotRenderError(f"cannot read frozen case manifest: {exc}") from exc
    if not raw_lines or any(not line.strip() for line in raw_lines):
        raise PilotRenderError("frozen case manifest must contain non-empty JSONL records")
    records: list[CaseRecordV2] = []
    for index, line in enumerate(raw_lines, start=1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PilotRenderError(f"manifest line {index} is invalid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise PilotRenderError(f"manifest line {index} must contain an object")
        try:
            records.append(CaseRecordV2.from_dict(value))
        except (TypeError, ValueError, KeyError) as exc:
            raise PilotRenderError(f"manifest line {index} is not a V2 case record: {exc}") from exc
    case_ids = [record.case_id for record in records]
    if case_ids != sorted(case_ids) or len(set(case_ids)) != len(case_ids):
        raise PilotRenderError("manifest case IDs must be unique and canonically sorted")
    return tuple(records)


def _ensure_outside_dataset(path: Path, dataset_root: Path, label: str) -> Path:
    candidate = path.resolve()
    try:
        candidate.relative_to(dataset_root)
    except ValueError:
        return candidate
    raise PilotRenderError(f"{label} must be outside the immutable dataset root")


def _artifact_path(root: Path, record: CaseRecordV2, name: str) -> Path:
    try:
        artifact = record.artifacts[name]
    except KeyError as exc:
        raise PilotRenderError(f"{record.case_id}: missing artifact {name}") from exc
    try:
        return resolve_relative_path(artifact.relative_path, root)
    except ValueError as exc:
        raise PilotRenderError(f"{record.case_id}: unsafe artifact path for {name}") from exc


def _validate_frozen_dataset(root: Path) -> tuple[DatasetFreezeRecordV2, tuple[CaseRecordV2, ...]]:
    marker_path = root / "DATASET_COMPLETE.json"
    if not marker_path.is_file():
        raise PilotRenderError("DATASET_COMPLETE.json is required; unfrozen data cannot be rendered")
    try:
        marker = DatasetFreezeRecordV2.from_dict(
            _read_json_object(marker_path, "DATASET_COMPLETE.json")
        )
    except (DatasetFreezeError, TypeError, ValueError, KeyError) as exc:
        raise PilotRenderError(f"invalid DATASET_COMPLETE.json: {exc}") from exc
    if marker.status != "complete" or marker.case_count != 3:
        raise PilotRenderError("visual QA accepts exactly three formally complete cases")
    try:
        manifest_path = resolve_relative_path(marker.manifest_relative_path, root)
    except ValueError as exc:
        raise PilotRenderError("completion marker contains an unsafe manifest path") from exc
    if not manifest_path.is_file() or sha256_file(manifest_path) != marker.manifest_sha256:
        raise PilotRenderError("frozen manifest is missing or differs from DATASET_COMPLETE")
    records = _read_manifest(manifest_path)
    if len(records) != 3:
        raise PilotRenderError("frozen manifest must contain exactly three cases")
    if not _VISUAL_ARTIFACTS.issubset(set(marker.required_artifact_names)):
        missing = sorted(_VISUAL_ARTIFACTS - set(marker.required_artifact_names))
        raise PilotRenderError(f"completion marker does not freeze visual evidence: {missing}")
    if any(record.split_plan_sha256 != marker.split_plan_sha256 for record in records):
        raise PilotRenderError("manifest records do not bind the frozen split plan")

    contract = DatasetContractV2(
        output_root=root,
        dataset_id=marker.dataset_id,
        dataset_version=marker.dataset_version,
        dataset_role=marker.dataset_role,
        expected_case_ids=tuple(record.case_id for record in records),
        allowed_profile_ids=tuple(sorted({record.profile_id for record in records})),
        split_plan_sha256=marker.split_plan_sha256,
        required_artifact_names=tuple(marker.required_artifact_names),
    )
    try:
        reaudited = freeze_dataset(records, contract)
    except (DatasetFreezeError, OSError, ValueError, KeyError) as exc:
        raise PilotRenderError(f"frozen dataset re-audit failed: {exc}") from exc
    if reaudited != marker:
        raise PilotRenderError("read-only freeze re-audit differs from DATASET_COMPLETE")
    return marker, records


def _case_payload(
    root: Path,
    record: CaseRecordV2,
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    metadata_path = _artifact_path(root, record, "metadata_json")
    document = _read_json_object(metadata_path, f"{record.case_id} metadata")
    identity = {
        "schema_version": CASE_SCHEMA_VERSION,
        "case_id": record.case_id,
        "case_family_id": record.case_family_id,
        "profile_id": record.profile_id,
        "dataset_id": record.dataset_id,
        "dataset_version": record.dataset_version,
        "dataset_role": record.dataset_role,
        "split": record.split,
    }
    if any(document.get(key) != value for key, value in identity.items()):
        raise PilotRenderError(f"{record.case_id}: metadata identity differs from manifest")
    if not _METADATA_FIELDS.issubset(document):
        raise PilotRenderError(f"{record.case_id}: metadata contract is incomplete")
    npz_path = _artifact_path(root, record, "phantom_npz")
    try:
        with np.load(npz_path, allow_pickle=False) as archive:
            arrays = {name: archive[name].copy() for name in archive.files}
    except (OSError, ValueError, KeyError) as exc:
        raise PilotRenderError(f"{record.case_id}: phantom NPZ cannot be read: {exc}") from exc
    payload = CasePayloadV2(
        case_id=record.case_id,
        case_family_id=record.case_family_id,
        profile_id=record.profile_id,
        dataset_id=record.dataset_id,
        dataset_version=record.dataset_version,
        dataset_role=record.dataset_role,
        split=record.split,
        population_weight=record.population_weight,
        sampling_probability=record.sampling_probability,
        arrays=arrays,
        metadata={name: document[name] for name in _METADATA_FIELDS},
    )
    try:
        validate_case_payload_v2(payload)
    except (TypeError, ValueError, KeyError) as exc:
        raise PilotRenderError(f"{record.case_id}: frozen case payload is invalid: {exc}") from exc
    return arrays, document


def _selected_tumor_center(instances: np.ndarray) -> tuple[int, int, int]:
    labels, counts = np.unique(instances[instances > 0], return_counts=True)
    if not len(labels):
        raise PilotRenderError("pilot case has no tumor instance")
    largest = int(labels[int(np.argmax(counts))])
    coordinates = np.argwhere(instances == largest)
    center = np.rint(coordinates.mean(axis=0)).astype(int)
    return tuple(int(value) for value in center)


def _load_case_visual(root: Path, record: CaseRecordV2) -> _CaseVisual:
    arrays, metadata = _case_payload(root, record)
    provenance = _read_json_object(
        _artifact_path(root, record, "simind_run_provenance"),
        f"{record.case_id} SIMIND provenance",
    )
    expected_shape = provenance.get("expected_shape")
    if (
        not isinstance(expected_shape, list)
        or len(expected_shape) != 3
        or expected_shape[0] != 60
        or any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in expected_shape)
    ):
        raise PilotRenderError(f"{record.case_id}: invalid frozen projection shape")
    shape = tuple(expected_shape)
    a00_path = _artifact_path(root, record, "projection_a00")
    try:
        projection = np.memmap(a00_path, dtype="<f4", mode="r", shape=shape)
        per_view = np.asarray(
            projection.sum(axis=(1, 2), dtype=np.float64), dtype=np.float64
        )
        sinogram = np.asarray(projection.sum(axis=1, dtype=np.float64), dtype=np.float64)
        del projection
    except (OSError, ValueError) as exc:
        raise PilotRenderError(f"{record.case_id}: projection cannot be mapped: {exc}") from exc
    if not np.isfinite(sinogram).all() or np.any(sinogram < 0):
        raise PilotRenderError(f"{record.case_id}: projection contains invalid values")

    simulation_stats = metadata["simulation"]["projection_stats"]
    stored = np.asarray(
        simulation_stats["projection_per_view_weight_sum"], dtype=np.float64
    )
    if stored.shape != per_view.shape or not np.allclose(stored, per_view, rtol=1e-9, atol=1e-5):
        raise PilotRenderError(f"{record.case_id}: per-view projection weights drifted")
    instances = np.asarray(arrays["tumor_instance_mask"], dtype=np.uint16)
    lesions = metadata["actual_metrics"]["tumors"]["lesions"]
    return _CaseVisual(
        case_id=record.case_id,
        split=record.split,
        morphology=str(metadata["patient"]["liver_morphology"]),
        recist_mm=tuple(float(item["recist_3d_mm"]) for item in lesions),
        injection_territory=str(metadata["activity"]["injection_territory"]),
        mismatch_challenge=bool(metadata["activity"]["mismatch_challenge"]),
        background=np.asarray(arrays["mu_input_140kev"], dtype=np.float32),
        body_mask=np.asarray(arrays["body_mask"], dtype=bool),
        liver_mask=np.asarray(arrays["liver_mask"], dtype=bool),
        tumor_instances=instances,
        perfusion_mask=np.asarray(arrays["perfusion_mask"], dtype=bool),
        selected_zyx=_selected_tumor_center(instances),
        projection_sinogram=sinogram,
        projection_per_view=per_view,
        projection_sum=float(per_view.sum(dtype=np.float64)),
    )


def _slice(volume: np.ndarray, plane: str, index: int) -> np.ndarray:
    if plane == "axial":
        return volume[index, :, :]
    if plane == "coronal":
        return volume[:, index, :]
    if plane == "sagittal":
        return volume[:, :, index]
    raise ValueError(f"unknown plane {plane}")


def _crop_slices(mask: np.ndarray, padding: int = 4) -> tuple[slice, slice]:
    coordinates = np.argwhere(mask)
    if not len(coordinates):
        return slice(0, mask.shape[0]), slice(0, mask.shape[1])
    lower = np.maximum(coordinates.min(axis=0) - padding, 0)
    upper = np.minimum(coordinates.max(axis=0) + padding + 1, mask.shape)
    return slice(int(lower[0]), int(upper[0])), slice(int(lower[1]), int(upper[1]))


def _contour_if_present(axis: Any, mask: np.ndarray, *, color: str, linewidth: float, linestyle: str = "-") -> None:
    if mask.any() and not mask.all():
        axis.contour(
            mask.astype(np.uint8),
            levels=(0.5,),
            colors=(color,),
            linewidths=(linewidth,),
            linestyles=(linestyle,),
        )


def _draw_plane(axis: Any, case: _CaseVisual, plane: str, index: int) -> None:
    background = _slice(case.background, plane, index)
    body = _slice(case.body_mask, plane, index)
    liver = _slice(case.liver_mask, plane, index)
    tumor = _slice(case.tumor_instances > 0, plane, index)
    perfusion = _slice(case.perfusion_mask, plane, index)
    crop = _crop_slices(body | liver | tumor | perfusion)
    background = background[crop]
    body = body[crop]
    liver = liver[crop]
    tumor = tumor[crop]
    perfusion = perfusion[crop]
    values = background[body]
    if values.size:
        vmin, vmax = np.percentile(values, (1.0, 99.5))
    else:
        vmin, vmax = float(background.min()), float(background.max())
    if not np.isfinite((vmin, vmax)).all() or vmax <= vmin:
        vmin, vmax = float(background.min()), float(background.max()) + 1e-6
    axis.imshow(background, cmap="gray", origin="lower", vmin=vmin, vmax=vmax)
    tumor_overlay = np.ma.masked_where(~tumor, tumor.astype(np.uint8))
    axis.imshow(
        tumor_overlay,
        cmap=ListedColormap(("#ff4d6d",)),
        origin="lower",
        interpolation="nearest",
        alpha=0.62,
        vmin=1,
        vmax=1,
    )
    _contour_if_present(
        axis,
        perfusion,
        color="#00a6d6",
        linewidth=1.0,
        linestyle="--",
    )
    _contour_if_present(axis, liver, color="#2ca25f", linewidth=1.15)
    labels = {
        "axial": ("X / right", "Y / anterior"),
        "coronal": ("X / right", "Z / superior"),
        "sagittal": ("Y / anterior", "Z / superior"),
    }
    axis.set_xlabel(labels[plane][0], fontsize=7)
    axis.set_ylabel(labels[plane][1], fontsize=7)
    axis.set_xticks([])
    axis.set_yticks([])


def _render_png(cases: Sequence[_CaseVisual], marker: DatasetFreezeRecordV2) -> bytes:
    figure, axes = plt.subplots(
        nrows=3,
        ncols=5,
        figsize=(22, 12),
        gridspec_kw={"width_ratios": (1.0, 1.0, 1.0, 1.22, 1.18)},
        constrained_layout=True,
    )
    planes = ("axial", "coronal", "sagittal")
    axis_keys = {"axial": "Z", "coronal": "Y", "sagittal": "X"}
    for row, case in enumerate(cases):
        indices = (case.selected_zyx[0], case.selected_zyx[1], case.selected_zyx[2])
        for column, (plane, index) in enumerate(zip(planes, indices)):
            _draw_plane(axes[row, column], case, plane, index)
            axes[row, column].set_title(
                f"{plane.capitalize()} · {axis_keys[plane]}={index}", fontsize=9
            )

        recist = "/".join(f"{value:.1f}" for value in case.recist_mm)
        perfusion = case.injection_territory + (
            " · mismatch" if case.mismatch_challenge else " · matched"
        )
        axes[row, 0].set_title(
            f"{case.case_id} · {case.split} · {case.morphology}\n"
            f"RECIST {recist} mm · {perfusion}\n"
            f"Axial · Z={indices[0]}",
            fontsize=9,
            fontweight="bold",
        )

        log_sinogram = np.log1p(case.projection_sinogram.T)
        sinogram_vmax = max(float(np.percentile(log_sinogram, 99.5)), 1e-12)
        axes[row, 3].imshow(
            log_sinogram,
            cmap="magma",
            origin="lower",
            aspect="auto",
            vmin=0.0,
            vmax=sinogram_vmax,
        )
        axes[row, 3].set_title("Projection sinogram · log1p Σ detector-v", fontsize=9)
        axes[row, 3].set_xlabel("view index", fontsize=8)
        axes[row, 3].set_ylabel("detector-u", fontsize=8)

        view_indices = np.arange(len(case.projection_per_view))
        axes[row, 4].plot(
            view_indices,
            case.projection_per_view,
            color="#355f8d",
            linewidth=1.4,
        )
        axes[row, 4].fill_between(
            view_indices,
            case.projection_per_view,
            color="#355f8d",
            alpha=0.13,
        )
        axes[row, 4].set_title(
            f"Per-view weight · total {case.projection_sum:.3g}", fontsize=9
        )
        axes[row, 4].set_xlabel("view index", fontsize=8)
        axes[row, 4].set_ylabel("weight", fontsize=8)
        axes[row, 4].grid(alpha=0.22, linewidth=0.6)
        axes[row, 4].tick_params(labelsize=7)

    figure.suptitle(
        f"Frozen PAR-S V2 three-case pilot QA · {marker.dataset_id} · {marker.dataset_version}",
        fontsize=14,
        fontweight="bold",
    )
    figure.legend(
        handles=(
            Line2D((0,), (0,), color="#2ca25f", linewidth=1.5, label="liver boundary"),
            Patch(facecolor="#ff4d6d", alpha=0.62, label="tumor"),
            Line2D((0,), (0,), color="#00a6d6", linestyle="--", label="perfusion boundary"),
        ),
        loc="outside lower center",
        ncols=3,
        frameon=False,
        fontsize=9,
    )
    buffer = io.BytesIO()
    try:
        figure.savefig(
            buffer,
            format="png",
            dpi=140,
            metadata={"Software": "PAR-S Generator Task 12 read-only pilot renderer"},
        )
    finally:
        plt.close(figure)
    return buffer.getvalue()


def render_pilot3(
    dataset_root: Path,
    output_png: Path,
    summary_json: Path,
) -> dict[str, Any]:
    """Re-audit a frozen pilot and atomically publish its visual QA outputs."""

    root = Path(dataset_root).resolve()
    if not root.is_dir():
        raise PilotRenderError(f"dataset root does not exist: {root}")
    png_path = _ensure_outside_dataset(Path(output_png), root, "output PNG")
    json_path = _ensure_outside_dataset(Path(summary_json), root, "summary JSON")
    if png_path.suffix.casefold() != ".png":
        raise PilotRenderError("output PNG path must end in .png")
    if json_path.suffix.casefold() != ".json":
        raise PilotRenderError("summary JSON path must end in .json")
    if png_path == json_path:
        raise PilotRenderError("output PNG and summary JSON paths must differ")

    marker, records = _validate_frozen_dataset(root)
    cases = tuple(_load_case_visual(root, record) for record in records)
    png_bytes = _render_png(cases, marker)
    summary: dict[str, Any] = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "status": "pass",
        "dataset_root": str(root),
        "dataset_id": marker.dataset_id,
        "dataset_version": marker.dataset_version,
        "manifest_sha256": marker.manifest_sha256,
        "completion_contract_sha256": marker.contract_sha256,
        "case_count": len(cases),
        "output_png": str(png_path),
        "output_png_sha256": sha256_bytes(png_bytes),
        "cases": [
            {
                "case_id": case.case_id,
                "split": case.split,
                "liver_morphology": case.morphology,
                "recist_3d_mm": list(case.recist_mm),
                "injection_territory": case.injection_territory,
                "mismatch_challenge": case.mismatch_challenge,
                "selected_voxel_zyx": list(case.selected_zyx),
                "projection_view_count": int(len(case.projection_per_view)),
                "projection_weight_sum": case.projection_sum,
            }
            for case in cases
        ],
    }
    atomic_write_bytes(png_path, png_bytes)
    atomic_write_json(json_path, summary)
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a read-only QA board for a frozen Task-12 three-case pilot."
    )
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--output-png", type=Path, required=True)
    parser.add_argument("--summary-json", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = render_pilot3(args.dataset_root, args.output_png, args.summary_json)
    except Exception as exc:  # CLI is intentionally fail-closed with one concise error.
        print(f"pilot render failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
