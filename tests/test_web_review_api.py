from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from webui.server import app as server_app


def _json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_case_evidence_exposes_backend_effective_contract_and_bounded_res_excerpt(tmp_path: Path, monkeypatch) -> None:
    run_root = tmp_path / "review-run"
    res = run_root / "expectation" / "case_0001.res"
    res.parent.mkdir(parents=True)
    res.write_text("SIMIND evidence\n/NN 10\n/RR 930001\n", encoding="utf-8")
    _json(
        run_root / "run.json",
        {
            "run_id": "review-run",
            "effective_config": {
                "projection_shape": [60, 128, 128],
                "nn_multiplier": 10,
                "detector_matrix_i": 160,
                "detector_matrix_j": 208,
                "source_activity_mbq": 60,
                "exposure_time_s_per_projection": 28.4,
                "smc_index25_activity_time": 1704,
                "type7_density_threshold_times_1000": 100,
                "phantom_cross_sections": ["h2o", "h2o"],
                "phantom": {"voxel_size_mm": 4.42},
            },
        },
    )
    case = {
        "case_id": "case_0001",
        "split": "train",
        "expectation": {"backend": "simind", "rr_seed": 930001, "res": str(res)},
    }
    (run_root / "cases.jsonl").write_text(json.dumps(case) + "\n", encoding="utf-8")
    monkeypatch.setattr(server_app.fsapi, "allowed_roots", lambda repo_root: [tmp_path.resolve()])

    with TestClient(server_app.app) as client:
        response = client.get(
            "/api/run/case-evidence",
            params={"root": str(run_root), "case": "case_0001"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["backend"] == "simind"
    assert payload["rr_seed"] == 930001
    assert payload["effective"]["detector_matrix"] == [160, 208]
    assert payload["effective"]["voxel_size_mm"] == 4.42
    assert "/RR 930001" in payload["res_excerpt"]


def test_arbitrary_a00_inspection_is_derived_and_path_safe(tmp_path: Path, monkeypatch) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    artifact = allowed / "selected.a00"
    data = np.arange(2 * 128 * 128, dtype=np.float32).reshape((2, 128, 128))
    data.tofile(artifact)
    monkeypatch.setattr(server_app.fsapi, "allowed_roots", lambda repo_root: [allowed.resolve()])

    with TestClient(server_app.app) as client:
        inspected = client.get("/api/artifact/inspect", params={"path": str(artifact)})
        projection = client.get("/api/artifact/projection", params={"path": str(artifact), "view": 1})
        sinogram = client.get("/api/artifact/sinogram", params={"path": str(artifact), "row": 64})
        forbidden = client.get("/api/artifact/inspect", params={"path": "C:/outside.a00"})
        invalid_view = client.get("/api/artifact/projection", params={"path": str(artifact), "view": 4})

    assert inspected.status_code == 200
    assert inspected.json()["shape"] == [2, 128, 128]
    assert inspected.json()["canonical_transform"] == "raw[:,::-1,:]"
    assert projection.status_code == 200
    assert Image.open(BytesIO(projection.content)).size == (384, 384)
    assert sinogram.status_code == 200
    assert Image.open(BytesIO(sinogram.content)).size == (512, 8)
    assert forbidden.status_code == 403
    assert invalid_view.status_code == 422
