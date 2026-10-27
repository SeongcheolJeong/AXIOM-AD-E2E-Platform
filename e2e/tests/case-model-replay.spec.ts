import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

test("case → full original split → registered ONNX model → COCO evaluation → same-case capture", async ({ page }, info) => {
  test.setTimeout(180_000);
  const errors: string[] = []; page.on("pageerror", e => errors.push(e.message));
  const work = info.outputPath("actual-model-replay");
  execFileSync(process.env.AXIOM_TEST_PYTHON ?? path.join(root, ".venv/bin/python"),
    [path.join(root, "examples/dataset_evaluation_workflow.py"), "--url", "http://127.0.0.1:8010", "--output", work],
    { env: { ...process.env, ADE2E_API_TOKEN: "" }, timeout: 100_000, maxBuffer: 2*1024*1024 });
  const fixture = JSON.parse(readFileSync(path.join(work, "summary.json"), "utf8"));
  const [, a, b] = fixture.evaluation_ids;
  const baseline = await (await page.request.get(`/api/v1/detection-evaluations/${a}`)).json();
  const originalCandidate = await (await page.request.get(`/api/v1/detection-evaluations/${b}`)).json();
  const model = originalCandidate.source_jobs[0].model_snapshot;
  const image = baseline.report.rows[0].image_id;
  const created = await page.request.post('/api/v1/cases', { headers: { 'Idempotency-Key': 'model-replay-browser-case' },
    data: { title: 'Registered model replay investigation', note: 'Authored pixels and pixel-rule models; actual ONNX/COCO execution, not road accuracy.',
      source: { kind: 'evaluation', evaluation_id: a, image_id: image } } });
  expect(created.status()).toBe(201);
  const first = await created.json();
  const originalCase = await (await page.request.get(first.download_url)).body();
  const originalBaseline = await (await page.request.get(baseline.download_url)).body();
  await page.goto(`/#cases?case=${first.id}`);
  await page.getByRole('button', { name: 'Configure recorded model replay', exact: true }).click();
  await page.getByLabel('Model replay baseline', { exact: true }).selectOption(a);
  await page.getByLabel('Model replay candidate ID', { exact: true }).fill(model.id);
  await page.getByLabel('Model replay candidate revision', { exact: true }).fill(String(model.revision));
  await page.getByLabel('Model replay purpose', { exact: true }).fill('Evaluate the alternative model on all six baseline images, not only the case image.');
  await page.getByRole('button', { name: 'Preview full replay workload', exact: true }).click();
  await expect(page.getByTestId('model-replay-preview')).toContainText('6 images');
  expect((await (await page.request.get(`/api/v1/cases/${first.id}/model-replays`)).json()).plans).toHaveLength(0);
  await page.getByLabel('Confirm trusted model replay', { exact: true }).check();
  let dropped = false;
  await page.route(`**/api/v1/cases/${first.id}/model-replays`, async route => {
    if (route.request().method() === 'POST' && !dropped) {
      dropped = true; const response = await route.fetch(); expect(response.status()).toBe(202); await route.abort('failed');
    } else await route.continue();
  });
  await page.getByRole('button', { name: 'Submit recorded inference', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Submit recorded inference', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Submit recorded inference', exact: true }).click();
  await page.unroute(`**/api/v1/cases/${first.id}/model-replays`);
  await expect(page.getByTestId('case-revision')).toHaveText('r2');
  await expect(page.getByTestId('model-replay-inference-state')).toHaveText('completed', { timeout: 40_000 });
  await page.getByRole('button', { name: 'Evaluate stored replay predictions', exact: true }).click();
  await expect(page.getByTestId('case-revision')).toHaveText('r3');
  await expect(page.getByTestId('model-replay-evaluation-state')).toHaveText('completed', { timeout: 40_000 });
  await expect(page.getByTestId('model-replay-comparison-state')).toHaveText('comparable');
  await page.getByRole('button', { name: 'Capture model replay outcome', exact: true }).click();
  await expect(page.getByTestId('case-revision')).toHaveText('r4');
  await expect(page.getByTestId('model-replay-captured')).toBeVisible();
  await expect(page.getByTestId('case-evaluations').locator('tbody tr')).toHaveCount(2);
  const report = await (await page.request.get(`/api/v1/cases/${first.id}/model-replays`)).json();
  expect(report.plans).toHaveLength(1);
  const plan = report.plans[0];
  expect(plan.definition.workload).toMatchObject({ full_split_images: 6, case_images: 1 });
  expect(plan.inference_counts.completed).toBe(6);
  expect(plan.comparison.rows).toHaveLength(6);
  expect(plan.comparison.rows.filter((r: { outcome: string }) => r.outcome === 'more_errors')).toHaveLength(2);
  expect(plan.comparison.metrics.find((m: { name: string }) => m.name === 'ap').delta).toBeLessThan(0);
  const result = await (await page.request.get(`/api/v1/detection-evaluations/${plan.evaluation.job_id}`)).json();
  expect(result.report.metrics).toEqual(originalCandidate.report.metrics);
  const downloading = page.waitForEvent('download');
  await page.getByRole('link', { name: 'Download model replay JSON', exact: true }).click();
  const bytes = readFileSync((await (await downloading).path())!);
  expect(createHash('sha256').update(bytes).digest('hex')).toBe(report.manifest_hash);
  await page.getByRole('link', { name: 'Inspect full replay A/B comparison', exact: true }).click();
  await expect(page.getByTestId('evaluation-comparison-status')).toHaveText('comparable');
  await expect(page.getByTestId('evaluation-comparison-images').locator('tbody tr')).toHaveCount(6);
  await page.goto(`/#cases?case=${first.id}`); await page.reload();
  await expect(page.getByTestId('model-replay-captured')).toBeVisible();
  expect(await (await page.request.get(first.download_url)).body()).toEqual(originalCase);
  expect(await (await page.request.get(baseline.download_url)).body()).toEqual(originalBaseline);
  const current = await (await page.request.get(`/api/v1/cases/${first.id}`)).json();
  expect(current.state).toBe('open');
  await page.screenshot({ path: info.outputPath('model-replay-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  await page.screenshot({ path: info.outputPath('model-replay-mobile.png'), fullPage: true });
  expect(errors).toEqual([]);
  writeFileSync(info.outputPath('acceptance.json'), JSON.stringify({ case_id: first.id, case_revision: 4,
    baseline_id: a, candidate_id: plan.evaluation.job_id, prior_candidate_id: b, model_id: model.id,
    inference_job_id: plan.inference.job_id, images: 6, case_images: 1, more_error_images: 2,
    comparison_status: plan.comparison.status, metrics_match_independent_candidate: true,
    report_hash: report.manifest_hash, report_url: report.download_url,
    old_case_and_evaluation_preserved: true, duplicate_after_lost_response: false, browser_errors: errors }, null, 2));
});
