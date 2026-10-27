import { test } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { annotationWorkflow } from '../workflows/annotation-workflow';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
test('original dataset → ONNX label proposals → unreviewed drafts → human edit → retained provenance',async({page},info)=>{
  await annotationWorkflow(page,info,root,process.env.AXIOM_TEST_PYTHON??path.join(root,'.venv/bin/python'),'http://127.0.0.1:8010');
});
