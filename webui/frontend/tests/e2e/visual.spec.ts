import { expect, test } from "@playwright/test";

const combinations = (["en", "zh", "fr"] as const).flatMap((locale) =>
  (["light", "dark"] as const).map((theme) => ({ locale, theme })),
);

const workspaces = ["protocol", "phantom", "simulation", "run", "review", "seal"] as const;

async function useEmptyRunFixture(page: import("@playwright/test").Page) {
  await page.route("**/api/runs**", (route) => route.fulfill({ json: { runs_root: "runs", runs: [] } }));
  await page.route("**/api/tasks", (route) => route.fulfill({ json: { tasks: [] } }));
}

for (const combination of combinations) {
  for (const [index, workspace] of workspaces.entries()) {
    test(`${workspace} ${combination.locale} ${combination.theme}`, async ({ page }) => {
      await useEmptyRunFixture(page);
      await page.addInitScript(({ locale, theme }) => {
        localStorage.setItem("pars.locale", locale);
        localStorage.setItem("pars.theme", theme);
        localStorage.removeItem("pars.workspace.v3");
      }, combination);
      await page.goto("/");
      if (index > 0) await page.locator("button.lifecycle-link").nth(index).click();
      await expect(page.locator(`.workspace-scroll[data-workspace="${workspace}"]`)).toBeVisible();
      await expect(page).toHaveScreenshot(`${workspace}-${combination.locale}-${combination.theme}.png`, {
        animations: "disabled",
        mask: workspace === "phantom" ? [page.locator(".surface-canvas")] : [],
        maxDiffPixelRatio: 0.002,
      });
    });
  }
}

for (const locale of ["zh", "fr"] as const) {
  for (const theme of ["light", "dark"] as const) {
    for (const [index, workspace] of workspaces.entries()) {
      test(`1280 ${workspace} ${locale} ${theme}`, async ({ page }) => {
        await page.setViewportSize({ width: 1280, height: 720 });
        await useEmptyRunFixture(page);
        await page.addInitScript(({ locale, theme }) => {
          localStorage.setItem("pars.locale", locale);
          localStorage.setItem("pars.theme", theme);
          localStorage.removeItem("pars.workspace.v3");
        }, { locale, theme });
        await page.goto("/");
        if (index > 0) await page.locator("button.lifecycle-link").nth(index).click();
        await expect(page.locator(`.workspace-scroll[data-workspace="${workspace}"]`)).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
        await expect(page).toHaveScreenshot(`1280-${workspace}-${locale}-${theme}.png`, {
          animations: "disabled",
          mask: workspace === "phantom" ? [page.locator(".surface-canvas")] : [],
          maxDiffPixelRatio: 0.002,
        });
      });
    }
  }
}

test("Phantom synchronized four-view workbench", async ({ page }) => {
  await useEmptyRunFixture(page);
  await page.addInitScript(() => {
    localStorage.setItem("pars.locale", "en");
    localStorage.setItem("pars.theme", "dark");
    localStorage.removeItem("pars.workspace.v3");
  });
  await page.goto("/");
  await page.locator("button.lifecycle-link").filter({ hasText: "Phantom" }).click();
  await page.getByRole("button", { name: /Regenerate preview/i }).click();
  await expect(page.locator(".surface-canvas canvas")).toBeVisible({ timeout: 30_000 });
  await expect(page).toHaveScreenshot("phantom-populated-en-dark.png", {
    animations: "disabled",
    mask: [page.locator(".surface-canvas")],
    maxDiffPixelRatio: 0.005,
  });
});
