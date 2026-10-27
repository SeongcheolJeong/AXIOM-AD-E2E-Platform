// Explicit local rehearsal: save a Case, submit one native variant, compare,
// then investigate the retained road clip. Reuse the same receipt with --resume.
// node e2e/log-sim-native.mjs .runtime/object-demo-20261009 .runtime/log-sim-demo
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';

assert(process.argv[2] && process.argv[3], 'Pass the prepared local runtime and a report directory');
const runtime = path.resolve(process.argv[2]), output = path.resolve(process.argv[3]);
const prepared = JSON.parse(fs.readFileSync(path.join(runtime, 'prepared.json'), 'utf8'));
const base = prepared.target_api.replace(/\/api\/v1$/, '');
assert.equal(new URL(base).hostname, '127.0.0.1');
const token = fs.readFileSync(path.join(runtime, 'access/presenter-token'), 'utf8').trim();
fs.mkdirSync(output, { recursive: true });
const receipt = path.join(output, 'browser.json');
const resume = process.argv.includes('--resume');
if (fs.existsSync(receipt) && !resume) throw new Error('Receipt already exists. Use --resume to avoid a duplicate native job.');
const report = resume ? JSON.parse(fs.readFileSync(receipt, 'utf8')) : {
  schema_version: 'log-sim-browser/1', project_id: prepared.project_id,
  started_at: new Date().toISOString(), checks: [], page_errors: [], submissions: [],
};
assert.equal(report.project_id, prepared.project_id);
if (resume) {
  fs.copyFileSync(receipt, path.join(output, `browser-attempt-${Date.now()}.json`));
  delete report.error; delete report.finished_at;
}
const persist = () => fs.writeFileSync(receipt, JSON.stringify(report, null, 2) + '\n');
const record = (name, detail = {}) => { report.checks.push({ name, ...detail }); persist(); console.log(name); };
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome' });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
page.setDefaultTimeout(30000);
page.on('pageerror', error => { report.page_errors.push(error.message); persist(); });
await page.addInitScript(value => sessionStorage.setItem('axiom.apiToken', value), token);
const api = async url => {
  const response = await page.request.get(base + '/api/v1' + url, { headers: { Authorization: 'Bearer ' + token } });
  assert(response.ok(), `${url}: ${response.status()}`); return response.json();
};
const open = async fields => {
  await page.goto(base + '/#log-sim?' + new URLSearchParams({ project: prepared.project_id, ...fields }));
  await page.reload();
};
const screenshot = name => page.screenshot({ path: path.join(output, `${name}.png`), fullPage: false });
const responseFor = suffix => page.waitForResponse(response =>
  new URL(response.url()).pathname.endsWith(suffix) && response.request().method() === 'POST');

try {
  await open({});
  await page.getByRole('button', { name: 'Case 저장', exact: true }).waitFor();
  await page.waitForFunction(() => [...document.querySelectorAll('.carla-camera-frame canvas')]
    .some(canvas => canvas.style.visibility === 'visible' && canvas.getAttribute('aria-label')?.includes('chase')));
  await page.getByRole('button', { name: 'Log Sim play', exact: true }).click();
  await page.waitForFunction(() => Number(document.querySelector('[aria-label="Log Sim playback time"]').value) > .6);
  await page.getByRole('button', { name: 'Log Sim pause', exact: true }).click();
  const events = page.locator('[aria-label^="Log Sim event "]');
  assert(await events.count() > 0, 'Recorded events must be selectable');
  await events.first().click();
  await screenshot('01-log-investigation');
  record('original_chase_rgb_and_recorded_event_selection');

  if (!report.native_submission) {
    {
      // Restore UI Case state on an interrupted pre-submit rehearsal. The
      // request body is the same selected event; keep its original key.
      await page.route('**/log-sim/sources/carla-urban-right-turn/cases', async route => {
        const headers = route.request().headers();
        report.carla_case_key ??= headers['idempotency-key']; persist();
        await route.continue({ headers: { ...headers, 'idempotency-key': report.carla_case_key } });
      });
      const saving = responseFor('/cases');
      await page.getByRole('button', { name: 'Case 저장', exact: true }).click();
      const saved = await saving; assert(saved.ok(), await saved.text());
      report.carla_case = await saved.json(); persist();
    }
    record('case_saved_from_original_source');
    const waitSlider = page.getByRole('slider', { name: /앞차 정지/ });
    await waitSlider.press('Home'); await waitSlider.press('ArrowRight'); await waitSlider.press('ArrowRight');
    assert.equal(await waitSlider.inputValue(), '2');
    const previewing = responseFor('/log-sim/variants/preview');
    await page.getByRole('button', { name: '변경 내용 확인', exact: true }).click();
    const preview = await previewing; assert(preview.ok(), await preview.text());
    report.preview = await preview.json(); persist();
    await screenshot('02-variant-preview');
    const submitting = responseFor('/log-sim/variants');
    await page.getByRole('button', { name: 'CARLA로 변경안 실행', exact: true }).click();
    const submitted = await submitting; assert(submitted.ok(), await submitted.text());
    report.native_submission = await submitted.json();
    report.submissions.push({ at: new Date().toISOString(), status: submitted.status() });
    persist();
    await page.waitForURL(url => url.hash.includes('variant='));
    report.submitted_url = page.url(); persist();
  } else if (report.submitted_url) {
    await page.goto(report.submitted_url); await page.reload();
  }
  // Polling is owned by the normal UI; this only waits for its completed action.
  await page.waitForFunction(() => [...document.querySelectorAll('button')]
    .some(button => button.textContent?.trim() === '원본과 비교' && !button.disabled), undefined, { timeout: 600000 });
  const execution = await api('/experiments/executions/' + report.native_submission.execution_id);
  assert.equal(execution.status, 'completed');
  assert.equal(execution.outcome_uncertain, false);
  assert.match(execution.manifest_hash, /^[a-f0-9]{64}$/);
  report.native_execution = execution; persist();
  await page.getByRole('button', { name: '원본과 비교', exact: true }).click();
  await page.getByRole('slider', { name: '비교 공통 시간' }).waitFor();
  await page.waitForFunction(() => [...document.querySelectorAll('.carla-camera-frame canvas')]
    .filter(canvas => canvas.style.visibility === 'visible').length === 2);
  const present = page.getByRole('button', { name: '발표 화면', exact: true });
  if (await present.isVisible()) await present.click();
  const timeline = page.getByRole('slider', { name: '비교 공통 시간' });
  const track = await timeline.boundingBox(); assert(track);
  await page.mouse.click(track.x + track.width * 7 / 24, track.y + track.height / 2);
  await page.waitForFunction(() => [...document.querySelectorAll('.carla-camera-frame canvas')]
    .filter(canvas => canvas.style.visibility === 'visible').length === 2);
  const beforePlay = Number(await timeline.inputValue());
  await page.getByRole('button', { name: '함께 재생', exact: true }).click();
  await page.waitForFunction(before => Number(document.querySelector('[aria-label="비교 공통 시간"]').value) > before + .6, beforePlay);
  await page.getByRole('button', { name: '일시정지', exact: true }).click();
  await screenshot('03-native-comparison');
  record('native_variant_completed_and_original_ab_visible');
  report.comparison_url = page.url(); persist();
  await page.reload();
  await page.getByRole('slider', { name: '비교 공통 시간' }).waitFor();
  record('comparison_survives_refresh');

  await open({});
  const sources = await api('/log-sim/sources?project_id=' + encodeURIComponent(prepared.project_id));
  const road = sources.items.find(source => source.kind === 'nuscenes' || source.kind === 'road' || source.kind === 'road_recording');
  assert(road, 'Registered original road source is required for this rehearsal');
  await page.getByRole('button', { name: `Log Sim source ${road.id}`, exact: true }).click();
  await page.getByRole('button', { name: 'Case 저장', exact: true }).waitFor();
  await page.waitForFunction(() => {
    const image = document.querySelector('.log-road-image img');
    return image?.complete && image.naturalWidth === 1600 && image.naturalHeight === 900;
  });
  const roadTime = page.getByRole('slider', { name: 'Log Sim playback time' });
  await page.getByRole('button', { name: 'Log Sim play', exact: true }).click();
  await page.waitForFunction(() => Number(document.querySelector('[aria-label="Log Sim playback time"]').value) > .6);
  await page.getByRole('button', { name: 'Log Sim pause', exact: true }).click();
  await page.waitForFunction(() => {
    const image = document.querySelector('.log-road-image img');
    return image?.complete && image.naturalWidth === 1600;
  });
  await page.evaluate(() => window.scrollTo(0, 0));
  await screenshot('04-road-investigation');
  if (!report.road_case) {
    const saving = responseFor('/cases');
    await page.getByRole('button', { name: 'Case 저장', exact: true }).click();
    const saved = await saving; assert(saved.ok(), await saved.text());
    report.road_case = await saved.json(); persist();
  }
  record('original_road_playback_and_case_saved', { source_id: road.id });
  assert.deepEqual(report.page_errors, []);
  report.complete = true;
} catch (error) {
  report.complete = false; report.error = String(error);
  await screenshot('failure').catch(() => {}); throw error;
} finally {
  report.finished_at = new Date().toISOString(); persist(); await browser.close();
}
