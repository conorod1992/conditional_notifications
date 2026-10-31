import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./browser",
  testMatch: "*.spec.mjs",
  fullyParallel: true,
  use: { baseURL: "http://127.0.0.1:8873", trace: "retain-on-failure" },
  webServer: {
    command: "node browser/server.mjs",
    url: "http://127.0.0.1:8873",
    reuseExistingServer: !process.env.CI,
  },
});
