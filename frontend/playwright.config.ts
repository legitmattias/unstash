import { defineConfig, devices } from "@playwright/test";

// Runs against a deployed environment (staging by default). Point SMOKE_BASE_URL
// elsewhere for local runs. Credentials come from SMOKE_EMAIL / SMOKE_PASSWORD;
// the spec skips when they are absent so the suite is safe to run unconfigured.
export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "line",
  use: {
    baseURL: process.env.SMOKE_BASE_URL ?? "https://staging.unstash.mattic.dev",
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
