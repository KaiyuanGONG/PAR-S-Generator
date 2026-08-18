"""Server-rendered grayscale PNG previews.

Volumes and projections are rendered here so the browser never receives raw
float binaries. The canonical projection transform ``raw[:, ::-1, :]`` from
pipeline.contracts is applied for display, matching the validated GUI viewer.
"""

from __future__ import annotations

import io
import threading
import uuid
from pathlib import Path

import numpy as np
from PIL import Image

from core.phantom_generator import PhantomConfig, PhantomGenerator
from pipeline.contracts import CANONICAL_PROJECTION_TRANSFORM  # noqa: F401 (documented)

_PREVIEW_CACHE: dict[str, dict] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 8


def _to_png(array2d: np.ndarray, zoom: int = 3) -> bytes:
    arr = np.asarray(array2d, dtype=np.float64)
    top = float(np.percentile(arr, 99.5)) if arr.size else 1.0
    arr = np.clip(arr / top if top > 0 else arr, 0.0, 1.0)
    img = Image.fromarray((arr * 255).astype(np.uint8), mode="L")
    if zoom > 1:
        img = img.resize((img.width * zoom, img.height * zoom), Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── phantom preview ────────────────────────────────────────────────────────

def generate_phantom_preview(phantom_config: dict, case_index: int, seed: int | None) -> dict:
    config = PhantomConfig.from_dict(phantom_config or {})
    generator = PhantomGenerator(config)
    result = generator.generate_one(case_index, seed=seed)
    pid = uuid.uuid4().hex[:10]
    with _CACHE_LOCK:
        if len(_PREVIEW_CACHE) >= _CACHE_MAX:
            _PREVIEW_CACHE.pop(next(iter(_PREVIEW_CACHE)))
        _PREVIEW_CACHE[pid] = {"activity": result.activity, "mu": result.mu_map}
    summary = {
        "case_id": result.case_id,
        "seed": result.seed,
        "liver_volume_ml": float(result.liver_volume_ml),
        "left_ratio": float(result.left_ratio),
        "perfusion_mode": result.perfusion_mode,
        "n_tumors": int(result.n_tumors),
        "tumor_diameters_mm": [float(v) for v in result.tumor_diameters_mm],
        "tumor_nominal_diameters_mm": [float(v) for v in result.tumor_nominal_diameters_mm],
        "tumor_modes_used": list(result.tumor_modes_used),
        "tumor_metadata": result.tumor_metadata,
        "total_counts_actual": float(result.total_counts_actual),
        "voxel_size_mm": float(result.voxel_size_mm),
        "cantlie_converged": bool(result.cantlie_converged),
        "generation_time_s": float(result.generation_time_s),
    }
    return {"preview_id": pid, "summary": summary}


def phantom_slice_png(pid: str, plane: str, index: int, layer: str) -> bytes | None:
    entry = _PREVIEW_CACHE.get(pid)
    if entry is None:
        return None
    volume = entry["mu" if layer == "mu" else "activity"]
    index = int(np.clip(index, 0, volume.shape[0] - 1))
    if plane == "coronal":
        sl = volume[:, index, :]
    elif plane == "sagittal":
        sl = volume[:, :, index]
    else:
        sl = volume[index]
    return _to_png(sl)


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
                return raw[:, ::-1, :]   # validated canonical transform
    return None


def projection_png(run_root: Path, case_id: str, view: int, layer: str) -> bytes | None:
    data = _load_projection(run_root, case_id, layer)
    if data is None:
        return None
    view = int(np.clip(view, 0, data.shape[0] - 1))
    return _to_png(data[view])


def sinogram_png(run_root: Path, case_id: str, row: int, layer: str) -> bytes | None:
    data = _load_projection(run_root, case_id, layer)
    if data is None:
        return None
    row = int(np.clip(row, 0, data.shape[1] - 1))
    return _to_png(data[:, row, :].T, zoom=4)
