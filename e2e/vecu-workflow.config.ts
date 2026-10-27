import {defineConfig} from '@playwright/test';

// Uses the explicitly started native service. No service restarts or mocked ECU.
export default defineConfig({testDir:'./vecu-workflow-tests',testMatch:'*.spec.ts',workers:1,timeout:600000,expect:{timeout:20000},outputDir:'./test-results/vecu-workflow-artifacts',
 use:{baseURL:process.env.AXIOM_VECU_URL??'http://127.0.0.1:5320',viewport:{width:1500,height:1100},trace:'retain-on-failure',screenshot:'only-on-failure'},
});
