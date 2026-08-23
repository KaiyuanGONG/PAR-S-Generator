"""Native Windows runtime identity and filesystem safety contracts."""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath

from pipeline.contracts import sha256_file


VERIFIED_SIMIND_SHA256 = "f984b8753f54b9f671f9fc1bcb2b45461e7cae8d027376b446dd1ed55a9a8319"
VERIFIED_SMC_SHA256 = "4d10eab246a7a6690663230d2f33aeb3c32f67c598af36b56d1575f0e3551d10"
MAX_RESOLVED_PATH_CHARS = 240

_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_FILE_SUFFIXES = {"simind_exe": ".exe", "smc": ".smc"}
_DIRECTORY_KINDS = {"runs_root", "export_root"}


class WindowsPathError(ValueError):
    """Raised when a selected path is unsafe for the Windows v1 workflow."""


@dataclass(frozen=True)
class WindowsRuntimeAssessment:
    status: str
    simind_path: str
    simind_sha256: str | None
    smc_path: str
    smc_sha256: str | None
    mismatches: tuple[str, ...]

    @property
    def validated(self) -> bool:
        return self.status == "validated_windows_v1"

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["mismatches"] = list(self.mismatches)
        return payload


def assess_windows_runtime(simind_exe: str | Path, smc_file: str | Path) -> WindowsRuntimeAssessment:
    exe = Path(simind_exe).resolve()
    smc = Path(smc_file).resolve()
    exe_hash = sha256_file(exe) if exe.is_file() else None
    smc_hash = sha256_file(smc) if smc.is_file() else None
    mismatches: list[str] = []
    if exe_hash != VERIFIED_SIMIND_SHA256:
        mismatches.append("simind_executable")
    if smc_hash != VERIFIED_SMC_SHA256:
        mismatches.append("smc")
    if exe_hash is None or smc_hash is None:
        status = "missing_runtime"
    elif mismatches:
        status = "unverified_runtime"
    else:
        status = "validated_windows_v1"
    return WindowsRuntimeAssessment(
        status=status,
        simind_path=str(exe),
        simind_sha256=exe_hash,
        smc_path=str(smc),
        smc_sha256=smc_hash,
        mismatches=tuple(mismatches),
    )


def _raw_components(path_value: str | Path) -> tuple[str, ...]:
    raw = os.fspath(path_value)
    normalized = raw.replace("/", "\\")
    if normalized.startswith("\\\\"):
        raise WindowsPathError("Windows v1 accepts paths on a local Windows drive only; UNC paths are rejected")
    parts = PureWindowsPath(normalized).parts
    for component in parts:
        if component in {"\\", "/"} or re.fullmatch(r"[A-Za-z]:\\?", component):
            continue
        if component.endswith((".", " ")):
            raise WindowsPathError(f"Windows path component has a trailing dot or space: {component!r}")
        stem = component.split(".", 1)[0].upper()
        if stem in _RESERVED_NAMES:
            raise WindowsPathError(f"Windows path uses a reserved Windows name: {component!r}")
    return parts


def _nearest_existing_parent(path: Path) -> Path | None:
    candidate = path
    while not candidate.exists() and candidate.parent != candidate:
        candidate = candidate.parent
    return candidate if candidate.exists() else None


def validate_windows_path(
    path_value: str | Path,
    kind: str,
    *,
    base: str | Path | None = None,
    require_exists: bool = True,
) -> Path:
    """Resolve and validate one native Windows v1 user-selected path."""
    if kind not in {*_FILE_SUFFIXES, *_DIRECTORY_KINDS}:
        raise WindowsPathError(f"Unsupported Windows path kind: {kind}")
    _raw_components(path_value)
    requested = Path(path_value)
    if not requested.is_absolute():
        requested = Path(base).resolve() / requested if base is not None else requested.absolute()
    resolved = requested.resolve()
    if str(resolved).replace("/", "\\").startswith("\\\\") or not resolved.drive:
        raise WindowsPathError("Windows v1 accepts paths on a local Windows drive only")
    if len(str(resolved)) > MAX_RESOLVED_PATH_CHARS:
        raise WindowsPathError(
            f"Resolved Windows path exceeds {MAX_RESOLVED_PATH_CHARS} characters: {resolved}"
        )

    expected_suffix = _FILE_SUFFIXES.get(kind)
    if expected_suffix is not None and resolved.suffix.lower() != expected_suffix:
        raise WindowsPathError(f"{kind} requires a {expected_suffix} file: {resolved}")
    if not require_exists:
        return resolved

    if expected_suffix is not None:
        if not resolved.is_file():
            raise WindowsPathError(f"Existing readable file required: {resolved}")
        try:
            with resolved.open("rb") as handle:
                handle.read(1)
        except OSError as exc:
            raise WindowsPathError(f"Selected file is not readable: {resolved}: {exc}") from exc
        return resolved

    if resolved.exists() and not resolved.is_dir():
        raise WindowsPathError(f"Directory required: {resolved}")
    ancestor = _nearest_existing_parent(resolved)
    if ancestor is None or not ancestor.is_dir():
        raise WindowsPathError(f"Existing or creatable directory required: {resolved}")
    if not os.access(ancestor, os.W_OK):
        raise WindowsPathError(f"Directory is not writable: {ancestor}")
    return resolved
