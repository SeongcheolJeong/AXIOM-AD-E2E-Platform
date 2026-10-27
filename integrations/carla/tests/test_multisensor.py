"""Mocked native lifecycle/closure contracts, explicitly no CARLA/GPU validation."""
import copy,hashlib,json,struct,zipfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
import pytest
from ade2e.sensor_capture_schemas import CaptureSensor,SensorCaptureSpec
from ade2e.experiment_schemas import ExperimentCreate
from axiom_carla import multisensor_native as native
from axiom_carla.multisensor import StreamingCaptureHandler,validate_capture_bundle,write_native_diagnostics,DIAGNOSTIC_LIMIT
from test_configured_sensor_pose import fake_carla

def rig():
    spec=SensorCaptureSpec(sensors=[CaptureSensor(key='lidar-front',kind='lidar')]).model_dump(mode='json')
    spec['seed']=42
    for sensor in spec['sensors']:sensor['native_parameters']=CaptureSensor.model_validate({k:v for k,v in sensor.items() if k!='native_parameters'}).native_parameters()
    return spec

class Blueprint:
    def __init__(self,identity):
        self.id=identity;kind=next((k for k,v in native.BLUEPRINTS.items() if v==identity),None)
        self.values=CaptureSensor(key='sensor',kind=kind).native_parameters() if kind else {}
    def __iter__(self):
        for key,value in self.values.items():yield NS(id=key,is_modifiable=True,recommended_values=[value],__str__=lambda:value)
    def has_attribute(self,key):return key in self.values
    def get_attribute(self,key):return NS(is_modifiable=True)
    def set_attribute(self,key,value):self.values[key]=value

class World:
    def __init__(self,*,malformed=False,mismatch=False,overflow=False,unknown=False,transient=False):
        self.carla=fake_carla();self.settings=NS(synchronous_mode=False,fixed_delta_seconds=None)
        self.actors=[];self.destroyed=[];self.frame=10;self.malformed=malformed;self.mismatch=mismatch;self.overflow=overflow
        self.transient=transient
        self.spawn_pose=self.carla.Transform(self.carla.Location(x=12 if transient else 0,y=-8 if transient else 0,z=.7 if transient else 0),self.carla.Rotation(pitch=0,yaw=0,roll=0))
        if unknown:self.actors.append(NS(id=999,type_id='vehicle.unowned'))
    def get_settings(self):return copy.copy(self.settings)
    def apply_settings(self,value):self.settings=copy.copy(value)
    def get_blueprint_library(self):return NS(find=Blueprint)
    def get_map(self):return NS(name='/Game/Carla/Maps/Town10HD_Opt',to_opendrive=lambda:'Authored unit fixture, not a road model',get_spawn_points=lambda:[self.spawn_pose])
    def get_weather(self):return NS(**{k:0. for k in ('cloudiness','precipitation','precipitation_deposits','wind_intensity','sun_azimuth_angle','sun_altitude_angle','fog_density','fog_distance','wetness')})
    def get_actors(self):return list(self.actors)
    def actor(self,bp,pose,parent=None):
        world=self
        if parent is not None:
            # These lifecycle fixtures keep the ego heading zero.
            base=parent.pose.location
            pose=self.carla.Transform(self.carla.Location(x=base.x+pose.location.x,y=base.y+pose.location.y,z=base.z+pose.location.z),pose.rotation)
        class Actor:
            def __init__(self):self.id=len(world.actors)+1;self.type_id=bp.id;self.attributes=dict(bp.values);self.parent=parent;self.listener=None;self.pose=pose
            def get_transform(self):
                if world.transient and self.parent is None and world.frame==10:
                    return world.carla.Transform(world.carla.Location(x=0,y=0,z=0),world.carla.Rotation(pitch=0,yaw=0,roll=0))
                return self.pose
            def set_simulate_physics(self,value):self.physics=value
            def listen(self,callback):self.listener=callback
            def stop(self):self.listener=None
            def destroy(self):world.destroyed.append(self.id);world.actors.remove(self);return True
        actor=Actor();self.actors.append(actor);return actor
    def try_spawn_actor(self,bp,pose):return self.actor(bp,pose)
    def spawn_actor(self,bp,pose,attach_to):return self.actor(bp,pose,attach_to)
    def tick(self,timeout):
        self.frame+=1
        for actor in self.actors:
            if getattr(actor,'listener',None):
                data=NS(frame=self.frame-1 if self.mismatch else self.frame,timestamp=self.frame*.1,
                        transform=actor.get_transform(),raw_data=b'\0'*15 if self.malformed else struct.pack('<ffff',1,2,3,.5))
                for _ in range(5 if self.overflow else 1):actor.listener(data)
        return self.frame
    def get_snapshot(self):
        return NS(frame=self.frame,timestamp=NS(elapsed_seconds=self.frame*.1),find=lambda identity:next((a for a in self.actors if a.id==identity),None))
    def client(self):return NS(get_world=lambda:self,get_server_version=lambda:'1ae5356',get_client_version=lambda:'1ae5356')

def capture_fixture(tmp_path,world=None):
    world=world or World();spec=rig();folder=tmp_path/'capture_unit'
    with patch.object(native,'connect',return_value=(world.carla,world.client())):
        manifest=native.capture(spec,folder)
    return spec,manifest,folder,world

def bundle(folder,manifest=None,*,duplicate=False,extra=False,mutate_bytes=False):
    if manifest is not None:(folder/'capture-manifest.json').write_bytes(native.canonical(manifest))
    archive=folder/'unit.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_STORED) as target:
        for path in sorted(folder.rglob('*')):
            if path.is_file() and path.name!='unit.zip':
                if mutate_bytes and path.name.endswith('.f32'):target.writestr(str(path.relative_to(folder)),b'corrupted')
                else:target.write(path,str(path.relative_to(folder)))
        if duplicate:target.writestr('capture-manifest.json',(folder/'capture-manifest.json').read_bytes())
        if extra:target.writestr('unexpected.bin',b'extra')
    raw=(folder/'capture-manifest.json').read_bytes();source=json.loads(raw)
    receipt={'status':'completed','external_execution_id':folder.name,'manifest':'capture-manifest.json','manifest_sha256':hashlib.sha256(raw).hexdigest(),
             'frame_count':source['frame_count'],'archive_sha256':native.sha_file(archive),'archive_size_bytes':archive.stat().st_size}
    return archive,receipt

def test_native_rechecks_entire_accepted_profile_before_connection(tmp_path):
    bads=[]
    for mutation in ('sensor_tick','points_per_second','noise_seed','rotation_order','schema_extra','native_corrupt'):
        value=rig()
        if mutation=='rotation_order':value['sensors'][0]['rotation_order']='ambiguous'
        elif mutation=='schema_extra':value['host']='192.0.2.2'
        elif mutation=='native_corrupt':value['sensors'][0]['native_parameters']['range']='1000'
        else:
            parameter={'sensor_tick':'.5','points_per_second':'100000000','noise_seed':'42'}[mutation]
            value['sensors'][0]['parameters'][mutation]=parameter;value['sensors'][0]['native_parameters'][mutation]=parameter
        bads.append(value)
    for value in bads:
        with patch.object(native,'connect') as connect,pytest.raises(ValueError):native.capture(value,tmp_path/'must_not_exist')
        connect.assert_not_called()
    assert not (tmp_path/'must_not_exist').exists()

def test_mocked_lifecycle_complete_rig_pose_clock_and_original_manifest(tmp_path):
    spec,manifest,folder,world=capture_fixture(tmp_path)
    assert manifest['status']=='completed' and manifest['frame_count']==100 and len(manifest['records'])==100
    assert manifest['cleanup_status']=='confirmed' and not world.actors and len(world.destroyed)==2
    assert not world.settings.synchronous_mode and world.settings.fixed_delta_seconds is None
    archive,receipt=bundle(folder);validated,raw=validate_capture_bundle(archive,receipt,spec,folder.name)
    assert raw==(folder/'capture-manifest.json').read_bytes() and validated==manifest
    assert manifest['randomness'].startswith('requested_seed_metadata')
    assert all(r['mount_qualification']['qualified'] for r in manifest['records'])

def test_transient_spawn_api_origin_is_diagnostic_and_first_coherent_snapshot_is_truth(tmp_path):
    spec,manifest,folder,world=capture_fixture(tmp_path,World(transient=True))
    assert manifest['ego_pre_first_tick_api_transform']['position_m']==[0,0,0]
    assert manifest['ego_initial_transform']['position_m'][:2]==[12,-8]
    assert manifest['ego_initial_spawn_qualification']['qualified'] is True
    assert manifest['ego_initial_world_frame']==manifest['records'][0]['frame']
    archive,receipt=bundle(folder);validate_capture_bundle(archive,receipt,spec,folder.name)
    assert not world.actors

def test_wrong_coherent_spawn_is_refused_instead_of_ignoring_the_requested_pose(tmp_path):
    world=World(transient=True);original=world.try_spawn_actor
    def wrong_spawn(bp,pose):
        actor=original(bp,pose)
        actor.pose=world.carla.Transform(world.carla.Location(x=13,y=-8,z=.7),world.carla.Rotation(pitch=0,yaw=0,roll=0))
        return actor
    world.try_spawn_actor=wrong_spawn
    with patch.object(native,'connect',return_value=(world.carla,world.client())),pytest.raises(ValueError,match='requested native spawn'):
        native.capture(rig(),tmp_path/'capture_bad_spawn')
    state=json.loads((tmp_path/'capture_bad_spawn/capture-manifest.json').read_bytes())
    assert state['status']=='failed' and state['cleanup_status']=='confirmed' and not world.actors

def test_native_process_diagnostics_keep_bounded_exception_tail_with_original_hash(tmp_path):
    stderr=b'warning\n'*(DIAGNOSTIC_LIMIT//4)+b'FINAL NATIVE EXCEPTION\n'
    outputs=write_native_diagnostics(tmp_path,'single',b'partial native output',stderr)
    retained=(tmp_path/'single-native-stderr.log').read_bytes()
    assert len(retained)<=DIAGNOSTIC_LIMIT and retained.startswith(b'[AXIOM diagnostic truncated:')
    assert retained.endswith(b'FINAL NATIVE EXCEPTION\n')
    metadata=json.loads((tmp_path/'single-native-diagnostics.json').read_bytes())
    assert metadata['diagnostics']['stderr']['original_sha256']==hashlib.sha256(stderr).hexdigest()
    assert metadata['diagnostics']['stderr']['truncated'] is True and metadata['confirms_remote_cleanup'] is False
    assert len(outputs)==3

def test_failed_native_transport_retains_stderr_and_external_id_without_cleanup_claim(tmp_path,monkeypatch):
    for key,value in {'AXIOM_CARLA_CAPTURE_SSH':'operator@example.invalid','AXIOM_CARLA_CAPTURE_PYTHON':'/configured/python',
        'AXIOM_CARLA_CAPTURE_SCRIPT':'/configured/native.py','AXIOM_CARLA_CAPTURE_DIR':'/configured/captures'}.items():monkeypatch.setenv(key,value)
    handler=StreamingCaptureHandler();world=World();observed=native.probe(world.client())
    spec=ExperimentCreate(name='mock transport failure',kind='sensor_capture',adapter_id='carla-streaming-capture',
        input=SensorCaptureSpec(sensors=[CaptureSensor(key='lidar-front',kind='lidar')]).model_dump(mode='json'))
    process=NS(returncode=1,communicate=lambda *args,**kwargs:(b'partial response',b'Native exception boundary\n'))
    with patch.object(handler,'_probe',return_value=observed),patch('axiom_carla.multisensor.subprocess.Popen',return_value=process):
        snapshot=handler.prepare(spec)
        with pytest.raises(RuntimeError,match='Native capture failed'):handler.execute(snapshot,tmp_path,lambda:False)
    assert (tmp_path/'single-native-stderr.log').read_bytes()==b'Native exception boundary\n'
    journal=json.loads((tmp_path/'single-external-dispatch.json').read_bytes())
    assert journal['status']=='failed_or_unknown' and journal['cleanup_status']=='unknown' and journal['external_execution_id'].startswith('capture_')
    assert json.loads((tmp_path/'external-dispatch.json').read_bytes())['candidates']['single']==journal

@pytest.mark.parametrize('fault',['malformed','mismatch','overflow'])
def test_mocked_frame_failure_cleans_only_owned_actors_and_preserves_failure_manifest(tmp_path,fault):
    world=World(**{fault:True});spec=rig();folder=tmp_path/'capture_fault'
    with patch.object(native,'connect',return_value=(world.carla,world.client())),pytest.raises(RuntimeError):native.capture(spec,folder)
    result=json.loads((folder/'capture-manifest.json').read_bytes())
    assert result['status']=='failed' and result['cleanup_status']=='confirmed' and not world.actors
    assert len(world.destroyed)==2 and not world.settings.synchronous_mode

def test_unowned_world_is_refused_without_destroy_or_settings_change(tmp_path):
    world=World(unknown=True)
    with patch.object(native,'connect',return_value=(world.carla,world.client())),pytest.raises(ValueError,match='unowned'):native.capture(rig(),tmp_path/'capture_refused')
    assert world.destroyed==[] and world.actors[0].id==999 and not world.settings.synchronous_mode

@pytest.mark.parametrize('fault',['duplicate_zip','extra_zip','raw_hash','missing_record','bad_clock','pose_shift','readback','drop','callback','world_restore','destroy_ack','wrong_execution','wrong_manifest_hash'])
def test_signed_archive_cannot_hide_invalid_complete_sensor_evidence(tmp_path,fault):
    spec,manifest,folder,_=capture_fixture(tmp_path);value=copy.deepcopy(manifest)
    if fault=='missing_record':value['records'].pop()
    if fault=='bad_clock':value['records'][5]['timestamp_seconds']=value['records'][4]['timestamp_seconds']
    if fault=='pose_shift':value['records'][4]['sensor_world_transform']['matrix'][0][3]+=1
    if fault=='readback':value['sensors'][0]['native_readback']['range']='99'
    if fault=='drop':value['drop_count']=1
    if fault=='callback':value['callback_drain']['active']=1
    if fault=='world_restore':value['world_settings_after_cleanup']['fixed_delta_seconds']=.2
    if fault=='destroy_ack':value['actor_destroy_acknowledgements'][0]['acknowledged']=False
    archive,receipt=bundle(folder,value,duplicate=fault=='duplicate_zip',extra=fault=='extra_zip',mutate_bytes=fault=='raw_hash')
    if fault=='wrong_execution':receipt['external_execution_id']='another_execution'
    if fault=='wrong_manifest_hash':receipt['manifest_sha256']='0'*64
    with pytest.raises((ValueError,KeyError)):validate_capture_bundle(archive,receipt,spec,folder.name)

def test_same_scene_comparison_cannot_change_fixed_spawn_or_duration():
    handler=StreamingCaptureHandler();a=SensorCaptureSpec(sensors=[CaptureSensor(key='front',kind='rgb')]).model_dump(mode='json')
    b=copy.deepcopy(a);b['spawn_index']=1
    spec=ExperimentCreate(name='invalid sensor comparison',kind='sensor_capture',adapter_id='carla-streaming-capture',comparison_kind='same_scene_sensor_ab',input={'rig_a':a,'rig_b':b})
    with patch.object(handler,'_probe') as probe,pytest.raises(ValueError,match='fix the same scene'):handler.prepare(spec)
    probe.assert_not_called()

def test_declared_yaw_and_mount_readback_use_the_physical_left_direction():
    spec=rig();sensor=spec['sensors'][0];sensor['rotation_deg']=[0,90,0];sensor['translation_m']=[2,1,3]
    requested=native.declared_mount(sensor)
    # +90 degree RH yaw takes forward +x into left +y; left mount stays +1 in RH.
    assert requested[0][0]==pytest.approx(0,abs=1e-14) and requested[1][0]==pytest.approx(1)
    assert [requested[i][3] for i in range(3)]==[2,1,3]
    native_matrix=native.reflect(requested)
    assert native_matrix[1][0]==pytest.approx(-1) and native_matrix[1][3]==-1
