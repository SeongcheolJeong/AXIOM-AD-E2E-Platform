import {useCallback,useEffect,useRef,useState} from 'react';
import {authHeaders} from '../../api';
import {CarlaCameraFrame,verifiedArtifactBytes,type CarlaCameraRecord} from '../../pages/CarlaCameraFrame';
import {sameValue} from '../object-scene/object-scene';
import {verifiedReceipt} from '../object-scene/load-object-scene';
import {createArtifactRangeReader,openCarlaStoredZip} from '../../pages/carla-zip';
import {cameraComparisonSources} from './hilModelComparison';
type D=Record<string,any>;
type CameraSource=CarlaCameraRecord&{time:number,executionId:string};
export default function VecuCameras({run,comparisonRun,step,onStep,rows=[],labels=['A · 정상','B · 고장 / 회복'],lastStep=359,period=.05}:{run:D;comparisonRun?:D;step:number;rows:D[];onStep:(n:number)=>void;labels?:string[];lastStep?:number;period?:number}){
 const gateway=run.summary?.schema_version==='hil-bridge-result/1';
 const stateName=(value:string)=>({tracking:'정상 추종',degraded:'보호 제동',recovering:'복구 확인 중',unobserved:'미관측'}[value]??value);
 const wallTime=(row:D)=>row?.vehicle_observed_monotonic_ns&&row?.reply?.bridge?.t0_monotonic_ns?`${((row.vehicle_observed_monotonic_ns-row.reply.bridge.t0_monotonic_ns)/1e9).toFixed(3)} s`:'미관측';
 const [records,setRecords]=useState<Record<string,CameraSource[]>>({}),[error,setError]=useState(''),[sensor,setSensor]=useState('chase'),[playing,setPlaying]=useState(false),[ready,setReady]=useState<Record<string,boolean>>({});
 const viewer=useRef<HTMLElement>(null);
 const cache=useRef(new Map<string,Uint8ClampedArray>());
 const report=useCallback((id:string,value:boolean)=>setReady(old=>old[id]===value?old:{...old,[id]:value}),[]);
 useEffect(()=>{const c=new AbortController();setRecords({});setError('');setPlaying(false);cache.current.clear();
  void(async()=>{const checked=new Set<string>();
   const result:Record<string,CameraSource[]>={};
   const sources=cameraComparisonSources(run,comparisonRun);
   for(const [slot,{subject,side}] of sources.entries()){
    if(!checked.has(subject.id)){const receipt:any=await verifiedReceipt(subject as any,c.signal,32*1024*1024);if(receipt.execution.id!==subject.id||receipt.execution.summary.mode!=='carla_closed_loop'||!sameValue(receipt.execution.artifacts,subject.artifacts))throw Error('CARLA 실행 근거 불일치');checked.add(subject.id);}
    const art=subject.artifacts.find((a:D)=>a.name===side+'-city-scene.json');if(!art)throw Error('영상 scene 누락');
    const scene=JSON.parse(new TextDecoder().decode(await verifiedArtifactBytes(subject.id,art,64*1024*1024,c.signal)));
    const archives=new Map();
    for(const name of new Set<string>(scene.camera_records.map((r:D)=>r.archive))){const a=subject.artifacts.find((a:D)=>a.name===side+'-'+name);if(!a)throw Error('영상 archive 누락');const zip=await openCarlaStoredZip(a.size_bytes,createArtifactRangeReader(subject.id,a,authHeaders()),c.signal,'city-camera');zip.assertClosure?.(scene.camera_records.filter((r:D)=>r.archive===name).map((r:D)=>({name:r.member,size_bytes:r.size_bytes,sha256:r.sha256})));archives.set(name,{a,zip});}
    result[String(slot)]=scene.camera_records.map((r:D)=>{const {a,zip}=archives.get(r.archive);return {index:r.index,frame:r.frame,time:r.time_s,timestampNs:String(Math.round(r.simulation_time_s*1e9)),timestampSeconds:r.simulation_time_s,width:r.width,height:r.height,key:String(slot)+'-'+r.sensor_id,executionId:subject.id,artifact:a,encoding:'bgra8',cacheKey:subject.id+side+r.member,archive:zip,member:{name:r.member,size_bytes:r.size_bytes,sha256:r.sha256}};});
   }if(!c.signal.aborted)setRecords(result);
  })().catch(e=>{if(!c.signal.aborted)setError(String(e));});return()=>c.abort();
 },[run.id,comparisonRun?.id]);
 const selected=['0','1'].map(side=>records[side]?.filter(r=>r.key===side+'-'+sensor).reduce<CameraSource|undefined>((best,r)=>Math.abs(r.time-(step+1)*period)<Math.abs((best?.time??Infinity)-(step+1)*period)?r:best,undefined));
 const both=selected.every(r=>r&&ready[r.cacheKey]);
 useEffect(()=>{if(!playing||!both)return;const t=setTimeout(()=>{if(step>=lastStep)setPlaying(false);else onStep(step+1);},100);return()=>clearTimeout(t);},[playing,both,step,onStep,lastStep]);
 return <section className="vecu-cameras" ref={viewer}><h3>{comparisonRun?'CARLA 복합 고장 실행 A/B':'CARLA 차량 영상 A/B'} · 저장 결과 동기 재생</h3><p>{gateway?'모델 입력':'ECU 요청'} {(step*period).toFixed(2)} s → {gateway?'CARLA 시뮬레이션 응답':'차량 응답 관측'} {((step+1)*period).toFixed(2)} s · 영상 샘플은 가장 가까운 보존 시각{gateway?' · 실제 경과 시간은 아래에 별도 표시':''}</p><div className="vecu-toolbar"><button onClick={()=>setPlaying(!playing)} disabled={!!error||!selected.every(Boolean)}>{playing?'영상 일시정지':'영상 A/B 재생'}</button><select aria-label="vECU camera" value={sensor} onChange={e=>setSensor(e.target.value)}><option value="chase">차량 추적</option><option value="front">전방 카메라</option></select><button onClick={()=>void viewer.current?.requestFullscreen().catch(e=>setError(String(e)))}>영상 크게 보기</button><small>0.5× 이하 · 두 영상 로딩 완료 후 진행</small></div>{error&&<p role="alert">{error}</p>}<div className="vecu-ab">{selected.map((r,i)=><article key={i}><h3>{labels[i]}</h3>{r?<><CarlaCameraFrame executionId={r.executionId} record={r} cache={cache.current} onReady={report}/><small>영상 촬영 {r.time.toFixed(2)} s · frame {r.frame} · 차량 응답 관측과 {Math.round((r.time-(step+1)*period)*1000)} ms 차이</small><p>선택 입력 {(step*period).toFixed(2)} s의 ECU 상태 · <b>{stateName(rows[i]?.reply.state)}</b> · 입력 age {rows[i]?.reply.object_age_s==null||rows[i]?.reply.network&&rows[i]?.reply.object_sequence<0?'—':`${(rows[i]?.reply.object_age_s*1000).toFixed(0)} ms`} · 제동 {(rows[i]?.reply.control.brake*100).toFixed(0)}%</p><p>{gateway?'CARLA 모델 시각':'차량 응답'} {((step+1)*period).toFixed(2)} s · 다음 속도 {rows[i]?.next_ego?.speed_mps.toFixed(3)} m/s</p>{gateway&&<p>실제 경과 · 게이트웨이 응답 {rows[i]?.reply.bridge.observed_time_ms!=null?`${(rows[i].reply.bridge.observed_time_ms/1000).toFixed(3)} s`:'미관측'} → CARLA readback {wallTime(rows[i])}</p>}<small>중복 검출 {rows[i]?.reply.duplicate_packet_count??'미기록'} · 미수신 {rows[i]?.reply.missing_packet_count??'미기록'}</small></>:<p>검증된 영상 읽는 중…</p>}</article>)}</div></section>;
}
