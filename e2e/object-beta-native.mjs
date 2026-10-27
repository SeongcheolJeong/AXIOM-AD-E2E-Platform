// Live API/browser acceptance in an explicitly isolated beta workspace.
// --no-submit performs authoring/save/refresh checks without submitting engine work.
// --submit additionally executes one edited scene and checks replay/A-B/observation.
// --cancel-submit also submits/cancels one queued rerun. Coordinate CARLA first.
import { chromium, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';

const runtime = path.resolve(process.argv[2] ?? '');
assert(process.argv[2], 'Pass an explicit isolated beta runtime');
assert(process.argv.includes('--no-submit') || process.argv.includes('--submit'), 'Choose --no-submit or --submit explicitly');
const submit = process.argv.includes('--submit');
const option = key => { const at = process.argv.indexOf(key); return at < 0 ? undefined : process.argv[at + 1]; };
const prepared = JSON.parse(fs.readFileSync(option('--prepared-file') ?? path.join(runtime, 'prepared.json'), 'utf8'));
const base = option('--base-url') ?? prepared.api.replace(/\/api\/v1$/, '');
assert.equal(new URL(base).hostname, '127.0.0.1', 'Use the isolated loopback beta API');
const invitation = option('--invite-file') ? JSON.parse(fs.readFileSync(option('--invite-file'), 'utf8')) : null;
const token = invitation ? null : fs.readFileSync(path.join(runtime, 'access/api-token'), 'utf8').trim();
const output = path.join(runtime, 'browser'); fs.mkdirSync(output, { recursive: true });
const report = { schema_version: 'object-beta-browser/1', started_at: new Date().toISOString(), mode: submit ? 'submit' : 'no-submit', checks: [], page_errors: [], submissions: [] };
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
page.setDefaultTimeout(25000);
if (token) await page.addInitScript(value => sessionStorage.setItem('axiom.apiToken', value), token);
page.on('pageerror', error => report.page_errors.push(error.message));
let auditParticipantNavigation = !!invitation && !submit;
const navigationAudit = { requests: [], responses: [] };
if (auditParticipantNavigation) report.navigation_audit = navigationAudit;
page.on('request', request => {
  if (!auditParticipantNavigation) return;
  const url = new URL(request.url()); if (!url.pathname.startsWith('/api/v1/')) return;
  let bodyProject;
  try { bodyProject = request.postDataJSON()?.project_id; } catch { /* GET or non-JSON body */ }
  const project = url.searchParams.get('project_id') ?? url.searchParams.get('project') ?? /^\/api\/v1\/projects\/([^/]+)/.exec(url.pathname)?.[1] ?? bodyProject ?? null;
  navigationAudit.requests.push({ method: request.method(), path: url.pathname, project_id: project });
});
let lastPreview;
page.on('response', async response => {
  const url = new URL(response.url()), route = url.pathname;
  if (auditParticipantNavigation && route.startsWith('/api/v1/')) navigationAudit.responses.push({ path: route, project_id: url.searchParams.get('project_id'), status: response.status() });
  if (/\/object-sim\/presets\/[^/]+\/preview$/.test(route) && response.status() === 200) lastPreview = await response.json().catch(() => lastPreview);
  if (/\/experiments\/[^/]+\/executions$/.test(route) && response.request().method() === 'POST') report.submissions.push({ status: response.status(), id: (await response.json().catch(() => ({}))).id });
});
const entry = `${base}/#object-sim?${new URLSearchParams({ project: prepared.project_id, mode: 'beta' })}`;
const record = (name, detail = {}) => { report.checks.push({ name, ...detail }); console.log(name); };
const saveButton = () => page.getByRole('button', { name: '개정 저장', exact: true });
const ready = async () => { await expect(saveButton()).toBeEnabled(); await expect(page.locator('.beta-validation')).toHaveCount(0); };
const screenshot = name => page.screenshot({ path: path.join(output, `${name}.png`), fullPage: true });
const get = route => page.evaluate(async ({ route, tokenMode }) => {
  const value = tokenMode ? sessionStorage.getItem('axiom.apiToken') : null;
  const response = await fetch(`/api/v1${route}`, value ? { headers: { Authorization: `Bearer ${value}` } } : {});
  if (!response.ok) throw new Error(`GET ${route}: ${response.status}`);
  return response.json();
}, { route, tokenMode: !!token });
const nativeReceipt = value => {
  assert.equal(value.project_id, prepared.project_id);
  assert.equal(value.status, 'completed', `Native run ${value.id} did not complete: ${value.error ?? value.status}`);
  assert.equal(value.adapter_id, 'carla-city-object');
  assert.equal(value.fidelity, 'native_carla_object_physics');
  assert.equal(value.outcome_uncertain, false);
  assert(value.manifest_hash && value.download_url && value.artifacts.length, 'Completed native receipt must retain its evidence');
  return { id: value.id, adapter_id: value.adapter_id, adapter_version: value.adapter_version, fidelity: value.fidelity, manifest_hash: value.manifest_hash, input_hash: value.input_hash };
};
const visibleRgb = scope => expect(scope.getByRole('img', { name: /^CARLA .* 원본 프레임 / }).first()).toBeVisible({ timeout: 60000 });
const choose = async index => { await page.locator('.beta-templates article').nth(index).getByRole('button', { name: '내 시나리오로 복제' }).click(); await ready(); };
const save = async () => {
  const response = page.waitForResponse(value => /\/scenario-v2(?:\/[^/]+\/revisions)?$/.test(new URL(value.url()).pathname) && value.request().method() === 'POST');
  await saveButton().click(); const saved = await (await response).json(); await ready(); return saved;
};
async function worldPoint(pose) {
  return page.locator('.object-beta .demo-map > svg').evaluate((svg, value) => { const point = new DOMPoint(value.x, value.y).matrixTransform(svg.getScreenCTM()); return { x: point.x, y: point.y }; }, pose);
}
async function assertParticipantLanding(since = 0) {
  await expect.poll(() => new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('project')).toBe(prepared.project_id);
  await expect(page.getByLabel('Demo project', { exact: true })).toHaveValue(prepared.project_id);
  await expect(page.locator('.beta-templates article')).toHaveCount(4);
  await expect(page.getByRole('button', { name: '시나리오 만들기 · 베타', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect.poll(() => navigationAudit.responses.slice(since).some(item => item.path === '/api/v1/object-sim/presets' && item.project_id === prepared.project_id && item.status === 200)).toBe(true);
  assert.deepEqual(navigationAudit.responses.filter(item => item.status === 403), [], 'Participant landing/navigation must not make forbidden requests');
  assert.deepEqual(navigationAudit.requests.filter(item => item.project_id !== null && item.project_id !== prepared.project_id), [], 'Every scoped navigation request must use the participant project, never implicit default');
}
try {
  if (invitation) {
    // Enter the user-reported bare fragment before login; the gateway must
    // preserve it and the app must resolve the authenticated project itself.
    await page.goto(auditParticipantNavigation ? `${base}/#object-sim` : base);
    await page.getByLabel('접속 비밀번호').fill(invitation.password);
    await page.locator('form button[type="submit"]').click();
    await page.waitForLoadState('domcontentloaded');
    record('participant_password_login_without_bootstrap_token');
  }
  if (auditParticipantNavigation) {
    // Inspect the gateway's real post-login landing before navigating anywhere.
    // Going directly to entry here used to hide an implicit-default project bug.
    await assertParticipantLanding();
    record('participant_login_lands_on_bound_beta_project');
    const bareStart = navigationAudit.responses.length;
    await page.goto(`${base}/#object-sim`);
    await assertParticipantLanding(bareStart);
    record('bare_object_sim_resolves_participant_project_without_default_requests');
    const sidebar = page.locator('aside.sidebar');
    await sidebar.getByRole('button', { name: '시작 · 가능한 작업', exact: true }).click();
    await expect.poll(() => new URL(page.url()).hash.split('?')[0]).toBe('#workspace');
    await expect.poll(() => new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('project')).toBe(prepared.project_id);
    const returnStart = navigationAudit.responses.length;
    await sidebar.getByRole('button', { name: 'Object Sim · 작성 / 재생', exact: true }).click();
    await assertParticipantLanding(returnStart);
    record('sidebar_out_and_back_preserves_participant_project_without_403');
    auditParticipantNavigation = false;
  } else await page.goto(entry);
  await expect(page.locator('.beta-templates article')).toHaveCount(4);
  await expect(page.getByRole('button', { name: '시나리오 만들기 · 베타', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await screenshot('beta-templates'); record('four_beta_templates_default_workflow');
  await choose(0);
  let baselineId;
  if (submit) {
    baselineId = lastPreview.saved_execution_id;
    assert(baselineId, 'The selected template must bind a completed native baseline before --submit; A/B is mandatory');
    report.baseline = nativeReceipt(await get(`/experiments/executions/${encodeURIComponent(baselineId)}`));
    record('completed_native_baseline_bound_before_submission', report.baseline);
  }
  const name = `브라우저 베타 · ${new Date().toISOString().slice(0,19)}`;
  await page.getByRole('textbox', { name: '시나리오 이름', exact: true }).fill(name); await ready();
  await page.getByLabel('ego 속도', { exact: true }).fill('4.5'); await ready();
  await expect.poll(() => lastPreview?.scenario.actors.find(actor => actor.id === 'ego').speed_mps).toBe(4.5);
  record('actor_speed_edit_without_parameter_conflict');

  const ego = lastPreview.scenario.actors.find(actor => actor.id === 'ego');
  await page.locator('.object-beta .demo-map').scrollIntoViewIfNeeded();
  const from = await worldPoint(ego.pose), to = await worldPoint(ego.route[Math.min(2, ego.route.length - 1)]);
  await page.mouse.move(from.x, from.y); await page.mouse.down(); await page.mouse.move(to.x, to.y, { steps: 8 }); await page.mouse.up(); await ready();
  const afterStart = structuredClone(lastPreview.scenario.actors.find(actor => actor.id === 'ego'));
  assert(Math.hypot(afterStart.pose.x - ego.pose.x, afterStart.pose.y - ego.pose.y) > .5);
  await page.getByRole('button', { name: '목적지 선택', exact: true }).click();
  const goal = afterStart.route[Math.max(2, afterStart.route.length - 4)], goalScreen = await worldPoint(goal);
  await page.mouse.click(goalScreen.x, goalScreen.y); await ready();
  await expect.poll(() => Math.hypot(lastPreview.scenario.actors.find(actor => actor.id === 'ego').destination.x - goal.x, lastPreview.scenario.actors.find(actor => actor.id === 'ego').destination.y - goal.y)).toBeLessThan(1);
  record('lane_snapped_actor_start_and_destination');

  await page.getByLabel('베타 선택 객체', { exact: true }).selectOption('lead');
  await page.getByLabel('lead_wait 시작 조건', { exact: true }).selectOption('distance'); await ready();
  await expect(page.getByLabel('lead_wait 기준 객체', { exact: true })).toHaveValue('ego');
  await page.getByLabel('lead_wait 조건값', { exact: true }).fill('8'); await ready();
  record('behavior_trigger_and_target_actor_edit');
  await page.getByLabel('lead_wait 시작 조건', { exact: true }).selectOption('time'); await ready();
  await page.getByLabel('lead_wait 조건값', { exact: true }).fill('100');
  await expect(page.locator('.beta-validation')).toContainText('lead'); await expect(saveButton()).toBeDisabled();
  await page.getByLabel('lead_wait 조건값', { exact: true }).fill('2'); await ready();
  record('invalid_behavior_field_blocks_save_and_recovers');
  const first = await save(); assert.equal(first.revision, 1);
  await page.reload(); await ready();
  await expect(page.getByRole('textbox', { name: '시나리오 이름', exact: true })).toHaveValue(name);
  await expect(page.locator('.beta-save-status')).toContainText('저장된 개정 r1');
  record('named_immutable_save_and_authenticated_draft_refresh', { scenario_id: first.id });
  await page.getByRole('textbox', { name: '시나리오 이름', exact: true }).fill(`${name} · 수정`); await ready();
  const second = await save(); assert.equal(second.id, first.id); assert.equal(second.revision, 2);
  await screenshot('beta-edited-scene'); record('editing_appends_revision_without_changing_saved_input', { revision: second.revision });
  if (process.argv.includes('--expect-engine-unavailable')) {
    await expect(page.getByRole('button', { name: '저장 후 실제 실행', exact: true })).toBeDisabled();
    await expect(page.locator('.object-beta')).toContainText('작성과 저장은 사용할 수 있습니다.');
    record('unconfigured_engine_blocks_execution_but_preserves_authoring_and_save');
  }

  if (submit) {
    const submitted = page.waitForResponse(value => /\/experiments\/[^/]+\/executions$/.test(new URL(value.url()).pathname) && value.request().method() === 'POST');
    await page.getByRole('button', { name: '저장 후 실제 실행', exact: true }).click();
    const submission = await submitted;
    assert.equal(submission.status(), 202);
    const run = await submission.json(); assert(run.id); assert.notEqual(run.id, baselineId); report.execution_id = run.id;
    await page.reload(); await expect(page.getByLabel('내 시나리오 실행 상태')).toBeVisible();
    record('pending_execution_restored_after_refresh', { execution_id: run.id });
    let completed;
    await expect.poll(async () => {
      completed = await get(`/experiments/executions/${encodeURIComponent(run.id)}`);
      return ['completed', 'failed', 'cancelled', 'interrupted', 'rejected'].includes(completed.status);
    }, { timeout: 360000, intervals: [1800, 3000, 5000] }).toBe(true);
    report.execution = nativeReceipt(completed);
    record('edited_revision_completed_with_native_evidence', report.execution);
    await page.getByRole('button', { name: '실제 결과 보기', exact: true }).waitFor();
    await page.getByRole('button', { name: '실제 결과 보기', exact: true }).click();
    await page.getByRole('slider', { name: 'City native timeline', exact: true }).waitFor({ timeout: 60000 });
    await visibleRgb(page.getByLabel('실제 실행 재생', { exact: true }));
    const behavior = await get(`/object-sim/executions/${encodeURIComponent(run.id)}/behavior?project_id=${encodeURIComponent(prepared.project_id)}`);
    assert.equal(behavior.status, 'available'); assert.equal(behavior.physical_sensor_validation, false);
    assert.deepEqual(behavior.scenario_ref, { id: second.id, revision: second.revision, configuration_hash: second.configuration_hash, manifest_hash: second.manifest_hash });
    await expect(page.locator('.beta-behavior .beta-evidence-grid')).toBeVisible();
    const timeoutEvents = behavior.actors.flatMap(actor => actor.actions.flatMap(action => action.events.filter(event => event.type === 'action_timeout')));
    await expect(page.locator('.beta-action-timeout')).toHaveCount(timeoutEvents.length);
    for (const event of timeoutEvents) await expect(page.locator('.beta-behavior')).toContainText(`(${event.reason}) · ${event.time_s.toFixed(2)}초`);
    report.behavior = { scenario_ref: behavior.scenario_ref, source: behavior.source, actors: behavior.actors, ego_outcome: behavior.ego_outcome };
    record('retained_behavior_and_ego_response_displayed_separately');
    await page.getByLabel('관찰 제목', { exact: true }).fill('베타 브라우저 실행 관찰');
    await page.getByLabel('관찰 시점', { exact: true }).fill('5');
    await page.getByLabel('관찰 메모', { exact: true }).fill('작성한 행동과 자차 반응을 별도로 확인하는 사용자 관찰입니다.');
    const observationResponse = page.waitForResponse(value => value.url().endsWith('/object-sim/observations') && value.request().method() === 'POST');
    await page.getByRole('button', { name: '관찰 저장', exact: true }).click();
    const observationReceipt = await observationResponse;
    assert.equal(observationReceipt.status(), 201);
    const observation = await observationReceipt.json();
    assert.equal(observation.execution_id, run.id); assert.deepEqual(observation.scenario_ref, behavior.scenario_ref);
    report.observation_id = observation.id;
    await expect(page.locator('.beta-observation-list')).toContainText('베타 브라우저 실행 관찰');
    await screenshot('beta-native-result');
    await page.reload(); await ready();
    await expect(page.locator('.beta-observation-list')).toContainText('베타 브라우저 실행 관찰');
    record('native_replay_and_revision_bound_observation_restore', { observation_id: observation.id, scenario_ref: observation.scenario_ref });
    const compare = page.getByRole('button', { name: '기준 A / 실행 B 비교', exact: true });
    await expect(compare).toBeVisible(); await compare.click();
    await page.getByRole('slider', { name: '비교 공통 시간', exact: true }).waitFor({ timeout: 60000 });
    const compareQuery = () => new URLSearchParams(page.url().split('?')[1]);
    assert.equal(compareQuery().get('origin'), 'beta'); assert.equal(compareQuery().get('left'), baselineId); assert.equal(compareQuery().get('right'), run.id);
    await visibleRgb(page.getByLabel('실행 A', { exact: true })); await visibleRgb(page.getByLabel('실행 B', { exact: true }));
    await screenshot('beta-native-comparison');
    await page.reload(); await page.getByRole('slider', { name: '비교 공통 시간', exact: true }).waitFor({ timeout: 60000 });
    assert.equal(compareQuery().get('origin'), 'beta'); assert.equal(compareQuery().get('left'), baselineId); assert.equal(compareQuery().get('right'), run.id);
    await visibleRgb(page.getByLabel('실행 A', { exact: true })); await visibleRgb(page.getByLabel('실행 B', { exact: true }));
    record('baseline_comparison_and_bookmark_restore', { baseline_execution_id: baselineId, edited_execution_id: run.id });
    await page.goto(entry); await page.reload(); await ready();
    if (process.argv.includes('--cancel-submit')) {
      const cancelledResponse = page.waitForResponse(value => /\/experiments\/[^/]+\/executions$/.test(new URL(value.url()).pathname) && value.request().method() === 'POST');
      await page.getByRole('button', { name: '저장 후 다시 실행', exact: true }).click();
      const rerunResponse = await cancelledResponse; assert.equal(rerunResponse.status(), 202);
      const cancelled = await rerunResponse.json(); assert(cancelled.id); assert.notEqual(cancelled.id, run.id); report.cancelled_execution_id = cancelled.id;
      if (option('--cancel-signal-file')) {
        // Explicit operator coordination only: briefly keep this real running
        // lease so the concurrent preview path can be checked before cancellation.
        await expect.poll(async () => (await get(`/experiments/executions/${cancelled.id}`)).status, { timeout: 60000, intervals: [500, 1000] }).toBe('running');
        const privateDir = path.join(runtime, 'browser-private'); fs.mkdirSync(privateDir, { recursive: true, mode: 0o700 });
        fs.writeFileSync(path.join(privateDir, 'cancellation-ready.json'), JSON.stringify({ execution_id: cancelled.id, experiment_id: cancelled.experiment_id, project_id: prepared.project_id, at: new Date().toISOString() }) + '\n', { mode: 0o600 });
        console.log(`cancellation_running_ready ${cancelled.id}`);
        const deadline = Date.now() + 20000;
        while (!fs.existsSync(option('--cancel-signal-file')) && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 250));
        report.cancellation_probe_signalled = fs.existsSync(option('--cancel-signal-file'));
      }
      await page.getByRole('button', { name: '실행 취소', exact: true }).click();
      await expect(page.getByLabel('내 시나리오 실행 상태')).toContainText('취소됨', { timeout: 120000 });
      await page.reload(); await ready();
      await expect(page.getByLabel('내 시나리오 실행 상태')).toContainText('취소됨');
      const cancelledDetail = await get(`/experiments/executions/${encodeURIComponent(cancelled.id)}`);
      assert.equal(cancelledDetail.status, 'cancelled'); assert.equal(cancelledDetail.input_hash, completed.input_hash);
      record('same_revision_rerun_cancel_and_terminal_refresh', { execution_id: cancelled.id, input_hash: cancelledDetail.input_hash });
    }
    assert.equal(report.submissions.length, process.argv.includes('--cancel-submit') ? 2 : 1, 'Submit acceptance must not create unexpected additional jobs');
  } else assert.equal(report.submissions.length, 0);

  await choose(1); await page.getByLabel('베타 선택 객체', { exact: true }).selectOption('intruder');
  const action = lastPreview.scenario.actors.find(actor => actor.id === 'intruder').behavior.actions[0];
  await expect(page.getByLabel(`${action.id} 목표 차로`, { exact: true })).toBeVisible();
  record('cut_in_target_lane_control');
  await choose(2); record('left_turn_template_preview');
  await choose(3); record('right_turn_template_preview');
  await screenshot('beta-ready-for-testers');
  if (invitation) {
    assert.equal(await page.evaluate(() => sessionStorage.getItem('axiom.apiToken')), null);
    if (option('--other-invite-file')) {
      const other = JSON.parse(fs.readFileSync(option('--other-invite-file'), 'utf8'));
      const otherProject = new URLSearchParams(other.start_page.split('?')[1]).get('project');
      const denied = await page.evaluate(async project => { const response = await fetch(`/api/v1/object-sim/presets?project_id=${encodeURIComponent(project)}`); return response.status; }, otherProject);
      assert([403, 404].includes(denied), `Cross-project response ${denied}`); record('different_participant_project_access_denied');
    }
    await page.context().clearCookies();
    const signedOut = await page.evaluate(async () => (await fetch('/api/v1/team/whoami')).status);
    assert.equal(signedOut, 401);
    await page.reload(); await expect(page.getByLabel('접속 비밀번호')).toBeVisible();
    await page.getByLabel('접속 비밀번호').fill(invitation.password);
    await page.locator('form button[type="submit"]').click(); await page.waitForLoadState('domcontentloaded');
    await page.goto(entry); await ready();
    await expect(page.getByRole('textbox', { name: '시나리오 이름', exact: true })).toHaveValue(/우회전/);
    record('expired_session_sign_in_and_same_identity_draft_restore');
  }
  assert.deepEqual(report.page_errors, []);
  report.complete = true;
} catch (error) {
  report.complete = false; report.error = String(error); await screenshot('beta-browser-failure').catch(() => {}); throw error;
} finally {
  report.finished_at = new Date().toISOString();
  fs.writeFileSync(path.join(output, `acceptance-${report.mode}.json`), JSON.stringify(report, null, 2) + '\n');
  // Preserve the real participant session for bounded recovery without
  // reconstructing an authored draft or resubmitting a successful native run.
  // Cookies are private credentials: never place this in browser evidence.
  const privateDir = path.join(runtime, 'browser-private'); fs.mkdirSync(privateDir, { recursive: true, mode: 0o700 });
  const stateFile = path.join(privateDir, `${report.mode}-storage.json`);
  await page.context().storageState({ path: stateFile }).then(() => fs.chmodSync(stateFile, 0o600)).catch(() => {});
  await browser.close();
}
