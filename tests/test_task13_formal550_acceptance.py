from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import finalize_task13_formal550_acceptance as acceptance  # noqa: E402
from finalize_task13_formal550_acceptance import (  # noqa: E402
    AcceptanceConfig,
    Formal550AcceptanceError,
    StageCommand,
    _negative_semantics_gates,
    _projection_metrics,
    _require_manifest_record_binding,
    _run_stage,
    _stage_can_resume,
    _stage_state,
    _validate_campaign_documents,
    _validate_coordinate_gate,
    build_final_summary,
    build_stage_commands,
    evidence_row,
    run_acceptance_pipeline,
    select_focus_cases,
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _config(tmp_path: Path) -> AcceptanceConfig:
    generator = tmp_path / "generator"
    pars2 = tmp_path / "pars2"
    campaign = tmp_path / "campaign"
    qa = tmp_path / "qa"
    coordinate = tmp_path / "coordinate.json"
    (generator / "scripts").mkdir(parents=True)
    (pars2 / "scripts").mkdir(parents=True)
    campaign.mkdir()
    (pars2 / "scripts" / "validate_synthetic_dataset.py").write_text(
        "# loader\n", encoding="utf-8"
    )
    coordinate.write_text("{}\n", encoding="utf-8")
    return AcceptanceConfig(
        python_executable=Path(sys.executable),
        generator_root=generator,
        pars2_root=pars2,
        campaign_root=campaign,
        qa_root=qa,
        coordinate_report=coordinate,
    )


def test_role_loader_commands_are_exact_and_use_500_and_50(tmp_path: Path) -> None:
    config = _config(tmp_path)

    stages = build_stage_commands(config)

    assert [stage.name for stage in stages] == [
        "formal550_main_loader_gate",
        "formal550_negative_loader_gate",
    ]
    for stage, role, expected_count in zip(
        stages, ("main", "negative"), ("500", "50")
    ):
        assert stage.command == (
            str(config.python_executable.resolve()),
            str(
                (
                    config.pars2_root
                    / "scripts"
                    / "validate_synthetic_dataset.py"
                ).resolve()
            ),
            "--dataset-root",
            str((config.campaign_root / role).resolve()),
            "--expected-count",
            expected_count,
            "--gate-json",
            str((config.qa_root / f"{role}_loader_gate.json").resolve()),
            "--gate-markdown",
            str((config.qa_root / f"{role}_loader_gate.md").resolve()),
            "--alignment-json",
            str((config.qa_root / f"{role}_loader_alignment.json").resolve()),
        )
        assert stage.cwd == config.pars2_root.resolve()
        assert stage.accepted_return_codes == (0, 1)
        assert stage.expected_status_by_return_code == ((0, "pass"), (1, "fail"))


def _campaign_documents() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    main_sha = "1" * 64
    negative_sha = "2" * 64
    campaign = {
        "schema_version": "pars_v2_task13_formal550_complete_v1",
        "status": "complete",
        "campaign": {
            "dataset_id": "PAR-S-V2-FORMAL550",
            "dataset_version": "2.0.0",
        },
        "case_count": 550,
        "role_case_counts": {"main": 500, "negative": 50},
        "datasets": {
            "main": {"relative_root": "main", "manifest_sha256": main_sha},
            "negative": {
                "relative_root": "negative",
                "manifest_sha256": negative_sha,
            },
        },
    }
    common = {
        "schema_version": "pars_dataset_freeze_v2",
        "status": "complete",
        "dataset_version": "2.0.0",
        "manifest_relative_path": "case_manifest.jsonl",
        "split_plan_sha256": "3" * 64,
        "contract_sha256": "4" * 64,
        "required_artifact_names": ["phantom_npz", "projection_a00"],
        "projection_coordinate_contract_id": "pars_simind_v8_xcat_zyx_sar_v1",
        "loader_transform_id": (
            "simind_v8_xcat_v1_views_forward_roll000_det_v_flip_det_u_keep"
        ),
        "frozen_utc": "2026-07-24T00:00:00Z",
    }
    main = {
        **common,
        "dataset_id": "PAR-S-TARE-HCC-NoPVI-SYN-v2",
        "dataset_role": "main",
        "case_count": 500,
        "split_counts": {"train": 400, "val": 50, "test": 50},
        "manifest_sha256": main_sha,
    }
    negative = {
        **common,
        "dataset_id": "PAR-S-TARE-HCC-NoPVI-NEG-v2",
        "dataset_role": "negative",
        "case_count": 50,
        "split_counts": {"train": 0, "val": 0, "test": 50},
        "manifest_sha256": negative_sha,
    }
    return campaign, main, negative


def test_campaign_binding_requires_exact_role_id_counts_splits_and_manifests() -> None:
    campaign, main, negative = _campaign_documents()

    result = _validate_campaign_documents(campaign, main, negative)

    assert result == {
        "main": {
            "dataset_id": "PAR-S-TARE-HCC-NoPVI-SYN-v2",
            "manifest_sha256": "1" * 64,
            "case_count": 500,
            "split_counts": {"train": 400, "val": 50, "test": 50},
        },
        "negative": {
            "dataset_id": "PAR-S-TARE-HCC-NoPVI-NEG-v2",
            "manifest_sha256": "2" * 64,
            "case_count": 50,
            "split_counts": {"train": 0, "val": 0, "test": 50},
        },
    }


@pytest.mark.parametrize(
    ("document", "path", "value", "message"),
    [
        ("campaign", ("case_count",), 549, "campaign"),
        (
            "campaign",
            ("datasets", "main", "manifest_sha256"),
            "9" * 64,
            "manifest",
        ),
        ("main", ("split_counts", "train"), 399, "split"),
        ("negative", ("split_counts", "val"), 1, "split"),
    ],
)
def test_campaign_binding_fails_closed_on_drift(
    document: str,
    path: tuple[str, ...],
    value: object,
    message: str,
) -> None:
    campaign, main, negative = _campaign_documents()
    target = {"campaign": campaign, "main": main, "negative": negative}[document]
    cursor = target
    for key in path[:-1]:
        cursor = cursor[key]  # type: ignore[index,assignment]
    cursor[path[-1]] = value  # type: ignore[index]

    with pytest.raises(Formal550AcceptanceError, match=message):
        _validate_campaign_documents(campaign, main, negative)


def test_manifest_row_must_equal_the_loaded_record_not_only_its_case_id() -> None:
    manifest_row = {"schema_version": "pars_case_record_v2", "case_id": "case_00001"}
    record = SimpleNamespace(
        case_id="case_00001",
        to_dict=lambda: {
            "schema_version": "pars_case_record_v2",
            "case_id": "case_00000",
        },
    )

    with pytest.raises(Formal550AcceptanceError, match="manifest record"):
        _require_manifest_record_binding(record, manifest_row)


def test_negative_semantics_require_test_only_zero_weight_and_zero_tumor() -> None:
    record = SimpleNamespace(
        split="test",
        population_weight=0.0,
        profile_id="negative_control_v2",
    )
    metadata = {
        "actual_metrics": {
            "tumors": {
                "realized_count": 0,
                "tumor_union_volume_ml": 0.0,
                "tumor_union_fraction_liver": 0.0,
                "tumor_union_fraction_perfused": 0.0,
                "lesions": [],
            }
        },
        "activity": {"mismatch_challenge": False},
    }
    arrays = {
        "tumor_union_mask": np.zeros((2, 3, 4), dtype=np.uint8),
        "tumor_instance_mask": np.zeros((2, 3, 4), dtype=np.uint16),
    }

    assert all(_negative_semantics_gates(record, metadata, arrays).values())

    arrays["tumor_union_mask"][0, 0, 0] = 1
    assert _negative_semantics_gates(record, metadata, arrays)[
        "zero_tumor_masks"
    ] is False


def test_negative_semantics_match_completed_metadata_without_volume_field() -> None:
    record = SimpleNamespace(
        split="test",
        population_weight=0.0,
        profile_id="negative_control_v2",
    )
    metadata = {
        "actual_metrics": {
            "tumors": {
                "count_bin": "0",
                "realized_count": 0,
                "lobe_extent": "none",
                "tumor_union_fraction_liver": 0.0,
                "tumor_union_fraction_perfused": 0.0,
                "lesions": [],
            }
        },
        "activity": {"mismatch_challenge": False},
    }
    arrays = {
        "tumor_union_mask": np.zeros((2, 3, 4), dtype=np.uint8),
        "tumor_instance_mask": np.zeros((2, 3, 4), dtype=np.uint16),
    }

    assert all(_negative_semantics_gates(record, metadata, arrays).values())


def _projection_with_ratio(ratio: float) -> np.ndarray:
    projection = np.ones((60, 128, 128), dtype=np.float32)
    projection[:, :8, :] = 0.0
    projection[:, -8:, :] = 0.0
    projection[:, :, :8] = 0.0
    projection[:, :, -8:] = 0.0
    projection[-1] *= ratio
    return projection


def test_projection_gate_requires_shape_support_and_ratio_at_most_80() -> None:
    metrics = _projection_metrics(_projection_with_ratio(80.0))

    assert metrics["shape"] == [60, 128, 128]
    assert metrics["view_sum_ratio"] == pytest.approx(80.0)
    assert metrics["minimum_positive_bin_fraction_per_view"] > 0.001
    assert metrics["outer_8px_count_fraction"] == 0.0
    assert all(metrics["gates"].values())

    failed = _projection_metrics(_projection_with_ratio(80.0001))
    assert failed["gates"]["view_sum_ratio_at_most_80"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.__setitem__((0, 64, 64), -1.0),
        lambda value: value.__setitem__((0, slice(None), slice(None)), 0.0),
    ],
)
def test_projection_gate_fails_closed_on_invalid_bins_or_empty_view(mutation) -> None:
    projection = _projection_with_ratio(1.0)
    mutation(projection)

    with pytest.raises(Formal550AcceptanceError, match="projection"):
        _projection_metrics(projection)


def test_focus_cases_are_deterministic_and_include_each_role() -> None:
    rows = [
        {
            "case_id": "case_00001",
            "dataset_role": "main",
            "projection_weight_sum": 12.0,
            "view_sum_ratio": 5.0,
            "status": "pass",
        },
        {
            "case_id": "case_00000",
            "dataset_role": "main",
            "projection_weight_sum": 8.0,
            "view_sum_ratio": 2.0,
            "status": "fail",
        },
        {
            "case_id": "negative_00000",
            "dataset_role": "negative",
            "projection_weight_sum": 4.0,
            "view_sum_ratio": 3.0,
            "status": "pass",
        },
    ]

    first = select_focus_cases(rows)
    second = select_focus_cases(list(reversed(rows)))

    assert first == second
    assert first[0] == {
        "case_id": "case_00000",
        "dataset_role": "main",
        "reasons": [
            "automatic_gate_attention",
            "minimum_projection_total",
            "minimum_view_sum_ratio",
        ],
    }
    assert any(row["dataset_role"] == "negative" for row in first)


def test_coordinate_evidence_must_be_the_frozen_blocking_pass_gate() -> None:
    coordinate = {
        "schema_version": "pars_projection_alignment_report_v1",
        "report_classification": {
            "schema_version": "projection_coordinate_gate_v2",
            "role": "projection-coordinate-gate",
            "blocking": True,
            "transform_uniqueness_required": True,
        },
        "freeze_gate": {
            "passed": True,
            "frozen_transform_recovered": True,
        },
        "projection_coordinates": {
            "coordinate_contract_id": "pars_simind_v8_xcat_zyx_sar_v1",
            "loader_transform_id": (
                "simind_v8_xcat_v1_views_forward_roll000_det_v_flip_det_u_keep"
            ),
        },
    }

    normalized = _validate_coordinate_gate(coordinate)

    assert normalized["schema_version"] == "projection_coordinate_gate_v2"
    assert normalized["status"] == "pass"
    assert normalized["blocking"] is True

    coordinate["freeze_gate"]["passed"] = False
    with pytest.raises(Formal550AcceptanceError, match="coordinate"):
        _validate_coordinate_gate(coordinate)


def test_resume_requires_command_script_return_status_and_every_output_hash(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    stage = build_stage_commands(config)[0]
    for output in stage.output_paths:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text('{"status":"pass"}\n', encoding="utf-8")
    state = {
        "status": "complete",
        "command": list(stage.command),
        "script_sha256": _sha256(stage.script_path),
        "return_code": 0,
        "formal_result_status": "pass",
        "outputs": {
            str(output.resolve()): _sha256(output) for output in stage.output_paths
        },
    }

    assert _stage_can_resume(stage, state) is True

    stage.output_paths[0].write_text('{"status":"fail"}\n', encoding="utf-8")
    assert _stage_can_resume(stage, state) is False


def test_stage_failure_is_recorded_with_logs_and_fresh_fail_evidence(
    tmp_path: Path,
) -> None:
    script = tmp_path / "loader.py"
    gate = tmp_path / "loader.json"
    markdown = tmp_path / "loader.md"
    alignment = tmp_path / "alignment.json"
    script.write_text(
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(gate)!r}).write_text("
        "json.dumps({'status':'fail'}), encoding='utf-8')\n"
        f"pathlib.Path({str(markdown)!r}).write_text('fail\\n', encoding='utf-8')\n"
        f"pathlib.Path({str(alignment)!r}).write_text("
        "json.dumps({'status':'fail'}), encoding='utf-8')\n"
        "print('loader-out')\nprint('loader-err', file=sys.stderr)\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    stage = SimpleNamespace(
        name="loader",
        command=(sys.executable, str(script)),
        cwd=tmp_path,
        script_path=script,
        output_paths=(gate, markdown, alignment),
        accepted_return_codes=(0, 1),
        expected_status_by_return_code=((0, "pass"), (1, "fail")),
    )

    return_code = _run_stage(stage, tmp_path / "logs")
    state = _stage_state(stage, return_code)

    assert return_code == 1
    assert state["formal_result_status"] == "fail"
    assert (tmp_path / "logs" / "loader.stdout.log").read_text(
        encoding="utf-8"
    ) == "loader-out\n"
    assert (tmp_path / "logs" / "loader.stderr.log").read_text(
        encoding="utf-8"
    ) == "loader-err\n"


def test_stage_process_disables_bytecode_writes_to_external_worktree(
    tmp_path: Path,
) -> None:
    script = tmp_path / "loader.py"
    script.write_text(
        "import sys\nprint(sys.dont_write_bytecode)\n",
        encoding="utf-8",
    )
    stage = SimpleNamespace(
        name="loader",
        command=(sys.executable, str(script)),
        cwd=tmp_path,
        script_path=script,
        output_paths=(),
        accepted_return_codes=(0,),
        expected_status_by_return_code=(),
    )

    assert _run_stage(stage, tmp_path / "logs") == 0
    assert (tmp_path / "logs" / "loader.stdout.log").read_text(
        encoding="utf-8"
    ) == "True\n"
    assert not (tmp_path / "__pycache__").exists()


def _summary_inputs(config: AcceptanceConfig, *, negative_status: str = "pass") -> None:
    campaign, main_marker, negative_marker = _campaign_documents()
    _write_json(config.campaign_root / "FORMAL550_COMPLETE.json", campaign)
    _write_json(config.campaign_root / "main" / "DATASET_COMPLETE.json", main_marker)
    _write_json(
        config.campaign_root / "negative" / "DATASET_COMPLETE.json",
        negative_marker,
    )
    generator_gate = {
        "schema_version": "formal550_generator_gate_v1",
        "status": "pass",
        "case_count": 550,
        "role_case_counts": {"main": 500, "negative": 50},
        "dataset_manifests": {"main": "1" * 64, "negative": "2" * 64},
        "focus_cases": [],
    }
    _write_json(config.qa_root / "generator_gate.json", generator_gate)
    for role, status, dataset_id, manifest, count in (
        (
            "main",
            "pass",
            "PAR-S-TARE-HCC-NoPVI-SYN-v2",
            "1" * 64,
            500,
        ),
        (
            "negative",
            negative_status,
            "PAR-S-TARE-HCC-NoPVI-NEG-v2",
            "2" * 64,
            50,
        ),
    ):
        _write_json(
            config.qa_root / f"{role}_loader_gate.json",
            {
                "schema_version": "pars_v2_synthetic_dataset_gate_v1",
                "status": status,
                "dataset_root": str((config.campaign_root / role).resolve()),
                "manifest_path": str(
                    (
                        config.campaign_root
                        / role
                        / "case_manifest.jsonl"
                    ).resolve()
                ),
                "completion_marker_path": str(
                    (
                        config.campaign_root
                        / role
                        / "DATASET_COMPLETE.json"
                    ).resolve()
                ),
                "expected_count": count,
                "observed_count": count,
                "dataset_id": dataset_id,
                "dataset_version": "2.0.0",
                "dataset_role": role,
                "manifest_sha256": manifest,
            },
        )
    _write_json(
        config.coordinate_report,
        {
            "schema_version": "pars_projection_alignment_report_v1",
            "report_classification": {
                "schema_version": "projection_coordinate_gate_v2",
                "role": "projection-coordinate-gate",
                "blocking": True,
                "transform_uniqueness_required": True,
            },
            "freeze_gate": {
                "passed": True,
                "frozen_transform_recovered": True,
            },
            "projection_coordinates": {
                "coordinate_contract_id": "pars_simind_v8_xcat_zyx_sar_v1",
                "loader_transform_id": (
                    "simind_v8_xcat_v1_views_forward_roll000_det_v_flip_det_u_keep"
                ),
            },
        },
    )


def test_evidence_rows_bind_source_bytes_by_sha256(tmp_path: Path) -> None:
    path = tmp_path / "gate.json"
    _write_json(path, {"schema_version": "gate_v1", "status": "pass"})

    row = evidence_row("formal550_generator_gate_v1", path)

    assert row == {
        "gate_id": "formal550_generator_gate_v1",
        "schema_version": "gate_v1",
        "status": "pass",
        "blocking": True,
        "path": str(path.resolve()),
        "sha256": _sha256(path),
    }


def test_evidence_row_parses_and_hashes_one_byte_snapshot(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "gate.json"
    _write_json(path, {"schema_version": "gate_v1", "status": "fail"})
    monkeypatch.setattr(
        acceptance,
        "_read_json",
        lambda *_args, **_kwargs: {
            "schema_version": "gate_v1",
            "status": "pass",
        },
    )

    row = evidence_row("formal550_generator_gate_v1", path)

    assert row["status"] == "fail"
    assert row["sha256"] == _sha256(path)


def test_final_summary_has_exact_authoritative_contract(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _summary_inputs(config)

    summary = build_final_summary(config)

    assert set(summary) == {
        "schema_version",
        "status",
        "automatic_gate_passed",
        "case_count",
        "role_case_counts",
        "gate_rows",
        "notebook_authority",
    }
    assert summary["schema_version"] == (
        "pars_v2_task13_formal550_automatic_acceptance_v1"
    )
    assert summary["status"] == "pass"
    assert summary["automatic_gate_passed"] is True
    assert summary["case_count"] == 550
    assert summary["role_case_counts"] == {"main": 500, "negative": 50}
    assert [row["gate_id"] for row in summary["gate_rows"]] == [
        "formal550_generator_gate_v1",
        "formal550_main_loader_gate_v1",
        "formal550_negative_loader_gate_v1",
        "projection_coordinate_gate_v2",
    ]
    assert summary["notebook_authority"] == "informational_read_only"


def test_final_aggregation_cannot_hide_a_failed_blocking_gate(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _summary_inputs(config, negative_status="fail")

    summary = build_final_summary(config)

    assert summary["status"] == "fail"
    assert summary["automatic_gate_passed"] is False


def test_final_summary_rejects_non_pars2_loader_schema(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _summary_inputs(config)
    gate_path = config.qa_root / "main_loader_gate.json"
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    gate["schema_version"] = "fake_loader_gate_v1"
    _write_json(gate_path, gate)

    with pytest.raises(Formal550AcceptanceError, match="loader"):
        build_final_summary(config)


def test_generator_audit_exception_writes_failed_progress(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    campaign, main, negative = _campaign_documents()
    _write_json(config.campaign_root / "FORMAL550_COMPLETE.json", campaign)
    _write_json(config.campaign_root / "main" / "DATASET_COMPLETE.json", main)
    _write_json(
        config.campaign_root / "negative" / "DATASET_COMPLETE.json", negative
    )

    def fail_audit(*_args: object, **_kwargs: object) -> object:
        raise Formal550AcceptanceError("artifact audit failed")

    monkeypatch.setattr(acceptance, "audit_formal550", fail_audit)
    stale_json = (
        config.qa_root / "TASK13_FORMAL550_AUTOMATIC_ACCEPTANCE.json"
    )
    stale_markdown = (
        config.qa_root / "TASK13_FORMAL550_AUTOMATIC_ACCEPTANCE.md"
    )
    _write_json(stale_json, {"status": "pass"})
    stale_markdown.write_text("PASS\n", encoding="utf-8")

    with pytest.raises(Formal550AcceptanceError, match="artifact audit failed"):
        run_acceptance_pipeline(config, resume=False)

    progress = json.loads(
        (config.qa_root / "PROGRESS.json").read_text(encoding="utf-8")
    )
    assert progress["status"] == "failed"
    assert progress["current_stage"] == "formal550_generator_gate"
    assert progress["error"] == "artifact audit failed"
    assert not stale_json.exists()
    assert not stale_markdown.exists()


def test_resume_always_reaudits_all_dataset_bytes_before_reusing_loaders(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    campaign, main, negative = _campaign_documents()
    _write_json(config.campaign_root / "FORMAL550_COMPLETE.json", campaign)
    _write_json(config.campaign_root / "main" / "DATASET_COMPLETE.json", main)
    _write_json(
        config.campaign_root / "negative" / "DATASET_COMPLETE.json", negative
    )
    generator_json = config.qa_root / "generator_gate.json"
    generator_markdown = config.qa_root / "generator_gate.md"
    _write_json(
        generator_json,
        {"schema_version": "formal550_generator_gate_v1", "status": "pass"},
    )
    generator_markdown.write_text("pass\n", encoding="utf-8")
    _write_json(
        config.qa_root / "PROGRESS.json",
        {
            "schema_version": acceptance.PROGRESS_SCHEMA,
            "status": "failed",
            "stages": {
                "formal550_generator_gate": {
                    "status": "complete",
                    "return_code": 0,
                    "outputs": {
                        str(generator_json.resolve()): _sha256(generator_json),
                        str(generator_markdown.resolve()): _sha256(
                            generator_markdown
                        ),
                    },
                }
            },
        },
    )
    calls: list[str] = []

    def fresh_audit(*_args: object, **_kwargs: object) -> object:
        calls.append("audit")
        raise Formal550AcceptanceError("fresh artifact audit failed")

    monkeypatch.setattr(acceptance, "audit_formal550", fresh_audit)
    monkeypatch.setattr(acceptance, "build_stage_commands", lambda _config: [])
    monkeypatch.setattr(
        acceptance,
        "build_final_summary",
        lambda _config: {
            "schema_version": acceptance.AUTOMATIC_SCHEMA,
            "status": "pass",
            "automatic_gate_passed": True,
            "case_count": 550,
            "role_case_counts": {"main": 500, "negative": 50},
            "gate_rows": [],
            "notebook_authority": "informational_read_only",
        },
    )

    with pytest.raises(Formal550AcceptanceError, match="fresh artifact audit"):
        run_acceptance_pipeline(config, resume=True)

    assert calls == ["audit"]


def test_resume_reruns_loader_even_when_prior_stage_hashes_match(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    campaign, main, negative = _campaign_documents()
    _write_json(config.campaign_root / "FORMAL550_COMPLETE.json", campaign)
    _write_json(config.campaign_root / "main" / "DATASET_COMPLETE.json", main)
    _write_json(
        config.campaign_root / "negative" / "DATASET_COMPLETE.json", negative
    )
    stage_script = config.pars2_root / "scripts" / "loader.py"
    stage_script.write_text("# loader\n", encoding="utf-8")
    gate = config.qa_root / "loader.json"
    _write_json(gate, {"status": "pass"})
    stage = StageCommand(
        name="formal550_main_loader_gate",
        command=(sys.executable, str(stage_script)),
        cwd=config.pars2_root,
        script_path=stage_script,
        output_paths=(gate,),
        accepted_return_codes=(0, 1),
        expected_status_by_return_code=((0, "pass"), (1, "fail")),
    )
    _write_json(
        config.qa_root / "PROGRESS.json",
        {
            "schema_version": acceptance.PROGRESS_SCHEMA,
            "status": "failed",
            "stages": {
                stage.name: {
                    "status": "complete",
                    "command": list(stage.command),
                    "script_sha256": _sha256(stage.script_path),
                    "return_code": 0,
                    "formal_result_status": "pass",
                    "outputs": {str(gate.resolve()): _sha256(gate)},
                }
            },
        },
    )

    def pass_audit(_campaign: Path, qa: Path) -> dict[str, object]:
        report = {
            "schema_version": "formal550_generator_gate_v1",
            "status": "pass",
        }
        _write_json(qa / "generator_gate.json", report)
        (qa / "generator_gate.md").write_text("pass\n", encoding="utf-8")
        return report

    calls: list[str] = []

    def run_loader(_stage: StageCommand, _logs: Path) -> int:
        calls.append("run")
        _write_json(gate, {"status": "pass"})
        return 0

    monkeypatch.setattr(acceptance, "audit_formal550", pass_audit)
    monkeypatch.setattr(acceptance, "build_stage_commands", lambda _config: [stage])
    monkeypatch.setattr(acceptance, "_run_stage", run_loader)
    monkeypatch.setattr(
        acceptance,
        "build_final_summary",
        lambda _config: {
            "schema_version": acceptance.AUTOMATIC_SCHEMA,
            "status": "pass",
            "automatic_gate_passed": True,
            "case_count": 550,
            "role_case_counts": {"main": 500, "negative": 50},
            "gate_rows": [],
            "notebook_authority": "informational_read_only",
        },
    )

    run_acceptance_pipeline(config, resume=True)

    assert calls == ["run"]


def test_resume_preflight_failure_removes_stale_authoritative_pass(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    campaign, main, negative = _campaign_documents()
    _write_json(config.campaign_root / "FORMAL550_COMPLETE.json", campaign)
    _write_json(config.campaign_root / "main" / "DATASET_COMPLETE.json", main)
    _write_json(
        config.campaign_root / "negative" / "DATASET_COMPLETE.json", negative
    )
    stale_json = (
        config.qa_root / "TASK13_FORMAL550_AUTOMATIC_ACCEPTANCE.json"
    )
    stale_markdown = (
        config.qa_root / "TASK13_FORMAL550_AUTOMATIC_ACCEPTANCE.md"
    )
    _write_json(stale_json, {"status": "pass"})
    stale_markdown.write_text("PASS\n", encoding="utf-8")
    config.coordinate_report.unlink()

    with pytest.raises(Formal550AcceptanceError, match="coordinate report"):
        run_acceptance_pipeline(config, resume=True)

    assert not stale_json.exists()
    assert not stale_markdown.exists()


def test_loader_evidence_exception_writes_failed_progress(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    campaign, main, negative = _campaign_documents()
    _write_json(config.campaign_root / "FORMAL550_COMPLETE.json", campaign)
    _write_json(config.campaign_root / "main" / "DATASET_COMPLETE.json", main)
    _write_json(
        config.campaign_root / "negative" / "DATASET_COMPLETE.json", negative
    )
    stage_script = config.pars2_root / "scripts" / "missing_output_loader.py"
    stage_script.write_text("raise SystemExit(1)\n", encoding="utf-8")
    missing_gate = config.qa_root / "missing_gate.json"
    stage = StageCommand(
        name="formal550_main_loader_gate",
        command=(sys.executable, str(stage_script)),
        cwd=config.pars2_root,
        script_path=stage_script,
        output_paths=(missing_gate,),
        accepted_return_codes=(0, 1),
        expected_status_by_return_code=((0, "pass"), (1, "fail")),
    )

    def pass_audit(_campaign: Path, qa: Path) -> dict[str, object]:
        report = {
            "schema_version": "formal550_generator_gate_v1",
            "status": "pass",
        }
        _write_json(qa / "generator_gate.json", report)
        (qa / "generator_gate.md").write_text("pass\n", encoding="utf-8")
        return report

    monkeypatch.setattr(acceptance, "audit_formal550", pass_audit)
    monkeypatch.setattr(acceptance, "build_stage_commands", lambda _config: [stage])

    with pytest.raises(Formal550AcceptanceError, match="required output"):
        run_acceptance_pipeline(config, resume=False)

    progress = json.loads(
        (config.qa_root / "PROGRESS.json").read_text(encoding="utf-8")
    )
    assert progress["status"] == "failed"
    assert progress["current_stage"] == "formal550_main_loader_gate"
    assert "required output" in progress["error"]


def test_final_binding_exception_writes_failed_progress(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    campaign, main, negative = _campaign_documents()
    _write_json(config.campaign_root / "FORMAL550_COMPLETE.json", campaign)
    _write_json(config.campaign_root / "main" / "DATASET_COMPLETE.json", main)
    _write_json(
        config.campaign_root / "negative" / "DATASET_COMPLETE.json", negative
    )

    def pass_audit(_campaign: Path, qa: Path) -> dict[str, object]:
        report = {
            "schema_version": "formal550_generator_gate_v1",
            "status": "pass",
        }
        _write_json(qa / "generator_gate.json", report)
        (qa / "generator_gate.md").write_text("pass\n", encoding="utf-8")
        return report

    def fail_summary(_config: AcceptanceConfig) -> dict[str, object]:
        raise Formal550AcceptanceError("final manifest binding failed")

    monkeypatch.setattr(acceptance, "audit_formal550", pass_audit)
    monkeypatch.setattr(acceptance, "build_stage_commands", lambda _config: [])
    monkeypatch.setattr(acceptance, "build_final_summary", fail_summary)

    with pytest.raises(Formal550AcceptanceError, match="final manifest binding"):
        run_acceptance_pipeline(config, resume=False)

    progress = json.loads(
        (config.qa_root / "PROGRESS.json").read_text(encoding="utf-8")
    )
    assert progress["status"] == "failed"
    assert progress["current_stage"] == "automatic_acceptance_summary"
    assert progress["error"] == "final manifest binding failed"


def test_markdown_publication_failure_removes_authoritative_json(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    campaign, main, negative = _campaign_documents()
    _write_json(config.campaign_root / "FORMAL550_COMPLETE.json", campaign)
    _write_json(config.campaign_root / "main" / "DATASET_COMPLETE.json", main)
    _write_json(
        config.campaign_root / "negative" / "DATASET_COMPLETE.json", negative
    )

    def pass_audit(_campaign: Path, qa: Path) -> dict[str, object]:
        report = {
            "schema_version": "formal550_generator_gate_v1",
            "status": "pass",
        }
        _write_json(qa / "generator_gate.json", report)
        (qa / "generator_gate.md").write_text("pass\n", encoding="utf-8")
        return report

    summary = {
        "schema_version": acceptance.AUTOMATIC_SCHEMA,
        "status": "pass",
        "automatic_gate_passed": True,
        "case_count": 550,
        "role_case_counts": {"main": 500, "negative": 50},
        "gate_rows": [],
        "notebook_authority": "informational_read_only",
    }
    monkeypatch.setattr(acceptance, "audit_formal550", pass_audit)
    monkeypatch.setattr(acceptance, "build_stage_commands", lambda _config: [])
    monkeypatch.setattr(
        acceptance, "build_final_summary", lambda _config: summary
    )
    monkeypatch.setattr(
        acceptance,
        "atomic_write_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("markdown disk failure")
        ),
    )

    with pytest.raises(OSError, match="markdown disk failure"):
        run_acceptance_pipeline(config, resume=False)

    assert not (
        config.qa_root / "TASK13_FORMAL550_AUTOMATIC_ACCEPTANCE.json"
    ).exists()
    assert not (
        config.qa_root / "TASK13_FORMAL550_AUTOMATIC_ACCEPTANCE.md"
    ).exists()
