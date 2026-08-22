# Repository governance

## Active production scope

Only `windows_v1` / `hybrid_v2_limited_activity_v1` / `windows_native` may create or resume production runs. New entry points must delegate to `PipelineRunner` and must enforce the same strict configuration contract. Unknown fields and old profiles are errors.

The scientific authority and binary/orientation contracts are defined in `WINDOWS_V1_SCIENTIFIC_AUTHORITY.md`. `PAR-S_2` is an immutable upstream evidence source: never add a runtime dependency, modify it, or copy unrelated code from it.

## Historical code

Legacy master generation, Task12/Task13 full V2, Gate B Linux and the PyQt UI are retained only for traceability. Do not extend them for new production. The PyQt application remains behind `legacy_pyqt.py`.

## Change control

- Add tests before changing active production behavior.
- Do not change scientific formulas, random sampling, seed-domain labels, array order, units or command tokens during cleanup refactors.
- Every resume-relevant input must have a persisted fingerprint or SHA-256 and be rechecked before reuse.
- Real SIMIND execution requires explicit consent; unverified runtime and more than ten real cases require separate consent.
- Do not commit SIMIND binaries, generated runs, caches, credentials or local settings.
- A release requires the automated Windows verification, one positive and one true-negative NN=10 real-SIMIND result, and byte-equivalence evidence across the final cleanup refactor.

## Cleanup

Branches and worktrees representing scientific milestones must be tagged before removal. Submit a closeout report listing dirty/untracked files, refs, test evidence, release SHA and proposed deletion targets. Destructive cleanup requires a new explicit user confirmation after that report. Never clean or modify `PAR-S_2`.
