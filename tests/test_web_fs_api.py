from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from webui.server import app as server_app


def test_native_picker_authorizes_only_the_selected_parent_for_this_session(
    tmp_path: Path,
    monkeypatch,
) -> None:
    selected = tmp_path / "含 空格" / "simind.exe"
    selected.parent.mkdir()
    selected.write_bytes(b"stub")
    monkeypatch.setattr(server_app.fsapi, "_SESSION_ROOTS", set())
    monkeypatch.setattr(
        server_app.fsapi,
        "_native_dialog",
        lambda kind, initial: str(selected),
    )

    with TestClient(server_app.app) as client:
        picked = client.post(
            "/api/fs/pick",
            json={"kind": "simind_exe", "initial_path": str(tmp_path)},
        )
        validated = client.get(
            "/api/fs/validate",
            params={"path": str(selected), "kind": "simind_exe"},
        )

    assert picked.status_code == 200, picked.text
    assert picked.json() == {"cancelled": False, "path": str(selected.resolve())}
    assert validated.status_code == 200, validated.text
    assert selected.parent.resolve() in server_app.fsapi._SESSION_ROOTS


def test_native_picker_cancel_does_not_change_session_authorization(tmp_path: Path, monkeypatch) -> None:
    prior = {tmp_path.resolve()}
    monkeypatch.setattr(server_app.fsapi, "_SESSION_ROOTS", set(prior))
    monkeypatch.setattr(server_app.fsapi, "_native_dialog", lambda kind, initial: "")

    with TestClient(server_app.app) as client:
        response = client.post(
            "/api/fs/pick",
            json={"kind": "runs_root", "initial_path": str(tmp_path)},
        )

    assert response.status_code == 200
    assert response.json() == {"cancelled": True, "path": None}
    assert server_app.fsapi._SESSION_ROOTS == prior


def test_filesystem_list_and_validate_use_typed_http_errors(tmp_path: Path, monkeypatch) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    invalid_smc = allowed / "broken.smc"
    invalid_smc.write_text("not SMCV2", encoding="ascii")
    monkeypatch.setattr(server_app.fsapi, "allowed_roots", lambda repo_root: [allowed.resolve()])

    with TestClient(server_app.app) as client:
        listed = client.get("/api/fs/list", params={"path": str(allowed)})
        forbidden = client.get("/api/fs/list", params={"path": str(outside)})
        missing = client.get("/api/fs/list", params={"path": str(allowed / "missing")})
        not_directory = client.get("/api/fs/list", params={"path": str(invalid_smc)})
        bad_kind = client.get(
            "/api/fs/validate",
            params={"path": str(invalid_smc), "kind": "unknown"},
        )
        malformed = client.get(
            "/api/fs/validate",
            params={"path": str(invalid_smc), "kind": "smc"},
        )

    assert listed.status_code == 200
    assert listed.json()["path"] == str(allowed.resolve())
    assert forbidden.status_code == 403
    assert missing.status_code == 404
    assert not_directory.status_code == 422
    assert bad_kind.status_code == 422
    assert malformed.status_code == 422


def test_create_and_start_reject_unsafe_paths_and_conflicting_ids(tmp_path: Path, monkeypatch) -> None:
    allowed = tmp_path / "allowed"
    outside = Path("C:/outside-pars-runs")
    allowed.mkdir()
    monkeypatch.setattr(
        server_app.fsapi,
        "allowed_roots",
        lambda repo_root: [repo_root.resolve(), allowed.resolve()],
    )

    with TestClient(server_app.app) as client:
        invalid_id = client.post(
            "/api/runs",
            json={"run_id": "../escape", "runs_root": str(allowed)},
        )
        forbidden_root = client.post(
            "/api/runs",
            json={"run_id": "safe-id", "runs_root": str(outside)},
        )
        created = client.post(
            "/api/runs",
            json={"run_id": "safe-id", "runs_root": str(allowed)},
        )
        duplicate = client.post(
            "/api/runs",
            json={"run_id": "safe-id", "runs_root": str(allowed)},
        )
        unsafe_start = client.post(
            "/api/run/start",
            json={"config_path": str(outside / "run.config.json")},
        )

    assert invalid_id.status_code == 422
    assert forbidden_root.status_code == 403
    assert created.status_code == 200
    assert duplicate.status_code == 409
    assert unsafe_start.status_code == 403


def test_projection_case_identifier_cannot_traverse(tmp_path: Path, monkeypatch) -> None:
    allowed = tmp_path / "allowed"
    run_root = allowed / "run"
    run_root.mkdir(parents=True)
    monkeypatch.setattr(server_app.fsapi, "allowed_roots", lambda repo_root: [allowed.resolve()])

    with TestClient(server_app.app) as client:
        response = client.get(
            "/api/run/projection",
            params={"root": str(run_root), "case": "../outside", "view": 0},
        )

    assert response.status_code == 422
