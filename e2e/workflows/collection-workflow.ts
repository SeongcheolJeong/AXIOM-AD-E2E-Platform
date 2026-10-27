import { expect, type Page, type TestInfo } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { createHash, randomUUID } from "node:crypto";
import path from "node:path";

export async function collectionWorkflow(page: Page, info: TestInfo, source: string, python: string, apiUrl: string, token = "") {
  info.setTimeout(180_000);
  const errors: string[] = []; page.on("pageerror", e => errors.push(e.message));
  const headers = token ? { Authorization: `Bearer ${token}` } : {};
  const work = info.outputPath("actual-collection-data");
  const env = { ...process.env, PYTHONDONTWRITEBYTECODE: "1", ADE2E_API_TOKEN: token,
    PYTHONPATH: ["backend/src", "sdk/python/src", "integrations/onnx/src", "integrations/coco/src", "examples"].map(p => path.join(source,p)).join(path.delimiter) };
  execFileSync(python, ["-B", path.join(source,"examples/dataset_evaluation_workflow.py"), "--url", apiUrl, "--output", work],
    { cwd:source,env,timeout:100_000,maxBuffer:2*1024*1024 });
  // A separate, authored one-frame recording; its equal PNG is not an independent-scene claim.
  const extra = JSON.parse(execFileSync(python, ["-B", "-c", `
from pathlib import Path
import json,os,sys
from generate_perception_fixture import make_recording
from axiom_sdk import AxiomClient
p=Path(sys.argv[2]);channel=make_recording(p,frames=1)
with AxiomClient(sys.argv[1],token=os.environ.get('ADE2E_API_TOKEN')) as api:
 r=api.import_recording(p,name='Collection normal-source example',source_kind='synthetic',source='Independent authored fixture container; not measured data')
 a=api.capture_dataset_image(r['id'],channel,0,decoder_id='foxglove-json',decoder_version='1')
 print(json.dumps(a))
`, apiUrl, path.join(work,"extra-recording.mcap")], { cwd:source,env,timeout:30_000,maxBuffer:1024*1024 }).toString());
  const fixture = JSON.parse(readFileSync(path.join(work,"summary.json"),"utf8"));
  const get = async (url: string) => { const r=await page.request.get(url,{headers});expect(r.status()).toBe(200);return r.json(); };
  const baseline = await get(`/api/v1/detection-evaluations/${fixture.evaluation_ids[1]}`);
  const originalDataset = await get(`/api/v1/datasets/${fixture.dataset_id}?revision=2`);
  const originalBytes = await (await page.request.get(originalDataset.download_url,{headers})).body();
  const cases=[];
  for(const n of [1,2]) {
    const result=await page.request.post('/api/v1/cases',{headers:{...headers,'Idempotency-Key':randomUUID()},data:{
      title:`Collection investigation ${n}`,note:'Authored-source curation; no independent road observation.',
      source:{kind:'evaluation',evaluation_id:baseline.id,image_id:baseline.report.rows[0].image_id}}});
    expect(result.status()).toBe(201);cases.push(await result.json());
  }
  await page.goto(`/#cases?case=${cases[0].id}`);
  await page.getByRole('link',{name:'Start collection from this case',exact:true}).click();
  await page.getByLabel('Collection name',{exact:true}).fill('Mixed-source regression collection');
  await page.getByLabel('Collection purpose',{exact:true}).fill('Combine investigations, normal and reviewed-negative images; not a representative road benchmark.');
  await page.getByLabel('Source 1 reason',{exact:true}).fill('First investigation');
  for(const [index,kind,id,reason] of [
    [2,'case',cases[1].id,'Second investigation of the same original'],
    [3,'dataset',fixture.dataset_id,'Retain all six images, including normal and negative images'],
    [4,'image',extra.id,'Separate recording with a captured, not-yet-reviewed normal image']
  ] as const) {
    await page.getByRole('button',{name:'Add collection source',exact:true}).click();
    await page.getByLabel(`Source ${index} type`,{exact:true}).selectOption(kind);
    await page.getByLabel(`Source ${index} ID`,{exact:true}).fill(id);
    if(kind==='dataset')await page.getByLabel(`Source ${index} revision`,{exact:true}).fill('2');
    await page.getByLabel(`Source ${index} reason`,{exact:true}).fill(reason);
  }
  await page.getByRole('button',{name:'Preview complete collection',exact:true}).click();
  await expect(page.getByTestId('collection-unique')).toHaveText('7');
  await expect(page.getByTestId('collection-overlap')).toHaveText('2');
  await expect(page.getByTestId('collection-recordings')).toHaveText('2');
  await expect(page.getByTestId('collection-messages').locator('tbody tr')).toHaveCount(7);
  await page.getByRole('button',{name:'Save collection snapshot',exact:true}).click();
  await expect(page.getByTestId('collection-revision')).toHaveText('r1');
  const collectionId=(await page.getByTestId('collection-id').textContent())!;
  const stored=await get('/api/v1/collections/'+collectionId);
  const collectionBytes=await (await page.request.get(stored.download_url,{headers})).body();
  await page.reload();await expect(page.getByTestId('collection-messages').locator('tbody tr')).toHaveCount(7);
  await page.getByRole('button',{name:'Prepare dataset from collection',exact:true}).click();
  await page.getByLabel('Collection dataset name',{exact:true}).fill('Frozen mixed-source subset');
  await page.getByLabel('Collection dataset note',{exact:true}).fill('Retain explicit original labels and every exclusion reason.');
  await page.getByRole('button',{name:'Use unambiguous dataset labels',exact:true}).click();
  await page.getByRole('button',{name:'Apply split to included images',exact:true}).click();
  await page.getByRole('button',{name:'Preview frozen dataset',exact:true}).click();
  await expect(page.getByTestId('collection-dataset-preview')).toContainText('6 reviewed · 1 unreviewed · 2 reviewed negatives');
  await page.getByLabel('Include source 7',{exact:true}).uncheck();
  await expect(page.getByRole('button',{name:'Publish frozen dataset',exact:true})).toHaveCount(0);
  await page.getByLabel('Source 7 decision note',{exact:true}).fill('Explicit duplicate-image exclusion for this subset; retained in the parent collection.');
  await page.getByRole('button',{name:'Preview frozen dataset',exact:true}).click();
  await expect(page.getByTestId('collection-dataset-preview')).toContainText('6 included + 1 excluded = 7 original messages');
  let dropped=false, publicationId='';
  await page.route(`**/api/v1/collections/${collectionId}/datasets`,async route=>{
    if(route.request().method()==='POST' && !dropped) {
      dropped=true;const r=await route.fetch();expect(r.status()).toBe(201);publicationId=(await r.json()).id;await route.abort('failed');
    } else await route.continue();
  });
  await page.getByRole('button',{name:'Publish frozen dataset',exact:true}).click();
  await expect(page.getByRole('button',{name:'Publish frozen dataset',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'Publish frozen dataset',exact:true}).click();
  await page.unroute(`**/api/v1/collections/${collectionId}/datasets`);
  await expect(page.getByTestId('collection-published')).toBeVisible();
  const receipts=(await get(`/api/v1/collection-datasets?collection_id=${collectionId}`)).items;
  expect(receipts).toHaveLength(1);const receipt=receipts[0];expect(receipt.id).toBe(publicationId);
  expect(receipt.proposal.denominator).toEqual({collection_messages:7,included:6,excluded:1});
  const downloading=page.waitForEvent('download');
  await page.getByRole('link',{name:'Download curation receipt JSON',exact:true}).click();
  const receiptBytes=readFileSync((await (await downloading).path())!);
  expect(createHash('sha256').update(receiptBytes).digest('hex')).toBe(receipt.manifest_hash);
  await page.getByRole('link',{name:'Open newly published dataset',exact:true}).click();
  await expect(page.getByTestId('dataset-detail')).toContainText('Frozen mixed-source subset');
  await expect(page.getByTestId('dataset-collection-lineage')).toContainText('6 included / 7 originally selected');
  const ds=await get(`/api/v1/datasets/${receipt.dataset.id}?revision=1`);
  expect(ds.stats.images).toBe(6);expect(ds.stats.negative_images).toBe(2);expect(ds.stats.unreviewed).toBe(0);
  const result=await page.request.post('/api/v1/detection-evaluations',{headers:{...headers,'Idempotency-Key':randomUUID()},
    data:{...baseline.request,dataset_id:ds.id,dataset_revision:1,name:'Evaluate frozen collection subset',note:'Actual COCO on the same authored inputs; no road accuracy claim.'}});
  expect(result.status()).toBe(202);const evaluationId=(await result.json()).id;
  await expect.poll(async()=> (await get('/api/v1/detection-evaluations/'+evaluationId)).status,{timeout:30000}).toBe('completed');
  const evaluation=await get('/api/v1/detection-evaluations/'+evaluationId);
  expect(evaluation.report.coverage.complete).toBe(true);expect(evaluation.report.coverage.total).toBe(6);
  expect(evaluation.report.metrics).toEqual(baseline.report.metrics);
  await page.getByRole('link',{name:'Open source collection revision',exact:true}).click();
  await expect(page.getByTestId('collection-id')).toHaveText(collectionId);
  await expect(page.getByTestId('collection-revision')).toHaveText('r1');
  expect(await (await page.request.get(stored.download_url,{headers})).body()).toEqual(collectionBytes);
  expect(await (await page.request.get(originalDataset.download_url,{headers})).body()).toEqual(originalBytes);
  if(token)expect((await page.request.get('/api/v1/collections/'+collectionId)).status()).toBe(401);
  await page.screenshot({path:info.outputPath('collections-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();
  await page.screenshot({path:info.outputPath('collections-mobile.png'),fullPage:true});
  expect(errors).toEqual([]);
  writeFileSync(info.outputPath('acceptance.json'),JSON.stringify({collection_id:collectionId,collection_revision:1,
    collection_hash:stored.manifest_hash,publication_id:receipt.id,publication_hash:receipt.manifest_hash,
    dataset_id:ds.id,dataset_hash:ds.manifest_hash,evaluation_id:evaluationId,baseline_id:baseline.id,
    recordings:2,source_references:9,unique_messages:7,overlap_references:2,included:6,excluded:1,
    reviewed_negatives:2,unreviewed_before_explicit_exclusion:1,actual_coco_complete:true,
    original_metric_values_preserved:true,no_duplicate_after_response_loss:true,original_snapshots_preserved:true,browser_errors:errors},null,2));
}
