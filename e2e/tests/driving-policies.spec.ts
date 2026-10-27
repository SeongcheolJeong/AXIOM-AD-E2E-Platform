import { test, expect } from '@playwright/test';

const cameras = ['CAM_FRONT', 'CAM_FRONT_RIGHT', 'CAM_FRONT_LEFT', 'CAM_BACK', 'CAM_BACK_LEFT', 'CAM_BACK_RIGHT'];
const identity = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];
test('driving input checks and trajectory inspection never imply GPU execution or a safety pass', async ({ page }, info) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  const input = { schema_version: 'driving-input/1', profile: 'vad-tiny-native/1',
    dataset: { name: 'nuScenes', version: 'v1.0-mini', scene_token: 'authored-scene', scene_name: 'authored-display', metadata_sha256: 'a'.repeat(64), use_scope: 'research_isolated', rights_note: 'Authored fixture; no native inference or real nuScenes pixels' },
    camera_order: cameras, frames: Array.from({ length: 3 }, (_, i) => ({ sample_token: `sample-${i}`, previous_sample_token: i ? `sample-${i - 1}` : '', timestamp_us: 1000000 + i * 500000, observed_until_us: 1000000 + i * 500000,
      cameras: cameras.map(name => ({ name, sample_data_token: `${name}-${i}`, path: `samples/${name}-${i}.png`, sha256: 'b'.repeat(64), timestamp_us: 1000000 + i * 500000, width: 1600, height: 900, lidar2img: identity })), can_bus: Array(18).fill(0),
      can_bus_source: { path: 'can_bus/authored_pose.json', sha256: 'c'.repeat(64), timestamp_us: 1000000 + i * 500000, policy: 'latest_at_or_before_anchor' },
      lidar_frame: { sample_data_token: `lidar-${i}`, calibration_token: 'cal', ego_pose_token: `pose-${i}`, ego2global: identity, lidar2ego: identity } })), route_command: 'all_modes', state_policy: 'reset_then_chronological_prefix' };
  const rawInput = JSON.stringify(input).replaceAll(JSON.stringify(identity), '[[1.0,0.0,0.0,0.0],[0.0,1.0,0.0,0.0],[0.0,0.0,1.0,0.0],[0.0,0.0,0.0,1.0]]');
  const preview = await (await page.request.post('/api/v1/driving-policies/preflight', { data: rawInput, headers: { 'Content-Type': 'application/json' } })).json();
  expect(preview.input_hash).toHaveLength(64);
  await page.goto('/#stacks');
  await page.getByRole('tab', { name: 'Driving policies', exact: true }).click();
  await expect(page.getByTestId('driving-native-status')).toHaveText('Native execution: not_configured');
  await page.getByLabel('Prepared driving input JSON', { exact: true }).setInputFiles({ name: 'authored-input.json', mimeType: 'application/json', buffer: Buffer.from(rawInput) });
  await expect(page.getByTestId('driving-input-check')).toContainText('3 frames · 18 camera observations');
  await expect(page.getByTestId('driving-input-check')).toContainText('not_checked');
  const result = { schema_version: 'driving-result/1', input, input_hash: preview.input_hash,
    model: { source_commit: '1688c4b1c3a9e2e7873ca9700ff8058170c0e3c8', source_files_hash: 'a'.repeat(64), adapter_source_hash: 'e'.repeat(64), checkpoint_sha256: 'b'.repeat(64), config_sha256: 'c'.repeat(64), resolved_config_hash: 'd'.repeat(64), adapter_version: 'axiom-vad-native/1', profile: input.profile, weights_rights: 'Authored display fixture' },
    execution: { status: 'completed', engine: 'native_torch', device: 'Authored, no GPU execution', library_versions: { fixture: '1' }, seed: 42, elapsed_ms: 0, peak_cuda_bytes: 0, ground_truth_in_model_input: false },
    outputs: input.frames.map(f => ({ sample_token: f.sample_token, timestamp_us: f.timestamp_us, coordinate_frame: 'current_lidar_xy_m', step_seconds: .5,
      modes: Object.fromEntries([['right', .2], ['left', -.2], ['straight', 0]].map(([name, factor]) => [name, Array.from({ length: 6 }, (_, i) => [(i + 1) * Number(factor), i + 1])])) })),
    limitations: ['Authored trajectory visualization fixture, not a learned inference result.'] };
  await page.getByLabel('Native driving result JSON', { exact: true }).setInputFiles({ name: 'authored-result.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(result).replace(`"input":${JSON.stringify(input)}`, `"input":${rawInput}`)) });
  await expect(page.getByTestId('driving-result-inspection')).toContainText('self_reported_receipt_not_independently_verified');
  await expect(page.getByTestId('driving-result-inspection')).toContainText('Verification: not_evaluated');
  await expect(page.getByRole('img', { name: 'Predicted driving trajectory modes' })).toBeVisible();
  await page.getByLabel('Recorded driving frame', { exact: true }).selectOption('2');
  await page.screenshot({ path: info.outputPath('driving-policy-desktop.png'), fullPage: true, animations: 'disabled' });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.locator('.sidebar').evaluate(e => e.getBoundingClientRect().right)).toBeLessThanOrEqual(0);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  await page.screenshot({ path: info.outputPath('driving-policy-mobile.png'), fullPage: true, animations: 'disabled' });
  const invalid = structuredClone(input); invalid.frames[0].can_bus_source.timestamp_us++;
  await page.getByLabel('Prepared driving input JSON', { exact: true }).setInputFiles({ name: 'invalid-future.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(invalid)) });
  await expect(page.getByRole('alert')).toContainText('CAN input must be observed');
  await expect(page.getByTestId('driving-result-inspection')).toHaveCount(0);
  expect(errors).toEqual([]);
});
