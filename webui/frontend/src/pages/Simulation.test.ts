import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { createInitialWorkspaceState, toCreateRunRequest, workspaceReducer } from "../workspace";

beforeAll(() => {
  vi.stubGlobal("location", { port: "", origin: "http://localhost" });
});

afterAll(() => {
  vi.unstubAllGlobals();
});

describe("Simulation observation mode contract", () => {
  it("writes the runner-safe fixed-scale combination for expectation-only output", async () => {
    const { EXPECTATION_ONLY_OBSERVATION } = await import("./Simulation");
    const state = workspaceReducer(createInitialWorkspaceState(), {
      type: "draft/observation",
      patch: EXPECTATION_ONLY_OBSERVATION,
    });

    expect(toCreateRunRequest(state.draft).config_overrides).toMatchObject({
      create_poisson_observation: false,
      observation_policy: "fixed_scale",
      observation_protocol_status: "toy",
    });
  });

  it("restores the empirical policy and protocol status with observation output", async () => {
    const { EMPIRICAL_OBSERVATION, EXPECTATION_ONLY_OBSERVATION } = await import("./Simulation");
    let state = workspaceReducer(createInitialWorkspaceState(), {
      type: "draft/observation",
      patch: EXPECTATION_ONLY_OBSERVATION,
    });
    state = workspaceReducer(state, {
      type: "draft/observation",
      patch: EMPIRICAL_OBSERVATION,
    });

    expect(toCreateRunRequest(state.draft).config_overrides).toMatchObject({
      create_poisson_observation: true,
      observation_policy: "empirical_total_counts",
      observation_protocol_status: "empirical_protocol_matching",
    });
  });

  it("keeps expert edits in runner-valid atomic combinations", async () => {
    const { observationEnabledPatch, observationPolicyPatch } = await import("./Simulation");

    expect(observationEnabledPatch(false, "empirical_protocol_matching")).toEqual({
      create_poisson_observation: false,
      observation_policy: "fixed_scale",
      observation_protocol_status: "toy",
    });
    expect(observationPolicyPatch("empirical_total_counts", "research")).toEqual({
      create_poisson_observation: true,
      observation_policy: "empirical_total_counts",
      observation_protocol_status: "empirical_protocol_matching",
    });
    expect(observationPolicyPatch("fixed_scale", "research")).toEqual({
      observation_policy: "fixed_scale",
      observation_protocol_status: "research",
    });
  });
});
