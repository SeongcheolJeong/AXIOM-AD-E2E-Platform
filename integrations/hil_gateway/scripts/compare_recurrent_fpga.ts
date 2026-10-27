import fs from 'node:fs';
import assert from 'node:assert/strict';
import {modelDetectionEvents,modelRecoveryEvents,modelRunComparisonError} from '../../../frontend/src/components/vecu/hilModelComparison.ts';
const previous='docs/validation/hil-model-recovery-order-20261018',root='docs/validation/hil-model-recurrence-20261018/overflow-200us';
const read=(p:string)=>JSON.parse(fs.readFileSync(p,'utf8'));
const old=read(previous+'/result.json'),current=read(root+'/result.json');
assert.equal(modelRunComparisonError(current,old),null);
const a=read(previous+'/vecu-trace.json'),b=read(root+'/vecu-trace.json');
const controls:Record<string,any>={};
for(const side of ['baseline','faulted']){
 const differences=a.sides[side].flatMap((r:any,i:number)=>JSON.stringify(r.reply.control)===JSON.stringify(b.sides[side][i].reply.control)?[]:[i]);
 if(side==='baseline')assert.equal(differences.length,0);else assert.ok(differences.length>0);
 controls[side]={compared:a.sides[side].length,different_steps:differences};
}
const t=82,oldRow=a.sides.faulted[t],newRow=b.sides.faulted[t];
assert.equal(oldRow.reply.control.brake,0);assert.equal(newRow.reply.control.brake,1);
assert.ok(oldRow.next_ego.speed_mps>.1);assert.ok(newRow.next_ego.speed_mps<.1);
const report={schema_version:'hil-recurrence-comparison/1',comparison_axis:'previous_faulted_vs_current_faulted',compatibility:'passed',
 previous:{id:old.id,manifest_sha256:old.manifest_hash,recovery_events:modelRecoveryEvents(a.sides.faulted)},
 current:{id:current.id,manifest_sha256:current.manifest_hash,detections:modelDetectionEvents(b.sides.faulted),recovery_events:modelRecoveryEvents(b.sides.faulted)},
 controls,at_input_4100ms:{previous:{brake:oldRow.reply.control.brake,speed_mps:oldRow.next_ego.speed_mps,remaining:oldRow.reply.bridge.model_monitor.active_boundaries},
 current:{brake:newRow.reply.control.brake,speed_mps:newRow.next_ego.speed_mps,remaining:newRow.reply.bridge.model_monitor.active_boundaries}},
 browser_workflow_verified:false,physical_validation:false};
fs.writeFileSync(root+'/comparison-checks.json',JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify({compatibility:report.compatibility,controls,at_input_4100ms:report.at_input_4100ms},null,2));
