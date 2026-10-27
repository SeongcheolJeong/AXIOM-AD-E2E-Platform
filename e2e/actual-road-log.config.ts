import { defineConfig } from '@playwright/test';

// Uses an explicitly selected existing isolated development service. It never
// launches a worker against an operating/source evidence DB or changes its auth.
export default defineConfig({
  testDir:'./tests', testMatch:'actual-road-log-replay.spec.ts', workers:1,
  timeout:45_000, expect:{timeout:10_000}, reporter:[['list']],
  use:{baseURL:process.env.AXIOM_ACTUAL_ROAD_LOG_UI_URL ?? 'http://127.0.0.1:8021',
    channel:process.env.PLAYWRIGHT_CHANNEL || 'chrome', viewport:{width:1440,height:1080},
    screenshot:'only-on-failure',trace:'retain-on-failure'},
});
