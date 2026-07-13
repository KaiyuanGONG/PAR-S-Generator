from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass
from typing import Mapping

import numpy as np
from scipy import ndimage

from .liver_regions import LiverRegionsV2, build_liver_regions
from .measurements import measure_liver
from .schemas_v2 import LiverTargetV2


@dataclass(frozen=True)
class GridSpecV2:
    shape: tuple[int, int, int] = (128, 128, 128)
    voxel_size_mm: float = 4.42

    def __post_init__(self) -> None:
        if len(self.shape) != 3 or any(not isinstance(item, int) or isinstance(item, bool) or item < 16 for item in self.shape):
            raise ValueError("shape must contain three integer dimensions >= 16")
        if not isinstance(self.voxel_size_mm, (int, float)) or isinstance(self.voxel_size_mm, bool):
            raise ValueError("voxel_size_mm must be a positive finite number")
        if not math.isfinite(float(self.voxel_size_mm)) or float(self.voxel_size_mm) <= 0:
            raise ValueError("voxel_size_mm must be a positive finite number")

    @property
    def affine_4x4(self) -> np.ndarray:
        spacing = float(self.voxel_size_mm)
        affine = np.eye(4, dtype=np.float64)
        affine[:3, :3] = np.diag((spacing, spacing, spacing))
        affine[:3, 3] = -0.5 * (np.asarray(self.shape, dtype=np.float64) - 1.0) * spacing
        return affine

    @property
    def voxel_volume_ml(self) -> float:
        return float(self.voxel_size_mm) ** 3 / 1000.0

    @property
    def world_edge_bounds_mm(self) -> tuple[np.ndarray, np.ndarray]:
        affine = self.affine_4x4
        half = float(self.voxel_size_mm) / 2.0
        lower = affine[:3, 3] - half
        upper = affine[:3, 3] + (np.asarray(self.shape) - 1) * float(self.voxel_size_mm) + half
        return lower, upper


@dataclass(frozen=True)
class LiverGeometryV2:
    mask: np.ndarray
    region_labels: np.ndarray
    affine_4x4: np.ndarray
    primitive_masks: Mapping[str, np.ndarray]
    target_metrics: Mapping[str, object]
    actual_metrics: Mapping[str, object]
    continuous_parameters: Mapping[str, object]
    evidence_types: Mapping[str, str]
    region_definition: str = "couinaud_proxy_without_vascular_tree"


def _validate_target(target: LiverTargetV2, grid: GridSpecV2) -> None:
    if not math.isfinite(target.volume_ml) or target.volume_ml <= 0:
        raise ValueError("target volume must be positive and finite")
    extents = np.array((target.si_mm, target.ap_mm, target.lr_mm), dtype=np.float64)
    if not np.isfinite(extents).all() or np.any(extents <= 0):
        raise ValueError("target extents must be positive and finite")
    if not 0.10 <= target.left_fraction <= 0.65:
        raise ValueError("target left_fraction must be within [0.10, 0.65]")
    if target.morphology not in {"normal", "cirrhotic"}:
        raise ValueError("target morphology must be normal or cirrhotic")
    if not 0 <= target.caudate_fraction < target.left_fraction:
        raise ValueError("target caudate_fraction must be non-negative and smaller than left_fraction")
    if target.s1_3_to_s4_8_ratio <= 0:
        raise ValueError("target S1-3/S4-8 ratio must be positive")

    centroid = np.asarray(target.centroid_mm, dtype=np.float64)
    lower, upper = grid.world_edge_bounds_mm
    margin = float(grid.voxel_size_mm)
    if np.any(centroid - extents / 2.0 < lower + margin) or np.any(centroid + extents / 2.0 > upper - margin):
        raise ValueError("target extents and centroid do not fit inside grid")


def _phase_from_target(target: LiverTargetV2) -> float:
    centroid = ",".join(f"{float(value):.6f}" for value in target.centroid_mm)
    payload = (
        f"{target.volume_ml:.6f}|{target.lr_mm:.6f}|{target.ap_mm:.6f}|"
        f"{target.si_mm:.6f}|{centroid}|{target.morphology}"
    ).encode("utf-8")
    integer = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    return 2.0 * math.pi * integer / float(2**64)


def _primitive_parameters(morphology: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    right_radii = np.array((0.70, 0.64, 0.70), dtype=np.float64)
    left_radii = np.array((0.53, 0.58, 0.50), dtype=np.float64)
    if morphology == "cirrhotic":
        right_radii *= 0.83 ** (1.0 / 3.0)
        left_radii *= 1.59 ** (1.0 / 3.0)
    right_center = 1.0 - right_radii
    left_center = -1.0 + left_radii
    return right_center, right_radii, left_center, left_radii


def _prepare_primitive_fields(
    grid: GridSpecV2,
    center_mm: np.ndarray,
    half_extents_mm: np.ndarray,
    target: LiverTargetV2,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, float, float]:
    affine = grid.affine_4x4
    spacing = float(grid.voxel_size_mm)
    z = (affine[0, 3] + np.arange(grid.shape[0]) * spacing - center_mm[0]) / half_extents_mm[0]
    y = (affine[1, 3] + np.arange(grid.shape[1]) * spacing - center_mm[1]) / half_extents_mm[1]
    x = (affine[2, 3] + np.arange(grid.shape[2]) * spacing - center_mm[2]) / half_extents_mm[2]
    zz = z[:, None, None]
    yy = y[None, :, None]
    xx = x[None, None, :]

    right_center, right_radii, left_center, left_radii = _primitive_parameters(target.morphology)
    right_field = (
        ((zz - right_center[0]) / right_radii[0]) ** 2
        + ((yy - right_center[1]) / right_radii[1]) ** 2
        + ((xx - right_center[2]) / right_radii[2]) ** 2
    )
    left_field = (
        ((zz - left_center[0]) / left_radii[0]) ** 2
        + ((yy - left_center[1]) / left_radii[1]) ** 2
        + ((xx - left_center[2]) / left_radii[2]) ** 2
    )
    bridge_center = 0.5 * (right_center + left_center)
    bridge_radii = np.array((0.35, 0.30, 0.40), dtype=np.float64)
    bridge_field = (
        ((zz - bridge_center[0]) / bridge_radii[0]) ** 2
        + ((yy - bridge_center[1]) / bridge_radii[1]) ** 2
        + ((xx - bridge_center[2]) / bridge_radii[2]) ** 2
    )

    phase = _phase_from_target(target)
    azimuth = np.arctan2(yy, xx)
    low_frequency = (
        0.55 * np.sin(3.0 * azimuth + phase)
        + 0.30 * np.cos(2.0 * math.pi * zz - 0.7 * phase)
        + 0.15 * np.sin(2.0 * azimuth + 1.5 * math.pi * zz + phase)
    )
    amplitude = min(max(float(target.surface_field_amplitude), 0.0), 0.50)
    return right_field, left_field, bridge_field, low_frequency, xx, float(bridge_center[2]), amplitude


def _threshold_primitive_fields(
    fields: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, float, float],
    threshold: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    right_field, left_field, bridge_field, low_frequency, xx, bridge_center_x, amplitude = fields
    limit = (threshold * np.clip(1.0 + amplitude * low_frequency, 0.35, 1.65)) ** 2
    right = right_field <= limit
    left = left_field <= limit
    bridge = bridge_field <= threshold**2
    mask = right | left | bridge
    right |= bridge & (xx >= bridge_center_x)
    left |= bridge & (xx < bridge_center_x)
    if mask.any():
        labeled, count = ndimage.label(mask)
        if count > 1:
            sizes = np.bincount(labeled.ravel())
            sizes[0] = 0
            mask = labeled == int(np.argmax(sizes))
            right &= mask
            left &= mask
    return mask, right, left, amplitude


def _render_primitives(
    grid: GridSpecV2,
    center_mm: np.ndarray,
    half_extents_mm: np.ndarray,
    threshold: float,
    target: LiverTargetV2,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    fields = _prepare_primitive_fields(grid, center_mm, half_extents_mm, target)
    return _threshold_primitive_fields(fields, threshold)


def _quick_metrics(mask: np.ndarray, grid: GridSpecV2) -> tuple[float, np.ndarray, np.ndarray]:
    indices = np.argwhere(mask)
    if len(indices) == 0:
        return 0.0, np.zeros(3), np.zeros(3)
    spacing = float(grid.voxel_size_mm)
    volume_ml = len(indices) * grid.voxel_volume_ml
    extent = (indices.max(axis=0) - indices.min(axis=0) + 1) * spacing
    centroid = indices.mean(axis=0) @ grid.affine_4x4[:3, :3].T + grid.affine_4x4[:3, 3]
    return float(volume_ml), extent.astype(np.float64), centroid.astype(np.float64)


def _fit_base_mask(
    target: LiverTargetV2,
    grid: GridSpecV2,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, object]]:
    target_extents = np.array((target.si_mm, target.ap_mm, target.lr_mm), dtype=np.float64)
    half_extents = target_extents / 2.0
    render_center = np.asarray(target.centroid_mm, dtype=np.float64)
    best: tuple[float, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, float, float] | None = None

    for _ in range(6):
        fields = _prepare_primitive_fields(grid, render_center, half_extents, target)
        lo, hi = 0.55, 1.35
        inner_best: tuple[float, np.ndarray, np.ndarray, np.ndarray, float, float] | None = None
        for _ in range(14):
            threshold = (lo + hi) / 2.0
            mask, right, left, amplitude = _threshold_primitive_fields(fields, threshold)
            volume, _, _ = _quick_metrics(mask, grid)
            error = abs(volume - target.volume_ml)
            if inner_best is None or error < inner_best[0]:
                inner_best = (error, mask, right, left, threshold, amplitude)
            if volume < target.volume_ml:
                lo = threshold
            else:
                hi = threshold
        if inner_best is None:
            raise RuntimeError("liver primitive threshold fit failed")
        _, mask, right, left, threshold, amplitude = inner_best
        volume, actual_extents, actual_centroid = _quick_metrics(mask, grid)
        if volume <= 0 or np.any(actual_extents <= 0):
            raise RuntimeError("liver primitive fit produced an empty mask")
        volume_error = abs(volume / target.volume_ml - 1.0)
        extent_error = np.max(np.abs(actual_extents - target_extents) / float(grid.voxel_size_mm))
        centroid_error = np.max(np.abs(actual_centroid - np.asarray(target.centroid_mm)) / float(grid.voxel_size_mm))
        score = volume_error / 0.04 + extent_error / 2.5 + centroid_error / 1.5
        if best is None or score < best[0]:
            best = (
                score,
                mask.copy(),
                right.copy(),
                left.copy(),
                render_center.copy(),
                half_extents.copy(),
                threshold,
                amplitude,
            )

        render_center += np.asarray(target.centroid_mm) - actual_centroid
        extent_update = np.clip(target_extents / actual_extents, 0.88, 1.12)
        half_extents *= extent_update

    if best is None:
        raise RuntimeError("liver primitive fit failed")
    _, mask, right, left, fitted_center, fitted_half_extents, fitted_threshold, amplitude = best
    return mask, right, left, {
        "fitted_center_mm_zyx": tuple(float(value) for value in fitted_center),
        "fitted_half_extents_mm_zyx": tuple(float(value) for value in fitted_half_extents),
        "fitted_threshold": float(fitted_threshold),
        "surface_field_amplitude": float(amplitude),
        "surface_field_phase_rad": float(_phase_from_target(target)),
        "hilar_bridge_radii_normalized_zyx": (0.35, 0.30, 0.40),
    }


def _fit_caudate_mask(mask: np.ndarray, target: LiverTargetV2, grid: GridSpecV2) -> np.ndarray:
    if not target.caudate_enabled or target.caudate_fraction <= 0:
        return np.zeros_like(mask)
    target_count = int(round(target.caudate_fraction * int(mask.sum())))
    if target_count <= 0:
        return np.zeros_like(mask)

    affine = grid.affine_4x4
    spacing = float(grid.voxel_size_mm)
    z = affine[0, 3] + np.arange(grid.shape[0]) * spacing
    y = affine[1, 3] + np.arange(grid.shape[1]) * spacing
    x = affine[2, 3] + np.arange(grid.shape[2]) * spacing
    center = np.asarray(target.centroid_mm, dtype=np.float64) + np.array(
        (0.05 * target.si_mm, -0.22 * target.ap_mm, -0.04 * target.lr_mm)
    )
    desired_volume_mm3 = target_count * spacing**3
    base_radius = (3.0 * desired_volume_mm3 / (4.0 * math.pi * 1.10 * 0.75)) ** (1.0 / 3.0)
    zz = z[:, None, None] - center[0]
    yy = y[None, :, None] - center[1]
    xx = x[None, None, :] - center[2]

    lo, hi = 0.25, 3.0
    best = np.zeros_like(mask)
    best_error = math.inf
    for _ in range(18):
        scale = (lo + hi) / 2.0
        radii = base_radius * scale * np.array((1.10, 0.75, 1.0))
        candidate = (
            (zz / radii[0]) ** 2 + (yy / radii[1]) ** 2 + (xx / radii[2]) ** 2 <= 1.0
        ) & mask
        error = abs(int(candidate.sum()) - target_count)
        if error < best_error:
            best = candidate
            best_error = error
        if int(candidate.sum()) < target_count:
            lo = scale
        else:
            hi = scale
    return best


def fit_liver_geometry(target: LiverTargetV2, grid: GridSpecV2) -> LiverGeometryV2:
    """Fit and rasterize a target-constrained double-ellipsoid liver in physical millimetres."""
    if not isinstance(grid, GridSpecV2):
        raise TypeError("grid must be GridSpecV2")
    _validate_target(target, grid)
    mask, right, left, fit_parameters = _fit_base_mask(target, grid)
    caudate = _fit_caudate_mask(mask, target, grid)
    regions: LiverRegionsV2 = build_liver_regions(
        mask,
        grid.affine_4x4,
        target_left_fraction=target.left_fraction,
        target_s1_3_to_s4_8_ratio=target.s1_3_to_s4_8_ratio,
        caudate_mask=caudate,
    )
    measured = measure_liver(mask, grid.affine_4x4)
    actual = asdict(measured)
    actual.update(
        {
            "left_fraction": regions.left_fraction,
            "s1_3_to_s4_8_ratio": regions.s1_3_to_s4_8_ratio,
            "caudate_fraction": regions.caudate_fraction,
            "region_voxel_counts": dict(regions.region_voxel_counts),
        }
    )
    target_metrics = {
        "volume_ml": float(target.volume_ml),
        "extent_mm_zyx": (float(target.si_mm), float(target.ap_mm), float(target.lr_mm)),
        "centroid_world_mm": tuple(float(value) for value in target.centroid_mm),
        "left_fraction": float(target.left_fraction),
        "s1_3_to_s4_8_ratio": float(target.s1_3_to_s4_8_ratio),
        "caudate_fraction": float(target.caudate_fraction),
        "surface_roughness": float(target.surface_roughness_target),
        "surface_field_amplitude": float(target.surface_field_amplitude),
    }
    right_center, right_radii, left_center, left_radii = _primitive_parameters(target.morphology)
    continuous_parameters = {
        **fit_parameters,
        "morphology": target.morphology,
        "surface_field_kind": "analytic_low_frequency",
        "right_center_normalized_zyx": tuple(float(value) for value in right_center),
        "right_radii_normalized_zyx": tuple(float(value) for value in right_radii),
        "left_center_normalized_zyx": tuple(float(value) for value in left_center),
        "left_radii_normalized_zyx": tuple(float(value) for value in left_radii),
        "caudate_enabled": bool(target.caudate_enabled),
    }
    return LiverGeometryV2(
        mask=mask,
        region_labels=regions.labels,
        affine_4x4=grid.affine_4x4,
        primitive_masks={"right": right, "left": left, "caudate": caudate},
        target_metrics=target_metrics,
        actual_metrics=actual,
        continuous_parameters=continuous_parameters,
        evidence_types=dict(target.evidence_types),
    )
