import {useEffect,useRef,useState} from 'react';
import {api,useResource} from '../../api';
type D=Record<string,any>;
const base='/hil-bridge/model-injections',eng='/hil-sim/engineering-lab';
const post=(body:D,key?:string):RequestInit=>({method:'POST',body:JSON.stringify(body),headers:key?{'Idempotency-Key':key}:{}});
const labels:Record<string,string>={channel:'채널 · 0–3',at_us:'주입 시작 · µs',duration_us:'주입 지속 · µs',start_ms:'주입 시작 · ms',end_ms:'주입 종료 · ms',stop_ms:'모델 종료 · ms',voltage_v:'강하 중 전원 전압 · V',offset_v:'센서 오프셋 · V',step_us:'최대 해석 간격 · µs',frames:'입력 프레임 수 · 채널당'};

export default function HilBoundaryModels({project,pairId,onPair}:{project:string,pairId:string,onPair:(id:string)=>void}){
 const catalog=useResource<D>(`${base}/catalog?project_id=${encodeURIComponent(project)}`);
 const [selected,setSelected]=useState('camera_raw_csi'),[settings,setSettings]=useState<D|null>(null),[pair,setPair]=useState<D|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
 const attempt=useRef<{signature:string,key:string}|null>(null);
 const item=catalog.data?.items.find((v:D)=>v.boundary===selected);
 useEffect(()=>{if(!settings&&item&&!pairId)setSettings({...item.settings});},[catalog.data,pairId]);
 useEffect(()=>{if(!pairId)return;let live=true;api<D>(`${base}/${pairId}?project_id=${encodeURIComponent(project)}`).then(v=>{if(live){setPair(v);setSelected(v.plan.settings.boundary);setSettings({...v.plan.settings});}}).catch(e=>{if(live)setError(String(e));});return()=>{live=false;};},[project,pairId]);
 const dirty=!pair||JSON.stringify(settings)!==JSON.stringify(pair.plan.settings);
 const displaySettings=settings&&['power','analog'].includes(settings.boundary)?{...settings,stop_ms:settings.stop_ms??10,...(settings.boundary==='power'?{step_us:settings.step_us??10}:{})}:settings?.boundary==='fpga'?{...settings,frames:settings.frames??30}:settings;
 async function action(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn();}catch(e){setError(String(e));}finally{setBusy(false);}}
 async function save(){
  if(!settings||!item)return;
  const body={project_id:project,name:item.title,settings,seed:42},signature=JSON.stringify(body);
  if(attempt.current?.signature!==signature)attempt.current={signature,key:crypto.randomUUID()};
  const value=await api<D>(base,post(body,attempt.current.key));setPair(value);setSettings({...value.plan.settings});onPair(value.id);setMessage('정상 A / 고장 B 설정 저장 완료 · 실행 결과는 별도');
 }
 async function execute(stay=false){
  if(!pair||dirty)return;
  const storage=`hil.model.execute.${project}.${pair.id}`;
  const key=sessionStorage.getItem(storage)??crypto.randomUUID();sessionStorage.setItem(storage,key);
  const prepared:Record<string,D>={},jobs:Record<string,D>={};
  for(const side of ['baseline','faulted']){
   const r=pair.tests[side];prepared[side]=await api<D>(`${eng}/tests/${r.id}/preview`,post({project_id:project,revision:r.revision,sha256:r.sha256}));
  }
  for(const side of ['baseline','faulted']){
   const p=prepared[side],id=`${key}-${side}`,experiment=await api<D>('/experiments',post(p.experiment,id));
   jobs[side]=await api<D>(`/experiments/${experiment.id}/executions`,post({preview_hash:p.preview_hash},id));
  }
  sessionStorage.removeItem(storage);
  if(stay){setMessage('모델 A/B 실행 제출 완료 · 완료 후 아래 ECU 연결에서 실행을 선택하세요.');return;}
  const q=new URLSearchParams({project,mode:'fpga',target:'engineering-lab',tab:pair.plan.engine==='spice'?'spice':'module_rtl',test:pair.tests.baseline.id,revision:String(pair.tests.baseline.revision),execution:jobs.baseline.id,compare:jobs.faulted.id,sample:'0',span:pair.plan.engine==='spice'?'250':'64',channel:String(pair.plan.settings.channel??0),frame:'0'});
  location.hash=`hil-sim?${q}`;
 }
 return <section id="hil-boundary-models" className="vecu-panel hil-boundary-models" aria-label="경계 모델 주입시험">
  <h2>RAW · FPGA · 전원 · 아날로그 주입시험</h2>
  <p>Verilator 또는 ngspice가 정상 A와 고장 B를 각각 실행합니다. 완료된 모델 출력을 아래 연결 설정에서 Virtual ECU·CARLA에 재생할 수 있습니다. 실물 I/O는 포함하지 않습니다.</p>
  <label>모델 경계<select aria-label="모델 주입 경계" disabled={busy} value={selected} onChange={e=>{const v=catalog.data?.items.find((x:D)=>x.boundary===e.target.value);setSelected(e.target.value);setSettings({...v.settings});setPair(null);onPair('');}}>{catalog.data?.items.map((v:D)=><option key={v.boundary} value={v.boundary}>{v.title}</option>)}</select></label>
  <p>{item?.scope}</p>
  <div className="vecu-toolbar">{displaySettings&&Object.entries(displaySettings).filter(([k])=>k!=='boundary'&&k!=='repeats').map(([k,v])=><label key={k}>{labels[k]??k}<input aria-label={labels[k]??k} type="number" step={['start_ms','end_ms','stop_ms','voltage_v','offset_v'].includes(k)?'.1':'1'} disabled={busy} value={v} onChange={e=>setSettings({...displaySettings,[k]:Number(e.target.value)})}/></label>)}</div>
  {settings?.boundary==='fpga'&&<fieldset><legend>FPGA 정체 재주입 일정</legend>
   <p>아래 시각은 {(settings.frames??30)/30}초 RTL 입력의 시작 기준입니다. 입력 프레임 수를 늘리면 같은 1080p30 시간축에서 실제 RTL 실행이 길어집니다. ECU 연결에서 재생 시작을 3초로 지정하면 800,000µs 재주입은 차량 시간 3.8초입니다. 같은 채널에 순서대로 주입하며 마지막 고장 뒤 50ms 이상을 남겨야 합니다. 짧은 정체는 FIFO가 흡수할 수 있으므로 실제 frame_abort·ECU 검출 결과를 확인하세요.</p>
   {(settings.repeats??[]).map((f:D,i:number)=><div className="vecu-toolbar" key={i}>
    {['at_us','duration_us'].map(k=><label key={k}>재주입 {i+1} · {labels[k]}<input type="number" step="1" disabled={busy} value={f[k]} onChange={e=>setSettings({...settings,repeats:settings.repeats.map((v:D,j:number)=>j===i?{...v,[k]:Number(e.target.value)}:v)})}/></label>)}
    <button disabled={busy} onClick={()=>setSettings({...settings,repeats:settings.repeats.filter((_:D,j:number)=>i!==j)})}>재주입 {i+1} 삭제</button>
   </div>)}
   <button disabled={busy||(settings.repeats?.length??0)>=4} onClick={()=>setSettings({...settings,repeats:[...(settings.repeats??[]),{at_us:800000,duration_us:100}]})}>FPGA 정체 재주입 추가</button>
  </fieldset>}
  {settings?.boundary==='power'&&<fieldset><legend>전원 강하 재주입 일정</legend>
   <p>원본 회로 시간 기준입니다. 재생 시작 3초 + 아래 1,350ms = 차량 시간 4.35초입니다. 같은 강하 전압을 사용하며 이전 구간과 겹치지 않게 지정하세요. 반복을 추가하면 기본 해석 종료 1,750ms·최대 간격 20µs를 적용합니다. 원본 시간이 늘어날 뿐 재생 속도는 1:1입니다.</p>
   {(settings.repeats??[]).map((f:D,i:number)=><div className="vecu-toolbar" key={i}>
    {['start_ms','end_ms'].map(k=><label key={k}>전원 재주입 {i+1} · {labels[k]}<input type="number" step=".1" disabled={busy} value={f[k]} onChange={e=>setSettings({...settings,repeats:settings.repeats.map((v:D,j:number)=>j===i?{...v,[k]:Number(e.target.value)}:v)})}/></label>)}
    <button disabled={busy} onClick={()=>setSettings({...settings,repeats:settings.repeats.filter((_:D,j:number)=>i!==j)})}>전원 재주입 {i+1} 삭제</button>
   </div>)}
   <button disabled={busy||(settings.repeats?.length??0)>=4} onClick={()=>setSettings({...settings,stop_ms:Math.max(settings.stop_ms??10,1750),step_us:Math.max(settings.step_us??10,20),repeats:[...(settings.repeats??[]),{start_ms:1350,end_ms:1650}]})}>전원 강하 재주입 추가</button>
  </fieldset>}
  <div className="vecu-toolbar"><button disabled={busy||!settings} onClick={()=>void action(save)}>모델 A/B 설정 저장</button><button disabled={busy||dirty} onClick={()=>void action(()=>execute())}>새 모델 A/B 실행 · 결과 열기</button><button disabled={busy||dirty} onClick={()=>void action(()=>execute(true))}>모델 A/B 실행 · ECU 연결 준비</button></div>
  {pair&&<p>설정 {pair.id} · {dirty?'미저장 변경':'저장됨'} · seed {pair.plan.seed} · {pair.plan.engine==='spice'?`${pair.plan.sides.baseline.stop_s*1000}ms 회로 해석`:`4×1920×1080 RAW12 · 30 FPS · 채널당 ${pair.plan.sides.baseline.frames}프레임 · 100MHz`}</p>}
  {(error||catalog.error)&&<p role="alert" className="vecu-error">{error||catalog.error}</p>}
  {message&&<p role="status">{message}</p>}
 </section>;
}
