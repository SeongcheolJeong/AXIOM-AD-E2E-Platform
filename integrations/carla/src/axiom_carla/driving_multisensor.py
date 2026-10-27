"""Optional native driving sensor adapter; original stationary/feedback contracts stay unchanged."""
import copy
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
from ade2e.driving_sensor_capture_schemas import DrivingSensorCaptureSpec
from ade2e.experiment_runtime import ExperimentHandlerMetadata, ExperimentExecution, ExperimentOutputFile
from . import driving_multisensor_native as native
from . import feedback_native, multisensor_native as support, sensor_scene
from .feedback import _route_valid, _ego_valid, _controls_valid
from .multisensor import _same_parameter, write_native_diagnostics
from .multisensor_native import BLUEPRINTS, canonical, sha_file, declared_mount, reflect, rigid_matrix, residual, qualify_pose

VERSION = native.VERSION
ADAPTER = 'carla-driving-multisensor'
FIDELITY = 'actual_carla_reference_driving_multisensor'
MAX_BYTES = native.MAX_BYTES
MAX_MANIFEST_BYTES = native.MAX_MANIFEST_BYTES
validate_spec = native.validate_spec
LIMITATIONS = [
    'Native map and ego state drive the reference lane controller; sensors are observations, not learned policy inputs.',
    'One physical ego in an exclusive existing CARLA world; no traffic or safety evaluator.',
    'Postprocessed RGB, ray-cast LiDAR and radar are uncalibrated native engine models; no physical RAW or real-world validation.',
    'A/B reruns both rigs sequentially; native world clock, renderer state and vehicle trajectories may differ.',
    'Requested seed is retained; independent sensor-noise randomization and deterministic replay are not established.',
]


def _validate_motion(manifest, spec, pinned_probe):
    count = spec['duration_seconds'] * 10
    configuration_hash = hashlib.sha256(canonical(spec)).hexdigest()
    if (manifest.get('probe') != pinned_probe or manifest.get('controller') != spec['controller']
            or manifest.get('fidelity') != FIDELITY or manifest.get('calibration_status') != 'uncalibrated_engine_model'
            or manifest.get('camera_is_policy_input') is not False or manifest.get('sensors_are_policy_input') is not False
            or manifest.get('ego_physics_enabled') is not True):
        raise ValueError('Driving motion source, scope or native physics differs')
    _route_valid(pinned_probe.get('route'), {**spec, 'configuration_revision': spec['rig_revision']}, pinned_probe)
    route = pinned_probe['route']
    length = sum(math.hypot(b['x_m'] - a['x_m'], b['y_m'] - a['y_m']) for a, b in zip(route['points'], route['points'][1:]))
    if length + 1e-6 < native.required_route_horizon(spec):
        raise ValueError('Native route does not cover the driving capture horizon')
    expected_ids = {manifest.get('ego_actor_id'), *(s.get('actor_id') for s in manifest.get('sensors', []))}
    if any(type(identity) is not int or identity <= 0 for identity in expected_ids):
        raise ValueError('Driving owned actor IDs are missing')
    _validate_interlock(manifest, configuration_hash, pinned_probe, expected_ids)
    _validate_physics(manifest, configuration_hash, pinned_probe, expected_ids)
    _validate_cleanup(manifest, configuration_hash, pinned_probe, expected_ids)
    warmup = manifest.get('warmup')
    if type(warmup) is not list or len(warmup) != 10 or manifest.get('warmup_policy') != '10_native_braked_settling_frames_all_sensor_queues_drained/1':
        raise ValueError('Driving startup requires 10 separate braked native warmup frames')
    for index, item in enumerate(warmup):
        _ego_valid(item.get('ego'))
        if (type(item.get('frame')) is not int or type(item.get('timestamp_seconds')) not in (int, float)
                or not math.isfinite(item['timestamp_seconds']) or (index and (item['frame'] != warmup[index - 1]['frame'] + 1
                or abs(item['timestamp_seconds'] - warmup[index - 1]['timestamp_seconds'] - .1) > 1e-5))):
            raise ValueError('Driving warmup native clock is discontinuous')
    initial = warmup[-1]
    spawn = manifest['ego_requested_spawn_transform']['position_m']
    if (manifest.get('ego_initial_transform') != initial['ego']['world_transform']
            or manifest.get('ego_initial_world_frame') != initial['frame']
            or manifest.get('ego_initial_timestamp_seconds') != initial['timestamp_seconds']
            or initial['ego']['speed_mps'] > .2
            or math.hypot(initial['ego']['x_m'] - spawn[0], initial['ego']['y_m'] - spawn[1]) > .25):
        raise ValueError('Driving startup is not the settled ego at the declared spawn')
    motion = manifest.get('motion')
    if (type(motion) is not dict or motion.get('schema_version') != 'carla-driving-motion-trace/1'
            or motion.get('adapter_version') != VERSION or motion.get('configuration_hash') != configuration_hash
            or motion.get('rig_revision') != spec['rig_revision'] or motion.get('seed') != spec['seed']
            or motion.get('coordinate_convention') != 'left_handed_x_forward_y_right_z_up'
            or motion.get('controller') != spec['controller'] or motion.get('route') != route
            or motion.get('camera_is_policy_input') is not False or motion.get('sensors_are_policy_input') is not False
            or type(motion.get('ticks')) is not list or len(motion['ticks']) != count):
        raise ValueError('Driving motion trace differs from the pinned source')
    keys = [sensor['key'] for sensor in spec['sensors']]
    records = manifest.get('records')
    if type(records) is not list or len(records) != count * len(keys):
        raise ValueError('Driving sensor denominator differs from motion ticks')
    previous = initial
    distance = 0.
    maximum = 0.
    for index, tick in enumerate(motion['ticks']):
        if (tick.get('index') != index or type(tick.get('frame')) is not int or tick.get('input_frame') != previous['frame']
                or tick['frame'] != tick['input_frame'] + 1 or tick.get('input_timestamp_seconds') != previous['timestamp_seconds']
                or tick.get('input_ego') != previous['ego'] or tick.get('actuator_acknowledged') is not True
                or tick.get('sensor_keys') != keys):
            raise ValueError('Driving input, acknowledgement or sensor identity does not follow its prior native frame')
        stamp = tick.get('timestamp_seconds')
        if (type(stamp) not in (int, float) or not math.isfinite(stamp)
                or abs(stamp - tick['input_timestamp_seconds'] - .1) > 1e-5
                or tick.get('simulation_timestamp_ns') != str(round(stamp * 1e9))):
            raise ValueError('Driving f to f+1 clock differs')
        _ego_valid(tick.get('input_ego')); _ego_valid(tick.get('ego'))
        for key in ('requested_control', 'acknowledged_control', 'observed_control'):
            _controls_valid(tick.get(key))
        planned, requested = feedback_native.plan_control(tick['input_ego'], route, spec['controller'])
        if tick.get('planned_path') != planned or any(abs(tick['requested_control'][key] - requested[key]) > 1e-10 for key in requested):
            raise ValueError('Driving plan/control differs from reference controller recomputation')
        if any(abs(tick[field][key] - requested[key]) > 1e-6 for field in ('acknowledged_control', 'observed_control') for key in requested):
            raise ValueError('Driving requested and applied control differ')
        for record, key in zip(records[index * len(keys):(index + 1) * len(keys)], keys):
            if (record.get('index') != index or record.get('key') != key or record.get('frame') != tick['frame']
                    or record.get('timestamp_seconds') != stamp or record.get('simulation_timestamp_ns') != tick['simulation_timestamp_ns']
                    or record.get('ego_world_transform') != tick['ego']['world_transform']):
                raise ValueError('Driving sensor record is not tied to its exact moving ego response')
        distance += math.hypot(tick['ego']['x_m'] - tick['input_ego']['x_m'], tick['ego']['y_m'] - tick['input_ego']['y_m'])
        maximum = max(maximum, tick['ego']['speed_mps'])
        previous = tick
    for key, expected in [('distance_travelled_m', distance), ('maximum_speed_mps', maximum)]:
        actual = manifest.get(key)
        if type(actual) not in (int, float) or not math.isfinite(actual) or abs(actual - expected) > 1e-8:
            raise ValueError('Driving summary differs from its original motion trace')
    drain = manifest.get('callback_drain', {})
    if type(drain.get('started')) is not int or drain['started'] < (count + 10) * len(keys):
        raise ValueError('Driving warmup sensor callbacks are not included in drained closure')

def validate_driving_bundle(archive,receipt,rig,external_id,pinned_probe):
    """Recompute closure, complete denominators, native clock and mounted poses."""
    validate_spec(rig)
    if (receipt.get('status')!='completed' or receipt.get('external_execution_id')!=external_id
        or receipt.get('frame_count')!=rig['duration_seconds']*10 or receipt.get('manifest')!='capture-manifest.json'):
        raise ValueError('Native receipt does not identify the complete pinned external execution')
    with zipfile.ZipFile(archive) as bundle:
        infos=bundle.infolist();names=[i.filename for i in infos]
        if len(names)!=len(set(names)):raise ValueError('Duplicate native archive members')
        if len(infos)>1210 or any(i.compress_type!=zipfile.ZIP_STORED or i.is_dir() for i in infos):
            raise ValueError('Native archive exceeds supported file count or compression format')
        if sum(i.file_size for i in infos)>MAX_BYTES+MAX_MANIFEST_BYTES:raise ValueError('Native archive exceeds the complete uncompressed budget')
        manifest_info=bundle.getinfo('capture-manifest.json')
        if manifest_info.file_size>MAX_MANIFEST_BYTES:raise ValueError('Native capture manifest exceeds its bound')
        raw=bundle.read(manifest_info)
        if hashlib.sha256(raw).hexdigest()!=receipt.get('manifest_sha256'):raise ValueError('Native manifest hash differs from the source receipt')
        manifest=json.loads(raw)
        if (manifest.get('schema_version')!='carla-driving-multisensor-result/1' or manifest.get('adapter_version')!=VERSION
            or manifest.get('configuration_hash')!=hashlib.sha256(canonical(rig)).hexdigest()
            or manifest.get('external_execution_id')!=external_id or manifest.get('seed')!=rig['seed']
            or manifest.get('rig_revision')!=rig['rig_revision'] or manifest.get('declared_coordinate_convention')!=rig['coordinate_convention']
            or manifest.get('native_coordinate_convention')!='left_handed_x_forward_y_right_z_up'
            or manifest.get('status')!='completed' or manifest.get('cleanup_status')!='confirmed'
            or manifest.get('cleanup_failures')!=[] or manifest.get('world_settings_restored') is not True or manifest.get('drop_count')!=0
            or manifest.get('frame_count')!=rig['duration_seconds']*10):raise ValueError('Capture correlation/completion/cleanup failed')
        drain=manifest.get('callback_drain',{})
        if (drain.get('accepting') is not False or drain.get('drained') is not True or drain.get('active')!=0
            or type(drain.get('started')) is not int or drain['started']!=drain.get('finished')
            or drain['started']<manifest['frame_count']*len(rig['sensors'])):raise ValueError('Native callback closure is incomplete')
        before=manifest.get('probe',{})
        if (before.get('server_version')!=rig['expected_server_version'] or before.get('client_version')!=rig['expected_server_version']
            or before.get('map','').split('/')[-1]!=rig['expected_map'].split('/')[-1] or before.get('active_dynamic_actor_ids')!=[]):
            raise ValueError('Captured native scene differs from the pinned free world')
        spawns=before.get('spawn_points',[])
        if (hashlib.sha256(canonical(spawns)).hexdigest()!=before.get('spawn_points_sha256')
            or not 0<=rig['spawn_index']<len(spawns) or manifest.get('ego_requested_spawn_transform')!=spawns[rig['spawn_index']]):
            raise ValueError('Native requested spawn differs from the pinned map spawn geometry')
        _validate_motion(manifest,rig,pinned_probe)
        if manifest.get('world_settings_after_cleanup')!=before.get('world_settings'):
            raise ValueError('Restored native world settings differ from the original readback')
        descriptors=manifest.get('artifacts');observed=manifest.get('sensors');records=manifest.get('records')
        if not isinstance(descriptors,list) or not isinstance(observed,list) or not isinstance(records,list):raise ValueError('Incomplete native manifest arrays')
        declared={sensor['key']:sensor for sensor in rig['sensors']}
        if len(observed)!=len(declared) or {s.get('key') for s in observed}!=set(declared):raise ValueError('Captured sensor denominator differs from rig')
        actor_ids=[];sensor_metadata={}
        for item in observed:
            sensor=declared[item['key']];parameters=sensor['native_parameters'];actor_ids.append(item.get('actor_id'))
            if (item.get('kind')!=sensor['kind'] or item.get('blueprint_id')!=BLUEPRINTS[sensor['kind']]
                or item.get('requested_native_parameters')!=parameters or set(item.get('native_readback',{}))!=set(parameters)
                or any(not _same_parameter(k,item['native_readback'][k],v) for k,v in parameters.items())):
                raise ValueError('Native sensor configuration/readback differs from the frozen rig')
            expected=declared_mount(sensor);actual=rigid_matrix(item['mount_native']['matrix'])
            if max(residual(reflect(expected),actual))>2e-6:raise ValueError('Native requested mount differs from the declared RH transform')
            if max(residual(expected,rigid_matrix(item['declared_rh_mount_matrix'])))>2e-6:raise ValueError('Native declared mount evidence differs')
            if sensor['kind']=='rgb':
                f=640/(2*math.tan(math.radians(float(parameters['fov']))/2));intrinsics=item.get('pinhole_intrinsics',{})
                if (intrinsics.get('width')!=640 or intrinsics.get('height')!=360 or intrinsics.get('cx')!=320 or intrinsics.get('cy')!=180
                    or abs(intrinsics.get('fx',0)-f)>1e-8 or abs(intrinsics.get('fy',0)-f)>1e-8
                    or intrinsics.get('distortion_status')!='engine_model_not_measured_calibration'):raise ValueError('Camera model intrinsics differ from the native FOV')
            sensor_metadata[item['key']]=item
        ego_id=manifest.get('ego_actor_id')
        if (type(ego_id) is not int or ego_id<=0 or len(set(actor_ids))!=len(actor_ids)
            or any(type(a) is not int or a<=0 or a==ego_id for a in actor_ids)):raise ValueError('Native sensor/ego identities are invalid')
        expected_actors={ego_id,*actor_ids};destroyed=manifest.get('actor_destroy_acknowledgements',[])
        if (manifest.get('ego_blueprint')!=rig['vehicle_blueprint'] or set(manifest.get('owned_actor_ids',[]))!=expected_actors
            or len(destroyed)!=len(expected_actors) or {a.get('actor_id') for a in destroyed}!=expected_actors
            or any(a.get('acknowledged') is not True for a in destroyed)):
            raise ValueError('Native owned actor lifecycle acknowledgement is incomplete')
        by_name={};total=0
        for entry in descriptors:
            name=entry.get('name');path=PurePosixPath(name or '')
            if (type(name) is not str or path.is_absolute() or '..' in path.parts or name in by_name or name=='capture-manifest.json'
                or path.as_posix()!=name or '\\' in name):raise ValueError('Unsafe/duplicate native artifact name')
            if (type(entry.get('size_bytes')) is not int or not 0<=entry['size_bytes']<=MAX_BYTES
                or not re.fullmatch(r'[a-f0-9]{64}',entry.get('sha256',''))):raise ValueError('Invalid native artifact descriptor')
            info=bundle.getinfo(name)
            if info.file_size!=entry['size_bytes']:raise ValueError('Native artifact size mismatch')
            total+=info.file_size
            if total>MAX_BYTES:raise ValueError('Native files exceed the accepted complete evidence budget')
            sha=hashlib.sha256()
            with bundle.open(info) as source:
                for chunk in iter(lambda:source.read(1024*1024),b''):sha.update(chunk)
            if sha.hexdigest()!=entry['sha256']:raise ValueError('Native artifact digest mismatch')
            by_name[name]=entry
        if set(names)!={'capture-manifest.json',*by_name} or manifest.get('native_bytes')!=total:
            raise ValueError('Native archive closure/byte denominator differs')
        count=manifest['frame_count'];keys=[s['key'] for s in rig['sensors']]
        if len(records)!=count*len(keys):raise ValueError('Missing or extra native sensor records')
        referenced=set();previous_frame=None;previous_stamp=None
        for index in range(count):
            tick=records[index*len(keys):(index+1)*len(keys)]
            if [record.get('key') for record in tick]!=keys:raise ValueError('Native record order/identity differs from rig')
            frames={r.get('frame') for r in tick};stamps={r.get('timestamp_seconds') for r in tick}
            if len(frames)!=1 or len(stamps)!=1:raise ValueError('Native sensors are not frame/time aligned')
            frame=next(iter(frames));stamp=next(iter(stamps))
            if (type(frame) is not int or frame<0 or type(stamp) not in (int,float) or not math.isfinite(stamp) or stamp<0
                or (previous_frame is not None and frame!=previous_frame+1)
                or (previous_stamp is not None and abs(stamp-previous_stamp-.1)>1e-5)):
                raise ValueError('Native frame/clock progression differs from the fixed 10 Hz input')
            for record in tick:
                sensor=declared[record['key']];metadata=sensor_metadata[record['key']];kind=sensor['kind']
                if (record.get('index')!=index or record.get('simulation_timestamp_ns')!=str(round(stamp*1e9))
                    or record.get('sensor_actor_id')!=metadata['actor_id'] or record.get('sensor_parent_actor_id')!=ego_id
                    or record.get('sensor_blueprint')!=BLUEPRINTS[kind]):raise ValueError('Native record identity/time correlation differs')
                qualified=qualify_pose(record,sensor);analysis=record.get('mount_qualification',{})
                if analysis.get('policy')!=qualified['policy'] or analysis.get('qualified') is not True:
                    raise ValueError('Native mount qualification claim differs from original matrices')
                for field in ('translation_residual_m','rotation_residual','translation_tolerance_m','rotation_tolerance'):
                    if type(analysis.get(field)) not in (int,float) or abs(analysis[field]-qualified[field])>1e-12:
                        raise ValueError('Native mount residual claim differs from recomputed observations')
                if max(residual(rigid_matrix(analysis['relative_rh_matrix']),qualified['relative_rh_matrix']))>1e-12:
                    raise ValueError('Native mounted transform differs from original observations')
                artifact=record.get('raw_artifact',{});name=artifact.get('name')
                suffix='bgra8' if kind=='rgb' else 'f32';expected_name=f'frames/{index:04d}-{sensor["key"]}.{suffix}'
                if name!=expected_name or by_name.get(name)!=artifact or name in referenced:raise ValueError('Native frame artifact correlation differs')
                referenced.add(name);size=artifact['size_bytes']
                if kind=='rgb':
                    if record.get('width')!=640 or record.get('height')!=360 or size!=640*360*4 or record.get('encoding')!='BGRA8 postprocessed engine RGB':
                        raise ValueError('Native camera payload is not a complete accepted-profile BGRA frame')
                    preview=record.get('preview_artifact')
                    if index in (0,count-1):
                        pname=f'previews/{index:04d}-{sensor["key"]}.png'
                        if not preview or preview.get('name')!=pname or by_name.get(pname)!=preview:raise ValueError('Missing native camera preview')
                        referenced.add(pname)
                    elif preview is not None:raise ValueError('Unexpected camera preview')
                else:
                    encoding='float32_le x_y_z_intensity native_sensor_LH' if kind=='lidar' else 'float32_le velocity_azimuth_altitude_depth'
                    if (size%16 or size>(3200 if kind=='lidar' else 150)*16 or record.get('point_count')!=size//16 or record.get('encoding')!=encoding):
                        raise ValueError('Native ranging payload shape/budget differs')
            previous_frame,previous_stamp=frame,stamp
        if referenced!=set(by_name):raise ValueError('Native artifact has no exact record provenance')
        return manifest,raw


def _validate_interlock(manifest, configuration_hash, pinned_probe, expected_ids):
    interlock=manifest.get('engine_interlock',{});reservation=interlock.get('reservation',{})
    if (interlock.get('schema_version')!='carla-feedback-engine-interlock/1' or interlock.get('lock_acquired') is not True
        or interlock.get('reservation_cleared_after_confirmed_cleanup') is not True or reservation.get('schema_version')!='carla-feedback-engine-reservation/1'
        or reservation.get('external_execution_id')!=manifest['external_execution_id'] or reservation.get('configuration_hash')!=configuration_hash
        or reservation.get('native_script_sha256')!=pinned_probe.get('native_feedback_script_sha256') or type(reservation.get('owner_pid')) is not int or reservation['owner_pid']<=0):
        raise ValueError('Feedback shared engine ownership reservation is not correlated/released')

def _validate_physics(manifest, configuration_hash, pinned_probe, expected_ids):
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

def _validate_cleanup(manifest, configuration_hash, pinned_probe, expected_ids):
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


def _source_hashes():
    return {'adapter_script_sha256': sha_file(__file__),
        'native_script_sha256': sha_file(native.__file__),
        'native_support_script_sha256': sha_file(support.__file__),
        'native_feedback_script_sha256': sha_file(feedback_native.__file__),
        'native_scene_script_sha256': sha_file(sensor_scene.__file__)}


class DrivingMultisensorHandler:
    def __init__(self, *, database=None):
        self.target = os.environ.get('AXIOM_CARLA_DRIVING_SSH', os.environ.get('AXIOM_CARLA_CAPTURE_SSH', ''))
        self.python = os.environ.get('AXIOM_CARLA_DRIVING_PYTHON', os.environ.get('AXIOM_CARLA_CAPTURE_PYTHON', ''))
        self.script = os.environ.get('AXIOM_CARLA_DRIVING_SCRIPT', '')
        self.directory = os.environ.get('AXIOM_CARLA_DRIVING_DIR', '')
        self.engine_spool = os.environ.get('AXIOM_CARLA_DRIVING_ENGINE_SPOOL', '')

    def metadata(self):
        return ExperimentHandlerMetadata(id=ADAPTER, version=VERSION, kinds=['sensor_capture'],
            status='available' if all((self.target, self.python, self.script, self.directory, self.engine_spool)) else 'not_configured',
            fidelity=FIDELITY, resource_id='carla', cancellation_supported=False,
            description='Native reference lane driving with coherent 10 Hz RGB, LiDAR and radar. Single or sequential full-rig A/B; no learned sensor policy.')

    def _config(self):
        if self.metadata().status != 'available':
            raise ValueError('Native driving sensor transport is not configured')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+', self.target):
            raise ValueError('Invalid configured driving SSH target')
        if any(not path.startswith('/') or '\n' in path or '\0' in path
               for path in (self.python, self.script, self.directory, self.engine_spool)):
            raise ValueError('Driving paths must be absolute operator-configured paths')

    def _command(self, args):
        return ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', self.target,
                shlex.join([self.python, self.script, *args])]

    def _transport_hash(self):
        return hashlib.sha256(canonical([self.target, self.python, self.script, self.directory, self.engine_spool])).hexdigest()

    def _probe(self, spec):
        self._config()
        result = subprocess.run(self._command(['--plan-probe']), input=canonical(spec), capture_output=True, timeout=45)
        if result.returncode or len(result.stdout) > 1024 * 1024:
            raise ValueError('Native driving route/capability probe failed or exceeded its bound')
        return json.loads(result.stdout)

    def prepare(self, spec):
        if spec.kind != 'sensor_capture' or any(value is not None for value in (spec.model, spec.sensor, spec.evaluator)):
            raise ValueError('Driving sensor capture requires explicit rigs without unconnected model/evaluator metadata')
        if spec.comparison_kind == 'single':
            rigs = {'single': DrivingSensorCaptureSpec.model_validate(spec.input)}
        elif spec.comparison_kind == 'same_scene_sensor_ab':
            if set(spec.input) != {'rig_a', 'rig_b'}:
                raise ValueError('Driving A/B requires exactly rig_a and rig_b')
            rigs = {key: DrivingSensorCaptureSpec.model_validate(value) for key, value in spec.input.items()}
            fixed = ('duration_seconds', 'sample_hz', 'spawn_index', 'vehicle_blueprint', 'expected_map', 'expected_server_version', 'controller')
            if any(getattr(rigs['rig_a'], key) != getattr(rigs['rig_b'], key) for key in fixed):
                raise ValueError('Driving A/B must fix scene, native controller and duration')
            if rigs['rig_a'].sensors == rigs['rig_b'].sensors:
                raise ValueError('Driving A/B requires a sensor configuration change')
        else:
            raise ValueError('Unsupported driving sensor comparison')
        hashes = _source_hashes()
        snapshots = {}; probes = {}
        for stage, model in rigs.items():
            rig = model.model_dump(mode='json'); rig['seed'] = spec.seed
            for sensor, declared in zip(rig['sensors'], model.sensors):
                sensor['native_parameters'] = declared.native_parameters()
            validate_spec(rig)
            probe = self._probe(rig)
            if (probe.get('schema_version') != 'carla-driving-multisensor-probe/1' or probe.get('adapter_version') != VERSION
                    or any(probe.get(key) != value for key, value in hashes.items() if key != 'adapter_script_sha256')):
                raise ValueError('Native driving codec/dependencies differ from the installed adapter')
            if probe.get('active_dynamic_actor_ids') != []:
                raise ValueError('Native world has unowned actors; driving capture was not dispatched')
            if (probe.get('server_version') != rig['expected_server_version'] or probe.get('client_version') != rig['expected_server_version']
                    or probe.get('map', '').split('/')[-1] != rig['expected_map'].split('/')[-1]
                    or probe.get('ego_blueprint_id') != rig['vehicle_blueprint']
                    or not 0 <= rig['spawn_index'] < len(probe.get('spawn_points', []))):
                raise ValueError('Native driving map, build, vehicle or spawn differs from requested inputs')
            for sensor in rig['sensors']:
                attrs = probe.get('sensors', {}).get(sensor['kind'], {}).get('attributes', {})
                if any(key not in attrs or attrs[key].get('modifiable') is not True for key in sensor['native_parameters']):
                    raise ValueError('Native blueprint cannot apply this driving sensor configuration')
            _route_valid(probe.get('route'), {**rig, 'configuration_revision': rig['rig_revision']}, probe)
            points = probe['route']['points']
            length = sum(math.hypot(b['x_m'] - a['x_m'], b['y_m'] - a['y_m']) for a, b in zip(points, points[1:]))
            if length + 1e-6 < native.required_route_horizon(rig):
                raise ValueError('Available native route is too short for the driving capture horizon')
            if probes:
                previous = next(iter(probes.values()))
                fields = ('server_version', 'client_version', 'map', 'map_opendrive_sha256', 'spawn_points', 'spawn_points_sha256', 'weather', 'sensors', 'world_settings')
                if any(previous.get(key) != probe.get(key) for key in fields) or previous['route']['points'] != points:
                    raise ValueError('Native scene changed while preparing driving A/B')
            snapshots[stage] = rig; probes[stage] = probe
        return {'schema_version': 'carla-driving-multisensor-experiment/1', 'rigs': snapshots,
            'comparison_kind': spec.comparison_kind, 'native_capability_snapshots': probes,
            'operator_transport_hash': self._transport_hash(), **hashes,
            'workload': {'capture_count': len(rigs), 'frames_per_sensor': next(iter(rigs.values())).duration_seconds * 10,
                'warmup_ticks_per_capture': 10, 'native_bytes_upper_bound': len(rigs) * (MAX_BYTES + MAX_MANIFEST_BYTES),
                'resource': 'carla', 'requires_exclusive_world': True}, 'limitations': LIMITATIONS}

    def execute(self, snapshot, work_dir, should_cancel):
        self._config()
        work = Path(work_dir); work.mkdir(parents=True, exist_ok=True)
        if (snapshot.get('schema_version') != 'carla-driving-multisensor-experiment/1'
                or snapshot.get('operator_transport_hash') != self._transport_hash()
                or any(snapshot.get(key) != value for key, value in _source_hashes().items())):
            raise ValueError('Driving transport or local source changed after preview; dispatch refused')
        stages = {'single'} if snapshot.get('comparison_kind') == 'single' else {'rig_a', 'rig_b'} if snapshot.get('comparison_kind') == 'same_scene_sensor_ab' else set()
        if not stages or set(snapshot.get('rigs', {})) != stages or set(snapshot.get('native_capability_snapshots', {})) != stages:
            raise ValueError('Driving prepared stage denominator differs')
        outputs = []; results = {}; dispatches = {}
        for stage, rig in snapshot['rigs'].items():
            validate_spec(rig)
            if should_cancel():
                raise AdapterCancelled('Driving capture cancelled before native dispatch')
            probe = self._probe(rig)
            if probe != snapshot['native_capability_snapshots'][stage] or probe.get('active_dynamic_actor_ids'):
                raise ValueError('Pinned native driving route/scene changed before dispatch')
            external_id = 'driving_' + uuid.uuid4().hex
            remote_dir = self.directory.rstrip('/') + '/' + external_id
            journal = dict(schema_version='external-driving-capture-dispatch/1', external_execution_id=external_id,
                candidate=stage, configuration_hash=hashlib.sha256(canonical(rig)).hexdigest(), status='dispatching', cleanup_status='unknown')
            journal_path = work / f'{stage}-external-dispatch.json'
            def save_journal():
                journal_path.write_bytes(canonical(journal)); dispatches[stage] = dict(journal)
                (work / 'external-dispatch.json').write_bytes(canonical({'schema_version': 'external-driving-capture-dispatches/1', 'candidates': dispatches}))
            save_journal()
            process = subprocess.Popen(self._command(['--output', remote_dir, '--engine-spool', self.engine_spool]),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                stdout, stderr = process.communicate(canonical({'spec': rig, 'pinned_probe': probe}), timeout=600)
            except subprocess.TimeoutExpired:
                process.kill(); stdout, stderr = process.communicate()
                write_native_diagnostics(work, stage, stdout, stderr)
                journal['status'] = 'outcome_unknown'; save_journal()
                raise RuntimeError('Native driving capture timed out; retained external execution/cleanup needs inspection')
            outputs.extend(write_native_diagnostics(work, stage, stdout, stderr))
            if process.returncode or len(stdout) > 65536:
                journal['status'] = 'failed_or_unknown'; save_journal()
                raise RuntimeError('Native driving capture failed; original external execution and diagnostics are retained')
            journal['status'] = 'response_received_unverified'; save_journal()
            receipt = json.loads(stdout)
            if (receipt.get('status') != 'completed' or type(receipt.get('archive_size_bytes')) is not int
                    or not 0 < receipt['archive_size_bytes'] <= MAX_BYTES + MAX_MANIFEST_BYTES + 1024 * 1024):
                raise ValueError('Invalid or oversized native driving receipt')
            archive = work / f'{stage}-native-capture.zip'
            transfer = subprocess.run(['scp', '-q', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                self.target + ':' + remote_dir + '/capture.zip', str(archive)], capture_output=True, timeout=300)
            if transfer.returncode:
                raise RuntimeError('Native driving capture completed but evidence transfer failed; inspect retained external execution')
            if archive.stat().st_size != receipt['archive_size_bytes'] or sha_file(archive) != receipt.get('archive_sha256'):
                raise ValueError('Transferred driving archive differs from the native receipt')
            manifest, manifest_raw = validate_driving_bundle(archive, receipt, rig, external_id, probe)
            manifest_path = work / f'{stage}-capture-manifest.json'; manifest_path.write_bytes(manifest_raw)
            with zipfile.ZipFile(archive) as bundle:
                for entry in manifest['artifacts']:
                    if entry['name'].startswith('previews/'):
                        path = work / (stage + '-' + PurePosixPath(entry['name']).name)
                        path.write_bytes(bundle.read(entry['name']))
                        outputs.append(ExperimentOutputFile(path, path.name, 'image/png'))
            journal.update(status='completed', cleanup_status='confirmed', archive_sha256=receipt['archive_sha256']); save_journal()
            outputs.extend([ExperimentOutputFile(archive, archive.name, 'application/zip'),
                ExperimentOutputFile(manifest_path, manifest_path.name, 'application/json'),
                ExperimentOutputFile(journal_path, journal_path.name, 'application/json')])
            results[stage] = dict(external_execution_id=external_id, frame_count=manifest['frame_count'],
                sensor_count=len(manifest['sensors']), drop_count=0, native_bytes=manifest['native_bytes'],
                elapsed_wall_seconds=manifest['elapsed_wall_seconds'], configuration_hash=manifest['configuration_hash'],
                archive_sha256=receipt['archive_sha256'], sensors=manifest['sensors'], cleanup_status='confirmed',
                simulation_clock_start_s=manifest['records'][0]['timestamp_seconds'], simulation_clock_end_s=manifest['records'][-1]['timestamp_seconds'],
                distance_travelled_m=manifest['distance_travelled_m'], maximum_speed_mps=manifest['maximum_speed_mps'],
                controller=rig['controller'], route_sha256=probe['route']['sha256'], sensors_are_policy_input=False)
            if should_cancel():
                raise AdapterCancelled('Driving capture and cleanup completed; cancellation prevents publication')
        return ExperimentExecution(summary={'schema_version': 'sensor-experiment-result/1', 'captures': results,
            'comparison_kind': snapshot['comparison_kind'], 'execution_status': 'completed', 'evaluation_status': 'not_evaluated',
            'verification_verdict': 'not_evaluated', 'calibration_status': 'uncalibrated_engine_model',
            'motion_mode': 'native_reference_driving', 'limitations': snapshot['limitations']}, artifacts=outputs, fidelity=FIDELITY)


def create_driving_multisensor_handler(*, database=None):
    return DrivingMultisensorHandler(database=database)
