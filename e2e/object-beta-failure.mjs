// Exercise an existing failed native run through a normal participant login.
// Does not submit experiments. The beta editor can observe runs submitted from
// its draft; an older API-authored run is observed through the same public API.
import { chromium, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';

const option = key => { const index = process.argv.indexOf(key); return index < 0 ? undefined : process.argv[index + 1]; };
const runtime = path.resolve(process.argv[2] ?? '');
assert(process.argv[2], 'Provide an explicit isolated runtime');
const base = option('--base-url'); const executionId = option('--execution');
assert(base && executionId && option('--invite-file'), 'Provide base URL, execution ID, and private invitation file');
assert.equal(new URL(base).hostname, '127.0.0.1');
const invitation = JSON.parse(fs.readFileSync(option('--invite-file'), 'utf8'));
const project = new URLSearchParams(invitation.start_page.split('?')[1]).get('project');
const report = { schema_version: 'object-beta-failed-execution-browser/1', checks: [], submissions: 0, page_errors: [] };
const output = path.join(runtime, 'browser'); fs.mkdirSync(output, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
page.on('pageerror', error => report.page_errors.push(error.message));
page.on('request', request => { if (request.method() === 'POST' && /\/experiments\/[^/]+\/executions$/.test(new URL(request.url()).pathname)) report.submissions++; });
const record = (name, detail = {}) => { report.checks.push({ name, ...detail }); console.log(name); };
try {
  await page.goto(base); await page.getByLabel('접속 비밀번호').fill(invitation.password);
  await page.locator('form button[type="submit"]').click(); await page.waitForLoadState('domcontentloaded');
  await page.goto(`${base}${invitation.start_page}`);
  await expect(page.locator('.beta-templates article')).toHaveCount(4);
  const result = await page.evaluate(async ({ project, executionId }) => {
    const executionResponse = await fetch(`/api/v1/experiments/executions/${encodeURIComponent(executionId)}`);
    const behaviorResponse = await fetch(`/api/v1/object-sim/executions/${encodeURIComponent(executionId)}/behavior?project_id=${encodeURIComponent(project)}`);
    return { executionStatus: executionResponse.status, behaviorStatus: behaviorResponse.status, execution: await executionResponse.json(), behavior: await behaviorResponse.json() };
  }, { project, executionId });
  assert.equal(result.executionStatus, 200); assert.equal(result.behaviorStatus, 200);
  assert.equal(result.execution.project_id, project); assert.equal(result.execution.status, 'failed');
  assert.equal(result.behavior.status, 'unavailable');
  assert.equal(result.behavior.ego_outcome.status, 'unavailable');
  record('failed_execution_reports_unavailable_behavior_without_false_verdict', { execution_id: executionId, reason: result.behavior.reason, action_statuses: result.behavior.actors.flatMap(actor => actor.actions.map(action => action.status)) });
  report.issue_ui = { reachable: false, reason: 'This existing execution was submitted outside the browser draft; no fabricated draft/session state was injected. Used the versioned API from the authenticated participant browser.' };
  const observation = await page.evaluate(async ({ project, executionId, scenarioRef }) => {
    const response = await fetch('/api/v1/object-sim/observations', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': `beta-browser-existing-failure-${executionId}` }, body: JSON.stringify({ project_id: project, execution_id: executionId, scenario_ref: scenarioRef, time_s: 0, title: '실행 환경 오류로 결과 확인 불가', note: 'CARLA 실행이 실패하여 보존된 관측을 확인할 수 없습니다. 행동 발생이나 자차 대응에 대한 판단을 보류하며, 실행 환경 복구 후 같은 개정을 다시 실행해야 합니다.', kind: 'issue', behavior_exercised: 'uncertain', ego_outcome: 'uncertain' }) });
    const item = await response.json();
    const listing = await fetch(`/api/v1/object-sim/observations?project_id=${encodeURIComponent(project)}&execution_id=${encodeURIComponent(executionId)}`);
    return { status: response.status, item, listingStatus: listing.status, listing: await listing.json() };
  }, { project, executionId, scenarioRef: result.behavior.scenario_ref });
  assert.equal(observation.status, 201); assert.equal(observation.listingStatus, 200);
  assert.equal(observation.item.kind, 'issue'); assert.equal(observation.item.behavior_exercised, 'uncertain');
  assert.equal(observation.item.ego_outcome, 'uncertain');
  assert(observation.listing.items.some(item => item.id === observation.item.id));
  record('authenticated_browser_api_saves_issue_against_exact_failed_execution_revision', { observation_id: observation.item.id, execution_id: executionId, scenario_ref: observation.item.scenario_ref });
  assert.equal(await page.evaluate(() => sessionStorage.getItem('axiom.apiToken')), null);
  assert.equal(report.submissions, 0); assert.deepEqual(report.page_errors, []);
  await page.screenshot({ path: path.join(output, 'failed-run-participant-workspace.png'), fullPage: true });
  report.complete = true;
} catch (error) { report.complete = false; report.error = String(error); throw error; }
finally { report.finished_at = new Date().toISOString(); fs.writeFileSync(path.join(output, 'failed-execution-acceptance.json'), JSON.stringify(report, null, 2) + '\n'); await browser.close(); }
