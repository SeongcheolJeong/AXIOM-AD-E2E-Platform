import { defineConfig } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const python = process.env.AXIOM_TEST_PYTHON ?? path.join(root, '.venv/bin/python');
export default defineConfig({
  testDir: './tests', testMatch: 'stunt-lab.spec.ts', outputDir: './test-results/stunt-artifacts',
  workers: 1, timeout: 60000, expect: { timeout: 10000 },
  use: { baseURL: 'http://127.0.0.1:5351', viewport: { width: 1500, height: 1100 }, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
  webServer: [
    { command: `"${python}" -m uvicorn ade2e.main:app --host 127.0.0.1 --port 8251`, cwd: root,
      env: { ADE2E_DATA_DIR: path.join(root, 'e2e/.runtime/stunt-lab'), ADE2E_SEED_EXAMPLES: '0', ADE2E_API_TOKEN: '', ADE2E_DATABASE_URL: '', ADE2E_TEAM_MODE: '0', ADE2E_CARLA_WORKER_URL: '', ADE2E_CARLA_STUNT_URL: '' },
      url: 'http://127.0.0.1:8251/health', reuseExistingServer: false },
    { command: 'npm run dev -- --port 5351 --strictPort', cwd: path.join(root, 'frontend'),
      env: { AXIOM_API_TARGET: 'http://127.0.0.1:8251' }, url: 'http://127.0.0.1:5351', reuseExistingServer: false },
  ],
});
