"""PAR-S local web service — FastAPI wrapper around the existing PipelineRunner.

Contract: docs/WEB_API_CONTRACT_DRAFT.md. Every action maps 1:1 onto an
existing runner/CLI verb; the frontend owns zero pipeline logic.

Launch (repo root):
    Windows:  $env:PYTHONPATH='src'; python -m uvicorn webui.server.app:app --port 8765
    or:       python webui/server/app.py  (adds src/ to sys.path itself)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sys
import threading
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Literal

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from pipeline import contracts
from pipeline.contracts import RunLayout, RunLedger, atomic_write_json
from pipeline.runner import PipelineConfig, PipelinePaused, PipelineRunner
from core.windows_runtime import (
    WindowsPathError,
    assess_windows_runtime,
    validate_windows_path,
)
from core.windows_v1 import WindowsV1Config

try:  # normal package import (uvicorn webui.server.app:app)
    from . import fsapi, previews
    from .state import REGISTRY
    from .watch import STAGE_ORDER, start_watcher
except ImportError:  # run directly as a script: python webui/server/app.py
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from webui.server import fsapi, previews
    from webui.server.state import REGISTRY
    from webui.server.watch import STAGE_ORDER, start_watcher

app = FastAPI(title="PAR-S Generator service", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"], allow_headers=["*"],
)


def _jsonable(value):
    if is_dataclass(value):
        return asdict(value)
    return value


# ── static info ────────────────────────────────────────────────────────────

@app.get("/api/health")
def health() -> dict:
    return {"service": "par-s", "version": app.version, "repo_root": str(REPO_ROOT)}


@app.get("/api/defaults")
def defaults() -> dict:
    return PipelineConfig.for_windows_v1(
        run_id="unnamed",
        windows_v1=WindowsV1Config.from_dict(None),
    ).to_dict()


@app.get("/api/protocol")
def protocol() -> dict:
    return {
        "canonical_projection_transform": contracts.CANONICAL_PROJECTION_TRANSFORM,
        "detector_matrix": [contracts.CURRENT_DETECTOR_MATRIX_I, contracts.CURRENT_DETECTOR_MATRIX_J],
        "source_activity_mbq": contracts.DEFAULT_SOURCE_ACTIVITY_MBQ,
        "exposure_s_per_projection": contracts.DEFAULT_EXPOSURE_S_PER_PROJECTION,
        "simind_activity_time_index25": contracts.DEFAULT_SIMIND_ACTIVITY_TIME,
        "activity_time_contract_status": contracts.ACTIVITY_TIME_CONTRACT_STATUS,
        "empirical_clinical_total_counts": list(contracts.EMPIRICAL_CLINICAL_TOTAL_COUNTS),
        "empirical_clinical_angular_cv_range": list(contracts.EMPIRICAL_CLINICAL_ANGULAR_CV_RANGE),
        "stage_order": STAGE_ORDER,
        "contract_version": contracts.CONTRACT_VERSION,
    }


# ── runs as resources ──────────────────────────────────────────────────────

def _runs_root(root: str | None) -> Path:
    path = Path(root) if root else REPO_ROOT / "runs"
    resolved = (path if path.is_absolute() else REPO_ROOT / path).resolve()
    if not _allowed_path(resolved):
        raise HTTPException(403, f"runs root is outside the configured filesystem roots: {resolved}")
    try:
        validate_windows_path(resolved, "runs_root")
    except WindowsPathError as exc:
        raise HTTPException(422, str(exc)) from exc
    return resolved


def _allowed_path(path: Path) -> bool:
    resolved = path.resolve()
    for allowed_root in fsapi.allowed_roots(REPO_ROOT):
        try:
            resolved.relative_to(allowed_root)
            return True
        except ValueError:
            continue
    return False


def _run_root(root: str) -> Path:
    path = Path(root)
    resolved = (path if path.is_absolute() else REPO_ROOT / path).resolve()
    if not _allowed_path(resolved):
        raise HTTPException(403, f"run root is outside the configured filesystem roots: {resolved}")
    return resolved


@app.get("/api/runs")
def list_runs(root: str | None = None) -> dict:
    runs_root = _runs_root(root)
    items = []
    if runs_root.is_dir():
        for ledger_path in sorted(runs_root.glob("*/run.json")):
            try:
                payload = json.loads(ledger_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            stages = payload.get("stages", {})
            config = payload.get("effective_config", {})
            config_path = ledger_path.parent.parent / f"{ledger_path.parent.name}.config.json"
            items.append({
                "run_id": payload.get("run_id", ledger_path.parent.name),
                "root": str(ledger_path.parent),
                "config_path": str(config_path) if config_path.is_file() else None,
                "created_utc": payload.get("created_utc"),
                "mode": config.get("simulation_mode"),
                "case_count": config.get("phantom", {}).get("n_cases"),
                "finalized": bool(payload.get("finalized")),
                "stages": {name: record.get("status") for name, record in stages.items()},
            })
    return {"runs_root": str(runs_root), "runs": items}


def _open_run(run_root: str) -> RunLedger:
    root = _run_root(run_root)
    if not (root / "run.json").is_file():
        raise HTTPException(404, f"run.json not found under {root}")
    return RunLedger(RunLayout.open(root))


def _run_json_file(run_root: str, filename: str) -> dict:
    root = _run_root(run_root)
    if not (root / "run.json").is_file():
        raise HTTPException(404, f"run.json not found under {root}")
    path = root / filename
    if not path.is_file():
        raise HTTPException(404, f"{filename} not found under {root}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(422, f"invalid JSON in {filename}: {exc}") from exc
    except OSError as exc:
        raise HTTPException(409, f"cannot read {filename}: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(422, f"{filename} must contain a JSON object")
    return payload


@app.get("/api/run")
def run_detail(root: str) -> dict:
    ledger = _open_run(root)
    payload = ledger.load()
    payload["case_count"] = len(ledger.read_cases())
    return payload


@app.get("/api/run/cases")
def run_cases(root: str, offset: int = 0, limit: int = 200) -> dict:
    cases = _open_run(root).read_cases()
    return {"total": len(cases), "offset": offset, "cases": cases[offset:offset + limit]}


@app.get("/api/run/case-evidence")
def run_case_evidence(
    root: str,
    case: str = Query(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
) -> dict:
    run_root = _run_root(root)
    ledger = _open_run(str(run_root))
    record = next((item for item in ledger.read_cases() if item.get("case_id") == case), None)
    if record is None:
        raise HTTPException(404, f"case not found in ledger: {case}")
    run_payload = ledger.load()
    config = run_payload.get("effective_config", {})
    expectation = record.get("expectation", {}) if isinstance(record.get("expectation"), dict) else {}
    res_excerpt = None
    res_value = expectation.get("res")
    if isinstance(res_value, str) and res_value:
        res_path = Path(res_value)
        res_path = (res_path if res_path.is_absolute() else run_root / res_path).resolve()
        try:
            res_path.relative_to(run_root)
        except ValueError:
            raise HTTPException(409, f"case result path escapes the run root: {res_path}")
        if res_path.is_file():
            try:
                text = res_path.read_text(encoding="utf-8", errors="replace")
                res_excerpt = text[-16_000:]
            except OSError as exc:
                raise HTTPException(409, f"cannot read result evidence: {exc}") from exc
    phantom = config.get("phantom", {}) if isinstance(config, dict) else {}
    return {
        "case": record,
        "effective": {
            "projection_shape": config.get("projection_shape"),
            "nn_multiplier": config.get("nn_multiplier"),
            "detector_matrix": [config.get("detector_matrix_i"), config.get("detector_matrix_j")],
            "voxel_size_mm": phantom.get("voxel_size_mm") if isinstance(phantom, dict) else None,
            "source_activity_mbq": config.get("source_activity_mbq"),
            "exposure_time_s_per_projection": config.get("exposure_time_s_per_projection"),
            "smc_index25_activity_time": config.get("smc_index25_activity_time"),
            "type7_density_threshold_times_1000": config.get("type7_density_threshold_times_1000"),
            "phantom_cross_sections": config.get("phantom_cross_sections"),
        },
        "backend": expectation.get("backend"),
        "rr_seed": expectation.get("rr_seed"),
        "res_excerpt": res_excerpt,
    }


@app.get("/api/run/stages")
def run_stages(root: str) -> dict:
    payload = _open_run(root).load()
    stages = dict(payload.get("stages", {}))
    if payload.get("finalized") and "finalize" not in stages:
        # ``finalize`` is a ledger flag rather than a stage record; surface it as
        # the terminal stage so the UI rail matches the documented vocabulary.
        stages["finalize"] = {
            "status": "passed",
            "package_sha256": payload.get("package_sha256"),
            "finalized_utc": payload.get("finalized_utc"),
        }
    ordered = [{"stage": name, **stages[name]} for name in STAGE_ORDER if name in stages]
    extra = [{"stage": name, **record} for name, record in stages.items() if name not in STAGE_ORDER]
    return {"stages": ordered + extra, "finalized": bool(payload.get("finalized"))}


@app.get("/api/run/manifest")
def run_manifest(root: str) -> dict:
    return _run_json_file(root, "dataset_manifest.json")


@app.get("/api/run/splits")
def run_splits(root: str) -> dict:
    return _run_json_file(root, "splits.json")


# ── actions ────────────────────────────────────────────────────────────────

class CreateRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
    runs_root: str = "runs"
    mode: Literal["prepare", "mock", "execute"] = "prepare"
    windows_v1: dict = Field(default_factory=dict)
    simind_exe: str = "simind/simind.exe"
    smc_file: str = "simind/ge870_czt.smc"
    nn_multiplier: int = Field(default=10, ge=1, le=1_000_000, strict=True)
    max_simind_workers: int = Field(default=1, ge=1, le=32, strict=True)


def _pipeline_config_from_request(body: CreateRun) -> PipelineConfig:
    runs_root = _runs_root(body.runs_root)
    try:
        windows_v1 = WindowsV1Config.from_dict(body.windows_v1)
        config = PipelineConfig.for_windows_v1(
            run_id=body.run_id,
            runs_root=str(runs_root),
            windows_v1=windows_v1,
            simulation_mode=body.mode,
            simind_exe=body.simind_exe,
            smc_file=body.smc_file,
            nn_multiplier=body.nn_multiplier,
            max_simind_workers=body.max_simind_workers,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return config


def _config_file(path_value: str, label: str, kind: str | None = None) -> Path:
    requested = Path(path_value)
    resolved = (requested if requested.is_absolute() else REPO_ROOT / requested).resolve()
    if not _allowed_path(resolved):
        raise HTTPException(403, f"{label} is outside the configured filesystem roots: {resolved}")
    if kind is not None:
        try:
            validate_windows_path(resolved, kind, require_exists=False)
        except WindowsPathError as exc:
            raise HTTPException(422, str(exc)) from exc
    return resolved


def _validate_config_paths(config: PipelineConfig, *, require_inputs: bool) -> dict[str, Path]:
    _runs_root(config.runs_root)
    paths = {
        "simind_exe": _config_file(config.simind_exe, "SIMIND executable", "simind_exe"),
        "smc_file": _config_file(config.smc_file, "SMC file", "smc"),
        "empirical_count_evidence": _config_file(
            config.empirical_count_evidence,
            "empirical count evidence",
        ),
    }
    if config.pilot_selection_evidence:
        paths["pilot_selection_evidence"] = _config_file(
            config.pilot_selection_evidence,
            "pilot selection evidence",
        )
    if require_inputs:
        for label in ("simind_exe", "smc_file"):
            if not paths[label].is_file():
                raise HTTPException(404, f"{label} not found: {paths[label]}")
    return paths


@app.post("/api/runs")
def create_run(body: CreateRun) -> dict:
    runs_root = _runs_root(body.runs_root)
    config_path = runs_root / f"{body.run_id}.config.json"
    run_root = runs_root / body.run_id
    if config_path.exists() or run_root.exists():
        raise HTTPException(409, f"run id already exists under {runs_root}: {body.run_id}")
    config = _pipeline_config_from_request(body)
    _validate_config_paths(config, require_inputs=True)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(config_path, config.to_dict())
    return {"config_path": str(config_path), "config": config.to_dict()}


@app.post("/api/run/preflight")
def preflight_run(body: CreateRun) -> dict:
    from core.smc_parser import parse_smc

    config = _pipeline_config_from_request(body)
    paths = _validate_config_paths(config, require_inputs=False)
    checks: list[dict] = []
    errors: list[str] = []
    warnings: list[str] = []

    def check(identifier: str, passed: bool, detail: str, *, warning: bool = False) -> None:
        status = "passed" if passed else "warning" if warning else "failed"
        checks.append({"id": identifier, "status": status, "detail": detail})
        if not passed:
            (warnings if warning else errors).append(detail)

    check("simind_executable", paths["simind_exe"].is_file(), f"SIMIND executable: {paths['simind_exe']}")
    check("smc_file", paths["smc_file"].is_file(), f"SMC file: {paths['smc_file']}")
    runtime = assess_windows_runtime(paths["simind_exe"], paths["smc_file"])
    if runtime.status != "missing_runtime":
        check(
            "windows_runtime_hashes",
            runtime.validated,
            (
                "SIMIND/SMC hashes match the validated Windows v1 runtime"
                if runtime.validated
                else "Runtime hashes do not match the validated Windows v1 pair; execute requires separate confirmation"
            ),
            warning=not runtime.validated,
        )
    smc_summary = None
    if paths["smc_file"].is_file():
        try:
            smc = parse_smc(paths["smc_file"])
            cross_sections = [value.lower() for value in smc.data_files[:2]]
            check(
                "type7_source_density",
                int(round(smc.get_value(14))) == -7 and int(round(smc.get_value(15))) == -7,
                f"SMC Index-14/15 must both be -7; found {smc.get_value(14):g}/{smc.get_value(15):g}",
            )
            check(
                "phantom_interactions",
                bool(smc.get_flag(11)),
                "SMC Flag-11 phantom interactions must be enabled",
            )
            check(
                "density_sampling",
                math.isclose(smc.get_value(31), config.phantom.voxel_size_mm / 10.0, abs_tol=1e-6),
                f"SMC Index-31 must equal {config.phantom.voxel_size_mm / 10.0:g} cm",
            )
            density_shape = tuple(int(round(smc.get_value(index))) for index in (81, 82, 34))
            check(
                "density_shape",
                density_shape == tuple(int(value) for value in config.phantom.volume_shape),
                f"SMC density shape must equal {tuple(config.phantom.volume_shape)}; found {density_shape}",
            )
            check(
                "cross_sections",
                cross_sections == [value.lower() for value in config.phantom_cross_sections],
                f"SMC cross sections must be {list(config.phantom_cross_sections)}; found {cross_sections}",
            )
            check(
                "activity_time",
                math.isclose(smc.get_value(25), config.smc_index25_activity_time, rel_tol=1e-6, abs_tol=1e-3),
                f"SMC Index-25 must equal {config.smc_index25_activity_time:g}; found {smc.get_value(25):g}",
            )
            check(
                "detector_request",
                int(round(smc.get_value(100))) == config.detector_matrix_i
                and int(round(smc.get_value(101))) == config.detector_matrix_j,
                (
                    f"Raw SMC detector request is {smc.get_value(100):g}×{smc.get_value(101):g}; "
                    f"effective runtime contract is {config.detector_matrix_i}×{config.detector_matrix_j}"
                ),
                warning=True,
            )
            smc_summary = {
                "path": str(paths["smc_file"]),
                "description": smc.description,
                "energy_kev": smc.get_value(1),
                "window_kev": [smc.get_value(21), smc.get_value(20)],
                "views": int(round(smc.get_value(29))),
                "rotation_radius_cm": smc.get_value(12),
                "density_shape": [
                    int(round(smc.get_value(81))),
                    int(round(smc.get_value(82))),
                    int(round(smc.get_value(34))),
                ],
                "density_voxel_cm": smc.get_value(31),
                "detector_request": [
                    int(round(smc.get_value(100))),
                    int(round(smc.get_value(101))),
                ],
                "detector_pitch_cm": smc.get_value(95),
                "activity_time_index25": smc.get_value(25),
                "raw_indices": {
                    str(index): smc.get_value(index)
                    for index in (14, 15, 25, 26, 81, 82, 100, 101)
                },
                "enabled_flags": [index for index, enabled in enumerate(smc.flags, 1) if enabled],
            }
        except (OSError, ValueError, IndexError) as exc:
            check("smc_parse", False, f"SMC cannot be parsed: {exc}")

    canonical = config.to_dict()
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "ready": not errors,
        "config_digest": digest,
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
        "smc": smc_summary,
        "canonical_config": canonical,
        "provenance": {
            "simind_executable": str(paths["simind_exe"]),
            "smc_file": str(paths["smc_file"]),
            "mode": config.simulation_mode,
            "execution_authorized": False,
            "windows_runtime": runtime.to_dict(),
        },
    }


class PrepareExperimentsRequest(BaseModel):
    destination: str
    simind_exe: str
    smc_file: str


@app.post("/api/experiments/prepare")
def prepare_experiments(body: PrepareExperimentsRequest) -> dict:
    """Prepare the five frozen validation command packages; never execute SIMIND."""
    from pipeline.experiments import prepare_all_experiments

    destination = Path(body.destination)
    destination = (destination if destination.is_absolute() else REPO_ROOT / destination).resolve()
    if not _allowed_path(destination):
        raise HTTPException(403, f"experiment destination is outside configured roots: {destination}")
    if destination.exists() and not destination.is_dir():
        raise HTTPException(422, f"experiment destination is not a directory: {destination}")
    try:
        validate_windows_path(destination, "export_root")
    except WindowsPathError as exc:
        raise HTTPException(422, str(exc)) from exc
    simind_exe = _config_file(body.simind_exe, "SIMIND executable", "simind_exe")
    smc_file = _config_file(body.smc_file, "SMC file", "smc")
    if not simind_exe.is_file():
        raise HTTPException(404, f"SIMIND executable not found: {simind_exe}")
    if not smc_file.is_file():
        raise HTTPException(404, f"SMC file not found: {smc_file}")
    try:
        roots = prepare_all_experiments(
            destination,
            simind_exe=simind_exe,
            smc_file=smc_file,
        )
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "prepared": len(roots),
        "execution_status": "prepared_not_run",
        "roots": [str(root) for root in roots],
    }


class StartRun(BaseModel):
    config_path: str
    resume: bool = False
    finalize: bool = False
    allow_simind_execution: bool = False
    allow_unverified_runtime: bool = False
    allow_large_simind_execution: bool = False


@app.post("/api/run/start")
def start_run(body: StartRun) -> dict:
    config_path = Path(body.config_path)
    config_path = (config_path if config_path.is_absolute() else REPO_ROOT / config_path).resolve()
    if not _allowed_path(config_path):
        raise HTTPException(403, f"config is outside the configured filesystem roots: {config_path}")
    if not config_path.is_file():
        raise HTTPException(404, f"config not found: {config_path}")
    try:
        config = PipelineConfig.from_dict(json.loads(config_path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise HTTPException(422, f"invalid run config: {exc}") from exc
    _validate_config_paths(config, require_inputs=True)
    if config.simulation_mode == "execute" and not body.allow_simind_execution:
        raise HTTPException(403, "Refusing to launch SIMIND without explicit allow_simind_execution confirmation.")
    if config.simulation_mode == "execute":
        runtime = assess_windows_runtime(config.simind_exe, config.smc_file)
        if runtime.status == "unverified_runtime" and not body.allow_unverified_runtime:
            raise HTTPException(
                403,
                "Runtime is unverified; set allow_unverified_runtime only after reviewing both hashes.",
            )
        if config.phantom.n_cases > 10 and not body.allow_large_simind_execution:
            raise HTTPException(
                403,
                "More than 10 real SIMIND cases require a cost review and allow_large_simind_execution confirmation.",
            )
    existing = REGISTRY.active_for_run(config.run_id)
    if existing and (existing.status == "running" or not body.resume):
        raise HTTPException(409, f"run already active in task {existing.task_id}")
    try:
        runner = PipelineRunner(config, resume=body.resume)
    except (RuntimeError, FileExistsError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    task, blocking, finalizing = REGISTRY.create_for_start(
        config.run_id,
        runner.layout.root,
        resume=body.resume,
    )
    if task is None:
        if finalizing:
            raise HTTPException(409, f"run {config.run_id} is being finalized")
        raise HTTPException(409, f"run already active in task {blocking.task_id}")
    task.runner = runner
    should_finalize = body.finalize and config.simulation_mode != "prepare"

    def _work() -> None:
        try:
            result = runner.run_all(finalize=should_finalize)
            task.result = {"finalized": bool(result.get("finalized"))}
            task.status = "finished"
        except PipelinePaused as exc:
            task.status = "paused"
            task.error = str(exc)
        except Exception as exc:   # noqa: BLE001 — surfaced verbatim to the UI
            task.status = "failed"
            task.error = f"{type(exc).__name__}: {exc}"

    task.thread = threading.Thread(target=_work, daemon=True)
    task.thread.start()
    start_watcher(task, total_cases=config.phantom.n_cases)
    return {"task_id": task.task_id, "run_root": task.run_root}


class FinalizeRun(BaseModel):
    run_root: str


@app.post("/api/run/finalize")
def finalize_run(body: FinalizeRun) -> dict:
    root = _run_root(body.run_root)
    if not (root / "run.json").is_file():
        raise HTTPException(404, f"run.json not found under {root}")
    ledger_state = _run_json_file(str(root), "run.json")
    run_id = ledger_state.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise HTTPException(422, "run.json does not contain a valid run_id")
    reserved, blocking = REGISTRY.begin_finalize(run_id)
    if not reserved:
        if blocking is not None:
            raise HTTPException(409, f"run already active in task {blocking.task_id}")
        raise HTTPException(409, f"run {run_id} is already being finalized")
    try:
        runner = PipelineRunner.open(root)
        opened_root = Path(runner.layout.root).resolve()
        if opened_root != root:
            raise HTTPException(
                409,
                f"run ledger resolves to a different root: requested {root}, opened {opened_root}",
            )
        state = runner.finalize()
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    finally:
        REGISTRY.end_finalize(run_id)
    manifest_path = root / "dataset_manifest.json"
    if not manifest_path.is_file():
        raise HTTPException(409, "finalize completed without dataset_manifest.json")
    return {
        "finalized": bool(state.get("finalized")),
        "package_sha256": state.get("package_sha256"),
        "manifest_path": str(manifest_path),
    }


@app.post("/api/tasks/{task_id}/pause")
def pause_task(task_id: str) -> dict:
    task = REGISTRY.get(task_id)
    if task is None:
        raise HTTPException(404, "unknown task")
    if task.runner is not None:
        task.runner.request_pause()
        task.emit({"type": "log", "level": "info", "line": "pause requested — stops at next safe boundary"})
    return task.public()


@app.get("/api/tasks/{task_id}")
def task_status(task_id: str, cursor: int = 0) -> dict:
    task = REGISTRY.get(task_id)
    if task is None:
        raise HTTPException(404, "unknown task")
    events, next_cursor = task.read(cursor)
    return {**task.public(), "events": events, "cursor": next_cursor}


@app.get("/api/tasks")
def tasks() -> dict:
    return {"tasks": REGISTRY.all()}


@app.websocket("/api/ws/tasks/{task_id}")
async def task_events(ws: WebSocket, task_id: str) -> None:
    await ws.accept()
    task = REGISTRY.get(task_id)
    if task is None:
        await ws.send_json({"type": "error", "message": "unknown task"})
        await ws.close()
        return
    cursor = 0
    try:
        while True:
            events, cursor = task.read(cursor)
            for event in events:
                await ws.send_json(event)
            if events and events[-1].get("type") == "finished":
                break
            await asyncio.sleep(0.4)
    except WebSocketDisconnect:
        return
    await ws.close()


# ── previews ───────────────────────────────────────────────────────────────

class PreviewRequest(BaseModel):
    phantom_config: dict = Field(default_factory=dict)
    case_index: int = 1
    seed: int | None = None
    overrides: dict = Field(default_factory=dict)


@app.post("/api/preview/phantom")
async def preview_phantom(body: PreviewRequest) -> dict:
    try:
        return await asyncio.to_thread(
            previews.generate_phantom_preview,
            body.phantom_config,
            body.case_index,
            body.seed,
            body.overrides,
        )
    except (ValueError, RuntimeError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/preview/phantom/{pid}/slice")
def preview_slice(
    pid: str,
    plane: Literal["axial", "coronal", "sagittal"] = "axial",
    index: int = 64,
    layer: Literal["activity", "mu"] = "activity",
    overlay: Literal["liver_and_tumors", "tumors", "liver", "contours", "none"] = "liver_and_tumors",
) -> Response:
    try:
        png = previews.phantom_slice_png(pid, plane, index, layer, overlay)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if png is None:
        raise HTTPException(404, "preview expired — regenerate")
    return Response(png, media_type="image/png")


@app.get("/api/preview/phantom/{pid}/mip")
def preview_mip(
    pid: str,
    plane: Literal["axial", "coronal", "sagittal"] = "axial",
    layer: Literal["activity", "mu"] = "activity",
    overlay: Literal["liver_and_tumors", "tumors", "liver", "contours", "none"] = "liver_and_tumors",
) -> Response:
    png = previews.phantom_mip_png(pid, plane, layer, overlay)
    if png is None:
        raise HTTPException(404, "preview expired — regenerate")
    return Response(png, media_type="image/png")


@app.get("/api/preview/phantom/{pid}/probe")
def preview_probe(pid: str, x: int, y: int, z: int) -> dict:
    try:
        payload = previews.phantom_probe(pid, x, y, z)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if payload is None:
        raise HTTPException(404, "preview expired — regenerate")
    return payload


@app.get("/api/preview/phantom/{pid}/mesh")
def preview_mesh(
    pid: str,
    structure: Literal["all", "liver", "tumors"] = "all",
) -> dict:
    payload = previews.phantom_mesh(pid, structure)
    if payload is None:
        raise HTTPException(404, "preview expired — regenerate")
    return payload


@app.get("/api/run/projection")
def run_projection(
    root: str,
    case: str = Query(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
    view: int = 0,
    layer: Literal["expectation", "observation"] = "expectation",
) -> Response:
    run_root = _run_root(root)
    png = previews.projection_png(run_root, case, view, layer)
    if png is None:
        raise HTTPException(404, f"no {layer} projection for {case}")
    return Response(png, media_type="image/png")


@app.get("/api/run/sinogram")
def run_sinogram(
    root: str,
    case: str = Query(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
    row: int = 64,
    layer: Literal["expectation", "observation"] = "expectation",
) -> Response:
    run_root = _run_root(root)
    png = previews.sinogram_png(run_root, case, row, layer)
    if png is None:
        raise HTTPException(404, f"no {layer} projection for {case}")
    return Response(png, media_type="image/png")


def _artifact_path(path: str) -> Path:
    resolved = _config_file(path, "artifact")
    if not resolved.is_file():
        raise HTTPException(404, f"artifact not found: {resolved}")
    if resolved.suffix.lower() != ".a00":
        raise HTTPException(422, f"only .a00 projection artifacts can be inspected: {resolved}")
    return resolved


@app.get("/api/artifact/inspect")
def inspect_artifact(path: str) -> dict:
    try:
        return previews.artifact_summary(_artifact_path(path))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/artifact/projection")
def artifact_projection(path: str, view: int = 0) -> Response:
    try:
        png = previews.artifact_projection_png(_artifact_path(path), view)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(409, str(exc)) from exc
    return Response(png, media_type="image/png")


@app.get("/api/artifact/sinogram")
def artifact_sinogram(path: str, row: int = 64) -> Response:
    try:
        png = previews.artifact_sinogram_png(_artifact_path(path), row)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(409, str(exc)) from exc
    return Response(png, media_type="image/png")


# ── filesystem ─────────────────────────────────────────────────────────────

@app.get("/api/fs/list")
def fs_list(path: str = Query(default="")) -> dict:
    payload = fsapi.list_dir(path, REPO_ROOT)
    error = payload.get("error")
    if error == "outside_allowed_roots":
        raise HTTPException(403, payload)
    if error == "not_a_directory":
        target = Path(payload.get("path", ""))
        raise HTTPException(404 if not target.exists() else 422, payload)
    if error:
        raise HTTPException(409, payload)
    return payload


@app.get("/api/fs/validate")
def fs_validate(path: str, kind: str) -> dict:
    payload = fsapi.validate_path(path, kind, REPO_ROOT)
    error = payload.get("error")
    if error == "outside_allowed_roots":
        raise HTTPException(403, payload)
    if error == "unsupported_kind":
        raise HTTPException(422, payload)
    if not payload.get("valid"):
        target = Path(payload["path"])
        raise HTTPException(404 if not target.exists() else 422, payload)
    return payload


class NativePathRequest(BaseModel):
    kind: Literal["simind_exe", "smc", "runs_root", "export_root"]
    initial_path: str = ""


@app.post("/api/fs/pick")
def fs_pick(body: NativePathRequest) -> dict:
    payload = fsapi.pick_native_path(body.kind, body.initial_path, REPO_ROOT)
    error = payload.get("error")
    if error == "unsupported_kind":
        raise HTTPException(422, payload)
    if error:
        raise HTTPException(422, payload)
    return payload


# ── static frontend (built bundle) ─────────────────────────────────────────

_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if _DIST.is_dir():
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="ui")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765)
