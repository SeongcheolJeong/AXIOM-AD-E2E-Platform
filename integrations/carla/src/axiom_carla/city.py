"""Optional, operator-fixed native city transport for normal experiment jobs."""
from __future__ import annotations
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
import base64
import zlib
from datetime import datetime, timezone
from ade2e.adapters import AdapterCancelled
from ade2e.experiment_runtime import ExperimentExecution,ExperimentHandlerMetadata,ExperimentOutputFile
from ade2e.scenario_v2 import get_scenario
from ade2e.scenario_v2_schemas import ScenarioCity, parse_scenario
from ade2e.scenario_v2_xml import digest
from ade2e.city_motion import scenario_fidelity
from ade2e.simulation_catalog import catalog_reference,validate_city_catalogs,map_lanes
from . import city_native,city_control,city_maneuvers,reactive_planner

VERSION=city_native.VERSION
canonical=city_native.canonical
sha=city_native.sha
file_sha=city_native.file_sha


def validate_camera_archive(path, records, *, network=False):
    """Check container profile and original bounded BGRA bytes before publication."""
    expected=zipfile.ZIP_DEFLATED if network else zipfile.ZIP_STORED
    with zipfile.ZipFile(path) as bundle:
        infos=bundle.infolist()
        if len(infos)!=len(records) or {i.filename for i in infos}!={r['member'] for r in records}:
            raise ValueError('Camera archive closure mismatch')
        if any(i.compress_type!=expected for i in infos):
            raise ValueError('Camera archive compression differs from native storage contract')
        for record in records:
            info=bundle.getinfo(record['member'])
            if (type(record['size_bytes']) is not int or not 0<record['size_bytes']<=16*1024**2
                or info.file_size!=record['size_bytes']):
                raise ValueError('Camera frame content size mismatch')
            with bundle.open(info) as member:
                content=member.read(record['size_bytes']+1)
            if len(content)!=record['size_bytes'] or sha(content)!=record['sha256']:
                raise ValueError('Camera frame content mismatch')


def validate_routes(scenario,map_definition):
    """Physical vehicle routes must follow the directed registered graph.

    Pedestrian paths are explicitly authored world paths, not driving lanes.
    Arbitrary vehicle trajectories remain the clearly separate replay mode.
    """
    nodes=map_definition['nodes'];by_id={p['id']:p for p in nodes}
    for actor in scenario['actors']:
        if actor.get('destination') is not None and not any(math.hypot(n['x']-actor['destination']['x'],n['y']-actor['destination']['y'])<=.25 and abs(n['z']-actor['destination']['z'])<=.5 for n in nodes):
            raise ValueError('Reactive destination must be a pinned lane graph point')
        if actor['speed_mps']>12:raise ValueError('Native city profile supports speeds up to 12 m/s')
        if actor['controller']['kind'] in ('traffic','autoware') and actor['kind']!='vehicle':raise ValueError('Vehicle controller requires a vehicle')
        if actor['controller']['kind']=='autoware' and actor['controller']['end_behavior']!='stop':raise ValueError('Autoware city profile requires end stop')
        if actor['controller']['kind']=='autoware' and (actor['controller'].get('wait_s') or actor['controller'].get('stop_time_s') is not None):
            raise ValueError('Autoware owns motion timing; scripted wait/stop controls would create a second control owner')
        if actor['controller']['kind']=='autoware' and actor['speed_mps']>8:raise ValueError('Autoware GT profile supports speeds up to 8 m/s')
        if not actor['route']:continue
        route=actor['route'];city_control.lengths(route)
        if math.hypot(actor['pose']['x']-route[0]['x'],actor['pose']['y']-route[0]['y'])>3:
            raise ValueError('Actor initial position must be within 3 m of its route start')
        if actor['controller']['end_behavior']=='repeat' and math.hypot(route[0]['x']-route[-1]['x'],route[0]['y']-route[-1]['y'])>2:
            raise ValueError('Repeated routes must form an explicit connected loop; teleporting is not supported')
        if actor['kind']!='vehicle':continue
        if actor.get('route_mode')=='authored_lane_change':
            city_maneuvers.validate_lane_change(actor,map_definition)
            continue
        previous_ids=None
        for point in route:
            candidates=[n for n in nodes if abs(n['x']-point['x'])<1 and abs(n['y']-point['y'])<1 and abs(n['z']-point['z'])<1]
            if not candidates:raise ValueError('Vehicle route must be generated from the pinned native lane graph')
            candidates=[n for n in candidates if math.sqrt(sum((n[k]-point[k])**2 for k in ('x','y','z')))<=.25]
            if not candidates:raise ValueError('Vehicle route point differs from native lane graph; use route preflight')
            candidates=[n for n in candidates if abs(math.atan2(math.sin(point['heading_rad']-n['heading_rad']),math.cos(point['heading_rad']-n['heading_rad'])))<=.2]
            if not candidates:raise ValueError('Vehicle route heading differs from directed native lane')
            # Junction endpoints can occupy identical coordinates. Carry all
            # reachable candidates so an arbitrary nearest-node tie cannot
            # reject a real edge or admit a disconnected jump.
            reachable={n['id'] for n in candidates if previous_ids is None or any(n['id']==p or n['id'] in by_id[p]['next_ids'] for p in previous_ids)}
            if not reachable:
                raise ValueError('Vehicle route contains an unreachable native lane edge')
            previous_ids=reachable


def _finite(value):
    return type(value) in (int,float) and math.isfinite(value)


def validate_controller_revision(declaration):
    if declaration['adapter_id']!='carla-city-object' or declaration['adapter_version']!=VERSION:
        raise ValueError('Pinned controller does not belong to the selected native city adapter version')
    if declaration['mode'] in ('route_follow','traffic'):
        math_versions=[c.split(':',1)[1] for c in declaration['capabilities'] if c.startswith('control_math:')]
        pinned_math=math_versions[0] if len(math_versions)==1 else 'axiom-city-pure-pursuit/1' if not math_versions else None
        if pinned_math!=city_control.VERSION:
            raise ValueError('Pinned controller math is unavailable; select its new catalog revision explicitly')


def _close(actual,expected,tolerance=1e-5):
    return _finite(actual) and _finite(expected) and abs(actual-expected)<=tolerance


def _vector_valid(value,keys=('x','y','z')):
    return isinstance(value,dict) and all(_finite(value.get(k)) for k in keys)


def _measured_metrics(scene,scenario,*,verify_diagnostics=False):
    """Independently derive metrics from retained response poses, never totals."""
    frames=scene.get('frames',[])
    if not frames:raise ValueError('No native response frames for evaluation')
    result=[]
    for spec in scenario['actors']:
        states=[];errors=[];progress=None;tracking_revision=None
        route=spec['route']
        for frame in frames:
            matches=[a for a in frame['actors'] if a['id']==spec['id']]
            if len(matches)!=1:raise ValueError('Metric actor denominator differs from scenario')
            actor=matches[0];states.append(actor)
            if not _vector_valid(actor) or not _finite(actor.get('speed_mps')) or actor['speed_mps']<0:
                raise ValueError('Native metric inputs must be finite response telemetry')
            if route:
                tracking_route=route
                if spec['controller']['kind']=='reactive':
                    history=[p for p in scene.get('planned_paths',{}).get(spec['id'],[]) if p['index']<=frame['index']]
                    if not history:raise ValueError('Reactive response lacks its executed route revision')
                    tracking_route=history[-1]['points']
                    revision=history[-1].get('route_revision',history[-1]['index'])
                    if revision!=tracking_revision:progress=None
                    tracking_revision=revision
                projection=city_control.project(actor,tracking_route,previous=progress)
                destination=spec.get('destination') or route[-1]
                goal=math.hypot(actor['x']-destination['x'],actor['y']-destination['y'])
                if verify_diagnostics and any(not _close(actor.get(key),expected) for key,expected in (
                    ('route_error_m',projection['error_m']),('route_progress_m',projection['progress_m']),('goal_distance_m',goal))):
                    raise ValueError('Native route diagnostics differ from response geometry')
                errors.append(projection['error_m']);progress=projection['progress_m']
                if spec['controller']['end_behavior']=='repeat' and projection['remaining_m']<=1 and goal<=2:
                    progress=0.
        errors.sort()
        result.append({'actor_id':spec['id'],
            'distance_travelled_m':sum(math.hypot(a['x']-b['x'],a['y']-b['y']) for a,b in zip(states,states[1:])),
            'maximum_speed_mps':max(a['speed_mps'] for a in states),
            'native_contact':any(spec['id'] in e.get('actor_ids',[]) for e in scene.get('events',[]) if e.get('kind')=='native_contact'),
            'lateral_error_p95_m':errors[max(0,math.ceil(.95*len(errors))-1)] if errors else None,
            'goal_error_m':math.hypot(states[-1]['x']-(spec.get('destination') or route[-1])['x'],states[-1]['y']-(spec.get('destination') or route[-1])['y']) if route else None,
            'final_speed_mps':states[-1]['speed_mps']})
    return result


def evaluate(scene,scenario):
    """Frozen acceptance against rederived metrics, separate from completion."""
    criterion=scenario.get('acceptance',{});ego=next(a for a in scenario['actors'] if a['role']=='ego')
    measured=_measured_metrics(scene,scenario,verify_diagnostics=True)
    replay_ids=[a['id'] for a in scenario['actors'] if a['controller']['kind']=='timed_replay']
    contact_sensor_ids=[a['id'] for a in scenario['actors'] if a['kind']=='vehicle' and a['id'] not in replay_ids]
    row=next(m for m in measured if m['actor_id']==ego['id']);checks=[]
    if ego['controller']['kind'] in ('route_follow','traffic','autoware','reactive'):
        checks.append({'metric':'lateral_error_p95_m','value':row['lateral_error_p95_m'],'threshold':criterion.get('max_lateral_error_p95_m',.5),'operator':'<='})
        if criterion.get('require_goal',True):
            checks.extend([{'metric':'goal_error_m','value':row['goal_error_m'],'threshold':criterion.get('max_goal_error_m',2.),'operator':'<='},
                {'metric':'final_speed_mps','value':row['final_speed_mps'],'threshold':criterion.get('max_final_speed_mps',.2),'operator':'<='}])
    if criterion.get('require_no_contacts',True):
        check={'metric':'all_actor_native_contact_count','value':None if replay_ids or not contact_sensor_ids else sum(e['kind']=='native_contact' for e in scene['events']),'threshold':0,'operator':'<='}
        if replay_ids:check['reason']='mixed_timed_replay_contact_is_diagnostic_only'
        elif not contact_sensor_ids:check['reason']='no_physical_vehicle_collision_sensor'
        checks.append(check)
    for check in checks:check['status']='unevaluated' if check['value'] is None else ('pass' if check['value']<=check['threshold'] else 'fail')
    verdict='not_evaluated' if not checks or any(c['status']=='unevaluated' for c in checks) else ('fail' if any(c['status']=='fail' for c in checks) else 'pass')
    return {'schema_version':'carla-city-evaluation/1','criteria':criterion,'checks':checks,'verdict':verdict,
            'measured_metrics':measured,'metric_source':'recomputed_retained_native_response_geometry',
            'classification':'sut_performance_failure' if verdict=='fail' else 'bounded_engine_profile_result',
            'scope':('timed_position_replay_diagnostic_only' if scenario_fidelity(scenario)=='native_carla_timed_position_replay' else
                     'physical_actor_route_control_with_replay_context' if replay_ids else 'native_simulator_ground_truth_route_control_only'),
            'diagnostic_only_actor_ids':replay_ids,'physical_qualification_actor_ids':[a['id'] for a in scenario['actors'] if a['id'] not in replay_ids],
            'contact_observation_scope':{'sensor_actor_ids':contact_sensor_ids,'coverage':'union_of_vehicle_collision_callbacks; pedestrian/static-only interactions are not fully instrumented'},
            'physical_sensor_validation':False}


def validate_scene(scene,snapshot):
    try:return _validate_scene(scene,snapshot)
    except (KeyError,TypeError,IndexError,AttributeError) as error:
        raise ValueError('Malformed or incomplete native city evidence: '+str(error)) from error


def _validate_reactive_evidence(scene, scenario, snapshot):
    reactive={a['id'] for a in scenario['actors'] if a['controller']['kind']=='reactive'}
    if not reactive:return
    frames=scene['frames'];histories=scene.get('planned_paths',{})
    definition=snapshot.get('map_definition')
    if definition is None and snapshot.get('map_definition_pack'):
        packed=snapshot['map_definition_pack'];decoder=zlib.decompressobj()
        raw=decoder.decompress(base64.b64decode(packed['data'],validate=True),64*1024**2+1)
        if len(raw)>64*1024**2 or not decoder.eof or decoder.unused_data or sha(raw)!=packed['sha256']:
            raise ValueError('Frozen reactive map archive is invalid')
        definition=json.loads(raw)
    if not definition or not definition.get('nodes'):raise ValueError('Reactive evidence lacks its pinned lane corridor')
    nodes={n['id']:n for n in definition['nodes']};cells={}
    for node in nodes.values():cells.setdefault((math.floor(node['x']/10),math.floor(node['y']/10)),[]).append(node)
    def in_corridor(point):
        x,y=math.floor(point['x']/10),math.floor(point['y']/10)
        for dx in (-1,0,1):
            for dy in (-1,0,1):
                for a in cells.get((x+dx,y+dy),[]):
                    for b in [nodes[i] for i in a.get('next_ids',[])]+[a]:
                        vx,vy=b['x']-a['x'],b['y']-a['y'];length=vx*vx+vy*vy
                        t=max(0,min(1,((point['x']-a['x'])*vx+(point['y']-a['y'])*vy)/length)) if length else 0
                        if (math.hypot(point['x']-a['x']-t*vx,point['y']-a['y']-t*vy)<=a['lane_width_m']/2+.5
                            and abs(point['z']-(a['z']+t*(b['z']-a['z'])))<=1.5):return True
        return False
    for identity in reactive:
        history=histories.get(identity,[])
        if not history or history[0].get('index')!=0:raise ValueError('Reactive route history must begin at the first command')
        previous=-1;revision=-1
        for row in history:
            index=row.get('index');current=row.get('route_revision');points=row.get('points')
            if (type(index) is not int or not previous<index<len(frames) or type(current) is not int or current<=revision
                or not isinstance(points,list) or not 2<=len(points)<=10000
                or any(not _vector_valid(p,('x','y','z','heading_rad')) for p in points)):
                raise ValueError('Reactive route history index, revision or geometry is invalid')
            city_control.lengths(points);previous=index;revision=current
            spec=next(a for a in scenario['actors'] if a['id']==identity)
            if spec['kind']=='vehicle':
                sampled=[]
                for a,b in zip(points,points[1:]):
                    count=max(1,math.ceil(math.hypot(b['x']-a['x'],b['y']-a['y'])))
                    if count>1000:raise ValueError('Reactive route contains an unbounded corridor jump')
                    sampled.extend({key:a[key]+(b[key]-a[key])*i/count for key in ('x','y','z')} for i in range(count))
                if any(not in_corridor(point) for point in [*sampled,points[-1]]):
                    raise ValueError('Reactive vehicle path leaves the pinned road corridor')
        for frame in frames:
            active=next(row for row in reversed(history) if row['index']<=frame['index'])
            actor=next(a for a in frame['actors'] if a['id']==identity)
            decision=actor.get('plan',{}).get('reactive',{})
            if (decision.get('route_revision')!=active['route_revision'] or decision.get('planner_version')!=reactive_planner.VERSION
                or not isinstance(decision.get('reason'),str)):
                raise ValueError('Reactive response decision differs from active route revision')
    events=scene.get('planner_events')
    if not isinstance(events,list):raise ValueError('Reactive event history is absent')
    previous=-1
    for event in events:
        index=event.get('index');identity=event.get('actor_id')
        if (type(index) is not int or not previous<=index<len(frames) or identity not in reactive
            or event.get('provenance')!='observed_state_reactive_planner'
            or event.get('input_frame')!=frames[index]['input_frame']
            or not _close(event.get('time_s'),index*scenario['timestep_s'])
            or event.get('type') not in {'action_started','action_completed','action_timeout','route_replanned','decision_changed','stop_sign_completed'}
            or not isinstance(event.get('reason'),str)):
            raise ValueError('Reactive event identity, clock or provenance is invalid')
        active=next(row for row in reversed(histories[identity]) if row['index']<=index)
        revision=event.get('route_revision')
        if type(revision) is not int or not 0<=revision<=active['route_revision']:
            raise ValueError('Reactive event claims a future or invalid route revision')
        previous=index


def _validate_scene(scene,snapshot):
    source=snapshot['source'];scenario=snapshot['scenario'];dt=scenario['timestep_s']
    if (scene.get('schema_version')!='carla-city-scene/1' or scene.get('physical_sensor_validation') is not False
        or scene.get('coordinate_system')!=scenario['coordinate_system'] or scene.get('fidelity')!=scenario_fidelity(scenario)
        or snapshot.get('execution_fidelity',scenario_fidelity(scenario))!=scenario_fidelity(scenario)):
        raise ValueError('Native city scene contract mismatch')
    if any(scene.get('source',{}).get(k)!=v for k,v in source.items()):raise ValueError('Native scene source differs from frozen scenario')
    if (scene['source'].get('runner_sha256')!=snapshot['native_script_sha256'] or scene['source'].get('control_sha256')!=snapshot['control_script_sha256']
        or scene['source'].get('adapter_version')!=VERSION or scene['source'].get('engine_version')!=snapshot['server_version']):
        raise ValueError('Native city controller/source/build version mismatch')
    if snapshot.get('maneuver_script_sha256') and scene['source'].get('maneuver_sha256')!=snapshot['maneuver_script_sha256']:
        raise ValueError('Native maneuver/signal source version mismatch')
    if snapshot.get('planner_script_sha256') and scene['source'].get('planner_sha256')!=snapshot['planner_script_sha256']:
        raise ValueError('Reactive planner source differs from frozen execution')
    if (scene['source'].get('xodr_sha256')!=snapshot['map']['opendrive_sha256']
        or scene['map'].get('opendrive_sha256')!=snapshot['map']['opendrive_sha256']
        or scene['map'].get('native_name')!=snapshot['map']['native_name']):
        raise ValueError('Native map differs from the registered map original')
    if snapshot.get('external_map_provenance') is not None and scene['map'].get('external')!=snapshot['external_map_provenance']:
        raise ValueError('Native external map provenance differs from pinned qualified catalog')
    identities=[a['id'] for a in scenario['actors']];expected=round(scenario['duration_s']/dt)
    frames=scene.get('frames',[])
    if len(frames)!=expected:raise ValueError('Native city frame denominator is incomplete')
    if [a.get('id') for a in scene.get('actors',[])]!=identities:raise ValueError('Native city actor denominator differs')
    native_ids={}
    for spec,actual in zip(scenario['actors'],scene['actors']):
        if any(actual.get(k)!=v for k,v in spec.items()):raise ValueError('Native scene actor intent differs from frozen authoring')
        if actual['native_blueprint']!=snapshot['assets'][spec['id']]['blueprint_id']:raise ValueError('Native blueprint differs from frozen asset revision')
        if type(actual.get('native_actor_id')) is not int or actual['native_actor_id']<=0:raise ValueError('Invalid native actor identity')
        native_ids[spec['id']]=actual['native_actor_id']
        bounds=actual.get('dimensions_m',{})
        if set(bounds)!={'length_m','width_m','height_m'} or any(not _finite(n) or n<=0 for n in bounds.values()):raise ValueError('Invalid native actor bounds')
        if not _vector_valid(actual.get('bbox_center')) or actual.get('geometry_source')!='native_actor_bounding_box':raise ValueError('Native actor geometry provenance is incomplete')
        expected_motion='timed_position_replay_no_physics' if spec['controller']['kind']=='timed_replay' else 'native_carla_physics'
        if actual.get('motion_fidelity')!=expected_motion:raise ValueError('Actor motion fidelity differs from its controller mode')
    if len(set(native_ids.values()))!=len(native_ids):raise ValueError('Native actor IDs are duplicated')
    if [s.get('id') for s in scene.get('sensors',[])]!=[s['id'] for s in scenario['sensors']]:raise ValueError('Native sensor denominator differs')
    for spec,actual in zip(scenario['sensors'],scene['sensors']):
        if any(actual.get(k)!=v for k,v in spec.items()):raise ValueError('Native sensor settings differ from frozen scenario')
    camera_records=[];clock_start=None
    for index,frame in enumerate(frames):
        if (type(frame.get('index')) is not int or frame['index']!=index or not _close(frame.get('time_s'),(index+1)*dt,1e-6)
            or type(frame.get('frame')) is not int or type(frame.get('input_frame')) is not int or frame['input_frame']<0
            or frame['frame']!=frame['input_frame']+1):raise ValueError('Native city response/tick alignment mismatch')
        if not _finite(frame.get('simulation_time_s')) or frame['simulation_time_s']<0:raise ValueError('Native world clock is invalid')
        if clock_start is None:clock_start=frame['simulation_time_s']-dt
        if not _close(frame['simulation_time_s'],clock_start+(index+1)*dt):raise ValueError('Native world clock interval differs from frozen timestep')
        if index and frame['input_frame']!=frames[index-1]['frame']:raise ValueError('Native city frame continuity broken')
        if [a.get('id') for a in frame['actors']]!=identities:raise ValueError('Native city frame actor identities differ')
        for spec,actor in zip(scenario['actors'],frame['actors']):
            if not _vector_valid(actor,('x','y','z','heading_rad','speed_mps','yaw_rate_rps')) or actor['speed_mps']<0:raise ValueError('Native city telemetry is nonfinite or invalid')
            if not _vector_valid(actor.get('velocity')) or not _vector_valid(actor.get('acceleration')):raise ValueError('Native city velocity/acceleration is invalid')
            if not _close(actor['speed_mps'],math.sqrt(sum(v*v for v in actor['velocity'].values()))):raise ValueError('Native speed differs from retained velocity')
            control=actor.get('control');mode=spec['controller']['kind']
            if mode in {'static','timed_replay'}:
                if control is not None:raise ValueError('Replay/static actor has an unexpected physical control owner')
            elif spec['kind']=='vehicle':
                if (not isinstance(control,dict) or set(control)!={'throttle','brake','steer'} or any(not _finite(v) for v in control.values())
                    or not 0<=control['throttle']<=1 or not 0<=control['brake']<=1 or not -1<=control['steer']<=1
                    or (control['throttle']>1e-6 and control['brake']>1e-6)):
                    raise ValueError('Native city control exceeds physical actuator domain')
            else:
                if (not isinstance(control,dict) or set(control)!={'speed_mps','direction'} or not _finite(control.get('speed_mps'))
                    or not 0<=control['speed_mps']<=12 or not isinstance(control.get('direction'),list) or len(control['direction'])!=3
                    or any(not _finite(v) for v in control['direction']) or sum(v*v for v in control['direction'])>1.00001):
                    raise ValueError('Native walker command is invalid')
        for record in frame.get('cameras',[]):
            sensor=next((s for s in scenario['sensors'] if s['id']==record['sensor_id']),None)
            if sensor is None or record['index']!=index or record['frame']!=frame['frame'] or record['width']!=sensor['width'] or record['height']!=sensor['height']:
                raise ValueError('Native city camera sample identity mismatch')
            if (record['size_bytes']!=sensor['width']*sensor['height']*4 or not _close(record.get('simulation_time_s'),frame['simulation_time_s'])
                or not _close(record.get('time_s'),frame['time_s']) or record.get('encoding')!='bgra8'):
                raise ValueError('Native city sensor/clock/encoding mismatch')
            if (not re.fullmatch(r'city-camera-[0-9]{4}\.zip',record.get('archive',''))
                or record.get('member')!=f'{sensor["id"]}/{index:06d}.bgra'
                or not re.fullmatch(r'[0-9a-f]{64}',record.get('sha256',''))
                or not _vector_valid(record.get('world_pose'),('x','y','z','heading_rad'))):
                raise ValueError('Native camera archive identity or world pose is invalid')
            camera_records.append(record)
    if scene.get('camera_records')!=camera_records:raise ValueError('Native city camera record denominator differs')
    for sensor in scenario['sensors']:
        expected_indices=list(range(0,expected,round(sensor['period_s']/dt)))
        if [r['index'] for r in camera_records if r['sensor_id']==sensor['id']]!=expected_indices:raise ValueError('Native camera retained sampling period differs from configuration')
    if scene.get('routes')!={a['id']:a['route'] for a in scenario['actors']}:raise ValueError('Native intended routes differ from frozen plan')
    if not isinstance(scene.get('events'),list):raise ValueError('Native event denominator is missing')
    for event in scene['events']:
        actors=event.get('actor_ids',[]);number=event.get('frame');details=event.get('details',{})
        if (event.get('kind')!='native_contact' or event.get('provenance')!='native_carla_collision_sensor'
            or not isinstance(actors,list) or not 1<=len(actors)<=2 or len(set(actors))!=len(actors) or any(a not in identities for a in actors)
            or type(number) is not int or not frames[0]['frame']<=number<=frames[-1]['frame']
            or not _close(event.get('time_s'),(number-frames[0]['input_frame'])*dt)
            or not _vector_valid(details.get('normal_impulse')) or type(details.get('other_native_id')) is not int
            or details['other_native_id']<0 or not isinstance(details.get('other_type'),str)):
            raise ValueError('Native contact event identity, clock or provenance is invalid')
        if len(actors)==2 and details['other_native_id']!=native_ids[actors[1]]:raise ValueError('Native contact actor mapping differs')
    _validate_reactive_evidence(scene,scenario,snapshot)
    measured=_measured_metrics(scene,scenario,verify_diagnostics=True)
    if [row.get('actor_id') for row in scene.get('metrics',[])]!=identities:raise ValueError('Native metric actor denominator differs')
    for actual,wanted in zip(scene['metrics'],measured):
        if set(actual)!=set(wanted):raise ValueError('Native metric contract differs from recomputed diagnostics')
        for key,value in wanted.items():
            if key=='actor_id' or value is None or type(value) is bool:
                if actual[key]!=value or type(actual[key]) is not type(value):raise ValueError('Native metric differs from response evidence')
            elif not _close(actual[key],value):raise ValueError('Native metric differs from response evidence: '+key)
    return scene

class CityExperimentHandler:
    def __init__(self,*,database=None):
        self.db=database;self.target=os.environ.get('AXIOM_CARLA_CITY_SSH','');self.python=os.environ.get('AXIOM_CARLA_CITY_PYTHON','')
        self.script=os.environ.get('AXIOM_CARLA_CITY_SCRIPT','');self.directory=os.environ.get('AXIOM_CARLA_CITY_DIR','')
        self.spool=os.environ.get('AXIOM_CARLA_CITY_SPOOL','');self.owner=os.environ.get('AXIOM_CARLA_CITY_OWNER','')
        self.port=int(os.environ.get('AXIOM_CARLA_CITY_PORT','2100'));self.autoware=os.environ.get('AXIOM_CARLA_CITY_AUTOWARE_PROFILE','')
    def metadata(self):
        return ExperimentHandlerMetadata(id='carla-city-object',version=VERSION,kinds=['object_sim'],
            status='available' if all((self.db,self.target,self.python,self.script,self.directory,self.spool,self.owner)) else 'not_configured',
            fidelity='scenario_dependent_native_carla_motion',resource_id='carla-city-'+str(self.port),cancellation_supported=True,
            description='Pinned native CARLA cities/assets, physical route control, separate timed replay, aligned RGB and explicit GT Autoware profile.')
    def config(self):
        if self.metadata().status!='available':raise ValueError('Dedicated native CARLA city transport is not configured')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+',self.target):raise ValueError('Invalid operator SSH target')
        if self.port==2000 or not 1024<=self.port<=65535:raise ValueError('A dedicated CARLA city endpoint is required')
        if any(not p.startswith('/') or '\n' in p or '\0' in p for p in (self.python,self.script,self.directory,self.spool,self.owner)):raise ValueError('City operator paths must be absolute')
    def command(self,args):
        return ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',self.target,shlex.join([self.python,self.script,'--port',str(self.port),'--owner',self.owner,'--spool',self.spool,*args])]
    def transport_hash(self):return sha(canonical([self.target,self.python,self.script,self.directory,self.spool,self.owner,self.port,self.autoware]))
    def native_probe(self):
        self.config();result=subprocess.run(self.command(['--probe']),capture_output=True,timeout=60)
        if result.returncode:raise ValueError('Native city endpoint probe failed: '+result.stderr.decode(errors='replace')[-1500:])
        if len(result.stdout)>1024**2:raise ValueError('Native city probe too large')
        return json.loads(result.stdout)
    def _owned_dispatch_active(self):
        """Only defer preview occupancy while this transport holds a live job.

        Preview does not acquire the engine. The native runner still takes its
        exclusive lease and requires an empty world immediately before capture.
        """
        from sqlalchemy import select
        from ade2e.job_models import EngineResourceRow, JobRow
        resource_id=self.metadata().resource_id
        with self.db.read_session() as session:
            resource=session.get(EngineResourceRow,resource_id)
            if (resource is None or resource.state!='reserved' or not resource.job_id or not resource.owner_token):return False
            row=session.scalar(select(JobRow).where(JobRow.id==resource.job_id))
            if (row is None or row.kind!='experiment' or row.status!='running' or not row.dispatched_at
                    or row.outcome_uncertain or not row.resource_required or row.resource_key!=resource_id
                    or row.owner_token!=resource.owner_token or row.adapter_id!=self.metadata().id
                    or row.adapter_version!=VERSION):return False
            try:
                live=datetime.fromisoformat(row.lease_expires_at)>datetime.now(timezone.utc)
                same_transport=row.prepared['input']['operator_transport_hash']==self.transport_hash()
            except (ValueError,TypeError,KeyError):return False
            return live and same_transport
    def prepare(self,spec):
        if spec.kind!='object_sim' or spec.comparison_kind!='single' or any(x is not None for x in (spec.model,spec.sensor,spec.evaluator)):
            raise ValueError('City v1 uses one scenario-pinned controller/sensor/acceptance configuration')
        if set(spec.input)!={'scenario_id','scenario_revision','configuration_hash'}:raise ValueError('City input pins scenario_id, scenario_revision and configuration_hash')
        with self.db.read_session() as session:
            original=get_scenario(session,spec.input['scenario_id'],spec.input['scenario_revision'])
            if original['project_id']!=spec.project_id:raise ValueError('Scenario belongs to another project')
            scenario=parse_scenario(original['scenario']);document=scenario.model_dump(mode='json')
            if not isinstance(scenario,ScenarioCity):raise ValueError('Native city requires city schema /2 or /3')
            if scenario.observers:
                raise ValueError('Legacy scenario observers are unsupported by native city execution; use frozen city acceptance and experiment verification metrics')
            if any(a.controller.kind=='autoware' and not a.controller.obey_signals for a in scenario.actors):
                raise ValueError('Autoware city bridge always obeys signals; obey_signals=false is unsupported')
            if digest(document)!=spec.input['configuration_hash'] or scenario.seed!=spec.seed:raise ValueError('Scenario configuration/seed differs from frozen request')
            validate_city_catalogs(session,spec.project_id,scenario)
            native_map=catalog_reference(session,'map',spec.project_id,scenario.map)
            assets={a.id:catalog_reference(session,'asset',spec.project_id,a.asset)['definition'] for a in scenario.actors}
            controllers={}
            for actor in scenario.actors:
                if actor.controller.ref:
                    item=catalog_reference(session,'controller',spec.project_id,actor.controller.ref)
                    if item['definition']['status']!='available':raise ValueError('Requested controller is not configured: '+actor.id)
                    controllers[actor.id]=item
        validate_routes(document,native_map['definition'])
        ticks=round(scenario.duration_s/scenario.timestep_s)
        if abs(ticks*scenario.timestep_s-scenario.duration_s)>1e-6 or ticks>2400:raise ValueError('Native city requires an integral bounded timestep count')
        camera_bytes=sum(math.ceil(ticks/(s.period_s/scenario.timestep_s))*s.width*s.height*4 for s in scenario.sensors)
        if camera_bytes>city_native.MAX_BYTES-128*1024**2:raise ValueError('Requested complete sensor evidence exceeds 2 GiB episode budget; shorten the scenario or reduce capture rate/resolution')
        probe=self.native_probe()
        if probe.get('native_script_sha256')!=file_sha(city_native.__file__) or probe.get('control_script_sha256')!=file_sha(city_control.__file__) or probe.get('maneuver_script_sha256')!=file_sha(city_maneuvers.__file__) or probe.get('adapter_version')!=VERSION:
            raise ValueError('Remote city runner/controller differs from installed version')
        if probe.get('dynamic_actor_ids') and not self._owned_dispatch_active():
            raise ValueError('Dedicated native city endpoint contains foreign actors')
        if probe['server_version']!=native_map['definition']['server_version'] or (not (native_map['definition'].get('generated') or native_map['definition'].get('external')) and native_map['definition']['native_name'].split('/')[-1] not in probe['available_maps']):
            raise ValueError('Registered map does not match available native server build')
        available=set(probe['vehicle_blueprints']+probe['walker_blueprints']+probe['static_blueprints'])
        if any(asset['blueprint_id'] not in available for asset in assets.values()):raise ValueError('Registered asset is absent from native server')
        for actor_id,controller in controllers.items():
            validate_controller_revision(controller['definition'])
        if any(a.controller.kind=='autoware' and assets[a.id]['blueprint_id']!='vehicle.toyota.prius' for a in scenario.actors):
            raise ValueError('Only Prius has a pinned Autoware control calibration')
        source={'scenario_id':original['id'],'scenario_revision':original['revision'],'scenario_manifest_hash':original['manifest_hash'],
            'configuration_hash':digest(document),'map_sha256':native_map['sha256'],'seed':spec.seed}
        snapshot={'schema_version':'carla-city-input/1','scenario':document,'source':source,'assets':assets,'controllers':controllers,
            'execution_fidelity':scenario_fidelity(document),
            'map':{'native_name':native_map['definition']['native_name'],'opendrive_sha256':native_map['definition']['opendrive']['sha256']},
            'native_script_sha256':file_sha(city_native.__file__),'control_script_sha256':file_sha(city_control.__file__),
            'maneuver_script_sha256':file_sha(city_maneuvers.__file__),
            'adapter_script_sha256':file_sha(__file__),'operator_transport_hash':self.transport_hash(),'server_version':probe['server_version'],
            'workload':{'ticks':ticks,'actors':len(document['actors']),'camera_frames':sum(math.ceil(ticks/(s.period_s/scenario.timestep_s)) for s in scenario.sensors),
                'camera_bytes':camera_bytes,'chunk_bytes':city_native.CHUNK_BYTES,'exclusive_endpoint':True,
                'empty_world_check':'exclusive_native_dispatch','physics_warmup_ticks':20}}
        if native_map['definition'].get('external'):
            snapshot['external_map_provenance']=native_map['definition']['external']
        if native_map['definition'].get('generated') or native_map['definition'].get('external') or any(a.controller.kind=='reactive' for a in scenario.actors):
            raw_map=canonical(native_map['definition'])
            if len(raw_map)>64*1024**2:raise ValueError('Frozen map definition exceeds 64 MiB')
            snapshot['map_definition_pack']={'encoding':'zlib+base64','sha256':sha(raw_map),
                'data':base64.b64encode(zlib.compress(raw_map,9)).decode('ascii')}
        if any(a.controller.kind=='reactive' for a in scenario.actors):
            planner_hash=file_sha(reactive_planner.__file__)
            if probe.get('planner_script_sha256')!=planner_hash:
                raise ValueError('Remote reactive planner differs from installed version')
            snapshot['planner_script_sha256']=planner_hash
            reactive_planner.ReactivePlanner(document['actors'],native_map['definition'],fixed_delta_seconds=scenario.timestep_s)
        if any(a.controller.kind=='autoware' for a in scenario.actors):
            if not self.autoware:raise ValueError('Autoware GT planning/control is not configured for this native endpoint')
            profile=json.loads(Path(self.autoware).read_bytes());snapshot['autoware']=profile['maps'].get(snapshot['map']['native_name'].split('/')[-1])
            if not snapshot['autoware']:raise ValueError('Autoware Lanelet2 alignment is not registered for this CARLA map')
            snapshot['autoware_profile_sha256']=file_sha(self.autoware)
        return snapshot
    def execute(self,snapshot,work_dir,should_cancel):
        self.config();work=Path(work_dir);work.mkdir(parents=True,exist_ok=True)
        if snapshot.get('schema_version')!='carla-city-input/1' or snapshot.get('operator_transport_hash')!=self.transport_hash():raise ValueError('City transport changed after preview')
        for key,path in [('native_script_sha256',city_native.__file__),('control_script_sha256',city_control.__file__),('maneuver_script_sha256',city_maneuvers.__file__),('adapter_script_sha256',__file__)]:
            if snapshot[key]!=file_sha(path):raise ValueError('City runner or controller changed after preview; create a new preview')
        if snapshot.get('planner_script_sha256') and snapshot['planner_script_sha256']!=file_sha(reactive_planner.__file__):
            raise ValueError('Reactive planner changed after preview; create a new preview')
        if should_cancel():raise AdapterCancelled('Cancelled before native city dispatch')
        external_id='city_'+uuid.uuid4().hex;remote_dir=self.directory.rstrip('/')+'/'+external_id;arguments=['--output',remote_dir]
        if 'autoware' in snapshot:
            if file_sha(self.autoware)!=snapshot['autoware_profile_sha256']:raise ValueError('Autoware profile changed after preview')
            profile=json.loads(Path(self.autoware).read_bytes());arguments+=['--autoware-command',profile['remote_command_file']]
        journal={'schema_version':'external-city-dispatch/1','external_execution_id':external_id,'status':'dispatching','remote_directory':remote_dir,
            'configuration_hash':snapshot['source']['configuration_hash'],'cleanup_status':'unknown'}
        def record(): (work/'external-dispatch.json').write_bytes(canonical(journal))
        record();log=(work/'native.stderr.log').open('wb');output=(work/'native.stdout.json').open('wb')
        process=subprocess.Popen(self.command(arguments),stdin=subprocess.PIPE,stdout=output,stderr=log)
        process.stdin.write(canonical(snapshot));process.stdin.close();started=time.monotonic();cancel_sent=False
        while process.poll() is None:
            if should_cancel() and not cancel_sent:
                marker=remote_dir+'/cancel.request'
                result=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',self.target,shlex.join(['touch',marker])],capture_output=True,timeout=20)
                if result.returncode==0:cancel_sent=True
            if time.monotonic()-started>max(300,snapshot['scenario']['duration_s']*15+90):
                process.terminate();process.wait(timeout=15);journal['status']='outcome_unknown';record();raise RuntimeError('City native timeout; external cleanup must be inspected')
            time.sleep(.15)
        output.close();log.close()
        if process.returncode:
            journal['status']='failed_or_cancelled';record()
            # Preserve the durable remote cleanup status even on failure/cancel.
            result=subprocess.run(['scp','-q','-o','BatchMode=yes',self.target+':'+remote_dir+'/state.json',str(work/'native-state.json')],capture_output=True,timeout=30)
            if not result.returncode:
                state=json.loads((work/'native-state.json').read_bytes());journal.update(cleanup_status=state.get('cleanup_status'),native_error=state.get('error'));record()
                if state.get('status')=='cancelled' and state.get('cleanup_status')=='confirmed':raise AdapterCancelled('City execution cancelled; owned actors cleaned up')
            raise RuntimeError('Native city execution failed: '+(work/'native.stderr.log').read_text(errors='replace')[-2500:])
        receipt=json.loads((work/'native.stdout.json').read_bytes())
        if receipt.get('status')!='completed' or receipt.get('cleanup_status')!='confirmed' or receipt.get('external_execution_id')!=external_id or receipt.get('source')!=snapshot['source']:
            raise ValueError('Native city receipt correlation or cleanup is incomplete')
        entries=receipt.get('artifacts',[])
        if (not 2<=len(entries)<=64 or any(type(e.get('size_bytes')) is not int or not 0<e['size_bytes']<=city_native.MAX_BYTES or
            not re.fullmatch('[a-f0-9]{64}',e.get('sha256','')) for e in entries) or
            sum(e['size_bytes'] for e in entries)>city_native.MAX_BYTES+128*1024**2):raise ValueError('Native city artifact denominator/budget invalid')
        outputs=[];names=set()
        for entry in entries:
            name=entry['name'];path=PurePosixPath(name)
            if path.name!=name or name in names or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,100}',name):raise ValueError('Unsafe/duplicate native city artifact name')
            names.add(name);local=work/name
            result=subprocess.run(['scp','-q','-o','BatchMode=yes',self.target+':'+remote_dir+'/'+name,str(local)],capture_output=True,timeout=180)
            if result.returncode or local.stat().st_size!=entry['size_bytes'] or file_sha(local)!=entry['sha256']:raise ValueError('Native city artifact transfer/hash mismatch')
            outputs.append(ExperimentOutputFile(local,name,entry['media_type']))
        if not {'city-scene.json','map.xodr'}.issubset(names):raise ValueError('Native city original scene/map missing')
        scene=validate_scene(json.loads((work/'city-scene.json').read_bytes()),snapshot)
        if file_sha(work/'map.xodr')!=scene['source']['xodr_sha256']:raise ValueError('Transferred map hash differs from original native map')
        archive_names={r['archive'] for r in scene['camera_records']}
        if archive_names!={n for n in names if n.endswith('.zip')}:raise ValueError('Unreferenced or missing camera archive')
        for name in archive_names:
            records=[r for r in scene['camera_records'] if r['archive']==name]
            validate_camera_archive(work/name,records,network=snapshot.get('hil',{}).get('runtime',{}).get('kind')=='network_reference')
        # Do not modify native city-scene bytes: evaluation and sampled display
        # geometry are independent derived artifacts with their source hashes.
        evaluation=evaluate(scene,snapshot['scenario']);evaluation['source_scene_sha256']=file_sha(work/'city-scene.json')
        eval_path=work/'city-evaluation.json';eval_path.write_bytes(canonical(evaluation));outputs.append(ExperimentOutputFile(eval_path,eval_path.name,'application/json'))
        journal.update(status='completed',cleanup_status='confirmed');record()
        outputs.append(ExperimentOutputFile(work/'external-dispatch.json','external-dispatch.json','application/json'))
        outputs.append(ExperimentOutputFile(work/'native.stdout.json','native-receipt.json','application/json'))
        if should_cancel():raise AdapterCancelled('Native city capture completed and cleaned up; cancellation prevents publication')
        return ExperimentExecution(summary={'schema_version':'carla-city-result/1','scene_artifact':'city-scene.json','scene_sha256':file_sha(work/'city-scene.json'),
            'map_artifact':'map.xodr','map_sha256':file_sha(work/'map.xodr'),'evaluation_artifact':eval_path.name,'frame_count':len(scene['frames']),
            'actor_count':len(scene['actors']),'camera_frame_count':len(scene['camera_records']),'drop_count':0,'metrics':scene['metrics'],
            'external_execution_id':external_id,'source':snapshot['source'],'execution_status':'completed','cleanup_status':'confirmed',
            'evaluation_status':'evaluated' if evaluation['verdict']!='not_evaluated' else 'not_evaluated','verification_verdict':evaluation['verdict'],
            'evaluation':evaluation,'physical_sensor_validation':False,'limitations':scene['limitations']},artifacts=outputs,fidelity=scene['fidelity'])


def create_city_handler(*,database=None):return CityExperimentHandler(database=database)
