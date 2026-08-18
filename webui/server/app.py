"""PAR-S local web service — FastAPI wrapper around the existing PipelineRunner.

Contract: docs/WEB_API_CONTRACT_DRAFT.md. Every action maps 1:1 onto an
existing runner/CLI verb; the frontend owns zero pipeline logic.

Launch (repo root):
    Windows:  $env:PYTHONPATH='src'; python -m uvicorn webui.server.app:app --port 8765
    or:       python webui/server/app.py  (adds src/ to sys.path itself)
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from dataclasses import asdict, is_dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from pipeline import contracts
from pipeline.contracts import RunLayout, RunLedger, atomic_write_json
from pipeline.runner import PipelineConfig, PipelinePaused, PipelineRunner

from . import fsapi, previews
from .state import REGISTRY
from .watch import STAGE_ORDER, start_watcher

app = FastAPI(title="PAR-S Generator service", version="0.1.0")
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
    return PipelineConfig(run_id="unnamed").to_dict()


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
    return path if path.is_absolute() else REPO_ROOT / path


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
            items.append({
                "run_id": payload.get("run_id", ledger_path.parent.name),
                "root": str(ledger_path.parent),
                "created_utc": payload.get("created_utc"),
                "mode": config.get("simulation_mode"),
                "case_count": config.get("phantom", {}).get("n_cases"),
                "finalized": bool(payload.get("finalized")),
                "stages": {name: record.get("status") for name, record in stages.items()},
            })
    return {"runs_root": str(runs_root), "runs": items}


def _open_run(run_root: str) -> RunLedger:
    root = Path(run_root)
    if not root.is_absolute():
        root = REPO_ROOT / root
    if not (root / "run.json").is_file():
        raise HTTPException(404, f"run.json not found under {root}")
    return RunLedger(RunLayout.open(root))


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


# ── actions ────────────────────────────────────────────────────────────────

class CreateRun(BaseModel):
    run_id: str
    runs_root: str = "runs"
    cases: int = 2
    mode: str = "prepare"
    config_overrides: dict = {}


@app.post("/api/runs")
def create_run(body: CreateRun) -> dict:
    from core.phantom_generator import PhantomConfig as _PhantomConfig
    phantom = _PhantomConfig(n_cases=body.cases, output_dir="managed_by_pipeline")
    try:
        config = PipelineConfig(
            run_id=body.run_id,
            runs_root=str(_runs_root(body.runs_root)),
            phantom=phantom,
            simulation_mode=body.mode,
            create_poisson_observation=True,
            observation_policy="empirical_total_counts",
            observation_protocol_status=contracts.EMPIRICAL_OBSERVATION_PROTOCOL_STATUS,
        )
        payload = config.to_dict()
        payload.update(body.config_overrides or {})
        config = PipelineConfig.from_dict(payload)   # re-validate after overrides
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    config_path = _runs_root(body.runs_root) / f"{body.run_id}.config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(config_path, config.to_dict())
    return {"config_path": str(config_path), "config": config.to_dict()}


class StartRun(BaseModel):
    config_path: str
    resume: bool = False
    finalize: bool = True
    allow_simind_execution: bool = False


@app.post("/api/run/start")
def start_run(body: StartRun) -> dict:
    config_path = Path(body.config_path)
    if not config_path.is_absolute():
        config_path = REPO_ROOT / config_path
    if not config_path.is_file():
        raise HTTPException(404, f"config not found: {config_path}")
    config = PipelineConfig.from_dict(json.loads(config_path.read_text(encoding="utf-8")))
    if config.simulation_mode == "execute" and not body.allow_simind_execution:
        raise HTTPException(403, "Refusing to launch SIMIND without explicit allow_simind_execution confirmation.")
    existing = REGISTRY.active_for_run(config.run_id)
    if existing:
        raise HTTPException(409, f"run already active in task {existing.task_id}")
    try:
        runner = PipelineRunner(config, resume=body.resume)
    except (RuntimeError, FileExistsError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    task = REGISTRY.create(config.run_id, runner.layout.root)
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
    phantom_config: dict = {}
    case_index: int = 1
    seed: int | None = None


@app.post("/api/preview/phantom")
async def preview_phantom(body: PreviewRequest) -> dict:
    try:
        return await asyncio.to_thread(
            previews.generate_phantom_preview, body.phantom_config, body.case_index, body.seed
        )
    except (ValueError, RuntimeError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/preview/phantom/{pid}/slice")
def preview_slice(pid: str, plane: str = "axial", index: int = 64, layer: str = "activity") -> Response:
    png = previews.phantom_slice_png(pid, plane, index, layer)
    if png is None:
        raise HTTPException(404, "preview expired — regenerate")
    return Response(png, media_type="image/png")


@app.get("/api/run/projection")
def run_projection(root: str, case: str, view: int = 0, layer: str = "expectation") -> Response:
    run_root = Path(root) if Path(root).is_absolute() else REPO_ROOT / root
    png = previews.projection_png(run_root, case, view, layer)
    if png is None:
        raise HTTPException(404, f"no {layer} projection for {case}")
    return Response(png, media_type="image/png")


@app.get("/api/run/sinogram")
def run_sinogram(root: str, case: str, row: int = 64, layer: str = "expectation") -> Response:
    run_root = Path(root) if Path(root).is_absolute() else REPO_ROOT / root
    png = previews.sinogram_png(run_root, case, row, layer)
    if png is None:
        raise HTTPException(404, f"no {layer} projection for {case}")
    return Response(png, media_type="image/png")


# ── filesystem ─────────────────────────────────────────────────────────────

@app.get("/api/fs/list")
def fs_list(path: str = Query(default="")) -> dict:
    return fsapi.list_dir(path, REPO_ROOT)


@app.get("/api/fs/validate")
def fs_validate(path: str, kind: str) -> dict:
    return fsapi.validate_path(path, kind, REPO_ROOT)


# ── static frontend (built bundle) ─────────────────────────────────────────

_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if _DIST.is_dir():
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="ui")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765)
