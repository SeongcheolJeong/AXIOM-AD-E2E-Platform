"""Audit an actual Restbus interruption with an overlapping ADAS reset.

Reads canonical retained artifacts in place; never runs or supplies DUT control.
"""
import argparse
import json
from pathlib import Path

from ade2e.service import digest
from axiom_carla.hil_network import decode_command
from analyze_vecu_network_evidence import api_json, dump, require, sha, verify_side


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--artifacts',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--api',default='http://127.0.0.1:8230')
    parser.add_argument('--reset-start-ms',type=int,choices=(5000,6900),default=5000)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    reset_start=args.reset_start_ms;restart=reset_start+200;start_index=reset_start//50;restart_index=restart//50
    report={'schema_version':'vecu-restbus-reset-validation/1','status':'running',
            'analyzer_sha256':sha(Path(__file__)),'driving_safety':'not_evaluated'}
    try:
        result=json.loads((args.evidence/'result.json').read_bytes())
        exported=json.loads((args.evidence/'export.json').read_bytes())
        require(result['status']=='completed','CARLA execution did not complete')
        require(sha(args.evidence/'export.json')==result['manifest_hash'],'Export hash differs')
        require(exported['execution']['id']==result['id'] and exported['execution']['artifacts']==result['artifacts'],'Export identity differs')
        require(api_json(args.api,'/api/v1/virtual-ecu-network/executions/'+result['id']+'?project_id='+result['project_id'])==result,'API receipt differs')
        prepared=exported['prepared']['input'];summary=result['summary'];faults=prepared['test']['faults']
        require([(f['kind'],f['start_ms'],f['end_ms']) for f in faults]==[('restbus_drop',3000,7000),('ecu_reset',reset_start,restart)],'Compound schedule differs')
        require(summary['checks']['driving_safety']=='not_evaluated','Validation boundary differs')
        artifacts={}
        for item in result['artifacts']:
            path=args.artifacts/item['id']
            require(not path.is_symlink() and path.is_file() and path.stat().st_size==item['size_bytes'] and sha(path)==item['sha256'],'Artifact hash or size differs: '+item['name'])
            artifacts[item['name']]={**item,'path':path}
        def read(name):return json.loads(artifacts[name]['path'].read_bytes())
        require(read('vecu-provenance.json')==prepared,'Provenance differs')
        require(read('vecu-summary.json')==summary and read('network-summary.json')==summary['network'],'Summary differs')
        trace,network=read('vecu-trace.json'),read('network-trace.json')
        require(digest(network['sides'])==summary['network']['logical_sha256'],'Network event hash differs')
        metrics={};checks={}
        for side in ('baseline','faulted'):
            metrics[side],checks[side]=verify_side(side,trace['sides'][side],artifacts,read,prepared,summary,network['sides'][side])
        rows=trace['sides']['faulted'];events=network['sides']['faulted']
        require(metrics['baseline']['first_degraded_after_fault_s'] is None,'Baseline degraded')
        require(metrics['faulted']['network_stats']['resets']==1 and metrics['faulted']['network_stats']['heartbeat_timeouts']==1,'Missing reset/heartbeat evidence')
        require(metrics['faulted']['network_stats']['watchdog_events']==1,'Missing independent Brake watchdog')
        for kind in ('fault_start','fault_end'):
            require(sum(e['kind']==kind for e in events)==2,'Compound fault boundaries missing')
        reset=next(e for e in events if e['layer']=='ecu' and e['kind']=='reset')
        require(reset['time_ms']==reset_start and reset['inhibit_until_ms']==restart,'Reset clock differs')
        require(all(r['reply']['network']['epoch']==(1 if r['step']>=start_index else 0) for r in rows),'ADAS epoch differs')
        require(all(r['reply']['network']['heartbeat_override'] for r in rows[start_index:141]),'Reset cleared heartbeat protection before fresh heartbeat recovery')
        require(all(not r['reply']['network']['controller_steps'] for r in rows[start_index:restart_index]),'Control ran during reset inhibit')
        require(bool(rows[restart_index]['reply']['network']['controller_steps']),'Control did not resume at restart boundary')
        expected_after=['restbus_drop'] if restart<7000 else []
        require(rows[start_index]['injection']['kinds']==['restbus_drop','ecu_reset'] and rows[restart_index]['injection']['kinds']==expected_after,'Overlapping authoring evidence missing')
        received=[e for e in events if e['kind']=='can_rx' and e.get('can_id')==256 and e.get('participant')=='brake']
        fresh=[e for e in received if e['time_ms']>=restart and decode_command(e['data_hex'])[2]==1]
        require(len(fresh)>=3 and [e['time_ms'] for e in fresh[:3]]==[restart+1,restart+51,restart+101],'Post-reset command recovery timing differs')
        heartbeats=[e for e in events if e['kind']=='can_rx' and e.get('can_id')==513 and e.get('participant')=='adas' and e['time_ms']>=7000]
        require(len(heartbeats)>=3 and [e['time_ms'] for e in heartbeats[:3]]==[7001,7011,7021],'Heartbeat recovery timing differs')
        earliest_release=max(fresh[2]['time_ms'],heartbeats[2]['time_ms'])
        require(all(r['next_ego']['control']['brake']==1 and r['next_ego']['control']['throttle']==0 for r in rows[63:] if r['step']*50<earliest_release),'Braking released before heartbeat and three-command recovery')
        held=[e for e in received if reset_start-50<=e['time_ms']<heartbeats[2]['time_ms']]
        require(bool(held) and all(decode_command(e['data_hex'])[0]['brake']==1 for e in held),'ADAS requested brake release while heartbeat protection was required')
        first_release_request=next(e['time_ms'] for e in received if e['time_ms']>=7000 and decode_command(e['data_hex'])[0]['brake']<1)
        # A release request may arrive before the third fresh command. Brake
        # must hold it; NetworkAudit replays that independent recovery gate.
        held_release_requests=[e for e in received if 7000<=e['time_ms']<earliest_release and decode_command(e['data_hex'])[0]['brake']<1]
        m=metrics['faulted']
        require(m['first_tracking_after_response_s']>=max(7000,restart)/1000 and m['first_brake_release_after_recovery_s']*1000>=earliest_release,'Premature recovery')
        require(m['final_state']=='tracking' and m['first_observed_speed_above_0_1_after_recovery_s'] is not None,'No vehicle recovery')
        selected=[]
        for index in sorted({99,100,102,104,107,120,137,138,139,140,141,142,143,144,145,146,148,150,start_index,restart_index}):
            row=rows[index];net=row['reply']['network'];brake=net['brake']
            selected.append(dict(input_s=index*.05,response_s=(index+1)*.05,state=row['reply']['state'],epoch=net['epoch'],
                heartbeat_override=net['heartbeat_override'],os_control_tasks=net['stats']['os_control_tasks'],
                brake_reason=brake['reason'],command_sequence=brake['command_sequence'],
                command_age_ms=net['time_ms']-brake['last_command_ms'],
                brake=row['next_ego']['control']['brake'],speed_mps=row['next_ego']['speed_mps']))
        report.update(status='passed',execution_id=result['id'],test_ref=summary['test_ref'],faults=faults,
            native_install_sha256=summary['runtime_sha256'],export_sha256=result['manifest_hash'],
            actual_carla_sides=2,audited_steps=720,camera_members_verified=720,artifacts_verified=len(artifacts),
            heartbeat_protected_can_frames_checked=len(held),first_brake_release_request_rx_ms=first_release_request,
            release_requests_held_before_recovery=len(held_release_requests),first_applied_brake_release_input_ms=round(m['first_brake_release_after_recovery_s']*1000),
            recovery_gates=dict(first_three_post_reset_commands_ms=[e['time_ms'] for e in fresh[:3]],first_three_recovered_heartbeats_ms=[e['time_ms'] for e in heartbeats[:3]],earliest_allowed_release_ms=earliest_release),
            artifact_hashes={k:v['sha256'] for k,v in artifacts.items()},side_checks=checks,metrics=metrics,samples=selected,
            ui_url='http://127.0.0.1:5330/#hil-sim?project='+result['project_id']+'&mode=vecu&tab=network&execution='+result['id']+'&step='+str(143 if reset_start==6900 else 120))
        lines=['# Restbus interruption + ADAS reset: actual CARLA A/B','',
            f'Restbus [3,7)s; single ADAS reset at {reset_start/1000:g}s with 200ms restart; seed 42, 18s per side.',
            'All 720 native control/readback steps and 720 BGRA camera members were independently checked.',
            'ADAS epoch changes to 1. Full braking remains active until heartbeat and post-reset command recovery gates are both satisfied.',
            '',f"ECU tracking: {m['first_tracking_after_response_s']:.2f}s; brake released: {m['first_brake_release_after_recovery_s']:.2f}s.",
            f"First response speed >0.1m/s: {m['first_observed_speed_above_0_1_after_recovery_s']+.05:.2f}s.",
            'Control timestamps use the ECU input clock; CARLA response follows 50ms later. Camera sampling is 10Hz.',
            '',f"[Retained video and ECU state]({report['ui_url']})",'',
            '| Input / response (s) | State | Epoch | Heartbeat protection | Brake cause | Brake | Speed (m/s) |',
            '|---|---|---:|---|---|---:|---:|']
        for row in selected:
            lines.append(f"| {row['input_s']:.2f} / {row['response_s']:.2f} | {row['state']} | {row['epoch']} | {row['heartbeat_override']} | {row['brake_reason']} | {row['brake']:.0%} | {row['speed_mps']:.4f} |")
        lines+=['','This is functional reference software with CARLA actor ground-truth object input. Physical ECU and vehicle safety are not evaluated.']
        (args.output/'comparison.md').write_text('\n'.join(lines)+'\n')
    except Exception as error:
        report.update(status='failed',error=f'{type(error).__name__}: {error}')
        raise
    finally:dump(args.output/'receipt.json',report)
    print(json.dumps({k:report[k] for k in ('status','execution_id','audited_steps','camera_members_verified','ui_url')}),flush=True)


if __name__=='__main__':main()
