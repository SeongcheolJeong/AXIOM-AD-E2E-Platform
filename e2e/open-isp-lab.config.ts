import {defineConfig} from '@playwright/test';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const python=process.env.AXIOM_TEST_PYTHON??path.join(root,'.venv/bin/python');
const env={ADE2E_DATA_DIR:process.env.AXIOM_E2E_DATA_DIR??path.join(root,'e2e/.runtime/open-isp-lab'),ADE2E_SEED_EXAMPLES:'0',ADE2E_API_TOKEN:'',ADE2E_DATABASE_URL:'',ADE2E_TEAM_MODE:'0',ADE2E_ENABLE_LOCAL_RTL:'1',ADE2E_CARLA_WORKER_URL:''};
export default defineConfig({outputDir:'./test-results/open-isp-artifacts',testDir:'./tests',testMatch:'open-isp-lab.spec.ts',workers:1,timeout:420000,expect:{timeout:25000},use:{baseURL:'http://127.0.0.1:5253',viewport:{width:1500,height:1100},screenshot:'only-on-failure',trace:'retain-on-failure'},webServer:[
 {command:`"${python}" -m uvicorn ade2e.main:app --host 127.0.0.1 --port 8153`,cwd:root,env,url:'http://127.0.0.1:8153/health',reuseExistingServer:false},
 {command:`"${python}" -m ade2e.worker --poll-interval 0.1`,cwd:root,env,wait:{stdout:/AXIOM worker ready:/},reuseExistingServer:false},
 {command:'npm run dev -- --port 5253 --strictPort',cwd:path.join(root,'frontend'),env:{AXIOM_API_TARGET:'http://127.0.0.1:8153'},url:'http://127.0.0.1:5253',reuseExistingServer:false},
]});
