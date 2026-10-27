import { defineConfig } from '@playwright/test';

// Use an explicitly started UI; these API-contract checks never start or restart services.
export default defineConfig({
  testDir: './tests', testMatch: 'external-maps.spec.ts',
  outputDir: './test-results/external-maps', workers: 1,
  timeout: 45000, expect: { timeout: 10000 },
  use: {
    baseURL: process.env.AXIOM_MAP_TEST_BASE_URL ?? 'http://127.0.0.1:5360',
    viewport: { width: 1500, height: 1050 },
    screenshot: 'only-on-failure', trace: 'retain-on-failure',
  },
});
