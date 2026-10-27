// Prepared local participant projects only. Executes CPU reference replay through the normal UI.
import {chromium} from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
const root=path.resolve(process.argv[2]);
const prepared=JSON.parse(fs.readFileSync(path.join(root,'participants.json'),'utf8'));
assert(prepared.ready);const participant=prepared.participants[0];assert(participant.ready);
const base=prepared.target_api.replace(/\/api\/v1$/,'');assert.equal(new URL(base).hostname,'127.0.0.1');
const token=fs.readFileSync(path.join(root,participant.token_file),'utf8').trim();
const browser=await chromium.launch({headless:true,channel:'chrome'});
const context=await browser.newContext({viewport:{width:1600,height:1000}});
await context.addInitScript(t=>sessionStorage.setItem('axiom.apiToken',t),token);
const page=await context.newPage();page.setDefaultTimeout(30000);
const file=path.join(root,'guided-browser.json');if(fs.existsSync(file))fs.copyFileSync(file,path.join(root,`guided-browser-attempt-${Date.now()}.json`));
const report={schema_version:'log-guided-browser/1',project_id:participant.project_id,scope:'retained native CARLA object input, new CPU reference A/B only',checks:[],page_errors:[]};
page.on('pageerror',e=>report.page_errors.push(e.message));
const persist=()=>fs.writeFileSync(file,JSON.stringify(report,null,2)+'\n');
const record=(name,detail={})=>{report.checks.push({name,...detail});persist();console.log(name);};
const post=suffix=>page.waitForResponse(r=>new URL(r.url()).pathname.endsWith(suffix)&&r.request().method()==='POST');
async function api(route,credentials=token){const r=await page.request.get(base+'/api/v1'+route,{headers:{Authorization:'Bearer '+credentials}});assert(r.ok(),route+': '+r.status());return r.json();}
async function taskUntil(predicate){for(let i=0;i<80;i++){const task=await api(`/log-sim/beta-tasks/${report.task_id}?project_id=${participant.project_id}`);if(predicate(task))return task;await page.waitForTimeout(250);}throw new Error('Expected task state did not settle');}
try {
 await page.goto(base+'/#log-sim?project='+participant.project_id);
 await page.getByLabel('Start Log Sim beta pack following-gap').click();await page.waitForFunction(()=>new URLSearchParams(location.hash.split('?')[1]??'').get('task'));report.task_id=new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('task');const task=await api(`/log-sim/beta-tasks/${report.task_id}?project_id=${participant.project_id}`);persist();
 assert.equal(task.pack_id,'following-gap');assert.equal(task.batch_id,null);record('recommended pack start selects source without execution');
 await page.getByLabel('Log Sim beta blocked', {exact:true}).click();
 await page.getByLabel('Log Sim beta feedback message').fill('Automated rehearsal: test feedback recovery before saving settings.');
 const blocked=post('/log-sim/beta-feedback');await page.getByLabel('Send Log Sim beta feedback').click();assert.equal((await blocked).status(),201);record('blocked-stage feedback saved with task context');
 await page.getByLabel('Log Sim B time_headway_s').fill('4');await page.getByLabel('Log Sim settings name').fill('Guided beta following gap');
 const settings=post('/log-sim/comparison-settings');await page.getByRole('button',{name:'비교 설정 저장',exact:true}).click();assert.equal((await settings).status(),201);
 await taskUntil(t=>t.comparison_ref);await page.reload();
 await page.waitForFunction(()=>document.querySelector('[aria-label="Log Sim B time_headway_s"]')?.value==='4');assert.equal(await page.getByLabel('Log Sim B time_headway_s').inputValue(),'4');record('exact saved settings and task resume after reload');
 const preview=post('/log-sim/batches/preview');await page.getByLabel('Preview Log Sim batch').click();const pv=await (await preview).json();assert.equal(pv.counts.supported_sources,1);assert.equal(pv.profiles.candidate.controller.time_headway_s,4);
 const submit=post('/log-sim/batches');await page.getByLabel('Run Log Sim batch').click();const batch=await (await submit).json();report.batch_id=batch.id;persist();
 await taskUntil(t=>t.batch_id===batch.id);await page.reload();
 await page.getByTestId('log-batch-candidate').first().waitFor({timeout:60000});record('one source real A/B execution restores after reload');
 await page.getByRole('button',{name:/^Inspect Log Sim candidate /}).first().click();
 await page.getByLabel('Log Sim inspector play').click();await page.waitForTimeout(400);await page.getByLabel('Log Sim inspector pause').click();
 await page.screenshot({path:path.join(root,'guided-investigation.png')});await page.getByLabel('Close Log Sim candidate').click();
 await taskUntil(t=>t.result_opened);record('investigation links original BEV and exact retained requests');
 await page.getByRole('checkbox',{name:/^Select Log Sim candidate /}).first().check();
 const bundle=post('/case-bundles');await page.getByLabel('Save Log Sim Case bundle').click();const saved=await (await bundle).json();report.case_id=saved.items[0].case_id;
 await taskUntil(t=>t.case_id===report.case_id);
 await page.getByTestId('log-batch-saved-bundle').first().locator('summary').click();await page.getByTestId('log-batch-saved-bundle').first().getByRole('button').first().click();
 await page.getByLabel('Log Sim review note').fill('Retained road-object trajectory: headway change alters control requests. Investigated in beta rehearsal.');
 await page.getByLabel('Log Sim review tags').fill('beta, following');await page.getByLabel('Log Sim review status').selectOption('reviewed');
 const review=post('/log-sim/reviews');await page.getByRole('button',{name:'검토 기록 저장',exact:true}).click();assert.equal((await review).status(),201);
 const finished=await taskUntil(t=>t.status==='completed');assert.equal(finished.stage,'completed');record('completion derived from executed batch and reviewed Case');
 await page.reload();await page.getByLabel('Log Sim beta task progress').waitFor();record('source Case and task completion restore');
 await page.getByLabel('Log Sim beta completion feedback').click();await page.getByLabel('Log Sim beta feedback message').fill('Automated rehearsal completed with scripted assistance; not actual human beta acceptance.');
 await page.getByLabel('Log Sim beta feedback needs help').selectOption('yes');
 const feedback=post('/log-sim/beta-feedback');await page.getByLabel('Send Log Sim beta feedback').click();const response=await feedback;assert.equal(response.status(),201);assert.equal((await response.json()).needs_help,true);record('completion assistance explicitly recorded');
 await page.screenshot({path:path.join(root,'guided-completed.png'),fullPage:true});
 const other=prepared.participants[1];const foreign=await page.request.get(base+`/api/v1/log-sim/beta-tasks/${report.task_id}?project_id=${participant.project_id}`,{headers:{Authorization:'Bearer '+fs.readFileSync(path.join(root,other.token_file),'utf8').trim()}});assert.equal(foreign.status(),404);
 const denied=await page.request.get(base+'/api/v1/log-sim/beta-overview',{headers:{Authorization:'Bearer '+token}});assert.equal(denied.status(),403);record('participant project isolation and operator-only summary');
 const operator=fs.readFileSync(path.join(root,'access/operator-token'),'utf8').trim();
 const operatorContext=await browser.newContext({viewport:{width:1600,height:1000}});await operatorContext.addInitScript(t=>sessionStorage.setItem('axiom.apiToken',t),operator);
 const operatorPage=await operatorContext.newPage();operatorPage.on('pageerror',e=>report.page_errors.push(e.message));await operatorPage.goto(base+'/#log-sim?project='+participant.project_id);
 const overview=operatorPage.getByLabel('Log Sim beta overview');await overview.locator('summary').click();await overview.getByText('Automated rehearsal completed with scripted assistance; not actual human beta acceptance.',{exact:false}).waitFor();
 for(const format of ['JSON','CSV']){const download=operatorPage.waitForEvent('download');await overview.getByRole('button',{name:format+' 내보내기',exact:true}).click();await (await download).saveAs(path.join(root,'guided-overview.'+format.toLowerCase()));}
 await operatorPage.screenshot({path:path.join(root,'guided-overview.png'),fullPage:true});record('operator participant overview and authenticated JSON CSV downloads');await operatorContext.close();
 assert.deepEqual(report.page_errors,[]);report.passed=true;
} catch(error){report.error=String(error);await page.screenshot({path:path.join(root,'guided-failure.png'),fullPage:true});throw error;}
finally{persist();await browser.close();}
