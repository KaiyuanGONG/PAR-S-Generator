# PAR-S Generator

PAR-S Generator prepares reproducible synthetic liver SPECT datasets for the current GE NM/CT 870 CZT research protocol. Its endpoint is a QC-checked, checksum-inventoried dataset package. It does **not** reconstruct images or run, train, manage or evaluate a model.

## Canonical workflow

There is one production path shared by the desktop UI and CLI:

```text
Generate → Phantom QC → float32 export → SIMIND plan/expectation
         → Projection QC → optional observation → Package/Finalize
```

Every invocation uses `runs/<run_id>/`; files from different runs are never globbed into one batch. `run.json` stores the effective configuration and stage evidence, `cases.jsonl` stores case-level provenance/QC, `splits.json` fixes the phantom-level partition, and `dataset_manifest.json` inventories packaged files by relative path, byte size and SHA-256 checksum. Resume accepts an artifact only when its hash and strong stage checks pass. A finalized manifest is immutable.

Current scientific limitations are visible states, not hidden defaults. In particular, the `/FD` attenuation mapping, detector matrix/FOV, activity–time contract and verified count scale remain pending the prepared control experiments. See [decision gates](docs/DECISION_GATES.md).

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

The canonical projection transform is `raw[::-1, ::-1, :]`, matching the existing PAR-S_2 consumer contract. Actual SIMIND launch always requires explicit confirmation.

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
- 128/160/208 detector matrix and FOV;
- point/line response and sensitivity;
- repeated `/RR` and `/NN` Monte Carlo behavior.

Each folder contains deterministic inputs, copied SMC variants, command JSON/BAT, a result template and an analyzer. Preparation never executes SIMIND. After an authorized run, analyze one package with:

```powershell
python -m cli analyze-experiment --experiment experiments/validation-v1/attenuation_ict
```

## Existing evidence

- [Implementation report](docs/IMPLEMENTATION_REPORT_2026-08-17.md)
- [Methods draft](docs/METHODS_SYNTHETIC_DATA.md)
- [Scientific decision gates](docs/DECISION_GATES.md)
- `manifests/legacy-v1-weighted-mc/` — read-only checksum freeze of the 500 historical cases.
- `runs/qa-smoke-20260817/` — finalized two-case deterministic software smoke, explicitly not scientific data.
- `docs/evidence/` — native Windows UI screenshots.

## Tests

```powershell
$env:PYTHONPATH = 'src'
$env:QT_QPA_PLATFORM = 'offscreen'
python -m pytest -q
```

The suite covers generator geometry and lesion placement, attenuation/export contracts, split determinism, QC, observation separation, pause/resume and corruption rejection, prepared experiments, UI boundaries and the complete two-case mock pipeline.

## Scope boundary

The software and documentation are limited to synthetic liver SPECT data preparation under the current protocol. Nothing here establishes general validity for every cancer, organ, scanner, collimator or acquisition protocol. Scanner-specific physical claims remain conditional on controlled experiments and correctly matched measurements.
