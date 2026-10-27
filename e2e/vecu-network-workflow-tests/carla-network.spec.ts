import {test,expect} from '@playwright/test';
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';

test('retained actual network CARLA → wire/BSW/OS counters and synchronized vehicle cameras → resume/export',async({page,request})=>{
 const execution=process.env.AXIOM_VECU_NETWORK_CARLA_EXECUTION;
 test.skip(!execution,'An actual completed network CARLA run ID must be supplied; retained playback is not a new run.');
 const project=process.env.AXIOM_VECU_PROJECT??'project_9f48a965d4194be0b18859e334e007bb',errors:string[]=[];
 const camerasReady=async()=>{await expect(page.locator('.vecu-cameras .carla-camera-frame[data-camera-state="ready"]')).toHaveCount(2);};
 page.on('pageerror',error=>errors.push(error.message));
 const run=await(await request.get(`/api/v1/virtual-ecu-network/executions/${execution}?project_id=${project}`)).json();
 expect(run.status).toBe('completed');expect(run.summary.mode).toBe('carla_closed_loop');expect(run.summary.verdict).toBe('passed');
 expect(run.summary.checks.transport_and_control_readback).toBe('passed');expect(run.summary.physical_hardware_connected).toBe(false);
 const archiveMethods:Record<string,number>={};
 for(const side of ['baseline','faulted']){
  const artifact=run.artifacts.find((a:any)=>a.name.startsWith(side+'-city-camera-')&&a.media_type==='application/zip');
  const response=await request.get(artifact.download_url,{headers:{Range:'bytes=0-29'}});expect(response.status()).toBe(206);
  const bytes=await response.body();archiveMethods[side]=bytes.readUInt16LE(8);
  expect([0,8]).toContain(archiveMethods[side]);if(process.env.AXIOM_VECU_NETWORK_EXPECT_DEFLATE==='1')expect(archiveMethods[side]).toBe(8);
 }
 await page.goto(`/#hil-sim?project=${project}&mode=vecu&tab=network&execution=${execution}&step=70`);
 await expect(page.locator('.vecu-result-title>b')).toHaveText('completed');
 await expect(page.locator('.vecu-cameras canvas')).toHaveCount(2);
 for(const canvas of await page.locator('.vecu-cameras canvas').all())await expect(canvas).toBeVisible();
 await camerasReady();
 await expect(page.locator('.vecu-cameras')).toContainText('저장 결과 동기 재생');
 const counts=run.summary.network.sides;
 const duplicateRow=page.getByRole('row',{name:/UDP 중복 검출/});
 for(const [index,side] of ['baseline','faulted'].entries())await expect(duplicateRow.locator('td').nth(index)).toHaveText(String(counts[side].udp_duplicates));
 await page.getByLabel('network trace layer',{exact:true}).selectOption('bsw');
 await expect(page.locator('.vecu-measured-table').last()).toContainText('bsw');
 await page.getByLabel('network trace layer',{exact:true}).selectOption('os');
 await expect(page.locator('.vecu-measured-table').last()).toContainText('os');
 await page.getByLabel('vECU camera',{exact:true}).selectOption('front');
 for(const canvas of await page.locator('.vecu-cameras canvas').all())await expect(canvas).toBeVisible();
 await camerasReady();
 await page.getByLabel('vECU camera',{exact:true}).selectOption('chase');
 await page.getByLabel('network time cursor',{exact:false}).fill('0');
 await expect(page.getByLabel('Native ECU network signal flow').locator('strong').filter({hasText:'미수신'})).toHaveCount(2);
 await expect(page.getByLabel('Native ECU network signal flow')).toContainText('입력 age —');
 await page.getByLabel('network time cursor',{exact:false}).fill('100');
 await expect(page.locator('.vecu-cameras')).toContainText('차량 응답 관측 5.05 s');
 await camerasReady();
 if(process.env.AXIOM_VECU_NETWORK_EXPECT_CAN_WATCHDOG==='1'){
  const faultFlow=page.getByLabel('Native ECU network signal flow').locator('article').last();
  await expect(faultFlow).toContainText('ADAS → Brake ECU CAN 명령 전달 단절');
  await expect(faultFlow.locator('.vecu-network-node').nth(1)).not.toHaveClass(/fault-active/);
  await expect(faultFlow.locator('.vecu-network-node').nth(1)).toContainText('객체 정책 정상 추종 (tracking)');
  await expect(faultFlow.locator('.vecu-network-node').nth(2)).toHaveClass(/fault-active/);
  await expect(faultFlow.locator('.vecu-network-node').nth(2)).toContainText('CAN 명령 수신 시간 초과 · 보호 제동');
 }
 await page.locator('.vecu-cameras').screenshot({path:'test-results/vecu-network-carla-fault-cameras.png'});
 await page.getByLabel('Native ECU network signal flow').screenshot({path:'test-results/vecu-network-carla-fault-flow.png'});
 await page.getByLabel('network time cursor',{exact:false}).fill('148');
 await expect(page.locator('.vecu-cameras')).toContainText('차량 응답 관측 7.45 s');
 await camerasReady();
 const resume=page.url();await page.reload();await expect(page.getByLabel('network time cursor',{exact:false})).toHaveValue('148');
 await expect(page.locator('.vecu-cameras canvas')).toHaveCount(2);expect(page.url()).toBe(resume);
 await camerasReady();
 const download=page.waitForEvent('download');await page.getByRole('button',{name:'네트워크 실행 근거 내보내기',exact:true}).click();
 await(await download).saveAs('test-results/vecu-network-carla-evidence.json');
 expect(createHash('sha256').update(await readFile('test-results/vecu-network-carla-evidence.json')).digest('hex')).toBe(run.manifest_hash);
 await page.locator('.vecu-cameras').screenshot({path:'test-results/vecu-network-carla-cameras.png'});
 await page.locator('.vecu-events').nth(1).screenshot({path:'test-results/vecu-network-carla-counters.png'});
 await expect(page.getByLabel('Native ECU network signal flow')).toContainText('CAN 0x100');
 await page.getByLabel('Native ECU network signal flow').screenshot({path:'test-results/vecu-network-carla-flow.png'});
 await writeFile('test-results/vecu-network-carla-browser-checks.json',JSON.stringify({execution,url:resume,manifest_sha256:run.manifest_hash,archive_methods:archiveMethods,verified_selected_cameras:true,counters:counts,errors},null,2));
 expect(errors).toEqual([]);
});
