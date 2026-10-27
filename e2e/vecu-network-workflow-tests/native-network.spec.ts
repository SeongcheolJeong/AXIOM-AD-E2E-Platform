import {test,expect} from '@playwright/test';
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';

test('normal UI → saved four-ECU config → native fault A/B → exact resume and sealed export',async({page,request})=>{
 const project=process.env.AXIOM_VECU_PROJECT??'project_9f48a965d4194be0b18859e334e007bb',errors:string[]=[];
 page.on('pageerror',e=>errors.push(e.message));
 const ready=await(await request.get(`/api/v1/virtual-ecu-network/readiness?project_id=${project}`)).json();
 expect(ready.available,'Native SIL Kit/BSW/OS profile must be available; a mocked result cannot satisfy this check.').toBe(true);
 const retained=process.env.AXIOM_VECU_NETWORK_RETAINED;
 await page.goto(`/#hil-sim?project=${project}&mode=vecu&tab=network${retained?'&execution='+retained:''}`);
 await expect(page.getByRole('heading',{name:'Virtual ECU Network Lab',exact:true})).toBeVisible();
 if(!retained){
  await page.getByRole('button',{name:'4 ECU 네트워크 등록',exact:true}).click();
  await expect(page.getByLabel('network configuration',{exact:true})).not.toHaveValue('');
  await page.getByRole('button',{name:'18초 예제 입력 등록',exact:true}).click();
  await expect(page.getByLabel('network input',{exact:true})).not.toHaveValue('');
  await page.getByLabel('network test name',{exact:true}).fill('Browser native CAN/Ethernet/BSW/OS '+Date.now());
  await page.getByLabel('network fault',{exact:true}).selectOption('udp_repeat');
  await page.getByRole('button',{name:'네트워크 시험 저장',exact:true}).click();
  await expect(page.getByRole('button',{name:'실행 준비 확인',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'실행 준비 확인',exact:true}).click();
  await expect(page.getByRole('status')).toContainText('실행 입력·엔진 준비 확인 완료');
  await page.getByRole('button',{name:'새 네트워크 A/B 실행',exact:true}).click();
 }
 await expect(page.locator('.vecu-result-title>b')).toHaveText(/completed|failed|cancelled/,{timeout:480000});
 await expect(page.locator('.vecu-result-title>b')).toHaveText('completed');
 const execution=await page.getByLabel('network retained execution',{exact:true}).inputValue();
 const run=await(await request.get(`/api/v1/virtual-ecu-network/executions/${execution}?project_id=${project}`)).json();
 expect(run.summary.verdict).toBe('passed');
 const artifacts=['vecu-trace.json','network-trace.json'];
 for(const name of artifacts){
  const artifact=run.artifacts.find((a:any)=>a.name===name);expect(artifact).toBeTruthy();
  const bytes=await(await request.get(artifact.download_url)).body();expect(createHash('sha256').update(bytes).digest('hex')).toBe(artifact.sha256);
 }
 await expect(page.getByLabel('network time cursor',{exact:false})).toBeVisible();
 await expect(page.getByLabel('Native ECU network signal flow')).toContainText('CAN 0x100');
 await expect(page.getByLabel('Native ECU network signal flow')).toContainText('OS ControlTask');
 await page.getByLabel('network time cursor',{exact:false}).fill('68');
 await expect(page.locator('.vecu-measured-table').last()).toContainText('ms');
 await page.getByLabel('network trace layer',{exact:true}).selectOption('bsw');
 const resume=page.url();await page.reload();await expect(page.getByLabel('network time cursor',{exact:false})).toHaveValue('68');expect(page.url()).toBe(resume);
 await expect(page.getByLabel('network saved test',{exact:true})).not.toHaveValue('');
 const download=page.waitForEvent('download');await page.getByRole('button',{name:'네트워크 실행 근거 내보내기',exact:true}).click();
 await(await download).saveAs('test-results/vecu-network-evidence.json');
 const bytes=await readFile('test-results/vecu-network-evidence.json');expect(createHash('sha256').update(bytes).digest('hex')).toBe(run.manifest_hash);
 const receipt=JSON.parse(bytes.toString());expect(receipt.prepared.spec.adapter_id).toBe('vecu-network');expect(receipt.prepared.input.test.network_ref.sha256).toMatch(/^[a-f0-9]{64}$/);
 await page.locator('.vecu-result').screenshot({path:'test-results/vecu-network-result.png'});
 await page.setViewportSize({width:760,height:1000});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await writeFile('test-results/vecu-network-browser-checks.json',JSON.stringify({execution,new_execution_performed:!retained,manifest_sha256:run.manifest_hash,url:resume,native_readiness:ready,errors},null,2));
 expect(errors).toEqual([]);
});
