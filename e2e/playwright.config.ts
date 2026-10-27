import {defineConfig} from '@playwright/test';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const python = process.env.AXIOM_TEST_PYTHON ?? path.join(root, '.venv/bin/python');
const dataDir = process.env.AXIOM_E2E_DATA_DIR
  ? path.resolve(process.env.AXIOM_E2E_DATA_DIR)
  : path.join(root, 'e2e/.runtime/workflow-db');

export default defineConfig({
  testDir: './tests',
  testIgnore: ['**/fpga-lab.spec.ts', '**/ecu-lab.spec.ts', '**/adas-module.spec.ts', '**/adas-build-comparison.spec.ts', '**/github-demo.spec.ts'],
  fullyParallel: false,
  workers: 1,
  timeout: 45_000,
  expect: {timeout: 10_000},
  reporter: [['list'], ['html', {open: 'never'}]],
  use: {
    baseURL: 'http://127.0.0.1:5174',
    viewport: {width: 1440, height: 1080},
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
  },
  webServer: [
    {
      command: `"${python}" -m uvicorn ade2e.main:app --host 127.0.0.1 --port 8010`,
      cwd: root,
      env: {ADE2E_DATA_DIR: dataDir, ADE2E_SEED_EXAMPLES: '1', ADE2E_API_TOKEN: '', ADE2E_CARLA_WORKER_URL: ''},
      url: 'http://127.0.0.1:8010/health', reuseExistingServer: false,
    },
    {
      command: `"${python}" -m ade2e.worker --poll-interval 0.1`,
      cwd: root,
      env: {ADE2E_DATA_DIR: dataDir, ADE2E_SEED_EXAMPLES: '0', ADE2E_API_TOKEN: '', ADE2E_CARLA_WORKER_URL: ''},
      wait: {stdout: /AXIOM worker ready:/},
      reuseExistingServer: false,
    },
    {
      command: 'npm run dev -- --port 5174 --strictPort',
      cwd: path.join(root, 'frontend'),
      env: {AXIOM_API_TARGET: 'http://127.0.0.1:8010'},
      url: 'http://127.0.0.1:5174', reuseExistingServer: false,
    },
  ],
});
