// Normal product workflow in a separately prepared local test runtime; no native engine run.
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
const root=path.resolve(process.argv[2]);
const prepared=JSON.parse(fs.readFileSync(path.join(root,'prepared.json'),'utf8'));
const base=prepared.target_api.replace(/\/api\/v1$/,'');assert.equal(new URL(base).hostname,'127.0.0.1');
let token=fs.readFileSync(path.join(root,'access/tester-token'),'utf8').trim();
if(process.argv.includes('--fresh-project')) {
 const operator=fs.readFileSync(path.join(root,'access/operator-token'),'utf8').trim();
 const headers={'Authorization':'Bearer '+operator,'Content-Type':'application/json'};
 const created=await fetch(base+'/api/v1/projects',{method:'POST',headers,body:JSON.stringify({name:'Log beta cold-start browser '+Date.now()})});assert(created.ok);
 prepared.project_id=(await created.json()).id;
 const grant=await fetch(base+'/api/v1/projects/'+prepared.project_id+'/tokens',{method:'POST',headers,body:JSON.stringify({user_label:'Log beta cold-start engineer',role:'Engineer'})});assert(grant.ok);
 token=(await grant.json()).token;
 fs.writeFileSync(path.join(root,'access/cold-start-token'),token,{mode:0o600});
}
const browser=await chromium.launch({headless:true,channel:'chrome'});
const page=await browser.newPage({viewport:{width:1600,height:1000}});
page.setDefaultTimeout(30000);
if(fs.existsSync(path.join(root,'browser.json')))fs.copyFileSync(path.join(root,'browser.json'),path.join(root,`browser-attempt-${Date.now()}.json`));
const report={project_id:prepared.project_id,scope:'authored MCAP through normal browser and real CPU reference adapter',checks:[],page_errors:[]};
page.on('pageerror',e=>report.page_errors.push(e.message));
await page.addInitScript(t=>sessionStorage.setItem('axiom.apiToken',t),token);
const record=name=>{report.checks.push(name);console.log(name);};
const waitPost=path=>page.waitForResponse(r=>new URL(r.url()).pathname.endsWith(path)&&r.request().method()==='POST');
try {
 await page.goto(base+'/#log-sim?project='+prepared.project_id);
 await page.getByLabel('Upload Log Sim MCAP').waitFor();
 const headers={Authorization:'Bearer '+token};
 for(const invalid of [true,false]){
  const response=await page.request.get(base+'/api/v1/log-sim/example.mcap?'+new URLSearchParams({project_id:prepared.project_id,invalid:String(invalid)}),{headers});assert(response.ok());
  const file=path.join(root,invalid?'invalid.mcap':'authored-browser.mcap');fs.writeFileSync(file,await response.body());
  const upload=waitPost('/log-sim/uploads');await page.getByLabel('Upload Log Sim MCAP').setInputFiles(file);assert.equal((await upload).status(),201);
  const inspect=waitPost('/log-sim/inputs/inspect');await page.getByRole('button',{name:'입력 점검',exact:true}).click();
  assert.equal((await inspect).status(),invalid?422:200);
  record(invalid?'missing objects rejected visibly':'owned upload and exact input inspection');
 }
 const registration=waitPost('/log-sim/inputs');await page.getByRole('button',{name:'분석 구간 등록',exact:true}).click();const source=(await registration).json();
 const registered=await source;
 await page.getByText('구간 등록 완료.',{exact:false}).waitFor();
 await page.getByLabel('Log Sim B time_headway_s').fill('4');
 await page.getByLabel('Log Sim settings name').fill('Browser beta custom settings');
 const settings=waitPost('/log-sim/comparison-settings');await page.getByRole('button',{name:'비교 설정 저장',exact:true}).click();assert.equal((await settings).status(),201);record('versioned custom A/B settings saved');
 while(await page.locator('button[aria-label^="Select Log Sim scene"][aria-pressed="true"]').count())await page.locator('button[aria-label^="Select Log Sim scene"][aria-pressed="true"]').first().click();
 await page.getByRole('button',{name:'Select Log Sim scene authored-browser.mcap',exact:true}).click();
 const preview=waitPost('/log-sim/batches/preview');await page.getByLabel('Preview Log Sim batch').click();const pv=await (await preview).json();assert.equal(pv.profiles.candidate.controller.time_headway_s,4);
 const submit=waitPost('/log-sim/batches');await page.getByLabel('Run Log Sim batch').click();const batch=await (await submit).json();report.batch_id=batch.id;
 await page.getByTestId('log-batch-candidate').first().waitFor({timeout:60000});record('actual reference A/B jobs completed and candidates displayed');
 await page.getByLabel('Group overlapping Log Sim candidates').check();
 await page.getByLabel('Group overlapping Log Sim candidates').uncheck();record('overlap grouping can be expanded');
 await page.getByRole('button',{name:/^Inspect Log Sim candidate /}).first().click();
 await page.getByLabel('Log Sim inspector play').click();
 await page.waitForFunction(()=>Number(document.querySelector('[aria-label="Log Sim candidate playback time"]')?.value)>.2);
 await page.getByLabel('Log Sim inspector pause').click();
 await page.screenshot({path:path.join(root,'03-ab-investigation.png'),fullPage:false});
 await page.getByLabel('Close Log Sim candidate').click();record('synchronized BEV and A/B requests play in candidate investigation');
 await page.getByRole('checkbox',{name:/^Select Log Sim candidate /}).first().check();
 const bundle=waitPost('/case-bundles');await page.getByLabel('Save Log Sim Case bundle').click();const saved=await (await bundle).json();assert.equal(saved.items.length,1);record('candidate saved as linked Case bundle');
 for(const format of ['JSON','CSV']){const download=page.waitForEvent('download');await page.getByRole('button',{name:format+' 내보내기',exact:true}).click();const d=await download;await d.saveAs(path.join(root,'browser-export.'+format.toLowerCase()));}
 record('JSON and CSV exports downloaded');await page.screenshot({path:path.join(root,'01-library.png'),fullPage:true});
 await page.getByTestId('log-batch-saved-bundle').first().locator('summary').click();
 await page.getByTestId('log-batch-saved-bundle').first().getByRole('button').first().click();
 await page.getByLabel('Log Sim review note').fill('Browser beta: investigate requested acceleration difference.');
 await page.getByLabel('Log Sim review tags').fill('beta, following');
 await page.getByLabel('Log Sim review status').selectOption('investigate');
 const review=waitPost('/log-sim/reviews');await page.getByRole('button',{name:'검토 기록 저장',exact:true}).click();const rv=await (await review).json();assert.equal(rv.revision,1);
 await page.reload();await page.getByLabel('Log Sim review note').waitFor();assert.equal(await page.getByLabel('Log Sim review note').inputValue(),rv.note);record('Case review persists after reload');
 await page.screenshot({path:path.join(root,'02-case-review.png'),fullPage:true});
 await page.goto(base+'/#log-sim?'+new URLSearchParams({project:prepared.project_id,source:registered.id,case:saved.items[0].case_id,time_s:'1.0'}));await page.reload();
 await page.getByLabel('Log Sim playback time').waitFor();assert(Math.abs(Number(await page.getByLabel('Log Sim playback time').inputValue())-1)<.01);record('shared source Case and moment restored');
 await page.getByRole('link',{name:'이 Case를 만든 A/B 결과 열기'}).click();
 await page.getByRole('dialog',{name:'Log Sim candidate investigation'}).waitFor();record('Case returns to its exact retained batch candidate');
 assert.deepEqual(report.page_errors,[]);report.passed=true;
} catch(e){report.error=String(e);await page.screenshot({path:path.join(root,'failure.png'),fullPage:true});throw e;}
finally{fs.writeFileSync(path.join(root,'browser.json'),JSON.stringify(report,null,2));await browser.close();}
