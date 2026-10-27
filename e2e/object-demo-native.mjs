// Opt-in browser acceptance against an explicitly prepared, isolated native demo.
// Submits one edited simulation and one immediately cancelled simulation.
// Usage: node e2e/object-demo-native.mjs .runtime/object-demo-20261009
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';

const runtime = path.resolve(process.argv[2] ?? '');
assert(process.argv[2], 'Pass an explicit isolated demo runtime');
const prepared = JSON.parse(fs.readFileSync(path.join(runtime, 'prepared.json'), 'utf8'));
const rehearsal = JSON.parse(fs.readFileSync(path.join(runtime, 'rehearsal.json'), 'utf8'));
const token = fs.readFileSync(path.join(runtime, 'access/presenter-token'), 'utf8').trim();
assert(prepared.complete && rehearsal.complete);
const base = prepared.target_api.replace(/\/api\/v1$/, '');
assert(new URL(base).hostname === '127.0.0.1', 'Run this rehearsal on the isolated loopback workspace');
const reportPath = path.join(runtime, 'browser-acceptance.json');
const resume = process.argv.includes('--resume');
const report = resume ? JSON.parse(fs.readFileSync(reportPath, 'utf8')) : {
  schema_version: 'object-demo-browser/1', started_at: new Date().toISOString(),
  project_id: prepared.project_id, checks: [], page_errors: [], submissions: [] };
if (resume) {
  assert.equal(report.project_id, prepared.project_id);
  fs.copyFileSync(reportPath, path.join(runtime, `browser-attempt-${Date.now()}.json`));
  delete report.error; delete report.finished_at;
}
const record = (name, details = {}) => { report.checks.push({ name, ...details }); console.log(name); };
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome' });
const page = await browser.newPage({ viewport: { width: 1720, height: 1100 } });
page.setDefaultTimeout(20000);
page.on('pageerror', error => report.page_errors.push(error.message));
page.on('response', response => {
  if (response.request().method() === 'POST' && /\/experiments\/[^/]+\/executions$/.test(new URL(response.url()).pathname))
    report.submissions.push({ url: response.url(), status: response.status() });
});
await page.addInitScript(value => sessionStorage.setItem('axiom.apiToken', value), token);
const screenshot = name => page.screenshot({ path: path.join(runtime, name + '.png') });
const quick = () => base + '/#object-sim?' + new URLSearchParams({ project: prepared.project_id });
const compare = (left, right) => base + '/#object-sim?' + new URLSearchParams({ project: prepared.project_id, mode: 'compare', left, right });
const runReady = () => page.waitForFunction(() => document.querySelector('.demo-run-button') && !document.querySelector('.demo-run-button').disabled);
const viewersReady = () => page.waitForFunction(() => document.querySelectorAll('[aria-label="CARLA city native scene viewer"]').length === 2 && [...document.querySelectorAll('.carla-camera-frame canvas')].filter(element => element.style.visibility === 'visible').length === 2);
try {
  await page.goto(quick());
  await page.getByRole('button', { name: '직접 수정하기' }).first().waitFor();
  assert.equal(await page.getByRole('button', { name: '직접 수정하기' }).count(), 3);
  await screenshot('demo-quick-start');
  record('three_bound_presets');
  await page.getByRole('button', { name: '직접 수정하기' }).first().click();
  await runReady();
  const speed = page.locator('.demo-controls input[type="range"]').first();
  await speed.focus(); await speed.press('ArrowLeft');
  await runReady();
  const parked = page.getByRole('button', { name: '객체 parked 선택' });
  await parked.scrollIntoViewIfNeeded();
  const rect = await parked.locator('rect').boundingBox();
  assert(rect);
  const previewWait = page.waitForResponse(response => response.url().includes('/obstacle_bypass/preview') && response.request().method() === 'POST');
  await page.mouse.move(rect.x + rect.width / 2, rect.y + rect.height / 2);
  await page.mouse.down(); await page.mouse.move(rect.x + rect.width / 2 + 24, rect.y + rect.height / 2, { steps: 8 }); await page.mouse.up();
  const preview = await (await previewWait).json();
  assert.equal(preview.scenario.actors.find(actor => actor.id === 'ego').speed_mps, 4.5);
  assert(preview.scenario.actors.find(actor => actor.id === 'parked').pose.x > 46);
  await runReady(); await page.locator('.demo-editor').scrollIntoViewIfNeeded(); await screenshot('demo-edited-scene');
  record('speed_and_drag_lane_snap', { speed_mps: 4.5, obstacle_pose: preview.scenario.actors.find(actor => actor.id === 'parked').pose });
  if (!report.edited_execution_id) {
    const submit = page.waitForResponse(response => /\/experiments\/[^/]+\/executions$/.test(new URL(response.url()).pathname) && response.request().method() === 'POST');
    await page.getByRole('button', { name: '변경안 실행', exact: true }).click();
    const job = await (await submit).json(); report.edited_execution_id = job.id;
    await page.getByRole('button', { name: 'A/B 나란히 비교', exact: true }).waitFor({ timeout: 240000 });
    assert.equal(report.submissions.length, 1);
    await page.getByRole('button', { name: 'A/B 나란히 비교', exact: true }).click();
  } else {
    await page.goto(compare(rehearsal.trials[0].execution_id, report.edited_execution_id)); await page.reload();
  }
  await viewersReady();
  await page.waitForFunction(() => Number(document.querySelector('[aria-label="비교 공통 시간"]').max) > 40);
  assert.match(await page.getByLabel('변경한 설정').innerText(), /설정 속도|목표 속도|speed_mps/);
  record('one_click_native_execution_and_comparison', { execution_id: report.edited_execution_id });
  const timeline = page.getByRole('slider', { name: '비교 공통 시간' });
  await timeline.scrollIntoViewIfNeeded();
  const track = await timeline.boundingBox(); assert(track);
  await page.mouse.click(track.x + track.width * .4, track.y + track.height / 2);
  await page.waitForFunction(() => [...document.querySelectorAll('.city-compare-telemetry')].every(element => /관측 시각 1[5-9]/.test(element.textContent)));
  await page.getByRole('combobox', { name: '비교 재생 배속' }).selectOption('2');
  const before = Number(await timeline.inputValue());
  await page.getByRole('button', { name: '함께 재생', exact: true }).click();
  await page.waitForFunction(value => Number(document.querySelector('[aria-label="비교 공통 시간"]').value) > value + .6, before);
  await page.getByRole('button', { name: '일시정지', exact: true }).click();
  await page.getByRole('button', { name: '발표 화면', exact: true }).click();
  await screenshot('demo-comparison-presentation');
  record('synchronized_seek_speed_rgb_and_presentation', { common_time_s: Number(await timeline.inputValue()) });
  await timeline.press('End');
  assert.equal(Number(await timeline.inputValue()), 45);
  await page.waitForFunction(() => [...document.querySelectorAll('.city-compare-telemetry')].every(element => /관측 시각 45.00/.test(element.textContent)));
  record('last_retained_frame');
  await page.getByRole('button', { name: '발표 화면 닫기', exact: true }).click();
  const bookmarked = page.url(); await page.reload(); await viewersReady();
  assert.equal(page.url(), bookmarked); record('comparison_url_reopen');
  await page.goto(compare(rehearsal.trials[0].execution_id, rehearsal.trials[4].execution_id)); await page.reload(); await viewersReady();
  await page.getByRole('slider', { name: '비교 공통 시간' }).press('End');
  await page.getByText('관측 종료', { exact: true }).first().waitFor();
  await page.getByText('비교 조건을 확인하세요', { exact: true }).waitFor();
  record('unequal_duration_end_and_incompatible_conditions');
  if (!report.cancelled_execution_id) {
    await page.goto(quick()); await page.reload();
    await page.getByRole('button', { name: '직접 수정하기' }).nth(1).click(); await runReady();
    const cancelSubmit = page.waitForResponse(response => /\/experiments\/[^/]+\/executions$/.test(new URL(response.url()).pathname) && response.request().method() === 'POST');
    await page.getByRole('button', { name: '변경안 실행', exact: true }).click();
    const cancelled = await (await cancelSubmit).json(); report.cancelled_execution_id = cancelled.id;
    await page.getByRole('button', { name: '실행 취소', exact: true }).click();
    await page.getByRole('heading', { name: '취소됨', exact: true }).waitFor({ timeout: 90000 });
    await page.getByRole('button', { name: 'A/B 나란히 비교', exact: true }).click();
  } else {
    await page.goto(compare(rehearsal.trials[2].execution_id, report.cancelled_execution_id)); await page.reload();
  }
  await page.getByRole('article', { name: '실행 B', exact: true }).getByText('실행 취소', { exact: true }).first().waitFor();
  record('cancel_and_partial_comparison', { execution_id: report.cancelled_execution_id });
  await page.goto(quick()); await page.reload();
  await page.getByRole('button', { name: '저장 결과', exact: true }).nth(2).click();
  await page.getByRole('combobox', { name: 'City viewer actor' }).waitFor();
  assert.equal(await page.getByRole('combobox', { name: 'City viewer actor' }).locator('option').count(), 8);
  assert.match(await page.getByLabel('선택한 실제 실행 재생').innerText(), /저장된 실제 실행/);
  await page.getByLabel('선택한 실제 실행 재생').scrollIntoViewIfNeeded(); await screenshot('demo-junction-recording');
  record('eight_actor_saved_native_replay');
  assert.deepEqual(report.page_errors, []);
  report.complete = true;
} catch (error) {
  report.complete = false; report.error = String(error); await screenshot('demo-browser-failure').catch(() => {});
  throw error;
} finally {
  report.finished_at = new Date().toISOString();
  fs.writeFileSync(path.join(runtime, 'browser-acceptance.json'), JSON.stringify(report, null, 2) + '\n');
  await browser.close();
}
