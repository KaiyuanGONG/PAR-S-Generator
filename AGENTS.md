# PAR-S Generator agent guide

## Project

PAR-S Generator is a native-Windows, local-only FastAPI/React application and
CLI for reproducible synthetic liver SPECT dataset preparation. It stops at a
QC-checked, checksum-inventoried dataset; reconstruction and model workflows
are out of scope.

## Scientific authority

- New production is only `windows_v1` / `hybrid_v2_limited_activity_v1` /
  `windows_native` through `PipelineRunner`.
- Anatomy is Hybrid V2 Gate A; lesions are corrected-master; activity is the
  read-only PAR-S_2 Gate C LimitedActivity v1 port.
- Never modify `D:\PFE-U\PAR-S_2` or add a runtime dependency on it.
- Legacy/master, Task12/13 full V2, Gate B Linux and PyQt are historical only.
- Do not change formulas, sampling, seed domains, units, array order, SIMIND
  tokens or locked parameters during cleanup/refactoring.

## Setup and verification

```powershell
.\setup_windows.ps1
.\start_windows.ps1
.\scripts\verify_windows_v1.ps1 -SkipRealSimind
```

Real release acceptance is one positive plus one true-negative case with
NN=10, worker=1 and the validated EXE/SMC hashes. See
`docs/WINDOWS_V1_ACCEPTANCE.md`; scientific details live in
`docs/WINDOWS_V1_SCIENTIFIC_AUTHORITY.md`.

## Stack and layout

- Python 3.11: `src/core`, `src/pipeline`, `webui/server`, `tests`.
- React/TypeScript/Vite: `webui/frontend`; prebuilt `dist` ships with source.
- Entrypoints: `main.py`, `src/cli.py`; PyQt only via `legacy_pyqt.py`.
- Generated runs, caches, local settings and licensed SIMIND binaries stay out
  of Git. Preserve unrelated dirty worktree content.

## Current status and next work

Windows v1.0.0 is the only active release line. Determine exact release state
from Git/remote evidence; do not infer merged or released from documentation.
Linux/WSL support is future work and must not appear as a v1 switch.

Before removing branches, worktrees or residue, write the closeout report and
obtain a new explicit user confirmation after they have reviewed it. Never
clean or modify PAR-S_2.
