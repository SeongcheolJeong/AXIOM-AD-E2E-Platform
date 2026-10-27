import { test } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { sceneWorkflow } from '../workflows/scene-workflow';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
test('collection to real image features, reviewed tags and selected collection', async ({ page }, info) => {
  await sceneWorkflow(page, info, root, process.env.AXIOM_TEST_PYTHON ?? path.join(root, '.venv/bin/python'), 'http://127.0.0.1:8010');
});
