import {test, expect} from '@playwright/test';

test('lost submission response retries the same job and opens its actual run', async ({page}) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const scenario = await (await page.request.post('/api/v1/scenarios', {data: {
    name: `Queue retry evidence ${Date.now()}`, category: 'lane_follow',
  }})).json();
  const keys: string[] = [];
  let originalId = '';
  await page.route('**/api/v1/jobs', async route => {
    if (route.request().method() !== 'POST') return route.continue();
    keys.push(route.request().headers()['idempotency-key']);
    if (keys.length === 1) {
      const response = await route.fetch();
      expect(response.status()).toBe(202);
      originalId = (await response.json()).id;
      return route.abort('failed');
    }
    return route.continue();
  });
  await page.goto('/#jobs');
  await expect(page.getByRole('heading', {name: 'Execution queue', exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'New execution', exact: true}).first().click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('combobox', {name: 'Scenario', exact: true}).selectOption(scenario.id);
  await dialog.getByRole('button', {name: 'Run simulation', exact: true}).click();
  await expect(dialog.getByRole('alert')).toBeVisible();
  await dialog.getByRole('button', {name: 'Retry submission', exact: true}).click();
  await expect(dialog).not.toBeVisible();
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBeTruthy();
  expect(keys[1]).toBe(keys[0]);
  const detail = page.getByTestId('job-detail');
  await expect(detail.getByTestId('job-id')).toHaveText(originalId);
  await expect(detail.getByTestId('job-status')).toHaveText('Completed');
  const saved = await (await page.request.get('/api/v1/jobs/' + originalId)).json();
  expect(saved.scenario_snapshot.id).toBe(scenario.id);
  expect(saved.input_hash).toHaveLength(64);
  const rows = await (await page.request.get('/api/v1/jobs')).json();
  expect(rows.items.filter((job: {scenario_id: string}) => job.scenario_id === scenario.id)).toHaveLength(1);
  await expect(page.getByRole('region', {name: 'Execution workers'})).toBeVisible();
  await page.setViewportSize({width: 390, height: 844});
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
  await page.getByRole('button', {name: 'Open result', exact: true}).click();
  await expect(page.getByRole('combobox', {name: 'Select execution', exact: true})).toHaveValue(saved.run_id);
  await expect(page.getByRole('button', {name: 'Play replay', exact: true})).toBeVisible();
  expect(errors).toEqual([]);
});
