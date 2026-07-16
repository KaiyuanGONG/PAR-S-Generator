#!/usr/bin/env python
"""Run the local automatic Task 12G acceptance pipeline without manual approval."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from core.provenance import (  # noqa: E402
    atomic_write_bytes,
    atomic_write_json,
    sha256_file,
)
from core.task12g_acceptance import (  # noqa: E402
    build_automatic_summary,
    ensure_qa_root_outside_dataset,
    gate_evidence_rows,
)


DEFAULT_DATASET_ROOT = Path(r"D:\PFE-U\PAR\outputs\pars_v2_linux50_v2")
DEFAULT_QA_ROOT = Path(r"D:\PFE-U\PAR\outputs\pars_v2_linux50_v2_qa")
DEFAULT_PARS2_ROOT = Path(r"D:\PFE-U\PAR\.worktrees\PAR-S_2-task12")
DEFAULT_COORDINATE_REPORT = Path(
    r"D:\PFE-U\PAR\outputs\task12e_linux_qa_v3"
    r"\linux_projection_coordinate_report.json"
)
EXPECTED_DATASET_ID = "PAR-S-TARE-HCC-NoPVI-SYN-v2-linux50"
EXPECTED_MANIFEST_SHA256 = (
    "d44a77f4604bc1df192c6af0674341c704d4e069d1665e1fe159832adfeae722"
)
PROGRESS_SCHEMA = "pars_v2_task12g_acceptance_progress_v1"
TASK12G_ALIGNMENT_CASE_IDS = tuple(f"case_{index:05d}" for index in range(15))


class Task12GAcceptanceError(RuntimeError):
    """Raised when automatic acceptance evidence is missing or inconsistent."""


@dataclass(frozen=True)
class AcceptanceConfig:
    python_executable: Path
    generator_root: Path
    pars2_root: Path
    dataset_root: Path
    qa_root: Path
    coordinate_report: Path
    device: str = "auto"

    def resolved(self) -> "AcceptanceConfig":
        return AcceptanceConfig(
            python_executable=self.python_executable.resolve(),
            generator_root=self.generator_root.resolve(),
            pars2_root=self.pars2_root.resolve(),
            dataset_root=self.dataset_root.resolve(),
            qa_root=self.qa_root.resolve(),
            coordinate_report=self.coordinate_report.resolve(),
            device=self.device,
        )


@dataclass(frozen=True)
class StageCommand:
    name: str
    command: tuple[str, ...]
    cwd: Path
    script_path: Path
    output_paths: tuple[Path, ...]
    accepted_return_codes: tuple[int, ...] = (0,)
    expected_status_by_return_code: tuple[tuple[int, str], ...] = ()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Task12GAcceptanceError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise Task12GAcceptanceError(f"{label} must contain a JSON object")
    return value


def _json_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_stage_commands(config: AcceptanceConfig) -> list[StageCommand]:
    """Return deterministic cross-repository commands in dependency order."""

    cfg = config.resolved()
    generator_audit = cfg.generator_root / "scripts" / "audit_task12g_linux50.py"
    loader_script = cfg.pars2_root / "scripts" / "validate_synthetic_dataset.py"
    descriptor_script = (
        cfg.pars2_root / "scripts" / "build_projection_alignment_descriptor.py"
    )
    search_script = cfg.pars2_root / "scripts" / "search_projection_transform.py"
    task12b_script = cfg.pars2_root / "scripts" / "evaluate_task12b_gates.py"
    python = str(cfg.python_executable)
    generator_gate = cfg.qa_root / "generator_gate.json"
    loader_gate = cfg.qa_root / "loader_gate.json"
    loader_markdown = cfg.qa_root / "loader_gate.md"
    loader_alignment = cfg.qa_root / "loader_alignment.json"
    descriptor = cfg.qa_root / "projection_alignment_cases.json"
    exploratory = cfg.qa_root / "clinical_alignment_exploratory.json"
    task12b_json = cfg.qa_root / "task12b_gate_summary.json"
    task12b_markdown = cfg.qa_root / "task12b_gate_summary.md"
    return [
        StageCommand(
            name="generator_statistics_visual_gate",
            command=(
                python,
                str(generator_audit),
                "--dataset-root",
                str(cfg.dataset_root),
                "--qa-root",
                str(cfg.qa_root),
            ),
            cwd=cfg.generator_root,
            script_path=generator_audit,
            output_paths=(generator_gate,),
            accepted_return_codes=(0, 1),
            expected_status_by_return_code=((0, "pass"), (1, "fail")),
        ),
        StageCommand(
            name="pars2_manifest_loader_gate",
            command=(
                python,
                str(loader_script),
                "--dataset-root",
                str(cfg.dataset_root),
                "--expected-count",
                "50",
                "--gate-json",
                str(loader_gate),
                "--gate-markdown",
                str(loader_markdown),
                "--alignment-json",
                str(loader_alignment),
            ),
            cwd=cfg.pars2_root,
            script_path=loader_script,
            output_paths=(loader_gate,),
            accepted_return_codes=(0, 1),
            expected_status_by_return_code=((0, "pass"), (1, "fail")),
        ),
        StageCommand(
            name="clinical_alignment_descriptor",
            command=(
                python,
                str(descriptor_script),
                "--dataset-root",
                str(cfg.dataset_root),
                *tuple(
                    value
                    for case_id in TASK12G_ALIGNMENT_CASE_IDS
                    for value in ("--case-id", case_id)
                ),
                "--output",
                str(descriptor),
            ),
            cwd=cfg.pars2_root,
            script_path=descriptor_script,
            output_paths=(descriptor,),
        ),
        StageCommand(
            name="clinical_alignment_exploratory",
            command=(
                python,
                str(search_script),
                str(descriptor),
                "--output",
                str(exploratory),
                "--device",
                cfg.device,
                "--minimum-score-margin",
                "0.005",
                "--minimum-bootstrap-top1-frequency",
                "0.95",
                "--minimum-case-top1-frequency",
                "1.0",
                "--report-role",
                "clinical-exploratory",
            ),
            cwd=cfg.pars2_root,
            script_path=search_script,
            output_paths=(exploratory,),
        ),
        StageCommand(
            name="task12b_projection_gates",
            command=(
                python,
                str(task12b_script),
                str(cfg.dataset_root),
                "--generator-gate",
                str(generator_gate),
                "--loader-gate",
                str(loader_gate),
                "--coordinate-report",
                str(cfg.coordinate_report),
                "--clinical-report",
                str(exploratory),
                "--output-json",
                str(task12b_json),
                "--output-markdown",
                str(task12b_markdown),
            ),
            cwd=cfg.pars2_root,
            script_path=task12b_script,
            output_paths=(task12b_json,),
            accepted_return_codes=(0, 2),
            expected_status_by_return_code=((0, "pass"), (2, "fail")),
        ),
    ]


def _stage_can_resume(stage: StageCommand, state: Mapping[str, object]) -> bool:
    """Accept a cached stage only when command, script, return code, and bytes match."""

    if (
        state.get("status") != "complete"
        or state.get("return_code") not in stage.accepted_return_codes
        or state.get("command") != list(stage.command)
        or not stage.script_path.is_file()
        or state.get("script_sha256") != sha256_file(stage.script_path)
    ):
        return False
    expected_statuses = dict(stage.expected_status_by_return_code)
    return_code = state.get("return_code")
    if return_code in expected_statuses and (
        state.get("formal_result_status") != expected_statuses[return_code]
    ):
        return False
    outputs = state.get("outputs")
    if not isinstance(outputs, Mapping) or not outputs:
        return False
    for raw_path, digest in outputs.items():
        if not isinstance(raw_path, str) or not isinstance(digest, str):
            return False
        path = Path(raw_path)
        if not path.is_file() or sha256_file(path) != digest:
            return False
    return all(str(path.resolve()) in outputs for path in stage.output_paths)


def _validate_config(config: AcceptanceConfig) -> AcceptanceConfig:
    cfg = config.resolved()
    try:
        ensure_qa_root_outside_dataset(cfg.dataset_root, cfg.qa_root)
    except ValueError as exc:
        raise Task12GAcceptanceError(str(exc)) from exc
    for path, label in (
        (cfg.python_executable, "Python executable"),
        (cfg.generator_root, "Generator root"),
        (cfg.pars2_root, "PAR-S_2 root"),
        (cfg.dataset_root, "dataset root"),
        (cfg.coordinate_report, "coordinate report"),
    ):
        if not path.exists():
            raise Task12GAcceptanceError(f"{label} does not exist: {path}")
    for stage in build_stage_commands(cfg):
        if not stage.script_path.is_file():
            raise Task12GAcceptanceError(
                f"{stage.name} script is missing: {stage.script_path}"
            )
    return cfg


def _formal_binding(
    document: Mapping[str, Any],
    *,
    label: str,
    dataset_id: str,
    manifest_sha256: str,
) -> None:
    if document.get("dataset_id") != dataset_id:
        raise Task12GAcceptanceError(f"{label} dataset ID binding mismatch")
    if document.get("manifest_sha256") != manifest_sha256:
        raise Task12GAcceptanceError(f"{label} manifest binding mismatch")


def build_final_summary(config: AcceptanceConfig) -> dict[str, Any]:
    """Bind all formal reports and build the still-manual automatic result."""

    cfg = config.resolved()
    marker = _read_json(
        cfg.dataset_root / "DATASET_COMPLETE.json",
        "DATASET_COMPLETE.json",
    )
    generation = _read_json(
        cfg.dataset_root / "TASK12G_GENERATION_GATE.json",
        "TASK12G_GENERATION_GATE.json",
    )
    generator_path = cfg.qa_root / "generator_gate.json"
    loader_path = cfg.qa_root / "loader_gate.json"
    exploratory_path = cfg.qa_root / "clinical_alignment_exploratory.json"
    task12b_path = cfg.qa_root / "task12b_gate_summary.json"
    generator = _read_json(generator_path, "Generator gate")
    loader = _read_json(loader_path, "PAR-S_2 loader gate")
    exploratory_raw = _read_json(exploratory_path, "clinical exploratory report")
    task12b = _read_json(task12b_path, "Task 12B gate summary")
    coordinate_raw = _read_json(cfg.coordinate_report, "coordinate report")

    dataset_id = marker.get("dataset_id")
    manifest_sha256 = marker.get("manifest_sha256")
    if dataset_id != EXPECTED_DATASET_ID or manifest_sha256 != EXPECTED_MANIFEST_SHA256:
        raise Task12GAcceptanceError("frozen dataset identity/manifest is not Linux50 v2")
    assert isinstance(dataset_id, str)
    assert isinstance(manifest_sha256, str)
    if (
        marker.get("status") != "complete"
        or marker.get("case_count") != 50
        or generation.get("status") != "ready_for_dataset_freeze"
        or generation.get("case_count") != 50
        or generation.get("absolute_projection_scale_retained") is not True
        or generation.get("linux_only") is not True
        or generation.get("go_for_500_case_generation") is not False
    ):
        raise Task12GAcceptanceError("Task 12G freeze/generation gate binding is invalid")
    _formal_binding(
        generator,
        label="Generator gate",
        dataset_id=dataset_id,
        manifest_sha256=manifest_sha256,
    )
    _formal_binding(
        loader,
        label="PAR-S_2 loader gate",
        dataset_id=dataset_id,
        manifest_sha256=manifest_sha256,
    )

    coordinate_classification = coordinate_raw.get("report_classification")
    if not isinstance(coordinate_classification, Mapping) or (
        coordinate_classification.get("schema_version")
        != "projection_coordinate_gate_v2"
        or coordinate_classification.get("role") != "projection-coordinate-gate"
        or coordinate_classification.get("blocking") is not True
    ):
        raise Task12GAcceptanceError(
            "coordinate evidence is not the blocking projection_coordinate_gate_v2"
        )
    exploratory_classification = exploratory_raw.get("report_classification")
    if not isinstance(exploratory_classification, Mapping) or (
        exploratory_classification.get("role") != "clinical-exploratory"
        or exploratory_classification.get("blocking") is not False
    ):
        raise Task12GAcceptanceError(
            "clinical exploratory evidence is not classified non-blocking"
        )

    gates = task12b.get("gates")
    diagnostics = task12b.get("diagnostics")
    if not isinstance(gates, Mapping) or not isinstance(diagnostics, Mapping):
        raise Task12GAcceptanceError("Task 12B report lacks gates/diagnostics")
    coordinate = gates.get("projection_coordinate_gate_v2")
    quality = gates.get("clinical_projection_quality_gate_v1")
    exploratory = diagnostics.get("clinical_alignment_exploratory_report_v1")
    if not all(isinstance(value, Mapping) for value in (coordinate, quality, exploratory)):
        raise Task12GAcceptanceError("Task 12B formal gate documents are missing")
    assert isinstance(coordinate, Mapping)
    assert isinstance(quality, Mapping)
    assert isinstance(exploratory, Mapping)
    _formal_binding(
        quality,
        label="clinical projection quality gate",
        dataset_id=dataset_id,
        manifest_sha256=manifest_sha256,
    )

    evidence = gate_evidence_rows(
        [
            {
                "gate_id": "generator_frozen_artifact_statistical_visual_gate_v1",
                "blocking": True,
                "report": generator,
                "path": generator_path,
                "sha256": sha256_file(generator_path),
                "meaning": (
                    "Frozen artifacts, metadata, cohort statistics and official "
                    "case boards."
                ),
            },
            {
                "gate_id": "pars2_manifest_loader_gate_v1",
                "blocking": True,
                "report": loader,
                "path": loader_path,
                "sha256": sha256_file(loader_path),
                "meaning": "All 50 cases load through the frozen training data path.",
            },
            {
                "gate_id": "projection_coordinate_gate_v2",
                "blocking": True,
                "report": coordinate,
                "path": cfg.coordinate_report,
                "sha256": sha256_file(cfg.coordinate_report),
                "meaning": (
                    "Dedicated sparse fixture uniquely recovers the frozen loader transform."
                ),
            },
            {
                "gate_id": "clinical_projection_quality_gate_v1",
                "blocking": True,
                "report": quality,
                "path": task12b_path,
                "sha256": sha256_file(task12b_path),
                "meaning": (
                    "Full-physics projection support and frozen-transform similarity."
                ),
            },
            {
                "gate_id": "clinical_alignment_exploratory_report_v1",
                "blocking": False,
                "report": exploratory,
                "path": exploratory_path,
                "sha256": sha256_file(exploratory_path),
                "meaning": (
                    "Complete 480-transform ranking for diagnosis; uniqueness is non-blocking."
                ),
            },
        ]
    )
    focus_cases = generator.get("focus_cases")
    if not isinstance(focus_cases, list):
        raise Task12GAcceptanceError("Generator gate lacks focus-case evidence")
    summary = build_automatic_summary(
        dataset_id=dataset_id,
        manifest_sha256=manifest_sha256,
        gate_rows=evidence,
        focus_cases=focus_cases,
    )
    summary.update(
        {
            "generated_utc": _utc_now(),
            "dataset_root": str(cfg.dataset_root),
            "qa_root": str(cfg.qa_root),
            "dataset_version": marker.get("dataset_version"),
            "case_count": marker.get("case_count"),
            "coordinate_report": {
                "path": str(cfg.coordinate_report),
                "sha256": sha256_file(cfg.coordinate_report),
            },
            "task12b_gate_summary": {
                "path": str(task12b_path),
                "sha256": sha256_file(task12b_path),
            },
            "notebook_authority": "informational_read_only",
            "absolute_projection_scale_retained": True,
            "challenge_semantics": (
                "three_zero_population_weight_coverage_challenges_not_prevalence"
            ),
        }
    )
    return summary


def _markdown(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Task 12G Linux50 automatic acceptance",
        "",
        f"- Status: **{str(summary['status']).upper()}**",
        f"- Dataset: `{summary['dataset_id']}` / `{summary['dataset_version']}`",
        f"- Cases: `{summary['case_count']}`",
        f"- Manifest SHA-256: `{summary['manifest_sha256']}`",
        f"- Automatic blocking gates passed: `{summary['automatic_gate_passed']}`",
        "- Manual review: **PENDING**",
        "- 500-case generation: **NOT APPROVED**",
        "",
        "## Formal evidence",
        "",
        "| Gate/report | Blocking | Status | Meaning |",
        "|---|---:|---|---|",
    ]
    for row in summary["gate_rows"]:
        lines.append(
            f"| `{row['gate_id']}` | {'yes' if row['blocking'] else 'no'} | "
            f"**{str(row['status']).upper()}** | {row['meaning']} |"
        )
    lines.extend(
        [
            "",
            "The read-only Notebook displays these reports and official images. It "
            "does not recompute or override formal gate results and cannot create "
            "manual approval.",
            "",
        ]
    )
    return "\n".join(lines)


def _stage_state(stage: StageCommand, return_code: int) -> dict[str, Any]:
    outputs: dict[str, str] = {}
    accepted = return_code in stage.accepted_return_codes
    formal_result_status: str | None = None
    if accepted:
        for path in stage.output_paths:
            if not path.is_file():
                raise Task12GAcceptanceError(
                    f"{stage.name} did not create required output: {path}"
                )
            outputs[str(path.resolve())] = sha256_file(path)
        expected_status = dict(stage.expected_status_by_return_code).get(return_code)
        if expected_status is not None:
            primary_report = _read_json(
                stage.output_paths[0],
                f"{stage.name} formal report",
            )
            formal_result_status = primary_report.get("status")
            if formal_result_status != expected_status:
                raise Task12GAcceptanceError(
                    f"{stage.name} exit code {return_code} requires formal report "
                    f"status {expected_status!r}, got {formal_result_status!r}"
                )
    state = {
        "status": "complete" if accepted else "failed",
        "command": list(stage.command),
        "command_sha256": _json_sha256(list(stage.command)),
        "cwd": str(stage.cwd),
        "script_path": str(stage.script_path),
        "script_sha256": sha256_file(stage.script_path),
        "return_code": return_code,
        "outputs": outputs,
    }
    if formal_result_status is not None:
        state["formal_result_status"] = formal_result_status
    return state


def _remove_stage_outputs(stage: StageCommand) -> None:
    """Remove only declared report files so an accepted result must be fresh."""

    for path in stage.output_paths:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise Task12GAcceptanceError(
                f"cannot remove stale {stage.name} output {path}: {exc}"
            ) from exc


def _run_stage(stage: StageCommand, logs_root: Path) -> int:
    """Run a stage while streaming child output to tail-able UTF-8 log files."""

    logs_root.mkdir(parents=True, exist_ok=True)
    stdout_path = logs_root / f"{stage.name}.stdout.log"
    stderr_path = logs_root / f"{stage.name}.stderr.log"
    with stdout_path.open("wb") as stdout_stream, stderr_path.open(
        "wb"
    ) as stderr_stream:
        completed = subprocess.run(
            list(stage.command),
            cwd=stage.cwd,
            stdout=stdout_stream,
            stderr=stderr_stream,
            check=False,
        )
    return completed.returncode


def _write_progress(
    path: Path,
    config: AcceptanceConfig,
    stages: Mapping[str, Mapping[str, Any]],
    *,
    status: str,
    current_stage: str | None = None,
    error: str | None = None,
) -> None:
    marker = _read_json(
        config.dataset_root / "DATASET_COMPLETE.json",
        "DATASET_COMPLETE.json",
    )
    atomic_write_json(
        path,
        {
            "schema_version": PROGRESS_SCHEMA,
            "status": status,
            "updated_utc": _utc_now(),
            "dataset_id": marker.get("dataset_id"),
            "manifest_sha256": marker.get("manifest_sha256"),
            "current_stage": current_stage,
            "error": error,
            "stages": dict(stages),
            "go_for_500_case_generation": False,
        },
    )


def run_acceptance_pipeline(
    config: AcceptanceConfig,
    *,
    resume: bool,
) -> dict[str, Any]:
    """Execute or safely resume all automatic stages, then bind their reports."""

    cfg = _validate_config(config)
    cfg.qa_root.mkdir(parents=True, exist_ok=True)
    progress_path = cfg.qa_root / "PROGRESS.json"
    if progress_path.is_file():
        progress = _read_json(progress_path, "acceptance progress")
        if progress.get("schema_version") != PROGRESS_SCHEMA:
            raise Task12GAcceptanceError("existing acceptance progress schema mismatch")
        raw_stages = progress.get("stages")
        stage_states = dict(raw_stages) if isinstance(raw_stages, Mapping) else {}
        if not resume:
            raise Task12GAcceptanceError(
                "QA progress already exists; rerun with --resume"
            )
    else:
        stage_states: dict[str, Mapping[str, Any]] = {}

    logs_root = cfg.qa_root / "logs"
    logs_root.mkdir(parents=True, exist_ok=True)
    for stage in build_stage_commands(cfg):
        existing = stage_states.get(stage.name)
        if resume and isinstance(existing, Mapping) and _stage_can_resume(stage, existing):
            print(
                json.dumps({"stage": stage.name, "status": "already_complete"}),
                flush=True,
            )
            continue
        _write_progress(
            progress_path,
            cfg,
            stage_states,
            status="running",
            current_stage=stage.name,
        )
        _remove_stage_outputs(stage)
        return_code = _run_stage(stage, logs_root)
        state = _stage_state(stage, return_code)
        stage_states[stage.name] = state
        if return_code not in stage.accepted_return_codes:
            error = (
                f"{stage.name} failed with exit code {return_code}; "
                f"see {logs_root}"
            )
            _write_progress(
                progress_path,
                cfg,
                stage_states,
                status="failed",
                current_stage=stage.name,
                error=error,
            )
            raise Task12GAcceptanceError(error)
        print(
            json.dumps(
                {
                    "stage": stage.name,
                    "status": "complete",
                    "return_code": return_code,
                    "formal_result_status": state.get("formal_result_status"),
                }
            ),
            flush=True,
        )

    summary = build_final_summary(cfg)
    automatic_json = cfg.qa_root / "TASK12G_AUTOMATIC_ACCEPTANCE.json"
    automatic_markdown = cfg.qa_root / "TASK12G_AUTOMATIC_ACCEPTANCE.md"
    atomic_write_json(automatic_json, summary)
    atomic_write_bytes(
        automatic_markdown,
        (_markdown(summary) + "\n").encode("utf-8"),
    )
    stage_states["automatic_acceptance_summary"] = {
        "status": "complete",
        "return_code": 0,
        "outputs": {
            str(automatic_json.resolve()): sha256_file(automatic_json),
            str(automatic_markdown.resolve()): sha256_file(automatic_markdown),
        },
    }
    _write_progress(
        progress_path,
        cfg,
        stage_states,
        status="complete",
    )
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--qa-root", type=Path, default=DEFAULT_QA_ROOT)
    parser.add_argument("--pars2-root", type=Path, default=DEFAULT_PARS2_ROOT)
    parser.add_argument(
        "--coordinate-report",
        type=Path,
        default=DEFAULT_COORDINATE_REPORT,
    )
    parser.add_argument("--python-executable", type=Path, default=Path(sys.executable))
    parser.add_argument("--device", default="auto")
    parser.add_argument("--resume", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = AcceptanceConfig(
        python_executable=args.python_executable,
        generator_root=REPO_ROOT,
        pars2_root=args.pars2_root,
        dataset_root=args.dataset_root,
        qa_root=args.qa_root,
        coordinate_report=args.coordinate_report,
        device=args.device,
    )
    try:
        summary = run_acceptance_pipeline(config, resume=args.resume)
    except Exception as exc:
        print(
            json.dumps(
                {"status": "fail", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "status": summary["status"],
                "automatic_gate_passed": summary["automatic_gate_passed"],
                "manual_review_status": summary["manual_review_status"],
                "go_for_500_case_generation": False,
                "qa_root": str(args.qa_root.resolve()),
            },
            ensure_ascii=False,
        )
    )
    return 0 if summary["automatic_gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
