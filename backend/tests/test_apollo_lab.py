"""Real SimPy + CPU pixels + queued API contracts, no mocked chip execution."""
import copy,hashlib,json
import numpy as np
import pytest
from fastapi import APIRouter,Depends,FastAPI,Request
from fastapi.testclient import TestClient
from ade2e.database import Database,Settings
from ade2e.adapters import Registry
from ade2e.experiment_runtime import ExperimentRegistry
from ade2e.experiment_routes import register_experiment_routes
from ade2e.fpga_lab import register_fpga_lab_routes
from ade2e.apollo_lab import register_apollo_lab_routes,templates_payload,asset_path
from ade2e.apollo_lab_schemas import RawMetadata
from ade2e.team_access import authenticate_request,register_team_routes
from ade2e.worker import run_once
from axiom_fpga_lab import TimingHandler
from axiom_apollo_lab import ApolloHandler
from axiom_apollo_lab.engine import simulate
from axiom_apollo_lab.pixels import decode,synthetic,process,image_hash
BASE='/api/v1/hil-sim/apollo-lab'
def ref(d):return {k:d[k] for k in ('id','revision','sha256')}
def preset(index=0,count=4,frames=12):
 p=copy.deepcopy(templates_payload()['items'][index]);p['channels']=p['channels'][:count]
 for c in p['channels']:
  c['config'].update(width=32,height=24,frames=frames);c['raw']['metadata'].update(width=32,height=24)
 return p

def hydrated(p):return [c|{'design':{'config':c['config']}} for c in p['channels']]
def run(p):return simulate(p['profile']['config'],hydrated(p),p['captures'],p['seed'])

@pytest.mark.parametrize('bits',[10,12])
def test_mipi_decode_with_stride(bits):
 m=RawMetadata(width=8,height=8,bits=bits,packing='mipi',black_level=0,white_level=2**bits-1,stride_bytes=16)
 values=np.arange(64,dtype=np.uint16).reshape(8,8)*13
 rows=[]
 for row in values:
  b=[]
  if bits==12:
   for a,c in row.reshape(-1,2):b.extend([int(a>>4),int(c>>4),int((a&15)|((c&15)<<4))])
  else:
   for g in row.reshape(-1,4):b.extend([int(x>>2) for x in g]+[sum(int(x&3)<<(2*i) for i,x in enumerate(g))])
  rows.append(bytes(b)+bytes(16-len(b)))
 assert np.array_equal(decode(b''.join(rows),m.model_dump()),values)
 with pytest.raises(ValueError):decode(b'\0',m.model_dump())

@pytest.mark.parametrize('pattern',['RGGB','BGGR','GRBG','GBRG'])
def test_bayer_red_and_black(pattern):
 from axiom_apollo_lab.pixels import BAYER
 m=RawMetadata(width=32,height=24,bayer=pattern,black_level=64);p=preset()['profile']['config']|{'wb':[1.,1.,1.],'denoise':0.,'sharpen':0.}
 raw=np.full((24,32),64,np.uint16)
 for y in range(2):
  for x in range(2):
   if BAYER[pattern][y][x]==0:raw[y::2,x::2]=4095
 images,stats=process(raw,m.model_dump(),p)
 assert images[2][4:-4,4:-4,0].mean()>250
 assert images[2][4:-4,4:-4,1:].mean()<4
 black,_=process(np.full_like(raw,64),m.model_dump(),p)
 assert black[-1].max()<=1
 assert sum(stats['histogram'])==raw.size

def test_shared_resources_zsl_pin_and_determinism():
 p=preset(4);a=run(p);b=run(p)
 assert a==b
 assert a['summary']['snapshot']['completed']==3
 assert a['summary']['preview']['completed']==48
 assert a['summary']['final_memory_bytes']==0
 byid={j['id']:j for j in a['jobs']}
 for j in a['jobs']:
  if j['kind']=='snapshot':
   parent=byid[j['parent_job']]
   assert parent['frame']==j['frame'] and j['raw_ready_ns']<=j['request_ns']
   assert j['frame_age_ns']==j['request_ns']-parent['source_ns']
 for node in ['TFE0','OFE','IPE']:
  spans=sorted((s['start_ns'],s['end_ns']) for j in a['jobs'] for s in j['stages'] if s['node']==node)
  assert all(a[1]<=b[0] for a,b in zip(spans,spans[1:]))

def test_empty_zsl_queue_overflow_and_memory_release():
 p=preset();p['captures']=[{'channel':0,'at_us':1}];p['profile']['config'].update(ofe_us=100000,queue_frames=1)
 r=run(p);assert r['summary']['snapshot']['dropped']==1
 assert any(j['loss_reason']=='OFE_queue_full' for j in r['jobs'])
 assert r['summary']['final_memory_bytes']==0
 p=preset(count=4,frames=2)
 for c in p['channels']:c['config'].update(width=640,height=480);c['raw']['metadata'].update(width=640,height=480)
 p['profile']['config']['memory_bytes']=1048576
 r=run(p);assert any(j['loss_reason']=='memory_capacity' for j in r['jobs'])
 assert r['summary']['final_memory_bytes']==0

def test_ofe_wait_and_cancellation():
 p=preset();a=run(p);p['profile']['config']['ofe_us']=20000;b=run(p)
 assert b['summary']['resources']['OFE']['wait_ns']>a['summary']['resources']['OFE']['wait_ns']
 from ade2e.adapters import AdapterCancelledBeforeDispatch
 with pytest.raises(AdapterCancelledBeforeDispatch):simulate(p['profile']['config'],hydrated(p),[],should_cancel=lambda:True)

@pytest.fixture
def env(tmp_path):
 settings=Settings(data_dir=tmp_path,api_token='test',team_mode=True,seed_examples=False);db=Database(settings);db.initialize()
 registry=ExperimentRegistry([TimingHandler(db),ApolloHandler(db)],load_plugins=False)
 def auth(request:Request):
  header=request.headers.get('authorization','');authenticate_request(request,db,settings,header[7:] if header.startswith('Bearer ') else None)
 router=APIRouter(prefix='/api/v1',dependencies=[Depends(auth)])
 register_team_routes(router,db);register_fpga_lab_routes(router,db,registry);register_apollo_lab_routes(router,db,registry);register_experiment_routes(router,db,registry)
 app=FastAPI();app.include_router(router)
 with TestClient(app,headers={'Authorization':'Bearer test'}) as client:yield db,client,registry

def post(c,path,body,key):
 r=c.post(path,json=body,headers={'Idempotency-Key':key});assert r.status_code==201,r.text;return r.json()
def author(env,p=None,key='a'):
 db,c,registry=env;p=p or preset(4,1)
 profile=post(c,BASE+'/profiles',dict(name=p['name'],config=p['profile']['config']),key+'profile');cs=[]
 for ch in p['channels']:
  d=post(c,'/api/v1/hil-sim/fpga-lab/designs',dict(name='source',config=ch['config'],seed=42),key+'ch'+str(ch['channel']))
  cs.append({k:v for k,v in ch.items() if k!='config'}|{'design_ref':ref(d)})
 body=dict(name=p['name'],profile_ref=ref(profile),channels=cs,captures=p['captures'],seed=42)
 t=post(c,BASE+'/tests',body,key+'test');assert post(c,BASE+'/tests',body,key+'test')==t
 return t,body

def execute(env,t,key='run'):
 db,c,reg=env
 r=c.post(BASE+'/tests/'+t['id']+'/preview',json={'project_id':'default','revision':t['revision'],'sha256':t['sha256']});assert r.status_code==200,r.text
 preview=r.json();e=post(c,'/api/v1/experiments',preview['experiment'],key)
 args=dict(json={'preview_hash':preview['preview_hash']},headers={'Idempotency-Key':key})
 r=c.post('/api/v1/experiments/'+e['id']+'/executions',**args);assert r.status_code==202,r.text;job=r.json()['id']
 assert c.post('/api/v1/experiments/'+e['id']+'/executions',**args).json()['id']==job
 run_once(db,Registry(),'apollo-real-worker',job_id=job,experiment_registry=reg)
 r=c.get('/api/v1/experiments/executions/'+job).json();assert r['status']=='completed',r
 a=next(a for a in r['artifacts'] if a['name']=='apollo-trace.json');content=c.get(a['download_url']).content
 assert hashlib.sha256(content).hexdigest()==a['sha256']
 return r,json.loads(content)

def test_queue_sdk_export_and_immutable_revision(env):
 from axiom_sdk import AxiomClient
 t,body=author(env);r,trace=execute(env,t)
 assert trace['summary']['snapshot']['completed']==3
 assert len(trace['images'])==15
 assert trace['summary']['final_memory_bytes']==0
 for a in r['artifacts']:
  content=env[1].get(a['download_url']).content
  assert hashlib.sha256(content).hexdigest()==a['sha256']
 sdk=AxiomClient('http://testserver',token='test',transport=env[1]._transport)
 assert sdk.apollo_lab_test(t['id'],revision=1)['sha256']==t['sha256']
 assert env[1].get('/api/v1/experiments/executions/'+r['id']+'/export').status_code==200
 assert sdk.apollo_lab_executions()['items'][0]['id']==r['id']
 bad=body|{'id':t['id'],'expected_revision':0}
 assert env[1].post(BASE+'/tests',json=bad,headers={'Idempotency-Key':'stale'}).status_code==409
 bad=body|{'channels':[body['channels'][0],body['channels'][0]]}
 assert env[1].post(BASE+'/tests',json=bad,headers={'Idempotency-Key':'dup'}).status_code==422
 assert env[1].get(BASE+'/tests/'+t['id'],params={'revision':1,'project_id':'other'}).status_code in [403,404]

def test_raw_upload_repeat_and_tamper(env):
 db,c,registry=env;m=RawMetadata(width=32,height=24);data=np.full((24,32),200,np.uint16).tobytes()
 args=dict(params={'metadata':m.model_dump_json()},headers={'Content-Type':'application/octet-stream'})
 assert c.post(BASE+'/assets',content=data[:-1],**args).status_code==422
 r=c.post(BASE+'/assets',content=data,**args);assert r.status_code==201,r.text;a=r.json()
 p=preset(0,1,3);p['channels'][0]['raw'].update(asset_ref=ref(a),repeat=True)
 t,_=author(env,p);r,trace=execute(env,t)
 assert trace['images'][0]['source_sha256']==a['content_sha256']
 assert len({i['raw_sha256'] for i in trace['images']})==1
 path=asset_path(db,a);path.write_bytes(b'X'*len(data))
 r=c.post(BASE+'/tests/'+t['id']+'/preview',json={'project_id':'default','revision':1,'sha256':t['sha256']})
 assert r.status_code==422,r.text
