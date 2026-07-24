# Task 2 report: safe Task13 archive validation and role contracts

## Requirements covered

- Added `stage_results_archive(...)`: verifies a strict SHA-256 sidecar, rejects mismatches before extraction, safely extracts to a unique temporary directory, writes an archive-bound completion marker, and atomically replaces the staging root.
- Added path-traversal, archive-SHA-mismatch, staging-resume, and no-local-SIMIND tests.
- Added `RoleContract` and `validate_formal_inputs(...)` for the exact `main` (500, 400/50/50) and `negative` (50 test-only) roles, including their distinct case-ID namespaces, profiles, weights, and sampling policy.
- Bound local role preflight generation/split/report bytes to the immutable uploaded bundle, and bound both remote preflight and downloaded master schemas/hashes/case records to that bundle.
- Added immutable defaults and CLI parsing for `--validate-only`, `--resume`, and `--max-cases`; `--max-cases` is validated as 1..550 before any archive work.
- Restored the existing Task 1 tumor-negative metadata test to `core.pilot_v2`.
- No Task 3+ case writing, projection processing, or local SIMIND execution was added.

## Files changed

- `scripts/finalize_task13_formal550_local.py` (new)
- `tests/test_task13_formal550_local.py`
- `.superpowers/sdd/task-2-report.md` (this report)

## TDD evidence

1. The initial focused run exposed the missing `validate_formal_inputs` API (2 expected `AttributeError` failures) before that validation layer was implemented.
2. The uploaded/local preflight byte-drift test was added and observed failing with “DID NOT RAISE” before byte binding was implemented.
3. Focused tests then passed after the minimal implementation changes.

The pre-existing untracked draft finalizer could not be deleted because Windows returned access denied, so it was replaced in place through patches. The red runs above still demonstrated the newly required behavior was absent before implementation.

## Test commands and results

```text
conda run -n SPECT python -m pytest tests/test_task13_formal550_local.py -q
12 passed, 1 warning in 0.90s

git diff --check
no output / success

conda run -n SPECT python -m pytest tests/test_task12f_linux50.py tests/test_task13_formal550.py tests/test_task13_formal550_local.py -q
24 passed, 1 warning in 20.66s
```

The warning is a pre-existing pytest cache permission warning for `.pytest_cache`; it does not affect test collection or results.

`python -m py_compile` could not write a `scripts/__pycache__` bytecode file because of a filesystem permission denial. The module was nevertheless imported and executed by all focused and regression tests.

## Self-review and concerns

- Reviewed role policies against the actual Task13 preflight builder: negative controls have zero population weight but normalized sampling probability (1/50), rather than zero sampling probability.
- The validator deliberately stops after archive/input contract validation. It does not inspect node result quartets or invoke SIMIND; those later responsibilities are outside Task 2.
- Primary implementation commit: `104e01c` (`Add Task13 formal550 archive validation`).
