// Normal browser/API flow in beta_workspace_server.py's isolated authored fixture.
// No simulator submission; no existing user workspace or service changes.
import { chromium, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';

assert(process.argv[2], 'Pass the isolated fixture runtime');
const runtime = path.resolve(process.argv[2]);
const base = process.argv[3] ?? 'http://127.0.0.1:8086';
assert.equal(new URL(base).hostname, '127.0.0.1');
const access = JSON.parse(fs.readFileSync(path.join(runtime, 'access.json'), 'utf8'));
const output = path.join(runtime, 'browser'); fs.mkdirSync(output, { recursive: true });
const report = { schema_version: 'beta-workspace-browser/1', scope: 'Authored isolated fixture; no simulator execution', checks: [], page_errors: [], execution_submissions: [] };
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome', headless: true });
async function open(token) {
  const context = await browser.newContext({ viewport: { width: 1600, height: 1100 } });
  await context.addInitScript(value => sessionStorage.setItem('axiom.apiToken', value), token);
  const page = await context.newPage(); page.setDefaultTimeout(20000);
  page.on('pageerror', error => report.page_errors.push(error.message));
  page.on('request', request => {
    if (request.method() === 'POST' && /\/experiments\/[^/]+\/executions/.test(request.url())) report.execution_submissions.push(new URL(request.url()).pathname);
  });
  return page;
}
const saveReport = () => fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
try {
  const participant = access.participants[0], page = await open(participant.token);
  const workspace = `${base}/#workspace?project=${participant.project_id}`;
  await page.goto(workspace);
  await expect(page.getByRole('region', { name: 'ADP 베타 작업공간', exact: true })).toBeVisible();
  await expect(page.getByLabel('Object Sim 베타 준비 상태')).toContainText(access.execution_id ? '확인 필요' : '불가');
  await expect(page.getByLabel('Software-DUT HIL 베타 준비 상태')).toContainText('불가');
  await expect(page.getByLabel('ADP 베타 운영자 현황')).toHaveCount(0);
  await page.getByLabel('새 베타 과제', { exact: true }).click();
  await page.getByLabel('베타 과제 유형').selectOption('retained_playback');
  await page.getByLabel('베타 과제 이름').fill('브라우저 독립 사용 · Object 과제');
  const creating = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/v1/beta/tasks');
  await page.getByRole('button', { name: '과제 만들기', exact: true }).click();
  const createResponse = await creating; assert.equal(createResponse.status(), 201);
  const task = await createResponse.json(); report.task_id = task.id;
  await expect(page.getByLabel('베타 과제 저장 개정').locator('option')).toHaveCount(2);
  await page.getByLabel('베타 과제 저장 개정').selectOption({ index: 1 });
  const updating = page.waitForResponse(response => response.request().method() === 'POST' && response.url().includes(`/beta/tasks/${task.id}/progress`));
  await page.getByRole('button', { name: '근거 연결·진행 저장', exact: true }).click();
  assert.equal((await updating).status(), 200);
  await page.reload();
  await expect(page.getByLabel('베타 과제 저장 개정')).toHaveValue(`${access.scenario.id}:1`);
  const resume = page.getByLabel('선택한 베타 과제 이어하기');
  const href = await resume.getAttribute('href');
  assert(href.includes(access.scenario.id) && href.includes('revision=1'));
  await page.screenshot({ path: path.join(output, 'participant-workspace.png'), fullPage: true });
  await resume.click();
  await expect(page).toHaveURL(new RegExp(`scenario=${access.scenario.id}`));
  await expect(page.getByLabel('ADP 베타 과제와 도움')).toBeVisible();
  await expect(page.getByLabel('City scenario name')).toHaveValue('Authored beta browser fixture');
  report.checks.push('Participant creates task, links exact saved revision, reloads and resumes normal Object UI');
  await page.goto(workspace + '&beta_task=' + task.id);
  if (access.execution_id) {
    await page.getByLabel('베타 과제 실행 기록').selectOption(access.execution_id);
    await page.getByLabel('베타 과제 재생 시점').fill('0.5');
    await page.getByRole('checkbox', { name: '선택한 실행의 결과를 열어 확인했습니다' }).check();
    const linking = page.waitForResponse(response => response.request().method() === 'POST' && response.url().includes(`/beta/tasks/${task.id}/progress`));
    await page.getByRole('button', { name: '근거 연결·진행 저장', exact: true }).click();
    assert.equal((await linking).status(), 200);
    await page.getByText('새 관찰 기록 작성', { exact: true }).click();
    await page.getByLabel('베타 관찰 제목').fill('작성 fixture 결과 조사');
    await page.getByLabel('베타 관찰 시점').fill('0.5');
    await page.getByLabel('베타 관찰 내용').fill('브라우저 연결 검사이며 실제 시뮬레이터 측정 결과가 아닙니다.');
    const observing = page.waitForResponse(response => response.request().method() === 'POST' && response.url().includes(`/beta/tasks/${task.id}/progress`));
    await page.getByRole('button', { name: '관찰 저장·과제 연결', exact: true }).click();
    const observed = await observing; assert.equal(observed.status(), 200);
    const completed = await observed.json();
    assert.equal(completed.status, 'completed');
    assert.equal(completed.completion_basis, 'retained_playback_observation');
    assert(completed.observation_id); report.observation_id = completed.observation_id;
    await page.reload();
    await expect(page.getByLabel('베타 과제 관찰 기록')).toHaveValue(completed.observation_id);
    report.checks.push('Create timestamped observation through normal API and link retained task to completion; reload preserves it');
  }
  await page.getByRole('button', { name: /이 과제에서 막혔어요/ }).click();
  await page.getByLabel('베타 신고 내용').fill('실행 환경이 준비되지 않아 저장한 개정으로 돌아왔습니다. 실행 시간을 안내해 주세요.');
  const reporting = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/v1/beta/feedback');
  await page.getByRole('button', { name: '의견 보내기', exact: true }).click();
  const feedbackResponse = await reporting; assert.equal(feedbackResponse.status(), 201);
  const feedback = await feedbackResponse.json(); report.feedback_id = feedback.id;
  assert.equal(feedback.context.source_ref.id, access.scenario.id);
  await expect(page.getByRole('status').filter({ hasText: '의견을 저장했습니다' })).toBeVisible();
  report.checks.push('Help request preserves exact task revision and source context');
  for (const module of ['log', 'hil']) {
    await page.goto(workspace);
    await page.getByLabel('새 베타 과제', { exact: true }).click();
    await page.getByLabel('베타 과제 모듈').selectOption(module);
    if (module === 'log') await page.getByLabel('베타 Log 대표 팩').selectOption('input-errors');
    else await page.getByLabel('베타 과제 이름').fill('브라우저 독립 사용 · HIL 과제');
    const starting = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/v1/beta/tasks');
    await page.getByRole('button', { name: '과제 만들기', exact: true }).click();
    const started = await starting; assert.equal(started.status(), 201);
    const value = await started.json();
    await page.getByLabel('선택한 베타 과제 이어하기').click();
    await expect(page).toHaveURL(new RegExp(module + '-sim'));
    if (module === 'log') {
      await expect(page.getByLabel('Log Sim beta task progress')).toContainText(value.id);
      await page.reload();
      await expect(page.getByLabel('Log Sim beta task progress')).toContainText(value.id);
    } else await expect(page.getByLabel('HIL 베타 시험 관리')).toBeVisible();
    report.checks.push(`${module} guidance resumes its existing normal module UI without submitting an execution`);
  }
  const second = await open(access.participants[1].token);
  await second.goto(workspace);
  await expect(second.getByLabel('Start workspace project')).toHaveValue(access.participants[1].project_id);
  await expect(second).toHaveURL(new RegExp(`project=${access.participants[1].project_id}`));
  await expect(second.getByRole('region', { name: 'ADP 베타 작업공간', exact: true })).not.toContainText('브라우저 독립 사용 · Object 과제');
  const denied = await second.evaluate(async project => (await fetch(`/api/v1/beta/tasks?project_id=${project}`, {
    headers: { Authorization: 'Bearer ' + sessionStorage.getItem('axiom.apiToken') },
  })).status, participant.project_id);
  assert.equal(denied, 404);
  report.checks.push('Second participant stays in assigned project; direct foreign-project API returns404');
  const operator = await open(access.operator);
  await operator.goto(workspace);
  await operator.getByLabel('ADP 베타 운영자 현황').locator('summary').click();
  await operator.getByLabel('베타 의견 처리 상태 ' + feedback.id).selectOption('investigating');
  await operator.getByLabel('베타 의견 운영 메모 ' + feedback.id).fill('격리된 브라우저 검사: 운영자 확인 중');
  const triaging = operator.waitForResponse(response => response.request().method() === 'POST' && response.url().includes(`/beta/feedback/${feedback.id}/triage`));
  await operator.getByLabel('베타 의견 ' + feedback.id, { exact: true }).getByRole('button', { name: '처리 상태 저장' }).click();
  assert.equal((await triaging).status(), 200);
  await expect(operator.getByLabel('베타 의견 처리 상태 ' + feedback.id)).toHaveValue('investigating');
  const downloading = operator.waitForEvent('download');
  await operator.getByRole('button', { name: 'JSON 내보내기', exact: true }).click();
  const download = await downloading; const file = path.join(output, 'overview.json'); await download.saveAs(file);
  const overview = JSON.parse(fs.readFileSync(file, 'utf8'));
  assert(overview.tasks.some(item => item.id === task.id));
  assert.equal(overview.counts.participants, 1, 'Same participant across three modules is counted once');
  assert.equal(overview.feedback.find(item => item.id === feedback.id).status, 'investigating');
  await operator.screenshot({ path: path.join(output, 'operator-overview.png'), fullPage: true });
  report.checks.push('Operator sees context, persists triage and exports matching JSON');
  const expired = await open('invalid-fixture-credential');
  await expired.goto(workspace);
  await expect(expired.getByText(/Invalid or revoked access token/).first()).toBeVisible();
  report.checks.push('Invalid credential produces an explicit recoverable access error');
  assert.deepEqual(report.page_errors, []);
  assert.deepEqual(report.execution_submissions, []);
  report.status = 'passed'; saveReport();
  console.log(JSON.stringify({ status: report.status, checks: report.checks.length, report: path.join(output, 'report.json') }));
} catch (error) {
  report.status = 'failed'; report.error = error.message; saveReport(); throw error;
} finally { await browser.close(); }
