import {useEffect,useRef,useState} from 'react';
import {api,useResource} from '../../api';
import {modelProtectionLabel,modelReleaseCandidateEvents,modelReleaseCancellationEvents} from './hilModelComparison';
type D=Record<string,any>;
const ref=(v:D)=>({id:v.id,revision:v.revision,sha256:v.sha256});
const post=(body:D,key?:string):RequestInit=>({method:'POST',body:JSON.stringify(body),headers:key?{'Idempotency-Key':key}:{}});
export default function HilModelLoop({project,source,sourceKind,pairId,onPair,saved,onRun}:{project:string,source:D|undefined,sourceKind:string,pairId:string,onPair:(v:string)=>void,saved:D|null,onRun:(binding:D,job:string)=>void}){
 const pairs=useResource<D>(`/hil-bridge/model-injections?project_id=${project}`),runs=useResource<D>(`/hil-sim/engineering-lab/executions?project_id=${project}`);
 const pair=pairs.data?.items.find((p:D)=>p.id===pairId);
 const [a,setA]=useState(''),[b,setB]=useState(''),[start,setStart]=useState(3),[hold,setHold]=useState(500),[fresh,setFresh]=useState(3),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [extras,setExtras]=useState<D[]>([]);
 const attempt=useRef<{body:string,key:string}|null>(null);
 const options=(side:string,p:D|undefined=pair)=>runs.data?.items.filter((r:D)=>r.status==='completed'&&r.test_ref?.id===p?.tests[side].id&&r.test_ref?.sha256===p?.tests[side].sha256)??[];
 useEffect(()=>{pairs.reload();runs.reload();},[pairId]);
 useEffect(()=>{const t=setInterval(()=>runs.reload(),5000);return()=>clearInterval(t);},[project]);
 useEffect(()=>{const members=saved?.model_couplings??[],s=saved?.model_coupling??members[0];if(s){setA(s.baseline_execution);setB(s.faulted_execution);setStart(s.start_ms/1000);setHold(s.min_hold_ms);setFresh(s.recovery_samples);}setExtras(members.slice(1).map((m:D)=>({pairId:m.pair_ref.id,a:m.baseline_execution,b:m.faulted_execution,start:m.start_ms/1000,hold:m.min_hold_ms,fresh:m.recovery_samples})));},[saved?.id,saved?.revision]);
 useEffect(()=>{if(pair&&runs.data){const s=saved?.model_coupling??saved?.model_couplings?.[0];if(!options('baseline').some((r:D)=>r.id===a))setA(options('baseline').find((r:D)=>s?.pair_ref.id===pair.id&&r.id===s.baseline_execution)?.id??options('baseline')[0]?.id??'');if(!options('faulted').some((r:D)=>r.id===b))setB(options('faulted').find((r:D)=>s?.pair_ref.id===pair.id&&r.id===s.faulted_execution)?.id??options('faulted')[0]?.id??'');}},[pairId,runs.data,pairs.data]);
 const updateExtra=(i:number,v:D)=>setExtras(old=>old.map((m,j)=>i===j?{...m,...v}:m));
 async function execute(){
  if(!source||!pair||!a||!b)return;setBusy(true);setError('');
  try{
   const primary={pair_ref:ref(pair),baseline_execution:a,faulted_execution:b,start_ms:Math.round(start*1000),min_hold_ms:hold,recovery_samples:fresh};
   const members=[primary,...extras.map(m=>{const p=pairs.data?.items.find((p:D)=>p.id===m.pairId);if(!p||!m.a||!m.b)throw Error('추가 모델의 정상/고장 완료 실행을 선택하세요.');return {pair_ref:ref(p),baseline_execution:m.a,faulted_execution:m.b,start_ms:Math.round(m.start*1000),min_hold_ms:m.hold,recovery_samples:m.fresh};})];
   const body={project_id:project,name:`${extras.length?'복합 모델':'모델'} 연결 · ${pair.name}`,source_kind:sourceKind,source_ref:ref(source),...(extras.length?{model_couplings:members}:{model_coupling:primary})};
   const signature=JSON.stringify(body);if(attempt.current?.body!==signature)attempt.current={body:signature,key:crypto.randomUUID()};const key=attempt.current.key;
   const binding=await api<D>('/hil-bridge/bindings',post(body,key));
   const target={project_id:project,revision:binding.revision,sha256:binding.sha256};
   await api(`/hil-bridge/bindings/${binding.id}/preview`,post(target));
   const job=await api<D>(`/hil-bridge/bindings/${binding.id}/executions`,post(target,key+'-run'));onRun(binding,job.id);attempt.current=null;
  }catch(e){setError(String(e));}finally{setBusy(false);}
 }
 return <section id="hil-model-loop" className="vecu-panel hil-model-loop">
  <h2>모델 출력 → Virtual ECU → CARLA 연결</h2>
  <p>원본 모델 A/B의 봉인된 출력을 같은 차량 시나리오에 재생합니다. 참조 ADAS가 측정값을 판정하고 CAN 제동 명령을 전송하며, Brake 보고 출력이 CARLA를 제어합니다.</p>
  <p>RAW/FPGA: 프레임 commit·abort 감시 · 전원: 8V 미만 감시 · 아날로그: 2–3V 범위 감시. 참조 감시 기준이며 실제 ECU 사양이 아닙니다.</p>
  <div className="vecu-toolbar"><label>저장 모델 A/B<select aria-label="ECU 연결 모델" value={pairId} disabled={busy} onChange={e=>{onPair(e.target.value);setA('');setB('');}}><option value="">모델 설정 선택</option>{pairs.data?.items.map((p:D)=><option value={p.id} key={p.id}>{p.name} · {p.id.slice(-6)}</option>)}</select></label><button onClick={()=>{pairs.reload();runs.reload();}}>완료 모델 실행 새로고침</button></div>
  <div className="vecu-toolbar">{[['baseline',a,setA,'A 정상 모델'],['faulted',b,setB,'B 고장 모델']].map(([side,value,setter,label]:any)=><label key={side}>{label}<select aria-label={label} value={value} disabled={busy} onChange={e=>setter(e.target.value)}><option value="">완료 실행 선택</option>{options(side).map((r:D)=><option key={r.id} value={r.id}>{r.id}</option>)}</select></label>)}</div>
  <div className="vecu-toolbar"><label>모델 재생 시작 · 초<input aria-label="모델 재생 시작" type="number" step=".05" value={start} onChange={e=>setStart(Number(e.target.value))}/></label><label>이상 검출 후 최소 보호 · ms<input aria-label="모델 최소 보호 시간" type="number" step="50" value={hold} onChange={e=>setHold(Number(e.target.value))}/></label><label>복귀 정상 표본 수<input aria-label="모델 복귀 표본 수" type="number" step="1" value={fresh} onChange={e=>setFresh(Number(e.target.value))}/></label></div>
  {extras.map((m,i)=>{const p=pairs.data?.items.find((p:D)=>p.id===m.pairId);return <fieldset key={i}><legend>동시 감시 모델 {i+2}</legend><div className="vecu-toolbar"><label>추가 모델<select aria-label={`추가 모델 ${i+2}`} value={m.pairId} onChange={e=>{const p=pairs.data?.items.find((p:D)=>p.id===e.target.value);updateExtra(i,{pairId:e.target.value,a:options('baseline',p)[0]?.id??'',b:options('faulted',p)[0]?.id??''});}}><option value="">모델 선택</option>{pairs.data?.items.map((p:D)=><option key={p.id} value={p.id}>{p.name} · {p.id.slice(-6)}</option>)}</select></label>{[['baseline','a','A 정상'],['faulted','b','B 고장']].map(([side,key,label])=><label key={key}>{label}<select value={m[key]} onChange={e=>updateExtra(i,{[key]:e.target.value})}><option value="">완료 실행 선택</option>{options(side,p).map((r:D)=><option key={r.id} value={r.id}>{r.id}</option>)}</select></label>)}</div><div className="vecu-toolbar"><label>재생 시작 · 초<input type="number" step=".05" value={m.start} onChange={e=>updateExtra(i,{start:Number(e.target.value)})}/></label><label>최소 보호 · ms<input type="number" step="50" value={m.hold} onChange={e=>updateExtra(i,{hold:Number(e.target.value)})}/></label><label>정상 표본 수<input type="number" step="1" value={m.fresh} onChange={e=>updateExtra(i,{fresh:Number(e.target.value)})}/></label><button onClick={()=>setExtras(old=>old.filter((_,j)=>i!==j))}>추가 모델 제거</button></div></fieldset>;})}
  <button disabled={busy||extras.length>=3} onClick={()=>setExtras(old=>[...old,{pairId:'',a:'',b:'',start:3,hold:500,fresh:3}])}>동시 감시 모델 추가</button>
  {extras.length>0&&<p><b>제동 유지 조건: 하나라도 보호 중이면 제동합니다.</b> 모델별 최소 보호 시간과 정상 확인을 모두 통과해야 전체 보호를 해제합니다.</p>}
  <p>위의 원본 시험 선택: <b>{source?.name??'고장 없는 CARLA Network Lab 시험을 선택하세요'}</b>. 같은 프로젝트의 정상 기반 시험과 모델을 연결합니다.</p>
  <p>모델 시간 배율 1:1 · ECU 입력은 50ms 구간 이벤트/전압 최솟값·최댓값 · 원본 종료 후 마지막 관측 상태 유지. 픽셀 인지나 실물 ADC, 차량 상태가 RTL/SPICE에 되먹임되는 연동은 포함하지 않습니다.</p>
  <button disabled={busy||!source||sourceKind!=='vecu-network-test'||!a||!b} onClick={()=>void execute()}>{busy?'모델 근거 확인·제출 중…':'모델 연결 저장 · 새 CARLA A/B 실행'}</button>
  {(error||pairs.error||runs.error)&&<p role="alert" className="vecu-error">{error||pairs.error||runs.error}</p>}
 </section>;
}

const title:Record<string,string>={camera_raw_csi:'RAW 프레임',fpga:'FPGA 프레임',power:'전원 전압',analog:'아날로그 전압'};
export function HilModelObservation({run,rows,index,onStep}:{run:D,rows:D[][],index:number,onStep:(v:number)=>void}){
 const link=run.summary?.model_link;if(!link)return null;
 if(link.boundary==='multiple')return <section className="hil-model-observation" aria-label="복합 고장 보호 상태"><h3>복합 고장 · 어느 한 보호 조건이라도 남으면 제동 유지</h3>{link.policy.release_qualification&&<p>개별 보호가 모두 해제돼도 연속 두 완료 측정구간을 확인할 때까지 전체 제동을 유지합니다. 누락·반복·10ms 초과 지연 표본은 해제 확인에 사용하지 않으며 새 이상이 오면 해제 후보를 취소합니다.</p>}<div className="vecu-ab">{rows.map((r,i)=>{const m=r[index]?.reply.bridge.model_monitor;return <article key={i}><h3>{i?'B · 복합 고장':'A · 모두 정상'}</h3><p>전체 보호: <b>{!m?'미관측':m.protect?'제동 유지':'모델 보호 조건 없음'}</b></p><p>남은 보호 조건: {modelProtectionLabel(m)}</p>{Object.entries(m?.members??{}).map(([n,v]:any)=><p key={n}>{title[n]}: {v.protect?'보호 유지':'보호 조건 없음'} · 정상 {v.fresh_samples}회</p>)}</article>;})}</div><div className="vecu-toolbar">{[...modelReleaseCandidateEvents(rows[1]??[]),...modelReleaseCancellationEvents(rows[1]??[])].sort((a:D,b:D)=>a.time_ms-b.time_ms).map((e:D,i:number)=><button key={`guard-${i}`} onClick={()=>onStep(e.input_step)}>{e.label} · {(e.time_ms/1000).toFixed(3)}초</button>)}{rows[1]?.flatMap(r=>r.reply.bridge.events??[]).filter(e=>e.kind==='model_group_recovered').map((e,i)=><button key={i} onClick={()=>onStep(Math.floor(e.time_ms/50))}>모든 모델 보호 해제 · {(e.time_ms/1000).toFixed(3)}s</button>)}</div>{link.members.map((member:D)=><HilModelObservation key={member.boundary} run={{summary:{model_link:member}}} rows={rows.map(r=>r.map(x=>({...x,reply:{...x.reply,bridge:{...x.reply.bridge,model_monitor:x.reply.bridge.model_monitor?.members?.[member.boundary]}}})))} index={index} onStep={onStep}/>)}</section>;
 const p=link.policy,stream=['camera_raw_csi','fpga'].includes(link.boundary);
 return <section className="hil-model-observation" aria-label="모델과 ECU 차량 A/B">
  <h3>{title[link.boundary]} → 참조 ADAS 감시 → CAN 제동 → Brake → CARLA</h3>
  <p>A: 정상 모델 출력 · B: 고장 모델 출력 · 동일 차량 조건. {link.start_ms/1000}초부터 원본 시간을 1:1 재생합니다.</p>
  <p>주입 시점은 원본 모델의 고장 시각을 재생 시간축에 옮긴 표식입니다. 측정값은 50ms마다 전달하므로, 아래 검출 지연에는 측정 구간을 모으는 시간도 포함됩니다.</p>
  <div className="vecu-ab">{rows.map((r,side)=>{const m=r[index]?.reply.bridge.model_monitor,s=m?.sample;return <article key={side}><h3>{side?'B · 고장 모델':'A · 정상 모델'}</h3>
   {s?<><p>{stream?`CH${s.channel} · 프레임 ${s.last_frame??'대기'} · 이번 구간 commit ${s.commit_count} / abort ${s.abort_count}`:`출력 ${s.last_v.toFixed(3)}V · 구간 ${s.min_v.toFixed(3)}–${s.max_v.toFixed(3)}V`}</p><p>원본 모델 {s.native_time_ms.toFixed(3)}ms · {s.phase==='before'?'감시 시작 전':s.phase==='tail_hold'?'원본 종료 · 마지막 관측값 유지':'원본 출력 재생'}</p><p>ECU 판정 <b>{m.protect?'보호 유지':m.state==='tracking'?'보호 조건 없음':m.state}</b> · 정상 확인 {m.fresh_samples}회 · 최소 보호 잔여 {m.hold_remaining_ms.toFixed(0)}ms</p></>:<p>ECU 측 모델 입력 관측 없음</p>}
   <p>수신 Brake 출력 {Number.isFinite(r[index]?.reply.control.brake)?`${(r[index].reply.control.brake*100).toFixed(0)}%`:'미관측'} · 다음 차량 속도 {Number.isFinite(r[index]?.next_ego?.speed_mps)?`${(r[index].next_ego.speed_mps*3.6).toFixed(2)} km/h`:'미관측'}</p>
   <svg viewBox="0 0 360 80" role="img" aria-label={`${side?'B':'A'} ECU 제동 기록`}><path d={r.map((x,i)=>`${i?'L':'M'}${i*360/Math.max(1,r.length-1)},${70-x.reply.control.brake*60}`).join(' ')} fill="none" stroke={side?'#cb6e27':'#268878'} strokeWidth="2"/><line x1={index*360/Math.max(1,r.length-1)} x2={index*360/Math.max(1,r.length-1)} y1="0" y2="80" stroke="#6574a0"/></svg><small>동일 50ms 입력 시간축 · 수신 Brake 출력 0–100%</small>
  </article>;})}</div>
  <p>감시 기준: {stream?'abort 또는 미복구 프레임 상태':link.boundary==='power'?`전압 최솟값 < ${p.power_min_v}V`:`전압 구간이 ${p.analog_min_v}–${p.analog_max_v}V 밖`} · 마지막 이상 뒤 {p.min_hold_ms}ms 이상 보호 및 정상 표본 {p.recovery_samples}개 확인.</p>
  <div className="vecu-toolbar">{rows[1]?.flatMap((row:D)=>row.reply.bridge.events??[]).filter((e:D)=>(e.kind==='detection'&&e.reason==='model_'+link.boundary)||(e.kind==='model_recovered'&&e.measurement?.boundary===link.boundary)).map((e:D,i:number)=><button key={i} onClick={()=>onStep(Math.min(rows[1].length-1,Math.floor(e.time_ms/50)))}>{e.kind==='detection'?'이 경계 이상 검출':e.aggregate_protect?'이 경계 회복 · 전체 제동 유지':'이 경계 회복 확인'} · {(e.time_ms/1000).toFixed(3)}s</button>)}</div>
  <details><summary>모델 출처·재생 경계·판정 기준</summary><pre>{JSON.stringify(link,null,2)}</pre></details>
 </section>;
}
