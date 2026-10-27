"""Independent rigid-transform and lifetime contracts, without a CARLA server."""
import copy
import math
import random
import threading
from types import SimpleNamespace as NS

import pytest

from ade2e.sensor_schemas import SensorTransform
from ade2e.simulation_sensor_pose import (declared_matrix, f32, matrix, multiply,
    native_rotation_matrix, reflect, residual, validate_sensor_configuration_evidence)
from axiom_carla.configured_sensor import CallbackGate, SensorConfigurationEvidence, native_mount


def axis_matrix(axis, angle):
    # Rodrigues, independently applied to basis vectors rather than Euler extraction.
    c, s = math.cos(angle), math.sin(angle)
    result = [[0.0]*4 for _ in range(4)]
    result[3][3] = 1.0
    for j in range(3):
        v = [float(i == j) for i in range(3)]
        cross = [axis[1]*v[2]-axis[2]*v[1], axis[2]*v[0]-axis[0]*v[2], axis[0]*v[1]-axis[1]*v[0]]
        dot = sum(axis[i]*v[i] for i in range(3))
        for i in range(3): result[i][j] = v[i]*c+cross[i]*s+axis[i]*dot*(1-c)
    return result


def fake_carla():
    def transform(location, rotation):
        # Independent native convention; each argument crosses float32 storage.
        location = NS(**{k:f32(getattr(location,k)) for k in ('x','y','z')})
        rotation = NS(**{k:f32(getattr(rotation,k)) for k in ('pitch','yaw','roll')})
        z = axis_matrix([0,0,1], math.radians(rotation.yaw))
        y = axis_matrix([0,1,0], -math.radians(rotation.pitch))
        x = axis_matrix([1,0,0], -math.radians(rotation.roll))
        out = multiply(multiply(z,y),x)
        for i,k in enumerate(('x','y','z')): out[i][3] = getattr(location,k)
        return NS(location=location,rotation=rotation,get_matrix=lambda:copy.deepcopy(out))
    return NS(Location=lambda **kw:NS(**kw),Rotation=lambda **kw:NS(**kw),Transform=transform)


@pytest.mark.parametrize('axis,angle,native_angle', [
    ([1,0,0],90,{'pitch':0,'yaw':0,'roll':90}),
    ([0,1,0],90,{'pitch':-90,'yaw':0,'roll':0}),
    ([0,0,1],90,{'pitch':0,'yaw':-90,'roll':0}),
    ([0,1,0],-90,{'pitch':90,'yaw':0,'roll':0}),
])
def test_axes_and_gimbal_lock_have_the_declared_physical_direction(axis,angle,native_angle):
    half = math.radians(angle)/2
    mount = SensorTransform(translation_m=[2.5,.4,1.5],rotation_xyzw=[*(v*math.sin(half) for v in axis),math.cos(half)])
    pose, saved = native_mount(fake_carla(),mount)
    assert residual(saved['declared_rh_matrix'],axis_matrix(axis,math.radians(angle)))[1] < 1e-12
    for key,value in native_angle.items(): assert getattr(pose.rotation,key) == pytest.approx(value,abs=1e-6)
    assert saved['native_location_m'] == [2.5,f32(-.4),1.5]


def test_independent_random_rotations_and_near_gimbal_cases_roundtrip():
    rng = random.Random(56123)
    for index in range(600):
        q = [rng.uniform(-1,1) for _ in range(4)]
        norm = math.sqrt(sum(v*v for v in q)); q = [v/norm for v in q]
        if index < 20:
            a = math.pi/4+(index-10)*1e-10
            q = [0,math.sin(a),0,math.cos(a)]
        mount = SensorTransform(translation_m=[rng.uniform(-5,5),rng.uniform(-5,5),rng.uniform(0,5)],rotation_xyzw=q)
        _, saved = native_mount(fake_carla(),mount)
        # Quaternion vector rotation is independent of Euler decomposition.
        for j in range(3):
            v = [float(i==j) for i in range(3)]; x,y,z,w=q
            cross=[y*v[2]-z*v[1],z*v[0]-x*v[2],x*v[1]-y*v[0]]
            second=[y*cross[2]-z*cross[1],z*cross[0]-x*cross[2],x*cross[1]-y*cross[0]]
            expected=[v[i]+2*w*cross[i]+2*second[i] for i in range(3)]
            actual=reflect(saved['native_spawn_matrix'])
            assert max(abs(actual[i][j]-expected[i]) for i in range(3)) < 2e-6


def example_evidence():
    mount = SensorTransform(translation_m=[2.5,.4,1.5],rotation_xyzw=[0,0,math.sin(.2),math.cos(.2)])
    profile = NS(mount=mount,sensor_frame='lidar_front_oblique',profile_hash='a'*64,
                 attributes={'channels':'16','range':'80.0','points_per_second':'32000','noise_seed':'42'})
    context = NS(schema_version='simulation-control-context/2',sensor_profile=profile,context_hash='b'*64,
                 rig_snapshot=NS(manifest_hash='c'*64),sensor_configuration_snapshot=NS(manifest_hash='d'*64))
    request = NS(execution_id='owned-execution',input_hash='e'*64,context=context)
    evidence = SensorConfigurationEvidence(request)
    _, evidence.spawn = native_mount(fake_carla(),mount)
    from ade2e.simulation_sensor_pose import typed_attributes
    evidence.detached_attributes = typed_attributes(profile.attributes)
    ego_matrix = native_rotation_matrix({'pitch':.01,'yaw':32,'roll':-.02},[120,200,1])
    sensor_matrix = multiply(ego_matrix,evidence.spawn['native_spawn_matrix'])
    ego = NS(id=11,get_transform=lambda:NS(get_matrix=lambda:copy.deepcopy(ego_matrix)))
    sensor = NS(id=12,parent=ego,type_id='sensor.lidar.ray_cast',attributes=profile.attributes,
                get_transform=lambda:NS(get_matrix=lambda:copy.deepcopy(sensor_matrix)))
    evidence.read_actor_attributes(sensor)
    current = NS(frame=40,timestamp=NS(elapsed_seconds=2.),find=lambda identifier:{11:ego,12:sensor}.get(identifier))
    world = NS(get_snapshot=lambda:current)
    evidence.observe_mount(world,current,sensor,ego)
    for frame in [41,42]:
        current.frame=frame;current.timestamp.elapsed_seconds=frame*.05
        cloud=NS(frame=frame,timestamp=frame*.05,transform=sensor.get_transform())
        evidence.gate.wrap(lambda _:None)(cloud)
        evidence.observe_mount(world,current,sensor,ego,cloud)
    evidence.gate.drain()
    payload = evidence.payload(completed=True,phase='cleanup',error=None)
    identity = {'execution_id':request.execution_id,'input_hash':request.input_hash,'context_hash':context.context_hash}
    return evidence,payload,context,identity


def test_original_pose_frames_parent_and_actor_attributes_are_verified():
    _,payload,context,identity=example_evidence()
    assert validate_sensor_configuration_evidence(payload,context,identity,completed=True,source_frames=[41,42]) == payload


@pytest.mark.parametrize('tamper', ['frame','parent','pose','measurement','attributes','angles','source','drain','identity'])
def test_forged_mount_or_incomplete_readback_does_not_validate(tamper):
    _,payload,context,identity=example_evidence()
    row=payload['observations'][0]
    if tamper=='frame': row['world_frame']+=1
    if tamper=='parent': row['parent_actor_id']=13
    if tamper=='pose': row['sensor_world_matrix'][0][3]+=.1
    if tamper=='measurement': row['measurement_world_matrix']=None
    if tamper=='attributes': payload['actor_attributes']['raw']['range']='81.0'
    if tamper=='angles': payload['spawn']['native_rotation_deg']['roll']=30
    if tamper=='source': payload['sensor_frame']='lidar_roof'
    if tamper=='drain': payload['callback_drain']['active']=1
    if tamper=='identity': payload['context_hash']='f'*64
    with pytest.raises(ValueError):
        validate_sensor_configuration_evidence(payload,context,identity,completed=True,source_frames=[41,42])


def test_reflection_or_nonfinite_matrix_is_rejected():
    with pytest.raises(ValueError): matrix([[-1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]])
    with pytest.raises(ValueError): matrix([[1,0,0,float('nan')],[0,1,0,0],[0,0,1,0],[0,0,0,1]])


def test_gate_timeout_preserves_uncertainty_and_late_callbacks_cannot_mutate():
    gate=CallbackGate();entered=threading.Event();release=threading.Event();writes=[]
    def work(value): entered.set();release.wait(2);writes.append(value)
    thread=threading.Thread(target=gate.wrap(work),args=(1,));thread.start()
    assert entered.wait(1)
    try:
        with pytest.raises(ValueError,match='drain bound'): gate.drain(timeout=.01)
        assert gate.snapshot()['active']==1 and not gate.snapshot()['drained']
        gate.wrap(work)(2)
        assert gate.snapshot()['rejected_after_stop']==1
    finally:
        release.set();thread.join(1)
    gate.drain()
    assert writes==[1] and gate.snapshot()['finished']==1
