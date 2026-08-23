"""
SIMIND Parallel Batch Runner
=============================
Manages parallel SIMIND simulation processes with progress tracking,
resume capability, and custom parameter overrides.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from pipeline.qc import validate_projection_artifacts
from pipeline.simind import (
    assert_simind_artifact_paths_clear,
    build_simind_tokens,
    relocate_simind_artifacts,
    simind_output_argument,
)


@dataclass
class SimindBatchConfig:
    """Runtime configuration for a SIMIND batch run."""

    simind_exe: Path = field(default_factory=lambda: Path("simind.exe"))
    smc_file: Path = field(default_factory=lambda: Path("ge870_czt.smc"))
    interfile_dir: Path = field(default_factory=lambda: Path("output/interfile"))
    output_dir: Path = field(default_factory=lambda: Path("output/simind"))
    nn_multiplier: int = 10
    max_parallel: int = 3
    case_start: int = 0
    case_end: int = 99999
    skip_completed: bool = True
    custom_overrides: list[tuple[int, str]] = field(default_factory=list)


def discover_cases(interfile_dir: Path, case_start: int, case_end: int) -> list[str]:
    """Find case stems in interfile_dir matching the case range."""
    act_files = sorted(interfile_dir.glob("case_*_act_av.bin"))
    stems: list[str] = []
    for f in act_files:
        stem = f.name.replace("_act_av.bin", "")
        # Extract numeric id from stem like "case_0001"
        try:
            parts = stem.split("_")
            case_num = int(parts[-1])
        except (ValueError, IndexError):
            continue
        if case_start <= case_num <= case_end:
            atn = interfile_dir / f"{stem}_atn_av.bin"
            if atn.exists():
                stems.append(stem)
    return stems


def build_simind_command(
    smc_stem: str,
    output_stem: str,
    source_stem: str,
    density_stem: str,
    nn: int,
    overrides: list[tuple[int, str]],
) -> list[str]:
    """Build the SIMIND command line arguments."""
    return build_simind_tokens(
        smc_stem=smc_stem,
        output_stem=output_stem,
        source_stem=source_stem,
        density_stem=density_stem,
        nn_multiplier=nn,
        overrides=overrides,
    )


class SimindBatchWorker(QThread):
    """Background thread that runs SIMIND in parallel using subprocess.Popen."""

    case_started = pyqtSignal(str, int)  # case_stem, pid
    case_finished = pyqtSignal(str, int)  # case_stem, exit_code
    case_output = pyqtSignal(str, str)  # case_stem, stdout line
    progress = pyqtSignal(int, int, int, float)  # completed, total, failed, eta_seconds
    all_done = pyqtSignal(int, int, list)  # completed, failed_count, failed_stems
    log = pyqtSignal(str)

    def __init__(self, config: SimindBatchConfig, parent=None):
        super().__init__(parent)
        self._config = config
        self._stop_flag = False

    def stop(self):
        """Request graceful stop. Running processes will be killed."""
        self._stop_flag = True

    def run(self):
        cfg = self._config
        simind_exe = cfg.simind_exe.resolve()
        smc_file = cfg.smc_file.resolve()
        interfile_dir = cfg.interfile_dir.resolve()
        output_dir = cfg.output_dir.resolve()

        # Validate paths
        if not simind_exe.exists():
            self.log.emit(f"[ERROR] simind.exe not found: {simind_exe}")
            self.all_done.emit(0, 0, [])
            return
        if not smc_file.exists():
            self.log.emit(f"[ERROR] .smc file not found: {smc_file}")
            self.all_done.emit(0, 0, [])
            return
        if not interfile_dir.exists():
            self.log.emit(f"[ERROR] Interfile directory not found: {interfile_dir}")
            self.all_done.emit(0, 0, [])
            return

        output_dir.mkdir(parents=True, exist_ok=True)

        # Discover cases
        stems = discover_cases(interfile_dir, cfg.case_start, cfg.case_end)
        if not stems:
            self.log.emit("[ERROR] No matching cases found in interfile directory.")
            self.all_done.emit(0, 0, [])
            return

        # Filter already completed
        pending = []
        skipped = 0
        for stem in stems:
            if cfg.skip_completed and validate_projection_artifacts(
                output_dir / f"{stem}.a00",
                require_mhd=True,
                expected_command_tokens=(f"/FS:{stem}", f"/FD:{stem}"),
            )["status"] == "passed":
                skipped += 1
                continue
            pending.append(stem)

        total = len(pending)
        if skipped > 0:
            self.log.emit(f"[INFO] Skipped {skipped} already-completed case(s).")
        if total == 0:
            self.log.emit("[INFO] All cases already completed. Nothing to do.")
            self.all_done.emit(skipped, 0, [])
            return

        self.log.emit(
            f"[INFO] Starting batch: {total} case(s), "
            f"NN={cfg.nn_multiplier}, parallel={cfg.max_parallel}"
        )

        # Copy .smc to interfile_dir for relative access
        smc_stem = smc_file.stem
        smc_dest = interfile_dir / smc_file.name
        try:
            shutil.copy2(str(smc_file), str(smc_dest))
        except Exception as exc:
            self.log.emit(f"[ERROR] Failed to copy .smc: {exc}")
            self.all_done.emit(0, 0, [])
            return

        # Write batch log header
        log_path = output_dir / "batch_log.txt"
        start_time = time.time()
        try:
            with open(log_path, "w", encoding="utf-8") as lf:
                lf.write(f"Batch started: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                lf.write(
                    f"Config: NN={cfg.nn_multiplier}, parallel={cfg.max_parallel}, "
                    f"cases={cfg.case_start}-{cfg.case_end}, total={total}\n"
                )
                if cfg.custom_overrides:
                    ov_str = " ".join(f"/{idx}:{val}" for idx, val in cfg.custom_overrides)
                    lf.write(f"Overrides: {ov_str}\n")
        except OSError:
            pass

        # Process pool
        running: dict[str, subprocess.Popen] = {}
        completed = 0
        failed_count = 0
        failed_stems: list[str] = []
        queue = list(pending)
        queue_idx = 0

        while (queue_idx < len(queue) or running) and not self._stop_flag:
            # Launch new processes up to max_parallel
            while queue_idx < len(queue) and len(running) < cfg.max_parallel and not self._stop_flag:
                stem = queue[queue_idx]
                queue_idx += 1

                out_stem = simind_output_argument(output_dir / stem, interfile_dir)
                cmd_args = build_simind_command(
                    smc_stem=smc_stem,
                    output_stem=out_stem,
                    source_stem=stem,
                    density_stem=stem,
                    nn=cfg.nn_multiplier,
                    overrides=cfg.custom_overrides,
                )

                try:
                    assert_simind_artifact_paths_clear(
                        interfile_dir / stem,
                        output_dir / stem,
                    )
                    proc = subprocess.Popen(
                        [str(simind_exe)] + cmd_args,
                        cwd=str(interfile_dir),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        errors="replace",
                    )
                    running[stem] = proc
                    self.case_started.emit(stem, proc.pid)
                    self.log.emit(f"  START: {stem} (PID: {proc.pid})")
                except Exception as exc:
                    self.log.emit(f"  FAIL: {stem} - failed to start: {exc}")
                    failed_count += 1
                    failed_stems.append(stem)

            # Poll running processes
            done_keys: list[str] = []
            for stem, proc in running.items():
                ret = proc.poll()
                if ret is not None:
                    done_keys.append(stem)
                    completed += 1

                    # Read remaining output
                    if proc.stdout:
                        try:
                            remaining = proc.stdout.read()
                            if remaining and remaining.strip():
                                for line in remaining.strip().splitlines()[-3:]:
                                    self.case_output.emit(stem, line)
                        except Exception:
                            pass

                    relocation_error = None
                    if ret == 0:
                        try:
                            relocate_simind_artifacts(
                                interfile_dir / stem,
                                output_dir / stem,
                            )
                        except (FileExistsError, FileNotFoundError, OSError) as exc:
                            relocation_error = exc

                    artifact_qc = validate_projection_artifacts(
                        output_dir / f"{stem}.a00",
                        require_mhd=True,
                        expected_command_tokens=(f"/FS:{stem}", f"/FD:{stem}"),
                    )
                    if relocation_error is not None:
                        artifact_qc["status"] = "failed"
                        artifact_qc.setdefault("failures", []).append(
                            f"artifact_relocation:{relocation_error}"
                        )
                    a00_exists = (output_dir / f"{stem}.a00").exists()
                    if ret == 0 and artifact_qc["status"] == "passed":
                        elapsed = time.time() - start_time
                        rate = completed / elapsed if elapsed > 0 else 1
                        eta = (total - completed) / rate if rate > 0 else 0
                        self.case_finished.emit(stem, 0)
                        self.log.emit(
                            f"  OK: {stem} [{completed}/{total}] "
                            f"ETA: {eta:.0f}s"
                        )
                        self.progress.emit(completed, total, failed_count, eta)
                        self._append_log(log_path, f"{stem} OK")
                    else:
                        failed_count += 1
                        failed_stems.append(stem)
                        self.case_finished.emit(stem, ret or -1)
                        reason = ", ".join(artifact_qc["failures"]) or "unknown artifact failure"
                        self.log.emit(
                            f"  FAIL: {stem} (exit={ret}, .a00={'found' if a00_exists else 'missing'}, "
                            f"QC={reason})"
                        )
                        self.progress.emit(completed, total, failed_count, 0)
                        self._append_log(log_path, f"{stem} FAILED (exit={ret})")

            for key in done_keys:
                running.pop(key, None)

            if running:
                time.sleep(0.5)

        # Handle stop request - kill remaining
        if self._stop_flag and running:
            self.log.emit(f"[WARN] Stopping: killing {len(running)} running process(es)...")
            for stem, proc in running.items():
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass
                self.log.emit(f"  KILLED: {stem}")
            running.clear()

        # Summary
        elapsed = time.time() - start_time
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)
        self.log.emit(
            f"[{'WARN' if self._stop_flag else 'OK'}] "
            f"Batch {'stopped' if self._stop_flag else 'complete'}: "
            f"{completed}/{total} done, {failed_count} failed, "
            f"{minutes}m {seconds}s elapsed"
        )

        self._append_log(
            log_path,
            f"\nBatch finished: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Completed: {completed}/{total}, Failed: {failed_count}, "
            f"Elapsed: {minutes}m {seconds}s",
        )

        self.all_done.emit(completed, failed_count, failed_stems)

    @staticmethod
    def _append_log(path: Path, text: str) -> None:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError:
            pass
