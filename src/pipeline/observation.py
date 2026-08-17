"""Explicit, reproducible expectation-to-observation transforms."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from pipeline.contracts import sha256_file
from pipeline.qc import load_projection


def sample_poisson_observation(
    expectation_path: Path,
    output_path: Path,
    *,
    seed: int,
    scale: float = 1.0,
    shape: tuple[int, int, int] = (60, 128, 128),
    protocol_status: str = "toy",
) -> dict:
    """Create a separate integer-valued Poisson realization.

    ``verified`` is reserved for an externally justified acquisition scaling;
    all other invocations are explicitly marked ``toy`` or ``research``.
    """
    if protocol_status not in {"toy", "research", "verified"}:
        raise ValueError("protocol_status must be toy, research, or verified")
    if scale <= 0:
        raise ValueError("scale must be positive")
    expectation = load_projection(expectation_path, shape=shape, canonical=False).astype(np.float64)
    if not np.isfinite(expectation).all() or np.any(expectation < 0):
        raise ValueError("expectation must be finite and non-negative")
    rng = np.random.default_rng(int(seed))
    observation = rng.poisson(expectation * float(scale)).astype(np.float32)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp = output_path.with_name(f".{output_path.name}.tmp")
    observation.tofile(temp)
    temp.replace(output_path)
    return {
        "kind": "offline_poisson_observation",
        "protocol_status": protocol_status,
        "seed": int(seed),
        "scale": float(scale),
        "expectation": str(Path(expectation_path).resolve()),
        "observation": str(output_path.resolve()),
        "sha256": sha256_file(output_path),
        "sum": float(observation.sum(dtype=np.float64)),
        "dtype": str(observation.dtype),
        "shape": list(observation.shape),
    }
