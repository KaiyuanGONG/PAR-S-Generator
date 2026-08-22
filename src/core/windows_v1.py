"""Strict public configuration for the native Windows v1 production profile."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


SCHEMA_VERSION = "windows_v1"
GENERATION_PROFILE = "hybrid_v2_limited_activity_v1"
RUNTIME_BACKEND = "windows_native"
MAX_SAFE_INTEGER = 2**53 - 1
GATE_A_GENERATOR_COMMIT = "921e2e723804ed9ce1771d79c6a3cead9885c8fd"
LIMITED_ACTIVITY_UPSTREAM_SOURCE_SHA256 = (
    "43e0b4de9231710d2956c1446c7afb373b2e4c0b49d57322c4b5d54765c3bfdb"
)
GATE_C_CONFIG_SHA256 = "04b40614ac8274cf7d474dc73eb360ea341ad65fa1c35634f3b8b18d7aa32fd7"
TERRITORY_POLICIES = (
    "auto_equal_feasible",
    "whole_liver",
    "right_lobar",
    "left_lobar",
)


class WindowsV1ConfigError(ValueError):
    """Raised when a public Windows v1 configuration is not authoritative."""


def _reject_unknown(payload: dict[str, Any], allowed: set[str], context: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise WindowsV1ConfigError(f"unknown {context} fields: {', '.join(unknown)}")


def _integer(value: Any, name: str, *, minimum: int = 0, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise WindowsV1ConfigError(f"{name} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        upper = f" and at most {maximum}" if maximum is not None else ""
        raise WindowsV1ConfigError(f"{name} must be at least {minimum}{upper}")
    return value


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise WindowsV1ConfigError(f"{name} must be finite")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise WindowsV1ConfigError(f"{name} must be finite") from exc
    if not math.isfinite(number):
        raise WindowsV1ConfigError(f"{name} must be finite")
    return number


@dataclass(frozen=True)
class CohortConfig:
    mode: str = "positive_only"
    positive_cases: int = 2
    negative_cases: int = 0

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "CohortConfig":
        data = dict(payload or {})
        _reject_unknown(data, {"mode", "positive_cases", "negative_cases"}, "cohort")
        cohort = cls(
            mode=str(data.get("mode", "positive_only")),
            positive_cases=_integer(data.get("positive_cases", 2), "cohort positive_cases"),
            negative_cases=_integer(data.get("negative_cases", 0), "cohort negative_cases"),
        )
        valid = (
            cohort.mode == "positive_only"
            and cohort.positive_cases >= 1
            and cohort.negative_cases == 0
        ) or (
            cohort.mode == "true_negative_only"
            and cohort.positive_cases == 0
            and cohort.negative_cases >= 1
        ) or (
            cohort.mode == "mixed"
            and cohort.positive_cases >= 1
            and cohort.negative_cases >= 1
        )
        if not valid:
            raise WindowsV1ConfigError("cohort mode and positive/negative counts disagree")
        return cohort

    @property
    def total_cases(self) -> int:
        return self.positive_cases + self.negative_cases


@dataclass(frozen=True)
class LesionConfig:
    tumor_count_min: int = 1
    tumor_count_max: int = 5
    size_band_weights: tuple[float, float, float] = (0.45, 0.40, 0.15)
    tnr_min: float = 2.0
    tnr_max: float = 8.0
    territory_policy: str = "auto_equal_feasible"

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "LesionConfig":
        data = dict(payload or {})
        _reject_unknown(
            data,
            {
                "tumor_count_min",
                "tumor_count_max",
                "size_band_weights",
                "normalized_size_band_weights",
                "tnr_min",
                "tnr_max",
                "territory_policy",
            },
            "lesion",
        )
        count_min = _integer(data.get("tumor_count_min", 1), "tumor_count_min", minimum=1, maximum=5)
        count_max = _integer(data.get("tumor_count_max", 5), "tumor_count_max", minimum=1, maximum=5)
        if count_min > count_max:
            raise WindowsV1ConfigError("tumor_count_min cannot exceed tumor_count_max")
        raw_weights = data.get("size_band_weights", (0.45, 0.40, 0.15))
        if not isinstance(raw_weights, (list, tuple)) or len(raw_weights) != 3:
            raise WindowsV1ConfigError("size_band_weights must contain exactly three values")
        weights = tuple(_finite(value, "size_band_weights") for value in raw_weights)
        if any(value < 0 for value in weights) or sum(weights) <= 0:
            raise WindowsV1ConfigError("size_band_weights must be non-negative with a positive sum")
        tnr_min = _finite(data.get("tnr_min", 2.0), "tnr_min")
        tnr_max = _finite(data.get("tnr_max", 8.0), "tnr_max")
        if not 2.0 <= tnr_min <= tnr_max <= 8.0:
            raise WindowsV1ConfigError("TNR range must satisfy 2 <= min <= max <= 8")
        territory = str(data.get("territory_policy", "auto_equal_feasible"))
        if territory not in TERRITORY_POLICIES:
            raise WindowsV1ConfigError("unsupported territory_policy")
        result = cls(count_min, count_max, weights, tnr_min, tnr_max, territory)
        if "normalized_size_band_weights" in data:
            claimed = data["normalized_size_band_weights"]
            if not isinstance(claimed, (list, tuple)) or len(claimed) != 3:
                raise WindowsV1ConfigError("normalized_size_band_weights must contain three values")
            claimed_values = tuple(_finite(value, "normalized_size_band_weights") for value in claimed)
            if any(abs(a - b) > 1e-12 for a, b in zip(claimed_values, result.normalized_size_band_weights)):
                raise WindowsV1ConfigError("normalized_size_band_weights disagree with raw weights")
        return result

    @property
    def normalized_size_band_weights(self) -> tuple[float, float, float]:
        total = sum(self.size_band_weights)
        return tuple(value / total for value in self.size_band_weights)


@dataclass(frozen=True)
class WindowsV1Config:
    schema_version: str = SCHEMA_VERSION
    generation_profile: str = GENERATION_PROFILE
    runtime_backend: str = RUNTIME_BACKEND
    cohort: CohortConfig = field(default_factory=CohortConfig)
    lesions: LesionConfig = field(default_factory=LesionConfig)
    seed: int = 42

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "WindowsV1Config":
        data = dict(payload or {})
        _reject_unknown(
            data,
            {"schema_version", "generation_profile", "runtime_backend", "cohort", "lesions", "seed"},
            "Windows v1",
        )
        schema = str(data.get("schema_version", SCHEMA_VERSION))
        profile = str(data.get("generation_profile", GENERATION_PROFILE))
        backend = str(data.get("runtime_backend", RUNTIME_BACKEND))
        if schema != SCHEMA_VERSION:
            raise WindowsV1ConfigError(f"schema_version must be {SCHEMA_VERSION}")
        if profile != GENERATION_PROFILE:
            raise WindowsV1ConfigError(f"generation_profile must be {GENERATION_PROFILE}")
        if backend != RUNTIME_BACKEND:
            raise WindowsV1ConfigError(f"runtime_backend must be {RUNTIME_BACKEND}")
        return cls(
            schema_version=schema,
            generation_profile=profile,
            runtime_backend=backend,
            cohort=CohortConfig.from_dict(data.get("cohort")),
            lesions=LesionConfig.from_dict(data.get("lesions")),
            seed=_integer(data.get("seed", 42), "seed", maximum=MAX_SAFE_INTEGER),
        )

    @property
    def total_cases(self) -> int:
        return self.cohort.total_cases

    def case_roles(self) -> list[str]:
        return ["positive"] * self.cohort.positive_cases + ["true_negative"] * self.cohort.negative_cases

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generation_profile": self.generation_profile,
            "runtime_backend": self.runtime_backend,
            "cohort": {
                "mode": self.cohort.mode,
                "positive_cases": self.cohort.positive_cases,
                "negative_cases": self.cohort.negative_cases,
            },
            "lesions": {
                "tumor_count_min": self.lesions.tumor_count_min,
                "tumor_count_max": self.lesions.tumor_count_max,
                "size_band_weights": list(self.lesions.size_band_weights),
                "normalized_size_band_weights": list(self.lesions.normalized_size_band_weights),
                "tnr_min": self.lesions.tnr_min,
                "tnr_max": self.lesions.tnr_max,
                "territory_policy": self.lesions.territory_policy,
            },
            "seed": self.seed,
        }

    def to_phantom_config(self):
        from .phantom_generator import PhantomConfig

        return PhantomConfig(
            volume_shape=(128, 128, 128),
            voxel_size_mm=4.42,
            anatomy_model="v2_population",
            activity_model="limited_v1",
            territory_policy=self.lesions.territory_policy,
            tumor_count_min=self.lesions.tumor_count_min,
            tumor_count_max=self.lesions.tumor_count_max,
            tumor_size_bins_mm=[[10.0, 20.0], [20.0, 40.0], [40.0, 60.0]],
            tumor_probs=list(self.lesions.normalized_size_band_weights),
            tumor_contrast_min=self.lesions.tnr_min,
            tumor_contrast_max=self.lesions.tnr_max,
            residual_bg=0.05,
            gradient_gain=0.08,
            total_counts=80_000.0,
            n_cases=self.total_cases,
            global_seed=self.seed,
            use_global_seed=True,
            output_dir="managed_by_pipeline",
        )
