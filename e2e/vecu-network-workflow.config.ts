import {defineConfig} from '@playwright/test';
// Point at an explicitly started isolated service with the actual native profile.
// This suite never mocks the simulator or starts/restarts an existing service.
export default defineConfig({testDir:'./vecu-network-workflow-tests',testMatch:'*.spec.ts',workers:1,timeout:600000,expect:{timeout:20000},outputDir:'./test-results/vecu-network-workflow-artifacts',
 use:{baseURL:process.env.AXIOM_VECU_NETWORK_URL??'http://127.0.0.1:5330',viewport:{width:1500,height:1100},trace:'retain-on-failure',screenshot:'only-on-failure'},
});
