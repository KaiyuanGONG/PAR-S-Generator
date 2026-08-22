# PAR-S Generator

PAR-S Generator prepares reproducible synthetic liver SPECT datasets for the current GE NM/CT 870 CZT research protocol. Its endpoint is a QC-checked, checksum-inventoried dataset package. It does **not** reconstruct images or run, train, manage or evaluate a model.

## Windows v1.0.0 canonical workflow

There is one production path shared by the desktop UI and CLI:

```text
Web / FastAPI / CLI → Hybrid V2 anatomy → corrected-master lesions
  → LimitedActivity v1 → physical μ-map → ACT / ATN → Phantom QC
  → native Windows SIMIND → Projection QC → Package / Finalize
```

The only profile allowed to create or resume production is
`schema_version=windows_v1`,
`generation_profile=hybrid_v2_limited_activity_v1`,
`runtime_backend=windows_native`. Legacy/master, Task12 full V2, Gate B Linux
and the old PyQt workflow remain inspectable historical evidence, never
alternative production modes. The scientific provenance and boundaries are
defined in [Windows v1 scientific authority](docs/WINDOWS_V1_SCIENTIFIC_AUTHORITY.md).

Every invocation uses `runs/<run_id>/`; files from different runs are never globbed into one batch. `run.json` stores the strict effective configuration and stage evidence, `cases.jsonl` stores roles and case-level provenance/QC, `splits.json` fixes the phantom-level partition, and `dataset_manifest.json` inventories packaged files by relative path, byte size and SHA-256 checksum. Resume rechecks the configuration fingerprint, input/runtime hashes and stage evidence. A finalized manifest is immutable.

Current scientific limitations are visible states, not hidden defaults. Array/orientation, the scoped type−7 attenuation contract, native 160×208 detector FOV, the scoped 300-mm point/line response control and repeated `/RR`–`/NN` sampling controls passed. Local evidence supports the nominal 60 MBq × 28.4 s activity–time contract (SIMIND Index-25 = 1704) and defines an empirical observation distribution from eight de-identified raw TOMO series. Stage 3 promoted these contracts, passed a 100-case phantom-population QC run and finalized a ten-case corrected SIMIND pilot. This permits full corrected synthetic-data production under the unchanged protocol; it is not an absolute cps/MBq or model-performance claim. See [decision gates](docs/DECISION_GATES.md).

## Desktop application

Requirements are Windows 10/11, 64-bit Python 3.11, Node.js 22.19+ for the one-time build, and a licensed user-provided SIMIND executable.

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup_windows.ps1
.\start_windows.ps1
```

`python main.py` starts the same loopback-only FastAPI/Web application and opens the browser. It handles a busy preferred port, prevents a second local instance and cleans up on exit. No EXE/installer is distributed. The historical PyQt application is available only through `python legacy_pyqt.py`.

The interface has six sequential data-preparation areas:

1. **Project / Protocol** — run identity, protocol and unresolved decisions.
2. **Phantom** — one-case visual preview using the same effective values as the run.
3. **Simulation** — native Windows SIMIND/SMC provenance, expectation output and experiment preparation.
4. **Run** — the only create/resume/pause/execute entry point.
5. **QC / Dataset** — stage evidence, case records and canonical projection view.
6. **Finalize** — completeness checks and immutable manifest.

Native pickers select SIMIND `.exe`, SMC `.smc`, the runs root and experiment export root. Only local drives are accepted; session authorization is not persisted. The validated transform for newly generated data is `raw[:, ::-1, :]`: acquisition view order is retained and the detector row is flipped. ACT/ATN are C-order ZYX little-endian `<f4`; ATN is `mu_map × 0.442`. Actual SIMIND launch always requires explicit confirmation. An unknown executable or SMC hash requires a separate confirmation and is permanently labeled `unverified_runtime` in that run.

## Local Web workbench

The React/FastAPI workstation implements the same six-step contract as a
Plan → Run → Review → Seal lifecycle. It is local-only and does not add a
second pipeline: FastAPI delegates generation, QC, pause/resume, experiment
preparation and Finalize to the existing Python implementation.

For development, run the service and Vite in separate PowerShell terminals after `npm ci`:

```powershell
$env:PYTHONPATH = 'src'
python -m uvicorn webui.server.app:app --host 127.0.0.1 --port 8765
Set-Location webui/frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

The workbench provides:

- a versioned strict Windows v1 draft; old drafts are read-only and are never
  silently migrated into production;
- synchronized axial/coronal/sagittal inspection plus interactive 3D or MIP,
  using one linked voxel cursor, measured values and real masks;
- allowlisted file/directory browsing, SMC parsing and preparation of all five
  validation experiments without launching SIMIND;
- Run monitoring with explicit execute consent and pause/resume, always ending
  in Review rather than automatic sealing;
- real ledger, `.res`, projection/sinogram, manifest and split inspection; and
- an independent irreversible Seal screen which calls the runner Finalize gate
  and displays the package SHA-256.

English, Simplified Chinese and French, equal light/dark themes, keyboard use
and 1280×720 workstation layouts are covered by automated browser checks. The
functional traceability matrix is [documented here](docs/WEB_UI_FUNCTIONAL_MATRIX.md),
and the local API contract is [documented here](docs/WEB_API_CONTRACT_DRAFT.md).

## Command line

Use the repository's `src` directory on `PYTHONPATH`. Positive, true-negative and mixed queues have explicit counts:

```powershell
$env:PYTHONPATH = 'src'
python -m cli init --run-id pilot-001 --cohort-mode mixed --positive-cases 1 --negative-cases 1 --mode prepare --output pilot-001.json
python -m cli run --config pilot-001.json
python -m cli inspect --run runs/pilot-001
```

Modes are:

- `prepare`: generate/QC/export and write exact SIMIND jobs, but do not execute or finalize a dataset;
- `mock`: software smoke testing only; projection physics are explicitly fake;
- `execute`: run SIMIND, requiring `--allow-simind-execution`.

Resume the same effective configuration with:

```powershell
python -m cli run --config pilot-001.json --resume
```

For `execute`, add `--allow-simind-execution`. More than ten real cases need
`--allow-large-simind-execution`; an unknown runtime hash independently needs
`--allow-unverified-runtime`. `run_batch.ps1` remains only a compatibility
wrapper around the same CLI and requires an explicit Windows v1 config:

```powershell
.\run_batch.ps1 -Config pilot-001.json -Resume
```

It owns no paths, case range, `/NN`, completion or resume defaults. Exported audit BAT files and the old notebook are not production entry points; `PipelineRunner` is authoritative.

## Run layout

```text
runs/<run_id>/
├── run.json
├── cases.jsonl
├── splits.json
├── dataset_manifest.json
├── phantom/
├── simind_input/
├── expectation/
├── observation/
├── qc/
├── logs/
└── figures/
```

Activity and attenuation are exported atomically as C-order ZYX little-endian `<f4`, immediately read back, size-checked and checksummed. Windows v1 finalizes the SIMIND expectation after projection QC and does not create a seeded offline Poisson observation. The historical observation implementation remains available only for read-only evidence from earlier profiles.

## Physics-validation packages

The following controls can be prepared without launching SIMIND:

```powershell
python -m cli prepare-experiment --name all --destination experiments/validation-v1
```

The packages cover:

- Flag-15 `.ict` attenuation readback;
- asymmetric-fiducial axis/orientation validation;
- legacy 128×128 versus GE-native 160×208 detector aperture and axis controls;
- point/line response and sensitivity;
- repeated `/RR` and `/NN` Monte Carlo behavior.

Each folder contains deterministic inputs, copied SMC variants, command JSON/BAT, a result template and an analyzer. Preparation never executes SIMIND. After an authorized run, analyze one package with:

```powershell
python -m cli analyze-experiment --experiment experiments/validation-v10/attenuation_ict
```

SIMIND V8 is invoked with a validated safe basename in its working directory. After a zero exit code, the shared executor collision-checks and relocates the generated artifacts to the run-isolated output directory before QC. This avoids silent parsing of hyphens in absolute paths as SIMIND switches.

## Existing evidence

`WINDOWS_V1_SCIENTIFIC_AUTHORITY.md`, `WINDOWS_V1_ACCEPTANCE.md` and this README define the active software contract. Earlier tutorials, Gate documents, configuration notes and audits are retained as historical evidence; their commands and profile claims are not production instructions.

- [Implementation report](docs/IMPLEMENTATION_REPORT_2026-08-17.md)
- [Methods draft](docs/METHODS_SYNTHETIC_DATA.md)
- [Scientific decision gates](docs/DECISION_GATES.md)
- [Validation results](docs/VALIDATION_RESULTS_2026-08-17.md)
- [Local protocol evidence](docs/LOCAL_PROTOCOL_EVIDENCE_2026-08-17.md)
- [Stage 3 protocol promotion and pilot](docs/STAGE3_PROTOCOL_PROMOTION_2026-08-18.md)
- [Windows v1 scientific authority](docs/WINDOWS_V1_SCIENTIFIC_AUTHORITY.md)
- [Windows v1 complete acceptance procedure](docs/WINDOWS_V1_ACCEPTANCE.md)
- [Repository governance](docs/REPOSITORY_GOVERNANCE.md)
- `manifests/legacy-v1-weighted-mc/` — read-only checksum freeze of the 500 historical cases.
- `runs/qa-smoke-20260817/` — finalized two-case deterministic software smoke, explicitly not scientific data.
- `runs/stage3-phantom-100-v3-20260818/` — accepted 100-case generated-population QC evidence.
- `runs/stage3-simind-pilot-10-v3-20260818/` — finalized corrected ten-case SIMIND/observation pilot.
- `docs/evidence/stage3_pilot_summary_2026-08-18.json` — compact machine-readable Stage-3 verdict and per-case metrics.
- `docs/evidence/` — native Windows UI screenshots.

## Verification

```powershell
.\scripts\verify_windows_v1.ps1 -SkipRealSimind
```

The script checks the exact local runtime hashes, Python suite, frontend lint/unit/build/E2E/a11y/visual, loopback launcher, prepare and mock state machines. Without `-SkipRealSimind`, it asks for the exact phrase `RUN SIMIND` before the required one-positive/one-true-negative NN=10, worker=1 native acceptance. See the [complete manual procedure](docs/WINDOWS_V1_ACCEPTANCE.md) for path-picker and corruption/resume cases.

Web checks run separately from `webui/frontend`:

```powershell
npm run lint
npm run test:unit
npm run build
npm run test:e2e
npm run test:a11y
npm run test:visual
```

The browser lifecycle suite uses deterministic mock/fixture data and never
launches real SIMIND. Visual baselines cover all six workspaces in three
languages and two themes at 1440×900, plus Chinese/French light/dark coverage at
1280×720.

## Scope boundary

The software and documentation are limited to synthetic liver SPECT data preparation under the current protocol. Nothing here establishes general validity for every cancer, organ, scanner, collimator or acquisition protocol. Scanner-specific physical claims remain conditional on controlled experiments and correctly matched measurements.
