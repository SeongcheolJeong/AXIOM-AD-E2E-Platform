import {test, expect} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const baseNs = 1789171200000000123n;

test('MCAP upload → exact clock seek → numeric window → opaque bytes → original download', async ({page}, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const source = testInfo.outputPath('synthetic.mcap');
  execFileSync(process.env.AXIOM_TEST_PYTHON ?? path.join(root, '.venv/bin/python'), [
    path.join(root, 'examples/generate_recording.py'), source,
  ]);
  const original = readFileSync(source);
  const name = `Recording workflow ${Date.now()}`;
  await page.goto('/');
  await expect(page.getByText('Local API connected')).toBeVisible();
  await page.getByRole('button', {name: 'Data catalog', exact: true}).click();
  await page.getByRole('tab', {name: 'Recordings', exact: true}).click();
  await page.getByRole('button', {name: 'Import MCAP', exact: true}).first().click();
  const dialog = page.getByRole('dialog', {name: 'Import an MCAP recording'});
  await dialog.getByLabel('MCAP file', {exact: true}).setInputFiles(source);
  await dialog.getByLabel('Recording name', {exact: true}).fill(name);
  await dialog.getByRole('combobox', {name: 'Source kind', exact: true}).selectOption('synthetic');
  await dialog.getByLabel(/Source label/).fill('Original browser workflow fixture');
  const importResponse = page.waitForResponse(response => response.request().method() === 'POST' && response.url().includes('/api/v1/recordings'));
  await dialog.getByRole('button', {name: 'Import recording', exact: true}).click();
  const response = await importResponse;
  expect(response.status()).toBe(201);
  await expect(dialog).not.toBeVisible();
  const records = await (await page.request.get('/api/v1/recordings?q=' + encodeURIComponent(name))).json();
  const saved = records.find((record: {name: string}) => record.name === name);
  expect(saved).toBeDefined();
  const recording = await (await page.request.get('/api/v1/recordings/' + saved.id)).json();
  expect(recording.message_count).toBe(374);
  expect(recording.channel_count).toBe(4);
  expect(recording.sha256).toBe(createHash('sha256').update(original).digest('hex'));
  expect(recording.index.source_sha256).toBe(recording.sha256);
  await expect(page.getByTestId('recording-detail').getByRole('heading', {name, exact: true})).toBeVisible();
  const telemetry = recording.channels.find((channel: {topic: string}) => channel.topic === '/vehicle/telemetry');
  await page.getByRole('button', {name: `Select channel ${telemetry.id} · /vehicle/telemetry`, exact: true}).click();
  await expect(page.getByTestId('recording-sample-log-time')).toHaveText(baseNs.toString());
  await page.getByLabel('Cursor offset (seconds)', {exact: true}).fill('8.05');
  await page.getByRole('button', {name: 'Seek', exact: true}).click();
  await expect(page.getByTestId('recording-sample-log-time')).toHaveText((baseNs + 8_000_000_000n).toString());
  await expect(page.getByTestId('recording-sample-publish-time')).toHaveText((baseNs + 7_997_000_000n).toString());
  await expect(page.getByLabel('Decoded sample JSON')).toContainText('speed_mps');
  await page.getByRole('combobox', {name: 'Numeric signal', exact: true}).selectOption('speed_mps');
  await expect(page.getByRole('region', {name: 'Actual sample window signal chart'})).toBeVisible();
  await page.getByRole('button', {name: 'Browse from first sample', exact: true}).click();
  await expect(page.getByTestId('recording-sample-log-time')).toHaveText(baseNs.toString());
  await page.getByRole('button', {name: 'Next window', exact: true}).click();
  await expect(page.getByTestId('recording-sample-log-time')).toHaveText((baseNs + 1_000_000_000n).toString());
  await page.getByRole('button', {name: 'Previous window', exact: true}).click();
  await expect(page.getByTestId('recording-sample-log-time')).toHaveText(baseNs.toString());
  await page.getByRole('button', {name: 'Seek at cursor', exact: true}).click();
  await expect(page.getByTestId('recording-sample-log-time')).toHaveText((baseNs + 8_000_000_000n).toString());
  const opaque = recording.channels.find((channel: {topic: string}) => channel.topic === '/sensor/opaque_example');
  await page.getByRole('button', {name: `Select channel ${opaque.id} · /sensor/opaque_example`, exact: true}).click();
  await expect(page.getByLabel('Opaque sample hex preview')).toBeVisible();
  await page.getByRole('button', {name: 'Go to recording start', exact: true}).click();
  await expect(page.getByTestId('recording-sample-log-time')).toHaveText((baseNs + 40_000_000n).toString());
  await expect(page.getByTestId('recording-sample-time-status')).toContainText(/future/i);
  const downloadEvent = page.waitForEvent('download');
  await page.getByRole('link', {name: 'Download original recording', exact: true}).click();
  const download = await downloadEvent;
  expect(readFileSync((await download.path())!)).toEqual(original);
  await page.setViewportSize({width: 390, height: 844});
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
  await page.reload();
  await page.getByRole('tab', {name: 'Recordings', exact: true}).click();
  await page.getByPlaceholder('Search recordings or sources…').fill(name);
  await page.getByRole('button', {name: `Open recording ${name}`, exact: true}).click();
  await expect(page.getByTestId('recording-detail').getByRole('heading', {name, exact: true})).toBeVisible();
  expect(errors).toEqual([]);
});

test('zero-time duplicates remain accessible across chronological windows', async ({page}, testInfo) => {
  const source = testInfo.outputPath('zero-time.mcap');
  execFileSync(process.env.AXIOM_TEST_PYTHON ?? path.join(root, '.venv/bin/python'), ['-c', `
import sys,json,pathlib
from mcap.writer import Writer
path=pathlib.Path(sys.argv[1]);path.parent.mkdir(parents=True,exist_ok=True)
with path.open('xb') as file:
    writer=Writer(file,enable_data_crcs=True);writer.start()
    channel=writer.register_channel('/same-time','json',0)
    for index in range(150):
        writer.add_message(channel,0,json.dumps({'value':index}).encode(),0,sequence=index)
    writer.finish()
`, source]);
  await page.goto('/#catalog');
  await page.getByRole('tab', {name: 'Recordings', exact: true}).click();
  await page.getByRole('button', {name: 'Import MCAP', exact: true}).first().click();
  const dialog = page.getByRole('dialog', {name: 'Import an MCAP recording'});
  await dialog.getByLabel('MCAP file', {exact: true}).setInputFiles(source);
  await dialog.getByLabel('Recording name', {exact: true}).fill(`Duplicate clock workflow ${Date.now()}`);
  await dialog.getByRole('combobox', {name: 'Source kind', exact: true}).selectOption('synthetic');
  await dialog.getByRole('button', {name: 'Import recording', exact: true}).click();
  await expect(dialog).not.toBeVisible();
  await expect(page.getByTestId('recording-sample-log-time')).toHaveText('0');
  await expect(page.getByLabel('Decoded sample JSON')).toContainText('149');
  await page.getByRole('combobox', {name: 'Samples per request', exact: true}).selectOption('100');
  await page.getByRole('button', {name: 'Browse from first sample', exact: true}).click();
  await expect(page.getByLabel('Decoded sample JSON')).toHaveText(/"value":\s*0/);
  await expect(page.getByRole('button', {name: /Inspect sample ordinal/})).toHaveCount(100);
  await page.getByRole('button', {name: 'Next window', exact: true}).click();
  await expect(page.getByLabel('Decoded sample JSON')).toHaveText(/"value":\s*100/);
  await expect(page.getByRole('button', {name: /Inspect sample ordinal/})).toHaveCount(50);
  await expect(page.getByRole('button', {name: 'Next window', exact: true})).toBeDisabled();
  await page.getByRole('button', {name: 'Previous window', exact: true}).click();
  await expect(page.getByLabel('Decoded sample JSON')).toHaveText(/"value":\s*0/);
});
