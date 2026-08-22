import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  type Dispatch,
  type ReactNode,
} from "react";

export const WORKSPACE_STORAGE_KEY = "pars.workspace.v3";
export const LEGACY_WORKSPACE_STORAGE_KEY = "pars.workspace.v2";
export const WORKSPACE_SCHEMA_VERSION = 3 as const;

export type RunMode = "prepare" | "mock" | "execute";

export type StageStatus =
  | "pending"
  | "running"
  | "passed"
  | "prepared"
  | "skipped"
  | "paused"
  | "failed";

export type RunLifecycleStatus =
  | "draft"
  | "ready"
  | "running"
  | "pause-requested"
  | "paused"
  | "review"
  | "blocked"
  | "sealed"
  | "failed";

export type WorkspaceView = "protocol" | "phantom" | "simulation" | "run" | "review" | "seal";

export type PlanSection = "protocol" | "phantom" | "simulation";
export type PlanSectionStatus = "incomplete" | "warning" | "ready" | "locked";

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type JsonObject = { [key: string]: JsonValue };
export type DraftObject = { [key: string]: JsonValue | undefined };

export interface RunIdentityDraft {
  runId: string;
  runsRoot: string;
  cases: number;
}

/** Protocol fields map to top-level PipelineConfig keys. */
export interface ProtocolDraft extends DraftObject {
  protocol_label?: string;
  protocol_status?: string;
  source_activity_mbq?: number;
  exposure_time_s_per_projection?: number;
  smc_index25_activity_time?: number;
  activity_time_contract_status?: string;
  /** UI-only acknowledgement that validated defaults are being overridden. */
  advanced_override?: boolean;
}

/**
 * Phantom keys intentionally use the PipelineConfig/PhantomConfig snake_case
 * vocabulary. The same object can therefore be sent to the preview endpoint
 * and nested under config_overrides.phantom without a second mapping layer.
 */
export interface PhantomDraft extends DraftObject {
  volume_shape?: JsonValue;
  voxel_size_mm?: number;
  target_left_ratio?: number;
  scale_jitter?: number;
  rot_jitter_deg?: number;
  tumor_count_min?: number;
  tumor_count_max?: number;
  tumor_size_bins_mm?: JsonValue;
  tumor_probs?: JsonValue;
  tumor_contrast_min?: number;
  tumor_contrast_max?: number;
  tumor_min_liver_margin_mm?: number;
  global_seed?: number;
  use_global_seed?: boolean;
}

/** Simulation settings map to top-level PipelineConfig keys. */
export interface SimulationDraft extends DraftObject {
  mode: RunMode;
  simind_exe?: string;
  smc_file?: string;
  nn_multiplier?: number;
  max_simind_workers?: number;
  simind_seed_base?: number;
  simind_overrides?: JsonValue;
}

/** Observation settings also map to top-level PipelineConfig keys. */
export interface ObservationDraft extends DraftObject {
  create_poisson_observation?: boolean;
  observation_policy?: string;
  observation_scale?: number;
  observation_seed_offset?: number;
  observation_protocol_status?: string;
  empirical_reference_counts?: JsonValue;
  empirical_angular_cv_range?: JsonValue;
}

export interface DraftRunConfig {
  identity: RunIdentityDraft;
  protocol: ProtocolDraft;
  phantom: PhantomDraft;
  simulation: SimulationDraft;
  observation: ObservationDraft;
}

export interface ActiveRunReference {
  runId: string | null;
  runRoot: string | null;
  configPath: string | null;
  taskId: string | null;
  locked: boolean;
  finalized: boolean;
  canonicalConfig: JsonObject | null;
}

export interface DraftDirtyState {
  identity: boolean;
  protocol: boolean;
  phantom: boolean;
  simulation: boolean;
  observation: boolean;
}

export interface PlanReadinessState {
  sections: Record<PlanSection, PlanSectionStatus>;
  previewConfigDigest: string | null;
  preflightConfigDigest: string | null;
  errors: string[];
  warnings: string[];
}

export interface WorkspaceState {
  version: typeof WORKSPACE_SCHEMA_VERSION;
  draft: DraftRunConfig;
  dirty: DraftDirtyState;
  plan: PlanReadinessState;
  view: WorkspaceView;
  lifecycle: RunLifecycleStatus;
  stages: Record<string, StageStatus>;
  activeRun: ActiveRunReference;
  offline: boolean;
  /** Raw service detail. Render verbatim in a diagnostic disclosure. */
  rawError: string | null;
}

export type ServerTaskStatus = "running" | "paused" | "finished" | "failed";

export type WorkspaceAction =
  | { type: "defaults/received"; defaults: Record<string, unknown> }
  | { type: "draft/replace"; draft: DraftRunConfig }
  | { type: "draft/identity"; patch: Partial<RunIdentityDraft> }
  | { type: "draft/protocol"; patch: Partial<ProtocolDraft> }
  | { type: "draft/phantom"; patch: Partial<PhantomDraft> }
  | { type: "draft/simulation"; patch: Partial<SimulationDraft> }
  | { type: "draft/observation"; patch: Partial<ObservationDraft> }
  | {
      type: "plan/section";
      section: PlanSection;
      status: PlanSectionStatus;
    }
  | { type: "plan/preview"; configDigest: string | null }
  | {
      type: "plan/preflight";
      configDigest: string | null;
      errors?: string[];
      warnings?: string[];
    }
  | { type: "view/set"; view: WorkspaceView }
  | {
      type: "run/created";
      configPath: string;
      canonicalConfig: Record<string, unknown>;
      runRoot?: string | null;
    }
  | {
      type: "run/restored";
      runId: string;
      runRoot: string;
      configPath?: string | null;
      finalized?: boolean;
      canonicalConfig?: Record<string, unknown> | null;
    }
  | { type: "run/started"; taskId: string; runRoot: string }
  | {
      type: "task/restored";
      taskId: string;
      status: ServerTaskStatus;
      runRoot?: string;
      finalized?: boolean;
      error?: string | null;
    }
  | { type: "run/pause-requested" }
  | { type: "run/stages"; stages: Record<string, string> }
  | { type: "run/sealed" }
  | { type: "run/fork"; runId: string }
  | { type: "run/clear" }
  | { type: "lifecycle/set"; lifecycle: RunLifecycleStatus }
  | { type: "connection/set"; offline: boolean }
  | { type: "error/set-raw"; error: string | null }
  | { type: "workspace/reset"; defaults?: Record<string, unknown> };

export interface CreateRunRequest {
  run_id: string;
  runs_root: string;
  cases: number;
  mode: RunMode;
  config_overrides: JsonObject;
}

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem?(key: string): void;
}

const VIEWS: readonly WorkspaceView[] = ["protocol", "phantom", "simulation", "run", "review", "seal"];
const MODES: readonly RunMode[] = ["prepare", "mock", "execute"];
const STAGE_STATUSES: readonly StageStatus[] = [
  "pending",
  "running",
  "passed",
  "prepared",
  "skipped",
  "paused",
  "failed",
];
const EMPTY_ACTIVE_RUN: ActiveRunReference = {
  runId: null,
  runRoot: null,
  configPath: null,
  taskId: null,
  locked: false,
  finalized: false,
  canonicalConfig: null,
};

const CLEAN_DRAFT: DraftDirtyState = {
  identity: false,
  protocol: false,
  phantom: false,
  simulation: false,
  observation: false,
};

const EMPTY_PLAN: PlanReadinessState = {
  sections: { protocol: "incomplete", phantom: "incomplete", simulation: "incomplete" },
  previewConfigDigest: null,
  preflightConfigDigest: null,
  errors: [],
  warnings: [],
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isOneOf<T extends string>(value: unknown, choices: readonly T[]): value is T {
  return typeof value === "string" && choices.includes(value as T);
}

function finiteNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function positiveInteger(value: unknown): number | undefined {
  const number = finiteNumber(value);
  return number !== undefined && number >= 1 ? Math.floor(number) : undefined;
}

function jsonValue(value: unknown): JsonValue | undefined {
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number") return Number.isFinite(value) ? value : undefined;
  if (Array.isArray(value)) {
    const items: JsonValue[] = [];
    for (const item of value) {
      const parsed = jsonValue(item);
      if (parsed === undefined) return undefined;
      items.push(parsed);
    }
    return items;
  }
  if (!isRecord(value)) return undefined;
  const result: JsonObject = {};
  for (const [key, item] of Object.entries(value)) {
    const parsed = jsonValue(item);
    if (parsed !== undefined) result[key] = parsed;
  }
  return result;
}

function jsonObject(value: unknown): JsonObject {
  const parsed = jsonValue(value);
  return isRecord(parsed) ? (parsed as JsonObject) : {};
}

function stringValue(value: unknown, fallback: string): string {
  return typeof value === "string" ? value : fallback;
}

function booleanValue(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function runMode(value: unknown, fallback: RunMode = "prepare"): RunMode {
  return isOneOf(value, MODES) ? value : fallback;
}

function knownPipelineFields(source: Record<string, unknown>, keys: readonly string[]): DraftObject {
  const result: DraftObject = {};
  for (const key of keys) {
    const parsed = jsonValue(source[key]);
    if (parsed !== undefined) result[key] = parsed;
  }
  return result;
}

const SIMULATION_FIELDS = [
  "simind_exe",
  "smc_file",
  "nn_multiplier",
  "max_simind_workers",
  "simind_seed_base",
  "simind_overrides",
] as const;

const OBSERVATION_FIELDS = [
  "create_poisson_observation",
  "observation_policy",
  "observation_scale",
  "observation_seed_offset",
  "observation_protocol_status",
  "empirical_reference_counts",
  "empirical_angular_cv_range",
] as const;

const PROTOCOL_FIELDS = [
  "protocol_label",
  "protocol_status",
  "source_activity_mbq",
  "exposure_time_s_per_projection",
  "smc_index25_activity_time",
  "activity_time_contract_status",
] as const;

function draftFromPipelineConfig(
  defaults: Record<string, unknown> | null | undefined,
  useNewRunObservationDefaults: boolean,
): DraftRunConfig {
  const source = defaults ?? {};
  const phantom = jsonObject(source.phantom);
  const cases = positiveInteger(phantom.n_cases) ?? 10;
  const runId = stringValue(source.run_id, "liver-spect-run");
  const observation = knownPipelineFields(source, OBSERVATION_FIELDS) as ObservationDraft;

  // POST /api/runs deliberately starts new datasets with the empirical
  // observation contract, while raw PipelineConfig defaults describe the
  // lower-level fixed-scale mode. Match the resource-creation contract here.
  if (useNewRunObservationDefaults) {
    observation.create_poisson_observation = true;
    observation.observation_policy = "empirical_total_counts";
    observation.observation_protocol_status = "empirical_protocol_matching";
    observation.observation_scale ??= 1;
  }

  return {
    identity: {
      runId: runId === "unnamed" ? "liver-spect-run" : runId,
      runsRoot: stringValue(source.runs_root, "runs"),
      cases,
    },
    protocol: knownPipelineFields(source, PROTOCOL_FIELDS) as ProtocolDraft,
    phantom: phantom as PhantomDraft,
    simulation: {
      mode: runMode(source.simulation_mode),
      ...knownPipelineFields(source, SIMULATION_FIELDS),
    } as SimulationDraft,
    observation,
  };
}

/** Convert /api/defaults into the editable defaults for a newly created run. */
export function draftFromDefaults(defaults?: Record<string, unknown> | null): DraftRunConfig {
  return draftFromPipelineConfig(defaults, true);
}

/**
 * Return the exact PhantomConfig-shaped payload used by both preview and run
 * creation. Only output routing is pinned to the run-isolated pipeline path.
 */
export function toPhantomConfig(draft: DraftRunConfig): JsonObject {
  return {
    ...jsonObject(draft.phantom),
    n_cases: Math.max(1, Math.floor(draft.identity.cases)),
    output_dir: "managed_by_pipeline",
  };
}

/** Map all four draft sections onto the existing POST /api/runs contract. */
export function toCreateRunRequest(draft: DraftRunConfig): CreateRunRequest {
  const simulation = jsonObject(draft.simulation);
  delete simulation.mode;
  const configOverrides: JsonObject = {
    phantom: toPhantomConfig(draft),
    simulation_mode: draft.simulation.mode,
    ...jsonObject({
      ...draft.protocol,
      advanced_override: undefined,
    }),
    ...simulation,
    ...jsonObject(draft.observation),
  };

  return {
    run_id: draft.identity.runId.trim(),
    runs_root: draft.identity.runsRoot.trim() || "runs",
    cases: Math.max(1, Math.floor(draft.identity.cases)),
    mode: draft.simulation.mode,
    config_overrides: configOverrides,
  };
}

function canonicalDraft(config: Record<string, unknown>, previous: DraftRunConfig): DraftRunConfig {
  const canonical = draftFromPipelineConfig(config, false);
  return {
    ...canonical,
    identity: {
      runId: stringValue(config.run_id, previous.identity.runId),
      runsRoot: stringValue(config.runs_root, previous.identity.runsRoot),
      cases: canonical.identity.cases,
    },
  };
}

export function createInitialWorkspaceState(defaults?: Record<string, unknown> | null): WorkspaceState {
  return {
    version: WORKSPACE_SCHEMA_VERSION,
    draft: draftFromDefaults(defaults),
    dirty: { ...CLEAN_DRAFT },
    plan: {
      ...EMPTY_PLAN,
      sections: { ...EMPTY_PLAN.sections },
      errors: [],
      warnings: [],
    },
    view: "protocol",
    lifecycle: "draft",
    stages: {},
    activeRun: { ...EMPTY_ACTIVE_RUN },
    offline: false,
    rawError: null,
  };
}

function unlocked(state: WorkspaceState): boolean {
  return !state.activeRun.locked;
}

function applyDefaults(state: WorkspaceState, defaults: Record<string, unknown>): WorkspaceState {
  // A created/restored run adopts its canonical server config. Late-arriving
  // defaults must never replace that locked snapshot.
  if (state.activeRun.locked) return state;
  const incoming = draftFromDefaults(defaults);
  return {
    ...state,
      draft: {
        identity: state.dirty.identity ? state.draft.identity : incoming.identity,
        protocol: state.dirty.protocol ? state.draft.protocol : incoming.protocol,
        phantom: state.dirty.phantom ? state.draft.phantom : incoming.phantom,
      simulation: state.dirty.simulation ? state.draft.simulation : incoming.simulation,
      observation: state.dirty.observation ? state.draft.observation : incoming.observation,
    },
  };
}

function normalizeStages(stages: Record<string, string>): Record<string, StageStatus> {
  const result: Record<string, StageStatus> = {};
  for (const [stage, status] of Object.entries(stages)) {
    if (isOneOf(status, STAGE_STATUSES)) result[stage] = status;
  }
  return result;
}

function lifecycleForTask(status: ServerTaskStatus, finalized = false): RunLifecycleStatus {
  if (finalized) return "sealed";
  if (status === "running") return "running";
  if (status === "paused") return "paused";
  if (status === "failed") return "failed";
  return "review";
}

function isSameRun(state: WorkspaceState, runId: string, runRoot: string): boolean {
  const current = state.activeRun;
  if (current.runRoot !== null) {
    return current.runRoot === runRoot && (current.runId === null || current.runId === runId);
  }
  return current.runId !== null && current.runId === runId;
}

function lifecycleForStages(
  state: WorkspaceState,
  stages: Record<string, StageStatus>,
): RunLifecycleStatus {
  if (state.activeRun.finalized || stages.finalize === "passed") return "sealed";

  const hasFailure = Object.values(stages).some((status) => status === "failed");
  if (hasFailure) return state.lifecycle === "failed" ? "failed" : "blocked";

  // Stage polling can race with task recovery. It must not demote a live or
  // safely paused task merely because package evidence is already present.
  if (
    state.lifecycle === "running" ||
    state.lifecycle === "pause-requested" ||
    state.lifecycle === "paused" ||
    state.lifecycle === "failed" ||
    state.lifecycle === "blocked"
  ) {
    return state.lifecycle;
  }

  if (stages.package === "passed") return "review";
  return state.lifecycle;
}

export function workspaceReducer(state: WorkspaceState, action: WorkspaceAction): WorkspaceState {
  switch (action.type) {
    case "defaults/received":
      return applyDefaults(state, action.defaults);
    case "draft/replace":
      if (!unlocked(state)) return state;
      return {
        ...state,
        draft: action.draft,
        dirty: { identity: true, protocol: true, phantom: true, simulation: true, observation: true },
        plan: {
          ...EMPTY_PLAN,
          sections: { ...EMPTY_PLAN.sections },
          errors: [],
          warnings: [],
        },
        lifecycle: "draft",
      };
    case "draft/identity":
      if (!unlocked(state)) return state;
      return {
        ...state,
        draft: { ...state.draft, identity: { ...state.draft.identity, ...action.patch } },
        dirty: { ...state.dirty, identity: true },
        plan: {
          ...state.plan,
          sections: { ...state.plan.sections, protocol: "incomplete" },
          preflightConfigDigest: null,
        },
        lifecycle: "draft",
      };
    case "draft/protocol":
      if (!unlocked(state)) return state;
      return {
        ...state,
        draft: { ...state.draft, protocol: { ...state.draft.protocol, ...action.patch } },
        dirty: { ...state.dirty, protocol: true },
        plan: {
          ...state.plan,
          sections: { ...state.plan.sections, protocol: "incomplete" },
          preflightConfigDigest: null,
        },
        lifecycle: "draft",
      };
    case "draft/phantom":
      if (!unlocked(state)) return state;
      return {
        ...state,
        draft: { ...state.draft, phantom: { ...state.draft.phantom, ...action.patch } },
        dirty: { ...state.dirty, phantom: true },
        plan: {
          ...state.plan,
          sections: { ...state.plan.sections, phantom: "incomplete" },
          previewConfigDigest: null,
          preflightConfigDigest: null,
        },
        lifecycle: "draft",
      };
    case "draft/simulation":
      if (!unlocked(state)) return state;
      return {
        ...state,
        draft: { ...state.draft, simulation: { ...state.draft.simulation, ...action.patch } },
        dirty: { ...state.dirty, simulation: true },
        plan: {
          ...state.plan,
          sections: { ...state.plan.sections, simulation: "incomplete" },
          preflightConfigDigest: null,
        },
        lifecycle: "draft",
      };
    case "draft/observation":
      if (!unlocked(state)) return state;
      return {
        ...state,
        draft: { ...state.draft, observation: { ...state.draft.observation, ...action.patch } },
        dirty: { ...state.dirty, observation: true },
        plan: {
          ...state.plan,
          sections: { ...state.plan.sections, simulation: "incomplete" },
          preflightConfigDigest: null,
        },
        lifecycle: "draft",
      };
    case "plan/section":
      return {
        ...state,
        plan: {
          ...state.plan,
          sections: { ...state.plan.sections, [action.section]: action.status },
        },
      };
    case "plan/preview":
      return {
        ...state,
        plan: {
          ...state.plan,
          sections: {
            ...state.plan.sections,
            phantom: action.configDigest ? "ready" : "incomplete",
          },
          previewConfigDigest: action.configDigest,
        },
      };
    case "plan/preflight":
      return {
        ...state,
        plan: {
          ...state.plan,
          sections: {
            ...state.plan.sections,
            simulation: action.errors?.length
              ? "incomplete"
              : action.warnings?.length
                ? "warning"
                : action.configDigest
                  ? "ready"
                  : "incomplete",
          },
          preflightConfigDigest: action.configDigest,
          errors: [...(action.errors ?? [])],
          warnings: [...(action.warnings ?? [])],
        },
      };
    case "view/set":
      return { ...state, view: action.view };
    case "run/created": {
      const config = jsonObject(action.canonicalConfig);
      const draft = canonicalDraft(action.canonicalConfig, state.draft);
      return {
        ...state,
        draft,
        dirty: { ...CLEAN_DRAFT },
        plan: {
          ...state.plan,
          sections: { protocol: "locked", phantom: "locked", simulation: "locked" },
          errors: [],
        },
        lifecycle: "ready",
        activeRun: {
          runId: draft.identity.runId,
          runRoot: action.runRoot ?? null,
          configPath: action.configPath,
          taskId: null,
          locked: true,
          finalized: false,
          canonicalConfig: config,
        },
        rawError: null,
      };
    }
    case "run/restored": {
      const sameRun = isSameRun(state, action.runId, action.runRoot);
      const finalized = action.finalized ?? (sameRun ? state.activeRun.finalized : false);
      const config = action.canonicalConfig
        ? jsonObject(action.canonicalConfig)
        : sameRun
          ? state.activeRun.canonicalConfig
          : null;
      const draft = action.canonicalConfig
        ? canonicalDraft(action.canonicalConfig, state.draft)
        : {
            ...state.draft,
            identity: { ...state.draft.identity, runId: action.runId },
          };
      return {
        ...state,
        draft,
        dirty: action.canonicalConfig ? { ...CLEAN_DRAFT } : state.dirty,
        plan: {
          ...state.plan,
          sections: { protocol: "locked", phantom: "locked", simulation: "locked" },
          errors: [],
        },
        lifecycle: finalized ? "sealed" : sameRun ? state.lifecycle : "ready",
        activeRun: {
          runId: action.runId,
          runRoot: action.runRoot,
          configPath:
            action.configPath === undefined
              ? sameRun
                ? state.activeRun.configPath
                : null
              : action.configPath,
          taskId: sameRun ? state.activeRun.taskId : null,
          locked: true,
          finalized,
          canonicalConfig: config,
        },
        rawError: sameRun ? state.rawError : null,
      };
    }
    case "run/started":
      return {
        ...state,
        lifecycle: "running",
        activeRun: { ...state.activeRun, taskId: action.taskId, runRoot: action.runRoot, locked: true },
        rawError: null,
      };
    case "task/restored": {
      const finalized = action.finalized ?? state.activeRun.finalized;
      const lifecycle =
        action.status === "running" && state.lifecycle === "pause-requested"
          ? "pause-requested"
          : lifecycleForTask(action.status, finalized);
      return {
        ...state,
        lifecycle,
        activeRun: {
          ...state.activeRun,
          taskId: action.taskId,
          runRoot: action.runRoot ?? state.activeRun.runRoot,
          finalized,
          locked: true,
        },
        rawError: action.error ?? (action.status === "failed" ? state.rawError : null),
      };
    }
    case "run/pause-requested":
      return state.lifecycle === "running" ? { ...state, lifecycle: "pause-requested" } : state;
    case "run/stages": {
      const stages = normalizeStages(action.stages);
      const finalized = state.activeRun.finalized || stages.finalize === "passed";
      return {
        ...state,
        stages,
        lifecycle: lifecycleForStages(state, stages),
        activeRun: finalized ? { ...state.activeRun, finalized: true } : state.activeRun,
      };
    }
    case "run/sealed":
      return {
        ...state,
        lifecycle: "sealed",
        activeRun: { ...state.activeRun, finalized: true, locked: true },
        rawError: null,
      };
    case "run/fork":
      return {
        ...state,
        draft: { ...state.draft, identity: { ...state.draft.identity, runId: action.runId } },
        dirty: { ...state.dirty, identity: true },
        plan: {
          ...EMPTY_PLAN,
          sections: { ...EMPTY_PLAN.sections },
          errors: [],
          warnings: [],
        },
        lifecycle: "draft",
        stages: {},
        activeRun: { ...EMPTY_ACTIVE_RUN },
        rawError: null,
      };
    case "run/clear":
      return {
        ...state,
        lifecycle: "draft",
        stages: {},
        plan: {
          ...EMPTY_PLAN,
          sections: { ...EMPTY_PLAN.sections },
          errors: [],
          warnings: [],
        },
        activeRun: { ...EMPTY_ACTIVE_RUN },
        rawError: null,
      };
    case "lifecycle/set":
      return { ...state, lifecycle: action.lifecycle };
    case "connection/set":
      return { ...state, offline: action.offline };
    case "error/set-raw":
      return { ...state, rawError: action.error };
    case "workspace/reset":
      return createInitialWorkspaceState(action.defaults);
  }
}

function parseIdentity(value: unknown, fallback: RunIdentityDraft): RunIdentityDraft {
  if (!isRecord(value)) return fallback;
  return {
    runId: stringValue(value.runId, fallback.runId),
    runsRoot: stringValue(value.runsRoot, fallback.runsRoot),
    cases: positiveInteger(value.cases) ?? fallback.cases,
  };
}

function parseDraft(value: unknown, fallback: DraftRunConfig): DraftRunConfig {
  if (!isRecord(value)) return fallback;
  const simulation = jsonObject(value.simulation);
  const mode = runMode(simulation.mode, fallback.simulation.mode);
  simulation.mode = mode;
  return {
    identity: parseIdentity(value.identity, fallback.identity),
    protocol: jsonObject(value.protocol) as ProtocolDraft,
    phantom: jsonObject(value.phantom) as PhantomDraft,
    simulation: simulation as SimulationDraft,
    observation: jsonObject(value.observation) as ObservationDraft,
  };
}

function parseDirty(value: unknown): DraftDirtyState {
  if (!isRecord(value)) {
    return { identity: true, protocol: true, phantom: true, simulation: true, observation: true };
  }
  return {
    identity: booleanValue(value.identity, true),
    protocol: booleanValue(value.protocol, true),
    phantom: booleanValue(value.phantom, true),
    simulation: booleanValue(value.simulation, true),
    observation: booleanValue(value.observation, true),
  };
}

function parseActiveRun(value: unknown): ActiveRunReference {
  if (!isRecord(value)) return { ...EMPTY_ACTIVE_RUN };
  const runId = typeof value.runId === "string" ? value.runId : null;
  const runRoot = typeof value.runRoot === "string" ? value.runRoot : null;
  const configPath = typeof value.configPath === "string" ? value.configPath : null;
  const taskId = typeof value.taskId === "string" ? value.taskId : null;
  return {
    runId,
    runRoot,
    configPath,
    taskId,
    // Persisted values are only recovery pointers. Keep the selected draft
    // read-only until /api/runs, /api/tasks, and the ledger reconcile it.
    locked: Boolean(runId || runRoot || configPath),
    finalized: false,
    canonicalConfig: null,
  };
}

/** Parse persisted state field-by-field; malformed or old schemas are ignored. */
export function parseStoredWorkspace(serialized: string | null): WorkspaceState | null {
  if (!serialized) return null;
  try {
    const value: unknown = JSON.parse(serialized);
    if (!isRecord(value) || (value.version !== WORKSPACE_SCHEMA_VERSION && value.version !== 2)) return null;
    const fallback = createInitialWorkspaceState();
    const activeRun = parseActiveRun(value.activeRun);
    return {
      ...fallback,
      draft: parseDraft(value.draft, fallback.draft),
      dirty: parseDirty(value.dirty),
      plan: {
        ...EMPTY_PLAN,
        sections: { ...EMPTY_PLAN.sections },
        errors: [],
        warnings: [],
      },
      view: isOneOf(value.view, VIEWS) ? value.view : fallback.view,
      lifecycle: activeRun.locked ? "ready" : "draft",
      stages: {},
      activeRun,
    };
  } catch {
    return null;
  }
}

function safeStorage(): StorageLike | null {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
}

export function loadWorkspace(storage: StorageLike | null = safeStorage()): WorkspaceState | null {
  try {
    if (!storage) return null;
    return (
      parseStoredWorkspace(storage.getItem(WORKSPACE_STORAGE_KEY)) ??
      parseStoredWorkspace(storage.getItem(LEGACY_WORKSPACE_STORAGE_KEY))
    );
  } catch {
    return null;
  }
}

export function persistWorkspace(state: WorkspaceState, storage: StorageLike | null = safeStorage()): void {
  if (!storage) return;
  try {
    // Draft and navigation are local authority. Active-run fields are stored
    // only as lookup hints; runtime status and evidence are always reloaded.
    storage.setItem(
      WORKSPACE_STORAGE_KEY,
      JSON.stringify({
        version: WORKSPACE_SCHEMA_VERSION,
        draft: state.draft,
        dirty: state.dirty,
        view: state.view,
        activeRun: {
          runId: state.activeRun.runId,
          runRoot: state.activeRun.runRoot,
          configPath: state.activeRun.configPath,
          taskId: state.activeRun.taskId,
          locked: state.activeRun.locked,
        },
      }),
    );
  } catch {
    // Private browsing, disabled storage, and quota errors must not break a run.
  }
}

export interface WorkspaceContextValue {
  state: WorkspaceState;
  dispatch: Dispatch<WorkspaceAction>;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

export interface WorkspaceProviderProps {
  children: ReactNode;
  defaults?: Record<string, unknown> | null;
  storage?: StorageLike | null;
}

interface WorkspaceInitializer {
  defaults?: Record<string, unknown> | null;
  storage: StorageLike | null;
}

function initializeWorkspace({ defaults, storage }: WorkspaceInitializer): WorkspaceState {
  const persisted = loadWorkspace(storage);
  if (!persisted) return createInitialWorkspaceState(defaults);
  return defaults ? applyDefaults(persisted, defaults) : persisted;
}

export function WorkspaceProvider({ children, defaults, storage }: WorkspaceProviderProps) {
  const targetStorage = storage === undefined ? safeStorage() : storage;
  const [state, dispatch] = useReducer(
    workspaceReducer,
    { defaults, storage: targetStorage },
    initializeWorkspace,
  );

  useEffect(() => {
    if (defaults) dispatch({ type: "defaults/received", defaults });
  }, [defaults]);

  useEffect(() => {
    persistWorkspace(state, targetStorage);
  }, [state, targetStorage]);

  const value = useMemo(() => ({ state, dispatch }), [state]);
  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): WorkspaceContextValue {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("useWorkspace must be used inside WorkspaceProvider");
  return value;
}
