from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = (
    REPO_ROOT / "docs" / "reports" / "task13_formal550_manual_acceptance.json"
)


def test_manual_acceptance_preserves_automatic_fail_and_releases_training() -> None:
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))

    assert report["schema_version"] == (
        "pars_v2_task13_formal550_manual_acceptance_v1"
    )
    assert report["status"] == "pass"
    assert report["campaign"] == {
        "dataset_id": "PAR-S-V2-FORMAL550",
        "dataset_version": "2.0.0",
        "case_count": 550,
        "campaign_complete_sha256": (
            "7e11de03c82455565574d785c6ec6a9cb2b2d2e05ccc33b3e763d77c5e4e5fd4"
        ),
        "main_manifest_sha256": (
            "b1fb588e0f1c6d3771a317b480704c403afc505d545b59b45dcc52f8f0bd3ffe"
        ),
        "negative_manifest_sha256": (
            "9dec479577633759c3ed2af838f5f2877dcfd930d81d7ff51d68f2fa43e82bc7"
        ),
    }

    automatic = report["automatic_acceptance_v1"]
    assert automatic["status"] == "fail"
    assert automatic["automatic_gate_passed"] is False
    assert automatic["sha256"] == (
        "b2710e034a0bf8869c160fa32b9fce88d9fb899a3989483f1bd0e02afd633de7"
    )
    assert automatic["preserved_unchanged"] is True

    review = report["manual_review"]
    assert review["status"] == "complete"
    assert review["decision_source"] == (
        "explicit_user_instruction_after_notebook_review"
    )
    assert review["decision"] == (
        "classify_view_sum_ratio_as_nonblocking_diagnostic"
    )
    assert review["waived_blocking_gate_ids"] == [
        "view_sum_ratio_at_most_80"
    ]
    assert review["other_failed_gate_ids"] == []
    assert len(review["affected_case_ids"]) == 11
    assert review["dataset_bytes_modified"] is False
    assert review["cases_removed"] is False

    release = report["release"]
    assert release["status"] == "pass"
    assert release["all_required_blocking_gates_passed"] is True
    assert release["go_for_training"] is True
    assert release["accepted_case_count"] == 550
    assert release["role_case_counts"] == {"main": 500, "negative": 50}


def test_manual_acceptance_binds_the_reviewed_notebook() -> None:
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    notebook = report["manual_review"]["reviewed_notebook"]

    assert notebook["git_commit"] == (
        "4f206cf2d4607cc69049b14adcfd33bc50deb0cd"
    )
    assert notebook["sha256"] == (
        "7568633982392b366e50bc9a14848423ff2d6db199ef403f90c35271dc4eefcc"
    )
