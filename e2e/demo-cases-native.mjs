// Actual retained-result browser rehearsal. No mocks and no engine submissions.
import { chromium, expect } from '@playwright/test';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';

const root = path.resolve(import.meta.dirname, '..');
const output = path.join(root, '.runtime/demo-cases-20261010/browser');
await mkdir(output, { recursive: true });
const evidence = JSON.parse(await readFile(path.join(root, '.runtime/demo-cases-20261010/evidence.json'), 'utf8'));
const ports = { object: 8053, sensor: 8054, hil: 8056, neural: 8060 };
const requested = process.argv.slice(2);
const report = { schema_version: 'demo-cases-browser-rehearsal/1', source: 'actual_retained_native_results', native_jobs_submitted: 0, workspaces: [] };
if (requested.length) {
  try { report.workspaces = JSON.parse(await readFile(path.join(output, 'report.json'), 'utf8')).workspaces.filter(item => !requested.includes(item.name)); } catch { /* First targeted run. */ }
}
const browser = await chromium.launch({ channel: 'chrome', headless: true });
try {
  for (const [name, workspace] of Object.entries(evidence.workspaces)) {
    if (requested.length && !requested.includes(name)) continue;
    const origin = `http://127.0.0.1:${ports[name]}`;
    const tokenPath = path.join(workspace.workspace, '../access', name === 'object' ? 'presenter-token' : 'api-token');
    const token = (await readFile(tokenPath, 'utf8')).trim();
    const context = await browser.newContext({ viewport: { width: 1600, height: 1100 }, recordVideo: { dir: path.join(output, 'video'), size: { width: 1600, height: 1100 } } });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const entry = { name, origin, project_id: workspace.project_id, cases: [], errors };
    report.workspaces.push(entry);
    try {
      const gallery = `${origin}/#workspace?project=${workspace.project_id}`;
      await page.goto(gallery);
      await page.getByRole('button', { name: 'Open workspace settings', exact: true }).click();
      await page.locator('input[type="password"]').fill(token);
      await page.getByRole('button', { name: 'Test & save connection', exact: true }).click();
      await expect(page.locator('.demo-case-card.is-ready').first()).toBeVisible({ timeout: 30000 });
      const response = await context.request.get(`${origin}/api/v1/demo-cases?project_id=${workspace.project_id}`, { headers: { Authorization: `Bearer ${token}` } });
      expect(response.status()).toBe(200);
      const catalog = await response.json();
      const expectedCases = evidence.items.filter(item => item.workspace === name && item.state === 'retained_actual').map(item => item.id).sort();
      expect(catalog.items.filter(item => item.ready).map(item => item.id).sort()).toEqual(expectedCases);
      entry.counts = catalog.counts;
      await page.screenshot({ path: path.join(output, `${name}-gallery.png`), fullPage: true });
      for (const item of catalog.items.filter(item => item.ready)) {
        await page.goto(gallery);
        const card = page.locator(`[data-demo-case="${item.id}"]`);
        await expect(card).toBeVisible({ timeout: 30000 });
        const moment = item.key_moments[0];
        const link = moment?.href ?? item.primary_link.href;
        await card.locator('a').filter({ hasText: moment?.label ?? item.primary_link.label }).first().click();
        const entryCase = { id: item.id, execution_ids: item.executions.map(run => run.id), link, requested_time_s: moment?.time_s ?? null };
        entry.cases.push(entryCase);
        let slider;
        if (item.module === 'object_sim' || item.module === 'log_sim') {
          slider = page.getByRole('slider', { name: '비교 공통 시간', exact: true });
          await expect(slider).toBeEnabled({ timeout: 60000 });
          await expect(page.getByRole('button', { name: '함께 재생', exact: true })).toBeEnabled({ timeout: 60000 });
          if (moment) await expect.poll(async () => Number(await slider.inputValue()), { timeout: 60000 }).toBeCloseTo(moment.time_s, 1);
          await page.getByRole('button', { name: '함께 재생', exact: true }).click();
          await expect.poll(async () => Number(await slider.inputValue()), { timeout: 30000 }).toBeGreaterThan(moment?.time_s ?? 0);
          await page.getByRole('button', { name: '일시정지', exact: true }).click();
        } else if (item.module === 'sensor_sim') {
          slider = page.getByRole('slider', { name: 'Sensor Sim 공통 상대 시간축', exact: true });
          await expect(slider).toBeVisible({ timeout: 60000 });
          await expect(page.locator('.sensor-presenter-stream-status')).toContainText('선택 스트림 원본 확인됨', { timeout: 60000 });
          const initial = Number(await slider.inputValue());
          await page.getByRole('button', { name: 'Sensor Sim 재생', exact: true }).click();
          await expect.poll(async () => Number(await slider.inputValue()), { timeout: 30000 }).toBeGreaterThan(initial);
          await page.getByRole('button', { name: 'Sensor Sim 일시 정지', exact: true }).click();
          entryCase.native_readback = await page.locator('.sensor-presenter-observed-difference').innerText();
        } else if (item.module === 'hil') {
          slider = page.getByRole('slider', { name: 'HIL 공통 시간축', exact: true });
          await expect(slider).toBeEnabled({ timeout: 60000 });
          const pin = evidence.items.find(value => value.id === item.id);
          if (pin?.parameters.delay_ms) await expect(page.locator('.hil-presenter-heading')).toContainText(String(pin.parameters.delay_ms));
          for (const run of item.executions) await expect(page.locator('.hil-provenance')).toContainText(run.id);
          if (moment) await expect.poll(async () => Number(await slider.inputValue()), { timeout: 60000 }).toBeCloseTo(moment.time_s, 1);
          await page.getByRole('button', { name: 'HIL A/B 재생', exact: true }).click();
          await expect.poll(async () => Number(await slider.inputValue()), { timeout: 30000 }).toBeGreaterThan(moment?.time_s ?? 0);
          await page.getByRole('button', { name: 'HIL 재생 일시정지', exact: true }).click();
        } else {
          const run = await (await context.request.get(`${origin}/api/v1/experiments/executions/${item.executions[0].id}`, { headers: { Authorization: `Bearer ${token}` } })).json();
          const artifact = run.artifacts.find(value => value.name === 'neural-render-result.json');
          const bytes = await (await context.request.get(`${origin}${artifact.download_url}`, { headers: { Authorization: `Bearer ${token}` } })).body();
          expect(createHash('sha256').update(bytes).digest('hex')).toBe(artifact.sha256);
          const frame = JSON.parse(bytes.toString('utf8')).frames[40];
          slider = page.getByRole('slider', { name: 'Neural 카메라 위치 탐색', exact: true });
          await expect(slider).toBeVisible({ timeout: 60000 });
          await expect(page.locator('.neural-playback-note')).toContainText('5.00 s 고정', { timeout: 60000 });
          await slider.fill('40');
          await expect(page.getByTestId('neural-actual-camera')).toContainText(frame.applied_lateral_m.toFixed(2), { timeout: 30000 });
          entryCase.rendered_pose_m = frame.applied_lateral_m;
          entryCase.frozen_source_time_s = frame.source_time_s;
        }
        entryCase.displayed_cursor = await slider.inputValue();
        await page.screenshot({ path: path.join(output, `${item.id}.png`), fullPage: true });
        await page.reload();
        await expect(slider).toBeVisible({ timeout: 60000 });
        if (moment && item.module !== 'sensor_sim') await expect.poll(async () => Number(await slider.inputValue()), { timeout: 60000 }).toBeCloseTo(moment.time_s, 1);
        await page.getByRole('link', { name: '← 질문 중심 사례 목록으로', exact: true }).click();
        await expect(page.locator(`[data-demo-case="${item.id}"]`)).toBeVisible({ timeout: 30000 });
        entryCase.status = 'passed';
      }
      entry.status = 'passed';
    } catch (error) {
      entry.status = 'failed'; entry.error = String(error);
      await page.screenshot({ path: path.join(output, `${name}-failure.png`), fullPage: true }).catch(() => {});
    } finally {
      await context.close();
      entry.video = await page.video()?.path();
      await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
      console.log(JSON.stringify({ workspace: name, status: entry.status, counts: entry.counts, tested: entry.cases.length, error: entry.error }));
    }
  }
} finally {
  await browser.close();
}
if (report.workspaces.some(item => item.status !== 'passed' || item.errors.length)) process.exitCode = 1;
