"""Mocked native driving contracts, never CARLA or physical validation evidence."""
import copy
import hashlib
import json
import struct
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from axiom_carla import driving_multisensor_native as native
from test_feedback_sensor import Blueprint as CameraBlueprint, World as FeedbackWorld


def specification(prepared=True, *, cameras=True):
    result={'schema_version':native.SPEC,'duration_seconds':20,'sample_hz':10,'spawn_index':0,
        'vehicle_blueprint':'vehicle.tesla.model3','expected_map':'Town10HD_Opt','expected_server_version':'1ae5356',
        'rig_revision':1,'coordinate_convention':'right_handed_x_forward_y_left_z_up',
        'controller':{'id':'native-map-lane-follow/1','target_speed_mps':3.,'lookahead_m':6.,'route_length_m':150.},'sensors':[]}
    for key,kind,xyz,rotation in [
        ('front','rgb',[1.5,0.,1.7],[0.,0.,0.]),('rear','rgb',[-1.5,0.,1.7],[0.,180.,0.]),
        ('lidar','lidar',[0.,0.,2.],[0.,0.,0.]),('radar','radar',[1.8,0.,1.],[0.,0.,0.])]:
        if not cameras and kind=='rgb':continue
        sensor={'key':key,'kind':kind,'translation_m':xyz,'rotation_deg':rotation,
            'rotation_order':'pitch_yaw_roll_right_hand_rule','parameters':{}}
        if prepared:sensor['native_parameters']=dict(native.SENSOR_DEFAULTS[kind])
        result['sensors'].append(sensor)
    if prepared:result['seed']=42
    return result


class Blueprint(CameraBlueprint):
    def __init__(self,identity):
        super().__init__(identity)
        self.values=next((dict(native.SENSOR_DEFAULTS[k]) for k,v in native.support.BLUEPRINTS.items() if v==identity),{})


class World(FeedbackWorld):
    """Straight authored lane and deterministic toy physics for contract checking."""
    def get_blueprint_library(self):return NS(find=Blueprint)

    def tick(self,timeout):
        self.frame+=1
        if self.foreign_during_fence and self.destroyed:self.actors.append(NS(id=999,type_id='sensor.foreign'))
        for actor in list(self.actors):
            if actor.type_id.startswith('vehicle.'):
                actor.speed=max(0.,actor.speed+.3*actor.control.throttle-.5*actor.control.brake)
                actor.pose=self.carla.Transform(self.carla.Location(x=actor.pose.location.x+actor.speed*.1,y=0,z=0),actor.pose.rotation)
            if getattr(actor,'listener',None):
                kind=next(k for k,v in native.support.BLUEPRINTS.items() if v==actor.type_id)
                raw=(bytes([self.frame%255,20,40,255])*(640*360) if kind=='rgb' else
                    struct.pack('<ffff',5.,.2,.1,.7) if kind=='lidar' else struct.pack('<ffff',-1.,.2,.1,12.))
                actor.listener(NS(frame=self.frame-1 if self.mismatch and self.frame>111 else self.frame,
                    timestamp=self.frame*.1,transform=actor.get_transform(),raw_data=raw,width=640,height=360))
        if self.foreign and self.frame==113:self.actors.append(NS(id=999,type_id='vehicle.foreign'))
        if self.foreign_settings and self.frame==113:self.settings.max_substeps=9
        self.actor_snapshot_cache=list(self.actors)
        return self.frame


def capture_fixture(tmp_path,world=None,spec=None):
    world=world or World();spec=spec or specification();folder=tmp_path/'driving_unit';spool=tmp_path/'spool';spool.mkdir()
    probe=native.probe(world.client(),spec)
    with patch.object(native.support,'connect',return_value=(world.carla,world.client())):
        with native.engine_lease(spool,folder.name,hashlib.sha256(native.canonical(spec)).hexdigest()) as lease:
            manifest=native.capture({'spec':spec,'pinned_probe':probe},folder,lease=lease)
        assert lease['quarantine_released']
    manifest['engine_interlock']['reservation_cleared_after_confirmed_cleanup']=True
    native.write_capture_manifest(folder/'capture-manifest.json',manifest)
    return spec,probe,manifest,folder,world


def test_all_four_mocked_streams_share_each_driving_physics_response_and_cleanup(tmp_path):
    spec,probe,manifest,folder,world=capture_fixture(tmp_path)
    assert manifest['schema_version']=='carla-driving-multisensor-result/1'
    assert manifest['frame_count']==200 and len(manifest['records'])==800
    assert manifest['ego_physics_enabled'] is True and manifest['distance_travelled_m']>20
    assert len(manifest['warmup'])==10 and manifest['warmup'][-1]['frame']==manifest['ego_initial_world_frame']
    assert manifest['ego_requested_spawn_transform']==manifest['warmup'][-1]['ego']['world_transform']
    ticks=manifest['motion']['ticks']
    assert ticks[0]['input_frame']==manifest['warmup'][-1]['frame']
    assert ticks[0]['ego']['world_transform']!=manifest['ego_requested_spawn_transform']
    assert manifest['motion']['route']==probe['route'] and probe['route']['configuration_revision']==spec['rig_revision']
    assert manifest['motion']['sensors_are_policy_input'] is False
    for index,tick in enumerate(ticks):
        assert tick['index']==index and tick['frame']==tick['input_frame']+1
        assert tick['actuator_acknowledged'] is True
        records=manifest['records'][index*4:index*4+4]
        assert [r['key'] for r in records]==['front','rear','lidar','radar']
        assert all(r['frame']==tick['frame'] and r['timestamp_seconds']==tick['timestamp_seconds'] for r in records)
        assert all(r['ego_world_transform']==tick['ego']['world_transform'] and r['mount_qualification']['qualified'] is True for r in records)
    assert not world.actors and world.destroyed==[5,4,3,2,1] and not world.settings.synchronous_mode
    assert manifest['cleanup_observation']['status']=='confirmed'
    assert manifest['callback_drain']['drained'] is True
    assert not (tmp_path/'spool/quarantine.json').exists()
    assert (folder/'capture-manifest.json').stat().st_size<native.MAX_MANIFEST_BYTES
    assert manifest['native_bytes']==sum(a['size_bytes'] for a in manifest['artifacts'])


@pytest.mark.parametrize('mutation',[
    lambda s:s.update(duration_seconds=10),lambda s:s.update(duration_seconds=31),lambda s:s.update(sample_hz=20),
    lambda s:s.update(engine_spool='/unauthorized'),lambda s:s['controller'].update(target_speed_mps=5),
    lambda s:s['controller'].update(route_length_m=60),lambda s:s['controller'].update(target_speed_mps=float('nan')),
    lambda s:s['sensors'][0]['native_parameters'].update(sensor_tick='.1'),
    lambda s:s['sensors'][0]['native_parameters'].update(fov='70'),lambda s:s.update(rig_revision=0)])
def test_native_rejects_unsupported_driving_contract_before_connection(tmp_path,mutation):
    spec=specification();mutation(spec)
    with patch.object(native.support,'connect') as connect,pytest.raises(ValueError):
        native.capture({'spec':spec,'pinned_probe':{}},tmp_path/'must_not_exist')
    connect.assert_not_called();assert not (tmp_path/'must_not_exist').exists()


def test_input_profiles_validate_without_premature_native_fields():
    native.validate_spec(specification(False),prepared=False)
    spec=specification(False);spec['sensors'][0]['native_parameters']={}
    with pytest.raises(ValueError):native.validate_spec(spec,prepared=False)


def test_route_horizon_checks_available_native_lane_not_only_requested_length():
    world=World();original_waypoint=world.waypoint
    def short_waypoint(x):
        result=original_waypoint(x);result.next=lambda distance:[short_waypoint(x+distance)] if x<40 else []
        return result
    world.waypoint=short_waypoint
    with pytest.raises(ValueError,match='too short'):native.probe(world.client(),specification())


@pytest.mark.parametrize('flag',['mismatch','command_error','foreign','foreign_settings'])
def test_failed_capture_keeps_partial_motion_and_never_cleans_foreign_ownership(tmp_path,flag):
    world=World(**{flag:True});spec=specification(cameras=False);probe=native.probe(world.client(),spec)
    spool=tmp_path/'spool';spool.mkdir();folder=tmp_path/'driving_failure'
    with patch.object(native.support,'connect',return_value=(world.carla,world.client())),pytest.raises(RuntimeError):
        with native.engine_lease(spool,folder.name,hashlib.sha256(native.canonical(spec)).hexdigest()) as lease:
            native.capture({'spec':spec,'pinned_probe':probe},folder,lease=lease)
    manifest=json.loads((folder/'capture-manifest.json').read_bytes())
    assert manifest['status']=='failed' and set(world.destroyed)=={1,2,3}
    if flag!='command_error':assert manifest['motion']['ticks']
    if flag=='foreign':assert [a.id for a in world.actors]==[999] and (spool/'quarantine.json').exists()
    if flag=='foreign_settings':
        assert (spool/'quarantine.json').exists() and world.settings.max_substeps==9
        assert manifest['cleanup_observation']['controlled_cleanup_tick_count']==0


def test_capture_requires_shared_engine_lease_before_connecting(tmp_path):
    with patch.object(native.support,'connect') as connect,pytest.raises(ValueError,match='lease'):
        native.capture({'spec':specification(),'pinned_probe':{}},tmp_path/'absent')
    connect.assert_not_called()


def test_changed_probe_preserves_diagnostic_and_releases_untouched_engine(tmp_path):
    world=World();spec=specification(cameras=False);spool=tmp_path/'spool';spool.mkdir();out=tmp_path/'changed'
    with patch.object(native.support,'connect',return_value=(world.carla,world.client())),pytest.raises(ValueError,match='changed after preview'):
        with native.engine_lease(spool,out.name,hashlib.sha256(native.canonical(spec)).hexdigest()) as lease:
            native.capture({'spec':spec,'pinned_probe':{}},out,lease=lease)
    result=json.loads((out/'capture-manifest.json').read_bytes())
    assert result['status']=='failed' and result['cleanup_scope']=='no_world_mutation_started'
    assert not world.actors and not world.destroyed and not (spool/'quarantine.json').exists()
