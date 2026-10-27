"""Retain four-channel ADAS RTL campaigns using only the public API/SDK.

Default scope is 4 x 1920 x 1080, 30 frames per channel, timed at 30 fps.
--smoke explicitly chooses reduced dimensions and accelerated functional timing;
it cannot establish 1080p30 acceptance. Starts no services. --resume reuses jobs,
revisions and retry keys and refuses to replace changed local evidence.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlencode
from uuid import uuid4
from axiom_sdk import AxiomClient


CASES = ['adas-normal', 'adas-wb', 'adas-stall-30us', 'adas-overflow-100us', 'adas-reset', 'adas-input-gap']


def checksum(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def campaign_config(preset, smoke):
    config = copy.deepcopy(preset['config'])
    if smoke:
        config.update(width=32, height=24, frames=2, raster_width=40, raster_height=32,
                      pixel_rate_hz=50000000, max_cycles=30000)
        for fault in config['faults']:
            fault['at_cycle'] = 500
            if fault['kind'] == 'input_gap':
                fault['duration_cycles'] = 32
        for update in config['csr_updates']:
            update['at_cycle'] = 1000
        config['trace_windows'] = [dict(start_cycle=0,end_cycle=2048),dict(start_cycle=2800,end_cycle=3200)]
    else:
        config['trace_windows'] = [dict(start_cycle=0,end_cycle=1024)]
        if config['faults']:
            fault=config['faults'][0];at=fault['at_cycle'];end=at+fault['duration_cycles']
            config['trace_windows'] += [dict(start_cycle=at-32,end_cycle=at+256)]
            if end>at+256:
                config['trace_windows'].append(dict(start_cycle=end-32,end_cycle=end+256))
        if config['csr_updates']:
            at=config['csr_updates'][0]['at_cycle']
            config['trace_windows'].append(dict(start_cycle=at-32,end_cycle=at+512))
    return config


def validation_gates(receipt):
    """Keep digital scenario acceptance separate from timing and physical HIL."""
    normal = receipt['runs']['adas-normal']['summary']
    build = receipt['runs'].get('ecp5-ooc')
    return dict(
        full_1080p30_normal='passed' if not receipt['smoke'] and normal.get('acceptance_1080p30_passed') else 'not_run',
        scenario_contracts='passed',
        ooc_timing=('passed' if build['summary'].get('timing_status') == 'passed' else 'failed') if build else 'not_run',
        physical_hil='not_configured',
    )


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8160')
    parser.add_argument('--ui',default='http://127.0.0.1:5260')
    parser.add_argument('--project',default='default');parser.add_argument('--token',default='')
    parser.add_argument('--output');parser.add_argument('--resume',action='store_true')
    parser.add_argument('--smoke',action='store_true');parser.add_argument('--build',action='store_true')
    parser.add_argument('--cases',default=','.join(CASES),help='Comma-separated preset IDs; baseline is always retained')
    parser.add_argument('--poll-timeout',type=int,default=3600)
    args=parser.parse_args()
    requested=args.cases.split(',')
    if any(x not in CASES for x in requested):raise SystemExit('Unknown ADAS preset ID')
    selected=[x for x in CASES if x=='adas-normal' or x in requested]
    suffix='-smoke' if args.smoke else ''
    output=Path(args.output or 'docs/validation/adas-module-rtl'+suffix+'-20261017.json')
    folder=output.parent/(output.stem+'-artifacts')
    if not args.resume and (output.exists() or folder.exists()):raise SystemExit('Evidence exists; choose a new path or --resume')
    if args.resume and not output.is_file():raise SystemExit('--resume requires an existing receipt')
    with AxiomClient(args.url,token=args.token or None,timeout=180) as client:
        scope=dict(api_url=args.url,ui_url=args.ui,project_id=args.project,smoke=args.smoke,include_build=args.build,cases=selected)
        if args.resume:
            receipt=json.loads(output.read_text())
            if receipt.get('schema_version')!='adas-module-validation/1' or any(receipt.get(k)!=v for k,v in scope.items()):
                raise SystemExit('Resume scope differs from the retained campaign')
        else:
            ready=client.engineering_lab_readiness(project_id=args.project)
            if not ready.get('engines',{}).get('adas_module_rtl',{}).get('available'):
                raise SystemExit('Native ADAS module adapter unavailable: '+json.dumps(ready))
            presets={p['id']:p for p in client.engineering_lab_templates(project_id=args.project)['items'] if p['id'] in selected}
            if set(presets)!=set(selected):raise SystemExit('ADAS preset catalog is incomplete')
            receipt=dict(schema_version='adas-module-validation/1',campaign_id=str(uuid4()),
                created_at=datetime.now(timezone.utc).isoformat(),**scope,readiness=ready,
                validation_scope='Reduced accelerated functional smoke; not 1080p30 acceptance' if args.smoke else
                    'Four-channel 1080p30 native open RTL; post-CSI digital contract, not hardware/PHY/ECU validation',
                presets=presets,runs={})
            output.parent.mkdir(parents=True,exist_ok=True)
            with output.open('x') as stream:json.dump(receipt,stream,ensure_ascii=False,indent=2)
        folder.mkdir(exist_ok=args.resume)
        def persist():
            stage=output.with_name(output.name+'.part');stage.write_text(json.dumps(receipt,ensure_ascii=False,indent=2));stage.replace(output)
        for case in selected:
            if case not in receipt['runs']:
                key=receipt['campaign_id']+'-'+case
                receipt['runs'][case]=dict(operation='simulate',config=campaign_config(receipt['presets'][case],args.smoke),
                    save_key=key+'-save',execution_key=key+'-execute')
                persist()
            item=receipt['runs'][case]
            if 'test' not in item:
                item['test']=client.save_engineering_lab_test(name='ADAS module · '+case+(' · smoke' if args.smoke else ''),
                    config=item['config'],project_id=args.project,seed=42,idempotency_key=item['save_key']);persist()
        if args.build and 'ecp5-ooc' not in receipt['runs']:
            receipt['runs']['ecp5-ooc']=dict(operation='build',test=receipt['runs']['adas-normal']['test'],
                execution_key=receipt['campaign_id']+'-ecp5-build',build_profile_id='ecp5-85k-cabga381-6-ooc/1');persist()
        manifests={}
        for case,item in receipt['runs'].items():
            test=item['test']
            if client.engineering_lab_test(test['id'],revision=test['revision'],project_id=args.project)!=test:
                raise RuntimeError('Immutable saved configuration readback differs: '+case)
            if 'preview' not in item:
                item['preview']=(client.preview_engineering_lab_build(test,build_profile_id=item['build_profile_id'])
                    if item['operation']=='build' else client.preview_engineering_lab_test(test));persist()
            if 'experiment_id' not in item:
                item['experiment_id']=client.create_experiment(**item['preview']['experiment'],idempotency_key=item['execution_key'])['id'];persist()
            if 'job_id' not in item:
                item['job_id']=client.submit_experiment(item['experiment_id'],preview_hash=item['preview']['preview_hash'],
                    idempotency_key=item['execution_key'])['id'];persist()
            print(case,item['job_id'],flush=True);started=time.monotonic()
            while True:
                run=client.experiment_execution(item['job_id'])
                if run['status'] in ['completed','failed','cancelled','interrupted']:break
                if time.monotonic()-started>args.poll_timeout:raise RuntimeError('Job still running; --resume reuses this saved job')
                time.sleep(1)
            item.update(status=run['status'],summary=run.get('summary'),error=run.get('error'));persist()
            if run['status']!='completed':raise RuntimeError('Native infrastructure failed; receipt retained: '+str(run.get('error')))
            destination=folder/case;destination.mkdir(exist_ok=True);verified=[]
            for artifact in run['artifacts']:
                path=destination/artifact['name']
                if not path.resolve().is_relative_to(destination.resolve()):raise RuntimeError('Artifact path escapes evidence folder')
                path.parent.mkdir(parents=True,exist_ok=True)
                if not path.exists():client.download_experiment_artifact(run['id'],artifact['id'],path)
                if checksum(path)!=artifact['sha256'] or path.stat().st_size!=artifact['size_bytes']:
                    raise RuntimeError('Existing artifact differs and was preserved: '+artifact['name'])
                verified.append(dict(name=artifact['name'],path=str(path),sha256=artifact['sha256'],size_bytes=artifact['size_bytes']))
            provenance=json.loads((destination/'provenance.json').read_text())
            if provenance['test_ref']!={k:test[k] for k in ('id','revision','sha256')} or provenance['config_hash']!=test['config_hash']:
                raise RuntimeError('Execution provenance differs from the saved configuration')
            export=destination/'execution-export.json'
            if not export.exists():client.export_experiment_execution(run['id'],export)
            if checksum(export)!=run['manifest_hash']:raise RuntimeError('Existing sealed export differs and was preserved')
            query=dict(project=args.project,mode='fpga',target='engineering-lab',tab='module_rtl',
                test=test['id'],revision=test['revision'],execution=run['id'],channel=0,frame=0)
            if item['operation']=='build':query.update(execution=receipt['runs']['adas-normal']['job_id'],build=run['id'])
            elif case!='adas-normal':query['compare']=receipt['runs']['adas-normal']['job_id']
            item.update(artifacts_verified=verified,export_path=str(export),export_sha256=run['manifest_hash'],
                all_artifact_hashes_verified=True,source_identity_verified=True,url=args.ui.rstrip('/')+'/#hil-sim?'+urlencode(query))
            if item['operation']=='simulate':
                frames=json.loads((destination/'frames.json').read_text());manifests[case]=frames
                item['frame_manifest']=dict(path=str(destination/'frames.json'),sha256=checksum(destination/'frames.json'),
                    frame_count=len(frames['frames']),channels=frames['channels'])
            persist()
            print(case,json.dumps({k:item['summary'].get(k) for k in ('verdict','completed_frames','aborted_frames',
                'committed_mismatch_count','discarded_mismatch_count','unexpected_mismatch_count','cycles','acceptance_1080p30_passed')}),flush=True)
            if item['operation']=='simulate' and item['summary']['verdict']!='passed':
                raise RuntimeError('Native module contract did not pass; evidence retained: '+case)
            if item['operation']=='simulate' and (item['summary']['committed_mismatch_count'] or item['summary']['unexpected_mismatch_count']):
                raise RuntimeError('Unexpected or committed pixel corruption; evidence retained: '+case)
        baseline={(f['channel'],f['frame']):f for f in manifests['adas-normal']['frames']}
        for case,manifest in manifests.items():
            for f in manifest['frames']:
                expected=baseline[(f['channel'],f['frame'])]
                if f['input_sha256']!=expected['input_sha256']:raise RuntimeError('A/B source pixels differ: '+case)
                if f['channel']!=0 and (f['status']!='committed' or f['output_sha256']!=expected['output_sha256']):
                    raise RuntimeError('CH0 experiment affected an independent channel: '+case)
            if case=='adas-wb' and not any(f['channel']==0 and f['output_sha256']!=baseline[(0,f['frame'])]['output_sha256'] for f in manifest['frames']):
                raise RuntimeError('WB campaign produced no changed CH0 frame')
        if not args.smoke:
            if not receipt['runs']['adas-normal']['summary']['acceptance_1080p30_passed']:
                raise RuntimeError('Full normal campaign did not establish 120 committed 1080p30 frames')
            if 'adas-wb' in manifests:
                for frame in manifests['adas-wb']['frames']:
                    if frame['channel']!=0:continue
                    expected_version=0 if frame['frame']<3 else 1
                    if frame['status']!='committed' or frame['config_version']!=expected_version:
                        raise RuntimeError('WB CSR change did not apply exactly at CH0 frame 3 SOF')
                    if frame['frame']<3 and frame['output_sha256']!=baseline[(0,frame['frame'])]['output_sha256']:
                        raise RuntimeError('WB changed CH0 pixels before the frame 3 commit boundary')
            for case,reason in [('adas-overflow-100us','fifo_overflow'),('adas-reset','channel_reset'),('adas-input-gap','frame_boundary')]:
                if case not in manifests:continue
                summary=receipt['runs'][case]['summary']
                if (summary['completed_frames'],summary['aborted_frames'],summary['missing_frames'])!=(119,1,0):
                    raise RuntimeError('Fault must abort exactly one CH0 frame and recover: '+case)
                for frame in manifests[case]['frames']:
                    if frame['channel']!=0:continue
                    if frame['frame']==0:
                        if frame['status']!='aborted' or frame.get('loss_reason')!=reason:
                            raise RuntimeError('CH0 frame 0 lacks the intended explicit abort reason: '+case)
                    elif frame['status']!='committed' or frame['output_sha256']!=baseline[(0,frame['frame'])]['output_sha256']:
                        raise RuntimeError('CH0 frames 1..29 did not recover to baseline pixels: '+case)
            if 'adas-stall-30us' in receipt['runs']:
                if receipt['runs']['adas-stall-30us']['summary']['completed_frames']!=120:
                    raise RuntimeError('30 us bounded stall did not preserve all frames')
                normal_ch0=next(ch for ch in manifests['adas-normal']['channels'] if ch['channel']==0)
                stalled_ch0=next(ch for ch in manifests['adas-stall-30us']['channels'] if ch['channel']==0)
                if stalled_ch0['fifo_peak_pixels']<=normal_ch0['fifo_peak_pixels']:
                    raise RuntimeError('30 us stall did not produce a measured CH0 FIFO increase')
        if args.build and receipt['runs']['ecp5-ooc']['summary']['design_hash']!=receipt['runs']['adas-normal']['summary']['design_hash']:
            raise RuntimeError('Simulation/build RTL design hash differs')
        receipt.update(same_input_ab_verified=True,independent_channels_verified=True,
            full_acceptance_checks_verified=not args.smoke,validation_gates=validation_gates(receipt),
            completed_at=datetime.now(timezone.utc).isoformat())
        persist();print('evidence',output,flush=True)


if __name__=='__main__':main()
