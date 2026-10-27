import {test,expect} from '@playwright/test';

test('retained pixel case keeps its verdict without implying missing evidence when no individual gate table exists',async({page,request})=>{
 const execution=process.env.AXIOM_ADAS_CAMPAIGN_DISPLAY_EXECUTION??'job_c5e9de75da7b4006acd3c2aae3a9f6c3';
 const run=await(await request.get(`/api/v1/experiments/executions/${execution}`)).json();expect(run.status).toBe('completed');
 const summary=await(await request.get(run.artifacts.find((a:{name:string})=>a.name==='summary.json').download_url)).json();
 const selected=summary.cases.find((c:{id:string})=>c.id==='long-continuous');expect(selected.validation_verdict).toBe('passed');expect(selected.summary.completed_frames).toBeGreaterThan(0);
 await page.goto(`/#hil-sim?project=default&mode=fpga&target=engineering-lab&tab=module_rtl&moduleView=campaign&campaignExecution=${execution}&campaignCase=long-continuous`);
 const panel=page.locator('.campaign-selected-case');await expect(panel).toContainText('통과 · seed');
 await expect(panel).not.toContainText('판정 항목이 아직 보존되지 않았습니다');await expect(panel.locator('.campaign-gates')).toHaveCount(0);
 await expect(panel.getByText('선택 case의 실제 설정·결과',{exact:true})).toBeVisible();
});
