import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("pars.locale", "fr");
    localStorage.setItem("pars.theme", "dark");
    localStorage.removeItem("pars.workspace.v3");
  });
  await page.goto("/");
});

for (const workspace of ["Protocol", "Fantôme", "Simulation", "Exécution", "Révision", "Sceller"]) {
  test(`${workspace} has no serious accessibility violations`, async ({ page }) => {
    if (workspace !== "Protocol") await page.getByRole("button", { name: new RegExp(workspace) }).click();
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag22aa"]).analyze();
    expect(results.violations.filter((item) => ["critical", "serious"].includes(item.impact ?? ""))).toEqual([]);
  });
}
