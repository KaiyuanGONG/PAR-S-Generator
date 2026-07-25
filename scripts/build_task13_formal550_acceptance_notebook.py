#!/usr/bin/env python
"""Build the read-only Task13 Formal550 acceptance review notebook."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

import nbformat
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CAMPAIGN_ROOT = Path(r"D:\PFE-U\PAR\outputs\pars_v2_formal550_v1")
DEFAULT_QA_ROOT = Path(r"D:\PFE-U\PAR\outputs\pars_v2_formal550_v1_qa")
DEFAULT_ACCEPTANCE_JSON = (
    DEFAULT_QA_ROOT / "TASK13_FORMAL550_AUTOMATIC_ACCEPTANCE.json"
)
DEFAULT_OUTPUT = REPO_ROOT / "notebook" / "Task13_Formal550_Acceptance_Review.ipynb"


def _markdown(source: str) -> nbformat.NotebookNode:
    return new_markdown_cell(source.strip() + "\n")


def _code(source: str) -> nbformat.NotebookNode:
    return new_code_cell(source.strip() + "\n")


def _setup_source(
    *,
    acceptance_json: Path,
    main_root: Path,
    negative_root: Path,
) -> str:
    source = r'''
from pathlib import Path
import hashlib
import json

import ipywidgets as widgets
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from IPython.display import JSON as DisplayJSON, Markdown, display

ACCEPTANCE_JSON = Path(__ACCEPTANCE_JSON__)
MAIN_ROOT = Path(__MAIN_ROOT__)
NEGATIVE_ROOT = Path(__NEGATIVE_ROOT__)
EXPECTED_ACCEPTANCE_SCHEMA = "pars_v2_task13_formal550_automatic_acceptance_v1"
EXPECTED_GENERATOR_SCHEMA = "formal550_generator_gate_v1"
EXPECTED_PROJECTION_SHAPE = (60, 128, 128)
COORDINATE_GATE_ID = "projection_coordinate_gate_v2"
EXPECTED_COORDINATE_REPORT_SCHEMA = "pars_projection_alignment_report_v1"


def read_json_bytes(path):
    payload = Path(path).read_bytes()
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value, payload


def gate_identity(gate_id, value):
    """Recover the (schema_version, status) Task4 normalized into `gate_rows`.

    The formal550 generator and loader gates state both at the top level. The
    frozen coordinate report predates that convention: it carries its gate
    identity in `report_classification` and its outcome in `freeze_gate`, so
    reading a top-level `status` from it would compare against nothing.
    """
    if gate_id != COORDINATE_GATE_ID:
        return value.get("schema_version"), value.get("status")
    if value.get("schema_version") != EXPECTED_COORDINATE_REPORT_SCHEMA:
        raise ValueError(f"coordinate report schema mismatch for {gate_id}")
    classification = value.get("report_classification") or {}
    freeze_gate = value.get("freeze_gate") or {}
    passed = (
        freeze_gate.get("passed") is True
        and freeze_gate.get("frozen_transform_recovered") is True
    )
    return classification.get("schema_version"), "pass" if passed else "fail"


def read_bound_gate(row):
    value, payload = read_json_bytes(Path(row["path"]))
    if hashlib.sha256(payload).hexdigest() != row["sha256"]:
        raise ValueError(f"SHA-256 mismatch for {row['gate_id']}")
    schema_version, status = gate_identity(row["gate_id"], value)
    if schema_version != row["schema_version"]:
        raise ValueError(f"schema mismatch for {row['gate_id']}")
    if status != row["status"]:
        raise ValueError(f"status mismatch for {row['gate_id']}")
    return value


def load_role_manifest(role, root, expected_manifest_sha256):
    role_root = root.resolve()
    marker, _ = read_json_bytes(root / "DATASET_COMPLETE.json")
    if marker.get("status") != "complete" or marker.get("dataset_role") != role:
        raise ValueError(f"{role} completion marker identity/status mismatch")
    manifest_relative = Path(marker["manifest_relative_path"])
    if manifest_relative.is_absolute():
        raise ValueError(f"{role} manifest path must be relative")
    manifest_path = (role_root / manifest_relative).resolve()
    try:
        manifest_path.relative_to(role_root)
    except ValueError as exc:
        raise ValueError(f"{role} manifest path escapes role root") from exc
    manifest_payload = manifest_path.read_bytes()
    actual_manifest_sha256 = hashlib.sha256(manifest_payload).hexdigest()
    if actual_manifest_sha256 != expected_manifest_sha256:
        raise ValueError(f"{role} generator gate manifest SHA-256 mismatch")
    if actual_manifest_sha256 != marker["manifest_sha256"]:
        raise ValueError(f"{role} manifest SHA-256 mismatch")
    rows = [json.loads(line) for line in manifest_payload.splitlines() if line.strip()]
    if len(rows) != marker["case_count"]:
        raise ValueError(f"{role} manifest count mismatch")
    return marker, rows


def resolve_role_artifact(root, artifact):
    role_root = root.resolve()
    relative = Path(artifact["relative_path"])
    if relative.is_absolute():
        raise ValueError("projection artifact path must be relative")
    path = (role_root / relative).resolve()
    try:
        path.relative_to(role_root)
    except ValueError as exc:
        raise ValueError("projection artifact path escapes role root") from exc
    return {
        "path": path,
        "size_bytes": int(artifact["size_bytes"]),
        "sha256": str(artifact["sha256"]),
    }


def require(condition, message):
    if not condition:
        raise ValueError(message)


automatic, _ = read_json_bytes(ACCEPTANCE_JSON)
require(
    automatic["schema_version"] == EXPECTED_ACCEPTANCE_SCHEMA,
    "automatic acceptance schema mismatch",
)
require(
    automatic["notebook_authority"] == "informational_read_only",
    "notebook authority mismatch",
)

gate_documents = {
    row["gate_id"]: read_bound_gate(row) for row in automatic['gate_rows']
}
generator_gate = gate_documents["formal550_generator_gate_v1"]
main_loader_gate = gate_documents["formal550_main_loader_gate_v1"]
negative_loader_gate = gate_documents["formal550_negative_loader_gate_v1"]
coordinate_gate = gate_documents["projection_coordinate_gate_v2"]
require(
    generator_gate["schema_version"] == EXPECTED_GENERATOR_SCHEMA,
    "generator gate schema mismatch",
)

main_marker, main_manifest_rows = load_role_manifest(
    "main", MAIN_ROOT, generator_gate["dataset_manifests"]["main"]
)
negative_marker, negative_manifest_rows = load_role_manifest(
    "negative",
    NEGATIVE_ROOT,
    generator_gate["dataset_manifests"]["negative"],
)
require(
    main_marker["case_count"] == automatic["role_case_counts"]["main"],
    "main role case count mismatch",
)
require(
    negative_marker["case_count"] == automatic["role_case_counts"]["negative"],
    "negative role case count mismatch",
)

role_roots = {"main": MAIN_ROOT, "negative": NEGATIVE_ROOT}
visual_registry = {}
for role, rows in (
    ("main", main_manifest_rows),
    ("negative", negative_manifest_rows),
):
    visual_registry[role] = {
        row["case_id"]: resolve_role_artifact(
            role_roots[role], row["artifacts"]["projection_a00"]
        )
        for row in rows
    }

case_frame = pd.DataFrame(generator_gate["cases"])
require(
    len(case_frame) == automatic["case_count"],
    "generator gate case count mismatch",
)
for role in ("main", "negative"):
    audited = set(case_frame.loc[case_frame["dataset_role"] == role, "case_id"])
    if audited != set(visual_registry[role]):
        raise ValueError(f"{role} generator/manifest case set mismatch")

display(pd.DataFrame([{
    "authority": automatic["notebook_authority"],
    "automatic_status": automatic["status"],
    "automatic_gate_passed": automatic["automatic_gate_passed"],
    "case_count": automatic["case_count"],
    "main_cases": automatic["role_case_counts"]["main"],
    "negative_cases": automatic["role_case_counts"]["negative"],
}]))
'''
    replacements = {
        "__ACCEPTANCE_JSON__": json.dumps(str(acceptance_json.resolve())),
        "__MAIN_ROOT__": json.dumps(str(main_root.resolve())),
        "__NEGATIVE_ROOT__": json.dumps(str(negative_root.resolve())),
    }
    for token, value in replacements.items():
        source = source.replace(token, value)
    return source


def acceptance_review_cells(
    *,
    acceptance_json: Path,
    main_root: Path,
    negative_root: Path,
) -> list[nbformat.NotebookNode]:
    """Return deterministic presentation-only cells for frozen Task13 evidence."""

    cells = [
        _markdown(
            """
# PAR-S V2 Task13 Formal550 Acceptance Review

> **Authority: `informational_read_only`.** This notebook explains existing,
> SHA-bound automatic evidence. It **does not define, override, or write PASS/FAIL**,
> does not collect human notes, and cannot authorize another
> generation or change any frozen dataset artifact.

Scope: the immutable 500-case main role and independent 50-case negative role.
The authoritative result remains the Task4 automatic acceptance JSON supplied
to this notebook.
"""
        ),
        _markdown(
            """
## 1. Frozen inputs and authority boundary

The setup reads the automatic acceptance JSON, verifies each referenced gate
against the SHA-256, schema and status recorded in `gate_rows`, and reads both
role manifests from their immutable roots. The frozen coordinate report states
its gate identity in `report_classification` and its outcome in `freeze_gate`
rather than at the top level, so its row is re-derived from those fields exactly
as Task4 normalized them. Projection paths are assembled into a display-only
`visual_registry`. Each selected projection is read once, checked against the
manifest size and SHA-256, and displayed from that verified in-memory byte
snapshot; files are never opened in a writable mode.
"""
        ),
        _code(
            _setup_source(
                acceptance_json=acceptance_json,
                main_root=main_root,
                negative_root=negative_root,
            )
        ),
        _markdown(
            """
## 2. Authoritative gate structure

```mermaid
flowchart LR
    A["Formal550 generator artifact/statistical gate"] --> E["Task4 automatic acceptance JSON"]
    B["Main PAR-S_2 loader gate"] --> E
    C["Negative PAR-S_2 loader gate"] --> E
    D["Frozen projection coordinate gate"] --> E
    E --> F["This informational read-only review"]
```

The notebook displays the statuses already recorded by Task4. It does not
derive a replacement decision from the tables or plots below.
"""
        ),
        _code(
            """
gate_frame = pd.DataFrame(automatic['gate_rows'])[
    ["gate_id", "blocking", "status", "schema_version", "path", "sha256"]
]
display(gate_frame)
display(DisplayJSON({
    "coordinate_contract": coordinate_gate["projection_coordinates"],
    "main_loader_status": main_loader_gate["status"],
    "negative_loader_status": negative_loader_gate["status"],
}))
"""
        ),
        _markdown(
            """
## 3. Main and negative role/split summaries

The main role contains the tumor-bearing population cohort; the independent
negative role is a test-only, zero-tumor control cohort. Counts below come from
the frozen generator and loader reports, not from notebook-authored criteria.
"""
        ),
        _code(
            """
role_summary = (
    case_frame.groupby(["dataset_role", "status"], sort=True)
    .size()
    .rename("case_count")
    .reset_index()
)
split_summary = (
    case_frame.groupby(["dataset_role", "split"], sort=True)
    .size()
    .rename("case_count")
    .reset_index()
)
loader_summary = pd.DataFrame([
    {
        "dataset_role": role,
        "status": gate["status"],
        "expected_count": gate.get("expected_count"),
        "observed_count": gate.get("observed_count"),
    }
    for role, gate in (
        ("main", main_loader_gate),
        ("negative", negative_loader_gate),
    )
])
display(role_summary)
display(split_summary)
display(loader_summary)
"""
        ),
        _markdown(
            """
## 4. Cohort distributions

These plots expose the measured projection distributions separately for main
and negative roles. They are descriptive views of the generator gate's frozen
per-case rows; no plotted value creates or changes a threshold.
"""
        ),
        _code(
            """
distribution_fields = [
    "projection_weight_sum",
    "view_sum_cv",
    "view_sum_ratio",
    "minimum_positive_bin_fraction_per_view",
    "outer_8px_count_fraction",
]
fig, axes = plt.subplots(
    len(distribution_fields), 1, figsize=(10, 3.0 * len(distribution_fields))
)
for axis, field in zip(axes, distribution_fields):
    for role, color in (("main", "#285f8f"), ("negative", "#b4513e")):
        values = case_frame.loc[case_frame["dataset_role"] == role, field].astype(float)
        axis.hist(values, bins=min(20, max(1, len(values))), alpha=0.55, label=role, color=color)
    axis.set_title(field.replace("_", " "))
    axis.set_ylabel("case count")
    axis.legend()
axes[-1].set_xlabel("reported value")
fig.tight_layout()
plt.show()
"""
        ),
        _markdown(
            """
## 5. Projection metrics

The aggregate table reproduces the min/median/mean/max values from the formal
generator gate. The per-case table keeps role and split visible so extrema and
focus-case reasons remain auditable.
"""
        ),
        _code(
            """
projection_summary_rows = []
for role, summaries in generator_gate["projection_statistics"].items():
    for metric, summary in summaries.items():
        projection_summary_rows.append({
            "dataset_role": role,
            "metric": metric,
            **summary,
        })
display(pd.DataFrame(projection_summary_rows))
display(case_frame[[
    "case_id",
    "dataset_role",
    "split",
    "status",
    *distribution_fields,
]])
"""
        ),
        _markdown(
            """
## 6. Main and negative focus-case projection sliders

Task4 selected focus cases deterministically from automatic attention cases and
per-role projection extrema. Each role has its own case selector and view
slider. Stored SIMIND view `v` is labelled in all frozen angle bases:

- `SIMIND = (180° + 6°v) mod 360°`, clockwise-positive;
- `projector = (90° + 6°v) mod 360°`, clockwise-positive;
- `clinical DICOM camera = (270° - projector) mod 360°`.

The detector-v flip shown here is the frozen loader transform. Controls affect
only the displayed projection; they do not mutate evidence or gate outcomes.
"""
        ),
        _code(
            r'''
focus_frame = pd.DataFrame(generator_gate["focus_cases"])
display(focus_frame)


def role_focus_case_ids(role):
    selected = [
        item["case_id"]
        for item in generator_gate["focus_cases"]
        if item["dataset_role"] == role
    ]
    if selected:
        return tuple(selected)
    return tuple(sorted(visual_registry[role]))


def load_projection(role, case_id):
    artifact = visual_registry[role][case_id]
    path = artifact["path"]
    payload = path.read_bytes()
    if len(payload) != artifact["size_bytes"]:
        raise ValueError(f"{case_id} projection size mismatch")
    if hashlib.sha256(payload).hexdigest() != artifact["sha256"]:
        raise ValueError(f"{case_id} projection SHA-256 mismatch")
    expected_bytes = int(np.prod(EXPECTED_PROJECTION_SHAPE)) * np.dtype("<f4").itemsize
    if len(payload) != expected_bytes:
        raise ValueError(f"{case_id} projection byte size mismatch")
    return np.frombuffer(payload, dtype="<f4").reshape(EXPECTED_PROJECTION_SHAPE)


focus_projection_snapshots = {
    (role, case_id): load_projection(role, case_id)
    for role in ("main", "negative")
    for case_id in role_focus_case_ids(role)
}


def show_focus_projection(role, case_id, view):
    projection = focus_projection_snapshots[(role, case_id)]
    canonical_image = np.asarray(projection[view, ::-1, :])
    simind_angle = (180.0 + view * 6.0) % 360.0
    projector_angle = (90.0 + view * 6.0) % 360.0
    clinical_angle = (270.0 - projector_angle) % 360.0
    degree = chr(176)
    fig, axis = plt.subplots(figsize=(6.8, 5.8), constrained_layout=True)
    artist = axis.imshow(np.log1p(canonical_image), cmap="magma", origin="lower")
    axis.set_title(
        f"{role} | {case_id} | canonical view {view:02d}\n"
        f"SIMIND {simind_angle:.1f}{degree} CW+  ↔  "
        f"projector {projector_angle:.1f}{degree} CW+  ↔  "
        f"clinical DICOM camera {clinical_angle:.1f}{degree}"
    )
    axis.set_xlabel("detector u")
    axis.set_ylabel("detector v (frozen loader flip applied)")
    fig.colorbar(artist, ax=axis, label="log1p SIMIND weight")
    plt.show()


main_focus_case_slider = widgets.SelectionSlider(
    options=role_focus_case_ids("main"),
    description="Main focus case",
    continuous_update=False,
    style={"description_width": "initial"},
    layout=widgets.Layout(width="760px"),
)
main_projection_view_slider = widgets.IntSlider(
    value=0,
    min=0,
    max=59,
    step=1,
    description="Main projection view",
    continuous_update=False,
    readout_format="02d",
    style={"description_width": "initial"},
    layout=widgets.Layout(width="760px"),
)
negative_focus_case_slider = widgets.SelectionSlider(
    options=role_focus_case_ids("negative"),
    description="Negative focus case",
    continuous_update=False,
    style={"description_width": "initial"},
    layout=widgets.Layout(width="760px"),
)
negative_projection_view_slider = widgets.IntSlider(
    value=0,
    min=0,
    max=59,
    step=1,
    description="Negative projection view",
    continuous_update=False,
    readout_format="02d",
    style={"description_width": "initial"},
    layout=widgets.Layout(width="760px"),
)

display(Markdown("### Main-role focus projection"))
display(widgets.interactive(
    lambda case_id, view: show_focus_projection("main", case_id, view),
    case_id=main_focus_case_slider,
    view=main_projection_view_slider,
))
display(Markdown("### Negative-role focus projection"))
display(widgets.interactive(
    lambda case_id, view: show_focus_projection("negative", case_id, view),
    case_id=negative_focus_case_slider,
    view=negative_projection_view_slider,
))
'''
        ),
        _markdown(
            """
## 7. Read-only conclusion

The final cell repeats the automatic status and evidence boundary verbatim.
Only the Task4 JSON is authoritative; interaction above changes display state
only.
"""
        ),
        _code(
            r"""
display(Markdown(
    f"### Automatic status: **{automatic['status'].upper()}**\n\n"
    f"- Automatic gate passed: `{automatic['automatic_gate_passed']}`\n"
    f"- Notebook authority: `{automatic['notebook_authority']}`\n"
    f"- Cases reviewed: `{automatic['case_count']}`"
))
display(pd.DataFrame(automatic['gate_rows'])[["gate_id", "status", "sha256"]])
"""
        ),
    ]
    for index, cell in enumerate(cells):
        cell["id"] = f"task13-review-{index:02d}"
    return cells


def build_notebook(
    *,
    acceptance_json: Path,
    output_path: Path,
    main_root: Path,
    negative_root: Path,
) -> None:
    notebook = new_notebook(cells=acceptance_review_cells(
        acceptance_json=acceptance_json,
        main_root=main_root,
        negative_root=negative_root,
    ))
    with Path(output_path).open("w", encoding="utf-8", newline="\n") as stream:
        nbformat.write(notebook, stream)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acceptance-json", type=Path, default=DEFAULT_ACCEPTANCE_JSON)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--main-root", type=Path, default=DEFAULT_CAMPAIGN_ROOT / "main")
    parser.add_argument(
        "--negative-root", type=Path, default=DEFAULT_CAMPAIGN_ROOT / "negative"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    build_notebook(
        acceptance_json=args.acceptance_json,
        output_path=args.output,
        main_root=args.main_root,
        negative_root=args.negative_root,
    )
    print(json.dumps({"status": "created", "notebook": str(args.output.resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
