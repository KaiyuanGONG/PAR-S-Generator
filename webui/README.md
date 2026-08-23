# PAR-S Windows v1 local Web workbench

The FastAPI service is a localhost-only boundary around `PipelineRunner`.
Frontend code does not generate anatomy/activity, construct SIMIND commands,
compute QC or write manifests. The API contract is documented in
`../docs/WEB_API_CONTRACT_DRAFT.md`; scientific authority is
`../docs/WINDOWS_V1_SCIENTIFIC_AUTHORITY.md`.

## Normal Windows use

From the repository root:

```powershell
.\setup_windows.ps1   # first use: locked Python 3.11 env, npm ci, build
.\start_windows.ps1   # daily start; binds loopback and opens the browser
```

The production bundle under `frontend/dist/` is tracked and included in source
releases. Rebuild it from the locked frontend dependencies with `npm ci` and
`npm run build`; do not substitute `npm install` in release verification.

Direct developer entrypoints, from the repository root:

```powershell
python main.py
python webui\server\app.py
python -m uvicorn webui.server.app:app --host 127.0.0.1 --port 8765
```

`main.py` is the product entrypoint and manages loopback binding, port
selection, the single-instance guard, browser opening and shutdown cleanup.
`legacy_pyqt.py` is the explicit historical compatibility entrypoint.

For hot reload, keep the API on port 8765, then run `npm run dev` from
`webui/frontend` and open `http://127.0.0.1:5173`. Interactive API docs are at
`http://127.0.0.1:8765/docs` when that fixed developer port is used.

## Windows v1 endpoints

- `GET /api/health`, `/api/defaults`, `/api/protocol`
- `POST /api/run/preflight` and `POST /api/runs`
- `POST /api/run/start`, `POST /api/tasks/{id}/pause`,
  `POST /api/run/finalize`
- `GET /api/tasks/{id}?cursor=` and `WS /api/ws/tasks/{id}`
- phantom preview slice/MIP/probe/mesh endpoints
- run projection/sinogram/evidence/manifest/splits endpoints
- `GET /api/fs/list`, `GET /api/fs/validate`, `POST /api/fs/pick`
- `POST /api/experiments/prepare` (prepares packages and never runs SIMIND)

Create/preflight accept only the strict `windows_v1` schema. Real SIMIND,
unverified runtime hashes and a batch over ten real cases use independent
authorization fields. Native pickers run in a short-lived GUI-main-thread
helper so FastAPI worker threads never own Qt dialogs; authorization is
session-scoped. Windows v1 creates the SIMIND expectation and does not create
the historical offline Poisson observation layer.

## Verification

The repository-level command is:

```powershell
.\scripts\verify_windows_v1.ps1 -SkipRealSimind
```

Frontend-only checks from `webui/frontend` are `npm run lint`,
`npm run test:unit`, `npm run build`, `npm run test:e2e`, `npm run test:a11y`
and `npm run test:visual`. The complete native-path, corruption/resume and real
SIMIND procedure is `../docs/WINDOWS_V1_ACCEPTANCE.md`.
