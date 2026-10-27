import {useMemo,useState} from 'react';
type Doc=Record<string,any>;
type Event={side:string;kind:string;label:string;index:number;time:number};
const names:Record<string,string>={fault_start:'고장 시작',fault_end:'고장 종료',brake:'제동 시작',full_brake:'최대 제동',stop:'정지 관측',recovering:'회복 시작',recovered:'추적 복귀'};
export const rowTime=(r:Doc|undefined,index=0)=>typeof r?.time_ns==='number'?r.time_ns/1e9:typeof r?.request?.time_s==='number'?r.request.time_s:index*.05;
const numberText=(value:unknown,digits=2)=>typeof value==='number'&&Number.isFinite(value)?value.toFixed(digits):'—';
export const faultActive=(r:Doc|undefined)=>r?.injection?.active??!!(r?.injection?.kind&&r.injection.kind!=='none');
function observedEvents(rows:Doc[],side:string,period:number):Event[]{
 const events:Event[]=[];
 for(let i=0;i<rows.length;i++){
  const row=rows[i],previous=rows[i-1],now=rowTime(row,i),state=row.reply?.state,oldState=previous?.reply?.state;
  const add=(kind:string,time=now,label=names[kind])=>events.push({side,kind,label,index:i,time});
  if(Array.isArray(row.injection?.faults)){
   const byKey=(r:Doc|undefined)=>new Map<string,Doc>((r?.injection?.faults??[]).map((f:Doc)=>[`${f.kind}:${f.start_ms}:${f.end_ms}`,f]));
   const current=byKey(row),before=byKey(previous);
   for(const [key,fault] of current)if(!before.has(key))add('fault_start',now,`${names.fault_start} · ${fault.kind}`);
   for(const [key,fault] of before)if(!current.has(key))add('fault_end',now,`${names.fault_end} · ${fault.kind}`);
  }else{
   if(faultActive(row)&&!faultActive(previous))add('fault_start');if(!faultActive(row)&&faultActive(previous))add('fault_end');
  }
  if(row.reply?.control?.brake>.01&&!(previous?.reply?.control?.brake>.01))add('brake');
  if(row.reply?.control?.brake>=.999&&!(previous?.reply?.control?.brake>=.999))add('full_brake');
  const before=previous?.next_ego?.speed_mps??row.request?.ego?.speed_mps;
  if(typeof row.next_ego?.speed_mps==='number'&&row.next_ego.speed_mps<.1&&before>=.1)add('stop',now+period);
  if(state==='recovering'&&oldState!=='recovering')add('recovering');
  if(state==='tracking'&&['recovering','degraded'].includes(oldState))add('recovered');
 }
 return events;
}
function measure(rows:Doc[],period:number,closed:boolean,truth:Doc[]=[]){
 const fault=rows.findIndex(faultActive),start=fault>=0?fault:0,degraded=rows.findIndex((r,i)=>i>=start&&r.reply?.state==='degraded');
 const full=rows.findIndex((r,i)=>i>=Math.max(start,degraded)&&r.reply?.control?.brake>=.999);
 const stop=closed?rows.findIndex((r,i)=>i>=Math.max(start,degraded)&&r.next_ego?.speed_mps<.1):-1;
 const recovering=rows.findIndex((r,i)=>i>=start&&r.reply?.state==='recovering');
 const recovered=rows.findIndex((r,i)=>i>Math.max(start,degraded,recovering)&&r.reply?.state==='tracking');
 const hasXY=(p:Doc|undefined)=>typeof p?.x==='number'&&Number.isFinite(p.x)&&typeof p?.y==='number'&&Number.isFinite(p.y);
 const pathRows=degraded>=0&&stop>=degraded?rows.slice(degraded,stop+1):[];
 const distance=!closed?'고정 입력 · 해당 없음':fault<0?'주입 없음':!pathRows.length?'degraded 이후 정지 미관측':pathRows.every(r=>hasXY(r.request?.ego)&&hasXY(r.next_ego))?`${pathRows.reduce((total,r)=>total+Math.hypot(r.next_ego.x-r.request.ego.x,r.next_ego.y-r.request.ego.y),0).toFixed(3)} m`:'위치 근거 미보존';
 const gaps=truth.flatMap(r=>{const lead=r.generated_objects?.find((o:Doc)=>o.id===(r.next_lead?.id??'lead'));return hasXY(r.request?.ego)&&hasXY(lead)?[Math.hypot(lead.x-r.request.ego.x,lead.y-r.request.ego.y)]:[];});
 const gap=!closed?'고정 입력 · 해당 없음':gaps.length?`${Math.min(...gaps).toFixed(3)} m`:'동시 생성 객체 근거 미보존';
 const at=(i:number,offset=0)=>i<0?'미관측':`${(rowTime(rows[i],i)+offset).toFixed(2)} s`;
 return {distance,gap,fault:fault<0?'주입 없음':at(fault),degraded:at(degraded),full:at(full),stop:closed?at(stop,period):'고정 입력 · 차량 반응 없음',recovered:degraded<0&&recovering<0?'전환 없음':at(recovered),ramp:degraded>=0&&full>=0?`${Math.round((rowTime(rows[full],full)-rowTime(rows[degraded],degraded))*1000)} ms`:'미관측',ipc:rows.length?`${Math.max(...rows.map(r=>r.wall_latency_ms??0)).toFixed(3)} ms`:'미보존'};
}
export default function VecuEvents({a,b,index,onStep,period=.05,closed,truthRows=[[],[]]}:{a:Doc[];b:Doc[];index:number;onStep:(index:number)=>void;period?:number;closed:boolean;truthRows?:Doc[][]}){
 const [kind,setKind]=useState('all');
 const events=useMemo(()=>[...observedEvents(a,'A',period),...observedEvents(b,'B',period)].sort((x,y)=>x.time-y.time||x.side.localeCompare(y.side)),[a,b,period]);
 const filtered=events.filter(e=>kind==='all'||e.kind===kind),metrics=[measure(a,period,closed,truthRows[0]),measure(b,period,closed,truthRows[1])];
 return <section className="vecu-events"><header><h3>보존 사건으로 이동</h3><label>사건 종류<select aria-label="vECU event filter" value={kind} onChange={e=>setKind(e.target.value)}><option value="all">모든 사건</option>{Object.entries(names).map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label></header><p>버튼은 저장 결과의 공통 커서를 이동합니다. 실제 실행을 step하거나 제어 명령을 보내지 않습니다.</p><div className="vecu-event-list">{filtered.slice(0,80).map((event,i)=><button key={`${event.side}-${event.kind}-${event.index}-${i}`} className={`${event.side==='A'?'side-a':'side-b'} ${event.index===index?'selected':''}`} onClick={()=>onStep(event.index)}>{event.side} · {event.label}<b>{event.time.toFixed(2)} s</b></button>)}{!filtered.length&&<span>선택한 사건이 이 실행에서 관측되지 않았습니다.</span>}</div>{filtered.length>80&&<small>첫 80개 표시 · 전체 사건 {filtered.length}개</small>}
 <div className="vecu-measured-table"><table><thead><tr><th>저장 trace에서 계산한 관측값</th><th>A</th><th>B</th></tr></thead><tbody>{[['fault','첫 고장 시작'],['degraded','첫 degraded'],['full','이후 최대 제동'],['ramp','degraded → 최대 제동 간격'],['stop','이후 정지 관측'],['distance','degraded 이후 CARLA 이동거리'],['gap','차량 원점 간 최소거리 · ego–lead'],['recovered','추적 복귀'],['ipc','최대 IPC wall latency']].map(([id,label])=><tr key={id}><th scope="row">{label}</th>{metrics.map((m,i)=><td key={i}>{m[id as keyof typeof m]}</td>)}</tr>)}</tbody></table></div><p className="vecu-measure-scope">고장이 있으면 첫 고장 이후, 없으면 실행 시작 이후를 관찰합니다. 정지는 CARLA 제어 적용 후 속도 &lt; 0.1 m/s 관측이며 물리 제동거리·안전성 판정이 아닙니다. 이동거리는 첫 degraded부터 첫 정지까지 ego XY 이동량의 합입니다. 최소거리는 전체 실행의 원본 HIL trace에서 동시 생성 객체 위치를 사용하며 차량 외곽 간격이 아닙니다. IPC wall latency는 호스트 처리 시간으로 실시간 보장을 의미하지 않습니다.</p></section>;
}

export function VecuSignalGraph({rows,closed,period=.05}:{rows:Doc[];closed:boolean;period?:number}){
 return <section className="vecu-signal-graph" aria-label="vECU saved signal flow"><h3>선택 시점의 저장 신호 경로 · 읽기 전용</h3>{rows.map((row,i)=><div className="vecu-signal-side" key={i}><b>{i?'B':'A'}</b><div><small>객체·차량 입력</small><strong>seq {row.request?.object_packet?.sequence??'—'}</strong><span>요청 {rowTime(row).toFixed(2)} s · ego {numberText(row.request?.ego?.speed_mps)} m/s</span></div><i>→</i><div className={faultActive(row)?'fault-active':''}><small>고장 주입 경계</small><strong>{faultActive(row)?row.injection.kind:'고장 비활성'}</strong><span>입력 age {numberText(row.reply?.object_age_s*1000,0)} ms</span></div><i>→</i><div><small>별도 프로세스 ECU</small><strong>{row.reply?.state}</strong><span>{row.reply?.policy_phase??'정책 phase 미보존'}{typeof row.reply?.phase_elapsed_s==='number'?` · ${Math.round(row.reply.phase_elapsed_s*1000)} ms`:''}</span></div><i>→</i><div><small>{closed?'CARLA 제어 적용 후':'고정 입력 · 명령 기록'}</small><strong>Brake {Math.round((row.reply?.control?.brake??0)*100)}%</strong><span>{closed?`${(rowTime(row)+period).toFixed(2)} s · ${numberText(row.next_ego?.speed_mps)} m/s`:'다음 ego 입력에 반영하지 않음'}</span></div></div>)}{closed&&<p>CARLA의 다음 관측 상태가 다음 ECU 입력으로 이어진 저장 폐루프입니다. 카메라 영상은 인지 입력으로 사용되지 않습니다.</p>}</section>;
}
