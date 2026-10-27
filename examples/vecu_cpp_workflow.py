"""Build two C++ ECUs, register them and run four actual CARLA package A/B tests.

No services are started. AXIOM_API_TOKEN is optional for operator-authenticated
deployments. --hil-refs accepts {delay,drop,repeat: {id,revision,sha256}} or the
retained docs/validation/vecu-repeat-20261017/receipt.json. Every referenced HIL
revision is fetched and checked before any build is submitted.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.parse import urlencode
from uuid import uuid4
from axiom_sdk import AxiomClient


def ref(value):
    return {key:value[key] for key in ('id','revision','sha256')}


def pinned_hil_tests(client,project,path):
    data=json.loads(Path(path).read_text())
    if 'runs' in data:
        references={name:item['test']['hil_test_ref'] for name,item in data['runs'].items()
                    if name in ('delay','drop','dropout','repeat')}
    else:
        references=data
    if 'drop' not in references and 'dropout' in references:references['drop']=references['dropout']
    tests={}
    for name in ('delay','drop','repeat'):
        requested=ref(references[name])
        saved=client.hil_sim_test(requested['id'],project_id=project,revision=requested['revision'])
        if ref(saved)!=requested:raise ValueError('Saved HIL revision hash changed: '+name)
        spec=saved['experiment'];fault=spec['input']['fault']
        if (spec['seed']!=42 or fault['kind']!=name or fault['start_s']!=3 or fault['duration_s']!=4
                or (name=='delay' and fault['delay_ms']!=400)):
            raise ValueError('Expected seed 42 and exact 3–7 s '+name+' test (delay=400ms)')
        tests[name]=saved
    scenes=[{key:value for key,value in t['experiment']['input'].items() if key!='fault'} for t in tests.values()]
    if any(scene!=scenes[0] for scene in scenes[1:]):raise ValueError('Fault tests do not share the same scene/profile revision')
    return tests


def wait(client,identity,timeout):
    start=time.monotonic()
    while True:
        result=client.experiment_execution(identity)
        if result['status'] in {'completed','failed','cancelled','interrupted'}:return result
        if time.monotonic()-start>timeout:raise TimeoutError('Execution remains active; resume this receipt: '+identity)
        time.sleep(2)


def retain(client,result,folder,download_media=False):
    folder.mkdir(parents=True,exist_ok=True)
    verified=[]
    for artifact in result['artifacts']:
        if not download_media and artifact['media_type'] not in {'application/json','text/plain','application/gzip'}:continue
        path=(folder/artifact['name']).resolve()
        if not path.is_relative_to(folder.resolve()):raise ValueError('Unsafe artifact name')
        path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists():client.download_experiment_artifact(result['id'],artifact['id'],path)
        sha=hashlib.sha256(path.read_bytes()).hexdigest()
        if sha!=artifact['sha256']:raise ValueError('Retained artifact hash differs: '+artifact['name'])
        verified.append(dict(name=artifact['name'],sha256=sha,path=str(path)))
    export=folder/'execution-export.json';client.export_experiment_execution(result['id'],export)
    if hashlib.sha256(export.read_bytes()).hexdigest()!=result['manifest_hash']:raise ValueError('Export hash differs')
    return verified


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True,help='API origin, e.g. http://127.0.0.1:8230')
    parser.add_argument('--ui',required=True,help='UI origin for saved links')
    parser.add_argument('--project',required=True)
    parser.add_argument('--hil-refs',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--download-media',action='store_true')
    parser.add_argument('--fixed-replay',action='store_true',help='Also run four fixed-input cases and three-run delay repeatability on the same builds')
    parser.add_argument('--timeout',type=float,default=1800)
    args=parser.parse_args();output=Path(args.output).resolve()
    if args.resume:
        receipt=json.loads(output.read_text())
        if (receipt['api'],receipt['project'])!=(args.url,args.project):raise ValueError('Resume API/project differs')
    else:
        if output.exists():raise ValueError('Receipt exists; use --resume to retain execution identity')
        receipt=dict(schema_version='vecu-cpp-workflow/1',key=str(uuid4()),api=args.url,project=args.project,
                     builds={},packages={},runs={},physical_hil='not_configured')
    output.parent.mkdir(parents=True,exist_ok=True)
    def save():
        temporary=output.with_suffix(output.suffix+'.tmp')
        temporary.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
        temporary.replace(output)
    def finish(client,item,folder):
        result=wait(client,item['job']['id'],args.timeout)
        item.update(status=result['status'],summary=result.get('summary'),error=result.get('error'),result=result)
        save()
        if result['status']!='completed':raise RuntimeError('Execution failed; inspect retained receipt: '+str(result.get('error')))
        item['verified_artifacts']=retain(client,result,folder,args.download_media)
        item['manifest_hash']=result['manifest_hash'];save()
        return result
    save()
    with AxiomClient(args.url,token=os.environ.get('AXIOM_API_TOKEN'),timeout=180) as client:
        tests=pinned_hil_tests(client,args.project,args.hil_refs)
        current={name:ref(test) for name,test in tests.items()}
        if receipt.get('hil_refs',current)!=current:raise ValueError('Resume HIL references differ')
        receipt['hil_refs']=current;save()
        artifacts=output.parent/(output.stem+'-artifacts')
        for source in ('cpp-immediate','cpp-ramp'):
            item=receipt['builds'].setdefault(source,{})
            if 'job' not in item:
                item['job']=client.build_vecu_package(name='CARLA package A/B · '+source,source_id=source,
                    project_id=args.project,idempotency_key=receipt['key']+'-build-'+source);save()
            print('build',source,item['job']['id'],flush=True)
            finish(client,item,artifacts/'builds'/source)
            if source not in receipt['packages']:
                receipt['packages'][source]=client.save_vecu_record('packages',dict(project_id=args.project,
                    name=source,build_execution_id=item['job']['id']),idempotency_key=receipt['key']+'-package-'+source)
                save()
        if args.fixed_replay:
            if 'fixed_input' not in receipt:
                receipt['fixed_input']=client.save_vecu_record('inputs',client.vecu_catalog(project_id=args.project)['input_example']|
                    {'project_id':args.project},idempotency_key=receipt['key']+'-fixed-input');save()
            fixed=receipt.setdefault('fixed_runs',{})
            for case in ('normal','delay','dropout','repeat','delay-repeat-2','delay-repeat-3'):
                item=fixed.setdefault(case,{})
                if 'test' not in item:
                    if case.startswith('delay-repeat-'):
                        item['test']=fixed['delay']['test']
                    else:
                        faults=[] if case=='normal' else [dict(kind=case,start_step=60,end_step=140,delay_steps=8)]
                        body=dict(project_id=args.project,name='C++ ECU fixed-input A/B · '+case,comparison='package_ab',
                            package_ref=ref(receipt['packages']['cpp-immediate']),variant_package_ref=ref(receipt['packages']['cpp-ramp']),
                            input_ref=ref(receipt['fixed_input']),faults=faults,fault_enabled=case!='normal',seed=42)
                        item['test']=client.save_vecu_record('tests',body,idempotency_key=receipt['key']+'-fixed-test-'+case)
                    save()
                if 'job' not in item:
                    item['job']=client.execute_vecu_test(item['test'],idempotency_key=receipt['key']+'-fixed-run-'+case);save()
                print('fixed',case,item['job']['id'],flush=True)
                result=finish(client,item,artifacts/'fixed'/case)
                sides=result['summary']['sides']
                if sides['baseline']['input_sha256']!=sides['faulted']['input_sha256']:
                    raise ValueError('Fixed package A/B input packets differ')
                trace=json.loads((artifacts/'fixed'/case/'vecu-trace.json').read_text())
                expected=(0,80) if case=='dropout' else (80,0) if case=='repeat' else (0,0) if case=='normal' else (8,0)
                counters={}
                for side,rows in trace['sides'].items():
                    final=rows[-1]['reply'];counts=(final['duplicate_packet_count'],final['missing_packet_count'])
                    if counts!=expected:raise ValueError('Packet diagnostic counts differ: '+case+' '+side+' '+str(counts))
                    if final['state']!='tracking':raise ValueError('Fixed replay did not recover: '+case+' '+side)
                    if (sides[side]['degraded_steps']>0)!=(case!='normal'):raise ValueError('Unexpected degradation: '+case+' '+side)
                    counters[side]=dict(duplicate=counts[0],missing=counts[1])
                item['packet_counters']=counters;save()
            repeated=[fixed[name]['summary']['sides'] for name in ('delay','delay-repeat-2','delay-repeat-3')]
            identical=all(len({value[side]['logical_sha256'] for value in repeated})==1 for side in ('baseline','faulted'))
            receipt['fixed_three_run_logical_hashes_equal']=identical;save()
            if not identical:raise ValueError('Fixed compiled ECU logical traces are not repeatable')
        for case in ('normal','delay','drop','repeat'):
            item=receipt['runs'].setdefault(case,{})
            if 'test' not in item:
                hil=tests['delay' if case=='normal' else case]
                body=dict(project_id=args.project,name='C++ ECU A/B · '+case,mode='carla_closed_loop',comparison='package_ab',
                    package_ref=ref(receipt['packages']['cpp-immediate']),variant_package_ref=ref(receipt['packages']['cpp-ramp']),
                    hil_test_ref=ref(hil),fault_enabled=case!='normal',seed=42)
                item['test']=client.save_vecu_record('tests',body,idempotency_key=receipt['key']+'-test-'+case);save()
            if 'job' not in item:
                item['job']=client.execute_vecu_test(item['test'],idempotency_key=receipt['key']+'-run-'+case);save()
            print('CARLA',case,item['job']['id'],flush=True)
            result=finish(client,item,artifacts/case)
            item['url']=args.ui.rstrip('/')+'/#hil-sim?'+urlencode(dict(project=args.project,mode='vecu',
                test=item['test']['id'],revision=item['test']['revision'],execution=result['id'],step=65))
            save();print(case,result['status'],item['url'],flush=True)
        receipt['all_runs_completed']=all(item.get('status')=='completed' for item in receipt['runs'].values())
        receipt['vehicle_executions']=8;save()


if __name__=='__main__':main()
