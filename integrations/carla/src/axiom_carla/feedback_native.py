"""Bounded native CARLA feedback experiment; cameras observe real physics.

This script runs beside multisensor_native.py on the native host. The planner
uses map and ego ground truth; its RGB output is not a learned policy input.
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
import zipfile
import fcntl
import tempfile
from contextlib import contextmanager

try:
    from . import multisensor_native as support
    from . import sensor_scene
except ImportError:
    import multisensor_native as support
    import sensor_scene

VERSION = 'carla-feedback-sensor/1.1.0'
MAX_BYTES = 128 * 1024**2
CAMERA_DEFAULTS = {'image_size_x':'640','image_size_y':'360','fov':'90','sensor_tick':'0','enable_postprocess_effects':'true'}
SETTING_FIELDS=('synchronous_mode','fixed_delta_seconds','no_rendering_mode','substepping','max_substep_delta_time','max_substeps')
canonical = support.canonical
sha_file = support.sha_file

def settings_readback(settings):return {key:getattr(settings,key) for key in SETTING_FIELDS}

def _fsync_dir(path):
    fd=os.open(path,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)

def write_manifest(path,value):
    """Seal an owned state file before acknowledging durable cleanup."""
    path=Path(path);data=canonical(value);fd,name=tempfile.mkstemp(prefix='.feedback-state-',dir=path.parent)
    temporary=Path(name)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path);_fsync_dir(path.parent)
    finally:temporary.unlink(missing_ok=True)

@contextmanager
def engine_lease(spool,external_id,configuration_hash):
    """Coordinate the legacy worker and leave a durable marker on uncertainty."""
    spool=Path(spool)
    if not spool.is_absolute() or not spool.is_dir() or spool.is_symlink():raise ValueError('Operator engine spool must be an existing absolute directory')
    lock=spool/'engine.lock';quarantine=spool/'quarantine.json'
    if lock.is_symlink() or quarantine.is_symlink():raise ValueError('Engine ownership paths must not be symlinks')
    with lock.open('a+b') as stream:
        os.chmod(lock,0o600)
        try:fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:raise RuntimeError('Another native execution owns the shared CARLA engine lock') from error
        if quarantine.exists():raise RuntimeError('Unresolved native engine quarantine requires operator inspection; dispatch refused')
        reservation={'schema_version':'carla-feedback-engine-reservation/1','external_execution_id':external_id,
            'configuration_hash':configuration_hash,'native_script_sha256':sha_file(__file__),'owner_pid':os.getpid()}
        fd,temp_name=tempfile.mkstemp(prefix='.feedback-reservation-',dir=spool);temporary=Path(temp_name)
        try:
            with os.fdopen(fd,'wb') as output:output.write(canonical(reservation));output.flush();os.fsync(output.fileno())
            os.link(temporary,quarantine);_fsync_dir(spool)
        finally:temporary.unlink(missing_ok=True)
        lease={'reservation':reservation,'spool':str(spool),'cleanup_confirmed':False,'quarantine_released':False}
        try:yield lease
        finally:
            # Never remove a foreign/replaced reservation, even when our cleanup ran.
            if lease['cleanup_confirmed']:
                if not quarantine.is_file() or quarantine.is_symlink() or json.loads(quarantine.read_bytes())!=reservation:
                    raise RuntimeError('Native feedback engine reservation changed; no foreign quarantine was removed')
                quarantine.unlink();_fsync_dir(spool);lease['quarantine_released']=True

def validate_spec(spec, *, prepared=True):
    required={'schema_version','duration_seconds','sample_hz','spawn_index','vehicle_blueprint','expected_map','expected_server_version',
        'configuration_revision','coordinate_convention','controller','sensors'}
    if prepared: required.add('seed')
    if type(spec) is not dict or set(spec)!=required or spec['schema_version']!='carla-feedback-sensor-spec/1':
        raise ValueError('Unsupported complete feedback sensor contract')
    for key,lo,hi in [('duration_seconds',2,6),('sample_hz',10,10),('spawn_index',0,10000),('configuration_revision',1,2147483647)]:
        if type(spec[key]) is not int or not lo<=spec[key]<=hi:raise ValueError('Invalid feedback '+key)
    if prepared and (type(spec['seed']) is not int or not 0<=spec['seed']<=2147483647):raise ValueError('Invalid feedback seed')
    if spec['coordinate_convention']!='right_handed_x_forward_y_left_z_up':raise ValueError('Invalid declared mounting coordinates')
    if type(spec['vehicle_blueprint']) is not str or not re.fullmatch(r'vehicle\.[A-Za-z0-9_.-]{1,100}',spec['vehicle_blueprint']):raise ValueError('Invalid ego blueprint')
    for key,limit in [('expected_map',200),('expected_server_version',100)]:
        if type(spec[key]) is not str or not 1<=len(spec[key])<=limit:raise ValueError('Invalid pinned '+key)
    control=spec['controller']
    if type(control) is not dict or set(control)!={'id','target_speed_mps','lookahead_m','route_length_m'} or control['id']!='native-map-lane-follow/1':
        raise ValueError('Unsupported reference controller')
    for key,lo,hi in [('target_speed_mps',1,6),('lookahead_m',3,12),('route_length_m',30,150)]:
        if type(control[key]) not in (int,float) or not math.isfinite(control[key]) or not lo<=control[key]<=hi:raise ValueError('Invalid controller '+key)
    sensors=spec['sensors']
    if type(sensors) is not list or not 1<=len(sensors)<=2 or any(type(s) is not dict for s in sensors) or [s.get('key') for s in sensors] not in [['front'],['front','rear']]:
        raise ValueError('Feedback cameras require front or front/rear in declared order')
    for sensor in sensors:
        fields={'key','kind','translation_m','rotation_deg','rotation_order','parameters'}
        if prepared:fields.add('native_parameters')
        if type(sensor) is not dict or set(sensor)!=fields or sensor['kind']!='rgb' or sensor['rotation_order']!='pitch_yaw_roll_right_hand_rule':
            raise ValueError('Unsupported feedback RGB mounting contract')
        for key,limit in [('translation_m',10),('rotation_deg',180)]:
            value=sensor[key]
            if type(value) not in (list,tuple) or len(value)!=3 or any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>limit for v in value):
                raise ValueError('Invalid feedback camera mount')
        params=sensor['parameters']
        if type(params) is not dict or set(params)-CAMERA_DEFAULTS.keys() or any(type(v) is not str or len(v)>80 for v in params.values()):
            raise ValueError('Invalid camera parameters')
        native={**CAMERA_DEFAULTS,**params}
        if native['image_size_x']!='640' or native['image_size_y']!='360' or native['sensor_tick']!='0' or native['enable_postprocess_effects'] not in ('true','false'):
            raise ValueError('Feedback camera shape/timing differs from the accepted profile')
        try:fov=float(native['fov'])
        except ValueError:raise ValueError('Invalid camera FOV')
        if not math.isfinite(fov) or not 10<=fov<=120:raise ValueError('Invalid camera FOV')
        if prepared and sensor['native_parameters']!=native:raise ValueError('Camera native parameters differ from frozen declaration')
    return spec

def route_for(world,spec):
    points=world.get_map().get_spawn_points()
    if not 0<=spec['spawn_index']<len(points):raise ValueError('Pinned ego spawn index is not available')
    waypoint=world.get_map().get_waypoint(points[spec['spawn_index']].location)
    if waypoint is None:raise ValueError('No native driving waypoint at the pinned spawn')
    route=[]
    for _ in range(math.ceil(spec['controller']['route_length_m']/2)+1):
        location=waypoint.transform.location
        route.append({'x_m':location.x,'y_m':location.y,'z_m':location.z,'road_id':waypoint.road_id,'lane_id':waypoint.lane_id})
        successors=waypoint.next(2.)
        if not successors:break
        # Pin one deterministic native-map branch; no future ego or sensor oracle.
        waypoint=sorted(successors,key=lambda w:(w.road_id!=waypoint.road_id,w.lane_id!=waypoint.lane_id,w.road_id,w.lane_id,w.s))[0]
    if len(route)<16:raise ValueError('Native lane route is too short for the accepted bounded test')
    document={'schema_version':'carla-native-map-route/1','coordinate_convention':'left_handed_x_forward_y_right_z_up',
        'map_opendrive_sha256':hashlib.sha256(world.get_map().to_opendrive().encode()).hexdigest(),
        'spawn_index':spec['spawn_index'],'configuration_revision':spec['configuration_revision'],'points':route}
    document['sha256']=hashlib.sha256(canonical(document)).hexdigest()
    return document

def probe(client,spec=None):
    result=support.probe(client)
    result.update(schema_version='carla-feedback-native-probe/1',adapter_version=VERSION,
        native_script_sha256=sha_file(__file__),native_support_script_sha256=sha_file(support.__file__),
        native_scene_script_sha256=sha_file(sensor_scene.__file__))
    result['world_settings']=settings_readback(client.get_world().get_settings())
    if spec is not None:
        result['route']=route_for(client.get_world(),spec)
        blueprint=client.get_world().get_blueprint_library().find(spec['vehicle_blueprint'])
        if not blueprint.id.startswith('vehicle.'):raise ValueError('Native ego blueprint is unsupported')
        result['ego_blueprint_id']=blueprint.id
        geometry,raw=sensor_scene.sampled_map(client.get_world().get_map(),result['route'],result['map_opendrive_sha256'])
        result['scene_map_summary']={'schema_version':'sensor-scene-map-probe/1','sample_count':geometry['sample_count'],
            'segment_count':geometry['segment_count'],'map_size_bytes':len(raw),'map_geometry_sha256':hashlib.sha256(canonical(geometry)).hexdigest()}
    return result

def ego_state(snapshot,actor):
    item=snapshot.find(actor.id)
    if item is None:raise RuntimeError('Missing native ego snapshot')
    pose=item.get_transform();velocity=item.get_velocity()
    return {'x_m':pose.location.x,'y_m':pose.location.y,'z_m':pose.location.z,'yaw_rad':math.radians(pose.rotation.yaw),
        'speed_mps':math.sqrt(velocity.x**2+velocity.y**2+velocity.z**2),'velocity_mps':[velocity.x,velocity.y,velocity.z],
        'world_transform':support.transform(pose)}

def control_fields(control):return {key:float(getattr(control,key)) for key in ('throttle','brake','steer')}

def plan_control(state,route,config):
    points=route['points'];nearest=min(range(len(points)),key=lambda i:(points[i]['x_m']-state['x_m'])**2+(points[i]['y_m']-state['y_m'])**2)
    target=next((i for i in range(nearest,len(points)) if math.hypot(points[i]['x_m']-state['x_m'],points[i]['y_m']-state['y_m'])>=config['lookahead_m']),len(points)-1)
    if target>=len(points)-1:raise RuntimeError('Native ego reached the bounded route end; no unsupported extrapolation')
    point=points[target];dx=point['x_m']-state['x_m'];dy=point['y_m']-state['y_m'];distance=max(.1,math.hypot(dx,dy))
    heading=math.atan2(dy,dx)-state['yaw_rad'];heading=math.atan2(math.sin(heading),math.cos(heading))
    steer=max(-.7,min(.7,math.atan2(2*2.8*math.sin(heading),distance)/math.radians(35)))
    error=config['target_speed_mps']-state['speed_mps']
    requested={'throttle':max(0.,min(.65,.18+.22*error)) if error>0 else 0.,'brake':max(0.,min(.6,-.22*error)) if error<0 else 0.,'steer':steer}
    plan=[{k:p[k] for k in ('x_m','y_m','z_m')} for p in points[nearest:min(len(points),target+10)]]
    return plan,requested

def capture(payload,out_dir,client=None,lease=None):
    if type(payload) is not dict or set(payload)!={'spec','pinned_probe'}:raise ValueError('Invalid frozen feedback execution envelope')
    spec=validate_spec(payload['spec']);carla,native=support.connect();client=client or native
    out=Path(out_dir);out.mkdir(parents=True,exist_ok=False);before=probe(client,spec)
    if before!=payload['pinned_probe']:raise ValueError('Native feedback capability/scene changed after preview')
    if before['active_dynamic_actor_ids']:raise ValueError('Native world has unowned actors; feedback execution refused')
    if before['server_version']!=spec['expected_server_version'] or before['client_version']!=spec['expected_server_version']:
        raise ValueError('Native feedback server/client build differs from the pinned version')
    if before['map'].split('/')[-1]!=spec['expected_map'].split('/')[-1]:raise ValueError('Pinned native map is not loaded')
    world=client.get_world();original=world.get_settings();owned=[];sensor_actors=[];descriptors=[];records=[];ticks=[];total=0
    started=time.monotonic();callback_lock=threading.Lock();sealed=False;settings_apply_attempted=False;requested_settings=None
    callback={'accepting':True,'started':0,'finished':0,'active':0,'rejected_after_stop':0,'drained':False}
    state={'schema_version':'carla-feedback-native-result/1','adapter_version':VERSION,'external_execution_id':out.name,
        'configuration_hash':hashlib.sha256(canonical(spec)).hexdigest(),'configuration_revision':spec['configuration_revision'],
        'seed':spec['seed'],'probe':before,'controller':spec['controller'],'camera_is_policy_input':False,
        'native_coordinate_convention':'left_handed_x_forward_y_right_z_up','declared_coordinate_convention':spec['coordinate_convention'],
        'fidelity':'actual_carla_reference_feedback_rgb','calibration_status':'uncalibrated_engine_model',
        'records':records,'artifacts':descriptors,'sensors':[],'status':'running','cleanup_status':'not_started','world_settings_restored':False,'drop_count':0}
    state['engine_interlock']={'schema_version':'carla-feedback-engine-interlock/1','lock_acquired':lease is not None,
        'reservation':lease['reservation'] if lease else None,'reservation_cleared_after_confirmed_cleanup':False,
        'scope':'shared_legacy_engine_lock; unrelated clients without this interlock are not controlled'}
    def save(name,data,media='application/octet-stream'):
        nonlocal total
        total+=len(data)
        if total>MAX_BYTES:raise ValueError('Feedback native evidence exceeds the bounded complete budget')
        target=out/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        descriptor={'name':name,'size_bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'media_type':media};descriptors.append(descriptor)
        return descriptor
    def check_world():
        observed={a.id for a in world.get_actors() if a.type_id.startswith(('vehicle.','walker.','sensor.'))}
        if observed!={a.id for a in owned}:raise RuntimeError('Feedback world acquired unowned or lost owned actors')
        if settings_readback(world.get_settings())!=state['world_settings_during_execution']:raise RuntimeError('Native world settings changed during feedback execution')
        weather=world.get_weather()
        if {key:float(getattr(weather,key)) for key in before['weather']}!=before['weather']:raise RuntimeError('Pinned native feedback weather changed during execution')
        if world.get_map().name!=before['map']:raise RuntimeError('Pinned native feedback map changed during execution')
    def read_cameras(snapshot,index=None):
        frame=snapshot.frame;stamp=snapshot.timestamp.elapsed_seconds;check_world();tick=[]
        for item,declared in zip(state['sensors'],spec['sensors']):
            try:data=item['_stream'].get(timeout=20)
            except queue.Empty as error:raise RuntimeError('Missing native feedback RGB '+item['key']) from error
            if data.frame!=frame or abs(data.timestamp-stamp)>1e-9:raise RuntimeError('Feedback RGB is not aligned with the response frame/time')
            raw=bytes(data.raw_data)
            if data.width!=640 or data.height!=360 or len(raw)!=640*360*4:raise RuntimeError('Incomplete feedback BGRA payload')
            if index is None:continue
            sensor_snapshot=snapshot.find(item['actor_id']);ego_snapshot=snapshot.find(ego.id);actor=item['_actor']
            if sensor_snapshot is None or ego_snapshot is None or actor.parent is None or actor.parent.id!=ego.id:raise RuntimeError('Invalid owned feedback RGB snapshot/parent')
            entry=save(f'frames/{index:04d}-{item["key"]}.bgra8',raw)
            record={'index':index,'key':item['key'],'frame':frame,'timestamp_seconds':stamp,'simulation_timestamp_ns':str(round(stamp*1e9)),
                'width':640,'height':360,'encoding':'BGRA8 postprocessed engine RGB','raw_artifact':entry,
                'sensor_world_transform':support.transform(data.transform),'ego_world_transform':support.transform(ego_snapshot.get_transform()),
                'sensor_snapshot_world_transform':support.transform(sensor_snapshot.get_transform()),'sensor_api_world_transform':support.transform(actor.get_transform()),
                'snapshot_world_frame':snapshot.frame,'bracket_after_world_frame':world.get_snapshot().frame,'world_timestamp_seconds':stamp,
                'sensor_actor_id':actor.id,'sensor_parent_actor_id':ego.id,'sensor_blueprint':actor.type_id}
            record['mount_qualification']=support.qualify_pose(record,declared)
            if index in (0,spec['duration_seconds']*10-1):record['preview_artifact']=save(f'previews/{index:04d}-{item["key"]}.png',support.png(640,360,raw),'image/png')
            tick.append(record)
        return tick
    try:
        map_data,map_raw=sensor_scene.sampled_map(world.get_map(),before['route'],before['map_opendrive_sha256'])
        if (hashlib.sha256(canonical(map_data)).hexdigest()!=before['scene_map_summary']['map_geometry_sha256']
            or len(map_raw)!=before['scene_map_summary']['map_size_bytes']):raise RuntimeError('Native sampled map geometry changed after preview')
        state['map_artifact']=save('map.xodr',map_raw,'application/xml')
        settings=world.get_settings();settings.synchronous_mode=True;settings.fixed_delta_seconds=.1
        settings.no_rendering_mode=False;settings.substepping=True;settings.max_substep_delta_time=.01;settings.max_substeps=10
        requested_settings=settings_readback(settings);settings_apply_attempted=True
        world.apply_settings(settings);state['world_settings_during_execution']=settings_readback(world.get_settings())
        if state['world_settings_during_execution']!=settings_readback(settings):raise RuntimeError('Native feedback settings readback differs from requested fixed rendered physics')
        library=world.get_blueprint_library();spawn=world.get_map().get_spawn_points()[spec['spawn_index']]
        ego=world.try_spawn_actor(library.find(spec['vehicle_blueprint']),spawn)
        if ego is None:raise RuntimeError('Pinned feedback spawn is occupied')
        owned.append(ego);ego.set_simulate_physics(True)
        state.update(ego_actor_id=ego.id,ego_blueprint=ego.type_id,ego_requested_spawn_transform=support.transform(spawn),ego_physics_enabled=True)
        bbox=ego.bounding_box
        state['ego_bounding_box']=sensor_scene.bounding_box(support.transform(carla.Transform(bbox.location,bbox.rotation)),
            [float(bbox.extent.x),float(bbox.extent.y),float(bbox.extent.z)])
        physics=ego.get_physics_control()
        state['vehicle_physics_readback']={'mass_kg':float(physics.mass),'drag_coefficient':float(physics.drag_coefficient),
            'center_of_mass':{'x':float(physics.center_of_mass.x),'y':float(physics.center_of_mass.y),'z':float(physics.center_of_mass.z)},
            'wheels':[{'tire_friction':float(w.tire_friction),'damping_rate':float(w.damping_rate),'radius_cm':float(w.radius),
                'max_steer_angle_deg':float(w.max_steer_angle)} for w in physics.wheels],
            'status':'native_engine_readback_not_measured_real_vehicle'}
        for declared in spec['sensors']:
            bp=library.find('sensor.camera.rgb');params=declared['native_parameters']
            for key,value in params.items():
                if not bp.has_attribute(key) or not bp.get_attribute(key).is_modifiable:raise ValueError('Unsupported native feedback RGB attribute '+key)
                bp.set_attribute(key,value)
            xyz=declared['translation_m'];rotation=declared['rotation_deg']
            mount=carla.Transform(carla.Location(x=xyz[0],y=-xyz[1],z=xyz[2]),carla.Rotation(pitch=-rotation[0],yaw=-rotation[1],roll=rotation[2]))
            actor=world.spawn_actor(bp,mount,attach_to=ego);owned.append(actor);sensor_actors.append(actor);stream=queue.Queue(maxsize=4)
            def receive(data,q=stream):
                with callback_lock:
                    if not callback['accepting']:
                        if not sealed:callback['rejected_after_stop']+=1
                        return
                    callback['started']+=1;callback['active']+=1
                    try:q.put_nowait(data)
                    except queue.Full:state['drop_count']+=1
                    finally:callback['finished']+=1;callback['active']-=1
            actor.listen(receive);readback={key:actor.attributes.get(key) for key in params}
            for key,value in params.items():
                if readback[key]!=value and (key=='enable_postprocess_effects' or struct.pack('<f',float(readback[key]))!=struct.pack('<f',float(value))):
                    raise ValueError('Feedback camera readback differs from declaration')
            focal=640/(2*math.tan(math.radians(float(params['fov']))/2))
            state['sensors'].append({'key':declared['key'],'kind':'rgb','blueprint_id':bp.id,'actor_id':actor.id,'mount_native':support.transform(mount),
                'declared_rh_mount_matrix':support.declared_mount(declared),'requested_native_parameters':params,'native_readback':readback,
                'pinhole_intrinsics':{'width':640,'height':360,'fx':focal,'fy':focal,'cx':320.,'cy':180.,'distortion_status':'engine_model_not_measured_calibration'},'_actor':actor,'_stream':stream})
        # Brake while physics settles; these are explicitly warm-up frames, never
        # counted as requested controller-response evidence.
        ego.apply_control(carla.VehicleControl(brake=1.))
        warmup=[]
        for _ in range(10):
            frame=world.tick(20);snapshot=world.get_snapshot()
            if frame!=snapshot.frame:raise RuntimeError('Native feedback warm-up frame differs')
            read_cameras(snapshot);warmup.append({'frame':frame,'timestamp_seconds':snapshot.timestamp.elapsed_seconds,'ego':ego_state(snapshot,ego)})
        state['warmup']=warmup;state['ego_initial_transform']=warmup[-1]['ego']['world_transform']
        if math.hypot(warmup[-1]['ego']['x_m']-spawn.location.x,warmup[-1]['ego']['y_m']-spawn.location.y)>.25 or warmup[-1]['ego']['speed_mps']>.2:
            raise RuntimeError('Native feedback startup is not a resting ego at the pinned spawn')
        trace={'schema_version':'carla-feedback-trace/1','adapter_version':VERSION,'configuration_hash':state['configuration_hash'],
            'configuration_revision':spec['configuration_revision'],'seed':spec['seed'],'coordinate_convention':'left_handed_x_forward_y_right_z_up',
            'controller':spec['controller'],'route':before['route'],'camera_is_policy_input':False,'ticks':ticks}
        for index in range(spec['duration_seconds']*10):
            input_snapshot=world.get_snapshot();input_state=ego_state(input_snapshot,ego);planned,requested=plan_control(input_state,before['route'],spec['controller'])
            if input_snapshot.frame!=(warmup[-1]['frame'] if not ticks else ticks[-1]['frame']):raise RuntimeError('Native world was advanced by an unrelated client before feedback control')
            check_world()
            control=carla.VehicleControl(**requested);responses=client.apply_batch_sync([carla.command.ApplyVehicleControl(ego.id,control)],False)
            if len(responses)!=1 or responses[0].error:raise RuntimeError('Native feedback actuator command was not acknowledged')
            frame=world.tick(20);snapshot=world.get_snapshot();stamp=snapshot.timestamp.elapsed_seconds
            if frame!=snapshot.frame or frame!=input_snapshot.frame+1 or abs(stamp-input_snapshot.timestamp.elapsed_seconds-.1)>1e-5:
                raise RuntimeError('Feedback response does not follow its acknowledged input by one fixed native tick')
            observed=control_fields(ego.get_control())
            if any(abs(observed[k]-requested[k])>1e-6 for k in requested):raise RuntimeError('Native feedback control readback differs from the acknowledged command')
            tick_records=read_cameras(snapshot,index);records.extend(tick_records)
            ticks.append({'index':index,'input_frame':input_snapshot.frame,'input_timestamp_seconds':input_snapshot.timestamp.elapsed_seconds,
                'input_ego':input_state,'planned_path':planned,'requested_control':requested,'acknowledged_control':control_fields(control),
                'actuator_acknowledged':True,'frame':frame,'timestamp_seconds':stamp,'simulation_timestamp_ns':str(round(stamp*1e9)),
                'ego':ego_state(snapshot,ego),'observed_control':observed,'rgb_keys':[r['key'] for r in tick_records]})
        if state['drop_count']:raise RuntimeError('Feedback callback dropped native camera frames')
        trace_raw=canonical(trace)
        state['trace_artifact']=save('feedback-trace.json',trace_raw,'application/json')
        scene=sensor_scene.build_scene(state,trace,trace_raw,map_data,state['ego_bounding_box'])
        sensor_scene.validate_scene(scene,map_raw,state,trace,trace_raw)
        state['scene_artifact']=save('sensor-scene.json',canonical(scene),'application/json')
        state['frame_count']=len(ticks);state['native_bytes']=total
        state['distance_travelled_m']=sum(math.hypot(t['ego']['x_m']-t['input_ego']['x_m'],t['ego']['y_m']-t['input_ego']['y_m']) for t in ticks)
        state['maximum_speed_mps']=max(t['ego']['speed_mps'] for t in ticks);state['status']='completed'
    except BaseException as error:
        state['status']='failed';state['error']=type(error).__name__+': '+str(error)[:2000]
        # Preserve partial physics evidence on the native host for investigation.
        if ticks and not state.get('trace_artifact'):
            state['trace_artifact']=save('feedback-trace.json',canonical(trace),'application/json')
        raise
    finally:
        failures=[];state['owned_actor_ids']=[a.id for a in owned];state['actor_destroy_acknowledgements']=[]
        with callback_lock:callback['accepting']=False
        for actor in sensor_actors:
            try:actor.stop()
            except Exception:failures.append('sensor_stop')
        for actor in reversed(owned):
            try:
                ack=bool(actor.destroy());state['actor_destroy_acknowledgements'].append({'actor_id':actor.id,'acknowledged':ack})
                if not ack:failures.append('actor_destroy')
            except Exception:failures.append('actor_destroy')
        # Actor.destroy() acknowledges the server command, while get_actors()
        # can still expose the preceding client snapshot. Observe one fresh
        # frame under our shared engine lease, without deleting unknown actors
        # or counting that cleanup frame as controller/RGB evidence.
        observation={'schema_version':'carla-feedback-cleanup-observation/1','source':'bounded_owned_sync_cleanup_fence/1',
            'status':'not_observed','callbacks_accepting':False,'owned_actor_ids':state['owned_actor_ids'],
            'controlled_cleanup_tick_count':0}
        state['cleanup_observation']=observation
        try:
            cached={a.id for a in world.get_actors() if a.type_id.startswith(('vehicle.','walker.','sensor.'))}
            owned_ids=set(state['owned_actor_ids']);foreign=cached-owned_ids
            observation['cached_dynamic_actor_ids']=sorted(cached);observation['foreign_actor_ids_before_fence']=sorted(foreign)
            destroyed=state['actor_destroy_acknowledgements']
            if foreign:
                observation['status']='blocked_foreign_actors';failures.append('foreign_dynamic_actors_before_cleanup_fence')
            elif len(destroyed)!=len(owned_ids) or any(a['acknowledged'] is not True for a in destroyed):
                observation['status']='blocked_unacknowledged_destroy';failures.append('unacknowledged_cleanup_fence')
            elif lease is None:
                observation['status']='blocked_missing_engine_lease';failures.append('cleanup_fence_requires_shared_engine_lease')
            elif not settings_apply_attempted or settings_readback(world.get_settings())!=requested_settings:
                observation['status']='blocked_settings_ownership';failures.append('cleanup_fence_settings_ownership_lost')
            else:
                before_cleanup=world.get_snapshot()
                observation.update(input_frame=before_cleanup.frame,input_timestamp_seconds=before_cleanup.timestamp.elapsed_seconds)
                cleanup_frame=world.tick(20);observation['controlled_cleanup_tick_count']=1
                after_cleanup=world.get_snapshot()
                observation.update(tick_acknowledged_frame=cleanup_frame,frame=after_cleanup.frame,
                    timestamp_seconds=after_cleanup.timestamp.elapsed_seconds,
                    simulation_timestamp_ns=str(round(after_cleanup.timestamp.elapsed_seconds*1e9)))
                if (after_cleanup.frame!=cleanup_frame or cleanup_frame!=before_cleanup.frame+1
                    or abs(after_cleanup.timestamp.elapsed_seconds-before_cleanup.timestamp.elapsed_seconds-.1)>1e-5):
                    raise RuntimeError('Cleanup fence does not produce one coherent fixed native frame')
                if settings_readback(world.get_settings())!=requested_settings:raise RuntimeError('Native settings ownership changed during cleanup fence')
                remaining=sorted(a.id for a in world.get_actors() if a.type_id.startswith(('vehicle.','walker.','sensor.')))
                absent=sorted(actor_id for actor_id in owned_ids if after_cleanup.find(actor_id) is None)
                observation.update(observed_dynamic_actor_ids=remaining,owned_snapshot_absent_actor_ids=absent)
                if remaining or absent!=sorted(owned_ids):raise RuntimeError('Fresh cleanup snapshot still contains dynamic/owned actors')
                observation['status']='confirmed'
        except Exception as error:
            observation['status']='failed';observation['error']=type(error).__name__+': '+str(error)[:500]
            failures.append('fresh_cleanup_actor_observation')
        try:
            current=settings_readback(world.get_settings());original_settings=settings_readback(original)
            if current==original_settings:
                pass
            elif settings_apply_attempted and current==requested_settings:
                world.apply_settings(original)
            else:
                # An unrelated client may own the current settings. Do not
                # overwrite them merely to report that our cleanup succeeded.
                failures.append('world_settings_ownership_lost')
            now=world.get_settings()
            state['world_settings_after_cleanup']=settings_readback(now)
            state['world_settings_restored']=settings_readback(now)==settings_readback(original)
            if not state['world_settings_restored']:failures.append('world_settings_readback')
        except Exception:failures.append('world_settings_restore')
        state['elapsed_wall_seconds']=time.monotonic()-started
        with callback_lock:callback['drained']=callback['active']==0 and callback['started']==callback['finished'];state['callback_drain']=dict(callback);sealed=True
        if not callback['drained']:failures.append('callback_drain')
        state['cleanup_failures']=failures;state['cleanup_status']='confirmed' if not failures else 'failed'
        for item in state['sensors']:item.pop('_actor',None);item.pop('_stream',None)
        if failures:state['status']='failed'
        write_manifest(out/'feedback-capture-manifest.json',state)
        if lease is not None and not failures:lease['cleanup_confirmed']=True
    if state['cleanup_status']!='confirmed':raise RuntimeError('Native feedback cleanup failed')
    return state

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--probe',action='store_true');parser.add_argument('--plan-probe',action='store_true');parser.add_argument('--output');parser.add_argument('--engine-spool')
    args=parser.parse_args()
    if args.probe or args.plan_probe:
        _,client=support.connect();spec=validate_spec(json.load(sys.stdin)) if args.plan_probe else None
        sys.stdout.buffer.write(canonical(probe(client,spec)));return
    if not args.output:parser.error('--output is required')
    if not args.engine_spool:parser.error('--engine-spool must identify the operator-configured shared legacy spool')
    payload=json.load(sys.stdin);validate_spec(payload.get('spec'))
    with engine_lease(args.engine_spool,Path(args.output).name,hashlib.sha256(canonical(payload['spec'])).hexdigest()) as lease:
        result=capture(payload,args.output,lease=lease)
    if not lease['quarantine_released']:raise RuntimeError('Native feedback shared engine reservation was not released')
    result['engine_interlock']['reservation_cleared_after_confirmed_cleanup']=True
    out=Path(args.output);write_manifest(out/'feedback-capture-manifest.json',result)
    with zipfile.ZipFile(out/'capture.zip','w',compression=zipfile.ZIP_STORED) as archive:
        for path in sorted(out.rglob('*')):
            if path.is_file() and path.name!='capture.zip':archive.write(path,str(path.relative_to(out)))
    print(json.dumps({'status':result['status'],'manifest':'feedback-capture-manifest.json','frame_count':result['frame_count'],
        'external_execution_id':result['external_execution_id'],'manifest_sha256':sha_file(out/'feedback-capture-manifest.json'),
        'archive_sha256':sha_file(out/'capture.zip'),'archive_size_bytes':(out/'capture.zip').stat().st_size}))

if __name__=='__main__':main()
