# PAR-S web UI — local service

Thin FastAPI wrapper around the existing `PipelineRunner`. Contract:
`docs/WEB_API_CONTRACT_DRAFT.md`. The frontend owns zero pipeline logic;
every action maps 1:1 onto an existing runner/CLI verb, and progress is
observed by diffing `run.json` + artifact counts (no runner changes).

## Run (Windows, repo root)

```powershell
pip install -r webui\requirements-web.txt

# build the UI once (dist/ is git-ignored, so it must exist locally)
cd webui\frontend; npm install; npm run build; cd ..\..

python webui\server\app.py            # serves http://127.0.0.1:8765
```

Either invocation works — `python webui\server\app.py` (direct script) or
`python -m uvicorn webui.server.app:app --port 8765` (module, from the repo
root). The script form adds the repo root to `sys.path` itself.

Interactive API docs: http://127.0.0.1:8765/docs

Frontend development with hot reload (optional): run the server as above, then
`cd webui\frontend; npm run dev` and open http://localhost:5173 — the dev
server talks to the API on port 8765.

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
