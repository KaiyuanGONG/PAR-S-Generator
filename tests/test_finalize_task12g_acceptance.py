from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from finalize_task12g_acceptance import (  # noqa: E402
    AcceptanceConfig,
    StageCommand,
    Task12GAcceptanceError,
    _run_stage,
    _stage_can_resume,
    _stage_state,
    build_final_summary,
    build_stage_commands,
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
    dataset = tmp_path / "dataset"
    qa = tmp_path / "qa"
    coordinate = tmp_path / "coordinate.json"
    for path in (generator / "scripts", pars2 / "scripts", dataset):
        path.mkdir(parents=True, exist_ok=True)
    for path in (
        generator / "scripts" / "audit_task12g_linux50.py",
        pars2 / "scripts" / "validate_synthetic_dataset.py",
        pars2 / "scripts" / "build_projection_alignment_descriptor.py",
        pars2 / "scripts" / "search_projection_transform.py",
        pars2 / "scripts" / "evaluate_task12b_gates.py",
    ):
        path.write_text("# script\n", encoding="utf-8")
    coordinate.write_text("{}\n", encoding="utf-8")
    return AcceptanceConfig(
        python_executable=Path(sys.executable),
        generator_root=generator,
        pars2_root=pars2,
        dataset_root=dataset,
        qa_root=qa,
        coordinate_report=coordinate,
        device="cpu",
    )


def test_stage_commands_use_exact_cross_repository_contract(tmp_path: Path) -> None:
    config = _config(tmp_path)

    stages = build_stage_commands(config)

    assert [stage.name for stage in stages] == [
        "generator_statistics_visual_gate",
        "pars2_manifest_loader_gate",
        "clinical_alignment_descriptor",
        "clinical_alignment_exploratory",
        "task12b_projection_gates",
    ]
    assert "--expected-count" in stages[1].command
    assert stages[1].command[stages[1].command.index("--expected-count") + 1] == "50"
    assert "--report-role" in stages[3].command
    assert (
        stages[3].command[stages[3].command.index("--report-role") + 1]
        == "clinical-exploratory"
    )
    assert str(config.coordinate_report.resolve()) in stages[4].command
    assert all(stage.cwd in {config.generator_root, config.pars2_root} for stage in stages)
    assert stages[0].accepted_return_codes == (0, 1)
    assert stages[1].accepted_return_codes == (0, 1)
    assert stages[4].accepted_return_codes == (0, 2)


def test_stage_resume_requires_matching_command_script_and_output_hashes(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    stage = build_stage_commands(config)[0]
    output = config.qa_root / "generator_gate.json"
    output.parent.mkdir(parents=True)
    output.write_text('{"status":"pass"}\n', encoding="utf-8")
    state = {
        "status": "complete",
        "command": list(stage.command),
        "script_sha256": _sha256(stage.script_path),
        "return_code": 0,
        "formal_result_status": "pass",
        "outputs": {str(output.resolve()): _sha256(output)},
    }

    assert _stage_can_resume(stage, state) is True

    output.write_text('{"status":"fail"}\n', encoding="utf-8")
    assert _stage_can_resume(stage, state) is False


def test_formal_gate_failure_is_a_completed_stage_with_fresh_fail_report(
    tmp_path: Path,
) -> None:
    script = tmp_path / "formal_gate.py"
    output = tmp_path / "formal_gate.json"
    script.write_text("# script\n", encoding="utf-8")
    output.write_text('{"status":"fail"}\n', encoding="utf-8")
    stage = StageCommand(
        name="formal_gate",
        command=(sys.executable, str(script)),
        cwd=tmp_path,
        script_path=script,
        output_paths=(output,),
        accepted_return_codes=(0, 1),
        expected_status_by_return_code=((0, "pass"), (1, "fail")),
    )

    state = _stage_state(stage, 1)

    assert state["status"] == "complete"
    assert state["return_code"] == 1
    assert state["formal_result_status"] == "fail"
    assert state["outputs"][str(output.resolve())] == _sha256(output)
    assert _stage_can_resume(stage, state) is True


def test_formal_gate_failure_without_required_report_is_execution_failure(
    tmp_path: Path,
) -> None:
    script = tmp_path / "formal_gate.py"
    output = tmp_path / "formal_gate.json"
    script.write_text("# script\n", encoding="utf-8")
    stage = StageCommand(
        name="formal_gate",
        command=(sys.executable, str(script)),
        cwd=tmp_path,
        script_path=script,
        output_paths=(output,),
        accepted_return_codes=(0, 1),
        expected_status_by_return_code=((0, "pass"), (1, "fail")),
    )

    with pytest.raises(Task12GAcceptanceError, match="required output"):
        _stage_state(stage, 1)


def test_run_stage_streams_stdout_and_stderr_to_logs(tmp_path: Path) -> None:
    script = tmp_path / "emit.py"
    script.write_text(
        "import sys\nprint('progress-line', flush=True)\n"
        "print('warning-line', file=sys.stderr, flush=True)\n",
        encoding="utf-8",
    )
    stage = StageCommand(
        name="emit",
        command=(sys.executable, str(script)),
        cwd=tmp_path,
        script_path=script,
        output_paths=(),
    )

    return_code = _run_stage(stage, tmp_path / "logs")

    assert return_code == 0
    assert (tmp_path / "logs" / "emit.stdout.log").read_text(
        encoding="utf-8"
    ) == "progress-line\n"
    assert (tmp_path / "logs" / "emit.stderr.log").read_text(
        encoding="utf-8"
    ) == "warning-line\n"


def _write_summary_inputs(
    config: AcceptanceConfig,
    *,
    generator_status: str = "pass",
    loader_status: str = "pass",
    coordinate_status: str = "pass",
    quality_status: str = "pass",
    exploratory_status: str = "diagnostic_nonunique",
) -> None:
    manifest_sha = "d44a77f4604bc1df192c6af0674341c704d4e069d1665e1fe159832adfeae722"
    _write_json(
        config.dataset_root / "DATASET_COMPLETE.json",
        {
            "schema_version": "pars_dataset_freeze_v2",
            "status": "complete",
            "dataset_id": "PAR-S-TARE-HCC-NoPVI-SYN-v2-linux50",
            "dataset_version": "2.0.0-linux50-v2",
            "dataset_role": "main",
            "case_count": 50,
            "manifest_sha256": manifest_sha,
        },
    )
    _write_json(
        config.dataset_root / "TASK12G_GENERATION_GATE.json",
        {
            "status": "ready_for_dataset_freeze",
            "case_count": 50,
            "absolute_projection_scale_retained": True,
            "linux_only": True,
            "go_for_500_case_generation": False,
        },
    )
    _write_json(
        config.qa_root / "generator_gate.json",
        {
            "schema_version": "generator_v1",
            "status": generator_status,
            "dataset_id": "PAR-S-TARE-HCC-NoPVI-SYN-v2-linux50",
            "manifest_sha256": manifest_sha,
            "focus_cases": [{"case_id": "case_00000", "reasons": ["challenge"]}],
        },
    )
    _write_json(
        config.qa_root / "loader_gate.json",
        {
            "schema_version": "loader_v1",
            "status": loader_status,
            "dataset_id": "PAR-S-TARE-HCC-NoPVI-SYN-v2-linux50",
            "manifest_sha256": manifest_sha,
        },
    )
    _write_json(
        config.qa_root / "clinical_alignment_exploratory.json",
        {
            "schema_version": "pars_projection_alignment_report_v1",
            "report_classification": {
                "schema_version": "clinical_alignment_exploratory_report_v1",
                "role": "clinical-exploratory",
                "blocking": False,
            },
            "freeze_gate": {"passed": exploratory_status == "diagnostic_unique"},
        },
    )
    _write_json(
        config.qa_root / "task12b_gate_summary.json",
        {
            "schema_version": "pars_v2_task12b_gate_summary_v1",
            "status": (
                "pass"
                if coordinate_status == quality_status == "pass"
                else "fail"
            ),
            "gates": {
                "projection_coordinate_gate_v2": {
                    "schema_version": "projection_coordinate_gate_v2",
                    "status": coordinate_status,
                },
                "clinical_projection_quality_gate_v1": {
                    "schema_version": "clinical_projection_quality_gate_v1",
                    "status": quality_status,
                    "dataset_id": "PAR-S-TARE-HCC-NoPVI-SYN-v2-linux50",
                    "manifest_sha256": manifest_sha,
                },
            },
            "diagnostics": {
                "clinical_alignment_exploratory_report_v1": {
                    "schema_version": "clinical_alignment_exploratory_report_v1",
                    "status": exploratory_status,
                    "blocking": False,
                }
            },
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
            },
        },
    )


def test_final_summary_preserves_formal_status_and_blocks_500(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _write_summary_inputs(config, exploratory_status="diagnostic_nonunique")

    summary = build_final_summary(config)

    assert summary["status"] == "pass_awaiting_manual_review"
    assert summary["automatic_gate_passed"] is True
    assert summary["manual_review_status"] == "pending"
    assert summary["go_for_500_case_generation"] is False
    statuses = {row["gate_id"]: row["status"] for row in summary["gate_rows"]}
    assert statuses["clinical_alignment_exploratory_report_v1"] == (
        "diagnostic_nonunique"
    )


def test_final_summary_cannot_hide_loader_failure(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _write_summary_inputs(config, loader_status="fail")

    summary = build_final_summary(config)

    assert summary["status"] == "fail"
    assert summary["automatic_gate_passed"] is False
    assert summary["go_for_500_case_generation"] is False


def test_final_summary_rejects_manifest_binding_mismatch(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _write_summary_inputs(config)
    loader = json.loads((config.qa_root / "loader_gate.json").read_text(encoding="utf-8"))
    loader["manifest_sha256"] = "a" * 64
    _write_json(config.qa_root / "loader_gate.json", loader)

    with pytest.raises(Task12GAcceptanceError, match="manifest"):
        build_final_summary(config)
