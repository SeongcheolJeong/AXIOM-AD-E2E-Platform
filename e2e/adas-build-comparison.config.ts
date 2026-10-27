import {defineConfig} from '@playwright/test';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const devApi=process.env.AXIOM_BUILD_COMPARE_DEV_API;
const frontend=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../frontend');

// Read retained, real native build artifacts through the normal API. No service
// restarts and no replacement build execution are needed for this UI check.
export default defineConfig({
  testDir:'./tests',testMatch:'adas-build-comparison.spec.ts',workers:1,timeout:60000,
  outputDir:'./test-results/adas-build-comparison-artifacts',expect:{timeout:20000},
  use:{baseURL:process.env.AXIOM_BUILD_COMPARE_URL??(devApi?'http://127.0.0.1:5281':'http://127.0.0.1:5270'),viewport:{width:1500,height:1100},screenshot:'only-on-failure',trace:'retain-on-failure'},
  ...(devApi?{webServer:{command:'npm run dev -- --port 5281 --strictPort',cwd:frontend,env:{AXIOM_API_TARGET:devApi},url:'http://127.0.0.1:5281',reuseExistingServer:false}}:{}),
});
