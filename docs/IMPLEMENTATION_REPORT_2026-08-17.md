# PAR-S Generator implementation and verification report — 2026-08-17

> [!IMPORTANT]
> Historical implementation report. It predates the Hybrid V2 + LimitedActivity Windows v1 integration; current authority is `WINDOWS_V1_SCIENTIFIC_AUTHORITY.md`.

## Outcome

PAR-S Generator now has one auditable, run-isolated data-preparation workflow. Its endpoint is a finalized synthetic dataset package. It contains no reconstruction, training, inference, checkpoint or model-evaluation workflow, and `D:\PFE-U\PAR-S_2` was kept read-only.

## Audit inputs and protected starting state

Before implementation, the working tree, existing documentation and tests were inspected. The already modified/user-owned paths included `.claude/settings.local.json`, core export and SIMIND/UI work (`src/core/interfile_writer.py`, `src/core/simind_runner.py`, `src/core/smc_parser.py`, `src/ui/app_state.py`, `src/ui/i18n.py`, `src/ui/pages/simulation_page.py`, `src/ui/settings_store.py` and related widgets/tests). They were preserved and integrated; none was reset, checked out or overwritten from Git.

The audit read the actual 500-case phantom/projection files, `docs/simind_manual.pdf` (SIMIND v8.0), `docs/DOC2109131-NMCT-870-CZT-PDS.pdf`, the project documentation and `C:\Users\86187\Downloads\PAR-S_synthetic_data_audit_2026-08-17.md`. Claims from the latter were reclassified where its evidence was indirect: the fitted attenuation factor required a controlled test rather than automatic adoption, single-image spatial variance is not a repeated-sampling Fano estimate, and a non-integer non-negative target does not by itself invalidate a generalized data-divergence objective. The reopened controlled attenuation result is reported below and passed its scoped type−7 readback and analytical-transmission thresholds.

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
| Generator correctness | Fixed ellipsoid coordinates; presampled lesion-size strata retained across layout retries; mask-derived diameter; adaptive Cantlie solver; explicit central/subcapsular placement and capacity fallback; full containment and non-overlap | Geometry regression tests and accepted 100-case population QC | Complete for the scoped generated-population contract |
| Attenuation contract | Explicit unit/reference/status plus tested type−7 `μ×Δx` representation; no fitted ×1.8 or duplicate `/0.442` conversion | v10 mode-3 readback 0.149978 cm⁻¹; same-run primary/air 0.266591 vs 0.265537 analytical; ten-case promoted pilot | **Stage-2 control and Stage-3 promotion passed** |
| Expectation/noise separation | Clean activity source; SIMIND expectation separated from optional seeded Poisson observation | Reproducibility, non-overwrite tests and eight-series empirical count summary | Complete for empirical matching; no absolute cps/MBq claim |
| Fixed split and manifest | Exact sorted-ID + `default_rng(42)` partition persisted at phantom level; observations inherit split | 500-case 400/50/50 test and frozen manifest | Complete |
| Strong SIMIND completion | Exact `.a00` size/finite/nonnegative checks, `.res` stop marker and command tokens, `.mhd` dimensions/type | Legacy sample and corrupt/truncated test | Complete |
| QC and figures | Case JSON QC, population and pilot summaries, CSV data, editable SVG and PNG | Generated for legacy freeze, 100-case population and finalized ten-case pilot | Complete |
| Data-preparation UI | Six sequential areas: Project/Protocol, Phantom, Simulation, Run, QC/Dataset, Finalize | Native Windows screenshots and UI smoke test | Complete |
| Blocking physics studies | Five isolated packages plus diagnostic revisions, executable only after explicit authorization; every job records command, logs, exit code, artifacts and hashes | Orientation, attenuation, FOV, point/line and RR/NN passed in scope; empirical observation policy selected; corrected joint pilot passed | **Stage 3 permits full corrected data production under the unchanged contract** |

## Canonical product workflow

1. **Project / Protocol** creates the run identity and exposes the locally supported nominal 60 MBq × 28.4 s contract (SMC Index-25=1704), the passed scoped controls and the empirical/non-absolute count policy.
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
- `experiments/validation-v1/asymmetric_fiducial/`: passing orientation run and preserved output-contract/low-history diagnostics.
- `experiments/validation-v10/attenuation_ict/`: current passed type−7 μΔx readback and same-run Scattwin analytical-transmission gate; v1–v9 are retained as diagnostics.
- `experiments/validation-v1/fov_matrix/`: five completed detector-matrix controls and the selected 160×208 candidate.
- `experiments/validation-v2/point_line_source/`: completed `/NN:1000` point/line response controls.
- `experiments/validation-v1/rr_nn_ladder/`: fifteen completed repeated-stream/effort controls.
- `docs/evidence/ui_project_protocol.png`, `docs/evidence/ui_simulation_contract.png` and `docs/evidence/ui_qc_dataset.png`: native UI evidence for protocol gating, semantic SMC values/expert separation and dataset QC.
- `docs/evidence/stage2_validation_summary_2026-08-18.json`: compact machine-readable Stage 2 verdict index linked to each detailed experiment ledger.
- `docs/evidence/clinical_empirical_count_summary_2026-08-18.json`: anonymous eight-series raw-count and angular-profile distribution for empirical observation matching.
- `runs/stage3-phantom-100-v3-20260818/`: accepted 100-case phantom-population QC run; 100/100 cases and all predeclared distribution gates passed.
- `runs/stage3-simind-pilot-10-v3-20260818/`: finalized ten-case corrected SIMIND/empirical-observation pilot; manifest SHA-256 `0ae80dc6bcea9d0f6780e9cee7d87472ebcff6a9a4f66318a10b81c6d4f63d61`.
- `docs/evidence/stage3_pilot_summary_2026-08-18.json`: independently rehashed Stage-3 verdict and per-case metrics.
- `docs/STAGE3_PROTOCOL_PROMOTION_2026-08-18.md`: bounded Stage-3 protocol, population and pilot report.
- `docs/METHODS_SYNTHETIC_DATA.md`: bounded manuscript Methods draft.
- `docs/DECISION_GATES.md`: remaining scientific decisions and pass criteria.

## Verification

The exact README/Goal smoke command passed 28 tests. At the reopened Stage 2 closeout, the full suite passed 81 tests; after Stage-3 generator, pilot-selection, case-ID mapping and projection-angular-metric regressions were added, the full suite passed 86 tests. Both runs had no failures or skips. Thirteen warnings came from upstream Matplotlib/Pyparsing deprecations. The tested scope includes anatomy, validation, state transitions, UI navigation and expert-mode separation, generator regressions, binary and `.res` contracts, split determinism, legacy-freeze integrity, mock end-to-end packaging, pause/resume, corrupt resume and immutable-manifest rejection, non-canonical selected-case ordering, CLI compatibility, safe SIMIND output staging, activity–time consistency, experiment preparation/execution summaries and SMC parsing.

Commands executed with the SPECT environment were:

```powershell
$env:PYTHONPATH='src'
$env:QT_QPA_PLATFORM='offscreen'
C:\Users\86187\anaconda3\envs\SPECT\python.exe -m pytest tests\test_phantom_anatomy.py tests\test_validation.py tests\test_workflow_state.py tests\test_ui_smoke.py -q
# 28 passed, 13 upstream deprecation warnings

C:\Users\86187\anaconda3\envs\SPECT\python.exe -m pytest -q
# Stage-2 closeout: 81 passed; Stage-3 closeout: 86 passed
# 13 upstream deprecation warnings; no failures or skips
```

The finalized smoke manifest was reopened and revalidated read-only (`qa-smoke-20260817`, manifest SHA-256 `8d510a93c325d5c87e2a5d1445e5df460db60925de84c1e9835116d1ed03a103`). The legacy inventory was independently rehashed after implementation: 3,000 checked, zero missing, zero mismatches. All Stage 2 scientific packages now have machine-readable analyses and execution ledgers; failed command-contract probes and interrupted partial artifacts are isolated under `diagnostics/` and excluded from scientific verdicts.

## Remaining work

Stage 3 promoted the tested type−7 `μΔx` export, runtime entry-21 threshold 100, current `h2o` cross-section contract, 160/208 detector FOV and empirical observation policy into the single production path. The 100-case population and ten-case corrected pilot passed. The next data-preparation step is a full corrected production run under the exact accepted contract, followed by the same automatic QC and immutable-manifest review. Any protocol, material-table, organ, scanner or observation-policy change must reopen the relevant decision gates. The existing smoke dataset remains non-physical/toy, and no reconstruction/model work is part of this repository.

## Post-baseline validation addendum — asymmetric orientation

After baseline commit `b1e1a06`, the first authorized SIMIND V8 experiment exposed and corrected an output-argument defect. Absolute paths containing `PFE-U` were silently truncated at the hyphen as a `/U` switch, while paths with directory components fell back to the SMC stem. The common executor now sends a safe basename, rejects switch-like names, checks staging and destination collisions, and relocates successful artifacts before QC. Diagnostic outputs were preserved under `experiments/validation-v1/asymmetric_fiducial/diagnostics/`.

The original `/NN:1` orientation run produced only 11 detector hits and failed the new statistics gate. A controlled `/NN:1000` run produced 11,000 photons per view, 10,807 detector hits, non-zero data in all 60 views and at least 55 positive pixels per view. Enumeration of all 16 view/row/column-flip and X/Y-exchange candidates selected `raw[:,::-1,:]` uniquely (best score 1.019 pixels, second 27.429 pixels, ratio 26.93; detector-row residual 0.010 pixels). New-data QC, viewer and manifest contracts now retain view order and flip only detector row. The read-only PAR-S_2 repository and frozen 500-case legacy transform `raw[::-1,::-1,:]` were not modified. Full evidence is recorded in `docs/VALIDATION_RESULTS_2026-08-17.md` and the experiment `results.json`.

## Local protocol-evidence addendum

A de-identified, read-only scan of 1,534 DICOM headers under `D:\PFE-U\CLIN` found ten unique original 60-view GE 870 CZT liver SPECT acquisitions. Frame duration had median 28.354 s and range 27.809–28.439 s; all used 128×128 at 4.4196 mm, WEHR45, 6° clockwise increments and 129.96–151.04 keV. This supports the nominal 60 MBq × 28.4 s definition of Index-25=1704 and identifies the historical `20s` directory label as non-authoritative. The UI/config now defaults to this coherent triple and the generated SIMIND command explicitly carries `/25:1704`.

The GE product sheet and SIMIND manual also show that Index-100/101 are native CZT pixel counts, not output-matrix dimensions. The FOV package was therefore replaced by legacy/single-axis/native/swapped controls centred on the 160×208 detector implied by 393.6×511.7 mm at 2.46 mm; the superseded prepared-only square ladder was preserved under `experiments/validation-v1/prepared_archive/`. Details are in `docs/LOCAL_PROTOCOL_EVIDENCE_2026-08-17.md`.

## Stage 2 execution addendum — 2026-08-18

The experiment executor now reconstructs every job from its recorded command, runs jobs sequentially, captures stdout/stderr and wall time, collision-checks outputs, performs structural QC and writes file size/SHA-256 inventories to `execution.json`. Resume reuses only verified artifacts. SIMIND is invoked with a safe local basename; `/RR` is terminal; `/PX` is explicit for type −1 sources; and runtime/numeric switches are combined in one slash token when required by the tested Windows V8 argument parser. These rules are covered by targeted regression tests.

Stage 2 produced the following bounded verdicts:

- **orientation passed:** new data use `raw[:,::-1,:]`; frozen PAR-S_2 keeps `raw[::-1,::-1,:]`;
- **activity–time supported:** 60 MBq × 28.4 s per projection, Index-25=1704, based on ten local de-identified GE acquisitions and the SIMIND definition;
- **FOV passed:** Index-100/101=160/208 gives 39.36×51.168 cm while output remains 128×128 at 4.42 mm;
- **point/line passed within scope:** 17.68-mm point/line FWHM at the zero-attenuation 300-mm test geometry versus 17.50 mm predicted, with 0.753-pixel centring error;
- **sampling passed:** repeated `/RR` variance decreased with `/NN`, classifying `.a00` as a weighted Monte Carlo expectation estimator rather than a clinical Poisson observation;
- **attenuation passed in scope after reopening:** type−7 stores μΔx; the v10 mode-3 readback was 0.149978 cm⁻¹ and primary/air was 0.266591 versus 0.265537 expected for μ=0.15 cm⁻¹ over 8.84 cm;
- **empirical observation policy selected:** eight de-identified raw TOMO series define totals of 2.042–4.113 million and angular CV 0.3336–0.6202, without an absolute cps/MBq claim.

That Stage-2 decision was **eligible to enter Stage 3 protocol promotion, but not yet authorized for formal production**. The subsequent Stage-3 result below supersedes the pending-pilot portion of that decision. `D:\PFE-U\PAR-S_2` remained read-only and no reconstruction or model workflow was run.

## Stage 3 protocol-promotion addendum — 2026-08-18

The accepted parent run `stage3-phantom-100-v3-20260818` generated 100 deterministic cases with 312 lesions. All case QC checks and predeclared population gates passed: sampled and effective size-bin counts were identical at 138/127/47 for 10–20/20–40/40–60 mm; zero lesion voxels lay outside the liver or overlapped another lesion; left-lobe fraction was 0.34515–0.35447; and eight explicitly labelled capacity-fallback lesions comprised 2.56% of lesions, below the 5% cap.

A standardized-feature maximin procedure selected ten parent phantoms for the corrected pilot. The accepted run `stage3-simind-pilot-10-v3-20260818` executed `/NN:10` with deterministic per-case `/RR` seeds, type−7 `μΔx`, entry-21 threshold 100, the current two `h2o` tables, Index-100/101=160/208 and Index-25=1704. Ten of ten SIMIND expectations and ten of ten empirical Poisson observations passed. The observation totals were 2.127–3.850 million, maximum target-total relative error was 0.000873, and angular CV was 0.39135–0.57296 inside the predeclared 0.33360–0.62017 range.

The finalized pilot manifest SHA-256 is `0ae80dc6bcea9d0f6780e9cee7d87472ebcff6a9a4f66318a10b81c6d4f63d61`; an independent check reproduced the digest and found no size or hash mismatch among its 147 inventoried files. A session interruption was handled through strong resume: six completed cases were reused after hash/QC checks, three truncated staging files were retained under `diagnostics/` but excluded from the expectation layer, and four cases were resubmitted by case ID. Earlier v1/v2 pilot diagnostics are excluded because they lacked explicit `/RR` or exposed a position-based case/job mismatch, respectively.

The Stage-3 decision is **passed for a full corrected synthetic-data production run under the unchanged liver/current-GE-870 protocol and empirical count-matching policy**. This is a data-preparation decision only. It does not include PAR-S reconstruction, training or evaluation and does not claim absolute cps/MBq.
