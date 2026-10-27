import type {Doc} from './TracePlot';

type BuildResult={run:Doc;summary:Doc|null;provenance:Doc|null};
const finite=(v:unknown):v is number=>typeof v==='number'&&Number.isFinite(v);
export const buildNumber=(v:unknown,digits=3)=>finite(v)?v.toFixed(digits):'—';
const signed=(v:number,digits=3)=>`${v>0?'+':''}${v.toFixed(digits)}`;
const preserved=(v:unknown)=>v!==undefined&&v!==null&&v!=='';
const status=(s:Doc)=>s.timing_met===true&&s.verdict==='passed'?'목표 타이밍 충족':s.timing_met===false?'목표 타이밍 미충족':'미평가 또는 빌드 실패';
function snapshot(result:BuildResult){
 const s=result.summary??{},p=result.provenance??{},identity=p.tool_identity??{},profile=identity.profile??{};
 const target=s.target_clock_hz??profile.target_clock_hz;
 return {s,profile,identity,target,seed:s.seed??p.seed,profileId:s.build_profile_id??p.build_profile_id??profile.id,
  fmax:s.fmax_mhz,margin:finite(s.fmax_mhz)&&finite(target)?s.fmax_mhz-target/1e6:null,
  design:s.design_hash??p.design_hash,config:s.config_hash??p.config_hash,ref:s.test_ref??p.test_ref};
}
function equivalence(a:unknown,b:unknown){return !preserved(a)||!preserved(b)?'미보존':a===b?'동일':'다름';}
export default function BuildComparison({a,b,onExport}:{a:BuildResult;b:BuildResult;onExport:(side:'A'|'B')=>void}){
 const A=snapshot(a),B=snapshot(b),sameRun=a.run.id===b.run.id;
 const checks=[['빌드 프로필 ID',A.profileId,B.profileId],['프로필 SHA-256',A.profile.sha256,B.profile.sha256],['목표 clock',A.target,B.target],['배치배선 seed',A.seed,B.seed]];
 const comparable=checks.every(([,left,right])=>equivalence(left,right)==='동일');
 const sourceA=A.identity.source_sha256??{},sourceB=B.identity.source_sha256??{},sourceNames=[...new Set([...Object.keys(sourceA),...Object.keys(sourceB)])].sort();
 const changedSources=sourceNames.filter(name=>sourceA[name]!==sourceB[name]);
 const toolNames=[...new Set([...Object.keys(A.identity.tool_sha256??{}),...Object.keys(B.identity.tool_sha256??{})])].sort();
 const toolState=toolNames.length?toolNames.every(name=>equivalence(A.identity.tool_sha256?.[name],B.identity.tool_sha256?.[name])==='동일')?'동일':'다름 또는 일부 미보존':'미보존';
 const numberRow=(label:string,left:unknown,right:unknown,digits=0)=>({label,left:buildNumber(left,digits),right:buildNumber(right,digits),difference:finite(left)&&finite(right)?signed(left-right,digits):'—'});
 const textRow=(label:string,left:unknown,right:unknown)=>({label,left:preserved(left)?String(left):'미보존',right:preserved(right)?String(right):'미보존',difference:equivalence(left,right)});
 const rows=[textRow('타이밍 판정',status(A.s),status(B.s)),numberRow('목표 clock · MHz',finite(A.target)?A.target/1e6:null,finite(B.target)?B.target/1e6:null,3),
  numberRow('Fmax · MHz',A.fmax,B.fmax,3),numberRow('주파수 여유 · MHz',A.margin,B.margin,3),
  ...['lut','ff','bram','dsp'].map(key=>numberRow(key.toUpperCase(),A.s.resources?.[key],B.s.resources?.[key])),
  textRow('배치배선 seed',A.seed,B.seed),textRow('빌드 프로필',A.profileId,B.profileId),textRow('프로필 SHA-256',A.profile.sha256,B.profile.sha256),
  textRow('디바이스',A.profile.device,B.profile.device),textRow('패키지 / speed',A.profile.package?`${A.profile.package} / ${A.profile.speed??'미보존'}`:null,B.profile.package?`${B.profile.package} / ${B.profile.speed??'미보존'}`:null),
  textRow('생성 RTL SHA-256',A.design,B.design),textRow('설정 SHA-256',A.config,B.config),
  textRow('저장 시험 개정',A.ref?`${A.ref.id} · r${A.ref.revision}`:null,B.ref?`${B.ref.id} · r${B.ref.revision}`:null)];
 return <section className="module-build-comparison" aria-label="ADAS 빌드 A/B 비교">
  <div className="eng-result-heading"><div><small>RETAINED OOC BUILD COMPARISON</small><h2>ADAS 빌드 A/B 비교</h2><p>A: {a.run.id}<br/>B: {b.run.id}</p></div><span className={comparable&&!sameRun?'eng-pass':'eng-observed'}>{sameRun?'동일 실행 선택':comparable?'프로필·목표·seed 동일':'비교 조건 확인 필요'}</span></div>
  <p className="eng-result-boundary">두 개의 저장된 합성·배치배선 결과를 비교합니다. 실제 보드 동작, PHY, 핀 연결과 물리 HIL 판정은 포함하지 않습니다. 픽셀 실행 A/B와 별도 비교입니다.</p>
  <div className="module-build-checks">{checks.map(([label,left,right])=><span key={String(label)}><b>{String(label)}</b> {equivalence(left,right)}</span>)}<span><b>빌드 도구 해시</b> {toolState}</span></div>
  {(!comparable||toolState!=='동일')&&<p className="module-history-warning">조건이 다르거나 일부 식별 정보가 없어 성능 차이를 RTL 변경 하나의 효과로 확정할 수 없습니다. 아래에 각각의 저장값을 표시합니다.</p>}
  {sameRun&&<p className="module-history-warning">같은 실행을 A와 B에 선택했습니다. 전후 변화를 확인하려면 서로 다른 빌드를 선택하세요.</p>}
  <p className="eng-muted">차이는 A − B입니다. 주파수 여유는 Fmax − 목표 clock으로 계산하며 경로별 slack 측정값이 아닙니다. 음수는 목표 주파수 미달을 뜻합니다.</p>
  <div className="module-table module-build-table"><table><thead><tr><th>항목</th><th>빌드 A</th><th>빌드 B</th><th>A − B / 동일 여부</th></tr></thead><tbody>{rows.map(row=><tr key={row.label}><th scope="row">{row.label}</th><td>{row.left}</td><td>{row.right}</td><td>{row.difference}</td></tr>)}</tbody></table></div>
  <details><summary>실행기·RTL 원본 파일 해시 · 변경 {changedSources.length}개 / 보존 {sourceNames.length}개</summary><p>생성 RTL 해시는 위 표의 설계 식별자입니다. 아래에는 실행기와 원본 RTL 파일별 해시를 함께 보존합니다.</p><div className="module-table module-build-table"><table><thead><tr><th>원본 파일</th><th>A SHA-256</th><th>B SHA-256</th><th>상태</th></tr></thead><tbody>{sourceNames.map(name=><tr key={name}><th scope="row">{name}</th><td>{sourceA[name]??'미보존'}</td><td>{sourceB[name]??'미보존'}</td><td>{equivalence(sourceA[name],sourceB[name])}</td></tr>)}</tbody></table></div></details>
  <div className="eng-toolbar"><button onClick={()=>onExport('A')}>빌드 A 근거 내보내기</button><button onClick={()=>onExport('B')}>빌드 B 근거 내보내기</button></div>
  <details><summary>두 빌드의 보존 메타데이터</summary><pre>{JSON.stringify({A:{summary:a.summary,provenance:a.provenance},B:{summary:b.summary,provenance:b.provenance}},null,2)}</pre></details>
 </section>;
}
