"""Fail-closed runtime and preflight-input bindings for formal V2 generation."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from .provenance import atomic_write_json, sha256_file


PYTHON_RUNTIME_SCHEMA = "pars_v2_python_conda_runtime_v1"
GENERATOR_SOURCE_SCHEMA = "pars_v2_generator_source_binding_v1"
PREFLIGHT_INPUT_BUNDLE_SCHEMA = "pars_v2_preflight_input_bundle_v1"
PREFLIGHT_BYTE_IDENTITY_SCHEMA = "pars_v2_preflight_byte_identity_v1"

_DETERMINISM_ENVIRONMENT_KEYS = (
    "CONDA_DEFAULT_ENV",
    "CONDA_PREFIX",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "PYTHONHASHSEED",
)
_CRITICAL_MODULES = ("numpy", "scipy", "skimage")
_SOURCE_PATHSPECS = (
    "src/core",
    "scripts/generate_dataset_v2.py",
    "scripts/preflight_pilot15_v2.py",
    "scripts/preflight_task12d_v2.py",
    "scripts/run_pilot15_v2.py",
    "scripts/run_task12d_v2.py",
    "scripts/finalize_task12d_v2.py",
    "scripts/build_task12e_linux_bundle.py",
    "scripts/capture_task12e_linux_environment.py",
    "scripts/run_task12e_linux_smoke.py",
    "scripts/finalize_task12e_linux_local.py",
    "scripts/finalize_task12e_linux_master.py",
    "scripts/prepare_task12e_linux_environment.sh",
    "scripts/run_task12e_linux_smoke.sh",
    "scripts/launch_task12e_linux_smoke_screen.sh",
    "scripts/launch_task12e_linux_screen.sh",
    "scripts/run_task12e_linux_node.sh",
    "scripts/run_task12e_linux_worker.py",
    "scripts/task12e_linux_common.py",
    "configs/task12e_linux_environment.yml",
    "configs/task12e_linux_homologation_v3.json",
)


def canonical_json_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def array_manifest(
    arrays: Mapping[str, np.ndarray],
) -> dict[str, dict[str, object]]:
    """Hash array semantics and C-order bytes, not an incidental container file."""

    manifest: dict[str, dict[str, object]] = {}
    for name, value in sorted(arrays.items()):
        contiguous = np.ascontiguousarray(value)
        digest = hashlib.sha256()
        digest.update(str(contiguous.dtype).encode("ascii"))
        digest.update(json.dumps(list(contiguous.shape)).encode("ascii"))
        digest.update(contiguous.tobytes(order="C"))
        manifest[str(name)] = {
            "dtype": str(contiguous.dtype),
            "shape": list(contiguous.shape),
            "sha256": digest.hexdigest(),
        }
    return manifest


def _normalise_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def _distribution_snapshot() -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        if not name:
            continue
        records.append(
            {
                "name": _normalise_distribution_name(str(name)),
                "version": str(distribution.version),
            }
        )
    return sorted(records, key=lambda item: (item["name"], item["version"]))


def _conda_snapshot(
    prefix: Path | None,
    *,
    raw_prefix: str | None,
    python_prefix: Path,
) -> dict[str, object]:
    if prefix is None:
        return {
            "detected": False,
            "raw_prefix": None,
            "resolved_prefix": None,
            "prefix_matches_python_prefix": False,
            "records": [],
            "records_sha256": None,
        }
    metadata_root = prefix / "conda-meta"
    if not metadata_root.is_dir():
        return {
            "detected": False,
            "raw_prefix": raw_prefix,
            "resolved_prefix": str(prefix),
            "prefix_matches_python_prefix": prefix == python_prefix,
            "records": [],
            "records_sha256": None,
        }
    records: list[dict[str, object]] = []
    for path in sorted(metadata_root.glob("*.json"), key=lambda value: value.name.casefold()):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"cannot capture Conda record {path}: {exc}") from exc
        records.append(
            {
                "name": str(document.get("name", "")),
                "version": str(document.get("version", "")),
                "build": str(document.get("build", "")),
                "build_number": int(document.get("build_number", 0)),
                "subdir": str(document.get("subdir", "")),
            }
        )
    history = metadata_root / "history"
    return {
        "detected": True,
        "raw_prefix": raw_prefix,
        "resolved_prefix": str(prefix),
        "prefix_matches_python_prefix": prefix == python_prefix,
        "records": records,
        "records_sha256": canonical_json_sha256(records),
        "history_sha256": sha256_file(history) if history.is_file() else None,
    }


def _critical_module_snapshot() -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for name in _CRITICAL_MODULES:
        module = importlib.import_module(name)
        module_file = Path(str(module.__file__)).resolve()
        records.append(
            {
                "name": name,
                "version": str(getattr(module, "__version__", "unknown")),
                "module_file": str(module_file),
                "module_file_sha256": (
                    sha256_file(module_file) if module_file.is_file() else None
                ),
            }
        )
    return records


def capture_python_runtime() -> dict[str, object]:
    """Capture a stable, path-aware Python/Conda runtime fingerprint."""

    executable = Path(sys.executable).resolve()
    python_prefix = Path(sys.prefix).resolve()
    conda_prefix_value = os.environ.get("CONDA_PREFIX")
    conda_prefix = Path(conda_prefix_value).resolve() if conda_prefix_value else None
    distributions = _distribution_snapshot()
    document: dict[str, object] = {
        "schema_version": PYTHON_RUNTIME_SCHEMA,
        "python": {
            "raw_executable": sys.executable,
            "executable": str(executable),
            "executable_sha256": sha256_file(executable),
            "version": platform.python_version(),
            "version_info": list(sys.version_info[:5]),
            "implementation": platform.python_implementation(),
            "cache_tag": sys.implementation.cache_tag,
            "raw_prefix": sys.prefix,
            "prefix": str(python_prefix),
            "raw_base_prefix": sys.base_prefix,
            "base_prefix": str(Path(sys.base_prefix).resolve()),
            "raw_exec_prefix": sys.exec_prefix,
            "exec_prefix": str(Path(sys.exec_prefix).resolve()),
        },
        "platform": {
            "os_name": os.name,
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
        },
        "environment": {
            key: os.environ.get(key) for key in _DETERMINISM_ENVIRONMENT_KEYS
        },
        "critical_modules": _critical_module_snapshot(),
        "python_distributions": distributions,
        "python_distributions_sha256": canonical_json_sha256(distributions),
        "conda": _conda_snapshot(
            conda_prefix,
            raw_prefix=conda_prefix_value,
            python_prefix=python_prefix,
        ),
    }
    document["binding_sha256"] = canonical_json_sha256(document)
    return document


def _git(repo_root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def capture_generator_source_binding(repo_root: Path) -> dict[str, object]:
    """Hash the committed generation pipeline in addition to its Git identity."""

    root = Path(repo_root).resolve()
    commit = _git(root, "rev-parse", "HEAD").strip()
    tree = _git(root, "rev-parse", "HEAD^{tree}").strip()
    status = _git(root, "status", "--porcelain", "--untracked-files=all").rstrip()
    tracked = _git(root, "ls-files", "--", *_SOURCE_PATHSPECS).splitlines()
    files = []
    for relative_value in sorted(set(tracked)):
        relative = Path(relative_value)
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise RuntimeError(f"tracked source escapes repository: {relative}") from exc
        if not path.is_file():
            raise FileNotFoundError(f"tracked generation source is missing: {path}")
        files.append(
            {
                "path": relative.as_posix(),
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
    if not files:
        raise RuntimeError("no tracked generation source files were captured")
    document: dict[str, object] = {
        "schema_version": GENERATOR_SOURCE_SCHEMA,
        "repository_root": str(root),
        "git_commit": commit,
        "git_tree": tree,
        "worktree_clean": not bool(status),
        "dirty_status": status,
        "source_files": files,
        "source_manifest_sha256": canonical_json_sha256(files),
    }
    document["binding_sha256"] = canonical_json_sha256(document)
    return document


@dataclass(frozen=True)
class FrozenPreflightInputV2:
    case_id: str
    source_path: Path
    density_path: Path
    source_sha256: str
    density_sha256: str
    source_size_bytes: int
    density_size_bytes: int
    array_manifest: Mapping[str, Mapping[str, object]]
    array_manifest_sha256: str
    bundle_manifest_path: Path
    bundle_manifest_sha256: str
    bundle_content_sha256: str


def write_preflight_input_bundle(
    work_root: Path,
    case_summaries: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Freeze every preflight source/density byte stream in one manifest."""

    root = Path(work_root).resolve()
    cases: list[dict[str, object]] = []
    for summary in case_summaries:
        case_id = str(summary["case_id"])
        case_root = root / "cases" / case_id
        source = case_root / f"{case_id}_act_av.bin"
        density = case_root / f"{case_id}_atn_av.bin"
        for label, path in (("source", source), ("density", density)):
            if not path.is_file():
                raise FileNotFoundError(f"{case_id}: preflight {label} is missing: {path}")
        source_sha = sha256_file(source)
        density_sha = sha256_file(density)
        if source_sha != summary.get("source_sha256"):
            raise RuntimeError(f"{case_id}: preflight source changed before bundle freeze")
        if density_sha != summary.get("density_sha256"):
            raise RuntimeError(f"{case_id}: preflight density changed before bundle freeze")
        arrays = summary.get("array_manifest")
        if not isinstance(arrays, Mapping) or not arrays:
            raise RuntimeError(f"{case_id}: preflight array manifest is missing")
        arrays_sha = canonical_json_sha256(arrays)
        cases.append(
            {
                "case_id": case_id,
                "source_relative_path": source.relative_to(root).as_posix(),
                "density_relative_path": density.relative_to(root).as_posix(),
                "source_sha256": source_sha,
                "density_sha256": density_sha,
                "source_size_bytes": source.stat().st_size,
                "density_size_bytes": density.stat().st_size,
                "array_manifest": dict(arrays),
                "array_manifest_sha256": arrays_sha,
            }
        )
    content_sha = canonical_json_sha256(cases)
    document = {
        "schema_version": PREFLIGHT_INPUT_BUNDLE_SCHEMA,
        "case_count": len(cases),
        "case_ids": [item["case_id"] for item in cases],
        "content_sha256": content_sha,
        "cases": cases,
    }
    manifest = root / "INPUT_BUNDLE.json"
    atomic_write_json(manifest, document)
    return {
        "manifest_relative_path": manifest.relative_to(root).as_posix(),
        "manifest_sha256": sha256_file(manifest),
        "content_sha256": content_sha,
        "case_count": len(cases),
    }


def _bound_relative_path(root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"{label} must be a non-empty relative path")
    relative = Path(value)
    if relative.is_absolute():
        raise RuntimeError(f"{label} must be relative")
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"{label} escapes the preflight root") from exc
    return path


def load_and_validate_preflight_input_bundle(
    preflight_report_path: Path,
    reference: Mapping[str, object],
    *,
    expected_case_ids: Sequence[str],
    case_summaries: Sequence[Mapping[str, object]],
) -> dict[str, FrozenPreflightInputV2]:
    """Load a preflight bundle and verify its manifest and every input byte."""

    report_path = Path(preflight_report_path).resolve()
    root = report_path.parent
    manifest = _bound_relative_path(
        root,
        reference.get("manifest_relative_path"),
        "input bundle manifest",
    )
    if not manifest.is_file():
        raise FileNotFoundError(f"preflight input bundle is missing: {manifest}")
    manifest_sha = sha256_file(manifest)
    if manifest_sha != reference.get("manifest_sha256"):
        raise RuntimeError("preflight input bundle manifest hash mismatch")
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read preflight input bundle: {exc}") from exc
    if not isinstance(document, Mapping):
        raise RuntimeError("preflight input bundle must be an object")
    if document.get("schema_version") != PREFLIGHT_INPUT_BUNDLE_SCHEMA:
        raise RuntimeError("preflight input bundle schema mismatch")
    raw_cases = document.get("cases")
    if not isinstance(raw_cases, list):
        raise RuntimeError("preflight input bundle cases must be a list")
    case_ids = [
        str(item.get("case_id")) for item in raw_cases if isinstance(item, Mapping)
    ]
    if case_ids != list(expected_case_ids) or len(case_ids) != len(raw_cases):
        raise RuntimeError("preflight input bundle case IDs/order mismatch")
    content_sha = canonical_json_sha256(raw_cases)
    if content_sha != document.get("content_sha256") or content_sha != reference.get(
        "content_sha256"
    ):
        raise RuntimeError("preflight input bundle content hash mismatch")
    if int(document.get("case_count", -1)) != len(case_ids) or int(
        reference.get("case_count", -1)
    ) != len(case_ids):
        raise RuntimeError("preflight input bundle case count mismatch")
    summaries = {str(item.get("case_id")): item for item in case_summaries}
    if set(summaries) != set(case_ids):
        raise RuntimeError("preflight report cases disagree with input bundle")

    bound: dict[str, FrozenPreflightInputV2] = {}
    for item in raw_cases:
        if not isinstance(item, Mapping):
            raise RuntimeError("preflight input bundle case must be an object")
        case_id = str(item["case_id"])
        source = _bound_relative_path(
            root, item.get("source_relative_path"), f"{case_id} source"
        )
        density = _bound_relative_path(
            root, item.get("density_relative_path"), f"{case_id} density"
        )
        for label, path, digest_key, size_key in (
            ("source", source, "source_sha256", "source_size_bytes"),
            ("density", density, "density_sha256", "density_size_bytes"),
        ):
            if not path.is_file():
                raise FileNotFoundError(f"{case_id}: frozen preflight {label} missing")
            if path.stat().st_size != int(item.get(size_key, -1)):
                raise RuntimeError(f"{case_id}: frozen preflight {label} size mismatch")
            if sha256_file(path) != item.get(digest_key):
                raise RuntimeError(f"{case_id}: frozen preflight {label} hash mismatch")
        summary = summaries[case_id]
        if summary.get("source_sha256") != item.get("source_sha256"):
            raise RuntimeError(f"{case_id}: report/source bundle hash mismatch")
        if summary.get("density_sha256") != item.get("density_sha256"):
            raise RuntimeError(f"{case_id}: report/density bundle hash mismatch")
        arrays = item.get("array_manifest")
        if not isinstance(arrays, Mapping) or not arrays:
            raise RuntimeError(f"{case_id}: frozen preflight array manifest missing")
        arrays_sha = canonical_json_sha256(arrays)
        if arrays_sha != item.get("array_manifest_sha256"):
            raise RuntimeError(f"{case_id}: frozen preflight array manifest hash mismatch")
        if summary.get("array_manifest") != arrays:
            raise RuntimeError(f"{case_id}: report/array bundle manifest mismatch")
        bound[case_id] = FrozenPreflightInputV2(
            case_id=case_id,
            source_path=source,
            density_path=density,
            source_sha256=str(item["source_sha256"]),
            density_sha256=str(item["density_sha256"]),
            source_size_bytes=int(item["source_size_bytes"]),
            density_size_bytes=int(item["density_size_bytes"]),
            array_manifest=dict(arrays),
            array_manifest_sha256=arrays_sha,
            bundle_manifest_path=manifest,
            bundle_manifest_sha256=manifest_sha,
            bundle_content_sha256=content_sha,
        )
    return bound


def prove_preflight_byte_identity(
    *,
    generated_source: Path,
    generated_density: Path,
    frozen: FrozenPreflightInputV2,
    generated_arrays: Mapping[str, np.ndarray] | None = None,
    evidence_path: Path | None = None,
) -> dict[str, object]:
    """Require regenerated bytes to equal the preflight bundle before SIMIND."""

    generated_source = Path(generated_source)
    generated_density = Path(generated_density)
    observed = {
        "source_sha256": sha256_file(generated_source),
        "density_sha256": sha256_file(generated_density),
        "source_size_bytes": generated_source.stat().st_size,
        "density_size_bytes": generated_density.stat().st_size,
    }
    expected = {
        "source_sha256": frozen.source_sha256,
        "density_sha256": frozen.density_sha256,
        "source_size_bytes": frozen.source_size_bytes,
        "density_size_bytes": frozen.density_size_bytes,
    }
    drifted = [name for name, value in expected.items() if observed[name] != value]
    observed_arrays = (
        array_manifest(generated_arrays) if generated_arrays is not None else None
    )
    array_drifted: list[str] = []
    if observed_arrays is not None:
        array_names = sorted(set(observed_arrays) | set(frozen.array_manifest))
        array_drifted = [
            name
            for name in array_names
            if observed_arrays.get(name) != frozen.array_manifest.get(name)
        ]
    else:
        array_drifted = ["generated_arrays_not_supplied"]
    drifted.extend(f"array:{name}" for name in array_drifted)
    document: dict[str, object] = {
        "schema_version": PREFLIGHT_BYTE_IDENTITY_SCHEMA,
        "status": "pass" if not drifted else "fail",
        "case_id": frozen.case_id,
        "generated": observed,
        "frozen_preflight": expected,
        "generated_array_manifest": observed_arrays,
        "frozen_array_manifest": dict(frozen.array_manifest),
        "frozen_array_manifest_sha256": frozen.array_manifest_sha256,
        "all_arrays_byte_identical": not array_drifted,
        "consumed_source_path": str(frozen.source_path),
        "consumed_density_path": str(frozen.density_path),
        "input_bundle_manifest_sha256": frozen.bundle_manifest_sha256,
        "input_bundle_content_sha256": frozen.bundle_content_sha256,
        "drifted": drifted,
    }
    if evidence_path is not None:
        atomic_write_json(Path(evidence_path), document)
    if drifted:
        raise RuntimeError(
            f"{frozen.case_id}: preflight-to-run input byte identity failed: {drifted}"
        )
    return document
