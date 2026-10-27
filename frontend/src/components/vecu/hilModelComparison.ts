type D=Record<string,any>;
const stable=(v:any):any=>Array.isArray(v)?v.map(stable):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,stable(v[k])])):v;
const equal=(a:any,b:any)=>JSON.stringify(stable(a))===JSON.stringify(stable(b));
export function modelRunComparisonError(current:D,previous:D,cp?:D,pp?:D):string|null{
 if(current.id===previous.id)return '서로 다른 실행을 선택하세요.';
 for(const r of [current,previous]){
  const s=r.summary;
  if(r.status!=='completed'||s?.verdict!=='passed'||s?.schema_version!=='hil-bridge-result/1'||s?.target!=='vcan'||s?.mode!=='carla_closed_loop'||s?.model_link?.boundary!=='multiple'||s.model_link.combination!=='any_protect'||!Array.isArray(s.model_link.members)||s.model_link.members.length<2)return '시간 조건을 통과한 복합 모델 CARLA 실행이 필요합니다.';
 }
 const a=current.summary,b=previous.summary;
 if(!current.fidelity||current.fidelity!==previous.fidelity)return '동일 fidelity 실행만 비교합니다.';
 if(!a.source_ref?.sha256||!equal(a.source_ref,b.source_ref)||!Number.isInteger(a.seed)||a.seed!==b.seed)return '같은 원본 시험 개정과 seed의 실행만 비교합니다.';
 const policy=(s:D)=>s.model_link.members.map((m:D)=>[m.boundary,m.start_ms,m.policy]).sort((x:any,y:any)=>x[0].localeCompare(y[0]));
 if(!equal(policy(a),policy(b)))return '같은 모델 경계·재생 시작·ECU 보호 정책이 필요합니다.';
 const upgrade=verifiedReleaseUpgrade(current,previous,cp,pp);
 if(!equal(a.model_link.policy?.release_qualification,b.model_link.policy?.release_qualification)&&!upgrade)return '보호 해제 로직 변경은 두 실행의 검증된 출처가 필요합니다.';
 if(!upgrade&&(!current.fidelity||current.fidelity!==previous.fidelity||!a.installation_sha256||a.installation_sha256!==b.installation_sha256||!a.adapter_sources||!b.adapter_sources||!equal(a.adapter_sources,b.adapter_sources)))return '같은 실행기·게이트웨이 설치·fidelity가 필요합니다.';
 return null;
}
export function cameraComparisonSources(run:D,previous?:D){
 return previous?[{subject:previous,side:'faulted'},{subject:run,side:'faulted'}]:[{subject:run,side:'baseline'},{subject:run,side:'faulted'}];
}
export const modelNames:Record<string,string>={power:'전원',fpga:'FPGA',camera_raw_csi:'RAW',analog:'아날로그'};
export function modelRecoveryEvents(rows:D[]){
 return rows.flatMap(row=>(row.reply?.bridge?.events??[]).filter((e:D)=>e.kind==='model_recovered'||e.kind==='model_group_recovered').map((e:D)=>({...e,input_step:row.step,label:e.kind==='model_group_recovered'?'전체 보호 해제':`${modelNames[e.measurement?.boundary]??e.measurement?.boundary} 보호 해제`,remaining:e.remaining_boundaries??[]})));
}

export function modelDetectionEvents(rows:D[]){
 const counts:Record<string,number>={};
 return rows.flatMap(row=>(row.reply?.bridge?.events??[]).filter((e:D)=>e.kind==='detection'&&e.reason?.startsWith('model_')).map((e:D)=>{
  const n=e.measurement?.boundary??e.reason.slice(6),count=counts[n]=(counts[n]??0)+1;
  return {...e,input_step:row.step,label:`${modelNames[n]??n} ${count>1?'재검출':'이상 검출'}`,occurrence:count};
 }));
}

const guardV2={algorithm:'fresh-window-v2',period_ms:50,fresh_windows:2,max_age_ms:10};
function verifiedReleaseUpgrade(current:D,previous:D,cp?:D,pp?:D){
 const a=current.summary,b=previous.summary;
 if(!cp||!pp||!equal(a.model_link.policy?.release_qualification,guardV2)||b.model_link.policy?.release_qualification)return false;
 const difference=(x:D,y:D)=>Object.keys({...x,...y}).filter(k=>x[k]!==y[k]).sort();
 if(!equal(difference(a.adapter_sources,b.adapter_sources),['model_monitor.py']))return false;
 for(const [r,p] of [[current,cp],[previous,pp]]){
  if(!equal(p.adapter_sources,r.summary.adapter_sources)||!equal(p.binding.model_link,r.summary.model_link)||
   p.installation?.installation_sha256!==r.summary.installation_sha256)return false;
 }
 const ci=cp.installation.installation,pi=pp.installation.installation;
 if(!ci?.files||!pi?.files||!equal({...ci,files:null},{...pi,files:null})||!equal(cp.installation.device,pp.installation.device))return false;
 return equal(difference(ci.files,pi.files),['bridge_model_monitor.py'])&&
  ci.files['bridge_model_monitor.py']===a.adapter_sources['model_monitor.py']&&pi.files['bridge_model_monitor.py']===b.adapter_sources['model_monitor.py'];
}
export function modelProtectionLabel(m?:D){
 if(!m)return '미관측';
 if(m.release_guard?.pending)return '해제 검증 대기 · 제동 유지';
 return m.active_boundaries?.map((n:string)=>modelNames[n]??n).join(', ')||'없음';
}
export function modelReleaseCandidateEvents(rows:D[]){
 let pending=false;
 return rows.flatMap(r=>{const g=r.reply?.bridge?.model_monitor?.release_guard,was=pending;pending=!!g?.pending;
  return pending&&!was?[{label:'전체 해제 검증 대기',time_ms:g.candidate?.time_ms??r.reply.bridge.observed_time_ms,input_step:r.step}]:[];
 });
}

export function modelReleaseCancellationEvents(rows:D[]){
 let previous:D|null=null;
 return rows.flatMap(r=>{
  const m=r.reply?.bridge?.model_monitor,g=m?.release_guard,old=previous;previous=g??null;
  if(!old?.pending||g?.pending||g?.reason!=='active_fault'||!m?.active_boundaries?.length)return [];
  return [{kind:'release_candidate_cancel_observed',label:'해제 후보 취소 관측',time_ms:r.reply.bridge.observed_time_ms,
   input_step:r.step,cancelled_candidate:old.candidate,active_boundaries:m.active_boundaries,
   evidence_source:'reference_process_state_transition',clock_basis:'gateway_state_readback'}];
 });
}
