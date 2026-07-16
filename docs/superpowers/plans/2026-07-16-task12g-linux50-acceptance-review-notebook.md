# Task 12G Linux50 Acceptance Review Notebook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fail-closed local 50-case acceptance pipeline whose independent scripts produce authoritative JSON and visual evidence, plus an executed read-only Notebook that explains and displays those artifacts without defining PASS/FAIL.

**Architecture:** Add a small pure acceptance-model module, a Generator-side statistical/visual audit, a cross-repository gate orchestrator, and a Notebook builder. Formal scripts write only to `D:\PFE-U\PAR\outputs\pars_v2_linux50_v2_qa`; the Notebook reads those outputs and never writes to the frozen dataset or creates manual approval records.

**Tech Stack:** Python 3.11, NumPy, pandas, Matplotlib, `nbformat`, `nbclient`, JSON/JSONL, SHA-256, pytest, existing PAR-S V2 case/manifest contracts, existing PAR-S_2 loader and Task 12B projection gates.

## Global Constraints

- Do not connect to HPC or any server.
- Do not rerun SIMIND.
- The frozen dataset root is `D:\PFE-U\PAR\outputs\pars_v2_linux50_v2`.
- The QA root is `D:\PFE-U\PAR\outputs\pars_v2_linux50_v2_qa` and must resolve outside the dataset root.
- Expected dataset ID is `PAR-S-TARE-HCC-NoPVI-SYN-v2-linux50`.
- Expected dataset version is `2.0.0-linux50-v2`.
- Expected case count is `50`, split `40/5/5`.
- Expected manifest SHA-256 is `d44a77f4604bc1df192c6af0674341c704d4e069d1665e1fe159832adfeae722`.
- Coordinate contract remains `pars_simind_v8_xcat_zyx_sar_v1`.
- Loader transform remains `simind_v8_xcat_v1_views_forward_roll000_det_v_flip_det_u_keep`.
- The three mismatch challenge cases are `case_00000`, `case_00001`, and `case_00002`; they have zero population weight and are never reported as prevalence.
- Absolute projection scale is retained. Only explicitly labelled per-view shape curves may divide by their own mean.
- The Notebook is `informational_read_only`: no thresholds, pass/fail controls, comments, approval export, or `go_for_500_case_generation=true`.
- Formal 500-case release remains blocked pending independent gates and separate manual approval.

---

### Task 1: Pure Task 12G acceptance model

**Files:**
- Create: `src/core/task12g_acceptance.py`
- Create: `tests/test_task12g_acceptance.py`

**Interfaces:**
- Produces: `ensure_qa_root_outside_dataset(dataset_root, qa_root) -> tuple[Path, Path]`
- Produces: `group_case_ids(case_ids, group_size=10) -> list[list[str]]`
- Produces: `partition_population_and_challenges(case_rows) -> dict[str, list[dict[str, object]]]`
- Produces: `select_focus_cases(case_rows, failed_case_ids=()) -> list[dict[str, object]]`
- Produces: `gate_evidence_rows(reports) -> list[dict[str, object]]`
- Produces: `build_automatic_summary(...) -> dict[str, object]`

- [ ] **Step 1: Write failing path and grouping tests**

```python
def test_qa_root_must_be_outside_frozen_dataset(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    with pytest.raises(ValueError, match="outside"):
        ensure_qa_root_outside_dataset(dataset, dataset / "qa")


def test_fifty_cases_are_grouped_exactly_once():
    ids = [f"case_{index:05d}" for index in range(50)]
    groups = group_case_ids(ids, group_size=10)
    assert [len(group) for group in groups] == [10, 10, 10, 10, 10]
    assert [case_id for group in groups for case_id in group] == ids
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
& 'D:\AnacondaEnvs\envs\SPECT\python.exe' -m pytest -q tests\test_task12g_acceptance.py
```

Expected: collection failure because `src.core.task12g_acceptance` does not exist.

- [ ] **Step 3: Implement path and grouping helpers**

```python
def ensure_qa_root_outside_dataset(dataset_root: Path, qa_root: Path) -> tuple[Path, Path]:
    dataset = dataset_root.resolve()
    qa = qa_root.resolve()
    if qa == dataset or dataset in qa.parents:
        raise ValueError("QA root must resolve outside the frozen dataset root")
    return dataset, qa


def group_case_ids(case_ids: Sequence[str], group_size: int = 10) -> list[list[str]]:
    if group_size <= 0:
        raise ValueError("group_size must be positive")
    ids = list(case_ids)
    if len(ids) != len(set(ids)):
        raise ValueError("case IDs must be unique")
    return [ids[start : start + group_size] for start in range(0, len(ids), group_size)]
```

- [ ] **Step 4: Add failing challenge and focus-selection tests**

```python
def test_challenges_are_separate_from_population():
    rows = [
        {"case_id": "case_00000", "mismatch_challenge": True, "population_weight": 0.0},
        {"case_id": "case_00003", "mismatch_challenge": False, "population_weight": 1.0},
    ]
    partition = partition_population_and_challenges(rows)
    assert [row["case_id"] for row in partition["challenge"]] == ["case_00000"]
    assert [row["case_id"] for row in partition["population"]] == ["case_00003"]


def test_focus_selection_deduplicates_reasons():
    rows = [
        {
            "case_id": "case_00000",
            "mismatch_challenge": True,
            "liver_volume_ml": 1000.0,
            "dmax_mm": 20.0,
            "tumor_fraction_liver": 0.01,
            "projection_weight_sum": 100.0,
        },
        {
            "case_id": "case_00001",
            "mismatch_challenge": False,
            "liver_volume_ml": 2000.0,
            "dmax_mm": 100.0,
            "tumor_fraction_liver": 0.20,
            "projection_weight_sum": 900.0,
        },
    ]
    focus = select_focus_cases(rows, failed_case_ids=["case_00000"])
    case0 = next(item for item in focus if item["case_id"] == "case_00000")
    assert "mismatch_challenge" in case0["reasons"]
    assert "automatic_gate_attention" in case0["reasons"]
    assert len({item["case_id"] for item in focus}) == len(focus)
```

- [ ] **Step 5: Implement semantic partition and focus selection**

Implement fail-closed validation that requires exactly the frozen challenge IDs
to be challenge rows, requires their population weight to be zero, rejects a
population row with zero/negative population weight, and selects min/max
liver volume, Dmax, tumor burden, and projection total with accumulated reasons.

- [ ] **Step 6: Add failing gate-authority tests**

```python
def test_gate_rows_preserve_formal_status():
    rows = gate_evidence_rows(
        [
            {
                "gate_id": "clinical_projection_quality_gate_v1",
                "blocking": True,
                "report": {"schema_version": "x", "status": "fail"},
                "path": Path("quality.json"),
                "sha256": "a" * 64,
            }
        ]
    )
    assert rows[0]["status"] == "fail"


def test_automatic_summary_never_releases_500():
    summary = build_automatic_summary(
        dataset_id="dataset",
        manifest_sha256="b" * 64,
        gate_rows=[{"blocking": True, "status": "pass"}],
        focus_cases=[],
    )
    assert summary["automatic_gate_passed"] is True
    assert summary["go_for_500_case_generation"] is False
    assert summary["manual_review_status"] == "pending"
```

- [ ] **Step 7: Implement formal report shaping**

Preserve report statuses exactly, compute `automatic_gate_passed` only from
blocking rows, mark exploratory evidence non-blocking, and always emit:

```python
{
    "status": "pass_awaiting_manual_review" if automatic_gate_passed else "fail",
    "manual_review_required": True,
    "manual_review_status": "pending",
    "go_for_500_case_generation": False,
}
```

- [ ] **Step 8: Run focused tests**

Expected: all `tests/test_task12g_acceptance.py` tests pass.

- [ ] **Step 9: Commit**

```powershell
git add src/core/task12g_acceptance.py tests/test_task12g_acceptance.py
git commit -m "feat: model Task 12G acceptance evidence"
```

### Task 2: Independent 50-case statistical and visual audit

**Files:**
- Create: `scripts/audit_task12g_linux50.py`
- Create: `tests/test_audit_task12g_linux50.py`

**Interfaces:**
- Consumes: frozen V2 `DATASET_COMPLETE.json`, `case_manifest.jsonl`, case records, NPZ, metadata, and read-only `.a00`.
- Produces: `audit_task12g(dataset_root, qa_root) -> dict[str, object]`
- Produces:
  - `generator_gate.json`
  - `generator_gate.md`
  - `case_metrics.jsonl`
  - `visual_artifacts.json`
  - `statistics/*.png`
  - `cases/case_00000.png` through `cases/case_00049.png`

- [ ] **Step 1: Write failing projection-metric tests**

Create a small `(3, 8, 8)` projection fixture and assert exact total, per-view
totals, coefficient of variation, view ratio, positive support, outer support,
centroid ranges, and `(views, detector_u)` sinogram shape.

- [ ] **Step 2: Run the test and verify RED**

Expected: import failure because `scripts.audit_task12g_linux50` does not exist.

- [ ] **Step 3: Implement bounded projection metrics**

Use float64 reductions and reject non-finite, negative, wrong-rank, or empty-view
projections. Never normalize the stored total. Return the raw per-view totals
and a separate normalized shape curve.

- [ ] **Step 4: Write failing case-row extraction tests**

Use a minimal metadata fixture containing patient, liver, tumors, activity,
quality control, and projection stats. Assert:

```python
assert row["dmax_mm"] == max(lesion["recist_3d_mm"] for lesion in lesions)
assert row["tnr_mean_median"] == pytest.approx(np.median([...]))
assert row["necrotic_fraction_max"] == max(...)
assert row["mismatch_semantics"] == "coverage_challenge_not_prevalence"
```

- [ ] **Step 5: Implement case-row extraction**

Extract the complete statistical contract from frozen metadata and computed
projection metrics. Preserve lesion-level TNR/necrosis in a nested list and
produce scalar cohort columns for plots.

- [ ] **Step 6: Write failing rendering tests**

Create small arrays and assert that a rendered case board:

- exists outside the dataset root;
- includes nine panels;
- receives labels `L`, `R`, `H/S`, `F/I`, `A`, and `P`;
- does not modify source arrays;
- closes its Matplotlib figure.

- [ ] **Step 7: Implement official case boards**

Each board contains:

1. tumor-centred axial overlay;
2. tumor-centred coronal overlay;
3. tumor-centred sagittal overlay;
4. `mu_true_140kev`;
5. `mu_input_140kev`;
6. activity relative/probability;
7. anterior directional projection of liver/tumor/perfusion;
8. log SIMIND sinogram;
9. raw per-view total plus a clearly labelled `/ mean` shape curve.

Use screen-direction annotations derived from source `ZYX/SAR`. Process one
case at a time and close figures after atomic PNG output.

- [ ] **Step 8: Write failing frozen-dataset audit test**

Create a two-case synthetic frozen fixture through the existing case-writer
test helpers, then assert:

- output root is outside the dataset;
- formal status fails when expected count is 50 but observed count is 2;
- the source manifest bytes and SHA-256 are unchanged;
- no file appears under `dataset_root/qa`.

- [ ] **Step 9: Implement full audit**

Use `validate_frozen_dataset`/existing case validation entry points so all
manifest artifact hashes are checked. For every case:

- verify finite/non-negative arrays;
- verify tumor containment;
- verify activity probability and SIMIND source sums;
- verify `mu_true` and `mu_input` separation;
- verify metadata quality;
- recompute projection metrics and bind them to metadata;
- apply the frozen clinical support thresholds;
- render the case board.

Aggregate categorical counts and continuous distributions. Render four
statistics figures and write an artifact registry with SHA-256 for every image.

- [ ] **Step 10: Run focused audit tests**

Expected: all tests in `tests/test_audit_task12g_linux50.py` pass.

- [ ] **Step 11: Commit**

```powershell
git add scripts/audit_task12g_linux50.py tests/test_audit_task12g_linux50.py
git commit -m "feat: audit and visualize Task 12G Linux50"
```

### Task 3: Cross-repository formal gate orchestrator

**Files:**
- Create: `scripts/finalize_task12g_acceptance.py`
- Create: `tests/test_finalize_task12g_acceptance.py`

**Interfaces:**
- Produces: `build_stage_commands(config) -> list[StageCommand]`
- Produces: `finalize_acceptance(...) -> dict[str, object]`
- Reuses:
  - Generator `audit_task12g_linux50.py`
  - PAR-S_2 `validate_synthetic_dataset.py --expected-count 50`
  - PAR-S_2 `build_projection_alignment_descriptor.py`
  - PAR-S_2 `search_projection_transform.py --report-role clinical-exploratory`
  - PAR-S_2 `evaluate_task12b_gates.py`
  - accepted Task 12E Linux `projection_coordinate_gate_v2` report

- [ ] **Step 1: Write failing command-contract test**

Assert exact stage order and arguments. The coordinate report is read-only
inherited evidence and is not regenerated. The clinical descriptor/search uses
the current 50-case frozen manifest.

- [ ] **Step 2: Run and verify RED**

Expected: missing module/function.

- [ ] **Step 3: Implement deterministic stage construction**

Stages:

1. `generator_statistics_visual_gate`
2. `pars2_manifest_loader_gate`
3. `clinical_alignment_descriptor`
4. `clinical_alignment_exploratory`
5. `task12b_projection_gates`
6. `automatic_acceptance_summary`

Every subprocess receives an explicit executable, working directory, and
absolute input/output paths.

- [ ] **Step 4: Write failing summary-binding tests**

Feed small formal reports and assert:

- dataset ID and manifest SHA must agree across Generator, loader, and marker;
- coordinate report role must be blocking `projection-coordinate-gate`;
- clinical quality is blocking;
- exploratory is non-blocking;
- a formal failure cannot become a pass;
- `go_for_500_case_generation` remains false.

- [ ] **Step 5: Implement final report binding**

Write:

- `TASK12G_AUTOMATIC_ACCEPTANCE.json`
- `TASK12G_AUTOMATIC_ACCEPTANCE.md`
- `PROGRESS.json`

The final status is `pass_awaiting_manual_review` only when all blocking gates
pass. No manual approval file is read or written by this orchestrator.

- [ ] **Step 6: Test resume behavior**

`--resume` may reuse a stage only when its recorded command, return code,
output SHA-256, dataset manifest SHA-256, and script SHA-256 still match.
Otherwise it reruns the stage or fails closed; it never trusts file existence
alone.

- [ ] **Step 7: Run focused orchestrator tests**

Expected: all `tests/test_finalize_task12g_acceptance.py` tests pass.

- [ ] **Step 8: Commit**

```powershell
git add scripts/finalize_task12g_acceptance.py tests/test_finalize_task12g_acceptance.py
git commit -m "feat: finalize Task 12G automatic acceptance"
```

### Task 4: Read-only acceptance Notebook builder

**Files:**
- Create: `scripts/build_task12g_acceptance_notebook.py`
- Create: `tests/test_task12g_acceptance_notebook.py`
- Generate: `notebook/Task12G_Linux50_Acceptance_Review.ipynb`

**Interfaces:**
- Produces: `build_notebook(qa_root, output_path) -> Path`
- Notebook reads:
  - `TASK12G_AUTOMATIC_ACCEPTANCE.json`
  - `generator_gate.json`
  - `loader_gate.json`
  - `task12b_gate_summary.json`
  - `clinical_alignment_exploratory.json`
  - `case_metrics.jsonl`
  - `visual_artifacts.json`

- [ ] **Step 1: Write failing notebook-structure test**

Build into a temporary path and inspect with `nbformat`. Assert required
sections, warning text, five case groups, focus section, coordinate section,
and conclusion section.

- [ ] **Step 2: Write failing read-only-authority test**

Assert source cells contain no:

- `open(..., "w")`, `write_text`, `write_bytes`, `to_json`, or approval export;
- widgets or input controls;
- `go_for_500_case_generation = True`;
- copied numerical gate thresholds.

Assert the Notebook displays statuses from loaded JSON rather than computing
formal status.

- [ ] **Step 3: Implement the Notebook builder**

Use `nbformat.v4`. The Notebook contains:

1. title and authority warning;
2. imports and path parameters;
3. frozen identity verification;
4. acceptance flow Mermaid/Markdown;
5. formal gate table;
6. cohort statistical images and tables;
7. five ten-case sections displaying official boards;
8. focus cases with reasons;
9. coordinate and projection gate explanation;
10. final automatic status and manual-review reminder.

Use `IPython.display.Image`, `display`, pandas, and Markdown only for
presentation. Do not recalculate formal thresholds.

- [ ] **Step 4: Generate and validate Notebook JSON**

Run:

```powershell
& 'D:\AnacondaEnvs\envs\SPECT\python.exe' `
  scripts\build_task12g_acceptance_notebook.py
```

Expected: valid Notebook at the approved path.

- [ ] **Step 5: Run focused Notebook tests**

Expected: all `tests/test_task12g_acceptance_notebook.py` tests pass.

- [ ] **Step 6: Commit**

```powershell
git add scripts/build_task12g_acceptance_notebook.py `
  tests/test_task12g_acceptance_notebook.py `
  notebook/Task12G_Linux50_Acceptance_Review.ipynb
git commit -m "feat: add Task 12G acceptance review notebook"
```

### Task 5: Real-data execution and verification

**Files:**
- Modify only generated QA files under:
  `D:\PFE-U\PAR\outputs\pars_v2_linux50_v2_qa`
- Execute and update:
  `notebook/Task12G_Linux50_Acceptance_Review.ipynb`

**Interfaces:**
- Consumes the frozen 50-case dataset and accepted Task 12E coordinate report.
- Produces a complete automatic review package without manual approval.

- [ ] **Step 1: Record the source manifest digest**

```powershell
Get-FileHash -Algorithm SHA256 `
  'D:\PFE-U\PAR\outputs\pars_v2_linux50_v2\case_manifest.jsonl'
```

Expected:
`D44A77F4604BC1DF192C6AF0674341C704D4E069D1665E1FE159832ADFEAE722`.

- [ ] **Step 2: Run focused and regression tests**

Run Task 12G acceptance tests plus existing Task 12D–12G, reproducibility, case
writer, and dataset-freeze tests in the SPECT environment.

- [ ] **Step 3: Run the automatic acceptance pipeline**

```powershell
$py = 'D:\AnacondaEnvs\envs\SPECT\python.exe'
$script = 'D:\PFE-U\PAR\.worktrees\PAR-S-Generator-task12\scripts\finalize_task12g_acceptance.py'

& $py $script --resume
```

Expected terminal status is either:

- `pass_awaiting_manual_review`; or
- a named blocking gate failure with evidence paths.

It must never return approval for 500 cases.

- [ ] **Step 4: Execute Notebook top-to-bottom**

```powershell
& 'D:\AnacondaEnvs\envs\SPECT\python.exe' -m jupyter nbconvert `
  --execute --to notebook --inplace `
  'D:\PFE-U\PAR\.worktrees\PAR-S-Generator-task12\notebook\Task12G_Linux50_Acceptance_Review.ipynb' `
  --ExecutePreprocessor.timeout=900
```

Expected: exit code `0`, no error outputs, all five ten-case sections rendered.

- [ ] **Step 5: Verify frozen dataset is unchanged**

Recompute the manifest SHA-256 and compare with Step 1. Confirm no QA directory
or Notebook artifact exists under the frozen dataset root.

- [ ] **Step 6: Inspect executed Notebook**

Confirm:

- 50 compact case boards appear exactly once in grouped sections;
- focus cases include all challenge and extreme/attention reasons;
- direction annotations are readable;
- figures are not clipped;
- formal failures, if any, remain visible;
- final page says manual review pending and 500 generation false.

- [ ] **Step 7: Run final verification**

Use `superpowers:verification-before-completion`. Report exact test counts,
pipeline status, Notebook path, QA root, manifest digest before/after, and all
remaining blockers/caveats.

