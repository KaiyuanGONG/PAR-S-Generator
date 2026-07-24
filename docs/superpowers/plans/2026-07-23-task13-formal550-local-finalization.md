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

- [x] Write a failing test that constructs a negative prepared case and proves completed metadata currently divides by zero.
- [x] Run `python -m pytest tests/test_task13_formal550_local.py::test_completed_metadata_supports_tumor_negative_case -q` and confirm the expected failure.
- [x] Guard the two tumor-derived fractions in `build_completed_metadata` while leaving positive-case behavior unchanged:

```python
tumor_voxels = int(np.count_nonzero(tumor_union))
perfusion_voxels = int(np.count_nonzero(perfusion))
tumor_coverage = intersection / tumor_voxels if tumor_voxels else 1.0
tumor_fraction_perfused = (
    intersection / perfusion_voxels if perfusion_voxels else 0.0
)
```

- [x] Re-run the focused test and existing negative/case-writer tests.
- [x] Commit the independently testable metadata change.

### Task 2: Safe Task13 archive validation and role contracts

**Files:**
- Create: `scripts/finalize_task13_formal550_local.py`
- Create: `tests/test_task13_formal550_local.py`

**Interfaces:**
- Consumes: downloaded `task13_formal550_results.tar.gz`, its sidecar, `task13_formal550_preflight_v1/{main,negative}`, and the immutable upload bundle.
- Produces: `stage_results_archive(...)`, `validate_formal_inputs(...)`, and role contracts for `main` and `negative`.

- [x] Write failing tests for path-traversal rejection, archive SHA mismatch, exact role counts/splits, role-specific IDs, master/schema binding, and prohibition of local SIMIND execution.
- [x] Run the new tests and confirm each fails because the Task13 local finalizer does not exist.
- [x] Implement atomic safe extraction using Python tar filtering and a staging completion marker bound to the archive SHA:

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

- [x] Implement bundle/preflight/master/node/case validation using the Task13 runtime schemas and existing quartet validation:

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

- [x] Implement `--validate-only`, `--resume`, and `--max-cases` argument contracts with immutable default paths.
- [x] Re-run the Task13 local tests and Task12F/Task13 regression tests.
- [x] Commit the archive and contract layer.

### Task 3: Dual dataset writer, freeze, and campaign marker

**Files:**
- Modify: `scripts/finalize_task13_formal550_local.py`
- Test: `tests/test_task13_formal550_local.py`

**Interfaces:**
- Consumes: validated role contracts and downloaded completed SIMIND results.
- Produces: `<output>/main/DATASET_COMPLETE.json`, `<output>/negative/DATASET_COMPLETE.json`, and `<output>/FORMAL550_COMPLETE.json`.

- [x] Write failing tests for main/negative dataset contracts, exact required artifacts, role-specific preparation, independent progress, resumability, and the campaign marker binding both manifest hashes.
- [x] Run the tests and confirm the missing writer behavior fails.
- [x] Regenerate deterministic main cases with `prepare_population_case` and negative cases with `prepare_negative_case`, prove byte identity against each frozen preflight, and wrap downloaded outputs without executing SIMIND.
- [x] Write/freeze each role independently using `write_case_v2` and `freeze_dataset`.
- [x] Emit role progress JSON and a campaign completion JSON only after both role freezes revalidate idempotently. The campaign marker has this exact top-level contract:

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

- [x] Re-run Task13 local, case-writer, dataset-freeze, negative, and Task12G regression tests.
- [x] Commit the writer/freeze layer.

### Task 4: Formal automatic acceptance

**Files:**
- Create: `scripts/finalize_task13_formal550_acceptance.py`
- Create: `tests/test_task13_formal550_acceptance.py`

**Interfaces:**
- Consumes: `FORMAL550_COMPLETE.json`, both role dataset roots, PAR-S_2 `validate_synthetic_dataset.py`, and frozen Task12G coordinate evidence.
- Produces: `TASK13_FORMAL550_AUTOMATIC_ACCEPTANCE.json`, Markdown summary, per-role loader gates, projection/statistical gate, and progress/log records.

- [x] Write failing tests for exact campaign binding, both loader subprocess commands, 500/50 role counts, 400/50/50 plus test-only splits, negative zero-tumor semantics, projection shape/support/ratio threshold, inherited coordinate evidence, resume, and fail-closed final aggregation.
- [x] Run tests and confirm the script is missing.
- [x] Implement role loader stages with expected counts 500 and 50.
- [x] Implement all-case projection/artifact/statistical checks with the frozen threshold 80.0 and deterministic focus-case selection.
- [x] Bind every evidence file by SHA-256 in the final automatic acceptance JSON using:

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

- [x] Re-run acceptance and PAR-S_2 loader regression tests.
- [x] Commit the automatic acceptance layer.

### Task 5: Read-only acceptance notebook

**Files:**
- Create: `scripts/build_task13_formal550_acceptance_notebook.py`
- Create: `notebook/Task13_Formal550_Acceptance_Review.ipynb`
- Create: `tests/test_task13_formal550_acceptance_notebook.py`

**Interfaces:**
- Consumes: frozen automatic acceptance JSON, role gate JSON, visual registry, and immutable dataset artifacts.
- Produces: a deterministic notebook that displays gate structure, role/split summaries, cohort distributions, projection metrics, and slider-based focus cases without writing notes or gate results.

- [x] Write failing structural tests requiring explanatory Markdown, no write calls, no manual-note widgets, exact SIMIND/projector/clinical angle labels, and main/negative projection sliders.
- [x] Run the tests and confirm the builder/notebook are absent.
- [x] Implement the deterministic notebook builder and generate the notebook through the exact interface:

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

- [x] Run structural tests and execute the notebook against fixture evidence.
- [x] Commit the notebook layer.

### Task 6: Real archive smoke verification and handoff

**Files:**
- Modify: `docs/superpowers/plans/2026-07-23-task13-formal550-local-finalization.md`

**Interfaces:**
- Consumes: the actual downloaded archive and frozen local preflight/upload roots.
- Produces: verified commands for the user to run the full local writer, acceptance pipeline, and notebook.

- [x] Run Task13 `--validate-only` against the real 550-case archive and confirm all bindings pass.
- [x] Run a one-case bounded write into a disposable dedicated smoke root, confirm pause/resume semantics, then preserve or remove it only within the dedicated smoke directory.
- [x] Run the full focused regression suite and compile every new Python script.
- [x] Check git diff/status and confirm the downloaded archive hash is unchanged.
- [x] Update this checklist with observed verification results and commit the handoff.

## Observed Verification and Handoff

Verified locally on 2026-07-24 and production-finalized on 2026-07-25
(Europe/Paris) in the `SPECT` Conda environment.

- Real immutable-input validation passed for all 550 cases: 500 `main`, 50 `negative`, all node/master/case/quartet bindings, recomputed generation/split semantic self-hashes, exact generation-to-split bindings, exact frozen plan schemas, and exact JSON value types. The strict resumed validation passed again after the final trust-chain hardening.
- The downloaded archive remained unchanged at SHA-256 `fecbd2d485d3f28dab8e195b208d9a9b5a115cf05d7fe1741ab11e3dc8496c74`.
- Two bounded runs were performed under the dedicated non-authoritative smoke root `C:\Users\86187\AppData\Local\Temp\pars_task6_formal550_smoke_20260724_019f9401`. The first wrote `case_00000`; the resumed run hash-verified and skipped it, then wrote `case_00001`. Progress is deliberately paused at `main=2/500`, `negative=0/50`.
- No `main/DATASET_COMPLETE.json`, `negative/DATASET_COMPLETE.json`, or campaign `FORMAL550_COMPLETE.json` exists in the smoke root. The root is preserved for audit and may be deleted later only as that exact dedicated directory.
- A bounded pause is successful operational behavior but returns process code `3`; wrappers such as `conda run` may therefore display a non-zero wrapper status. Inspect the emitted JSON (`"status": "paused"`) and `PROGRESS.json`.
- The automatic acceptance now resolves coordinate evidence through the frozen Task12G chain: manual release SHA-256 `aa2eb5621cef6d5e8a2a952f041963743ed83fcd5f814813fcf24a4eb455cf96`, automatic acceptance SHA-256 `3e81bc365cd88325c2cca40af75233a5550b0025102c006bf06f43378f1541a9`, and coordinate report SHA-256 `12b83f5c0627d824cbb408c8042c159dfff9afdf39143a511dfb88f1c259108a`.
- The full production writer materialized and froze exactly 500 `main` and 50 `negative` cases under `D:\PFE-U\PAR\outputs\pars_v2_formal550_v1`. The main split is exactly 400/50/50 train/val/test; the negative split is exactly 50 test-only. Both role progress files and both `DATASET_COMPLETE.json` markers are `complete`, and the campaign marker reports exactly 550 cases.
- The production writer encountered one process-memory exhaustion after 428 main cases (`MemoryError` while allocating a 2 MiB boolean array). The writer had exited and no duplicate process was present. A single `--resume` writer hash-verified the completed cases, continued from `case_00428`, and completed both roles without rewriting frozen cases.
- Main manifest SHA-256 is `b1fb588e0f1c6d3771a317b480704c403afc505d545b59b45dcc52f8f0bd3ffe`; negative manifest SHA-256 is `9dec479577633759c3ed2af838f5f2877dcfd930d81d7ff51d68f2fa43e82bc7`. Each actual manifest hash matches both its role marker and the campaign marker. The case-directory and manifest-line counts are exactly 500 and 50. `FORMAL550_COMPLETE.json` SHA-256 is `7e11de03c82455565574d785c6ec6a9cb2b2d2e05ccc33b3e763d77c5e4e5fd4`.
- Automatic acceptance was run and then rerun with `--resume`; both runs produced the same substantive result: **FAIL**. The final authoritative JSON has SHA-256 `b2710e034a0bf8869c160fa32b9fce88d9fb899a3989483f1bd0e02afd633de7`, `automatic_gate_passed=false`, and exact role counts 500/50.
- The main loader gate (`ef073ddcb21b507c6474bf96d85616b874677a2b043d990e65879b2caa051986`), negative loader gate (`6f8341bcffaadb05955696b4b4ca835f4ae5b8b224a0ed87bfcc974a1eade2f0`), projection-coordinate gate, and inherited Task12G release chain all pass. All evidence hashes embedded in the authoritative acceptance JSON match the referenced files.
- The sole blocking result is the generator gate (`3ae7eaa0dcabec5fe46f9c56ff41581da35b6871d631074ce35134fb98ef9710`): 539 cases pass and 11 cases exceed the frozen per-view max/min ratio limit of 80.0. The failed IDs are `case_00081`, `case_00159`, `case_00320`, `case_00340`, `case_00359`, `case_00411`, `case_00475`, `negative_00001`, `negative_00023`, `negative_00040`, and `negative_00048`; their failed ratios range from `80.296159183210534` to `172.89813690218202`. Every other generator check for these cases passes, including artifact hashes, projection metadata binding, shape, finite/nonnegative values, support, centroid guard band, and role semantics.
- The failed projections are immutable downloaded SIMIND outputs, local SIMIND execution is forbidden, and the accepted threshold is frozen at 80.0. Therefore the production campaign is fully materialized but **not automatically release-accepted**. No case was removed, rewritten, or waived, and the threshold was not relaxed to manufacture a pass. Resolving the release blocker requires an explicitly authorized upstream simulation/acceptance decision outside this local finalization.
- Final post-production focused regressions passed: core writer/freeze/Formal550 matrix `121 passed`; automatic acceptance matrix `72 passed` with 13 existing Matplotlib/Pyparsing deprecation warnings; notebook matrix `12 passed` with one existing Windows ZMQ warning; PAR-S_2 loader matrix `17 passed`. The three new Python entry points compiled successfully, and every regression stderr log was empty.
- The deterministic notebook contains 15 cells, 7 code cells, no stored outputs, and no removable `assert` integrity checks. Its SHA-256 is `7568633982392b366e50bc9a14848423ff2d6db199ef403f90c35271dc4eefcc`.
- The earlier smoke root remains non-authoritative and deliberately paused. The production roots and QA evidence above are now the authoritative observed local result.

The commands below are retained as recovery references. Do not start a fresh writer
against the existing production roots; use `--resume` only after confirming that no
writer is already running:

```powershell
conda run -n SPECT python scripts\finalize_task13_formal550_local.py --resume
```

The QA root already exists, so any deliberate acceptance recheck must use
`--resume`:

```powershell
conda run -n SPECT python scripts\finalize_task13_formal550_acceptance.py --resume
```

Regenerate the read-only review notebook from the authoritative acceptance evidence
with:

```powershell
conda run -n SPECT python scripts\build_task13_formal550_acceptance_notebook.py
```
