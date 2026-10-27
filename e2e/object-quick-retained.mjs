// Five quick scenes through normal participant UI. Never submits native work.
// Edits are authoring previews; retained RGB/A-B remains explicitly baseline data.
import { chromium, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';

const option = key => { const index = process.argv.indexOf(key); return index < 0 ? undefined : process.argv[index + 1]; };
const runtime = path.resolve(process.argv[2] ?? '');
assert(process.argv[2] && process.argv.includes('--no-submit') && option('--base-url') && option('--invite-file'));
const base = option('--base-url'); assert.equal(new URL(base).hostname, '127.0.0.1');
const invitation = JSON.parse(fs.readFileSync(option('--invite-file'), 'utf8'));
const project = new URLSearchParams(invitation.start_page.split('?')[1]).get('project'); assert(project);
const expectedIds = ['obstacle_bypass', 'gap_release', 'junction_eight', 'urban_city', 'urban_right_turn'];
const requiredPairs = new Set(['obstacle_bypass', 'gap_release', 'urban_right_turn']);
const output = path.join(runtime, 'browser-quick'); fs.mkdirSync(output, { recursive: true });
const report = { schema_version: 'object-quick-retained-browser/1', started_at: new Date().toISOString(), project_id: project, checks: [], page_errors: [], denied: [], wrong_projects: [], forbidden_mutations: [] };
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } }); page.setDefaultTimeout(30000);
page.on('pageerror', error => report.page_errors.push(error.message));
page.on('response', response => { const route = new URL(response.url()).pathname; if (route.startsWith('/api/v1/') && response.status() === 403) report.denied.push(route); });
await page.route('**/api/v1/**', async route => {
  const request = route.request(), url = new URL(request.url());
  const scoped = url.searchParams.get('project_id') ?? url.searchParams.get('project') ?? /^\/api\/v1\/projects\/([^/]+)/.exec(url.pathname)?.[1];
  if (scoped && scoped !== project) report.wrong_projects.push({ path: url.pathname, project_id: scoped });
  if (!['GET', 'HEAD'].includes(request.method()) && !/^\/api\/v1\/object-sim\/presets\/[^/]+\/preview$/.test(url.pathname)) {
    report.forbidden_mutations.push({ path: url.pathname, method: request.method() }); await route.abort(); return;
  }
  await route.continue();
});
const record = (name, detail = {}) => { report.checks.push({ name, ...detail }); console.log(name); };
const quick = `${base}/#object-sim?${new URLSearchParams({ project, mode: 'quick' })}`;
const get = route => page.evaluate(async route => { const response = await fetch(`/api/v1${route}`); if (!response.ok) throw new Error(`GET ${route}: ${response.status}`); return response.json(); }, route);
const card = title => page.locator('.demo-presets article').filter({ has: page.getByRole('heading', { name: title, exact: true }) });
const previewResponse = id => page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/object-sim/presets/${id}/preview` && response.request().method() === 'POST');
const rgb = scope => expect(scope.getByRole('img', { name: /^CARLA .* 원본 프레임 / }).first()).toBeVisible({ timeout: 60000 });
async function openQuick() {
  await page.goto(quick);
  await expect(page.getByLabel('Demo project', { exact: true })).toHaveValue(project);
  await expect(page.locator('.demo-presets article')).toHaveCount(5);
  await expect(page.getByRole('button', { name: '빠른 시작 · 데모', exact: true })).toHaveAttribute('aria-pressed', 'true');
}
try {
  await page.goto(quick); await page.getByLabel('접속 비밀번호').fill(invitation.password);
  await page.locator('form button[type="submit"]').click(); await page.waitForLoadState('domcontentloaded');
  await expect(page.getByLabel('Demo project', { exact: true })).toHaveValue(project);
  await expect(page.locator('.demo-presets article')).toHaveCount(5);
  const catalog = await get(`/object-sim/presets?project_id=${project}`);
  const presets = expectedIds.map(id => catalog.items.find(item => item.id === id));
  assert(presets.every(item => item?.ready && item.saved_execution_id), 'Every quick scene needs an actual bound native baseline');
  for (const preset of presets) {
    await expect(card(preset.title).locator('.demo-readiness')).toHaveText('편집 준비됨');
    await expect(card(preset.title).getByRole('button', { name: '직접 수정하기', exact: true })).toBeEnabled();
    if (requiredPairs.has(preset.id)) assert(preset.saved_comparison_execution_id && preset.saved_comparison_execution_id !== preset.saved_execution_id, `${preset.id} requires a retained A/B pair`);
  }
  await page.screenshot({ path: path.join(output, 'five-quick-scenes-ready.png'), fullPage: true });
  record('five_quick_cards_ready_in_authenticated_project');
  for (const preset of presets) {
    const receipt = await get(`/experiments/executions/${preset.saved_execution_id}`);
    assert.equal(receipt.project_id, project); assert.equal(receipt.status, 'completed'); assert.equal(receipt.outcome_uncertain, false);
    assert.equal(receipt.adapter_id, 'carla-city-object'); assert.equal(receipt.fidelity, 'native_carla_object_physics'); assert(receipt.manifest_hash);
    const pending = previewResponse(preset.id); await card(preset.title).getByRole('button', { name: '직접 수정하기', exact: true }).click();
    const initialResponse = await pending; assert.equal(initialResponse.status(), 200); const initial = await initialResponse.json();
    assert.equal(initial.project_id, project); assert.equal(initial.binding_revision, preset.binding_revision); assert.equal(initial.saved_execution_id, preset.saved_execution_id);
    await expect(page.locator('.demo-map-loading')).toHaveCount(0);
    const parameter = preset.parameters.find(item => item.type === 'number');
    if (parameter) {
      const before = Number(initial.parameters[parameter.key]), next = before + (parameter.step ?? .5) <= parameter.max ? 'ArrowRight' : 'ArrowLeft';
      const edited = previewResponse(preset.id); await page.getByRole('slider', { name: parameter.label, exact: true }).focus(); await page.getByRole('slider', { name: parameter.label, exact: true }).press(next);
      const editedResponse = await edited; assert.equal(editedResponse.status(), 200); const changed = await editedResponse.json();
      assert.notEqual(changed.parameters[parameter.key], before); assert.equal(changed.saved_execution_id, preset.saved_execution_id);
      await expect(page.locator('.demo-changes')).not.toContainText('기준과 같은 설정');
      const restored = previewResponse(preset.id); await page.getByRole('button', { name: '기준 복원', exact: true }).click();
      assert.equal((await (await restored).json()).parameters[parameter.key], before);
    } else {
      assert.equal(preset.id, 'junction_eight'); assert.equal(initial.scenario.actors.length, 8);
      await expect(page.getByLabel('데모 선택 객체', { exact: true }).locator('option')).toHaveCount(8);
      await page.getByLabel('데모 선택 객체', { exact: true }).selectOption(initial.scenario.actors[1].id);
    }
    await card(preset.title).getByRole('button', { name: '저장 결과', exact: true }).click();
    const viewer = page.getByLabel('선택한 실제 실행 재생', { exact: true }); await rgb(viewer);
    await expect(viewer).toContainText('현재 변경안의 결과가 아닙니다');
    if (preset.id === 'junction_eight') await expect(viewer.getByLabel('City viewer actor', { exact: true }).locator('option')).toHaveCount(8);
    await viewer.scrollIntoViewIfNeeded(); await page.screenshot({ path: path.join(output, `${preset.id}-native-rgb.png`), fullPage: true });
    await page.getByRole('button', { name: '재생 닫기', exact: true }).click();
    record('quick_edit_preview_and_actual_baseline_rgb', { preset_id: preset.id, execution_id: receipt.id, manifest_hash: receipt.manifest_hash, actor_count: initial.scenario.actors.length });
    if (preset.saved_comparison_execution_id) {
      await card(preset.title).getByRole('button', { name: '저장 A/B 비교', exact: true }).click();
      await page.getByRole('slider', { name: '비교 공통 시간', exact: true }).waitFor({ timeout: 60000 });
      const query = new URLSearchParams(new URL(page.url()).hash.split('?')[1]);
      assert.equal(query.get('project'), project); assert.equal(query.get('left'), preset.saved_comparison_execution_id); assert.equal(query.get('right'), preset.saved_execution_id);
      await rgb(page.getByLabel('실행 A', { exact: true })); await rgb(page.getByLabel('실행 B', { exact: true }));
      await page.screenshot({ path: path.join(output, `${preset.id}-native-ab.png`), fullPage: true });
      await page.reload(); await page.getByRole('slider', { name: '비교 공통 시간', exact: true }).waitFor({ timeout: 60000 });
      await rgb(page.getByLabel('실행 A', { exact: true })); await rgb(page.getByLabel('실행 B', { exact: true }));
      record('quick_saved_ab_exact_bindings_and_reload', { preset_id: preset.id, left: preset.saved_comparison_execution_id, right: preset.saved_execution_id });
      await openQuick();
    }
  }
  await openQuick(); assert.equal(await page.evaluate(() => sessionStorage.getItem('axiom.apiToken')), null);
  assert.deepEqual(report.page_errors, []); assert.deepEqual(report.denied, []); assert.deepEqual(report.wrong_projects, []); assert.deepEqual(report.forbidden_mutations, []);
  report.complete = true;
} catch (error) { report.complete = false; report.error = String(error); await page.screenshot({ path: path.join(output, 'quick-retained-failure.png'), fullPage: true }).catch(() => {}); throw error; }
finally { report.finished_at = new Date().toISOString(); fs.writeFileSync(path.join(output, 'acceptance.json'), JSON.stringify(report, null, 2) + '\n'); await browser.close(); }
