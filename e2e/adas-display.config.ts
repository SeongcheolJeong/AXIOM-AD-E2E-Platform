import {defineConfig} from '@playwright/test';

// Read-only UI checks against retained artifacts; no worker or native execution.
export default defineConfig({
 testDir:'./adas-display-tests',testMatch:'*.spec.ts',workers:1,timeout:45000,
 outputDir:'./test-results/adas-display-artifacts',expect:{timeout:15000},
 use:{baseURL:process.env.AXIOM_ADAS_DISPLAY_URL??'http://127.0.0.1:5280',viewport:{width:1500,height:1100},trace:'retain-on-failure'},
});
