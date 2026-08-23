# PAR-S local Web API contract — Windows v1

Scope: a localhost-only FastAPI boundary around the existing `PipelineRunner`,
phantom generator, SMC parser, validation-experiment preparer and projection
readback code. The frontend does not construct SIMIND commands, compute QC,
invent manifests or write run ledgers.

Transport: JSON/PNG over HTTP on `127.0.0.1:<port>`, plus a WebSocket event
stream. The production React bundle is served from the same origin.

## 1. Read resources

| Endpoint | Result / authority |
|---|---|
| `GET /api/health` | service name/version and repository root |
| `GET /api/defaults` | strict `PipelineConfig.for_windows_v1(...).to_dict()` |
| `GET /api/protocol` | scoped protocol constants, stage order, detector/projection geometry and canonical transform |
| `GET /api/runs?root=<runs_root>` | allowlisted run scan including `config_path` for recovery |
| `GET /api/run?root=<run_root>` | full `run.json`, case count and effective configuration |
| `GET /api/run/cases?root=&offset=&limit=` | paginated `cases.jsonl` records |
| `GET /api/run/case-evidence?root=&case=` | selected case backend, effective SMC values and bounded `.res` excerpt |
| `GET /api/run/stages?root=` | ordered stage records; finalized ledgers expose a terminal `finalize` record |
| `GET /api/run/manifest?root=` | parsed real `dataset_manifest.json` |
| `GET /api/run/splits?root=` | parsed real `splits.json` |
| `GET /api/tasks` | in-process task registry used for refresh recovery |
| `GET /api/tasks/{task_id}?cursor=` | task status plus incremental events and next cursor |

Every root/path endpoint resolves the requested path and checks it against the
filesystem allowlist before reading it.

## 2. Planning and lifecycle actions

| Endpoint | Body | Semantics |
|---|---|---|
| `POST /api/run/preflight` | create-run payload | Normalize and validate the complete draft, parse the selected SMC, return checks/digest/canonical config; writes nothing and never launches SIMIND |
| `POST /api/runs` | `{run_id,runs_root,mode,windows_v1,simind_exe,smc_file,nn_multiplier,max_simind_workers}` | Create one strict, server-normalized Windows v1 config JSON; unknown fields are rejected and no run is executed |
| `POST /api/experiments/prepare` | `{destination,simind_exe,smc_file}` | Call the frozen five-experiment preparer; returns `prepared_not_run` and never executes SIMIND |
| `POST /api/run/start` | `{config_path,resume=false,finalize=false,allow_simind_execution=false,allow_unverified_runtime=false,allow_large_simind_execution=false}` | Start or explicitly resume `PipelineRunner.run_all()` in a background task. The Web UI always sends `finalize:false`; real SIMIND, unknown runtime hashes and more than ten real cases have independent consent gates |
| `POST /api/tasks/{task_id}/pause` | none | `runner.request_pause()`; the task stops at the next safe boundary |
| `POST /api/run/finalize` | `{run_root}` | Reserve the run and call only `PipelineRunner.open(root).finalize()`; returns finalized state, manifest path and package SHA-256 |

The complete production draft is the strict top-level transport/runtime
surface plus `windows_v1`, whose public fields are `schema_version`,
`generation_profile`, `runtime_backend`, `cohort`, `lesions` and `seed`.
Preview and create both derive the same locked `PhantomConfig` from this object.
Old profiles, unknown fields and an offline observation request are rejected;
they are never silently migrated.

## 3. Phantom-derived inspection

| Endpoint | Notes |
|---|---|
| `POST /api/preview/phantom` | `{phantom_config,case_index,seed,overrides}`; generates one bounded in-memory preview and returns geometry, measured summary, config digest and opaque preview ID |
| `GET /api/preview/phantom/{id}/slice?plane=&index=&layer=&overlay=` | fixed-window PNG for axial/coronal/sagittal; activity or μ; liver/tumor/contour/none overlays |
| `GET /api/preview/phantom/{id}/mip?plane=&layer=&overlay=` | maximum-intensity projection using the same windows/overlays |
| `GET /api/preview/phantom/{id}/probe?x=&y=&z=` | voxel/physical position, activity, μ, liver membership and lesion IDs |
| `GET /api/preview/phantom/{id}/mesh?structure=` | gzip-friendly flat XYZ vertices/faces derived by marching cubes for liver and/or tumors |

Preview entries are process-local, TTL/LRU bounded and read-only. A missing or
expired preview returns 404; invalid indices/config return 422. Raw 3D arrays are
never transferred to the browser.

## 4. Projection and artifact inspection

| Endpoint | Notes |
|---|---|
| `GET /api/run/projection?root=&case=&view=&layer=` | selected run case/view after `raw[:,::-1,:]`; Windows v1 produces `expectation`, while `observation` is accepted only for read-only inspection of historical runs |
| `GET /api/run/sinogram?root=&case=&row=&layer=` | detector columns horizontal and acquisition views vertical |
| `GET /api/artifact/inspect?path=` | safe `.a00` shape/type/transform/count statistics; rejects malformed or excessive stacks |
| `GET /api/artifact/projection?path=&view=` | selected canonical projection PNG |
| `GET /api/artifact/sinogram?path=&row=` | linked canonical sinogram PNG |

Artifact endpoints only accept allowlisted `.a00` files containing whole
128×128 float32 views and cap the view count at 4096.

## 5. Server-side filesystem browser

| Endpoint | Notes |
|---|---|
| `GET /api/fs/list?path=` | roots, parent, directories and files with size/mtime; never lists outside configured roots |
| `GET /api/fs/validate?path=&kind=simind_exe\|smc\|runs_root\|export_root` | existence/type/extension and Windows path rules as applicable |
| `POST /api/fs/pick` | `{kind,initial_path}` invokes the native Windows file/folder dialog; cancellation returns `{cancelled:true,path:null}` without changing the draft and an accepted parent is authorized only for the current service session |

Filesystem errors use HTTP rather than an `{error}` payload with status 200:
403 outside allowlist, 404 missing, 409 I/O/runtime conflict and 422 wrong type,
extension, parse or configuration.

## 6. Progress stream

`WS /api/ws/tasks/{task_id}` emits the same event records returned by task
polling, for example:

```json
{"type":"stage_started","stage":"generate","ts":1787180000.0}
{"type":"progress","stage":"generate","done":4,"total":10}
{"type":"stage_passed","stage":"phantom_qc","status":"passed"}
{"type":"paused","stage":"export"}
{"type":"error","stage":"expectation","message":"..."}
{"type":"finished","finalized":false,"run_root":"..."}
```

Active Windows v1 execution is `generate → phantom_qc → export → simind_plan →
expectation → projection_qc → package → finalize`. The watcher also recognizes
the historical `observation` stage so old ledgers remain reviewable; a new
Windows v1 run never enters it.

## 7. Lifecycle and safety invariants

- Execute mode requires both a UI acknowledgement and
  `allow_simind_execution:true`; the server returns 403 without it.
- An unverified SIMIND/SMC pair additionally requires
  `allow_unverified_runtime:true`; more than ten real cases additionally require
  `allow_large_simind_execution:true` after cost review.
- Start defaults to `finalize:false`. Normal Web execution always ends in Review;
  Seal is a separate explicit action.
- A paused task is resumable only through `resume:true`. Running work, paused work
  and a finalization reservation block conflicting starts/finalize with 409.
- After opening a ledger for Finalize, the server verifies that
  `runner.layout.root.resolve()` exactly equals the requested allowlisted root
  before the irreversible call.
- Resume artifact acceptance, QC gates and manifest immutability remain wholly in
  the runner.
- Pydantic/path/config validation maps to 403/404/409/422 while preserving the
  original detail for diagnostics.

## 8. Explicit exclusions

No authentication, cloud/remote execution, multi-user coordination,
reconstruction, model workflow, hard task cancellation or `select-pilot` Web
endpoint is included. The browser never launches real SIMIND during tests.
