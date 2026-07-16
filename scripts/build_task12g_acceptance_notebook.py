#!/usr/bin/env python
"""Build the read-only Task 12G Linux50 acceptance review Notebook."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

import nbformat


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from core.provenance import atomic_write_bytes  # noqa: E402


DEFAULT_DATASET_ROOT = Path(r"D:\PFE-U\PAR\outputs\pars_v2_linux50_v2")
DEFAULT_QA_ROOT = Path(r"D:\PFE-U\PAR\outputs\pars_v2_linux50_v2_qa")
DEFAULT_OUTPUT = (
    REPO_ROOT / "notebook" / "Task12G_Linux50_Acceptance_Review.ipynb"
)
EXPECTED_DATASET_ID = "PAR-S-TARE-HCC-NoPVI-SYN-v2-linux50"
EXPECTED_MANIFEST_SHA256 = (
    "d44a77f4604bc1df192c6af0674341c704d4e069d1665e1fe159832adfeae722"
)


def _markdown(source: str) -> nbformat.NotebookNode:
    return nbformat.v4.new_markdown_cell(source.strip() + "\n")


def _code(source: str) -> nbformat.NotebookNode:
    return nbformat.v4.new_code_cell(source.strip() + "\n")


def build_notebook(
    qa_root: str | Path,
    output_path: str | Path,
    *,
    dataset_root: str | Path = DEFAULT_DATASET_ROOT,
) -> Path:
    """Create a Notebook whose code path is presentation-only and read-only."""

    qa = Path(qa_root).resolve()
    dataset = Path(dataset_root).resolve()
    output = Path(output_path).resolve()
    cells: list[nbformat.NotebookNode] = [
        _markdown(
            """
# PAR-S V2 Task 12G Linux50 Acceptance Review

> **Authority: `informational_read_only`.** This Notebook is a visual and
> explanatory review surface. It **does not define or override PASS/FAIL**, does
> not write comments or approval records, and cannot authorize 500-case
> generation. Formal results come only from the independent versioned JSON
> reports displayed below.

Dataset: `PAR-S-TARE-HCC-NoPVI-SYN-v2-linux50`  
Scope: frozen 50-case Linux pilot, train/val/test = 40/5/5.
"""
        ),
        _markdown(
            """
## 1. Setup and frozen identity

This section loads existing evidence only. The frozen dataset identity and
manifest digest are checked before any visual is displayed. A mismatch stops
the Notebook rather than silently reviewing a different dataset.
"""
        ),
        _code(
            f"""
from pathlib import Path
import hashlib
import json

import pandas as pd
from IPython.display import Image, JSON as DisplayJSON, Markdown, display

DATASET_ROOT = Path(r"{dataset}")
QA_ROOT = Path(r"{qa}")
EXPECTED_DATASET_ID = "{EXPECTED_DATASET_ID}"
EXPECTED_MANIFEST_SHA256 = "{EXPECTED_MANIFEST_SHA256}"

def read_json(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{{path}} must contain a JSON object")
    return value

def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def show_image(path, width=1100):
    image_path = Path(path)
    if image_path.is_file():
        display(Image(filename=str(image_path), width=width))
    else:
        display(Markdown(f"**Missing visual artifact:** `{{image_path}}`"))

marker = read_json(DATASET_ROOT / "DATASET_COMPLETE.json")
automatic = read_json(QA_ROOT / "TASK12G_AUTOMATIC_ACCEPTANCE.json")
generator_gate = read_json(QA_ROOT / "generator_gate.json")
loader_gate = read_json(QA_ROOT / "loader_gate.json")
task12b = read_json(QA_ROOT / "task12b_gate_summary.json")
exploratory_raw = read_json(QA_ROOT / "clinical_alignment_exploratory.json")
visual = read_json(QA_ROOT / "visual_artifacts.json")

manifest_path = DATASET_ROOT / marker["manifest_relative_path"]
actual_manifest_sha256 = sha256_file(manifest_path)
assert marker["dataset_id"] == EXPECTED_DATASET_ID
assert marker["manifest_sha256"] == EXPECTED_MANIFEST_SHA256
assert actual_manifest_sha256 == EXPECTED_MANIFEST_SHA256
assert automatic["dataset_id"] == EXPECTED_DATASET_ID
assert automatic["manifest_sha256"] == EXPECTED_MANIFEST_SHA256
assert automatic['go_for_500_case_generation'] is False

case_metrics_path = Path(generator_gate["case_metrics_path"])
case_rows = [
    json.loads(line)
    for line in case_metrics_path.read_text(encoding="utf-8").splitlines()
    if line.strip()
]
case_frame = pd.DataFrame(case_rows)

display(pd.DataFrame([{{
    "dataset_id": marker["dataset_id"],
    "dataset_version": marker["dataset_version"],
    "case_count": marker["case_count"],
    "manifest_sha256": marker["manifest_sha256"],
    "automatic_status": automatic["status"],
    "manual_review_status": automatic["manual_review_status"],
    "go_for_500": automatic["go_for_500_case_generation"],
}}]))
"""
        ),
        _markdown(
            """
## 2. Acceptance structure

```mermaid
flowchart LR
    A["Frozen integrity"] --> B["Generator artifact / statistics / visuals"]
    B --> C["PAR-S_2 frozen-manifest loader"]
    C --> D["Cohort statistics"]
    D --> E["projection_coordinate_gate_v2"]
    E --> F["clinical_projection_quality_gate_v1"]
    F --> G["clinical_alignment_exploratory_report_v1"]
    G --> H["Manual visual review"]

    E:::blocking
    F:::blocking
    G:::diagnostic
    H:::manual

    classDef blocking fill:#f7d6d0,stroke:#8a2f20;
    classDef diagnostic fill:#dceaf7,stroke:#285f8f;
    classDef manual fill:#f4edcf,stroke:#806b1b;
```

- Frozen integrity, Generator audit, loader, coordinate identity, and clinical
  projection quality are blocking.
- The 480-transform clinical alignment search is diagnostic and non-blocking.
- Human review is separate from this Notebook and remains pending.
"""
        ),
        _markdown(
            """
## 3. Formal automatic gate summary

The table below displays formal statuses exactly as written by the independent
reports. The Notebook does not recompute a status from the measurements.
"""
        ),
        _code(
            """
gate_frame = pd.DataFrame(automatic['gate_rows'])[
    ["gate_id", "blocking", "status", "schema_version", "meaning", "path", "sha256"]
]
display(gate_frame)
"""
        ),
        _markdown(
            """
## 4. Cohort statistical review

Continuous distributions use all 50 frozen cases for visibility. Red markers in
the official figures identify the three zero-population-weight perfusion
mismatch challenges; `3/50` is **not** interpreted as clinical prevalence.
Population-only summaries use the remaining 47 cases.
"""
        ),
        _code(
            """
aggregate = generator_gate["aggregate_statistics"]
display(pd.DataFrame([{
    "all_cases": aggregate["case_count"],
    "population_cases": aggregate["population_case_count"],
    "challenge_cases": aggregate["challenge_case_count"],
    "challenge_case_ids": ", ".join(aggregate["challenge_case_ids"]),
    "challenge_semantics": aggregate["challenge_semantics"],
}]))

for name, artifact in visual["statistics"].items():
    display(Markdown(f"### {name.replace('_', ' ').title()}"))
    show_image(artifact["path"], width=1050)

summary_columns = [
    "case_id", "split", "sex", "liver_morphology", "liver_volume_ml",
    "liver_extent_si_mm", "liver_extent_ap_mm", "liver_extent_lr_mm",
    "left_fraction", "s1_3_to_s4_8_ratio", "surface_roughness",
    "tumor_count", "dmax_mm", "lobe_extent", "tumor_fraction_liver",
    "tnr_mean_median", "tnr_max_maximum", "necrotic_fraction_max",
    "injection_territory", "mismatch_challenge", "projection_weight_sum",
    "view_sum_cv", "view_sum_ratio", "outer_8px_count_fraction",
]
display(case_frame[summary_columns])
"""
        ),
        _markdown(
            """
## 5. All 50 cases

The official nine-panel boards were generated by the independent Generator
audit. Every case appears exactly once in the five grouped sections below.
Direction labels follow source `ZYX/SAR`: axial L/R and P/A, coronal L/R and
foot/head, sagittal P/A and foot/head, plus an anterior A→P view.
"""
        ),
        _code(
            """
compact_columns = [
    "case_id", "split", "liver_morphology", "injection_territory",
    "mismatch_challenge", "tumor_count", "dmax_mm", "tnr_mean_median",
    "liver_volume_ml", "tumor_fraction_liver", "projection_weight_sum", "status",
]

def render_case_group(group_index):
    case_ids = visual["case_groups"][group_index]
    group_frame = (
        case_frame.set_index("case_id").loc[case_ids, compact_columns[1:]].reset_index()
    )
    display(group_frame)
    for case_id in case_ids:
        display(Markdown(f"#### {case_id}"))
        show_image(visual["case_boards"][case_id]["path"], width=1050)

display_case_group = render_case_group
"""
        ),
    ]
    for index in range(5):
        cells.extend(
            [
                _markdown(f"### 5.{index + 1} Case group {index + 1}/5"),
                _code(f"display_case_group({index})"),
            ]
        )
    cells.extend(
        [
            _markdown(
                """
## 6. Focus cases

Focus cases are selected automatically from the formal Generator report:
all mismatch challenges, minimum/maximum liver volume, Dmax, tumor burden,
projection total, and every case named by an automatic gate. Repeated reasons
are accumulated without duplicating the case.
"""
            ),
            _code(
                """
focus_frame = pd.DataFrame(automatic["focus_cases"])
display(focus_frame)
for item in automatic["focus_cases"]:
    case_id = item["case_id"]
    display(Markdown(
        f"### {case_id} — reasons: {', '.join(item['reasons'])}"
    ))
    show_image(visual["case_boards"][case_id]["path"], width=1150)
"""
            ),
            _markdown(
                """
## 7. Projection coordinate and quality evidence

Coordinate identity and clinical full-physics plausibility answer different
questions:

- `projection_coordinate_gate_v2` is blocking and is the only evidence allowed
  to establish the frozen storage transform.
- `clinical_projection_quality_gate_v1` is blocking for projection support and
  absolute frozen-transform engineering plausibility.
- `clinical_alignment_exploratory_report_v1` retains the complete 480-transform
  ranking, but non-unique ranking is non-blocking by contract.
"""
            ),
            _code(
                """
display(pd.DataFrame([{
    "coordinate_contract_id": generator_gate["projection_coordinate_contract_id"],
    "loader_transform_id": generator_gate["loader_transform_id"],
    "absolute_projection_scale_retained": generator_gate[
        "absolute_projection_scale_retained"
    ],
}]))
display(pd.DataFrame.from_dict(visual["direction_labels"], orient="index"))

formal_projection_rows = [
    row for row in automatic["gate_rows"]
    if row["gate_id"] in {
        "projection_coordinate_gate_v2",
        "clinical_projection_quality_gate_v1",
        "clinical_alignment_exploratory_report_v1",
    }
]
display(pd.DataFrame(formal_projection_rows))

projection_gates = task12b["gates"]
display(DisplayJSON({
    "projection_coordinate_gate_v2": projection_gates[
        "projection_coordinate_gate_v2"
    ],
    "clinical_projection_quality_gate_v1": projection_gates[
        "clinical_projection_quality_gate_v1"
    ],
}))

decision = exploratory_raw.get("decision", {})
ranking = decision.get("ranking", [])
ranking_rows = []
for rank, entry in enumerate(ranking, start=1):
    transform = entry.get("transform", {})
    metrics = entry.get("metrics", {})
    ranking_rows.append({
        "rank": rank,
        "transform_id": transform.get("transform_id"),
        "correlation": metrics.get("normalized_correlation"),
        "scale_fit_nrmse": metrics.get("scale_fit_nrmse"),
        "centroid_rmse_pixels": metrics.get("centroid_rmse_pixels"),
        "composite_score": metrics.get("composite_score"),
    })
display(pd.DataFrame(ranking_rows))
"""
            ),
            _markdown(
                """
## 8. Automatic conclusion and manual-review boundary

This page reports the automatic outcome and the remaining human task. It does
not contain an approval button, editable checklist, or comment export.
"""
            ),
            _code(
                """
display(Markdown(
    f"### Automatic status: **{automatic['status'].upper()}**\\n\\n"
    f"- Blocking gates passed: `{automatic['automatic_gate_passed']}`\\n"
    f"- Manual review status: `{automatic['manual_review_status']}`\\n"
    f"- 500-case generation: `{automatic['go_for_500_case_generation']}`\\n"
    f"- Next action: {automatic['next_action']}"
))
assert automatic['go_for_500_case_generation'] is False
display(pd.DataFrame([{
    "automatic_gate_passed": automatic["automatic_gate_passed"],
    "manual_review_required": automatic["manual_review_required"],
    "manual_review_status": automatic["manual_review_status"],
    "go_for_500_case_generation": automatic["go_for_500_case_generation"],
    "notebook_authority": automatic["notebook_authority"],
}]))
"""
            ),
        ]
    )

    notebook = nbformat.v4.new_notebook(
        cells=cells,
        metadata={
            "kernelspec": {
                "display_name": "Python (SPECT)",
                "language": "python",
                "name": "python3",
            },
            "language_info": {
                "name": "python",
                "version": "3.11",
            },
            "task12g": {
                "authority": "informational_read_only",
                "dataset_id": EXPECTED_DATASET_ID,
                "manifest_sha256": EXPECTED_MANIFEST_SHA256,
                "qa_root": str(qa),
            },
        },
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(
        output,
        nbformat.writes(notebook, version=4).encode("utf-8"),
    )
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa-root", type=Path, default=DEFAULT_QA_ROOT)
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output = build_notebook(
        args.qa_root,
        args.output,
        dataset_root=args.dataset_root,
    )
    print(
        f'{{"status":"created","notebook":"{str(output).replace(chr(92), chr(92) * 2)}"}}'
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
