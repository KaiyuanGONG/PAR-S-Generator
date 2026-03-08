"""
Shared parameter metadata for validation and UI controls.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NumericParameterSpec:
    key: str
    label: str
    description: str
    recommended_min: float
    recommended_max: float
    hard_min: float
    hard_max: float
    decimals: int = 0

    @property
    def is_int(self) -> bool:
        return self.decimals == 0


VOLUME_PRESETS: list[tuple[int, float]] = [
    (96, 5.89),
    (128, 4.42),
    (160, 3.54),
]


NUMERIC_SPECS: dict[str, NumericParameterSpec] = {
    "matrix_size": NumericParameterSpec(
        key="matrix_size",
        label="Matrix (NxNxN)",
        description="Volume matrix size. Larger matrices give finer sampling but need more compute.",
        recommended_min=96,
        recommended_max=160,
        hard_min=64,
        hard_max=256,
        decimals=0,
    ),
    "voxel_size_mm": NumericParameterSpec(
        key="voxel_size_mm",
        label="Voxel size (mm)",
        description="Physical voxel edge length in millimeters. Tumor size remains defined in millimeters.",
        recommended_min=3.54,
        recommended_max=5.89,
        hard_min=2.5,
        hard_max=8.0,
        decimals=2,
    ),
    "scale_jitter": NumericParameterSpec(
        key="scale_jitter",
        label="Scale jitter",
        description="Random overall liver size variation around the base geometry.",
        recommended_min=0.0,
        recommended_max=0.20,
        hard_min=0.0,
        hard_max=0.40,
        decimals=2,
    ),
    "rot_jitter_deg": NumericParameterSpec(
        key="rot_jitter_deg",
        label="Rotation jitter (°)",
        description="Random liver rotation amplitude in degrees.",
        recommended_min=0.0,
        recommended_max=15.0,
        hard_min=0.0,
        hard_max=30.0,
        decimals=1,
    ),
    "global_shift_range": NumericParameterSpec(
        key="global_shift_range",
        label="Global shift range",
        description="Random whole-liver position shift around the reference center.",
        recommended_min=0.0,
        recommended_max=0.10,
        hard_min=0.0,
        hard_max=0.20,
        decimals=3,
    ),
    "target_left_ratio": NumericParameterSpec(
        key="target_left_ratio",
        label="Target left ratio",
        description="Desired proportion of liver volume in the left lobe.",
        recommended_min=0.25,
        recommended_max=0.45,
        hard_min=0.15,
        hard_max=0.60,
        decimals=2,
    ),
    "smooth_sigma": NumericParameterSpec(
        key="smooth_sigma",
        label="Smoothing σ (px)",
        description="Surface smoothing applied to the liver mask after geometry composition.",
        recommended_min=0.8,
        recommended_max=2.0,
        hard_min=0.0,
        hard_max=4.0,
        decimals=1,
    ),
    "tumor_count": NumericParameterSpec(
        key="tumor_count",
        label="Tumor count",
        description="Number of tumors. Preview can force an exact count; batch still uses min/max.",
        recommended_min=0,
        recommended_max=5,
        hard_min=0,
        hard_max=8,
        decimals=0,
    ),
    "tumor_contrast": NumericParameterSpec(
        key="tumor_contrast",
        label="Tumor contrast",
        description="Tumor-to-liver activity ratio.",
        recommended_min=2.0,
        recommended_max=8.0,
        hard_min=1.0,
        hard_max=12.0,
        decimals=1,
    ),
    "total_counts": NumericParameterSpec(
        key="total_counts",
        label="Total counts (×10⁴)",
        description="Total activity normalisation target for the source map (used by SIMIND as probability density).",
        recommended_min=5.0,
        recommended_max=20.0,
        hard_min=1.0,
        hard_max=50.0,
        decimals=1,
    ),
    "residual_bg": NumericParameterSpec(
        key="residual_bg",
        label="Residual BG",
        description="Residual activity in non-dominant perfusion regions.",
        recommended_min=0.0,
        recommended_max=0.15,
        hard_min=0.0,
        hard_max=0.50,
        decimals=2,
    ),
}


TUMOR_MODE_OPTIONS = [
    ("random", "Random"),
    ("ellipsoid", "Ellipsoid"),
    ("spiculated", "Spiculated"),
]

PERFUSION_MODE_OPTIONS = [
    ("random", "Random"),
    ("whole_liver", "Whole Liver"),
    ("tumor_only", "Tumor Only"),
    ("left_only", "Left Only"),
    ("right_only", "Right Only"),
]
