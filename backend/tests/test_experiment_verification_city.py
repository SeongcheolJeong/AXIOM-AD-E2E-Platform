"""Authored contract fixtures through the normal queue; no native engine run."""
import copy
import hashlib
import json
import pytest
from sqlalchemy import select
from ade2e.adapters import Registry
from ade2e.experiment_runtime import ExperimentExecution, ExperimentHandlerMetadata, ExperimentOutputFile
from ade2e.experiment_models import ExperimentArtifactRow
from ade2e.scenario_v2_schemas import ScenarioCity
from ade2e.scenario_v2_xml import canonical_bytes,digest
from ade2e.worker import run_once
from ade2e.city_motion import scenario_fidelity,MIXED,REPLAY
from test_experiment_core import env,AuthoredHandler,create_submit,definition
from test_experiment_verification import plan,evaluate,plan_body


class CityContractHandler(AuthoredHandler):
    """Imitates the wire contract exclusively to test evidence joins."""
    def __init__(self,contact=False,corrupt_source=False,missing=False,fail=False,signals=False,bad_signal=False,replay=False,maneuver=False,bad_maneuver=False,pedestrian=False,signal_transition=False,signal_mismatch_at=None,reactive=False,bad_planner=False):
        super().__init__('carla-city-object',fail=fail)
        self.contact,self.corrupt_source,self.missing=contact,corrupt_source,missing
        self.signals,self.bad_signal=signals,bad_signal
        self.replay,self.maneuver,self.bad_maneuver,self.pedestrian=replay,maneuver,bad_maneuver,pedestrian
        self.signal_transition,self.signal_mismatch_at=signal_transition,signal_mismatch_at
        self.reactive,self.bad_planner=reactive,bad_planner
    def metadata(self):
        return ExperimentHandlerMetadata(id=self.identity,version='carla-city-object/1.0.0',kinds=['object_sim'],
            status='available',fidelity='native_carla_object_physics',cancellation_supported=True,
            description='AUTHORED CONTRACT TEST ONLY: no CARLA process or sensor observation')
    def prepare(self,spec):
        reference={'id':'fixture','revision':1,'sha256':'a'*64}
        def pose(x,y):return dict(x=x,y=y,z=0,heading_rad=0)
        scenario=ScenarioCity.model_validate({'name':'Authored verification contract fixture','map':reference,
            'duration_s':1,'timestep_s':.05 if self.signal_transition else .1,'actors':[{'id':identity,'role':'ego' if identity=='ego' else 'other',
                'asset':reference,'pose':pose(0,y),'route':[pose(0,y),pose(10,y)],'controller':{'kind':'route_follow'}}
                for identity,y in [('ego',0),('other',10)]],
            'signals':[{'id':'opendrive-light-1','initial_state':'red',
                'changes':[{'time_s':.0500005,'state':'green'}] if self.signal_transition else []}]
                if self.signals or self.signal_transition else []}).model_dump(mode='json')
        if self.replay:
            for actor in (scenario['actors'] if self.replay=='all' else [scenario['actors'][1]]):
                y=actor['pose']['y']
                actor.update(route=[],controller={**actor['controller'],'kind':'timed_replay'},
                    trajectory=[{**pose(0,y),'time_s':0.},{**pose(10,y),'time_s':1.}])
        if self.pedestrian:scenario['actors'][1]['kind']='pedestrian'
        if self.reactive:
            scenario['schema_version']='scenario-v2/3'
            scenario['actors'][0]['controller']['kind']='reactive'
            scenario['actors'][0]['destination']=pose(8,0)
        source={'scenario_id':'scenario2_fixture','scenario_revision':1,'scenario_manifest_hash':'b'*64,
                'configuration_hash':digest(scenario),'seed':42,'map_sha256':'a'*64}
        return {'schema_version':'carla-city-input/1','scenario':scenario,'source':source,
                'execution_fidelity':scenario_fidelity(scenario),
                **({'maneuver_script_sha256':'e'*64} if self.maneuver else {}),
                **({'planner_script_sha256':'f'*64} if self.reactive else {}),
                'map':{'opendrive_sha256':hashlib.sha256(b'fixture xodr').hexdigest()},'native_script_sha256':'c'*64,
                'control_script_sha256':'d'*64,'server_version':'fixture-build','fixture_is_authored':True}
    def execute(self,snapshot,work_dir,should_cancel):
        if self.fail:raise RuntimeError('Authored native infrastructure failure fixture')
        scenario=snapshot['scenario'];source=snapshot['source'];frames=[]
        dt=scenario['timestep_s'];count=round(scenario['duration_s']/dt)
        for i in range(count):
            # Explicit authored readback: a .0500005 change is first issued at
            # command .10, so commands .00 and .05 still observe red.
            signal_state='green' if (self.signal_transition and i>=2) or self.bad_signal else 'red'
            if i==self.signal_mismatch_at:signal_state='red' if signal_state=='green' else 'green'
            frames.append({'index':i,'frame':101+i,'input_frame':100+i,'time_s':(i+1)*dt,'simulation_time_s':100+(i+1)*dt,
                'actors':[{'id':identity,'x':10.*(i+1)/count,'y':float(y),'speed_mps':0. if i==count-1 else 1.,'route_error_m':0.}
                          for identity,y in [('ego',0),('other',10)]],
                'signals':[{'id':'opendrive-light-1','state':signal_state}] if scenario.get('signals') else []})
        events=[{'kind':'native_contact','actor_ids':['other'],'frame':105,'time_s':.5,
                 'provenance':'native_carla_collision_sensor'}] if self.contact else []
        measured=[{'actor_id':a['id'],'distance_travelled_m':10.-10./count,'maximum_speed_mps':1.,
            'native_contact':self.contact and a['id']=='other','lateral_error_p95_m':0. if a['route'] else None,
            'goal_error_m':abs(10.-(a.get('destination') or a['route'][-1])['x']) if a['route'] else None,'final_speed_mps':0.}
            for a in scenario['actors']]
        scene={'schema_version':'carla-city-scene/1','coordinate_system':scenario['coordinate_system'],
            'physical_sensor_validation':False,'fidelity':scenario_fidelity(scenario),
            'source':{**source,'runner_sha256':snapshot['native_script_sha256'],'control_sha256':snapshot['control_script_sha256'],
                      'adapter_version':self.metadata().version,'engine_version':snapshot['server_version'],
                      'xodr_sha256':snapshot['map']['opendrive_sha256']},
            'actors':scenario['actors'],'frames':frames,'events':events,'metrics':measured,
            'routes':{a['id']:a['route'] for a in scenario['actors']}}
        if self.corrupt_source:scene['source']['runner_sha256']='0'*64
        if self.maneuver:scene['source']['maneuver_sha256']=('0' if self.bad_maneuver else 'e')*64
        if self.reactive:scene['source']['planner_sha256']=('0' if self.bad_planner else 'f')*64
        scene_bytes=canonical_bytes(scene);scene_sha=hashlib.sha256(scene_bytes).hexdigest()
        evaluation={'schema_version':'carla-city-evaluation/1','physical_sensor_validation':False,
            'criteria':scenario['acceptance'],'source_scene_sha256':scene_sha,'measured_metrics':measured}
        documents=[('city-scene.json',scene_bytes,'application/json'),
            ('city-evaluation.json',canonical_bytes(evaluation),'application/json'),('map.xodr',b'fixture xodr','application/xml')]
        outputs=[]
        for name,content,media in documents:
            if self.missing and name=='city-evaluation.json':continue
            path=work_dir/name;path.write_bytes(content);outputs.append(ExperimentOutputFile(path,name,media))
        summary={'schema_version':'carla-city-result/1','execution_status':'completed','cleanup_status':'confirmed',
            'physical_sensor_validation':False,'source':source,'scene_sha256':scene_sha,'map_sha256':snapshot['map']['opendrive_sha256'],
            'frame_count':count,'metrics':measured,'evaluation':evaluation,
            'observers':[{'observer':{'kind':'collision','actor_id':'ego'},'status':'observed','value':False,'units':'boolean'}]}
        return ExperimentExecution(summary=summary,artifacts=outputs,fidelity=scene['fidelity'])


def city_plan():
    body=plan_body(fidelity='native_carla_object_physics');body['criteria']=[
        {'metric':'native_contact','unit':'boolean','operator':'eq','threshold':False,'actor_id':'all'},
        {'metric':'lateral_error_p95','unit':'m','operator':'lte','threshold':.5},
        {'metric':'goal_error','unit':'m','operator':'lte','threshold':2},
        {'metric':'final_speed','unit':'m/s','operator':'lte','threshold':.2}]
    body['criteria'].append({'metric':'distance_travelled','unit':'m','operator':'gte','threshold':2})
    return body


def complete(env,handler,key='city'):
    db,handlers,client=env;handlers.register(handler,replace=True)
    _,execution=create_submit(client,definition(adapter=handler.identity),key)
    result=run_once(db,Registry(),'authored-city-contract',job_id=execution['id'],experiment_registry=handlers)
    return result


def test_city_sealed_fixed_metrics_are_evaluable_without_native_adapter_import(env):
    db,_,client=env;execution=complete(env,CityContractHandler())
    assert execution.status=='completed'
    created=plan(client,city_plan());result=evaluate(client,created['id'],[execution.id])
    assert result['verdict']=='passed' and result['is_safety_validation'] is False
    for item in result['entries'][0]['criteria']:
        observation=item['observation']
        assert observation['source_evaluator']=='native-city-observers/1'
        assert observation['denominator']==10 and observation['evaluated']==10
        assert observation['source_scene_sha256']==execution.summary['scene_sha256']
        assert observation['physical_sensor_validation'] is False
    assert result['entries'][0]['criteria'][0]['observation']['provenance']=='native_collision_sensor_events'


def test_other_actor_native_contact_is_sut_failure_not_infrastructure_failure(env):
    _,_,client=env;execution=complete(env,CityContractHandler(contact=True))
    result=evaluate(client,plan(client,city_plan())['id'],[execution.id])
    assert result['counts']['sut_failure']==1 and result['counts']['infrastructure_failure']==0
    assert result['entries'][0]['criteria'][0]['observation']['value'] is True
    ego_only=city_plan();ego_only['criteria'][0]['actor_id']='ego'
    ego_result=evaluate(client,plan(client,ego_only,'ego-only')['id'],[execution.id],key='ego-only')
    assert ego_result['verdict']=='passed'


def test_legacy_sampled_collision_never_aliases_native_contact(env):
    _,_,client=env;execution=complete(env,CityContractHandler())
    body=plan_body(fidelity='native_carla_object_physics',metric='collision',unit='boolean',threshold=False)
    body['criteria'][0]['operator']='eq'
    result=evaluate(client,plan(client,body)['id'],[execution.id])
    assert result['verdict']=='not_evaluated'
    assert result['entries'][0]['criteria'][0]['observation']['reason']=='metric_not_in_native_city_profile'


def test_missing_original_is_unevaluated_and_corrupt_original_source_refuses_capture(env):
    _,_,client=env;execution=complete(env,CityContractHandler(missing=True),'missing-native')
    created=plan(client,city_plan())
    result=evaluate(client,created['id'],[execution.id]);assert result['verdict']=='not_evaluated'
    corrupt=complete(env,CityContractHandler(corrupt_source=True),'corrupt-native')
    response=client.post(f'/api/v1/experiment-verification-plans/{created["id"]}/evaluations',
        json={'plan_revision':1,'execution_ids':[corrupt.id]},headers={'Idempotency-Key':'corrupt'})
    assert response.status_code==409,response.text


def test_native_artifact_changed_after_sealing_is_not_accepted(env):
    db,_,client=env;execution=complete(env,CityContractHandler())
    created=plan(client,city_plan())
    with db.read_session() as session:
        artifact=session.scalar(select(ExperimentArtifactRow).where(ExperimentArtifactRow.job_id==execution.id,
            ExperimentArtifactRow.name=='city-evaluation.json'))
        path=db.experiment_dir/'artifacts'/artifact.storage_key
    path.write_bytes(b'{"fabricated":"pass"}')
    response=client.post(f'/api/v1/experiment-verification-plans/{created["id"]}/evaluations',
        json={'plan_revision':1,'execution_ids':[execution.id]},headers={'Idempotency-Key':'corrupt-original'})
    assert response.status_code==409,response.text


def test_city_infrastructure_failure_and_typed_fidelity_gate_stay_distinct(env):
    _,_,client=env;execution=complete(env,CityContractHandler(fail=True))
    assert execution.status=='failed'
    result=evaluate(client,plan(client,city_plan())['id'],[execution.id])
    assert result['counts']['infrastructure_failure']==1 and result['verdict']=='not_evaluated'
    unsupported=city_plan();unsupported['allowed_fidelities']=['authored_planar_reference']
    response=client.post('/api/v1/experiment-verification-plans',json=unsupported,headers={'Idempotency-Key':'wrong-native-fidelity'})
    assert response.status_code==422,response.text
    capabilities=client.get('/api/v1/experiment-verification-plans/capabilities').json()
    assert capabilities['profiles'][0]['id']=='native-city-observers/1'


def test_fixed_red_inputs_require_retained_native_signal_observations(env):
    _,_,client=env;execution=complete(env,CityContractHandler(signals=True),'fixed-red')
    created=plan(client,city_plan())
    result=evaluate(client,created['id'],[execution.id])
    assert result['verdict']=='passed'
    assert 'no stop-line' in result['entries'][0]['criteria'][0]['observation']['signal_input_scope']
    wrong=complete(env,CityContractHandler(signals=True,bad_signal=True),'wrong-native-red')
    response=client.post(f'/api/v1/experiment-verification-plans/{created["id"]}/evaluations',
        json={'plan_revision':1,'execution_ids':[wrong.id]},headers={'Idempotency-Key':'wrong-red-eval'})
    assert response.status_code==409,response.text


def test_signal_change_just_after_command_tick_preserves_native_readback(env):
    _,_,client=env;execution=complete(env,CityContractHandler(signal_transition=True),'signal-transition')
    result=evaluate(client,plan(client,city_plan())['id'],[execution.id])
    assert result['verdict']=='passed'
    for item in result['entries'][0]['criteria']:
        observation=item['observation']
        assert observation['denominator']==20
        assert observation['signal_schedule_evaluator_version']=='native-city-command-schedule/1'
        assert observation['signal_command_tolerance_s']==1e-9


@pytest.mark.parametrize('mismatch_at',[1,2],ids=['early-green-at-command-005','late-red-at-command-010'])
def test_signal_transition_rejects_genuine_native_state_mismatch(env,mismatch_at):
    _,_,client=env
    execution=complete(env,CityContractHandler(signal_transition=True,signal_mismatch_at=mismatch_at),'signal-mismatch')
    created=plan(client,city_plan())
    response=client.post(f'/api/v1/experiment-verification-plans/{created["id"]}/evaluations',
        json={'plan_revision':1,'execution_ids':[execution.id]},headers={'Idempotency-Key':'signal-mismatch-eval'})
    assert response.status_code==409,response.text
    assert 'Native signal observation differs from frozen input schedule' in response.json()['detail']


def test_replay_cannot_qualify_physical_motion_or_mixed_contact_absence(env):
    _,_,client=env;execution=complete(env,CityContractHandler(replay=True))
    assert execution.fidelity==MIXED
    body=city_plan();body['allowed_fidelities']=[MIXED];body['criteria'].append({'metric':'distance_travelled','unit':'m','operator':'gte','threshold':2,'actor_id':'other'})
    result=evaluate(client,plan(client,body)['id'],[execution.id])
    assert result['verdict']=='not_evaluated'
    checks=result['entries'][0]['criteria']
    assert checks[0]['observation']['reason']=='mixed_timed_replay_contact_is_diagnostic_only'
    assert checks[-1]['observation']['reason']=='timed_position_replay_is_diagnostic_only'
    assert all(c['status']=='passed' for c in checks[1:-1])


def test_pure_replay_preview_and_receipt_are_diagnostic_and_outside_physical_plan(env):
    _,handlers,client=env;handler=CityContractHandler(replay='all');handlers.register(handler,replace=True)
    preview=client.post('/api/v1/experiments/preview',json=definition(adapter=handler.identity))
    assert preview.status_code==200,preview.text
    assert preview.json()['execution_fidelity']==REPLAY
    execution=complete(env,handler)
    assert execution.fidelity==REPLAY
    physical=evaluate(client,plan(client,city_plan())['id'],[execution.id])
    assert physical['counts']['unsupported']==1
    body=city_plan();body['allowed_fidelities']=[REPLAY]
    diagnostic=evaluate(client,plan(client,body,'replay-plan')['id'],[execution.id],key='replay-eval')
    assert diagnostic['verdict']=='not_evaluated'
    assert all(c['status']=='unevaluated' for c in diagnostic['entries'][0]['criteria'])
    assert all(c['observation']['scope']=='timed_position_replay_diagnostic_only' for c in diagnostic['entries'][0]['criteria'])


def test_optional_maneuver_hash_is_pinned_without_breaking_prior_evidence(env):
    _,_,client=env;created=plan(client,city_plan())
    execution=complete(env,CityContractHandler(maneuver=True),'with-maneuver')
    assert evaluate(client,created['id'],[execution.id])['verdict']=='passed'
    wrong=complete(env,CityContractHandler(maneuver=True,bad_maneuver=True),'wrong-maneuver')
    response=client.post(f'/api/v1/experiment-verification-plans/{created["id"]}/evaluations',
        json={'plan_revision':1,'execution_ids':[wrong.id]},headers={'Idempotency-Key':'wrong-maneuver-eval'})
    assert response.status_code==409,response.text


def test_uninstrumented_pedestrian_contact_absence_is_unevaluated(env):
    _,_,client=env;execution=complete(env,CityContractHandler(pedestrian=True))
    body=city_plan();body['criteria'][0]['actor_id']='other'
    result=evaluate(client,plan(client,body)['id'],[execution.id])
    assert result['verdict']=='not_evaluated'
    observation=result['entries'][0]['criteria'][0]['observation']
    assert observation['reason']=='actor_contact_absence_is_not_fully_instrumented'
    assert observation['contact_observation_scope']['sensor_actor_ids']==['ego']


def test_reactive_destination_uses_pinned_goal_and_captures_planner_version(env):
    _,_,client=env;execution=complete(env,CityContractHandler(reactive=True),'reactive-destination')
    result=evaluate(client,plan(client,city_plan())['id'],[execution.id])
    assert result['verdict']=='passed'
    goal=next(c['observation'] for c in result['entries'][0]['criteria'] if c['criterion']['metric']=='goal_error')
    assert goal['value']==2.0  # Retained position 10, explicit destination 8; guide ends at 10.
    assert goal['reactive_evaluator_version']=='native-city-reactive-destination/1'
    assert goal['source_planner_sha256']=='f'*64


def test_reactive_planner_source_mismatch_refuses_verification_capture(env):
    _,_,client=env;execution=complete(env,CityContractHandler(reactive=True,bad_planner=True),'reactive-mismatch')
    created=plan(client,city_plan())
    response=client.post(f'/api/v1/experiment-verification-plans/{created["id"]}/evaluations',
        json={'plan_revision':1,'execution_ids':[execution.id]},headers={'Idempotency-Key':'bad-reactive-source'})
    assert response.status_code==409,response.text
    assert 'reactive planner version differs' in response.json()['detail']
