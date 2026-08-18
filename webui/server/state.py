"""In-memory task registry for background pipeline runs.

The web layer NEVER reimplements pipeline logic: a task is one
``PipelineRunner(config, resume=...).run_all(...)`` call in a daemon thread.
Progress is observed non-invasively by diffing ``run.json`` and artifact
counts (see watch.py) so the validated runner code stays untouched.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Task:
    task_id: str
    run_id: str
    run_root: str
    status: str = "running"          # running | paused | finished | failed
    error: str | None = None
    result: dict | None = None
    created_ts: float = field(default_factory=time.time)
    events: list[dict] = field(default_factory=list)   # append-only, cursor-read
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    runner: Any = None               # PipelineRunner (for request_pause)
    thread: threading.Thread | None = None

    def emit(self, event: dict) -> None:
        event.setdefault("ts", time.time())
        with self._lock:
            self.events.append(event)

    def read(self, cursor: int) -> tuple[list[dict], int]:
        with self._lock:
            return self.events[cursor:], len(self.events)

    def public(self) -> dict:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "run_root": self.run_root,
            "status": self.status,
            "error": self.error,
            "result": self.result,
            "created_ts": self.created_ts,
            "event_count": len(self.events),
        }


class TaskRegistry:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()

    def create(self, run_id: str, run_root: Path) -> Task:
        task = Task(task_id=uuid.uuid4().hex[:12], run_id=run_id, run_root=str(run_root))
        with self._lock:
            self._tasks[task.task_id] = task
        return task

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def all(self) -> list[dict]:
        return [t.public() for t in self._tasks.values()]

    def active_for_run(self, run_id: str) -> Task | None:
        for t in self._tasks.values():
            if t.run_id == run_id and t.status in {"running", "paused"}:
                return t
        return None


REGISTRY = TaskRegistry()
