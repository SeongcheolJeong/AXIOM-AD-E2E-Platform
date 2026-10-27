import {test, expect} from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Opt-in real evidence. Never substitute authored inputs when this file is absent.
const receiptPath = process.env.AXIOM_NATIVE_DRIVING_RECEIPT;
test('actual VAD receipt is inspected and rendered through the shared API', async ({page}, info) => {
  test.skip(!receiptPath, 'Requires a separately accepted real native receipt');
  const filename = path.resolve(receiptPath!);
  const rawResult = fs.readFileSync(filename, 'utf8');
  const result = JSON.parse(rawResult);
  expect(result.model.checkpoint_sha256).toBe('430e8881f33bdb19f86557db00c2ccc77da22b3bc042ba7f41a9ef4c2b258a16');
  expect(result.model.adapter_version).toBe('axiom-vad-native/2');
  expect(result.input.dataset.scene_name).toBe('scene-0061');
  expect(result.execution.engine).toBe('native_torch');
  expect(result.execution.status).toBe('completed');
  expect(result.outputs).toHaveLength(3);
  const rawInput = fs.readFileSync(path.join(path.dirname(filename), 'driving-input-20261005.json'), 'utf8');
  const check = await page.request.post('/api/v1/driving-policies/preflight', {data:rawInput, headers:{'Content-Type':'application/json'}});
  expect(check.status()).toBe(200);
  expect((await check.json()).input_hash).toBe(result.input_hash);
  const inspected = await page.request.post('/api/v1/driving-policies/inspect-result', {data:rawResult, headers:{'Content-Type':'application/json'}});
  expect(inspected.status()).toBe(200);
  const receipt = await inspected.json();
  expect(receipt.outputs).toEqual(result.outputs);
  expect(receipt.verification_verdict).toBe('not_evaluated');
  const errors:string[]=[];page.on('pageerror', e=>errors.push(e.message));
  await page.goto('/#stacks');
  await page.getByRole('tab',{name:'Driving policies',exact:true}).click();
  await page.getByLabel('Prepared driving input JSON',{exact:true}).setInputFiles({name:'real-input.json',mimeType:'application/json',buffer:Buffer.from(rawInput)});
  await expect(page.getByTestId('driving-input-check')).toContainText('3 frames · 18 camera observations');
  await page.getByLabel('Native driving result JSON',{exact:true}).setInputFiles({name:'real-result.json',mimeType:'application/json',buffer:Buffer.from(rawResult)});
  await expect(page.getByTestId('driving-result-inspection')).toContainText('self_reported_receipt_not_independently_verified');
  await expect(page.getByTestId('driving-result-inspection')).toContainText('Verification: not_evaluated');
  await expect(page.getByRole('img',{name:'Predicted driving trajectory modes'})).toBeVisible();
  await page.getByLabel('Recorded driving frame',{exact:true}).selectOption('2');
  await page.screenshot({path:info.outputPath('real-vad-trajectories.png'),fullPage:true,animations:'disabled'});
  expect(errors).toEqual([]);
});
