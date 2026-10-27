import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import crypto from 'node:crypto';

const acceptancePath = process.env.AXIOM_NATIVE_CAMERA_PRODUCT_ACCEPTANCE;
const specificationPath = process.env.AXIOM_NATIVE_CAMERA_SPECIFICATION;

test('actual CameraE2E ISP execution is authored and inspected through normal product UI', async ({ page }) => {
  test.skip(!acceptancePath || !specificationPath, 'Opt-in requires a retained actual CameraE2E product receipt and its original specification; no fixture fallback.');
  test.setTimeout(90_000);
  const acceptance = JSON.parse(fs.readFileSync(acceptancePath!, 'utf8'));
  const original = acceptance.execution;
  expect(original.status).toBe('completed');
  expect(original.artifacts.length).toBeGreaterThan(10);
  expect(original.adapter_id).toBe('camera-e2e-retained-isp');
  await page.goto(`/#experiments?execution=${encodeURIComponent(original.id)}`);
  await expect(page.getByLabel('Execution type')).toHaveValue('isp_compare');
  await expect(page.getByLabel('Execution adapter')).toHaveValue('camera-e2e-retained-isp');
  await expect(page.getByRole('heading', { name: 'Retained native images', exact: true })).toBeVisible();
  const images = original.artifacts.filter((item: {media_type: string}) => item.media_type === 'image/png');
  const baseline = images.find((item: {name: string}) => /baseline-rgb-000/.test(item.name));
  const candidate = images.find((item: {name: string}) => /candidate-001-rgb-003/.test(item.name));
  expect(baseline).toBeTruthy(); expect(candidate).toBeTruthy();
  await page.getByLabel('Native image artifact').selectOption(baseline.id);
  await expect(page.getByRole('img', { name: `Retained native artifact ${baseline.name}`, exact: true })).toBeVisible();
  await page.getByLabel('Native image artifact').selectOption(candidate.id);
  await expect(page.getByRole('img', { name: `Retained native artifact ${candidate.name}`, exact: true })).toBeVisible();
  const native = await page.request.get(candidate.download_url);
  expect(native.ok()).toBe(true);
  const raw = await native.body();
  expect(raw.length).toBe(candidate.size_bytes);
  expect(crypto.createHash('sha256').update(raw).digest('hex')).toBe(candidate.sha256);
  const imageRow = page.locator('.expansion-artifact').filter({ has: page.getByText(candidate.name, {exact: true}) });
  const pngDownload = page.waitForEvent('download');
  await imageRow.getByRole('button', {name: 'Download', exact: true}).click();
  expect((await pngDownload).suggestedFilename()).toBe(candidate.name);
  await expect(page.getByText('Native evidence downloaded and SHA-256 verified.')).toBeVisible();
  const existing = await (await page.request.get(`/api/v1/experiments/executions/${original.id}`)).json();
  expect(existing.manifest_hash).toBe(original.manifest_hash);
  expect(existing.summary.raw_sha256).toBe(original.summary.raw_sha256);
  // The configured native service can reuse its content-identical execution;
  // no fixture or uploaded receipt can substitute for external processing.
  await page.getByLabel('Experiment name').fill('Browser accepted retained RAW ISP A/B');
  await page.getByLabel('Comparison contract').selectOption('same_raw_isp_ab');
  await page.getByLabel('Experiment input JSON').fill(JSON.stringify({specification: JSON.parse(fs.readFileSync(specificationPath!, 'utf8'))}, null, 2));
  await expect(page.getByRole('button', {name: 'Preview workload', exact: true})).toBeEnabled();
  await page.getByRole('button', {name: 'Preview workload', exact: true}).click();
  await expect(page.getByText('Preview SHA-256:', {exact: false})).toBeVisible();
  await page.getByRole('button', {name: 'Save frozen experiment', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Submit execution', exact: true})).toBeEnabled();
  const submitResponse = page.waitForResponse((response) => response.url().includes('/api/v1/experiments/') && response.url().endsWith('/executions') && response.request().method() === 'POST');
  await page.getByRole('button', {name: 'Submit execution', exact: true}).click();
  const submitted = await (await submitResponse).json();
  let latest: any;
  await expect.poll(async () => {
    latest = await (await page.request.get(`/api/v1/experiments/executions/${submitted.id}`)).json();
    return latest.status;
  }, {timeout: 60_000}).toBe('completed');
  await page.getByRole('button', {name: 'Refresh', exact: true}).click();
  await expect(page.getByLabel('Native image artifact')).toBeVisible();
  expect(latest.summary.raw_sha256).toBe(original.summary.raw_sha256);
  expect(latest.summary.selected_array_sha256).toBe(original.summary.selected_array_sha256);
  expect(latest.summary.verification_verdict).toBe('not_evaluated');
  expect(latest.artifacts.length).toBe(original.artifacts.length);
  const sealedDownload = page.waitForEvent('download');
  await page.getByRole('button', {name: 'Download sealed receipt', exact: true}).click();
  expect((await sealedDownload).suggestedFilename()).toBe(`${submitted.id}.json`);
  await expect(page.getByText('Native evidence downloaded and SHA-256 verified.')).toBeVisible();
  const image = latest.artifacts.find((item: {name:string}) => /candidate-001-rgb-003/.test(item.name));
  await page.getByLabel('Native image artifact').selectOption(image.id);
  await expect(page.getByRole('img', {name: `Retained native artifact ${image.name}`, exact:true})).toBeVisible();
  await page.getByRole('region', {name:'Retained native image viewer', exact:true}).screenshot({path:test.info().outputPath('native-camera-isp-images.png')});
  fs.writeFileSync(test.info().outputPath('native-camera-browser-acceptance.json'), JSON.stringify({
    schema_version:'camera-product-browser-acceptance/1',status:'passed',actual_receipt_no_fixture_fallback:true,
    original_job_id:original.id,original_manifest_hash:original.manifest_hash,
    browser_job_id:latest.id,browser_manifest_hash:latest.manifest_hash,raw_sha256:latest.summary.raw_sha256,
    selected_array_sha256:latest.summary.selected_array_sha256,native_artifact_count:latest.artifacts.length,
    external_execution_id:latest.summary.external_execution_id,verification_verdict:latest.summary.verification_verdict,
    preview_image:{name:image.name,sha256:image.sha256,size_bytes:image.size_bytes},
    workflow:['inspect_original_actual_receipt','select_baseline_and_candidate_original_pixels','download_native_png_sha_verified','author_json_specification','preview','save_frozen_intent','submit_durable_queue','inspect_actual_external_receipt','download_sealed_sha_verified_receipt']
  },null,2));
});
