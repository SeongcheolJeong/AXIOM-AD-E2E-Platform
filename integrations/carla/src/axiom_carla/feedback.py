"""Optional operator-fixed SSH transport for native feedback/RGB experiments."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import struct
import subprocess
import uuid
import zipfile

from ade2e.adapters import AdapterCancelled
from ade2e.experiment_runtime import ExperimentHandlerMetadata, ExperimentExecution, ExperimentOutputFile
from . import multisensor_native as support
from . import sensor_scene
from .feedback_native import VERSION,MAX_BYTES,CAMERA_DEFAULTS,canonical,sha_file,validate_spec,plan_control
from .multisensor import write_native_diagnostics,_same_parameter

LIMITATIONS=[
    'The native map/ego oracle supplies a fixed lane route to a reference feedback controller; this is not a learned E2E policy.',
    'RGB is freshly rendered after each acknowledged control and native physics tick; cameras are observations, not controller inputs.',
    'One ego in an exclusive existing native world; no reactive traffic, collision/safety evaluator, physical RAW, ISP or measured calibration.',
    'Execution completion and observed vehicle response do not establish a driving verification verdict.',
    'Requested seed is retained metadata; independently seeded renderer/sensor noise and repeatability are not established.',
    'Viewer lane geometry is sampled from the pinned native map; lane ribbons are derived displays and not renderer pavement, measured map or scenery meshes.'
]

def _route_valid(route,spec,probe):
    if type(route) is not dict or route.get('schema_version')!='carla-native-map-route/1' or route.get('coordinate_convention')!='left_handed_x_forward_y_right_z_up':
        raise ValueError('Invalid native map route contract')
    if route.get('configuration_revision')!=spec['configuration_revision'] or route.get('spawn_index')!=spec['spawn_index'] or route.get('map_opendrive_sha256')!=probe.get('map_opendrive_sha256'):
        raise ValueError('Native route is not tied to the pinned map/spawn/configuration')
    document={key:value for key,value in route.items() if key!='sha256'}
    if hashlib.sha256(canonical(document)).hexdigest()!=route.get('sha256'):raise ValueError('Native route hash differs from the frozen route')
    points=route.get('points')
    if type(points) is not list or not 16<=len(points)<=76:raise ValueError('Native route point count exceeds the accepted bound')
    for point in points:
        if type(point) is not dict or set(point)!={'x_m','y_m','z_m','road_id','lane_id'}:raise ValueError('Invalid native route point')
        if any(type(point[k]) not in (int,float) or not math.isfinite(point[k]) or abs(point[k])>1e6 for k in ('x_m','y_m','z_m')) or any(type(point[k]) is not int for k in ('road_id','lane_id')):
            raise ValueError('Nonfinite native route geometry/identity')

def _ego_valid(state):
    if type(state) is not dict:raise ValueError('Missing native ego response state')
    for key in ('x_m','y_m','z_m','yaw_rad','speed_mps'):
        if type(state.get(key)) not in (int,float) or not math.isfinite(state[key]):raise ValueError('Nonfinite native ego state')
    if not 0<=state['speed_mps']<=100:raise ValueError('Native ego speed exceeds accepted telemetry domain')
    pose=state.get('world_transform',{});matrix=support.rigid_matrix(pose.get('matrix'))
    if pose.get('position_m')!=[state['x_m'],state['y_m'],state['z_m']] or any(abs(matrix[i][3]-state[k])>1e-8 for i,k in enumerate(('x_m','y_m','z_m'))):
        raise ValueError('Native ego position differs from original pose matrix')
    rotation=pose.get('rotation_deg')
    if type(rotation) is not list or len(rotation)!=3 or any(type(v) not in (int,float) or not math.isfinite(v) for v in rotation) or abs(math.radians(rotation[1])-state['yaw_rad'])>1e-10:
        raise ValueError('Native ego yaw differs from original pose')
    velocity=state.get('velocity_mps')
    if type(velocity) is not list or len(velocity)!=3 or any(type(v) not in (int,float) or not math.isfinite(v) for v in velocity) or abs(math.sqrt(sum(v*v for v in velocity))-state['speed_mps'])>1e-10:
        raise ValueError('Native ego speed differs from original velocity')

def _controls_valid(control):
    if type(control) is not dict or set(control)!={'throttle','brake','steer'}:raise ValueError('Invalid feedback control contract')
    for key,lo,hi in [('throttle',0,1),('brake',0,1),('steer',-1,1)]:
        if type(control[key]) not in (int,float) or not math.isfinite(control[key]) or not lo<=control[key]<=hi:raise ValueError('Invalid feedback actuator value')

def validate_feedback_bundle(archive,receipt,spec,external_id,pinned_probe):
    """Validate complete bytes, physical frame causality and reference planning."""
    validate_spec(spec)
    count=spec['duration_seconds']*10
    if receipt.get('status')!='completed' or receipt.get('external_execution_id')!=external_id or receipt.get('manifest')!='feedback-capture-manifest.json' or receipt.get('frame_count')!=count:
        raise ValueError('Native feedback receipt does not identify the pinned complete execution')
    with zipfile.ZipFile(archive) as bundle:
        infos=bundle.infolist();names=[i.filename for i in infos]
        if len(names)!=len(set(names)) or len(infos)>128 or any(i.compress_type!=zipfile.ZIP_STORED or i.is_dir() for i in infos):raise ValueError('Feedback archive file count/compression/uniqueness differs')
        if sum(i.file_size for i in infos)>MAX_BYTES+4*1024**2:raise ValueError('Feedback archive exceeds the complete byte budget')
        manifest_info=bundle.getinfo('feedback-capture-manifest.json')
        if manifest_info.file_size>2*1024**2:raise ValueError('Feedback manifest exceeds its bound')
        raw=bundle.read(manifest_info)
        if hashlib.sha256(raw).hexdigest()!=receipt.get('manifest_sha256'):raise ValueError('Feedback source manifest hash differs')
        manifest=json.loads(raw);configuration_hash=hashlib.sha256(canonical(spec)).hexdigest()
        if (manifest.get('schema_version')!='carla-feedback-native-result/1' or manifest.get('adapter_version')!=VERSION
            or manifest.get('configuration_hash')!=configuration_hash or manifest.get('configuration_revision')!=spec['configuration_revision']
            or manifest.get('seed')!=spec['seed'] or manifest.get('external_execution_id')!=external_id or manifest.get('controller')!=spec['controller']
            or manifest.get('camera_is_policy_input') is not False or manifest.get('native_coordinate_convention')!='left_handed_x_forward_y_right_z_up'
            or manifest.get('declared_coordinate_convention')!=spec['coordinate_convention'] or manifest.get('frame_count')!=count
            or manifest.get('status')!='completed' or manifest.get('cleanup_status')!='confirmed' or manifest.get('cleanup_failures')!=[]
            or manifest.get('world_settings_restored') is not True or manifest.get('drop_count')!=0 or manifest.get('ego_physics_enabled') is not True):
            raise ValueError('Feedback correlation/completion/physics/cleanup failed')
        interlock=manifest.get('engine_interlock',{});reservation=interlock.get('reservation',{})
        if (interlock.get('schema_version')!='carla-feedback-engine-interlock/1' or interlock.get('lock_acquired') is not True
            or interlock.get('reservation_cleared_after_confirmed_cleanup') is not True or reservation.get('schema_version')!='carla-feedback-engine-reservation/1'
            or reservation.get('external_execution_id')!=external_id or reservation.get('configuration_hash')!=configuration_hash
            or reservation.get('native_script_sha256')!=pinned_probe.get('native_script_sha256') or type(reservation.get('owner_pid')) is not int or reservation['owner_pid']<=0):
            raise ValueError('Feedback shared engine ownership reservation is not correlated/released')
        probe=manifest.get('probe')
        if probe!=pinned_probe or probe.get('active_dynamic_actor_ids')!=[]:raise ValueError('Feedback captured scene differs from preview free world')
        _route_valid(probe.get('route'),spec,probe)
        if manifest.get('world_settings_after_cleanup')!=probe.get('world_settings'):raise ValueError('Feedback original world settings were not restored')
        requested_settings={'synchronous_mode':True,'fixed_delta_seconds':.1,'no_rendering_mode':False,'substepping':True,'max_substep_delta_time':.01,'max_substeps':10}
        actual_settings=manifest.get('world_settings_during_execution',{})
        def same_setting(key,expected):
            actual=actual_settings.get(key)
            if type(expected) is float:
                return type(actual) in (int,float) and math.isfinite(actual) and struct.pack('<f',actual)==struct.pack('<f',expected)
            return type(actual) is type(expected) and actual==expected
        if set(actual_settings)!=set(requested_settings) or any(not same_setting(key,expected) for key,expected in requested_settings.items()):
            raise ValueError('Feedback fixed rendered/substepped native physics is not qualified')
        physics=manifest.get('vehicle_physics_readback')
        if type(physics) is not dict or set(physics)!={'mass_kg','drag_coefficient','center_of_mass','wheels','status'} or physics['status']!='native_engine_readback_not_measured_real_vehicle':raise ValueError('Missing exact native engine vehicle physics readback')
        def bounded(value,lo,hi):return type(value) in (int,float) and math.isfinite(value) and lo<=value<=hi
        if not bounded(physics['mass_kg'],1,100000) or not bounded(physics['drag_coefficient'],0,100):raise ValueError('Invalid native vehicle mass/drag readback')
        center=physics['center_of_mass']
        if type(center) is not dict or set(center)!={'x','y','z'} or any(not bounded(v,-100,100) for v in center.values()):raise ValueError('Invalid native vehicle center of mass')
        wheels=physics['wheels']
        if type(wheels) is not list or not 2<=len(wheels)<=18:raise ValueError('Invalid native vehicle wheel denominator')
        for wheel in wheels:
            if type(wheel) is not dict or set(wheel)!={'tire_friction','damping_rate','radius_cm','max_steer_angle_deg'} or not bounded(wheel['tire_friction'],0,100) or not bounded(wheel['damping_rate'],0,10000) or not bounded(wheel['radius_cm'],.1,1000) or not bounded(wheel['max_steer_angle_deg'],0,180):raise ValueError('Invalid native vehicle wheel physics readback')
        drain=manifest.get('callback_drain',{})
        if drain.get('accepting') is not False or drain.get('drained') is not True or drain.get('active')!=0 or type(drain.get('started')) is not int or drain['started']!=drain.get('finished') or drain['started']<(count+10)*len(spec['sensors']):
            raise ValueError('Feedback callbacks are not completely closed/drained')
        entries=manifest.get('artifacts');observed=manifest.get('sensors');records=manifest.get('records')
        if type(entries) is not list or type(observed) is not list or type(records) is not list or len(observed)!=len(spec['sensors']) or len(records)!=count*len(observed):raise ValueError('Incomplete feedback sensor/artifact denominator')
        by_name={};total=0
        for entry in entries:
            name=entry.get('name');path=PurePosixPath(name or '')
            if type(name) is not str or path.is_absolute() or '..' in path.parts or path.as_posix()!=name or '\\' in name or name in by_name or name=='feedback-capture-manifest.json':raise ValueError('Unsafe/duplicate feedback artifact path')
            if type(entry.get('size_bytes')) is not int or not 0<=entry['size_bytes']<=MAX_BYTES or not re.fullmatch('[a-f0-9]{64}',entry.get('sha256','')):raise ValueError('Invalid feedback artifact descriptor')
            info=bundle.getinfo(name)
            if info.file_size!=entry['size_bytes']:raise ValueError('Feedback native artifact size differs')
            total+=info.file_size
            if total>MAX_BYTES:raise ValueError('Feedback artifacts exceed the budget')
            digest=hashlib.sha256()
            with bundle.open(info) as source:
                for data in iter(lambda:source.read(1024*1024),b''):digest.update(data)
            if digest.hexdigest()!=entry['sha256']:raise ValueError('Feedback native artifact content hash differs')
            by_name[name]=entry
        if set(names)!={'feedback-capture-manifest.json',*by_name} or manifest.get('native_bytes')!=total:raise ValueError('Feedback archive is not a closed complete artifact set')
        descriptor=manifest.get('trace_artifact',{})
        if descriptor.get('name')!='feedback-trace.json' or descriptor!=by_name.get('feedback-trace.json') or descriptor['size_bytes']>1024*1024:raise ValueError('Missing bounded exact feedback trace')
        trace_raw=bundle.read('feedback-trace.json');trace=json.loads(trace_raw)
        if (trace.get('schema_version')!='carla-feedback-trace/1' or trace.get('adapter_version')!=VERSION or trace.get('configuration_hash')!=configuration_hash
            or trace.get('configuration_revision')!=spec['configuration_revision'] or trace.get('seed')!=spec['seed'] or trace.get('controller')!=spec['controller']
            or trace.get('route')!=probe['route'] or trace.get('camera_is_policy_input') is not False or trace.get('coordinate_convention')!='left_handed_x_forward_y_right_z_up'
            or type(trace.get('ticks')) is not list or len(trace['ticks'])!=count):raise ValueError('Feedback trace correlation differs from the frozen execution')
        keys=[s['key'] for s in spec['sensors']];ego_id=manifest.get('ego_actor_id');sensor_ids=[]
        for item,sensor in zip(observed,spec['sensors']):
            params=sensor['native_parameters'];sensor_ids.append(item.get('actor_id'))
            if item.get('key')!=sensor['key'] or item.get('kind')!='rgb' or item.get('blueprint_id')!='sensor.camera.rgb' or item.get('requested_native_parameters')!=params or set(item.get('native_readback',{}))!=set(params) or any(not _same_parameter(k,item['native_readback'][k],v) for k,v in params.items()):raise ValueError('Feedback camera native parameter readback differs')
            expected=support.declared_mount(sensor)
            if max(support.residual(support.reflect(expected),support.rigid_matrix(item.get('mount_native',{}).get('matrix'))))>2e-6 or max(support.residual(expected,support.rigid_matrix(item.get('declared_rh_mount_matrix'))))>2e-6:raise ValueError('Feedback camera declared mount differs')
            focal=640/(2*math.tan(math.radians(float(params['fov']))/2));intrinsics=item.get('pinhole_intrinsics',{})
            if intrinsics.get('width')!=640 or intrinsics.get('height')!=360 or intrinsics.get('cx')!=320 or intrinsics.get('cy')!=180 or abs(intrinsics.get('fx',0)-focal)>1e-8 or abs(intrinsics.get('fy',0)-focal)>1e-8 or intrinsics.get('distortion_status')!='engine_model_not_measured_calibration':raise ValueError('Feedback camera nominal pinhole model differs')
        expected_ids={ego_id,*sensor_ids};destroyed=manifest.get('actor_destroy_acknowledgements',[])
        if (type(ego_id) is not int or ego_id<=0 or len(expected_ids)!=1+len(sensor_ids) or any(type(i) is not int or i<=0 for i in sensor_ids)
            or manifest.get('ego_blueprint')!=spec['vehicle_blueprint'] or set(manifest.get('owned_actor_ids',[]))!=expected_ids
            or len(destroyed)!=len(expected_ids) or {a.get('actor_id') for a in destroyed}!=expected_ids or any(a.get('acknowledged') is not True for a in destroyed)):
            raise ValueError('Feedback owned actor destruction is not fully acknowledged')
        cleanup=manifest.get('cleanup_observation',{})
        if (cleanup.get('schema_version')!='carla-feedback-cleanup-observation/1' or cleanup.get('source')!='bounded_owned_sync_cleanup_fence/1'
            or cleanup.get('status')!='confirmed' or cleanup.get('callbacks_accepting') is not False
            or cleanup.get('controlled_cleanup_tick_count')!=1 or cleanup.get('foreign_actor_ids_before_fence')!=[]
            or cleanup.get('observed_dynamic_actor_ids')!=[] or cleanup.get('owned_actor_ids')!=manifest['owned_actor_ids']
            or cleanup.get('owned_snapshot_absent_actor_ids')!=sorted(expected_ids)
            or type(cleanup.get('cached_dynamic_actor_ids')) is not list or not set(cleanup['cached_dynamic_actor_ids']).issubset(expected_ids)
            or type(cleanup.get('input_frame')) is not int or type(cleanup.get('frame')) is not int
            or cleanup['frame']!=cleanup['input_frame']+1 or cleanup.get('tick_acknowledged_frame')!=cleanup['frame']):
            raise ValueError('Feedback cleanup actor absence has no fresh controlled snapshot fence')
        cleanup_input_stamp=cleanup.get('input_timestamp_seconds');cleanup_stamp=cleanup.get('timestamp_seconds')
        if (type(cleanup_input_stamp) not in (int,float) or type(cleanup_stamp) not in (int,float)
            or not math.isfinite(cleanup_input_stamp) or not math.isfinite(cleanup_stamp)
            or abs(cleanup_stamp-cleanup_input_stamp-.1)>1e-5 or cleanup.get('simulation_timestamp_ns')!=str(round(cleanup_stamp*1e9))):
            raise ValueError('Feedback cleanup fence clock is not one fixed native frame')
        warmup=manifest.get('warmup')
        if type(warmup) is not list or len(warmup)!=10:raise ValueError('Missing explicitly separated physics warm-up')
        for i,item in enumerate(warmup):
            _ego_valid(item.get('ego'))
            if type(item.get('frame')) is not int or type(item.get('timestamp_seconds')) not in (int,float) or not math.isfinite(item['timestamp_seconds']) or (i and (item['frame']!=warmup[i-1]['frame']+1 or abs(item['timestamp_seconds']-warmup[i-1]['timestamp_seconds']-.1)>1e-5)):raise ValueError('Invalid native feedback warm-up clock')
        scene_descriptor=manifest.get('scene_artifact',{});map_descriptor=manifest.get('map_artifact',{})
        if (scene_descriptor!=by_name.get('sensor-scene.json') or scene_descriptor.get('name')!='sensor-scene.json'
            or scene_descriptor.get('media_type')!='application/json' or scene_descriptor.get('size_bytes',0)>sensor_scene.SCENE_MAX_BYTES
            or map_descriptor!=by_name.get('map.xodr') or map_descriptor.get('name')!='map.xodr'
            or map_descriptor.get('media_type')!='application/xml' or map_descriptor.get('size_bytes',0)>sensor_scene.MAP_MAX_BYTES):
            raise ValueError('Feedback native scene/map original artifact is missing or oversized')
        scene=json.loads(bundle.read('sensor-scene.json'))
        sensor_scene.validate_scene(scene,bundle.read('map.xodr'),manifest,trace,trace_raw)
        if (scene['map']['sample_count']!=probe.get('scene_map_summary',{}).get('sample_count')
            or scene['map']['segment_count']!=probe.get('scene_map_summary',{}).get('segment_count')
            or hashlib.sha256(canonical(scene['map'])).hexdigest()!=probe.get('scene_map_summary',{}).get('map_geometry_sha256')
            or map_descriptor['size_bytes']!=probe.get('scene_map_summary',{}).get('map_size_bytes')):
            raise ValueError('Feedback sampled map geometry differs from its frozen capability probe')
        referenced={'feedback-trace.json','sensor-scene.json','map.xodr'};previous=None;distance=0.;maximum=0.
        for index,tick in enumerate(trace['ticks']):
            if tick.get('index')!=index or type(tick.get('frame')) is not int or tick.get('input_frame')!=tick['frame']-1 or tick.get('actuator_acknowledged') is not True or tick.get('rgb_keys')!=keys:raise ValueError('Feedback tick/control/camera identity differs')
            stamp=tick.get('timestamp_seconds');input_stamp=tick.get('input_timestamp_seconds')
            if type(stamp) not in (int,float) or type(input_stamp) not in (int,float) or not math.isfinite(stamp) or not math.isfinite(input_stamp) or abs(stamp-input_stamp-.1)>1e-5 or tick.get('simulation_timestamp_ns')!=str(round(stamp*1e9)):raise ValueError('Feedback causal response clock differs')
            if previous is None:
                if tick['input_frame']!=warmup[-1]['frame'] or input_stamp!=warmup[-1]['timestamp_seconds'] or tick.get('input_ego')!=warmup[-1]['ego']:raise ValueError('Feedback first input is not the final resting native warm-up state')
            elif tick['input_frame']!=previous['frame'] or input_stamp!=previous['timestamp_seconds'] or tick.get('input_ego')!=previous['ego']:raise ValueError('Feedback next input is not the previous native physics response')
            _ego_valid(tick.get('input_ego'));_ego_valid(tick.get('ego'))
            for key in ('requested_control','acknowledged_control','observed_control'):_controls_valid(tick.get(key))
            plan,requested=plan_control(tick['input_ego'],trace['route'],spec['controller'])
            # Linux x86 native execution and an Apple Silicon API may differ by
            # one libm rounding bit. This tolerance is only for recomputation;
            # original acknowledged/readback controls remain separately bound.
            if tick.get('planned_path')!=plan or any(abs(tick['requested_control'][axis]-requested[axis])>1e-10 for axis in requested):raise ValueError('Feedback plan/control was not generated from the pinned route and input state')
            if any(abs(tick[k][axis]-requested[axis])>1e-6 for k in ('acknowledged_control','observed_control') for axis in requested):raise ValueError('Feedback actuator acknowledgement/readback differs from the requested control')
            group=records[index*len(keys):(index+1)*len(keys)]
            for sensor,item,record in zip(spec['sensors'],observed,group):
                if record.get('index')!=index or record.get('key')!=sensor['key'] or record.get('frame')!=tick['frame'] or record.get('timestamp_seconds')!=stamp or record.get('simulation_timestamp_ns')!=tick['simulation_timestamp_ns'] or record.get('ego_world_transform')!=tick['ego']['world_transform'] or record.get('sensor_actor_id')!=item['actor_id'] or record.get('sensor_parent_actor_id')!=ego_id or record.get('sensor_blueprint')!='sensor.camera.rgb':raise ValueError('Feedback RGB is not correlated with its exact native response state')
                qualified=support.qualify_pose(record,sensor)
                if record.get('mount_qualification')!=qualified:raise ValueError('Feedback camera pose qualification differs from original observations')
                artifact=record.get('raw_artifact',{});name=f'frames/{index:04d}-{sensor["key"]}.bgra8'
                if artifact!=by_name.get(name) or artifact.get('name')!=name or artifact.get('size_bytes')!=640*360*4 or record.get('width')!=640 or record.get('height')!=360 or record.get('encoding')!='BGRA8 postprocessed engine RGB':raise ValueError('Missing complete exact feedback BGRA frame')
                referenced.add(name);preview=record.get('preview_artifact')
                if index in (0,count-1):
                    pname=f'previews/{index:04d}-{sensor["key"]}.png'
                    if preview!=by_name.get(pname) or preview.get('name')!=pname:raise ValueError('Missing native feedback endpoint preview')
                    referenced.add(pname)
                elif preview is not None:raise ValueError('Unexpected feedback preview')
            distance+=math.hypot(tick['ego']['x_m']-tick['input_ego']['x_m'],tick['ego']['y_m']-tick['input_ego']['y_m']);maximum=max(maximum,tick['ego']['speed_mps']);previous=tick
        if referenced!=set(by_name):raise ValueError('Feedback native artifact has no exact record provenance')
        if cleanup['input_frame']!=previous['frame'] or cleanup_input_stamp!=previous['timestamp_seconds']:
            raise ValueError('Feedback cleanup frame is not separated immediately after the last controller/RGB response')
        if type(manifest.get('distance_travelled_m')) not in (int,float) or not math.isfinite(manifest['distance_travelled_m']) or abs(distance-manifest['distance_travelled_m'])>1e-10 or type(manifest.get('maximum_speed_mps')) not in (int,float) or abs(maximum-manifest['maximum_speed_mps'])>1e-10:raise ValueError('Feedback motion metric differs from the original physics trace')
        return manifest,raw,trace,trace_raw

class FeedbackSensorHandler:
    def __init__(self,*,database=None):
        self.target=os.environ.get('AXIOM_CARLA_FEEDBACK_SSH',os.environ.get('AXIOM_CARLA_CAPTURE_SSH',''))
        self.python=os.environ.get('AXIOM_CARLA_FEEDBACK_PYTHON',os.environ.get('AXIOM_CARLA_CAPTURE_PYTHON',''))
        self.script=os.environ.get('AXIOM_CARLA_FEEDBACK_SCRIPT','')
        self.directory=os.environ.get('AXIOM_CARLA_FEEDBACK_DIR',os.environ.get('AXIOM_CARLA_CAPTURE_DIR',''))
        self.engine_spool=os.environ.get('AXIOM_CARLA_FEEDBACK_ENGINE_SPOOL','')
    def metadata(self):
        return ExperimentHandlerMetadata(id='carla-feedback-sensor',version=VERSION,kinds=['sensor_capture'],
            status='available' if all((self.target,self.python,self.script,self.directory,self.engine_spool)) else 'not_configured',
            fidelity='actual_carla_reference_feedback_rgb',resource_id='carla',cancellation_supported=False,
            description='Native map reference planning -> acknowledged feedback controls -> physics -> fresh aligned RGB. No learned camera policy or safety verdict.')
    def _config(self):
        if self.metadata().status!='available':raise ValueError('CARLA feedback transport is not configured')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+',self.target):raise ValueError('Invalid operator-configured feedback SSH target')
        if any(not p.startswith('/') or '\n' in p or '\0' in p for p in (self.python,self.script,self.directory,self.engine_spool)):raise ValueError('Feedback paths must be absolute operator-configured paths')
    def _command(self,args):return ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',self.target,shlex.join([self.python,self.script,*args])]
    def _transport_hash(self):return hashlib.sha256(canonical([self.target,self.python,self.script,self.directory,self.engine_spool])).hexdigest()
    def _probe(self,spec):
        self._config();result=subprocess.run(self._command(['--plan-probe']),input=canonical(spec),capture_output=True,timeout=45)
        if result.returncode:raise ValueError('Native feedback connection/route probe failed; inspect the configured host')
        if len(result.stdout)>1024*1024:raise ValueError('Native feedback probe exceeds its bound')
        return json.loads(result.stdout)
    def prepare(self,spec):
        if spec.kind!='sensor_capture' or spec.comparison_kind!='single' or any(v is not None for v in (spec.model,spec.evaluator,spec.sensor)):
            raise ValueError('Feedback v1 requires a single explicit rig/reference controller; model/evaluator/sensor metadata are not accepted')
        declared=json.loads(canonical(spec.input));validate_spec(declared,prepared=False)
        declared['seed']=spec.seed
        for sensor in declared['sensors']:sensor['native_parameters']={**CAMERA_DEFAULTS,**sensor['parameters']}
        validate_spec(declared);observed=self._probe(declared)
        if (observed.get('adapter_version')!=VERSION or observed.get('native_script_sha256')!=sha_file(Path(__file__).with_name('feedback_native.py'))
            or observed.get('native_support_script_sha256')!=sha_file(Path(support.__file__))
            or observed.get('native_scene_script_sha256')!=sha_file(Path(sensor_scene.__file__))):
            raise ValueError('Remote feedback native codec/support differs from the installed adapter')
        if observed.get('active_dynamic_actor_ids'):raise ValueError('Native feedback world has unowned actors; no execution dispatched')
        if observed.get('server_version')!=declared['expected_server_version'] or observed.get('client_version')!=declared['expected_server_version'] or observed.get('map','').split('/')[-1]!=declared['expected_map'].split('/')[-1]:raise ValueError('Native feedback map/server/client differs from the requested build')
        if observed.get('ego_blueprint_id')!=declared['vehicle_blueprint'] or not 0<=declared['spawn_index']<len(observed.get('spawn_points',[])):raise ValueError('Native feedback ego/spawn is not available')
        for sensor in declared['sensors']:
            attrs=observed.get('sensors',{}).get('rgb',{}).get('attributes',{})
            if any(key not in attrs or attrs[key].get('modifiable') is not True for key in sensor['native_parameters']):raise ValueError('Native feedback camera capability does not support the requested parameters')
        _route_valid(observed.get('route'),declared,observed)
        geometry=observed.get('scene_map_summary',{})
        if (geometry.get('schema_version')!='sensor-scene-map-probe/1' or type(geometry.get('sample_count')) is not int
            or not 1<=geometry['sample_count']<=sensor_scene.MAX_SAMPLES or type(geometry.get('segment_count')) is not int
            or not 1<=geometry['segment_count']<=sensor_scene.MAX_SEGMENTS or type(geometry.get('map_size_bytes')) is not int
            or not 1<=geometry['map_size_bytes']<=sensor_scene.MAP_MAX_BYTES
            or not re.fullmatch('[a-f0-9]{64}',geometry.get('map_geometry_sha256',''))):raise ValueError('Native sampled scene map capability is unavailable or exceeds its bound')
        return {'schema_version':'carla-feedback-experiment/1','spec':declared,'native_capability_snapshot':observed,
            'operator_transport_hash':self._transport_hash(),'adapter_script_sha256':sha_file(__file__),
            'native_script_sha256':observed['native_script_sha256'],
            'native_support_script_sha256':observed['native_support_script_sha256'],
            'native_scene_script_sha256':observed['native_scene_script_sha256'],'route_sha256':observed['route']['sha256'],
            'workload':{'capture_count':1,'controller_ticks':declared['duration_seconds']*10,'warmup_ticks':10,
                'cleanup_observation_ticks':1,
                'frames_per_sensor':declared['duration_seconds']*10,'camera_count':len(declared['sensors']),
                'native_bytes_upper_bound':MAX_BYTES,'scene_geometry_sample_count':geometry['sample_count'],
                'scene_geometry_segment_count':geometry['segment_count'],'map_original_bytes':geometry['map_size_bytes'],
                'resource':'carla','requires_exclusive_world':True},'limitations':LIMITATIONS}
    def execute(self,snapshot,work_dir,should_cancel):
        self._config();work=Path(work_dir);work.mkdir(parents=True,exist_ok=True)
        if snapshot.get('schema_version')!='carla-feedback-experiment/1' or snapshot.get('operator_transport_hash')!=self._transport_hash():raise ValueError('Feedback configured transport/snapshot changed after preview')
        expected_hashes={'adapter_script_sha256':sha_file(__file__),'native_script_sha256':sha_file(Path(__file__).with_name('feedback_native.py')),
            'native_support_script_sha256':sha_file(Path(support.__file__)),'native_scene_script_sha256':sha_file(Path(sensor_scene.__file__))}
        if (any(snapshot.get(k)!=v for k,v in expected_hashes.items())
            or snapshot.get('native_capability_snapshot',{}).get('adapter_version')!=VERSION):
            raise ValueError('Local feedback native/helper/adapter version changed after preview; dispatch refused')
        spec=validate_spec(snapshot['spec']);probe=self._probe(spec)
        if probe!=snapshot['native_capability_snapshot'] or probe.get('active_dynamic_actor_ids'):raise ValueError('Pinned native feedback scene/capability changed before dispatch')
        if should_cancel():raise AdapterCancelled('Feedback experiment cancelled before native dispatch')
        external_id='feedback_'+uuid.uuid4().hex;remote_dir=self.directory.rstrip('/')+'/'+external_id
        journal={'schema_version':'external-feedback-dispatch/1','external_execution_id':external_id,'configuration_hash':hashlib.sha256(canonical(spec)).hexdigest(),'status':'dispatching','cleanup_status':'unknown'}
        journal_path=work/'external-dispatch.json'
        def save_journal():journal_path.write_bytes(canonical(journal))
        save_journal();process=subprocess.Popen(self._command(['--output',remote_dir,'--engine-spool',self.engine_spool]),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:stdout,stderr=process.communicate(canonical({'spec':spec,'pinned_probe':probe}),timeout=180)
        except subprocess.TimeoutExpired:
            process.kill();stdout,stderr=process.communicate();write_native_diagnostics(work,'feedback',stdout,stderr)
            journal['status']='outcome_unknown';save_journal();raise RuntimeError('Native feedback timed out; cleanup/outcome require external execution inspection')
        outputs=write_native_diagnostics(work,'feedback',stdout,stderr)
        if process.returncode or len(stdout)>65536:
            journal['status']='failed_or_unknown';save_journal();raise RuntimeError('Native feedback failed; external execution ID/diagnostics retained for inspection')
        journal['status']='response_received_unverified';save_journal();receipt=json.loads(stdout)
        if receipt.get('status')!='completed' or type(receipt.get('archive_size_bytes')) is not int or not 0<receipt['archive_size_bytes']<=MAX_BYTES+4*1024**2:raise ValueError('Invalid or oversized native feedback receipt')
        archive=work/'feedback-native-capture.zip'
        result=subprocess.run(['scp','-q','-o','BatchMode=yes','-o','ConnectTimeout=10',self.target+':'+remote_dir+'/capture.zip',str(archive)],capture_output=True,timeout=180)
        if result.returncode:raise RuntimeError('Feedback completed but native evidence transfer failed; retained external execution requires inspection')
        if archive.stat().st_size!=receipt['archive_size_bytes'] or sha_file(archive)!=receipt.get('archive_sha256'):raise ValueError('Feedback transferred archive hash/size differs from receipt')
        manifest,manifest_raw,trace,trace_raw=validate_feedback_bundle(archive,receipt,spec,external_id,probe)
        manifest_path=work/'feedback-capture-manifest.json';manifest_path.write_bytes(manifest_raw)
        trace_path=work/'feedback-trace.json';trace_path.write_bytes(trace_raw)
        with zipfile.ZipFile(archive) as bundle:
            for entry in manifest['artifacts']:
                if entry['name'].startswith('previews/'):
                    preview=work/('feedback-'+PurePosixPath(entry['name']).name);preview.write_bytes(bundle.read(entry['name']));outputs.append(ExperimentOutputFile(preview,preview.name,'image/png'))
                elif entry['name'] in ('sensor-scene.json','map.xodr'):
                    original=work/entry['name'];original.write_bytes(bundle.read(entry['name']))
                    outputs.append(ExperimentOutputFile(original,original.name,entry['media_type']))
        journal.update(status='completed',cleanup_status='confirmed',archive_sha256=receipt['archive_sha256']);save_journal()
        outputs.extend([ExperimentOutputFile(archive,archive.name,'application/zip'),ExperimentOutputFile(manifest_path,manifest_path.name,'application/json'),
            ExperimentOutputFile(trace_path,trace_path.name,'application/json'),ExperimentOutputFile(journal_path,journal_path.name,'application/json')])
        if should_cancel():raise AdapterCancelled('Native feedback completed and cleanup confirmed; cancellation prevents publication')
        return ExperimentExecution(summary={'schema_version':'carla-feedback-sensor-result/1','capture_manifest_artifact':manifest_path.name,
            'trace_artifact':trace_path.name,'archive_artifact':archive.name,'external_execution_id':external_id,
            'configuration_hash':manifest['configuration_hash'],'configuration_revision':spec['configuration_revision'],'seed':spec['seed'],
            'frame_count':manifest['frame_count'],'sensor_count':len(manifest['sensors']),'drop_count':0,
            'distance_travelled_m':manifest['distance_travelled_m'],'maximum_speed_mps':manifest['maximum_speed_mps'],
            'observed_response':{'ego_moved':manifest['distance_travelled_m']>.05,'fresh_rgb_frames':len(manifest['records']),
                'aligned_control_response_ticks':len(trace['ticks']),'camera_is_policy_input':False},
            'execution_status':'completed','evaluation_status':'not_evaluated','verification_verdict':'not_evaluated',
            'calibration_status':'uncalibrated_engine_model','cleanup_status':'confirmed','route_sha256':probe['route']['sha256'],
            'scene_artifact':'sensor-scene.json','scene_sha256':manifest['scene_artifact']['sha256'],
            'map_artifact':'map.xodr','map_sha256':manifest['map_artifact']['sha256'],'scene_geometry_fidelity':sensor_scene.FIDELITY,
            'scene_geometry_sample_count':probe['scene_map_summary']['sample_count'],'scene_geometry_segment_count':probe['scene_map_summary']['segment_count'],
            'limitations':snapshot['limitations']},artifacts=outputs,fidelity='actual_carla_reference_feedback_rgb')

def create_feedback_handler(*,database=None):return FeedbackSensorHandler(database=database)
