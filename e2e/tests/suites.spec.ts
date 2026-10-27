import {test, expect} from '@playwright/test';
import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';

test('suite dialog keeps sequential typing in the selected field and restores keyboard focus', async ({page}) => {
  await page.goto('/#suites');
  const trigger = page.getByRole('button', {name: 'New suite', exact: true});
  await expect(trigger).toBeEnabled();
  await trigger.click();
  const dialog = page.getByRole('dialog', {name: 'Create a test suite', exact: true});
  const name = dialog.getByRole('textbox', {name: 'Suite name', exact: true});
  await name.pressSequentially('Keyboard suite', {delay: 10});
  const description = dialog.getByRole('textbox', {name: 'Description', exact: true});
  await description.pressSequentially('Every character stays in the description.', {delay: 10});
  await expect(description).toHaveValue('Every character stays in the description.');
  await expect(description).toBeFocused();
  await expect(name).toHaveValue('Keyboard suite');
  const close = dialog.getByRole('button', {name: 'Close dialog', exact: true});
  await close.focus();
  await page.keyboard.press('Shift+Tab');
  await expect(dialog.getByRole('button', {name: 'Create suite', exact: true})).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
  await expect(trigger).toBeFocused();
});

test('author suite → recover submission response → baseline → changed conditions → sealed comparison', async ({page}) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const name = `Suite browser workflow ${Date.now()}`;
  const scenario = await (await page.request.post('/api/v1/scenarios', {data: {
    name: `${name} lane follow`, category: 'lane_follow', duration_s: 3,
    parameters: {trigger_time_s: 1, initial_gap_m: 45},
  }})).json();
  const requirement = await (await page.request.post('/api/v1/requirements', {data: {
    name: `${name} collision criterion`, metric_name: 'collision_count', operator: 'eq', threshold: 0,
    scenario_ids: [scenario.id],
  }})).json();
  await page.goto('/#suites');
  await page.getByRole('button', {name: 'New suite', exact: true}).first().click();
  let dialog = page.getByRole('dialog');
  await dialog.getByRole('textbox', {name: 'Suite name', exact: true}).fill(name);
  let first = dialog.getByRole('article', {name: 'Suite case 1', exact: true});
  await first.getByRole('combobox', {name: 'Scenario', exact: true}).selectOption(scenario.id);
  await first.getByRole('checkbox', {name: new RegExp(requirement.name)}).check();
  await first.getByRole('spinbutton', {name: /Seed override/}).fill('41');
  await first.getByRole('button', {name: 'Duplicate case 1', exact: true}).click();
  await dialog.getByRole('article', {name: 'Suite case 2', exact: true}).getByRole('spinbutton', {name: /Seed override/}).fill('42');
  await dialog.getByRole('button', {name: 'Create suite', exact: true}).click();
  await expect(dialog).not.toBeVisible();
  await expect(page.getByTestId('suite-detail')).toContainText(name);
  const suite = (await (await page.request.get('/api/v1/suites')).json()).find((entry: {name: string}) => entry.name === name);
  expect(suite.case_count).toBe(2);

  const keys: string[] = [];
  let firstCampaignId = '';
  await page.route('**/api/v1/campaigns', async route => {
    if (route.request().method() !== 'POST') return route.continue();
    keys.push(route.request().headers()['idempotency-key']);
    if (keys.length === 1) {
      const response = await route.fetch();
      expect(response.status()).toBe(202);
      firstCampaignId = (await response.json()).id;
      return route.abort('failed');
    }
    return route.continue();
  });
  await page.getByRole('button', {name: 'Run suite', exact: true}).click();
  dialog = page.getByRole('dialog');
  await dialog.getByRole('textbox', {name: 'Campaign label', exact: true}).fill('Captured reference');
  await dialog.getByRole('button', {name: 'Submit campaign', exact: true}).click();
  await expect(dialog.getByRole('alert')).toBeVisible();
  await dialog.getByRole('button', {name: 'Retry campaign submission', exact: true}).click();
  await expect(dialog).not.toBeVisible();
  expect(keys[0]).toBeTruthy();
  expect(keys[0]).toBe(keys[1]);
  await expect(page.getByRole('button', {name: 'Capture baseline', exact: true})).toBeEnabled();
  const firstReport = await (await page.request.get('/api/v1/campaigns/' + firstCampaignId)).json();
  expect(firstReport.counts.total).toBe(2);
  expect(firstReport.counts.checks_passed).toBe(2);
  expect(new Set(firstReport.cases.map((entry: {job_id: string}) => entry.job_id)).size).toBe(2);
  await page.getByRole('button', {name: 'Capture baseline', exact: true}).click();
  dialog = page.getByRole('dialog');
  await dialog.getByRole('textbox', {name: 'Baseline name', exact: true}).fill(`${name} baseline`);
  await dialog.getByRole('textbox', {name: 'Baseline note', exact: true}).fill('Original reference fixture; preserve every case.');
  await dialog.getByRole('button', {name: 'Capture baseline', exact: true}).click();
  await expect(dialog).not.toBeVisible();
  const baseline = (await (await page.request.get('/api/v1/baselines?suite_id=' + suite.id)).json())[0];
  const originalBytes = await (await page.request.get('/api/v1/baselines/' + baseline.id + '/export')).body();

  await page.getByRole('button', {name: 'Create revision', exact: true}).click();
  dialog = page.getByRole('dialog');
  first = dialog.getByRole('article', {name: 'Suite case 1', exact: true});
  await first.locator('summary').filter({hasText: 'Parameter overrides'}).click();
  await first.getByRole('spinbutton', {name: /Initial gap/}).fill('55');
  await dialog.getByRole('button', {name: 'Publish suite revision', exact: true}).click();
  await expect(dialog).not.toBeVisible();
  await page.getByRole('button', {name: 'Run suite', exact: true}).click();
  dialog = page.getByRole('dialog');
  await dialog.getByRole('textbox', {name: 'Campaign label', exact: true}).fill('Changed conditions candidate');
  const candidateResponse = page.waitForResponse(response => response.url().endsWith('/api/v1/campaigns') && response.request().method() === 'POST');
  await dialog.getByRole('button', {name: 'Submit campaign', exact: true}).click();
  const candidate = await (await candidateResponse).json();
  await expect(dialog).not.toBeVisible();
  await page.getByRole('combobox', {name: 'Comparison baseline', exact: true}).selectOption(baseline.id);
  await expect(page.getByRole('button', {name: 'Capture comparison', exact: true})).toBeEnabled();
  await expect(page.getByTestId('comparison-report')).toContainText(/not comparable|conditions changed|conditions differ/i);
  const comparison = await (await page.request.get(`/api/v1/campaigns/${candidate.id}/compare?baseline_id=${baseline.id}`)).json();
  expect(comparison.counts.not_comparable).toBe(1);
  expect(comparison.counts.unchanged_pass).toBe(1);
  expect(comparison.counts.total).toBe(2);
  await page.getByRole('button', {name: 'Capture comparison', exact: true}).click();
  dialog = page.getByRole('dialog');
  await dialog.getByRole('textbox', {name: 'Comparison name', exact: true}).fill(`${name} review`);
  await dialog.getByRole('textbox', {name: 'Comparison note', exact: true}).fill('Gap changed in one case; this is not classified as performance regression.');
  await dialog.getByRole('button', {name: 'Capture comparison', exact: true}).click();
  await expect(dialog).not.toBeVisible();
  const saved = page.getByTestId('saved-comparison-detail');
  await expect(saved).toContainText(`${name} review`);
  const downloadPromise = page.waitForEvent('download');
  await saved.getByRole('link', {name: 'Download comparison', exact: true}).click();
  const download = await downloadPromise;
  const content = await readFile((await download.path())!);
  const captures = await (await page.request.get('/api/v1/comparisons?campaign_id=' + candidate.id)).json();
  expect(createHash('sha256').update(content).digest('hex')).toBe(captures[0].manifest_hash);
  expect(JSON.parse(content.toString()).report.counts.not_comparable).toBe(1);
  expect(await (await page.request.get('/api/v1/baselines/' + baseline.id + '/export')).body()).toEqual(originalBytes);
  await page.setViewportSize({width: 390, height: 844});
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
  // A failed lookup for a different capture must never borrow the previous report.
  const unavailable = await (await page.request.post('/api/v1/comparisons', {
    headers: {'Idempotency-Key': `unavailable-comparison-${Date.now()}`},
    data: {campaign_id: candidate.id, baseline_id: baseline.id, name: `${name} second review`, note: 'Independent lookup fixture'},
  })).json();
  await page.route(`**/api/v1/comparisons/${unavailable.id}`, route => route.fulfill({
    status: 503, contentType: 'application/json', body: JSON.stringify({detail: 'Comparison lookup temporarily unavailable'}),
  }));
  await page.getByRole('tab', {name: 'Pinned cases', exact: true}).click();
  await page.getByRole('tab', {name: 'Saved comparisons', exact: true}).click();
  await page.getByRole('combobox', {name: 'Select saved comparison', exact: true}).selectOption(unavailable.id);
  await expect(page.getByText('Comparison lookup temporarily unavailable', {exact: true}).first()).toBeVisible();
  await expect(page.getByTestId('saved-comparison-detail')).not.toBeVisible();
  await expect(page.getByTestId('saved-comparison-report')).not.toBeVisible();
  await expect(page.getByTestId('comparison-report')).not.toBeVisible();
  expect(errors).toEqual([]);
});
