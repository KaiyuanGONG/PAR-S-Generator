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


class LiverShapeRejectedError(RuntimeError):
    """A valid target whose rasterized engineering shape failed explicit QA gates."""

    def __init__(
        self,
        failed_gates: tuple[str, ...],
        shape_quality: Mapping[str, object],
    ) -> None:
        self.failed_gates = tuple(failed_gates)
        self.shape_quality = shape_quality
        super().__init__(f"liver shape candidate rejected by gates: {list(failed_gates)}")


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
    if not 0.0 <= float(target.surface_field_amplitude) <= 0.20:
        raise ValueError("surface_field_amplitude must be within [0, 0.20]")

    centroid = np.asarray(target.centroid_mm, dtype=np.float64)
    lower, upper = grid.world_edge_bounds_mm
    margin = float(grid.voxel_size_mm)
    if np.any(centroid - extents / 2.0 < lower + margin) or np.any(centroid + extents / 2.0 > upper - margin):
        raise ValueError("target extents and centroid do not fit inside grid")
    target_bbox_fill = float(target.volume_ml * 1000.0 / np.prod(extents))
    if not 0.18 <= target_bbox_fill <= 0.40:
        raise ValueError(
            "target volume and extents are jointly implausible for the constructive liver family"
        )


def _phase_from_target(target: LiverTargetV2, shape_seed: int | None = None) -> float:
    centroid = ",".join(f"{float(value):.6f}" for value in target.centroid_mm)
    payload_text = (
        f"{target.volume_ml:.6f}|{target.lr_mm:.6f}|{target.ap_mm:.6f}|"
        f"{target.si_mm:.6f}|{centroid}|{target.morphology}"
    )
    if shape_seed is not None:
        payload_text += f"|shape_seed={shape_seed}"
    payload = payload_text.encode("utf-8")
    integer = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    return 2.0 * math.pi * integer / float(2**64)


@dataclass(frozen=True)
class _ConstructiveTemplate:
    right_center: np.ndarray
    right_radii: np.ndarray
    right_rotation_xz_deg: float
    right_shape_power: float
    left_center: np.ndarray
    left_radii: np.ndarray
    left_rotation_xz_deg: float
    left_shape_power: float
    body_center: np.ndarray
    body_radii: np.ndarray
    dome_center: np.ndarray
    dome_radii: np.ndarray
    visceral_center: np.ndarray
    visceral_radii: np.ndarray
    gallbladder_center: np.ndarray
    gallbladder_radii: np.ndarray
    hilum_center: np.ndarray
    hilum_radii: np.ndarray
    caudate_center: np.ndarray
    caudate_radii: np.ndarray
    shape_coordinates: tuple[float, float]
    left_response_coordinate: float
    caudate_scale_raw: float
    caudate_scale_multiplier: float
    target_bbox_fill_fraction: float
    shape_power_calibration_offset: float


@dataclass(frozen=True)
class _PrimitiveFields:
    right: np.ndarray
    left: np.ndarray
    body: np.ndarray
    dome: np.ndarray
    visceral: np.ndarray
    gallbladder: np.ndarray
    hilum: np.ndarray
    caudate: np.ndarray
    low_frequency: np.ndarray
    template: _ConstructiveTemplate
    requested_surface_amplitude: float
    effective_surface_amplitude: float
    surface_smoothing_sigma_vox: float


def shape_coordinates_for_target(
    target: LiverTargetV2,
    shape_seed: int | None = None,
) -> tuple[float, float]:
    """Return stable, centre-weighted coordinates for continuous outer-shape modes."""
    centroid = ",".join(f"{float(value):.6f}" for value in target.centroid_mm)
    payload_text = (
        f"shape|{target.volume_ml:.6f}|{target.lr_mm:.6f}|{target.ap_mm:.6f}|"
        f"{target.si_mm:.6f}|{centroid}"
    )
    if shape_seed is not None:
        payload_text += f"|shape_seed={shape_seed}"
    payload = payload_text.encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    u1 = max(int.from_bytes(digest[:8], "big") / float(2**64), np.finfo(float).eps)
    u2 = int.from_bytes(digest[8:16], "big") / float(2**64)
    radius = math.sqrt(-2.0 * math.log(u1))
    first = radius * math.cos(2.0 * math.pi * u2)
    second = radius * math.sin(2.0 * math.pi * u2)
    return tuple(float(np.clip(value / 2.5, -1.0, 1.0)) for value in (first, second))


def _constructive_template(
    target: LiverTargetV2,
    *,
    shape_power_offset: float = 0.0,
    caudate_scale_multiplier: float = 1.0,
    shape_seed: int | None = None,
) -> _ConstructiveTemplate:
    """Create an asymmetric wedge family directly in the target-normalized frame."""
    longitudinal, transverse = shape_coordinates_for_target(target, shape_seed)
    cirrhotic = target.morphology == "cirrhotic"
    # The production profile reaches 0.55 in cirrhosis. Do not saturate the
    # outer-shape response at 0.45: doing so silently erased the high-left tail.
    left_delta = float((target.left_fraction - 0.31) / 0.14)
    target_bbox_fill = float(
        target.volume_ml * 1000.0 / (target.si_mm * target.ap_mm * target.lr_mm)
    )
    # A lower Lp power produces a more tapered wedge at fixed outer extents;
    # this is the explicit degree of freedom that reconciles volume and three
    # population-anchored diameters without silently rescaling samples.
    base_shape_power = float(
        np.clip(
            1.54
            + 0.70 * (target_bbox_fill - 0.274) / 0.070
            + float(shape_power_offset),
            1.20,
            2.40,
        )
    )
    right_scale = (
        1.0
        - (0.075 if cirrhotic else 0.0)
        - 0.050 * max(left_delta, 0.0)
        + 0.025 * max(-left_delta, 0.0)
    )
    left_scale = 1.0 + (0.120 if cirrhotic else 0.0) + 0.160 * left_delta
    caudate_reference = max(float(target.caudate_fraction), 0.020)
    caudate_scale_raw = (
        (caudate_reference / 0.020) ** (1.0 / 3.0)
        if target.caudate_enabled
        else 0.0
    )
    caudate_scale = caudate_scale_raw * float(caudate_scale_multiplier)
    return _ConstructiveTemplate(
        right_center=np.array((-0.02 - 0.035 * longitudinal, 0.02, 0.34), dtype=np.float64),
        right_radii=np.array(
            (
                0.96 * right_scale * (1.0 + 0.070 * longitudinal),
                0.78 * right_scale,
                0.46 * right_scale * (1.0 - 0.025 * transverse),
            ),
            dtype=np.float64,
        ),
        right_rotation_xz_deg=-9.0 - 3.0 * longitudinal,
        right_shape_power=float(
            np.clip(base_shape_power + 0.05 + 0.04 * longitudinal, 1.35, 2.30)
        ),
        left_center=np.array((0.12, -0.01, -0.32 - 0.030 * transverse), dtype=np.float64),
        left_radii=np.array(
            (
                0.46 * left_scale * (1.0 - 0.035 * longitudinal),
                0.36 * left_scale,
                0.75 * left_scale * (1.0 + 0.110 * transverse),
            ),
            dtype=np.float64,
        ),
        left_rotation_xz_deg=11.0 + 3.0 * transverse,
        left_shape_power=float(
            np.clip(base_shape_power - 0.08 + 0.04 * transverse, 1.25, 2.20)
        ),
        body_center=np.array((0.00, 0.00, 0.05), dtype=np.float64),
        body_radii=np.array((1.08, 0.88, 1.17), dtype=np.float64),
        dome_center=np.array((-0.45, 0.00, 0.06), dtype=np.float64),
        dome_radii=np.array((1.00, 0.96, 1.22), dtype=np.float64),
        # A broad ellipsoid remains mostly outside the inferior-posterior surface;
        # only its cap is subtracted, producing a shallow visceral concavity rather
        # than a cavity through the parenchyma.
        visceral_center=np.array((-0.85, -0.55, 0.02), dtype=np.float64),
        visceral_radii=np.array((0.50, 0.48, 0.76), dtype=np.float64),
        gallbladder_center=np.array((-0.66, 0.20, 0.16), dtype=np.float64),
        gallbladder_radii=np.array((0.29, 0.23, 0.17), dtype=np.float64),
        hilum_center=np.array((-0.25, -0.46, 0.10), dtype=np.float64),
        hilum_radii=np.array((0.13, 0.18, 0.18), dtype=np.float64),
        caudate_center=np.array((0.05, -0.48, 0.10), dtype=np.float64),
        caudate_radii=np.array((0.27, 0.22, 0.19), dtype=np.float64) * caudate_scale,
        shape_coordinates=(longitudinal, transverse),
        left_response_coordinate=left_delta,
        caudate_scale_raw=caudate_scale_raw,
        caudate_scale_multiplier=float(caudate_scale_multiplier),
        target_bbox_fill_fraction=target_bbox_fill,
        shape_power_calibration_offset=float(shape_power_offset),
    )


def _rotated_ellipsoid_field(
    zz: np.ndarray,
    yy: np.ndarray,
    xx: np.ndarray,
    center: np.ndarray,
    radii: np.ndarray,
    rotation_xz_deg: float = 0.0,
    shape_power: float = 2.0,
) -> np.ndarray:
    theta = math.radians(float(rotation_xz_deg))
    delta_x = xx - center[2]
    delta_z = zz - center[0]
    x_rotated = delta_x * math.cos(theta) - delta_z * math.sin(theta)
    z_rotated = delta_x * math.sin(theta) + delta_z * math.cos(theta)
    lp_sum = (
        np.abs(z_rotated / radii[0]) ** shape_power
        + np.abs((yy - center[1]) / radii[1]) ** shape_power
        + np.abs(x_rotated / radii[2]) ** shape_power
    )
    return lp_sum ** (2.0 / shape_power)


def _prepare_primitive_fields(
    grid: GridSpecV2,
    center_mm: np.ndarray,
    half_extents_mm: np.ndarray,
    target: LiverTargetV2,
    *,
    shape_power_offset: float = 0.0,
    caudate_scale_multiplier: float = 1.0,
    shape_seed: int | None = None,
) -> _PrimitiveFields:
    affine = grid.affine_4x4
    spacing = float(grid.voxel_size_mm)
    z = (affine[0, 3] + np.arange(grid.shape[0]) * spacing - center_mm[0]) / half_extents_mm[0]
    y = (affine[1, 3] + np.arange(grid.shape[1]) * spacing - center_mm[1]) / half_extents_mm[1]
    x = (affine[2, 3] + np.arange(grid.shape[2]) * spacing - center_mm[2]) / half_extents_mm[2]
    zz = z[:, None, None]
    yy = y[None, :, None]
    xx = x[None, None, :]

    template = _constructive_template(
        target,
        shape_power_offset=shape_power_offset,
        caudate_scale_multiplier=caudate_scale_multiplier,
        shape_seed=shape_seed,
    )
    right_field = _rotated_ellipsoid_field(
        zz,
        yy,
        xx,
        template.right_center,
        template.right_radii,
        template.right_rotation_xz_deg,
        template.right_shape_power,
    )
    left_field = _rotated_ellipsoid_field(
        zz,
        yy,
        xx,
        template.left_center,
        template.left_radii,
        template.left_rotation_xz_deg,
        template.left_shape_power,
    )
    body_field = _rotated_ellipsoid_field(
        zz, yy, xx, template.body_center, template.body_radii
    )
    dome_field = _rotated_ellipsoid_field(
        zz, yy, xx, template.dome_center, template.dome_radii
    )
    visceral_field = _rotated_ellipsoid_field(
        zz, yy, xx, template.visceral_center, template.visceral_radii
    )
    gallbladder_field = _rotated_ellipsoid_field(
        zz, yy, xx, template.gallbladder_center, template.gallbladder_radii
    )
    hilum_field = _rotated_ellipsoid_field(
        zz, yy, xx, template.hilum_center, template.hilum_radii
    )
    if target.caudate_enabled:
        caudate_field = _rotated_ellipsoid_field(
            zz, yy, xx, template.caudate_center, template.caudate_radii
        )
    else:
        caudate_field = np.full_like(right_field, np.inf)

    phase = _phase_from_target(target, shape_seed)
    azimuth = np.arctan2(yy, xx)
    low_frequency = (
        0.35 * np.sin(3.0 * azimuth + phase)
        + 0.20 * np.cos(2.0 * math.pi * zz - 0.7 * phase)
        + 0.25 * np.sin(6.0 * azimuth + 3.0 * math.pi * zz + 0.5 * phase)
        + 0.20 * np.cos(5.0 * azimuth - 2.0 * math.pi * zz - phase)
    )
    requested_amplitude = float(target.surface_field_amplitude)
    effective_amplitude = requested_amplitude
    return _PrimitiveFields(
        right=right_field,
        left=left_field,
        body=body_field,
        dome=dome_field,
        visceral=visceral_field,
        gallbladder=gallbladder_field,
        hilum=hilum_field,
        caudate=caudate_field,
        low_frequency=low_frequency,
        template=template,
        requested_surface_amplitude=requested_amplitude,
        effective_surface_amplitude=effective_amplitude,
        surface_smoothing_sigma_vox=0.55 if target.morphology == "cirrhotic" else 1.20,
    )


def _threshold_primitive_fields(
    fields: _PrimitiveFields,
    threshold: float,
    *,
    smooth: bool = False,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    limit = (
        threshold
        * np.clip(
            1.0 + fields.effective_surface_amplitude * fields.low_frequency,
            0.65,
            1.35,
        )
    ) ** 2
    right_raw = fields.right <= limit
    left_raw = fields.left <= limit
    caudate_raw = fields.caudate <= threshold**2
    if not fields.template.caudate_radii.any():
        caudate_raw = np.zeros_like(right_raw)
    body = fields.body <= 1.0
    dome = fields.dome <= 1.0
    local_cut_scale = float(np.clip(1.0 + 1.75 * max(0.0, 1.0 - threshold), 1.0, 1.35))
    visceral = fields.visceral <= local_cut_scale**2
    gallbladder = fields.gallbladder <= local_cut_scale**2
    hilum = fields.hilum <= local_cut_scale**2
    lobe_union = right_raw | left_raw | caudate_raw
    body_clipped = lobe_union & body
    dome_removed = body_clipped & ~dome
    pre_cut = body_clipped & dome
    visceral_removed = pre_cut & visceral
    after_visceral = pre_cut & ~visceral
    gallbladder_removed = after_visceral & gallbladder
    after_gallbladder = after_visceral & ~gallbladder
    hilum_removed = after_gallbladder & hilum
    mask = after_gallbladder & ~hilum
    if smooth and mask.any():
        mask = (
            ndimage.gaussian_filter(
                mask.astype(np.float32), sigma=fields.surface_smoothing_sigma_vox
            )
            > 0.5
        )
        mask &= body & dome & ~visceral & ~gallbladder & ~hilum
    right = right_raw & mask
    left = left_raw & mask
    caudate = caudate_raw & mask
    fossa_removed = visceral_removed | gallbladder_removed | hilum_removed
    primitives = {
        "right": right,
        "left": left,
        "caudate_outer": caudate,
        "body_envelope": body,
        "dome_envelope": dome,
        "dome_removed": dome_removed & ~mask,
        "visceral_concavity": visceral,
        "visceral_removed": visceral_removed & ~mask,
        "gallbladder_fossa": gallbladder,
        "gallbladder_removed": gallbladder_removed & ~mask,
        "hilum_cutout": hilum,
        "hilum_removed": hilum_removed & ~mask,
        "fossa_cutout": visceral | gallbladder | hilum,
        "fossa_removed": fossa_removed & ~mask,
    }
    return mask, primitives


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
    *,
    shape_seed: int | None = None,
) -> tuple[np.ndarray, dict[str, np.ndarray], dict[str, object]]:
    target_extents = np.array((target.si_mm, target.ap_mm, target.lr_mm), dtype=np.float64)
    half_extents = target_extents / 2.0
    render_center = np.asarray(target.centroid_mm, dtype=np.float64)
    target_bbox_fill = float(target.volume_ml * 1000.0 / np.prod(target_extents))
    shape_power_offset = 0.0
    caudate_scale_multiplier = 1.0
    caudate_calibration_updates = 0
    best: tuple[
        float,
        np.ndarray,
        dict[str, np.ndarray],
        np.ndarray,
        np.ndarray,
        float,
        _ConstructiveTemplate,
        float,
        float,
        int,
    ] | None = None

    for _ in range(10):
        fields = _prepare_primitive_fields(
            grid,
            render_center,
            half_extents,
            target,
            shape_power_offset=shape_power_offset,
            caudate_scale_multiplier=caudate_scale_multiplier,
            shape_seed=shape_seed,
        )
        lo, hi = 0.85, 1.15
        inner_best: tuple[float, float] | None = None
        for _ in range(14):
            threshold = (lo + hi) / 2.0
            mask, _ = _threshold_primitive_fields(fields, threshold)
            volume, _, _ = _quick_metrics(mask, grid)
            error = abs(volume - target.volume_ml)
            if inner_best is None or error < inner_best[0]:
                inner_best = (error, threshold)
            if volume < target.volume_ml:
                lo = threshold
            else:
                hi = threshold
        if inner_best is None:
            raise RuntimeError("liver primitive threshold fit failed")
        _, threshold = inner_best
        mask, primitives = _threshold_primitive_fields(fields, threshold, smooth=True)
        volume, actual_extents, actual_centroid = _quick_metrics(mask, grid)
        if volume <= 0 or np.any(actual_extents <= 0):
            raise RuntimeError("liver primitive fit produced an empty mask")
        volume_error = abs(volume / target.volume_ml - 1.0)
        extent_error = np.max(np.abs(actual_extents - target_extents) / float(grid.voxel_size_mm))
        centroid_error = np.max(np.abs(actual_centroid - np.asarray(target.centroid_mm)) / float(grid.voxel_size_mm))
        caudate_outer_only = primitives["caudate_outer"] & ~(
            primitives["right"] | primitives["left"]
        )
        caudate_outer_fraction = float(caudate_outer_only.sum() / mask.sum())
        caudate_outer_upper = (
            min(0.08, float(target.caudate_fraction) + 1.0 / float(mask.sum()))
            if target.caudate_enabled
            else 0.0
        )
        caudate_excess = max(0.0, caudate_outer_fraction - caudate_outer_upper)
        score = (
            volume_error / 0.02
            + extent_error / 2.5
            + centroid_error / 1.5
            + caudate_excess / 0.002
        )
        if best is None or score < best[0]:
            best = (
                score,
                mask.copy(),
                {name: value.copy() for name, value in primitives.items()},
                render_center.copy(),
                half_extents.copy(),
                threshold,
                fields.template,
                fields.requested_surface_amplitude,
                fields.effective_surface_amplitude,
                caudate_calibration_updates,
            )

        render_center += np.asarray(target.centroid_mm) - actual_centroid
        extent_update = np.clip(target_extents / actual_extents, 0.75, 1.30)
        half_extents *= extent_update
        actual_bbox_fill = float(volume * 1000.0 / np.prod(actual_extents))
        shape_power_offset = float(
            np.clip(
                shape_power_offset + 4.0 * (target_bbox_fill - actual_bbox_fill),
                -0.45,
                0.45,
            )
        )
        if target.caudate_enabled and caudate_excess > 0.0:
            shrink = float(
                np.clip(
                    0.98
                    * (caudate_outer_upper / max(caudate_outer_fraction, 1e-12))
                    ** (1.0 / 3.0),
                    0.55,
                    0.98,
                )
            )
            caudate_scale_multiplier *= shrink
            caudate_calibration_updates += 1

    if best is None:
        raise RuntimeError("liver primitive fit failed")
    (
        _,
        mask,
        primitives,
        fitted_center,
        fitted_half_extents,
        fitted_threshold,
        template,
        requested_amplitude,
        effective_amplitude,
        caudate_calibration_updates,
    ) = best
    def normalized(value: np.ndarray) -> tuple[float, float, float]:
        return tuple(float(item) for item in value)

    return mask, primitives, {
        "fitted_center_mm_zyx": tuple(float(value) for value in fitted_center),
        "fitted_half_extents_mm_zyx": tuple(float(value) for value in fitted_half_extents),
        "fitted_threshold": float(fitted_threshold),
        "surface_field_amplitude_requested": float(requested_amplitude),
        "surface_field_amplitude_effective": float(effective_amplitude),
        "surface_field_phase_rad": float(_phase_from_target(target, shape_seed)),
        "shape_seed": int(shape_seed) if shape_seed is not None else None,
        "surface_smoothing_sigma_vox": float(fields.surface_smoothing_sigma_vox),
        "connection_kind": "natural_lobe_overlap",
        "component_policy": "reject_not_keep_largest",
        "constructive_source": "population_anchored_continuous_csg",
        "shape_family": "asymmetric_wedge_with_continuous_variation",
        "shape_variation_coordinate": float(template.shape_coordinates[0]),
        "shape_transverse_coordinate": float(template.shape_coordinates[1]),
        "left_response_coordinate": float(template.left_response_coordinate),
        "right_center_normalized_zyx": normalized(template.right_center),
        "right_radii_normalized_zyx": normalized(template.right_radii),
        "right_rotation_xz_deg": float(template.right_rotation_xz_deg),
        "right_shape_power": float(template.right_shape_power),
        "left_center_normalized_zyx": normalized(template.left_center),
        "left_radii_normalized_zyx": normalized(template.left_radii),
        "left_rotation_xz_deg": float(template.left_rotation_xz_deg),
        "left_shape_power": float(template.left_shape_power),
        "body_center_normalized_zyx": normalized(template.body_center),
        "body_radii_normalized_zyx": normalized(template.body_radii),
        "dome_center_normalized_zyx": normalized(template.dome_center),
        "dome_radii_normalized_zyx": normalized(template.dome_radii),
        "visceral_center_normalized_zyx": normalized(template.visceral_center),
        "visceral_radii_normalized_zyx": normalized(template.visceral_radii),
        "gallbladder_center_normalized_zyx": normalized(template.gallbladder_center),
        "gallbladder_radii_normalized_zyx": normalized(template.gallbladder_radii),
        "hilum_center_normalized_zyx": normalized(template.hilum_center),
        "hilum_radii_normalized_zyx": normalized(template.hilum_radii),
        "caudate_center_normalized_zyx": normalized(template.caudate_center),
        "caudate_radii_normalized_zyx": normalized(template.caudate_radii),
        "caudate_scale_raw": float(template.caudate_scale_raw),
        "caudate_scale_multiplier": float(template.caudate_scale_multiplier),
        "caudate_calibration_updates": int(caudate_calibration_updates),
        "target_bbox_fill_fraction": float(template.target_bbox_fill_fraction),
        "shape_power_calibration_offset": float(template.shape_power_calibration_offset),
    }


def _fit_caudate_mask(
    mask: np.ndarray,
    target: LiverTargetV2,
    grid: GridSpecV2,
    *,
    required_seed: np.ndarray | None = None,
) -> np.ndarray:
    if not target.caudate_enabled or target.caudate_fraction <= 0:
        return np.zeros_like(mask)
    if required_seed is None:
        required = np.zeros_like(mask)
    else:
        required_array = np.asarray(required_seed, dtype=bool)
        if required_array.shape != mask.shape:
            raise ValueError("required caudate seed must match liver mask shape")
        required = required_array & mask
    target_count = max(
        int(round(target.caudate_fraction * int(mask.sum()))),
        int(required.sum()),
    )
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
        candidate |= required
        error = abs(int(candidate.sum()) - target_count)
        if error < best_error:
            best = candidate
            best_error = error
        if int(candidate.sum()) < target_count:
            lo = scale
        else:
            hi = scale
    return best


def _single_component_slice_fractions(mask: np.ndarray) -> tuple[float, float, float]:
    fractions: list[float] = []
    for axis in range(3):
        component_counts = [
            ndimage.label(np.take(mask, index, axis=axis))[1]
            for index in range(mask.shape[axis])
            if np.take(mask, index, axis=axis).any()
        ]
        fractions.append(
            float(np.mean(np.asarray(component_counts, dtype=np.int32) == 1))
            if component_counts
            else 0.0
        )
    return tuple(fractions)  # type: ignore[return-value]


def _external_background(mask: np.ndarray) -> np.ndarray:
    seed = np.zeros_like(mask, dtype=bool)
    seed[0, :, :] = True
    seed[-1, :, :] = True
    seed[:, 0, :] = True
    seed[:, -1, :] = True
    seed[:, :, 0] = True
    seed[:, :, -1] = True
    seed &= ~mask
    return ndimage.binary_propagation(seed, mask=~mask)


def _central_waist_ratio(
    mask: np.ndarray,
    right: np.ndarray,
    left: np.ndarray,
) -> float:
    total_areas = ndimage.gaussian_filter1d(
        mask.sum(axis=(0, 1)).astype(np.float64), sigma=1.0
    )
    right_areas = right.sum(axis=(0, 1))
    left_areas = left.sum(axis=(0, 1))
    if not total_areas.any() or not right_areas.any() or not left_areas.any():
        return 0.0
    right_peak = int(np.argmax(right_areas))
    left_peak = int(np.argmax(left_areas))
    lo, hi = sorted((right_peak, left_peak))
    if hi <= lo:
        return 1.0
    denominator = min(total_areas[right_peak], total_areas[left_peak])
    if denominator <= 0:
        return 0.0
    return float(np.min(total_areas[lo : hi + 1]) / denominator)


def _left_lobe_taper(left: np.ndarray) -> tuple[float, float, float, float]:
    """Measure whether the lateral left lobe grows smoothly from a thin tip."""
    areas = left.sum(axis=(0, 1)).astype(np.float64)
    active = np.flatnonzero(areas)
    if len(active) < 4:
        return 1.0, 0.0, 1.0, 0.0
    profile = ndimage.gaussian_filter1d(areas[active], sigma=1.0)
    peak = int(np.argmax(profile))
    rising = profile[: peak + 1]
    if len(rising) < 4 or profile.max() <= 0:
        return 1.0, 0.0, 1.0, float(peak / max(len(profile) - 1, 1))
    positions = np.linspace(0.0, 1.0, len(rising))
    lateral = rising[(positions >= 0.10) & (positions <= 0.35)]
    medial = rising[(positions >= 0.65) & (positions <= 0.90)]
    if len(lateral) == 0 or len(medial) == 0 or medial.mean() <= 0:
        taper_ratio = 1.0
    else:
        taper_ratio = float(lateral.mean() / medial.mean())
    allowed_drop = 0.03 * float(profile.max())
    rising_step_fraction = float(np.mean(np.diff(rising) >= -allowed_drop))
    maximum_rise_fraction = float(np.max(np.diff(rising)) / profile.max())
    peak_position_fraction = float(peak / max(len(profile) - 1, 1))
    return taper_ratio, rising_step_fraction, maximum_rise_fraction, peak_position_fraction


def _geometric_left_fraction(right: np.ndarray, left: np.ndarray) -> float:
    """Assign overlap equally to expose the lobes' actual geometric balance."""
    union = right | left
    if not union.any():
        return 0.0
    overlap = right & left
    weighted_left = float((left & ~right).sum()) + 0.5 * float(overlap.sum())
    return weighted_left / float(union.sum())


def _shape_quality(
    mask: np.ndarray,
    primitives: Mapping[str, np.ndarray],
    target: LiverTargetV2,
    grid: GridSpecV2,
) -> dict[str, object]:
    right = primitives["right"]
    left = primitives["left"]
    lobe_union = right | left
    overlap_fraction = (
        float((right & left).sum() / lobe_union.sum()) if lobe_union.any() else 0.0
    )
    dome_removed = primitives["dome_removed"]
    fossa_removed = primitives["fossa_removed"]
    pre_dome = mask | fossa_removed | dome_removed
    pre_fossa = mask | fossa_removed
    dome_removed_fraction = (
        float(dome_removed.sum() / pre_dome.sum()) if pre_dome.any() else 0.0
    )
    fossa_removed_fraction = (
        float(fossa_removed.sum() / pre_fossa.sum()) if pre_fossa.any() else 0.0
    )
    slice_fractions = _single_component_slice_fractions(mask)
    indices = np.argwhere(mask)
    bbox_fill_fraction = 0.0
    if len(indices):
        bbox_shape = indices.max(axis=0) - indices.min(axis=0) + 1
        bbox_fill_fraction = float(mask.sum() / np.prod(bbox_shape))
    target_bbox_fill_fraction = float(
        target.volume_ml * 1000.0 / (target.si_mm * target.ap_mm * target.lr_mm)
    )
    external_background = _external_background(mask)
    fossa_open = bool(fossa_removed.any() and np.any(fossa_removed & external_background))
    internal_background_voxels = int((~mask & ~external_background).sum())
    central_waist_ratio = _central_waist_ratio(mask, right, left)
    (
        taper_ratio,
        rising_step_fraction,
        maximum_rise_fraction,
        left_peak_position,
    ) = _left_lobe_taper(left)
    geometric_left_fraction = _geometric_left_fraction(right, left)
    caudate_outer_only = primitives["caudate_outer"] & ~(right | left)
    caudate_outer_fraction = (
        float(caudate_outer_only.sum() / mask.sum()) if mask.any() else 0.0
    )
    caudate_outer_upper = (
        min(0.08, float(target.caudate_fraction) + 1.0 / float(mask.sum()))
        if target.caudate_enabled and mask.any()
        else 0.0
    )
    surface = mask & ~ndimage.binary_erosion(mask)
    caudate_surface_voxels = int((caudate_outer_only & surface).sum())
    caudate_surface_exposure = (
        float(caudate_surface_voxels / caudate_outer_only.sum())
        if caudate_outer_only.any()
        else 0.0
    )
    voxel_volume_ml = grid.voxel_volume_ml
    voxel_width_mm = float(grid.voxel_size_mm)
    gallbladder_removed_ml = float(primitives["gallbladder_removed"].sum()) * voxel_volume_ml
    hilum_removed_ml = float(primitives["hilum_removed"].sum()) * voxel_volume_ml
    pre_cut = mask | fossa_removed
    pre_cut_surface = pre_cut & ~ndimage.binary_erosion(pre_cut)
    cut_mouths: dict[str, dict[str, float]] = {}
    for name in ("visceral_removed", "gallbladder_removed", "hilum_removed"):
        removed = primitives[name]
        mouth_voxels = int((removed & pre_cut_surface).sum())
        cut_mouths[name] = {
            "removed_voxels": int(removed.sum()),
            "mouth_voxels": mouth_voxels,
            "mouth_fraction": float(mouth_voxels / removed.sum()) if removed.any() else 0.0,
            "mouth_area_mm2_proxy": float(mouth_voxels * voxel_width_mm**2),
        }
    cuts_have_open_mouth = all(
        record["mouth_fraction"] >= 0.05 and record["mouth_area_mm2_proxy"] >= 50.0
        for record in cut_mouths.values()
    )
    gates = {
        "connected_3d": ndimage.label(mask)[1] == 1,
        "natural_lobe_overlap": overlap_fraction >= 0.03,
        "no_dumbbell_waist": central_waist_ratio >= 0.55,
        "left_lobe_tapers_laterally": taper_ratio <= 0.65
        and rising_step_fraction >= 0.70
        and maximum_rise_fraction <= 0.30
        and left_peak_position >= 0.30,
        "geometric_left_fraction_tracks_target": abs(
            geometric_left_fraction - float(target.left_fraction)
        )
        <= 0.12,
        "caudate_changes_outer_geometry": (
            max(0.001, 0.02 * float(target.caudate_fraction))
            <= caudate_outer_fraction
            <= caudate_outer_upper
            and caudate_surface_exposure >= 0.20
            and caudate_surface_voxels >= 8
            if target.caudate_enabled
            else caudate_outer_fraction == 0.0 and caudate_surface_voxels == 0
        ),
        "dome_arc_present": 0.005 <= dome_removed_fraction <= 0.30,
        "open_visceral_surface": 0.005 <= fossa_removed_fraction <= 0.12
        and fossa_open
        and cuts_have_open_mouth,
        "no_internal_cavity": internal_background_voxels == 0,
        "local_gallbladder_fossa_scale": 2.0 <= gallbladder_removed_ml <= 80.0,
        "local_hilum_present": hilum_removed_ml > 0.0,
        "single_component_slices": min(slice_fractions) >= 0.80,
        "target_joint_volume_extent_plausibility": 0.18 <= target_bbox_fill_fraction <= 0.40,
    }
    gates = {name: bool(value) for name, value in gates.items()}
    return {
        "status": "pass" if all(gates.values()) else "fail",
        "lobe_overlap_fraction": overlap_fraction,
        "dome_removed_fraction": dome_removed_fraction,
        "fossa_removed_fraction": fossa_removed_fraction,
        "fossa_open_to_surface": fossa_open,
        "internal_background_voxels": internal_background_voxels,
        "central_waist_ratio": central_waist_ratio,
        "left_lateral_to_medial_area_ratio": taper_ratio,
        "left_rising_step_fraction": rising_step_fraction,
        "left_maximum_rise_fraction": maximum_rise_fraction,
        "left_peak_position_fraction": left_peak_position,
        "geometric_left_fraction": geometric_left_fraction,
        "geometric_left_fraction_error": geometric_left_fraction
        - float(target.left_fraction),
        "caudate_outer_only_voxels": int(caudate_outer_only.sum()),
        "caudate_outer_fraction": caudate_outer_fraction,
        "caudate_outer_fraction_upper": caudate_outer_upper,
        "caudate_surface_voxels": caudate_surface_voxels,
        "caudate_surface_exposure": caudate_surface_exposure,
        "cut_mouths": cut_mouths,
        "gallbladder_removed_ml": gallbladder_removed_ml,
        "hilum_removed_ml": hilum_removed_ml,
        "single_component_slice_fraction_zyx": slice_fractions,
        "bbox_fill_fraction": bbox_fill_fraction,
        "target_bbox_fill_fraction": target_bbox_fill_fraction,
        "bbox_fill_absolute_error": abs(bbox_fill_fraction - target_bbox_fill_fraction),
        "gates": gates,
    }


def fit_liver_geometry(
    target: LiverTargetV2,
    grid: GridSpecV2,
    *,
    shape_seed: int | None = None,
) -> LiverGeometryV2:
    """Fit and rasterize a population-anchored asymmetric liver CSG in millimetres."""
    if not isinstance(grid, GridSpecV2):
        raise TypeError("grid must be GridSpecV2")
    if shape_seed is not None and (
        not isinstance(shape_seed, int)
        or isinstance(shape_seed, bool)
        or not 1 <= shape_seed <= 2**63 - 1
    ):
        raise ValueError("shape_seed must be an integer within [1, 2^63-1]")
    _validate_target(target, grid)
    mask, primitives, fit_parameters = _fit_base_mask(
        target,
        grid,
        shape_seed=shape_seed,
    )
    shape_quality = _shape_quality(mask, primitives, target, grid)
    caudate_outer_seed = primitives["caudate_outer"] & ~(
        primitives["right"] | primitives["left"]
    )
    caudate = _fit_caudate_mask(
        mask,
        target,
        grid,
        required_seed=caudate_outer_seed,
    )
    caudate_seed_covered = bool(np.all(~caudate_outer_seed | caudate))
    shape_quality["gates"]["caudate_outer_is_s1"] = caudate_seed_covered
    shape_quality["caudate_outer_is_s1"] = caudate_seed_covered
    shape_quality["status"] = (
        "pass" if all(shape_quality["gates"].values()) else "fail"
    )
    if shape_quality["status"] != "pass":
        failed = tuple(
            name for name, passed in shape_quality["gates"].items() if not passed
        )
        raise LiverShapeRejectedError(failed, shape_quality)
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
            "shape_quality": shape_quality,
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
    continuous_parameters = {
        **fit_parameters,
        "morphology": target.morphology,
        "surface_field_kind": "analytic_multiscale",
        "caudate_enabled": bool(target.caudate_enabled),
    }
    primitive_masks = {**primitives, "caudate": caudate}
    return LiverGeometryV2(
        mask=mask,
        region_labels=regions.labels,
        affine_4x4=grid.affine_4x4,
        primitive_masks=primitive_masks,
        target_metrics=target_metrics,
        actual_metrics=actual,
        continuous_parameters=continuous_parameters,
        evidence_types=dict(target.evidence_types),
    )
