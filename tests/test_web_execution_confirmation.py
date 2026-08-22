from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from core.phantom_generator import PhantomConfig
from pipeline.contracts import atomic_write_json
from pipeline.runner import PipelineConfig
from webui.server import app as server_app


def _write_execute_config(
    tmp_path: Path,
    *,
    cases: int,
    simind_exe: Path,
    smc_file: Path,
) -> Path:
    config = PipelineConfig(
        run_id=f"confirm-{cases}",
        runs_root=str(tmp_path),
        phantom=PhantomConfig(n_cases=cases),
        simulation_mode="execute",
        simind_exe=str(simind_exe),
        smc_file=str(smc_file),
    )
    path = tmp_path / f"confirm-{cases}.config.json"
    atomic_write_json(path, config.to_dict())
    return path


def test_execute_requires_separate_unverified_runtime_confirmation(tmp_path: Path, monkeypatch) -> None:
    custom_exe = tmp_path / "custom.exe"
    custom_smc = tmp_path / "custom.smc"
    custom_exe.write_bytes(b"custom exe")
    custom_smc.write_bytes(b"custom smc")
    config_path = _write_execute_config(
        tmp_path,
        cases=1,
        simind_exe=custom_exe,
        smc_file=custom_smc,
    )
    monkeypatch.setattr(
        server_app.fsapi,
        "allowed_roots",
        lambda repo_root: [repo_root.resolve(), tmp_path.resolve()],
    )

    with TestClient(server_app.app) as client:
        response = client.post(
            "/api/run/start",
            json={
                "config_path": str(config_path),
                "allow_simind_execution": True,
            },
        )

    assert response.status_code == 403
    assert "allow_unverified_runtime" in response.json()["detail"]


def test_execute_over_ten_cases_requires_separate_cost_confirmation(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_execute_config(
        tmp_path,
        cases=11,
        simind_exe=server_app.REPO_ROOT / "simind" / "simind.exe",
        smc_file=server_app.REPO_ROOT / "simind" / "ge870_czt.smc",
    )
    monkeypatch.setattr(
        server_app.fsapi,
        "allowed_roots",
        lambda repo_root: [repo_root.resolve(), tmp_path.resolve()],
    )

    with TestClient(server_app.app) as client:
        response = client.post(
            "/api/run/start",
            json={
                "config_path": str(config_path),
                "allow_simind_execution": True,
            },
        )

    assert response.status_code == 403
    assert "allow_large_simind_execution" in response.json()["detail"]
