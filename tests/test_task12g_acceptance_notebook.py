from __future__ import annotations

import sys
from pathlib import Path

import nbformat


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from build_task12g_acceptance_notebook import build_notebook  # noqa: E402


def _sources(notebook: nbformat.NotebookNode) -> str:
    return "\n".join(str(cell.source) for cell in notebook.cells)


def test_notebook_contains_required_read_only_review_structure(
    tmp_path: Path,
) -> None:
    output = tmp_path / "review.ipynb"

    build_notebook(tmp_path / "qa", output)
    notebook = nbformat.read(output, as_version=4)
    source = _sources(notebook)

    assert "informational_read_only" in source
    assert "does not define or override PASS/FAIL" in source
    assert "## 2. Acceptance structure" in source
    assert "## 3. Formal automatic gate summary" in source
    assert "## 4. Cohort statistical review" in source
    assert "## 6. Focus cases" in source
    assert "## 7. Projection coordinate and quality evidence" in source
    assert "## 8. Automatic conclusion and manual-review boundary" in source
    for group_index in range(1, 6):
        assert f"### 5.{group_index} Case group {group_index}/5" in source


def test_notebook_source_has_no_mutating_or_approval_controls(
    tmp_path: Path,
) -> None:
    output = tmp_path / "review.ipynb"

    build_notebook(tmp_path / "qa", output)
    notebook = nbformat.read(output, as_version=4)
    source = _sources(notebook)

    forbidden = (
        "write_text(",
        "write_bytes(",
        "to_json(",
        "nbformat.write",
        "ipywidgets",
        "input(",
        "go_for_500_case_generation = True",
        "--minimum-score-margin",
        "--minimum-bootstrap-top1-frequency",
        "--minimum-case-top1-frequency",
    )
    for token in forbidden:
        assert token not in source
    assert "automatic['gate_rows']" in source
    assert "automatic['go_for_500_case_generation'] is False" in source


def test_notebook_has_five_group_display_cells(tmp_path: Path) -> None:
    output = tmp_path / "review.ipynb"

    build_notebook(tmp_path / "qa", output)
    notebook = nbformat.read(output, as_version=4)
    group_cells = [
        cell
        for cell in notebook.cells
        if cell.cell_type == "code" and "display_case_group(" in cell.source
    ]

    assert len(group_cells) == 5
    assert [cell.source.strip() for cell in group_cells] == [
        f"display_case_group({index})" for index in range(5)
    ]
