"""Engine-neutral queue handler for an explicitly configured optional esmini process."""
import hashlib,json,math,os,subprocess
from pathlib import Path
from fastapi import HTTPException
from .experiment_runtime import ExperimentHandlerMetadata,ExperimentExecution,ExperimentOutputFile
from .scenario_v2 import get_scenario
from .scenario_v2_schemas import ScenarioV2
from .scenario_v2_xml import canonical_bytes,digest,scenario_xml,road_xml,sweep_variants
from .object_sim_observers import evaluate_observers,VERSION as OBSERVER_VERSION
from .object_scene import build_object_scene, map_geometry, sha256, trajectory_preview, validate_native_frames

ADAPTER_ID='esmini-object'
ADAPTER_VERSION='0.2.0+esmini3.8.2'
PINNED_BINARY_SHA256='7f31d9a52a1da6c9d062a89f15b714f004308eb0d3c007107f09cce85afbf619'

class EsminiExperimentHandler:
    def __init__(self,database=None,command=None):
        self.database=database
        self.command=command if command is not None else json.loads(os.environ.get('AXIOM_ESMINI_COMMAND','null'))
        if self.command is not None and (not isinstance(self.command,list) or not self.command or any(not isinstance(v,str) or not v or len(v)>2000 for v in self.command)):
            raise ValueError('AXIOM_ESMINI_COMMAND must be a nonempty JSON string array, configured by the operator')
        self.runner=Path(os.environ.get('AXIOM_ESMINI_RUNNER_SOURCE',str(Path(__file__).resolve().parents[3]/'integrations/esmini/src/axiom_esmini/runner.py')))

    def metadata(self):
        return ExperimentHandlerMetadata(id=ADAPTER_ID,version=ADAPTER_VERSION,kinds=['object_sim'],
          status='available' if self.command else 'not_configured',fidelity='authored_object_kinematics',
          cancellation_supported=False,description='Pinned esmini v3.8.2 headless CPU process; authored actors and observers, no sensor physics or learned controller')

    def prepare(self,spec):
        if not self.command:raise HTTPException(422,'esmini native command is not configured; no synthetic fallback')
        if spec.kind!='object_sim' or spec.comparison_kind!='single' or spec.model or spec.sensor:
            raise HTTPException(422,'esmini object adapter supports single authored behavior execution; model/sensor controllers unsupported')
        if spec.evaluator not in (None,{'id':'object-trace-observers','version':'1'}):
            raise HTTPException(422,'Unsupported object observer evaluator')
        allowed={'scenario_id','scenario_revision','configuration_hash'}
        if set(spec.input)-allowed or not isinstance(spec.input.get('scenario_id'),str) or type(spec.input.get('scenario_revision'))!=int:
            raise HTTPException(422,'Pin scenario_id and integer scenario_revision; unsupported input keys rejected')
        with self.database.read_session() as session:source=get_scenario(session,spec.input['scenario_id'],spec.input['scenario_revision'])
        if source['project_id']!=spec.project_id:raise HTTPException(403,'Scenario belongs to another project')
        parsed=ScenarioV2.model_validate(source['scenario']);variants=list(sweep_variants(parsed))
        requested=spec.input.get('configuration_hash')
        if not requested and len(variants)>1:raise HTTPException(422,'Select a configuration_hash from the sweep preview')
        selected=next((v for v in variants if requested is None or v['configuration_hash']==requested),None)
        if selected is None:raise HTTPException(422,'Configuration hash is not in the pinned scenario sweep')
        scenario=ScenarioV2.model_validate(selected['scenario'])
        request={'schema_version':'esmini-native-request/1','scenario_xml':scenario_xml(scenario),'road_xml':road_xml(scenario),
                 'duration_s':scenario.duration_s,'timestep_s':scenario.timestep_s,'seed':spec.seed,'actor_ids':[a.id for a in scenario.actors]}
        map_geometry(scenario,request['road_xml'])
        return {'schema_version':'object-sim-input/1','scenario_id':source['id'],'scenario_revision':source['revision'],
          'scenario_manifest_hash':source['manifest_hash'],'project_id':source['project_id'],'configuration_hash':selected['configuration_hash'],
          'scenario':selected['scenario'],'selection':selected['selection'],'seed':spec.seed,'native_request':request,
          'adapter_version':ADAPTER_VERSION,'evaluator_version':OBSERVER_VERSION,'command_hash':digest(self.command),
          'runner_sha256':hashlib.sha256(self.runner.read_bytes()).hexdigest(),'engine_binary_sha256':PINNED_BINARY_SHA256}

    def execute(self,snapshot,work_dir,should_cancel):
        if should_cancel():raise RuntimeError('Object experiment canceled before native execution')
        if snapshot.get('adapter_version')!=ADAPTER_VERSION:
            raise RuntimeError('Pinned object adapter version is unavailable; prior sealed outputs are not migrated')
        if snapshot['command_hash']!=digest(self.command) or snapshot['runner_sha256']!=hashlib.sha256(self.runner.read_bytes()).hexdigest():
            raise RuntimeError('Configured native command or adapter code changed after preview')
        request=snapshot['native_request'];work_dir.mkdir(parents=True,exist_ok=True)
        scenario=ScenarioV2.model_validate(snapshot['scenario'])
        if (digest(snapshot['scenario'])!=snapshot['configuration_hash'] or
            request['scenario_xml']!=scenario_xml(scenario) or request['road_xml']!=road_xml(scenario) or
            request['actor_ids']!=[actor.id for actor in scenario.actors] or request['seed']!=snapshot['seed'] or
            request['duration_s']!=scenario.duration_s or request['timestep_s']!=scenario.timestep_s):
            raise RuntimeError('Native request differs from the pinned scenario configuration')
        map_geometry(scenario,request['road_xml'])
        stdout=work_dir/'native-response.json'
        with stdout.open('wb') as output:
            result=subprocess.run(self.command,input=canonical_bytes(request),stdout=output,stderr=subprocess.PIPE,timeout=90)
        if result.returncode:raise RuntimeError('esmini process failed: '+result.stderr.decode(errors='replace')[-3000:])
        if stdout.stat().st_size>32*1024*1024:raise RuntimeError('Native response exceeds 32 MiB; not truncated')
        native=json.loads(stdout.read_bytes())
        for key,expected in [('schema_version','esmini-native-result/1'),('engine_version','v3.8.2'),('engine_build','6400'),
                             ('runner_sha256',snapshot['runner_sha256']),('seed',snapshot['seed'])]:
            if native.get(key)!=expected:raise RuntimeError('Native receipt differs from pinned '+key)
        if native.get('binary_sha256')!=snapshot['engine_binary_sha256']:raise RuntimeError('Native binary differs from the pinned official release content hash')
        for name in ('scenario_xml','road_xml'):
            if native[name+'_sha256']!=hashlib.sha256(request[name].encode()).hexdigest():raise RuntimeError('Native source hash mismatch')
        if native['native_csv_sha256']!=hashlib.sha256(native['native_csv'].encode()).hexdigest():raise RuntimeError('Native CSV integrity mismatch')
        frames=native['frames'];expected_ids=set(request['actor_ids'])
        if len(frames)<2 or len(frames)>math.ceil(request['duration_s']/request['timestep_s'])+4:
            raise RuntimeError('Native frame count invalid')
        validate_native_frames(frames,expected_ids)
        if frames[-1]['time_s']<request['duration_s']-request['timestep_s']:
            raise RuntimeError('Native trace ended before the requested observation window')
        trace_content=canonical_bytes({'schema_version':'object-trace/1','coordinate_system':snapshot['scenario']['coordinate_system'],'frames':frames})
        scene=build_object_scene(snapshot,native,trace_content);scene_content=canonical_bytes(scene)
        observations=evaluate_observers(snapshot['scenario'],frames)
        counts={status:sum(o['status']==status for o in observations) for status in ('passed','failed','observed','unevaluated')}
        files=[]
        for name,content,media in [('scenario.xosc',request['scenario_xml'],'application/xml'),('road.xodr',request['road_xml'],'application/xml'),
          ('native-trace.csv',native['native_csv'],'text/csv'),('engine.log',native['native_log'],'text/plain'),
          ('object-trace.json',trace_content.decode(),'application/json'),
          ('object-scene.json',scene_content.decode(),'application/json')]:
            path=work_dir/name;path.write_bytes(content.encode());files.append(ExperimentOutputFile(path=path,name=name,media_type=media))
        files.append(ExperimentOutputFile(path=stdout,name='native-response.json',media_type='application/json'))
        receipt={key:value for key,value in native.items() if key not in {'frames','native_csv','native_log','engine_stdout'}}
        summary={'schema_version':'object-sim-evidence/1','scenario_id':snapshot['scenario_id'],'scenario_revision':snapshot['scenario_revision'],
          'scenario_manifest_hash':snapshot['scenario_manifest_hash'],'configuration_hash':snapshot['configuration_hash'],'seed':snapshot['seed'],
          'adapter_version':ADAPTER_VERSION,'engine_receipt':receipt,'execution_status':'completed','evaluation_status':'completed' if not counts['unevaluated'] else 'partial',
          'verdict':'failed' if counts['failed'] else 'not_evaluated' if counts['unevaluated'] else 'passed',
          'observers':observations,'observer_counts':counts,'sample_count':len(frames),'observed_duration_s':frames[-1]['time_s']-frames[0]['time_s'],
          'trajectory_preview':trajectory_preview(frames),
          'object_scene':{'schema_version':'object-scene/1','artifact_name':'object-scene.json','sha256':sha256(scene_content),
                          'event_count':len(scene['events'])},
          'original_artifact_hashes':{'object-scene.json':sha256(scene_content),'object-trace.json':sha256(trace_content),
                                     'road.xodr':native['road_xml_sha256'],'scenario.xosc':native['scenario_xml_sha256'],
                                     'native-trace.csv':native['native_csv_sha256'],'engine.log':sha256(native['native_log'])},
          'limitations':['Authored object kinematics; no learned controller','Constant-velocity TTC is a diagnostic estimate','No physical sensor or real-world safety validation','Different seeds in deterministic templates do not prove independent scene diversity']}
        return ExperimentExecution(summary=summary,artifacts=files,fidelity='authored_object_kinematics')
