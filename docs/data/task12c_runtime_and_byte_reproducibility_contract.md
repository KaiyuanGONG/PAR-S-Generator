# PAR-S V2 Task 12C runtime and byte reproducibility contract

Status: implemented; formal one-case fixture must pass before the 50-case pilot is configured.

## Scope

This contract applies to every formal V2 preflight, SIMIND run and resume. It is not a retrospective upgrade of the already frozen pilot15 v1 dataset. That dataset remains internally valid under its existing manifest, but it cannot be used as evidence that two different Python environments generate identical inputs.

## Frozen bindings

The preflight report now records and hashes:

1. Python executable path, version and executable SHA-256.
2. Conda prefix, Conda package records/builds and `conda-meta/history` SHA-256.
3. All visible Python distribution names and versions.
4. NumPy, SciPy and scikit-image versions, module paths and module-file SHA-256 values.
5. Determinism-relevant environment variables (`PYTHONHASHSEED`, OpenMP, MKL, OpenBLAS and NumExpr thread settings).
6. Generator Git commit/tree, clean-worktree state and a per-file SHA-256 manifest covering `src/core` and the formal generation scripts.
7. Pilot plan, population profile, scanner profile, evidence registry, SMC, `simind.ini` and SIMIND executable hashes.
8. A frozen `INPUT_BUNDLE.json` containing every case's source/density path, byte size, SHA-256 and the semantic byte hash of every phantom/GT array.

The canonical runtime and source documents have their own `binding_sha256`. The formal runner recomputes both documents and requires exact JSON equality with preflight.

## Preflight-to-run byte contract

For each case, preflight creates the only source and density files eligible for SIMIND. The formal runner regenerates the deterministic case in a new attempt directory only to prove reproducibility. Before SIMIND can launch, it requires:

- exact source SHA-256 and byte count;
- exact density SHA-256 and byte count;
- identical dtype, shape and semantic C-order byte hash for every phantom/GT array;
- a valid, unchanged input-bundle manifest.

On success, the runner discards the regenerated source/density choice and points SIMIND to the original files in the frozen preflight bundle. It writes `PREFLIGHT_BYTE_IDENTITY.json` and freezes that evidence with the case. On any mismatch, the run fails before SIMIND launch.

## Resume contract

`PILOT_RUNTIME.json` contains the complete Python/Conda, source-code and input-bundle bindings. Resume reconstructs the expected document from the current process and requires exact equality. A changed interpreter, package set/build, deterministic environment variable, source commit, configuration, bundle manifest or input byte therefore forbids resume.

Completed SIMIND output is reusable only when its provenance still matches the bound source/density hashes, SIMIND binary, `/RR` and `/NN` values. Failed attempts remain immutable and a new attempt directory is allocated.

## Required validation sequence

1. Run the focused fail/pass tests.
2. Run the complete suite in the `SPECT` environment.
3. From a clean committed worktree, run the real one-case fixture twice without SIMIND.
4. Require `status=pass`, `formal_eligible=true`, runtime/source stability, full-array byte identity and source/density byte identity.
5. Only then create a new 50-case plan and preflight. Never upgrade or overwrite pilot15 v1 in place.

Formal one-case fixture command:

```powershell
conda activate SPECT
Set-Location "D:\PFE-U\PAR\.worktrees\PAR-S-Generator-task12"
python scripts\validate_task12c_reproducibility_v2.py
```

The fixture does not invoke SIMIND. Its default evidence root is `D:\PFE-U\PAR\outputs\pars_v2_task12c_fixture`.

## 50-case release gate

Task 12C closes only when the formal fixture and complete SPECT test suite pass. The later 50-case expansion must use newly generated preflight and dataset roots, must run preflight and runner in the same activated `SPECT` environment, and must archive each case's byte-identity evidence. Long SIMIND execution may be run locally and resumed, but it may not bypass these gates.
