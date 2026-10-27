"""Standalone native capture codec. Stream frames to disk, never inline in JSON.

Can run on the CARLA Python host without installing the platform. Only actors
created by this process are destroyed. An occupied world is refused; no reload.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import struct
import sys
import threading
import time
import zlib
import zipfile

VERSION = 'carla-streaming-capture/1'
BLUEPRINTS = {'rgb':'sensor.camera.rgb','lidar':'sensor.lidar.ray_cast','radar':'sensor.other.radar'}
MAX_BYTES = 768 * 1024**2
POSE_POLICY='carla-streaming-rigid-readback/1'
ROTATION_TOLERANCE=2e-5

def validate_spec(spec):
    required={'schema_version','duration_seconds','sample_hz','spawn_index','vehicle_blueprint','expected_map','expected_server_version',
              'rig_revision','coordinate_convention','sensors','seed'}
    if not isinstance(spec,dict) or set(spec)!=required or spec['schema_version']!='sensor-capture/1':
        raise ValueError('Unsupported complete capture contract')
    if (type(spec['duration_seconds']) is not int or not 10<=spec['duration_seconds']<=30 or spec['sample_hz']!=10
        or type(spec['sample_hz']) is not int or type(spec['seed']) is not int or not 0<=spec['seed']<=2147483647
        or type(spec['spawn_index']) is not int or not 0<=spec['spawn_index']<=10000
        or type(spec['rig_revision']) is not int or spec['rig_revision']<1
        or spec['coordinate_convention']!='right_handed_x_forward_y_left_z_up'):
        raise ValueError('Capture timing, seed, revision or coordinates exceed accepted profile')
    if (not isinstance(spec['vehicle_blueprint'],str) or not re.fullmatch(r'vehicle\.[A-Za-z0-9_.-]{1,100}',spec['vehicle_blueprint'])
        or any(not isinstance(spec[k],str) or not 1<=len(spec[k])<=limit for k,limit in [('expected_map',200),('expected_server_version',100)])):
        raise ValueError('Invalid pinned vehicle/map/build')
    sensors=spec['sensors']
    if not isinstance(sensors,list) or not 1<=len(sensors)<=4:raise ValueError('Invalid capture rig')
    defaults={'rgb':{'image_size_x':'640','image_size_y':'360','fov':'90','sensor_tick':'0','enable_postprocess_effects':'true'},
       'lidar':{'channels':'16','range':'80','points_per_second':'32000','rotation_frequency':'10','upper_fov':'10','lower_fov':'-30',
                'sensor_tick':'0','noise_stddev':'0','dropoff_general_rate':'0.45','dropoff_intensity_limit':'0.8','dropoff_zero_intensity':'0.4'},
       'radar':{'horizontal_fov':'30','vertical_fov':'15','range':'80','points_per_second':'1500','sensor_tick':'0'}}
    keys=set();counts={'rgb':0,'lidar':0,'radar':0}
    for sensor in sensors:
        if (not isinstance(sensor,dict) or set(sensor)!={'key','kind','translation_m','rotation_deg','rotation_order','parameters','native_parameters'}
            or sensor['kind'] not in defaults or not isinstance(sensor['key'],str)
            or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,31}',sensor['key']) or sensor['key'] in keys
            or sensor['rotation_order']!='pitch_yaw_roll_right_hand_rule'):
            raise ValueError('Unsupported sensor/key/rotation contract')
        keys.add(sensor['key']);counts[sensor['kind']]+=1
        for field,limit in [('translation_m',10),('rotation_deg',180)]:
            vector=sensor[field]
            if not isinstance(vector,(list,tuple)) or len(vector)!=3 or any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>limit for v in vector):
                raise ValueError('Invalid mount')
        parameters=sensor['parameters'];native=sensor['native_parameters'];kind=sensor['kind']
        if (not isinstance(parameters,dict) or set(parameters)-defaults[kind].keys() or any(type(v) is not str or len(v)>80 for v in parameters.values())
            or native!={**defaults[kind],**parameters}):raise ValueError('Native parameters differ from the pinned declared configuration')
        values={k:float(v) for k,v in native.items() if k!='enable_postprocess_effects'}
        if any(not math.isfinite(v) for v in values.values()) or values['sensor_tick']!=0:raise ValueError('Native tick/parameters must be finite and aligned at 10 Hz')
        def integer(key):
            if not re.fullmatch(r'[0-9]+',native[key]):raise ValueError('Native integer parameter is invalid')
            return int(native[key])
        if kind=='rgb':
            if (integer('image_size_x')!=640 or integer('image_size_y')!=360 or not 10<=values['fov']<=120
                or native['enable_postprocess_effects'] not in {'true','false'}):raise ValueError('Unsupported camera profile')
        elif kind=='lidar':
            if (integer('channels')!=16 or not 160<=integer('points_per_second')<=32000 or values['rotation_frequency']!=10
                or not .1<=values['range']<=100 or not -89<=values['lower_fov']<values['upper_fov']<=89
                or any(not 0<=values[k]<=1 for k in ('noise_stddev','dropoff_general_rate','dropoff_intensity_limit','dropoff_zero_intensity'))
                or values['dropoff_intensity_limit']==0):raise ValueError('Unsupported LiDAR profile')
        else:
            if (not 10<=integer('points_per_second')<=1500 or not .1<=values['range']<=100
                or any(not 1<=values[k]<=90 for k in ('horizontal_fov','vertical_fov'))):raise ValueError('Unsupported radar profile')
    if counts['rgb']>2 or counts['lidar']>1 or counts['radar']>1:raise ValueError('Capture sensor count exceeds accepted profile')
    return spec

def rigid_matrix(value):
    if (not isinstance(value,list) or len(value)!=4 or any(not isinstance(row,list) or len(row)!=4 for row in value)
        or any(type(v) not in (int,float) or not math.isfinite(v) for row in value for v in row)
        or value[3]!=[0,0,0,1]):raise ValueError('Pose matrix must be finite homogeneous 4x4')
    gram=max(abs(sum(value[k][i]*value[k][j] for k in range(3))-(i==j)) for i in range(3) for j in range(3))
    a,b,c=value[:3];det=a[0]*(b[1]*c[2]-b[2]*c[1])-a[1]*(b[0]*c[2]-b[2]*c[0])+a[2]*(b[0]*c[1]-b[1]*c[0])
    if gram>ROTATION_TOLERANCE or abs(det-1)>2*ROTATION_TOLERANCE:raise ValueError('Pose matrix is not a rigid proper rotation')
    if max(abs(r[3]) for r in value[:3])>1e6:raise ValueError('Pose location exceeds accepted numeric domain')
    return value

def multiply(a,b):return [[math.fsum(a[i][k]*b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]
def inverse_rigid(a):return [[a[j][i] for j in range(3)]+[-math.fsum(a[k][i]*a[k][3] for k in range(3))] for i in range(3)]+[[0,0,0,1]]
def reflect(a):return [[a[i][j]*(1,-1,1,1)[i]*(1,-1,1,1)[j] for j in range(4)] for i in range(4)]
def residual(a,b):return max(abs(a[i][3]-b[i][3]) for i in range(3)),max(abs(a[i][j]-b[i][j]) for i in range(3) for j in range(3))

def qualify_initial_spawn(initial,requested):
    observed=rigid_matrix(initial['matrix']);expected=rigid_matrix(requested['matrix']);dt,dr=residual(observed,expected)
    scale=max(1.,*(sum(abs(row[3]) for row in value[:3]) for value in (observed,expected)))
    tolerance=2e-5+16*2**-23*scale
    if tolerance>.01 or dt>tolerance or dr>ROTATION_TOLERANCE:
        raise ValueError('First coherent ego snapshot differs from the requested native spawn')
    return {'policy':'carla-streaming-first-coherent-spawn/1','translation_residual_m':dt,'rotation_residual':dr,
            'translation_tolerance_m':tolerance,'rotation_tolerance':ROTATION_TOLERANCE,'qualified':True}

def declared_mount(sensor):
    pitch,yaw,roll=map(math.radians,sensor['rotation_deg']);cp,sp,cy,sy,cr,sr=math.cos(pitch),math.sin(pitch),math.cos(yaw),math.sin(yaw),math.cos(roll),math.sin(roll)
    rz=[[cy,-sy,0,0],[sy,cy,0,0],[0,0,1,0],[0,0,0,1]];ry=[[cp,0,sp,0],[0,1,0,0],[-sp,0,cp,0],[0,0,0,1]];rx=[[1,0,0,0],[0,cr,-sr,0],[0,sr,cr,0],[0,0,0,1]]
    result=multiply(multiply(rz,ry),rx)
    for i,v in enumerate(sensor['translation_m']):result[i][3]=v
    return result

def qualify_pose(record,sensor):
    ego=rigid_matrix(record['ego_world_transform']['matrix']);observed=rigid_matrix(record['sensor_world_transform']['matrix'])
    snapshot=rigid_matrix(record['sensor_snapshot_world_transform']['matrix']);api=rigid_matrix(record['sensor_api_world_transform']['matrix'])
    relative=reflect(multiply(inverse_rigid(ego),observed));requested=declared_mount(sensor)
    dt,dr=residual(relative,requested);snapshot_t,snapshot_r=residual(snapshot,observed);api_t,api_r=residual(api,observed)
    scale=max(1.,*(sum(abs(row[3]) for row in matrix[:3]) for matrix in (ego,observed)))
    tolerance=2e-5+16*2**-23*scale
    if (tolerance>.01 or dt>tolerance or dr>ROTATION_TOLERANCE or max(snapshot_t,api_t)>tolerance
        or max(snapshot_r,api_r)>ROTATION_TOLERANCE):raise ValueError('Native measurement/snapshot/API pose differs from the declared RH mount')
    if (record['frame']!=record['snapshot_world_frame'] or record['frame']!=record['bracket_after_world_frame']
        or record['timestamp_seconds']!=record['world_timestamp_seconds']):raise ValueError('Native measurement frame/time does not match bracketed world snapshot')
    return {'policy':POSE_POLICY,'relative_rh_matrix':relative,'translation_residual_m':dt,'rotation_residual':dr,
            'translation_tolerance_m':tolerance,'rotation_tolerance':ROTATION_TOLERANCE,'qualified':True}

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()

def sha_file(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def transform(value):
    return {'position_m':[value.location.x,value.location.y,value.location.z],
            'rotation_deg':[value.rotation.pitch,value.rotation.yaw,value.rotation.roll],
            'matrix':value.get_matrix()}

def png(width,height,raw):
    if len(raw)!=width*height*4: raise ValueError('Native BGRA size mismatch')
    rgb=bytearray(width*height*3)
    rgb[0::3]=raw[2::4];rgb[1::3]=raw[1::4];rgb[2::3]=raw[0::4]
    rows=b''.join(b'\0'+rgb[y*width*3:(y+1)*width*3] for y in range(height))
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(rows))+chunk(b'IEND',b'')

def connect(host='127.0.0.1',port=2000):
    import carla
    client=carla.Client(host,port);client.set_timeout(20)
    return carla,client

def probe(client):
    world=client.get_world(); library=world.get_blueprint_library()
    sensors={}
    for kind,identity in BLUEPRINTS.items():
        blueprint=library.find(identity)
        sensors[kind]={'blueprint_id':identity,'attributes':{attr.id:{'value':str(attr),'modifiable':attr.is_modifiable,
            'recommended_values':list(attr.recommended_values)} for attr in blueprint}}
    settings=world.get_settings()
    weather=world.get_weather()
    spawns=[transform(value) for value in world.get_map().get_spawn_points()]
    return {'schema_version':'carla-native-sensor-probe/1','adapter_version':VERSION,'native_script_sha256':sha_file(__file__),
        'server_version':client.get_server_version(),'client_version':client.get_client_version(),
        'map':world.get_map().name,'map_opendrive_sha256':hashlib.sha256(world.get_map().to_opendrive().encode()).hexdigest(),
        'spawn_points':spawns,'spawn_points_sha256':hashlib.sha256(canonical(spawns)).hexdigest(),
        'weather':{k:float(getattr(weather,k)) for k in ('cloudiness','precipitation','precipitation_deposits','wind_intensity',
            'sun_azimuth_angle','sun_altitude_angle','fog_density','fog_distance','wetness')},'sensors':sensors,
        'active_dynamic_actor_ids':[actor.id for actor in world.get_actors() if actor.type_id.startswith(('vehicle.','walker.','sensor.'))],
        'world_settings':{'synchronous_mode':settings.synchronous_mode,'fixed_delta_seconds':settings.fixed_delta_seconds},
        'calibration_status':'uncalibrated_engine_model','raw_isp_supported':False}

def capture(spec,out_dir,client=None):
    validate_spec(spec)
    carla, native=connect()
    client=client or native
    out_dir=Path(out_dir);out_dir.mkdir(parents=True,exist_ok=False)
    sensors=spec.get('sensors')
    before=probe(client)
    if before['server_version']!=spec['expected_server_version'] or before['client_version']!=spec['expected_server_version']:
        raise ValueError('Native server/client version differs from the pinned build')
    if before['map'].split('/')[-1]!=spec['expected_map'].split('/')[-1]:
        raise ValueError('Map differs from the pinned map; automatic map reload refused')
    if before['active_dynamic_actor_ids']:
        raise ValueError('World contains unowned dynamic actors; capture refused')
    world=client.get_world(); original=world.get_settings(); owned=[];sensor_actors=[];records=[];descriptors=[]
    state={'schema_version':'carla-streaming-capture-result/1','adapter_version':VERSION,'seed':spec['seed'],
        'external_execution_id':out_dir.name,
        'rig_revision':spec['rig_revision'],'configuration_hash':hashlib.sha256(canonical(spec)).hexdigest(),
        'probe':before,'native_coordinate_convention':'left_handed_x_forward_y_right_z_up',
        'declared_coordinate_convention':spec['coordinate_convention'],
        'calibration_status':'uncalibrated_engine_model','fidelity':'actual_carla_native_sensor_model',
        'status':'running','cleanup_status':'not_started','world_settings_restored':False,
        'randomness':'requested_seed_metadata; sensor noise seed is not independently verified',
        'records':records,'artifacts':descriptors,'drop_count':0}
    total=0;started=time.monotonic();callback_lock=threading.Lock()
    callback={'accepting':True,'started':0,'finished':0,'active':0,'rejected_after_stop':0,'drained':False};sealed=False
    def save(name,data,media='application/octet-stream'):
        nonlocal total
        total+=len(data)
        if total>MAX_BYTES:raise ValueError('Full capture exceeds the accepted native artifact budget')
        target=out_dir/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        entry={'name':name,'size_bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'media_type':media}
        descriptors.append(entry)
        return entry
    try:
        settings=world.get_settings();settings.synchronous_mode=True;settings.fixed_delta_seconds=0.1
        world.apply_settings(settings)
        library=world.get_blueprint_library();spawns=world.get_map().get_spawn_points()
        if not 0<=spec['spawn_index']<len(spawns):raise ValueError('Unknown spawn index')
        ego=world.try_spawn_actor(library.find(spec['vehicle_blueprint']),spawns[spec['spawn_index']])
        if ego is None:raise ValueError('Pinned ego spawn is occupied')
        owned.append(ego);ego.set_simulate_physics(False)
        state['ego_actor_id']=ego.id;state['ego_blueprint']=ego.type_id
        state['ego_requested_spawn_transform']=transform(spawns[spec['spawn_index']])
        # CARLA API caches may still return the origin immediately after spawn.
        # Preserve that observation as a diagnostic, never as the stationary baseline.
        state['ego_pre_first_tick_api_transform']=transform(ego.get_transform())
        state['ego_initial_transform']=None
        state['sensors']=[]
        for sensor in sensors:
            kind=sensor['kind'];key=sensor['key']
            if kind not in BLUEPRINTS or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,31}',key):raise ValueError('Unsupported sensor/key')
            parameters=sensor['native_parameters'];bp=library.find(BLUEPRINTS[kind])
            if kind=='rgb' and (parameters.get('image_size_x')!='640' or parameters.get('image_size_y')!='360'):
                raise ValueError('Camera dimensions exceed accepted profile')
            for k,v in parameters.items():
                if not bp.has_attribute(k) or not bp.get_attribute(k).is_modifiable:raise ValueError('Unsupported native attribute '+k)
                bp.set_attribute(k,v)
            xyz=sensor['translation_m'];rotation=sensor['rotation_deg']
            if any(not math.isfinite(v) or abs(v)>10 for v in xyz) or any(not math.isfinite(v) or abs(v)>180 for v in rotation):
                raise ValueError('Invalid mount')
            # RH (Y left) -> CARLA LH (Y right); pitch/roll/yaw axial-vector signs.
            mount=carla.Transform(carla.Location(x=xyz[0],y=-xyz[1],z=xyz[2]),
                carla.Rotation(pitch=-rotation[0],yaw=-rotation[1],roll=rotation[2]))
            actor=world.spawn_actor(bp,mount,attach_to=ego);owned.append(actor);sensor_actors.append(actor)
            stream=queue.Queue(maxsize=4)
            def receive(data,q=stream):
                with callback_lock:
                    if not callback['accepting']:
                        if not sealed:callback['rejected_after_stop']+=1
                        return
                    callback['started']+=1;callback['active']+=1
                    try:q.put_nowait(data)
                    except queue.Full: state['drop_count']+=1
                    finally:callback['finished']+=1;callback['active']-=1
            actor.listen(receive)
            native_readback={k:actor.attributes.get(k) for k in parameters}
            def same(k,v):
                if native_readback[k]==v:return True
                if k=='enable_postprocess_effects':return False
                return struct.pack('<f',float(native_readback[k]))==struct.pack('<f',float(v))
            if any(not same(k,v) for k,v in parameters.items()):
                raise ValueError('Native parameter readback differs from request')
            item={'key':key,'kind':kind,'blueprint_id':bp.id,'actor_id':actor.id,'mount_native':transform(mount),
                  'requested_native_parameters':parameters,'native_readback':native_readback,'_stream':stream,'_actor':actor}
            item['declared_rh_mount_matrix']=declared_mount(sensor)
            if kind=='rgb':
                f=640/(2*math.tan(math.radians(float(parameters['fov']))/2))
                item['pinhole_intrinsics']={'width':640,'height':360,'fx':f,'fy':f,'cx':320.0,'cy':180.0,
                    'distortion_status':'engine_model_not_measured_calibration'}
            state['sensors'].append(item)
        # Every tick is a real native simulation frame. No wall-clock/UTC inference.
        previous_frame=None;previous_time=None
        for index in range(spec['duration_seconds']*10):
            frame=world.tick(20);snapshot=world.get_snapshot();tick=[]
            observed_dynamic={actor.id for actor in world.get_actors() if actor.type_id.startswith(('vehicle.','walker.','sensor.'))}
            if observed_dynamic!={actor.id for actor in owned}:raise RuntimeError('Native world acquired unowned or lost owned dynamic actors during capture')
            stamp=snapshot.timestamp.elapsed_seconds
            if (snapshot.frame!=frame or not math.isfinite(stamp) or (previous_frame is not None and frame!=previous_frame+1)
                or (previous_time is not None and abs(stamp-previous_time-.1)>1e-5)):
                raise RuntimeError('Native world frame/clock progression differs from the pinned 10 Hz clock')
            ego_snapshot=snapshot.find(ego.id)
            if ego_snapshot is None:raise RuntimeError('Native ego snapshot is missing')
            if index==0:
                state['ego_initial_transform']=transform(ego_snapshot.get_transform())
                state['ego_initial_world_frame']=frame;state['ego_initial_timestamp_seconds']=stamp
                state['ego_initial_spawn_qualification']=qualify_initial_spawn(state['ego_initial_transform'],state['ego_requested_spawn_transform'])
            for item in state['sensors']:
                try:data=item['_stream'].get(timeout=20)
                except queue.Empty as error:raise RuntimeError('Missing native sensor frame '+item['key']) from error
                if data.frame!=frame:raise RuntimeError('Native frame mismatch: '+item['key'])
                raw=bytes(data.raw_data);kind=item['kind'];key=item['key']
                if kind=='rgb' and (data.width!=640 or data.height!=360 or len(raw)!=640*360*4):
                    raise RuntimeError('Native camera shape or complete BGRA payload differs from the accepted profile')
                if kind in ('lidar','radar') and (len(raw)%16 or len(raw)>(3200 if kind=='lidar' else 150)*16):
                    raise RuntimeError('Native ranging payload is incomplete or exceeds the per-frame budget')
                sensor_snapshot=snapshot.find(item['_actor'].id)
                if sensor_snapshot is None:raise RuntimeError('Native sensor snapshot is missing')
                actor=item['_actor']
                if actor.parent is None or actor.parent.id!=ego.id:raise RuntimeError('Native sensor parent differs from the owned ego')
                suffix='bgra8' if kind=='rgb' else 'f32'
                entry=save(f'frames/{index:04d}-{key}.{suffix}',raw)
                record={'index':index,'key':key,'frame':data.frame,'timestamp_seconds':data.timestamp,
                        'simulation_timestamp_ns':str(round(data.timestamp*1e9)),
                        'sensor_world_transform':transform(data.transform),'raw_artifact':entry,
                        'ego_world_transform':transform(ego_snapshot.get_transform()),'sensor_snapshot_world_transform':transform(sensor_snapshot.get_transform()),
                        'sensor_api_world_transform':transform(actor.get_transform()),'snapshot_world_frame':snapshot.frame,
                        'bracket_after_world_frame':world.get_snapshot().frame,'world_timestamp_seconds':stamp,
                        'sensor_actor_id':actor.id,'sensor_parent_actor_id':actor.parent.id,'sensor_blueprint':actor.type_id}
                declared=next(s for s in sensors if s['key']==key)
                record['mount_qualification']=qualify_pose(record,declared)
                stationary_t,stationary_r=residual(state['ego_initial_transform']['matrix'],record['ego_world_transform']['matrix'])
                if stationary_t>record['mount_qualification']['translation_tolerance_m'] or stationary_r>ROTATION_TOLERANCE:
                    raise RuntimeError('Native ego moved during the declared stationary capture')
                if kind=='rgb':
                    record.update(width=data.width,height=data.height,encoding='BGRA8 postprocessed engine RGB')
                    if index in (0,spec['duration_seconds']*10-1):
                        record['preview_artifact']=save(f'previews/{index:04d}-{key}.png',png(data.width,data.height,raw),'image/png')
                elif kind=='lidar':record.update(encoding='float32_le x_y_z_intensity native_sensor_LH',point_count=len(raw)//16)
                else:record.update(encoding='float32_le velocity_azimuth_altitude_depth',point_count=len(raw)//16)
                tick.append(record)
            if len({r['simulation_timestamp_ns'] for r in tick})!=1:raise RuntimeError('Sensor timestamps are not aligned')
            records.extend(tick)
            previous_frame,previous_time=frame,stamp
        if state['drop_count']:raise RuntimeError('Sensor callback queue dropped frames')
        state['status']='completed';state['frame_count']=spec['duration_seconds']*10;state['native_bytes']=total
    except BaseException as error:
        state['status']='failed';state['error']=type(error).__name__+': '+str(error)[:2000]
        raise
    finally:
        failures=[]
        state['owned_actor_ids']=[actor.id for actor in owned];state['actor_destroy_acknowledgements']=[]
        with callback_lock:callback['accepting']=False
        for actor in sensor_actors:
            try:actor.stop()
            except Exception:failures.append('sensor_stop')
        for actor in reversed(owned):
            try:
                acknowledged=bool(actor.destroy())
                state['actor_destroy_acknowledgements'].append({'actor_id':actor.id,'acknowledged':acknowledged})
                if not acknowledged:failures.append('actor_destroy')
            except Exception:failures.append('actor_destroy')
        try:
            world.apply_settings(original)
            now=world.get_settings()
            state['world_settings_after_cleanup']={'synchronous_mode':now.synchronous_mode,'fixed_delta_seconds':now.fixed_delta_seconds}
            state['world_settings_restored']=now.synchronous_mode==original.synchronous_mode and now.fixed_delta_seconds==original.fixed_delta_seconds
            if not state['world_settings_restored']:failures.append('world_settings_readback')
        except Exception:failures.append('world_settings_restore')
        state['elapsed_wall_seconds']=time.monotonic()-started
        with callback_lock:
            callback['drained']=callback['active']==0 and callback['started']==callback['finished']
            state['callback_drain']=dict(callback);sealed=True
        if not callback['drained']:failures.append('callback_drain')
        state['cleanup_status']='confirmed' if not failures else 'failed';state['cleanup_failures']=failures
        for item in state.get('sensors',[]):item.pop('_stream',None);item.pop('_actor',None)
        (out_dir/'capture-manifest.json').write_bytes(canonical(state))
        if failures:state['status']='failed';(out_dir/'capture-manifest.json').write_bytes(canonical(state))
    if state['cleanup_status']!='confirmed':raise RuntimeError('Native cleanup failed')
    return state

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--probe',action='store_true');parser.add_argument('--output')
    args=parser.parse_args()
    if args.probe:
        _,client=connect();sys.stdout.buffer.write(canonical(probe(client)));return
    if not args.output:parser.error('--output required for capture')
    spec=json.load(sys.stdin);result=capture(spec,args.output)
    out=Path(args.output)
    with zipfile.ZipFile(out/'capture.zip','w',compression=zipfile.ZIP_STORED) as archive:
        for target in sorted(out.rglob('*')):
            if target.is_file() and target.name!='capture.zip':archive.write(target,str(target.relative_to(out)))
    print(json.dumps({'status':result['status'],'manifest':'capture-manifest.json','frame_count':result['frame_count'],
        'external_execution_id':result['external_execution_id'],'manifest_sha256':sha_file(out/'capture-manifest.json'),
        'archive_sha256':sha_file(out/'capture.zip'),'archive_size_bytes':(out/'capture.zip').stat().st_size}))

if __name__=='__main__':main()
