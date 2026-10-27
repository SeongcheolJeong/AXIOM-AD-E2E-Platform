"""Execute and retain Open ISP pixel A/B and optional independent ECP5 OOC build.

Starts no services. --resume reuses saved keys, revisions and jobs. A changed adapter or
tool identity requires a new campaign; existing runs and source evidence are preserved.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlencode
from uuid import uuid4

from axiom_sdk import AxiomClient, AxiomError


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8150')
    parser.add_argument('--ui',default='http://127.0.0.1:5250')
    parser.add_argument('--project',default='default');parser.add_argument('--token',default='')
    parser.add_argument('--output',default='docs/validation/open-isp-rtl-20261017.json')
    parser.add_argument('--resume',action='store_true');parser.add_argument('--build',action='store_true')
    args=parser.parse_args();output=Path(args.output)
    folder=output.parent/(output.stem+'-artifacts')
    if not args.resume and (output.exists() or folder.exists()):
        raise SystemExit('Evidence exists; use --resume or choose a new output')
    if args.resume and not output.is_file():
        raise SystemExit('--resume requires an existing receipt')
    with AxiomClient(args.url,token=args.token or None,timeout=180) as client:
        if args.resume:
            receipt=json.loads(output.read_text())
            if (receipt['api_url'],receipt['ui_url'],receipt['project_id'],receipt['include_build'])!=(args.url,args.ui,args.project,args.build):
                raise SystemExit('Resume URLs/project/build scope differ from retained campaign')
        else:
            templates=client.engineering_lab_templates(project_id=args.project)
            ready=client.engineering_lab_readiness(project_id=args.project)
            if not ready.get('engines',{}).get('isp_rtl',{}).get('available'):
                raise SystemExit('Open ISP native adapter unavailable: '+json.dumps(ready))
            base=next(item for item in templates['items'] if item['id']=='open-isp')['config'] | {'source_pattern':'gradient'}
            receipt=dict(schema_version='open-isp-validation/1',campaign_id=str(uuid4()),created_at=datetime.now(timezone.utc).isoformat(),
                api_url=args.url,ui_url=args.ui,project_id=args.project,include_build=args.build,readiness=ready,
                scope='Open RTL pixel contract and separate FPGA OOC build; not Qualcomm internal or physical board validation',
                base_config=base,runs={})
            output.parent.mkdir(parents=True,exist_ok=True)
            with output.open('x') as stream:json.dump(receipt,stream,ensure_ascii=False,indent=2)
        folder.mkdir(exist_ok=args.resume)
        def persist():
            stage=output.with_name(output.name+'.part')
            stage.write_text(json.dumps(receipt,ensure_ascii=False,indent=2));stage.replace(output)
        base=receipt['base_config']
        cases=[('baseline',{}),('white-balance',{'gains':[192,128,128,160]}),
            ('backpressure-active',{'stalls':[{'start_cycle':15000,'end_cycle':16000}]}),
            ('reset-replay',{'reset_at_cycle':64}),('bayer-bggr',{'bayer_pattern':'BGGR'}),
            ('uploaded-raw',{'width':32,'height':24,'source_kind':'raw_asset'})]
        for case,changes in cases:
            if case not in receipt['runs']:
                key=receipt['campaign_id']+'-'+case
                receipt['runs'][case]=dict(save_key=key+'-save',execution_key=key+'-execute',operation='simulate',config=base|changes)
                persist()
            item=receipt['runs'][case]
            if case=='uploaded-raw' and 'asset' not in item:
                # A deterministic original source, not an image emitted by the ISP.
                raw=b''.join(((x*47+y*31+64)%4096).to_bytes(2,'little') for y in range(24) for x in range(32))
                item['asset']=client.upload_apollo_lab_raw(raw,name='Open ISP preserved RAW12',project_id=args.project,
                    metadata=dict(width=32,height=24,bits=12,packing='uint16le',bayer='RGGB',black_level=64,white_level=4095))
                item['source_sha256']=hashlib.sha256(raw).hexdigest()
                item['config']['raw_asset_ref']={k:item['asset'][k] for k in ('id','revision','sha256')}
                persist()
            if 'test' not in item:
                item['test']=client.save_engineering_lab_test(name='Open ISP · '+case,config=item['config'],seed=42,
                    project_id=args.project,idempotency_key=item['save_key']);persist()
        if args.build and 'ecp5-ooc' not in receipt['runs']:
            receipt['runs']['ecp5-ooc']=dict(operation='build',test=receipt['runs']['baseline']['test'],
                execution_key=receipt['campaign_id']+'-ecp5-build',build_profile_id='ecp5-85k-cabga381-6-ooc/1')
            persist()
        for case,item in receipt['runs'].items():
            test=item['test']
            if client.engineering_lab_test(test['id'],revision=test['revision'],project_id=args.project)!=test:
                raise RuntimeError('Immutable test readback differs: '+case)
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
                if time.monotonic()-started>1800:
                    raise RuntimeError('Job still running; resume saved job rather than replacing it')
                time.sleep(.5)
            item.update(status=run['status'],summary=run.get('summary'),error=run.get('error'));persist()
            if run['status']!='completed':raise RuntimeError('Native job did not complete: '+json.dumps(item,ensure_ascii=False))
            destination=folder/case;destination.mkdir(exist_ok=True);verified=[]
            for artifact in run['artifacts']:
                name=artifact['name'];path=destination/name
                if not path.resolve().is_relative_to(destination.resolve()):raise RuntimeError('Artifact path escapes output folder')
                path.parent.mkdir(parents=True,exist_ok=True)
                if not path.exists():client.download_experiment_artifact(run['id'],artifact['id'],path)
                data=path.read_bytes();sha=hashlib.sha256(data).hexdigest()
                if sha!=artifact['sha256'] or len(data)!=artifact['size_bytes']:
                    raise RuntimeError('Existing artifact differs and was preserved: '+name)
                verified.append(dict(name=name,path=str(path),sha256=sha,size_bytes=len(data)))
            export=destination/'execution-export.json'
            if not export.exists():client.export_experiment_execution(run['id'],export)
            if hashlib.sha256(export.read_bytes()).hexdigest()!=run['manifest_hash']:
                raise RuntimeError('Retained execution export hash differs')
            query=dict(project=args.project,mode='fpga',target='engineering-lab',tab='isp_rtl',test=test['id'],revision=test['revision'],execution=run['id'])
            if item['operation']=='build':
                query.update(execution=receipt['runs']['baseline']['job_id'],build=run['id'])
            if case in ['white-balance','backpressure','backpressure-active','reset-replay']:
                query['compare']=receipt['runs']['baseline']['job_id']
            item.update(artifacts_verified=verified,export_path=str(export),all_artifact_hashes_verified=True,
                url=args.ui.rstrip('/')+'/#hil-sim?'+urlencode(query));persist()
            if item['operation']=='simulate' and run['summary']['verdict']!='passed':
                raise RuntimeError('Pixel contract failed; result preserved: '+case)
            print(case,run['summary']['verdict'],flush=True)
        baseline=receipt['runs']['baseline']['summary']
        for case in ['white-balance','backpressure-active','reset-replay']:
            if receipt['runs'][case]['summary']['input_sha256']!=baseline['input_sha256']:
                raise RuntimeError('A/B original RAW differs: '+case)
        stalled=receipt['runs']['backpressure-active']['summary']
        if stalled['cycles']<=baseline['cycles'] or stalled['output_sha256']!=baseline['output_sha256']:
            raise RuntimeError('Active backpressure must delay completion while preserving every output pixel')
        if receipt['runs']['white-balance']['summary']['output_sha256']==baseline['output_sha256']:
            raise RuntimeError('WB example did not change the output; choose a nonsaturated original')
        if 'backpressure' in receipt['runs']:
            receipt['runs']['backpressure']['interpretation']='Preserved initial scheduled stall before RGB output; no measured completion delay. Use backpressure-active for the overlapping-output experiment.'
        if args.build and receipt['runs']['ecp5-ooc']['summary']['design_hash']!=baseline['design_hash']:
            raise RuntimeError('Build and pixel simulation used different RTL designs')
        receipt['same_input_ab_verified']=True
        receipt['completed_at']=datetime.now(timezone.utc).isoformat();persist()
        print(str(output),flush=True)


if __name__=='__main__':main()
