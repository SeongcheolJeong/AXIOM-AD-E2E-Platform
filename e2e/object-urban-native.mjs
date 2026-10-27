// Read-only browser acceptance of the explicitly published, retained urban A/B run.
// node e2e/object-urban-native.mjs .runtime/object-demo-20261009 .runtime/object-urban-20261009
// Requires filming.json; never submits jobs, binds presets or restarts services.
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';

assert(process.argv[2] && process.argv[3], 'Pass demo runtime and published urban receipt directory');
const runtime = path.resolve(process.argv[2]), output = path.resolve(process.argv[3]);
const prepared = JSON.parse(fs.readFileSync(path.join(runtime, 'prepared.json'), 'utf8'));
const filmed = JSON.parse(fs.readFileSync(path.join(output, 'filming.json'), 'utf8'));
assert.equal(filmed.schema_version, 'object-sim-urban-demo/1');
assert(filmed.complete && filmed.published && !filmed.probe, 'Urban filming must be complete, published and not a probe');
assert.equal(filmed.project_id, prepared.project_id);
const trials = ['A', 'B'].map(side => {
  const matches = filmed.trials.filter(trial => trial.side === side);
  assert.equal(matches.length, 1, `Expected one retained ${side} execution`);
  const trial = matches[0];
  assert.equal(trial.status, 'completed');
  assert(!trial.outcome_uncertain, `${side} execution has uncertain outcome`);
  assert.match(trial.manifest_hash, /^[a-f0-9]{64}$/);
  return trial;
});
const [left, right] = trials.map(trial => trial.execution_id);
assert.notEqual(left, right, 'A/B acceptance requires two distinct retained executions');
const sensors = Object.fromEntries(['front', 'chase'].map(key => {
  const sensor = filmed.scenario.sensors.find(item => item.id === key && item.kind === 'camera_rgb');
  assert(sensor && sensor.width > 0 && sensor.height > 0, `Missing authored ${key} RGB sensor`);
  return [key, { width: sensor.width, height: sensor.height }];
}));
const token = fs.readFileSync(path.join(runtime, 'access/presenter-token'), 'utf8').trim();
const base = prepared.target_api.replace(/\/api\/v1$/, '');
assert.equal(new URL(base).hostname, '127.0.0.1', 'Only the isolated local demo is authorized');
const report = {
  schema_version: 'object-urban-browser/1', started_at: new Date().toISOString(),
  project_id: prepared.project_id, receipt: 'filming.json', native_map_ref: filmed.native_map_ref,
  executions: trials.map(({ side, execution_id, manifest_hash, scenario_ref }) => ({ side, execution_id, manifest_hash, scenario_ref })),
  checks: [], page_errors: [], attempted_mutations: [],
};
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome' });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
page.setDefaultTimeout(30000);
await page.addInitScript(value => sessionStorage.setItem('axiom.apiToken', value), token);
page.on('pageerror', error => report.page_errors.push(error.message));
// These acceptance flows are read-only, including any unexpected UI request.
await page.route('**/api/v1/**', async route => {
  const request = route.request();
  if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
    report.attempted_mutations.push({ method: request.method(), path: new URL(request.url()).pathname });
    await route.abort('blockedbyclient');
  } else await route.continue();
});
const url = (mode, a, b) => base + '/#object-sim?' + new URLSearchParams({
  project: prepared.project_id, ...(mode ? { mode } : {}), ...(a ? { left: a } : {}), ...(b ? { right: b } : {}),
});
const record = (name, details = {}) => { report.checks.push({ name, ...details }); console.log(name); };
const timeline = () => page.getByRole('slider', { name: '비교 공통 시간' });
const layoutGroup = () => page.getByRole('group', { name: '공통 장면 구성', exact: true });
const cameraGroup = () => page.getByRole('group', { name: '공통 카메라', exact: true });
const storyPanel = () => page.getByLabel('관측 사건 시연', { exact: true });
const state = mode => page.waitForFunction(value => document.querySelector('[aria-label="관측 사건 시연"]')?.dataset.storyMode === value, mode);
const navigate = async target => { await page.goto(target); await page.reload(); };
const screenshot = async name => {
  await page.waitForTimeout(900); // Paused Three.js camera convergence only; source samples stay fixed.
  await page.screenshot({ path: path.join(output, name + '.png') });
};
const ready = async (count, sensor = 'front') => {
  await page.waitForFunction(({ count, sensor }) => {
    const frames = [...document.querySelectorAll('.carla-camera-frame')];
    return frames.length === count && frames.every(frame => {
      const canvas = frame.querySelector('canvas');
      return canvas?.style.visibility === 'visible' &&
        canvas.getAttribute('aria-label')?.startsWith(`CARLA ${sensor} 원본 프레임 `) &&
        frame.textContent.includes('SHA-256 verified');
    });
  }, { count, sensor });
};
const verifyOriginals = async (count, sensor) => {
  await ready(count, sensor);
  const frames = await page.locator('.carla-camera-frame').evaluateAll(elements => elements.map(frame => {
    const canvas = frame.querySelector('canvas');
    return {
      label: canvas.getAttribute('aria-label'), width: canvas.width, height: canvas.height,
      evidence: frame.querySelector('details')?.textContent,
      capture: frame.querySelector('figcaption')?.textContent,
    };
  }));
  assert.equal(frames.length, count);
  for (const frame of frames) {
    assert.equal(frame.width, sensors[sensor].width);
    assert.equal(frame.height, sensors[sensor].height);
    assert.match(frame.evidence, /SHA-256: [a-f0-9]{64}/);
    assert.match(frame.capture, /SHA-256 verified/);
  }
  return frames;
};
const verifyLayout = async (count, expected) => {
  const layouts = await page.locator('.city-workspace[data-presentation-layout]').evaluateAll(elements => elements.map(el => el.dataset.presentationLayout));
  assert.deepEqual(layouts, Array(count).fill(expected));
};
const heroRectangles = async count => {
  const rectangles = await page.locator('.city-workspace[data-presentation-layout]').evaluateAll(elements => elements.map(el => {
    const rect = selector => {
      const r = el.querySelector(selector)?.getBoundingClientRect();
      return r ? { x: r.x, y: r.y, width: r.width, height: r.height, right: r.right, bottom: r.bottom } : null;
    };
    return { camera: rect('.city-native-camera-panel'), scene: rect('.city-scene-panel'), image: rect('.carla-camera-frame canvas'), source: el.querySelector('.city-native-town-badge')?.textContent };
  }));
  assert.equal(rectangles.length, count);
  for (const item of rectangles) {
    assert.match(item.source, /CARLA 도시 지도 · Town\d/);
    assert(item.camera && item.scene && item.image, 'Native RGB and analysis panels must both exist');
    assert(item.camera.x < item.scene.x && item.camera.width > item.scene.width, 'Native camera must be the larger, first panel');
    for (const [name, r] of Object.entries(item).filter(([key]) => key !== 'source')) {
      assert(r.width > 0 && r.height > 0 && r.x >= 0 && r.y >= 0 && r.right <= 1920 && r.bottom <= 1080, `${name} exceeds the 1080p presentation viewport`);
    }
  }
  return rectangles;
};

try {
  await page.goto(url());
  await page.getByRole('heading', { name: '도심 주행', exact: true }).waitFor();
  assert(await page.locator('.demo-card').count() >= 4, 'Existing presets remain available when new scenes are added');
  for (const title of ['장애물 회피', '양보 후 진입', '8대 교차로', '도심 주행']) {
    const card = page.locator('.demo-card').filter({ has: page.getByRole('heading', { name: title, exact: true }) });
    assert.equal(await card.count(), 1);
    assert(await card.getByRole('button', { name: '시연 시작', exact: true }).isEnabled());
  }
  const urban = page.locator('.demo-card').filter({ has: page.getByRole('heading', { name: '도심 주행', exact: true }) });
  assert.match(await urban.innerText(), /CARLA 도시 배경은 저장된 원본 RGB에서 확인합니다/);
  await screenshot('urban-four-presets');
  record('four_presets_original_three_preserved_and_published_urban_entry');

  await urban.getByRole('button', { name: '시연 시작', exact: true }).click();
  await state('guided'); await ready(1);
  await page.getByRole('button', { name: '일시정지', exact: true }).click(); await state('manual');
  assert.equal(new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('left'), left);
  assert.equal(await layoutGroup().getByRole('button', { name: '자동', exact: true }).getAttribute('aria-pressed'), 'true');
  await verifyLayout(1, 'native_camera');
  assert.equal(await page.getByRole('combobox', { name: 'City viewer actor' }).locator('option').count(), filmed.scenario.actors.length);
  const singleFrames = await verifyOriginals(1, 'front');
  const singleLayout = await heroRectangles(1);
  await screenshot('urban-single-native-hero');
  record('urban_story_entry_actual_town_auto_native_camera', { frames: singleFrames, layout: singleLayout });

  await navigate(url('compare', left, right)); await ready(2);
  await page.getByRole('button', { name: '발표 화면', exact: true }).click();
  await verifyLayout(2, 'native_camera');
  for (const [index, letter] of ['A', 'B'].entries()) {
    const side = page.getByRole('article', { name: `실행 ${letter}`, exact: true });
    assert.match(await side.locator('.object-compare-source').textContent(), new RegExp(trials[index].manifest_hash));
  }
  const frontFrames = await verifyOriginals(2, 'front');
  const pairLayout = await heroRectangles(2);
  const commonTime = await timeline().inputValue();
  await screenshot('urban-ab-front-native-hero');
  record('ab_auto_native_camera_original_front_and_pinned_manifests', { common_time_s: Number(commonTime), frames: frontFrames, layout: pairLayout });

  await cameraGroup().getByRole('button', { name: '추적', exact: true }).click();
  const chaseFrames = await verifyOriginals(2, 'chase');
  assert.equal(await timeline().inputValue(), commonTime);
  await screenshot('urban-ab-chase-native-hero');
  record('shared_chase_originals_preserve_common_clock', { frames: chaseFrames });

  await layoutGroup().getByRole('button', { name: '분석 중심', exact: true }).click();
  await verifyLayout(2, 'analysis'); await ready(2, 'chase');
  assert.equal(await timeline().inputValue(), commonTime);
  await screenshot('urban-ab-analysis');
  await layoutGroup().getByRole('button', { name: '카메라 중심', exact: true }).click();
  await verifyLayout(2, 'native_camera'); await ready(2, 'chase');
  assert.equal(await timeline().inputValue(), commonTime);
  await layoutGroup().getByRole('button', { name: '자동', exact: true }).click();
  await verifyLayout(2, 'native_camera');
  record('shared_analysis_camera_auto_toggle_preserves_frames_and_clock');

  await cameraGroup().getByRole('button', { name: '전방', exact: true }).click(); await ready(2);
  await storyPanel().getByRole('button', { name: '시연 시작', exact: true }).click();
  await state('guided'); await ready(2);
  await layoutGroup().getByRole('button', { name: '분석 중심', exact: true }).click(); await state('manual');
  const stopped = await timeline().inputValue(); await page.waitForTimeout(300);
  assert.equal(await timeline().inputValue(), stopped);
  await layoutGroup().getByRole('button', { name: '카메라 중심', exact: true }).click();
  await verifyLayout(2, 'native_camera'); await ready(2);
  record('manual_layout_change_interrupts_guide_without_clock_jump');

  await page.getByRole('button', { name: '함께 재생', exact: true }).click();
  await page.waitForFunction(before => Number(document.querySelector('[aria-label="비교 공통 시간"]').value) > before + .3, Number(stopped));
  await page.getByRole('button', { name: '일시정지', exact: true }).click(); await ready(2);
  record('manual_ab_replay_retained_rgb_ready_gate', { from_time_s: Number(stopped), to_time_s: Number(await timeline().inputValue()), frames: await verifyOriginals(2, 'front') });
  await screenshot('urban-ab-final');
  assert.deepEqual(report.page_errors, []);
  assert.deepEqual(report.attempted_mutations, []);
  report.complete = true;
} catch (error) {
  report.complete = false; report.error = String(error);
  await screenshot('urban-browser-failure').catch(() => {});
  throw error;
} finally {
  report.finished_at = new Date().toISOString();
  fs.writeFileSync(path.join(output, process.env.OBJECT_URBAN_REPORT ?? 'browser-acceptance.json'), JSON.stringify(report, null, 2) + '\n');
  await browser.close();
}
