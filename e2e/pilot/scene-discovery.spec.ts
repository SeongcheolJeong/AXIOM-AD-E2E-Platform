import { test, expect } from '@playwright/test';
import path from 'node:path';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { sceneWorkflow } from '../workflows/scene-workflow';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
test('packaged scene review and appearance search retain original sources', async ({ page }, info) => {
  const work = process.env.AXIOM_PILOT_TEST_ROOT!;
  const token = readFileSync(path.join(work, 'access/api-token'), 'utf8').trim();
  await page.goto('/#scene-discovery');
  await page.getByRole('button', { name: 'Workspace settings', exact: true }).first().click();
  const dialog = page.getByRole('dialog', { name: 'Workspace settings', exact: true });
  await dialog.locator('input[type="password"]').fill(token);
  await dialog.getByRole('button', { name: 'Test & save connection', exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await sceneWorkflow(page, info, path.join(work, 'relocated application'), process.env.AXIOM_TEST_PYTHON ?? path.join(root, '.venv/bin/python'), 'http://127.0.0.1:8191', token);
});
