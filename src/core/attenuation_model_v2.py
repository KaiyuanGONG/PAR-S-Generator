from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping

import numpy as np
from scipy import ndimage

from .schemas_v2 import PopulationProfileV2


@dataclass(frozen=True)
class AttenuationAnatomyV2:
    body_mask: np.ndarray
    liver_mask: np.ndarray
    lung_mask: np.ndarray
    bone_mask: np.ndarray
    fat_mask: np.ndarray
    affine_4x4: np.ndarray


@dataclass(frozen=True)
class AttenuationDegradationMetadataV2:
    profile_id: str
    mu_true_semantic_key: str
    mu_input_semantic_key: str
    simind_allowed_map_key: str
    unit: str
    hu_conversion: str
    blur_sigma_mm: float
    blur_sigma_voxels: float
    hu_noise_sd: float
    hu_bias_field_sd: float
    hu_bias_correlation_length_mm: float
    uncalibrated_ct_degradation: bool
    degradation_applied_only_to_mu_input: bool
    tissue_coefficients_cm1: Mapping[str, float] = field(default_factory=dict)


def _mask(value: np.ndarray, name: str, shape: tuple[int, int, int] | None = None) -> np.ndarray:
    array = np.asarray(value)
    if array.ndim != 3:
        raise ValueError(f"{name} must be a 3D array")
    if shape is not None and array.shape != shape:
        raise ValueError(f"{name} must match body_mask shape")
    return array.astype(bool, copy=False)


def _validate_anatomy(anatomy: AttenuationAnatomyV2) -> tuple[dict[str, np.ndarray], np.ndarray, float]:
    if not isinstance(anatomy, AttenuationAnatomyV2):
        raise TypeError("anatomy must be AttenuationAnatomyV2")
    body = _mask(anatomy.body_mask, "body_mask")
    if not body.any():
        raise ValueError("body_mask is empty")
    masks = {
        "body": body,
        "liver": _mask(anatomy.liver_mask, "liver_mask", body.shape),
        "lung": _mask(anatomy.lung_mask, "lung_mask", body.shape),
        "bone": _mask(anatomy.bone_mask, "bone_mask", body.shape),
        "fat": _mask(anatomy.fat_mask, "fat_mask", body.shape),
    }
    for name in ("liver", "lung", "bone", "fat"):
        if np.any(masks[name] & ~body):
            raise ValueError(f"{name}_mask must be contained in body_mask")
    names = ("liver", "lung", "bone", "fat")
    for index, first in enumerate(names):
        for second in names[index + 1 :]:
            if np.any(masks[first] & masks[second]):
                raise ValueError(f"{first}_mask and {second}_mask must not overlap")

    affine = np.asarray(anatomy.affine_4x4, dtype=np.float64)
    if affine.shape != (4, 4) or not np.isfinite(affine).all():
        raise ValueError("affine_4x4 must be a finite 4x4 matrix")
    if not np.allclose(affine[3], (0.0, 0.0, 0.0, 1.0), atol=1e-10):
        raise ValueError("affine_4x4 must have homogeneous last row")
    spacing = np.linalg.norm(affine[:3, :3], axis=0)
    if np.any(spacing <= 0.0) or not np.allclose(spacing, spacing[0], atol=1e-6):
        raise ValueError("attenuation model requires positive isotropic voxel spacing")
    return masks, affine, float(spacing[0])


def _coefficients(profile: PopulationProfileV2) -> dict[str, float]:
    raw = profile.value("attenuation_coefficients_140kev_cm1")
    if not isinstance(raw, Mapping):
        raise TypeError("attenuation_coefficients_140kev_cm1 must be a mapping")
    required = ("outside_air", "lung", "fat", "water", "soft_tissue", "liver", "bone")
    if set(raw) != set(required):
        raise ValueError(f"attenuation coefficient table must contain exactly {required}")
    result = {name: float(raw[name]) for name in required}
    if any(not math.isfinite(value) or value < 0.0 for value in result.values()):
        raise ValueError("attenuation coefficients must be finite and non-negative")
    if not math.isclose(result["fat"], 0.146, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("V2 fat attenuation must be exactly 0.146 cm^-1")
    if result["water"] <= 0.0:
        raise ValueError("water attenuation must be positive")
    return result


def mu_to_hu(mu_cm1: np.ndarray, water_mu_cm1: float) -> np.ndarray:
    values = np.asarray(mu_cm1, dtype=np.float32)
    if not math.isfinite(water_mu_cm1) or water_mu_cm1 <= 0.0:
        raise ValueError("water_mu_cm1 must be positive and finite")
    if not np.isfinite(values).all() or np.any(values < 0.0):
        raise ValueError("mu values must be finite and non-negative")
    return (1000.0 * (values / float(water_mu_cm1) - 1.0)).astype(np.float32)


def hu_to_mu(hu: np.ndarray, water_mu_cm1: float) -> np.ndarray:
    values = np.asarray(hu, dtype=np.float32)
    if not math.isfinite(water_mu_cm1) or water_mu_cm1 <= 0.0:
        raise ValueError("water_mu_cm1 must be positive and finite")
    if not np.isfinite(values).all():
        raise ValueError("HU values must be finite")
    return np.maximum(0.0, float(water_mu_cm1) * (values / 1000.0 + 1.0)).astype(
        np.float32
    )


def _physical_mu_true(
    masks: Mapping[str, np.ndarray],
    coefficients: Mapping[str, float],
) -> np.ndarray:
    result = np.zeros(masks["body"].shape, dtype=np.float32)
    result[masks["body"]] = coefficients["soft_tissue"]
    result[masks["fat"]] = coefficients["fat"]
    result[masks["lung"]] = coefficients["lung"]
    result[masks["liver"]] = coefficients["liver"]
    result[masks["bone"]] = coefficients["bone"]
    return result


def _standardized_bias_field(
    shape: tuple[int, int, int],
    body_mask: np.ndarray,
    sigma_voxels: float,
    rng: np.random.Generator,
) -> np.ndarray:
    noise = rng.normal(size=shape).astype(np.float32)
    field = ndimage.gaussian_filter(noise, sigma=sigma_voxels, mode="reflect")
    values = field[body_mask]
    sd = float(values.std(ddof=0))
    if sd <= 1e-8:
        return np.zeros(shape, dtype=np.float32)
    return ((field - float(values.mean())) / sd).astype(np.float32)


def generate_attenuation_maps(
    anatomy: AttenuationAnatomyV2,
    profile: PopulationProfileV2,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray, AttenuationDegradationMetadataV2]:
    """Return deterministic physical mu_true and independently degraded CT-like mu_input."""
    if not isinstance(rng, np.random.Generator):
        raise TypeError("rng must be numpy.random.Generator")
    masks, _, voxel_size_mm = _validate_anatomy(anatomy)
    coefficients = _coefficients(profile)
    mu_true = _physical_mu_true(masks, coefficients)

    raw_model = profile.value("ct_degradation_model_v2")
    if not isinstance(raw_model, Mapping):
        raise TypeError("ct_degradation_model_v2 must be a mapping")
    blur_sigma_mm = float(raw_model["blur_sigma_mm"])
    hu_noise_sd = float(raw_model["hu_noise_sd"])
    hu_bias_sd = float(raw_model["hu_bias_field_sd"])
    bias_length_mm = float(raw_model["hu_bias_correlation_length_mm"])
    hu_lower, hu_upper = map(float, raw_model["hu_clip_range"])
    if min(blur_sigma_mm, hu_noise_sd, hu_bias_sd, bias_length_mm) < 0.0:
        raise ValueError("CT degradation magnitudes must be non-negative")
    if not hu_lower < hu_upper:
        raise ValueError("hu_clip_range must be increasing")
    blur_sigma_voxels = blur_sigma_mm / voxel_size_mm
    hu = mu_to_hu(mu_true, coefficients["water"])
    hu[~masks["body"]] = -1000.0
    if blur_sigma_voxels > 0.0:
        hu = ndimage.gaussian_filter(hu, sigma=blur_sigma_voxels, mode="nearest")
    if hu_bias_sd > 0.0:
        bias = _standardized_bias_field(
            hu.shape,
            masks["body"],
            bias_length_mm / voxel_size_mm,
            rng,
        )
        hu[masks["body"]] += hu_bias_sd * bias[masks["body"]]
    if hu_noise_sd > 0.0:
        hu[masks["body"]] += rng.normal(
            0.0,
            hu_noise_sd,
            size=int(np.count_nonzero(masks["body"])),
        ).astype(np.float32)
    hu = np.clip(hu, hu_lower, hu_upper)
    mu_input = hu_to_mu(hu, coefficients["water"])
    mu_input[~masks["body"]] = 0.0

    for name, values in (("mu_true_140kev", mu_true), ("mu_input_140kev", mu_input)):
        if values.dtype != np.float32:
            raise RuntimeError(f"{name} must be float32")
        if not np.isfinite(values).all() or np.any(values < 0.0):
            raise RuntimeError(f"{name} must be finite and non-negative")
        if np.any(values[~masks["body"]] != 0.0):
            raise RuntimeError(f"{name} must be zero outside body")

    metadata = AttenuationDegradationMetadataV2(
        profile_id=profile.profile_id,
        mu_true_semantic_key="mu_true_140kev",
        mu_input_semantic_key="mu_input_140kev",
        simind_allowed_map_key="mu_true_140kev",
        unit="cm^-1",
        hu_conversion=str(raw_model["hu_conversion"]),
        blur_sigma_mm=blur_sigma_mm,
        blur_sigma_voxels=blur_sigma_voxels,
        hu_noise_sd=hu_noise_sd,
        hu_bias_field_sd=hu_bias_sd,
        hu_bias_correlation_length_mm=bias_length_mm,
        uncalibrated_ct_degradation=bool(raw_model["uncalibrated_ct_degradation"]),
        degradation_applied_only_to_mu_input=True,
        tissue_coefficients_cm1=dict(coefficients),
    )
    return mu_true, mu_input.astype(np.float32, copy=False), metadata


def select_simind_attenuation_map(
    map_key: str,
    mu_true_140kev: np.ndarray,
) -> np.ndarray:
    """Typed semantic gate used by the future Task 7 SIMIND execution layer."""
    if map_key != "mu_true_140kev":
        raise ValueError("SIMIND attenuation input accepts only mu_true_140kev")
    values = np.asarray(mu_true_140kev)
    if values.ndim != 3 or values.dtype != np.float32:
        raise ValueError("mu_true_140kev must be a 3D float32 array")
    if not np.isfinite(values).all() or np.any(values < 0.0):
        raise ValueError("mu_true_140kev must be finite and non-negative")
    return values
