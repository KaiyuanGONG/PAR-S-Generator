import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

const evidenceRoot = path.resolve(import.meta.dirname, "../../../..", "docs/evidence");

async function open(page: Page, locale: "en" | "zh" | "fr", theme: "light" | "dark") {
  await page.route("**/api/runs**", (route) => route.fulfill({ json: { runs_root: "runs", runs: [] } }));
  await page.route("**/api/tasks", (route) => route.fulfill({ json: { tasks: [] } }));
  await page.addInitScript(({ locale, theme }) => {
    localStorage.setItem("pars.locale", locale);
    localStorage.setItem("pars.theme", theme);
    localStorage.removeItem("pars.workspace.v3");
  }, { locale, theme });
  await page.goto("/");
  await page.evaluate(() => document.fonts.ready);
}

test("curated Web UI evidence", async ({ page }) => {
  await open(page, "en", "light");
  await expect(page.locator('.workspace-scroll[data-workspace="protocol"]')).toBeVisible();
  await page.screenshot({ path: path.join(evidenceRoot, "webui-protocol-light-en-1440x900.png") });

  await page.locator("button.lifecycle-link").filter({ hasText: "Phantom" }).click();
  const regenerate = page.getByRole("button", { name: /Regenerate preview/i });
  await regenerate.click();
  await expect(page.locator(".surface-canvas canvas")).toBeVisible({ timeout: 30_000 });
  await expect(regenerate).toBeEnabled();
  await page.getByLabel("Theme").selectOption("dark");
  await page.screenshot({ path: path.join(evidenceRoot, "webui-phantom-four-view-dark-en-1440x900.png") });

  await page.setViewportSize({ width: 1280, height: 720 });
  await page.getByLabel("Language").selectOption("fr");
  await page.locator("button.lifecycle-link").filter({ hasText: "Simulation" }).click();
  await page.screenshot({ path: path.join(evidenceRoot, "webui-simulation-dark-fr-1280x720.png") });

  await page.getByLabel("Langue").selectOption("zh");
  await page.getByLabel("主题").selectOption("light");
  await page.locator("button.lifecycle-link").filter({ hasText: "审查" }).click();
  await page.screenshot({ path: path.join(evidenceRoot, "webui-review-light-zh-1280x720.png") });
});
