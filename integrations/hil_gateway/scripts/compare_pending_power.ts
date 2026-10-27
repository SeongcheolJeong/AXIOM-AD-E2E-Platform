import fs from 'node:fs';
import assert from 'node:assert/strict';
import {modelDetectionEvents,modelRecoveryEvents,modelReleaseCancellationEvents,modelRunComparisonError} from '../../../frontend/src/components/vecu/hilModelComparison.ts';
const previous='docs/validation/hil-pending-fpga-20261018',root='docs/validation/hil-pending-power-20261018';
const read=(p:string)=>JSON.parse(fs.readFileSync(p,'utf8'));
const old=read(previous+'/result.json'),current=read(root+'/result.json');
assert.equal(modelRunComparisonError(current,old,read(root+'/hil-bridge-provenance.json'),read(previous+'/hil-bridge-provenance.json')),null);
const a=read(previous+'/vecu-trace.json'),b=read(root+'/vecu-trace.json');
const controls:Record<string,any>={};
for(const side of ['baseline','faulted']){
 const differences=a.sides[side].flatMap((r:any,i:number)=>JSON.stringify(r.reply.control)===JSON.stringify(b.sides[side][i].reply.control)?[]:[i]);
 if(side==='baseline')assert.equal(differences.length,0);else {assert.ok(differences.length>0);assert.ok(differences.every((i:number)=>i>=118));}
 controls[side]={compared:a.sides[side].length,different_steps:differences};
}
const selected:Record<string,any>={};
for(const t of [117,118,124]){
 const ar=a.sides.faulted[t],br=b.sides.faulted[t];
 selected[t*50]={previous:{brake:ar.reply.control.brake,speed_mps:ar.next_ego.speed_mps,monitor:ar.reply.bridge.model_monitor},current:{brake:br.reply.control.brake,speed_mps:br.next_ego.speed_mps,monitor:br.reply.bridge.model_monitor}};
 if(t===117){assert.equal(ar.reply.control.brake,1);assert.equal(br.reply.control.brake,1);assert.ok(br.reply.bridge.model_monitor.release_guard.pending);}
 if(t===118){assert.equal(ar.reply.control.brake,0);assert.equal(br.reply.control.brake,1);assert.equal(br.reply.bridge.model_monitor.release_guard.reason,'active_fault');}
 if(t===124){assert.ok(ar.next_ego.speed_mps>.1);assert.ok(br.next_ego.speed_mps<.1);assert.equal(br.reply.control.brake,1);}
}
const cancellations=modelReleaseCancellationEvents(b.sides.faulted);
assert.ok(cancellations.some((v:any)=>v.input_step===118&&v.active_boundaries.includes('power')));
const report={schema_version:'hil-pending-power-comparison/1',comparison_axis:'previous_faulted_vs_current_faulted',compatibility:'passed',
 previous:{id:old.id,manifest_sha256:old.manifest_hash,recovery_events:modelRecoveryEvents(a.sides.faulted)},
 current:{id:current.id,manifest_sha256:current.manifest_hash,detections:modelDetectionEvents(b.sides.faulted),recovery_events:modelRecoveryEvents(b.sides.faulted),cancellations},
 controls,selected,power_native_duration_seconds:{previous:1.75,current:4},fpga_native_seconds:2.5,browser_workflow_verified:false,physical_validation:false};
fs.writeFileSync(root+'/comparison-checks.json',JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify({compatibility:report.compatibility,controls,cancellations,selected:Object.fromEntries(Object.entries(selected).map(([k,v])=>[k,{previous:{brake:v.previous.brake,speed_mps:v.previous.speed_mps},current:{brake:v.current.brake,speed_mps:v.current.speed_mps}}]))},null,2));
