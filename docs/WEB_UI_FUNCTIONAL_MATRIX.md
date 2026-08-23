# PAR-S Windows v1 Web UI functional traceability

The active contract is `windows_v1` / `hybrid_v2_limited_activity_v1` /
`windows_native`. The frozen PyQt application at tag `pyqt-v0.5-freeze` is a
historical comparison target, not a scientific or workflow authority. The
React/FastAPI workbench remains a thin boundary around `PipelineRunner` and the
shared generator, exporter, SIMIND command builder, readback and QC code.

Status vocabulary: **complete** is reachable in the current application and
backed by the named evidence; **historical-read** means old evidence can be
viewed but cannot create or resume production; **bounded** means the strict
scientific profile intentionally restricts the capability.

## Contract-to-implementation matrix

| Capability | Windows v1 implementation | Evidence | Status |
|---|---|---|---|
| Run identity and root | Run ID and a native/local validated runs root are chosen before creation; each root is run-isolated | filesystem/API tests; lifecycle E2E | complete |
| Unique production profile | The UI emits only `schema_version=windows_v1`, `generation_profile=hybrid_v2_limited_activity_v1`, `runtime_backend=windows_native`; unknown/legacy fields are rejected server-side | config/API boundary tests | complete |
| Cohort roles | Positive-only, true-negative-only and mixed queues expose separate positive/negative counts; true negatives are zero-lesion independent test controls | Windows v1 config/generator/pipeline tests | complete |
| Lesion controls | Count interval 1–5, three fixed size bands with editable non-negative weights, TNR subrange 2–8 and feasible territory policy | boundary tests; preview/API tests | complete |
| Locked physical values | 128³, 4.42 mm, 80,000 activity counts, residual background 0.05, gradient gain 0.08, physical μ-map and acquisition/FOV constants are readable but not editable | preflight/config tests | bounded |
| Preview/run parity | Preview and batch both derive the same locked `PhantomConfig` from `windows_v1`; every scientific draft edit invalidates the preview digest | workspace/unit/API/E2E tests | complete |
| Reproducible seeds | Global seed is limited to the JavaScript-safe integer range and produces recorded domain-separated patient/liver/μ/lesion/activity streams | schema and generator tests | complete |
| Multi-plane inspection | Activity/μ slices, liver/tumor masks or contours, direction labels, scale, voxel probe, lesion jumps, linked cursor, 3D/MIP | preview/API/E2E/visual tests | complete |
| Native path selection | SIMIND `.exe`, SMC `.smc`, runs root and experiment export root use a short-lived GUI-main-thread helper for Windows native dialogs; cancellation preserves the draft and accepted parents are session-scoped | filesystem/path/helper/API tests; real-desktop acceptance remains manual | bounded |
| Path safety | Local fixed drives only; UNC, inaccessible/read-only, wrong extension, reserved/trailing-dot-space and resolved paths over 240 characters fail preflight; spaces and Unicode are supported | Windows runtime/path tests | complete |
| Runtime provenance | Preflight shows executable/SMC hashes. Hash mismatch requires independent execute consent and yields `unverified_runtime`; hashes are recalculated before and after execution | runtime/SIMIND/API tests; native evidence | complete |
| SIMIND settings | Prepare/mock/execute, NN 1–1,000,000 and workers 1–32 enter the canonical config; NN=1/10/>10 and >10 real-case cost messages are explicit | unit/API/lifecycle tests | complete |
| Observation boundary | Windows v1 packages the projection expectation after QC and exposes no production observation controls | config rejection and Simulation unit tests | bounded |
| Historical observation review | Review can display observation artifacts and QC from older ledgers without enabling their creation or resume as Windows v1 | review API/UI tests | historical-read |
| Validation experiment preparation | The export-root picker and `POST /api/experiments/prepare` create the frozen experiment packages without launching SIMIND | API tests | complete |
| Effective contract inspection | Run Center displays the server-normalized canonical JSON before execution | build/E2E lifecycle | complete |
| Execution consent | Real execution, unverified runtime and a real batch over ten cases require separate confirmations; prepare/mock bypass only the real-cost gate | API gate tests; E2E | complete |
| Pause/resume integrity | Pause occurs at safe boundaries; resume rechecks config fingerprint, inputs, intermediate artifacts and runtime hashes, and conflicts return 409 | lifecycle/integrity tests; native resume evidence | complete |
| Active monitoring | Generate, Phantom QC, Export, SIMIND plan, Expectation, Projection QC and Package are monitored; Finalize is separate. The watcher recognizes historical Observation only for compatibility | lifecycle E2E; run ledgers | complete |
| Projection orientation | Projection and sinogram views use the single validated `raw[:, ::-1, :]` transform | orientation/API/visual tests | complete |
| Review and export | Real ledger/stage evidence, `.res` excerpt, projections, sinograms, manifest and splits are reviewable; CSV/QC exports derive from loaded evidence | API/E2E tests | complete |
| Explicit finalize | Seal requires readiness, exact run ID and acknowledgement and calls only `PipelineRunner.open(root).finalize()` | lifecycle/concurrency/gate tests | complete |
| Immutable run/fork | Finalized data are read-only; Fork creates a new draft/run identity and never edits the sealed root | reducer/E2E tests | complete |
| Refresh recovery | Storage key `pars.workspace.windows-v1`, payload schema 4, keeps draft/view and lookup pointers only; server run/task/ledger state is authoritative. Old schema-3 payloads are ignored, not migrated | workspace tests; lifecycle E2E | complete |
| Error semantics | Localized notices distinguish 403/404/409/422 while retaining bounded raw detail | unit/API tests | complete |
| Theme/language/accessibility | English, Simplified Chinese and French; light/dark/system; keyboard navigation, visible focus, reduced motion and non-colour status | unit/E2E/axe/visual tests | complete |

## Intentional exclusions

- Linux, WSL, server scheduling, cloud execution, authentication and multi-user
  operation are not Windows v1 modes or UI switches.
- Reconstruction, training, inference, checkpoint/model management and hard
  process cancellation are outside this product boundary.
- Browser automation never launches real SIMIND. Native NN=10 evidence is an
  operator/release step recorded separately in `WINDOWS_V1_ACCEPTANCE.md`.
- Legacy PyQt, legacy/master, Task12/Task13 full V2 and old observation drafts
  remain inspectable history and cannot be silently continued as Windows v1.

## Verification and visual evidence

- Python: 279 tests, including Gate A 100-case regression, LimitedActivity and
  Windows v1 integration; active paths also pass the pinned Ruff rules.
- Frontend: lint; 19 unit tests; production build; 6 E2E; 6 accessibility; 61
  visual comparisons with no baseline update.
- Native Windows: one positive plus one true-negative, seed 42, NN=10,
  worker=1, verified runtime; the pre/post-refactor comparison passed 42/42
  checks including byte-identical NPZ, ACT, ATN and `.a00` artifacts.

Playwright baselines live in
`webui/frontend/tests/e2e/visual.spec.ts-snapshots/`; curated UI and
machine-readable acceptance evidence live under `docs/evidence/`.
