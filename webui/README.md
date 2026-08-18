# PAR-S web UI — local service

Thin FastAPI wrapper around the existing `PipelineRunner`. Contract:
`docs/WEB_API_CONTRACT_DRAFT.md`. The frontend owns zero pipeline logic;
every action maps 1:1 onto an existing runner/CLI verb, and progress is
observed by diffing `run.json` + artifact counts (no runner changes).

## Run (Windows, repo root)

```powershell
pip install -r webui/requirements-web.txt
python webui/server/app.py            # serves http://127.0.0.1:8765
```

Interactive API docs: http://127.0.0.1:8765/docs

## Endpoints (v0.1)

- `GET  /api/health · /api/defaults · /api/protocol`
- `GET  /api/runs?root=` — list runs; `GET /api/run|/api/run/cases|/api/run/stages?root=`
- `POST /api/runs` — write editable run config (CLI `init` equivalent)
- `POST /api/run/start` — background `run_all`; **403 unless
  `allow_simind_execution:true` when mode=execute** (mirrors CLI flag)
- `POST /api/tasks/{id}/pause` — `request_pause()`; resume = start with `resume:true`
- `GET  /api/tasks/{id}?cursor=` and `WS /api/ws/tasks/{id}` — event stream
- `POST /api/preview/phantom` + `GET /api/preview/phantom/{pid}/slice` — in-memory
  one-case preview, server-rendered PNG
- `GET  /api/run/projection|/api/run/sinogram` — PNG in the validated canonical
  orientation `raw[:, ::-1, :]`
- `GET  /api/fs/list|/api/fs/validate` — allowlisted server-side browsing
  (extra roots via env `PARS_FS_ROOTS`, e.g. `D:\`)

## Verified (cloud, 2026-08-18)

Full 2-case mock run through the API: create → start → 8 stages passed →
finalized manifest; WebSocket replayed 17 events ending in `finished`;
projection/sinogram/phantom-slice PNGs valid; execute-without-confirmation
correctly refused (403); invalid config override surfaced as 422 with the
original `PipelineConfig` message. Core pipeline tests: 47 passed.
