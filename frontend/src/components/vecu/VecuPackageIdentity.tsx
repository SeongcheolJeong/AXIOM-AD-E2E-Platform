type Doc=Record<string,any>;
const scalar=(value:any):string=>typeof value==='string'||typeof value==='number'?String(value):'';
export function publicRuntime(runtime:Doc={}){
 const compiler=runtime.compiler,platform=runtime.target_platform;
 return {
  source_id:runtime.source_id,policy_id:runtime.policy_id,dut_version:runtime.dut_version,
  protocol:runtime.protocol,runtime_sha256:runtime.runtime_sha256,binary_sha256:runtime.binary_sha256,
  source_sha256:runtime.source_sha256,
  compiler:typeof compiler==='string'?compiler:compiler?{name:compiler.name,version:typeof compiler.version==='string'?compiler.version.split('\n')[0]:compiler.version,sha256:compiler.sha256}:undefined,
  target_platform:typeof platform==='string'?platform:platform?{system:platform.system??platform.os,machine:platform.machine??platform.arch,platform:platform.platform,architecture:platform.architecture}:undefined,
 };
}
export default function VecuPackageIdentity({label,pkg}:{label:string;pkg?:Doc}){
 const runtime=publicRuntime(pkg?.runtime),identity=pkg?.ref??pkg;
 return <article className="vecu-package-identity"><h3>{label}</h3><p>{pkg?.name??runtime.source_id??runtime.dut_version??'실행 패키지'}{runtime.policy_id&&<> · {runtime.policy_id}</>}</p><dl><dt>저장 개정</dt><dd>{identity?.id?`${identity.id} · r${identity.revision}`:'미보존'}</dd><dt>패키지 SHA-256</dt><dd>{identity?.sha256??'미보존'}</dd><dt>실행 파일 SHA-256</dt><dd>{runtime.binary_sha256??'기존 등록 런타임 · 개별 실행 파일 해시는 원본 근거 참조'}</dd><dt>런타임 SHA-256</dt><dd>{runtime.runtime_sha256??'미보존'}</dd><dt>컴파일러 / 플랫폼</dt><dd>{typeof runtime.compiler==='object'?runtime.compiler.version??runtime.compiler.name:scalar(runtime.compiler)||'미보존'} / {typeof runtime.target_platform==='object'?[runtime.target_platform.system??runtime.target_platform.platform,runtime.target_platform.machine??runtime.target_platform.architecture].filter(Boolean).join(' '):scalar(runtime.target_platform)||'미보존'}</dd></dl><details><summary>공개 빌드 식별 정보</summary><pre>{JSON.stringify(runtime,null,2)}</pre></details></article>;
}
