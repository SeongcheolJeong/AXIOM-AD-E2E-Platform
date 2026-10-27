"""Opt-in actual native 36-run API/queue/worker acceptance, isolated from operating DB."""
import argparse,hashlib,json,os,sys
from pathlib import Path
from fastapi import APIRouter,Depends,FastAPI,Request
from fastapi.testclient import TestClient
from ade2e.database import Database,Settings
from ade2e.adapters import Registry
from ade2e.experiment_runtime import ExperimentRegistry
from ade2e.experiment_routes import register_experiment_routes
from ade2e.object_sim import EsminiExperimentHandler
from ade2e.scenario_v2 import register_scenario_v2_routes
from ade2e.scenario_v2_templates import templates
from ade2e.team_access import Principal
from ade2e.worker import run_once

def acceptance(directory,command):
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    if (directory/'data/platform.db').exists():raise ValueError('Use a new empty acceptance directory; existing evidence is preserved')
    db=Database(Settings(data_dir=directory/'data',seed_examples=False));db.initialize()
    handler=EsminiExperimentHandler(database=db,command=command);registry=ExperimentRegistry([handler],load_plugins=False)
    def auth(request:Request):request.state.principal=Principal('legacy')
    api=APIRouter(prefix='/api/v1',dependencies=[Depends(auth)])
    register_scenario_v2_routes(api,db);register_experiment_routes(api,db,registry)
    app=FastAPI();app.include_router(api);client=TestClient(app)
    def post(path,body,key=None):
        response=client.post('/api/v1'+path,json=body,headers={'Idempotency-Key':key} if key else {})
        if response.status_code not in (200,201,202):raise RuntimeError(response.text)
        return response.json()
    records=[];inputs=[]
    for item in templates():
        scenario=item['scenario'];scenario['sweep']=[{'actor_id':'ego','field':'speed_mps','values':[10,12]}]
        source=post('/scenario-v2',{'project_id':'default','scenario':scenario},'scenario-'+item['id'])
        preview=post('/scenario-v2/'+source['id']+'/sweep-preview',{'seeds':[42,43,44]})
        assert preview['job_count']==6 and preview['submitted_jobs']==0
        for position,job in enumerate(preview['jobs']):
            spec={'project_id':'default','name':item['id']+' native '+str(position),'kind':'object_sim','adapter_id':'esmini-object',
                  'input':{'scenario_id':source['id'],'scenario_revision':source['revision'],'configuration_hash':job['configuration_hash']},'seed':job['seed']}
            execution_preview=post('/experiments/preview',spec)
            experiment=post('/experiments',spec,'experiment-'+item['id']+'-'+str(position))
            assert experiment['preview_hash']==execution_preview['preview_hash']
            execution=post('/experiments/'+experiment['id']+'/executions',{'preview_hash':execution_preview['preview_hash']},'run-'+item['id']+'-'+str(position))
            inputs.append((item['id'],job,execution['id']))
    for index,(template,selection,job_id) in enumerate(inputs):
        finished=run_once(db,Registry(load_plugins=False),'object-native-acceptance',job_id=job_id,experiment_registry=registry)
        if finished.status!='completed':raise RuntimeError(str(finished.model_dump()))
        response=client.get('/api/v1/experiments/executions/'+job_id);assert response.status_code==200,response.text
        result=response.json();assert result['summary']['engine_receipt']['engine_version']=='v3.8.2'
        assert result['summary']['seed']==selection['seed']
        artifact_hashes={}
        for artifact in result['artifacts']:
            download=client.get(artifact['download_url']);assert download.status_code==200,download.text
            assert len(download.content)==artifact['size_bytes']
            checksum=hashlib.sha256(download.content).hexdigest();assert checksum==artifact['sha256']
            artifact_hashes[artifact['name']]=checksum
        receipt=client.get(result['download_url']);assert receipt.status_code==200
        assert hashlib.sha256(receipt.content).hexdigest()==result['manifest_hash']
        record={'template':template,'selection':selection['selection'],'configuration_hash':selection['configuration_hash'],'seed':selection['seed'],
                'job_id':job_id,'execution_status':result['status'],'verdict':result['summary']['verdict'],
                'counts':result['summary']['observer_counts'],'native_elapsed_s':result['summary']['engine_receipt']['elapsed_s'],
                'sample_count':result['summary']['sample_count'],'manifest_hash':result['manifest_hash'],'artifact_hashes':artifact_hashes,
                'native_binary_sha256':result['summary']['engine_receipt']['binary_sha256']}
        records.append(record);print(f'{index+1}/36 {template} seed={selection["seed"]} execution=completed verdict={record["verdict"]}',flush=True)
    # Deterministic seed repeats are expected; changed authored speed must change native trace.
    for template in {r['template'] for r in records}:
        grouped=[r for r in records if r['template']==template]
        hashes={r['configuration_hash']:r['artifact_hashes']['object-trace.json'] for r in grouped}
        assert len(set(hashes.values()))==2,'Speed configuration did not affect native trace'
        for configuration in hashes:
            assert len({r['artifact_hashes']['object-trace.json'] for r in grouped if r['configuration_hash']==configuration})==1
    source_paths=[Path(__file__),Path('backend/src/ade2e/object_sim.py'),Path('backend/src/ade2e/object_sim_observers.py'),
                  Path('backend/src/ade2e/scenario_v2_schemas.py'),Path('backend/src/ade2e/scenario_v2_xml.py'),Path('integrations/esmini/src/axiom_esmini/runner.py')]
    report={'schema_version':'object-native-acceptance/1','scope':'Actual native esmini via normal route handlers and durable existing queue/worker; isolated acceptance database',
      'engine':{'version':'v3.8.2','build':'6400','official_linux_archive_sha256':'bf9bfd637a76910910a1d12b7e2584e3637342d89ef6b6f4bcc041af505f8711'},
      'run_count':len(records),'completed':sum(r['execution_status']=='completed' for r in records),'records':records,
      'seed_result':'All selected templates are deterministic; different seeds do not establish independent randomness',
      'fidelity':'authored_object_kinematics','physical_sensor_validation':False,'sut_quality_pass_claim':False,
      'code_hashes':{str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths}}
    (directory/'acceptance.json').write_text(json.dumps(report,indent=2));db.engine.dispose()
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--directory',required=True);parser.add_argument('--command-json',default=os.environ.get('AXIOM_ESMINI_COMMAND'))
    args=parser.parse_args()
    if not args.command_json:parser.error('Configure an explicit native command; there is no mock fallback')
    acceptance(args.directory,json.loads(args.command_json))
