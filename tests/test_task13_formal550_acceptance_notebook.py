from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

import nbformat
import numpy as np
import pytest
from nbclient import NotebookClient
from nbclient.exceptions import CellExecutionError


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from build_task13_formal550_acceptance_notebook import build_notebook  # noqa: E402


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _notebook_source(path: Path) -> str:
    notebook = nbformat.read(path, as_version=4)
    return "\n".join(str(cell.source) for cell in notebook.cells)


def _execute_notebook(path: Path, execution_root: Path) -> nbformat.NotebookNode:
    notebook = nbformat.read(path, as_version=4)
    return NotebookClient(
        notebook,
        timeout=180,
        kernel_name="python3",
        resources={"metadata": {"path": str(execution_root)}},
    ).execute()


def _fixture_evidence(tmp_path: Path) -> tuple[Path, Path, Path]:
    qa_root = tmp_path / "qa"
    main_root = tmp_path / "campaign" / "main"
    negative_root = tmp_path / "campaign" / "negative"
    projection = np.ones((60, 128, 128), dtype="<f4")
    role_rows: dict[str, dict[str, object]] = {}

    for role, root, case_id, split in (
        ("main", main_root, "case_00000", "train"),
        ("negative", negative_root, "negative_00000", "test"),
    ):
        projection_path = root / "cases" / case_id / "artifacts" / f"{case_id}.a00"
        projection_path.parent.mkdir(parents=True, exist_ok=True)
        projection.tofile(projection_path)
        record = {
            "case_id": case_id,
            "dataset_role": role,
            "split": split,
            "artifacts": {
                "projection_a00": {
                    "relative_path": projection_path.relative_to(root).as_posix(),
                    "sha256": _sha256(projection_path),
                    "size_bytes": projection_path.stat().st_size,
                }
            },
        }
        manifest = root / "case_manifest.jsonl"
        manifest.write_text(
            json.dumps(record, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _write_json(
            root / "DATASET_COMPLETE.json",
            {
                "status": "complete",
                "dataset_role": role,
                "case_count": 1,
                "manifest_relative_path": "case_manifest.jsonl",
                "manifest_sha256": _sha256(manifest),
            },
        )
        role_rows[role] = {
            "case_id": case_id,
            "dataset_role": role,
            "split": split,
            "status": "pass",
            "gates": {"artifact_hashes": True},
            "projection_weight_sum": float(projection.sum()),
            "view_sum_cv": 0.0,
            "view_sum_ratio": 1.0,
            "minimum_positive_bin_fraction_per_view": 1.0,
            "outer_8px_count_fraction": 0.0,
        }

    generator = {
        "schema_version": "formal550_generator_gate_v1",
        "status": "pass",
        "case_count": 2,
        "role_case_counts": {"main": 1, "negative": 1},
        "dataset_manifests": {
            "main": json.loads(
                (main_root / "DATASET_COMPLETE.json").read_text(encoding="utf-8")
            )["manifest_sha256"],
            "negative": json.loads(
                (negative_root / "DATASET_COMPLETE.json").read_text(encoding="utf-8")
            )["manifest_sha256"],
        },
        "split_counts": {
            "main": {"train": 1},
            "negative": {"test": 1},
        },
        "thresholds": {"maximum_view_sum_ratio": 80.0},
        "projection_statistics": {
            role: {
                field: {"min": value, "median": value, "mean": value, "max": value}
                for field, value in (
                    ("projection_weight_sum", float(projection.sum())),
                    ("view_sum_cv", 0.0),
                    ("view_sum_ratio", 1.0),
                    ("minimum_positive_bin_fraction_per_view", 1.0),
                    ("outer_8px_count_fraction", 0.0),
                )
            }
            for role in ("main", "negative")
        },
        "passed_case_count": 2,
        "failed_case_ids": [],
        "focus_cases": [
            {
                "case_id": str(role_rows[role]["case_id"]),
                "dataset_role": role,
                "reasons": ["minimum_projection_total"],
            }
            for role in ("main", "negative")
        ],
        "cases": list(role_rows.values()),
    }
    gate_values = {
        "formal550_generator_gate_v1": generator,
        "formal550_main_loader_gate_v1": {
            "schema_version": "pars_v2_synthetic_dataset_gate_v1",
            "status": "pass",
            "dataset_role": "main",
            "expected_count": 1,
            "observed_count": 1,
        },
        "formal550_negative_loader_gate_v1": {
            "schema_version": "pars_v2_synthetic_dataset_gate_v1",
            "status": "pass",
            "dataset_role": "negative",
            "expected_count": 1,
            "observed_count": 1,
        },
        # The frozen coordinate report has no top-level status or gate schema:
        # Task4 normalizes its row from report_classification and freeze_gate.
        "projection_coordinate_gate_v2": {
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
                "uniqueness_error": None,
            },
            "projection_coordinates": {
                "coordinate_contract_id": "pars_simind_v8_xcat_zyx_sar_v1",
                "loader_transform_id": (
                    "simind_v8_xcat_v1_views_forward_roll000_det_v_flip_det_u_keep"
                ),
            },
        },
    }
    gate_rows = []
    for gate_id, value in gate_values.items():
        path = qa_root / f"{gate_id}.json"
        _write_json(path, value)
        classification = value.get("report_classification")
        row_schema = (
            classification["schema_version"]
            if isinstance(classification, dict)
            else value["schema_version"]
        )
        gate_rows.append(
            {
                "gate_id": gate_id,
                "schema_version": str(row_schema),
                "status": "pass",
                "blocking": True,
                "path": str(path.resolve()),
                "sha256": _sha256(path),
            }
        )
    acceptance_json = qa_root / "TASK13_FORMAL550_AUTOMATIC_ACCEPTANCE.json"
    _write_json(
        acceptance_json,
        {
            "schema_version": "pars_v2_task13_formal550_automatic_acceptance_v1",
            "status": "pass",
            "automatic_gate_passed": True,
            "case_count": 2,
            "role_case_counts": {"main": 1, "negative": 1},
            "gate_rows": gate_rows,
            "notebook_authority": "informational_read_only",
        },
    )
    return acceptance_json, main_root, negative_root


def test_notebook_contains_required_read_only_review_structure(tmp_path: Path) -> None:
    acceptance_json, main_root, negative_root = _fixture_evidence(tmp_path)
    output = tmp_path / "review.ipynb"

    build_notebook(
        acceptance_json=acceptance_json,
        output_path=output,
        main_root=main_root,
        negative_root=negative_root,
    )
    source = _notebook_source(output)

    assert "informational_read_only" in source
    assert "does not define, override, or write PASS/FAIL" in source
    assert "## 2. Authoritative gate structure" in source
    assert "## 3. Main and negative role/split summaries" in source
    assert "## 4. Cohort distributions" in source
    assert "## 5. Projection metrics" in source
    assert "## 6. Main and negative focus-case projection sliders" in source
    assert "## 7. Read-only conclusion" in source
    assert "visual_registry" in source
    assert "automatic['gate_rows']" in source


def test_notebook_source_has_no_writes_network_processes_or_manual_notes(
    tmp_path: Path,
) -> None:
    acceptance_json, main_root, negative_root = _fixture_evidence(tmp_path)
    output = tmp_path / "review.ipynb"

    build_notebook(
        acceptance_json=acceptance_json,
        output_path=output,
        main_root=main_root,
        negative_root=negative_root,
    )
    source = _notebook_source(output)

    forbidden = (
        "write_text(",
        "write_bytes(",
        "to_json(",
        "nbformat.write",
        "open(\"w",
        "open('w",
        "subprocess",
        "requests",
        "urllib",
        "socket",
        "input(",
        "Textarea(",
        "Text(",
        "Button(",
        "manual_note",
        "review_note",
        "approval",
    )
    for token in forbidden:
        assert token not in source
    notebook = nbformat.read(output, as_version=4)
    assert not any(
        isinstance(node, ast.Assert)
        for cell in notebook.cells
        if cell.cell_type == "code"
        for node in ast.walk(ast.parse(str(cell.source)))
    )
    assert "automatic_gate_passed =" not in source
    assert "automatic['status'] =" not in source


def test_notebook_has_exact_angle_labels_and_separate_role_sliders(
    tmp_path: Path,
) -> None:
    acceptance_json, main_root, negative_root = _fixture_evidence(tmp_path)
    output = tmp_path / "review.ipynb"

    build_notebook(
        acceptance_json=acceptance_json,
        output_path=output,
        main_root=main_root,
        negative_root=negative_root,
    )
    source = _notebook_source(output)

    assert "SIMIND {simind_angle:.1f}{degree} CW+" in source
    assert "projector {projector_angle:.1f}{degree} CW+" in source
    assert "clinical DICOM camera {clinical_angle:.1f}{degree}" in source
    assert "main_focus_case_slider = widgets.SelectionSlider(" in source
    assert "negative_focus_case_slider = widgets.SelectionSlider(" in source
    assert "main_projection_view_slider = widgets.IntSlider(" in source
    assert "negative_projection_view_slider = widgets.IntSlider(" in source


def test_builder_is_deterministic(tmp_path: Path) -> None:
    acceptance_json, main_root, negative_root = _fixture_evidence(tmp_path)
    first = tmp_path / "first.ipynb"
    second = tmp_path / "second.ipynb"

    for output in (first, second):
        build_notebook(
            acceptance_json=acceptance_json,
            output_path=output,
            main_root=main_root,
            negative_root=negative_root,
        )

    assert first.read_bytes() == second.read_bytes()
    assert b"\r\n" not in first.read_bytes()


def test_generated_notebook_executes_end_to_end_against_fixture(
    tmp_path: Path,
) -> None:
    acceptance_json, main_root, negative_root = _fixture_evidence(tmp_path)
    output = tmp_path / "review.ipynb"
    build_notebook(
        acceptance_json=acceptance_json,
        output_path=output,
        main_root=main_root,
        negative_root=negative_root,
    )
    executed = _execute_notebook(output, tmp_path)

    assert all(
        output.get("output_type") != "error"
        for cell in executed.cells
        for output in cell.get("outputs", [])
    )


def test_notebook_rejects_marker_and_manifest_substitution_together(
    tmp_path: Path,
) -> None:
    acceptance_json, main_root, negative_root = _fixture_evidence(tmp_path)
    manifest_path = main_root / "case_manifest.jsonl"
    row = json.loads(manifest_path.read_text(encoding="utf-8"))
    row["substituted"] = True
    manifest_path.write_text(
        json.dumps(row, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    marker_path = main_root / "DATASET_COMPLETE.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["manifest_sha256"] = _sha256(manifest_path)
    _write_json(marker_path, marker)
    output = tmp_path / "substituted.ipynb"
    build_notebook(
        acceptance_json=acceptance_json,
        output_path=output,
        main_root=main_root,
        negative_root=negative_root,
    )

    with pytest.raises(CellExecutionError, match="generator gate manifest SHA-256"):
        _execute_notebook(output, tmp_path)


def test_notebook_rejects_same_size_projection_tamper(tmp_path: Path) -> None:
    acceptance_json, main_root, negative_root = _fixture_evidence(tmp_path)
    output = tmp_path / "tampered.ipynb"
    build_notebook(
        acceptance_json=acceptance_json,
        output_path=output,
        main_root=main_root,
        negative_root=negative_root,
    )
    projection_path = (
        main_root / "cases" / "case_00000" / "artifacts" / "case_00000.a00"
    )
    payload = bytearray(projection_path.read_bytes())
    payload[0] ^= 1
    projection_path.write_bytes(payload)

    with pytest.raises(CellExecutionError, match="projection SHA-256 mismatch"):
        _execute_notebook(output, tmp_path)


def _rebind_coordinate_report(
    acceptance_json: Path,
    mutate: "callable[[dict], None]",
) -> None:
    """Mutate the coordinate report and re-bind its SHA-256 in the acceptance row."""

    acceptance = json.loads(acceptance_json.read_text(encoding="utf-8"))
    row = next(
        item
        for item in acceptance["gate_rows"]
        if item["gate_id"] == "projection_coordinate_gate_v2"
    )
    report_path = Path(row["path"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    mutate(report)
    _write_json(report_path, report)
    row["sha256"] = _sha256(report_path)
    _write_json(acceptance_json, acceptance)


def test_notebook_accepts_coordinate_report_without_top_level_status(
    tmp_path: Path,
) -> None:
    """The frozen report states its outcome in freeze_gate, not a top-level status."""

    acceptance_json, main_root, negative_root = _fixture_evidence(tmp_path)
    coordinate_row = next(
        row
        for row in json.loads(acceptance_json.read_text(encoding="utf-8"))["gate_rows"]
        if row["gate_id"] == "projection_coordinate_gate_v2"
    )
    report = json.loads(Path(coordinate_row["path"]).read_text(encoding="utf-8"))
    assert "status" not in report
    assert coordinate_row["schema_version"] == "projection_coordinate_gate_v2"
    output = tmp_path / "coordinate.ipynb"
    build_notebook(
        acceptance_json=acceptance_json,
        output_path=output,
        main_root=main_root,
        negative_root=negative_root,
    )

    executed = _execute_notebook(output, tmp_path)

    assert all(
        cell_output.get("output_type") != "error"
        for cell in executed.cells
        for cell_output in cell.get("outputs", [])
    )


def test_notebook_rejects_coordinate_report_with_failed_freeze_gate(
    tmp_path: Path,
) -> None:
    acceptance_json, main_root, negative_root = _fixture_evidence(tmp_path)

    def _fail_freeze(report: dict) -> None:
        report["freeze_gate"]["passed"] = False

    _rebind_coordinate_report(acceptance_json, _fail_freeze)
    output = tmp_path / "freeze_failed.ipynb"
    build_notebook(
        acceptance_json=acceptance_json,
        output_path=output,
        main_root=main_root,
        negative_root=negative_root,
    )

    with pytest.raises(
        CellExecutionError,
        match="status mismatch for projection_coordinate_gate_v2",
    ):
        _execute_notebook(output, tmp_path)


def test_notebook_rejects_coordinate_report_with_substituted_gate_schema(
    tmp_path: Path,
) -> None:
    acceptance_json, main_root, negative_root = _fixture_evidence(tmp_path)

    def _swap_schema(report: dict) -> None:
        report["report_classification"]["schema_version"] = (
            "projection_coordinate_gate_v1"
        )

    _rebind_coordinate_report(acceptance_json, _swap_schema)
    output = tmp_path / "schema_swapped.ipynb"
    build_notebook(
        acceptance_json=acceptance_json,
        output_path=output,
        main_root=main_root,
        negative_root=negative_root,
    )

    with pytest.raises(
        CellExecutionError,
        match="schema mismatch for projection_coordinate_gate_v2",
    ):
        _execute_notebook(output, tmp_path)


@pytest.mark.parametrize("escape_kind", ["absolute", "parent"])
def test_notebook_rejects_manifest_path_escape(
    tmp_path: Path,
    escape_kind: str,
) -> None:
    acceptance_json, main_root, negative_root = _fixture_evidence(tmp_path)
    marker_path = main_root / "DATASET_COMPLETE.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if escape_kind == "absolute":
        marker["manifest_relative_path"] = str(
            (main_root / "case_manifest.jsonl").resolve()
        )
    else:
        escaped = main_root.parent / "escaped_manifest.jsonl"
        escaped.write_bytes((main_root / "case_manifest.jsonl").read_bytes())
        marker["manifest_relative_path"] = "../escaped_manifest.jsonl"
    _write_json(marker_path, marker)
    output = tmp_path / f"{escape_kind}.ipynb"
    build_notebook(
        acceptance_json=acceptance_json,
        output_path=output,
        main_root=main_root,
        negative_root=negative_root,
    )

    with pytest.raises(CellExecutionError, match="manifest path"):
        _execute_notebook(output, tmp_path)
