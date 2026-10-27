import {useEffect,useState} from 'react';
import {api} from '../../api';
import {verifiedArtifactBytes} from '../../pages/CarlaCameraFrame';
import VecuCameras from './VecuCameras';
import {modelNames,modelRecoveryEvents,modelDetectionEvents,modelRunComparisonError,modelProtectionLabel,modelReleaseCandidateEvents,modelReleaseCancellationEvents} from './hilModelComparison';
type D=Record<string,any>;
export default function HilModelRunComparison({project,run,rows,history,comparison,onComparison,step,onStep}:{project:string,run:D,rows:D[],history:D[],comparison:string,onComparison:(v:string)=>void,step:number,onStep:(v:number)=>void}){
 const [previous,setPrevious]=useState<D|null>(null),[oldRows,setOldRows]=useState<D[]>([]),[error,setError]=useState('');
 useEffect(()=>{const c=new AbortController();setPrevious(null);setOldRows([]);setError('');if(!comparison)return;
  void(async()=>{const old=await api<D>(`/hil-bridge/executions/${comparison}?project_id=${project}`);
   let reason=modelRunComparisonError(run,old);
   if(reason&&run.summary.model_link.policy?.release_qualification?.algorithm==='fresh-window-v2'&&!old.summary?.model_link?.policy?.release_qualification){
    const provenance=async(r:D)=>{const a=r.artifacts.find((a:D)=>a.name==='hil-bridge-provenance.json');if(!a)throw Error('보호 로직 비교의 출처가 없습니다.');return JSON.parse(new TextDecoder().decode(await verifiedArtifactBytes(r.id,a,16*1024*1024,c.signal)));};
    const [cp,pp]=await Promise.all([provenance(run),provenance(old)]);reason=modelRunComparisonError(run,old,cp,pp);
   }
   if(reason)throw Error(reason);
   const artifact=old.artifacts.find((a:D)=>a.name==='vecu-trace.json');if(!artifact)throw Error('비교 실행의 trace가 없습니다.');
   const trace=JSON.parse(new TextDecoder().decode(await verifiedArtifactBytes(old.id,artifact,128*1024*1024,c.signal)));
   if(trace.sides.faulted.length!==rows.length)throw Error('비교 실행의 입력 시간축 길이가 다릅니다.');
   if(!c.signal.aborted){setPrevious(old);setOldRows(trace.sides.faulted);}
  })().catch(e=>{if(!c.signal.aborted)setError(String(e));});return()=>c.abort();
 },[project,comparison,run.id]);
 const index=Math.max(0,Math.min(step,rows.length-1));
 return <section className="hil-model-observation"><h3>복합 고장 실행 비교 · A 이전 / B 현재</h3>
  <p>두 영상 모두 각 실행의 고장 측입니다. 같은 원본·seed·개별 보호 정책을 비교합니다. 보호 해제 로직이 바뀐 비교는 두 실행의 출처를 검증하고 변경 범위를 표시합니다.</p>
  <label>이전 연결 실행<select aria-label="모델 회복 순서 비교 실행" value={comparison} onChange={e=>onComparison(e.target.value)}><option value="">기존 실행 선택</option>{history.filter(r=>r.status==='completed'&&r.id!==run.id).map(r=><option key={r.id} value={r.id}>{r.name} · {r.id.slice(-6)}</option>)}</select></label>
  {error&&<p role="alert" className="vecu-error">{error}</p>}
  {previous&&<>{run.summary.model_link.policy?.release_qualification&&!previous.summary.model_link.policy?.release_qualification&&<p role="status">보호 해제 로직 A/B · A 즉시 해제 / B 연속 두 완료 측정구간 확인 · 모델 감시 코드만 바뀌었는지 설치·artifact 출처 확인 완료</p>}<label>공통 입력 커서 {(index*.05).toFixed(2)}초<input aria-label="회복 순서 공통 커서" type="range" min="0" max={rows.length-1} value={index} onChange={e=>onStep(Number(e.target.value))}/></label>
  <VecuCameras run={run} comparisonRun={previous} step={index} onStep={onStep} rows={[oldRows[index],rows[index]]} labels={['A · 이전 복합 고장','B · 현재 복합 고장']} lastStep={rows.length-1}/>
  <div className="vecu-ab">{[[previous,oldRows],[run,rows]].map(([r,list]:any,i)=>{const row=list[index],m=row?.reply.bridge.model_monitor;return <article key={r.id}><h3>{i?'B · 현재':'A · 이전'} · {r.name}</h3><p>남은 보호: <b>{modelProtectionLabel(m)}</b></p>{Object.entries(m?.members??{}).map(([n,v]:any)=><p key={n}>{modelNames[n]??n}: {v.protect?'보호 유지':'보호 조건 없음'} · 복귀 확인 {v.fresh_samples}표본</p>)}<p>수신 CAN 제동 {(row?.reply.control.brake*100).toFixed(0)}%</p><svg viewBox="0 0 360 80" role="img" aria-label={`${i?'B':'A'} CAN 제동 기록`}><path d={list.map((x:D,j:number)=>`${j?'L':'M'}${j*360/Math.max(1,list.length-1)},${70-x.reply.control.brake*60}`).join(' ')} fill="none" stroke={i?'#cb6e27':'#268878'} strokeWidth="2"/><line x1={index*360/Math.max(1,list.length-1)} x2={index*360/Math.max(1,list.length-1)} y1="0" y2="80" stroke="#6574a0"/></svg><div className="vecu-toolbar">{[...modelDetectionEvents(list),...modelRecoveryEvents(list),...modelReleaseCandidateEvents(list),...modelReleaseCancellationEvents(list)].sort((a:D,b:D)=>a.time_ms-b.time_ms).map((e:D,j:number)=><button key={j} onClick={()=>onStep(e.input_step)}>{e.label} {(e.time_ms/1000).toFixed(3)}초{e.aggregate_protect?' · 전체 제동 유지':''}</button>)}</div><details><summary>실행·주입·출처</summary><pre>{JSON.stringify({execution:r.id,manifest:r.manifest_hash,model_link:r.summary.model_link},null,2)}</pre></details></article>;})}</div>
  <p>입력 커서·CARLA 모델 촬영 시각과 각 실행의 ECU 경과 시각은 별도로 읽습니다. 서로 다른 실행의 절대 시계를 빼서 물리 지연값으로 해석하지 않습니다.</p>
  </>}
 </section>;
}
