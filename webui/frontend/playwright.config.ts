import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  globalSetup: "./tests/global-setup.ts",
  outputDir: "../../.test-artifacts/playwright",
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  reporter: "list",
  timeout: 60_000,
  expect: { timeout: 12_000 },
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } }, testIgnore: [/visual\.spec\.ts/, /a11y\.spec\.ts/, /evidence\.spec\.ts/] },
    { name: "visual", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } }, testMatch: /visual\.spec\.ts/ },
    { name: "a11y", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 720 } }, testMatch: /a11y\.spec\.ts/ },
    { name: "evidence", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } }, testMatch: /evidence\.spec\.ts/ },
  ],
});
