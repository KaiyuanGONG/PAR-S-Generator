"""Derived browser previews for phantoms and projection evidence.

The browser never receives raw phantom volumes or masks. It receives rendered
PNG slices/MIPs, individual voxel probe values, and reduced marching-cubes
surfaces. Projection rendering keeps the validated canonical transform
``raw[:, ::-1, :]`` used by the frozen desktop UI.
"""

from __future__ import annotations

import hashlib
import io
import json
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image

from core.phantom_generator import (
    PhantomConfig,
    PhantomGenerator,
    PreviewOverrides as GeneratorPreviewOverrides,
)
from pipeline.contracts import CANONICAL_PROJECTION_TRANSFORM  # noqa: F401 (documented)

Plane = Literal["axial", "coronal", "sagittal"]
Layer = Literal["activity", "mu"]
Overlay = Literal["liver_and_tumors", "tumors", "liver", "contours", "none"]


@dataclass
class _PreviewEntry:
    created: float
    activity: np.ndarray
    mu: np.ndarray
    liver: np.ndarray
    tumors: tuple[np.ndarray, ...]
    voxel_size_mm: float
    windows: dict[str, tuple[float, float]]
    byte_size: int


def _entry_size(*arrays: np.ndarray) -> int:
    return sum(int(array.nbytes) for array in arrays)


class PreviewStore:
    """Small, thread-safe, expiring in-memory store for derived previews."""

    def __init__(self, *, max_entries: int = 8, max_bytes: int = 256 * 1024**2, ttl_s: float = 900.0):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.ttl_s = ttl_s
        self._entries: OrderedDict[str, _PreviewEntry] = OrderedDict()
        self._bytes = 0
        self._lock = threading.RLock()

    def _prune(self, now: float) -> None:
        expired = [key for key, value in self._entries.items() if now - value.created > self.ttl_s]
        for key in expired:
            self._bytes -= self._entries.pop(key).byte_size
        while self._entries and (len(self._entries) > self.max_entries or self._bytes > self.max_bytes):
            _, removed = self._entries.popitem(last=False)
            self._bytes -= removed.byte_size

    def put(self, entry: _PreviewEntry) -> str:
        preview_id = uuid.uuid4().hex
        with self._lock:
            self._prune(time.monotonic())
            self._entries[preview_id] = entry
            self._bytes += entry.byte_size
            self._prune(time.monotonic())
        return preview_id

    def get(self, preview_id: str) -> _PreviewEntry | None:
        with self._lock:
            self._prune(time.monotonic())
            entry = self._entries.get(preview_id)
            if entry is not None:
                self._entries.move_to_end(preview_id)
            return entry

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0


PREVIEW_STORE = PreviewStore()


def _window(volume: np.ndarray) -> tuple[float, float]:
    positive = np.asarray(volume, dtype=np.float32)
    positive = positive[positive > 0]
    if not positive.size:
        return (0.0, 1.0)
    low = float(np.percentile(positive, 1.0))
    high = float(np.percentile(positive, 99.5))
    if high <= low:
        high = float(positive.max())
    return (low, high if high > low else low + 1.0)


def _normalize(array2d: np.ndarray, window: tuple[float, float], gamma: float = 0.65) -> np.ndarray:
    low, high = window
    normalized = np.clip((np.asarray(array2d, dtype=np.float32) - low) / max(high - low, 1e-12), 0, 1)
    return normalized**gamma if gamma and gamma != 1 else normalized


def _edge(mask: np.ndarray) -> np.ndarray:
    body = np.asarray(mask, dtype=bool)
    if not body.any():
        return body
    eroded = body.copy()
    eroded[1:, :] &= body[:-1, :]
    eroded[:-1, :] &= body[1:, :]
    eroded[:, 1:] &= body[:, :-1]
    eroded[:, :-1] &= body[:, 1:]
    return body & ~eroded


def _render_png(
    data: np.ndarray,
    *,
    window: tuple[float, float] | None = None,
    liver: np.ndarray | None = None,
    tumors: np.ndarray | None = None,
    overlay: Overlay = "none",
    zoom: int = 3,
    gamma: float = 0.65,
) -> bytes:
    normalized = _normalize(data, window or _window(data), gamma)
    if overlay == "none":
        image = Image.fromarray((normalized * 255).astype(np.uint8), mode="L")
    else:
        rgb = np.stack([normalized, normalized, normalized], axis=-1)
        liver_mask = np.zeros_like(normalized, dtype=bool) if liver is None else liver.astype(bool)
        tumor_mask = np.zeros_like(normalized, dtype=bool) if tumors is None else tumors.astype(bool)
        if overlay in {"liver", "liver_and_tumors"}:
            rgb[liver_mask, 0] = np.clip(rgb[liver_mask, 0] * 0.48 + 0.10, 0, 1)
            rgb[liver_mask, 1] = np.clip(rgb[liver_mask, 1] * 0.48 + 0.48, 0, 1)
            rgb[liver_mask, 2] = np.clip(rgb[liver_mask, 2] * 0.40 + 0.22, 0, 1)
        if overlay in {"tumors", "liver_and_tumors"}:
            rgb[tumor_mask] = rgb[tumor_mask] * np.array([0.25, 0.18, 0.18]) + np.array([0.75, 0.12, 0.10])
        if overlay == "contours":
            rgb[_edge(liver_mask)] = np.array([0.15, 0.95, 0.65])
            rgb[_edge(tumor_mask)] = np.array([1.0, 0.35, 0.35])
        image = Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8), mode="RGB")
    if zoom > 1:
        image = image.resize((image.width * zoom, image.height * zoom), Image.Resampling.NEAREST)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _tumor_union(entry: _PreviewEntry) -> np.ndarray:
    union = np.zeros_like(entry.liver, dtype=bool)
    for mask in entry.tumors:
        union |= mask
    return union


def _plane_slice(volume: np.ndarray, plane: Plane, index: int) -> np.ndarray:
    if plane == "coronal":
        return volume[:, index, :]
    if plane == "sagittal":
        return volume[:, :, index]
    return volume[index, :, :]


def _plane_limit(shape: tuple[int, ...], plane: Plane) -> int:
    return shape[{"axial": 0, "coronal": 1, "sagittal": 2}[plane]]


def _plane_projection(volume: np.ndarray, plane: Plane, *, mask: bool = False) -> np.ndarray:
    axis = {"axial": 0, "coronal": 1, "sagittal": 2}[plane]
    return np.any(volume, axis=axis) if mask else np.max(volume, axis=axis)


def generate_phantom_preview(
    phantom_config: dict,
    case_index: int,
    seed: int | None,
    overrides: dict | None = None,
) -> dict:
    config = PhantomConfig.from_dict(phantom_config or {})
    generator_overrides = GeneratorPreviewOverrides(**(overrides or {}))
    result = PhantomGenerator(config).generate_one(case_index, seed=seed, overrides=generator_overrides)
    tumors = tuple(np.asarray(mask, dtype=bool) for mask in result.tumor_masks)
    entry = _PreviewEntry(
        created=time.monotonic(),
        activity=np.asarray(result.activity, dtype=np.float32),
        mu=np.asarray(result.mu_map, dtype=np.float32),
        liver=np.asarray(result.liver_mask, dtype=bool),
        tumors=tumors,
        voxel_size_mm=float(result.voxel_size_mm),
        windows={"activity": _window(result.activity), "mu": _window(result.mu_map)},
        byte_size=_entry_size(result.activity, result.mu_map, result.liver_mask, *tumors),
    )
    preview_id = PREVIEW_STORE.put(entry)
    canonical_config = config.to_dict()
    digest = hashlib.sha256(
        json.dumps(canonical_config, sort_keys=True, separators=(",", ":"), default=list).encode("utf-8")
    ).hexdigest()
    summary = {
        "case_id": result.case_id,
        "seed": result.seed,
        "liver_volume_ml": float(result.liver_volume_ml),
        "left_ratio": float(result.left_ratio),
        "perfusion_mode": result.perfusion_mode,
        "n_tumors": int(result.n_tumors),
        "tumor_diameters_mm": [float(value) for value in result.tumor_diameters_mm],
        "tumor_nominal_diameters_mm": [float(value) for value in result.tumor_nominal_diameters_mm],
        "tumor_modes_used": list(result.tumor_modes_used),
        "tumor_metadata": result.tumor_metadata,
        "total_counts_actual": float(result.total_counts_actual),
        "voxel_size_mm": float(result.voxel_size_mm),
        "volume_shape": [int(value) for value in result.volume_shape],
        "mu_unit": result.mu_unit,
        "mu_reference_energy_kev": float(result.mu_reference_energy_kev),
        "cantlie_converged": bool(result.cantlie_converged),
        "generation_time_s": float(result.generation_time_s),
    }
    return {
        "preview_id": preview_id,
        "config_digest": digest,
        "geometry": {
            "shape_zyx": summary["volume_shape"],
            "voxel_size_mm": summary["voxel_size_mm"],
            "origin": "voxel-center",
        },
        "summary": summary,
    }


def phantom_slice_png(
    preview_id: str,
    plane: Plane,
    index: int,
    layer: Layer,
    overlay: Overlay = "liver_and_tumors",
) -> bytes | None:
    entry = PREVIEW_STORE.get(preview_id)
    if entry is None:
        return None
    limit = _plane_limit(entry.activity.shape, plane)
    if index < 0 or index >= limit:
        raise ValueError(f"slice index must be in [0, {limit - 1}] for {plane}")
    volume = entry.mu if layer == "mu" else entry.activity
    tumor_union = _tumor_union(entry)
    return _render_png(
        _plane_slice(volume, plane, index),
        window=entry.windows[layer],
        liver=_plane_slice(entry.liver, plane, index),
        tumors=_plane_slice(tumor_union, plane, index),
        overlay=overlay,
    )


def phantom_mip_png(
    preview_id: str,
    plane: Plane,
    layer: Layer,
    overlay: Overlay = "liver_and_tumors",
) -> bytes | None:
    entry = PREVIEW_STORE.get(preview_id)
    if entry is None:
        return None
    volume = entry.mu if layer == "mu" else entry.activity
    return _render_png(
        _plane_projection(volume, plane),
        window=entry.windows[layer],
        liver=_plane_projection(entry.liver, plane, mask=True),
        tumors=_plane_projection(_tumor_union(entry), plane, mask=True),
        overlay=overlay,
    )


def phantom_probe(preview_id: str, x: int, y: int, z: int) -> dict | None:
    entry = PREVIEW_STORE.get(preview_id)
    if entry is None:
        return None
    depth, height, width = entry.activity.shape
    if not (0 <= x < width and 0 <= y < height and 0 <= z < depth):
        raise ValueError(f"voxel must satisfy x=0..{width - 1}, y=0..{height - 1}, z=0..{depth - 1}")
    lesion_ids = [index + 1 for index, mask in enumerate(entry.tumors) if bool(mask[z, y, x])]
    return {
        "voxel": {"x": x, "y": y, "z": z},
        "position_mm": {
            "x": x * entry.voxel_size_mm,
            "y": y * entry.voxel_size_mm,
            "z": z * entry.voxel_size_mm,
        },
        "activity": float(entry.activity[z, y, x]),
        "mu": float(entry.mu[z, y, x]),
        "in_liver": bool(entry.liver[z, y, x]),
        "lesion_ids": lesion_ids,
    }


def _surface(mask: np.ndarray, step_size: int) -> dict | None:
    if int(mask.sum()) <= 20:
        return None
    from skimage.measure import marching_cubes

    vertices_zyx, faces, _, _ = marching_cubes(mask.astype(np.uint8), level=0.5, step_size=step_size)
    vertices_xyz = vertices_zyx[:, ::-1]
    return {
        "vertices": vertices_xyz.astype(np.float32).round(4).reshape(-1).tolist(),
        "faces": faces.astype(np.uint32).reshape(-1).tolist(),
    }


def phantom_mesh(preview_id: str, structure: Literal["all", "liver", "tumors"] = "all") -> dict | None:
    entry = PREVIEW_STORE.get(preview_id)
    if entry is None:
        return None
    objects: list[dict] = []
    if structure in {"all", "liver"}:
        surface = _surface(entry.liver, 2)
        if surface:
            objects.append({"id": "liver", "kind": "liver", **surface})
    if structure in {"all", "tumors"}:
        for index, tumor in enumerate(entry.tumors):
            surface = _surface(tumor, 1)
            if surface:
                objects.append({"id": f"lesion-{index + 1}", "kind": "tumor", "lesion_id": index + 1, **surface})
    return {
        "shape_zyx": list(entry.activity.shape),
        "voxel_size_mm": entry.voxel_size_mm,
        "coordinate_order": "xyz-voxel",
        "objects": objects,
    }


# ── run projection / sinogram ──────────────────────────────────────────────

def _load_projection(run_root: Path, case_id: str, layer: str) -> np.ndarray | None:
    if layer == "observation":
        candidates = sorted((run_root / "observation").glob(f"{case_id}*.npy"))
        if candidates:
            raw = np.load(candidates[0]).astype(np.float32)
            return raw[:, ::-1, :]
        candidates = sorted((run_root / "observation").glob(f"{case_id}*.a00"))
    else:
        candidates = [run_root / "expectation" / f"{case_id}.a00"]
    for path in candidates:
        if path.is_file():
            flat = np.fromfile(path, dtype=np.float32)
            if flat.size % (128 * 128) == 0:
                views = flat.size // (128 * 128)
                raw = flat.reshape((views, 128, 128))
                return raw[:, ::-1, :]
    return None


def projection_png(run_root: Path, case_id: str, view: int, layer: str) -> bytes | None:
    data = _load_projection(run_root, case_id, layer)
    if data is None:
        return None
    view = int(np.clip(view, 0, data.shape[0] - 1))
    return _render_png(data[view], overlay="none")


def sinogram_png(run_root: Path, case_id: str, row: int, layer: str) -> bytes | None:
    data = _load_projection(run_root, case_id, layer)
    if data is None:
        return None
    row = int(np.clip(row, 0, data.shape[1] - 1))
    # Pillow interprets the last array axis as image width. Keep detector
    # columns horizontal and acquisition views vertical, matching the viewer
    # contract (PyQt's ImageView applies its own axis convention).
    return _render_png(data[:, row, :], overlay="none", zoom=4)


def load_a00(path: Path) -> np.ndarray:
    """Load a bounded 128×128 projection stack for read-only inspection."""
    size = path.stat().st_size
    plane_bytes = 128 * 128 * np.dtype(np.float32).itemsize
    if size <= 0 or size % plane_bytes:
        raise ValueError(f"file size {size} is not a whole number of 128x128 float32 views")
    views = size // plane_bytes
    if views > 4096:
        raise ValueError(f"projection stack has an unreasonable view count: {views}")
    return np.fromfile(path, dtype=np.float32).reshape((views, 128, 128))[:, ::-1, :]


def artifact_summary(path: Path) -> dict:
    data = load_a00(path)
    return {
        "path": str(path),
        "shape": list(data.shape),
        "dtype": "float32",
        "canonical_transform": CANONICAL_PROJECTION_TRANSFORM,
        "sum": float(data.sum(dtype=np.float64)),
        "minimum": float(data.min()),
        "maximum": float(data.max()),
        "nonzero_fraction": float(np.count_nonzero(data) / data.size),
    }


def artifact_projection_png(path: Path, view: int) -> bytes:
    data = load_a00(path)
    if view < 0 or view >= data.shape[0]:
        raise ValueError(f"view must be in [0, {data.shape[0] - 1}]")
    return _render_png(data[view], overlay="none")


def artifact_sinogram_png(path: Path, row: int) -> bytes:
    data = load_a00(path)
    if row < 0 or row >= data.shape[1]:
        raise ValueError(f"row must be in [0, {data.shape[1] - 1}]")
    return _render_png(data[:, row, :], overlay="none", zoom=4)
