/** Explicitly gated normal-browser CARLA acceptance. No network mocking or engine mutation outside UI.
 * Execute only on an operator-owned, commissioned endpoint with its worker already running:
 * AXIOM_STUNT_NATIVE_ACCEPTANCE=execute node stunt-native-acceptance.mjs
 * Or inspect existing actual native runs without submitting any work:
 * AXIOM_STUNT_NATIVE_ACCEPTANCE=retained AXIOM_STUNT_BASELINE_EXECUTION=... AXIOM_STUNT_LOW_EXECUTION=... node stunt-native-acceptance.mjs
 */
import { chromium, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const mode = process.env.AXIOM_STUNT_NATIVE_ACCEPTANCE;
if (!['execute', 'retained'].includes(mode)) {
  console.error('Set AXIOM_STUNT_NATIVE_ACCEPTANCE=execute only after commissioning and operator authorization, or retained with two existing execution IDs.');
  process.exit(2);
}
const base = process.env.AXIOM_STUNT_BROWSER_URL ?? 'http://127.0.0.1:5350';
const project = process.env.AXIOM_STUNT_PROJECT ?? 'default';
const stamp = new Date().toISOString().replaceAll(':', '-');
const output = path.resolve(process.env.AXIOM_STUNT_ACCEPTANCE_OUTPUT ?? `test-results/stunt-native-${stamp}`);
const changes = JSON.parse(process.env.AXIOM_STUNT_BASELINE_CONFIG ?? '{}');
const labels = { mass_kg: '차량 질량', initial_speed_mps: '진입 속도', max_thrust_n: '최대 추력', impulse_budget_ns: '총 충격량 예산', thrust_scale: '추력 배율', target_height_m: '목표 고도', duration_s: '실행 시간' };
for (const key of Object.keys(changes)) if (!(key in labels) && key !== 'dt_s') throw Error(`Unsupported UI override: ${key}`);
fs.mkdirSync(output, { recursive: true });
const report = { schema_version: 'stunt-native-browser-acceptance/1', mode, started_at: new Date().toISOString(), project, base,
  physical_validation: 'not_established', native_execution_verified: false, browser_workflow_verified: false, checks: [], executions: [], console_errors: [] };
const browser = await chromium.launch({ headless: process.env.AXIOM_STUNT_HEADED !== '1' });
const page = await browser.newPage({ viewport: { width: 1600, height: 1100 }, acceptDownloads: true });
page.on('pageerror', error => report.console_errors.push(error.message));
const lab = page.getByRole('region', { name: '추력 차량 시험장', exact: true });
const api = async url => {
  const response = await page.request.get(`${base}/api/v1${url}`);
  if (!response.ok()) throw Error(`API ${response.status()} ${url}: ${await response.text()}`);
  return response.json();
};
async function actualRun(id, expectedLanding) {
  const run = await api(`/experiments/executions/${encodeURIComponent(id)}`);
  expect(run.project_id).toBe(project);
  expect(run.adapter_id).toBe('carla-stunt');
  expect(run.status).toBe('completed');
  expect(run.fidelity).toBe('assumed_vehicle_rigid_body');
  expect(run.summary.physical_validation).toBe('not_established');
  expect(run.summary.demo_landing_passed).toBe(expectedLanding);
  for (const name of ['stunt-trace.json', 'camera-manifest.json', 'external.mp4', 'onboard.mp4'])
    expect(run.artifacts.some(item => item.name === name)).toBe(true);
  const artifact = run.artifacts.find(item => item.name === 'stunt-trace.json');
  const response = await page.request.get(`${base}${artifact.download_url}`);
  expect(response.ok()).toBe(true);
  const trace = await response.json();
  expect(trace.schema_version).toBe('carla-stunt-trace/1');
  expect(trace.design_ref).toEqual(run.summary.design_ref);
  expect(trace.native_identity.server_version).toBeTruthy();
  expect(trace.cleanup_status).toBe('confirmed');
  report.executions.push({ id, input_hash: run.input_hash, manifest_hash: run.manifest_hash, summary: run.summary, artifacts: run.artifacts });
  return run;
}
async function createAndExecute(label, low) {
  if (!low) {
    await lab.getByRole('button', { name: /^01 \/ KICKFLIP/ }).click();
    for (const [key, value] of Object.entries(changes)) {
      if (key === 'dt_s') await lab.getByLabel('물리 시간 간격', { exact: true }).selectOption(String(value));
      else await lab.getByLabel(labels[key], { exact: true }).fill(String(value));
    }
  } else {
    // Same model, map, seed, and configuration; only the thrust scale changes.
    await lab.getByLabel('추력 배율', { exact: true }).fill('0.3');
  }
  await lab.getByLabel('시험 이름', { exact: true }).fill(`Native browser ${label} ${stamp}`);
  await lab.getByRole('button', { name: '개정 저장', exact: true }).click();
  await expect(lab.getByRole('button', { name: '실행 계획 확인', exact: true })).toBeEnabled();
  await lab.getByRole('button', { name: '실행 계획 확인', exact: true }).click();
  await expect(lab.getByText('고정된 실행 계획', { exact: true })).toBeVisible();
  await expect(lab.getByRole('button', { name: 'CARLA 실행', exact: true })).toBeEnabled({ timeout: 60000 });
  const previous = new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('execution');
  await lab.getByRole('button', { name: 'CARLA 실행', exact: true }).click();
  await expect.poll(() => new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('execution'), { timeout: 60000 }).not.toBe(previous);
  const id = new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('execution');
  if (!id) throw Error('UI did not retain a submitted execution ID');
  report.checks.push({ step: 'submitted_through_ui', label, id, url: page.url() });
  await expect.poll(async () => (await api(`/experiments/executions/${id}`)).status, { timeout: 600000, intervals: [1000, 2000, 5000] }).toMatch(/^(completed|failed|cancelled|interrupted)$/);
  return id;
}
try {
  await page.goto(`${base}/#object-sim?${new URLSearchParams({ project, mode: 'stunt' })}`);
  await expect(lab.getByRole('heading', { name: '추력 차량 시험장', exact: true })).toBeVisible();
  let baseline, low;
  if (mode === 'execute') {
    const ready = await api(`/object-sim/stunt-lab/readiness?project_id=${encodeURIComponent(project)}`);
    expect(ready.available).toBe(true); expect(ready.physics_verified).toBe(true);
    baseline = await createAndExecute('baseline', false);
    await actualRun(baseline, true);
    low = await createAndExecute('low thrust', true);
    await actualRun(low, false);
  } else {
    baseline = process.env.AXIOM_STUNT_BASELINE_EXECUTION; low = process.env.AXIOM_STUNT_LOW_EXECUTION;
    if (!baseline || !low) throw Error('Retained mode requires both native execution IDs');
    const left = await actualRun(baseline, true); await actualRun(low, false);
    const ref = left.summary.design_ref;
    await page.goto(`${base}/#object-sim?${new URLSearchParams({ project, mode: 'stunt', design: ref.id, revision: String(ref.revision), execution: baseline })}`);
    await page.reload();
  }
  await lab.getByLabel('Stunt 실행 A', { exact: true }).selectOption(baseline);
  await lab.getByLabel('Stunt 비교 B', { exact: true }).selectOption(low);
  await expect(lab.getByRole('heading', { name: 'A/B 설정 차이', exact: true })).toBeVisible({ timeout: 30000 });
  await expect(lab.getByRole('cell', { name: 'thrust_scale', exact: true })).toBeVisible();
  const leftVideo = lab.getByLabel('실행 A · 저장 영상', { exact: true }), rightVideo = lab.getByLabel('비교 B · 저장 영상', { exact: true });
  await expect(leftVideo).toBeVisible({ timeout: 30000 }); await expect(rightVideo).toBeVisible({ timeout: 30000 });
  await expect.poll(() => leftVideo.evaluate(video => video.readyState), { timeout: 30000 }).toBeGreaterThanOrEqual(2);
  await expect.poll(() => rightVideo.evaluate(video => video.readyState), { timeout: 30000 }).toBeGreaterThanOrEqual(2);
  await lab.getByLabel('Stunt 재생 시간', { exact: true }).fill('2');
  await expect(page).toHaveURL(/time=2.000/);
  for (const [id, video] of [[baseline, leftVideo], [low, rightVideo]]) {
    const row = report.executions.find(item => item.id === id);
    const manifestArtifact = row.artifacts.find(item => item.name === 'camera-manifest.json');
    const manifest = await (await page.request.get(`${base}${manifestArtifact.download_url}`)).json();
    const offset = manifest.frames.find(item => item.camera === 'external' && item.index === 0).time_s;
    await expect.poll(async () => Math.abs(await video.evaluate(element => element.currentTime) - (2 - offset))).toBeLessThan(0.08);
  }
  await leftVideo.evaluate(video => video.play());
  await expect.poll(() => leftVideo.evaluate(video => video.currentTime)).toBeGreaterThan(2.2);
  await leftVideo.evaluate(video => video.pause());
  await lab.getByLabel('Stunt 재생 시간', { exact: true }).fill('2');
  await expect(page).toHaveURL(/time=2.000/);
  const savedURL = page.url();
  await page.screenshot({ path: path.join(output, 'native-ab-desktop.png'), fullPage: true });
  await page.reload();
  await expect(lab.getByLabel('Stunt 재생 시간', { exact: true })).toHaveValue('2');
  await expect(lab.getByLabel('Stunt 실행 A', { exact: true })).toHaveValue(baseline);
  await expect(lab.getByLabel('Stunt 비교 B', { exact: true })).toHaveValue(low);
  expect(page.url()).toBe(savedURL);
  const downloadEvent = page.waitForEvent('download');
  await lab.getByRole('button', { name: '실행 근거 JSON', exact: true }).click();
  const download = await downloadEvent; await download.saveAs(path.join(output, download.suggestedFilename()));
  await lab.getByRole('button', { name: '차량 카메라', exact: true }).click();
  await expect.poll(() => leftVideo.evaluate(video => video.readyState), { timeout: 30000 }).toBeGreaterThanOrEqual(2);
  await page.screenshot({ path: path.join(output, 'native-onboard-ab.png'), fullPage: true });
  await page.setViewportSize({ width: 760, height: 1000 }); await page.mouse.move(700, 0); await page.reload();
  await expect(lab.getByRole('heading', { name: 'A/B 설정 차이', exact: true })).toBeVisible();
  await page.screenshot({ path: path.join(output, 'native-ab-narrow.png'), fullPage: true });
  expect(report.console_errors).toEqual([]);
  report.native_execution_verified = true; report.browser_workflow_verified = true;
  report.url = savedURL;
  report.checks.push({ step: 'native_baseline_low_thrust_ab_restore_export', passed: true });
} catch (error) {
  report.error = String(error); report.url = page.url(); process.exitCode = 1;
  await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }).catch(() => {});
} finally {
  report.finished_at = new Date().toISOString();
  fs.writeFileSync(path.join(output, 'acceptance.json'), JSON.stringify(report, null, 2) + '\n');
  await browser.close(); console.log(JSON.stringify({ output, ...report }, null, 2));
}
