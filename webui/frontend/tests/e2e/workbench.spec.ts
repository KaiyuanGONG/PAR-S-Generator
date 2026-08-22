import { expect, test } from "@playwright/test";

async function openWorkbench(page: import("@playwright/test").Page, locale = "en", theme = "light") {
  await page.addInitScript(({ locale, theme }) => {
    if (!localStorage.getItem("pars.locale")) localStorage.setItem("pars.locale", locale);
    if (!localStorage.getItem("pars.theme")) localStorage.setItem("pars.theme", theme);
    localStorage.removeItem("pars.workspace.v3");
  }, { locale, theme });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: locale === "zh" ? "协议" : "Protocol", exact: true })).toBeVisible();
}

test("protocol remains editable until the complete plan is locked", async ({ page }) => {
  await openWorkbench(page);
  const runId = page.locator("#protocol-run-id");
  const serviceDefault = await runId.inputValue();
  await runId.fill("temporary-draft");
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Reset draft" }).click();
  await expect(runId).toHaveValue(serviceDefault);
  await expect(page.getByRole("button", { name: /Continue to Phantom/i })).toBeEnabled();
  await page.getByRole("button", { name: /Continue to Phantom/i }).click();
  await expect(page.getByRole("heading", { name: "Cohort parameters" })).toBeVisible();
  await expect(page.getByLabel("Target left-lobe ratio")).toBeEnabled();
});

test("phantom has one linked cursor across orthogonal, 3D, and MIP views", async ({ page }) => {
  await openWorkbench(page);
  await page.locator("button.lifecycle-link").filter({ hasText: "Phantom" }).click();
  await page.getByRole("button", { name: /Regenerate preview/i }).click();
  await expect(page.locator(".scan-image-frame img")).toHaveCount(3, { timeout: 30_000 });
  await expect(page.locator(".surface-canvas canvas")).toBeVisible({ timeout: 30_000 });

  const axial = page.locator(".slice-cell").filter({ hasText: "Axial" }).getByRole("slider");
  await axial.fill("40");
  await expect(page.locator(".imaging-probe")).toContainText("z 40");

  const axialFrame = page.locator(".slice-cell").filter({ hasText: "Axial" }).locator(".scan-image-frame");
  const beforeKeyboard = await page.locator(".imaging-probe").textContent();
  const beforeX = Number(beforeKeyboard?.match(/x\s+(\d+)/)?.[1]);
  await axialFrame.focus();
  await axialFrame.press("ArrowRight");
  await expect(page.locator(".imaging-probe")).toContainText(`x ${beforeX + 1}`);

  const surface = page.locator(".surface-canvas");
  const surfaceLabel = await surface.getAttribute("aria-label");
  const beforeZ = Number(surfaceLabel?.match(/z\s+(\d+)$/)?.[1]);
  await surface.focus();
  await surface.press("PageUp");
  await expect(surface).toHaveAttribute("aria-label", new RegExp(`z ${beforeZ + 1}$`));

  await page.getByRole("button", { name: "MIP", exact: true }).click();
  await expect(page.locator(".fourth-view .scan-image-frame img")).toBeVisible();
  await page.getByRole("button", { name: "3D", exact: true }).click();
  await expect(page.locator(".surface-canvas canvas")).toBeVisible();
});

test("theme and language preferences persist and 1280px has no page overflow", async ({ page }) => {
  await openWorkbench(page);
  await page.getByLabel("Language").selectOption("fr");
  await page.getByLabel("Thème").selectOption("dark");
  await page.reload();
  await expect(page.getByRole("heading", { name: "Protocole", exact: true })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.setViewportSize({ width: 1280, height: 720 });
  for (let index = 0; index < 6; index += 1) {
    if (index > 0) await page.locator("button.lifecycle-link").nth(index).click();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    const command = page.locator(".page-command-shelf:visible, .run-command-inline:visible").first();
    await expect(command).toBeVisible();
    const box = await command.boundingBox();
    expect(box && box.y + box.height).toBeLessThanOrEqual(720);
  }
});
