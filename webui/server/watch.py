"""Non-invasive run observer.

Diffs ``run.json`` stage records and per-stage artifact counts on a short
interval and translates changes into the event vocabulary defined in
docs/WEB_API_CONTRACT_DRAFT.md. No pipeline code is modified or wrapped.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .state import Task

STAGE_ORDER = [
    "generate", "phantom_qc", "export", "simind_plan",
    "expectation", "projection_qc", "observation", "package", "finalize",
]

_ARTIFACT_GLOBS = {
    "generate": ("phantom", "case_*.npz"),
    "export": ("simind_input", "case_*"),
    "expectation": ("expectation", "case_*.a00"),
    "observation": ("observation", "case_*"),
}


def _stage_states(run_root: Path) -> dict:
    ledger = run_root / "run.json"
    if not ledger.is_file():
        return {}
    try:
        payload = json.loads(ledger.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    stages = payload.get("stages", {})
    out = {name: record.get("status") for name, record in stages.items()}
    if payload.get("finalized"):
        out["finalize"] = "passed"
    return out


def _artifact_counts(run_root: Path) -> dict:
    counts = {}
    for stage, (subdir, pattern) in _ARTIFACT_GLOBS.items():
        directory = run_root / subdir
        counts[stage] = len(list(directory.glob(pattern))) if directory.is_dir() else 0
    return counts


def watch_task(task: Task, total_cases: int, interval: float = 0.7) -> None:
    """Poll until the task thread finishes; emit stage/progress diffs."""
    run_root = Path(task.run_root)
    last_stages: dict = {}
    last_counts: dict = {}
    while True:
        alive = task.thread is not None and task.thread.is_alive()

        stages = _stage_states(run_root)
        for name in STAGE_ORDER:
            new = stages.get(name)
            if new and last_stages.get(name) != new:
                task.emit({"type": "stage_" + ("passed" if new in {"passed", "prepared"} else new),
                           "stage": name, "status": new})
        last_stages = stages

        counts = _artifact_counts(run_root)
        for stage, count in counts.items():
            if count != last_counts.get(stage) and count > 0:
                task.emit({"type": "progress", "stage": stage,
                           "done": count, "total": total_cases})
        last_counts = counts

        if not alive:
            task.emit({"type": "finished", "status": task.status,
                       "error": task.error, "run_root": task.run_root})
            return
        time.sleep(interval)


def start_watcher(task: Task, total_cases: int) -> threading.Thread:
    thread = threading.Thread(target=watch_task, args=(task, total_cases), daemon=True)
    thread.start()
    return thread
