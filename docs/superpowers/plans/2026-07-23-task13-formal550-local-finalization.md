# Task 13 Formal550 Local Finalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the downloaded, master-passed Task13 Linux results into independently frozen 500-main and 50-negative PAR-S V2 datasets, then produce fail-closed automatic acceptance evidence and a read-only review notebook.

**Architecture:** A Task13-specific local finalizer validates the immutable upload bundle, both local preflight roles, the downloaded master archive, and every retained SIMIND quartet before regenerating deterministic phantom arrays. It writes two independent V2 dataset roots under one campaign root and binds them with a campaign completion marker. Separate acceptance code consumes only the frozen roots and produces machine-authoritative JSON gates; the notebook only renders those frozen outputs.

**Tech Stack:** Python 3.11, NumPy, existing PAR-S Generator V2 case writer/freezer, existing PAR-S_2 manifest loader gate, pytest, nbformat/Jupyter.

## Global Constraints

- Never launch SIMIND locally; consume only the downloaded Linux outputs.
- Preserve the downloaded archive byte-for-byte and verify SHA-256 before extraction.
- Main dataset identity is `PAR-S-TARE-HCC-NoPVI-SYN-v2` version `2.0.0`, with 500 cases and splits 400/50/50.
- Negative dataset identity is `PAR-S-TARE-HCC-NoPVI-NEG-v2` version `2.0.0`, with 50 test-only, zero-population-weight cases.
- Campaign identity is `PAR-S-V2-FORMAL550` version `2.0.0`, with exactly 550 unique case IDs and 550 unique SIMIND `/RR` seeds.
- Projection shape remains `(60, 128, 128)` and the accepted maximum per-view max/min ratio remains `80.0`.
- Automatic JSON gates are authoritative. The notebook is read-only and cannot store manual notes or change PASS/FAIL.
- Resume verifies completed case hashes and skips them; it never overwrites a frozen dataset.

---

### Task 1: Negative-compatible completed metadata

**Files:**
- Modify: `src/core/pilot_v2.py`
- Test: `tests/test_task13_formal550_local.py`

**Interfaces:**
- Consumes: `PreparedPilotCaseV2` from `prepare_negative_case`.
- Produces: `build_completed_metadata(...)` output with `injection_tumor_coverage_fraction=1.0` and `tumor_volume_fraction_perfused=0.0` when tumor support is empty.

- [ ] Write a failing test that constructs a negative prepared case and proves completed metadata currently divides by zero.
- [ ] Run `python -m pytest tests/test_task13_formal550_local.py::test_completed_metadata_supports_tumor_negative_case -q` and confirm the expected failure.
- [ ] Guard the two tumor-derived fractions in `build_completed_metadata` while leaving positive-case behavior unchanged:

```python
tumor_voxels = int(np.count_nonzero(tumor_union))
perfusion_voxels = int(np.count_nonzero(perfusion))
tumor_coverage = intersection / tumor_voxels if tumor_voxels else 1.0
tumor_fraction_perfused = (
    intersection / perfusion_voxels if perfusion_voxels else 0.0
)
```

- [ ] Re-run the focused test and existing negative/case-writer tests.
- [ ] Commit the independently testable metadata change.

### Task 2: Safe Task13 archive validation and role contracts

**Files:**
- Create: `scripts/finalize_task13_formal550_local.py`
- Create: `tests/test_task13_formal550_local.py`

**Interfaces:**
- Consumes: downloaded `task13_formal550_results.tar.gz`, its sidecar, `task13_formal550_preflight_v1/{main,negative}`, and the immutable upload bundle.
- Produces: `stage_results_archive(...)`, `validate_formal_inputs(...)`, and role contracts for `main` and `negative`.

- [ ] Write failing tests for path-traversal rejection, archive SHA mismatch, exact role counts/splits, role-specific IDs, master/schema binding, and prohibition of local SIMIND execution.
- [ ] Run the new tests and confirm each fails because the Task13 local finalizer does not exist.
- [ ] Implement atomic safe extraction using Python tar filtering and a staging completion marker bound to the archive SHA:

```python
def stage_results_archive(
    archive: Path,
    sidecar: Path,
    staging_root: Path,
    *,
    resume: bool,
) -> Path:
    expected_sha = read_sha256_sidecar(sidecar, archive.name)
    if sha256_file(archive) != expected_sha:
        raise Formal550LocalError("downloaded result archive SHA-256 mismatch")
    return extract_archive_atomically(archive, staging_root, resume=resume)
```

- [ ] Implement bundle/preflight/master/node/case validation using the Task13 runtime schemas and existing quartet validation:

```python
@dataclass(frozen=True)
class RoleContract:
    role: str
    preflight_root: Path
    generation: Mapping[str, object]
    split: Mapping[str, object]
    entries: tuple[Mapping[str, object], ...]
    summaries: Mapping[str, Mapping[str, object]]
    expected_case_ids: tuple[str, ...]
```

- [ ] Implement `--validate-only`, `--resume`, and `--max-cases` argument contracts with immutable default paths.
- [ ] Re-run the Task13 local tests and Task12F/Task13 regression tests.
- [ ] Commit the archive and contract layer.

### Task 3: Dual dataset writer, freeze, and campaign marker

**Files:**
- Modify: `scripts/finalize_task13_formal550_local.py`
- Test: `tests/test_task13_formal550_local.py`

**Interfaces:**
- Consumes: validated role contracts and downloaded completed SIMIND results.
- Produces: `<output>/main/DATASET_COMPLETE.json`, `<output>/negative/DATASET_COMPLETE.json`, and `<output>/FORMAL550_COMPLETE.json`.

- [ ] Write failing tests for main/negative dataset contracts, exact required artifacts, role-specific preparation, independent progress, resumability, and the campaign marker binding both manifest hashes.
- [ ] Run the tests and confirm the missing writer behavior fails.
- [ ] Regenerate deterministic main cases with `prepare_population_case` and negative cases with `prepare_negative_case`, prove byte identity against each frozen preflight, and wrap downloaded outputs without executing SIMIND.
- [ ] Write/freeze each role independently using `write_case_v2` and `freeze_dataset`.
- [ ] Emit role progress JSON and a campaign completion JSON only after both role freezes revalidate idempotently. The campaign marker has this exact top-level contract:

```python
{
    "schema_version": "pars_v2_task13_formal550_complete_v1",
    "status": "complete",
    "campaign": {"dataset_id": "PAR-S-V2-FORMAL550", "dataset_version": "2.0.0"},
    "case_count": 550,
    "role_case_counts": {"main": 500, "negative": 50},
    "datasets": {
        "main": {"relative_root": "main", "manifest_sha256": main_marker.manifest_sha256},
        "negative": {"relative_root": "negative", "manifest_sha256": negative_marker.manifest_sha256},
    },
}
```

- [ ] Re-run Task13 local, case-writer, dataset-freeze, negative, and Task12G regression tests.
- [ ] Commit the writer/freeze layer.

### Task 4: Formal automatic acceptance

**Files:**
- Create: `scripts/finalize_task13_formal550_acceptance.py`
- Create: `tests/test_task13_formal550_acceptance.py`

**Interfaces:**
- Consumes: `FORMAL550_COMPLETE.json`, both role dataset roots, PAR-S_2 `validate_synthetic_dataset.py`, and frozen Task12G coordinate evidence.
- Produces: `TASK13_FORMAL550_AUTOMATIC_ACCEPTANCE.json`, Markdown summary, per-role loader gates, projection/statistical gate, and progress/log records.

- [ ] Write failing tests for exact campaign binding, both loader subprocess commands, 500/50 role counts, 400/50/50 plus test-only splits, negative zero-tumor semantics, projection shape/support/ratio threshold, inherited coordinate evidence, resume, and fail-closed final aggregation.
- [ ] Run tests and confirm the script is missing.
- [ ] Implement role loader stages with expected counts 500 and 50.
- [ ] Implement all-case projection/artifact/statistical checks with the frozen threshold 80.0 and deterministic focus-case selection.
- [ ] Bind every evidence file by SHA-256 in the final automatic acceptance JSON using:

```python
{
    "schema_version": "pars_v2_task13_formal550_automatic_acceptance_v1",
    "status": "pass",
    "automatic_gate_passed": True,
    "case_count": 550,
    "role_case_counts": {"main": 500, "negative": 50},
    "gate_rows": [
        evidence_row("formal550_generator_gate_v1", generator_gate),
        evidence_row("formal550_main_loader_gate_v1", main_loader_gate),
        evidence_row("formal550_negative_loader_gate_v1", negative_loader_gate),
        evidence_row("projection_coordinate_gate_v2", coordinate_gate),
    ],
    "notebook_authority": "informational_read_only",
}
```

- [ ] Re-run acceptance and PAR-S_2 loader regression tests.
- [ ] Commit the automatic acceptance layer.

### Task 5: Read-only acceptance notebook

**Files:**
- Create: `scripts/build_task13_formal550_acceptance_notebook.py`
- Create: `notebook/Task13_Formal550_Acceptance_Review.ipynb`
- Create: `tests/test_task13_formal550_acceptance_notebook.py`

**Interfaces:**
- Consumes: frozen automatic acceptance JSON, role gate JSON, visual registry, and immutable dataset artifacts.
- Produces: a deterministic notebook that displays gate structure, role/split summaries, cohort distributions, projection metrics, and slider-based focus cases without writing notes or gate results.

- [ ] Write failing structural tests requiring explanatory Markdown, no write calls, no manual-note widgets, exact SIMIND/projector/clinical angle labels, and main/negative projection sliders.
- [ ] Run the tests and confirm the builder/notebook are absent.
- [ ] Implement the deterministic notebook builder and generate the notebook through the exact interface:

```python
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
    nbformat.write(notebook, output_path)
```

- [ ] Run structural tests and execute the notebook against fixture evidence.
- [ ] Commit the notebook layer.

### Task 6: Real archive smoke verification and handoff

**Files:**
- Modify: `docs/superpowers/plans/2026-07-23-task13-formal550-local-finalization.md`

**Interfaces:**
- Consumes: the actual downloaded archive and frozen local preflight/upload roots.
- Produces: verified commands for the user to run the full local writer, acceptance pipeline, and notebook.

- [ ] Run Task13 `--validate-only` against the real 550-case archive and confirm all bindings pass.
- [ ] Run a one-case bounded write into a disposable dedicated smoke root, confirm pause/resume semantics, then preserve or remove it only within the dedicated smoke directory.
- [ ] Run the full focused regression suite and compile every new Python script.
- [ ] Check git diff/status and confirm the downloaded archive hash is unchanged.
- [ ] Update this checklist with observed verification results and commit the handoff.
