# PAR-S V2 Task 12C acceptance

Date: 2026-07-15

Decision: **PASS**

## Closed defects

- Formal preflight and runner now require an identical Python/Conda runtime fingerprint.
- The runtime records the interpreter binary, Conda records/builds, installed distributions, critical numerical modules and deterministic thread/hash environment variables.
- Raw Windows environment paths and resolved junction targets are both recorded. The current `SPECT` Conda prefix and Python prefix resolve to the same environment.
- The Generator Git commit/tree and generation-source file hashes are frozen and must match exactly.
- Preflight emits one immutable `INPUT_BUNDLE.json`; its source/density files are the only bytes eligible for formal SIMIND execution.
- Runner regeneration is now a proof step. Source, density and every phantom/GT array must be byte-identical before SIMIND can launch.
- Successful cases freeze `PREFLIGHT_BYTE_IDENTITY.json`; resume rejects any runtime, source, configuration, manifest or byte drift.

## Formal fixture

The clean-worktree fixture generated `case_00000` twice in the `SPECT` environment without launching SIMIND.

| Gate | Result |
|---|---|
| Fixture status / formal eligibility | PASS / true |
| Conda prefix resolves to Python prefix | PASS |
| Python runtime stable within fixture | PASS |
| Generator source binding stable within fixture | PASS |
| Source and density SHA-256 + size identity | PASS |
| All 11 phantom/GT array dtype, shape and byte hashes | PASS |
| SIMIND launched | false |

Evidence root: `D:\PFE-U\PAR\outputs\pars_v2_task12c_fixture_v2`

- Fixture report SHA-256: `74974699b5571f24596453d15a8b92acdc32acc06e64e536ceb0737c7469f87f`
- Input bundle SHA-256: `e274ccceb033c9fec039e01d7548acc531e09233da8dc2654fccca05005f4c1c`
- Byte-identity report SHA-256: `94a5e374561032a69a446e62d08a65341da873476ad41e41492a3cb92705a9c3`
- Python/Conda binding SHA-256: `58db0b8d4afbb2df00d87a32f1941877cdb0e33eb543cedac8b378c336c63e52`
- Generator source binding SHA-256: `f35271ccb5d7955c8a89f2948defc076a09846109cfbe0af1a78e82ef690c744`
- Bound Generator commit: `b640fc15f22673c64254bef4f61008e0ee92c1f4`

## Tests

`conda run -n SPECT python -m pytest -q`

- **220 passed**
- 13 existing matplotlib/pyparsing deprecation warnings
- 459.60 seconds reported by pytest; 462.5 seconds wall time

## Release decision

Task 12C is closed. The next approved stage is a new 1–3 case full-chain run that exercises preflight bundle → byte proof → SIMIND → case writer → manifest/audit. The 50-case expansion remains disabled until that short full-chain verification passes. The frozen pilot15 v1 dataset is not modified or upgraded in place.
