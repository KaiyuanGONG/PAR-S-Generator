"""Minimal Linux SIMIND bridge for the frozen hybrid Gate B pilot.

The module intentionally separates source packaging, subprocess invocation,
and analysis.  It has no Task9 token/authority/self-hash state machine.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import os
import platform
import re
import stat
import subprocess
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np


class GateBError(RuntimeError):
    """Fail-closed Gate B bridge error."""


PACKAGE_SCHEMA = "pars_gate_b_source_package_v1"
JOB_SCHEMA = "pars_gate_b_job_v1"
RUN_SCHEMA = "pars_gate_b_run_v1"
ANALYSIS_SCHEMA = "pars_gate_b_analysis_v1"
RETURN_SCHEMA = "pars_gate_b_return_manifest_v1"
ANALYSIS_FLOAT_DECIMALS = 7
PACKAGE_ROOT_NAME = "pars_gate_b_hybrid_921e2e7"
SAFE_CASE = re.compile(r"case_\d{4}")
SAFE_LABEL = re.compile(r"[A-Za-z0-9_-]+")
SHA_RE = re.compile(r"[0-9a-f]{64}")
RES_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][-+]?\d+)?"
COMPONENT_FILES = {
    "total": "mc_tot_w1.a00",
    "scatter": "mc_sca_w1.a00",
    "primary": "mc_pri_w1.a00",
    "air": "mc_air_w1.a00",
}
REQUIRED_OUTPUTS = (*COMPONENT_FILES.values(), "mc.res", "mc.bis", "mc.ict", "mc.hct")
OPTIONAL_HEADER_RE = re.compile(r"mc_(?:tot|sca|pri|air)_w1\.(?:mhd|h00)")
REFERENCE_SMC_SHA256 = "91758622ff7b2bba8fc57336b47040ffd90617c4fe0bdf6b2a4a7170bb8ef3ea"
REFERENCE_WINDOW_SHA256 = "010ebc4beeafdd4857a97521f100d38f651f188c1472d8c3dfc6a593a3f5e112"
IMMUTABLE_SMC_NAMES = (
    "simind.ini",
    "collim.col",
    "h2o.cr4",
    "al.cr4",
    "czt.cr4",
    "pmt.cr4",
    "pb_sb2.cr4",
    "w.cr4",
    "errors.txt",
    "ctunits.ini",
    "intfile.key",
)


@dataclass(frozen=True)
class VerifiedRuntime:
    root: Path
    binary: Path
    smc_source: Path
    binary_sha256: str
    records: tuple[dict[str, Any], ...]


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for candidate in (here.parents[2], here.parents[1]):
        if (candidate / "configs" / "gate_b_hybrid_921e2e7.json").is_file():
            return candidate
    raise GateBError("cannot locate Gate B package/repository root")


def json_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise GateBError(f"non-finite JSON value: {exc}") from exc


def canonicalize_analysis_numeric(value: Any) -> Any:
    """Normalize report-only floats after all unrounded gate decisions."""
    if isinstance(value, (float, np.floating)):
        rounded = round(float(value), ANALYSIS_FLOAT_DECIMALS)
        return 0.0 if rounded == 0.0 else rounded
    if isinstance(value, dict):
        return {key: canonicalize_analysis_numeric(item) for key, item in value.items()}
    if isinstance(value, list):
        return [canonicalize_analysis_numeric(item) for item in value]
    if isinstance(value, tuple):
        return tuple(canonicalize_analysis_numeric(item) for item in value)
    return value


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise GateBError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def descriptor(path: Path) -> dict[str, Any]:
    info = strict_regular(path, label="descriptor input")
    return {"bytes": info.st_size, "sha256": sha256_file(path)}


def strict_regular(path: Path, *, label: str) -> os.stat_result:
    path = Path(path)
    try:
        info = path.lstat()
    except OSError as exc:
        raise GateBError(f"cannot stat {label} {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise GateBError(f"{label} must be a regular non-symlink file: {path}")
    return info


def read_json(path: Path, *, label: str = "JSON") -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GateBError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise GateBError(f"{label} must be a JSON object: {path}")
    return value


def write_new(path: Path, raw: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise GateBError(f"cannot create {path}: {exc}") from exc


def replace_file(path: Path, raw: bytes) -> None:
    path = Path(path)
    staging = path.with_name(path.name + ".tmp")
    if staging.exists():
        raise GateBError(f"stale replacement path exists: {staging}")
    write_new(staging, raw)
    os.replace(staging, path)


def publish_or_verify(path: Path, raw: bytes) -> None:
    if path.exists():
        if strict_regular(path, label="existing derived artifact").st_size != len(raw):
            raise GateBError(f"existing derived artifact size differs: {path}")
        if sha256_file(path) != sha256_bytes(raw):
            raise GateBError(f"existing derived artifact bytes differ: {path}")
    else:
        write_new(path, raw)


def load_contract(root: Path | None = None) -> dict[str, Any]:
    base = Path(root).resolve() if root is not None else repo_root()
    config = read_json(base / "configs" / "gate_b_hybrid_921e2e7.json", label="Gate B config")
    physics = config.get("physics", {})
    exact = {
        "NN": 10,
        "index_25_activity_time": 1704,
        "index_100_detector_i": 160,
        "index_101_detector_j": 208,
        "projection_shape": [60, 128, 128],
        "start_angle_degrees": 180.0,
        "angle_step_degrees": 6.0,
        "consumer_transform": "raw[:,::-1,:]",
        "window_stem": "n2_photopeak",
        "scattwin_calculation_mode": 2,
        "basic_change_84": 1,
        "entry_21_density_threshold_times_1000": 100,
        "entry_22_aligned_mu_readback_mode": 3,
    }
    for key, value in exact.items():
        if physics.get(key) != value:
            raise GateBError(f"Gate B frozen physics differs at {key}")
    observation = config.get("observation", {})
    if observation.get("expectation_role") != "total_only" or observation.get("policy") != "empirical_total_counts":
        raise GateBError("Gate B observation/expectation contract differs")
    runtime = config.get("runtime", {})
    if runtime.get("child_smc_dir") != "smc_dir/" or runtime.get("simind_elf_sha256") != "e143e2e0b0315c9cd8b6bb187d6bd28448e096c255f8d16ee0c14787d1537f9d":
        raise GateBError("Gate B Linux runtime identity differs")
    return config


def load_runtime_lock(root: Path | None = None) -> dict[str, Any]:
    base = Path(root).resolve() if root is not None else repo_root()
    lock = read_json(base / "configs" / "gate_b_linux_runtime_v8.json", label="runtime lock")
    if lock.get("schema_version") != "pars_n2_science_runtime_linux_v8":
        raise GateBError("runtime lock schema differs")
    if lock.get("platform") != "Linux" or lock.get("architecture") != "x86-64":
        raise GateBError("runtime lock is not Linux x86-64")
    records = lock.get("required_smc_files")
    if not isinstance(records, list) or len(records) != 12:
        raise GateBError("runtime lock must contain exactly 12 SMC records")
    names = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {"name", "size_bytes", "sha256"}:
            raise GateBError("runtime SMC record fields differ")
        name = record["name"]
        if not isinstance(name, str) or Path(name).name != name or name in names:
            raise GateBError("runtime SMC names are unsafe or duplicate")
        if type(record["size_bytes"]) is not int or record["size_bytes"] <= 0:
            raise GateBError(f"runtime SMC size invalid: {name}")
        if not isinstance(record["sha256"], str) or not SHA_RE.fullmatch(record["sha256"]):
            raise GateBError(f"runtime SMC SHA invalid: {name}")
        names.append(name)
    return lock


def validate_reference_smc(path: Path) -> dict[str, Any]:
    raw = strict_regular(path, label="reference SMC") and Path(path).read_bytes()
    if sha256_bytes(raw) != REFERENCE_SMC_SHA256:
        raise GateBError("reference type-7 SMC SHA differs")
    lines = raw.splitlines()
    try:
        basic = next(i for i, line in enumerate(lines) if b"# Basic Change data" in line)
        flags_at = next(i for i, line in enumerate(lines) if b"# Simulation flags" in line)
        data_at = next(i for i, line in enumerate(lines) if b"# Data files" in line)
    except StopIteration as exc:
        raise GateBError("reference SMC markers missing") from exc
    number_re = re.compile(rb"[-+]?\d*\.\d+E[-+]\d+")
    values = [
        float(token.decode("ascii"))
        for line in lines[basic + 1 : flags_at]
        for token in number_re.findall(line)
    ]
    flags = lines[flags_at + 1].strip()
    data = [line.strip().decode("ascii") for line in lines[data_at + 1 :] if line.strip()]
    if len(values) != 120 or len(flags) != 30:
        raise GateBError("reference SMC basic/flag counts differ")
    if values[13] != -7.0 or values[14] != -7.0:
        raise GateBError("reference SMC is not paired type-7")
    if flags[10:11] != b"T" or flags[14:15] != b"T":
        raise GateBError("reference SMC interaction/readback flags differ")
    if data[:2] != ["h2o", "h2o"]:
        raise GateBError("reference SMC phantom cross sections differ")
    return {
        "sha256": sha256_bytes(raw),
        "indices_14_15": [values[13], values[14]],
        "flag_11": True,
        "flag_15": True,
        "phantom_cross_sections": data[:2],
    }


def verify_runtime(runtime_root: Path, lock: dict[str, Any] | None = None) -> VerifiedRuntime:
    if platform.system() != "Linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        raise GateBError("real SIMIND invocation requires Linux x86-64")
    lock = lock or load_runtime_lock()
    root = Path(runtime_root).resolve()
    if not root.is_dir() or root.is_symlink():
        raise GateBError(f"official runtime root invalid: {root}")
    binary = root / lock["runtime_layout"]["binary_relative_path"]
    binary_info = strict_regular(binary, label="SIMIND ELF")
    binary_record = lock["simind"]
    if binary_info.st_size != binary_record["size_bytes"] or sha256_file(binary) != binary_record["sha256"]:
        raise GateBError("SIMIND ELF size/SHA mismatch")
    header = binary.read_bytes()[:20]
    if len(header) < 20 or header[:4] != b"\x7fELF" or header[4:6] != b"\x02\x01" or int.from_bytes(header[18:20], "little") != 62:
        raise GateBError("SIMIND executable is not little-endian ELF64 EM_X86_64")
    if binary.stat().st_mode & 0o111 == 0:
        raise GateBError("SIMIND ELF is not executable")
    source = root / lock["runtime_layout"]["smc_dir_relative_path"]
    if not source.is_dir() or source.is_symlink():
        raise GateBError("official SMC source directory invalid")
    for record in lock["required_smc_files"]:
        path = source / record["name"]
        info = strict_regular(path, label=f"official SMC {record['name']}")
        if info.st_size != record["size_bytes"] or sha256_file(path) != record["sha256"]:
            raise GateBError(f"official SMC mismatch: {record['name']}")
    return VerifiedRuntime(root, binary, source, binary_record["sha256"], tuple(dict(r) for r in lock["required_smc_files"]))


def materialize_private_smc(job_dir: Path, runtime: VerifiedRuntime) -> tuple[Path, list[dict[str, Any]]]:
    target = Path(job_dir).resolve() / "smc_dir"
    if target.exists():
        raise GateBError(f"private SMC directory already exists: {target}")
    target.mkdir(mode=0o700)
    records = []
    for record in runtime.records:
        source = runtime.smc_source / record["name"]
        raw = source.read_bytes()
        if len(raw) != record["size_bytes"] or sha256_bytes(raw) != record["sha256"]:
            raise GateBError(f"official SMC changed during copy: {record['name']}")
        destination = target / record["name"]
        write_new(destination, raw)
        destination.chmod(0o600)
        records.append({"name": record["name"], **descriptor(destination)})
    if sorted(path.name for path in target.iterdir()) != sorted(r["name"] for r in runtime.records):
        raise GateBError("private SMC inventory differs from 12-file lock")
    return target, records


def verify_private_smc_post(path: Path, pre: list[dict[str, Any]]) -> list[dict[str, Any]]:
    path = Path(path)
    names = sorted(item.name for item in path.iterdir())
    if names != sorted(record["name"] for record in pre):
        raise GateBError("private SMC post-run inventory differs")
    post = [{"name": name, **descriptor(path / name)} for name in names]
    pre_by_name = {record["name"]: record for record in pre}
    post_by_name = {record["name"]: record for record in post}
    for name in IMMUTABLE_SMC_NAMES:
        if post_by_name[name] != pre_by_name[name]:
            raise GateBError(f"immutable private SMC changed: {name}")
    if post_by_name["ranlux2.num"]["bytes"] <= 0:
        raise GateBError("private ranlux2.num became empty")
    return post


def build_packed_argv(binary: str | Path, case_id: str, rr_seed: int, config: dict[str, Any]) -> tuple[str, str, str]:
    binary_text = str(binary)
    if not PurePosixPath(binary_text).is_absolute() or "\\" in binary_text or not SAFE_CASE.fullmatch(case_id):
        raise GateBError("packed command binary/case identity invalid")
    physics = config["physics"]
    packed = (
        f"mc/FS:{case_id}/FD:{case_id}"
        f"/NN:{physics['NN']}/RR:{rr_seed}"
        f"/FW:{physics['window_stem']}"
        f"/IN:x21,{physics['entry_21_density_threshold_times_1000']}x"
        f"/IN:x22,{physics['entry_22_aligned_mu_readback_mode']}x"
        f"/25:{physics['index_25_activity_time']}"
        f"/100:{physics['index_100_detector_i']}"
        f"/101:{physics['index_101_detector_j']}"
        f"/CA:{physics['scattwin_calculation_mode']}"
        f"/84:{physics['basic_change_84']}"
        f"{physics['spectrum_override']}"
    )
    expected = (
        f"mc/FS:{case_id}/FD:{case_id}/NN:10/RR:{rr_seed}/FW:n2_photopeak"
        "/IN:x21,100x/IN:x22,3x/25:1704/100:160/101:208/CA:2/84:1/IN:x50,Nx"
    )
    if packed != expected:
        raise GateBError("packed command differs from frozen Gate B protocol")
    return binary_text, "ge870_czt", packed


def assign_count_targets(case_ids: list[str], config: dict[str, Any]) -> dict[str, int]:
    observation = config["observation"]
    ids = sorted(case_ids)
    reference = np.sort(np.asarray(observation["reference_counts"], dtype=np.float64))
    quantiles = (np.arange(len(ids), dtype=np.float64) + 0.5) / len(ids)
    try:
        values = np.quantile(reference, quantiles, method="linear")
    except TypeError:
        values = np.quantile(reference, quantiles, interpolation="linear")
    targets = np.rint(values).astype(np.int64)
    permutation = np.random.default_rng(int(observation["target_assignment_seed"])).permutation(len(ids))
    return {case_id: int(targets[index]) for case_id, index in zip(ids, permutation)}


def _copy_new(source: Path, destination: Path) -> None:
    strict_regular(source, label="package source")
    write_new(destination, Path(source).read_bytes())


def _write_raw_float32(path: Path, array: np.ndarray) -> dict[str, Any]:
    encoded = np.ascontiguousarray(array, dtype=np.dtype("<f4"))
    raw = encoded.tobytes(order="C")
    write_new(path, raw)
    decoded = np.frombuffer(path.read_bytes(), dtype=np.dtype("<f4")).reshape(encoded.shape, order="C")
    if not np.array_equal(decoded, encoded):
        raise GateBError(f"float32 binary readback differs: {path}")
    return {**descriptor(path), "shape": list(encoded.shape), "dtype": "<f4", "order": "C_ZYX"}


def _export_with_master_write_bin(
    *, output_stem: Path, suffix: str, array: np.ndarray
) -> dict[str, Any]:
    # Local packaging deliberately reuses the qualified master exporter.  The
    # import stays local because the Linux consumption package does not export.
    from core.interfile_writer import write_bin

    expected = output_stem.parent / f"{output_stem.name}{suffix}.bin"
    created = write_bin(array, output_stem, suffix)
    if created.resolve() != expected.resolve():
        raise GateBError("master exporter returned an unexpected path")
    encoded = np.ascontiguousarray(array, dtype=np.dtype("<f4"))
    decoded = np.fromfile(created, dtype=np.dtype("<f4")).reshape(encoded.shape, order="C")
    if not np.array_equal(decoded, encoded):
        raise GateBError(f"master exporter little-endian readback differs: {created}")
    return {**descriptor(created), "shape": list(encoded.shape), "dtype": "<f4", "order": "C_ZYX"}


def _package_source_paths(root: Path) -> dict[str, Path]:
    return {
        "gate_b/__init__.py": root / "src" / "gate_b" / "__init__.py",
        "gate_b/bridge.py": root / "src" / "gate_b" / "bridge.py",
        "gate_b/selection.py": root / "src" / "gate_b" / "selection.py",
        "configs/gate_b_hybrid_921e2e7.json": root / "configs" / "gate_b_hybrid_921e2e7.json",
        "configs/gate_b_linux_runtime_v8.json": root / "configs" / "gate_b_linux_runtime_v8.json",
        "reference/ge870_czt_type7.smc": root / "reference" / "simind" / "gate_b" / "ge870_czt_type7.smc",
        "reference/n2_photopeak.win": root / "reference" / "simind" / "gate_b" / "n2_photopeak.win",
        "freeze/selection.json": root / "gate_b" / "freeze" / "selection.json",
        "freeze/selection.csv": root / "gate_b" / "freeze" / "selection.csv",
        "freeze/selection.md": root / "gate_b" / "freeze" / "selection.md",
        "freeze/candidate_features.json": root / "gate_b" / "freeze" / "candidate_features.json",
        "freeze/candidate_features.csv": root / "gate_b" / "freeze" / "candidate_features.csv",
    }


def _tree_inventory(root: Path, *, excluded: set[str] | None = None) -> list[dict[str, Any]]:
    excluded = excluded or set()
    records = []
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        directories.sort()
        files.sort()
        current_path = Path(current)
        for name in directories:
            path = current_path / name
            if path.is_symlink() or not path.is_dir():
                raise GateBError(f"tree contains symlink/special directory: {path}")
        for name in files:
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if relative in excluded:
                continue
            records.append({"path": relative, **descriptor(path)})
    return records


def _deterministic_tar_gz(root: Path, archive_path: Path, *, arc_root: str) -> None:
    if archive_path.exists():
        raise GateBError(f"archive target already exists: {archive_path}")
    members: list[Path] = [root]
    members.extend(sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix()))
    with archive_path.open("xb") as raw_stream:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_stream, compresslevel=9, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT) as archive:
                for path in members:
                    relative = path.relative_to(root).as_posix()
                    name = arc_root if relative == "." else f"{arc_root}/{relative}"
                    info = path.lstat()
                    if stat.S_ISLNK(info.st_mode):
                        raise GateBError(f"tar input symlink forbidden: {path}")
                    tar_info = tarfile.TarInfo(name)
                    tar_info.mtime = 0
                    tar_info.uid = tar_info.gid = 0
                    tar_info.uname = tar_info.gname = ""
                    tar_info.mode = 0o755 if path.is_dir() else 0o644
                    if path.is_dir():
                        tar_info.type = tarfile.DIRTYPE
                        archive.addfile(tar_info)
                    elif path.is_file():
                        tar_info.size = info.st_size
                        with path.open("rb") as stream:
                            archive.addfile(tar_info, stream)
                    else:
                        raise GateBError(f"tar input special file forbidden: {path}")


def build_source_package(*, output_dir: Path, freeze_commit: str, root: Path | None = None) -> dict[str, Any]:
    if not isinstance(freeze_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", freeze_commit):
        raise GateBError("freeze_commit must be a full lowercase Git commit")
    source_root = Path(root).resolve() if root is not None else repo_root()
    config = load_contract(source_root)
    selection_path = source_root / "gate_b" / "freeze" / "selection.json"
    selection = read_json(selection_path, label="frozen selection")
    if selection.get("status") != "frozen" or selection.get("selected_count") != 10:
        raise GateBError("selection is not a frozen ten-case plan")
    if selection["selection_algorithm"]["code_sha256"] != sha256_file(source_root / "src" / "gate_b" / "selection.py"):
        raise GateBError("selection code changed after the freeze")
    if selection["selection_algorithm"]["config_sha256"] != sha256_file(source_root / "configs" / "gate_b_hybrid_921e2e7.json"):
        raise GateBError("selection config changed after the freeze")
    selected = selection["selected"]
    if selected[0]["case_id"] != selection["sentinel"]["case_id"]:
        raise GateBError("sentinel must remain first in frozen order")
    output = Path(output_dir).resolve()
    if output.exists():
        raise GateBError(f"source package output must be fresh: {output}")
    output.mkdir(parents=True)
    package = output / PACKAGE_ROOT_NAME
    package.mkdir()
    for relative, source in _package_source_paths(source_root).items():
        _copy_new(source, package / relative)
    if sha256_file(package / "reference" / "n2_photopeak.win") != REFERENCE_WINDOW_SHA256:
        raise GateBError("photopeak window SHA differs")
    smc_contract = validate_reference_smc(package / "reference" / "ge870_czt_type7.smc")
    targets = assign_count_targets(selection["selected_case_ids_in_order"], config)
    runtime_binary = str(PurePosixPath(config["runtime"]["official_runtime_root"]) / "simind")
    jobs = []
    for selected_record in selected:
        case_id = selected_record["case_id"]
        case_number = int(case_id.rsplit("_", 1)[1])
        short = f"j{case_number:04d}"
        job_dir = package / "jobs" / short
        job_dir.mkdir(parents=True)
        parent = selected_record["candidate"]["parent"]
        source_paths = {
            "parent.npz": Path(selection["parent"]["root_read_only"]) / parent["npz"]["relative_path"],
            "parent_meta.json": Path(selection["parent"]["root_read_only"]) / parent["metadata"]["relative_path"],
            "parent_qc.json": Path(selection["parent"]["root_read_only"]) / parent["qc"]["relative_path"],
        }
        for name, source in source_paths.items():
            role = "npz" if name == "parent.npz" else "metadata" if name == "parent_meta.json" else "qc"
            if sha256_file(source) != parent[role]["sha256"] or source.stat().st_size != parent[role]["bytes"]:
                raise GateBError(f"selected parent artifact changed: {case_id} {role}")
            _copy_new(source, job_dir / name)
        with np.load(job_dir / "parent.npz") as payload:
            activity = np.asarray(payload["activity"], dtype=np.dtype("<f4"), order="C")
            mu = np.asarray(payload["mu_map"], dtype=np.dtype("<f4"), order="C")
        if activity.shape != (128, 128, 128) or mu.shape != activity.shape:
            raise GateBError(f"selected parent shape differs: {case_id}")
        if not np.isfinite(activity).all() or np.any(activity < 0.0) or not np.isfinite(mu).all() or np.any(mu < 0.0):
            raise GateBError(f"selected parent physical arrays invalid: {case_id}")
        output_stem = job_dir / case_id
        source_record = _export_with_master_write_bin(
            output_stem=output_stem,
            suffix="_act_av",
            array=activity,
        )
        stored_mu = np.asarray(mu * np.float32(0.442), dtype=np.dtype("<f4"), order="C")
        recovered_mu = np.asarray(stored_mu / np.float32(0.442), dtype=np.dtype("<f4"), order="C")
        roundtrip_error = float(np.max(np.abs(recovered_mu.astype(np.float64) - mu.astype(np.float64))))
        if roundtrip_error > float(config["physics"]["type7_roundtrip_max_abs_error_cm_inverse"]):
            raise GateBError(f"type-7 roundtrip failed: {case_id}")
        attenuation_record = _export_with_master_write_bin(
            output_stem=output_stem,
            suffix="_atn_av",
            array=stored_mu,
        )
        _copy_new(package / "reference" / "ge870_czt_type7.smc", job_dir / "ge870_czt.smc")
        _copy_new(package / "reference" / "n2_photopeak.win", job_dir / "n2_photopeak.win")
        parent_meta = read_json(job_dir / "parent_meta.json", label="parent metadata")
        rr_seed = 930000 + case_number
        argv = build_packed_argv(runtime_binary, case_id, rr_seed, config)
        source_mass = float(np.sum(activity, dtype=np.float64))
        job = {
            "schema_version": JOB_SCHEMA,
            "case_id": case_id,
            "job_dir": f"jobs/{short}",
            "selection_rank": int(selected_record["rank"]),
            "sentinel": bool(selected_record["sentinel"]),
            "pilot_only": True,
            "freeze_commit": freeze_commit,
            "NN": 10,
            "rr_seed": rr_seed,
            "expected_histories": source_mass * 10.0,
            "exact_argv": list(argv),
            "packed_token": argv[2],
            "source_mass_float64": source_mass,
            "observation": {
                "expectation_role": "total_only",
                "target_total_counts": targets[case_id],
                "seed": int(parent_meta["seed"]) + int(config["observation"]["per_case_seed_offset"]),
                "angular_cv_range": config["observation"]["angular_cv_range"],
            },
            "type7": {
                "stored_formula": config["physics"]["type7_stored_formula"],
                "roundtrip_max_abs_error_cm_inverse": roundtrip_error,
                "entry_21": 100,
                "entry_22": 3,
                "reference_smc": smc_contract,
            },
            "preflight": {
                "native_aperture_passed": selected_record["candidate"]["preflight_native_aperture_passed"],
                "native_fov_min_margin_mm": selected_record["candidate"]["activity_native_fov_min_margin_mm"],
                "fov_pressure_ratio": selected_record["candidate"]["activity_fov_pressure_ratio"],
                "per_view": selected_record["preflight_per_view"],
            },
            "parent": parent,
            "files": {
                "parent_npz": descriptor(job_dir / "parent.npz"),
                "parent_metadata": descriptor(job_dir / "parent_meta.json"),
                "parent_qc": descriptor(job_dir / "parent_qc.json"),
                "source": source_record,
                "attenuation": attenuation_record,
                "smc": descriptor(job_dir / "ge870_czt.smc"),
                "window": descriptor(job_dir / "n2_photopeak.win"),
            },
        }
        write_new(job_dir / "job.json", json_bytes(job))
        jobs.append({"case_id": case_id, "job_dir": f"jobs/{short}", "sentinel": job["sentinel"], "job_sha256": sha256_file(job_dir / "job.json")})
    plan = {
        "schema_version": "pars_gate_b_plan_v1",
        "freeze_commit": freeze_commit,
        "pilot_only": True,
        "selection_sha256": sha256_file(package / "freeze" / "selection.json"),
        "config_sha256": sha256_file(package / "configs" / "gate_b_hybrid_921e2e7.json"),
        "runtime_lock_sha256": sha256_file(package / "configs" / "gate_b_linux_runtime_v8.json"),
        "sentinel": selection["sentinel"]["case_id"],
        "case_ids_in_order": selection["selected_case_ids_in_order"],
        "jobs": jobs,
        "scientific_invocation_plan": "one sentinel then unchanged remaining nine with max concurrency three",
        "maximum_concurrency_after_sentinel": 3,
    }
    write_new(package / "GATE_B_PLAN.json", json_bytes(plan))
    inventory = _tree_inventory(package)
    manifest = {
        "schema_version": PACKAGE_SCHEMA,
        "package_root": PACKAGE_ROOT_NAME,
        "freeze_commit": freeze_commit,
        "pilot_only": True,
        "contains_formal550": False,
        "contains_training_or_evaluation": False,
        "selection_sha256": plan["selection_sha256"],
        "plan_sha256": sha256_file(package / "GATE_B_PLAN.json"),
        "runtime_identity": {
            "simind_elf_sha256": config["runtime"]["simind_elf_sha256"],
            "required_smc_count": 12,
            "runtime_lock_sha256": plan["runtime_lock_sha256"],
        },
        "files": inventory,
        "real_simind_invocations_performed": 0,
    }
    manifest_raw = json_bytes(manifest)
    write_new(package / "PACKAGE_MANIFEST.json", manifest_raw)
    archive = output / f"{PACKAGE_ROOT_NAME}_{freeze_commit[:7]}.tar.gz"
    _deterministic_tar_gz(package, archive, arc_root=PACKAGE_ROOT_NAME)
    external_manifest = output / "PACKAGE_MANIFEST.json"
    write_new(external_manifest, manifest_raw)
    sums = (
        f"{sha256_file(archive)}  {archive.name}\n"
        f"{sha256_file(external_manifest)}  {external_manifest.name}\n"
    ).encode("ascii")
    write_new(output / "SHA256SUMS", sums)
    return {**manifest, "archive": {"name": archive.name, **descriptor(archive)}, "manifest_sha256": sha256_bytes(manifest_raw)}


def verify_package_root(package_root: Path, *, allow_job_outputs: bool = False) -> dict[str, Any]:
    root = Path(package_root).resolve()
    if root.name != PACKAGE_ROOT_NAME or not root.is_dir() or root.is_symlink():
        raise GateBError(f"source package root invalid: {root}")
    manifest = read_json(root / "PACKAGE_MANIFEST.json", label="package manifest")
    if manifest.get("schema_version") != PACKAGE_SCHEMA or manifest.get("package_root") != PACKAGE_ROOT_NAME:
        raise GateBError("package manifest identity differs")
    expected = {record["path"]: record for record in manifest.get("files", [])}
    observed = {record["path"]: record for record in _tree_inventory(root, excluded={"PACKAGE_MANIFEST.json"})}
    if any(observed.get(path) != record for path, record in expected.items()):
        raise GateBError("source package inventory/SHA differs")
    extra_paths = sorted(set(observed) - set(expected))
    if extra_paths and not allow_job_outputs:
        raise GateBError("source package contains files outside the frozen inventory")
    plan = read_json(root / "GATE_B_PLAN.json", label="Gate B plan")
    if manifest["plan_sha256"] != sha256_file(root / "GATE_B_PLAN.json"):
        raise GateBError("Gate B plan SHA differs")
    if len(plan.get("jobs", [])) != 10 or plan.get("sentinel") != plan["case_ids_in_order"][0]:
        raise GateBError("Gate B plan job/sentinel identity differs")
    if extra_paths:
        allowed_prefixes = tuple(f"{record['job_dir']}/" for record in plan["jobs"])
        if any(not path.startswith(allowed_prefixes) for path in extra_paths):
            raise GateBError("runtime-created file exists outside a frozen job directory")
    load_contract(root)
    load_runtime_lock(root)
    validate_reference_smc(root / "reference" / "ge870_czt_type7.smc")
    if sha256_file(root / "reference" / "n2_photopeak.win") != REFERENCE_WINDOW_SHA256:
        raise GateBError("package window SHA differs")
    for plan_job in plan["jobs"]:
        job_dir = root / plan_job["job_dir"]
        job = read_json(job_dir / "job.json", label="job")
        if job["case_id"] != plan_job["case_id"] or sha256_file(job_dir / "job.json") != plan_job["job_sha256"]:
            raise GateBError("package job identity differs")
        for name, relative in {
            "parent_npz": "parent.npz",
            "parent_metadata": "parent_meta.json",
            "parent_qc": "parent_qc.json",
            "source": f"{job['case_id']}_act_av.bin",
            "attenuation": f"{job['case_id']}_atn_av.bin",
            "smc": "ge870_czt.smc",
            "window": "n2_photopeak.win",
        }.items():
            if descriptor(job_dir / relative) != {"bytes": job["files"][name]["bytes"], "sha256": job["files"][name]["sha256"]}:
                raise GateBError(f"prepared job file changed: {job['case_id']} {name}")
        expected_argv = build_packed_argv(job["exact_argv"][0], job["case_id"], job["rr_seed"], load_contract(root))
        if tuple(job["exact_argv"]) != expected_argv:
            raise GateBError("prepared job argv differs")
        if not job["preflight"]["native_aperture_passed"] or job["preflight"]["native_fov_min_margin_mm"] < 0.0:
            raise GateBError("prepared job failed frozen source-aperture preflight")
    return {"status": "passed", "manifest_sha256": sha256_file(root / "PACKAGE_MANIFEST.json"), "job_count": 10}


def _plan_job(package_root: Path, case_id: str) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    plan = read_json(package_root / "GATE_B_PLAN.json", label="Gate B plan")
    records = [record for record in plan["jobs"] if record["case_id"] == case_id]
    if len(records) != 1:
        raise GateBError(f"unknown/duplicate Gate B case: {case_id}")
    job_dir = (package_root / records[0]["job_dir"]).resolve()
    try:
        job_dir.relative_to(package_root.resolve())
    except ValueError as exc:
        raise GateBError("job directory escapes package root") from exc
    return records[0], job_dir, read_json(job_dir / "job.json", label="job")


def child_environment(private_smc: Path) -> dict[str, str]:
    if Path(private_smc).name != "smc_dir" or not Path(private_smc).is_absolute():
        raise GateBError("private SMC must be an absolute job-local smc_dir")
    return {
        "LC_ALL": "C",
        "LANG": "C",
        "SMC_DIR": "smc_dir/",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
    }


def _current_output_inventory(job_dir: Path) -> list[dict[str, Any]]:
    names = set(REQUIRED_OUTPUTS) | {"simind.stdout.txt", "simind.stderr.txt"}
    names.update(path.name for path in job_dir.iterdir() if path.is_file() and OPTIONAL_HEADER_RE.fullmatch(path.name))
    return [{"name": name, **descriptor(job_dir / name)} for name in sorted(names) if (job_dir / name).is_file()]


def run_job(*, package_root: Path, runtime_root: Path, case_id: str, verify_package: bool = True) -> dict[str, Any]:
    package = Path(package_root).resolve()
    if verify_package:
        verify_package_root(package)
    _, job_dir, job = _plan_job(package, case_id)
    if (job_dir / "run.json").exists() or (job_dir / "smc_dir").exists():
        raise GateBError(f"job is not fresh; refusing a second invocation: {case_id}")
    lock = load_runtime_lock(package)
    runtime = verify_runtime(runtime_root, lock)
    expected_binary = Path(job["exact_argv"][0])
    if runtime.binary != expected_binary or runtime.binary_sha256 != load_contract(package)["runtime"]["simind_elf_sha256"]:
        raise GateBError("runtime path/SHA differs from prepared exact argv")
    private, private_pre = materialize_private_smc(job_dir, runtime)
    argv = build_packed_argv(runtime.binary, case_id, int(job["rr_seed"]), load_contract(package))
    if list(argv) != job["exact_argv"]:
        raise GateBError("consumption-time argv differs from prepared job")
    environment = child_environment(private)
    started = time.perf_counter()
    process_error = None
    completed: subprocess.CompletedProcess[bytes] | None = None
    try:
        completed = subprocess.run(
            argv,
            cwd=job_dir,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            check=False,
            timeout=86400,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        process_error = f"{type(exc).__name__}: {exc}"
    walltime = time.perf_counter() - started
    stdout = bytes(completed.stdout) if completed is not None else b""
    stderr = bytes(completed.stderr) if completed is not None else str(process_error).encode("utf-8")
    write_new(job_dir / "simind.stdout.txt", stdout)
    write_new(job_dir / "simind.stderr.txt", stderr)
    runtime_post_error = None
    private_post_error = None
    private_post: list[dict[str, Any]] = []
    try:
        verify_runtime(runtime_root, lock)
    except GateBError as exc:
        runtime_post_error = str(exc)
    try:
        private_post = verify_private_smc_post(private, private_pre)
    except GateBError as exc:
        private_post_error = str(exc)
    exit_code = completed.returncode if completed is not None else None
    status = "subprocess_completed" if exit_code == 0 and not process_error and not runtime_post_error and not private_post_error else "subprocess_failed"
    record = {
        "schema_version": RUN_SCHEMA,
        "case_id": case_id,
        "status": status,
        "scientific_invocation_count": 1,
        "exit_code": exit_code,
        "process_error": process_error,
        "runtime_post_error": runtime_post_error,
        "private_post_error": private_post_error,
        "walltime_seconds": walltime,
        "command": list(argv),
        "cwd_relative": job["job_dir"],
        "environment": environment,
        "runtime": {
            "binary_sha256": runtime.binary_sha256,
            "required_smc_count": len(runtime.records),
            "official_runtime_reverified_after_subprocess": runtime_post_error is None,
        },
        "private_smc_pre": private_pre,
        "private_smc_post": private_post,
        "output_inventory": _current_output_inventory(job_dir),
    }
    write_new(job_dir / "run.json", json_bytes(record))
    if status != "subprocess_completed":
        raise GateBError(f"SIMIND invocation failed for {case_id}; run.json preserves evidence")
    return record


def run_remaining(*, package_root: Path, runtime_root: Path, max_workers: int = 3) -> list[dict[str, Any]]:
    if max_workers < 1 or max_workers > 3:
        raise GateBError("remaining-case concurrency must be between one and three")
    package = Path(package_root).resolve()
    verify_package_root(package, allow_job_outputs=True)
    plan = read_json(package / "GATE_B_PLAN.json", label="plan")
    sentinel = plan["sentinel"]
    _, sentinel_dir, _ = _plan_job(package, sentinel)
    sentinel_analysis = read_json(sentinel_dir / "analysis.json", label="sentinel analysis")
    if sentinel_analysis.get("conclusion") != "PASS":
        raise GateBError("remaining nine are locked until sentinel conclusion is PASS")
    remaining = [case for case in plan["case_ids_in_order"] if case != sentinel]
    if len(remaining) != 9:
        raise GateBError("remaining-case plan must contain exactly nine cases")
    results = []
    errors = []
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="gate-b-simind") as executor:
        futures = {executor.submit(run_job, package_root=package, runtime_root=runtime_root, case_id=case, verify_package=False): case for case in remaining}
        for future in as_completed(futures):
            case = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # evidence remains per job
                errors.append({"case_id": case, "error": f"{type(exc).__name__}: {exc}"})
    if errors:
        error_path = package / "remaining_run_failures.json"
        write_new(error_path, json_bytes({"schema_version": "pars_gate_b_run_failures_v1", "errors": errors}))
        raise GateBError(f"one or more remaining invocations failed: {errors}")
    return sorted(results, key=lambda row: row["case_id"])


def _read_ascii(path: Path, *, label: str) -> str:
    strict_regular(path, label=label)
    try:
        return Path(path).read_bytes().decode("ascii")
    except (OSError, UnicodeDecodeError) as exc:
        raise GateBError(f"cannot read ASCII {label}: {exc}") from exc


def _single_match(text: str, pattern: str, *, label: str) -> str:
    matches = re.findall(pattern, text, flags=re.MULTILINE)
    if len(matches) != 1:
        raise GateBError(f"SIMIND report must contain exactly one {label}")
    return str(matches[0]).strip()


def _equal_ints(text: str, pattern: str, *, label: str) -> int:
    matches = [int(value) for value in re.findall(pattern, text, flags=re.MULTILINE)]
    if not matches or len(set(matches)) != 1:
        raise GateBError(f"SIMIND report missing/conflicting {label}")
    return matches[0]


def _wrapped_command(text: str) -> str:
    lines = text.splitlines()
    indexes = [i for i, line in enumerate(lines) if re.match(r"^[ \t]*Command:[ \t]*", line)]
    if len(indexes) != 1:
        raise GateBError("SIMIND report must contain exactly one Command")
    index = indexes[0]
    first = re.sub(r"^[ \t]*Command:[ \t]*", "", lines[index]).strip()
    continuation = "".join(line.strip() for line in lines[index + 1 :] if line.strip())
    command = first + continuation
    if not command:
        raise GateBError("SIMIND report Command is empty")
    return command


def parse_report(path: Path, *, expected_argv: tuple[str, ...], expected_histories: float, config: dict[str, Any]) -> dict[str, Any]:
    text = _read_ascii(path, label="SIMIND report")
    values: dict[str, Any] = {
        "input_file": _single_match(text, r"(?<![A-Za-z0-9_/])InputFile\.*:[ \t]*([^ \t\r\n]+)", label="InputFile"),
        "output_file": _single_match(text, r"(?<![A-Za-z0-9_/])OutputFile:[ \t]*([^ \t\r\n]+)", label="OutputFile"),
        "score_route": _single_match(text, r"(?<![A-Za-z0-9_/])ScoreRout\.*:[ \t]*([^ \t\r\n]+)", label="ScoreRout"),
        "source_type": _single_match(text, r"(?<![A-Za-z0-9_/])SourceType\.*:[ \t]*([^ \t\r\n]+)", label="SourceType"),
        "phantom_type": _single_match(text, r"(?<![A-Za-z0-9_/])PhantomType\.*:[ \t]*([^ \t\r\n]+)", label="PhantomType"),
        "window_file": _single_match(text, r"^[ \t]*Scattwin results:[ \t]*Window file:[ \t]*([^ \t\r\n]+)", label="Scattwin window"),
        "matrix_i": _equal_ints(text, r"(?<![A-Za-z0-9_/])MatrixSize I\.*:[ \t]*(\d+)", label="MatrixSize I"),
        "matrix_j": _equal_ints(text, r"(?<![A-Za-z0-9_/])MatrixSize J\.*:[ \t]*(\d+)", label="MatrixSize J"),
        "detector_i": int(_single_match(text, r"(?<![A-Za-z0-9_/])Number detectors[ \t]+I:[ \t]*(\d+)", label="Number detectors I")),
        "detector_j": int(_single_match(text, r"(?<![A-Za-z0-9_/])Number Detectors J:[ \t]*(\d+)", label="Number detectors J")),
        "projections": int(_single_match(text, r"(?<![A-Za-z0-9_/])Nr of Projections\.*:[ \t]*(\d+)", label="Nr of Projections")),
        "starting_angle": float(_single_match(text, rf"(?<![A-Za-z0-9_/])StartingAngle\.*:[ \t]*({RES_NUMBER})", label="StartingAngle").replace("D", "E")),
        "rotation_angle": float(_single_match(text, rf"(?<![A-Za-z0-9_/])RotationAngle\.*:[ \t]*({RES_NUMBER})", label="RotationAngle").replace("D", "E")),
        "projection_start": int(_single_match(text, r"(?<![A-Za-z0-9_/])Projection\.\[start\]:[ \t]*(\d+)", label="Projection start")),
        "projection_end": int(_single_match(text, r"(?<![A-Za-z0-9_/])Projection\.\.\.\[end\]:[ \t]*(\d+)", label="Projection end")),
        "command": _wrapped_command(text),
    }
    photons = float(_single_match(text, rf"(?<![A-Za-z0-9_/])PhotonsPerProj\.{{4}}:[ \t]*({RES_NUMBER})(?![A-Za-z0-9_.])", label="PhotonsPerProj").replace("D", "E"))
    scatter_total = float(_single_match(text, rf"(?<![A-Za-z0-9_/])Scatter/Total\.{{6}}:[ \t]*({RES_NUMBER})(?![A-Za-z0-9_.])", label="Scatter/Total").replace("D", "E"))
    scatter_primary_matches = re.findall(rf"(?<![A-Za-z0-9_/])Scatter/Primary\.{{4}}:[ \t]*({RES_NUMBER})(?![A-Za-z0-9_.])", text, flags=re.MULTILINE)
    scatter_primary = float(scatter_primary_matches[0].replace("D", "E")) if len(scatter_primary_matches) == 1 else None
    expected = {
        "input_file": "ge870_czt",
        "output_file": "mc",
        "score_route": "scattwin",
        "source_type": "XcatBinMap",
        "phantom_type": "XcatBinMap",
        "window_file": "n2_photopeak.win",
        "matrix_i": 128,
        "matrix_j": 128,
        "detector_i": 160,
        "detector_j": 208,
        "projections": 60,
        "starting_angle": 180.0,
        "rotation_angle": 6.0,
        "projection_start": 1,
        "projection_end": 60,
        "command": " ".join(expected_argv[1:]),
    }
    if values != expected:
        differences = {key: {"expected": expected[key], "observed": values[key]} for key in expected if values[key] != expected[key]}
        raise GateBError(f"SIMIND report contract differs: {differences}")
    if not math.isfinite(photons) or abs(photons - expected_histories) > float(config["physics"]["histories_absolute_tolerance"]):
        raise GateBError("SIMIND histories differ from source mass times NN")
    if not math.isfinite(scatter_total) or scatter_total < 0.0:
        raise GateBError("SIMIND Scatter/Total invalid")
    return {**values, "photons_per_proj": photons, "scatter_total": scatter_total, "scatter_primary": scatter_primary}


def parse_hct(path: Path) -> dict[str, Any]:
    text = _read_ascii(path, label="HCT")
    fields = {
        "data_file": _single_match(text, r"^[ \t]*!name of data file[ \t]*:=[ \t]*([^\r\n]+)", label="HCT data file"),
        "byte_order": _single_match(text, r"^[ \t]*imagedata byte order[ \t]*:=[ \t]*([^\r\n]+)", label="HCT byte order"),
        "bytes_per_pixel": int(_single_match(text, r"^[ \t]*!number of bytes per pixel[ \t]*:=[ \t]*(\d+)", label="HCT bytes per pixel")),
        "number_format": _single_match(text, r"^[ \t]*!number format[ \t]*:=[ \t]*([^\r\n]+)", label="HCT number format"),
        "unit": _single_match(text, r"^[ \t]*;#[ \t]*Units of data \(ECT\)[ \t]*:=[ \t]*([^\r\n]+)", label="HCT unit"),
    }
    matrix = [int(_single_match(text, rf"^[ \t]*!matrix size \[{axis}\][ \t]*:=[ \t]*(\d+)", label=f"HCT matrix {axis}")) for axis in (1, 2, 3)]
    spacing = [float(_single_match(text, rf"^[ \t]*scaling factor \(mm/pixel\) \[{axis}\][ \t]*:=[ \t]*({RES_NUMBER})", label=f"HCT spacing {axis}").replace("D", "E")) for axis in (1, 2, 3)]
    if fields != {"data_file": "mc.ict", "byte_order": "LITTLEENDIAN", "bytes_per_pixel": 4, "number_format": "short float", "unit": "mu"}:
        raise GateBError("HCT semantic fields differ")
    if matrix != [128, 128, 128] or any(abs(value - 4.42) > 1e-6 for value in spacing):
        raise GateBError("HCT matrix/spacing differs")
    return {**fields, "matrix_size_xyz": matrix, "spacing_mm_xyz": spacing}


def _load_component(job_dir: Path, role: str) -> tuple[np.ndarray, dict[str, Any]]:
    path = job_dir / COMPONENT_FILES[role]
    expected_bytes = 60 * 128 * 128 * 4
    if strict_regular(path, label=f"{role} raw component").st_size != expected_bytes:
        raise GateBError(f"{role} component byte size differs")
    raw = np.fromfile(path, dtype=np.dtype("<f4")).reshape((60, 128, 128), order="C")
    if not np.isfinite(raw).all() or np.any(raw < 0.0):
        raise GateBError(f"{role} component must be finite and nonnegative")
    canonical = np.ascontiguousarray(raw[:, ::-1, :], dtype=np.dtype("<f4"))
    return canonical, {"filename": path.name, **descriptor(path), "shape": [60, 128, 128], "dtype": "<f4"}


def _projection_view_metrics(array: np.ndarray, *, edge_width: int = 4) -> dict[str, Any]:
    rows = []
    view_sums = np.sum(array, axis=(1, 2), dtype=np.float64)
    for view in range(array.shape[0]):
        image = array[view]
        support = np.argwhere(image > 0.0)
        mass = float(view_sums[view])
        border = np.zeros(image.shape, dtype=bool)
        border[:edge_width, :] = True
        border[-edge_width:, :] = True
        border[:, :edge_width] = True
        border[:, -edge_width:] = True
        edge_mass = float(np.sum(image[border], dtype=np.float64))
        if support.size:
            weights = image[image > 0.0].astype(np.float64)
            centroid = np.sum(support * weights[:, None], axis=0) / float(np.sum(weights))
            bbox = [int(np.min(support[:, 0])), int(np.max(support[:, 0])), int(np.min(support[:, 1])), int(np.max(support[:, 1]))]
            touches = bbox[0] == 0 or bbox[1] == 127 or bbox[2] == 0 or bbox[3] == 127
            centroid_list = [float(value) for value in centroid]
        else:
            bbox = None
            touches = False
            centroid_list = None
        rows.append({"view": view, "mass": mass, "bbox_row_min_row_max_col_min_col_max": bbox, "centroid_row_col": centroid_list, "edge_strip_width_pixels": edge_width, "edge_strip_mass": edge_mass, "edge_strip_fraction": edge_mass / mass if mass > 0.0 else None, "touches_projection_boundary": touches})
    mean = float(np.mean(view_sums))
    return {
        "per_view": rows,
        "view_mass_minimum": float(np.min(view_sums)),
        "view_mass_maximum": float(np.max(view_sums)),
        "view_mass_maximum_over_minimum": float(np.max(view_sums) / np.min(view_sums)) if np.min(view_sums) > 0.0 else None,
        "angular_cv": float(np.std(view_sums, ddof=0) / mean) if mean > 0.0 else None,
        "any_boundary_contact": any(row["touches_projection_boundary"] for row in rows),
    }


def _gate(name: str, passed: bool, evidence: Any) -> dict[str, Any]:
    return {"name": name, "status": "passed" if passed else "failed", "evidence": evidence}


def analyze_job(*, package_root: Path, case_id: str, write: bool = True) -> dict[str, Any]:
    package = Path(package_root).resolve()
    config = load_contract(package)
    _, job_dir, job = _plan_job(package, case_id)
    run = read_json(job_dir / "run.json", label="run record")
    if run.get("schema_version") != RUN_SCHEMA or run.get("case_id") != case_id or run.get("status") != "subprocess_completed" or run.get("exit_code") != 0 or run.get("scientific_invocation_count") != 1:
        raise GateBError("analysis requires exactly one successful recorded invocation")
    expected_argv = build_packed_argv(job["exact_argv"][0], case_id, int(job["rr_seed"]), config)
    if tuple(run.get("command", [])) != expected_argv or job["exact_argv"] != list(expected_argv):
        raise GateBError("run/prepared exact argv differs")
    inventory = {record["name"]: record for record in run["output_inventory"]}
    for name, record in inventory.items():
        if descriptor(job_dir / name) != {"bytes": record["bytes"], "sha256": record["sha256"]}:
            raise GateBError(f"raw output changed after invocation: {name}")
    missing = [name for name in REQUIRED_OUTPUTS if name not in inventory]
    if missing:
        raise GateBError(f"required SIMIND output missing from run record: {missing}")
    if strict_regular(job_dir / "mc.bis", label="BIS spectrum").st_size <= 0:
        raise GateBError("BIS spectrum is empty")
    component_patterns = {path.name for path in job_dir.iterdir() if path.is_file() and re.fullmatch(r"mc_(?:tot|sca|pri|air)_w\d+\.a00", path.name)}
    if component_patterns != set(COMPONENT_FILES.values()):
        raise GateBError("Scattwin component/window inventory differs")
    components = {}
    component_records = {}
    for role in ("total", "scatter", "primary", "air"):
        components[role], component_records[role] = _load_component(job_dir, role)
    total64 = components["total"].astype(np.float64)
    scatter64 = components["scatter"].astype(np.float64)
    direct64 = components["primary"].astype(np.float64)
    p_mc_signed = total64 - scatter64
    p_tolerance = max(float(config["physics"]["p_mc_negative_absolute_floor"]), float(config["physics"]["p_mc_negative_relative_floor"]) * float(np.max(total64)))
    negative = p_mc_signed < 0.0
    material_negative = p_mc_signed < -p_tolerance
    p_mc = p_mc_signed.copy(order="C")
    p_mc[negative & ~material_negative] = 0.0
    direct_tolerance = max(float(config["physics"]["direct_primary_absolute_floor"]), float(config["physics"]["direct_primary_relative_floor"]) * float(np.max(total64)))
    direct_max_error = float(np.max(np.abs(direct64 - p_mc)))
    masses = {role: float(np.sum(array, dtype=np.float64)) for role, array in components.items()}
    p_mc_mass = float(np.sum(p_mc, dtype=np.float64))
    report_res = parse_report(job_dir / "mc.res", expected_argv=expected_argv, expected_histories=float(job["expected_histories"]), config=config)
    report_stdout = parse_report(job_dir / "simind.stdout.txt", expected_argv=expected_argv, expected_histories=float(job["expected_histories"]), config=config)
    if report_res != report_stdout:
        raise GateBError("RES and stdout semantic reports differ")
    computed_ratio = masses["scatter"] / masses["total"] if masses["total"] > 0.0 else float("nan")
    ratio_relative = abs(report_res["scatter_total"] - computed_ratio) / max(abs(computed_ratio), 1e-12)
    hct = parse_hct(job_dir / "mc.hct")
    ict_path = job_dir / "mc.ict"
    if strict_regular(ict_path, label="ICT").st_size != 128 * 128 * 128 * 4:
        raise GateBError("ICT byte size differs")
    ict = np.fromfile(ict_path, dtype=np.dtype("<f4")).reshape((128, 128, 128), order="C")
    if not np.isfinite(ict).all() or np.any(ict < 0.0):
        raise GateBError("ICT must be finite and nonnegative")
    with np.load(job_dir / "parent.npz") as payload:
        parent_mu = np.asarray(payload["mu_map"], dtype=np.float32)
    ict_max_error = float(np.max(np.abs(ict.astype(np.float64) - parent_mu.astype(np.float64))))
    total_metrics = _projection_view_metrics(components["total"])
    expectation_raw = np.ascontiguousarray(components["total"], dtype=np.dtype("<f4")).tobytes(order="C")
    target = int(job["observation"]["target_total_counts"])
    expectation_sum = masses["total"]
    scale = target / expectation_sum
    observation = np.random.default_rng(int(job["observation"]["seed"])).poisson(total64 * scale).astype(np.uint32)
    observation_raw = np.ascontiguousarray(observation, dtype=np.dtype("<u4")).tobytes(order="C")
    observation_sum = int(np.sum(observation, dtype=np.uint64))
    observation_views = np.sum(observation, axis=(1, 2), dtype=np.uint64).astype(np.float64)
    observation_cv = float(np.std(observation_views, ddof=0) / np.mean(observation_views))
    observation_error = abs(observation_sum - target) / target
    low_cv, high_cv = [float(value) for value in config["observation"]["angular_cv_range"]]
    gates = [
        _gate("parent_plan_and_argv_identity", True, {"case_id": case_id, "argv": list(expected_argv)}),
        _gate("source_native_aperture", bool(job["preflight"]["native_aperture_passed"] and job["preflight"]["native_fov_min_margin_mm"] >= 0.0), job["preflight"]),
        _gate("component_shape_dtype_finite_nonnegative", True, component_records),
        _gate("positive_required_component_masses", masses["total"] > 0.0 and masses["primary"] > 0.0 and masses["air"] > 0.0 and masses["scatter"] >= 0.0, masses),
        _gate("scale_aware_p_mc", not np.any(material_negative) and p_mc_mass > 0.0, {"tolerance": p_tolerance, "material_negative_count": int(np.count_nonzero(material_negative)), "clipped_small_negative_count": int(np.count_nonzero(negative & ~material_negative)), "clipped_small_negative_mass": float(-np.sum(p_mc_signed[negative & ~material_negative], dtype=np.float64)), "P_MC_mass": p_mc_mass}),
        _gate("direct_primary_equals_total_minus_scatter", direct_max_error <= direct_tolerance, {"maximum_absolute_error": direct_max_error, "tolerance": direct_tolerance}),
        _gate("res_scatter_total_matches_components", ratio_relative <= float(config["physics"]["res_scatter_total_relative_tolerance"]), {"computed": computed_ratio, "reported": report_res["scatter_total"], "relative_error": ratio_relative}),
        _gate("ict_mu_readback", ict_max_error <= float(config["physics"]["aligned_mu_absolute_tolerance_cm_inverse"]), {"maximum_absolute_error_cm_inverse": ict_max_error, "tolerance_cm_inverse": config["physics"]["aligned_mu_absolute_tolerance_cm_inverse"], "hct": hct}),
        _gate("observation_nonnegative_integer_total_only", observation.dtype == np.uint32, {"dtype": "<u4", "expectation_role": "total_only", "seed": job["observation"]["seed"]}),
        _gate("observation_total_count", observation_error <= float(config["observation"]["target_total_relative_error_max"]), {"target": target, "observed": observation_sum, "relative_error": observation_error}),
        _gate("observation_angular_cv", low_cv <= observation_cv <= high_cv, {"observed": observation_cv, "range": [low_cv, high_cv]}),
    ]
    hard_failed = [gate["name"] for gate in gates if gate["status"] != "passed"]
    needs_review_fov = total_metrics["any_boundary_contact"]
    conclusion = "FAIL" if hard_failed else "NEEDS_REVIEW_FOV" if needs_review_fov else "PASS"
    tumor_tnrs = [
        float(tumor["tnr_from_saved_activity"])
        for tumor in read_json(job_dir / "parent_qc.json", label="parent QC")["metrics"]["tumors"]
    ]
    result = canonicalize_analysis_numeric({
        "schema_version": ANALYSIS_SCHEMA,
        "case_id": case_id,
        "sentinel": bool(job["sentinel"]),
        "pilot_only": True,
        "conclusion": conclusion,
        "hard_gate_failures": hard_failed,
        "needs_review_fov_reason": "nonzero total support touches the projection-array boundary; no edge-mass threshold is invented" if needs_review_fov else None,
        "gates": gates,
        "command": list(expected_argv),
        "runtime": run["runtime"],
        "walltime_seconds": run["walltime_seconds"],
        "components": {role: {**component_records[role], "mass": masses[role]} for role in component_records},
        "P_MC": {"mass": p_mc_mass, "identity": "clip(total-scatter only within scale-aware tolerance)", "direct_primary_mass_relative_difference": abs(masses["primary"] - p_mc_mass) / max(p_mc_mass, 1e-12)},
        "report": report_res,
        "expectation": {"role": "total_only", "filename": "expectation_total.bin", "shape": [60, 128, 128], "dtype": "<f4", "canonical_transform": "raw[:,::-1,:]", "sum": expectation_sum, "sha256": sha256_bytes(expectation_raw)},
        "observation": {"filename": "observation_total_counts.bin", "shape": [60, 128, 128], "dtype": "<u4", "seed": job["observation"]["seed"], "target_total_counts": target, "sum": observation_sum, "target_relative_error": observation_error, "angular_cv": observation_cv, "scale": scale, "source_expectation_role": "total_only", "sha256": sha256_bytes(observation_raw)},
        "fov": {"source_preflight": job["preflight"], "total_projection": total_metrics, "edge_metrics_are_descriptive_only": True},
        "descriptive_metrics": {"scatter_total": computed_ratio, "scatter_primary": masses["scatter"] / masses["primary"], "expectation_sum": expectation_sum, "primary_mass": masses["primary"], "air_mass": masses["air"], "projection_nonzero_fraction": float(np.count_nonzero(components["total"]) / components["total"].size), "realized_tnr_range": [min(tumor_tnrs), max(tumor_tnrs)]},
    })
    if write:
        publish_or_verify(job_dir / "expectation_total.bin", expectation_raw)
        publish_or_verify(job_dir / "observation_total_counts.bin", observation_raw)
        analysis_path = job_dir / "analysis.json"
        if analysis_path.exists():
            replace_file(analysis_path, json_bytes(result))
        else:
            write_new(analysis_path, json_bytes(result))
    return result


def analyze_all(*, package_root: Path, selected_cases: set[str] | None = None, write: bool = True) -> list[dict[str, Any]]:
    package = Path(package_root).resolve()
    plan = read_json(package / "GATE_B_PLAN.json", label="plan")
    cases = plan["case_ids_in_order"]
    if selected_cases is not None:
        unknown = selected_cases.difference(cases)
        if unknown:
            raise GateBError(f"unknown analysis cases: {sorted(unknown)}")
        cases = [case for case in cases if case in selected_cases]
    return [analyze_job(package_root=package, case_id=case, write=write) for case in cases]


def finalize_report(*, package_root: Path, write: bool = True) -> dict[str, Any]:
    package = Path(package_root).resolve()
    plan = read_json(package / "GATE_B_PLAN.json", label="plan")
    analyses = []
    runs = []
    for case in plan["case_ids_in_order"]:
        _, job_dir, _ = _plan_job(package, case)
        analyses.append(read_json(job_dir / "analysis.json", label="analysis"))
        runs.append(read_json(job_dir / "run.json", label="run"))
    if len(analyses) != 10 or sum(int(row["scientific_invocation_count"]) for row in runs) != 10:
        raise GateBError("final Gate B report requires exactly ten analyses/invocations")
    if any(row["conclusion"] == "FAIL" for row in analyses):
        conclusion = "FAIL"
    elif any(row["conclusion"] == "NEEDS_REVIEW_FOV" for row in analyses):
        conclusion = "NEEDS_REVIEW_FOV"
    else:
        conclusion = "PASS"
    rows = []
    for analysis in analyses:
        rows.append({
            "case_id": analysis["case_id"],
            "sentinel": analysis["sentinel"],
            "conclusion": analysis["conclusion"],
            "walltime_seconds": analysis["walltime_seconds"],
            "total_mass": analysis["components"]["total"]["mass"],
            "scatter_mass": analysis["components"]["scatter"]["mass"],
            "primary_mass": analysis["components"]["primary"]["mass"],
            "air_mass": analysis["components"]["air"]["mass"],
            "scatter_total": analysis["descriptive_metrics"]["scatter_total"],
            "scatter_primary": analysis["descriptive_metrics"]["scatter_primary"],
            "observation_total": analysis["observation"]["sum"],
            "observation_angular_cv": analysis["observation"]["angular_cv"],
        })
    report = canonicalize_analysis_numeric({
        "schema_version": "pars_gate_b_report_v1",
        "conclusion": conclusion,
        "pilot_only": True,
        "freeze_commit": plan["freeze_commit"],
        "sentinel": plan["sentinel"],
        "case_ids_in_frozen_order": plan["case_ids_in_order"],
        "scientific_invocation_count": 10,
        "successful_exit_count": sum(row["exit_code"] == 0 for row in runs),
        "hard_gate_pass_count": sum(not row["hard_gate_failures"] for row in analyses),
        "commands": [row["command"] for row in analyses],
        "case_rows": rows,
        "unresolved_risks": ["Gate-B-N zero-lesion smoke remains required before Gate C", "central-only master tumor/activity population remains a pre-Gate-C scope decision"],
        "claim_boundary": load_contract(package)["claim_boundary"],
    })
    if write:
        report_path = package / "GATE_B_REPORT.json"
        if report_path.exists():
            replace_file(report_path, json_bytes(report))
        else:
            write_new(report_path, json_bytes(report))
        csv_buffer = io.StringIO(newline="")
        writer = csv.DictWriter(csv_buffer, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        csv_raw = csv_buffer.getvalue().encode("utf-8")
        if (package / "GATE_B_REPORT.csv").exists():
            replace_file(package / "GATE_B_REPORT.csv", csv_raw)
        else:
            write_new(package / "GATE_B_REPORT.csv", csv_raw)
        md = [
            "# Gate B hybrid Linux SIMIND pilot",
            "",
            f"- Conclusion: **{conclusion}**",
            f"- Freeze commit: `{plan['freeze_commit']}`",
            f"- Sentinel: `{plan['sentinel']}`",
            "- Scientific invocations: 10 (one sentinel, then nine unchanged cases)",
            "- Scope: positive `pilot_only`; no Formal550, E-CAL, training, validation, sealed test, or negative-control membership.",
            "",
            "| Case | Sentinel | Conclusion | Total | Scatter/Total | Observation total | Angular CV | Walltime s |",
            "|---|:---:|---|---:|---:|---:|---:|---:|",
        ]
        for row in rows:
            md.append(f"| `{row['case_id']}` | {'yes' if row['sentinel'] else ''} | {row['conclusion']} | {row['total_mass']:.6g} | {row['scatter_total']:.6g} | {row['observation_total']} | {row['observation_angular_cv']:.6f} | {row['walltime_seconds']:.2f} |")
        md.extend(["", "Scatter/Total, scatter/primary, view max/min, support, edge mass, expectation totals, primary/air mass, nonzero fraction, and realized TNR are descriptive only; no post-hoc thresholds were added.", ""])
        md_raw = "\n".join(md).encode("utf-8")
        if (package / "GATE_B_REPORT.md").exists():
            replace_file(package / "GATE_B_REPORT.md", md_raw)
        else:
            write_new(package / "GATE_B_REPORT.md", md_raw)
    return report


def package_results(*, package_root: Path, output_dir: Path, label: str) -> dict[str, Any]:
    if not SAFE_LABEL.fullmatch(label):
        raise GateBError("unsafe result package label")
    package = Path(package_root).resolve()
    final = finalize_report(package_root=package, write=True)
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"{label}_results.tar.gz"
    side_manifest = output / "RETURN_MANIFEST.json"
    sums_path = output / "SHA256SUMS"
    if any(path.exists() for path in (archive, side_manifest, sums_path)):
        raise GateBError("result package targets already exist")
    inventory = _tree_inventory(package, excluded={"RETURN_MANIFEST.json"})
    manifest = {
        "schema_version": RETURN_SCHEMA,
        "label": label,
        "conclusion": final["conclusion"],
        "scientific_invocation_count": final["scientific_invocation_count"],
        "safe_relative_paths_only": True,
        "files": inventory,
    }
    manifest_raw = json_bytes(manifest)
    write_new(package / "RETURN_MANIFEST.json", manifest_raw)
    _deterministic_tar_gz(package, archive, arc_root="gate_b_results")
    write_new(side_manifest, manifest_raw)
    sums = f"{sha256_file(archive)}  {archive.name}\n{sha256_file(side_manifest)}  {side_manifest.name}\n".encode("ascii")
    write_new(sums_path, sums)
    return {**manifest, "archive": {"name": archive.name, **descriptor(archive)}, "manifest_sha256": sha256_bytes(manifest_raw)}


def safe_extract(archive_path: Path, destination: Path, *, expected_root: str) -> Path:
    destination = Path(destination).resolve()
    if destination.exists():
        raise GateBError(f"extract destination must be fresh: {destination}")
    destination.mkdir(parents=True)
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        if not members:
            raise GateBError("archive is empty")
        for member in members:
            pure = Path(member.name)
            if pure.is_absolute() or ".." in pure.parts or not pure.parts or pure.parts[0] != expected_root:
                raise GateBError(f"unsafe archive path: {member.name}")
            if member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
                raise GateBError(f"archive link/special member forbidden: {member.name}")
            target = destination.joinpath(*pure.parts)
            try:
                target.resolve().relative_to(destination)
            except ValueError as exc:
                raise GateBError(f"archive path escapes destination: {member.name}") from exc
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise GateBError(f"cannot read archive member: {member.name}")
                write_new(target, extracted.read())
    return destination / expected_root


def verify_return_tree(root: Path, *, independent_reanalysis: bool = True) -> dict[str, Any]:
    root = Path(root).resolve()
    manifest = read_json(root / "RETURN_MANIFEST.json", label="return manifest")
    if manifest.get("schema_version") != RETURN_SCHEMA:
        raise GateBError("return manifest schema differs")
    expected = {row["path"]: row for row in manifest["files"]}
    observed = {row["path"]: row for row in _tree_inventory(root, excluded={"RETURN_MANIFEST.json"})}
    if observed != expected:
        raise GateBError("returned file inventory/SHA differs")
    reanalysis = []
    if independent_reanalysis:
        plan = read_json(root / "GATE_B_PLAN.json", label="returned plan")
        for case in plan["case_ids_in_order"]:
            computed = analyze_job(package_root=root, case_id=case, write=False)
            _, job_dir, _ = _plan_job(root, case)
            recorded = read_json(job_dir / "analysis.json", label="recorded analysis")
            if computed != recorded:
                raise GateBError(f"independent analysis differs: {case}")
            reanalysis.append(case)
        computed_report = finalize_report(package_root=root, write=False)
        recorded_report = read_json(root / "GATE_B_REPORT.json", label="recorded Gate B report")
        if computed_report != recorded_report:
            raise GateBError("independent aggregate report differs")
    return {"status": "passed", "file_count": len(expected), "independently_reanalyzed_cases": reanalysis, "conclusion": manifest["conclusion"]}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    package = sub.add_parser("package-source")
    package.add_argument("--output-dir", type=Path, required=True)
    package.add_argument("--freeze-commit", required=True)
    verify = sub.add_parser("verify-package")
    verify.add_argument("--package-root", type=Path, required=True)
    verify.add_argument("--runtime-root", type=Path)
    extract = sub.add_parser("extract-source")
    extract.add_argument("--archive", type=Path, required=True)
    extract.add_argument("--destination", type=Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("--package-root", type=Path, required=True)
    run.add_argument("--runtime-root", type=Path, required=True)
    run.add_argument("--case", required=True)
    remaining = sub.add_parser("run-remaining")
    remaining.add_argument("--package-root", type=Path, required=True)
    remaining.add_argument("--runtime-root", type=Path, required=True)
    remaining.add_argument("--max-workers", type=int, default=3)
    analyze = sub.add_parser("analyze")
    analyze.add_argument("--package-root", type=Path, required=True)
    analyze.add_argument("--case", action="append")
    finalize = sub.add_parser("finalize")
    finalize.add_argument("--package-root", type=Path, required=True)
    results = sub.add_parser("package-results")
    results.add_argument("--package-root", type=Path, required=True)
    results.add_argument("--output-dir", type=Path, required=True)
    results.add_argument("--label", required=True)
    return_verify = sub.add_parser("verify-return")
    return_verify.add_argument("--root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "package-source":
        result = build_source_package(output_dir=args.output_dir, freeze_commit=args.freeze_commit)
    elif args.command == "verify-package":
        result = verify_package_root(args.package_root)
        if args.runtime_root:
            runtime = verify_runtime(args.runtime_root, load_runtime_lock(args.package_root))
            result["runtime"] = {"binary": str(runtime.binary), "sha256": runtime.binary_sha256, "smc_count": len(runtime.records)}
    elif args.command == "extract-source":
        result = {"root": str(safe_extract(args.archive, args.destination, expected_root=PACKAGE_ROOT_NAME))}
    elif args.command == "run":
        result = run_job(package_root=args.package_root, runtime_root=args.runtime_root, case_id=args.case)
    elif args.command == "run-remaining":
        result = {"runs": run_remaining(package_root=args.package_root, runtime_root=args.runtime_root, max_workers=args.max_workers)}
    elif args.command == "analyze":
        selected = set(args.case) if args.case else None
        analyses = analyze_all(package_root=args.package_root, selected_cases=selected)
        result = {"analyses": analyses}
        if any(row["conclusion"] != "PASS" for row in analyses):
            print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
            return 2
    elif args.command == "finalize":
        result = finalize_report(package_root=args.package_root)
    elif args.command == "package-results":
        result = package_results(package_root=args.package_root, output_dir=args.output_dir, label=args.label)
    else:
        result = verify_return_tree(args.root)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
