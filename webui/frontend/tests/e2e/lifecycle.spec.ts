import { expect, test, type Page, type Route } from "@playwright/test";

const RUN_ID = "e2e-lifecycle-run";
const RUN_ROOT = `D:\\PFE-U\\PAR-S-Generator\\runs\\${RUN_ID}`;
const CONFIG_PATH = `D:\\PFE-U\\PAR-S-Generator\\runs\\${RUN_ID}.config.json`;

async function installLifecycleFixture(page: Page) {
  let canonical: Record<string, unknown> | null = null;
  let taskStatus: "running" | "paused" | "finished" = "running";
  let taskId = "task-1";
  let sealed = false;

  const stagePayload = () => ({
    finalized: sealed,
    stages: ["generate", "phantom_qc", "export", "simind_plan", "expectation", "projection_qc", "observation", "package", "finalize"].map((stage) => ({
      stage,
      status: stage === "finalize" ? (sealed ? "passed" : "pending") : "passed",
    })),
  });
  const caseRecord = {
    case_id: "case_0001",
    split: "train",
    seed: 43,
    expectation: { backend: "deterministic_mock_not_simind", rr_seed: 930001 },
    observation: { sum: 2_050_000, angular_cv: 0.41 },
    qc: { phantom: { status: "passed" }, projection: { status: "passed" }, observation: { status: "passed" } },
  };
  const manifest = {
    dataset_id: RUN_ID,
    scope: "synthetic_liver_spect",
    case_count: 1,
    projection_orientation: "raw[:,::-1,:]",
    files: [{ path: "cases.jsonl", bytes: 512, sha256: "a".repeat(64) }],
  };
  const task = () => ({
    task_id: taskId,
    run_id: RUN_ID,
    run_root: RUN_ROOT,
    status: taskStatus,
    error: null,
    result: taskStatus === "finished" ? { finalized: false } : null,
    event_count: 0,
    events: [],
    cursor: 0,
  });

  await page.route("**/api/**", async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    if (method === "GET" && path === "/api/runs") {
      return route.fulfill({ json: { runs_root: "runs", runs: canonical ? [{ run_id: RUN_ID, root: RUN_ROOT, config_path: CONFIG_PATH, mode: "prepare", case_count: 1, finalized: sealed, stages: Object.fromEntries(stagePayload().stages.map((item) => [item.stage, item.status])) }] : [] } });
    }
    if (method === "GET" && path === "/api/tasks") return route.fulfill({ json: { tasks: sealed || !canonical ? [] : [task()] } });
    if (method === "GET" && path === "/api/run") return route.fulfill({ json: { effective_config: canonical } });
    if (method === "POST" && path === "/api/runs") {
      const body = request.postDataJSON() as Record<string, any>;
      canonical = {
        run_id: RUN_ID,
        runs_root: body.runs_root,
        schema_version: "windows_v1",
        generation_profile: "hybrid_v2_limited_activity_v1",
        runtime_backend: "windows_native",
        simulation_mode: body.mode,
        windows_v1: body.windows_v1,
        simind_exe: body.simind_exe,
        smc_file: body.smc_file,
        nn_multiplier: body.nn_multiplier,
        max_simind_workers: body.max_simind_workers,
        phantom: { n_cases: 1, volume_shape: [128, 128, 128], voxel_size_mm: 4.42 },
      };
      return route.fulfill({ json: { config_path: CONFIG_PATH, config: canonical } });
    }
    if (method === "POST" && path === "/api/run/start") {
      const body = request.postDataJSON() as { resume?: boolean };
      if (body.resume) {
        taskId = "task-2";
        taskStatus = "finished";
      } else {
        taskStatus = "running";
      }
      return route.fulfill({ json: { task_id: taskId, run_root: RUN_ROOT } });
    }
    if (method === "GET" && path.startsWith("/api/tasks/")) return route.fulfill({ json: task() });
    if (method === "POST" && path.endsWith("/pause")) {
      taskStatus = "paused";
      return route.fulfill({ json: task() });
    }
    if (method === "GET" && path === "/api/run/stages") return route.fulfill({ json: stagePayload() });
    if (method === "GET" && path === "/api/run/cases") return route.fulfill({ json: { total: 1, offset: 0, cases: [caseRecord] } });
    if (method === "GET" && path === "/api/run/case-evidence") return route.fulfill({ json: { case: caseRecord, effective: { projection_shape: [60, 128, 128], nn_multiplier: 10, detector_matrix: [160, 208], voxel_size_mm: 4.42, source_activity_mbq: 60, smc_index25_activity_time: 1704 }, backend: "deterministic_mock_not_simind", rr_seed: 930001, res_excerpt: "MOCK FIXTURE" } });
    if (method === "GET" && path === "/api/run/manifest") return route.fulfill({ json: manifest });
    if (method === "GET" && path === "/api/run/splits") return route.fulfill({ json: { splits: { train: ["case_0001"], val: [], test: [] } } });
    if (method === "POST" && path === "/api/run/finalize") {
      sealed = true;
      return route.fulfill({ json: { finalized: true, manifest_path: `${RUN_ROOT}\\dataset_manifest.json`, package_sha256: "b".repeat(64) } });
    }
    return route.continue();
  });
}

test("plan, run, pause/resume, review and explicit seal form one recoverable lifecycle", async ({ page }) => {
  test.setTimeout(180_000);
  await installLifecycleFixture(page);
  await page.addInitScript(() => {
    localStorage.setItem("pars.locale", "en");
    localStorage.setItem("pars.theme", "light");
    if (!sessionStorage.getItem("pars-e2e-initialized")) {
      localStorage.removeItem("pars.workspace.windows-v1");
      sessionStorage.setItem("pars-e2e-initialized", "1");
    }
  });
  await page.goto("/");
  await page.getByRole("textbox", { name: /^Run ID/ }).fill(RUN_ID);
  await page.getByRole("spinbutton", { name: /^Positive cases/ }).fill("1");
  await page.getByRole("button", { name: "Continue to Phantom" }).click();
  await expect(page.getByRole("img", { name: /· activity/ })).toHaveCount(3, { timeout: 90_000 });

  await page.locator("button.lifecycle-link").nth(2).click();
  await page.getByRole("radio", { name: /Mock/ }).check();
  await page.getByRole("button", { name: "Run preflight" }).click();
  await expect(page.getByText("All plan sections are ready to lock")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "Lock run plan" }).click();
  await expect(page.getByRole("heading", { name: "Run center" })).toBeVisible();

  await page.getByRole("button", { name: "Start run" }).click();
  await expect(page.getByRole("button", { name: "Pause" })).toBeEnabled();
  await page.getByRole("button", { name: "Pause" }).click();
  await expect(page.getByRole("status")).toHaveText("Paused");
  await page.getByRole("button", { name: "Resume" }).click();
  await expect(page.getByText(/Run finished/)).toBeVisible();

  await page.locator("button.lifecycle-link").nth(4).click();
  await expect(page.getByRole("button", { name: "Continue to Seal" })).toBeEnabled();
  await page.getByRole("button", { name: "Continue to Seal" }).click();
  await page.getByLabel("Type the run ID").fill(RUN_ID);
  await page.getByLabel(/cannot be edited after sealing/).check();
  await page.getByRole("button", { name: "Seal dataset" }).click();
  await expect(page.getByRole("heading", { name: "Dataset sealed" })).toBeVisible();
  await expect(page.getByText("b".repeat(64))).toBeVisible();

  await page.reload();
  await expect(page.getByRole("heading", { name: "Dataset sealed" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Fork as new run" })).toBeVisible();
});

test("offline bootstrap provides an in-place retry", async ({ page }) => {
  let offline = true;
  await page.route("**/api/{health,protocol,defaults}", (route) => offline ? route.abort("connectionrefused") : route.continue());
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("local service cannot be reached");
  offline = false;
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("Service online")).toBeVisible();
});

test("a stale saved run pointer is explained and can be cleared", async ({ page }) => {
  await page.route("**/api/runs**", (route) => route.fulfill({ json: { runs_root: "runs", runs: [] } }));
  await page.route("**/api/tasks", (route) => route.fulfill({ json: { tasks: [] } }));
  await page.addInitScript(() => {
    localStorage.setItem("pars.locale", "en");
    localStorage.setItem("pars.workspace.windows-v1", JSON.stringify({
      version: 4,
      view: "run",
      draft: { identity: { runId: "missing-run", runsRoot: "runs", cohortMode: "positive_only", positiveCases: 1, negativeCases: 0, cases: 1 }, simulation: { mode: "prepare" }, phantom: {}, protocol: {}, observation: {} },
      dirty: { identity: false, protocol: false, phantom: false, simulation: false, observation: false },
      activeRun: {
        runId: "missing-run",
        runRoot: "D:\\missing\\run",
        configPath: "D:\\missing\\run.config.json",
        taskId: null,
        locked: true,
      },
    }));
  });
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("saved run no longer exists");
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("saved run no longer exists")).toHaveCount(0);
});
