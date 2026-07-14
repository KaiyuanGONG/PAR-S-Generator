from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.case_writer_v2 import (  # noqa: E402
    ArtifactRecordV2,
    DatasetContractV2,
    DatasetFreezeError,
    build_split_plan,
    freeze_dataset,
    write_case_manifest,
    write_case_v2,
    write_split_plan,
)
from core.provenance import sha256_file  # noqa: E402
from test_case_writer_v2 import _metadata, make_payload  # noqa: E402


def _records(root: Path, count: int = 6):
    families = [f"family_{index:05d}" for index in range(count)]
    plan = build_split_plan(
        families,
        dataset_id="PAR-S-TARE-HCC-NoPVI-SYN-v2-test",
        profile_id="population_tare_hcc_nopvi_v2",
        global_seed=20260714,
        ratios={"train": 0.5, "val": 1 / 6, "test": 1 / 3},
    )
    write_split_plan(plan, root)
    records = []
    for index, family in enumerate(families):
        records.append(
            write_case_v2(
                make_payload(
                    f"case_{index:05d}",
                    family_id=family,
                    split=plan.family_to_split[family],
                ),
                root,
            )
        )
    return plan, tuple(records)


def _contract(root: Path, plan, records) -> DatasetContractV2:
    return DatasetContractV2(
        output_root=root,
        dataset_id="PAR-S-TARE-HCC-NoPVI-SYN-v2-test",
        dataset_version="2.0.0-test",
        dataset_role="main",
        expected_case_ids=tuple(record.case_id for record in records),
        allowed_profile_ids=("population_tare_hcc_nopvi_v2",),
        split_plan_sha256=plan.sha256,
        required_artifact_names=("phantom_npz", "metadata_json"),
    )


def test_split_is_fixed_by_family_before_any_case_is_written(tmp_path: Path) -> None:
    families = [f"family_{index:03d}" for index in range(20)]
    first = build_split_plan(
        families,
        dataset_id="dataset-A",
        profile_id="population_tare_hcc_nopvi_v2",
        global_seed=77,
    )
    second = build_split_plan(
        list(reversed(families)),
        dataset_id="dataset-A",
        profile_id="population_tare_hcc_nopvi_v2",
        global_seed=77,
    )
    assert first == second
    assert first.family_to_split == second.family_to_split
    assert set(first.family_to_split.values()) == {"train", "val", "test"}
    assert set(first.family_seeds) == set(families)

    path = write_split_plan(first, tmp_path)
    assert path.name == "SPLIT_PLAN.json"
    assert not (tmp_path / "cases").exists()
    assert json.loads(path.read_text(encoding="utf-8"))["sha256"] == first.sha256

    changed = build_split_plan(
        families,
        dataset_id="dataset-A",
        profile_id="population_tare_hcc_nopvi_v2",
        global_seed=78,
    )
    with pytest.raises(DatasetFreezeError, match="immutable split plan"):
        write_split_plan(changed, tmp_path)


def test_split_plan_rejects_rehashed_but_non_derived_family_seed(tmp_path: Path) -> None:
    family = "family_00000"
    plan = build_split_plan(
        [family],
        dataset_id="PAR-S-TARE-HCC-NoPVI-SYN-v2-test",
        profile_id="population_tare_hcc_nopvi_v2",
        global_seed=20260714,
        ratios={"train": 1.0, "val": 0.0, "test": 0.0},
    )
    path = write_split_plan(plan, tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["family_seeds"][family] += 1
    from core.provenance import sha256_json

    content = {key: value for key, value in raw.items() if key != "sha256"}
    raw["sha256"] = sha256_json(content)
    path.write_text(json.dumps(raw), encoding="utf-8")
    from core.case_writer_v2 import CaseWriteError

    with pytest.raises(CaseWriteError, match="family seeds.*derived"):
        write_case_v2(make_payload("case_00000", family_id=family), tmp_path)


def test_manifest_pairs_records_rejects_duplicates_and_family_leakage(tmp_path: Path) -> None:
    plan, records = _records(tmp_path)
    manifest = write_case_manifest(records, tmp_path, split_plan_sha256=plan.sha256)
    lines = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == len(records)
    assert {line["case_id"] for line in lines} == {record.case_id for record in records}
    assert all("phantom_npz" in line["artifacts"] and "metadata_json" in line["artifacts"] for line in lines)
    assert all(
        line["projection_coordinate_contract_id"]
        == "pars_simind_v8_xcat_zyx_sar_v1"
        for line in lines
    )
    assert all(
        line["loader_transform_id"]
        == "simind_v8_xcat_v1_views_forward_roll000_det_v_flip_det_u_keep"
        for line in lines
    )

    with pytest.raises(DatasetFreezeError, match="duplicate case_id"):
        write_case_manifest(records + (records[0],), tmp_path / "duplicate", split_plan_sha256=plan.sha256)

    leaked_split = "test" if records[0].split != "test" else "train"
    leaked = replace(records[1], case_family_id=records[0].case_family_id, split=leaked_split)
    with pytest.raises(DatasetFreezeError, match="case_family_id.*multiple splits"):
        write_case_manifest((records[0], leaked), tmp_path / "leak", split_plan_sha256=plan.sha256)


def test_record_cannot_borrow_artifact_from_another_case(tmp_path: Path) -> None:
    plan, records = _records(tmp_path, count=2)
    borrowed = records[1].artifacts["phantom_npz"]
    artifacts = dict(records[0].artifacts)
    artifacts["phantom_npz"] = ArtifactRecordV2(
        relative_path=borrowed.relative_path,
        size_bytes=borrowed.size_bytes,
        sha256=borrowed.sha256,
    )
    corrupted_record = replace(records[0], artifacts=artifacts)
    contract = _contract(tmp_path, plan, records)
    with pytest.raises(DatasetFreezeError, match="not inside its own case directory"):
        freeze_dataset((corrupted_record, records[1]), contract)


def test_manifest_record_cannot_override_dataset_projection_contract(
    tmp_path: Path,
) -> None:
    plan, records = _records(tmp_path, count=2)
    corrupted = replace(records[0], loader_transform_id="implicit_flip")
    with pytest.raises(DatasetFreezeError, match="unfrozen projection contract"):
        freeze_dataset((corrupted, records[1]), _contract(tmp_path, plan, records))


def test_freeze_verifies_every_hash_and_writes_complete_marker_last(tmp_path: Path) -> None:
    plan, records = _records(tmp_path)
    frozen = freeze_dataset(records, _contract(tmp_path, plan, records))
    manifest = tmp_path / "case_manifest.jsonl"
    marker = tmp_path / "DATASET_COMPLETE.json"
    assert manifest.is_file() and marker.is_file()
    assert frozen.case_count == len(records)
    assert frozen.manifest_sha256 == sha256_file(manifest)
    assert marker.stat().st_mtime_ns >= manifest.stat().st_mtime_ns
    marker_data = json.loads(marker.read_text(encoding="utf-8"))
    assert marker_data["status"] == "complete"
    assert marker_data["manifest_sha256"] == sha256_file(manifest)
    assert marker_data["split_counts"] == frozen.split_counts
    assert (
        marker_data["projection_coordinate_contract_id"]
        == "pars_simind_v8_xcat_zyx_sar_v1"
    )
    assert (
        marker_data["loader_transform_id"]
        == "simind_v8_xcat_v1_views_forward_roll000_det_v_flip_det_u_keep"
    )
    assert freeze_dataset(records, _contract(tmp_path, plan, records)) == frozen

    marker_data["loader_transform_id"] = "tampered"
    marker.write_text(json.dumps(marker_data), encoding="utf-8")
    with pytest.raises(DatasetFreezeError, match="not frozen V2"):
        freeze_dataset(records, _contract(tmp_path, plan, records))


def test_freeze_revalidates_coordinate_metadata_not_only_hashes(tmp_path: Path) -> None:
    plan, records = _records(tmp_path, count=2)
    record = records[0]
    metadata_path = tmp_path / record.artifacts["metadata_json"].relative_path
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["acquisition"]["projection_coordinates"][
        "loader_transform_id"
    ] = "unversioned_flip"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    artifacts = dict(record.artifacts)
    artifacts["metadata_json"] = ArtifactRecordV2(
        relative_path=record.artifacts["metadata_json"].relative_path,
        size_bytes=metadata_path.stat().st_size,
        sha256=sha256_file(metadata_path),
    )
    rehashed_record = replace(record, artifacts=artifacts)

    with pytest.raises(DatasetFreezeError, match="projection coordinate metadata"):
        freeze_dataset(
            (rehashed_record, records[1]),
            _contract(tmp_path, plan, records),
        )


def test_freeze_failure_never_leaves_completion_marker(tmp_path: Path) -> None:
    plan, records = _records(tmp_path)
    phantom = tmp_path / records[0].artifacts["phantom_npz"].relative_path
    corrupted = bytearray(phantom.read_bytes())
    corrupted[len(corrupted) // 2] ^= 0x01
    phantom.write_bytes(corrupted)
    with pytest.raises(DatasetFreezeError, match="SHA-256 mismatch"):
        freeze_dataset(records, _contract(tmp_path, plan, records))
    assert not (tmp_path / "case_manifest.jsonl").exists()
    assert not (tmp_path / "DATASET_COMPLETE.json").exists()


def test_freeze_requires_exact_case_set_and_rejects_identity_mixing(tmp_path: Path) -> None:
    plan, records = _records(tmp_path)
    with pytest.raises(DatasetFreezeError, match="expected case set"):
        freeze_dataset(records[:-1], _contract(tmp_path, plan, records))

    mixed = replace(records[-1], dataset_role="negative", dataset_id="negative-test")
    with pytest.raises(DatasetFreezeError, match="dataset identity"):
        freeze_dataset(records[:-1] + (mixed,), _contract(tmp_path, plan, records))


def test_negative_dataset_requires_separate_identity_root_and_zero_weight(tmp_path: Path) -> None:
    main_root = tmp_path / "main"
    negative_root = tmp_path / "negative"
    main_plan, main_records = _records(main_root, count=3)
    negative_plan = build_split_plan(
        ["negative_family_00000"],
        dataset_id="PAR-S-negative-v2-test",
        profile_id="negative_control_v2",
        global_seed=20260714,
        ratios={"train": 0.0, "val": 0.0, "test": 1.0},
    )
    write_split_plan(negative_plan, negative_root)
    negative_payload = make_payload(
        "negative_case_00000",
        family_id="negative_family_00000",
        split="test",
        role="negative",
    )
    arrays = dict(negative_payload.arrays)
    arrays["tumor_instance_mask"] = arrays["tumor_instance_mask"] * 0
    arrays["tumor_union_mask"] = arrays["tumor_union_mask"] * 0
    negative_payload = replace(
        negative_payload,
        dataset_id="PAR-S-negative-v2-test",
        profile_id="negative_control_v2",
        arrays=arrays,
        population_weight=0.0,
    )
    negative_record = write_case_v2(negative_payload, negative_root)
    negative_contract = DatasetContractV2(
        output_root=negative_root,
        dataset_id="PAR-S-negative-v2-test",
        dataset_version="2.0.0-test",
        dataset_role="negative",
        expected_case_ids=(negative_record.case_id,),
        allowed_profile_ids=("negative_control_v2",),
        split_plan_sha256=negative_plan.sha256,
        required_artifact_names=("phantom_npz", "metadata_json"),
    )
    freeze_dataset(negative_records := (negative_record,), negative_contract)
    assert (negative_root / "DATASET_COMPLETE.json").is_file()
    assert not (main_root / "DATASET_COMPLETE.json").exists()
    assert main_plan.dataset_id != negative_plan.dataset_id
    assert all(record.population_weight == 0 for record in negative_records)


def test_generate_cli_writes_family_split_and_generation_plan_before_cases(tmp_path: Path) -> None:
    output = tmp_path / "planned"
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "generate_dataset_v2.py"),
            "--output-root",
            str(output),
            "--case-count",
            "10",
            "--family-size",
            "2",
            "--global-seed",
            "1234",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "planned"
    assert result["written_case_count"] == 0
    assert (output / "SPLIT_PLAN.json").is_file()
    generation = json.loads((output / "GENERATION_PLAN.json").read_text(encoding="utf-8"))
    assert len(generation["entries"]) == 10
    family_splits: dict[str, set[str]] = {}
    for entry in generation["entries"]:
        family_splits.setdefault(entry["case_family_id"], set()).add(entry["split"])
    assert all(len(splits) == 1 for splits in family_splits.values())
    assert not (output / "cases").exists()


def test_cli_ingest_then_freeze_requires_and_binds_simind_artifacts(tmp_path: Path) -> None:
    output = tmp_path / "dataset"
    staging_case = tmp_path / "staging" / "case_00000"
    staging_case.mkdir(parents=True)
    payload = make_payload("case_00000", family_id="family_00000")
    npz_path = staging_case / "phantom.npz"
    import numpy as np

    np.savez_compressed(npz_path, **payload.arrays)
    artifact_files = {
        "projection_a00": "projection.a00",
        "projection_mhd": "projection.mhd",
        "projection_res": "projection.res",
        "projection_spe": "projection.spe",
        "simind_run_provenance": "run_provenance.json",
    }
    for index, relative in enumerate(artifact_files.values(), start=1):
        (staging_case / relative).write_bytes(f"artifact-{index}".encode("ascii"))
    metadata = _metadata(case_id="case_00000")
    for artifact_name, suffix in (
        ("projection_a00", "a00"),
        ("projection_mhd", "mhd"),
        ("projection_res", "res"),
        ("projection_spe", "spe"),
    ):
        metadata["simulation"]["output_sha256"][suffix] = sha256_file(
            staging_case / artifact_files[artifact_name]
        )
    (staging_case / "payload.json").write_text(
        json.dumps(
            {"metadata": metadata, "extra_artifacts": artifact_files},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    generated = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "generate_dataset_v2.py"),
            "--output-root",
            str(output),
            "--case-count",
            "1",
            "--staging-root",
            str(tmp_path / "staging"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert generated.returncode == 0, generated.stderr
    assert json.loads(generated.stdout)["written_case_count"] == 1

    frozen = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "freeze_dataset_v2.py"),
            "--output-root",
            str(output),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert frozen.returncode == 0, frozen.stderr
    marker = json.loads((output / "DATASET_COMPLETE.json").read_text(encoding="utf-8"))
    assert marker["status"] == "complete"
    assert set(marker["required_artifact_names"]) == {
        "phantom_npz",
        "metadata_json",
        "projection_a00",
        "projection_mhd",
        "projection_res",
        "projection_spe",
        "simind_run_provenance",
    }
