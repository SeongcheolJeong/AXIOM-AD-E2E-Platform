// Read-only browser acceptance against explicitly selected retained native results.
// node e2e/object-story-native.mjs .runtime/object-demo-20261009 .runtime/object-story-20261009
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';

assert(process.argv[2] && process.argv[3], 'Pass demo runtime and filmed story receipt directory');
const runtime = path.resolve(process.argv[2]), output = path.resolve(process.argv[3]);
const reportName = process.env.OBJECT_STORY_REPORT ?? 'browser-acceptance.json';
const prepared = JSON.parse(fs.readFileSync(path.join(runtime, 'prepared.json'), 'utf8'));
const filmed = JSON.parse(fs.readFileSync(path.join(output, 'camera-rehearsal.json'), 'utf8'));
assert(filmed.complete && filmed.baselines_published);
const token = fs.readFileSync(path.join(runtime, 'access/presenter-token'), 'utf8').trim();
const base = prepared.target_api.replace(/\/api\/v1$/, '');
assert.equal(new URL(base).hostname, '127.0.0.1');
const [bypassA, bypassB, gapA, gapB, junction] = filmed.trials.map(item => item.execution_id);
const report = { schema_version: 'object-story-browser/1', started_at: new Date().toISOString(),
  project_id: prepared.project_id, checks: [], page_errors: [], asset_responses: [], submissions: [] };
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome' });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
page.setDefaultTimeout(30000);
await page.addInitScript(value => sessionStorage.setItem('axiom.apiToken', value), token);
page.on('pageerror', error => report.page_errors.push(error.message));
page.on('response', response => {
  if (response.url().includes('/assets/kenney-car-kit/')) report.asset_responses.push({ url: response.url(), status: response.status() });
  if (response.request().method() === 'POST' && /\/executions$/.test(new URL(response.url()).pathname))
    report.submissions.push(response.url());
});
const route = (mode, left, right, perf = false) => base + (perf ? '/?cityPerf=1' : '/') + '#object-sim?' + new URLSearchParams({ project: prepared.project_id, ...(mode ? { mode } : {}), ...(left ? { left } : {}), ...(right ? { right } : {}) });
const shot = async name => {
  // Let paused seek/camera transitions settle before judging the visual composition.
  await page.waitForTimeout(900);
  return page.screenshot({ path: path.join(output, name + '.png') });
};
const record = (name, details = {}) => { report.checks.push({ name, ...details }); console.log(name); };
const panel = () => page.getByLabel('관측 사건 시연', { exact: true });
const timeline = () => page.getByRole('slider', { name: '비교 공통 시간' });
const ready = async count => {
  await page.waitForTimeout(150);
  await page.waitForFunction(n => [...document.querySelectorAll('.carla-camera-frame')].filter(el => el.querySelector('canvas')?.style.visibility === 'visible' && el.textContent.includes('SHA-256 verified')).length === n, count);
};
const navigate = async url => { await page.goto(url); await page.reload(); };
const state = mode => page.waitForFunction(value => document.querySelector('[aria-label="관측 사건 시연"]')?.dataset.storyMode === value, mode, { timeout: mode === 'completed' ? 120000 : 30000 });
try {
  await page.goto(route());
  await page.getByRole('button', { name: '시연 시작', exact: true }).first().waitFor();
  for (const title of ['장애물 회피', '양보 후 진입', '8대 교차로']) {
    const card = page.locator('.demo-card').filter({ has: page.getByRole('heading', { name: title, exact: true }) });
    assert.equal(await card.count(), 1, `Missing original preset: ${title}`);
    assert(await card.getByRole('button', { name: '시연 시작', exact: true }).isEnabled(), `Missing saved story: ${title}`);
  }
  record('three_original_saved_story_entrypoints', { total_saved_stories: await page.getByRole('button', { name: '시연 시작', exact: true }).count() });
  await page.locator('.demo-card').filter({ has: page.getByRole('heading', { name: '장애물 회피', exact: true }) }).getByRole('button', { name: '시연 시작', exact: true }).click();
  await state('guided'); await ready(1);
  assert(Number(await timeline().inputValue()) >= .05);
  await page.getByRole('button', { name: '일시정지', exact: true }).click(); await state('manual');
  const paused = await timeline().inputValue(); await page.waitForTimeout(500);
  assert.equal(await timeline().inputValue(), paused);
  await page.getByRole('button', { name: '이 구간 시연 재개', exact: true }).click(); await state('guided');
  const guidedStart = performance.now();
  await shot('story-single');
  await state('completed');
  record('single_autostart_observed_first_frame_pause_resume_and_complete', { guided_wall_s: (performance.now() - guidedStart) / 1000 });

  await navigate(route('story', bypassA, bypassB)); await state('guided'); await ready(2);
  await page.getByRole('button', { name: '일시정지', exact: true }).click();
  assert.match(await page.getByLabel('변경한 설정', { exact: true }).innerText(), /차로 변경/);
  assert.match(await page.getByLabel('변경한 설정', { exact: true }).innerText(), /A · 예/);
  assert.match(await page.getByLabel('변경한 설정', { exact: true }).innerText(), /B · 아니요/);
  await page.getByLabel('시연 구간', { exact: true }).getByRole('button').nth(1).click(); await state('manual'); await ready(2);
  await shot('story-bypass-ab');
  const layout = await page.evaluate(() => {
    const selectors = ['.object-compare-changes', '.object-compare-transport', '.city-3d-canvas', '.city-decision-reason'];
    return Object.fromEntries(selectors.map(selector => [selector, [...document.querySelectorAll(selector)].map(el => {
      const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height, bottom: r.bottom };
    })]));
  });
  report.presentation_layout = layout;
  for (const [selector, rectangles] of Object.entries(layout)) {
    assert(rectangles.length > 0, `Missing visible ${selector}`);
    assert(rectangles.every(r => r.height > 0 && r.y >= 0 && r.bottom <= 1080), `${selector} exceeds1080p`);
  }
  record('ab_shared_clock_configuration_and_manual_segment', { common_time_s: Number(await timeline().inputValue()) });
  await page.getByRole('group', { name: '공통 카메라' }).getByRole('button', { name: '추적', exact: true }).click(); await state('manual'); await ready(2);
  assert((await page.locator('.carla-camera-frame canvas').evaluateAll(elements => elements.map(el => el.getAttribute('aria-label')))).every(label => label.includes('chase')));
  await shot('story-chase-ab'); record('common_chase_native_camera');
  await page.getByRole('group', { name: '공통 카메라' }).getByRole('button', { name: '전방', exact: true }).click(); await ready(2);
  await timeline().press('End'); await ready(2);
  assert.equal(Number(await timeline().inputValue()), 45);
  record('manual_seek_retains_original_end');
  await page.getByRole('button', { name: '이 구간 시연 재개', exact: true }).click(); await state('guided');
  await page.getByRole('group', { name: '공통 카메라' }).getByRole('button', { name: '추적', exact: true }).click(); await state('manual');
  record('camera_interaction_interrupts_guide');

  await navigate(route('story', gapA, gapB)); await state('guided'); await ready(2);
  await page.getByRole('button', { name: '일시정지', exact: true }).click(); await shot('story-gap-ab');
  record('gap_release_guided_ab');
  await navigate(route('story', junction)); await state('guided'); await ready(1);
  await page.getByRole('button', { name: '일시정지', exact: true }).click();
  assert.equal(await page.getByRole('combobox', { name: 'City viewer actor' }).locator('option').count(), 8);
  await shot('story-junction-eight'); record('eight_actor_guided_story');

  await navigate(route('compare', bypassA, junction)); await ready(2);
  await page.getByText('비교 조건을 확인하세요', { exact: true }).waitFor();
  assert(await panel().getByRole('button', { name: '시연 시작', exact: true }).isDisabled());
  await timeline().press('End'); await ready(2); record('incompatible_story_manual_fallback');

  await navigate(route('compare', bypassA, 'job_3c4dfa2730b9405ca0eaad510324e53c')); await ready(1);
  assert(await panel().getByRole('button', { name: '시연 시작', exact: true }).isDisabled());
  await page.getByRole('article', { name: '실행 B', exact: true }).getByText('실행 취소', { exact: true }).first().waitFor();
  record('cancelled_side_retains_status_and_manual_replay');
  await navigate(route('story', 'job_264321e2aadf4165900d0f38a6fe7d5a')); await ready(1);
  assert.equal(await page.locator('.carla-camera-frame canvas').getAttribute('width'), '320');
  record('older_320_pixel_retained_result_still_playable');

  await navigate(route('compare', junction, junction, true)); await ready(2);
  await page.getByRole('button', { name: '발표 화면', exact: true }).click();
  await page.getByRole('button', { name: '함께 재생', exact: true }).click();
  await page.waitForTimeout(1500);
  const sample = () => page.evaluate(() => ({ at: performance.now(), counts: [...document.querySelectorAll('canvas[data-render-count]')].map(el => Number(el.dataset.renderCount)), time: Number(document.querySelector('[aria-label="비교 공통 시간"]').value) }));
  const before = await sample(); assert.equal(before.counts.length, 2);
  await page.waitForTimeout(10000); const after = await sample();
  const fps = after.counts.map((value, index) => (value - before.counts[index]) * 1000 / (after.at - before.at));
  report.performance = { purpose: 'Two views of the same retained eight-actor run; rendering load only', viewport: { width: 1920, height: 1080 }, browser: browser.version(), interval_ms: after.at - before.at, frames: after.counts.map((value, index) => value - before.counts[index]), actual_render_fps: fps, simulation_progress_s: after.time - before.time, target_fps: 30, target_met: fps.every(value => value >= 30) };
  report.performance.renderer = await page.evaluate(() => {
    const canvas = document.querySelector('canvas[data-render-count]'), gl = canvas.getContext('webgl2');
    const extension = gl?.getExtension('WEBGL_debug_renderer_info');
    return extension ? gl.getParameter(extension.UNMASKED_RENDERER_WEBGL) : 'unavailable';
  });
  await shot('story-eight-ab-performance'); record('eight_actor_ab_actual_render_measurement', report.performance);
  assert(report.performance.target_met, 'Measured 3D rendering misses30fps target');
  assert.equal(report.asset_responses.filter(item => item.status !== 200).length, 0);
  assert(report.asset_responses.some(item => item.url.endsWith('sedan.glb')));
  assert.deepEqual(report.page_errors, []); assert.deepEqual(report.submissions, []);
  report.complete = true;
} catch (error) {
  report.complete = false; report.error = String(error); await shot('story-browser-failure').catch(() => {}); throw error;
} finally {
  report.finished_at = new Date().toISOString();
  fs.writeFileSync(path.join(output, reportName), JSON.stringify(report, null, 2) + '\n');
  await browser.close();
}
