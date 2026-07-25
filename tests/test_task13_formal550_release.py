from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import build_task13_formal550_release as release  # noqa: E402


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_sidecar(path: Path) -> None:
    path.with_name(f"{path.name}.sha256").write_text(
        f"{_sha256(path)}  {path.name}\n",
        encoding="utf-8",
    )


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    campaign_root = tmp_path / "campaign"
    qa_root = tmp_path / "qa"
    external_root = tmp_path / "external"
    input_archive = tmp_path / "input" / "task13_formal550_results.tar.gz"
    input_archive.parent.mkdir()
    input_archive.write_bytes(b"upstream")
    _write_sidecar(input_archive)

    role_hashes = {}
    for role, count, split_counts in (
        ("main", 500, release.EXPECTED_MAIN_SPLITS),
        ("negative", 50, release.EXPECTED_NEGATIVE_SPLITS),
    ):
        root = campaign_root / role
        cases = root / "cases"
        cases.mkdir(parents=True)
        rows = []
        for index in range(count):
            case_id = f"{'case' if role == 'main' else 'negative'}_{index:05d}"
            (cases / case_id).mkdir()
            rows.append({"case_id": case_id, "split": "test"})
        manifest = root / "case_manifest.jsonl"
        manifest.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )
        role_hashes[role] = _sha256(manifest)
        _write_json(
            root / "DATASET_COMPLETE.json",
            {
                "schema_version": release.DATASET_SCHEMA,
                "status": "complete",
                "case_count": count,
                "split_counts": split_counts,
                "manifest_sha256": role_hashes[role],
            },
        )

    campaign_path = campaign_root / "FORMAL550_COMPLETE.json"
    _write_json(
        campaign_path,
        {
            "schema_version": release.CAMPAIGN_SCHEMA,
            "status": "complete",
            "case_count": 550,
            "role_case_counts": release.EXPECTED_ROLE_COUNTS,
            "datasets": {
                role: {
                    "relative_root": role,
                    "manifest_sha256": role_hashes[role],
                }
                for role in ("main", "negative")
            },
        },
    )

    gate_specs = {
        "formal550_generator_gate_v1": ("fail", qa_root / "generator_gate.json"),
        "formal550_main_loader_gate_v1": ("pass", qa_root / "main_loader_gate.json"),
        "formal550_negative_loader_gate_v1": (
            "pass",
            qa_root / "negative_loader_gate.json",
        ),
        "projection_coordinate_gate_v2": (
            "pass",
            external_root / "coordinate.json",
        ),
    }
    gate_rows = []
    for gate_id, (status, path) in gate_specs.items():
        _write_json(path, {"gate_id": gate_id, "status": status})
        gate_rows.append(
            {
                "gate_id": gate_id,
                "status": status,
                "path": str(path),
                "sha256": _sha256(path),
            }
        )

    task12g_bindings = {}
    for key in ("automatic_acceptance", "coordinate_report", "release"):
        path = external_root / f"task12g_{key}.json"
        _write_json(path, {"key": key, "status": "pass"})
        task12g_bindings[key] = {"path": str(path), "sha256": _sha256(path)}
    automatic_path = qa_root / "TASK13_FORMAL550_AUTOMATIC_ACCEPTANCE.json"
    _write_json(
        automatic_path,
        {
            "schema_version": release.AUTOMATIC_SCHEMA,
            "status": "fail",
            "automatic_gate_passed": False,
            "case_count": 550,
            "role_case_counts": release.EXPECTED_ROLE_COUNTS,
            "gate_rows": gate_rows,
            "task12g_release_chain": {
                "schema_version": "pars_v2_task12g_release_chain_v1",
                "status": "pass",
                **task12g_bindings,
            },
        },
    )
    manual_path = qa_root / "TASK13_FORMAL550_MANUAL_ACCEPTANCE.json"
    _write_json(
        manual_path,
        {
            "schema_version": release.MANUAL_SCHEMA,
            "status": "pass",
            "campaign": {
                "campaign_complete_sha256": _sha256(campaign_path),
                "main_manifest_sha256": role_hashes["main"],
                "negative_manifest_sha256": role_hashes["negative"],
            },
            "automatic_acceptance_v1": {
                "status": "fail",
                "automatic_gate_passed": False,
                "sha256": _sha256(automatic_path),
                "preserved_unchanged": True,
            },
            "manual_review": {
                "waived_blocking_gate_ids": ["view_sum_ratio_at_most_80"],
                "other_failed_gate_ids": [],
                "dataset_bytes_modified": False,
                "cases_removed": False,
            },
            "release": {
                "status": "pass",
                "go_for_training": True,
                "accepted_case_count": 550,
                "role_case_counts": release.EXPECTED_ROLE_COUNTS,
            },
        },
    )
    _write_sidecar(manual_path)
    return campaign_root, qa_root, input_archive


def test_validate_authority_accepts_versioned_manual_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_root, qa_root, input_archive = _fixture(tmp_path)
    monkeypatch.setattr(
        release,
        "EXPECTED_INPUT_ARCHIVE_SHA256",
        _sha256(input_archive),
    )

    snapshot = release.validate_authority(campaign_root, qa_root, input_archive)

    assert snapshot.campaign["case_count"] == 550
    assert snapshot.automatic["automatic_gate_passed"] is False
    assert snapshot.manual["release"]["go_for_training"] is True
    assert snapshot.critical_hashes["main_manifest"] == _sha256(
        campaign_root / "main" / "case_manifest.jsonl"
    )


def test_validate_authority_rejects_other_failed_gates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_root, qa_root, input_archive = _fixture(tmp_path)
    monkeypatch.setattr(
        release,
        "EXPECTED_INPUT_ARCHIVE_SHA256",
        _sha256(input_archive),
    )
    manual_path = qa_root / "TASK13_FORMAL550_MANUAL_ACCEPTANCE.json"
    manual = json.loads(manual_path.read_text(encoding="utf-8"))
    manual["manual_review"]["other_failed_gate_ids"] = ["unexpected_gate"]
    _write_json(manual_path, manual)
    _write_sidecar(manual_path)

    with pytest.raises(release.Formal550ReleaseError, match="other failed gates"):
        release.validate_authority(campaign_root, qa_root, input_archive)


def test_validate_authority_rejects_manifest_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_root, qa_root, input_archive = _fixture(tmp_path)
    monkeypatch.setattr(
        release,
        "EXPECTED_INPUT_ARCHIVE_SHA256",
        _sha256(input_archive),
    )
    with (campaign_root / "main" / "case_manifest.jsonl").open(
        "a",
        encoding="utf-8",
    ) as stream:
        stream.write("{}\n")

    with pytest.raises(
        release.Formal550ReleaseError,
        match="main marker/manifest hash mismatch",
    ):
        release.validate_authority(campaign_root, qa_root, input_archive)


def test_inventory_round_trip_detects_byte_tamper(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    (payload / "dataset").mkdir(parents=True)
    first = payload / "dataset" / "a.bin"
    second = payload / "evidence.json"
    first.write_bytes(b"abc")
    second.write_text("{}\n", encoding="utf-8")
    manifest = payload / release.CONTENT_MANIFEST_NAME
    release.write_inventory(
        manifest,
        release.inventory_rows(
            payload,
            excluded_relative_paths=(release.CONTENT_MANIFEST_NAME,),
        ),
    )

    assert release.verify_inventory(payload, manifest) == 2
    first.write_bytes(b"abd")
    with pytest.raises(release.Formal550ReleaseError, match="SHA mismatch"):
        release.verify_inventory(payload, manifest)


def test_verify_existing_release_is_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "release"
    root.mkdir()
    archive = root / release.ARCHIVE_NAME
    archive.write_bytes(b"archive")
    _write_sidecar(archive)
    content = root / release.CONTENT_MANIFEST_NAME
    content.write_text("{}\n", encoding="utf-8")
    marker_path = root / release.RELEASE_MARKER_NAME
    _write_json(
        marker_path,
        {
            "schema_version": release.RELEASE_SCHEMA,
            "status": "complete",
            "go_for_training": True,
            "case_count": 550,
            "archive": {
                "name": archive.name,
                "sha256": _sha256(archive),
            },
            "content_manifest": {"sha256": _sha256(content)},
        },
    )
    _write_sidecar(marker_path)
    assert release.verify_existing_release(root)["case_count"] == 550

    archive.write_bytes(b"tampered")
    with pytest.raises(
        release.Formal550ReleaseError,
        match="release archive SHA-256 mismatch",
    ):
        release.verify_existing_release(root)
