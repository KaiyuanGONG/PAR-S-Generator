# PAR-S Web UI functional traceability

Baseline: Git tag `pyqt-v0.5-freeze` (`f423b81`). The frozen PyQt application is
the workflow contract. The React/FastAPI workbench can reorganize the tasks, but
the service remains a thin boundary around `PipelineRunner` and the existing
phantom/SMC/readback code.

Status vocabulary: **complete** is reachable in the current Web UI and backed by
the named API/runner behavior; **replaced** means the old control is superseded by
an explicit new mechanism; **bounded** means the capability remains available but
the current scientific run contract intentionally restricts it.

## Contract-to-implementation matrix

| Capability | Frozen behavior | Current Web implementation | Automated evidence | Status |
|---|---|---|---|---|
| Run identity and root | Edit run ID, case count and destination before execution | Protocol edits a shared v3 draft; allowlisted directory browser and `fs/validate` verify the root | lifecycle E2E; filesystem API tests | complete |
| Protocol contract | Label/status, activity, exposure and SMC Index-25; require `activity × time = Index-25` | Readable validated preset with explicit expert override and live product gate | lifecycle E2E; runner validation regression | complete |
| Delayed plan lock | Apply settings before executing | Protocol and Phantom only advance readiness. Simulation preflight is the sole `Lock run plan` action; server normalization then makes the run read-only | lifecycle E2E; reducer tests | complete |
| Reset defaults | Reset editable settings | Confirmed `Reset draft` restores `/api/defaults`; sealed/locked runs require Fork instead | workbench E2E; reducer tests | complete |
| Phantom preview/run parity | Preview and batch share one `PhantomConfig` | `toPhantomConfig()` supplies both preview and `config_overrides.phantom`; a preview digest is invalidated by every draft edit | workspace/unit tests; preview API tests; lifecycle E2E | complete |
| Phantom cohort parameters | Matrix/voxel, geometry jitter, lobe ratio, tumor count/contrast/morphology, perfusion, counts/background and placement constraints | Recommended controls plus a disclosed expert layer; measured lesion surface margin is retained | preview API tests; visual/E2E checks | complete |
| Phantom matrix boundary | PyQt exposed research matrix controls although the frozen runner only validates `128³` | `128³` is labelled as the validated run matrix. Other frozen discrete sizes are expert, preview-only research values and correctly receive preflight 422 | preflight API regression | bounded |
| Reproducible seeds | Preview draw plus global batch seed strategy | Preview seed/case navigation is separate from editable `global_seed`/`use_global_seed`; Next draw uses the incremented seed | workspace mapping tests; browser E2E | complete |
| Phantom preset exchange | Save/load Phantom JSON | Browser download/upload round-trips the actual draft payload; managed output fields are not trusted from the file | lint/build; browser interaction surface | complete |
| Multi-plane inspection | Three orthogonal slices, overlays and measured metrics | Activity/μ, liver/tumor masks or contours, direction labels, scale, probe and lesion jump controls | preview API tests; browser E2E | complete |
| Fourth Phantom view | Frozen PyQt had a separate 3D Surface tab; its four-grid cell held metrics | The fourth cell defaults to interactive 3D and toggles to MIP. All four views share one voxel cursor; dragging, sliders, lesion jumps and keyboard movement update together | linked-cursor E2E; 61 visual snapshots | complete |
| Simulation paths | Browse executable and SMC | Both buttons open the allowlisted server-side file browser; no dead native-dialog placeholders remain | filesystem API tests; axe | complete |
| SMC preflight/provenance | Parse the selected SMC and display acquisition plus raw expert indices/flags | `run/preflight` checks paths, type-7 inputs, shape/sampling, cross sections, activity-time and detector request without creating a run | preflight API tests | complete |
| Transport settings | Mode, `/NN`, workers and deterministic `/RR` base | All enter the shared draft and effective server config; Execute still requires a second Run confirmation | lifecycle E2E; API lifecycle tests | complete |
| Observation contract | Poisson on/off, empirical/fixed policy, scale and status | Presets plus expert controls for policy, scale, protocol status and seed offset; UI transitions only produce runner-valid combinations | Simulation unit tests; preflight/lifecycle tests | complete |
| Validation experiment preparation | Prepare five experiment packages without launching SIMIND | Allowlisted destination browser calls the existing `prepare_all_experiments()` via `POST /api/experiments/prepare` | preflight API tests | complete |
| Effective contract inspection | Read complete effective `PipelineConfig` before Run | Run Center exposes server-normalized canonical JSON and copy action | build/E2E lifecycle fixture | complete |
| Start/execute gate | Explicit confirmation before real SIMIND | Execute Start/Resume stays disabled until the checkbox is selected and the server also requires `allow_simind_execution:true` | lifecycle/API tests | complete |
| Pause/resume | Pause at a safe boundary; explicitly resume | `pause-requested` is visible; paused registry entries can be atomically resumed while running conflicts remain 409 | lifecycle E2E; API lifecycle tests | complete |
| No automatic finalize | Run first, review, then finalize | every start sends `finalize:false`; completed tasks enter Review | lifecycle E2E; API lifecycle tests | complete |
| Run monitoring | Ordered stages, case ledger and execution feedback | Nine pipeline stages, aggregate progress, per-case QC and live WebSocket stream with polling recovery | lifecycle E2E | complete |
| Review refresh/evidence | Refresh ledger, inspect stage JSON, per-case backend/effective `.res` values and images | Manual refresh, expandable/copyable stage records, linked ledger/effective evidence and expectation/observation projection/sinogram | review API tests; lifecycle E2E | complete |
| Arbitrary `.a00` inspection | Open a projection file and inspect shape/views/sinogram/statistics | Allowlisted artifact browser plus safe summary, canonical projection and correctly oriented sinogram endpoints | review API tests | complete |
| Review export | Preserve evidence outside the screen | Cases CSV and QC JSON report are generated from the currently loaded real ledger/evidence | browser build/E2E surface | complete |
| Manifest and splits | Inspect package inventory and fixed partition | Review and Seal read real `dataset_manifest.json` and `splits.json`; no synthetic manifest is generated in Web code | API lifecycle and lifecycle E2E | complete |
| Explicit Seal | Readiness checklist, irreversible acknowledgement, Finalize and hash | Separate Seal workspace requires readiness, exact run ID and acknowledgement; calls only `PipelineRunner.open(root).finalize()` and displays package SHA-256 | lifecycle E2E; API concurrency/gate tests | complete |
| Immutable run/fork | Sealed data is read-only | sealed reducer rejects edits; Fork creates a new draft/run identity without altering the sealed root | reducer tests; lifecycle E2E | complete |
| Refresh recovery | Recover selected run/task after reload | `pars.workspace.v3` stores only draft/view and lookup pointers; `/api/runs`, `/api/tasks` and ledger state are authoritative after reload | lifecycle E2E; workspace tests | complete |
| Error semantics | Actionable validation/path/conflict failures | Shared localized notice guides 403/404/409/422 and preserves raw server detail in a disclosure | ErrorNotice unit tests; API error regressions | complete |
| Theme/language | Desktop settings | English, Simplified Chinese and French plus light/dark/system theme are persisted; pre-paint bootstrap avoids a light flash | i18n/unit/E2E/visual tests | complete |
| Desktop accessibility | Keyboard-operable scientific workstation | skip link, landmarks, labelled controls, visible focus, ≥24 px targets, reduced motion, non-colour status and keyboard-linked imaging cursor | axe 6-workspace suite; keyboard E2E; token contrast tests | complete |

## Intentional replacements and exclusions

- The old autosave toggle is replaced by versioned, always-on local draft
  persistence. Runtime evidence is never trusted from local storage.
- The old global default-output field is replaced by an explicit, validated
  runs root and mandatory run-isolated layout. Global SIMIND/SMC settings are
  now per-plan inputs so provenance is visible before locking.
- The old About dialog is replaced by persistent product, contract, service
  version and repository-root diagnostics in the shell.
- The historical `ResultsPage` code was not reachable from the frozen main
  window and is not treated as a product contract.
- Cancel/kill, cloud, authentication, multi-user operation, reconstruction,
  model management and `select-pilot` are not exposed. No backend verb exists
  for a safe hard cancel, and this work does not add fake UI for it.
- Tests never launch real SIMIND. Execute mode is covered only at the explicit
  authorization boundary; scientific execution remains an operator action.

## Verification and visual evidence

- `npm run lint`, `npm run test:unit`, `npm run build`
- `npm run test:e2e` — full Plan → preview → preflight → lock → Run →
  pause/resume → Review → explicit Seal plus reload/offline recovery
- `npm run test:a11y` — axe scan of all six workspaces at 1280×720
- `npm run test:visual` — 36 screenshots at 1440×900 (six workspaces × three
  languages × two themes), 24 screenshots at 1280×720 (Chinese/French × two
  themes), plus a populated synchronized Phantom screenshot
- focused Web API tests cover preview, filesystem, preflight, Review and
  lifecycle/finalize races
- full Python regression includes generator/pipeline contracts and frozen PyQt
  smoke tests

Playwright baselines live in
`webui/frontend/tests/e2e/visual.spec.ts-snapshots/`; curated, unmasked runtime
screenshots live under `docs/evidence/`.
