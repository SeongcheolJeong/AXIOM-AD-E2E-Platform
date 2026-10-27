"""Run and retain the pre-board campaign through the public API/SDK.

Default: four 1080p30 streams, 300 continuous frames/channel, three seeds,
repeated and simultaneous faults, FIFO threshold sweep and four-state checks.
No service is started and no hardware is programmed. --resume reuses saved IDs.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlencode
from uuid import uuid4

from axiom_sdk import AxiomClient


def checksum(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8180')
    parser.add_argument('--ui',default='http://127.0.0.1:5280')
    parser.add_argument('--output',default='docs/validation/adas-preboard-20261017.json')
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--smoke',action='store_true')
    parser.add_argument('--poll-timeout',type=int,default=7200)
    args=parser.parse_args()
    output=Path(args.output);folder=output.parent/(output.stem+'-artifacts')
    scope=dict(api_url=args.url,ui_url=args.ui,smoke=args.smoke)
    if args.resume:
        receipt=json.loads(output.read_text())
        if any(receipt[k]!=v for k,v in scope.items()):raise ValueError('Resume scope differs')
    else:
        if output.exists() or folder.exists():raise ValueError('Evidence exists; use --resume or a new output')
        receipt=dict(schema_version='adas-preboard-validation/1',created_at=datetime.now(timezone.utc).isoformat(),
            campaign_id=str(uuid4()),**scope,runs={})
    output.parent.mkdir(parents=True,exist_ok=True);folder.mkdir(exist_ok=True)
    def persist():
        staged=output.with_suffix('.part');staged.write_text(json.dumps(receipt,ensure_ascii=False,indent=2));staged.replace(output)
    persist()
    with AxiomClient(args.url,timeout=180) as client:
        if not receipt['runs']:
            templates=client.engineering_lab_templates()['items']
            config=next(x['config'] for x in templates if x['config']['engine']=='adas_validation_campaign')
            if args.smoke:config=config|dict(mode='extended_smoke',long_frames=30,seeds=[42,7],reset_count=2,stall_cycles=[5000,8000])
            receipt['plan']=client.preview_adas_campaign_plan(config)
            receipt['runs']['campaign']=dict(operation='simulate',config=config)
            # Timing is always an independent full-resolution OOC job.
            core=next(x['config'] for x in templates if x['id']=='adas-normal')
            receipt['runs']['ooc']=dict(operation='build',config=core)
            persist()
        for name,item in receipt['runs'].items():
            key=receipt['campaign_id']+'-'+name
            if 'test' not in item:
                item['test']=client.save_engineering_lab_test(name='ADAS pre-board · '+name,config=item['config'],
                    seed=42,idempotency_key=key+'-save');persist()
            test=item['test']
            if client.engineering_lab_test(test['id'],revision=test['revision'])!=test:raise ValueError('Saved test changed')
            if 'preview' not in item:
                item['preview']=client.preview_engineering_lab_build(test) if item['operation']=='build' else client.preview_engineering_lab_test(test);persist()
            if 'experiment_id' not in item:
                item['experiment_id']=client.create_experiment(**item['preview']['experiment'],idempotency_key=key)['id'];persist()
            if 'job_id' not in item:
                item['job_id']=client.submit_experiment(item['experiment_id'],preview_hash=item['preview']['preview_hash'],idempotency_key=key)['id'];persist()
            print(name,item['job_id'],flush=True);started=time.monotonic()
            while True:
                run=client.experiment_execution(item['job_id'])
                if run['status'] in {'completed','failed','cancelled','interrupted'}:break
                if time.monotonic()-started>args.poll_timeout:raise TimeoutError('Still running; use --resume')
                time.sleep(2)
            item.update(status=run['status'],summary=run.get('summary'),error=run.get('error'));persist()
            if run['status']!='completed':raise RuntimeError('Native execution failed; receipt preserved: '+str(run.get('error')))
            destination=folder/name;destination.mkdir(exist_ok=True);verified=[]
            for artifact in run['artifacts']:
                path=destination/artifact['name']
                if not path.resolve().is_relative_to(destination.resolve()):raise ValueError('Unsafe artifact name')
                path.parent.mkdir(parents=True,exist_ok=True)
                if not path.exists():client.download_experiment_artifact(run['id'],artifact['id'],path)
                if checksum(path)!=artifact['sha256'] or path.stat().st_size!=artifact['size_bytes']:raise ValueError('Artifact changed; preserved '+str(path))
                verified.append(dict(name=artifact['name'],sha256=artifact['sha256'],size_bytes=artifact['size_bytes']))
            provenance=json.loads((destination/'provenance.json').read_text())
            if provenance['test_ref']!={k:test[k] for k in ('id','revision','sha256')}:raise ValueError('Provenance revision differs')
            if provenance['config_hash']!=test['config_hash']:raise ValueError('Provenance config differs')
            export=destination/'execution-export.json'
            if not export.exists():client.export_experiment_execution(run['id'],export)
            if checksum(export)!=run['manifest_hash']:raise ValueError('Sealed export differs')
            query=dict(project='default',mode='fpga',target='engineering-lab',tab='module_rtl')
            if name=='campaign':query.update(moduleView='campaign',campaign=test['id'],campaignRevision=test['revision'],campaignExecution=run['id'],campaignCase='long-continuous',campaignFrame=29)
            else:query.update(test=test['id'],revision=test['revision'],build=run['id'])
            item.update(artifact_count=len(verified),artifacts_verified=verified,export_sha256=run['manifest_hash'],
                all_hashes_verified=True,url=args.ui+'/#hil-sim?'+urlencode(query));persist()
            print(name,json.dumps(item['summary'],ensure_ascii=False),flush=True)
        a,b=receipt['runs']['campaign']['summary'],receipt['runs']['ooc']['summary']
        receipt['same_fullhd_design_hash']=(a['design_hash']==b['design_hash']) if not args.smoke else None
        receipt['validation_complete']=a['verdict']=='passed' and b['verdict']=='passed' and all(
            b.get(gate)=='passed' for gate in ('synthesis_status','place_route_status','timing_status')) and (args.smoke or receipt['same_fullhd_design_hash'])
        receipt['physical_hil']='not_configured';persist()
        if not receipt['validation_complete']:raise RuntimeError('One or more validation gates failed; evidence retained')


if __name__=='__main__':main()
