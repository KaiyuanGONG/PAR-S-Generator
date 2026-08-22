import { describe, expect, it } from "vitest";
import {
  createInitialWorkspaceState,
  draftFromDefaults,
  LEGACY_WORKSPACE_STORAGE_KEY,
  parseStoredWorkspace,
  persistWorkspace,
  toCreateRunRequest,
  toPhantomConfig,
  WORKSPACE_STORAGE_KEY,
  workspaceReducer,
  type StorageLike,
} from "./workspace";

class MemoryStorage implements StorageLike {
  private values = new Map<string, string>();
  getItem(key: string) { return this.values.get(key) ?? null; }
  setItem(key: string, value: string) { this.values.set(key, value); }
}

describe("RunWorkspace draft contract", () => {
  it("uses one PhantomConfig payload for preview and run creation", () => {
    const draft = draftFromDefaults({
      run_id: "study-01",
      runs_root: "runs",
      simulation_mode: "execute",
      simind_exe: "simind/simind.exe",
      smc_file: "simind/ge870_czt.smc",
      nn_multiplier: 12,
      protocol_label: "GE 870 controlled research protocol",
      source_activity_mbq: 60,
      exposure_time_s_per_projection: 28.4,
      smc_index25_activity_time: 1704,
      phantom: {
        n_cases: 7,
        target_left_ratio: 0.37,
        tumor_count_min: 2,
        tumor_count_max: 4,
      },
    });
    const request = toCreateRunRequest(draft);

    expect(request.config_overrides.phantom).toEqual(toPhantomConfig(draft));
    expect(request.config_overrides).toMatchObject({
      simulation_mode: "execute",
      simind_exe: "simind/simind.exe",
      smc_file: "simind/ge870_czt.smc",
      nn_multiplier: 12,
      create_poisson_observation: true,
      observation_policy: "empirical_total_counts",
      protocol_label: "GE 870 controlled research protocol",
      source_activity_mbq: 60,
      exposure_time_s_per_projection: 28.4,
      smc_index25_activity_time: 1704,
    });
    expect(request.cases).toBe(7);
  });

  it("locks canonical server config and requires an explicit fork before editing", () => {
    const initial = createInitialWorkspaceState();
    const created = workspaceReducer(initial, {
      type: "run/created",
      configPath: "runs/study.config.json",
      canonicalConfig: {
        run_id: "study",
        runs_root: "runs",
        simulation_mode: "mock",
        phantom: { n_cases: 3, target_left_ratio: 0.33 },
      },
    });
    const rejected = workspaceReducer(created, {
      type: "draft/phantom",
      patch: { target_left_ratio: 0.4 },
    });
    expect(rejected).toBe(created);

    const forked = workspaceReducer(created, { type: "run/fork", runId: "study-fork" });
    const edited = workspaceReducer(forked, {
      type: "draft/phantom",
      patch: { target_left_ratio: 0.4 },
    });
    expect(forked.activeRun.locked).toBe(false);
    expect(edited.draft.phantom.target_left_ratio).toBe(0.4);
  });

  it("persists lookup pointers but discards cached runtime evidence on reload", () => {
    const storage = new MemoryStorage();
    let state = createInitialWorkspaceState();
    state = workspaceReducer(state, {
      type: "run/restored",
      runId: "study",
      runRoot: "runs/study",
      configPath: "runs/study.config.json",
    });
    state = workspaceReducer(state, { type: "run/stages", stages: { generate: "passed" } });
    state = workspaceReducer(state, { type: "lifecycle/set", lifecycle: "review" });
    persistWorkspace(state, storage);

    const restored = parseStoredWorkspace(storage.getItem(WORKSPACE_STORAGE_KEY));
    expect(restored?.activeRun).toMatchObject({
      runId: "study",
      runRoot: "runs/study",
      configPath: "runs/study.config.json",
      locked: true,
    });
    expect(restored?.stages).toEqual({});
    expect(restored?.lifecycle).toBe("ready");
  });

  it("migrates a v2 draft into v3 without trusting cached runtime evidence", () => {
    const legacy = JSON.stringify({
      version: 2,
      draft: {
        identity: { runId: "legacy", runsRoot: "legacy-runs", cases: 5 },
        phantom: { target_left_ratio: 0.36 },
        simulation: { mode: "mock" },
        observation: { create_poisson_observation: true },
      },
      dirty: { identity: true, phantom: true, simulation: true, observation: true },
      view: "phantom",
      activeRun: { runId: "legacy", runRoot: "legacy-runs/legacy", taskId: "stale-task" },
    });
    const storage = new MemoryStorage();
    storage.setItem(LEGACY_WORKSPACE_STORAGE_KEY, legacy);

    const migrated = parseStoredWorkspace(storage.getItem(LEGACY_WORKSPACE_STORAGE_KEY));
    expect(migrated?.version).toBe(3);
    expect(migrated?.draft.identity).toEqual({ runId: "legacy", runsRoot: "legacy-runs", cases: 5 });
    expect(migrated?.draft.protocol).toEqual({});
    expect(migrated?.activeRun.taskId).toBe("stale-task");
    expect(migrated?.stages).toEqual({});
    expect(migrated?.plan.sections).toEqual({
      protocol: "incomplete",
      phantom: "incomplete",
      simulation: "incomplete",
    });
  });

  it("invalidates preview and preflight evidence when a draft section changes", () => {
    let state = createInitialWorkspaceState();
    state = workspaceReducer(state, { type: "plan/preview", configDigest: "preview-a" });
    state = workspaceReducer(state, {
      type: "plan/preflight",
      configDigest: "plan-a",
      warnings: [],
      errors: [],
    });
    expect(state.plan.sections.phantom).toBe("ready");
    expect(state.plan.sections.simulation).toBe("ready");

    state = workspaceReducer(state, {
      type: "draft/phantom",
      patch: { tumor_count_max: 6 },
    });
    expect(state.plan.previewConfigDigest).toBeNull();
    expect(state.plan.preflightConfigDigest).toBeNull();
    expect(state.plan.sections.phantom).toBe("incomplete");
  });

  it("keeps pause-requested until the server reaches a paused boundary", () => {
    let state = createInitialWorkspaceState();
    state = workspaceReducer(state, { type: "run/started", taskId: "task-1", runRoot: "runs/study" });
    state = workspaceReducer(state, { type: "run/pause-requested" });
    state = workspaceReducer(state, { type: "task/restored", taskId: "task-1", status: "running" });
    expect(state.lifecycle).toBe("pause-requested");

    state = workspaceReducer(state, { type: "task/restored", taskId: "task-1", status: "paused" });
    expect(state.lifecycle).toBe("paused");
  });

  it("keeps a recovered task and lifecycle when canonical detail restores the same run", () => {
    let state = createInitialWorkspaceState();
    state = workspaceReducer(state, {
      type: "run/restored",
      runId: "study",
      runRoot: "runs/study",
      configPath: "runs/study.config.json",
    });
    state = workspaceReducer(state, {
      type: "task/restored",
      taskId: "task-1",
      status: "running",
      runRoot: "runs/study",
    });
    state = workspaceReducer(state, { type: "run/pause-requested" });

    state = workspaceReducer(state, {
      type: "run/restored",
      runId: "study",
      runRoot: "runs/study",
      configPath: "runs/study.config.json",
      finalized: false,
      canonicalConfig: {
        run_id: "study",
        runs_root: "runs",
        simulation_mode: "prepare",
        phantom: { n_cases: 4 },
      },
    });

    expect(state.activeRun.taskId).toBe("task-1");
    expect(state.lifecycle).toBe("pause-requested");
    expect(state.activeRun.canonicalConfig?.run_id).toBe("study");
  });

  it("clears task state when restoring a different root even if the run ID is reused", () => {
    let state = createInitialWorkspaceState();
    state = workspaceReducer(state, {
      type: "run/restored",
      runId: "study",
      runRoot: "runs-a/study",
    });
    state = workspaceReducer(state, {
      type: "task/restored",
      taskId: "task-a",
      status: "running",
      runRoot: "runs-a/study",
    });

    state = workspaceReducer(state, {
      type: "run/restored",
      runId: "study",
      runRoot: "runs-b/study",
    });

    expect(state.activeRun.taskId).toBeNull();
    expect(state.activeRun.runRoot).toBe("runs-b/study");
    expect(state.lifecycle).toBe("ready");
  });

  it("derives review from a passed package when no task is active", () => {
    let state = createInitialWorkspaceState();
    state = workspaceReducer(state, {
      type: "run/restored",
      runId: "study",
      runRoot: "runs/study",
    });
    state = workspaceReducer(state, {
      type: "run/stages",
      stages: { generate: "passed", package: "passed", finalize: "pending" },
    });

    expect(state.activeRun.taskId).toBeNull();
    expect(state.lifecycle).toBe("review");
  });

  it("does not demote active task lifecycles when package has passed", () => {
    let state = createInitialWorkspaceState();
    state = workspaceReducer(state, {
      type: "run/restored",
      runId: "study",
      runRoot: "runs/study",
    });
    state = workspaceReducer(state, { type: "run/started", taskId: "task-1", runRoot: "runs/study" });
    state = workspaceReducer(state, { type: "run/stages", stages: { package: "passed" } });
    expect(state.lifecycle).toBe("running");

    state = workspaceReducer(state, { type: "run/pause-requested" });
    state = workspaceReducer(state, { type: "run/stages", stages: { package: "passed" } });
    expect(state.lifecycle).toBe("pause-requested");

    state = workspaceReducer(state, { type: "task/restored", taskId: "task-1", status: "paused" });
    state = workspaceReducer(state, { type: "run/stages", stages: { package: "passed" } });
    expect(state.lifecycle).toBe("paused");
  });
});
