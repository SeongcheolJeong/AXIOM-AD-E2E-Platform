// Actual normal UI workflow. No route interception or mocked engine/API responses.
import { chromium, expect } from '@playwright/test';
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';

const base = process.env.AXIOM_NEURAL_LIVE_URL ?? 'http://127.0.0.1:8040';
const project = process.env.AXIOM_NEURAL_PROJECT ?? 'project_c653516fae7447e0bd77f53af15c6964';
const run = process.env.AXIOM_NEURAL_RUN ?? 'job_9f91dd1e944e4cd28b48db7c8ceb0d05';
const result = process.env.AXIOM_NEURAL_RESULT ?? '';
const submitFrame = process.env.AXIOM_NEURAL_SUBMIT_FRAME === '1';
const output = process.env.AXIOM_NEURAL_BROWSER_OUTPUT ?? '.runtime/neural-demo-20261010/browser';
const token = readFileSync(process.env.AXIOM_NEURAL_TOKEN_FILE ?? '.runtime/neural-demo-20261010/access/api-token', 'utf8').trim();
mkdirSync(output, { recursive: true });
const keepOpen = process.env.AXIOM_NEURAL_KEEP_OPEN === '1';
const browser = await chromium.launch({ headless: process.env.AXIOM_NEURAL_HEADLESS !== '0', args: ['--window-size=1920,1080'], channel: process.env.PLAYWRIGHT_CHANNEL ?? 'chrome' });
let livePage;
const report = { evidence: 'actual_normal_browser_api', base, project, run, selected_result: result || null, checks: [], page_errors: [] };
try {
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
  const page = await context.newPage(); livePage = page;
  page.on('pageerror', error => report.page_errors.push(error.message));
  const query = new URLSearchParams({ project, ...(run ? { run } : {}), ...(result ? { result } : {}) });
  await page.goto(`${base}/#neural-sim?${query}`);
  await page.getByRole('button', { name: 'Workspace settings', exact: true }).first().click();
  const dialog = page.getByRole('dialog', { name: 'Workspace settings', exact: true });
  await dialog.locator('input[type="password"]').fill(token);
  await dialog.getByRole('button', { name: 'Test & save connection', exact: true }).click();
  await expect(dialog).not.toBeVisible();
  const authenticatedAt = Date.now();
  report.checks.push('credential entered through Workspace settings UI');
  await expect(page.getByRole('heading', { name: /기록한 길을,.*새로운 시점으로/ })).toBeVisible();
  await expect(page.locator('select[aria-label="Neural 원본 실행"]')).not.toHaveValue('', { timeout: 20000 });
  const setupWasClosed = await page.locator('.neural-setup').getAttribute('open') === null;
  if (setupWasClosed) await page.locator('.neural-setup summary').click();
  await expect.poll(async () => page.getByRole('combobox', { name: 'Neural 원본 실행', exact: true }).inputValue(), { timeout: 20000 }).not.toBe('');
  report.source_execution_id = await page.getByRole('combobox', { name: 'Neural 원본 실행', exact: true }).inputValue();
  report.reconstruction_execution_id = await page.getByRole('combobox', { name: 'Neural 학습 장면', exact: true }).inputValue();
  if (setupWasClosed) await page.locator('.neural-setup summary').click();
  report.checks.push('actual source discovered in normal scene selector');
  if (run) {
    await expect(page.locator('.neural-run')).toContainText(run);
    await expect.poll(async () => page.locator('.neural-run strong').textContent(), { timeout: 20000 }).not.toBe('실행 상태 확인 중');
    report.observed_run_status = await page.locator('.neural-run').innerText();
    report.checks.push('actual execution status visible');
  }
  if (result) {
    await expect(page.getByAltText('Neural 렌더 · 새로운 시점')).toBeVisible({ timeout: 150000 });
    await expect(page.locator('.neural-playback-note')).toContainText('동일한 원본 시각', { timeout: 150000 });
    report.initial_verified_frame_ms_after_auth = Date.now() - authenticatedAt;
    report.display_settings = await page.locator('.neural-display-settings').innerText();
    report.checks.push('sealed actual render ZIP and PNGs loaded');
    await page.getByRole('button', { name: '원본 ↔ 재구성', exact: true }).click();
    await expect(page.getByAltText('원본 CARLA 영상')).toBeVisible();
    await page.getByRole('button', { name: '슬라이더', exact: true }).click();
    await page.getByRole('slider', { name: 'Neural 영상 비교 슬라이더' }).fill('45');
    await page.screenshot({ path: path.join(output, 'native-original-reconstruction.png'), fullPage: true });
    await page.getByRole('button', { name: '기준 ↔ 새 시점', exact: true }).click();
    await page.getByRole('button', { name: '나란히', exact: true }).click();
    report.inspected_frames = [];
    for (const frame of [0, 100, 199]) {
      await page.getByRole('slider', { name: 'Neural 공통 시간축' }).fill(String(frame));
      await expect(page.locator('.neural-playback-note')).toContainText(`frame ${frame} ·`, { timeout: 10000 });
      await page.getByRole('button', { name: '원본 ↔ 재구성', exact: true }).click();
      await expect(page.getByAltText('원본 CARLA 영상')).toBeVisible();
      await page.locator('.neural-presenter').screenshot({path:path.join(output,`native-frame-${frame}-original-reconstruction.png`)});
      await page.getByRole('button', { name: '기준 ↔ 새 시점', exact: true }).click();
      await expect(page.getByAltText('Neural 렌더 · 새로운 시점')).toBeVisible();
      await page.locator('.neural-presenter').screenshot({path:path.join(output,`native-frame-${frame}-baseline-novel.png`)});
      report.inspected_frames.push({ frame, note: await page.locator('.neural-playback-note').innerText(), images: await page.locator('.neural-image-stage img').evaluateAll(items => items.map(image => ({alt:image.alt,width:image.naturalWidth,height:image.naturalHeight,complete:image.complete}))) });
    }
    await page.getByRole('button', { name: 'Neural 처음으로' }).click();
    await expect(page.locator('.neural-playback-note')).toContainText('frame 0 ·');
    await page.getByRole('combobox', { name: 'Neural 재생 속도' }).selectOption('1');
    const playbackStarted = Date.now();
    await page.getByRole('button', { name: 'Neural 영상 재생', exact: true }).click();
    await expect.poll(async () => Number(await page.getByRole('slider', { name: 'Neural 공통 시간축' }).inputValue()), { timeout: 10000 }).toBeGreaterThanOrEqual(10);
    await page.getByRole('button', { name: 'Neural 재생 일시정지', exact: true }).click();
    report.playback_frame = await page.getByRole('slider', { name: 'Neural 공통 시간축' }).inputValue();
    report.playback_observation = { speed: 1, nominal_fps: 10, frame: Number(report.playback_frame), elapsed_ms: Date.now() - playbackStarted };
    report.checks.push('actual images: original/reconstruction, novel, wipe, 1x playback');
    await page.getByRole('button', { name: 'Neural 발표 전체 화면' }).click();
    await expect(page.locator('.neural-presenter')).toHaveClass(/neural-expanded/);
    await page.screenshot({ path: path.join(output, 'native-neural-fullscreen.png') });
    await page.getByRole('button', { name: 'Neural 발표 전체 화면' }).click();
    report.checks.push('actual render fullscreen presentation');
    if (submitFrame) {
      const previousDisplay = await page.locator('.neural-display-settings').textContent();
      await page.getByRole('spinbutton', { name: 'Neural 좌우 이동', exact: true }).fill('0.15');
      await page.getByRole('spinbutton', { name: 'Neural 높이 이동', exact: true }).fill('0.1');
      await page.getByRole('spinbutton', { name: 'Neural 미리보기 프레임' }).fill('50');
      await expect(page.locator('.neural-display-settings')).toHaveText(previousDisplay);
      const executionResponse = page.waitForResponse(response => response.request().method() === 'POST' && /\/api\/v1\/experiments\/[^/]+\/executions$/.test(new URL(response.url()).pathname));
      await page.getByRole('button', { name: '현재 프레임 생성', exact: true }).click();
      const submitted = await executionResponse;
      if (!submitted.ok()) throw new Error(`Actual frame submission failed (${submitted.status()})`);
      const body = await submitted.json(); report.submitted_frame_execution_id = body.id;
      await expect(page.locator('.neural-run')).toContainText(body.id);
      await expect(page.locator('.neural-display-settings')).toHaveText(previousDisplay);
      report.checks.push('UI submitted changed camera pose, retained previous result during GPU execution');
      await page.screenshot({ path: path.join(output, 'native-new-frame-pending.png'), fullPage: true });
      if (process.env.AXIOM_NEURAL_WAIT_FRAME === '1') {
        await expect(page.getByRole('button', { name: '새 결과 열기', exact: true })).toBeVisible({ timeout: 180000 });
        await page.getByRole('button', { name: '새 결과 열기', exact: true }).click();
        await expect(page.locator('.neural-display-settings')).toContainText('+0.15', { timeout: 30000 });
        await expect(page.locator('.neural-playback-note')).toContainText('frame 50', { timeout: 30000 });
        report.checks.push('actual UI frame execution completed and opened with frozen new settings');
        await page.screenshot({ path: path.join(output, 'native-new-frame-result.png'), fullPage: true });
      }
    }
  }
  if (keepOpen && result) { await page.getByRole('button', { name: 'Neural 처음으로' }).click(); await expect(page.locator('.neural-playback-note')).toContainText('frame 0 ·'); await page.getByRole('button', { name: 'Neural 발표 전체 화면' }).click(); await page.bringToFront(); }
  await page.screenshot({ path: path.join(output, result ? 'native-neural-workbench.png' : 'actual-training-status.png'), fullPage: true });
  if (report.page_errors.length) throw new Error(report.page_errors.join('\n'));
  report.status = 'passed';
  if (!keepOpen) await context.close();
} catch (error) {
  if (livePage && !livePage.isClosed() && await livePage.getByRole('dialog').count() === 0) { report.ui_failure_excerpt = (await livePage.locator('main').innerText()).slice(0, 8000); await livePage.screenshot({path:path.join(output,'live-browser-failure.png'),fullPage:true}); }
  report.status = 'failed'; report.error = (error instanceof Error ? error.message : String(error)).split(token).join('[REDACTED]');
  throw new Error(report.error);
} finally {
  report.page_errors = report.page_errors.map(message => message.split(token).join('[REDACTED]'));
  writeFileSync(path.join(output, result ? 'native-result-browser-report.json' : 'training-browser-report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
  if (keepOpen && report.status === 'passed') { console.log('Authenticated native Neural Sim window is open; close its browser window to end this presentation session.'); await new Promise(resolve => browser.once('disconnected', resolve)); }
  else await browser.close();
}
