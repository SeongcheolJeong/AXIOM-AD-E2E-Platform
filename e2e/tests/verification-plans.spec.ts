import { test } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { verificationWorkflow } from '../workflows/verification-workflow';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
test('normal plan UI → actual ONNX/COCO batch → gaps → investigation → return → revision',async({page},info)=>{
 await verificationWorkflow(page,info,root,process.env.AXIOM_TEST_PYTHON??path.join(root,'.venv/bin/python'),'http://127.0.0.1:8010');
});
