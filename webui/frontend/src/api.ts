/** Typed client for the PAR-S local service (docs/WEB_API_CONTRACT_DRAFT.md).
 *  The UI owns no pipeline logic — every call maps onto a runner/CLI verb. */

export const API =
  (import.meta as any).env?.VITE_API ?? (location.port === "5173" ? "http://127.0.0.1:8765" : "");

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(API + path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const payload = await res.json();
      const serverDetail = payload.detail ?? payload;
      detail = typeof serverDetail === "string" ? serverDetail : JSON.stringify(serverDetail, null, 2);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
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
  config_path?: string | null;
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
  volume_shape: [number, number, number];
  mu_unit: string;
  mu_reference_energy_kev: number;
  cantlie_converged: boolean;
  generation_time_s: number;
}

export interface PreviewGeometry {
  shape_zyx: [number, number, number];
  voxel_size_mm: number;
  origin: "voxel-center";
}

export interface PhantomProbe {
  voxel: { x: number; y: number; z: number };
  position_mm: { x: number; y: number; z: number };
  activity: number;
  mu: number;
  in_liver: boolean;
  lesion_ids: number[];
}

export interface PreviewMeshObject {
  id: string;
  kind: "liver" | "tumor";
  lesion_id?: number;
  vertices: number[];
  faces: number[];
}

export interface PreviewMesh {
  shape_zyx: [number, number, number];
  voxel_size_mm: number;
  coordinate_order: "xyz-voxel";
  objects: PreviewMeshObject[];
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

export interface FinalizeResult {
  finalized: boolean;
  manifest_path: string;
  package_sha256: string | null;
}

export interface PreflightCheck {
  id: string;
  status: "passed" | "warning" | "failed";
  detail: string;
}

export interface SmcSummary {
  path: string;
  description: string;
  energy_kev: number;
  window_kev: [number, number];
  views: number;
  rotation_radius_cm: number;
  density_shape: [number, number, number];
  density_voxel_cm: number;
  detector_request: [number, number];
  detector_pitch_cm: number;
  activity_time_index25: number;
  raw_indices: Record<string, number>;
  enabled_flags: number[];
}

export interface PreflightResult {
  ready: boolean;
  config_digest: string;
  checks: PreflightCheck[];
  errors: string[];
  warnings: string[];
  smc: SmcSummary | null;
  canonical_config: Record<string, any>;
  provenance: {
    simind_executable: string;
    smc_file: string;
    mode: string;
    execution_authorized: false;
    windows_runtime: {
      status: "validated_windows_v1" | "unverified_runtime" | "missing_runtime";
      simind_path: string;
      simind_sha256: string | null;
      smc_path: string;
      smc_sha256: string | null;
      mismatches: string[];
    };
  };
}

export interface CaseEvidence {
  case: CaseRecord;
  effective: {
    projection_shape?: [number, number, number];
    nn_multiplier?: number;
    detector_matrix?: [number, number];
    voxel_size_mm?: number;
    source_activity_mbq?: number;
    exposure_time_s_per_projection?: number;
    smc_index25_activity_time?: number;
    type7_density_threshold_times_1000?: number;
    phantom_cross_sections?: string[];
  };
  backend?: string;
  rr_seed?: number;
  res_excerpt?: string | null;
}

export interface ArtifactSummary {
  path: string;
  shape: [number, number, number];
  dtype: "float32";
  canonical_transform: string;
  sum: number;
  minimum: number;
  maximum: number;
  nonzero_fraction: number;
}

export interface CreateRunBody {
  run_id: string;
  runs_root?: string;
  mode?: string;
  windows_v1: Record<string, any>;
  simind_exe: string;
  smc_file: string;
  nn_multiplier: number;
  max_simind_workers: number;
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
  caseEvidence: (root: string, caseId: string) =>
    j<CaseEvidence>(
      `/api/run/case-evidence?root=${encodeURIComponent(root)}&case=${encodeURIComponent(caseId)}`,
    ),
  stages: (root: string) =>
    j<{ stages: StageRecord[]; finalized: boolean }>(
      `/api/run/stages?root=${encodeURIComponent(root)}`
    ),
  manifest: (root: string) =>
    j<Record<string, any>>(`/api/run/manifest?root=${encodeURIComponent(root)}`),
  splits: (root: string) =>
    j<Record<string, any>>(`/api/run/splits?root=${encodeURIComponent(root)}`),

  createRun: (body: CreateRunBody) =>
    j<{ config_path: string; config: Record<string, any> }>("/api/runs", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  preflightRun: (body: CreateRunBody) =>
    j<PreflightResult>("/api/run/preflight", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  prepareExperiments: (body: { destination: string; simind_exe: string; smc_file: string }) =>
    j<{ prepared: number; execution_status: "prepared_not_run"; roots: string[] }>(
      "/api/experiments/prepare",
      { method: "POST", body: JSON.stringify(body) },
    ),
  inspectArtifact: (path: string) =>
    j<ArtifactSummary>(`/api/artifact/inspect?path=${encodeURIComponent(path)}`),

  startRun: (body: {
    config_path: string;
    resume?: boolean;
    finalize?: boolean;
    allow_simind_execution?: boolean;
    allow_unverified_runtime?: boolean;
    allow_large_simind_execution?: boolean;
  }) =>
    j<{ task_id: string; run_root: string }>("/api/run/start", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  finalizeRun: (runRoot: string) =>
    j<FinalizeResult>("/api/run/finalize", {
      method: "POST",
      body: JSON.stringify({ run_root: runRoot }),
    }),

  pause: (taskId: string) => j<TaskState>(`/api/tasks/${taskId}/pause`, { method: "POST" }),
  task: (taskId: string, cursor = 0) => j<TaskState>(`/api/tasks/${taskId}?cursor=${cursor}`),
  tasks: () => j<{ tasks: TaskState[] }>("/api/tasks"),

  previewPhantom: (body: {
    phantom_config?: Record<string, any>;
    case_index?: number;
    seed?: number | null;
    overrides?: Record<string, any>;
  }) =>
    j<{ preview_id: string; config_digest: string; geometry: PreviewGeometry; summary: PreviewSummary }>("/api/preview/phantom", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  previewProbe: (previewId: string, voxel: { x: number; y: number; z: number }) =>
    j<PhantomProbe>(
      `/api/preview/phantom/${previewId}/probe?x=${voxel.x}&y=${voxel.y}&z=${voxel.z}`,
    ),
  previewMesh: (previewId: string, structure: "all" | "liver" | "tumors" = "all") =>
    j<PreviewMesh>(
      `/api/preview/phantom/${previewId}/mesh?structure=${structure}`,
    ),

  fsList: (path = "") => j<any>(`/api/fs/list?path=${encodeURIComponent(path)}`),
  fsValidate: (path: string, kind: string) =>
    j<{ path: string; kind: string; valid: boolean; detail: string }>(
      `/api/fs/validate?path=${encodeURIComponent(path)}&kind=${kind}`
    ),
  fsPick: (kind: "simind_exe" | "smc" | "runs_root" | "export_root", initialPath = "") =>
    j<{ cancelled: boolean; path: string | null }>("/api/fs/pick", {
      method: "POST",
      body: JSON.stringify({ kind, initial_path: initialPath }),
    }),
};

/* image URLs (server-rendered PNG) */
export const img = {
  slice: (pid: string, plane: string, index: number, layer = "activity", overlay = "liver_and_tumors") =>
    `${API}/api/preview/phantom/${pid}/slice?plane=${plane}&index=${index}&layer=${layer}&overlay=${overlay}`,
  mip: (pid: string, plane: string, layer = "activity", overlay = "liver_and_tumors") =>
    `${API}/api/preview/phantom/${pid}/mip?plane=${plane}&layer=${layer}&overlay=${overlay}`,
  projection: (root: string, cs: string, view: number, layer = "expectation") =>
    `${API}/api/run/projection?root=${encodeURIComponent(root)}&case=${cs}&view=${view}&layer=${layer}`,
  sinogram: (root: string, cs: string, row: number, layer = "expectation") =>
    `${API}/api/run/sinogram?root=${encodeURIComponent(root)}&case=${cs}&row=${row}&layer=${layer}`,
  artifactProjection: (path: string, view: number) =>
    `${API}/api/artifact/projection?path=${encodeURIComponent(path)}&view=${view}`,
  artifactSinogram: (path: string, row: number) =>
    `${API}/api/artifact/sinogram?path=${encodeURIComponent(path)}&row=${row}`,
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
