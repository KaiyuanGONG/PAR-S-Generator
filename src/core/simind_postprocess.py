"""Strict, Qt-free completion audit for one SIMIND projection case."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import numpy as np


class SimindCompletionError(RuntimeError):
    pass


@dataclass(frozen=True)
class SimindCompletionAudit:
    case_id: str
    complete: bool
    expected_shape: tuple[int, int, int]
    view_count: int
    expected_bytes: int
    actual_bytes: int
    finite: bool
    nonnegative: bool
    projection_sum: float
    projection_mean: float
    projection_sd: float
    sha256: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "complete": self.complete,
            "expected_shape": list(self.expected_shape),
            "view_count": self.view_count,
            "expected_bytes": self.expected_bytes,
            "actual_bytes": self.actual_bytes,
            "finite": self.finite,
            "nonnegative": self.nonnegative,
            "projection_sum": self.projection_sum,
            "projection_mean": self.projection_mean,
            "projection_sd": self.projection_sd,
            "sha256": dict(self.sha256),
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_mhd(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="ascii", errors="strict").splitlines():
        if "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        normalized = key.strip().lower()
        if normalized in result:
            raise SimindCompletionError(f"MHD contains duplicate field {key.strip()!r}")
        result[normalized] = value.strip()
    return result


def _require_mhd_value(
    header: Mapping[str, str],
    key: str,
    expected: str,
) -> None:
    actual = header.get(key.lower())
    if actual is None or actual.casefold() != expected.casefold():
        raise SimindCompletionError(
            f"MHD {key} must be {expected}, got {actual!r}"
        )


def audit_simind_completion(
    output_stem: Path,
    *,
    expected_shape: tuple[int, int, int],
    exit_code: int,
) -> SimindCompletionAudit:
    """Require a valid, paired ``a00/mhd/res/spe`` quartet and exit code zero."""
    output_stem = Path(output_stem)
    if (
        len(expected_shape) != 3
        or any(not isinstance(value, int) or value <= 0 for value in expected_shape)
    ):
        raise ValueError("expected_shape must contain three positive integers")
    if expected_shape[0] != 60:
        raise ValueError("V2 completion contract requires exactly 60 views")
    if exit_code != 0:
        raise SimindCompletionError(f"SIMIND exit code must be zero, got {exit_code}")

    paths = {
        suffix: output_stem.with_suffix(f".{suffix}")
        for suffix in ("a00", "mhd", "res", "spe")
    }
    missing = [suffix for suffix, path in paths.items() if not path.is_file()]
    if missing:
        raise SimindCompletionError(
            f"missing required outputs for {output_stem.name}: {', '.join(missing)}"
        )
    empty = [suffix for suffix in ("mhd", "res", "spe") if paths[suffix].stat().st_size <= 0]
    if empty:
        raise SimindCompletionError(f"empty required outputs: {', '.join(empty)}")

    expected_bytes = int(np.prod(expected_shape, dtype=np.int64)) * np.dtype("<f4").itemsize
    actual_bytes = paths["a00"].stat().st_size
    if actual_bytes != expected_bytes:
        raise SimindCompletionError(
            f"a00 byte size {actual_bytes} does not match expected {expected_bytes}"
        )

    header = _parse_mhd(paths["mhd"])
    _require_mhd_value(header, "ObjectType", "Image")
    _require_mhd_value(header, "BinaryData", "True")
    _require_mhd_value(header, "BinaryDataByteOrderMSB", "False")
    _require_mhd_value(header, "CompressedData", "False")
    _require_mhd_value(header, "NDims", "3")
    try:
        dims_xyz = tuple(int(item) for item in header["dimsize"].split())
    except (KeyError, ValueError) as exc:
        raise SimindCompletionError("MHD DimSize is missing or invalid") from exc
    expected_xyz = (expected_shape[2], expected_shape[1], expected_shape[0])
    if dims_xyz != expected_xyz:
        raise SimindCompletionError(
            f"MHD DimSize {dims_xyz} does not match expected {expected_xyz}"
        )
    _require_mhd_value(header, "ElementType", "MET_FLOAT")
    element_file = header.get("elementdatafile", "").replace("\\", "/").split("/")[-1]
    if element_file.casefold() != paths["a00"].name.casefold():
        raise SimindCompletionError(
            f"MHD ElementDataFile {element_file!r} does not pair with {paths['a00'].name!r}"
        )

    projections = np.memmap(paths["a00"], dtype="<f4", mode="r", shape=expected_shape)
    finite = bool(np.isfinite(projections).all())
    if not finite:
        raise SimindCompletionError("a00 contains non-finite projection values")
    nonnegative = bool(np.all(projections >= 0.0))
    if not nonnegative:
        raise SimindCompletionError("a00 contains negative projection values")
    projection_sum = float(projections.sum(dtype=np.float64))
    projection_mean = float(projections.mean(dtype=np.float64))
    projection_sd = float(projections.std(dtype=np.float64))
    del projections

    hashes = {suffix: sha256_file(path) for suffix, path in paths.items()}
    return SimindCompletionAudit(
        case_id=output_stem.name,
        complete=True,
        expected_shape=expected_shape,
        view_count=expected_shape[0],
        expected_bytes=expected_bytes,
        actual_bytes=actual_bytes,
        finite=finite,
        nonnegative=nonnegative,
        projection_sum=projection_sum,
        projection_mean=projection_mean,
        projection_sd=projection_sd,
        sha256=hashes,
    )
