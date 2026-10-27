"""Optional SSH transport for native streaming capture on an existing CARLA host.

Connection settings are operator configuration, never user-supplied API URLs.
Only bounded manifests are JSON. Native frame closure transfers as a file.
"""
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import time
import uuid
import zipfile

from ade2e.adapters import AdapterCancelled, AdapterOwnershipLost
from ade2e.experiment_runtime import ExperimentHandlerMetadata, ExperimentExecution, ExperimentOutputFile
from ade2e.sensor_capture_schemas import SensorCaptureSpec
from .multisensor_native import VERSION, MAX_BYTES, BLUEPRINTS, canonical, sha_file, validate_spec, qualify_pose, qualify_initial_spawn, declared_mount, reflect, rigid_matrix, residual

DIAGNOSTIC_LIMIT=1024*1024

def write_native_diagnostics(work_dir,candidate,stdout,stderr):
    metadata={};outputs=[]
    for kind,data in [('stdout',stdout),('stderr',stderr)]:
        data=data or b'';original_size=len(data);original_hash=hashlib.sha256(data).hexdigest()
        if original_size>DIAGNOSTIC_LIMIT:
            marker=f'[AXIOM diagnostic truncated: original {original_size} bytes; retaining the last bytes below]\n'.encode()
            retained=marker+data[-(DIAGNOSTIC_LIMIT-len(marker)):]
        else:retained=data
        path=Path(work_dir)/f'{candidate}-native-{kind}.log';path.write_bytes(retained)
        metadata[kind]={'original_size_bytes':original_size,'original_sha256':original_hash,'retained_size_bytes':len(retained),
                        'truncated':original_size>DIAGNOSTIC_LIMIT,'retained_sha256':hashlib.sha256(retained).hexdigest(),'file':path.name}
        outputs.append(ExperimentOutputFile(path,path.name,'text/plain'))
    path=Path(work_dir)/f'{candidate}-native-diagnostics.json'
    path.write_bytes(canonical({'schema_version':'native-process-diagnostics/1','diagnostics':metadata,'confirms_remote_cleanup':False}))
    outputs.append(ExperimentOutputFile(path,path.name,'application/json'))
    return outputs

def _same_parameter(key,observed,requested):
    if type(observed) is not str: return False
    if observed==requested:return True
    if key=='enable_postprocess_effects':return False
    import struct
    try:return struct.pack('<f',float(observed))==struct.pack('<f',float(requested))
    except (ValueError,OverflowError):return False

def validate_capture_bundle(archive,receipt,rig,external_id):
    """Recompute closure, complete denominators, native clock and mounted poses."""
    validate_spec(rig)
    if (receipt.get('status')!='completed' or receipt.get('external_execution_id')!=external_id
        or receipt.get('frame_count')!=rig['duration_seconds']*10 or receipt.get('manifest')!='capture-manifest.json'):
        raise ValueError('Native receipt does not identify the complete pinned external execution')
    with zipfile.ZipFile(archive) as bundle:
        infos=bundle.infolist();names=[i.filename for i in infos]
        if len(names)!=len(set(names)):raise ValueError('Duplicate native archive members')
        if len(infos)>1205 or any(i.compress_type!=zipfile.ZIP_STORED or i.is_dir() for i in infos):
            raise ValueError('Native archive exceeds supported file count or compression format')
        if sum(i.file_size for i in infos)>MAX_BYTES+4*1024**2:raise ValueError('Native archive exceeds the complete uncompressed budget')
        manifest_info=bundle.getinfo('capture-manifest.json')
        if manifest_info.file_size>4*1024**2:raise ValueError('Native capture manifest exceeds its bound')
        raw=bundle.read(manifest_info)
        if hashlib.sha256(raw).hexdigest()!=receipt.get('manifest_sha256'):raise ValueError('Native manifest hash differs from the source receipt')
        manifest=json.loads(raw)
        if (manifest.get('schema_version')!='carla-streaming-capture-result/1' or manifest.get('adapter_version')!=VERSION
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
        initial_qualification=qualify_initial_spawn(manifest['ego_initial_transform'],manifest['ego_requested_spawn_transform'])
        stored_qualification=manifest.get('ego_initial_spawn_qualification',{})
        if stored_qualification.get('policy')!=initial_qualification['policy'] or stored_qualification.get('qualified') is not True:
            raise ValueError('Native first coherent spawn qualification is missing')
        for field in ('translation_residual_m','rotation_residual','translation_tolerance_m','rotation_tolerance'):
            if type(stored_qualification.get(field)) not in (int,float) or abs(stored_qualification[field]-initial_qualification[field])>1e-12:
                raise ValueError('Native spawn residual claim differs from the requested/coherent transforms')
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
        if (manifest.get('ego_initial_world_frame')!=records[0]['frame'] or manifest.get('ego_initial_timestamp_seconds')!=records[0]['timestamp_seconds']
            or manifest['ego_initial_transform']!=records[0]['ego_world_transform']):
            raise ValueError('Native stationary baseline is not the first coherent recorded ego snapshot')
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
                initial=rigid_matrix(manifest['ego_initial_transform']['matrix']);current=rigid_matrix(record['ego_world_transform']['matrix'])
                stationary_t,stationary_r=residual(initial,current)
                if stationary_t>qualified['translation_tolerance_m'] or stationary_r>qualified['rotation_tolerance']:
                    raise ValueError('Native ego moved during the declared stationary capture')
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

class StreamingCaptureHandler:
    def __init__(self, *, database=None):
        self.target=os.environ.get('AXIOM_CARLA_CAPTURE_SSH','')
        self.python=os.environ.get('AXIOM_CARLA_CAPTURE_PYTHON','')
        self.script=os.environ.get('AXIOM_CARLA_CAPTURE_SCRIPT','')
        self.directory=os.environ.get('AXIOM_CARLA_CAPTURE_DIR','')

    def metadata(self):
        configured=bool(self.target and self.python and self.script and self.directory)
        return ExperimentHandlerMetadata(id='carla-streaming-capture',version=VERSION,kinds=['sensor_capture'],
            status='available' if configured else 'not_configured',fidelity='actual_carla_native_sensor_model',
            resource_id='carla',cancellation_supported=False,
            description='Native 10 Hz RGB/LiDAR/radar capture. Existing world only; streamed files; uncalibrated engine models.')

    def _config(self):
        if self.metadata().status!='available':raise ValueError('Native streaming capture transport is not configured')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+',self.target):raise ValueError('Invalid configured SSH target')
        if any(not x.startswith('/') or '\n' in x or '\0' in x for x in (self.python,self.script,self.directory)):
            raise ValueError('Capture paths must be absolute operator-configured paths')

    def _command(self,args):
        return ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',self.target,
                shlex.join([self.python,self.script,*args])]

    def _probe(self):
        self._config()
        result=subprocess.run(self._command(['--probe']),capture_output=True,timeout=35)
        if result.returncode:raise ValueError('Native capture connection/capability probe failed; inspect the configured host')
        if len(result.stdout)>1024*1024:raise ValueError('Native capability probe exceeds its bound')
        return json.loads(result.stdout)

    def prepare(self,spec):
        if spec.kind!='sensor_capture' or spec.model is not None or spec.evaluator is not None:
            raise ValueError('Sensor capture does not run a model or imply an accuracy evaluator')
        if spec.sensor is not None:raise ValueError('Use the explicit capture rig input; unbound sensor metadata is refused')
        if spec.comparison_kind=='single':
            rigs={'single':SensorCaptureSpec.model_validate(spec.input)}
        elif spec.comparison_kind=='same_scene_sensor_ab':
            if set(spec.input)!={'rig_a','rig_b'}:raise ValueError('Sensor A/B requires exactly rig_a and rig_b')
            rigs={key:SensorCaptureSpec.model_validate(value) for key,value in spec.input.items()}
            fixed=('duration_seconds','sample_hz','spawn_index','vehicle_blueprint','expected_map','expected_server_version')
            if any(getattr(rigs['rig_a'],k)!=getattr(rigs['rig_b'],k) for k in fixed):
                raise ValueError('Sensor A/B must fix the same scene, ego spawn and capture duration')
            if rigs['rig_a']==rigs['rig_b']:raise ValueError('Sensor A/B requires an explicit configuration change')
        else:raise ValueError('Unsupported sensor comparison type')
        observed=self._probe()
        if observed.get('native_script_sha256')!=sha_file(Path(__file__).with_name('multisensor_native.py')):
            raise ValueError('Remote native capture codec differs from the installed adapter')
        if observed.get('active_dynamic_actor_ids'):raise ValueError('Native world has unowned actors; no capture was dispatched')
        for rig in rigs.values():
            if observed['server_version']!=rig.expected_server_version or observed['client_version']!=rig.expected_server_version:
                raise ValueError('Native server/client build differs from the requested build')
            if observed['map'].split('/')[-1]!=rig.expected_map.split('/')[-1]:raise ValueError('Pinned map is not loaded')
            if not 0<=rig.spawn_index<len(observed.get('spawn_points',[])):
                raise ValueError('Requested ego spawn index is not available in the pinned native map')
            for sensor in rig.sensors:
                attrs=observed['sensors'][sensor.kind]['attributes']
                if any(k not in attrs or not attrs[k]['modifiable'] for k in sensor.native_parameters()):
                    raise ValueError('Native blueprint does not support this sensor configuration')
        snapshots={}
        for key,rig in rigs.items():
            document=rig.model_dump(mode='json');document['seed']=spec.seed
            for sensor,declared in zip(document['sensors'],rig.sensors):sensor['native_parameters']=declared.native_parameters()
            snapshots[key]=document
        return {'schema_version':'carla-streaming-experiment/1','rigs':snapshots,'comparison_kind':spec.comparison_kind,
            'operator_transport_hash':hashlib.sha256(canonical([self.target,self.python,self.script,self.directory])).hexdigest(),
            'native_capability_snapshot':observed,'native_script_sha256':sha_file(Path(__file__).with_name('multisensor_native.py')),
            'workload':{'capture_count':len(rigs),'frames_per_sensor':next(iter(rigs.values())).duration_seconds*10,
                'native_bytes_upper_bound':len(rigs)*MAX_BYTES,'resource':'carla','requires_exclusive_world':True},
            'limitations':['Stationary ego in a fixed existing native map; no driving controller is evaluated',
                'Requested seed does not establish independently randomized native sensor noise',
                'Native engine RGB is postprocessed RGB, not physical RAW; no ISP or measured calibration',
                'Sensor A/B fixes scene definition and ego spawn; sequential native world clock and traffic-light/renderer state may differ']}

    def execute(self,snapshot,work_dir,should_cancel):
        self._config();work_dir=Path(work_dir);work_dir.mkdir(parents=True,exist_ok=True)
        if snapshot['operator_transport_hash']!=hashlib.sha256(canonical([self.target,self.python,self.script,self.directory])).hexdigest():
            raise ValueError('Configured native host/transport changed after preview')
        results={};outputs=[];dispatches={}
        for key,rig in snapshot['rigs'].items():
            if should_cancel():raise AdapterCancelled('Capture cancelled before native dispatch')
            probe=self._probe()
            pinned=snapshot['native_capability_snapshot']
            fields=('server_version','client_version','map','map_opendrive_sha256','spawn_points','spawn_points_sha256','weather','sensors','native_script_sha256','world_settings')
            for field in fields:
                if probe.get(field)!=pinned.get(field):raise ValueError('Pinned native scene/capability changed: '+field)
            if probe.get('active_dynamic_actor_ids'):raise ValueError('Native world acquired unowned actors; dispatch refused')
            execution_id='capture_'+uuid.uuid4().hex
            remote_dir=self.directory.rstrip('/')+'/'+execution_id
            journal={'schema_version':'external-capture-dispatch/1','external_execution_id':execution_id,'candidate':key,
                'configuration_hash':hashlib.sha256(canonical(rig)).hexdigest(),'status':'dispatching','cleanup_status':'unknown'}
            journal_path=work_dir/f'{key}-external-dispatch.json'
            def save_journal():
                journal_path.write_bytes(canonical(journal));dispatches[key]=dict(journal)
                (work_dir/'external-dispatch.json').write_bytes(canonical({'schema_version':'external-capture-dispatches/1',
                    'candidates':dispatches,'cleanup_is_verified_only_per_completed_candidate':True}))
            save_journal()
            process=subprocess.Popen(self._command(['--output',remote_dir]),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                stdout,stderr=process.communicate(canonical(rig),timeout=300)
            except subprocess.TimeoutExpired:
                # Stopping SSH is not an acknowledgement that native cleanup ran.
                process.kill();stdout,stderr=process.communicate()
                outputs.extend(write_native_diagnostics(work_dir,key,stdout,stderr))
                journal['status']='outcome_unknown';save_journal()
                raise RuntimeError('Native capture timed out; remote outcome and cleanup require inspection')
            outputs.extend(write_native_diagnostics(work_dir,key,stdout,stderr))
            if process.returncode or len(stdout)>65536:
                journal['status']='failed_or_unknown';save_journal()
                raise RuntimeError('Native capture failed; its external execution ID is retained for inspection')
            journal['status']='response_received_unverified';save_journal()
            receipt=json.loads(stdout);archive=work_dir/f'{key}-native-capture.zip'
            if receipt.get('status')!='completed' or not 0<receipt.get('archive_size_bytes',0)<=MAX_BYTES+8*1024**2:
                raise ValueError('Invalid or oversized native capture receipt')
            transferred=subprocess.run(['scp','-q','-o','BatchMode=yes','-o','ConnectTimeout=10',
                self.target+':'+remote_dir+'/capture.zip',str(archive)],capture_output=True,timeout=180)
            if transferred.returncode:raise RuntimeError('Capture completed but native evidence transfer failed; inspect retained external execution')
            if archive.stat().st_size!=receipt['archive_size_bytes'] or sha_file(archive)!=receipt['archive_sha256']:
                raise ValueError('Native archive transfer hash/size differs from the receipt')
            manifest,manifest_raw=validate_capture_bundle(archive,receipt,rig,execution_id)
            for field in fields:
                if manifest['probe'].get(field)!=pinned.get(field):raise ValueError('Captured scene differs from frozen native '+field)
            with zipfile.ZipFile(archive) as bundle:
                for entry in manifest['artifacts']:
                    if entry['name'].startswith('previews/'):
                        preview=work_dir/(key+'-'+PurePosixPath(entry['name']).name);preview.write_bytes(bundle.read(entry['name']))
                        outputs.append(ExperimentOutputFile(preview,preview.name,'image/png'))
            # Preserve the original manifest bytes; the outer queue computes its own hashes.
            manifest_path=work_dir/f'{key}-capture-manifest.json';manifest_path.write_bytes(manifest_raw)
            journal.update(status='completed',cleanup_status='confirmed',archive_sha256=receipt['archive_sha256'])
            save_journal()
            outputs.extend([ExperimentOutputFile(archive,archive.name,'application/zip'),
                ExperimentOutputFile(manifest_path,manifest_path.name,'application/json'),ExperimentOutputFile(journal_path,journal_path.name,'application/json')])
            results[key]={'external_execution_id':execution_id,'frame_count':manifest['frame_count'],
                'sensor_count':len(manifest['sensors']),'drop_count':0,'native_bytes':manifest['native_bytes'],
                'elapsed_wall_seconds':manifest['elapsed_wall_seconds'],'configuration_hash':manifest['configuration_hash'],
                'archive_sha256':receipt['archive_sha256'],'sensors':manifest['sensors'],'cleanup_status':'confirmed'}
            results[key]['simulation_clock_start_s']=manifest['records'][0]['timestamp_seconds']
            results[key]['simulation_clock_end_s']=manifest['records'][-1]['timestamp_seconds']
            if should_cancel():raise AdapterCancelled('Native capture finished and cleanup confirmed; cancellation prevents publication')
        return ExperimentExecution(summary={'schema_version':'sensor-experiment-result/1','captures':results,
            'comparison_kind':snapshot['comparison_kind'],'execution_status':'completed','evaluation_status':'not_evaluated',
            'verification_verdict':'not_evaluated','calibration_status':'uncalibrated_engine_model',
            'limitations':snapshot['limitations']},artifacts=outputs,fidelity='actual_carla_native_sensor_model')

def create_capture_handler(*,database=None):
    return StreamingCaptureHandler(database=database)
