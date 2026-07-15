"""Dependency-light contracts for the Task 12F Linux 50-case pilot."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tarfile
import uuid
from pathlib import Path
from typing import Any, Mapping


BUNDLE_SCHEMA = "pars_v2_task12f_linux50_bundle_v2"
PLAN_SCHEMA = "pars_v2_task12f_linux50_plan_v2"
NODE_COMPLETE_SCHEMA = "pars_v2_task12f_linux50_node_complete_v2"
MASTER_SCHEMA = "pars_v2_task12f_linux50_master_v2"
CASE_SCHEMA = "pars_v2_task12f_linux50_case_v2"
REMOTE_PREFLIGHT_SCHEMA = "pars_v2_task12f_linux50_remote_preflight_v2"
EXPECTED_PROJECTION_SHAPE = (60, 128, 128)
EXPECTED_A00_BYTES = 60 * 128 * 128 * 4
QUARTET_EXTENSIONS = ("a00", "mhd", "res", "spe")
_DYNAMIC_RES_PREFIXES = (
    "Simulation started.",
    "Simulation stopped.",
    "Elapsed time.......",
    "DetectorHits/CPUsec:",
)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def directory_manifest(root: str | Path) -> tuple[list[dict[str, object]], str]:
    directory = Path(root).resolve()
    if not directory.is_dir():
        raise FileNotFoundError(directory)
    records = []
    for path in sorted(directory.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise ValueError(f"runtime links are forbidden: {path}")
        if path.is_file():
            records.append(
                {
                    "relative_path": path.relative_to(directory).as_posix(),
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    if not records:
        raise ValueError("runtime directory is empty")
    payload = (
        json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")
    return records, hashlib.sha256(payload).hexdigest()


def normalized_res_sha256(path: str | Path) -> str:
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    retained = [
        line.rstrip()
        for line in lines
        if not line.lstrip().startswith(_DYNAMIC_RES_PREFIXES)
    ]
    payload = ("\n".join(retained) + "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def read_json(path: str | Path) -> Mapping[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def atomic_write_json(path: str | Path, value: Mapping[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    payload = (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def validate_case_id(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]*", value
    ):
        raise ValueError(f"unsafe case_id: {value!r}")
    return value


def validate_bundle(bundle_root: str | Path) -> Mapping[str, Any]:
    root = Path(bundle_root).resolve()
    manifest = read_json(root / "BUNDLE_MANIFEST.json")
    if manifest.get("schema_version") != BUNDLE_SCHEMA:
        raise ValueError(f"bundle schema must be {BUNDLE_SCHEMA}")
    if manifest.get("status") != "complete":
        raise ValueError("bundle status must be complete")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("bundle files must be a non-empty list")
    seen: set[str] = set()
    for record in files:
        if not isinstance(record, Mapping):
            raise ValueError("bundle file record must be an object")
        relative = record.get("relative_path")
        if not isinstance(relative, str) or not relative or relative in seen:
            raise ValueError("bundle paths must be unique non-empty strings")
        seen.add(relative)
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"bundle path escapes root: {relative}") from exc
        if not path.is_file():
            raise FileNotFoundError(f"bundle file missing: {relative}")
        if path.stat().st_size != int(record.get("size_bytes", -1)):
            raise ValueError(f"bundle size mismatch: {relative}")
        if sha256_file(path) != record.get("sha256"):
            raise ValueError(f"bundle hash mismatch: {relative}")
    plan_path = root / str(manifest.get("plan_relative_path", ""))
    plan = read_json(plan_path)
    if plan.get("schema_version") != PLAN_SCHEMA:
        raise ValueError(f"plan schema must be {PLAN_SCHEMA}")
    if sha256_file(plan_path) != manifest.get("plan_sha256"):
        raise ValueError("bound plan hash mismatch")
    return manifest


def load_plan(bundle_root: str | Path) -> Mapping[str, Any]:
    manifest = validate_bundle(bundle_root)
    return read_json(Path(bundle_root) / str(manifest["plan_relative_path"]))


def cases_for_node(
    plan: Mapping[str, Any], node_id: str
) -> tuple[Mapping[str, Any], ...]:
    cases = plan.get("cases")
    if not isinstance(cases, list):
        raise ValueError("plan cases must be a list")
    selected = [
        case
        for case in cases
        if isinstance(case, Mapping) and case.get("node_id") == node_id
    ]
    if not selected:
        raise ValueError(f"node {node_id} has no assigned cases")
    return tuple(selected)


def validate_node(plan: Mapping[str, Any], node_id: str, hostname: str) -> None:
    nodes = plan.get("expected_nodes")
    prefixes = plan.get("hostname_prefix_by_node")
    if not isinstance(nodes, list) or node_id not in nodes:
        raise ValueError(f"unexpected node: {node_id}")
    if not isinstance(prefixes, Mapping):
        raise ValueError("hostname prefixes are missing")
    prefix = prefixes.get(node_id)
    if not isinstance(prefix, str) or not hostname.startswith(prefix):
        raise ValueError(
            f"node {node_id} cannot run on {hostname}; expected prefix {prefix}"
        )


def safe_extract_tar(archive: str | Path, destination: str | Path) -> None:
    target = Path(destination).resolve()
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as stream:
        for member in stream.getmembers():
            resolved = (target / member.name).resolve()
            try:
                resolved.relative_to(target)
            except ValueError as exc:
                raise ValueError(f"unsafe archive member: {member.name}") from exc
            if member.issym() or member.islnk():
                raise ValueError(f"archive links are forbidden: {member.name}")
        stream.extractall(target)
