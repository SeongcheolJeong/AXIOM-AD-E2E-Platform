"""Native physics driving with coherent RGB, LiDAR and Radar capture.

Runs beside unchanged feedback_native.py, multisensor_native.py and sensor_scene.py.
Map/ego reference control is not a sensor-driven learned policy. No teleportation,
map reload, foreign actor destruction or silent sensor frame substitution.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import queue
import struct
import sys
import threading
import time
import zipfile

try:
    from . import multisensor_native as support
    from . import feedback_native as feedback
except ImportError:
    import multisensor_native as support
    import feedback_native as feedback

ADAPTER_ID='carla-driving-multisensor'
VERSION='carla-driving-multisensor/1'
SPEC='carla-driving-multisensor-spec/1'
MAX_BYTES=768*1024**2
MAX_MANIFEST_BYTES=8*1024**2
SENSOR_DEFAULTS={
    'rgb':{'image_size_x':'640','image_size_y':'360','fov':'90','sensor_tick':'0','enable_postprocess_effects':'true'},
    'lidar':{'channels':'16','range':'80','points_per_second':'32000','rotation_frequency':'10','upper_fov':'10','lower_fov':'-30',
        'sensor_tick':'0','noise_stddev':'0','dropoff_general_rate':'0.45','dropoff_intensity_limit':'0.8','dropoff_zero_intensity':'0.4'},
    'radar':{'horizontal_fov':'30','vertical_fov':'15','range':'80','points_per_second':'1500','sensor_tick':'0'}}
canonical=support.canonical
sha_file=support.sha_file
settings_readback=feedback.settings_readback
engine_lease=feedback.engine_lease
ego_state=feedback.ego_state
control_fields=feedback.control_fields
plan_control=feedback.plan_control


def validate_spec(spec, *, prepared=True):
    if type(spec) is not dict or spec.get('schema_version')!=SPEC:raise ValueError('Unsupported driving multisensor contract')
    control=spec.get('controller')
    if type(control) is not dict or set(control)!={'id','target_speed_mps','lookahead_m','route_length_m'} or control['id']!='native-map-lane-follow/1':
        raise ValueError('Unsupported driving reference controller')
    for key,lo,hi in [('target_speed_mps',1,4),('lookahead_m',3,12),('route_length_m',30,150)]:
        if type(control[key]) not in (int,float) or not math.isfinite(control[key]) or not lo<=control[key]<=hi:
            raise ValueError('Invalid driving controller '+key)
    if type(spec.get('duration_seconds')) is not int or not 20<=spec['duration_seconds']<=30:
        raise ValueError('Driving duration must be 20 to 30 seconds')
    if required_route_horizon(spec)>control['route_length_m']:
        raise ValueError('Declared native route cannot cover the driving capture horizon')
    sensor_spec={**spec,'schema_version':'sensor-capture/1'};sensor_spec.pop('controller')
    if not prepared:
        if 'seed' in sensor_spec:raise ValueError('Unprepared driving input cannot pin a separate seed')
        sensor_spec['seed']=0
        if type(sensor_spec.get('sensors')) is not list:raise ValueError('Invalid driving sensors')
        sensor_spec['sensors']=[]
        for sensor in spec['sensors']:
            if type(sensor) is not dict or 'native_parameters' in sensor:raise ValueError('Unprepared sensor cannot override native parameters')
            kind=sensor.get('kind');parameters=sensor.get('parameters')
            if kind not in SENSOR_DEFAULTS or type(parameters) is not dict:raise ValueError('Invalid driving sensor kind/parameters')
            sensor_spec['sensors'].append({**sensor,'native_parameters':{**SENSOR_DEFAULTS[kind],**parameters}})
    support.validate_spec(sensor_spec)
    return spec


def required_route_horizon(spec):
    return spec['duration_seconds']*spec['controller']['target_speed_mps']+spec['controller']['lookahead_m']+10.


def route_for(world,spec):
    route=feedback.route_for(world,{**spec,'configuration_revision':spec['rig_revision']})
    length=sum(math.hypot(b['x_m']-a['x_m'],b['y_m']-a['y_m']) for a,b in zip(route['points'],route['points'][1:]))
    if length+1e-6<required_route_horizon(spec):raise ValueError('Available native route is too short for the driving capture horizon')
    return route


def probe(client,spec=None):
    result=support.probe(client)
    result.update(schema_version='carla-driving-multisensor-probe/1',adapter_version=VERSION,
        native_script_sha256=sha_file(__file__),native_support_script_sha256=sha_file(support.__file__),
        native_feedback_script_sha256=sha_file(feedback.__file__),native_scene_script_sha256=sha_file(feedback.sensor_scene.__file__))
    result['world_settings']=settings_readback(client.get_world().get_settings())
    if spec is not None:
        validate_spec(spec)
        result['route']=route_for(client.get_world(),spec)
        blueprint=client.get_world().get_blueprint_library().find(spec['vehicle_blueprint'])
        if blueprint.id!=spec['vehicle_blueprint'] or not blueprint.id.startswith('vehicle.'):raise ValueError('Native driving ego blueprint differs')
        result['ego_blueprint_id']=blueprint.id
    return result


def write_capture_manifest(path,state):
    if len(canonical(state))>MAX_MANIFEST_BYTES:raise RuntimeError('Driving manifest exceeds the complete 8 MiB budget')
    feedback.write_manifest(path,state)


def capture(payload,out_dir,client=None,lease=None):
    if type(payload) is not dict or set(payload)!={'spec','pinned_probe'}:raise ValueError('Invalid frozen driving execution envelope')
    spec=validate_spec(payload['spec'])
    if lease is None:raise ValueError('Driving capture requires the shared native engine lease')
    carla,native=support.connect();client=client or native
    out=Path(out_dir);out.mkdir(parents=True,exist_ok=False)
    try:
        before=probe(client,spec)
        if before!=payload['pinned_probe']:raise ValueError('Native driving capability/scene changed after preview')
        if before['active_dynamic_actor_ids']:raise ValueError('Native world has unowned actors; driving execution refused')
        if before['server_version']!=spec['expected_server_version'] or before['client_version']!=spec['expected_server_version']:
            raise ValueError('Native driving server/client build differs from the pinned version')
        if before['map'].split('/')[-1]!=spec['expected_map'].split('/')[-1]:raise ValueError('Pinned native map is not loaded')
    except BaseException as error:
        # A preflight failure performs no world mutation and keeps its diagnostic.
        # Only release our lease once that fact is durably recorded.
        failure={'schema_version':'carla-driving-multisensor-result/1','adapter_version':VERSION,
            'external_execution_id':out.name,'configuration_hash':hashlib.sha256(canonical(spec)).hexdigest(),
            'status':'failed','error':type(error).__name__+': '+str(error)[:2000],
            'cleanup_status':'confirmed','cleanup_scope':'no_world_mutation_started','owned_actor_ids':[]}
        write_capture_manifest(out/'capture-manifest.json',failure);lease['cleanup_confirmed']=True
        raise
    world=client.get_world();original=world.get_settings();owned=[];sensor_actors=[];descriptors=[];records=[];ticks=[];total=0
    started=time.monotonic();callback_lock=threading.Lock();sealed=False;settings_apply_attempted=False;requested_settings=None
    callback={'accepting':True,'started':0,'finished':0,'active':0,'rejected_after_stop':0,'drained':False}
    state={'schema_version':'carla-driving-multisensor-result/1','adapter_version':VERSION,'external_execution_id':out.name,
        'configuration_hash':hashlib.sha256(canonical(spec)).hexdigest(),'rig_revision':spec['rig_revision'],
        'seed':spec['seed'],'probe':before,'controller':spec['controller'],'camera_is_policy_input':False,'sensors_are_policy_input':False,
        'native_coordinate_convention':'left_handed_x_forward_y_right_z_up','declared_coordinate_convention':spec['coordinate_convention'],
        'fidelity':'actual_carla_reference_driving_multisensor','calibration_status':'uncalibrated_engine_model',
        'records':records,'artifacts':descriptors,'sensors':[],'status':'running','cleanup_status':'not_started','world_settings_restored':False,'drop_count':0}
    state['engine_interlock']={'schema_version':'carla-feedback-engine-interlock/1','lock_acquired':lease is not None,
        'reservation':lease['reservation'] if lease else None,'reservation_cleared_after_confirmed_cleanup':False,
        'scope':'shared_legacy_engine_lock; unrelated clients without this interlock are not controlled'}
    def save(name,data,media='application/octet-stream'):
        nonlocal total
        total+=len(data)
        if total>MAX_BYTES:raise ValueError('Driving native evidence exceeds the bounded complete budget')
        target=out/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        descriptor={'name':name,'size_bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'media_type':media};descriptors.append(descriptor)
        return descriptor
    def check_world():
        observed={a.id for a in world.get_actors() if a.type_id.startswith(('vehicle.','walker.','sensor.'))}
        if observed!={a.id for a in owned}:raise RuntimeError('Driving world acquired unowned or lost owned actors')
        if settings_readback(world.get_settings())!=state['world_settings_during_execution']:raise RuntimeError('Native world settings changed during driving execution')
        weather=world.get_weather()
        if {key:float(getattr(weather,key)) for key in before['weather']}!=before['weather']:raise RuntimeError('Pinned native driving weather changed during execution')
        if world.get_map().name!=before['map']:raise RuntimeError('Pinned native driving map changed during execution')
    def read_sensors(snapshot,index=None):
        frame=snapshot.frame;stamp=snapshot.timestamp.elapsed_seconds;check_world();tick=[]
        ego_snapshot=snapshot.find(ego.id)
        if ego_snapshot is None:raise RuntimeError('Missing coherent native ego snapshot')
        for item,declared in zip(state['sensors'],spec['sensors']):
            try:data=item['_stream'].get(timeout=20)
            except queue.Empty as error:raise RuntimeError('Missing native driving sensor '+item['key']) from error
            if data.frame!=frame or data.timestamp!=stamp:raise RuntimeError('Driving sensor is not aligned with the response frame/time')
            raw=bytes(data.raw_data);kind=item['kind'];key=item['key']
            if kind=='rgb' and (data.width!=640 or data.height!=360 or len(raw)!=640*360*4):
                raise RuntimeError('Incomplete driving BGRA payload')
            if kind in ('lidar','radar') and (len(raw)%16 or len(raw)>(3200 if kind=='lidar' else 150)*16):
                raise RuntimeError('Incomplete or oversized driving ranging payload')
            sensor_snapshot=snapshot.find(item['actor_id']);actor=item['_actor']
            if sensor_snapshot is None or actor.parent is None or actor.parent.id!=ego.id:raise RuntimeError('Invalid owned driving sensor snapshot/parent')
            record={'index':index,'key':key,'frame':data.frame,'timestamp_seconds':data.timestamp,'simulation_timestamp_ns':str(round(data.timestamp*1e9)),
                'sensor_world_transform':support.transform(data.transform),'ego_world_transform':support.transform(ego_snapshot.get_transform()),
                'sensor_snapshot_world_transform':support.transform(sensor_snapshot.get_transform()),'sensor_api_world_transform':support.transform(actor.get_transform()),
                'snapshot_world_frame':snapshot.frame,'bracket_after_world_frame':world.get_snapshot().frame,'world_timestamp_seconds':stamp,
                'sensor_actor_id':actor.id,'sensor_parent_actor_id':ego.id,'sensor_blueprint':actor.type_id}
            record['mount_qualification']=support.qualify_pose(record,declared)
            if index is None:continue
            suffix='bgra8' if kind=='rgb' else 'f32'
            record['raw_artifact']=save(f'frames/{index:04d}-{key}.{suffix}',raw)
            if kind=='rgb':
                record.update(width=640,height=360,encoding='BGRA8 postprocessed engine RGB')
                if index in (0,spec['duration_seconds']*10-1):
                    record['preview_artifact']=save(f'previews/{index:04d}-{key}.png',support.png(640,360,raw),'image/png')
            elif kind=='lidar':record.update(encoding='float32_le x_y_z_intensity native_sensor_LH',point_count=len(raw)//16)
            else:record.update(encoding='float32_le velocity_azimuth_altitude_depth',point_count=len(raw)//16)
            tick.append(record)
        if tick and len({r['simulation_timestamp_ns'] for r in tick})!=1:raise RuntimeError('Driving sensor timestamps differ')
        return tick
    try:
        settings=world.get_settings();settings.synchronous_mode=True;settings.fixed_delta_seconds=.1
        settings.no_rendering_mode=False;settings.substepping=True;settings.max_substep_delta_time=.01;settings.max_substeps=10
        requested_settings=settings_readback(settings);settings_apply_attempted=True
        world.apply_settings(settings);state['world_settings_during_execution']=settings_readback(world.get_settings())
        if state['world_settings_during_execution']!=settings_readback(settings):raise RuntimeError('Native driving settings readback differs from requested fixed rendered physics')
        library=world.get_blueprint_library();spawn=world.get_map().get_spawn_points()[spec['spawn_index']]
        ego=world.try_spawn_actor(library.find(spec['vehicle_blueprint']),spawn)
        if ego is None:raise RuntimeError('Pinned driving spawn is occupied')
        owned.append(ego);ego.set_simulate_physics(True)
        state.update(ego_actor_id=ego.id,ego_blueprint=ego.type_id,ego_requested_spawn_transform=support.transform(spawn),ego_physics_enabled=True)
        physics=ego.get_physics_control()
        state['vehicle_physics_readback']={'mass_kg':float(physics.mass),'drag_coefficient':float(physics.drag_coefficient),
            'center_of_mass':{'x':float(physics.center_of_mass.x),'y':float(physics.center_of_mass.y),'z':float(physics.center_of_mass.z)},
            'wheels':[{'tire_friction':float(w.tire_friction),'damping_rate':float(w.damping_rate),'radius_cm':float(w.radius),
                'max_steer_angle_deg':float(w.max_steer_angle)} for w in physics.wheels],
            'status':'native_engine_readback_not_measured_real_vehicle'}
        for declared in spec['sensors']:
            kind=declared['kind'];bp=library.find(support.BLUEPRINTS[kind]);params=declared['native_parameters']
            for key,value in params.items():
                if not bp.has_attribute(key) or not bp.get_attribute(key).is_modifiable:raise ValueError('Unsupported native driving sensor attribute '+key)
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
                    raise ValueError('Driving sensor readback differs from declaration')
            item={'key':declared['key'],'kind':kind,'blueprint_id':bp.id,'actor_id':actor.id,'mount_native':support.transform(mount),
                'declared_rh_mount_matrix':support.declared_mount(declared),'requested_native_parameters':params,'native_readback':readback,
                '_actor':actor,'_stream':stream}
            if kind=='rgb':
                focal=640/(2*math.tan(math.radians(float(params['fov']))/2))
                item['pinhole_intrinsics']={'width':640,'height':360,'fx':focal,'fy':focal,'cx':320.,'cy':180.,'distortion_status':'engine_model_not_measured_calibration'}
            state['sensors'].append(item)
        # Brake while physics settles; these are explicitly warm-up frames, never
        # counted as requested controller-response evidence.
        brake=carla.VehicleControl(brake=1.)
        responses=client.apply_batch_sync([carla.command.ApplyVehicleControl(ego.id,brake)],False)
        if len(responses)!=1 or responses[0].error:raise RuntimeError('Native warmup brake was not acknowledged')
        warmup=[];state['warmup']=warmup
        for _ in range(10):
            frame=world.tick(20);snapshot=world.get_snapshot()
            if frame!=snapshot.frame:raise RuntimeError('Native driving warmup frame differs')
            if warmup and (frame!=warmup[-1]['frame']+1 or abs(snapshot.timestamp.elapsed_seconds-warmup[-1]['timestamp_seconds']-.1)>1e-5):
                raise RuntimeError('Native driving warmup clock changed')
            if any(abs(control_fields(ego.get_control())[k]-control_fields(brake)[k])>1e-6 for k in ('throttle','brake','steer')):
                raise RuntimeError('Native driving warmup brake readback differs')
            read_sensors(snapshot);warmup.append({'frame':frame,'timestamp_seconds':snapshot.timestamp.elapsed_seconds,'ego':ego_state(snapshot,ego)})
        state['ego_initial_transform']=warmup[-1]['ego']['world_transform']
        state['ego_initial_world_frame']=warmup[-1]['frame'];state['ego_initial_timestamp_seconds']=warmup[-1]['timestamp_seconds']
        state['warmup_policy']='10_native_braked_settling_frames_all_sensor_queues_drained/1'
        if math.hypot(warmup[-1]['ego']['x_m']-spawn.location.x,warmup[-1]['ego']['y_m']-spawn.location.y)>.25 or warmup[-1]['ego']['speed_mps']>.2:
            raise RuntimeError('Native driving startup is not a resting ego at the pinned spawn')
        trace={'schema_version':'carla-driving-motion-trace/1','adapter_version':VERSION,'configuration_hash':state['configuration_hash'],
            'rig_revision':spec['rig_revision'],'seed':spec['seed'],'coordinate_convention':'left_handed_x_forward_y_right_z_up',
            'controller':spec['controller'],'route':before['route'],'camera_is_policy_input':False,'sensors_are_policy_input':False,'ticks':ticks}
        state['motion']=trace
        for index in range(spec['duration_seconds']*10):
            input_snapshot=world.get_snapshot();input_state=ego_state(input_snapshot,ego);planned,requested=plan_control(input_state,before['route'],spec['controller'])
            if input_snapshot.frame!=(warmup[-1]['frame'] if not ticks else ticks[-1]['frame']):raise RuntimeError('Native world was advanced by an unrelated client before driving control')
            check_world()
            control=carla.VehicleControl(**requested);responses=client.apply_batch_sync([carla.command.ApplyVehicleControl(ego.id,control)],False)
            if len(responses)!=1 or responses[0].error:raise RuntimeError('Native driving actuator command was not acknowledged')
            frame=world.tick(20);snapshot=world.get_snapshot();stamp=snapshot.timestamp.elapsed_seconds
            if frame!=snapshot.frame or frame!=input_snapshot.frame+1 or abs(stamp-input_snapshot.timestamp.elapsed_seconds-.1)>1e-5:
                raise RuntimeError('Driving response does not follow its acknowledged input by one fixed native tick')
            observed=control_fields(ego.get_control())
            if any(abs(observed[k]-requested[k])>1e-6 for k in requested):raise RuntimeError('Native driving control readback differs from the acknowledged command')
            tick_records=read_sensors(snapshot,index);records.extend(tick_records)
            ticks.append({'index':index,'input_frame':input_snapshot.frame,'input_timestamp_seconds':input_snapshot.timestamp.elapsed_seconds,
                'input_ego':input_state,'planned_path':planned,'requested_control':requested,'acknowledged_control':control_fields(control),
                'actuator_acknowledged':True,'frame':frame,'timestamp_seconds':stamp,'simulation_timestamp_ns':str(round(stamp*1e9)),
                'ego':ego_state(snapshot,ego),'observed_control':observed,'sensor_keys':[r['key'] for r in tick_records]})
        if state['drop_count']:raise RuntimeError('Driving callback dropped native sensor frames')
        state['frame_count']=len(ticks);state['native_bytes']=total
        state['distance_travelled_m']=sum(math.hypot(t['ego']['x_m']-t['input_ego']['x_m'],t['ego']['y_m']-t['input_ego']['y_m']) for t in ticks)
        state['maximum_speed_mps']=max(t['ego']['speed_mps'] for t in ticks);state['status']='completed'
    except BaseException as error:
        state['status']='failed';state['error']=type(error).__name__+': '+str(error)[:2000]
        # Inline motion preserves partial physics evidence on the native host.
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
        write_capture_manifest(out/'capture-manifest.json',state)
        if lease is not None and not failures:lease['cleanup_confirmed']=True
    if state['cleanup_status']!='confirmed':raise RuntimeError('Native driving cleanup failed')
    return state

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--probe',action='store_true');parser.add_argument('--plan-probe',action='store_true')
    parser.add_argument('--output');parser.add_argument('--engine-spool')
    args=parser.parse_args()
    if args.probe or args.plan_probe:
        _,client=support.connect();spec=validate_spec(json.load(sys.stdin)) if args.plan_probe else None
        sys.stdout.buffer.write(canonical(probe(client,spec)));return
    if not args.output:parser.error('--output is required')
    if not args.engine_spool:parser.error('--engine-spool must identify the operator-configured shared legacy spool')
    payload=json.load(sys.stdin);validate_spec(payload.get('spec'))
    with engine_lease(args.engine_spool,Path(args.output).name,hashlib.sha256(canonical(payload['spec'])).hexdigest()) as lease:
        result=capture(payload,args.output,lease=lease)
    if not lease['quarantine_released']:raise RuntimeError('Native driving shared engine reservation was not released')
    result['engine_interlock']['reservation_cleared_after_confirmed_cleanup']=True
    out=Path(args.output);write_capture_manifest(out/'capture-manifest.json',result)
    with zipfile.ZipFile(out/'capture.zip','w',compression=zipfile.ZIP_STORED) as archive:
        for path in sorted(out.rglob('*')):
            if path.is_file() and path.name!='capture.zip':archive.write(path,str(path.relative_to(out)))
    print(json.dumps({'status':result['status'],'manifest':'capture-manifest.json','frame_count':result['frame_count'],
        'external_execution_id':result['external_execution_id'],'manifest_sha256':sha_file(out/'capture-manifest.json'),
        'archive_sha256':sha_file(out/'capture.zip'),'archive_size_bytes':(out/'capture.zip').stat().st_size}))

if __name__=='__main__':main()
