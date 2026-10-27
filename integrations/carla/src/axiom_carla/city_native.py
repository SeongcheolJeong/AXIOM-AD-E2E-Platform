"""Native CARLA city executor. Install only beside the optional CARLA API.

An operator-owned dedicated endpoint is mandatory. The API never accepts a
host, executable, shell command or arbitrary output directory from scenarios.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import time
import zipfile
import base64
import zlib
from contextlib import contextmanager
try:
    from . import city_control,city_maneuvers,reactive_planner
except ImportError:
    import city_control
    import city_maneuvers
    import reactive_planner

VERSION='carla-city-object/1.0.0'
COORDINATES='carla_world_m_x_forward_y_right_z_up'
CHUNK_BYTES=64*1024**2
MAX_BYTES=2*1024**3
CANCELLED=False


def scenario_fidelity(scenario):
    # Standalone native process has no core backend dependency. The shared
    # wire values are checked against the core classifier by adapter tests.
    modes={actor['controller']['kind'] for actor in scenario['actors']}
    if 'timed_replay' not in modes:return 'native_carla_object_physics'
    return ('native_carla_mixed_physics_and_replay' if modes & {'route_follow','traffic','autoware','reactive'}
            else 'native_carla_timed_position_replay')


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for data in iter(lambda:handle.read(1024*1024),b''):result.update(data)
    return result.hexdigest()


def write_json(path,value):
    temporary=Path(str(path)+'.tmp'); temporary.write_bytes(canonical(value)); os.replace(temporary,path)


@contextmanager
def lease(directory,identity):
    root=Path(directory);root.mkdir(parents=True,exist_ok=True)
    with (root/'engine.lock').open('a+b') as handle:
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:raise RuntimeError('Dedicated CARLA world is already owned') from error
        quarantine=root/'quarantine.json'
        if quarantine.exists():raise RuntimeError('Previous city execution has unconfirmed cleanup; inspect quarantine')
        marker={'id':identity,'pid':os.getpid(),'runner_sha256':file_sha(__file__)}
        write_json(quarantine,marker);state={'clean':False}
        try:yield state
        finally:
            if state['clean'] and json.loads(quarantine.read_bytes())==marker:quarantine.unlink()


def dynamic(world):
    return [a for a in world.get_actors() if a.type_id.startswith(('vehicle.','walker.','sensor.'))]


def pose(transform):
    p=transform.location
    yaw=math.radians(transform.rotation.yaw)
    return {'x':float(p.x),'y':float(p.y),'z':float(p.z),'heading_rad':math.atan2(math.sin(yaw),math.cos(yaw))}


def vector(v):return {'x':float(v.x),'y':float(v.y),'z':float(v.z)}


def connect(port,owner):
    record=json.loads(Path(owner).read_bytes())
    if record.get('port')!=port or port==2000 or record.get('purpose')!='AXIOM isolated city acceptance':
        raise ValueError('City experiments require the operator-owned dedicated endpoint')
    os.kill(record['pid'],0)
    import carla
    client=carla.Client('127.0.0.1',port);client.set_timeout(50)
    if client.get_server_version()!=client.get_client_version():raise ValueError('CARLA server/client build mismatch')
    return carla,client


def probe(client):
    world=client.get_world()
    return {'schema_version':'carla-city-probe/1','adapter_version':VERSION,
        'native_script_sha256':file_sha(__file__),'control_script_sha256':file_sha(city_control.__file__),
        'maneuver_script_sha256':file_sha(city_maneuvers.__file__),
        'planner_script_sha256':file_sha(reactive_planner.__file__),
        'server_version':client.get_server_version(),'client_version':client.get_client_version(),
        'available_maps':sorted(n.split('/')[-1] for n in client.get_available_maps()),
        'map':world.get_map().name,'dynamic_actor_ids':sorted(a.id for a in dynamic(world)),
        'vehicle_blueprints':sorted(a.id for a in world.get_blueprint_library().filter('vehicle.*')),
        'walker_blueprints':sorted(a.id for a in world.get_blueprint_library().filter('walker.pedestrian.*')),
        'static_blueprints':sorted(a.id for a in world.get_blueprint_library().filter('static.prop.*'))}


def map_definition(world,version):
    native=world.get_map();raw=native.to_opendrive(); waypoints=native.generate_waypoints(2.)
    # Preserve native junction endpoints as well as regular samples.
    by_id={str(w.id):w for w in waypoints}
    for ends in native.get_topology():
        for w in ends:by_id[str(w.id)]=w
    if len(by_id)>50000:raise ValueError('Map exceeds registered graph bound')
    by_lane={}
    for w in by_id.values():by_lane.setdefault((w.road_id,w.section_id,w.lane_id),[]).append(w)
    nodes=[]
    for identity,w in by_id.items():
        next_ids=[]
        for successor in w.next(2.):
            group=by_lane.get((successor.road_id,successor.section_id,successor.lane_id),[])
            candidates=[n for n in group if str(n.id)!=identity]
            if not candidates:continue
            chosen=min(candidates,key=lambda n:abs(n.s-successor.s))
            if chosen.transform.location.distance(successor.transform.location)<=3.:
                next_ids.append(str(chosen.id))
        node={'id':identity,**pose(w.transform),'road_id':w.road_id,'section_id':w.section_id,
            'lane_id':w.lane_id,'lane_width_m':float(w.lane_width),'is_junction':w.is_junction,'next_ids':sorted(set(next_ids))}
        node.update(native_lane_relations(w,by_lane))
        nodes.append(node)
    signals=[]
    for actor in world.get_actors():
        if actor.type_id.startswith(('traffic.traffic_light','traffic.stop')):
            stable_id=actor.get_opendrive_id() if 'traffic_light' in actor.type_id else 'stop-'+sha(canonical(pose(actor.get_transform())))[:16]
            signals.append({'id':stable_id,'pose':pose(actor.get_transform()),
                'kind':'traffic_light' if 'traffic_light' in actor.type_id else 'stop','lanelet_ids':[]})
    return {'resource_kind':'map','name':native.name.split('/')[-1],'engine':'carla','native_name':native.name,
        'server_version':version,'coordinate_system':COORDINATES,'opendrive':{'content':raw,'sha256':sha(raw.encode())},
        'nodes':sorted(nodes,key=lambda n:n['id']),'spawn_points':[pose(t) for t in native.get_spawn_points()],
        'signals':signals,'rights':{'license':'CARLA simulator content license; verify individual content redistribution',
            'source_url':'https://github.com/carla-simulator/carla','redistribution':'unknown',
        'notes':'Extracted from locally installed engine. Geometry metadata is native observation; engine meshes are not distributed.'}}


def native_lane_relations(waypoint,by_lane):
    """Retain observed lane permissions and junction membership, not inferred priority.

    CARLA LaneChange is a bitmask (Right=1, Left=2). Only a sampled native
    same-direction driving lane can become a legal lateral graph edge.
    Historical catalogs without these optional fields remain unchanged.
    """
    result={}
    if waypoint.is_junction:
        junction_id=waypoint.junction_id
        if junction_id>=0:result['conflict_ids']=['native-junction-'+str(junction_id)]
        return result
    heading=math.radians(waypoint.transform.rotation.yaw)
    for side,mask in (('left',2),('right',1)):
        adjacent=getattr(waypoint,'get_'+side+'_lane')()
        if adjacent is None or adjacent.is_junction or str(adjacent.lane_type)!='Driving':continue
        difference=math.radians(adjacent.transform.rotation.yaw)-heading
        if abs(math.atan2(math.sin(difference),math.cos(difference)))>.4:continue
        candidates=by_lane.get((adjacent.road_id,adjacent.section_id,adjacent.lane_id),[])
        if not candidates:continue
        chosen=min(candidates,key=lambda n:(abs(n.s-adjacent.s),str(n.id)))
        if chosen.transform.location.distance(adjacent.transform.location)>3.:continue
        result[side+'_id']=str(chosen.id)
        if int(waypoint.lane_change)&mask:result['lane_change_'+side]=True
    return result


def map_lanes(definition):
    groups={}
    for node in definition['nodes']:groups.setdefault((node['road_id'],node['section_id'],node['lane_id']),[]).append(node)
    lanes=[]
    for key,group in groups.items():
        nodes={n['id']:n for n in group};pending=set(nodes)
        incoming={i for n in group for i in n['next_ids'] if i in nodes}
        for seed in [n['id'] for n in group if n['id'] not in incoming]+[n['id'] for n in group]:
            if seed not in pending:continue
            chain=[];current=seed
            while current in pending:
                pending.remove(current);node=nodes[current];chain.append(node)
                successors=[i for i in node['next_ids'] if i in pending]
                if not successors:break
                current=successors[0]
            lanes.append({'id':f'{key[0]}:{key[1]}:{key[2]}:{seed}','road_id':key[0],'section_id':key[1],
                'lane_id':key[2],'width_m':chain[0]['lane_width_m'],'points':[{k:n[k] for k in ('x','y','z','heading_rad')} for n in chain]})
    return lanes


def catalog(client):
    world=client.get_world();assets=[]
    for bp in world.get_blueprint_library():
        kind='vehicle' if bp.id.startswith('vehicle.') else ('pedestrian' if bp.id.startswith('walker.pedestrian.') else ('static' if bp.id.startswith('static.prop.') else None))
        if kind is None:continue
        attrs={attribute.id:list(attribute.recommended_values) for attribute in bp if attribute.is_modifiable and attribute.recommended_values}
        assets.append({'resource_kind':'asset','name':bp.id,'engine':'carla','blueprint_id':bp.id,'actor_kind':kind,
            'dimensions':None,'attributes':attrs,'thumbnail_uri':None,'rights':{'license':'Installed CARLA content; asset-specific rights require review',
                'source_url':'https://github.com/carla-simulator/carla','redistribution':'unknown','notes':'Native blueprint catalog only; no model mesh or texture redistribution.'}})
    return {'map':map_definition(world,client.get_server_version()),'assets':sorted(assets,key=lambda a:a['blueprint_id']),'probe':probe(client)}


def state_of(snapshot,actor,identity):
    item=snapshot.find(actor.id)
    if item is None:raise RuntimeError('Native snapshot lost actor '+identity)
    velocity=item.get_velocity();result={'id':identity,**pose(item.get_transform()),'velocity':vector(velocity),
        'acceleration':vector(item.get_acceleration()),'speed_mps':math.sqrt(velocity.x**2+velocity.y**2+velocity.z**2),
        'yaw_rate_rps':math.radians(item.get_angular_velocity().z)}
    return result


def physics(actor):
    native=actor.get_physics_control();wheels=native.wheels
    front=sum(w.position.x for w in wheels[:2])/2/100;rear=sum(w.position.x for w in wheels[2:4])/2/100
    # Wheel positions are world coordinates in the native API: use distances,
    # and transform the axle center back into the actor frame.
    transform=actor.get_transform(); inverse=transform.get_inverse_matrix()
    center=[sum(getattr(w.position,k) for w in wheels[2:4])/2/100 for k in ('x','y','z')]
    rear_local=sum(inverse[0][i]*center[i] for i in range(3))+inverse[0][3]
    wheelbase=math.sqrt(sum((sum(getattr(w.position,k) for w in wheels[:2])/2/100-center[i])**2 for i,k in enumerate(('x','y','z'))))
    tread=math.sqrt(sum(((getattr(wheels[2].position,k)-getattr(wheels[3].position,k))/100)**2 for k in ('x','y','z')))
    bb=actor.bounding_box;front_local=rear_local+wheelbase
    return {'wheelbase_m':wheelbase,'rear_axle_offset_m':-rear_local,
        'wheel_base':wheelbase,'wheel_tread':tread,'wheel_radius':sum(w.radius for w in wheels)/len(wheels)/100,
        'front_overhang':bb.location.x+bb.extent.x-front_local,'rear_overhang':rear_local-(bb.location.x-bb.extent.x),
        'left_overhang':bb.extent.y-bb.location.y-tread/2,'right_overhang':bb.extent.y+bb.location.y-tread/2,
        'vehicle_height':2*bb.extent.z,
        'max_steer_angle_rad':math.radians(max(w.max_steer_angle for w in wheels)),
        'steering_curve':[{'native_speed_axis':float(p.x),'maximum_steer_fraction':float(p.y)} for p in native.steering_curve],
        'steering_curve_speed_unit':'not_exposed_by_python_api; native_values_retained_without_gain_inversion',
        'steering_curve_applied_by':'native_vehicle_physics; reference_command_not_empirically_gain_fitted',
        'mass_kg':native.mass,'source':'native_engine_readback_not_real_vehicle_calibration'}


def autoware_observation(snapshot,actors,metadata,ego_id,world,vehicle_physics):
    import carla
    current={identity:state_of(snapshot,actor,identity) for identity,actor in actors.items()}
    state=current[ego_id]
    def policy_pose(p):return {'x':p['x'],'y':p['y'],'z':p['z'],'yaw_rad':p['heading_rad']}
    def object_pose(other):
        center=actors[other['id']].bounding_box.location
        return {'x':other['x']+math.cos(other['heading_rad'])*center.x-math.sin(other['heading_rad'])*center.y,
                'y':other['y']+math.sin(other['heading_rad'])*center.x+math.cos(other['heading_rad'])*center.y,
                'z':other['z']+center.z,'yaw_rad':other['heading_rad']}
    return {'op':'step','frame':snapshot.frame,'sim_time_s':snapshot.timestamp.elapsed_seconds,'wait_s':.2,
        'ego':{'pose':policy_pose(state),'velocity':state['velocity'],'acceleration':state['acceleration'],
            'yaw_rate_rps':state['yaw_rate_rps'],'steering_tire_angle_rad':math.radians(actors[ego_id].get_wheel_steer_angle(carla.VehicleWheelLocation.FL_Wheel))},
        'actors':[{'id':other['id'],'kind':next(a['kind'] for a in metadata if a['id']==other['id']),
            'pose':object_pose(other),'velocity':other['velocity'],
            'dimensions':{k.removesuffix('_m'):v for k,v in next(a['dimensions_m'] for a in metadata if a['id']==other['id']).items()}}
            for other in current.values() if other['id']!=ego_id],
        'traffic_lights':[{'id':str(light.id),'opendrive_id':light.get_opendrive_id(),
            'pose':policy_pose(pose(light.get_transform())),'state':str(light.get_state()).lower()}
            for light in world.get_actors().filter('traffic.traffic_light*')]}


def camera_archive(path, *, network=False):
    """Lossless container compression for the native network capture profile.

    Raw BGRA member bytes/hashes, sampling and archive size budgets are unchanged.
    Existing capture profiles retain their original stored ZIP representation.
    """
    return zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED if network else zipfile.ZIP_STORED,
                           compresslevel=1 if network else None)


def capture(payload,out,carla,client,lease_state,args):
    if 'map_definition_pack' in payload:
        packed=payload['map_definition_pack']
        if packed.get('encoding')!='zlib+base64':raise ValueError('Unknown frozen map encoding')
        decoder=zlib.decompressobj()
        raw=decoder.decompress(base64.b64decode(packed['data'],validate=True),64*1024**2+1)
        if len(raw)>64*1024**2 or not decoder.eof or decoder.unused_data or sha(raw)!=packed['sha256']:
            raise ValueError('Frozen map archive exceeds bounds or hash differs')
        payload={**payload,'map_definition':json.loads(raw)}
    scenario=payload['scenario'];source=payload['source'];out=Path(out);out.mkdir(parents=True,exist_ok=False)
    world=client.get_world()
    if dynamic(world):raise RuntimeError('Dedicated world has foreign dynamic actors')
    map_name=payload['map']['native_name'].split('/')[-1]
    generated=payload.get('map_definition',{}).get('generated')
    external=payload.get('map_definition',{}).get('external')
    if generated or external:
        definition=payload['map_definition']
        content=definition['opendrive']['content']
        if sha(content.encode())!=payload['map']['opendrive_sha256']:
            raise ValueError('Generated map source differs from pinned build')
        parameters=carla.OpendriveGenerationParameters(vertex_distance=1., wall_height=0., additional_width=0., smooth_junctions=False)
        world=client.generate_opendrive_world(content,parameters)
    elif world.get_map().name.split('/')[-1]!=map_name:world=client.load_world(map_name)
    xodr=world.get_map().to_opendrive().encode()
    if sha(xodr)!=payload['map']['opendrive_sha256']:raise ValueError('Native OpenDRIVE differs from frozen map revision')
    dt=scenario['timestep_s'];count=round(scenario['duration_s']/dt)
    if abs(count*dt-scenario['duration_s'])>1e-6 or count>2400:raise ValueError('Scenario clock exceeds bounded complete execution')
    original=world.get_settings();owned=[];actors={};cameras=[];collisions=queue.Queue();frames=[];events=[];camera_records=[];descriptors=[];archives=[];total=0
    start_wall=time.monotonic();scene=None;controller_process=None;clean=False;hil_bridge=None
    original_weather=world.get_weather();signal_original=[];native_signals={};maneuver_started=set()
    status={'external_execution_id':out.name,'status':'running','cleanup_status':'unknown','configuration_hash':source['configuration_hash'],'owned_actor_ids':[]}
    write_json(out/'state.json',status)
    def save(name,data,media):
        nonlocal total
        total+=len(data)
        if total>MAX_BYTES:raise RuntimeError('Native complete evidence byte bound exceeded')
        path=out/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        desc={'name':name,'sha256':sha(data),'size_bytes':len(data),'media_type':media};descriptors.append(desc);return desc
    def cancelled():return CANCELLED or (out/'cancel.request').exists()
    def apply_signals(elapsed):
        for schedule in scenario.get('signals',[]):
            native_signals[schedule['id']].set_state(getattr(carla.TrafficLightState,city_maneuvers.scheduled_state(schedule,elapsed).title()))
    def collect_contacts():
        while not collisions.empty():
            identity,event=collisions.get_nowait()
            if event.frame<=first_input_frame:continue
            if event.frame>previous_frame:raise RuntimeError('Collision callback is ahead of the retained world frame')
            other=next((k for k,v in actors.items() if v.id==event.other_actor.id),None)
            events.append({'kind':'native_contact','actor_ids':[identity]+([other] if other else []),'time_s':max(0.,event.timestamp-start_sim),'frame':event.frame,
                'provenance':'native_carla_collision_sensor','details':{'other_native_id':event.other_actor.id,'other_type':event.other_actor.type_id,'normal_impulse':vector(event.normal_impulse)}})
    try:
        settings=world.get_settings();settings.synchronous_mode=True;settings.fixed_delta_seconds=dt;settings.no_rendering_mode=False
        settings.substepping=True;settings.max_substep_delta_time=.01;settings.max_substeps=10
        world.apply_settings(settings)
        world.set_weather(carla.WeatherParameters.ClearNoon)
        if scenario.get('signals'):
            lights=list(world.get_actors().filter('traffic.traffic_light*'))
            native_signals={light.get_opendrive_id():light for light in lights}
            if len(native_signals)!=len(lights) or any(s['id'] not in native_signals for s in scenario['signals']):raise ValueError('Native signal identity is absent or ambiguous')
            signal_original=[(light,light.get_state(),light.is_frozen()) for light in lights]
            # CARLA freeze can affect the whole group/world: retain all original
            # states, not just the explicitly scheduled light.
            for light in lights:light.freeze(True)
            apply_signals(0.)
        applied=world.get_settings()
        if (not applied.synchronous_mode or abs(applied.fixed_delta_seconds-dt)>1e-6 or not applied.substepping or
            applied.max_substeps!=10 or abs(applied.max_substep_delta_time-.01)>1e-6):raise RuntimeError('Native fixed-step/substep settings were not acknowledged')
        actor_metadata=[];route_progress={};steering_progress={};actor_physics={}
        for spec in scenario['actors']:
            definition=payload['assets'][spec['id']];bp=world.get_blueprint_library().find(definition['blueprint_id'])
            if bp.has_attribute('role_name'):bp.set_attribute('role_name','axiom-city-'+spec['id'])
            if bp.has_attribute('is_invincible'):bp.set_attribute('is_invincible','false')
            p=spec['pose'];transform=carla.Transform(carla.Location(x=p['x'],y=p['y'],z=p['z']+.3),carla.Rotation(yaw=math.degrees(p['heading_rad'])))
            actor=world.try_spawn_actor(bp,transform)
            if actor is None:raise RuntimeError('Native spawn blocked for '+spec['id'])
            owned.append(actor);actors[spec['id']]=actor
            mode=spec['controller']['kind']
            if mode=='timed_replay':actor.set_simulate_physics(False)
            if spec['kind']=='vehicle':
                actor.set_autopilot(False);actor.apply_control(carla.VehicleControl(brake=1.))
                collision=world.spawn_actor(world.get_blueprint_library().find('sensor.other.collision'),carla.Transform(),attach_to=actor)
                owned.append(collision);collision.listen(lambda event,identity=spec['id']:collisions.put((identity,event)))
            bb=actor.bounding_box
            actor_metadata.append({**spec,'native_actor_id':actor.id,'native_blueprint':bp.id,
                'dimensions_m':{'length_m':2*bb.extent.x,'width_m':2*bb.extent.y,'height_m':2*bb.extent.z},
                'bbox_center':vector(bb.location),'geometry_source':'native_actor_bounding_box',
                'motion_fidelity':'timed_position_replay_no_physics' if mode=='timed_replay' else 'native_carla_physics'})
        ego_spec=next(a for a in scenario['actors'] if a['role']=='ego');ego=actors[ego_spec['id']]
        for sensor in scenario.get('sensors',[]):
            bp=world.get_blueprint_library().find('sensor.camera.rgb')
            for key,value in {'image_size_x':sensor['width'],'image_size_y':sensor['height'],'fov':sensor['fov_deg'],'sensor_tick':0}.items():bp.set_attribute(key,str(value))
            p=sensor['pose'];mount=carla.Transform(carla.Location(x=p['x'],y=p['y'],z=p['z']),carla.Rotation(yaw=math.degrees(p['heading_rad'])))
            camera=world.spawn_actor(bp,mount,attach_to=actors[sensor['actor_id']]);owned.append(camera);samples=queue.Queue(maxsize=64)
            camera.listen(lambda image,q=samples:q.put_nowait(image));cameras.append((sensor,camera,samples))
        status['owned_actor_ids']=[a.id for a in owned];write_json(out/'state.json',status)
        # Warm up physics and rendered cameras separately from measurement.
        for _ in range(20):
            frame=world.tick();snap=world.get_snapshot()
            if snap.frame!=frame:raise RuntimeError('Another client advanced the dedicated world')
            for _,_,samples in cameras:
                while True:
                    image=samples.get(timeout=12)
                    if image.frame==frame:break
                    if image.frame>frame:raise RuntimeError('Camera clock advanced past warm-up')
        for spec in scenario['actors']:
            if spec['kind']=='vehicle':actor_physics[spec['id']]=physics(actors[spec['id']])
        for identity,phys in actor_physics.items():
            if not 1<phys['wheelbase_m']<12 or not .1<phys['max_steer_angle_rad']<1.6:raise ValueError('Unsupported native vehicle steering geometry: '+identity)
        warmup_contacts=[]
        while not collisions.empty():
            identity,event=collisions.get_nowait();warmup_contacts.append({'actor_id':identity,'frame':event.frame,'other_type':event.other_actor.type_id})
        if any(e['other_type'].startswith(('vehicle.','walker.','static.')) for e in warmup_contacts):raise ValueError('Initial actors contact before measurement; revise spawn placement')
        map_data=map_definition(world,client.get_server_version());map_data['lanes']=map_lanes(map_data);map_data.pop('opendrive');map_data['opendrive_sha256']=sha(xodr)
        if generated or external:
            map_data['engine_world_name']=map_data['native_name']
            map_data['native_name']=payload['map']['native_name']
            if generated:map_data['generated']=generated
            if external:map_data['external']=external
        scene={'schema_version':'carla-city-scene/1','coordinate_system':COORDINATES,'fidelity':scenario_fidelity(scenario),
            'physical_sensor_validation':False,'source':{**source,'adapter_version':VERSION,'engine_version':client.get_server_version(),
                'runner_sha256':file_sha(__file__),'control_sha256':file_sha(city_control.__file__),'maneuver_sha256':file_sha(city_maneuvers.__file__),'xodr_sha256':sha(xodr)},
            'map':map_data,'actors':actor_metadata,'frames':frames,'events':events,
            'routes':{a['id']:a['route'] for a in scenario['actors']},'planned_paths':{},'camera_records':camera_records,
            'vehicle_physics':actor_physics,'metrics':[],
            'environment':{'requested':scenario['environment'],'native_preset':'ClearNoon','native_readback':{key:float(getattr(world.get_weather(),key)) for key in ('cloudiness','precipitation','precipitation_deposits','sun_altitude_angle','sun_azimuth_angle','fog_density','wetness')}},
            'warmup_contacts':warmup_contacts,
            'contact_capture':{'instrumented_actor_ids':[a['id'] for a in scenario['actors'] if a['kind']=='vehicle'],
                'method':'native_vehicle_collision_sensor_callbacks','final_callback_grace_s':.25,
                'completion':'listener_stop_and_queue_drain_after_bounded_grace; native_api_has_no_event_denominator_ack',
                'absence_scope':'no_observed_callback_in_recorded_window_not_physical_collision_freedom'},
            'signal_control':{'schedules':scenario.get('signals',[]),'scope':'explicit_states_with_all_native_light_cycles_frozen' if signal_original else 'native_uncontrolled_cycles',
                'phase_clock':'command_index_times_timestep','prior_elapsed_cycle_phase_restorable':False},
            'world_settings':{key:getattr(applied,key) for key in ('synchronous_mode','fixed_delta_seconds','substepping','max_substep_delta_time','max_substeps','no_rendering_mode')},
            'sensors':[{**s,'native_sensor_tick_s':0.,'capture_selection':'retain_every_declared_period_from_first_post_warmup_tick',
                'sampling_phase_s':dt,'all_native_callbacks_drained':True} for s in scenario.get('sensors',[])],
            'limitations':['CARLA native vehicle physics and rendered RGB are not measured sensor or real vehicle validation.',
                'Route-follow uses map and actor ground truth. Camera pixels are observations, not controller inputs.',
                'Map lane ribbons and actor proxies use native geometry; they do not reproduce engine meshes.',
                'Traffic behavior is a bounded rule-based controller, not a learned traffic-agent distribution.',
                'Requested seed is recorded; renderer and physics byte-level determinism is not established.']}
        reactive=any(a['controller']['kind']=='reactive' for a in scenario['actors'])
        planner=reactive_planner.ReactivePlanner(scenario['actors'],payload['map_definition'],fixed_delta_seconds=dt) if reactive else None
        active_routes={a['id']:a['route'] for a in scenario['actors']};route_revisions={}
        if reactive:
            scene['source']['planner_sha256']=file_sha(reactive_planner.__file__)
            scene['planner_events']=[]
            scene['planner_scope']='simulator_ground_truth_low_speed_reactive_no_v2x'
        if ego_spec['controller']['kind']=='autoware':
            if not args.autoware_command:raise ValueError('Autoware native runtime is not configured')
            command=json.loads(Path(args.autoware_command).read_bytes())
            if type(command) is not list or any(type(s) is not str for s in command):raise ValueError('Invalid operator Autoware command')
            if payload['autoware'].get('command_sha256')!=file_sha(args.autoware_command):raise ValueError('Operator Autoware launcher changed after preview')
            command=[part.format(episode_id=out.name,output_dir=str(out),map_id=map_name) for part in command]
            controller_process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=(out/'autoware.log').open('wb'),text=True,bufsize=1)
            init={'op':'init','schema_version':'axiom-autoware-init/1','episode_id':out.name,'map_id':map_name,
                'map_sha256':payload.get('autoware',{}).get('lanelet_sha256'),'goal':{**ego_spec['route'][-1],'yaw_rad':ego_spec['route'][-1]['heading_rad']},
                'waypoints':[{k:p[k] for k in ('x','y','z')}|{'yaw_rad':p['heading_rad']} for p in ego_spec['route'][10:-1:10]],
                'dt_s':dt,'vehicle':{'blueprint':payload['assets'][ego_spec['id']]['blueprint_id'],**actor_physics[ego_spec['id']]},
                'max_speed_mps':ego_spec['speed_mps']}
            controller_process.stdin.write(json.dumps(init)+'\n');controller_process.stdin.flush()
            import select
            if not select.select([controller_process.stdout],[],[],90)[0]:raise RuntimeError('Autoware init timed out')
            reply=json.loads(controller_process.stdout.readline())
            if reply.get('status') not in ('initializing','initialized','ready','preparing'):raise RuntimeError('Autoware initialization failed: '+str(reply))
            if reply.get('receipt',{}).get('source_fingerprint')!=payload['autoware'].get('source_fingerprint'):
                raise ValueError('Autoware package source changed after preview')
            scene['autoware_initialization']=reply
            startup=[];scene['autoware_startup']=startup;startup_deadline=time.monotonic()+180
            while True:
                if cancelled():raise InterruptedError('Cancelled during Autoware startup')
                if time.monotonic()>startup_deadline:raise RuntimeError('Autoware startup did not produce a fresh nonzero trajectory/control')
                current_snapshot=world.get_snapshot()
                message=autoware_observation(current_snapshot,actors,actor_metadata,ego_spec['id'],world,actor_physics)
                controller_process.stdin.write(json.dumps(message)+'\n');controller_process.stdin.flush()
                if not select.select([controller_process.stdout],[],[],20)[0]:raise RuntimeError('Autoware startup response timed out')
                reply=json.loads(controller_process.stdout.readline())
                startup.append({'frame':current_snapshot.frame,'simulation_time_s':current_snapshot.timestamp.elapsed_seconds,
                    'status':reply.get('status'),'route':reply.get('route'),'trajectory_count':len(reply.get('trajectory',[])),
                    'maximum_planned_speed_mps':max((p.get('speed_mps',0) for p in reply.get('trajectory',[])),default=0),
                    'command_stamp_s':reply.get('command_stamp_s'),'diagnostics':reply.get('diagnostics',[])})
                if reply.get('status') in ('error','route_rejected','startup_timeout'):raise RuntimeError('Autoware startup failed: '+str(reply))
                ready=reply.get('control') is not None and len(reply.get('trajectory',[]))>=2 and any(p.get('speed_mps',0)>.1 for p in reply['trajectory'])
                # Hold every actor throughout startup, and advance one final
                # exclusive tick so the first trial observation has a new stamp.
                frame=world.tick()
                for _,_,samples in cameras:
                    image=samples.get(timeout=15)
                    if image.frame!=frame:raise RuntimeError('Camera callback lost during Autoware startup')
                if ready:break
        if payload.get('hil'):
            try:
                from .hil_native import HilBridge
            except ImportError:
                from hil_native import HilBridge
            if (dt!=.05 or ego_spec['controller']['kind']!='traffic'
                    or any(a['controller']['kind'] in ('reactive','autoware','timed_replay') for a in scenario['actors'])):
                raise ValueError('HIL requires the 20 Hz physical traffic lead-stop profile')
            hil_bridge=HilBridge(payload['hil'],ego_spec,actor_physics[ego_spec['id']],out,
                lead_spec=next((a for a in scenario['actors'] if a['role']!='ego'),None))
            scene['source']['hil_source_hashes']=payload['hil']['source_hashes']
            scene['hil']={'execution_target':'software_dut','side':payload['hil']['side'],
                'object_source':'carla_actor_ground_truth','camera_is_dut_input':False}
            if hil_bridge.scene_profile is not None:
                scene['hil']['scene_profile']=hil_bridge.scene_profile
        snap=world.get_snapshot();start_sim=snap.timestamp.elapsed_seconds;previous_frame=snap.frame;first_input_frame=snap.frame
        chunk=None;chunk_name=None;chunk_size=0;chunk_index=-1
        for index in range(count):
            if cancelled():raise InterruptedError('Cancelled by operator')
            if time.monotonic()-start_wall>max(240,scenario['duration_s']*15):raise RuntimeError('Native episode wall deadline exceeded')
            current={identity:state_of(snap,actor,identity) for identity,actor in actors.items()}
            commands={};plans={};elapsed=index*dt
            apply_signals(elapsed)
            decisions={}
            if planner:
                for meta in actor_metadata:
                    current[meta['id']]['dimensions_m']=meta['dimensions_m']
                    current[meta['id']]['bbox_center']=meta['bbox_center']
                    current[meta['id']]['kind']=meta['kind']
                signal_input={light.get_opendrive_id():str(light.get_state()).lower().split('.')[-1]
                    for light in world.get_actors().filter('traffic.traffic_light*')}
                for spec in scenario['actors']:
                    native=actors[spec['id']]
                    if spec['kind']=='vehicle' and native.is_at_traffic_light():
                        light=native.get_traffic_light()
                        signal_input[spec['id']]={'state':str(native.get_traffic_light_state()).lower().split('.')[-1],
                            'signal_id':light.get_opendrive_id() if light else None,'distance_m':0.}
                decisions=planner.step(current,elapsed,signals=signal_input)
            foreign={a.id for a in dynamic(world)}-{a.id for a in owned}
            if foreign:raise RuntimeError('Foreign client spawned actors in the owned episode')
            for spec in scenario['actors']:
                identity=spec['id'];actor=actors[identity];control_spec=spec['controller'];mode=control_spec['kind'];state=current[identity]
                wait=elapsed<control_spec.get('wait_s',0);stop_time=control_spec.get('stop_time_s');stop=wait or (stop_time is not None and elapsed>=stop_time)
                if mode=='static':plans[identity]={'state':'static'};continue
                if mode=='timed_replay':
                    replay_time=max(0,min(elapsed,stop_time if stop_time is not None else elapsed)-control_spec.get('wait_s',0))
                    p,speed=city_control.replay_pose(spec['trajectory'],replay_time,control_spec['end_behavior'])
                    actor.set_transform(carla.Transform(carla.Location(x=p['x'],y=p['y'],z=p['z']),carla.Rotation(yaw=math.degrees(p['heading_rad']))))
                    plans[identity]={'state':'timed_replay','requested_pose':p,'replay_speed_mps':speed};continue
                desired=spec['speed_mps']
                hold_reasons=[]
                if wait:hold_reasons.append({'reason':'authored_wait'})
                if stop_time is not None and elapsed>=stop_time:hold_reasons.append({'reason':'authored_stop_time'})
                lead_overlay=None
                if hil_bridge and hil_bridge.scene_profile is not None and identity==hil_bridge.lead_id:
                    # The original scenario remains unchanged. This explicit,
                    # source-pinned overlay replaces only lead speed/stop timing.
                    lead_overlay=hil_bridge.lead_motion(elapsed)
                    desired=lead_overlay['target_speed_mps'];stop=desired==0
                    hold_reasons=[{'reason':'hil_scene_profile','profile_id':hil_bridge.scene_profile['id'],
                                   'phase':lead_overlay['phase']}]
                start_gate=None
                if spec.get('route_mode')=='authored_lane_change' and identity not in maneuver_started and not wait:
                    start_gate=city_maneuvers.lane_change_gap(spec,state,current.values())
                    if not start_gate['permitted']:stop=True;hold_reasons.append({'reason':'lane_change_gap','observation':start_gate})
                if mode=='traffic' and not (hil_bridge and identity==ego_spec['id']):
                    route=spec['route'];ego_projection=city_control.project(state,route)
                    lead=[]
                    for other in current.values():
                        if other['id']==identity:continue
                        p=city_control.project(other,route);gap=p['progress_m']-ego_projection['progress_m']
                        if 0<gap<60 and p['error_m']<2.2:lead.append((gap,other['speed_mps']))
                    if lead:
                        gap,lead_speed=min(lead);desired=min(desired,max(0.,(gap-6)/max(1.,control_spec.get('headway_s',1.5))))
                        hold_reasons.append({'reason':'lead_headway','origin_gap_m':gap,'lead_speed_mps':lead_speed,'limited_speed_mps':desired})
                    if control_spec.get('obey_signals',True) and actor.is_at_traffic_light() and actor.get_traffic_light_state()!=carla.TrafficLightState.Green:
                        stop=True;light=actor.get_traffic_light()
                        hold_reasons.append({'reason':'native_traffic_signal','signal_id':light.get_opendrive_id() if light else None,'observed_state':str(actor.get_traffic_light_state()).lower()})
                if hil_bridge and identity==ego_spec['id']:
                    command,plan=hil_bridge.step(index,current,snap.frame)
                    route_progress[identity]=plan['progress_m'];steering_progress[identity]=plan['steering_progress_m']
                elif mode=='autoware':
                    import select
                    message=autoware_observation(snap,actors,actor_metadata,identity,world,actor_physics)
                    controller_process.stdin.write(json.dumps(message)+'\n');controller_process.stdin.flush()
                    if not select.select([controller_process.stdout],[],[],20)[0]:raise RuntimeError('Autoware command timed out')
                    reply=json.loads(controller_process.stdout.readline());plan={'state':reply.get('status'),'autoware':{k:v for k,v in reply.items() if k!='trajectory'}}
                    if reply.get('status') in ('error','route_rejected','startup_timeout'):raise RuntimeError('Autoware runtime failed: '+str(reply))
                    command=reply.get('control')
                    if command is None:
                        command={'throttle':0.,'brake':1.,'steer':0.}
                    else:
                        stamp=reply.get('command_stamp_s')
                        if type(stamp) not in (float,int) or not -.0001<=snap.timestamp.elapsed_seconds-stamp<=.25:raise ValueError('Autoware command is stale or in the future')
                    if reply.get('trajectory'):scene['planned_paths'].setdefault(identity,[]).append({'index':index,'points':[{**p,'heading_rad':p['yaw_rad']} for p in reply['trajectory']]})
                else:
                    phys=actor_physics.get(identity,{'wheelbase_m':2.7,'max_steer_angle_rad':.6})
                    decision=decisions.get(identity) if mode=='reactive' else None
                    if decision:
                        active_routes[identity]=decision['route'];desired=min(12.,decision['speed_mps']);stop=stop or decision['stop']
                        if route_revisions.get(identity)!=decision['route_revision']:
                            route_progress.pop(identity,None);steering_progress.pop(identity,None)
                            route_revisions[identity]=decision['route_revision']
                            scene['planned_paths'].setdefault(identity,[]).append({'index':index,'points':decision['route'],
                                'route_revision':decision['route_revision'],'reason':decision['reason']})
                        scene['planner_events'].extend({**event,'index':index,'input_frame':snap.frame,
                            'provenance':'observed_state_reactive_planner'} for event in decision['events'])
                    command,plan=city_control.feedback(state,active_routes[identity],desired,wheelbase_m=phys['wheelbase_m'],max_steer_rad=phys['max_steer_angle_rad'],rear_axle_offset_m=phys.get('rear_axle_offset_m',0.),previous=route_progress.get(identity),previous_steering=steering_progress.get(identity),end_behavior=control_spec['end_behavior'],stop=stop)
                    if decision:
                        plan['reactive']={k:v for k,v in decision.items() if k not in {'route','events','original_route'}}
                        plan['state']=decision['state']
                    route_progress[identity]=plan['progress_m']
                    steering_progress[identity]=plan['steering_progress_m']
                    if control_spec['end_behavior']=='repeat' and plan['goal_reached']:route_progress[identity]=0.;steering_progress[identity]=0.
                if start_gate is not None:
                    plan['lane_change_start_gate']=start_gate
                    if start_gate['permitted'] and not stop and command.get('throttle',0)>0:
                        maneuver_started.add(identity)
                plan['hold_reasons']=hold_reasons
                if lead_overlay is not None:plan['hil_lead_motion']=lead_overlay
                plans[identity]=plan
                if spec['kind']=='pedestrian':
                    target=plan['target'];dx,dy=target['x']-state['x'],target['y']-state['y'];norm=max(1e-9,math.hypot(dx,dy))
                    actor.apply_control(carla.WalkerControl(direction=carla.Vector3D(x=dx/norm,y=dy/norm,z=0),speed=plan['desired_speed_mps']))
                    commands[identity]={'speed_mps':plan['desired_speed_mps'],'direction':[dx/norm,dy/norm,0.]}
                else:
                    if set(command)!={'throttle','brake','steer'} or any(not math.isfinite(v) for v in command.values()) or not 0<=command['throttle']<=1 or not 0<=command['brake']<=1 or not -1<=command['steer']<=1:raise ValueError('Invalid native vehicle command')
                    if not hil_bridge:actor.apply_control(carla.VehicleControl(**command))
                    commands[identity]=command
            if hil_bridge:
                hil_bridge.apply_controls(carla,client,actors,commands)
            frame=world.tick();snap=world.get_snapshot()
            if frame!=previous_frame+1 or snap.frame!=frame or abs(snap.timestamp.elapsed_seconds-start_sim-(index+1)*dt)>1e-5:raise RuntimeError('Native step ownership/clock discontinuity')
            observed=[]
            for spec in scenario['actors']:
                identity=spec['id'];state=state_of(snap,actors[identity],identity);plan=plans.get(identity,{})
                if spec['route']:
                    projection=city_control.project(state,active_routes[identity],previous=route_progress.get(identity))
                    state.update(route_error_m=projection['error_m'],route_progress_m=projection['progress_m'],goal_distance_m=math.hypot(state['x']-(spec.get('destination') or spec['route'][-1])['x'],state['y']-(spec.get('destination') or spec['route'][-1])['y']))
                state.update(control=commands.get(identity),desired_speed_mps=plan.get('desired_speed_mps'),controller_state=plan.get('state'),plan=plan)
                observed.append(state)
            if hil_bridge:
                applied_control=ego.get_control()
                hil_bridge.observed(next(a for a in observed if a['id']==ego_spec['id']),frame,
                    {key:float(getattr(applied_control,key)) for key in ('throttle','brake','steer')},
                    lead=next((a for a in observed if a['id']==hil_bridge.lead_id),None))
            record={'index':index,'frame':frame,'input_frame':previous_frame,'time_s':(index+1)*dt,'simulation_time_s':snap.timestamp.elapsed_seconds,'actors':observed,'cameras':[],
                'signals':[{'id':light.get_opendrive_id(),'state':str(light.get_state()).lower()} for light in world.get_actors().filter('traffic.traffic_light*')]}
            signal_states={s['id']:s['state'] for s in record['signals']}
            if any(signal_states.get(s['id'])!=city_maneuvers.scheduled_state(s,elapsed) for s in scenario.get('signals',[])):
                raise RuntimeError('Native signal state differs from the fixed authored schedule')
            for sensor,camera,samples in cameras:
                image=samples.get(timeout=15)
                if image.frame!=frame:raise RuntimeError('Missing, late or duplicate rendered camera frame; no sample is dropped')
                raw=bytes(image.raw_data)
                # Every native callback is drained. Retention is an explicit
                # authored sampling policy, not a missing-frame tolerance.
                if index % round(sensor['period_s']/dt):continue
                if chunk is None or chunk_size+len(raw)>CHUNK_BYTES:
                    if chunk:
                        chunk.close();archives.append(chunk_name)
                    chunk_index+=1;chunk_name=f'city-camera-{chunk_index:04d}.zip';chunk=camera_archive(out/chunk_name,network=payload.get('hil',{}).get('runtime',{}).get('kind')=='network_reference');chunk_size=0
                member=f'{sensor["id"]}/{index:06d}.bgra';chunk.writestr(member,raw);chunk_size+=len(raw);total+=len(raw)
                if total>MAX_BYTES:raise RuntimeError('Complete native camera capture exceeds approved budget')
                image_record={'sensor_id':sensor['id'],'index':index,'frame':frame,'time_s':record['time_s'],'simulation_time_s':image.timestamp,
                    'archive':chunk_name,'member':member,'sha256':sha(raw),'size_bytes':len(raw),'width':image.width,'height':image.height,'encoding':'bgra8',
                    'world_pose':pose(image.transform)}
                if abs(image.timestamp-snap.timestamp.elapsed_seconds)>1e-5:raise RuntimeError('Camera/world timestamps differ')
                camera_records.append(image_record);record['cameras'].append(image_record)
            frames.append(record)
            previous_frame=frame
            collect_contacts()
        # With the world stopped, allow asynchronous final collision callbacks
        # to arrive; do not advance or add a hidden driving tick.
        time.sleep(.25)
        for actor in owned:
            if actor.type_id=='sensor.other.collision':actor.stop()
        collect_contacts()
        if chunk:chunk.close();archives.append(chunk_name);chunk=None
        for name in archives:
            path=out/name;descriptors.append({'name':name,'sha256':file_sha(path),'size_bytes':path.stat().st_size,'media_type':'application/zip'})
        if controller_process and not any(p.get('state')=='driving' for f in frames for p in [next(a for a in f['actors'] if a['id']==ego_spec['id'])['plan']]):
            raise RuntimeError('Autoware never engaged with a valid nonzero native trajectory')
        for spec in scenario['actors']:
            states=[next(a for a in frame['actors'] if a['id']==spec['id']) for frame in frames]
            errors=sorted(a['route_error_m'] for a in states if 'route_error_m' in a)
            travelled=sum(math.hypot(a['x']-b['x'],a['y']-b['y']) for a,b in zip(states,states[1:]))
            collision=any(spec['id'] in e['actor_ids'] for e in events if e['kind']=='native_contact')
            values={'actor_id':spec['id'],'distance_travelled_m':travelled,'maximum_speed_mps':max(a['speed_mps'] for a in states),'native_contact':collision,
                'lateral_error_p95_m':errors[min(len(errors)-1,math.ceil(.95*len(errors))-1)] if errors else None,
                'goal_error_m':states[-1].get('goal_distance_m'),'final_speed_mps':states[-1]['speed_mps']}
            scene['metrics'].append(values)
        scene['evaluation_status']='not_evaluated';scene['verification_verdict']='not_evaluated'
        if hil_bridge:
            hil_bridge.dut.close();hil_bridge.trace['status']='completed'
            save('hil-trace.json',canonical(hil_bridge.trace),'application/json')
        save('city-scene.json',canonical(scene),'application/json');save('map.xodr',xodr,'application/xml')
        status.update(status='completed',frame_count=len(frames),camera_count=len(camera_records),drop_count=0)
    except BaseException as error:
        status.update(status='cancelled' if isinstance(error,InterruptedError) else 'failed',error_type=type(error).__name__,error=str(error))
        if scene:write_json(out/'partial-city-scene.json',scene)
        raise
    finally:
        cleanup=[]
        if 'chunk' in locals() and chunk is not None:chunk.close()
        if hil_bridge and hil_bridge.dut.process.poll() is None:
            try:hil_bridge.dut.close(graceful=False)
            except Exception as error:status['hil_cleanup_error']=str(error)
        if hil_bridge and hil_bridge.trace['status']!='completed':
            hil_bridge.trace.update(status='failed',error=status.get('error'))
            write_json(out/'partial-hil-trace.json',hil_bridge.trace)
        if controller_process:
            try:
                controller_process.stdin.write('{"op":"close"}\n');controller_process.stdin.flush();controller_process.wait(timeout=20)
            except Exception:
                controller_process.terminate()
                try:controller_process.wait(timeout=10)
                except subprocess.TimeoutExpired:controller_process.kill();controller_process.wait()
        for actor in reversed(owned):
            try:
                if actor.type_id.startswith('sensor.'):actor.stop()
                cleanup.append({'actor_id':actor.id,'destroy_acknowledged':actor.destroy()})
            except Exception as error:cleanup.append({'actor_id':actor.id,'destroy_acknowledged':False,'error':str(error)})
        try:
            for light,state,frozen in signal_original:light.set_state(state);light.freeze(frozen)
            world.tick();snap=world.get_snapshot();absent=all(snap.find(a.id) is None for a in owned)
            clean=all(e['destroy_acknowledged'] for e in cleanup) and absent and not dynamic(world)
            if hil_bridge:
                clean=clean and hil_bridge.dut.process.poll() is not None and not status.get('hil_cleanup_error')
                status['hil_dut_exit_confirmed']=hil_bridge.dut.process.poll() is not None
            clean=clean and all(light.is_frozen()==frozen for light,_,frozen in signal_original)
            status['signal_restoration']={'states_requested':len(signal_original),'freeze_restored':clean,'elapsed_phase_restored':False}
            world.apply_settings(original);world.set_weather(original_weather)
            restored=world.get_settings()
            clean=clean and restored.synchronous_mode==original.synchronous_mode and restored.fixed_delta_seconds==original.fixed_delta_seconds
        except Exception as error:status['cleanup_error']=str(error);clean=False
        status.update(cleanup_status='confirmed' if clean else 'unconfirmed',cleanup=cleanup)
        lease_state['clean']=clean;write_json(out/'state.json',status)
    if not clean:raise RuntimeError('Native cleanup is unconfirmed; engine quarantined')
    status['artifacts']=descriptors;status['source']=source;status['schema_version']='carla-city-native-receipt/1'
    write_json(out/'receipt.json',status)
    return status


def qualify_map(payload,out,carla,client,lease_state):
    """Queued load/readback only. No actors, guessed topology or drive verdict."""
    if payload.get('schema_version')!='carla-map-qualification-input/1':
        raise ValueError('Unknown external map qualification contract')
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    status={'schema_version':'carla-map-native-receipt/1','external_execution_id':out.name,
            'status':'running','cleanup_status':'unknown','source':payload['source']}
    write_json(out/'state.json',status)
    try:
        if dynamic(client.get_world()):raise RuntimeError('Dedicated map endpoint has foreign dynamic actors')
        if payload['server_version']!=client.get_server_version():raise ValueError('External map engine build differs')
        content=payload['opendrive']
        if not isinstance(content,str) or len(content.encode())>20*1024**2 or sha(content.encode())!=payload['candidate_sha256']:
            raise ValueError('External map candidate byte bound/hash mismatch')
        if CANCELLED or (out/'cancel.request').exists():raise RuntimeError('Map qualification cancelled')
        parameters=carla.OpendriveGenerationParameters(vertex_distance=1.,wall_height=0.,additional_width=0.,smooth_junctions=False)
        world=client.generate_opendrive_world(content,parameters)
        if CANCELLED or (out/'cancel.request').exists():raise RuntimeError('Map qualification cancelled')
        native=world.get_map().to_opendrive().encode()
        status.update(input_opendrive_sha256=payload['candidate_sha256'],native_opendrive_sha256=sha(native),
                      native_readback_size_bytes=len(native))
        if len(native)>20*1024**2:raise ValueError('Native OpenDRIVE readback exceeds 20 MiB')
        # Keep discrepant readback bytes in the owned failed-attempt directory.
        # Hash differences are still failures and never become a ready map.
        (out/'native-readback.xodr').write_bytes(native)
        if sha(native)!=payload['candidate_sha256']:
            raise ValueError('Native OpenDRIVE differs from external candidate; preserve and inspect before retry')
        definition=map_definition(world,client.get_server_version())
        if not definition['nodes']:raise ValueError('Native map has no drivable lane nodes')
        if any(not math.isfinite(n[k]) or abs(n[k])>10000 for n in definition['nodes'] for k in ('x','y','z')):
            raise ValueError('Native map exceeds local ±10000 m coordinate contract; use a pinned local origin')
        catalog_raw=canonical(definition)
        report={'schema_version':'carla-map-qualification/1','status':'completed','cleanup_status':'confirmed',
            'source_ref':payload['source_ref'],'conversion_ref':payload['conversion_ref'],
            'input_opendrive_sha256':payload['candidate_sha256'],'native_opendrive_sha256':sha(native),
            'map_catalog_sha256':sha(catalog_raw),'server_version':client.get_server_version(),
            'native_runner_sha256':file_sha(__file__),'node_count':len(definition['nodes']),
            'spawn_point_count':len(definition['spawn_points']),'physical_validation':False,
            'qualification_scope':'native_map_load_readback_not_driving','driving_evaluated':False,
            'mesh_parameters':{'vertex_distance':1.,'wall_height':0.,'additional_width':0.,'smooth_junctions':False}}
        files=[('map.xodr',native,'application/xml'),('map-catalog.json',catalog_raw,'application/json'),
               ('map-qualification.json',canonical(report),'application/json')]
        descriptors=[]
        for name,raw,media in files:
            (out/name).write_bytes(raw)
            descriptors.append({'name':name,'sha256':sha(raw),'size_bytes':len(raw),'media_type':media})
        status.update(status='completed',artifacts=descriptors)
    except BaseException as error:
        status.update(status='cancelled' if CANCELLED or (out/'cancel.request').exists() else 'failed',error=str(error))
        raise
    finally:
        try:clean=not dynamic(client.get_world())
        except Exception:clean=False
        status['cleanup_status']='confirmed' if clean else 'unconfirmed'
        lease_state['clean']=clean;write_json(out/'state.json',status)
    if not clean:raise RuntimeError('External map cleanup unconfirmed; endpoint quarantined')
    write_json(out/'receipt.json',status)
    return status


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,required=True);parser.add_argument('--owner',required=True)
    parser.add_argument('--spool',required=True);parser.add_argument('--probe',action='store_true');parser.add_argument('--catalog',action='store_true')
    parser.add_argument('--map');parser.add_argument('--output');parser.add_argument('--autoware-command')
    parser.add_argument('--qualify-map',action='store_true')
    args=parser.parse_args();carla,client=connect(args.port,args.owner)
    if args.probe:print(json.dumps(probe(client)));return
    identity=Path(args.output).name if args.output else 'catalog'
    with lease(args.spool,identity) as state:
        if args.catalog:
            world=client.get_world()
            if dynamic(world):raise RuntimeError('Catalog map loading refused with foreign actors')
            if args.map and world.get_map().name.split('/')[-1]!=args.map:client.load_world(args.map)
            result=catalog(client);state['clean']=True
        else:
            if not args.output:raise ValueError('Execution output directory required')
            raw=sys.stdin.buffer.read(64*1024**2+1)
            if len(raw)>64*1024**2:raise ValueError('Frozen city input exceeds 64 MiB')
            payload=json.loads(raw)
            result=qualify_map(payload,args.output,carla,client,state) if args.qualify_map else capture(payload,args.output,carla,client,state,args)
    print(json.dumps(result,allow_nan=False))


if __name__=='__main__':
    def cancel_signal(*_):
        global CANCELLED
        CANCELLED=True
    signal.signal(signal.SIGTERM,cancel_signal);signal.signal(signal.SIGINT,cancel_signal)
    main()
