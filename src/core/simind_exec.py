"""Auditable, atomic and Qt-free execution of one SIMIND V2 case."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from .simind_postprocess import (
    SimindCompletionError,
    audit_simind_completion,
    sha256_file,
)
from .smc_parser import parse_smc, validate_voxel_source_smc


SIMIND_PROTOCOL_NAME_V2 = "SPECT_60MBq_28p4s_v2"
_MAX_RR = 2_147_483_646
_CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass(frozen=True)
class SimindRunSpec:
    case_id: str
    simind_exe: Path
    smc_file: Path
    simind_ini: Path
    source_bin: Path
    density_bin: Path
    output_root: Path
    rr_seed: int
    nn_multiplier: int
    expected_shape: tuple[int, int, int] = (60, 128, 128)
    protocol_name: str = SIMIND_PROTOCOL_NAME_V2
    simind_executable_args: tuple[str, ...] = ()
    environment_overrides: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SimindRunResult:
    case_id: str
    success: bool
    exit_code: int | None
    command: tuple[str, ...]
    expected_shape: tuple[int, int, int]
    started_utc: str
    finished_utc: str
    final_dir: Path | None = None
    output_hashes: Mapping[str, str] = field(default_factory=dict)
    error: str = ""
    stdout_tail: str = ""
    stderr_tail: str = ""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_simind_command(
    *,
    executable: Path,
    smc_stem: str,
    output_stem: str,
    source_stem: str,
    density_stem: str,
    nn_multiplier: int,
    rr_seed: int,
    executable_args: tuple[str, ...] = (),
) -> tuple[str, ...]:
    if not isinstance(nn_multiplier, int) or isinstance(nn_multiplier, bool) or nn_multiplier <= 0:
        raise ValueError("nn_multiplier must be a positive integer")
    if not isinstance(rr_seed, int) or isinstance(rr_seed, bool) or not 1 <= rr_seed <= _MAX_RR:
        raise ValueError(f"rr_seed must be an integer in [1, {_MAX_RR}]")
    for name, value in (
        ("smc_stem", smc_stem),
        ("output_stem", output_stem),
        ("source_stem", source_stem),
        ("density_stem", density_stem),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
    return (
        str(Path(executable)),
        *tuple(str(item) for item in executable_args),
        smc_stem,
        output_stem,
        f"/FS:{source_stem}",
        f"/FD:{density_stem}",
        f"/NN:{nn_multiplier}",
        f"/RR:{rr_seed}",
    )


def _validate_spec(spec: SimindRunSpec) -> None:
    if not isinstance(spec, SimindRunSpec):
        raise TypeError("spec must be SimindRunSpec")
    if not _CASE_ID_RE.fullmatch(spec.case_id):
        raise ValueError("case_id contains unsafe path characters")
    if spec.protocol_name != SIMIND_PROTOCOL_NAME_V2:
        raise ValueError(f"protocol_name must be {SIMIND_PROTOCOL_NAME_V2}")
    for name in ("simind_exe", "smc_file", "simind_ini", "source_bin", "density_bin"):
        path = Path(getattr(spec, name))
        if not path.is_file():
            raise FileNotFoundError(f"{name} not found: {path}")
    if spec.source_bin.name != f"{spec.case_id}_act_av.bin":
        raise ValueError("source_bin must pair with case_id and end in _act_av.bin")
    if spec.density_bin.name != f"{spec.case_id}_atn_av.bin":
        raise ValueError("density_bin must pair with case_id and end in _atn_av.bin")
    if spec.expected_shape[0] != 60:
        raise ValueError("expected_shape must use the frozen 60-view protocol")
    build_simind_command(
        executable=spec.simind_exe,
        executable_args=spec.simind_executable_args,
        smc_stem=spec.smc_file.stem,
        output_stem=spec.case_id,
        source_stem=spec.case_id,
        density_stem=spec.case_id,
        nn_multiplier=spec.nn_multiplier,
        rr_seed=spec.rr_seed,
    )
    validate_voxel_source_smc(parse_smc(spec.smc_file))
    for key, value in spec.environment_overrides.items():
        if not isinstance(key, str) or not key or not isinstance(value, str):
            raise TypeError("environment_overrides must map non-empty strings to strings")


def _text_snapshot(path: Path) -> str:
    return Path(path).read_bytes().decode("utf-8", errors="replace")


def _tail(text: str, limit: int = 4000) -> str:
    return text[-limit:]


def _failed_result(
    spec: SimindRunSpec,
    *,
    command: tuple[str, ...],
    started: str,
    exit_code: int | None,
    error: str,
    stdout: str = "",
    stderr: str = "",
) -> SimindRunResult:
    return SimindRunResult(
        case_id=spec.case_id,
        success=False,
        exit_code=exit_code,
        command=command,
        expected_shape=spec.expected_shape,
        started_utc=started,
        finished_utc=_utc_now(),
        error=error,
        stdout_tail=_tail(stdout),
        stderr_tail=_tail(stderr),
    )


def run_simind_case(spec: SimindRunSpec) -> SimindRunResult:
    """Run one case in a temporary directory and publish only after strict audit."""
    _validate_spec(spec)
    output_root = Path(spec.output_root)
    protocol_dir = output_root / spec.protocol_name
    protocol_dir.mkdir(parents=True, exist_ok=True)
    final_dir = protocol_dir / spec.case_id
    if final_dir.exists():
        raise FileExistsError(f"formal case directory already exists: {final_dir}")
    temp_dir = protocol_dir / f".{spec.case_id}.tmp-{uuid.uuid4().hex}"
    temp_dir.mkdir(parents=False, exist_ok=False)
    started = _utc_now()
    command: tuple[str, ...] = ()
    completed: subprocess.CompletedProcess[str] | None = None
    try:
        local_smc = temp_dir / Path(spec.smc_file).name
        local_ini = temp_dir / "simind.ini"
        local_source = temp_dir / f"{spec.case_id}_act_av.bin"
        local_density = temp_dir / f"{spec.case_id}_atn_av.bin"
        shutil.copy2(spec.smc_file, local_smc)
        shutil.copy2(spec.simind_ini, local_ini)
        shutil.copy2(spec.source_bin, local_source)
        shutil.copy2(spec.density_bin, local_density)
        command = build_simind_command(
            executable=spec.simind_exe,
            executable_args=spec.simind_executable_args,
            smc_stem=local_smc.stem,
            output_stem=spec.case_id,
            source_stem=spec.case_id,
            density_stem=spec.case_id,
            nn_multiplier=spec.nn_multiplier,
            rr_seed=spec.rr_seed,
        )
        environment = os.environ.copy()
        environment.update(spec.environment_overrides)
        completed = subprocess.run(
            command,
            cwd=temp_dir,
            env=environment,
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        )
        if completed.returncode != 0:
            return _failed_result(
                spec,
                command=command,
                started=started,
                exit_code=completed.returncode,
                error=f"SIMIND exited with code {completed.returncode}",
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        try:
            audit = audit_simind_completion(
                temp_dir / spec.case_id,
                expected_shape=spec.expected_shape,
                exit_code=completed.returncode,
            )
        except SimindCompletionError as exc:
            return _failed_result(
                spec,
                command=command,
                started=started,
                exit_code=completed.returncode,
                error=str(exc),
                stdout=completed.stdout,
                stderr=completed.stderr,
            )

        argument_file_hashes = {
            value: sha256_file(Path(value))
            for value in spec.simind_executable_args
            if Path(value).is_file()
        }
        provenance = {
            "schema_version": "pars_simind_run_v2",
            "case_id": spec.case_id,
            "protocol_name": spec.protocol_name,
            "expected_shape": list(spec.expected_shape),
            "command": list(command),
            "environment_overrides": dict(spec.environment_overrides),
            "rr_seed": spec.rr_seed,
            "nn_multiplier": spec.nn_multiplier,
            "exit_code": completed.returncode,
            "started_utc": started,
            "finished_utc": _utc_now(),
            "binary_sha256": sha256_file(spec.simind_exe),
            "executable_argument_file_sha256": argument_file_hashes,
            "smc": {
                "source_name": Path(spec.smc_file).name,
                "sha256": sha256_file(spec.smc_file),
                "snapshot": _text_snapshot(spec.smc_file),
            },
            "simind_ini": {
                "source_name": Path(spec.simind_ini).name,
                "sha256": sha256_file(spec.simind_ini),
                "snapshot": _text_snapshot(spec.simind_ini),
            },
            "inputs": {
                "source_sha256": sha256_file(spec.source_bin),
                "density_sha256": sha256_file(spec.density_bin),
            },
            "completion_audit": audit.to_dict(),
            "stdout_tail": _tail(completed.stdout),
            "stderr_tail": _tail(completed.stderr),
        }
        (temp_dir / "run_provenance.json").write_text(
            json.dumps(provenance, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temp_dir, final_dir)
        return SimindRunResult(
            case_id=spec.case_id,
            success=True,
            exit_code=completed.returncode,
            command=command,
            expected_shape=spec.expected_shape,
            started_utc=started,
            finished_utc=provenance["finished_utc"],
            final_dir=final_dir,
            output_hashes=dict(audit.sha256),
            stdout_tail=_tail(completed.stdout),
            stderr_tail=_tail(completed.stderr),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _failed_result(
            spec,
            command=command,
            started=started,
            exit_code=None if completed is None else completed.returncode,
            error=f"SIMIND execution failed: {exc}",
            stdout="" if completed is None else completed.stdout,
            stderr="" if completed is None else completed.stderr,
        )
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
