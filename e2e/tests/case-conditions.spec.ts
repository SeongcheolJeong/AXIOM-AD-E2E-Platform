import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { createHash, randomUUID } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
test("actual model miss → reviewed conditions → case search → full-denominator coverage and pinned export", async ({ page }, info) => {
  test.setTimeout(180_000);
  const errors: string[] = []; page.on("pageerror", e => errors.push(e.message));
  const output = info.outputPath("actual-condition-workflow");
  execFileSync(process.env.AXIOM_TEST_PYTHON ?? path.join(root, ".venv/bin/python"),
    [path.join(root, "examples/dataset_evaluation_workflow.py"), "--url", "http://127.0.0.1:8010", "--output", output],
    { env: { ...process.env, ADE2E_API_TOKEN: "" }, timeout: 100_000, maxBuffer: 2*1024*1024 });
  const original = JSON.parse(readFileSync(path.join(output, "summary.json"), "utf8"));
  const evaluation = await (await page.request.get(`/api/v1/detection-evaluations/${original.evaluation_ids[2]}`)).json();
  const failure = evaluation.report.rows.find((r: { diagnostics: { fn: number } | null }) => (r.diagnostics?.fn ?? 0) > 0);
  const evaluationBytes = await (await page.request.get(evaluation.download_url)).body();
  const create = async (title: string, source: object) => {
    const response = await page.request.post('/api/v1/cases', { headers: { 'Idempotency-Key': randomUUID() },
      data: { title, note: 'Authored pixels and condition declarations for workflow checks, not road accuracy.', source } });
    expect(response.status()).toBe(201); return response.json();
  };
  const source = { kind: 'evaluation', evaluation_id: evaluation.id, image_id: failure.image_id };
  const first = await create('Condition investigation A', source);
  const draft = await create('Condition investigation draft', source);
  const raw = await create('Unclassified log interval', { kind: 'recording', recording_id: first.segment.recording_id,
    channel_id: first.segment.channel_id, start_time_ns: '0', end_time_ns: '18446744073709551615' });
  const firstBytes = await (await page.request.get(first.download_url)).body();
  const taxonomy = await (await page.request.get('/api/v1/case-condition-taxonomy')).json();
  const declaration = { expected_revision: 1, taxonomy_id: taxonomy.id, taxonomy_revision: taxonomy.revision,
    taxonomy_hash: taxonomy.manifest_hash, values: { lighting: 'low_light', target: 'other', target_size: 'medium' },
    author: 'Test author declaration', note: 'Test-only lighting classification; original rectangle area is medium. No automatic scene inference.', review_status: 'draft' };
  expect((await page.request.post(`/api/v1/cases/${draft.id}/conditions`, { data: declaration, headers: { 'Idempotency-Key': randomUUID() } })).status()).toBe(201);
  await page.goto(`/#cases?case=${first.id}`);
  await expect(page.getByTestId('case-condition-state')).toHaveText('unclassified');
  await page.getByRole('button', { name: 'Edit scene conditions', exact: true }).click();
  for (const [key, value] of Object.entries(declaration.values)) await page.getByLabel(`Case condition ${key}`, { exact: true }).selectOption(value);
  await page.getByLabel('Condition reviewer', { exact: true }).fill(declaration.author);
  await page.getByLabel('Condition source note', { exact: true }).fill(declaration.note);
  await page.getByLabel('Confirm condition review', { exact: true }).check();
  // Editing the evidence invalidates the review checkbox until explicitly reconfirmed.
  await page.getByLabel('Case condition lighting', { exact: true }).selectOption('night');
  await expect(page.getByLabel('Confirm condition review', { exact: true })).not.toBeChecked();
  await page.getByLabel('Case condition lighting', { exact: true }).selectOption('low_light');
  await page.getByLabel('Confirm condition review', { exact: true }).check();
  let lost = false;
  await page.route(`**/api/v1/cases/${first.id}/conditions`, async route => {
    if (!lost) { lost = true; const response = await route.fetch(); expect(response.status()).toBe(201); await route.abort('failed'); }
    else await route.continue();
  });
  await page.getByRole('button', { name: 'Publish condition revision', exact: true }).click();
  await page.getByRole('button', { name: 'Retry condition save', exact: true }).click();
  await page.unroute(`**/api/v1/cases/${first.id}/conditions`);
  await expect(page.getByTestId('case-revision')).toHaveText('r2');
  await expect(page.getByTestId('case-condition-state')).toHaveText('reviewed');
  await page.reload(); await expect(page.getByTestId('case-condition-state')).toHaveText('reviewed');
  expect(await (await page.request.get(first.download_url)).body()).toEqual(firstBytes);
  expect(await (await page.request.get(evaluation.download_url)).body()).toEqual(evaluationBytes);

  await page.getByLabel('Condition recording scope', { exact: true }).fill(first.segment.recording_id);
  await page.getByRole('tab', { name: 'Condition coverage', exact: true }).click();
  await expect(page.getByTestId('coverage-total-cases')).toHaveText('3');
  await expect(page.getByTestId('coverage-classified-cases')).toHaveText('1');
  await expect(page.getByTestId('coverage-unclassified-cases')).toHaveText('2');
  await expect(page.getByTestId('condition-grid').locator('tbody tr')).toHaveCount(36);
  await expect(page.getByTestId('coverage-source-duplicates')).toContainText('1 repeated-selection');
  await page.getByLabel('Coverage case filter', { exact: true }).selectOption('unclassified');
  await expect(page.getByTestId('coverage-case-table').locator('tbody tr')).toHaveCount(2);
  const descriptor = await (await page.request.get(`/api/v1/case-condition-coverage?recording_id=${first.segment.recording_id}`)).json();
  const downloading = page.waitForEvent('download');
  await page.getByRole('link', { name: 'Download condition coverage JSON', exact: true }).click();
  const bytes = readFileSync((await (await downloading).path())!);
  expect(createHash('sha256').update(bytes).digest('hex')).toBe(descriptor.manifest_hash);
  expect(JSON.parse(bytes.toString()).cases).toHaveLength(3);
  writeFileSync(info.outputPath('coverage.json'), bytes);
  await page.getByLabel('Coverage case filter', { exact: true }).selectOption('all');
  await page.screenshot({ path: info.outputPath('condition-coverage-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  await page.screenshot({ path: info.outputPath('condition-coverage-mobile.png'), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1080 });
  await page.getByRole('button', { name: 'Inspect condition low_light other medium', exact: true }).click();
  await expect(page.locator('.case-library .case-card')).toHaveCount(1);
  await expect(page.locator('.case-library .case-card')).toContainText('Condition investigation A');
  await page.getByLabel('Filter diagnostic tag', { exact: true }).selectOption('has_false_negative');
  await expect(page.locator('.case-library .case-card')).toHaveCount(1);
  await page.getByRole('tab', { name: 'Condition coverage', exact: true }).click();
  await expect(page.getByTestId('coverage-total-cases')).toHaveText('3');
  // A failed new scope must never display the old scope's report as current.
  await page.route('**/api/v1/case-condition-coverage?recording_id=rec_unavailable', route => route.fulfill({ status: 503, json: { detail: 'Coverage scope unavailable' } }));
  await page.getByLabel('Condition recording scope', { exact: true }).fill('rec_unavailable');
  await expect(page.getByText('Coverage scope unavailable', { exact: true })).toBeVisible();
  await expect(page.getByTestId('condition-coverage-report')).toHaveCount(0);
  expect(errors).toEqual([]);
  writeFileSync(info.outputPath('acceptance.json'), JSON.stringify({ case_id: first.id, draft_case_id: draft.id,
    raw_case_id: raw.id, recording_id: first.segment.recording_id, evaluation_id: evaluation.id,
    taxonomy_hash: taxonomy.manifest_hash, coverage_hash: descriptor.manifest_hash, total_cases: 3,
    classified_cases: 1, unclassified_cases: 2, grid_cells: 36, source_selections: 2,
    lost_response_deduplicated: true, original_case_and_evaluation_unchanged: true, browser_errors: errors }, null, 2));
});
