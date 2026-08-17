"""Shared SIMIND command, preparation, execution, and completion contracts."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.contracts import atomic_write_json, atomic_write_text, sha256_file, utc_now
from pipeline.qc import DEFAULT_PROJECTION_SHAPE, validate_projection_artifacts


@dataclass(frozen=True)
class SimindJob:
    case_id: str
    simind_exe: Path
    smc_file: Path
    working_dir: Path
    output_stem: Path
    source_stem: str
    density_stem: str
    nn_multiplier: int = 0
    rr_seed: int | None = None
    overrides: tuple[tuple[int, str], ...] = field(default_factory=tuple)


def build_simind_args(job: SimindJob) -> list[str]:
    return build_simind_tokens(
        smc_stem=job.smc_file.stem,
        output_stem=str(job.output_stem),
        source_stem=job.source_stem,
        density_stem=job.density_stem,
        nn_multiplier=job.nn_multiplier,
        rr_seed=job.rr_seed,
        overrides=job.overrides,
    )


def expected_res_tokens(job: SimindJob) -> tuple[str, ...]:
    """Tokens that must be echoed by SIMIND's final ``.res`` command."""
    return tuple(token for token in build_simind_args(job) if token.startswith("/"))


def build_simind_tokens(
    *,
    smc_stem: str,
    output_stem: str,
    source_stem: str,
    density_stem: str,
    nn_multiplier: int = 0,
    rr_seed: int | None = None,
    overrides: tuple[tuple[int, str], ...] | list[tuple[int, str]] = (),
) -> list[str]:
    """Single token builder used by pipeline, GUI worker and audit BAT."""
    args = [smc_stem, output_stem, f"/FS:{source_stem}", f"/FD:{density_stem}"]
    if nn_multiplier > 0:
        args.append(f"/NN:{nn_multiplier}")
    if rr_seed is not None:
        args.append(f"/RR:{rr_seed}")
    args.extend(f"/{int(index)}:{value}" for index, value in overrides)
    return args


def job_record(job: SimindJob) -> dict:
    return {
        "case_id": job.case_id,
        "executable": str(job.simind_exe.resolve()),
        "executable_sha256": sha256_file(job.simind_exe) if job.simind_exe.exists() else None,
        "smc": str(job.smc_file.resolve()),
        "smc_sha256": sha256_file(job.smc_file) if job.smc_file.exists() else None,
        "working_dir": str(job.working_dir.resolve()),
        "output_stem": str(job.output_stem.resolve()),
        "args": build_simind_args(job),
        "prepared_utc": utc_now(),
    }


def render_batch_script(jobs: list[SimindJob]) -> str:
    if not jobs:
        raise ValueError("Cannot render an empty SIMIND job list")
    lines = [
        "@echo off",
        "setlocal",
        "echo PAR-S synthetic-data SIMIND job plan",
        "echo Commands below are generated from the same contract as the GUI and CLI.",
        "",
    ]
    for job in jobs:
        args = " ".join(f'"{value}"' if " " in value else value for value in build_simind_args(job))
        lines.extend(
            [
                f'pushd "{job.working_dir.resolve()}"',
                f'"{job.simind_exe.resolve()}" {args}',
                "if errorlevel 1 exit /b 1",
                "popd",
                "",
            ]
        )
    lines.extend(["echo All prepared jobs returned exit code 0.", "endlocal", ""])
    return "\r\n".join(lines)


def prepare_jobs(jobs: list[SimindJob], plan_dir: Path) -> Path:
    """Write an auditable plan and script without launching SIMIND."""
    plan_dir = Path(plan_dir)
    plan_dir.mkdir(parents=True, exist_ok=True)
    for job in jobs:
        if not job.simind_exe.exists():
            raise FileNotFoundError(job.simind_exe)
        if not job.smc_file.exists():
            raise FileNotFoundError(job.smc_file)
        job.working_dir.mkdir(parents=True, exist_ok=True)
        destination = job.working_dir / job.smc_file.name
        if destination.resolve() != job.smc_file.resolve():
            shutil.copy2(job.smc_file, destination)
    atomic_write_json(plan_dir / "simind_jobs.json", [job_record(job) for job in jobs])
    atomic_write_text(plan_dir / "run_simind.bat", render_batch_script(jobs), encoding="ascii")
    return plan_dir / "simind_jobs.json"


def completion_qc(job: SimindJob, shape=DEFAULT_PROJECTION_SHAPE) -> dict:
    return validate_projection_artifacts(
        job.output_stem.with_suffix(".a00"),
        shape=shape,
        require_mhd=True,
        expected_command_tokens=expected_res_tokens(job),
    )


def run_job(job: SimindJob, log_path: Path, shape=DEFAULT_PROJECTION_SHAPE) -> dict:
    """Run one prepared job.  Callers must explicitly authorize this stage."""
    command = [str(job.simind_exe.resolve()), *build_simind_args(job)]
    completed = subprocess.run(
        command,
        cwd=job.working_dir,
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{utc_now()}] {job.case_id} exit={completed.returncode}\n")
        handle.write(completed.stdout)
        handle.write(completed.stderr)
        handle.write("\n")
    qc = completion_qc(job, shape=shape)
    qc["exit_code"] = completed.returncode
    if completed.returncode != 0:
        qc["status"] = "failed"
        qc.setdefault("failures", []).append(f"nonzero_exit:{completed.returncode}")
    return qc
