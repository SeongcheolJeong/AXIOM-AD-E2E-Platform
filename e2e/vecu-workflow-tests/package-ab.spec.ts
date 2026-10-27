import {test,expect} from '@playwright/test';
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';

test('native built packages → fixed-input A/B → observed ramp events → exact resume and sealed export',async({page,request})=>{
 const project=process.env.AXIOM_VECU_PROJECT??'project_9f48a965d4194be0b18859e334e007bb',errors:string[]=[];
 page.on('pageerror',e=>errors.push(e.message));
 const builds=(await(await request.get(`/api/v1/virtual-ecu/builds?project_id=${project}`)).json()).items;
 const retained=process.env.AXIOM_VECU_RETAINED_EXECUTION,retainedRun=retained?await(await request.get(`/api/v1/experiments/executions/${retained}`)).json():null;
 const selected=['cpp-immediate','cpp-ramp'].map((source,i)=>builds.find((b:any)=>b.source_id===source&&b.status==='completed'&&b.summary?.verdict==='passed'&&(!retainedRun||b.summary.runtime.binary_sha256===retainedRun.summary.packages[i?'faulted':'baseline'].runtime.binary_sha256)));
 expect(selected.every(Boolean)).toBe(true);
 const resumeTest=process.env.AXIOM_VECU_RESUME_TEST;
 if(retained){await page.goto(`/#hil-sim?project=${project}&mode=vecu&execution=${retained}&build=${selected[1].id}`);}else{
 if(resumeTest){await page.goto(`/#hil-sim?project=${project}&mode=vecu&test=${resumeTest}&revision=${process.env.AXIOM_VECU_RESUME_REVISION??1}&build=${selected[1].id}`);await expect(page.getByRole('button',{name:'새 vECU 실행',exact:true})).toBeEnabled();}else{
 await page.goto(`/#hil-sim?project=${project}&mode=vecu`);
 for(const [i,build] of selected.entries()){
  await page.getByLabel('vECU build source',{exact:true}).selectOption(build.source_id);
  await page.getByLabel('vECU build name',{exact:true}).fill(`Browser ${build.source_id} ${Date.now()}`);
  await page.getByLabel('vECU retained build',{exact:true}).selectOption(build.id);
  await expect(page.locator('.vecu-build-status')).toContainText('완료');
  await expect(page.getByText('보존 컴파일 로그',{exact:true})).toBeVisible();
  await expect(page.locator('.vecu-build-result')).toContainText(build.summary.runtime.binary_sha256);
  await expect(page.locator('.vecu-build-result')).toContainText('linux x86_64');
  await page.getByRole('button',{name:'빌드 결과를 패키지로 등록',exact:true}).click();
  await expect(page.getByLabel(i?'vECU variant package':'vECU package',{exact:true})).not.toHaveValue('');
 }
 await expect(page.getByLabel('vECU comparison',{exact:true})).toHaveValue('package_ab');
 await page.getByRole('button',{name:'18초 예제 입력 등록',exact:true}).click();await expect(page.getByLabel('vECU input',{exact:true})).not.toHaveValue('');
 await page.getByLabel('vECU test name',{exact:true}).fill('Browser native package A/B delay '+Date.now());
 await page.getByLabel('vECU mode',{exact:true}).selectOption('fixed_replay');
 }
 await page.getByLabel('vECU start',{exact:true}).fill('20');await page.getByLabel('vECU end',{exact:true}).fill('60');await page.getByRole('button',{name:'시험 개정 저장',exact:true}).click();await expect(page.getByRole('button',{name:'새 vECU 실행',exact:true})).toBeEnabled();
 await page.getByRole('button',{name:'새 vECU 실행',exact:true}).click();
 }
 await expect(page.locator('.vecu-result-title>b')).toHaveText(/completed|failed|cancelled/,{timeout:480000});await expect(page.locator('.vecu-result-title>b')).toHaveText('completed');
 const execution=await page.getByLabel('vECU retained execution',{exact:true}).inputValue();
 const run=await(await request.get(`/api/v1/experiments/executions/${execution}`)).json();expect(run.summary.comparison).toBe('package_ab');expect(run.summary.verdict).toBe('passed');
 const a=run.summary.packages.baseline,b=run.summary.packages.faulted;expect(a.runtime.binary_sha256).toBe(selected[0].summary.runtime.binary_sha256);expect(b.runtime.binary_sha256).toBe(selected[1].summary.runtime.binary_sha256);expect(a.runtime.binary_sha256).not.toBe(b.runtime.binary_sha256);
 expect(run.summary.sides.baseline.input_sha256).toBe(run.summary.sides.faulted.input_sha256);
 const trace=await(await request.get(run.artifacts.find((a:any)=>a.name==='vecu-trace.json').download_url)).json();
 const ar=trace.sides.baseline,br=trace.sides.faulted,first=br.findIndex((r:any)=>r.reply.state==='degraded'),full=br.findIndex((r:any,i:number)=>i>=first&&r.reply.control.brake>=.999);
 expect(first).toBeGreaterThan(0);expect(ar[first].reply.control.brake).toBe(1);expect(br[first].reply.control.brake).toBeLessThan(1);expect(br[full].time_ns-br[first].time_ns).toBe(300000000);
 await expect(page.locator('.vecu-measured-table')).toContainText('300 ms');await expect(page.locator('.vecu-playback-notice')).toContainText('ECU를 새로 실행하거나 실시간으로 제어하지 않습니다');
 await page.getByLabel('vECU event filter',{exact:true}).selectOption('full_brake');await page.locator('.vecu-event-list button.side-b').first().click();await expect(page.getByLabel('vECU time cursor',{exact:false})).toHaveValue(String(full));
 await expect(page.getByLabel('vECU saved signal flow')).toContainText('고정 입력 · 명령 기록');
 const resume=page.url();await page.reload();await expect(page.getByLabel('vECU time cursor',{exact:false})).toHaveValue(String(full));await expect(page.getByLabel('vECU retained build',{exact:true})).toHaveValue(selected[1].id);expect(page.url()).toBe(resume);
 const download=page.waitForEvent('download');await page.getByRole('button',{name:'실행 근거 내보내기',exact:true}).click();const file=await download;await file.saveAs('test-results/vecu-package-ab-evidence.json');const bytes=await readFile('test-results/vecu-package-ab-evidence.json');expect(createHash('sha256').update(bytes).digest('hex')).toBe(run.manifest_hash);
 await writeFile('test-results/vecu-package-ab-checks.json',JSON.stringify({execution,source_builds:selected.map(b=>b.id),runtime_a:a.runtime,runtime_b:b.runtime,first_degraded_step:first,full_brake_step:full,ramp_ns:br[full].time_ns-br[first].time_ns,manifest_sha256:run.manifest_hash,url:resume},null,2));
 await page.locator('.vecu-result').screenshot({path:'test-results/vecu-package-ab-result.png'});await page.locator('.vecu-build').screenshot({path:'test-results/vecu-package-ab-build.png'});
 await page.setViewportSize({width:760,height:1000});await expect.poll(()=>page.locator('.sidebar').evaluate(e=>e.getBoundingClientRect().right)).toBeLessThanOrEqual(0);expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.locator('.vecu-result').screenshot({path:'test-results/vecu-package-ab-narrow.png'});expect(errors).toEqual([]);
});
