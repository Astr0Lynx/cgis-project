// Browser tests for the viewer.
//
//  * Default: two local mock gateways (no Docker needed) —
//      :8099 "full"   MOCK national Level 1 (11 years), Level 2 towns, 12k boundaries, auth/admin/exports
//      :8098 "phase2" exactly the Phase 2 synthetic fixtures with no application API (legacy regression)
//  * Against a real stack: CGIS_BASE_URL=http://localhost:8080 npx playwright test --project=stack
import { defineConfig } from "@playwright/test";

const real = process.env.CGIS_BASE_URL;
const launchOptions = { args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] };

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"], ["json", { outputFile: "results/e2e-results.json" }]],
  outputDir: "results/artifacts",
  use: { viewport: { width: 1366, height: 768 }, launchOptions, trace: "retain-on-failure", screenshot: "only-on-failure", acceptDownloads: true },
  projects: [
    { name: "mock-phase2", testMatch: /legacy\.spec\.js/, use: { baseURL: "http://127.0.0.1:8098" } },
    { name: "mock-full", testIgnore: [/legacy\.spec\.js/, /stack\.spec\.js/], use: { baseURL: "http://127.0.0.1:8099" } },
    ...(real ? [{ name: "stack", testMatch: [/stack\.spec\.js/, /legacy\.spec\.js/], use: { baseURL: real } }] : []),
  ],
  webServer: real ? undefined : [
    { command: "node mock-gateway/server.mjs --port 8099", url: "http://127.0.0.1:8099/viewer/", reuseExistingServer: true, timeout: 20_000 },
    { command: "node mock-gateway/server.mjs --port 8098 --dataset-set phase2 --app-api off", url: "http://127.0.0.1:8098/viewer/", reuseExistingServer: true, timeout: 20_000 },
  ],
});
