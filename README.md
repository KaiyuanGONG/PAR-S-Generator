# PAR-S Generator

PAR-S Generator prepares reproducible synthetic liver SPECT datasets for the current GE NM/CT 870 CZT research protocol. Its endpoint is a QC-checked, checksum-inventoried dataset package. It does **not** reconstruct images or run, train, manage or evaluate a model.

## Canonical workflow

There is one production path shared by the desktop UI and CLI:

```text
Generate → Phantom QC → float32 export → SIMIND plan/expectation
         → Projection QC → optional observation → Package/Finalize
```

Every invocation uses `runs/<run_id>/`; files from different runs are never globbed into one batch. `run.json` stores the effective configuration and stage evidence, `cases.jsonl` stores case-level provenance/QC, `splits.json` fixes the phantom-level partition, and `dataset_manifest.json` inventories packaged files by relative path, byte size and SHA-256 checksum. Resume accepts an artifact only when its hash and strong stage checks pass. A finalized manifest is immutable.

Current scientific limitations are visible states, not hidden defaults. Array/orientation, the scoped type−7 attenuation contract, native 160×208 detector FOV, the scoped 300-mm point/line response control and repeated `/RR`–`/NN` sampling controls passed. Local evidence supports the nominal 60 MBq × 28.4 s activity–time contract (SIMIND Index-25 = 1704) and defines an empirical observation distribution from eight de-identified raw TOMO series. Stage 3 promoted these contracts, passed a 100-case phantom-population QC run and finalized a ten-case corrected SIMIND pilot. This permits full corrected synthetic-data production under the unchanged protocol; it is not an absolute cps/MBq or model-performance claim. See [decision gates](docs/DECISION_GATES.md).

## Desktop application

Requirements are Windows 10/11, Python 3.10+ and a user-provided SIMIND executable.

```powershell
pip install -r requirements.txt
python main.py
```

The interface has six sequential data-preparation areas:

1. **Project / Protocol** — run identity, protocol and unresolved decisions.
2. **Phantom** — one-case visual preview using the same effective values as the run.
3. **Simulation** — SIMIND/SMC provenance, expectation/observation policy and experiment preparation.
4. **Run** — the only create/resume/pause/execute entry point.
5. **QC / Dataset** — stage evidence, case records and canonical projection view.
6. **Finalize** — completeness checks and immutable manifest.

The validated transform for newly generated data is `raw[:, ::-1, :]`: acquisition view order is retained and the detector row is flipped. The frozen historical PAR-S_2 set keeps its separate legacy contract `raw[::-1, ::-1, :]`; PAR-S_2 itself is not modified. Actual SIMIND launch always requires explicit confirmation.

## Local Web workbench

The React/FastAPI workstation implements the same six-step contract as a
Plan → Run → Review → Seal lifecycle. It is local-only and does not add a
second pipeline: FastAPI delegates generation, QC, pause/resume, experiment
preparation and Finalize to the existing Python implementation.

For development, run the service and Vite in separate PowerShell terminals:

```powershell
conda run -n SPECT python -m uvicorn webui.server.app:app --host 127.0.0.1 --port 8765
Set-Location webui/frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

The workbench provides:

- a versioned shared draft whose Protocol, Phantom, Simulation and observation
  values are normalized and locked together only after preflight;
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

Use the repository's `src` directory on `PYTHONPATH`:

```powershell
$env:PYTHONPATH = 'src'
python -m cli init --run-id pilot-001 --cases 2 --mode prepare --output pilot-001.json
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

`run_batch.ps1` is now only a compatibility wrapper around this same CLI and requires an explicit config:

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

Activity and attenuation are exported atomically as C-order `float32`, immediately read back and checksummed. A SIMIND expectation is kept separate from any seeded offline Poisson observation. Observation records reference their parent phantom and inherit its fixed split.

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

This README, `DECISION_GATES.md`, `VALIDATION_RESULTS_2026-08-17.md`, `METHODS_SYNTHETIC_DATA.md` and the implementation report are the current knowledge set. Earlier Chinese tutorials, configuration notes, comparison documents and audits are retained as explicitly bannered historical records; their commands and protocol claims are not production instructions.

- [Implementation report](docs/IMPLEMENTATION_REPORT_2026-08-17.md)
- [Methods draft](docs/METHODS_SYNTHETIC_DATA.md)
- [Scientific decision gates](docs/DECISION_GATES.md)
- [Validation results](docs/VALIDATION_RESULTS_2026-08-17.md)
- [Local protocol evidence](docs/LOCAL_PROTOCOL_EVIDENCE_2026-08-17.md)
- [Stage 3 protocol promotion and pilot](docs/STAGE3_PROTOCOL_PROMOTION_2026-08-18.md)
- `manifests/legacy-v1-weighted-mc/` — read-only checksum freeze of the 500 historical cases.
- `runs/qa-smoke-20260817/` — finalized two-case deterministic software smoke, explicitly not scientific data.
- `runs/stage3-phantom-100-v3-20260818/` — accepted 100-case generated-population QC evidence.
- `runs/stage3-simind-pilot-10-v3-20260818/` — finalized corrected ten-case SIMIND/observation pilot.
- `docs/evidence/stage3_pilot_summary_2026-08-18.json` — compact machine-readable Stage-3 verdict and per-case metrics.
- `docs/evidence/` — native Windows UI screenshots.

## Tests

```powershell
$env:PYTHONPATH = 'src'
$env:QT_QPA_PLATFORM = 'offscreen'
python -m pytest -q
```

The suite covers generator geometry and lesion placement, attenuation/export contracts, split determinism, QC, observation separation, pause/resume and corruption rejection, prepared experiments, UI boundaries and the complete two-case mock pipeline.

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
