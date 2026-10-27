"""Retain six native Engineering Lab runs and an unavailable Qualcomm preflight.

No services are started. --resume reuses saved revisions/keys/jobs and checks existing
artifacts without replacing them. Source/adapter changes require a new campaign path.
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
    parser.add_argument('--url',default='http://127.0.0.1:8140');parser.add_argument('--ui',default='http://127.0.0.1:5240')
    parser.add_argument('--project',default='default');parser.add_argument('--token',default='')
    parser.add_argument('--output',default='docs/validation/engineering-lab-20261014.json')
    parser.add_argument('--resume',action='store_true');args=parser.parse_args()
    output=Path(args.output);folder=output.parent/(output.stem+'-artifacts')
    if output.exists() and not args.resume:raise SystemExit('Receipt exists; use --resume or choose a new output path')
    if args.resume and not output.is_file():raise SystemExit('--resume requires an existing receipt')
    if folder.exists() and not args.resume:raise SystemExit('Artifact folder exists; choose a new output path')
    client=AxiomClient(args.url,token=args.token,timeout=180)
    ready=client.engineering_lab_readiness(project_id=args.project)
    for engine in ['rtl','spice']:
        if not ready.get('engines',{}).get(engine,{}).get('available'):
            raise SystemExit(engine+' unavailable: '+json.dumps(ready))
    if args.resume:
        evidence=json.loads(output.read_text())
        if (evidence['schema_version']!='engineering-validation/1' or evidence['api_url']!=args.url
                or evidence['ui_url']!=args.ui or evidence['project_id']!=args.project):
            raise SystemExit('Resume API/UI/project differs from the saved campaign')
    else:
        evidence=dict(schema_version='engineering-validation/1',created_at=datetime.now(timezone.utc).isoformat(),
            campaign_id=str(uuid4()),api_url=args.url,ui_url=args.ui,project_id=args.project,readiness=ready,
            scope='Native supplied Stream32 RTL and ngspice transient models; no hardware or Qualcomm-runtime validation',
            presets=client.engineering_lab_templates(project_id=args.project)['items'],runs={})
        output.parent.mkdir(parents=True,exist_ok=True)
        with output.open('x') as f:json.dump(evidence,f,ensure_ascii=False,indent=2)
    folder.mkdir(exist_ok=args.resume)
    def save():
        staged=output.with_name(output.name+'.part');staged.write_text(json.dumps(evidence,ensure_ascii=False,indent=2));staged.replace(output)
    byid={p['id']:p for p in evidence['presets']}
    cases=['passthrough','xor-transform','buggy-xor','rc-step','rc-slow','rlc','qualcomm-sa8650']
    for identity in cases:
        r=evidence['runs'].get(identity)
        if r is None:
            key=evidence['campaign_id']+'-'+identity
            r=dict(definition=byid[identity],save_key=key+'-save',execution_key=key+'-run')
            evidence['runs'][identity]=r;save()
        preset=r['definition']
        if 'test' not in r:
            r['test']=client.save_engineering_lab_test(name=preset['name']+' '+evidence['created_at'],
                config=preset['config'],seed=preset['seed'],project_id=args.project,idempotency_key=r['save_key']);save()
        test=r['test']
        if client.engineering_lab_test(test['id'],revision=test['revision'],project_id=args.project)!=test:
            raise RuntimeError('Saved source revision readback differs')
        query=dict(project=args.project,mode='fpga',target='engineering-lab',test=test['id'],revision=test['revision'],tab=test['config']['engine'])
        if test['config']['engine'] in ['rtl','spice']:
            query.update(sample=2 if test['config']['engine']=='rtl' else 1000,window=0 if test['config']['engine']=='rtl' else 500,span=64 if test['config']['engine']=='rtl' else 1000)
        r['url']=args.ui.rstrip('/')+'/#hil-sim?'+urlencode(query);save()
        if test['config']['engine']=='qualcomm':
            before={x['id'] for x in client.engineering_lab_executions(project_id=args.project)['items'] if x['test_ref']['id']==test['id']}
            try:client.preview_engineering_lab_test(test)
            except AxiomError as exc:
                if exc.status_code!=422:raise
                r['preflight']=dict(status_code=exc.status_code,detail=exc.detail,status='not_configured',execution_created=False)
            else:raise RuntimeError('Qualcomm unexpectedly executable; stop and review actual target/runtime scope')
            after={x['id'] for x in client.engineering_lab_executions(project_id=args.project)['items'] if x['test_ref']['id']==test['id']}
            if after!=before:raise RuntimeError('Unavailable target profile acquired an execution unexpectedly')
            save();print(identity,'not_configured; no execution created',flush=True);continue
        if 'preview' not in r:r['preview']=client.preview_engineering_lab_test(test);save()
        if 'experiment_id' not in r:
            r['experiment_id']=client.create_experiment(**r['preview']['experiment'],idempotency_key=r['execution_key'])['id'];save()
        if 'job_id' not in r:
            job=client.submit_experiment(r['experiment_id'],preview_hash=r['preview']['preview_hash'],idempotency_key=r['execution_key'])
            r.update(job_id=job['id'],execution=job);save();print('submitted',identity,job['id'],flush=True)
        else:print('resuming',identity,r['job_id'],flush=True)
        start=time.monotonic()
        while True:
            run=client.experiment_execution(r['job_id']);r['execution']=run;save()
            if run['status'] in ['completed','failed','cancelled','interrupted']:break
            if time.monotonic()-start>300:raise RuntimeError('Still running; resume the saved job, do not create a replacement')
            time.sleep(.5)
        if run['status']!='completed':raise RuntimeError('Native execution failed; receipt preserved: '+json.dumps(run,ensure_ascii=False))
        dest=folder/identity;dest.mkdir(exist_ok=args.resume);verified={a['name']:a for a in r.get('artifacts_verified',[])}
        docs={}
        for a in run['artifacts']:
            name=a['name']
            if Path(name).name!=name:raise RuntimeError('Unexpected nested artifact name')
            path=dest/name
            if not path.exists():client.download_experiment_artifact(run['id'],a['id'],path)
            raw=path.read_bytes();sha=hashlib.sha256(raw).hexdigest()
            if sha!=a['sha256'] or len(raw)!=a['size_bytes']:raise RuntimeError('Existing artifact differs and was preserved: '+name)
            verified[name]=dict(name=name,sha256=sha,size_bytes=len(raw),path=str(path));r['artifacts_verified']=list(verified.values())
            if a['media_type']=='application/json':docs[name]=json.loads(raw)
            save()
        trace=docs['trace.json'];r.update(summary=docs['summary.json'],all_artifact_hashes_verified=True)
        r.pop('trace',None)  # Keep full native waveforms in their verified artifact, not duplicated in receipts.
        points=trace['samples'];indices=sorted({0,len(points)//2,len(points)-1})
        r['trace_manifest']=dict(path=str(dest/'trace.json'),axis=trace['axis'],signals=trace['signals'],
            sample_count=len(points),excerpts=[dict(sample_index=i,values=points[i]) for i in indices],provenance=trace['provenance'])
        if test['config']['engine']=='rtl':
            words=[sample['in_data'] for sample in points if sample['accepted_in']]
            r['input_sequence_sha256']=hashlib.sha256(json.dumps(words,separators=(',',':')).encode('ascii')).hexdigest()
            r['input_sequence_words']=len(words)
        if trace['provenance']['config_hash']!=test['config_hash']:raise RuntimeError('Trace source configuration differs')
        for name in ['source_hashes','netlist_sha256']:
            if name in test and trace['provenance'].get(name)!=test[name]:raise RuntimeError('Trace original source hash differs')
        r['source_identity_verified']=True
        export=dest/'execution-export.json'
        if not export.exists():client.export_experiment_execution(run['id'],export)
        if hashlib.sha256(export.read_bytes()).hexdigest()!=run['manifest_hash']:raise RuntimeError('Existing export differs; it was not replaced')
        r['export_path']=str(export)
        r['url']=args.ui.rstrip('/')+'/#hil-sim?'+urlencode(query|dict(execution=run['id']))
        baseline='passthrough' if identity in ['xor-transform','buggy-xor'] else 'rc-step' if identity in ['rc-slow','rlc'] else None
        if baseline:
            r['comparison_url']=args.ui.rstrip('/')+'/#hil-sim?'+urlencode(query|dict(execution=run['id'],compare=evidence['runs'][baseline]['job_id']))
        save()
        expected='failed' if identity=='buggy-xor' else 'passed' if test['config']['engine']=='rtl' else 'observed'
        if r['summary']['verdict']!=expected:raise RuntimeError('Unexpected verdict; keep the receipt for review')
        summary={k:v for k,v in r['summary'].items() if k in ['verdict','input_transfers','output_transfers','mismatch_count','first_mismatch','sample_count','final_values']}
        print(identity,json.dumps(summary,ensure_ascii=False),flush=True)
    rtl=[evidence['runs'][name] for name in ['passthrough','xor-transform','buggy-xor']]
    words=[(r['input_sequence_sha256'],r['input_sequence_words']) for r in rtl]
    if not all(x==words[0] for x in words):raise RuntimeError('RTL input word sequences differ')
    evidence['matched_rtl_input_sequence_verified']=True
    evidence['completed_at']=datetime.now(timezone.utc).isoformat();save();print('evidence',output,flush=True)

if __name__=='__main__':main()
