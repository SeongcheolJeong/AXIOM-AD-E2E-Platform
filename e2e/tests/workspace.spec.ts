import {test, expect} from '@playwright/test';

test('author → run → replay → edit revision → requirement → export', async ({page}) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.getByText('Local API connected')).toBeVisible();
  await page.getByRole('button', {name: 'Scenario library', exact: false}).first().click();
  await page.getByRole('button', {name: 'Create scenario', exact: true}).first().click();
  const name = `Workflow check ${Date.now()}`;
  let dialog = page.getByRole('dialog');
  await dialog.getByLabel('Scenario name').fill(name);
  await dialog.getByRole('combobox', {name: 'Category', exact: true}).selectOption('lane_follow');
  await dialog.getByLabel('Ego speed').fill('8');
  await dialog.getByLabel('Actor speed').fill('8');
  await dialog.getByLabel('Initial gap').fill('40');
  await dialog.getByRole('button', {name: 'Create scenario', exact: true}).click();
  await expect(dialog).not.toBeVisible();
  await page.getByPlaceholder(/Search scenarios/).fill(name);
  await expect(page.getByText(name, {exact: true}).first()).toBeVisible();
  const runResponse = await page.request.get('/api/v1/scenarios?q=' + encodeURIComponent(name));
  const scenario = (await runResponse.json())[0];
  expect(scenario.name).toBe(name);

  await page.locator('article.scenario-card').filter({hasText: name}).getByRole('button', {name: 'Run', exact: true}).click();
  dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('combobox', {name: 'Scenario', exact: true})).toHaveValue(scenario.id);
  await dialog.getByRole('button', {name: 'Run simulation', exact: true}).click();
  await expect(dialog).not.toBeVisible();
  await expect(page.getByRole('heading', {name: 'Execution queue', exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'Open result', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Play replay'})).toBeVisible();
  await page.getByRole('button', {name: 'Play replay'}).click();
  await expect(page.getByRole('button', {name: 'Pause replay'})).toBeVisible();
  await expect.poll(async () => Number(await page.getByRole('slider', {name: 'Replay timeline'}).inputValue())).toBeGreaterThan(0);
  await page.getByRole('button', {name: 'Pause replay'}).click();
  await page.getByRole('button', {name: 'Top-down', exact: true}).click();
  await page.getByRole('button', {name: 'Reset replay'}).click();
  await expect(page.getByRole('slider', {name: 'Replay timeline'})).toHaveValue('0');
  const runs = await (await page.request.get('/api/v1/runs?scenario_id=' + scenario.id)).json();
  expect(runs[0].status).toBe('completed');
  const runDetail = await (await page.request.get('/api/v1/runs/' + runs[0].id)).json();
  expect(runDetail.trajectory.length).toBeGreaterThan(10);
  expect(runDetail.provenance.input_hash).toHaveLength(64);
  const downloadPromise = page.waitForEvent('download');
  await page.getByText('Export evidence', {exact: true}).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toContain('.json');

  await page.getByRole('button', {name: 'Scenario library', exact: false}).first().click();
  await page.getByPlaceholder(/Search scenarios/).fill(name);
  await page.getByRole('button', {name: `Edit ${name}`, exact: true}).click();
  dialog = page.getByRole('dialog');
  await dialog.getByLabel('Ego speed').fill('10');
  await dialog.getByRole('button', {name: 'Save revision', exact: true}).click();
  await expect(dialog).not.toBeVisible();
  const updated = await (await page.request.get('/api/v1/scenarios/' + scenario.id)).json();
  expect(updated.revision).toBe(2);
  const immutable = await (await page.request.get('/api/v1/runs/' + runs[0].id)).json();
  expect(immutable.scenario_snapshot.parameters.ego_speed_mps).toBe(8);

  await page.getByRole('button', {name: 'Validation', exact: true}).click();
  await page.getByRole('button', {name: 'Add requirement', exact: true}).click();
  dialog = page.getByRole('dialog');
  await dialog.getByLabel('Requirement name').fill(`No collision · ${name}`);
  await dialog.getByRole('checkbox', {name, exact: true}).check();
  await dialog.getByRole('button', {name: 'Add requirement', exact: true}).click();
  await expect(dialog).not.toBeVisible();
  await expect(page.getByText(`No collision · ${name}`, {exact: true})).toBeVisible();
  const requirements = await (await page.request.get('/api/v1/requirements')).json();
  expect(requirements.find((r: {name: string}) => r.name === `No collision · ${name}`).status).toBe('untested');
  expect(errors).toEqual([]);
});

test('every workspace page and a narrow viewport remain usable', async ({page}) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.getByText('Local API connected')).toBeVisible();
  for (const name of ['Execution queue', 'Data catalog', 'Validation', 'Integrations', 'Agent studio & API', 'Overview']) {
    await page.getByRole('button', {name, exact: false}).first().click();
    await expect(page.locator('main h1')).toBeVisible();
    if (name === 'Integrations') {
      const before = await (await page.request.get('/api/v1/runs')).json();
      await page.getByRole('button', {name: 'Check connection for Object Reference', exact: true}).click();
      await expect(page.getByRole('region', {name: 'Object Reference connection', exact: true}).getByText('Ready', {exact: true})).toBeVisible();
      await page.getByRole('button', {name: 'Check connection for CARLA · remote worker', exact: true}).click();
      await expect(page.getByRole('region', {name: 'CARLA · remote worker connection', exact: true}).getByText('Not configured', {exact: true})).toBeVisible();
      const after = await (await page.request.get('/api/v1/runs')).json();
      expect(after.map((run: {id: string}) => run.id)).toEqual(before.map((run: {id: string}) => run.id));
    }
  }
  await page.setViewportSize({width: 390, height: 844});
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
  await page.getByRole('button', {name: 'Open navigation'}).click();
  await page.getByRole('button', {name: 'Scenario library', exact: false}).first().click();
  await expect(page.locator('main h1')).toBeVisible();
  expect(errors).toEqual([]);
});

test('a failed execution lookup never displays the previous run as evidence', async ({page}) => {
  await page.goto('/');
  await expect(page.getByText('Local API connected')).toBeVisible();
  await page.getByRole('button', {name: 'Run explorer', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Play replay'})).toBeVisible();
  const runs = await (await page.request.get('/api/v1/runs')).json();
  const selection = page.getByRole('combobox', {name: 'Select execution', exact: true});
  const original = await selection.inputValue();
  const unavailable = runs.find((run: {id: string}) => run.id !== original).id;
  await page.route('**/api/v1/runs/' + unavailable, route => route.fulfill({
    status: 503, contentType: 'application/json', body: JSON.stringify({detail: 'Evidence temporarily unavailable'}),
  }));
  await selection.selectOption(unavailable);
  await expect(page.getByRole('alert')).toContainText('Evidence temporarily unavailable');
  await expect(selection).toHaveValue(unavailable);
  await expect(page.getByText('Execution unavailable', {exact: true})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Play replay'})).not.toBeVisible();
  await expect(page.getByText('Export evidence', {exact: true})).not.toBeVisible();
});
