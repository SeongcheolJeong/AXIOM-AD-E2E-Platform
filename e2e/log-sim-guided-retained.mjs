// Read-only checks on saved beta results; no new simulation or Case creation.
import {chromium} from 'playwright';import fs from 'node:fs';import path from 'node:path';import assert from 'node:assert/strict';
const root=path.resolve(process.argv[2]),setup=JSON.parse(fs.readFileSync(path.join(root,'participants.json'),'utf8')),receipt=JSON.parse(fs.readFileSync(path.join(root,'guided-browser.json'),'utf8'));
const person=setup.participants.find(p=>p.project_id===receipt.project_id),base=setup.target_api.replace(/\/api\/v1$/,'');assert.equal(new URL(base).hostname,'127.0.0.1');
const token=fs.readFileSync(path.join(root,person.token_file),'utf8').trim();const browser=await chromium.launch({headless:true,channel:'chrome'});const page=await browser.newPage({viewport:{width:1600,height:1000}});await page.addInitScript(({token,origin})=>{if(location.origin===origin)sessionStorage.setItem('axiom.apiToken',token);},{token,origin:base});
const report={checks:[],page_errors:[]};page.on('pageerror',e=>report.page_errors.push(e.message));const record=x=>{report.checks.push(x);console.log(x);};
try{
 const r=await page.request.get(base+'/api/v1/log-sim/batches/'+receipt.batch_id+'?project_id='+person.project_id,{headers:{Authorization:'Bearer '+token}});assert(r.ok());const batch=await r.json(),candidate=batch.candidates[0];
 const candidateUrl=base+'/#log-sim?'+new URLSearchParams({project:person.project_id,task:receipt.task_id,batch:batch.id,candidate:candidate.id});
 await page.goto(candidateUrl);await page.getByRole('dialog',{name:'Log Sim candidate investigation'}).waitFor();await page.getByLabel('Log Sim inspector play').waitFor();await page.reload();await page.getByRole('dialog',{name:'Log Sim candidate investigation'}).waitFor();await page.getByLabel('Log Sim inspector play').waitFor();record('exact candidate task and batch deeplink survives reload');await page.getByLabel('Close Log Sim candidate').click();
 await page.waitForFunction(()=>document.querySelector('[aria-label="Log Sim B time_headway_s"]')?.value==='4');assert(await page.getByLabel('Log Sim B time_headway_s').isDisabled());assert(await page.getByLabel('Run Log Sim batch').count()===0||await page.getByLabel('Run Log Sim batch').isDisabled());record('retained task exact settings locked against accidental new execution');
 // Hold the real task response so a user can dismiss the candidate before task restoration.
 let releaseTask;
 const gate=new Promise(resolve=>{releaseTask=resolve;});
 const taskPattern='**/api/v1/log-sim/beta-tasks/'+receipt.task_id+'?**';
 await page.route(taskPattern,async route=>{const response=await route.fetch();await gate;await route.fulfill({response});});
 try{
  await page.goto('about:blank');await Promise.all([page.waitForRequest(taskPattern),page.goto(candidateUrl)]);await page.getByLabel('Log Sim inspector play').waitFor();await page.getByLabel('Close Log Sim candidate').click();assert.equal(await page.getByRole('dialog',{name:'Log Sim candidate investigation'}).count(),0);
  const restoredBatch=page.waitForResponse(response=>response.url().includes('/log-sim/batches/'+batch.id+'?'));
  releaseTask();await restoredBatch;await page.waitForFunction(()=>document.querySelector('[aria-label="Log Sim B time_headway_s"]')?.value==='4');await page.getByLabel('Log Sim ranked candidates').waitFor();
  assert.equal(await page.getByRole('dialog',{name:'Log Sim candidate investigation'}).count(),0);assert.equal(new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('candidate'),null);record('candidate dismissal survives delayed task restoration');
 }finally{releaseTask();await page.unroute(taskPattern);}
 await page.screenshot({path:path.join(root,'guided-final-library.png'),fullPage:true});
 const unauth=await browser.newPage();await unauth.goto(base+'/#log-sim?project='+person.project_id);await unauth.getByRole('button',{name:/Workspace 설정/}).click();await unauth.getByLabel('Bearer token',{exact:false}).waitFor();assert.equal(await unauth.getByLabel('Log Sim beta task progress').count(),0);await unauth.close();record('missing token shows one login path without mounting protected beta UI');
 assert.deepEqual(report.page_errors,[]);report.passed=true;
}finally{fs.writeFileSync(path.join(root,'guided-retained.json'),JSON.stringify(report,null,2));await browser.close();}
