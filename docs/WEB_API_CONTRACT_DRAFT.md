# PAR-S Web API Contract — Draft v0.1 (D0)

Scope: thin local service wrapping the existing `PipelineRunner`. The frontend never
constructs SIMIND commands, never computes QC, never writes run state. All state is
read from `run.json` / `cases.jsonl` / `splits.json` via the service. One pipeline,
one grammar — same as CLI/GUI today.

Transport: FastAPI on `127.0.0.1:<port>` (localhost only). JSON over HTTP + one
WebSocket for progress. Static React bundle served from the same origin (`/`).

## 1. Resources (GET)

| Endpoint | Returns | Source of truth |
|---|---|---|
| `/api/health` | `{version, python, pyqt_available}` | — |
| `/api/defaults` | full `PipelineConfig.to_dict()` defaults incl. nested `PhantomConfig` | `runner.PipelineConfig`, `phantom_generator.PhantomConfig` |
| `/api/protocol` | protocol constants: 60 MBq, 28.4 s, Index-25 1704, detector 160×208, projection (60,128,128), canonical transform `raw[:,::-1,:]`, contract statuses | `pipeline.contracts` |
| `/api/runs?root=<path>` | list of runs: `{run_id, root, created_utc, mode, case_count, finalized, stage_summary}` | scan `<root>/*/run.json` |
| `/api/runs/{id}` | full `run.json` payload + `case_count` (= CLI `inspect`) | `RunLedger.load()` |
| `/api/runs/{id}/cases` | array from `cases.jsonl` (paginated `?offset&limit`) | `RunLedger.read_cases()` |
| `/api/runs/{id}/stages` | ordered stage records `{stage, status, evidence}` | `run.json` stages |
| `/api/runs/{id}/manifest` | `dataset_manifest.json` if finalized | file |
| `/api/runs/{id}/splits` | `splits.json` | file |

## 2. Actions (POST)

| Endpoint | Body | Semantics |
|---|---|---|
| `/api/runs` | `{run_id, runs_root, cases, mode, config_overrides?}` | = CLI `init`; writes editable config JSON; **does not execute** |
| `/api/runs/{id}/start` | `{resume: bool, finalize: bool, allow_simind_execution: bool}` | = CLI `run`. Server refuses `mode=execute` without `allow_simind_execution:true` (mirror of `--allow-simind-execution`). Returns `{task_id}`; runs in background thread via `PipelineRunner(config, resume=...)`, `run_all()` |
| `/api/tasks/{task_id}/pause` | — | `runner.request_pause()`; resume = new `start` with `resume:true` |
| `/api/preview/phantom` | `{phantom_config, seed, overrides?}` | one-case in-memory `PhantomGenerator.generate_one`; returns case summary (liver_volume_ml, left_ratio, lesion table with measured diameters/margins/TNR) + preview id |
| `/api/runs/{id}/select-pilot` | `{count}` | = CLI `select-pilot` |
| `/api/runs/{id}/finalize` | `{}` | `runner.finalize()` — only when stage gates allow |

Rule: every action endpoint is a 1:1 mapping onto an existing runner/CLI verb.
No new orchestration logic lives in the service.

## 3. Previews (GET, PNG)

| Endpoint | Notes |
|---|---|
| `/api/preview/phantom/{pid}/slice?plane=axial\|coronal\|sagittal&index=0..127&layer=activity\|mu` | server-rendered PNG (matplotlib/PIL, grayscale) |
| `/api/runs/{id}/cases/{case}/projection?view=0..59&layer=expectation\|observation` | applies validated transform `raw[:,::-1,:]` before rendering |
| `/api/runs/{id}/cases/{case}/sinogram?row=0..127` | one detector row across all views |

## 4. Filesystem (server-side, replaces native dialogs)

| Endpoint | Notes |
|---|---|
| `GET /api/fs/list?path=` | dirs + files with size/mtime; roots restricted to configured allowlist (repo root, runs root, drives on demand) |
| `GET /api/fs/validate?path=&kind=simind_exe\|smc\|runs_root` | existence + extension + (smc) parseable check |

## 5. Progress events — `WS /api/ws/tasks/{task_id}`

```json
{"type": "stage_started",  "stage": "generate", "ts": "..."}
{"type": "case_done",      "stage": "generate", "case_id": "case_0001", "index": 1, "total": 100}
{"type": "stage_passed",   "stage": "phantom_qc", "evidence": { ... }}
{"type": "log",            "level": "info", "line": "..."}
{"type": "paused",         "stage": "export"}
{"type": "error",          "stage": "simind", "case_id": "case_0007", "message": "..."}
{"type": "finished",       "finalized": true, "run_root": "..."}
```

Stage vocabulary (ordered, from `run_all`): `generate → phantom_qc → export →
simind_plan → expectation(simulate|mock) → projection_qc → observation → package → finalize`.

## 6. Invariants carried over from the pipeline

- Explicit confirmation gate for real SIMIND execution (UI checkbox + server flag).
- Resume accepts artifacts only when hash + stage checks pass (server just relays).
- A finalized manifest is immutable; `start` on a finalized run returns 409.
- `PipelineConfig.__post_init__` validation errors surface as HTTP 422 with the
  original message (single source of validation truth).

## 7. Non-goals (v1)

No auth (localhost only), no multi-user, no remote execution, no reconstruction,
no experiment preparation UI (CLI keeps owning `prepare-experiment`).
