from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from webui.server import app as server_app
from webui.server import previews


def _preview(client: TestClient) -> dict:
    response = client.post(
        "/api/preview/phantom",
        json={
            "phantom_config": {
                "volume_shape": [128, 128, 128],
                "tumor_count_min": 1,
                "tumor_count_max": 1,
            },
            "case_index": 1,
            "seed": 42,
            "overrides": {"exact_tumor_count": 1},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_preview_contract_returns_geometry_digest_and_no_raw_volumes() -> None:
    previews.PREVIEW_STORE.clear()
    with TestClient(server_app.app) as client:
        payload = _preview(client)

    assert len(payload["preview_id"]) == 32
    assert len(payload["config_digest"]) == 64
    assert payload["geometry"] == {
        "shape_zyx": [128, 128, 128],
        "voxel_size_mm": 4.42,
        "origin": "voxel-center",
    }
    assert payload["summary"]["volume_shape"] == [128, 128, 128]
    assert payload["summary"]["n_tumors"] == 1
    assert "activity" not in payload
    assert "mu_map" not in payload
    assert "liver_mask" not in payload


def test_slice_mip_probe_and_mesh_are_derived_from_one_preview() -> None:
    previews.PREVIEW_STORE.clear()
    with TestClient(server_app.app) as client:
        payload = _preview(client)
        preview_id = payload["preview_id"]
        center_z, center_y, center_x = payload["summary"]["tumor_metadata"][0]["center_vox"]

        for plane in ("axial", "coronal", "sagittal"):
            slice_response = client.get(
                f"/api/preview/phantom/{preview_id}/slice",
                params={"plane": plane, "index": 64, "layer": "activity", "overlay": "contours"},
            )
            mip_response = client.get(
                f"/api/preview/phantom/{preview_id}/mip",
                params={"plane": plane, "layer": "mu", "overlay": "liver_and_tumors"},
            )
            assert slice_response.status_code == 200
            assert mip_response.status_code == 200
            assert Image.open(BytesIO(slice_response.content)).size == (384, 384)
            assert Image.open(BytesIO(mip_response.content)).size == (384, 384)

        probe_response = client.get(
            f"/api/preview/phantom/{preview_id}/probe",
            params={"x": center_x, "y": center_y, "z": center_z},
        )
        mesh_response = client.get(f"/api/preview/phantom/{preview_id}/mesh")

    assert probe_response.status_code == 200
    probe = probe_response.json()
    assert probe["voxel"] == {"x": center_x, "y": center_y, "z": center_z}
    assert probe["in_liver"] is True
    assert probe["lesion_ids"] == [1]
    assert probe["activity"] > 0
    assert probe["mu"] > 0

    assert mesh_response.status_code == 200
    mesh = mesh_response.json()
    assert mesh["coordinate_order"] == "xyz-voxel"
    assert {obj["kind"] for obj in mesh["objects"]} == {"liver", "tumor"}
    assert all(len(obj["vertices"]) % 3 == 0 for obj in mesh["objects"])
    assert all(len(obj["faces"]) % 3 == 0 for obj in mesh["objects"])


def test_preview_endpoints_reject_invalid_or_expired_requests() -> None:
    previews.PREVIEW_STORE.clear()
    with TestClient(server_app.app) as client:
        payload = _preview(client)
        preview_id = payload["preview_id"]
        bad_plane = client.get(
            f"/api/preview/phantom/{preview_id}/slice",
            params={"plane": "diagonal", "index": 64},
        )
        bad_index = client.get(
            f"/api/preview/phantom/{preview_id}/slice",
            params={"plane": "axial", "index": 999},
        )
        bad_probe = client.get(
            f"/api/preview/phantom/{preview_id}/probe",
            params={"x": -1, "y": 0, "z": 0},
        )
        expired = client.get("/api/preview/phantom/not-present/mesh")

    assert bad_plane.status_code == 422
    assert bad_index.status_code == 422
    assert bad_probe.status_code == 422
    assert expired.status_code == 404
