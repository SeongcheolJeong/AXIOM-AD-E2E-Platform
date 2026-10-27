"""Full optional-adapter/control/MCAP contracts over an authored fake world.

This checks wiring, physical-pose readback gates and failure preservation. It is
not a simulator run, a sampling test or a vehicle-performance acceptance result.
"""
import copy
import hashlib
import io
import json
import math
from types import SimpleNamespace as NS

import pytest
from mcap.reader import make_reader

from ade2e.adapters import AdapterExecutionError
from ade2e.sensor_schemas import RigCreate, RigSnapshot, RigDetail
from ade2e.simulation_context import (SimulationControlSelectionV2, build_simulation_context,
    make_simulation_execution_request, simulation_input_hash)
from ade2e.simulation_evidence import validate_simulation_artifacts
from ade2e.simulation_sensor_capabilities import canonical, content_hash, simulation_sensor_capabilities
from ade2e.simulation_sensor_schemas import SensorConfigurationCreate, SensorConfigurationSnapshot, SensorConfigurationDetail
from ade2e.simulation_sensor_pose import multiply
from axiom_carla.runner import CarlaRunner
from axiom_carla.version import ADAPTER_VERSION
from test_live_stack import install_live_world, live_request


def configured_request(scenario):
    old = live_request(scenario)
    rig_create = RigCreate(name='Oblique front mount',note='Authored test geometry, not calibration',
        reference_frame='ego',coordinate_convention='right_handed_z_up',sensors=[{
            'key':'front','name':'Front LiDAR','kind':'lidar','frame_id':'lidar_front_oblique',
            'transform':{'translation_m':[2.5,.4,1.5], 'rotation_xyzw':[0,0,math.sin(.2),math.cos(.2)]}}])
    rig_snapshot = RigSnapshot(**rig_create.model_dump(mode='json'),id='test-front-rig',revision=1,
        created_at='2026-09-14T00:00:00Z',definition_hash=content_hash(rig_create.model_dump(mode='json')))
    raw = canonical(rig_snapshot.model_dump(mode='json'))
    rig = RigDetail(**rig_snapshot.model_dump(mode='json'),manifest_hash=hashlib.sha256(raw).hexdigest(),
        manifest_size_bytes=len(raw),download_url='/api/v1/sensor-rigs/test-front-rig/export?revision=1')
    definition=SensorConfigurationCreate(name='Full front ray recipe',note='Authored test only',parameters={'horizontal_fov':180.0})
    snapshot=SensorConfigurationSnapshot(**definition.model_dump(mode='json'),id='test-config',revision=1,
        created_at='2026-09-14T00:00:00Z',definition_hash=content_hash(definition.model_dump(mode='json')),
        capability_schema_hash=simulation_sensor_capabilities()[0].schema_hash)
    raw=canonical(snapshot.model_dump(mode='json'))
    recipe=SensorConfigurationDetail(**snapshot.model_dump(mode='json'),manifest_hash=hashlib.sha256(raw).hexdigest(),
        manifest_size_bytes=len(raw),download_url='/api/v1/simulation-sensor-configurations/test-config/export?revision=1')
    selection=SimulationControlSelectionV2(schema_version='simulation-control-selection/2',stack_id=old.context.stack_snapshot.id,stack_revision=1,
        rig_id=rig.id,rig_revision=1,lidar_sensor_key='front',sensor_configuration_id=recipe.id,sensor_configuration_revision=1)
    context=build_simulation_context(old.context.stack_snapshot,rig,selection,old.scenario.seed,sensor_configuration=recipe)
    return make_simulation_execution_request(execution_id=old.execution_id,scenario=old.scenario,context=context,
        input_hash=simulation_input_hash(old.scenario,'carla',ADAPTER_VERSION,context))


def install_configured_world(monkeypatch, *, fault=None):
    world=install_live_world(monkeypatch)
    original_spawn,original_snapshot=world.spawn_actor,world.get_snapshot
    world.spawn_order=[]
    def spawn(blueprint,relative_pose,**kwargs):
        sensor=original_spawn(blueprint,relative_pose,**kwargs)
        world.spawn_order.append((blueprint.name,world.frame,len(world.vehicles)))
        if blueprint.name=='sensor.lidar.ray_cast':
            sensor.id=900; sensor.type_id=blueprint.name; sensor.parent=kwargs['attach_to']
            sensor.attributes=dict(blueprint.values)
            if fault=='attributes': sensor.attributes['range']='81.0'
            sensor.relative_pose=copy.deepcopy(relative_pose)
            sensor.get_transform=lambda:NS(get_matrix=lambda:multiply(sensor.parent.pose.get_matrix(),sensor.relative_pose.get_matrix()))
            original_listen=sensor.listen
            def listen(callback):
                def receive(cloud):
                    value=copy.copy(cloud)
                    pose_matrix=copy.deepcopy(sensor.get_transform().get_matrix())
                    if fault=='measurement_pose' and world.sensor_ticks==3: pose_matrix[0][3]+=.1
                    value.transform=NS(get_matrix=lambda:pose_matrix)
                    return callback(value)
                original_listen(receive)
            sensor.listen=listen
        return sensor
    world.spawn_actor=spawn
    def snapshot():
        value=original_snapshot(); original_find=value.find
        lidar=world.lidar
        if hasattr(lidar,'id'):
            observed=copy.deepcopy(lidar.get_transform().get_matrix())
            # Actor inventory can lag the returned spawn until the next frame.
            value.find=lambda identifier: (NS(get_transform=lambda:NS(get_matrix=lambda:copy.deepcopy(observed)))
                if identifier==900 and world.frame>0 and fault!='missing_snapshot' else original_find(identifier))
        return value
    world.get_snapshot=snapshot
    return world


def test_configured_sensor_spawns_before_rest_and_full_source_frame_is_preserved(monkeypatch,carla_scenario):
    world=install_configured_world(monkeypatch)
    request=configured_request(carla_scenario)
    result=CarlaRunner().execute_with_context(request)
    validate_simulation_artifacts(request,result.artifact_payloads,completed=True,
        trajectory=result.trajectory,adapter_version=ADAPTER_VERSION)
    blobs={p.name:p.content for p in result.artifact_payloads}
    config=json.loads(blobs['carla-sensor-configuration.json'])
    assert world.spawn_order[0]==('sensor.lidar.ray_cast',0,2)
    assert config['startup_mount']['analysis']['qualified']
    assert config['spawn']['native_location_m']==pytest.approx([2.5,-.4,1.5])
    assert config['spawn']['native_rotation_deg']['yaw']==pytest.approx(-math.degrees(.4))
    assert config['actor_attributes']['qualified'] and config['callback_drain']['drained']
    assert len(config['observations'])==81
    assert all(row['analysis']['qualified'] for row in config['observations'])
    manifest=json.loads(blobs['carla-stack-manifest.json'])
    mcap=list(make_reader(io.BytesIO(blobs['carla-stack-sources.mcap'])).iter_messages(log_time_order=False))
    lidar_messages=[(channel,message) for schema,channel,message in mcap if channel.id==1]
    assert len(lidar_messages)==81
    assert all(channel.metadata['frame_id']=='lidar_front_oblique' and json.loads(message.data)['frame_id']=='lidar_front_oblique'
               for channel,message in lidar_messages)
    assert manifest['execution_context']==request.context.model_dump(mode='json')
    assert len(blobs['carla-stack-raw-xyzi.bin'])==81*len(world.raw)
    assert world.lidar.destroyed and all(vehicle.destroy_called for vehicle in world.vehicles)


@pytest.mark.parametrize('fault',['attributes','missing_snapshot','measurement_pose'])
def test_actual_readback_failure_preserves_partial_chain_and_owns_cleanup(monkeypatch,carla_scenario,fault):
    world=install_configured_world(monkeypatch,fault=fault)
    request=configured_request(carla_scenario)
    with pytest.raises(AdapterExecutionError) as failure:
        CarlaRunner().execute_with_context(request)
    payloads=failure.value.artifact_payloads
    validate_simulation_artifacts(request,payloads,completed=False,adapter_version=ADAPTER_VERSION)
    config=json.loads(next(p.content for p in payloads if p.kind=='sensor_configuration'))
    assert config['status']=='partial' and config['error']
    assert config['callback_drain']['drained']
    assert world.lidar.destroyed and all(vehicle.destroy_called for vehicle in world.vehicles)
    if fault in {'attributes','missing_snapshot'}:
        assert all(not vehicle.commands for vehicle in world.vehicles)
    else:
        assert len(world.vehicles[0].commands)==2
        assert not config['observations'][-1]['analysis']['qualified']


def test_native_preflight_failure_has_explicit_not_observed_evidence(monkeypatch,carla_scenario):
    request=configured_request(carla_scenario)
    monkeypatch.setattr(CarlaRunner,'_connect',lambda _:(_ for _ in ()).throw(RuntimeError('offline test')))
    with pytest.raises(AdapterExecutionError) as failure:
        CarlaRunner().execute_with_context(request)
    validate_simulation_artifacts(request,failure.value.artifact_payloads,completed=False,adapter_version=ADAPTER_VERSION)
    value=json.loads(failure.value.artifact_payloads[0].content)
    assert value['spawn'] is None and value['startup_mount'] is None and value['observations']==[]
    assert not value['callback_drain']['drained']
