from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import nbformat
import numpy as np
from nbclient import NotebookClient


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
        "projection_coordinate_gate_v2": {
            "schema_version": "pars_projection_alignment_report_v1",
            "status": "pass",
            "report_classification": {
                "schema_version": "projection_coordinate_gate_v2",
                "blocking": True,
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
        gate_rows.append(
            {
                "gate_id": gate_id,
                "schema_version": str(value["schema_version"]),
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
    notebook = nbformat.read(output, as_version=4)

    executed = NotebookClient(
        notebook,
        timeout=180,
        kernel_name="python3",
        resources={"metadata": {"path": str(tmp_path)}},
    ).execute()

    assert all(
        output.get("output_type") != "error"
        for cell in executed.cells
        for output in cell.get("outputs", [])
    )
