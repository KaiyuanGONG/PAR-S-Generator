import { describe, expect, it } from "vitest";
import { createInitialWorkspaceState, toCreateRunRequest } from "../workspace";

describe("Simulation Windows v1 creation contract", () => {
  it("does not retain legacy observation controls in a production draft", () => {
    const state = createInitialWorkspaceState({
      create_poisson_observation: true,
      observation_policy: "empirical_total_counts",
      observation_protocol_status: "empirical_protocol_matching",
    });

    expect(state.draft).not.toHaveProperty("observation");
    expect(toCreateRunRequest(state.draft)).not.toHaveProperty("config_overrides");
  });
});
