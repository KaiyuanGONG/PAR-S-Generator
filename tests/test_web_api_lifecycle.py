from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from webui.server import app as server_app


@pytest.fixture(autouse=True)
def _allow_test_root(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        server_app.fsapi,
        "allowed_roots",
        lambda repo_root: [repo_root.resolve(), tmp_path.resolve()],
    )


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_start_defaults_to_not_finalize(tmp_path: Path, monkeypatch) -> None:
    run_id = f"web-start-{uuid.uuid4().hex}"
    config_path = tmp_path / f"{run_id}.config.json"
    config = server_app.PipelineConfig(run_id=run_id, runs_root=str(tmp_path))
    _write_json(config_path, config.to_dict())

    called: dict[str, bool] = {}
    finished = threading.Event()

    class FakeRunner:
        def __init__(self, parsed_config, *, resume: bool = False):
            assert parsed_config.run_id == run_id
            assert resume is False
            self.layout = SimpleNamespace(root=tmp_path / run_id)

        def run_all(self, *, finalize: bool = True) -> dict:
            called["finalize"] = finalize
            finished.set()
            return {"finalized": finalize}

    monkeypatch.setattr(server_app, "PipelineRunner", FakeRunner)
    monkeypatch.setattr(server_app, "start_watcher", lambda *args, **kwargs: None)

    with TestClient(server_app.app) as client:
        response = client.post("/api/run/start", json={"config_path": str(config_path)})

    assert response.status_code == 200
    assert finished.wait(timeout=2)
    assert called == {"finalize": False}


def test_run_summary_exposes_adjacent_config_path(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    run_root = runs_root / "recoverable-run"
    _write_json(
        run_root / "run.json",
        {
            "run_id": "recoverable-run",
            "effective_config": {"simulation_mode": "mock", "phantom": {"n_cases": 3}},
            "stages": {},
            "finalized": False,
        },
    )
    config_path = runs_root / "recoverable-run.config.json"
    _write_json(config_path, {"run_id": "recoverable-run"})

    with TestClient(server_app.app) as client:
        response = client.get("/api/runs", params={"root": str(runs_root)})

    assert response.status_code == 200
    assert response.json()["runs"][0]["config_path"] == str(config_path)


def test_create_run_persists_only_authoritative_windows_v1_controls(tmp_path: Path) -> None:
    run_id = f"web-overrides-{uuid.uuid4().hex}"
    request = {
        "run_id": run_id,
        "runs_root": str(tmp_path),
        "mode": "mock",
        "windows_v1": {
            "cohort": {"mode": "mixed", "positive_cases": 2, "negative_cases": 1},
            "lesions": {
                "tumor_count_min": 1,
                "tumor_count_max": 2,
                "size_band_weights": [2, 1, 1],
                "tnr_min": 3,
                "tnr_max": 7,
                "territory_policy": "whole_liver",
            },
            "seed": 1234,
        },
        "nn_multiplier": 7,
        "max_simind_workers": 2,
    }

    with TestClient(server_app.app) as client:
        response = client.post("/api/runs", json=request)

    assert response.status_code == 200
    payload = response.json()
    effective = payload["config"]
    assert effective["schema_version"] == "windows_v1"
    assert effective["generation_profile"] == "hybrid_v2_limited_activity_v1"
    assert effective["phantom"]["n_cases"] == 3
    assert effective["phantom"]["tumor_count_max"] == 2
    assert effective["phantom"]["tumor_probs"] == [0.5, 0.25, 0.25]
    assert effective["windows_v1"]["cohort"]["mode"] == "mixed"
    assert effective["windows_v1"]["seed"] == 1234
    assert effective["nn_multiplier"] == 7
    assert effective["max_simind_workers"] == 2
    assert effective["create_poisson_observation"] is False
    assert effective["observation_policy"] == "fixed_scale"
    assert json.loads(Path(payload["config_path"]).read_text(encoding="utf-8")) == effective


def test_new_run_rejects_legacy_or_unknown_creation_fields(tmp_path: Path) -> None:
    with TestClient(server_app.app) as client:
        response = client.post(
            "/api/runs",
            json={
                "run_id": "legacy-create",
                "runs_root": str(tmp_path),
                "cases": 2,
                "config_overrides": {"phantom": {"anatomy_model": "legacy"}},
            },
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "cases" in str(detail)
    assert "config_overrides" in str(detail)


def test_manifest_and_splits_read_run_files(tmp_path: Path) -> None:
    run_root = tmp_path / "readable-run"
    _write_json(run_root / "run.json", {"run_id": "readable-run"})
    manifest = {"dataset_id": "readable-run", "case_count": 2, "files": []}
    splits = {"seed": 42, "splits": {"train": ["case_0001"], "val": [], "test": ["case_0002"]}}
    _write_json(run_root / "dataset_manifest.json", manifest)
    _write_json(run_root / "splits.json", splits)

    with TestClient(server_app.app) as client:
        manifest_response = client.get("/api/run/manifest", params={"root": str(run_root)})
        splits_response = client.get("/api/run/splits", params={"root": str(run_root)})

    assert manifest_response.status_code == 200
    assert manifest_response.json() == manifest
    assert splits_response.status_code == 200
    assert splits_response.json() == splits


def test_manifest_rejects_run_outside_filesystem_allowlist(tmp_path: Path, monkeypatch) -> None:
    allowed_root = tmp_path / "allowed"
    outside_root = tmp_path / "outside" / "run"
    allowed_root.mkdir()
    _write_json(outside_root / "run.json", {"run_id": "outside"})
    _write_json(outside_root / "dataset_manifest.json", {"dataset_id": "outside"})
    monkeypatch.setattr(server_app.fsapi, "allowed_roots", lambda repo_root: [allowed_root.resolve()])

    with TestClient(server_app.app) as client:
        response = client.get("/api/run/manifest", params={"root": str(outside_root)})

    assert response.status_code == 403


def test_explicit_finalize_delegates_to_pipeline_runner(tmp_path: Path, monkeypatch) -> None:
    run_root = tmp_path / "finalizable-run"
    _write_json(run_root / "run.json", {"run_id": "finalizable-run"})
    opened: list[Path] = []

    class FakeOpenedRunner:
        layout = SimpleNamespace(root=run_root)

        def finalize(self) -> dict:
            _write_json(run_root / "dataset_manifest.json", {"dataset_id": "finalizable-run"})
            return {"finalized": True, "package_sha256": "abc123"}

    class FakePipelineRunner:
        @classmethod
        def open(cls, root: Path):
            opened.append(root)
            return FakeOpenedRunner()

    monkeypatch.setattr(server_app, "PipelineRunner", FakePipelineRunner)

    with TestClient(server_app.app) as client:
        response = client.post("/api/run/finalize", json={"run_root": str(run_root)})

    assert response.status_code == 200
    assert opened == [run_root.resolve()]
    assert response.json() == {
        "finalized": True,
        "package_sha256": "abc123",
        "manifest_path": str(run_root.resolve() / "dataset_manifest.json"),
    }


def test_finalize_maps_missing_invalid_and_blocked_runs(tmp_path: Path, monkeypatch) -> None:
    missing_root = tmp_path / "missing-run"
    invalid_root = tmp_path / "invalid-run"
    blocked_root = tmp_path / "blocked-run"
    _write_json(invalid_root / "run.json", {"run_id": "invalid-run"})
    _write_json(blocked_root / "run.json", {"run_id": "blocked-run"})

    class FakePipelineRunner:
        @classmethod
        def open(cls, root: Path):
            if root == invalid_root.resolve():
                raise ValueError("invalid effective configuration")
            raise RuntimeError("required stages not passed")

    monkeypatch.setattr(server_app, "PipelineRunner", FakePipelineRunner)

    with TestClient(server_app.app) as client:
        missing = client.post("/api/run/finalize", json={"run_root": str(missing_root)})
        invalid = client.post("/api/run/finalize", json={"run_root": str(invalid_root)})
        blocked = client.post("/api/run/finalize", json={"run_root": str(blocked_root)})

    assert missing.status_code == 404
    assert invalid.status_code == 422
    assert blocked.status_code == 409


def test_paused_task_allows_explicit_resume_but_running_task_still_conflicts(
    tmp_path: Path, monkeypatch
) -> None:
    run_id = f"web-resume-{uuid.uuid4().hex}"
    config_path = tmp_path / f"{run_id}.config.json"
    config = server_app.PipelineConfig(run_id=run_id, runs_root=str(tmp_path))
    _write_json(config_path, config.to_dict())
    run_root = tmp_path / run_id
    paused = server_app.REGISTRY.create(run_id, run_root)
    paused.status = "paused"
    started = threading.Event()
    release = threading.Event()

    class FakeRunner:
        def __init__(self, parsed_config, *, resume: bool = False):
            assert parsed_config.run_id == run_id
            assert resume is True
            self.layout = SimpleNamespace(root=run_root)

        def run_all(self, *, finalize: bool = True) -> dict:
            started.set()
            assert release.wait(timeout=2)
            return {"finalized": finalize}

    monkeypatch.setattr(server_app, "PipelineRunner", FakeRunner)
    monkeypatch.setattr(server_app, "start_watcher", lambda *args, **kwargs: None)

    with TestClient(server_app.app) as client:
        resumed = client.post(
            "/api/run/start",
            json={"config_path": str(config_path), "resume": True},
        )
        assert resumed.status_code == 200
        resumed_task = server_app.REGISTRY.get(resumed.json()["task_id"])
        assert resumed_task is not None
        assert started.wait(timeout=2)
        conflicted = client.post(
            "/api/run/start",
            json={"config_path": str(config_path), "resume": True},
        )
        release.set()

    assert paused.status == "finished"
    assert paused.result == {"resumed_by": resumed_task.task_id}
    assert conflicted.status_code == 409


def test_finalize_rejects_running_and_paused_tasks_before_open(
    tmp_path: Path, monkeypatch
) -> None:
    opened: list[Path] = []

    class FakePipelineRunner:
        @classmethod
        def open(cls, root: Path):
            opened.append(root)
            raise AssertionError("finalize must not open a run with active work")

    monkeypatch.setattr(server_app, "PipelineRunner", FakePipelineRunner)

    with TestClient(server_app.app) as client:
        responses = []
        for status in ("running", "paused"):
            run_id = f"finalize-{status}-{uuid.uuid4().hex}"
            run_root = tmp_path / run_id
            _write_json(run_root / "run.json", {"run_id": run_id})
            task = server_app.REGISTRY.create(run_id, run_root)
            task.status = status
            responses.append(
                client.post("/api/run/finalize", json={"run_root": str(run_root)})
            )

    assert [response.status_code for response in responses] == [409, 409]
    assert opened == []


def test_finalize_rejects_ledger_root_redirect_without_side_effect(
    tmp_path: Path, monkeypatch
) -> None:
    run_id = f"redirect-{uuid.uuid4().hex}"
    requested_root = tmp_path / "requested" / run_id
    redirected_root = tmp_path / "elsewhere" / run_id
    _write_json(requested_root / "run.json", {"run_id": run_id})
    finalized: list[bool] = []

    class FakeOpenedRunner:
        layout = SimpleNamespace(root=redirected_root)

        def finalize(self) -> dict:
            finalized.append(True)
            return {"finalized": True}

    class FakePipelineRunner:
        @classmethod
        def open(cls, root: Path):
            assert root == requested_root.resolve()
            return FakeOpenedRunner()

    monkeypatch.setattr(server_app, "PipelineRunner", FakePipelineRunner)

    with TestClient(server_app.app) as client:
        response = client.post(
            "/api/run/finalize",
            json={"run_root": str(requested_root)},
        )

    assert response.status_code == 409
    assert "different root" in response.json()["detail"]
    assert finalized == []


def test_finalize_reservation_blocks_a_concurrent_start(tmp_path: Path) -> None:
    registry = server_app.REGISTRY.__class__()
    run_id = f"finalize-lock-{uuid.uuid4().hex}"

    reserved, blocking = registry.begin_finalize(run_id)
    task, start_blocking, finalizing = registry.create_for_start(
        run_id,
        tmp_path / run_id,
        resume=True,
    )
    registry.end_finalize(run_id)

    assert reserved is True
    assert blocking is None
    assert task is None
    assert start_blocking is None
    assert finalizing is True
