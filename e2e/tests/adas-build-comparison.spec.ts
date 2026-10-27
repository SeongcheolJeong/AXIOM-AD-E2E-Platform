import {test,expect,type APIRequestContext} from '@playwright/test';

async function retainedBuild(request:APIRequestContext,id:string){
 const response=await request.get(`/api/v1/experiments/executions/${id}`);expect(response.ok()).toBeTruthy();
 const run=await response.json();expect(run.experiment_kind).toBe('fpga_build');
 const artifact=run.artifacts.find((item:{name:string})=>item.name==='summary.json');expect(artifact).toBeTruthy();
 const summary=await(await request.get(artifact.download_url)).json();expect(summary.engine).toBe('adas_module_rtl');
 return {run,summary};
}

test('retained native build A/B: normal selection, exact URL, numeric evidence and export',async({page,request})=>{
 const aId=process.env.AXIOM_BUILD_A,bId=process.env.AXIOM_BUILD_B;
 test.skip(!aId||!bId,'Provide two retained native build IDs using AXIOM_BUILD_A / AXIOM_BUILD_B');
 const [a,b]=await Promise.all([retainedBuild(request,aId!),retainedBuild(request,bId!)]);
 const query=new URLSearchParams({project:'default',mode:'fpga',target:'engineering-lab',tab:'module_rtl'});
 if(process.env.AXIOM_PIXEL_A)query.set('execution',process.env.AXIOM_PIXEL_A);
 if(process.env.AXIOM_PIXEL_B)query.set('compare',process.env.AXIOM_PIXEL_B);
 const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto('/#hil-sim?'+query);
 await page.getByLabel('ADAS 빌드 A',{exact:true}).selectOption(aId!);
 await page.getByLabel('ADAS 빌드 비교 B',{exact:true}).selectOption(bId!);
 const panel=page.getByRole('region',{name:'ADAS 빌드 A/B 비교',exact:true});await expect(panel).toBeVisible();
 const row=(label:string)=>panel.getByRole('rowheader',{name:label,exact:true}).locator('..');
 const values=(label:string)=>row(label).getByRole('cell');
 await expect(values('Fmax · MHz').nth(0)).toHaveText(a.summary.fmax_mhz.toFixed(3));
 await expect(values('Fmax · MHz').nth(1)).toHaveText(b.summary.fmax_mhz.toFixed(3));
 await expect(values('목표 clock · MHz').nth(0)).toHaveText((a.summary.target_clock_hz/1e6).toFixed(3));
 await expect(values('생성 RTL SHA-256').nth(0)).toHaveText(a.summary.design_hash);
 await expect(values('생성 RTL SHA-256').nth(1)).toHaveText(b.summary.design_hash);
 await expect(values('배치배선 seed').nth(0)).toHaveText(String(a.summary.seed));
 await expect(panel).toContainText('경로별 slack 측정값이 아닙니다');
 if(aId===bId)await expect(panel).toContainText('동일 실행 선택');
 else if(a.summary.target_clock_hz===b.summary.target_clock_hz&&a.summary.seed===b.summary.seed&&a.summary.build_profile_id===b.summary.build_profile_id)await expect(panel).toContainText('프로필·목표·seed 동일');
 if(a.summary.timing_met===true&&b.summary.timing_met===false){await expect(values('타이밍 판정').nth(0)).toHaveText('목표 타이밍 충족');await expect(values('타이밍 판정').nth(1)).toHaveText('목표 타이밍 미충족');}
 const bookmark=page.url(),saved=new URLSearchParams(bookmark.split('?')[1]);expect(saved.get('build')).toBe(aId);expect(saved.get('buildCompare')).toBe(bId);
 if(process.env.AXIOM_PIXEL_A)expect(saved.get('execution')).toBe(process.env.AXIOM_PIXEL_A);
 if(process.env.AXIOM_PIXEL_B)expect(saved.get('compare')).toBe(process.env.AXIOM_PIXEL_B);
 await page.reload();await expect(panel).toBeVisible();await expect(page.getByLabel('ADAS 빌드 A',{exact:true})).toHaveValue(aId!);await expect(page.getByLabel('ADAS 빌드 비교 B',{exact:true})).toHaveValue(bId!);expect(page.url()).toBe(bookmark);
 const download=page.waitForEvent('download');await panel.getByRole('button',{name:'빌드 B 근거 내보내기',exact:true}).click();expect((await download).suggestedFilename()).toBe(`${bId}-evidence.json`);
 await panel.screenshot({path:'test-results/adas-build-comparison-desktop.png'});
 await page.setViewportSize({width:760,height:1000});await expect.poll(()=>page.locator('.sidebar').evaluate(el=>el.getBoundingClientRect().right)).toBeLessThanOrEqual(0);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await panel.screenshot({path:'test-results/adas-build-comparison-narrow.png'});
 await page.getByLabel('ADAS 빌드 비교 B',{exact:true}).selectOption('');await expect(panel).toHaveCount(0);expect(new URLSearchParams(page.url().split('?')[1]).has('buildCompare')).toBe(false);expect(errors).toEqual([]);
});
