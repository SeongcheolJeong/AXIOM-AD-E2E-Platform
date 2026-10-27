// Recheck retained native A/B in a fresh participant browser. Optional cancellation
// uses the existing immutable experiment through normal UI; no fabricated drafts.
import { chromium, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';

const option = key => { const index = process.argv.indexOf(key); return index < 0 ? undefined : process.argv[index + 1]; };
const runtime = path.resolve(process.argv[2] ?? '');
assert(process.argv[2] && option('--base-url') && option('--invite-file') && option('--report-file'));
assert(process.argv.includes('--no-submit') || process.argv.includes('--cancel-existing'), 'Choose retained-only or one cancellation explicitly');
const base = option('--base-url'); assert.equal(new URL(base).hostname, '127.0.0.1');
const invitation = JSON.parse(fs.readFileSync(option('--invite-file'), 'utf8'));
const previous = JSON.parse(fs.readFileSync(option('--report-file'), 'utf8'));
const project = new URLSearchParams(invitation.start_page.split('?')[1]).get('project');
const baseline = previous.baseline.id, executionId = previous.execution_id;
const output = path.join(runtime, 'browser');
const report = { schema_version: 'object-beta-native-followup/1', started_at: new Date().toISOString(), checks: [], submissions: [], page_errors: [], asset_responses: [] };
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } }); page.setDefaultTimeout(30000);
page.on('pageerror', error => report.page_errors.push(error.message));
page.on('response', async response => {
  const route = new URL(response.url()).pathname;
  if (/\/assets\/kenney-car-kit\//.test(route)) report.asset_responses.push({ path: route, status: response.status() });
  if (/\/experiments\/[^/]+\/executions$/.test(route) && response.request().method() === 'POST') report.submissions.push({ status: response.status(), id: (await response.json()).id });
});
const get = route => page.evaluate(async route => { const response = await fetch(`/api/v1${route}`); if (!response.ok) throw new Error(`GET ${route}: ${response.status}`); return response.json(); }, route);
const record = (name, detail = {}) => { report.checks.push({ name, ...detail }); console.log(name); };
const rgb = letter => expect(page.getByLabel(`실행 ${letter}`, { exact: true }).getByRole('img', { name: /^CARLA .* 원본 프레임 / }).first()).toBeVisible({ timeout: 60000 });
try {
  await page.goto(base); await page.getByLabel('접속 비밀번호').fill(invitation.password); await page.locator('form button[type="submit"]').click(); await page.waitForLoadState('domcontentloaded');
  const execution = await get(`/experiments/executions/${executionId}`);
  assert.equal(execution.project_id, project); assert.equal(execution.status, 'completed'); assert.equal(execution.manifest_hash, previous.execution.manifest_hash);
  const compareUrl = `${base}/#object-sim?${new URLSearchParams({ project, mode: 'compare', origin: 'beta', left: baseline, right: executionId })}`;
  await page.goto(compareUrl); await page.getByRole('slider', { name: '비교 공통 시간', exact: true }).waitFor({ timeout: 60000 }); await rgb('A'); await rgb('B');
  await expect.poll(() => report.asset_responses.some(item => item.path.endsWith('/sedan.glb') && item.status === 200)).toBe(true);
  const changes = page.getByLabel('변경한 설정', { exact: true });
  await expect(changes).toContainText('경유점');
  assert((await changes.boundingBox()).height < 650, 'Default comparison configuration must not bury native RGB under raw route JSON');
  const exact = changes.locator('.object-compare-exact-diff'); await expect(exact).not.toHaveAttribute('open');
  await exact.getByText('원본 설정 차이 보기', { exact: true }).click(); await expect(exact.locator('pre')).toContainText('"path": "/actors/ego/route"');
  await exact.getByText('원본 설정 차이 보기', { exact: true }).click();
  record('compact_route_summary_with_collapsed_exact_diff');
  await page.screenshot({ path: path.join(output, 'beta-native-comparison-recovered.png'), fullPage: true });
  await page.reload(); await page.getByRole('slider', { name: '비교 공통 시간', exact: true }).waitFor({ timeout: 60000 }); await rgb('A'); await rgb('B');
  assert.equal(page.url(), compareUrl); assert.deepEqual(report.page_errors, []); assert(report.asset_responses.every(item => item.status === 200));
  record('native_ab_rgb_and_3d_assets_restore_without_page_errors', { baseline_execution_id: baseline, edited_execution_id: executionId });
  const observations = await get(`/object-sim/observations?project_id=${project}&execution_id=${executionId}`);
  const observation = observations.items.find(item => item.id === previous.observation_id); assert(observation); assert.deepEqual(observation.scenario_ref, previous.behavior.scenario_ref);
  record('previous_native_observation_retained_with_exact_revision', { observation_id: observation.id });
  if (previous.cancelled_execution_id) {
    const cancelled = await get(`/experiments/executions/${previous.cancelled_execution_id}`);
    assert.equal(cancelled.status, 'cancelled'); assert.equal(cancelled.input_hash, execution.input_hash); assert.equal(cancelled.outcome_uncertain, false);
    record('cancelled_beta_rerun_receipt_is_certain_and_same_input', { execution_id: cancelled.id, status: cancelled.status, outcome_uncertain: cancelled.outcome_uncertain, evidence_eligible: cancelled.evidence_eligible, error: cancelled.error, summary: cancelled.summary });
  }

  if (process.argv.includes('--cancel-existing')) {
    await page.goto(`${base}/#experiments?${new URLSearchParams({ project, kind: 'object_sim', adapter: 'carla-city-object' })}`);
    await page.locator('.expansion-list button').filter({ hasText: execution.experiment_id }).click();
    const submit = page.getByRole('button', { name: 'Submit execution', exact: true }); await expect(submit).toBeEnabled();
    const submitted = page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/experiments/${execution.experiment_id}/executions` && response.request().method() === 'POST');
    await submit.click(); const response = await submitted; assert.equal(response.status(), 202); const rerun = await response.json(); assert.notEqual(rerun.id, executionId); report.cancelled_execution_id = rerun.id;
    await page.getByRole('button', { name: 'Cancel execution', exact: true }).click();
    let cancelled;
    await expect.poll(async () => { cancelled = await get(`/experiments/executions/${rerun.id}`); return cancelled.status; }, { timeout: 120000, intervals: [1500, 3000] }).toBe('cancelled');
    assert.equal(cancelled.input_hash, execution.input_hash); assert.equal(cancelled.experiment_id, execution.experiment_id);
    await page.reload(); await page.locator('.expansion-list button').filter({ hasText: execution.experiment_id }).click();
    await expect(page.locator('.expansion-workspace')).toContainText(rerun.id); await expect(page.locator('.expansion-workspace')).toContainText('cancelled');
    await page.screenshot({ path: path.join(output, 'beta-existing-revision-cancelled.png'), fullPage: true });
    record('same_immutable_experiment_ui_rerun_cancel_and_reload', { execution_id: rerun.id, experiment_id: execution.experiment_id, input_hash: cancelled.input_hash, surface: 'Experiments UI; beta draft was not reconstructed' });
  }
  assert.equal(report.submissions.length, process.argv.includes('--cancel-existing') ? 1 : 0); assert.deepEqual(report.page_errors, []);
  assert.equal(await page.evaluate(() => sessionStorage.getItem('axiom.apiToken')), null); report.complete = true;
} catch (error) { report.complete = false; report.error = String(error); await page.screenshot({ path: path.join(output, 'beta-native-followup-failure.png'), fullPage: true }).catch(() => {}); throw error; }
finally {
  report.finished_at = new Date().toISOString(); fs.writeFileSync(path.join(output, 'native-followup-acceptance.json'), JSON.stringify(report, null, 2) + '\n');
  const privateDir = path.join(runtime, 'browser-private'); fs.mkdirSync(privateDir, { recursive: true, mode: 0o700 }); const stateFile = path.join(privateDir, 'followup-storage.json');
  await page.context().storageState({ path: stateFile }).then(() => fs.chmodSync(stateFile, 0o600)).catch(() => {}); await browser.close();
}
