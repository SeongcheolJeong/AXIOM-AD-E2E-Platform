"""Author and execute matched 1080p Apollo Preview/CSI/OFE/DDR/ZSL runs via public SDK.
Run: .venv/bin/python examples/apollo_lab_workflow.py --url http://127.0.0.1:8110 --output docs/validation/apollo-lab-20261014.json
"""
import argparse,hashlib,json,time
from pathlib import Path
from datetime import datetime,timezone
import httpx
from axiom_sdk import AxiomClient

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8110');parser.add_argument('--ui',default='http://127.0.0.1:5210');parser.add_argument('--output',required=True);args=parser.parse_args()
 client=AxiomClient(args.url,token='',timeout=180);items=client.apollo_lab_templates()['items'];ref=lambda d:{k:d[k] for k in ('id','revision','sha256')}
 out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True)
 if out.exists():raise SystemExit('Output exists; preserve prior receipts and choose another path')
 stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S');evidence={'schema':'apollo-validation/1','created_at':stamp,'scope':'actual queued SimPy + full-resolution CPU reference image processing; not physical/vendor validation','runs':{}}
 def save():out.write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
 channels=[]
 for ch in items[0]['channels']:
  d=client.save_fpga_lab_design(name='Apollo common RAW '+stamp,config=ch['config'],seed=42,idempotency_key=stamp+'ch'+str(ch['channel']))
  channels.append({k:v for k,v in ch.items() if k!='config'}|{'design_ref':ref(d)})
 evidence['common_channels']=channels;save()
 for p in items:
  key=stamp+'-'+p['id'];profile=client.save_apollo_lab_profile(name=p['name'],config=p['profile']['config'],idempotency_key=key+'profile')
  test=client.save_apollo_lab_test(name=p['name']+' '+stamp,profile_ref=ref(profile),channels=channels,captures=p['captures'],seed=42,idempotency_key=key+'test')
  r={'test':test,'profile':profile};evidence['runs'][p['id']]=r;save()
  job=client.execute_apollo_lab_test(test,idempotency_key=key+'run');r['job_id']=job['id'];save();print('submitted',p['id'],job['id'],flush=True)
  start=time.monotonic()
  while True:
   run=client.experiment_execution(job['id'])
   if run['status'] in ['completed','failed','cancelled','interrupted']:break
   if time.monotonic()-start>240:raise RuntimeError('Still running; use saved receipt to resume')
   time.sleep(.5)
  r['execution']=run;save()
  if run['status']!='completed':raise RuntimeError(run)
  a=next(a for a in run['artifacts'] if a['name']=='apollo-trace.json');raw=httpx.get(args.url+a['download_url'],timeout=60).content
  assert hashlib.sha256(raw).hexdigest()==a['sha256'];trace=json.loads(raw)
  r['trace']=trace;r['trace_sha256_verified']=True
  compare=evidence['runs']['normal']['job_id'];j=next(j for j in trace['jobs'] if j['id']==('s-0' if p['id']=='zsl' else 'p-0-10'))
  event=next((e['id'] for e in trace['events'] if e['job_id']==j['id'] and e['kind']=='output_complete'),0)
  r['url']=args.ui+f'/#hil-sim?project=default&mode=fpga&target=apollo-sm8750&test={test["id"]}&revision=1&execution={job["id"]}&compare={compare}&job={j["id"]}&event={event}&stage=3'
  save();print(p['id'],json.dumps(trace['summary'],ensure_ascii=False),flush=True)
 baseline=evidence['runs']['normal']['trace'];base_images={i['job_id']:i for i in baseline['images']}
 for k,r in evidence['runs'].items():
  if k=='normal':continue
  assert r['test']['channels']==evidence['runs']['normal']['test']['channels']
  matched=[i for i in r['trace']['images'] if i['job_id'] in base_images]
  assert all(i['raw_sha256']==base_images[i['job_id']]['raw_sha256'] and i['ipe_yuv_sha256']==base_images[i['job_id']]['ipe_yuv_sha256'] for i in matched)
  r['matched_pixel_hashes_verified']=True
 evidence['matched_input_and_pixel_check']=True;save();print('evidence',out,flush=True)
if __name__=='__main__':main()
