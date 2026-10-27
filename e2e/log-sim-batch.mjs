// Actual local road library -> normal queued reference A/B -> original investigation -> saved Cases.
// node e2e/log-sim-batch.mjs RUNTIME OUTPUT
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';

assert(process.argv[2] && process.argv[3], 'Pass isolated runtime and evidence directory');
const runtime=path.resolve(process.argv[2]), output=path.resolve(process.argv[3]);
const prepared=JSON.parse(fs.readFileSync(path.join(runtime,'prepared.json'),'utf8'));
const base=prepared.target_api.replace(/\/api\/v1$/,'');
assert.equal(new URL(base).hostname,'127.0.0.1');
const token=fs.readFileSync(path.join(runtime,'access/presenter-token'),'utf8').trim();
fs.mkdirSync(output,{recursive:true});
const receipt=path.join(output,'browser.json');
const report=fs.existsSync(receipt)?JSON.parse(fs.readFileSync(receipt,'utf8')):{project_id:prepared.project_id,keys:{},checks:[],page_errors:[],started_at:new Date().toISOString()};
if(report.error){report.recovered_attempt_errors??=[];report.recovered_attempt_errors.push(report.error);delete report.error;}
const persist=()=>fs.writeFileSync(receipt,JSON.stringify(report,null,2)+'\n');
const checked=name=>{if(!report.checks.includes(name))report.checks.push(name);persist();console.log(name);};
const browser=await chromium.launch({headless:true,channel:'chrome'});
const page=await browser.newPage({viewport:{width:1600,height:1050}});
page.setDefaultTimeout(30000);
page.on('pageerror',error=>{report.page_errors.push(error.message);persist();});
await page.addInitScript(value=>sessionStorage.setItem('axiom.apiToken',value),token);
const api=async url=>{const r=await page.request.get(base+'/api/v1'+url,{headers:{Authorization:'Bearer '+token}});assert(r.ok(),`${url}: ${r.status()}`);return r.json();};
const posted=suffix=>page.waitForResponse(r=>new URL(r.url()).pathname.endsWith(suffix)&&r.request().method()==='POST',{timeout:180000});
const shot=name=>page.screenshot({path:path.join(output,name+'.png'),fullPage:false});
await page.route('**/api/v1/log-sim/**',async route=>{
  const request=route.request(),headers=request.headers();
  if(request.method()==='POST'&&headers['idempotency-key']){
    const id=new URL(request.url()).pathname+'/'+request.postData();
    report.keys[id]??=headers['idempotency-key'];persist();
    await route.continue({headers:{...headers,'idempotency-key':report.keys[id]}});
  }else await route.continue();
});
try{
  const library=await api('/log-sim/library?project_id='+report.project_id);
  report.library=library;assert.equal(library.counts.scene_count,10);assert.equal(library.counts.original_frame_count,404);
  assert.equal(library.counts.available_frame_count+library.counts.excluded_frame_count,404);
  let url=base+'/#log-sim?'+new URLSearchParams({project:report.project_id,...(report.batch?{batch:report.batch.id}:{})});
  await page.goto(url);await page.getByTestId('log-batch-library').waitFor();
  await page.getByTestId('log-library-scene').nth(9).waitFor();
  assert.equal(await page.getByTestId('log-library-scene').count(),10);
  await page.locator('.log-batch-scene-image img').last().waitFor();
  await shot('01-real-log-library');checked('ten_original_scenes_and_explicit_excluded_frame_denominator');
  if(!report.batch){
    const previewing=posted('/batches/preview');await page.getByRole('button',{name:'Preview Log Sim batch',exact:true}).click();
    const response=await previewing;assert(response.ok(),await response.text());report.preview=await response.json();persist();
    assert.equal(report.preview.counts.distinct_scenes,10);assert.equal(report.preview.counts.unsupported_sources,0);
    assert.equal(report.preview.planned_execution_count,library.counts.segment_count*2);
    const submitting=posted('/batches');await page.getByRole('button',{name:'Run Log Sim batch',exact:true}).click();
    const submitted=await submitting;assert(submitted.ok(),await submitted.text());report.batch=await submitted.json();persist();
  }
  await page.waitForFunction(()=>{const e=document.querySelector('[aria-label="Log Sim actual batch progress"]');return e&&e.querySelector('h3')?.textContent.startsWith('완료');},null,{timeout:180000});
  report.batch=await api(`/log-sim/batches/${report.batch.id}?project_id=${report.project_id}`);persist();
  assert.equal(report.batch.status,'completed');assert.equal(report.batch.counts.completed_sources,library.counts.segment_count);
  const jobs=report.batch.sources.flatMap(source=>Object.values(source.execution_ids));assert.equal(new Set(jobs).size,library.counts.segment_count*2);
  assert(report.batch.candidates.length>0);assert.equal(report.batch.learned_model,false);
  checked('all_original_intervals_completed_two_real_queued_reference_executions_each');
  await page.getByTestId('log-batch-board').scrollIntoViewIfNeeded();await shot('02-ranked-investigation-candidates');
  const opposite=report.batch.candidates.find(candidate=>candidate.metrics.opposite_acceleration_count>0);
  assert(opposite,'Actual input should expose an opposite-sign request candidate');
  await page.getByRole('combobox',{name:'Log Sim candidate sign filter',exact:true}).selectOption('opposite');
  await page.getByRole('button',{name:'Inspect Log Sim candidate '+opposite.id,exact:true}).click();
  await page.getByRole('dialog',{name:'Log Sim candidate investigation',exact:true}).waitFor();
  await page.getByRole('button',{name:'Log Sim inspector play',exact:true}).waitFor();
  await page.getByText('이 표본에서 가속과 감속 요청이 반대입니다.',{exact:true}).waitFor();
  await page.waitForFunction(()=>document.querySelector('.log-batch-original img')?.naturalWidth>0);
  await shot('03-original-and-opposite-requests');
  report.inspected_candidate=opposite;report.inspector_url=page.url();persist();
  const before=Number(await page.getByRole('slider',{name:'Log Sim candidate playback time',exact:true}).inputValue());
  await page.getByRole('button',{name:'Log Sim inspector play',exact:true}).click();
  await page.waitForFunction(time=>Number(document.querySelector('[aria-label="Log Sim candidate playback time"]')?.value)!==time,before);
  const pause=page.getByRole('button',{name:'Log Sim inspector pause',exact:true});if(await pause.count())await pause.click();
  checked('actual_original_rgb_bev_and_opposite_control_requests_share_playback_time');
  await page.reload();await page.getByRole('dialog',{name:'Log Sim candidate investigation',exact:true}).waitFor();
  await page.getByText('이 표본에서 가속과 감속 요청이 반대입니다.',{exact:true}).waitFor();
  checked('candidate_deep_link_restores_original_and_pinned_requests');
  await page.getByRole('button',{name:'Close Log Sim candidate',exact:true}).click();
  await page.getByRole('combobox',{name:'Log Sim candidate sign filter',exact:true}).selectOption('');
  const selected=[...new Map([opposite,...report.batch.candidates.slice(0,3)].map(x=>[x.id,x])).values()].slice(0,3);
  for(const candidate of selected) await page.getByRole('checkbox',{name:'Select Log Sim candidate '+candidate.id,exact:true}).check();
  await page.getByRole('textbox',{name:'Log Sim Case bundle name',exact:true}).fill('실도로 10개 장면 · A/B 차이 조사');
  const saving=posted('/case-bundles');await page.getByRole('button',{name:'Save Log Sim Case bundle',exact:true}).click();
  const saved=await saving;assert(saved.ok(),await saved.text());report.bundle=await saved.json();persist();
  assert.equal(report.bundle.items.length,3);assert.equal(report.bundle.verdict,'not_evaluated');
  await page.getByTestId('log-batch-saved-bundle').first().scrollIntoViewIfNeeded();await shot('04-saved-case-bundle');
  await page.reload();await page.getByTestId('log-batch-saved-bundle').first().waitFor();
  const list=await api('/log-sim/case-bundles?project_id='+report.project_id);assert(list.items.some(item=>item.id===report.bundle.id));
  const bundle=page.getByTestId('log-batch-saved-bundle').filter({hasText:report.bundle.name}).first();
  await bundle.locator('summary').click();await bundle.getByRole('button').first().click();
  await page.getByRole('region',{name:'Log Sim automatic event search',exact:true}).waitFor();
  assert(page.url().includes('case='));checked('explicit_selected_cases_saved_restored_and_open_in_existing_source_workflow');
  report.case_url=page.url();report.url=base+'/#log-sim?'+new URLSearchParams({project:report.project_id,batch:report.batch.id});
  assert.deepEqual(report.page_errors,[]);report.complete=true;delete report.error;
}catch(error){report.complete=false;report.error=String(error);await shot('failure').catch(()=>{});throw error;}
finally{report.finished_at=new Date().toISOString();persist();await browser.close();}
