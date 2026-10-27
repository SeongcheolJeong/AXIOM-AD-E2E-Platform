import {test, expect, type Locator} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const baseNs = 1700000000000000123n;

async function pixels(image: Locator) {
  return image.evaluate(async element => {
    const img = element as HTMLImageElement;
    await img.decode();
    const canvas = document.createElement('canvas');
    canvas.width = img.naturalWidth; canvas.height = img.naturalHeight;
    const context = canvas.getContext('2d')!;
    context.drawImage(img, 0, 0);
    const digest = await crypto.subtle.digest('SHA-256', context.getImageData(0, 0, canvas.width, canvas.height).data);
    return {width: canvas.width, height: canvas.height, hash: Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')};
  });
}

test('standard sensor MCAP → real pixels/points → explicit rig bindings → fixed revisions and withheld stale frames', async ({page}, testInfo) => {
  test.setTimeout(100_000);
  page.setDefaultTimeout(15_000);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const source = testInfo.outputPath('sensors.mcap');
  execFileSync(process.env.AXIOM_TEST_PYTHON ?? path.join(root, '.venv/bin/python'), [path.join(root, 'examples/generate_sensor_recording.py'), source, '--frames', '10']);
  const name = `Sensor workflow ${Date.now()}`;
  await page.goto('/#catalog');
  await page.getByRole('tab', {name: 'Recordings', exact: true}).click();
  await page.getByRole('button', {name: 'Import MCAP', exact: true}).first().click();
  const upload = page.getByRole('dialog', {name: 'Import an MCAP recording'});
  await upload.getByLabel('MCAP file', {exact: true}).setInputFiles(source);
  await upload.getByLabel('Recording name', {exact: true}).fill(name);
  await upload.getByRole('combobox', {name: 'Source kind', exact: true}).selectOption('synthetic');
  const imported = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/v1/recordings');
  await upload.getByRole('button', {name: 'Import recording', exact: true}).click();
  expect((await imported).status()).toBe(201);
  await expect(upload).not.toBeVisible();
  const records = await (await page.request.get('/api/v1/recordings?q=' + encodeURIComponent(name))).json();
  const recording = records.find((record: {name: string}) => record.name === name);
  expect(recording).toBeDefined();
  await page.getByRole('tab', {name: 'Sensor replay', exact: true}).click();
  await expect(page.getByLabel('Replay binding revision', {exact: true})).toHaveValue('0');
  const camera = page.getByTestId('sensor-camera-panel');
  const lidar = page.getByTestId('sensor-lidar-panel');
  const image = camera.getByTestId('decoded-camera-image');
  await expect(image).toBeVisible();
  const first = await pixels(image);
  expect([first.width, first.height]).toEqual([640, 360]);
  await expect(page.getByTestId('sensor-time-2')).toContainText('No sample at cursor');
  await page.getByLabel('Camera channel', {exact: true}).selectOption('3');
  await expect(page.getByTestId('sensor-time-3')).toContainText('in the future');
  await expect(camera.getByTestId('decoded-camera-image')).toHaveCount(0);
  await page.getByLabel('Camera channel', {exact: true}).selectOption('1');
  await page.getByRole('button', {name: 'Next sample time', exact: true}).click();
  await expect(page.getByTestId('sensor-cursor-ns')).toHaveText((baseNs + 5_000_000n).toString());
  await expect(lidar.getByTestId('sensor-point-counts')).toContainText('18,164 rendered / 18,164 source');
  const coordinate = lidar.getByTestId('sensor-point-coordinate');
  const native = (await coordinate.innerText()).split(',').map(Number);
  expect(native[2]).toBeCloseTo(-2.2, 4);
  await expect(lidar.getByRole('button', {name: 'Declared rig frame', exact: true})).toBeDisabled();

  await page.getByRole('button', {name: 'Create sensor rig', exact: true}).click();
  const rigForm = page.getByRole('dialog', {name: 'Create sensor rig', exact: true});
  await rigForm.getByLabel('Rig name', {exact: true}).fill(name + ' rig');
  await rigForm.getByLabel('Reference frame', {exact: true}).fill('ego');
  await rigForm.getByRole('combobox', {name: 'Coordinate convention', exact: true}).selectOption('right_handed_z_up');
  await rigForm.getByRole('combobox', {name: 'Rig source kind', exact: true}).selectOption('synthetic');
  await rigForm.getByLabel('Rig source', {exact: true}).fill('Original analytic browser fixture');
  await rigForm.getByLabel('Rig note', {exact: true}).fill('Declared fixture geometry, not measured calibration');
  const cameraRow = rigForm.getByRole('article', {name: 'Rig sensor 1'});
  const lidarRow = rigForm.getByRole('article', {name: 'Rig sensor 2'});
  await cameraRow.getByLabel('Sensor frame ID', {exact: true}).fill('camera_front_optical');
  await lidarRow.getByLabel('Sensor frame ID', {exact: true}).fill('lidar_roof');
  await lidarRow.getByLabel('Declare static transform', {exact: true}).check();
  await lidarRow.getByLabel('Translation Z', {exact: true}).fill('2.2');
  const rigSaved = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/api/v1/sensor-rigs'));
  await rigForm.getByRole('button', {name: 'Save sensor rig', exact: true}).click();
  const rig = await (await rigSaved).json();
  await expect(rigForm).not.toBeVisible();
  await page.getByRole('button', {name: 'Bind recording', exact: true}).click();
  const bindingForm = page.getByRole('dialog', {name: 'Bind recording to sensor rig'});
  await bindingForm.getByLabel('Binding note', {exact: true}).fill('Fixture clock offsets and bounded sample age');
  for (const [index, offset] of [[1, '-3000000'], [2, '-5000000']] as const) {
    const row = bindingForm.getByRole('article', {name: `Channel binding ${index}`});
    await row.getByLabel('Maximum sample age (ns)', {exact: true}).fill('150000000');
    await row.getByLabel('Log-time offset (ns)', {exact: true}).fill(offset);
  }
  await bindingForm.getByRole('button', {name: 'Save recording binding', exact: true}).click();
  await expect(bindingForm).not.toBeVisible();
  await expect(page.getByLabel('Replay binding revision', {exact: true})).toHaveValue('1');
  await lidar.getByRole('button', {name: 'Declared rig frame', exact: true}).click();
  await expect.poll(async () => Number((await coordinate.innerText()).split(',')[2])).toBeCloseTo(0, 4);
  const downloadEvent = page.waitForEvent('download');
  await page.getByRole('link', {name: 'Download binding', exact: true}).click();
  const bindingBytes = readFileSync((await (await downloadEvent).path())!);
  const savedBinding = await (await page.request.get(`/api/v1/recordings/${recording.id}/sensor-bindings?revision=1`)).json();
  await page.getByRole('button', {name: 'Next sample time', exact: true}).click();
  await expect(page.getByTestId('sensor-cursor-ns')).toHaveText((baseNs + 100_000_000n).toString());
  await expect(image).toBeVisible();
  await expect.poll(async () => (await pixels(image)).hash).not.toBe(first.hash);

  // Publish through the UI. The applied recording binding still owns its old rig snapshot.
  await page.getByRole('button', {name: 'Create rig revision', exact: true}).click();
  const revisionForm = page.getByRole('dialog', {name: 'Create sensor rig revision'});
  await revisionForm.getByRole('article', {name: 'Rig sensor 2'}).getByLabel('Translation X', {exact: true}).fill('2');
  await revisionForm.getByRole('button', {name: 'Publish rig revision', exact: true}).click();
  await expect(revisionForm).not.toBeVisible();
  await expect(page.getByLabel('Replay binding revision', {exact: true})).toHaveValue('1');
  await expect.poll(async () => Number((await coordinate.innerText()).split(',')[0])).toBeCloseTo(native[0], 4);
  const oldExport = await page.request.get(`/api/v1/recordings/${recording.id}/sensor-bindings/export?revision=1`);
  expect(await oldExport.body()).toEqual(bindingBytes);

  // Another author adds a new binding. Refresh must not silently change the applied revision.
  const newer = await page.request.post(`/api/v1/recordings/${recording.id}/sensor-bindings`, {data: {
    expected_revision: 1, rig_id: rig.id, rig_revision: 2, note: 'Explicit new geometry with exact-only age policy',
    channels: savedBinding.channels.map((channel: object) => ({...channel, max_age_ns: '0'})),
  }});
  expect(newer.status()).toBe(201);
  await page.getByRole('button', {name: 'Refresh sensor replay', exact: true}).click();
  await expect(page.getByLabel('Replay binding revision', {exact: true}).locator('option[value="2"]')).toHaveCount(1);
  await expect(page.getByLabel('Replay binding revision', {exact: true})).toHaveValue('1');
  await page.getByLabel('Replay binding revision', {exact: true}).selectOption('2');
  await expect.poll(async () => Number((await coordinate.innerText()).split(',')[0])).toBeCloseTo(native[0] + 2, 4);
  await page.getByLabel('Cursor offset (seconds)', {exact: true}).fill('0.15');
  await page.getByRole('button', {name: 'Seek', exact: true}).click();
  await expect(page.getByTestId('sensor-time-1')).toContainText('Stale · frame withheld');
  await expect(page.getByTestId('sensor-time-2')).toContainText('Stale · frame withheld');
  await expect(image).toHaveCount(0);
  await expect(coordinate).toHaveCount(0);
  const cursorBeforeInspect = await page.getByTestId('sensor-cursor-ns').innerText();
  await camera.getByRole('button', {name: 'Inspect original sample from channel 1', exact: true}).click();
  const inspector = page.getByRole('dialog', {name: 'Inspect original sensor sample'});
  await expect(inspector.getByTestId('decoded-camera-image')).toBeVisible();
  await expect(inspector.getByText('Explicit inspection · not a current-frame assertion')).toBeVisible();
  const ordinal = await inspector.getByLabel('Original sample ordinal', {exact: true}).inputValue();
  const exact = await (await page.request.get(`/api/v1/recordings/${recording.id}/sensor-frame?channel_id=1&ordinal=${ordinal}`)).json();
  const payloadEvent = page.waitForEvent('download');
  await inspector.getByRole('link', {name: 'Download original sample payload', exact: true}).click();
  const payload = readFileSync((await (await payloadEvent).path())!);
  expect(createHash('sha256').update(payload).digest('hex')).toBe(exact.message.sha256);
  await inspector.getByLabel('Original sample ordinal', {exact: true}).fill('99999');
  const missingFrame = page.waitForResponse(response => response.url().includes('/sensor-frame?') && new URL(response.url()).searchParams.get('ordinal') === '99999');
  await inspector.getByRole('button', {name: 'Inspect ordinal', exact: true}).click();
  expect((await missingFrame).status()).toBe(404);
  await expect(inspector.getByRole('alert')).toBeVisible();
  await expect(inspector.getByTestId('decoded-camera-image')).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(inspector).not.toBeVisible();
  await expect(page.getByTestId('sensor-cursor-ns')).toHaveText(cursorBeforeInspect);
  await page.setViewportSize({width: 390, height: 844});
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  expect(errors).toEqual([]);
});
