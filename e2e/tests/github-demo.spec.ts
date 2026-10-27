import {test, expect} from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'examples/github-demo/manifest.json'), 'utf8'));

test('packaged results replay, export, and survive refresh without CARLA', async ({page}, info) => {
  const entry = manifest.samples[info.project.name];
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  if (info.project.name === 'object-reference') {
    for (const run of entry.runs) {
      await page.goto('/' + run.ui);
      await expect(page.getByRole('heading', {name: 'Run explorer', exact: true})).toBeVisible();
      await expect(page.locator('.run-summary-strip')).toContainText(run.verdict);
      const slider = page.getByRole('slider', {name: 'Replay timeline'});
      await page.getByRole('button', {name: 'Play replay', exact: true}).click();
      await expect.poll(async () => Number(await slider.inputValue())).toBeGreaterThan(0);
      await page.getByRole('button', {name: 'Pause replay', exact: true}).click();
      const downloadPromise = page.waitForEvent('download');
      await page.getByRole('link', {name: 'Export evidence'}).click();
      const download = await downloadPromise;
      const file = await download.path();
      const evidence = JSON.parse(fs.readFileSync(file!, 'utf8'));
      expect(evidence.id).toBe(run.id);
      expect(evidence.provenance.output_hash).toBe(run.output_hash);
      await page.reload();
      await expect(page.locator('.run-summary-strip')).toContainText(run.verdict);
    }
  } else {
    for (const execution of entry.executions) {
      await page.goto('/' + execution.ui);
      await expect(page.getByRole('img', {name: 'Replay trajectory and object observations'})).toBeVisible();
      const slider = page.getByRole('slider', {name: 'Replay timeline'});
      await page.getByRole('button', {name: 'Play', exact: true}).click();
      await expect.poll(async () => Number(await slider.inputValue())).toBeGreaterThan(0);
      await page.getByRole('button', {name: 'Pause', exact: true}).click();
      const downloadPromise = page.waitForEvent('download');
      await page.getByRole('button', {name: 'Download sealed receipt', exact: true}).click();
      const download = await downloadPromise;
      const content = fs.readFileSync((await download.path())!);
      expect(crypto.createHash('sha256').update(content).digest('hex')).toBe(execution.manifest_hash);
      await page.reload();
      await expect(page.getByRole('img', {name: 'Replay trajectory and object observations'})).toBeVisible();
    }
  }
  await page.screenshot({path: info.outputPath('retained-result.png'), fullPage: true});
  expect(errors).toEqual([]);
});
