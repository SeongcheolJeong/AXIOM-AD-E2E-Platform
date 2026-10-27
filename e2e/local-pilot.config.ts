import { defineConfig } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const python = process.env.AXIOM_TEST_PYTHON ?? path.join(root, ".venv/bin/python");
process.env.AXIOM_PILOT_TEST_ROOT ??= path.join(root, ".runtime", `local-pilot-browser-${randomUUID()}`);

export default defineConfig({
  testDir: "./pilot", workers: 1, fullyParallel: false, timeout: 150_000,
  expect: { timeout: 15_000 }, reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report/local-pilot" }]],
  outputDir: "test-results/local-pilot",
  use: { baseURL: "http://127.0.0.1:8191", viewport: { width: 1440, height: 1080 },
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined, screenshot: "only-on-failure", trace: "retain-on-failure" },
  webServer: {
    command: `"${python}" -B e2e/start_local_pilot.py`, cwd: root,
    env: { AXIOM_PILOT_TEST_ROOT: process.env.AXIOM_PILOT_TEST_ROOT, PYTHONDONTWRITEBYTECODE: "1" },
    wait: { stdout: /"status": "running"/ }, reuseExistingServer: false,
    timeout: 90_000, gracefulShutdown: { signal: "SIGTERM", timeout: 150_000 },
  },
});
