import { expect, type Page, type TestInfo } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import path from 'node:path';

export async function sceneWorkflow(page: Page, info: TestInfo, source: string, python: string, apiUrl: string, token = '') {
  info.setTimeout(180_000);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const headers = token ? { Authorization: `Bearer ${token}` } : {};
  const work = info.outputPath('actual-scene-sources');
  execFileSync(python, ['-B', path.join(source, 'examples/scene_discovery_workflow.py'), '--url', apiUrl, '--output', work], {
    cwd: source, env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1', ADE2E_API_TOKEN: token,
      PYTHONPATH: ['backend/src', 'sdk/python/src'].map(p => path.join(source, p)).join(path.delimiter) },
    timeout: 90_000, maxBuffer: 2*1024*1024,
  });
  const fixture = JSON.parse(readFileSync(path.join(work, 'summary.json'), 'utf8'));
  const keys = fixture.key_by_role;
  const get = async (url: string) => {
    const response = await page.request.get(url, { headers });
    expect(response.status()).toBe(200); return response.json();
  };
  const original = await get(`/api/v1/collections/${fixture.collection_id}`);
  const originalBytes = await (await page.request.get(original.download_url, { headers })).body();
  await page.goto(`/#collections?collection=${fixture.collection_id}`);
  await page.getByRole('link', { name: 'Discover scenes in collection', exact: true }).click();
  await page.getByLabel('Scene index purpose', { exact: true }).fill('Compare original pixel appearances. Not semantic or measured environmental validation.');
  await page.getByRole('button', { name: 'Preview scene workload', exact: true }).click();
  await expect(page.getByTestId('scene-workload')).toContainText('7 original messages');
  await expect(page.getByTestId('scene-workload')).toContainText('6 candidate images');
  await page.getByLabel('Confirm local scene analysis', { exact: true }).check();
  await page.getByRole('button', { name: 'Build scene index', exact: true }).click();
  await expect(page.getByTestId('scene-index-id')).toBeVisible();
  const indexId = (await page.getByTestId('scene-index-id').textContent())!;
  await expect(page.getByTestId('scene-count-all_sources')).toHaveText('7');
  await expect(page.getByTestId('scene-count-indexed')).toHaveText('6');
  await expect(page.getByTestId('scene-count-unindexed')).toHaveText('1');
  await expect(page.getByTestId('scene-duplicate-groups')).toHaveText('1');
  const index = await get(`/api/v1/scene-indexes/${indexId}`);
  const indexBytes = await (await page.request.get(index.download_url, { headers })).body();
  const dark = index.rows.find((row: any) => row.key === keys.dark);
  expect(dark.features.proposals.some((proposal: any) => proposal.tag === 'dark_image')).toBe(true);
  await page.getByLabel('Scene lighting filter', { exact: true }).selectOption('night');
  await expect(page.getByTestId('scene-count-matched')).toHaveText('0');
  await page.getByLabel('Scene lighting filter', { exact: true }).selectOption('all');
  await page.locator(`[data-testid="scene-card"][data-source-key="${keys.dark}"]`).getByRole('button', { name: 'Review scene tags', exact: true }).click();
  await page.getByLabel('Accept scene tag dark_image', { exact: true }).check();
  await page.getByLabel('Declare scene lighting', { exact: true }).selectOption('night');
  await page.getByLabel('Scene declaration status', { exact: true }).selectOption('reviewed');
  await page.getByLabel('Scene reviewer', { exact: true }).fill('Fixture reviewer declaration');
  await page.getByLabel('Scene review note', { exact: true }).fill('Manual workflow classification of an authored dark drawing, not measured night.');
  await page.getByRole('button', { name: 'Save scene tag review', exact: true }).click();
  await expect(page.getByTestId('scene-review-revision')).toHaveText('1');
  await page.getByLabel('Scene lighting filter', { exact: true }).selectOption('night');
  await expect(page.getByTestId('scene-count-matched')).toHaveText('1');
  let downloading = page.waitForEvent('download');
  await page.getByRole('link', { name: 'Download full scene search JSON', exact: true }).click();
  const nightReport = JSON.parse(readFileSync((await (await downloading).path())!, 'utf8'));
  expect(nightReport.rows).toHaveLength(7);
  expect(nightReport.denominator.unindexed).toBe(1);
  expect(nightReport.denominator.matched).toBe(1);
  await page.getByLabel('Scene lighting filter', { exact: true }).selectOption('all');
  await page.getByLabel('Scene query image', { exact: true }).selectOption(keys.day);
  await expect(page.getByTestId('scene-count-matched')).toHaveText('5');
  await expect(page.getByTestId('scene-card').first()).toHaveAttribute('data-source-key', keys.duplicate);
  await expect(page.getByTestId('scene-card').first().getByTestId('scene-distance')).toHaveText('0.000000');
  await page.getByLabel('Exclude identical pixels', { exact: true }).check();
  await expect(page.getByTestId('scene-count-matched')).toHaveText('4');
  await expect(page.getByTestId('scene-card').first()).toHaveAttribute('data-source-key', keys.near);
  for (const key of [keys.near, keys.dark]) await page.getByLabel(`Select scene result ${key}`, { exact: true }).check();
  await page.getByLabel('Scene collection name', { exact: true }).fill('Visually related scene selection');
  await page.getByLabel('Scene collection purpose', { exact: true }).fill('Keep near and dark appearance candidates for later human review.');
  await page.getByRole('button', { name: 'Create collection from scene selection', exact: true }).click();
  await expect(page.getByTestId('scene-selection-saved')).toContainText('Saved 2 images');
  downloading = page.waitForEvent('download');
  await page.getByRole('link', { name: 'Download selection receipt', exact: true }).click();
  const receiptBytes = readFileSync((await (await downloading).path())!);
  const receipt = JSON.parse(receiptBytes.toString());
  const descriptor = await get(`/api/v1/scene-selections/${receipt.id}`);
  expect(createHash('sha256').update(receiptBytes).digest('hex')).toBe(descriptor.manifest_hash);
  expect(receipt.search.rows).toHaveLength(7);
  expect(receipt.not_selected_keys).toHaveLength(5);
  expect(receipt.labels_copied).toBe(false);
  await page.screenshot({ path: info.outputPath('scene-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+1)).toBeTruthy();
  await page.screenshot({ path: info.outputPath('scene-mobile.png'), fullPage: true });
  await page.getByRole('link', { name: 'Open selected collection', exact: true }).click();
  await expect(page.getByTestId('collection-unique')).toHaveText('2');
  await expect(page.getByRole('heading', { name: 'Scene search selection origin', exact: true })).toBeVisible();
  expect(await (await page.request.get(original.download_url, { headers })).body()).toEqual(originalBytes);
  expect(await (await page.request.get(index.download_url, { headers })).body()).toEqual(indexBytes);
  await page.goto(`/#scene-discovery?index=${indexId}`);
  await expect(page.getByTestId('scene-review-revision')).toHaveText('1');
  if (token) expect((await page.request.get(`/api/v1/scene-indexes/${indexId}`)).status()).toBe(401);
  expect(errors).toEqual([]);
  writeFileSync(info.outputPath('acceptance.json'), JSON.stringify({ index_id: indexId, selection_id: receipt.id,
    collection_id: receipt.collection.id, source_collection_id: fixture.collection_id,
    index_hash: index.manifest_hash, selection_hash: descriptor.manifest_hash,
    query: receipt.search.query, search_hash: receipt.search_hash,
    all_sources: 7, indexed: 6, unindexed: 1, reviewed: 1, selected: 2, not_selected: 5, duplicate_groups: 1,
    nearest_nonduplicate: keys.near, night_is_manual: true, original_bytes_preserved: true, browser_errors: errors }, null, 2));
}
