from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RECORD_PATH = (
    REPO_ROOT / "docs" / "reports" / "task13_formal550_release_v1.json"
)


def test_release_record_binds_complete_archive_and_training_handoff() -> None:
    record = json.loads(RECORD_PATH.read_text(encoding="utf-8"))

    assert record["schema_version"] == (
        "pars_v2_task13_formal550_release_record_v1"
    )
    assert record["status"] == "complete"
    assert record["release_id"] == "PAR-S-V2-FORMAL550-v2.0.0-release-v1"
    assert record["archive"] == {
        "relative_path": "PAR-S-V2-FORMAL550-v2.0.0-release-v1.tar.zst",
        "format": "tar+zstd",
        "bytes": 2103357066,
        "sha256": (
            "425c70abb16433e06603878961d4134fa483b7268d68b5f008ce24c8bfa6277e"
        ),
        "member_count": 18201,
    }
    assert record["content_manifest"]["payload_file_count_excluding_manifest"] == (
        17089
    )
    assert record["campaign"]["case_count"] == 550
    assert record["campaign"]["role_case_counts"] == {
        "main": 500,
        "negative": 50,
    }
    assert record["authority"]["automatic_acceptance_status"] == "fail_preserved"
    assert record["authority"]["manual_release_status"] == "pass"
    assert record["authority"]["frozen_campaign_modified"] is False
    assert record["authority"]["go_for_training"] is True
    assert record["write_policy"] == "immutable_do_not_modify_create_new_version"


def test_release_record_preserves_exact_manifest_and_source_identities() -> None:
    record = json.loads(RECORD_PATH.read_text(encoding="utf-8"))

    assert record["campaign"]["main_manifest_sha256"] == (
        "b1fb588e0f1c6d3771a317b480704c403afc505d545b59b45dcc52f8f0bd3ffe"
    )
    assert record["campaign"]["negative_manifest_sha256"] == (
        "9dec479577633759c3ed2af838f5f2877dcfd930d81d7ff51d68f2fa43e82bc7"
    )
    assert record["source_snapshot"]["git_commit"] == (
        "166e6427af33ac516ba99701249ac170f7333178"
    )
    assert record["release_marker"]["sha256"] == (
        "0d3040c3349246bc21408ab64e08498522864d29563e3b276fde53057170c614"
    )
