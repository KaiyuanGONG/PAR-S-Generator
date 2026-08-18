"""Shared SIMIND command, preparation, execution, and completion contracts."""

from __future__ import annotations

import re
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
    runtime_switches: tuple[str, ...] = field(default_factory=tuple)
    primary_artifact_suffix: str = ".a00"


def build_simind_args(job: SimindJob) -> list[str]:
    return build_simind_tokens(
        smc_stem=job.smc_file.stem,
        output_stem=simind_output_argument(job.output_stem, job.working_dir),
        source_stem=job.source_stem,
        density_stem=job.density_stem,
        nn_multiplier=job.nn_multiplier,
        rr_seed=job.rr_seed,
        overrides=job.overrides,
        runtime_switches=job.runtime_switches,
    )


SIMIND_COMPONENT_SUFFIXES = tuple(f".b{index:02d}" for index in range(1, 21)) + tuple(
    f".s{index:02d}" for index in range(1, 21)
)
SIMIND_SCATTWIN_SUFFIXES = tuple(
    f"_{component}_w{window}.{extension}"
    for window in range(1, 21)
    for component in ("air", "pri", "sca", "tot")
    for extension in ("a00", "mhd")
)
SIMIND_ARTIFACT_SUFFIXES = (
    ".a00",
    ".mhd",
    ".res",
    ".spe",
    ".ict",
    ".hct",
    ".csv",
    ".bis",
    *SIMIND_COMPONENT_SUFFIXES,
    *SIMIND_SCATTWIN_SUFFIXES,
)


def artifact_path(stem: Path, suffix: str) -> Path:
    if not re.fullmatch(r"(?:\.[A-Za-z0-9]+|_[A-Za-z0-9_]+\.[A-Za-z0-9]+)", suffix):
        raise ValueError(f"Unsafe SIMIND artifact suffix: {suffix!r}")
    stem = Path(stem)
    return stem.parent / f"{stem.name}{suffix}"


def simind_output_argument(output_stem: Path | str, working_dir: Path | str) -> str:
    """Return the safe basename SIMIND must receive as its output stem.

    SIMIND V8 parses ``-X`` substrings inside an absolute path as command-line
    switches and ignores output arguments containing directory components.
    Execution therefore uses only a validated basename in ``working_dir``;
    successful artifacts are then moved to the resolved, isolated
    ``SimindJob.output_stem`` used by provenance and QC.
    """
    output = Path(output_stem).resolve()
    Path(working_dir).resolve()
    argument = output.name
    if not re.fullmatch(r"[A-Za-z0-9_]+", argument):
        raise ValueError(
            "SIMIND output argument contains characters that may be parsed as switches: "
            f"{argument!r}"
        )
    return argument


def staged_output_stem(job: SimindJob) -> Path:
    return job.working_dir.resolve() / simind_output_argument(job.output_stem, job.working_dir)


def assert_simind_artifact_paths_clear(
    staging_stem: Path,
    destination_stem: Path,
) -> None:
    """Refuse a launch that could overwrite or mix SIMIND artifacts."""
    occupied: list[Path] = []
    checked: set[Path] = set()
    for stem in (Path(staging_stem), Path(destination_stem)):
        for suffix in SIMIND_ARTIFACT_SUFFIXES:
            candidate = (stem.parent / f"{stem.name}{suffix}").resolve()
            if candidate not in checked and candidate.exists():
                occupied.append(candidate)
            checked.add(candidate)
    if occupied:
        rendered = ", ".join(str(path) for path in occupied)
        raise FileExistsError(f"Refusing to mix or overwrite SIMIND artifacts: {rendered}")


def relocate_simind_artifacts(
    staging_stem: Path,
    destination_stem: Path,
    *,
    primary_artifact_suffix: str = ".a00",
) -> list[Path]:
    """Move completed SIMIND outputs from its cwd to the isolated destination."""
    staging_stem = Path(staging_stem)
    destination_stem = Path(destination_stem)
    destination_stem.parent.mkdir(parents=True, exist_ok=True)
    pairs: list[tuple[Path, Path]] = []
    for suffix in SIMIND_ARTIFACT_SUFFIXES:
        source = staging_stem.parent / f"{staging_stem.name}{suffix}"
        destination = destination_stem.parent / f"{destination_stem.name}{suffix}"
        if source.exists():
            if destination.exists() and source.resolve() != destination.resolve():
                raise FileExistsError(f"Refusing to overwrite SIMIND artifact: {destination}")
            pairs.append((source, destination))
    required_source = artifact_path(staging_stem, primary_artifact_suffix)
    if not required_source.exists():
        raise FileNotFoundError(
            f"SIMIND did not create primary projection: {required_source}"
        )
    moved: list[Path] = []
    for source, destination in pairs:
        if source.resolve() != destination.resolve():
            shutil.move(str(source), str(destination))
        moved.append(destination)
    return moved


def expected_res_tokens(job: SimindJob) -> tuple[str, ...]:
    """Tokens that must be echoed by SIMIND's final ``.res`` command."""
    # SIMIND V8 applies a terminal /RR seed (identical-seed probes are
    # bitwise reproducible) but omits that final token from the .res command
    # echo.  Every other effective switch must be echoed.
    return tuple(
        token
        for token in build_simind_args(job)
        if token.startswith("/") and not token.startswith("/RR:")
    )


def build_simind_tokens(
    *,
    smc_stem: str,
    output_stem: str,
    source_stem: str,
    density_stem: str,
    nn_multiplier: int = 0,
    rr_seed: int | None = None,
    overrides: tuple[tuple[int, str], ...] | list[tuple[int, str]] = (),
    runtime_switches: tuple[str, ...] | list[str] = (),
) -> list[str]:
    """Single token builder used by pipeline, GUI worker and audit BAT."""
    args = [smc_stem, output_stem, f"/FS:{source_stem}", f"/FD:{density_stem}"]
    if nn_multiplier > 0:
        args.append(f"/NN:{nn_multiplier}")
    control_switches: list[str] = []
    for switch in runtime_switches:
        if not re.fullmatch(r"/[A-Za-z0-9]+(?::[-+A-Za-z0-9.,]+)?", switch):
            raise ValueError(f"Unsafe SIMIND runtime switch: {switch!r}")
        control_switches.append(switch)
    # Index-85 and some other overrides terminate parsing of subsequent
    # runtime switches in the tested Windows V8 build.  Geometry/runtime
    # switches therefore precede overrides, and /RR remains terminal.
    control_switches.extend(f"/{int(index)}:{value}" for index, value in overrides)
    # This Windows V8 executable only reliably sees a limited number of argv
    # entries, while its documented slash grammar accepts concatenated
    # switches.  Bundle controls into one argv entry so /PX and Index-85 can
    # both be effective without displacing the terminal /RR seed.
    if control_switches:
        args.append("".join(control_switches))
    # Keep /RR terminal.  This Windows V8 build stops parsing subsequent
    # switches after /RR; command-order probes are preserved with the
    # attenuation validation evidence.
    if rr_seed is not None:
        args.append(f"/RR:{rr_seed}")
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
        "output_argument": simind_output_argument(job.output_stem, job.working_dir),
        "staging_output_stem": str(staged_output_stem(job)),
        "primary_artifact_suffix": job.primary_artifact_suffix,
        "rr_seed": job.rr_seed,
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
        stage = staged_output_stem(job)
        destination = job.output_stem.resolve()
        collision_checks = []
        move_commands = []
        for suffix in SIMIND_ARTIFACT_SUFFIXES:
            source = stage.parent / f"{stage.name}{suffix}"
            target = destination.parent / f"{destination.name}{suffix}"
            collision_checks.extend(
                [
                    f'if exist "{source}" exit /b 2',
                    f'if exist "{target}" exit /b 2',
                ]
            )
            move_commands.append(
                f'if exist "{source}" move /Y "{source}" "{target}" >nul'
            )
        lines.extend(
            [
                f'pushd "{job.working_dir.resolve()}"',
                *collision_checks,
                f'"{job.simind_exe.resolve()}" {args}',
                "if errorlevel 1 exit /b 1",
                f'if not exist "{artifact_path(stage, job.primary_artifact_suffix)}" exit /b 3',
                *move_commands,
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
    projection_path = artifact_path(job.output_stem, job.primary_artifact_suffix)
    return validate_projection_artifacts(
        projection_path,
        shape=shape,
        require_mhd=True,
        expected_command_tokens=expected_res_tokens(job),
        res_path=artifact_path(job.output_stem, ".res"),
        mhd_path=projection_path.with_suffix(".mhd"),
    )


def run_job(job: SimindJob, log_path: Path, shape=DEFAULT_PROJECTION_SHAPE) -> dict:
    """Run one prepared job.  Callers must explicitly authorize this stage."""
    assert_simind_artifact_paths_clear(staged_output_stem(job), job.output_stem)
    command = [str(job.simind_exe.resolve()), *build_simind_args(job)]
    completed = subprocess.run(
        command,
        cwd=job.working_dir,
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    relocation_error: Exception | None = None
    if completed.returncode == 0:
        try:
            relocate_simind_artifacts(
                staged_output_stem(job),
                job.output_stem,
                primary_artifact_suffix=job.primary_artifact_suffix,
            )
        except (FileExistsError, FileNotFoundError, OSError) as exc:
            relocation_error = exc
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{utc_now()}] {job.case_id} exit={completed.returncode}\n")
        handle.write(completed.stdout)
        handle.write(completed.stderr)
        handle.write("\n")
    qc = completion_qc(job, shape=shape)
    qc["exit_code"] = completed.returncode
    if relocation_error is not None:
        qc["status"] = "failed"
        qc.setdefault("failures", []).append(f"artifact_relocation:{relocation_error}")
    if completed.returncode != 0:
        qc["status"] = "failed"
        qc.setdefault("failures", []).append(f"nonzero_exit:{completed.returncode}")
    return qc
