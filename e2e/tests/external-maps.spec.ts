import { createHash } from 'node:crypto';
import { expect, test, type Page } from '@playwright/test';
import type { MapConversion, MapExecution, SourceRecord } from '../../frontend/src/components/external-maps/map-source-contract';
import { mapSourceRoute, publishedMapRoute, sourceRef } from '../../frontend/src/components/external-maps/map-source-contract';
import { newCityScenario, savedCityScenarioRoute } from '../../frontend/src/components/city-sim/city-contract';

// Mocked API boundary checks only. No fixture below is a native simulator receipt.
const bytes = Buffer.from([0x50, 0x4b, 0x03, 0x04, 0x00, 0xff, 0x81, 0x80, 0x00]);
const digest = (value: Buffer) => createHash('sha256').update(value).digest('hex');
const original: SourceRecord = {
  id: 'source-seoul', project_id: 'default', revision: 1, sha256: 'a'.repeat(64),
  name: '서울 원본 · API 계약 fixture', source_kind: 'ngii_shp', filename: 'original.zip',
  source_sha256: digest(bytes), size_bytes: bytes.length,
  rights: { license: 'API contract fixture only', source_url: '', redistribution: 'restricted', notes: 'No real NGII data' },
  metadata: { test_fixture: true }, editable: false, native_ready: false, created_at: '2026-10-24T00:00:00Z',
};
const converted = (ready: boolean): MapConversion => ({
  id: 'conversion-seoul', revision: 1, sha256: 'b'.repeat(64), source_ref: sourceRef(original), ready,
  candidate_sha256: ready ? 'c'.repeat(64) : undefined, converter: { name: 'API contract fixture' },
  coordinate_transform: { source_crs: 'fixture', target_crs: 'fixture' },
  inference_flags: [{ kind: 'flat_elevation', source_id: 'L-001' }],
  diagnostics: ready ? [] : [{ severity: 'error', code: 'missing_boundary', message: 'L-001 원본 경계선이 부족합니다.', source_id: 'L-001' }],
});
const published = { id: 'map-from-source', revision: 1, sha256: 'd'.repeat(64) };
function fixtureState() {
  return { source: null as SourceRecord | null, conversions: [] as MapConversion[], jobs: [] as MapExecution[], maps: [] as typeof published[], posts: [] as { path: string; body: any; key?: string }[], pollError: false };
}
async function mockApi(page: Page, state: ReturnType<typeof fixtureState>) {
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname.replace('/api/v1', '');
    const respond = (json: unknown, status = 200) => route.fulfill({ status, json });
    if (request.method() === 'POST') {
      const body = request.postDataJSON();
      state.posts.push({ path, body, key: request.headers()['idempotency-key'] });
      if (path === '/map-sources') {
        state.source = { ...original, name: body.name, source_kind: body.source_kind, filename: body.filename, rights: body.rights, metadata: body.metadata };
        return respond(state.source, 201);
      }
      if (path.endsWith('/convert')) { state.conversions = [converted(false)]; return respond(state.conversions[0]); }
      if (path.endsWith('/qualify')) {
        state.jobs = [{ id: 'job-native-check', project_id: 'default', status: 'queued', outcome_uncertain: false, cancellation_supported: true }];
        return respond(state.jobs[0], 202);
      }
      if (path.endsWith('/publish')) { state.maps = [published]; return respond({ map_ref: published, map: published }, 201); }
      return respond({ detail: 'Unexpected write in contract fixture' }, 400);
    }
    if (path === '/team/whoami') return respond({ mode: 'project', role: 'Engineer', principal_id: 'fixture', user_label: 'Fixture', project_id: 'default', legacy_domains_available: false });
    if (path === '/projects') return respond({ items: [{ id: 'default', name: 'API contract fixture' }] });
    if (path === '/health') return respond({ status: 'ok', version: 'contract-fixture', database: 'fixture' });
    if (path === '/object-sim/presets') return respond({ items: [] });
    if (path === '/experiments/capabilities') return respond({ handlers: [] });
    if (path === '/map-sources/capabilities') return respond({
      schema_version: 'map-source-capabilities/1', source_kinds: ['opendrive', 'ngii_shp'], max_upload_bytes: 33554432, editable: false,
      conversion: { opendrive: { available: true }, ngii_shp: { available: true } },
      qualification: { adapter_id: 'carla-map-qualification', configured: true, readiness: 'not_probed', scope: 'native_map_load_readback_not_driving' }, limits: {},
    });
    if (path === '/map-sources') return respond({ items: state.source ? [state.source] : [] });
    if (path.endsWith('/revisions')) return respond({ items: state.source ? [state.source] : [] });
    if (path === '/map-sources/source-seoul' && state.source) return respond({ ...state.source, conversions: state.conversions, executions: state.jobs, published_maps: state.maps });
    if (path === '/experiments/executions/job-native-check') return state.pollError ? respond({ detail: 'Fixture worker status unavailable' }, 503) : respond(state.jobs[0]);
    if (path === '/maps/map-from-source') return respond({ ...published, project_id: 'default', sha256: 'e'.repeat(64) });
    return respond({ detail: 'Outside isolated map-source UI fixture' }, 404);
  });
}

test('binary ZIP, rights and metadata survive upload; unsupported conversion stays blocked and resumes exact pins', async ({ page }) => {
  const state = fixtureState(); await mockApi(page, state);
  await page.goto('/#object-sim?project=default&mode=map-sources');
  const library = page.getByRole('region', { name: '한국·외부 지도 라이브러리' });
  await library.getByLabel('지도 원본 종류').selectOption('ngii_shp');
  await library.getByLabel('외부 지도 이름').fill('서울 원본 · 계약 확인');
  await library.getByLabel('지도 원본 파일').setInputFiles({ name: 'original.zip', mimeType: 'application/zip', buffer: bytes });
  await library.getByLabel('지도 라이선스').fill('Fixture only');
  await library.getByLabel('지도 재배포 조건').selectOption('restricted');
  await library.getByText('원본 메타데이터 · JSON', { exact: true }).click();
  await library.getByLabel('지도 원본 메타데이터 JSON').fill('{"test_fixture":true,"origin":"binary"}');
  await library.getByRole('button', { name: '원본 등록', exact: true }).click();
  await expect(library.getByRole('heading', { name: '서울 원본 · 계약 확인' })).toBeVisible();
  const upload = state.posts.find(item => item.path === '/map-sources')!;
  expect(Buffer.from(upload.body.content_base64, 'base64').equals(bytes)).toBe(true);
  expect(upload.body.rights.redistribution).toBe('restricted');
  expect(upload.body.metadata).toEqual({ test_fixture: true, origin: 'binary' });
  expect(upload.key).toBeTruthy();
  await library.getByLabel('변환 선택 차로 ID').fill('L-001,L-002');
  await library.getByRole('checkbox', { name: /고도가 없는 2D/ }).check();
  await library.getByRole('button', { name: '변환·진단 실행' }).click();
  await expect(library.getByText('L-001 원본 경계선이 부족합니다.', { exact: true })).toBeVisible();
  await expect(library.getByRole('button', { name: 'CARLA 지도 확인 요청' })).toBeDisabled();
  await expect(library.getByRole('button', { name: '확인된 실행 지도로 게시' })).toBeDisabled();
  expect(state.posts.find(item => item.path.endsWith('/convert'))?.body.options).toEqual({ selected_link_ids: ['L-001', 'L-002'], allow_2d: true });
  const resume = page.url(); expect(resume).toContain('conversion_hash='); expect(resume).toContain('source_hash=');
  await page.reload();
  await expect(library.getByText('L-001 원본 경계선이 부족합니다.', { exact: true })).toBeVisible();
  await expect(library.getByRole('button', { name: 'CARLA 지도 확인 요청' })).toBeDisabled();
  expect(page.url()).toBe(resume);
});

test('queued and authored completed results cannot publish; only matching explicit native contract enables server request', async ({ page }, testInfo) => {
  const state = fixtureState(); state.source = { ...original, metadata: {
    provider: 'OpenStreetMap contributors · UI contract fixture', fidelity: 'osm_derived_procedural_roads_not_surveyed_hd_map',
    inference_flags: ['차로 폭과 평면 고도는 이 UI 계약 fixture의 가정입니다.'], coordinate_transform: { mode: 'fixture_only' },
  } }; state.conversions = [{ ...converted(true), inference_flags: [] }]; await mockApi(page, state);
  await page.goto('/' + mapSourceRoute('default', original, state.conversions[0]));
  const library = page.getByRole('region', { name: '한국·외부 지도 라이브러리' });
  const publish = library.getByRole('button', { name: '확인된 실행 지도로 게시' });
  await expect(library.getByText('OSM 도로에서 생성한 형상 · 측량 정밀도로지도가 아닙니다.', { exact: true })).toBeVisible();
  await expect(library.getByText('차로 폭과 평면 고도는 이 UI 계약 fixture의 가정입니다.', { exact: true })).toBeVisible();
  await expect(publish).toBeDisabled();
  await library.getByRole('button', { name: 'CARLA 지도 확인 요청' }).click();
  await expect(library.getByRole('heading', { name: '대기 중', exact: true })).toBeVisible();
  await expect(publish).toBeDisabled();
  await expect(library.getByRole('button', { name: 'CARLA 지도 확인 요청' })).toBeDisabled();
  state.jobs[0] = { ...state.jobs[0], status: 'completed', fidelity: 'authored_fixture', summary: { native_ready: true } };
  await library.getByRole('button', { name: '상태 다시 확인' }).click();
  await expect(library.getByText(/완료 상태만으로 지도 준비를 인정하지 않습니다/)).toBeVisible();
  await expect(publish).toBeDisabled();
  state.jobs[0] = { ...state.jobs[0], fidelity: 'native_carla_external_map_load_readback', summary: {
    source_ref: sourceRef(original), conversion_ref: sourceRef(state.conversions[0]), native_ready: true,
    driving_evaluated: false, physical_validation: false, qualification: { ready: true, scope: 'native_map_load_readback_not_driving' },
  } };
  await library.getByRole('button', { name: '상태 다시 확인' }).click();
  await expect(publish).toBeEnabled();
  await publish.click();
  await expect(library.getByRole('link', { name: '이 지도로 시나리오 작성 →' })).toHaveAttribute('href', publishedMapRoute('default', published));
  expect(state.posts.find(item => item.path.endsWith('/publish'))?.body).toMatchObject({ revision: 1, sha256: original.sha256, conversion_ref: sourceRef(state.conversions[0]), execution_id: 'job-native-check' });
  await library.getByRole('heading', { name: original.name }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath('source-declarations-desktop.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.locator('.sidebar').evaluate(element => element.getBoundingClientRect().right)).toBeLessThanOrEqual(1);
  await library.getByLabel('원본에 기록된 가공·가정').scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('source-declarations-mobile.png') });
});

test('a missing execution response or wrong source hash fails closed after URL resume', async ({ page }) => {
  const state = fixtureState(); state.source = { ...original }; state.conversions = [converted(true)];
  state.jobs = [{ id: 'job-native-check', project_id: 'default', status: 'queued' }]; state.pollError = true;
  await mockApi(page, state);
  await page.goto('/' + mapSourceRoute('default', original, state.conversions[0], 'job-native-check'));
  const library = page.getByRole('region', { name: '한국·외부 지도 라이브러리' });
  await expect(library.getByRole('alert')).toContainText('Fixture worker status unavailable');
  await expect(library.getByRole('button', { name: 'CARLA 지도 확인 요청' })).toBeDisabled();
  await expect(library.getByRole('button', { name: '확인된 실행 지도로 게시' })).toBeDisabled();
  await page.goto('/' + mapSourceRoute('default', { ...original, sha256: 'f'.repeat(64) }));
  await page.reload();
  await expect(library.getByRole('alert')).toContainText('원본의 프로젝트·개정·해시가 응답과 다릅니다');
  await expect(library.getByRole('button', { name: '변환·진단 실행' })).toHaveCount(0);
});

test('published map handoff rejects a changed catalog hash before opening scenario authoring', async ({ page }) => {
  const state = fixtureState(); await mockApi(page, state);
  await page.goto('/' + publishedMapRoute('default', published));
  await expect(page.getByText('요청한 지도 개정·해시·프로젝트가 저장 기록과 다릅니다. 다른 지도로 대신 열지 않습니다.', { exact: true })).toBeVisible();
});

test('saving a resumed city revision updates the exact URL and reloads the new revision', async ({ page }) => {
  const state = fixtureState(); await mockApi(page, state);
  const pose = { x: 0, y: 0, z: 0, heading_rad: 0 };
  const map = { ...published, project_id: 'default', definition: { name: 'External contract map', engine: 'carla',
    lanes: [{ id: 'lane-a', width_m: 3.5, points: [pose, { ...pose, x: 20 }] }], spawn_points: [pose] } };
  const asset = { id: 'prius', revision: 1, sha256: 'f'.repeat(64), project_id: 'default',
    definition: { actor_kind: 'vehicle' as const, blueprint_id: 'vehicle.toyota.prius' } };
  const first = { id: 'scenario-city', revision: 1, project_id: 'default', scenario: newCityScenario(map, asset), manifest_hash: 'a'.repeat(64), configuration_hash: 'c'.repeat(64) };
  let current = structuredClone(first);
  const reads: string[] = [];
  await page.route('**/api/v1/maps**', route => route.fulfill({ json: new URL(route.request().url()).pathname.endsWith('/maps') ? { items: [map] } : { ...map, lanes: map.definition.lanes } }));
  await page.route('**/api/v1/assets**', route => route.fulfill({ json: { items: [asset] } }));
  await page.route('**/api/v1/controllers**', route => route.fulfill({ json: { items: [] } }));
  await page.route('**/api/v1/scenario-v2**', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname.endsWith('/yaml/validate')) {
      const scenario = JSON.parse(request.postDataJSON().yaml);
      return route.fulfill({ json: { scenario, canonical_yaml: JSON.stringify(scenario), configuration_hash: 'c'.repeat(64), catalog_validation: {} } });
    }
    if (url.pathname.endsWith('/revisions')) {
      const body = request.postDataJSON(); expect(body.expected_revision).toBe(1);
      current = { ...current, revision: 2, scenario: body.scenario };
      return route.fulfill({ status: 201, json: current });
    }
    if (url.pathname.endsWith('/scenario-v2')) return route.fulfill({ json: { items: [current] } });
    reads.push(url.searchParams.get('revision') ?? '');
    return route.fulfill({ json: url.searchParams.get('revision') === '1' ? first : current });
  });
  await page.goto('/' + savedCityScenarioRoute(first));
  await page.getByLabel('City scenario name', { exact: true }).fill('Edited external scenario r2');
  await page.getByLabel('City actor speed', { exact: true }).fill('2');
  await page.getByRole('button', { name: '개정 저장', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('r2 저장됨');
  expect(new URLSearchParams(page.url().split('?')[1]).get('revision')).toBe('2');
  expect(page.url()).toContain('map_hash=' + published.sha256);
  await page.reload();
  await expect(page.getByLabel('City scenario name', { exact: true })).toHaveValue('Edited external scenario r2');
  await expect(page.getByLabel('City actor speed', { exact: true })).toHaveValue('2');
  expect(reads.at(-1)).toBe('2');
});
