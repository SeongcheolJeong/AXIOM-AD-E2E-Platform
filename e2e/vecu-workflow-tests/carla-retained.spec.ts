import {test,expect} from '@playwright/test';
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';

test('retained native CARLA packages → synchronized cameras and measured geometry → exact resume/export',async({page,request})=>{
 const project=process.env.AXIOM_VECU_PROJECT??'project_9f48a965d4194be0b18859e334e007bb',execution=process.env.AXIOM_VECU_CARLA_EXECUTION??'job_9d954e90612546cca4097c3f12cc513d',errors:string[]=[];
 page.on('pageerror',e=>errors.push(e.message));
 const run=await(await request.get(`/api/v1/experiments/executions/${execution}`)).json();
 expect(run.status).toBe('completed');expect(run.summary.comparison).toBe('package_ab');expect(run.summary.mode).toBe('carla_closed_loop');expect(run.summary.checks.transport_and_control_readback).toBe('passed');expect(run.summary.physical_hardware_connected).toBe(false);
 const artifact=async(name:string)=>(await(await request.get(run.artifacts.find((a:any)=>a.name===name).download_url)).json());
 const [trace,at,bt]=await Promise.all([artifact('vecu-trace.json'),artifact('baseline-hil-trace.json'),artifact('faulted-hil-trace.json')]);
 const measured=[trace.sides.baseline,trace.sides.faulted].map((rows:any[],side:number)=>{
  const degraded=rows.findIndex(r=>r.reply.state==='degraded'),stop=rows.findIndex((r,i)=>i>=degraded&&r.next_ego.speed_mps<.1);
  expect(degraded).toBeGreaterThan(0);expect(stop).toBeGreaterThan(degraded);
  const distance=rows.slice(degraded,stop+1).reduce((sum,r)=>sum+Math.hypot(r.next_ego.x-r.request.ego.x,r.next_ego.y-r.request.ego.y),0);
  const gt=(side?bt:at).rows,minGap=Math.min(...gt.map((r:any)=>{const lead=r.generated_objects.find((o:any)=>o.id==='lead');return Math.hypot(lead.x-r.request.ego.x,lead.y-r.request.ego.y);}));
  return {degraded,stop,distance,minGap};
 });
 expect(measured[1].distance).toBeGreaterThan(measured[0].distance);
 await page.goto(`/#hil-sim?project=${project}&mode=vecu&execution=${execution}&step=${measured[1].degraded}`);
 await expect(page.locator('.vecu-result-title>b')).toHaveText('completed');
 await expect(page.locator('.vecu-cameras canvas')).toHaveCount(2);for(const canvas of await page.locator('.vecu-cameras canvas').all())await expect(canvas).toBeVisible();
 await expect(page.locator('.vecu-cameras')).toContainText('저장 결과 동기 재생');await expect(page.locator('.vecu-cameras')).toContainText('A · 패키지 A');await expect(page.locator('.vecu-cameras')).toContainText('B · 패키지 B');
 const distanceRow=page.getByRole('row',{name:/degraded 이후 CARLA 이동거리/}),gapRow=page.getByRole('row',{name:/차량 원점 간 최소거리/});
 for(const [i,m] of measured.entries()){await expect(distanceRow.locator('td').nth(i)).toHaveText(`${m.distance.toFixed(3)} m`);await expect(gapRow.locator('td').nth(i)).toHaveText(`${m.minGap.toFixed(3)} m`);}
 await page.getByLabel('vECU event filter',{exact:true}).selectOption('stop');await page.locator('.vecu-event-list button.side-b').first().click();await expect(page.getByLabel('vECU time cursor')).toHaveValue(String(measured[1].stop));
 await expect(page.locator('.vecu-cameras')).toContainText(`차량 응답 관측 ${((measured[1].stop+1)*.05).toFixed(2)} s`);
 for(const canvas of await page.locator('.vecu-cameras canvas').all())await expect(canvas).toBeVisible();
 await page.getByLabel('vECU camera',{exact:true}).selectOption('front');for(const canvas of await page.locator('.vecu-cameras canvas').all())await expect(canvas).toBeVisible();
 await page.getByLabel('vECU camera',{exact:true}).selectOption('chase');
 await page.getByRole('button',{name:'영상 A/B 재생',exact:true}).click();await expect.poll(async()=>Number(await page.getByLabel('vECU time cursor').inputValue())).toBeGreaterThan(measured[1].stop);await page.getByRole('button',{name:'영상 일시정지',exact:true}).click();
 const cursor=await page.getByLabel('vECU time cursor').inputValue(),resume=page.url();await page.reload();await expect(page.getByLabel('vECU time cursor')).toHaveValue(cursor);await expect(page.locator('.vecu-cameras canvas')).toHaveCount(2);for(const canvas of await page.locator('.vecu-cameras canvas').all())await expect(canvas).toBeVisible();expect(page.url()).toBe(resume);
 const download=page.waitForEvent('download');await page.getByRole('button',{name:'실행 근거 내보내기',exact:true}).click();await(await download).saveAs('test-results/vecu-carla-package-evidence.json');expect(createHash('sha256').update(await readFile('test-results/vecu-carla-package-evidence.json')).digest('hex')).toBe(run.manifest_hash);
 await page.locator('.vecu-cameras').screenshot({path:'test-results/vecu-carla-package-cameras.png'});await page.locator('.vecu-events').screenshot({path:'test-results/vecu-carla-package-events.png'});await page.locator('.vecu-signal-graph').screenshot({path:'test-results/vecu-carla-package-graph.png'});
 await writeFile('test-results/vecu-carla-package-checks.json',JSON.stringify({execution,measured,stop_threshold_mps:.1,distance_scope:'sum request.ego → next_ego XY from first degraded through first stop',minimum_distance_scope:'concurrent generated_objects lead and request.ego origins; not stale packet or body clearance',manifest_sha256:run.manifest_hash,url:resume},null,2));
 await page.goto(`/#hil-sim?project=${project}&mode=vecu&execution=${process.env.AXIOM_VECU_CARLA_NORMAL??'job_3a280ff28bc14b0fb9a9482cc6eb34c0'}`);await expect(page.getByRole('row',{name:/degraded 이후 CARLA 이동거리/})).toContainText('주입 없음');expect(errors).toEqual([]);
});
