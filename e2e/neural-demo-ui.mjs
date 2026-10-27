// Focused MOCKED browser contract check. PNGs below are test fixtures, never demo evidence.
import { chromium, expect } from '@playwright/test';
import { createHash } from 'node:crypto';
import { deflateSync } from 'node:zlib';
import { mkdirSync } from 'node:fs';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
function crc32(bytes) { let crc = -1; for (const b of bytes) { crc ^= b; for (let i = 0; i < 8; i++) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1)); } return (crc ^ -1) >>> 0; }
function chunk(type, bytes) { const t = Buffer.from(type), n = Buffer.alloc(4), crc = Buffer.alloc(4); n.writeUInt32BE(bytes.length); crc.writeUInt32BE(crc32(Buffer.concat([t, bytes]))); return Buffer.concat([n, t, bytes, crc]); }
function png(rgb) { const header = Buffer.alloc(13); header.writeUInt32BE(8, 0); header.writeUInt32BE(8, 4); header[8] = 8; header[9] = 2; const pixels = Buffer.alloc(8 * 25); for (let y = 0; y < 8; y++) for (let x = 0; x < 8; x++) rgb.forEach((v, c) => pixels[y * 25 + 1 + x * 3 + c] = v); return Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]), chunk('IHDR', header), chunk('IDAT', deflateSync(pixels)), chunk('IEND', Buffer.alloc(0))]); }
function zip(files) { let offset = 0; const locals = [], directory = []; for (const [name, bytes] of files) { const encoded = Buffer.from(name), local = Buffer.alloc(30), entry = Buffer.alloc(46); local.writeUInt32LE(0x04034b50, 0); local.writeUInt16LE(20, 4); local.writeUInt32LE(crc32(bytes), 14); local.writeUInt32LE(bytes.length, 18); local.writeUInt32LE(bytes.length, 22); local.writeUInt16LE(encoded.length, 26); entry.writeUInt32LE(0x02014b50, 0); entry.writeUInt16LE(20, 4); entry.writeUInt16LE(20, 6); entry.writeUInt32LE(crc32(bytes), 16); entry.writeUInt32LE(bytes.length, 20); entry.writeUInt32LE(bytes.length, 24); entry.writeUInt16LE(encoded.length, 28); entry.writeUInt32LE(offset, 42); locals.push(local, encoded, bytes); directory.push(entry, encoded); offset += local.length + encoded.length + bytes.length; } const central = Buffer.concat(directory), end = Buffer.alloc(22); end.writeUInt32LE(0x06054b50); end.writeUInt16LE(files.length, 8); end.writeUInt16LE(files.length, 10); end.writeUInt32LE(central.length, 12); end.writeUInt32LE(offset, 16); return Buffer.concat([...locals, central, end]); }
const files = [], frames = [0,1,2].map(index => { const frame = { frame_index: index, source_frame: 100+index, time_s:index/10 }; for (const [side,color] of [['original',[30,50,40]],['baseline',[40,65,50]],['novel',[45,90,55]]]) { const bytes = png(color.map(v => v + index)), name = `${side}/${String(index).padStart(6,'0')}.png`; files.push([name,bytes]); frame[side] = {name,sha256:hash(bytes),size_bytes:bytes.length}; } return frame; });
const archive = zip(files), h = 'a'.repeat(64);
const manifest = { schema_version:'neural-render-result/1',source_execution_id:'source',reconstruction_execution_id:'scene',source_manifest_sha256:h,checkpoint_sha256:h,frames_archive_sha256:hash(archive),source_kind:'simulation',fidelity:'neural_reconstruction_of_carla',lateral_m:-.3,height_m:0,mode:'sequence',frame_index:0,width:8,height:8,fps:10,frames };
const json = Buffer.from(JSON.stringify(manifest));
const artifact = (id,name,media,bytes) => ({id,name,media_type:media,size_bytes:bytes.length,sha256:hash(bytes),download_url:`/api/v1/experiments/executions/saved/artifacts/${id}`});
const artifacts = [artifact('result','neural-render-result.json','application/json',json),artifact('frames','neural-render-frames.zip','application/zip',archive)];
const execution = {id:'saved',experiment_id:'saved-exp',project_id:'demo',experiment_kind:'neural_render',status:'completed',adapter_id:'neural-splatfacto',adapter_version:'1.0.0',input_hash:h,fidelity:manifest.fidelity,artifacts,summary:{...manifest,frames:undefined},created_at:'2026-10-10T00:00:00Z',outcome_uncertain:false};
const receipt = Buffer.from(JSON.stringify({schema_version:1,prepared:{input:{}},execution}));
execution.manifest_hash = hash(receipt); execution.download_url = '/api/v1/experiments/executions/saved/export';
const presets = [['left','왼쪽 시점',-.3,0],['right','오른쪽 시점',.3,0],['raised','높은 시점',0,.2]].map(([id,title,lateral_m,height_m]) => ({id,title,description:'Fixture',defaults:{lateral_m,height_m},ready:true,status:'ready',reason:null,binding_revision:1,saved_execution_id:id==='left'?'saved':null,source:{source_execution_id:'source',reconstruction_execution_id:'scene',saved_execution_id:id==='left'?'saved':null,seed:42,adapter_version:'1.0.0'}}));
const sources = {sources:[{id:'source',name:'CARLA test fixture',ready:true}],reconstructions:[{id:'scene',name:'Test scene',source_execution_id:'source'}],renders:[{id:'saved',name:'Mocked stored render',source_execution_id:'source',reconstruction_execution_id:'scene'}]};
const previewRequests = [], requests = [], reconstructionRequests = [], bindingRequests = []; let newRunStatus='running', submissionCount=0, currentKind='neural_render';
const browser = await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL ?? "chrome"});
try {
  const context = await browser.newContext({viewport:{width:1440,height:1100}}), page = await context.newPage();
  const errors=[]; page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/api/v1/**',async route=>{
    const url=new URL(route.request().url()), path=url.pathname.slice('/api/v1'.length), method=route.request().method();
    requests.push({path,method});
    const send = value => route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(value)});
    if(path==='/team/whoami') return send({mode:'project',role:'Engineer',legacy_domains_available:false});
    if(path==='/projects') return send({items:[{id:'demo',name:'Neural mocked UI checks'}]});
    if(path==='/health') return send({status:'ok'});
    if(path==='/neural-sim/presets') return send({items:presets});
    if(path==='/neural-sim/sources') return send(sources);
    if(path==='/experiments/executions/saved') return send(execution);
    if(path==='/experiments/executions/saved/export') return route.fulfill({status:200,contentType:'application/json',body:receipt});
    if(path==='/experiments/executions/saved/artifacts/result') return route.fulfill({status:200,contentType:'application/json',body:json});
    if(path==='/experiments/executions/saved/artifacts/frames') { const match=/bytes=(\d+)-(\d+)/.exec(route.request().headers().range),start=Number(match?.[1]),end=Number(match?.[2]); if(!match) return route.fulfill({status:200,contentType:'application/zip',body:archive}); return route.fulfill({status:206,headers:{'Content-Type':'application/zip','Content-Range':`bytes ${start}-${end}/${archive.length}`,'Content-Length':String(end-start+1)},body:archive.subarray(start,end+1)}); }
    if(path.endsWith('/preview') && path.startsWith('/neural-sim/')) { const input=route.request().postDataJSON(); previewRequests.push(input); return send({experiment:{project_id:'demo',kind:'neural_render',adapter_id:'neural-splatfacto',adapter_version:'1.0.0',input:{reconstruction_execution_id:'scene',...input.parameters,mode:input.mode,frame_index:input.frame_index}},preview:{preview_hash:h}}); }
    if(path==='/experiments/preview') { reconstructionRequests.push(route.request().postDataJSON()); return send({preview_hash:h}); }
    if(path.endsWith('/bindings') && path.startsWith('/neural-sim/')) {bindingRequests.push(route.request().postDataJSON()); return send({revision:2});}
    if(path==='/experiments' && method==='POST') {currentKind=route.request().postDataJSON().kind; return send({id:'new-exp',preview_hash:h});}
    if(path==='/experiments/new-exp/executions') { submissionCount++; return send({id:`new-run-${submissionCount}`}); }
    if(/^\/experiments\/executions\/new-run-\d+$/.test(path)) return send({...execution,id:path.split('/').at(-1),status:newRunStatus,experiment_kind:currentKind,error:newRunStatus==='failed'?'MOCK GPU worker unavailable':null,cancellation_supported:true});
    if(/^\/experiments\/executions\/new-run-\d+\/cancel$/.test(path)) {newRunStatus='cancelled';return send({status:'cancelled'});}
    return route.fulfill({status:404,contentType:'application/json',body:JSON.stringify({detail:`Unmocked test route ${path}`})});
  });
  await page.goto(`${process.env.AXIOM_NEURAL_UI_URL??'http://127.0.0.1:5184'}/#neural-sim?project=demo`);
  await expect(page.getByRole('heading',{name:/기록한 길을,.*새로운 시점으로/})).toBeVisible();
  await expect(page.getByAltText('Neural 렌더 · 새로운 시점')).toBeVisible();
  await expect(page.locator('.neural-playback-note')).toContainText('frame 0');
  await page.getByRole('button',{name:'원본 ↔ 재구성',exact:true}).click();
  await expect(page.getByAltText('원본 CARLA 영상')).toBeVisible();
  await page.getByRole('button',{name:'슬라이더',exact:true}).click();
  await page.getByRole('slider',{name:'Neural 영상 비교 슬라이더'}).fill('70');
  await expect(page.locator('.neural-wipe-front')).toHaveCSS('clip-path','inset(0px 30% 0px 0px)');
  await page.getByRole('button',{name:'나란히',exact:true}).click();
  await page.getByRole('slider',{name:'Neural 공통 시간축'}).fill('2');
  await expect(page.locator('.neural-playback-note')).toContainText('frame 2');
  await expect(page.getByRole('spinbutton',{name:'Neural 미리보기 프레임'})).toHaveValue('2');
  await page.getByRole('combobox',{name:'Neural 재생 속도'}).selectOption('2');
  await page.getByRole('button',{name:'Neural 처음으로'}).click();
  await page.getByRole('button',{name:'Neural 영상 재생',exact:true}).click();
  await expect(page.locator('.neural-playback-note')).toContainText('frame 2');
  await page.getByRole('button',{name:'Neural 발표 전체 화면'}).click();
  await expect(page.locator('.neural-presenter')).toHaveClass(/neural-expanded/);
  await page.getByRole('button',{name:'Neural 발표 전체 화면'}).click();
  await page.getByRole('spinbutton',{name:'Neural 좌우 이동',exact:true}).fill('0.4');
  await expect(page.locator('.neural-display-settings')).toContainText('−0.30'.replace('−','-'));
  await page.getByRole('button',{name:'현재 프레임 생성',exact:true}).click();
  await expect(page.locator('.neural-run')).toContainText('Neural 엔진 실행 중');
  if(previewRequests[0].parameters.lateral_m!==.4||previewRequests[0].frame_index!==2||previewRequests[0].mode!=='frame') throw new Error('Current frame intent was not preserved');
  await expect(page.getByAltText('원본 CARLA 영상')).toBeVisible();
  await expect(page.locator('.neural-display-settings')).toContainText('-0.30');
  newRunStatus='failed';
  await expect(page.locator('.neural-run')).toContainText('MOCK GPU worker unavailable',{timeout:6000});
  await page.getByRole('button',{name:'전체 구간 새 시점 생성',exact:true}).click();
  await expect.poll(()=>submissionCount).toBe(2);
  if(previewRequests[1].mode!=='sequence'||previewRequests[1].frame_index!==0) throw new Error('Sequence should use full frame interval');
  await expect(page.locator('.neural-run')).toContainText('실행 실패');
  mkdirSync('.runtime/neural-ui-checks',{recursive:true});
  await page.screenshot({path:'.runtime/neural-ui-checks/mocked-neural-workbench.png',fullPage:true});
  // Source-only preparation must be executable before any reconstruction exists.
  for(const preset of presets) {preset.ready=false;preset.status='needs_reconstruction';preset.saved_execution_id=null;preset.source.reconstruction_execution_id=null;preset.source.saved_execution_id=null;}
  sources.reconstructions=[];sources.renders=[];newRunStatus='running';
  await page.goto(`${process.env.AXIOM_NEURAL_UI_URL??'http://127.0.0.1:5184'}/#neural-sim?project=demo&setup=1`);
  await expect(page.getByRole('button',{name:'새 3D 장면 학습',exact:true})).toBeEnabled();
  await expect(page.getByRole('button',{name:'전체 구간 새 시점 생성',exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'프리셋에 장면 연결',exact:true}).click();
  await expect.poll(()=>bindingRequests.length).toBe(3);
  if(bindingRequests.some(input=>input.source_execution_id!=='source'||input.reconstruction_execution_id!==null||input.expected_revision!==1)) throw new Error('Source-only binding shape differs');
  await page.getByRole('button',{name:'새 3D 장면 학습',exact:true}).click();
  await expect.poll(()=>reconstructionRequests.length).toBe(1);
  if(JSON.stringify(reconstructionRequests[0].input)!==JSON.stringify({source_execution_id:'source',rig:'rig_a',camera:'front'})) throw new Error('Reconstruction input differs');
  await expect(page.locator('.neural-run')).toContainText('Neural 엔진 실행 중');
  await page.getByRole('button',{name:'실행 취소',exact:true}).click();
  await expect(page.locator('.neural-run')).toContainText('취소됨');
  if(errors.length) throw new Error(errors.join('\n'));
  console.log(JSON.stringify({status:'passed',evidence:'mocked_browser_only',checks:['hash-verified ZIP PNG playback','original/reconstruction and baseline/novel','wipe slider','time/speed/fullscreen','separate pending versus displayed settings','real API submission shape','GPU failure visibility','retry after failure','source-only binding','reconstruction submission before scene exists','cancel through API'],preview_requests:previewRequests,submission_count:submissionCount}));
  await context.close();
} finally { await browser.close(); }
