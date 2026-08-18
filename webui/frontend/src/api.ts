/** Typed client for the PAR-S local service (docs/WEB_API_CONTRACT_DRAFT.md).
 *  The UI owns no pipeline logic — every call maps onto a runner/CLI verb. */

export const API =
  (import.meta as any).env?.VITE_API ?? (location.port === "5173" ? "http://127.0.0.1:8765" : "");

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(API + path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

/* ── shapes ─────────────────────────────────────────────────────────── */

export interface Protocol {
  canonical_projection_transform: string;
  detector_matrix: [number, number];
  source_activity_mbq: number;
  exposure_s_per_projection: number;
  simind_activity_time_index25: number;
  activity_time_contract_status: string;
  empirical_clinical_total_counts: number[];
  empirical_clinical_angular_cv_range: [number, number];
  stage_order: string[];
  contract_version: number;
}

export interface RunSummary {
  run_id: string;
  root: string;
  created_utc?: string;
  mode?: string;
  case_count?: number;
  finalized: boolean;
  stages: Record<string, string>;
}

export interface StageRecord {
  stage: string;
  status: string;
  [k: string]: unknown;
}

export interface CaseRecord {
  case_id: string;
  split?: string;
  seed?: number;
  qc?: Record<string, any>;
  phantom?: Record<string, any>;
  expectation?: Record<string, any>;
  observation?: Record<string, any>;
  [k: string]: unknown;
}

export interface LesionRecord {
  id?: number;
  effective_diameter_mm?: number;
  nominal_diameter_mm?: number;
  surface_margin_mm?: number;
  /** measured tumour-to-normal ratios (local = versus pre-lesion neighbourhood) */
  tnr_local?: number;
  tnr_global?: number;
  target_contrast?: number;
  volume_ml?: number;
  mode?: string;
  lobe?: string;
  placement_stratum?: string;
  sampled_size_bin_mm?: [number, number];
  [k: string]: unknown;
}

export interface PreviewSummary {
  case_id: number;
  seed: number;
  liver_volume_ml: number;
  left_ratio: number;
  perfusion_mode: string;
  n_tumors: number;
  tumor_diameters_mm: number[];
  tumor_nominal_diameters_mm: number[];
  tumor_modes_used: string[];
  tumor_metadata: LesionRecord[];
  total_counts_actual: number;
  voxel_size_mm: number;
  cantlie_converged: boolean;
  generation_time_s: number;
}

export interface TaskState {
  task_id: string;
  run_id: string;
  run_root: string;
  status: "running" | "paused" | "finished" | "failed";
  error: string | null;
  result: { finalized: boolean } | null;
  event_count: number;
  events?: RunEvent[];
  cursor?: number;
}

export interface RunEvent {
  type: string;
  ts: number;
  stage?: string;
  status?: string;
  done?: number;
  total?: number;
  level?: string;
  line?: string;
  message?: string;
  error?: string | null;
  run_root?: string;
}

export interface FsEntry {
  name: string;
  type: "dir" | "file";
  size: number | null;
  mtime: number;
}

/* ── calls ──────────────────────────────────────────────────────────── */

export const api = {
  health: () => j<{ service: string; version: string; repo_root: string }>("/api/health"),
  defaults: () => j<Record<string, any>>("/api/defaults"),
  protocol: () => j<Protocol>("/api/protocol"),

  runs: (root?: string) =>
    j<{ runs_root: string; runs: RunSummary[] }>(
      "/api/runs" + (root ? `?root=${encodeURIComponent(root)}` : "")
    ),
  run: (root: string) => j<Record<string, any>>(`/api/run?root=${encodeURIComponent(root)}`),
  cases: (root: string, offset = 0, limit = 200) =>
    j<{ total: number; offset: number; cases: CaseRecord[] }>(
      `/api/run/cases?root=${encodeURIComponent(root)}&offset=${offset}&limit=${limit}`
    ),
  stages: (root: string) =>
    j<{ stages: StageRecord[]; finalized: boolean }>(
      `/api/run/stages?root=${encodeURIComponent(root)}`
    ),

  createRun: (body: {
    run_id: string;
    runs_root?: string;
    cases?: number;
    mode?: string;
    config_overrides?: Record<string, any>;
  }) =>
    j<{ config_path: string; config: Record<string, any> }>("/api/runs", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  startRun: (body: {
    config_path: string;
    resume?: boolean;
    finalize?: boolean;
    allow_simind_execution?: boolean;
  }) =>
    j<{ task_id: string; run_root: string }>("/api/run/start", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  pause: (taskId: string) => j<TaskState>(`/api/tasks/${taskId}/pause`, { method: "POST" }),
  task: (taskId: string, cursor = 0) => j<TaskState>(`/api/tasks/${taskId}?cursor=${cursor}`),
  tasks: () => j<{ tasks: TaskState[] }>("/api/tasks"),

  previewPhantom: (body: { phantom_config?: Record<string, any>; case_index?: number; seed?: number | null }) =>
    j<{ preview_id: string; summary: PreviewSummary }>("/api/preview/phantom", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  fsList: (path = "") => j<any>(`/api/fs/list?path=${encodeURIComponent(path)}`),
  fsValidate: (path: string, kind: string) =>
    j<{ path: string; kind: string; valid: boolean; detail: string }>(
      `/api/fs/validate?path=${encodeURIComponent(path)}&kind=${kind}`
    ),
};

/* image URLs (server-rendered PNG) */
export const img = {
  slice: (pid: string, plane: string, index: number, layer = "activity") =>
    `${API}/api/preview/phantom/${pid}/slice?plane=${plane}&index=${index}&layer=${layer}`,
  projection: (root: string, cs: string, view: number, layer = "expectation") =>
    `${API}/api/run/projection?root=${encodeURIComponent(root)}&case=${cs}&view=${view}&layer=${layer}`,
  sinogram: (root: string, cs: string, row: number, layer = "expectation") =>
    `${API}/api/run/sinogram?root=${encodeURIComponent(root)}&case=${cs}&row=${row}&layer=${layer}`,
};

export function openTaskSocket(taskId: string, onEvent: (e: RunEvent) => void): () => void {
  const base = API || location.origin;
  const url = base.replace(/^http/, "ws") + `/api/ws/tasks/${taskId}`;
  let closed = false;
  let poll: number | undefined;
  try {
    const ws = new WebSocket(url);
    ws.onmessage = (m) => onEvent(JSON.parse(m.data));
    ws.onerror = () => {
      /* fall back to polling below */
      if (!closed && poll === undefined) poll = startPolling(taskId, onEvent);
    };
    return () => {
      closed = true;
      ws.close();
      if (poll !== undefined) clearInterval(poll);
    };
  } catch {
    poll = startPolling(taskId, onEvent);
    return () => {
      closed = true;
      if (poll !== undefined) clearInterval(poll);
    };
  }
}

function startPolling(taskId: string, onEvent: (e: RunEvent) => void): number {
  let cursor = 0;
  return window.setInterval(async () => {
    try {
      const s = await api.task(taskId, cursor);
      cursor = s.cursor ?? cursor;
      (s.events ?? []).forEach(onEvent);
    } catch {
      /* keep polling */
    }
  }, 1000);
}
