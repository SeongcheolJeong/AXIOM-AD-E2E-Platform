"""Core checks over authored raw-body evidence, not simulator validation."""
from copy import deepcopy
import json
import math

import pytest

from ade2e.schemas import ArtifactPayload
from ade2e.service import canonical_bytes
from ade2e.simulation_context import execution_correlation
from ade2e.vehicle_response import (VehicleResponse, validate_vehicle_response,
    SCALAR_FIELDS, WHEEL_FIELDS, TELEMETRY_FIELDS, TELEMETRY_SCALARS, float32)
from test_simulation_evidence import chain, context


def unavailable():
    return {'status':'unavailable','value':None,'reason':'Not exposed by authored fixture'}


def available(value):
    return {'status':'available','value':value,'reason':None}


@pytest.fixture
def response(chain):
    request, _, payloads = chain
    physics = json.loads(next(p.content for p in payloads if p.kind == 'physics_trace'))
    rows = []
    for row in physics['observations']:
        actors = {}
        for role, identifier, state in [('ego',1,row['frame']['ego']), ('lead',2,row['frame']['actors'][0])]:
            yaw = -state['yaw']
            actors[role] = {'actor_id':identifier,'position_m':{'x':state['x'],'y':-state['y'],'z':0.0},
                'rotation_deg':{'pitch':0.0,'yaw':math.degrees(yaw),'roll':0.0},
                'forward_vector':{'x':float32(math.cos(yaw)),'y':float32(math.sin(yaw)),'z':0.0},
                'velocity_mps':{'x':state['speed_mps'],'y':0.0,'z':0.0},
                'acceleration_mps2':{'x':123.0,'y':0.0,'z':0.0},
                'angular_velocity_deg_s':{'x':0.0,'y':0.0,'z':0.0}}
        rows.append({'world_frame':row['world_frame'],'source_time_s':row['source_time_s'],
            'delta_seconds':.05,'actors':actors})
    rest = deepcopy(rows[0]);rest['world_frame']-=1;rest['source_time_s']-=.05
    for actor in rest['actors'].values():
        actor['velocity_mps'] = {'x':0.0,'y':0.0,'z':0.0}
        actor['acceleration_mps2'] = {'x':0.0,'y':0.0,'z':0.0}
    startup = {'status':'passed','trace':[{'world_frame':rest['world_frame'],'elapsed_seconds':rest['source_time_s'],
        'delta_seconds':.05,'actors':deepcopy(rest['actors'])}]}
    config, requests = {}, []
    for role, actor in rest['actors'].items():
        config[role] = {'actor_id':actor['actor_id'],'before_world_frame':rest['world_frame'],
            'after_world_frame':rest['world_frame'],'status':'available','error':None,
            'requested_tire_friction':request.scenario.parameters.friction,
            'requested_tire_friction_float32':float32(request.scenario.parameters.friction),'friction_status':'matched',
            'scalars':{key:unavailable() for key in SCALAR_FIELDS},
            'wheels':[{'index':i,'fields':{key:available(float32(request.scenario.parameters.friction)) if key=='tire_friction'
                else unavailable() for key in WHEEL_FIELDS}} for i in range(4)]}
        speed = request.scenario.parameters.ego_speed_mps if role=='ego' else request.scenario.parameters.target_speed_mps
        requests.append({'role':role,'actor_id':actor['actor_id'],'after_world_frame':rest['world_frame'],
            'velocity_mps':{key:float32(v*speed) for key,v in actor['forward_vector'].items()},'requested_speed_mps':speed})
    data = VehicleResponse(**execution_correlation(request),worker_adapter_version='0.4.1',server_version='authored-fixture',
        status='completed',expected_observations=len(rows),after_rest=rest,physics_configuration=config,
        initial_velocity_requests=requests,observations=rows,limitations=['Authored contract fixture; no actual physics.']).model_dump(mode='json')
    return request, data, physics, startup


def check(response, *, data=None, completed=True, physics=None, startup=None):
    request, original, original_physics, original_startup = response
    payload = ArtifactPayload(name='carla-vehicle-response.json',kind='vehicle_response',media_type='application/json',
        content=canonical_bytes(original if data is None else data))
    return validate_vehicle_response(request,payload,original_physics if physics is None else physics,
        original_startup if startup is None else startup,completed=completed,adapter_version='0.4.1')


def test_complete_norm_and_snapshot_acceleration_are_distinct(response):
    report = check(response)
    assert report.observations[0].actors['ego'].acceleration_mps2.x == 123
    assert response[2]['observations'][0]['frame']['acceleration_mps2'] == 0
    assert report.initial_velocity_requests[0].ack_status == 'unknown'


@pytest.mark.parametrize('mutate',[
    lambda d:d.update(execution_id='foreign'),
    lambda d:d.update(input_hash='0'*64),
    lambda d:d.update(context_hash='0'*64),
    lambda d:d.update(worker_adapter_version='0.4.0'),
    lambda d:d.update(expected_observations=1),
    lambda d:d['observations'].pop(),
    lambda d:d['observations'][0].update(world_frame=999),
    lambda d:d['observations'][0].update(source_time_s=99.),
    lambda d:d['observations'][0].update(delta_seconds=.1),
    lambda d:d['after_rest'].update(world_frame=0),
    lambda d:d['after_rest'].update(delta_seconds=.1),
    lambda d:d['after_rest']['actors']['ego']['velocity_mps'].update(x=10.),
    lambda d:d['observations'][0]['actors']['lead'].update(actor_id=3),
    lambda d:d['observations'][0]['actors']['ego']['velocity_mps'].update(z=.1),
    lambda d:d['observations'][0]['actors']['ego']['position_m'].update(y=100.),
    lambda d:d['observations'][0]['actors']['ego']['rotation_deg'].update(yaw=15.),
    lambda d:d['observations'][0]['actors']['ego']['forward_vector'].update(x=2.),
    lambda d:d['initial_velocity_requests'][0].update(ack_status='acknowledged'),
    lambda d:d['initial_velocity_requests'][0].update(after_world_frame=100),
    lambda d:d['initial_velocity_requests'][0]['velocity_mps'].update(x=0.),
    lambda d:d['initial_velocity_requests'].pop(),
    lambda d:d['physics_configuration']['ego'].update(requested_tire_friction_float32=.8),
    lambda d:d['physics_configuration']['ego']['wheels'][0]['fields']['tire_friction'].update(value=3.5),
    lambda d:d['physics_configuration']['ego'].update(after_world_frame=100),
    lambda d:d['physics_configuration']['ego']['wheels'][0]['fields']['radius'].update(status='available',value=True,reason=None),
    lambda d:d['physics_configuration']['ego']['scalars']['mass'].update(status='available',value='10',reason=None),
])
def test_contradictory_evidence_is_rejected(response,mutate):
    data = deepcopy(response[1]);mutate(data)
    with pytest.raises(ValueError): check(response,data=data)


def test_partial_friction_mismatch_retains_both_readbacks_but_forbids_motion(response):
    data = deepcopy(response[1]);data.update(status='partial',observations=[],initial_velocity_requests=[],error='Friction mismatch')
    config = data['physics_configuration']['ego'];config['friction_status']='mismatch'
    for wheel in config['wheels']: wheel['fields']['tire_friction']['value']=3.5
    assert check(response,data=data,completed=False,physics={'observations':[]}).physics_configuration['ego'].friction_status=='mismatch'
    data['initial_velocity_requests']=response[1]['initial_velocity_requests']
    with pytest.raises(ValueError,match='unverified friction'): check(response,data=data,completed=False,physics={'observations':[]})


def test_startup_failure_can_have_empty_raw_prefix(response):
    data = deepcopy(response[1]);data.update(status='partial',after_rest=None,observations=[],initial_velocity_requests=[],physics_configuration={},error='startup failure')
    assert check(response,data=data,completed=False,physics={'observations':[]}).after_rest is None
    with pytest.raises(ValueError,match='missing rest'): check(response,data=data,completed=False)


def test_raw_capture_failure_can_follow_already_retained_physics(response):
    data=deepcopy(response[1]);data.update(status='partial',error='next snapshot vector invalid',observations=data['observations'][:2])
    assert len(check(response,data=data,completed=False,physics={'observations':response[2]['observations'][:3]}).observations)==2


def test_available_control_requires_same_frame_bracket(response):
    data=deepcopy(response[1]);row=data['observations'][0]
    row['actors']['ego']['reported_control']={'status':'available','error':None,'before_world_frame':row['world_frame'],
        'after_world_frame':row['world_frame']+1,'data':{'throttle':0.,'brake':0.,'steer':0.,'reverse':False,'hand_brake':False,'manual_gear_shift':False,'gear':0}}
    with pytest.raises(ValueError,match='different world frame'):check(response,data=data)


def test_telemetry_classifications_and_strict_fields(response):
    data=deepcopy(response[1]);row=data['observations'][0]
    telemetry={'status':'available','error':None,'before_world_frame':row['world_frame'],'after_world_frame':row['world_frame'],
        'source_api':'vehicle.get_telemetry_data','interpretation':'raw_api_fields_units_unverified',
        'scalars':{k:available(1.) for k in TELEMETRY_SCALARS},
        'wheels':[{'index':i,'fields':{k:available(0.) for k in TELEMETRY_FIELDS}} for i in range(4)]}
    telemetry['wheels'][0]['fields']['omega']={'status':'nonfinite','value':None,'reason':'API returned inf'}
    telemetry['wheels'][1]['fields']['long_slip']={'status':'null','value':None,'reason':'API returned null'}
    row['actors']['ego']['telemetry']=telemetry
    assert check(response,data=data).observations[0].actors['ego'].telemetry.wheels[0].fields['omega'].value is None
    telemetry['wheels'][0]['fields']['omega']['value']=0.
    with pytest.raises(ValueError):check(response,data=data)


def test_unavailable_physics_cannot_smuggle_claims(response):
    data=deepcopy(response[1]);data.update(status='partial',observations=[],initial_velocity_requests=[])
    config=data['physics_configuration']['ego'];config.update(status='unavailable',error='RPC failed',friction_status='unavailable')
    with pytest.raises(ValueError,match='unavailable physics'):check(response,data=data,completed=False,physics={'observations':[]})
