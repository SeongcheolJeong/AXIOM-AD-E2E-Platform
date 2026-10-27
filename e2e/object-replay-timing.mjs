// Read-only timing check using the retained eight-actor native result in two panes.
// Usage: node e2e/object-replay-timing.mjs <demo-runtime> <story-runtime> <output.json>
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
const [runtime, stories, output] = process.argv.slice(2);
assert(runtime && stories && output);
const prepared = JSON.parse(fs.readFileSync(path.join(runtime, 'prepared.json')));
const filmed = JSON.parse(fs.readFileSync(path.join(stories, 'camera-rehearsal.json')));
const access = JSON.parse(fs.readFileSync(path.join(runtime, 'access/access.json')));
const job = filmed.trials.find(item => item.preset === 'junction_eight').execution_id;
const browser = await chromium.launch({ headless: true, channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
const report = { schema_version: 'object-replay-timing/1', execution_id: job, built_ui_sha256: access.built_ui_sha256,
  viewport: { width: 1920, height: 1080 }, browser: browser.version(), samples: [], page_errors: [],
  notes: ['Repeat uses the same page with its bounded8-image cache; it is not a fully preloaded run.', 'Fetch duration ends at response headers; not a standalone decode or full-body profiler.'] };
page.on('pageerror', error => report.page_errors.push(error.message));
await page.addInitScript(token => {
  sessionStorage.setItem('axiom.apiToken', token);
  window.replayTiming = { fetches: [], timer50: [] };
  const fetchOriginal = window.fetch;
  window.fetch = async (...args) => {
    const start = performance.now();
    try { return await fetchOriginal(...args); }
    finally { window.replayTiming.fetches.push({ start, end: performance.now() }); }
  };
  const timerOriginal = window.setTimeout;
  window.setTimeout = (callback, delay, ...args) => timerOriginal(() => {
    if (delay === 50) window.replayTiming.timer50.push(performance.now());
    if (typeof callback === 'function') callback(...args);
    else throw new Error('Unexpected string timer');
  }, delay);
}, fs.readFileSync(path.join(runtime, 'access/presenter-token'), 'utf8').trim());
const ready = () => page.waitForFunction(() => [...document.querySelectorAll('.carla-camera-frame canvas')].filter(el => el.style.visibility === 'visible').length === 2);
try {
  await page.goto('http://127.0.0.1:8033/?cityPerf=1#object-sim?' + new URLSearchParams({ project: prepared.project_id, mode: 'compare', left: job, right: job }));
  await ready(); await page.getByRole('button', { name: '발표 화면', exact: true }).click();
  for (const pass of ['first_play', 'repeat_play']) {
    await page.getByRole('button', { name: '비교 처음으로', exact: true }).click(); await ready();
    await page.getByRole('button', { name: '함께 재생', exact: true }).click();
    const sample = await page.evaluate(async () => {
      const cursor = () => Number(document.querySelector('[aria-label="비교 공통 시간"]').value);
      const counts = () => [...document.querySelectorAll('canvas[data-render-count]')].map(el => Number(el.dataset.renderCount));
      const start = performance.now(), initial = cursor(), frames = counts(), changes = [];
      let previous = initial;
      const poll = setInterval(() => { const value = cursor(); if (value !== previous) { changes.push({ at: performance.now(), value }); previous = value; } }, 5);
      await new Promise(resolve => setTimeout(resolve, 10000)); clearInterval(poll);
      const end = performance.now(), elapsed = end - start;
      const gaps = changes.slice(1).map((item, index) => item.at - changes[index].at).sort((a, b) => a - b);
      const fetches = window.replayTiming.fetches.filter(item => item.start >= start);
      return { elapsed_ms: elapsed, source_progress_s: cursor() - initial, rate: (cursor() - initial) * 1000 / elapsed,
        source_advances: changes.length, cursor_gap_median_ms: gaps[Math.floor(gaps.length / 2)], cursor_gap_p95_ms: gaps[Math.floor(gaps.length * .95)],
        render_fps: counts().map((count, index) => (count - frames[index]) * 1000 / elapsed),
        timer50_count: window.replayTiming.timer50.filter(at => at >= start && at <= end).length,
        fetch_count: fetches.length, fetch_total_ms: fetches.reduce((sum, item) => sum + item.end - item.start, 0),
        fetch_max_ms: Math.max(0, ...fetches.map(item => item.end - item.start)) };
    });
    await page.getByRole('button', { name: '일시정지', exact: true }).click();
    report.samples.push({ pass, ...sample }); console.log(JSON.stringify({ pass, ...sample }));
  }
  assert.deepEqual(report.page_errors, []);
  report.complete = true;
} finally {
  report.finished_at = new Date().toISOString();
  fs.writeFileSync(output, JSON.stringify(report, null, 2) + '\n'); await browser.close();
}
