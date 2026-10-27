import { test, expect } from '@playwright/test';
import fs from 'node:fs';

const selected = process.env.AXIOM_NATIVE_VAD_PRODUCT_ACCEPTANCE;

test('actual native VAD product receipt preserves all modes through the normal experiment UI', async ({ page }) => {
  test.skip(!selected, 'Opt-in requires an actual product queue acceptance JSON; no authored receipt fallback.');
  const acceptance = JSON.parse(fs.readFileSync(selected!, 'utf8'));
  expect(acceptance.status).toBe('native_queue_actual_execution_accepted');
  expect(acceptance.summary.commercial_use).toBe(false);
  expect(acceptance.summary.origin_verification).toBe('platform_dispatched_pinned_native_runner_over_authenticated_ssh');
  await page.goto(`/#experiments?execution=${encodeURIComponent(acceptance.job_id)}`);
  await expect(page.getByRole('heading', { name: 'Native learned policy · all trajectory modes', exact: true })).toBeVisible();
  await expect(page.getByRole('img', { name: 'Native driving policy trajectory modes', exact: true })).toBeVisible();
  await expect(page.getByText('not_evaluated_open_loop_research', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Execution type')).toHaveValue('driving_open_loop');
  await page.getByLabel('Driving policy input timeline').fill('2');
  await expect(page.getByText('Input 3 / 3', { exact: true })).toBeVisible();
  const table = page.locator('table').filter({ hasText: 'Final native XY' });
  await expect(table.getByRole('cell', { name: 'right', exact: true })).toBeVisible();
  await expect(table.getByRole('cell', { name: 'left', exact: true })).toBeVisible();
  await expect(table.getByRole('cell', { name: 'straight', exact: true })).toBeVisible();
  const result = await (await page.request.get(`/api/v1/experiments/executions/${acceptance.job_id}`)).json();
  expect(result.status).toBe('completed');
  expect(result.manifest_hash).toBe(acceptance.manifest_hash);
  expect(result.summary.driving_outputs).toEqual(acceptance.summary.driving_outputs);
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download sealed receipt', exact: true }).click();
  expect((await download).suggestedFilename()).toBe(`${acceptance.job_id}.json`);
  await expect(page.getByText('Native evidence downloaded and SHA-256 verified.')).toBeVisible();
  await page.screenshot({ path: test.info().outputPath('native-vad-product-trajectories.png'), fullPage: true });
});

