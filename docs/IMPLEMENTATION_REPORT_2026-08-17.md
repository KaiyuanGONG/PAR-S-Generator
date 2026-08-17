# PAR-S Generator implementation and verification report — 2026-08-17

## Outcome

PAR-S Generator now has one auditable, run-isolated data-preparation workflow. Its endpoint is a finalized synthetic dataset package. It contains no reconstruction, training, inference, checkpoint or model-evaluation workflow, and `D:\PFE-U\PAR-S_2` was kept read-only.

## Audit inputs and protected starting state

Before implementation, the working tree, existing documentation and tests were inspected. The already modified/user-owned paths included `.claude/settings.local.json`, core export and SIMIND/UI work (`src/core/interfile_writer.py`, `src/core/simind_runner.py`, `src/core/smc_parser.py`, `src/ui/app_state.py`, `src/ui/i18n.py`, `src/ui/pages/simulation_page.py`, `src/ui/settings_store.py` and related widgets/tests). They were preserved and integrated; none was reset, checked out or overwritten from Git.

The audit read the actual 500-case phantom/projection files, `docs/simind_manual.pdf` (SIMIND v8.0), `docs/DOC2109131-NMCT-870-CZT-PDS.pdf`, the project documentation and `C:\Users\86187\Downloads\PAR-S_synthetic_data_audit_2026-08-17.md`. Claims from the latter were reclassified where its evidence was indirect: the fitted attenuation factor is an anomaly pending a control, single-image spatial variance is not a repeated-sampling Fano estimate, and a non-integer non-negative target does not by itself invalidate a generalized data-divergence objective.

`D:\PFE-U\PAR-S_2\src\data\preprocessing.py` and `dataset.py` were inspected read-only to recover the canonical `raw[::-1,::-1,:]` transform and sorted-ID/`default_rng(42)` split. No model was run. At final audit, all 3,000 paths in the legacy checksum inventory were rehashed: 3,000 present, zero missing and zero mismatches. Existing `output/` data, SIMIND binaries and the Downloads report remained read-only.

## Implementation file map

- Generator and contracts: `src/core/phantom_generator.py`, `validation.py`, `interfile_writer.py`, `simind_runner.py`, `smc_parser.py`.
- Canonical workflow: `src/pipeline/contracts.py`, `runner.py`, `simind.py`, `qc.py`, `observation.py`, `legacy.py`, `experiments.py`, `figures.py` and `src/cli.py`.
- Product surface: `src/ui/main_window.py`, `app_state.py`, `pages/phantom_page.py`, `pages/pipeline_pages.py`, the SIMIND/SMC widgets, translations and light/dark styles. `run_batch.ps1` is now a config-only CLI wrapper.
- Verification: generator, pipeline, legacy freeze, experiment, SMC and UI tests under `tests/` plus the isolated smoke, manifests, figures and screenshots listed below.
- Documentation: `README.md`, this report, `METHODS_SYNTHETIC_DATA.md` and `DECISION_GATES.md`.

## Requirement-to-evidence map

| Requirement | Implementation | Verification/evidence | Status |
|---|---|---|---|
| One generation workflow | `src/pipeline/runner.py`; GUI, CLI and the compatibility `run_batch.ps1` call the same runner and SIMIND command builder; the historical page name aliases the canonical page | Two-case end-to-end mock smoke; exact token equality and compatibility-entry tests | Complete |
| Isolated and resumable runs | Dedicated `runs/<run_id>/` tree, atomic ledger, per-case hashes, safe pause boundaries, immutable final manifest | Corrupt-artifact resume rejection and pause/resume tests | Complete |
| Generator correctness | Fixed ellipsoid coordinates; mask-derived diameter; adaptive Cantlie solver; explicit central/subcapsular placement; full containment and non-overlap | Geometry regression tests and phantom QC | Complete in software; population claims require a production run |
| Attenuation contract | Explicit unit/reference/status in config, metadata, exports and manifests; no automatic ×1.8 scaling | Exact binary readback; Flag-15 package prepared | Software complete; physical gate pending |
| Expectation/noise separation | Clean activity source; SIMIND expectation separated from optional seeded Poisson observation | Reproducibility, integer-valued realization and non-overwrite tests | Complete; clinical scale pending |
| Fixed split and manifest | Exact sorted-ID + `default_rng(42)` partition persisted at phantom level; observations inherit split | 500-case 400/50/50 test and frozen manifest | Complete |
| Strong SIMIND completion | Exact `.a00` size/finite/nonnegative checks, `.res` stop marker and command tokens, `.mhd` dimensions/type | Legacy sample and corrupt/truncated test | Complete |
| QC and figures | Case JSON QC, summary evidence, CSV data, editable SVG and PNG | Generated for legacy freeze and smoke run | Complete |
| Data-preparation UI | Six sequential areas: Project/Protocol, Phantom, Simulation, Run, QC/Dataset, Finalize | Native Windows screenshots and UI smoke test | Complete |
| Blocking physics studies | Five self-contained prepared packages with analyzers, including a μ=0/0.15 water-column pair, point/line sensitivity and FWHM/FWTM, matrix FOV, and RR/NN confidence intervals; never auto-executed | Experiment-preparation tests and empty output directories | Prepared; execution intentionally pending |

## Canonical product workflow

1. **Project / Protocol** creates the run identity and exposes unresolved protocol fields, including 60 MBq × 20 s versus SMC Index-25=1704.
2. **Phantom** previews the same effective count and contrast values used by the batch configuration.
3. **Simulation** records executable/SMC provenance, expectation backend, observation policy and experiment preparation.
4. **Run** is the only execution entry point and supports create, resume and pause. Actual SIMIND launch requires confirmation.
5. **QC / Dataset** shows stage evidence, case records and projection data using the canonical orientation.
6. **Finalize** blocks incomplete or prepared-only datasets and records the immutable package manifest.

The UI adopts a compact scientific acquisition-console layout with an explicit 01–06 stage rail. This visual structure was chosen so protocol state, execution state and evidence are not mistaken for unrelated application modules.

## Legacy 500-case freeze

`manifests/legacy-v1-weighted-mc/` is a read-only reference to the existing `syn3d_noNoise + SPECT_60Mbq20s` data. It contains exactly 500 cases, 400/50/50 fixed split identifiers and checksums for 3,000 source artifacts. No legacy source file was copied over or rewritten.

Key measured findings are:

- ellipsoid equivalent diameter divided by nominal diameter: 1.488 ± 0.413 (1,026 lesions), maximum 2.836;
- lesion contact with the liver surface: 1,152/1,459 (79.0%);
- 59 lesions in 55 cases overlap another saved lesion mask;
- achieved left-lobe fraction: 0.316 ± 0.038, maximum 0.3525;
- mean fraction of non-integer positive projection samples: 0.9999997;
- 465/500 cases share the dominant 72-row by 72-column non-zero support, while 35 cases have minor support variants;
- all 500 legacy projection artifact sets pass structural size/header/result-marker QC.

These measurements support the legacy classification `legacy_weighted_mc_expectation_like_output`. They do not prove a sub-Poisson sampling law, a correct attenuation unit mapping or a clinically correct count scale.

## Generated artifacts

- `manifests/legacy-v1-weighted-mc/`: frozen legacy cases, splits, checksums, QC and figures.
- `runs/qa-smoke-20260817/`: finalized two-case deterministic mock pipeline evidence. Its projection backend and Poisson observation are explicitly marked non-physical/toy.
- `experiments/validation-v1/`: five prepared physics-validation packages; SIMIND outputs remain absent until deliberately executed.
- `docs/evidence/ui_project_protocol.png`, `docs/evidence/ui_simulation_contract.png` and `docs/evidence/ui_qc_dataset.png`: native UI evidence for protocol gating, semantic SMC values/expert separation and dataset QC.
- `docs/METHODS_SYNTHETIC_DATA.md`: bounded manuscript Methods draft.
- `docs/DECISION_GATES.md`: remaining scientific decisions and pass criteria.

## Verification

The exact README/Goal smoke command passed 28 tests. The final full suite passed 67 tests with no failures or skips; 13 warnings came from upstream Matplotlib/Pyparsing deprecations. The tested scope includes anatomy, validation, state transitions, UI navigation and expert-mode separation, generator regressions, binary and `.res` contracts, split determinism, legacy-freeze integrity, mock end-to-end packaging, pause/resume, corrupt resume and immutable-manifest rejection, CLI compatibility, experiment preparation and SMC parsing.

Commands executed with the SPECT environment were:

```powershell
$env:PYTHONPATH='src'
$env:QT_QPA_PLATFORM='offscreen'
C:\Users\86187\anaconda3\envs\SPECT\python.exe -m pytest tests\test_phantom_anatomy.py tests\test_validation.py tests\test_workflow_state.py tests\test_ui_smoke.py -q
# 28 passed, 13 upstream deprecation warnings

C:\Users\86187\anaconda3\envs\SPECT\python.exe -m pytest -q
# 67 passed, 13 upstream deprecation warnings; no failures or skips
```

The finalized smoke manifest was reopened and revalidated read-only (`qa-smoke-20260817`, manifest SHA-256 `8d510a93c325d5c87e2a5d1445e5df460db60925de84c1e9835116d1ed03a103`). The legacy inventory was independently rehashed after implementation: 3,000 checked, zero missing, zero mismatches. The rebuilt validation package contains 57 prepared files and zero `.a00`, `.res`, `.mhd`, `.ict` or `.hct` outputs.

## Remaining external work

Software implementation does not determine the physical answers to the five prepared SIMIND experiments, the activity–time protocol choice or clinical count scaling. Those require SIMIND execution and, for scanner claims, comparison with the correctly matched GE protocol or measurement. The complete gate list is in `docs/DECISION_GATES.md`. Full production generation should begin only after the parameters affected by those gates are fixed; the smoke dataset must never be presented as a scientific result.
