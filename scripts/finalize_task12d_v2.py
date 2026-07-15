"""Run Generator, loader and Task-12B projection gates for frozen Task 12D."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.provenance import atomic_write_json, sha256_file  # noqa: E402


SCHEMA_VERSION = "pars_v2_task12d_fullchain_gate_v1"
DEFAULT_DATASET_ROOT = Path(r"D:\PFE-U\PAR\outputs\pars_v2_task12d3")
DEFAULT_QA_ROOT = Path(r"D:\PFE-U\PAR\outputs\pars_v2_task12d3_qa")
DEFAULT_PARS2_ROOT = Path(r"D:\PFE-U\PAR\.worktrees\PAR-S_2-task12")
DEFAULT_SIMIND_EXE = Path(r"D:\PFE-U\PAR-S-Generator\simind\simind.exe")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--qa-root", type=Path, default=DEFAULT_QA_ROOT)
    parser.add_argument("--pars2-root", type=Path, default=DEFAULT_PARS2_ROOT)
    parser.add_argument("--simind-exe", type=Path, default=DEFAULT_SIMIND_EXE)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--resume", action="store_true")
    return parser


def _run(stage: str, command: Sequence[str], *, cwd: Path) -> dict[str, object]:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Task 12D {stage} failed with exit {completed.returncode}\n"
            f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    return {
        "stage": stage,
        "command": list(command),
        "return_code": completed.returncode,
        "stdout": completed.stdout.rstrip(),
        "stderr": completed.stderr.rstrip(),
    }


def _read_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean_git_binding(root: Path, label: str) -> dict[str, str]:
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.rstrip()
    if status:
        raise RuntimeError(
            f"{label} worktree must be clean for Task 12D gates:\n{status}"
        )
    return {
        "commit": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip(),
        "tree": subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip(),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    dataset = args.dataset_root.resolve()
    qa = args.qa_root.resolve()
    pars2 = args.pars2_root.resolve()
    if not (dataset / "DATASET_COMPLETE.json").is_file():
        raise FileNotFoundError("Task 12D dataset is not frozen complete")
    if not pars2.is_dir():
        raise FileNotFoundError(f"PAR-S_2 worktree not found: {pars2}")
    coordinate_report = (
        pars2
        / "docs"
        / "reports"
        / "v2_projection_coordinate_current_runtime_fixture_report.json"
    )
    if not coordinate_report.is_file():
        raise FileNotFoundError("dedicated projection coordinate report is missing")
    generator_git = _clean_git_binding(REPO_ROOT, "Generator")
    pars2_git = _clean_git_binding(pars2, "PAR-S_2")
    if qa.exists() and not args.resume:
        raise FileExistsError("Task 12D QA root exists; use --resume to rerun gates")
    qa.mkdir(parents=True, exist_ok=True)

    generator_json = qa / "generator_gate.json"
    generator_md = qa / "generator_gate.md"
    loader_json = qa / "loader_gate.json"
    loader_md = qa / "loader_gate.md"
    loader_alignment = qa / "loader_alignment.json"
    descriptor = qa / "projection_alignment_cases.json"
    exploratory = qa / "clinical_alignment_exploratory.json"
    gate_summary_json = qa / "task12b_gate_summary.json"
    gate_summary_md = qa / "task12b_gate_summary.md"
    stages: list[dict[str, object]] = []
    stages.append(
        _run(
            "generator_gate",
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "validate_dataset_v2.py"),
                "--dataset-root",
                str(dataset),
                "--config",
                str(REPO_ROOT / "configs" / "task12d_fullchain_v2.json"),
                "--simind-exe",
                str(args.simind_exe.resolve()),
                "--output-json",
                str(generator_json),
                "--output-md",
                str(generator_md),
            ],
            cwd=REPO_ROOT,
        )
    )
    stages.append(
        _run(
            "pars2_loader_gate",
            [
                sys.executable,
                str(pars2 / "scripts" / "validate_synthetic_dataset.py"),
                "--dataset-root",
                str(dataset),
                "--expected-count",
                "3",
                "--gate-json",
                str(loader_json),
                "--gate-markdown",
                str(loader_md),
                "--alignment-json",
                str(loader_alignment),
            ],
            cwd=pars2,
        )
    )
    stages.append(
        _run(
            "alignment_descriptor",
            [
                sys.executable,
                str(pars2 / "scripts" / "build_projection_alignment_descriptor.py"),
                "--dataset-root",
                str(dataset),
                "--output",
                str(descriptor),
            ],
            cwd=pars2,
        )
    )
    stages.append(
        _run(
            "clinical_alignment_exploratory",
            [
                sys.executable,
                str(pars2 / "scripts" / "search_projection_transform.py"),
                str(descriptor),
                "--output",
                str(exploratory),
                "--device",
                args.device,
                "--minimum-score-margin",
                "0.005",
                "--minimum-bootstrap-top1-frequency",
                "0.95",
                "--minimum-case-top1-frequency",
                "1.0",
                "--report-role",
                "clinical-exploratory",
            ],
            cwd=pars2,
        )
    )
    stages.append(
        _run(
            "task12b_projection_gates",
            [
                sys.executable,
                str(pars2 / "scripts" / "evaluate_task12b_gates.py"),
                str(dataset),
                "--generator-gate",
                str(generator_json),
                "--loader-gate",
                str(loader_json),
                "--coordinate-report",
                str(coordinate_report),
                "--clinical-report",
                str(exploratory),
                "--output-json",
                str(gate_summary_json),
                "--output-markdown",
                str(gate_summary_md),
            ],
            cwd=pars2,
        )
    )

    generator = _read_json(generator_json, "Generator gate")
    loader = _read_json(loader_json, "loader gate")
    task12b = _read_json(gate_summary_json, "Task 12B gate summary")
    gates = task12b.get("gates")
    if not isinstance(gates, Mapping):
        raise RuntimeError("Task 12B gate summary lacks blocking gates")
    coordinate = gates.get("projection_coordinate_gate_v2")
    clinical = gates.get("clinical_projection_quality_gate_v1")
    if not isinstance(coordinate, Mapping) or not isinstance(clinical, Mapping):
        raise RuntimeError("Task 12B blocking gate documents are missing")
    passed = (
        generator.get("status") == "pass"
        and loader.get("status") == "pass"
        and coordinate.get("status") == "pass"
        and clinical.get("status") == "pass"
        and task12b.get("expansion_gate_passed") is True
    )
    document = {
        "schema_version": SCHEMA_VERSION,
        "generated_utc": _utc_now(),
        "status": "pass_awaiting_manual_review" if passed else "fail",
        "dataset_root": str(dataset),
        "dataset_complete_sha256": sha256_file(dataset / "DATASET_COMPLETE.json"),
        "manifest_sha256": generator.get("manifest_sha256"),
        "case_count": generator.get("case_count"),
        "code_bindings": {
            "generator": generator_git,
            "pars2": pars2_git,
        },
        "gates": {
            "generator_gate": generator.get("status"),
            "pars2_loader_gate": loader.get("status"),
            "projection_coordinate_gate_v2": coordinate.get("status"),
            "clinical_projection_quality_gate_v1": clinical.get("status"),
            "clinical_alignment_exploratory_report_v1": "non_blocking_diagnostic",
        },
        "task12b_release_decision": task12b.get("release_decision"),
        "go_for_50_case_generation": False,
        "next_action": (
            "manual review of Task 12D evidence"
            if passed
            else "repair failed Task 12D gate"
        ),
        "evidence": {
            "generator_gate": {
                "path": str(generator_json),
                "sha256": sha256_file(generator_json),
            },
            "loader_gate": {
                "path": str(loader_json),
                "sha256": sha256_file(loader_json),
            },
            "coordinate_report": {
                "path": str(coordinate_report),
                "sha256": sha256_file(coordinate_report),
            },
            "clinical_exploratory": {
                "path": str(exploratory),
                "sha256": sha256_file(exploratory),
            },
            "task12b_gate_summary": {
                "path": str(gate_summary_json),
                "sha256": sha256_file(gate_summary_json),
            },
        },
        "stages": stages,
    }
    atomic_write_json(qa / "TASK12D_COMPLETE.json", document)
    print(
        json.dumps(
            {
                "status": document["status"],
                "qa_root": str(qa),
                "go_for_50_case_generation": False,
                "next_action": document["next_action"],
            }
        )
    )
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
