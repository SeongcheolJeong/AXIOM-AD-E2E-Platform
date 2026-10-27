// Real retained road log -> discovered event -> Case -> two queued controllers -> regression.
// node e2e/log-sim-workflow.mjs RUNTIME OUTPUT
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';

assert(process.argv[2] && process.argv[3], 'Pass runtime and a fresh evidence directory');
const runtime = path.resolve(process.argv[2]), output = path.resolve(process.argv[3]);
const prepared = JSON.parse(fs.readFileSync(path.join(runtime, 'prepared.json'), 'utf8'));
const base = prepared.target_api.replace(/\/api\/v1$/, '');
assert.equal(new URL(base).hostname, '127.0.0.1');
const token = fs.readFileSync(path.join(runtime, 'access/presenter-token'), 'utf8').trim();
fs.mkdirSync(output, { recursive: true });
const receipt = path.join(output, 'browser.json');
const report = fs.existsSync(receipt) ? JSON.parse(fs.readFileSync(receipt, 'utf8')) : {
  project_id: prepared.project_id, keys: {}, checks: [], page_errors: [], started_at: new Date().toISOString(),
};
assert.equal(report.project_id, prepared.project_id);
const persist = () => fs.writeFileSync(receipt, JSON.stringify(report, null, 2) + '\n');
const browser = await chromium.launch({ headless: true, channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
page.setDefaultTimeout(30000);
page.on('pageerror', error => { report.page_errors.push(error.message); persist(); });
await page.addInitScript(value => sessionStorage.setItem('axiom.apiToken', value), token);
const api = async url => {
  const response = await page.request.get(base + '/api/v1' + url, { headers: { Authorization: 'Bearer '+token } });
  assert(response.ok(), `${url}: ${response.status()}`); return response.json();
};
const responseFor = suffix => page.waitForResponse(response =>
  new URL(response.url()).pathname.endsWith(suffix) && response.request().method() === 'POST');
const shot = name => page.screenshot({ path: path.join(output, name + '.png'), fullPage: false });
const checked = name => { report.checks.push(name); persist(); console.log(name); };
await page.route('**/api/v1/log-sim/**', async route => {
  const request = route.request(); const headers = request.headers();
  if (request.method() === 'POST' && headers['idempotency-key']) {
    const id = new URL(request.url()).pathname + '/' + request.postData();
    report.keys[id] ??= headers['idempotency-key']; persist();
    await route.continue({ headers: { ...headers, 'idempotency-key': report.keys[id] } });
  } else await route.continue();
});
try {
  const sources = await api('/log-sim/sources?project_id='+prepared.project_id);
  const road = sources.items.find(item => item.kind === 'nuscenes'); assert(road);
  const url = base + '/#log-sim?' + new URLSearchParams({ project: prepared.project_id, source: road.id });
  report.source_id = road.id;
  await page.goto(url); await page.reload();
  await page.getByRole('region', { name: 'Log Sim automatic event search', exact: true }).waitFor();
  const events = await api(`/log-sim/sources/${road.id}/events?project_id=${prepared.project_id}&kind=nearby_ahead`);
  assert(events.items.length > 0); report.event_analysis = events;
  const event = page.getByRole('button', { name: `Log Sim detected event ${events.items[0].id}`, exact: true });
  await event.click();
  const saving = responseFor('/cases');
  await page.getByRole('button', { name: 'Case 저장', exact: true }).click();
  const saved = await saving; assert(saved.ok(), await saved.text()); report.case = await saved.json(); persist();
  checked('recorded_rule_event_selected_and_case_saved');
  const previewing = responseFor('/comparisons/preview');
  await page.getByRole('button', { name: 'A/B 입력 확인', exact: true }).click();
  const preview = await previewing; assert(preview.ok(), await preview.text()); report.preview = await preview.json();
  assert.equal(report.preview.frame_count, 10); assert(report.preview.case_frame_count > 0);
  const submitting = responseFor('/comparisons');
  await page.getByRole('button', { name: '동일 로그 A/B 실행', exact: true }).click();
  const submitted = await submitting; assert(submitted.ok(), await submitted.text());
  report.comparison = await submitted.json(); persist();
  await page.getByTestId('log-reference-chart').waitFor({ timeout: 120000 });
  report.comparison = await api(`/log-sim/comparisons/${report.comparison.id}?project_id=${prepared.project_id}`);
  assert.equal(report.comparison.result.status, 'comparable');
  assert.equal(report.comparison.result.samples.length, 10);
  assert(report.comparison.result.changed_request_count > 0);
  assert.equal(report.comparison.learned_model, false);
  checked('two_actual_queued_reference_executions_compared_all_original_samples');
  await page.getByTestId('log-reference-replay').scrollIntoViewIfNeeded(); await shot('01-same-log-comparison');
  await page.getByRole('button', { name: 'Log Sim reference sample 5', exact: true }).click();
  assert(Math.abs(Number(await page.getByRole('slider', { name: 'Log Sim playback time' }).inputValue()) - report.comparison.result.samples[5].time_s) < .01);
  checked('request_sample_seeks_original_rgb_timeline');
  const savingRegression = responseFor('/regressions');
  await page.getByRole('button', { name: '반복 시험 저장', exact: true }).click();
  const savedRegression = await savingRegression; assert(savedRegression.ok(), await savedRegression.text());
  report.regression = await savedRegression.json(); persist();
  const running = responseFor('/runs');
  await page.getByRole('button', { name: 'Log Sim rerun regression '+report.regression.id, exact: true }).click();
  const started = await running; assert(started.ok(), await started.text()); report.run = await started.json(); persist();
  await page.getByTestId('log-regression-verdict').filter({ hasText: '요청 기준 충족' }).waitFor({ timeout: 120000 });
  const regressions = await api(`/log-sim/regressions?project_id=${prepared.project_id}&source_id=${road.id}`);
  report.regression = regressions.items.find(item => item.id === report.regression.id);
  assert.equal(report.regression.runs[0].matches_saved_outputs, true);
  assert.equal(report.regression.runs[0].verdict, 'pass');
  assert.notEqual(report.regression.runs[0].comparison_id, report.comparison.id);
  checked('saved_regression_reexecutes_and_matches_pinned_outputs');
  await page.getByTestId('log-regression').first().scrollIntoViewIfNeeded(); await shot('02-regression-result');
  await page.reload();
  await page.getByTestId('log-reference-chart').waitFor();
  await page.getByTestId('log-regression-verdict').filter({ hasText: '요청 기준 충족' }).waitFor();
  assert(page.url().includes('case='));
  checked('case_comparison_and_regression_survive_reload');
  await page.getByRole('button', { name: '원본에서 보기', exact: true }).click();
  await shot('03-original-event');
  assert.deepEqual(report.page_errors, []);
  report.complete = true; report.url = page.url();
} catch (error) {
  report.complete = false; report.error = String(error); await shot('failure').catch(() => {}); throw error;
} finally {
  report.finished_at = new Date().toISOString(); persist(); await browser.close();
}
