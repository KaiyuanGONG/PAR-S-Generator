#!/usr/bin/env python
"""Build the immutable local Task13 Formal550 training release.

The frozen campaign is never edited.  The builder validates its authority chain,
creates a content-addressed archive from hard-linked staging files on the same
volume, verifies the archive, and writes RELEASE_COMPLETE.json last.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Iterable
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CAMPAIGN_ROOT = Path(r"D:\PFE-U\PAR\outputs\pars_v2_formal550_v1")
DEFAULT_QA_ROOT = Path(r"D:\PFE-U\PAR\outputs\pars_v2_formal550_v1_qa")
DEFAULT_INPUT_ARCHIVE = Path(
    r"D:\PFE-U\PAR\outputs\task13_formal550_download_v1"
    r"\task13_formal550_results.tar.gz"
)
DEFAULT_RELEASE_ROOT = Path(
    r"D:\PFE-U\PAR\outputs\pars_v2_formal550_v1_release_v1"
)

RELEASE_SCHEMA = "pars_v2_task13_formal550_release_v1"
RELEASE_ID = "PAR-S-V2-FORMAL550-v2.0.0-release-v1"
PAYLOAD_NAME = RELEASE_ID
ARCHIVE_NAME = "PAR-S-V2-FORMAL550-v2.0.0-release-v1.tar.zst"
CONTENT_MANIFEST_NAME = "CONTENT_SHA256SUMS.jsonl"
RELEASE_MARKER_NAME = "RELEASE_COMPLETE.json"
TRAINING_HANDOFF_NAME = "TRAINING_HANDOFF.md"
SOURCE_ARCHIVE_PREFIX = "PAR-S-Generator-source"

CAMPAIGN_SCHEMA = "pars_v2_task13_formal550_complete_v1"
AUTOMATIC_SCHEMA = "pars_v2_task13_formal550_automatic_acceptance_v1"
MANUAL_SCHEMA = "pars_v2_task13_formal550_manual_acceptance_v1"
DATASET_SCHEMA = "pars_dataset_freeze_v2"
EXPECTED_ROLE_COUNTS = {"main": 500, "negative": 50}
EXPECTED_MAIN_SPLITS = {"train": 400, "val": 50, "test": 50}
EXPECTED_NEGATIVE_SPLITS = {"train": 0, "val": 0, "test": 50}
EXPECTED_INPUT_ARCHIVE_SHA256 = (
    "fecbd2d485d3f28dab8e195b208d9a9b5a115cf05d7fe1741ab11e3dc8496c74"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


class Formal550ReleaseError(RuntimeError):
    """Raised when a release prerequisite or verification fails."""


@dataclass(frozen=True)
class AuthoritySnapshot:
    campaign: dict[str, Any]
    automatic: dict[str, Any]
    manual: dict[str, Any]
    role_markers: dict[str, dict[str, Any]]
    critical_hashes: dict[str, str]
    external_evidence: dict[str, Path]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Formal550ReleaseError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise Formal550ReleaseError(f"{label} must be a JSON object")
    return value


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(value, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def parse_sha256_sidecar(path: Path) -> str:
    try:
        token = path.read_text(encoding="utf-8").strip().split()[0].lower()
    except (OSError, IndexError) as exc:
        raise Formal550ReleaseError(f"cannot read SHA-256 sidecar {path}") from exc
    if not _SHA256.fullmatch(token):
        raise Formal550ReleaseError(f"invalid SHA-256 sidecar {path}")
    return token


def require(condition: bool, message: str) -> None:
    if not condition:
        raise Formal550ReleaseError(message)


def _manifest_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    line_number = 0
    try:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("row is not an object")
            rows.append(value)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise Formal550ReleaseError(
            f"cannot parse manifest {path} at or before line {line_number}: {exc}"
        ) from exc
    return rows


def _gate_rows_by_id(automatic: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = automatic.get("gate_rows")
    require(isinstance(rows, list), "automatic acceptance gate_rows missing")
    mapped = {
        row.get("gate_id"): row
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("gate_id"), str)
    }
    require(len(mapped) == len(rows), "automatic acceptance gate ids invalid")
    return mapped


def validate_authority(
    campaign_root: Path,
    qa_root: Path,
    input_archive: Path,
) -> AuthoritySnapshot:
    campaign_root = campaign_root.resolve()
    qa_root = qa_root.resolve()
    input_archive = input_archive.resolve()

    campaign_path = campaign_root / "FORMAL550_COMPLETE.json"
    automatic_path = qa_root / "TASK13_FORMAL550_AUTOMATIC_ACCEPTANCE.json"
    manual_path = qa_root / "TASK13_FORMAL550_MANUAL_ACCEPTANCE.json"
    campaign = read_json(campaign_path, "campaign marker")
    automatic = read_json(automatic_path, "automatic acceptance")
    manual = read_json(manual_path, "manual acceptance")

    require(campaign.get("schema_version") == CAMPAIGN_SCHEMA, "campaign schema mismatch")
    require(campaign.get("status") == "complete", "campaign is not complete")
    require(campaign.get("case_count") == 550, "campaign case_count is not 550")
    require(
        campaign.get("role_case_counts") == EXPECTED_ROLE_COUNTS,
        "campaign role counts mismatch",
    )

    require(
        automatic.get("schema_version") == AUTOMATIC_SCHEMA,
        "automatic acceptance schema mismatch",
    )
    require(
        automatic.get("status") == "fail"
        and automatic.get("automatic_gate_passed") is False,
        "historical automatic FAIL was not preserved",
    )
    require(automatic.get("case_count") == 550, "automatic case_count mismatch")
    require(
        automatic.get("role_case_counts") == EXPECTED_ROLE_COUNTS,
        "automatic role counts mismatch",
    )

    require(manual.get("schema_version") == MANUAL_SCHEMA, "manual schema mismatch")
    require(manual.get("status") == "pass", "manual acceptance is not PASS")
    release = manual.get("release")
    review = manual.get("manual_review")
    require(isinstance(release, dict), "manual release block missing")
    require(isinstance(review, dict), "manual review block missing")
    require(
        release.get("status") == "pass"
        and release.get("go_for_training") is True
        and release.get("accepted_case_count") == 550,
        "manual release does not authorize all 550 cases for training",
    )
    require(
        release.get("role_case_counts") == EXPECTED_ROLE_COUNTS,
        "manual release role counts mismatch",
    )
    require(
        review.get("waived_blocking_gate_ids")
        == ["view_sum_ratio_at_most_80"],
        "unexpected manual gate waiver",
    )
    require(review.get("other_failed_gate_ids") == [], "other failed gates remain")
    require(review.get("dataset_bytes_modified") is False, "dataset was marked modified")
    require(review.get("cases_removed") is False, "cases were marked removed")

    critical_hashes = {
        "campaign_complete": sha256_file(campaign_path),
        "automatic_acceptance": sha256_file(automatic_path),
        "manual_acceptance": sha256_file(manual_path),
    }
    manual_campaign = manual.get("campaign")
    manual_automatic = manual.get("automatic_acceptance_v1")
    require(isinstance(manual_campaign, dict), "manual campaign binding missing")
    require(isinstance(manual_automatic, dict), "manual automatic binding missing")
    require(
        manual_campaign.get("campaign_complete_sha256")
        == critical_hashes["campaign_complete"],
        "manual campaign marker binding mismatch",
    )
    require(
        manual_automatic.get("sha256")
        == critical_hashes["automatic_acceptance"],
        "manual automatic acceptance binding mismatch",
    )
    require(
        manual_automatic.get("preserved_unchanged") is True,
        "automatic FAIL preservation flag missing",
    )

    gate_rows = _gate_rows_by_id(automatic)
    expected_gate_statuses = {
        "formal550_generator_gate_v1": "fail",
        "formal550_main_loader_gate_v1": "pass",
        "formal550_negative_loader_gate_v1": "pass",
        "projection_coordinate_gate_v2": "pass",
    }
    for gate_id, expected_status in expected_gate_statuses.items():
        row = gate_rows.get(gate_id)
        require(isinstance(row, dict), f"missing gate row {gate_id}")
        require(row.get("status") == expected_status, f"{gate_id} status mismatch")
        gate_path = Path(str(row.get("path", ""))).resolve()
        require(gate_path.is_file(), f"{gate_id} evidence missing")
        gate_hash = sha256_file(gate_path)
        require(gate_hash == row.get("sha256"), f"{gate_id} SHA-256 mismatch")
        critical_hashes[gate_id] = gate_hash

    task12g_chain = automatic.get("task12g_release_chain")
    require(isinstance(task12g_chain, dict), "Task12G release chain missing")
    require(task12g_chain.get("status") == "pass", "Task12G release chain is not PASS")
    external_evidence: dict[str, Path] = {}
    for key in ("automatic_acceptance", "coordinate_report", "release"):
        binding = task12g_chain.get(key)
        require(isinstance(binding, dict), f"Task12G {key} binding missing")
        path = Path(str(binding.get("path", ""))).resolve()
        require(path.is_file(), f"Task12G {key} evidence missing")
        actual = sha256_file(path)
        require(actual == binding.get("sha256"), f"Task12G {key} SHA-256 mismatch")
        external_evidence[f"task12g_{key}"] = path
        critical_hashes[f"task12g_{key}"] = actual

    role_markers: dict[str, dict[str, Any]] = {}
    for role, expected_count, expected_splits in (
        ("main", 500, EXPECTED_MAIN_SPLITS),
        ("negative", 50, EXPECTED_NEGATIVE_SPLITS),
    ):
        role_root = campaign_root / role
        marker_path = role_root / "DATASET_COMPLETE.json"
        manifest_path = role_root / "case_manifest.jsonl"
        marker = read_json(marker_path, f"{role} dataset marker")
        require(marker.get("schema_version") == DATASET_SCHEMA, f"{role} schema mismatch")
        require(marker.get("status") == "complete", f"{role} is not complete")
        require(marker.get("case_count") == expected_count, f"{role} count mismatch")
        require(
            marker.get("split_counts") == expected_splits,
            f"{role} split counts mismatch",
        )
        manifest_hash = sha256_file(manifest_path)
        require(
            marker.get("manifest_sha256") == manifest_hash,
            f"{role} marker/manifest hash mismatch",
        )
        datasets = campaign.get("datasets")
        require(isinstance(datasets, dict), "campaign dataset bindings missing")
        campaign_binding = datasets.get(role)
        require(isinstance(campaign_binding, dict), f"{role} campaign binding missing")
        require(
            campaign_binding.get("manifest_sha256") == manifest_hash,
            f"{role} campaign/manifest hash mismatch",
        )
        require(
            manual_campaign.get(f"{role}_manifest_sha256") == manifest_hash,
            f"{role} manual/manifest hash mismatch",
        )
        rows = _manifest_rows(manifest_path)
        require(len(rows) == expected_count, f"{role} manifest row count mismatch")
        case_dirs = [path for path in (role_root / "cases").iterdir() if path.is_dir()]
        require(len(case_dirs) == expected_count, f"{role} case directory count mismatch")
        role_markers[role] = marker
        critical_hashes[f"{role}_dataset_complete"] = sha256_file(marker_path)
        critical_hashes[f"{role}_manifest"] = manifest_hash

    input_sidecar = input_archive.with_name(f"{input_archive.name}.sha256")
    require(input_archive.is_file(), "upstream result archive missing")
    require(input_sidecar.is_file(), "upstream result archive sidecar missing")
    expected_input_hash = parse_sha256_sidecar(input_sidecar)
    actual_input_hash = sha256_file(input_archive)
    require(
        expected_input_hash == EXPECTED_INPUT_ARCHIVE_SHA256,
        "unexpected upstream result archive identity",
    )
    require(actual_input_hash == expected_input_hash, "upstream archive SHA-256 mismatch")
    critical_hashes["upstream_result_archive"] = actual_input_hash

    manual_sidecar = manual_path.with_name(f"{manual_path.name}.sha256")
    require(manual_sidecar.is_file(), "manual acceptance sidecar missing")
    require(
        parse_sha256_sidecar(manual_sidecar) == critical_hashes["manual_acceptance"],
        "manual acceptance sidecar mismatch",
    )
    return AuthoritySnapshot(
        campaign=campaign,
        automatic=automatic,
        manual=manual,
        role_markers=role_markers,
        critical_hashes=critical_hashes,
        external_evidence=external_evidence,
    )


def _git(repo_root: Path, *args: str) -> str:
    command = [
        "git",
        "-c",
        f"safe.directory={repo_root.as_posix()}",
        "-C",
        str(repo_root),
        *args,
    ]
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise Formal550ReleaseError(
            f"git {' '.join(args)} failed: {completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def validate_source_repo(repo_root: Path) -> str:
    repo_root = repo_root.resolve()
    head = _git(repo_root, "rev-parse", "HEAD")
    require(bool(_GIT_COMMIT.fullmatch(head)), "source HEAD is not a full commit hash")
    status = _git(repo_root, "status", "--porcelain", "--untracked-files=all")
    require(not status, f"source worktree is not clean:\n{status}")
    return head


def _link_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError as exc:
        raise Formal550ReleaseError(
            f"hard-link staging failed for {source}; release must stay on the "
            f"same volume and will not silently duplicate or alter data: {exc}"
        ) from exc


def _link_tree(source_root: Path, destination_root: Path) -> None:
    for source in sorted(
        (path for path in source_root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(source_root).as_posix(),
    ):
        _link_file(source, destination_root / source.relative_to(source_root))


def _create_source_archive(repo_root: Path, commit: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "git",
        "-c",
        f"safe.directory={repo_root.as_posix()}",
        "-C",
        str(repo_root),
        "archive",
        "--format=tar.gz",
        f"--output={destination}",
        commit,
    ]
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise Formal550ReleaseError(
            f"git archive failed: {completed.stderr.strip()}"
        )


def _copy_external_evidence(
    snapshot: AuthoritySnapshot,
    destination_root: Path,
) -> None:
    for label, source in sorted(snapshot.external_evidence.items()):
        destination = destination_root / f"{label}{source.suffix.lower()}"
        shutil.copyfile(source, destination)
        require(
            sha256_file(destination) == snapshot.critical_hashes[label],
            f"copied external evidence mismatch for {label}",
        )


def inventory_rows(
    payload_root: Path,
    *,
    excluded_relative_paths: Iterable[str] = (),
) -> list[dict[str, Any]]:
    excluded = set(excluded_relative_paths)
    rows: list[dict[str, Any]] = []
    for path in sorted(
        (item for item in payload_root.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(payload_root).as_posix(),
    ):
        relative = path.relative_to(payload_root).as_posix()
        if relative in excluded:
            continue
        rows.append(
            {
                "relative_path": relative,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return rows


def write_inventory(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    lines = [
        json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        for row in rows
    ]
    write_text(path, "\n".join(lines) + "\n")


def verify_inventory(payload_root: Path, manifest_path: Path) -> int:
    try:
        rows = [
            json.loads(line)
            for line in manifest_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Formal550ReleaseError(f"cannot parse content inventory: {exc}") from exc
    for row in rows:
        relative = row.get("relative_path")
        require(isinstance(relative, str), "inventory relative_path missing")
        path = payload_root / Path(relative)
        require(path.is_file(), f"inventory member missing: {relative}")
        require(path.stat().st_size == row.get("bytes"), f"size mismatch: {relative}")
        require(sha256_file(path) == row.get("sha256"), f"SHA mismatch: {relative}")
    expected_paths = {
        path.relative_to(payload_root).as_posix()
        for path in payload_root.rglob("*")
        if path.is_file() and path != manifest_path
    }
    require(
        expected_paths == {row["relative_path"] for row in rows},
        "inventory path set does not match payload",
    )
    return len(rows)


def training_handoff(campaign_root: Path, source_commit: str) -> str:
    return f"""# PAR-S V2 Formal550 training handoff

Release: `{RELEASE_ID}`

Source commit: `{source_commit}`

## Training inputs

- Main dataset root: `{campaign_root / "main"}`
- Main manifest: `{campaign_root / "main" / "case_manifest.jsonl"}`
- Main split: train=400, val=50, test=50
- Negative-control root: `{campaign_root / "negative"}`
- Negative-control manifest: `{campaign_root / "negative" / "case_manifest.jsonl"}`
- Negative controls: 50 cases, test-only, population weight zero
- Loader transform: `simind_v8_xcat_v1_views_forward_roll000_det_v_flip_det_u_keep`

Use only the main `train` rows for optimization and main `val` rows for model
selection. Evaluate the main test set and the independent negative-control test
set separately. Do not merge negative controls into train or validation.

The historical automatic acceptance remains FAIL because the frozen
`view_sum_ratio_at_most_80` diagnostic exceeded 80 in 11 cases. Following the
completed visual review, the versioned manual release reclassifies that one
measurement as diagnostic/nonblocking. No projection bytes were changed and no
case was removed. `RELEASE_COMPLETE.json` is the authoritative release marker.

The materialized campaign above is the convenient local training source. The
`.tar.zst` archive in the release directory is the immutable preservation copy;
verify its SHA-256 sidecar before restoring it elsewhere.
"""


def _release_declaration(
    snapshot: AuthoritySnapshot,
    source_commit: str,
) -> dict[str, Any]:
    return {
        "schema_version": RELEASE_SCHEMA,
        "release_id": RELEASE_ID,
        "dataset_id": "PAR-S-V2-FORMAL550",
        "dataset_version": "2.0.0",
        "case_count": 550,
        "role_case_counts": EXPECTED_ROLE_COUNTS,
        "main_split_counts": EXPECTED_MAIN_SPLITS,
        "negative_split_counts": EXPECTED_NEGATIVE_SPLITS,
        "negative_policy": "independent_test_only_population_weight_zero",
        "source_commit": source_commit,
        "authority": {
            **snapshot.critical_hashes,
            "manual_release_status": "pass",
            "go_for_training": True,
            "automatic_v1_status": "fail_preserved",
            "view_sum_ratio_classification": "diagnostic_nonblocking",
        },
        "write_policy": "immutable_do_not_modify_create_new_version",
    }


def _create_archive(tar_executable: str, payload_root: Path, archive_path: Path) -> None:
    command = [
        tar_executable,
        "--zstd",
        "-cf",
        str(archive_path),
        "-C",
        str(payload_root.parent),
        payload_root.name,
    ]
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise Formal550ReleaseError(
            f"archive command failed with exit code {completed.returncode}"
        )


def _verify_archive(
    tar_executable: str,
    archive_path: Path,
    expected_payload_file_count: int,
) -> int:
    completed = subprocess.run(
        [tar_executable, "-tf", str(archive_path)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise Formal550ReleaseError(
            f"cannot list archive: {completed.stderr.strip()}"
        )
    listed = [line for line in completed.stdout.splitlines() if line]
    names = [line.rstrip("/") for line in listed]
    required = {
        f"{PAYLOAD_NAME}/dataset/FORMAL550_COMPLETE.json",
        f"{PAYLOAD_NAME}/qa/TASK13_FORMAL550_AUTOMATIC_ACCEPTANCE.json",
        f"{PAYLOAD_NAME}/qa/TASK13_FORMAL550_MANUAL_ACCEPTANCE.json",
        f"{PAYLOAD_NAME}/{CONTENT_MANIFEST_NAME}",
        f"{PAYLOAD_NAME}/{TRAINING_HANDOFF_NAME}",
    }
    require(required.issubset(set(names)), "archive is missing required members")
    archived_files = [name for name in listed if not name.endswith("/")]
    require(
        len(archived_files) == expected_payload_file_count,
        "archive file count does not match the verified payload",
    )
    return len(names)


def _copy_release_access_files(payload_root: Path, release_stage: Path) -> None:
    shutil.copyfile(
        payload_root / CONTENT_MANIFEST_NAME,
        release_stage / CONTENT_MANIFEST_NAME,
    )
    shutil.copyfile(
        payload_root / TRAINING_HANDOFF_NAME,
        release_stage / TRAINING_HANDOFF_NAME,
    )
    source_files = list((payload_root / "source").glob("*.tar.gz"))
    require(len(source_files) == 1, "source snapshot count mismatch")
    shutil.copyfile(source_files[0], release_stage / source_files[0].name)
    evidence_out = release_stage / "evidence"
    evidence_out.mkdir()
    for source in sorted((payload_root / "evidence").iterdir()):
        if source.is_file():
            shutil.copyfile(source, evidence_out / source.name)


def verify_existing_release(release_root: Path) -> dict[str, Any]:
    marker_path = release_root / RELEASE_MARKER_NAME
    marker = read_json(marker_path, "release marker")
    require(marker.get("schema_version") == RELEASE_SCHEMA, "release schema mismatch")
    require(marker.get("status") == "complete", "release is not complete")
    require(marker.get("go_for_training") is True, "release forbids training")
    archive = release_root / str(marker.get("archive", {}).get("name", ""))
    require(archive.is_file(), "release archive missing")
    require(
        sha256_file(archive) == marker["archive"]["sha256"],
        "release archive SHA-256 mismatch",
    )
    sidecar = archive.with_name(f"{archive.name}.sha256")
    require(parse_sha256_sidecar(sidecar) == marker["archive"]["sha256"], "archive sidecar mismatch")
    content_manifest = release_root / CONTENT_MANIFEST_NAME
    require(
        sha256_file(content_manifest) == marker["content_manifest"]["sha256"],
        "content manifest SHA-256 mismatch",
    )
    marker_sidecar = marker_path.with_name(f"{marker_path.name}.sha256")
    require(
        parse_sha256_sidecar(marker_sidecar) == sha256_file(marker_path),
        "release marker sidecar mismatch",
    )
    return marker


def build_release(
    *,
    campaign_root: Path,
    qa_root: Path,
    input_archive: Path,
    release_root: Path,
    repo_root: Path,
    tar_executable: str,
) -> dict[str, Any]:
    campaign_root = campaign_root.resolve()
    qa_root = qa_root.resolve()
    input_archive = input_archive.resolve()
    release_root = release_root.resolve()
    repo_root = repo_root.resolve()

    if release_root.exists():
        marker = verify_existing_release(release_root)
        return {
            "status": "already_complete",
            "release_root": str(release_root),
            "archive_sha256": marker["archive"]["sha256"],
            "case_count": marker["case_count"],
        }

    snapshot = validate_authority(campaign_root, qa_root, input_archive)
    source_commit = validate_source_repo(repo_root)
    build_root = release_root.with_name(
        f".{release_root.name}.building-{uuid4().hex}"
    )
    payload_root = build_root / PAYLOAD_NAME
    completed = False
    try:
        payload_root.mkdir(parents=True)
        _link_tree(campaign_root, payload_root / "dataset")
        _link_tree(qa_root, payload_root / "qa")
        upstream_root = payload_root / "upstream"
        _link_file(input_archive, upstream_root / input_archive.name)
        input_sidecar = input_archive.with_name(f"{input_archive.name}.sha256")
        _link_file(input_sidecar, upstream_root / input_sidecar.name)

        evidence_root = payload_root / "evidence"
        evidence_root.mkdir()
        _copy_external_evidence(snapshot, evidence_root)
        reviewed_notebook = snapshot.manual["manual_review"]["reviewed_notebook"]
        reviewed_commit = str(reviewed_notebook["git_commit"])
        reviewed_relative = str(reviewed_notebook["relative_path"])
        reviewed_payload = subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={repo_root.as_posix()}",
                "-C",
                str(repo_root),
                "show",
                f"{reviewed_commit}:{reviewed_relative}",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        require(reviewed_payload.returncode == 0, "cannot recover reviewed notebook")
        reviewed_path = evidence_root / (
            f"reviewed_notebook_{reviewed_commit[:8]}.ipynb"
        )
        reviewed_path.write_bytes(reviewed_payload.stdout)
        require(
            sha256_file(reviewed_path) == reviewed_notebook["sha256"],
            "reviewed notebook SHA-256 mismatch",
        )

        source_archive = (
            payload_root
            / "source"
            / f"{SOURCE_ARCHIVE_PREFIX}-{source_commit[:12]}.tar.gz"
        )
        _create_source_archive(repo_root, source_commit, source_archive)
        write_json(
            payload_root / "RELEASE_DECLARATION.json",
            _release_declaration(snapshot, source_commit),
        )
        write_text(
            payload_root / TRAINING_HANDOFF_NAME,
            training_handoff(campaign_root, source_commit),
        )

        inventory_path = payload_root / CONTENT_MANIFEST_NAME
        rows = inventory_rows(
            payload_root,
            excluded_relative_paths=(CONTENT_MANIFEST_NAME,),
        )
        write_inventory(inventory_path, rows)
        verified_payload_file_count = verify_inventory(payload_root, inventory_path)
        content_manifest_hash = sha256_file(inventory_path)

        archive_path = build_root / ARCHIVE_NAME
        _create_archive(tar_executable, payload_root, archive_path)
        archive_member_count = _verify_archive(
            tar_executable,
            archive_path,
            verified_payload_file_count + 1,
        )
        post_archive_file_count = verify_inventory(payload_root, inventory_path)
        require(
            post_archive_file_count == verified_payload_file_count,
            "payload changed while archiving",
        )
        archive_hash = sha256_file(archive_path)
        write_text(
            archive_path.with_name(f"{archive_path.name}.sha256"),
            f"{archive_hash}  {archive_path.name}\n",
        )
        _copy_release_access_files(payload_root, build_root)

        marker = {
            **_release_declaration(snapshot, source_commit),
            "status": "complete",
            "go_for_training": True,
            "frozen_campaign_modified": False,
            "released_utc": utc_now(),
            "campaign_root": str(campaign_root),
            "qa_root": str(qa_root),
            "release_root": str(release_root),
            "archive": {
                "name": ARCHIVE_NAME,
                "format": "tar+zstd",
                "bytes": archive_path.stat().st_size,
                "sha256": archive_hash,
                "member_count": archive_member_count,
            },
            "content_manifest": {
                "name": CONTENT_MANIFEST_NAME,
                "sha256": content_manifest_hash,
                "payload_file_count_excluding_manifest": verified_payload_file_count,
            },
            "training": {
                "main_root": str(campaign_root / "main"),
                "main_manifest": str(
                    campaign_root / "main" / "case_manifest.jsonl"
                ),
                "negative_root": str(campaign_root / "negative"),
                "negative_manifest": str(
                    campaign_root / "negative" / "case_manifest.jsonl"
                ),
            },
            "verification": {
                "authority_chain": "pass",
                "payload_inventory_before_archive": "pass",
                "archive_stream_and_required_members": "pass",
                "payload_inventory_after_archive": "pass",
            },
        }
        marker_path = build_root / RELEASE_MARKER_NAME
        write_json(marker_path, marker)
        marker_hash = sha256_file(marker_path)
        write_text(
            marker_path.with_name(f"{marker_path.name}.sha256"),
            f"{marker_hash}  {marker_path.name}\n",
        )

        shutil.rmtree(payload_root)
        os.replace(build_root, release_root)
        completed = True
        verified = verify_existing_release(release_root)
        return {
            "status": "complete",
            "release_root": str(release_root),
            "archive": str(release_root / ARCHIVE_NAME),
            "archive_sha256": verified["archive"]["sha256"],
            "archive_bytes": verified["archive"]["bytes"],
            "case_count": verified["case_count"],
            "source_commit": verified["source_commit"],
        }
    finally:
        if not completed and build_root.exists():
            shutil.rmtree(build_root)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the immutable local Task13 Formal550 release."
    )
    parser.add_argument("--campaign-root", type=Path, default=DEFAULT_CAMPAIGN_ROOT)
    parser.add_argument("--qa-root", type=Path, default=DEFAULT_QA_ROOT)
    parser.add_argument("--input-archive", type=Path, default=DEFAULT_INPUT_ARCHIVE)
    parser.add_argument("--release-root", type=Path, default=DEFAULT_RELEASE_ROOT)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--tar", default=shutil.which("tar") or "tar")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = build_release(
            campaign_root=args.campaign_root,
            qa_root=args.qa_root,
            input_archive=args.input_archive,
            release_root=args.release_root,
            repo_root=args.repo_root,
            tar_executable=args.tar,
        )
    except Formal550ReleaseError as exc:
        print(
            json.dumps(
                {"status": "fail", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
