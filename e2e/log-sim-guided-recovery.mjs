// No-candidate/manual Case, missing-auth guidance and ambiguous-response recovery in isolated beta.
import {chromium} from 'playwright';import fs from 'node:fs';import path from 'node:path';import assert from 'node:assert/strict';
const root=path.resolve(process.argv[2]), prepared=JSON.parse(fs.readFileSync(path.join(root,'participants.json'),'utf8'));
const person=prepared.participants[1],base=prepared.target_api.replace(/\/api\/v1$/,'');assert.equal(new URL(base).hostname,'127.0.0.1');
const token=fs.readFileSync(path.join(root,person.token_file),'utf8').trim();
const browser=await chromium.launch({headless:true,channel:'chrome'});const page=await browser.newPage({viewport:{width:1600,height:1000}});page.setDefaultTimeout(30000);
await page.addInitScript(t=>sessionStorage.setItem('axiom.apiToken',t),token);
const output=path.join(root,'guided-recovery.json');if(fs.existsSync(output))fs.copyFileSync(output,path.join(root,`recovery-attempt-${Date.now()}.json`));
const report={schema_version:'log-beta-recovery-browser/1',project_id:person.project_id,scope:'authored no-object MCAP plus actual CPU reference replay; intentional browser response loss',checks:[],page_errors:[]};
page.on('pageerror',e=>report.page_errors.push(e.message));const persist=()=>fs.writeFileSync(output,JSON.stringify(report,null,2));const record=name=>{report.checks.push(name);persist();console.log(name);};
const post=suffix=>page.waitForResponse(r=>new URL(r.url()).pathname.endsWith(suffix)&&r.request().method()==='POST');
const api=async route=>{const response=await page.request.get(base+'/api/v1'+route,{headers:{Authorization:'Bearer '+token}});assert(response.ok(),route);return response.json();};
async function taskUntil(predicate){for(let i=0;i<80;i++){const t=await api(`/log-sim/beta-tasks/${report.task_id}?project_id=${person.project_id}`);if(predicate(t))return t;await page.waitForTimeout(250);}throw new Error('Task did not reach expected stage');}
try {
 const unauth=await browser.newPage();await unauth.goto(base+'/#log-sim?project='+person.project_id);await unauth.getByRole('button',{name:/Workspace 설정/}).click();await unauth.getByLabel('Bearer token',{exact:false}).waitFor();await unauth.close();record('missing credentials offer working Workspace settings action');
 await page.goto(base+'/#log-sim?project='+person.project_id);await page.getByLabel('Start Log Sim beta pack input-errors').click();await page.waitForFunction(()=>new URLSearchParams(location.hash.split('?')[1]??'').get('task'));
 report.task_id=new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('task');persist();
 for(const [file,status] of [['authored-missing-objects.mcap',422],['authored-no-candidates.mcap',200]]){
  const uploaded=post('/log-sim/uploads');await page.getByLabel('Upload Log Sim MCAP').setInputFiles(path.join(root,file));assert.equal((await uploaded).status(),201);
  const checked=post('/log-sim/inputs/inspect');await page.getByRole('button',{name:'입력 점검',exact:true}).click();assert.equal((await checked).status(),status);
 }
 const registered=post('/log-sim/inputs');await page.getByRole('button',{name:'분석 구간 등록',exact:true}).click();const source=await (await registered).json();await taskUntil(t=>t.source_id===source.id);record('input exercise rejects missing objects then registers supported source');
 const setting=post('/log-sim/comparison-settings');await page.getByRole('button',{name:'비교 설정 저장',exact:true}).click();assert.equal((await setting).status(),201);await taskUntil(t=>t.comparison_ref);
 const preview=post('/log-sim/batches/preview');await page.getByLabel('Preview Log Sim batch').click();assert.equal((await preview).status(),200);
 let dispatched=null;
 await page.route('**/api/v1/log-sim/batches',async route=>{if(route.request().method()!=='POST')return route.continue();const response=await route.fetch();assert.equal(response.status(),202);dispatched=await response.json();report.batch_id=dispatched.id;persist();await route.abort('failed');});
 await page.getByLabel('Run Log Sim batch').click();await page.waitForFunction(()=>document.body.textContent.includes('fetch')||document.body.textContent.includes('network')||document.body.textContent.includes('Failed'));
 assert(dispatched);await page.unroute('**/api/v1/log-sim/batches');await page.reload();await page.getByLabel('Recover Log Sim beta batch').click();
 await taskUntil(t=>t.batch_id===dispatched.id);const batches=await api('/log-sim/batches?project_id='+person.project_id);assert.equal(batches.items.length,1);record('lost submit response recovers same batch after reload without duplicate execution');
 await page.getByLabel('Log Sim beta no candidates Case').waitFor({timeout:60000});const batch=await api('/log-sim/batches/'+dispatched.id+'?project_id='+person.project_id);assert.equal(batch.candidates.length,0);
 await page.getByLabel('Log Sim beta no candidates Case').click();await page.getByRole('button',{name:'Case 저장',exact:true}).waitFor();
 const caseSaved=post('/cases');await page.getByRole('button',{name:'Case 저장',exact:true}).click();const saved=await (await caseSaved).json();report.case_id=saved.id;await taskUntil(t=>t.case_id===saved.id);record('zero candidates permits manual original-interval Case');
 await page.getByLabel('Log Sim review note').fill('Authored fixture: no objects and identical cruise requests. No candidate is not a safety pass.');await page.getByLabel('Log Sim review status').selectOption('reviewed');
 const review=post('/log-sim/reviews');await page.getByRole('button',{name:'검토 기록 저장',exact:true}).click();assert.equal((await review).status(),201);await taskUntil(t=>t.status==='completed');await page.reload();await page.getByLabel('Log Sim beta completion feedback').waitFor();record('manual Case review completes and restores guided task');
 await page.screenshot({path:path.join(root,'guided-no-candidates.png'),fullPage:true});assert.deepEqual(report.page_errors,[]);report.passed=true;
}catch(e){report.error=String(e);await page.screenshot({path:path.join(root,'recovery-failure.png'),fullPage:true});throw e;}finally{persist();await browser.close();}
