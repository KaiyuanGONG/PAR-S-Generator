"""Explicit, reproducible expectation-to-observation transforms."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from pipeline.contracts import sha256_file
from pipeline.qc import load_projection


def assign_empirical_count_targets(
    case_ids: list[str],
    reference_counts: tuple[int, ...] | list[int],
    *,
    seed: int,
) -> dict[str, int]:
    """Assign deterministic stratified empirical-ECDF targets.

    Quantile stratification covers the observed range even for a small pilot;
    a seeded permutation prevents count level from tracking case identifier.
    """
    ids = sorted(dict.fromkeys(str(value) for value in case_ids))
    reference = np.asarray(reference_counts, dtype=np.float64)
    if not ids:
        return {}
    if reference.ndim != 1 or reference.size < 2:
        raise ValueError("reference_counts must contain at least two values")
    if not np.isfinite(reference).all() or np.any(reference <= 0):
        raise ValueError("reference_counts must be finite and positive")
    reference.sort()
    quantiles = (np.arange(len(ids), dtype=np.float64) + 0.5) / len(ids)
    try:
        targets = np.quantile(reference, quantiles, method="linear")
    except TypeError:  # NumPy < 1.22 compatibility
        targets = np.quantile(reference, quantiles, interpolation="linear")
    targets = np.rint(targets).astype(np.int64)
    order = np.random.default_rng(int(seed)).permutation(len(ids))
    return {case_id: int(targets[target_index]) for case_id, target_index in zip(ids, order)}


def sample_poisson_observation(
    expectation_path: Path,
    output_path: Path,
    *,
    seed: int,
    scale: float = 1.0,
    target_total_counts: int | None = None,
    shape: tuple[int, int, int] = (60, 128, 128),
    protocol_status: str = "toy",
) -> dict:
    """Create a separate integer-valued Poisson realization.

    ``empirical_protocol_matching`` denotes distribution matching without an
    activity, administered-dose, sensitivity, or absolute cps/MBq claim.
    """
    if protocol_status not in {
        "toy",
        "research",
        "verified",
        "empirical_protocol_matching",
    }:
        raise ValueError(
            "protocol_status must be toy, research, verified, or empirical_protocol_matching"
        )
    expectation = load_projection(expectation_path, shape=shape, canonical=False).astype(np.float64)
    if not np.isfinite(expectation).all() or np.any(expectation < 0):
        raise ValueError("expectation must be finite and non-negative")
    expectation_sum = float(expectation.sum(dtype=np.float64))
    if expectation_sum <= 0:
        raise ValueError("expectation sum must be positive")
    if target_total_counts is not None:
        if int(target_total_counts) <= 0:
            raise ValueError("target_total_counts must be positive")
        effective_scale = float(int(target_total_counts) / expectation_sum)
    else:
        if scale <= 0:
            raise ValueError("scale must be positive")
        effective_scale = float(scale)
    rng = np.random.default_rng(int(seed))
    observation = rng.poisson(expectation * effective_scale).astype(np.float32)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp = output_path.with_name(f".{output_path.name}.tmp")
    observation.tofile(temp)
    temp.replace(output_path)
    view_sums = observation.sum(axis=(1, 2), dtype=np.float64)
    observed_sum = float(view_sums.sum(dtype=np.float64))
    target = int(target_total_counts) if target_total_counts is not None else None
    return {
        "kind": "offline_poisson_observation",
        "protocol_status": protocol_status,
        "claim_boundary": (
            "empirical distribution matching; not activity, administered-dose, "
            "scanner-sensitivity, or absolute cps/MBq calibration"
            if protocol_status == "empirical_protocol_matching"
            else None
        ),
        "seed": int(seed),
        "scale": effective_scale,
        "scale_policy": (
            "target_total_counts_divided_by_expectation_sum"
            if target is not None
            else "fixed_scale"
        ),
        "target_total_counts": target,
        "expectation_sum_before_scale": expectation_sum,
        "expectation_sum_after_scale": expectation_sum * effective_scale,
        "expectation": str(Path(expectation_path).resolve()),
        "observation": str(output_path.resolve()),
        "sha256": sha256_file(output_path),
        "sum": observed_sum,
        "target_relative_error": (
            abs(observed_sum - target) / target if target is not None else None
        ),
        "angular_cv": (
            float(view_sums.std(ddof=0) / view_sums.mean())
            if float(view_sums.mean()) > 0
            else None
        ),
        "dtype": str(observation.dtype),
        "shape": list(observation.shape),
    }
