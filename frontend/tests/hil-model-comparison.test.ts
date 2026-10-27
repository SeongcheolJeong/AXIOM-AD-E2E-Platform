import test from 'node:test';
import assert from 'node:assert/strict';
import {cameraComparisonSources,modelRecoveryEvents,modelDetectionEvents,modelRunComparisonError,modelProtectionLabel,modelReleaseCandidateEvents,modelReleaseCancellationEvents} from '../src/components/vecu/hilModelComparison.ts';
const make=(id:string)=>({id,status:'completed',fidelity:'functional',summary:{schema_version:'hil-bridge-result/1',verdict:'passed',target:'vcan',mode:'carla_closed_loop',source_ref:{id:'source',revision:1,sha256:'a'.repeat(64)},seed:42,installation_sha256:'b'.repeat(64),adapter_sources:{runner:'c'},model_link:{boundary:'multiple',combination:'any_protect',members:[{boundary:'power',start_ms:3000,policy:{min_hold_ms:500},injection:{start_ms:3003,end_ms:3007}},{boundary:'fpga',start_ms:3000,policy:{min_hold_ms:500},injection:{start_ms:3005,end_ms:3305}}]}}});
test('same scenario and policy can compare changed native fault durations without changing A/B meaning',()=>{
 const a=make('new'),b=make('old');a.summary.model_link.members[0].injection.end_ms=3303;a.summary.model_link.members[1].injection.end_ms=3005.1;
 assert.equal(modelRunComparisonError(a,b),null);
 assert.match(modelRunComparisonError(a,a)!,/서로 다른/);
});
test('unlike sources, incomplete timing, policy or engine differences are rejected',()=>{
 const current=make('new');
 const variants=[make('old'),make('old'),make('old'),make('old'),make('old'),make('old')];
 variants[0].summary.source_ref.revision=2;variants[1].summary.seed=43;variants[2].summary.verdict='timing_invalid';variants[3].summary.model_link.members[0].policy.min_hold_ms=1000;variants[4].summary.installation_sha256='other';variants[5].summary.adapter_sources.runner='other';
 assert.ok(variants.every(v=>modelRunComparisonError(current,v)!==null));
});
test('camera comparison retains separate execution provenance and uses both faulted sides',()=>{
 const current=make('new'),old=make('old');
 assert.deepEqual(cameraComparisonSources(current).map(v=>[v.subject.id,v.side]),[['new','baseline'],['new','faulted']]);
 assert.deepEqual(cameraComparisonSources(current,old).map(v=>[v.subject.id,v.side]),[['old','faulted'],['new','faulted']]);
});
test('individual recovery does not imply all-clear and seeks its observed input row',()=>{
 const rows=[{step:72,reply:{bridge:{events:[{kind:'model_recovered',time_ms:3605,measurement:{boundary:'fpga'},aggregate_protect:true,remaining_boundaries:['power']}]}}},{step:77,reply:{bridge:{events:[{kind:'model_group_recovered',time_ms:3855,measurement:{boundary:'multiple'}}]}}}];
 const events=modelRecoveryEvents(rows);
 assert.equal(events[0].label,'FPGA 보호 해제');assert.equal(events[0].aggregate_protect,true);assert.deepEqual(events[0].remaining,['power']);assert.equal(events[0].input_step,72);
 assert.equal(events[1].label,'전체 보호 해제');assert.equal(events[1].input_step,77);
});

test('recurrent detection remains separate from both member recoveries and seeks its observed step',()=>{
 const rows=[{step:61,reply:{bridge:{events:[{kind:'detection',reason:'model_fpga',time_ms:3055,measurement:{boundary:'fpga'}}]}}},
 {step:77,reply:{bridge:{events:[{kind:'detection',reason:'model_fpga',time_ms:3855,measurement:{boundary:'fpga'}},{kind:'model_recovered',time_ms:3855.1,measurement:{boundary:'power'},aggregate_protect:true}]}}}];
 const events=modelDetectionEvents(rows);
 assert.equal(events[0].label,'FPGA 이상 검출');assert.equal(events[1].label,'FPGA 재검출');
 assert.equal(events[1].input_step,77);assert.equal(events[1].occurrence,2);
 assert.equal(modelRecoveryEvents(rows)[0].aggregate_protect,true);
});

test('release-logic comparison requires sealed installation provenance and only a monitor change',()=>{
 const old:any=make('old'),current:any=make('new');
 old.summary.model_link.policy={boundary:'multiple'};
 current.summary.model_link.policy={boundary:'multiple',release_qualification:{algorithm:'fresh-window-v2',period_ms:50,fresh_windows:2,max_age_ms:10}};
 old.summary.adapter_sources={'runtime.py':'same','model_monitor.py':'old'};
 current.summary.adapter_sources={'runtime.py':'same','model_monitor.py':'new'};
 current.summary.installation_sha256='new-install';
 const provenance=(r:any)=>({adapter_sources:r.summary.adapter_sources,binding:{model_link:r.summary.model_link},installation:{installation_sha256:r.summary.installation_sha256,device:{can_a:'a',can_b:'b'},installation:{schema_version:'hil-gateway-install/1',files:{'bridge_runtime.py':'same','bridge_model_monitor.py':r.summary.adapter_sources['model_monitor.py']}}}});
 const cp=provenance(current),pp=provenance(old);
 assert.notEqual(modelRunComparisonError(current,old),null);
 assert.equal(modelRunComparisonError(current,old,cp,pp),null);
 const forged=structuredClone(cp);forged.installation.installation.files['bridge_runtime.py']='other';
 assert.notEqual(modelRunComparisonError(current,old,forged,pp),null);
 const changed=structuredClone(current);changed.summary.adapter_sources['runtime.py']='other';
 assert.notEqual(modelRunComparisonError(changed,old,provenance(changed),pp),null);
 const different=structuredClone(current);different.fidelity='physical';
 assert.notEqual(modelRunComparisonError(different,old,cp,pp),null);
});

test('aggregate release qualification remains visible when individual faults are already clear',()=>{
 const monitor={active_boundaries:[],protect:true,release_guard:{pending:true,candidate:{time_ms:4355}}};
 assert.equal(modelProtectionLabel(monitor),'해제 검증 대기 · 제동 유지');
 const rows=[{step:87,reply:{bridge:{model_monitor:monitor}}},{step:88,reply:{bridge:{model_monitor:monitor}}},
  {step:89,reply:{bridge:{model_monitor:{...monitor,release_guard:{pending:false}}}}}];
 assert.equal(modelReleaseCandidateEvents(rows).length,1);
 assert.equal(modelReleaseCandidateEvents(rows)[0].input_step,87);
});

test('release candidate cancellation is an observed state transition with its own readback time',()=>{
 const candidate={sequence:104,required_sequence:106,time_ms:5205};
 const rows=[{step:105,reply:{bridge:{observed_time_ms:5263,model_monitor:{release_guard:{pending:true,candidate},active_boundaries:[]}}}},
  {step:106,reply:{bridge:{observed_time_ms:5313,model_monitor:{release_guard:{pending:false,candidate:null,reason:'active_fault'},active_boundaries:['fpga']}}}}];
 const events=modelReleaseCancellationEvents(rows);
 assert.equal(events.length,1);assert.equal(events[0].input_step,106);assert.equal(events[0].time_ms,5313);
 assert.equal(events[0].clock_basis,'gateway_state_readback');assert.deepEqual(events[0].cancelled_candidate,candidate);
 assert.deepEqual(events[0].active_boundaries,['fpga']);
 assert.equal(modelReleaseCancellationEvents([rows[0],{step:106,reply:{bridge:{model_monitor:{release_guard:{pending:false,reason:'fresh_window_confirmed'},active_boundaries:[]}}}}]).length,0);
});
