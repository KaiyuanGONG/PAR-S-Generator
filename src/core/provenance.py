"""Small, dependency-free provenance primitives for immutable V2 artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import numpy as np


def json_compatible(value: Any) -> Any:
    """Convert common scientific-Python values without permitting NaN/Infinity."""
    if is_dataclass(value):
        return json_compatible(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return json_compatible(value.item())
    if isinstance(value, np.ndarray):
        return json_compatible(value.tolist())
    if isinstance(value, dict):
        return {str(key): json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_compatible(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("provenance JSON must not contain NaN or Infinity")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported provenance value type: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            json_compatible(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def pretty_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            json_compatible(value),
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write_bytes(path: str | Path, content: bytes) -> Path:
    """Replace one file atomically; callers enforce any immutability policy."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def atomic_write_json(path: str | Path, value: Any, *, pretty: bool = True) -> Path:
    content = pretty_json_bytes(value) if pretty else canonical_json_bytes(value)
    return atomic_write_bytes(path, content)


def safe_relative_path(path: str | Path, root: str | Path) -> str:
    root_path = Path(root).resolve()
    candidate = Path(path).resolve()
    try:
        relative = candidate.relative_to(root_path)
    except ValueError as exc:
        raise ValueError(f"artifact path escapes dataset root: {candidate}") from exc
    return relative.as_posix()


def resolve_relative_path(relative_path: str, root: str | Path) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe dataset-relative path: {relative_path!r}")
    root_path = Path(root).resolve()
    candidate = (root_path / relative).resolve()
    try:
        candidate.relative_to(root_path)
    except ValueError as exc:
        raise ValueError(f"artifact path escapes dataset root: {relative_path!r}") from exc
    return candidate

