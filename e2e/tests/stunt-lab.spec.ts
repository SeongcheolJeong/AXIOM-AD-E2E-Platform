import { expect, test } from '@playwright/test';

test('real API: immutable stunt authoring, exact revision restore and Ramp execution blocked', async ({ page, request }) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto('/#object-sim?project=default&mode=stunt');
  const lab = page.getByRole('region', { name: '추력 차량 시험장', exact: true });
  await expect(lab.getByRole('heading', { name: '추력 차량 시험장', exact: true })).toBeVisible();
  await expect(lab.getByText('physical_validation: not_established', { exact: true })).toBeVisible();
  await expect(lab.getByRole('button', { name: 'CARLA 실행', exact: true })).toBeDisabled();
  await expect(lab.locator('video')).toHaveCount(0);
  const name = `Stunt browser authoring ${Date.now()}`;
  await lab.getByLabel('시험 이름', { exact: true }).fill(name);
  await lab.getByLabel('최대 추력', { exact: true }).fill('41000');
  await lab.getByRole('button', { name: '개정 저장', exact: true }).click();
  await expect(lab.getByText('개정 r1 저장 완료', { exact: true })).toBeVisible();
  const firstURL = page.url();
  const query = new URLSearchParams(firstURL.split('?')[1]);
  const id = query.get('design'); expect(id).toBeTruthy();
  await lab.getByLabel('최대 추력', { exact: true }).fill('39000');
  await expect(lab.getByRole('button', { name: '실행 계획 확인', exact: true })).toBeDisabled();
  await lab.getByRole('button', { name: '개정 저장', exact: true }).click();
  await expect(lab.getByText('개정 r2 저장 완료', { exact: true })).toBeVisible();
  const first = await (await request.get(`/api/v1/object-sim/stunt-lab/designs/${id}?project_id=default&revision=1`)).json();
  expect(first.config.max_thrust_n).toBe(41000);
  await page.goto(firstURL); await page.reload();
  await expect(lab.getByLabel('최대 추력', { exact: true })).toHaveValue('41000');
  await expect(lab.getByLabel('시험 이름', { exact: true })).toHaveValue(name);
  await expect(lab.getByRole('button', { name: '개정 저장', exact: true })).toBeDisabled();
  await lab.getByRole('button', { name: /02 \/ RAMP/ }).click();
  await expect(lab.getByText('Ramp 실행 차단', { exact: true })).toBeVisible();
  await expect(lab.getByText(/missing_collision_asset/)).toBeVisible();
  await lab.getByRole('button', { name: '개정 저장', exact: true }).click();
  await expect(lab.getByText('개정 r1 저장 완료', { exact: true })).toBeVisible();
  await lab.getByRole('button', { name: '실행 계획 확인', exact: true }).click();
  await expect(lab.getByRole('alert')).toContainText('Ramp collision asset');
  await expect(lab.getByRole('button', { name: 'CARLA 실행', exact: true })).toBeDisabled();
  await page.screenshot({ path: 'test-results/stunt-authoring-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 760, height: 1000 });
  await page.mouse.move(700, 0);
  await page.reload();
  await expect(lab.getByText('Ramp 실행 차단', { exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/stunt-authoring-narrow.png', fullPage: true });
  expect(errors).toEqual([]);
});

test('mocked native adapter contract: submission recovery, cancellation, recorded telemetry A/B and export', async ({ page }) => {
  // These fixtures test the UI contract only. They are not CARLA or physics evidence.
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  const hash = 'a'.repeat(64), otherHash = 'b'.repeat(64);
  let reference = { id: 'pending', revision: 1, sha256: hash };
  let submitted = false, status = 'running', submitCount = 0, createCount = 0;
  const submissionKeys: string[] = [];
  const run = (id: string, state = 'completed') => ({ id, project_id: 'default', adapter_id: 'carla-stunt', experiment_kind: 'object_sim', status: state, cancellation_supported: true, input_hash: hash, summary: { design_ref: { ...reference, sha256: id.endsWith('b') ? otherHash : reference.sha256 }, trace_sha256: hash }, artifacts: state === 'running' ? [] : [{ name: 'stunt-trace.json', download_url: `/api/v1/stunt-ui-fixture/${id}`, sha256: hash }] });
  await page.route('**/api/v1/object-sim/stunt-lab/readiness?*', route => route.fulfill({ json: { available: true, configured: true, adapter_installed: true, physics_verified: true, worker_readiness: 'not_verified', reason: null } }));
  await page.route('**/api/v1/object-sim/stunt-lab/executions?*', route => route.fulfill({ json: { items: submitted ? [{ id: 'fixture-run-a', status, name: 'Fixture A', design_ref: reference }, { id: 'fixture-run-b', status: 'completed', name: 'Fixture B', design_ref: { ...reference, sha256: otherHash } }] : [] } }));
  await page.route('**/api/v1/object-sim/stunt-lab/designs/*/preview', async route => {
    const body = route.request().postDataJSON(); reference = { id: route.request().url().split('/').at(-2)!, revision: body.revision, sha256: body.sha256 };
    await route.fulfill({ json: { experiment: { fixture: true }, preview_hash: hash, worker_readiness: 'not_verified', fidelity: 'ui_contract_fixture' } });
  });
  await page.route('**/api/v1/experiments', route => {
    if (route.request().method() !== 'POST') return route.continue();
    createCount += 1;
    return route.fulfill({ json: { id: 'fixture-experiment' } });
  });
  await page.route('**/api/v1/experiments/fixture-experiment/executions', async route => {
    submitted = true; submitCount += 1;
    submissionKeys.push(route.request().headers()['idempotency-key']);
    if (submitCount === 1) await route.abort('failed');
    else await route.fulfill({ json: { id: 'fixture-run-a' } });
  });
  await page.route('**/api/v1/experiments/executions/fixture-run-**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/cancel')) { status = 'cancelled'; return route.fulfill({ json: { status } }); }
    if (path.endsWith('/export')) return route.fulfill({ json: { fixture: true, physical_validation: 'not_established' } });
    const id = path.split('/').at(-1)!;
    await route.fulfill({ json: run(id, id === 'fixture-run-a' ? status : 'completed') });
  });
  await page.route('**/api/v1/stunt-ui-fixture/*', route => route.fulfill({ json: {
    schema_version: 'carla-stunt-trace/1', physical_validation: 'not_established', design_ref: { ...reference, sha256: route.request().url().endsWith('b') ? otherHash : reference.sha256 }, seed: 42,
    config: { thrust_scale: route.request().url().endsWith('b') ? .3 : 1 }, summary: { demo_landing_passed: false, rotation_complete: false, accumulated_roll_deg: 12.1234567, max_height_m: 1.234567, impact_vertical_speed_mps: null, settled_window_s: 0, impulse_used_ns: 12345.67, reason: 'rotation_incomplete', contact: null, physical_validation: 'not_established', fixture: 'UI contract only' },
    frames: Array.from({ length: 11 }, (_, i) => ({ time_s: i / 10, position_m: [0, 0, i / 10], rotation_deg: [i, 0, 0], velocity_mps: [0, 0, 1], angular_velocity_rad_s: [1, 0, 0], force_n: [0, 0, 40000], torque_nm: [0, 0, 0], remaining_impulse_ns: 120000 - i * 1000, phase: 'fixture_observation' })),
  } }));
  await page.goto('/#object-sim?project=default&mode=stunt');
  const lab = page.getByRole('region', { name: '추력 차량 시험장', exact: true });
  await lab.getByRole('button', { name: '개정 저장', exact: true }).click();
  await expect(lab.getByRole('button', { name: 'CARLA 실행', exact: true })).toBeEnabled();
  await lab.getByRole('button', { name: 'CARLA 실행', exact: true }).click();
  await expect(lab.getByRole('alert')).toBeVisible();
  await page.reload();
  await expect(lab.getByRole('button', { name: 'CARLA 실행', exact: true })).toBeEnabled();
  await lab.getByRole('button', { name: 'CARLA 실행', exact: true }).click();
  await expect(lab.getByRole('button', { name: '실행 취소', exact: true })).toBeVisible();
  await lab.getByRole('button', { name: '실행 취소', exact: true }).click();
  await expect(lab.getByRole('heading', { name: '실행 판정 · 원본 근거', exact: true })).toBeVisible();
  await expect(lab.getByText('데모 착지 기준 미통과', { exact: true })).toBeVisible();
  await expect(lab.getByText('12.1 °', { exact: true })).toBeVisible();
  await expect(lab.locator('.stunt-raw-result pre')).toBeHidden();
  await lab.getByLabel('Stunt 비교 B', { exact: true }).selectOption('fixture-run-b');
  await expect(lab.getByRole('heading', { name: 'A/B 설정 차이', exact: true })).toBeVisible();
  await lab.getByLabel('Stunt 재생 시간', { exact: true }).fill('0.5');
  await expect(page).toHaveURL(/time=0.500/);
  await page.reload();
  await expect(lab.getByLabel('Stunt 재생 시간', { exact: true })).toHaveValue('0.5');
  await expect(lab.getByLabel('Stunt 비교 B', { exact: true })).toHaveValue('fixture-run-b');
  const download = page.waitForEvent('download');
  await lab.getByRole('button', { name: '실행 근거 JSON', exact: true }).click();
  expect((await download).suggestedFilename()).toBe('fixture-run-a.json');
  expect(submitCount).toBe(2); // Same pending request retried after lost transport response.
  expect(createCount).toBe(1);
  expect(submissionKeys[0]).toBeTruthy();
  expect(submissionKeys[1]).toBe(submissionKeys[0]);
  await page.route('**/api/v1/stunt-ui-fixture/fixture-run-a', route => route.fulfill({ json: {
    schema_version: 'carla-stunt-trace/1', physical_validation: 'not_established', design_ref: { ...reference, sha256: otherHash }, frames: [], summary: {},
  } }));
  await page.reload();
  await expect(lab.getByRole('alert')).toContainText('실행과 관측 기록의 개정·해시가 다릅니다');
  await expect(lab.getByRole('heading', { name: '실행 판정 · 원본 근거', exact: true })).toHaveCount(0);
  await expect(lab.locator('video')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('mocked API outage: stale readiness blocks execution and retry restores revision, A/B media and time', async ({ page, request }) => {
  // Authored browser media and mocked API responses test recovery only, not native physics.
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  const templates = await (await request.get('/api/v1/object-sim/stunt-lab/templates?project_id=default')).json();
  const config = templates.items[0].config;
  const hash = 'c'.repeat(64), reference = { id: 'outage-design', revision: 7, sha256: hash };
  const design = { ...reference, project_id: 'default', name: 'Outage recovery fixture r7', seed: 42, config };
  await page.goto('/');
  const media = Buffer.from(await page.evaluate(async () => {
    const canvas = document.createElement('canvas'); canvas.width = 32; canvas.height = 32;
    const context = canvas.getContext('2d')!;
    const stream = canvas.captureStream(10);
    const recorder = new MediaRecorder(stream, { mimeType: 'video/webm;codecs=vp8' });
    const chunks: BlobPart[] = [];
    recorder.ondataavailable = event => chunks.push(event.data);
    const stopped = new Promise<void>(resolve => { recorder.onstop = () => resolve(); });
    recorder.start();
    let frame = 0;
    const timer = setInterval(() => { context.fillStyle = `rgb(${frame++ % 255}, 40, 80)`; context.fillRect(0, 0, 32, 32); }, 50);
    await new Promise(resolve => setTimeout(resolve, 2000));
    recorder.stop(); await stopped; clearInterval(timer); stream.getTracks().forEach(track => track.stop());
    return Array.from(new Uint8Array(await new Blob(chunks, { type: 'video/webm' }).arrayBuffer()));
  }));
  let designUnavailable = true, readinessStatus = 200, readinessDetail = '';
  let traceUnavailable = false, mediaUnavailable = false;
  const hits: Record<string, number> = {};
  const rows = ['outage-a', 'outage-b'].map(id => ({ id, name: id, status: 'completed', design_ref: reference }));
  const ready = { available: true, configured: true, adapter_installed: true, physics_verified: true, worker_readiness: 'not_verified', reason: null };
  await page.route('**/api/v1/object-sim/stunt-lab/readiness?*', route => readinessStatus === 200
    ? route.fulfill({ json: ready })
    : route.fulfill({ status: readinessStatus, ...(readinessDetail ? { json: { detail: readinessDetail } } : { body: '' }) }));
  await page.route('**/api/v1/object-sim/stunt-lab/designs?*', route => route.fulfill({ json: { items: [design] } }));
  await page.route('**/api/v1/object-sim/stunt-lab/designs/outage-design?*', route => designUnavailable
    ? route.fulfill({ status: 500, body: '' }) : route.fulfill({ json: design }));
  await page.route('**/api/v1/object-sim/stunt-lab/executions?*', route => route.fulfill({ json: { items: rows } }));
  await page.route('**/api/v1/experiments/executions/outage-*', route => {
    const id = new URL(route.request().url()).pathname.split('/').at(-1)!;
    return route.fulfill({ json: {
      id, project_id: 'default', adapter_id: 'carla-stunt', experiment_kind: 'object_sim', status: 'completed', cancellation_supported: false,
      summary: { design_ref: reference, trace_sha256: hash }, input_hash: hash,
      artifacts: ['stunt-trace.json', 'external.mp4', 'onboard.mp4', 'camera-manifest.json'].map(name => ({ name, sha256: hash, download_url: `/api/v1/stunt-outage-fixture/${id}/${name}` })),
    } });
  });
  await page.route('**/api/v1/stunt-outage-fixture/**', route => {
    const path = new URL(route.request().url()).pathname; hits[path] = (hits[path] ?? 0) + 1;
    if (path.endsWith('stunt-trace.json')) return traceUnavailable ? route.fulfill({ status: 503, body: '' }) : route.fulfill({ json: {
      schema_version: 'carla-stunt-trace/1', physical_validation: 'not_established', design_ref: reference, seed: 42, config, summary: { demo_landing_passed: false },
      frames: Array.from({ length: 201 }, (_, index) => ({ time_s: index / 100, height_m: 0, position_m: [0, 0, 0], rotation_deg: [0, 0, 0], velocity_mps: [0, 0, 0], angular_velocity_rad_s: [0, 0, 0], force_n: [0, 0, 0], torque_nm: [0, 0, 0], remaining_impulse_ns: 120000, phase: 'authored_ui_fixture' })),
    } });
    if (mediaUnavailable) return route.fulfill({ status: 503, body: '' });
    if (path.endsWith('camera-manifest.json')) return route.fulfill({ json: {
      frames: ['external', 'onboard'].flatMap(camera => [{ camera, index: 0, time_s: .05 }, { camera, index: 39, time_s: 2 }]),
    } });
    return route.fulfill({ contentType: 'video/webm', body: media });
  });
  await page.goto('/#object-sim?project=default&mode=stunt&design=outage-design&revision=7&execution=outage-a&compare=outage-b&time=1.130');
  const lab = page.getByRole('region', { name: '추력 차량 시험장', exact: true });
  await expect(lab.getByRole('alert')).toContainText('서버가 정상 응답을 반환하지 않았습니다 (HTTP 500)');
  await expect(lab.getByLabel('시험 이름', { exact: true })).toHaveCount(0); // Do not silently replace the requested revision with a preset.
  designUnavailable = false;
  await lab.getByRole('button', { name: '다시 확인', exact: true }).click();
  const assertRestored = async () => {
    await expect(lab.getByLabel('시험 이름', { exact: true })).toHaveValue(design.name);
    await expect(lab.getByLabel('Stunt 저장 개정', { exact: true })).toHaveValue('outage-design:7');
    await expect(lab.getByLabel('Stunt 실행 A', { exact: true })).toHaveValue('outage-a');
    await expect(lab.getByLabel('Stunt 비교 B', { exact: true })).toHaveValue('outage-b');
    await expect(lab.locator('video')).toHaveCount(2);
    await expect.poll(() => lab.locator('video').evaluateAll(videos => videos.every(video => (video as HTMLVideoElement).readyState >= 2))).toBe(true);
    await expect.poll(() => lab.locator('video').evaluateAll(videos => videos.map(video => (video as HTMLVideoElement).currentTime.toFixed(2)))).toEqual(['1.08', '1.08']);
    await expect(lab.getByLabel('Stunt 재생 시간', { exact: true })).toHaveValue('1.13');
    await expect(lab.getByRole('button', { name: 'CARLA 실행', exact: true })).toBeEnabled();
    await expect(lab.getByRole('alert')).toHaveCount(0);
  };
  await assertRestored();
  readinessStatus = 500;
  await lab.getByRole('button', { name: '준비 상태 새로고침', exact: true }).click();
  await expect(lab.getByRole('alert')).toContainText('HTTP 500');
  await expect(lab.getByText('CARLA 현재 준비 상태 확인 필요', { exact: true })).toBeVisible();
  await expect(lab.getByText('CARLA 실행 준비됨', { exact: true })).toHaveCount(0);
  await expect(lab.getByRole('button', { name: 'CARLA 실행', exact: true })).toBeDisabled();
  readinessStatus = 200; traceUnavailable = true;
  await lab.getByRole('button', { name: '다시 확인', exact: true }).click();
  await expect(lab.getByRole('alert')).toContainText('HTTP 503');
  await expect(lab.getByRole('button', { name: 'CARLA 실행', exact: true })).toBeDisabled();
  await expect(lab.locator('video')).toHaveCount(0);
  await expect(page).toHaveURL(/time=1.130/);
  for (const id of ['outage-a', 'outage-b']) expect(hits[`/api/v1/stunt-outage-fixture/${id}/stunt-trace.json`]).toBeGreaterThanOrEqual(3);
  traceUnavailable = false; mediaUnavailable = true;
  await lab.getByRole('button', { name: '다시 확인', exact: true }).click();
  await expect(lab.getByRole('alert')).toContainText('HTTP 503');
  await expect(lab.getByRole('button', { name: 'CARLA 실행', exact: true })).toBeDisabled();
  await expect(lab.getByText('영상을 읽지 못했습니다', { exact: true })).toHaveCount(2);
  const beforeRecovery = { ...hits };
  mediaUnavailable = false;
  await lab.getByRole('button', { name: '다시 확인', exact: true }).click();
  await assertRestored();
  for (const id of ['outage-a', 'outage-b']) for (const name of ['stunt-trace.json', 'external.mp4', 'camera-manifest.json']) {
    const path = `/api/v1/stunt-outage-fixture/${id}/${name}`;
    expect(hits[path]).toBeGreaterThan(beforeRecovery[path]);
  }
  readinessStatus = 500; readinessDetail = '전용 엔진 RPC 요청이 거절되었습니다.';
  await lab.getByRole('button', { name: '준비 상태 새로고침', exact: true }).click();
  await expect(lab.getByRole('alert')).toContainText(readinessDetail);
  await expect(lab.getByRole('alert')).not.toContainText('서버가 정상 응답을 반환하지 않았습니다');
  await expect(lab.getByRole('button', { name: 'CARLA 실행', exact: true })).toBeDisabled();
  readinessStatus = 200;
  await lab.getByRole('button', { name: '다시 확인', exact: true }).click();
  await assertRestored();
  expect(errors).toEqual([]);
});
