from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from webui.server import app as server_app
from pipeline import experiments


@pytest.fixture(autouse=True)
def _allow_test_root(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        server_app.fsapi,
        "allowed_roots",
        lambda repo_root: [repo_root.resolve(), tmp_path.resolve()],
    )


def _request(tmp_path: Path, **overrides) -> dict:
    return {
        "run_id": "preflight-run",
        "runs_root": str(tmp_path),
        "cases": 2,
        "mode": "prepare",
        "config_overrides": overrides,
    }


def test_preflight_parses_real_smc_without_creating_or_running_a_run(tmp_path: Path) -> None:
    with TestClient(server_app.app) as client:
        response = client.post("/api/run/preflight", json=_request(tmp_path))

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ready"] is True
    assert len(payload["config_digest"]) == 64
    assert payload["smc"]["energy_kev"] == 140.0
    assert payload["smc"]["views"] == 60
    assert payload["smc"]["raw_indices"]["25"] == 1704.0
    assert payload["provenance"]["execution_authorized"] is False
    assert not (tmp_path / "preflight-run.config.json").exists()
    assert not (tmp_path / "preflight-run").exists()


def test_preflight_reports_parse_failure_and_rejects_outside_paths(tmp_path: Path, monkeypatch) -> None:
    malformed = tmp_path / "broken.smc"
    malformed.write_text("not SMCV2", encoding="ascii")
    outside = Path("C:/outside-pars-preflight.smc")

    with TestClient(server_app.app) as client:
        malformed_response = client.post(
            "/api/run/preflight",
            json=_request(tmp_path, smc_file=str(malformed)),
        )
        monkeypatch.setattr(
            server_app.fsapi,
            "allowed_roots",
            lambda repo_root: [repo_root.resolve(), tmp_path.resolve()],
        )
        outside_response = client.post(
            "/api/run/preflight",
            json=_request(tmp_path, smc_file=str(outside)),
        )

    assert malformed_response.status_code == 200
    assert malformed_response.json()["ready"] is False
    assert "SMC cannot be parsed" in malformed_response.json()["errors"][0]
    assert outside_response.status_code == 403


def test_preflight_rejects_phantom_matrix_outside_the_validated_run_contract(tmp_path: Path) -> None:
    with TestClient(server_app.app) as client:
        response = client.post(
            "/api/run/preflight",
            json=_request(tmp_path, phantom={"volume_shape": [96, 96, 96]}),
        )

    assert response.status_code == 422
    assert "128x128x128 phantom" in response.json()["detail"]


def test_prepare_experiments_delegates_to_frozen_preparer_without_execution(tmp_path: Path, monkeypatch) -> None:
    destination = tmp_path / "experiments"
    calls: list[tuple[Path, Path, Path]] = []

    def fake_prepare(target: Path, *, simind_exe: Path, smc_file: Path) -> list[Path]:
        calls.append((target, simind_exe, smc_file))
        return [target / name for name in experiments.EXPERIMENT_NAMES]

    monkeypatch.setattr(experiments, "prepare_all_experiments", fake_prepare)

    with TestClient(server_app.app) as client:
        response = client.post(
            "/api/experiments/prepare",
            json={
                "destination": str(destination),
                "simind_exe": "simind/simind.exe",
                "smc_file": "simind/ge870_czt.smc",
            },
        )

    assert response.status_code == 200
    assert response.json()["prepared"] == 5
    assert response.json()["execution_status"] == "prepared_not_run"
    assert calls == [
        (
            destination.resolve(),
            (server_app.REPO_ROOT / "simind/simind.exe").resolve(),
            (server_app.REPO_ROOT / "simind/ge870_czt.smc").resolve(),
        )
    ]
