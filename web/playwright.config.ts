import { defineConfig, devices } from "@playwright/test";

// End to end against the built static site, with the API mocked in the browser (e2e/*.spec.ts):
// CI can't sign in to Entra ID, so the site runs with sign-in off (public/config.json).
export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  use: { baseURL: "http://localhost:3100", trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: { command: "npx serve out -l 3100", url: "http://localhost:3100", reuseExistingServer: !process.env.CI },
});
