import {defineConfig} from '@playwright/test';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const python = process.env.AXIOM_TEST_PYTHON ?? path.join(root, '.venv/bin/python');
const samples = ['object-reference', 'log-open-loop', 'log-feedback'];
export default defineConfig({
  testDir: './tests', testMatch: 'github-demo.spec.ts', workers: 1, timeout: 60_000,
  expect: {timeout: 15_000}, reporter: [['list']],
  outputDir: './test-results/github-demo',
  use: {viewport: {width: 1440, height: 1000}, screenshot: 'only-on-failure', trace: 'retain-on-failure'},
  projects: samples.map((sample, index) => ({name: sample, use: {baseURL: `http://127.0.0.1:${5341 + index}`}})),
  webServer: samples.flatMap((sample, index) => [
    {command: `"${python}" e2e/github-demo.fixture.py ${sample} --port ${8341 + index}`, cwd: root,
      url: `http://127.0.0.1:${8341 + index}/health`, reuseExistingServer: false, timeout: 60_000},
    {command: `npm run dev -- --port ${5341 + index} --strictPort`, cwd: path.join(root, 'frontend'),
      env: {AXIOM_API_TARGET: `http://127.0.0.1:${8341 + index}`},
      url: `http://127.0.0.1:${5341 + index}`, reuseExistingServer: false, timeout: 60_000},
  ]),
});
