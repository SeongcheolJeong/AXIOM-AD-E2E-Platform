import {test,expect,type Page,type APIRequestContext} from '@playwright/test';
const url='/#hil-sim?project=default&mode=fpga&target=rtl-fifo';
async function execute(page:Page) {
 const previous=await page.getByLabel('RTL 실행 A',{exact:true}).inputValue();
 await page.getByRole('button',{name:'RTL 개정 저장',exact:true}).click();
 await expect(page.getByRole('button',{name:'RTL A/B 실행',exact:true})).toBeEnabled();
 await page.getByRole('button',{name:'RTL A/B 실행',exact:true}).click();
 await expect(page.getByLabel('RTL 실행 A',{exact:true})).not.toHaveValue(previous);
 await expect(page.locator('.rtl-status')).toContainText(/실행 (completed|failed|cancelled|interrupted)/,{timeout:90000});
 await expect(page.locator('.rtl-status')).toContainText('실행 completed');
 await expect(page.getByRole('img',{name:'RTL 디지털 파형',exact:true})).toBeVisible();
 return page.getByLabel('RTL 실행 A',{exact:true}).inputValue();
}
async function artifact(request:APIRequestContext,id:string,name:string) {const run=await(await request.get(`/api/v1/experiments/executions/${id}`)).json();return(await request.get(run.artifacts.find((a:{name:string})=>a.name===name).download_url)).json();}
test('actual Verilator normal/stall model comparison, scenario AB, resume and export',async({page,request})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url);await expect(page.getByRole('heading',{name:'FPGA RTL Lab',exact:true})).toBeVisible();
 await page.getByLabel('RTL 예제',{exact:true}).selectOption('normal');await page.getByLabel('RTL words',{exact:true}).fill('512');await page.getByLabel('RTL 시험 이름',{exact:true}).fill('Browser RTL normal '+Date.now());
 const normal=await execute(page);const normalTrace=await artifact(request,normal,'rtl-trace.json');expect(normalTrace.summary.output_transfers).toBe(512);expect(normalTrace.summary.input_stall_cycles).toBe(0);await expect(page.getByText('검증 통과 · SimPy ↔ RTL 일치',{exact:true})).toBeVisible();
 await page.getByLabel('RTL 예제',{exact:true}).selectOption('stall');await page.getByLabel('RTL words',{exact:true}).fill('512');await page.getByLabel('RTL 시험 이름',{exact:true}).fill('Browser RTL stall '+Date.now());
 const stall=await execute(page);const t=await artifact(request,stall,'rtl-trace.json'),compare=await artifact(request,stall,'comparison.json');expect(t.summary.output_transfers).toBe(512);expect(t.summary.input_stall_cycles).toBeGreaterThan(0);expect(t.summary.peak_occupancy).toBe(64);expect(compare.matched).toBe(true);expect(compare.scoreboards.rtl.passed).toBe(true);
 await page.getByLabel('RTL 비교 B',{exact:true}).selectOption(normal);await page.getByLabel('RTL 선택 클록',{exact:true}).fill('250');await page.getByLabel('RTL 파형 범위',{exact:true}).selectOption('32');
 await expect(page.getByText('FULL · backpressure',{exact:true})).toBeVisible();const row=page.locator('.rtl-cycle-detail tr').filter({has:page.getByRole('rowheader',{name:'occupancy',exact:true})});await expect(row).toContainText('64');await expect(row).toHaveClass('different');
 await page.getByRole('button',{name:'엔진 비교 · SimPy ↔ RTL',exact:true}).click();await expect(page.locator('.rtl-cycle-detail tr.different')).toHaveCount(0);await expect(page.getByRole('button',{name:'첫 불일치 이동',exact:true})).toBeDisabled();
 await page.getByRole('button',{name:'시나리오 비교 · RTL A ↔ RTL B',exact:true}).click();const resume=page.url();await page.reload();await expect(page.getByRole('img',{name:'RTL 디지털 파형',exact:true})).toBeVisible();await expect(page.getByLabel('RTL 선택 클록',{exact:true})).toHaveValue('250');await expect(page.getByLabel('RTL 파형 범위',{exact:true})).toHaveValue('32');await expect(page.getByLabel('RTL 비교 B',{exact:true})).toHaveValue(normal);expect(page.url()).toBe(resume);
 for(const name of ['fifo.vcd','rtl-trace.json','transfers.csv','comparison.json']) {const download=page.waitForEvent('download');await page.getByRole('button',{name,exact:true}).click();expect((await download).suggestedFilename()).toBe(name);}
 await page.getByText('FIFO / 입력 / 수신 일정',{exact:true}).click();await page.screenshot({path:'test-results/rtl-ab.png',fullPage:true});await page.setViewportSize({width:760,height:1000});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(errors).toEqual([]);
});
test('reset disposal and bounded timeout are visible as distinct validation outcomes',async({page,request})=>{
 await page.goto(url);await page.getByLabel('RTL 예제',{exact:true}).selectOption('reset');await page.getByLabel('RTL words',{exact:true}).fill('512');
 const reset=await execute(page);const r=await artifact(request,reset,'rtl-trace.json');expect(r.summary.reset_discarded).toBeGreaterThan(0);expect(r.summary.output_transfers+r.summary.reset_discarded).toBe(r.summary.input_transfers);await expect(page.getByText('검증 통과 · SimPy ↔ RTL 일치',{exact:true})).toBeVisible();
 await page.getByLabel('RTL 예제',{exact:true}).selectOption('normal');await page.getByLabel('RTL words',{exact:true}).fill('512');await page.getByLabel('RTL max_cycles',{exact:true}).fill('16');
 const timeout=await execute(page);const t=await artifact(request,timeout,'rtl-trace.json');expect(t.summary.timeout).toBe(true);await expect(page.getByText('검증 실패 · 최대 클록 초과 / 미배출',{exact:true})).toBeVisible();await expect(page.locator('.rtl-pass')).toHaveCount(0);
});
