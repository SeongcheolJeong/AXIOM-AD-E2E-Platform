"""Mocked native contracts only; these fixtures are not CARLA/sensor validation."""
import copy
import hashlib
import json
import struct
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
import zipfile
import fcntl

import pytest
from ade2e.experiment_schemas import ExperimentCreate
from axiom_carla import feedback_native as native
from axiom_carla.feedback import FeedbackSensorHandler,validate_feedback_bundle
from test_configured_sensor_pose import fake_carla

def specification(prepared=True):
    value={'schema_version':'carla-feedback-sensor-spec/1','duration_seconds':2,'sample_hz':10,'spawn_index':0,
        'vehicle_blueprint':'vehicle.tesla.model3','expected_map':'Town10HD_Opt','expected_server_version':'1ae5356',
        'configuration_revision':1,'coordinate_convention':'right_handed_x_forward_y_left_z_up',
        'controller':{'id':'native-map-lane-follow/1','target_speed_mps':3.,'lookahead_m':6.,'route_length_m':40.},
        'sensors':[{'key':'front','kind':'rgb','translation_m':[1.5,0.,1.7],'rotation_deg':[0.,0.,0.],
            'rotation_order':'pitch_yaw_roll_right_hand_rule','parameters':{'fov':'90'}}]}
    if prepared:
        value['seed']=42
        for sensor in value['sensors']:sensor['native_parameters']={**native.CAMERA_DEFAULTS,**sensor['parameters']}
    return value

class Attribute:
    def __init__(self,key,value):self.id=key;self.value=value;self.is_modifiable=True;self.recommended_values=[value]
    def __str__(self):return self.value

class Blueprint:
    def __init__(self,identity):self.id=identity;self.values=dict(native.CAMERA_DEFAULTS) if identity=='sensor.camera.rgb' else {}
    def __iter__(self):return iter(Attribute(k,v) for k,v in self.values.items())
    def has_attribute(self,key):return key in self.values
    def get_attribute(self,key):return Attribute(key,self.values[key])
    def set_attribute(self,key,value):self.values[key]=value

class World:
    def __init__(self,*,mismatch=False,command_error=False,foreign=False,restore_failure=False,foreign_settings=False,
        cached_destroy_snapshot=False,foreign_during_fence=False):
        self.carla=fake_carla();self.carla.VehicleControl=lambda **kw:NS(throttle=kw.get('throttle',0.),brake=kw.get('brake',0.),steer=kw.get('steer',0.))
        self.carla.command=NS(ApplyVehicleControl=lambda actor_id,control:NS(actor_id=actor_id,control=control))
        self.settings=NS(synchronous_mode=False,fixed_delta_seconds=None,no_rendering_mode=False,substepping=True,max_substep_delta_time=.01,max_substeps=10)
        self.actors=[];self.destroyed=[];self.frame=100;self.mismatch=mismatch;self.command_error=command_error;self.foreign=foreign;self.restore_failure=restore_failure;self.foreign_settings=foreign_settings
        self.cached_destroy_snapshot=cached_destroy_snapshot;self.actor_snapshot_cache=[];self.foreign_during_fence=foreign_during_fence
        self.spawn=self.carla.Transform(self.carla.Location(x=0,y=0,z=0),self.carla.Rotation(pitch=0,yaw=0,roll=0))
    def get_settings(self):return copy.copy(self.settings)
    def apply_settings(self,settings):
        self.settings=copy.copy(settings)
        if self.restore_failure and not settings.synchronous_mode:self.settings.no_rendering_mode=True
    def get_blueprint_library(self):return NS(find=Blueprint)
    def get_weather(self):return NS(**{k:0. for k in ('cloudiness','precipitation','precipitation_deposits','wind_intensity','sun_azimuth_angle','sun_altitude_angle','fog_density','fog_distance','wetness')})
    def waypoint(self,x):
        return NS(transform=self.carla.Transform(self.carla.Location(x=x,y=0,z=0),self.carla.Rotation(pitch=0,yaw=0,roll=0)),
            road_id=1,section_id=0,lane_id=-1,lane_type='Driving',lane_width=3.4,is_junction=False,s=x,next=lambda distance:[self.waypoint(x+distance)])
    def get_map(self):
        # Authored contract fixture only; this is not an actual CARLA road map.
        xml='<OpenDRIVE><road id="1" length="1000"><lanes><laneSection s="0"><right><lane id="-1" type="driving"><width sOffset="0" a="3.4" b="0" c="0" d="0"/></lane></right></laneSection></lanes></road></OpenDRIVE>'
        return NS(name='/Game/Carla/Maps/Town10HD_Opt',to_opendrive=lambda:xml,get_spawn_points=lambda:[self.spawn],
            get_waypoint=lambda location:self.waypoint(location.x),generate_waypoints=lambda spacing:[self.waypoint(x) for x in range(0,102,2)])
    def get_actors(self):return list(self.actor_snapshot_cache if self.cached_destroy_snapshot else self.actors)
    def actor(self,bp,pose,parent=None):
        world=self;mount=pose
        class Actor:
            def __init__(self):
                self.id=len(world.actors)+1;self.type_id=bp.id;self.attributes=dict(bp.values);self.parent=parent;self.listener=None
                self.pose=copy.deepcopy(pose);self.speed=0.;self.control=world.carla.VehicleControl()
                self.bounding_box=NS(location=NS(x=.35,y=.02,z=.75),rotation=NS(pitch=0,yaw=0,roll=0),extent=NS(x=2.1,y=.95,z=.8))
            def get_transform(self):
                if self.parent:
                    return world.carla.Transform(world.carla.Location(x=self.parent.pose.location.x+mount.location.x,y=mount.location.y,z=mount.location.z),mount.rotation)
                return self.pose
            def get_velocity(self):return NS(x=self.speed,y=0.,z=0.)
            def get_control(self):return self.control
            def apply_control(self,control):self.control=control
            def get_physics_control(self):return NS(mass=1500.,drag_coefficient=.3,center_of_mass=NS(x=0,y=0,z=0),wheels=[NS(tire_friction=3.5,damping_rate=.25,radius=30,max_steer_angle=35) for _ in range(4)])
            def set_simulate_physics(self,value):self.physics=value
            def listen(self,callback):self.listener=callback
            def stop(self):self.listener=None
            def destroy(self):world.destroyed.append(self.id);world.actors.remove(self);return True
        actor=Actor();self.actors.append(actor);return actor
    def try_spawn_actor(self,bp,pose):return self.actor(bp,pose)
    def spawn_actor(self,bp,pose,attach_to):return self.actor(bp,pose,attach_to)
    def tick(self,timeout):
        self.frame+=1
        if self.foreign_during_fence and self.destroyed:self.actors.append(NS(id=999,type_id='sensor.foreign'))
        for actor in list(self.actors):
            if actor.type_id.startswith('vehicle.'):
                actor.speed=max(0.,actor.speed+.3*actor.control.throttle-.5*actor.control.brake)
                actor.pose=self.carla.Transform(self.carla.Location(x=actor.pose.location.x+actor.speed*.1,y=0,z=0),actor.pose.rotation)
            if getattr(actor,'listener',None):
                actor.listener(NS(frame=self.frame-1 if self.mismatch else self.frame,timestamp=self.frame*.1,
                    transform=actor.get_transform(),raw_data=bytes([self.frame%255,20,40,255])*(640*360),width=640,height=360))
        if self.foreign and self.frame==113:self.actors.append(NS(id=999,type_id='vehicle.foreign'))
        if self.foreign_settings and self.frame==113:self.settings.max_substeps=9
        self.actor_snapshot_cache=list(self.actors)
        return self.frame
    def get_snapshot(self):
        actors=list(self.actor_snapshot_cache if self.cached_destroy_snapshot else self.actors)
        return NS(frame=self.frame,timestamp=NS(elapsed_seconds=self.frame*.1),find=lambda identity:next((a for a in actors if a.id==identity),None))
    def client(self):
        def apply(commands,tick):
            assert tick is False
            for command in commands:
                next(a for a in self.actors if a.id==command.actor_id).apply_control(command.control)
            return [NS(error='fixture command failure' if self.command_error else '')]
        return NS(get_world=lambda:self,get_server_version=lambda:'1ae5356',get_client_version=lambda:'1ae5356',apply_batch_sync=apply)

def captured(tmp_path,world=None,spec=None):
    world=world or World();spec=spec or specification();folder=tmp_path/'feedback_unit';spool=tmp_path/'spool';spool.mkdir()
    probe=native.probe(world.client(),spec)
    with patch.object(native.support,'connect',return_value=(world.carla,world.client())):
        with native.engine_lease(spool,folder.name,hashlib.sha256(native.canonical(spec)).hexdigest()) as lease:
            manifest=native.capture({'spec':spec,'pinned_probe':probe},folder,lease=lease)
        assert lease['quarantine_released']
    manifest['engine_interlock']['reservation_cleared_after_confirmed_cleanup']=True
    (folder/'feedback-capture-manifest.json').write_bytes(native.canonical(manifest))
    return spec,probe,manifest,folder,world

def bundle(folder,manifest=None,mutate_trace=None):
    if mutate_trace is not None:
        trace=json.loads((folder/'feedback-trace.json').read_bytes());mutate_trace(trace)
        data=native.canonical(trace);(folder/'feedback-trace.json').write_bytes(data)
        descriptor=next(e for e in manifest['artifacts'] if e['name']=='feedback-trace.json')
        old=descriptor['size_bytes'];descriptor.update(size_bytes=len(data),sha256=hashlib.sha256(data).hexdigest());manifest['native_bytes']+=len(data)-old
        # Keep the new artifact's source joins valid while testing independent
        # trace causality validation with deliberately re-hashed originals.
        from axiom_carla import sensor_scene
        scene=json.loads((folder/'sensor-scene.json').read_bytes())
        data=native.canonical(sensor_scene.build_scene(manifest,trace,data,scene['map'],manifest['ego_bounding_box']))
        (folder/'sensor-scene.json').write_bytes(data)
        descriptor=next(e for e in manifest['artifacts'] if e['name']=='sensor-scene.json')
        old=descriptor['size_bytes'];descriptor.update(size_bytes=len(data),sha256=hashlib.sha256(data).hexdigest());manifest['native_bytes']+=len(data)-old
    if manifest is not None:(folder/'feedback-capture-manifest.json').write_bytes(native.canonical(manifest))
    target=folder/'unit.zip'
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_STORED) as archive:
        for path in sorted(folder.rglob('*')):
            if path.is_file() and path.name!='unit.zip':archive.write(path,str(path.relative_to(folder)))
    raw=(folder/'feedback-capture-manifest.json').read_bytes();source=json.loads(raw)
    return target,{'status':'completed','manifest':'feedback-capture-manifest.json','external_execution_id':folder.name,
        'frame_count':source['frame_count'],'manifest_sha256':hashlib.sha256(raw).hexdigest(),
        'archive_sha256':native.sha_file(target),'archive_size_bytes':target.stat().st_size}

def test_complete_mocked_feedback_capture_aligned_response_original_bytes_and_cleanup(tmp_path):
    spec,probe,manifest,folder,world=captured(tmp_path);archive,receipt=bundle(folder)
    parsed,raw,trace,trace_raw=validate_feedback_bundle(archive,receipt,spec,folder.name,probe)
    assert parsed==manifest and raw==(folder/'feedback-capture-manifest.json').read_bytes() and trace_raw==(folder/'feedback-trace.json').read_bytes()
    assert len(trace['ticks'])==20 and len(manifest['records'])==20 and manifest['distance_travelled_m']>0
    assert all(t['frame']==t['input_frame']+1 for t in trace['ticks']) and trace['camera_is_policy_input'] is False
    assert not world.actors and world.destroyed==[2,1] and not world.settings.synchronous_mode
    assert not (tmp_path/'spool/quarantine.json').exists()

@pytest.mark.parametrize('mutation',[
    lambda s:s.update(duration_seconds=30),lambda s:s.update(sample_hz=20),lambda s:s.update(host='untrusted'),
    lambda s:s['controller'].update(id='unconnected-camera-e2e'),lambda s:s['controller'].update(target_speed_mps=float('nan')),
    lambda s:s['sensors'][0]['native_parameters'].update(sensor_tick='.1'),lambda s:s['sensors'][0].update(key='other')])
def test_native_rejects_unsupported_contract_before_connection(tmp_path,mutation):
    spec=specification();mutation(spec)
    with patch.object(native.support,'connect') as connect,pytest.raises(ValueError):native.capture({'spec':spec,'pinned_probe':{}},tmp_path/'must_not_exist')
    connect.assert_not_called();assert not (tmp_path/'must_not_exist').exists()

@pytest.mark.parametrize('mutation',[
    lambda m:m.update(cleanup_status='unknown'),lambda m:m['actor_destroy_acknowledgements'][0].update(acknowledged=False),
    lambda m:m['world_settings_after_cleanup'].update(no_rendering_mode=True),lambda m:m['records'][0].update(frame=999),
    lambda m:m['engine_interlock'].update(reservation_cleared_after_confirmed_cleanup=False),
    lambda m:m['sensors'][0]['native_readback'].update(fov='70'),lambda m:m.pop('vehicle_physics_readback'),
    lambda m:m['vehicle_physics_readback']['wheels'][0].update(radius_cm=-1),
    lambda m:m['vehicle_physics_readback'].update(mass_kg=float('nan'))])
def test_bundle_refuses_uncorrelated_frame_configuration_ownership_or_cleanup(tmp_path,mutation):
    spec,probe,manifest,folder,_=captured(tmp_path);mutation(manifest)
    try:archive,receipt=bundle(folder,manifest)
    except ValueError:
        # Canonical JSON forbids nonfinite metadata before a source manifest can
        # be formed, so this mutation must fail at that earlier boundary.
        assert not (folder/'unit.zip').exists();return
    with pytest.raises(ValueError):validate_feedback_bundle(archive,receipt,spec,folder.name,probe)

@pytest.mark.parametrize('mutation',[
    lambda t:t['ticks'][0].update(input_frame=999),lambda t:t['ticks'][0]['planned_path'][0].update(x_m=777.),
    lambda t:t['ticks'][1].update(input_ego=t['ticks'][0]['input_ego']),lambda t:t['ticks'][0]['observed_control'].update(brake=.9),
    lambda t:t.update(camera_is_policy_input=True),lambda t:t['ticks'][0]['ego'].update(speed_mps=90.)])
def test_bundle_recomputes_causal_plan_and_response_even_with_rehashed_trace(tmp_path,mutation):
    spec,probe,manifest,folder,_=captured(tmp_path);archive,receipt=bundle(folder,manifest,mutation)
    with pytest.raises(ValueError):validate_feedback_bundle(archive,receipt,spec,folder.name,probe)

def test_shared_lock_and_foreign_quarantine_are_never_taken_over(tmp_path):
    spool=tmp_path/'spool';spool.mkdir();lock=(spool/'engine.lock').open('a+b');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    with pytest.raises(RuntimeError),native.engine_lease(spool,'unit','a'*64):pass
    lock.close();foreign=b'{"execution_id":"foreign legacy owner"}';(spool/'quarantine.json').write_bytes(foreign)
    with pytest.raises(RuntimeError),native.engine_lease(spool,'unit','a'*64):pass
    assert (spool/'quarantine.json').read_bytes()==foreign

def test_uncertain_native_outcome_leaves_durable_owned_quarantine(tmp_path):
    spool=tmp_path/'spool';spool.mkdir()
    with pytest.raises(RuntimeError):
        with native.engine_lease(spool,'unit','a'*64):raise RuntimeError('Fixture process outcome uncertain')
    assert json.loads((spool/'quarantine.json').read_bytes())['external_execution_id']=='unit'

@pytest.mark.parametrize('flag',['mismatch','command_error','foreign','restore_failure','foreign_settings'])
def test_native_failures_cleanup_only_owned_actors_and_preserve_failed_manifest(tmp_path,flag):
    world=World(**{flag:True});spec=specification();probe=native.probe(world.client(),spec);spool=tmp_path/'spool';spool.mkdir();folder=tmp_path/'feedback_failure'
    with patch.object(native.support,'connect',return_value=(world.carla,world.client())),pytest.raises(RuntimeError):
        with native.engine_lease(spool,folder.name,hashlib.sha256(native.canonical(spec)).hexdigest()) as lease:native.capture({'spec':spec,'pinned_probe':probe},folder,lease=lease)
    manifest=json.loads((folder/'feedback-capture-manifest.json').read_bytes())
    assert manifest['status']=='failed' and set(world.destroyed)=={1,2}
    if flag=='foreign':assert [a.id for a in world.actors]==[999] and (spool/'quarantine.json').exists()
    if flag=='restore_failure':assert (spool/'quarantine.json').exists()
    if flag=='foreign_settings':
        assert (spool/'quarantine.json').exists() and world.settings.max_substeps==9 and world.settings.synchronous_mode
        assert 'world_settings_ownership_lost' in manifest['cleanup_failures']
        assert manifest['cleanup_observation']['controlled_cleanup_tick_count']==0 and world.frame==113

def test_prepare_binds_operator_transport_native_route_and_dependency_versions(monkeypatch):
    for key,value in {'AXIOM_CARLA_FEEDBACK_SSH':'operator@example','AXIOM_CARLA_FEEDBACK_PYTHON':'/python',
        'AXIOM_CARLA_FEEDBACK_SCRIPT':'/feedback.py','AXIOM_CARLA_FEEDBACK_DIR':'/results','AXIOM_CARLA_FEEDBACK_ENGINE_SPOOL':'/spool'}.items():monkeypatch.setenv(key,value)
    world=World();spec=specification();probe=native.probe(world.client(),spec);handler=FeedbackSensorHandler()
    request=ExperimentCreate(name='Mocked feedback contract',kind='sensor_capture',adapter_id='carla-feedback-sensor',input=specification(False),seed=42)
    with patch.object(handler,'_probe',return_value=probe):prepared=handler.prepare(request)
    assert prepared['spec']==spec and prepared['route_sha256']==probe['route']['sha256'] and prepared['workload']['controller_ticks']==20
    assert handler.metadata().resource_id=='carla' and prepared['native_support_script_sha256']==native.sha_file(native.support.__file__)
    changed=copy.deepcopy(probe);changed['active_dynamic_actor_ids']=[9]
    with patch.object(handler,'_probe',return_value=changed),pytest.raises(ValueError):handler.prepare(request)
    changed=copy.deepcopy(probe);changed['native_support_script_sha256']='a'*64
    with patch.object(handler,'_probe',return_value=changed),pytest.raises(ValueError):handler.prepare(request)

def test_prepare_refuses_policy_evaluator_and_sensor_ab_without_dispatch(monkeypatch):
    handler=FeedbackSensorHandler()
    for extra in [{'comparison_kind':'same_scene_sensor_ab'},{'model':{'id':'unbound','revision':1}}]:
        request=ExperimentCreate(name='Mocked unsupported contract',kind='sensor_capture',adapter_id='carla-feedback-sensor',input=specification(False),seed=42,**extra)
        with patch.object(handler,'_probe') as probe,pytest.raises(ValueError):handler.prepare(request)
        probe.assert_not_called()

def test_false_or_missing_physics_response_cannot_be_labeled_camera_feedback(tmp_path):
    spec,probe,manifest,folder,_=captured(tmp_path)
    manifest['ego_physics_enabled']=False;archive,receipt=bundle(folder,manifest)
    with pytest.raises(ValueError):validate_feedback_bundle(archive,receipt,spec,folder.name,probe)

def test_bgra_content_tamper_is_rejected_before_pose_or_motion_claim(tmp_path):
    spec,probe,manifest,folder,_=captured(tmp_path);frame=folder/'frames/0000-front.bgra8'
    data=bytearray(frame.read_bytes());data[12]^=1;frame.write_bytes(data);archive,receipt=bundle(folder,manifest)
    with pytest.raises(ValueError,match='content hash'):validate_feedback_bundle(archive,receipt,spec,folder.name,probe)

def test_operator_transport_paths_cannot_be_supplied_in_experiment_input(tmp_path):
    spec=specification(False);spec['engine_spool']='/not-authorized'
    with pytest.raises(ValueError):native.validate_spec(spec,prepared=False)

def test_replaced_foreign_quarantine_is_not_removed_after_our_cleanup(tmp_path):
    spool=tmp_path/'spool';spool.mkdir();foreign={'execution_id':'replaced foreign reservation'}
    with pytest.raises(RuntimeError):
        with native.engine_lease(spool,'unit','a'*64) as lease:
            (spool/'quarantine.json').write_bytes(native.canonical(foreign));lease['cleanup_confirmed']=True
    assert json.loads((spool/'quarantine.json').read_bytes())==foreign

def test_mocked_front_rear_cameras_share_every_actual_feedback_response_clock(tmp_path):
    spec=specification();rear=copy.deepcopy(spec['sensors'][0]);rear.update(key='rear',translation_m=[-1.5,0.,1.7],rotation_deg=[0.,180.,0.]);spec['sensors'].append(rear)
    spec,probe,manifest,folder,world=captured(tmp_path,spec=spec);archive,receipt=bundle(folder)
    _,_,trace,_=validate_feedback_bundle(archive,receipt,spec,folder.name,probe)
    assert len(manifest['records'])==40 and all(t['rgb_keys']==['front','rear'] for t in trace['ticks']) and world.destroyed==[3,2,1]

def test_unconfigured_transport_is_fail_closed_not_an_available_adapter(monkeypatch):
    for key in ['AXIOM_CARLA_FEEDBACK_SSH','AXIOM_CARLA_FEEDBACK_PYTHON','AXIOM_CARLA_FEEDBACK_SCRIPT','AXIOM_CARLA_FEEDBACK_DIR','AXIOM_CARLA_FEEDBACK_ENGINE_SPOOL',
        'AXIOM_CARLA_CAPTURE_SSH','AXIOM_CARLA_CAPTURE_PYTHON','AXIOM_CARLA_CAPTURE_DIR']:monkeypatch.delenv(key,raising=False)
    handler=FeedbackSensorHandler();assert handler.metadata().status=='not_configured'
    with pytest.raises(ValueError):handler._config()

def test_preview_does_not_accept_sensor_primitives():
    spec=specification(False);spec['sensors']=['rgb']
    with pytest.raises(ValueError):native.validate_spec(spec,prepared=False)

def test_native_float32_substep_readback_is_accepted_without_relaxing_profile(tmp_path):
    spec,probe,manifest,folder,_=captured(tmp_path)
    manifest['world_settings_during_execution']['max_substep_delta_time']=struct.unpack('<f',struct.pack('<f',.01))[0]
    archive,receipt=bundle(folder,manifest);validate_feedback_bundle(archive,receipt,spec,folder.name,probe)
    manifest['world_settings_during_execution']['max_substep_delta_time']=.02
    archive,receipt=bundle(folder,manifest)
    with pytest.raises(ValueError):validate_feedback_bundle(archive,receipt,spec,folder.name,probe)

def test_cross_architecture_reference_control_rounding_is_bounded(tmp_path):
    spec,probe,manifest,folder,_=captured(tmp_path)
    def small_rounding(trace):trace['ticks'][0]['requested_control']['throttle']-=1e-12
    archive,receipt=bundle(folder,manifest,small_rounding)
    validate_feedback_bundle(archive,receipt,spec,folder.name,probe)
    def changed_control(trace):trace['ticks'][0]['requested_control']['throttle']-=2e-6
    archive,receipt=bundle(folder,manifest,changed_control)
    with pytest.raises(ValueError,match='not generated'):validate_feedback_bundle(archive,receipt,spec,folder.name,probe)

def test_fresh_cleanup_fence_qualifies_destroy_ack_despite_stale_actor_cache(tmp_path):
    world=World(cached_destroy_snapshot=True)
    spec,probe,manifest,folder,world=captured(tmp_path,world);archive,receipt=bundle(folder)
    _,_,trace,_=validate_feedback_bundle(archive,receipt,spec,folder.name,probe)
    observation=manifest['cleanup_observation']
    assert observation['cached_dynamic_actor_ids']==[1,2]
    assert observation['observed_dynamic_actor_ids']==[] and observation['owned_snapshot_absent_actor_ids']==[1,2]
    assert observation['frame']==trace['ticks'][-1]['frame']+1 and observation['status']=='confirmed'
    assert len(trace['ticks'])==20 and len(manifest['records'])==20 and observation['controlled_cleanup_tick_count']==1
    assert manifest['callback_drain']['started']==30 and manifest['callback_drain']['finished']==30
    assert not (tmp_path/'spool/quarantine.json').exists()

def test_foreign_actor_before_cleanup_blocks_fence_and_is_not_deleted(tmp_path):
    world=World(foreign=True);spec=specification();probe=native.probe(world.client(),spec);spool=tmp_path/'spool';spool.mkdir();folder=tmp_path/'feedback_failure'
    with patch.object(native.support,'connect',return_value=(world.carla,world.client())),pytest.raises(RuntimeError):
        with native.engine_lease(spool,folder.name,hashlib.sha256(native.canonical(spec)).hexdigest()) as lease:native.capture({'spec':spec,'pinned_probe':probe},folder,lease=lease)
    observation=json.loads((folder/'feedback-capture-manifest.json').read_bytes())['cleanup_observation']
    assert observation['status']=='blocked_foreign_actors' and observation['controlled_cleanup_tick_count']==0
    assert world.frame==113 and [a.id for a in world.actors]==[999] and (spool/'quarantine.json').exists()

def test_foreign_actor_during_cleanup_fence_is_retained_and_prevents_confirmation(tmp_path):
    world=World(foreign_during_fence=True,cached_destroy_snapshot=True);spec=specification();probe=native.probe(world.client(),spec);spool=tmp_path/'spool';spool.mkdir();folder=tmp_path/'feedback_failure'
    with patch.object(native.support,'connect',return_value=(world.carla,world.client())),pytest.raises(RuntimeError):
        with native.engine_lease(spool,folder.name,hashlib.sha256(native.canonical(spec)).hexdigest()) as lease:native.capture({'spec':spec,'pinned_probe':probe},folder,lease=lease)
    observation=json.loads((folder/'feedback-capture-manifest.json').read_bytes())['cleanup_observation']
    assert observation['status']=='failed' and observation['observed_dynamic_actor_ids']==[999]
    assert [a.id for a in world.actors]==[999] and (spool/'quarantine.json').exists()

@pytest.mark.parametrize('mutation',[
    lambda m:m['cleanup_observation'].update(controlled_cleanup_tick_count=0),
    lambda m:m['cleanup_observation'].update(owned_snapshot_absent_actor_ids=[]),
    lambda m:m['cleanup_observation'].update(frame=999),
    lambda m:m['cleanup_observation'].update(input_frame=m['cleanup_observation']['input_frame']+1),
    lambda m:m['cleanup_observation'].update(observed_dynamic_actor_ids=[999]),
    lambda m:m['cleanup_observation'].update(source='cached_actor_list_without_native_frame')])
def test_rehashed_cleanup_claim_cannot_replace_a_fresh_separate_native_snapshot(tmp_path,mutation):
    spec,probe,manifest,folder,_=captured(tmp_path);mutation(manifest);archive,receipt=bundle(folder,manifest)
    with pytest.raises(ValueError):validate_feedback_bundle(archive,receipt,spec,folder.name,probe)
