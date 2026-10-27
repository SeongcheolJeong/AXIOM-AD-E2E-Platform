"""Reference planning replay with causal vehicle-state feedback.

The bicycle is a declared planar reference model. Retained world object tracks
are nonreactive; logged camera pixels are never treated as a new camera view.
"""
import copy
import math
from pathlib import Path

from .adapters import AdapterCancelled
from .replay_spec_schemas import ReplaySpecCreate
from .service import canonical_bytes, digest

KERNEL_VERSION = 'reference-planning-replay/1'


def angle(value):
    return math.atan2(math.sin(value), math.cos(value))


def world_objects(frame):
    batch = frame['objects']
    if batch is None or frame['object_drop']:
        return None
    objects = copy.deepcopy(batch['objects'])
    if batch['coordinate_frame'] == 'world':
        return objects
    reference = frame['object_reference_ego']
    if reference is None:
        raise ValueError('Relative object input lacks its original world ego pose')
    c, s = math.cos(reference['yaw_rad']), math.sin(reference['yaw_rad'])
    for obj in objects:
        x, y = obj['x_m'], obj['y_m']
        obj['x_m'] = reference['x_m'] + c*x-s*y
        obj['y_m'] = reference['y_m'] + s*x+c*y
        obj['yaw_rad'] = angle(reference['yaw_rad']+obj['yaw_rad'])
        if obj['vx_mps'] is not None:
            vx, vy = obj['vx_mps'], obj['vy_mps']
            obj['vx_mps'], obj['vy_mps'] = c*vx-s*vy, s*vx+c*vy
    return objects


def observe_objects(ego, objects):
    if objects is None:
        return None
    c, s = math.cos(ego['yaw_rad']), math.sin(ego['yaw_rad'])
    result = []
    for obj in objects:
        dx, dy = obj['x_m']-ego['x_m'], obj['y_m']-ego['y_m']
        value = copy.deepcopy(obj)
        value['x_m'], value['y_m'] = c*dx+s*dy, -s*dx+c*dy
        value['yaw_rad'] = angle(obj['yaw_rad']-ego['yaw_rad'])
        if obj['vx_mps'] is not None:
            vx, vy = obj['vx_mps'], obj['vy_mps']
            value['vx_mps'], value['vy_mps'] = c*vx+s*vy, -s*vx+c*vy
        result.append(value)
    return result


def _corners(body):
    c, s = math.cos(body['yaw_rad']), math.sin(body['yaw_rad'])
    halfx, halfy = body['length_m']/2, body['width_m']/2
    return [(body['x_m']+c*x-s*y, body['y_m']+s*x+c*y) for x,y in
        ((halfx, halfy), (halfx, -halfy), (-halfx, -halfy), (-halfx, halfy))]


def _collides(a, b):
    ac, bc = _corners(a), _corners(b)
    for body in (a,b):
        c,s = math.cos(body['yaw_rad']), math.sin(body['yaw_rad'])
        for axis in ((c,s),(-s,c)):
            aa = [x*axis[0]+y*axis[1] for x,y in ac]
            bb = [x*axis[0]+y*axis[1] for x,y in bc]
            if max(aa) < min(bb) or max(bb) < min(aa):
                return False
    return True


def _segment_distance(p, a, b):
    vx,vy = b[0]-a[0],b[1]-a[1]
    denominator = vx*vx+vy*vy
    t = max(0., min(1., ((p[0]-a[0])*vx+(p[1]-a[1])*vy)/denominator)) if denominator else 0.
    return math.hypot(p[0]-(a[0]+t*vx),p[1]-(a[1]+t*vy))


def _distance(a,b):
    if _collides(a,b):
        return 0.
    ac,bc = _corners(a),_corners(b)
    return min(_segment_distance(p, poly[i], poly[(i+1)%4])
        for points,poly in ((ac,bc),(bc,ac)) for p in points for i in range(4))


def _ttc(ego, obj):
    """Fixed-yaw swept rectangle TTC for constant declared planar velocities.

    The separating-axis time intervals give rectangle overlap, not center-point
    collision. Turning/accelerating vehicles and future object responses remain
    outside this diagnostic model.
    """
    if obj['vx_mps'] is None:
        return None
    evx = ego['speed_mps']*math.cos(ego['yaw_rad'])
    evy = ego['speed_mps']*math.sin(ego['yaw_rad'])
    rv = (obj['vx_mps']-evx, obj['vy_mps']-evy)
    first,last = 0., math.inf
    ac,bc = _corners(ego),_corners(obj)
    for body in (ego,obj):
        c,s = math.cos(body['yaw_rad']),math.sin(body['yaw_rad'])
        for axis in ((c,s),(-s,c)):
            aa = [x*axis[0]+y*axis[1] for x,y in ac]
            bb = [x*axis[0]+y*axis[1] for x,y in bc]
            speed = rv[0]*axis[0]+rv[1]*axis[1]
            lower,upper = min(aa)-max(bb),max(aa)-min(bb)
            if abs(speed) < 1e-12:
                if lower > 0 or upper < 0:
                    return math.inf
            else:
                t1,t2 = sorted((lower/speed,upper/speed))
                first,last = max(first,t1),min(last,t2)
                if last < first:
                    return math.inf
    return first if last >= first else math.inf


def control(ego, observed, reference, config, dynamics):
    if observed is None:
        return {'acceleration_mps2': -config.max_deceleration_mps2, 'steering_rad': 0.,
            'target_speed_mps': 0., 'decision': 'unavailable_objects_stop'}
    target = config.cruise_speed_mps
    decision = 'reference_cruise'
    for obj in observed:
        # Conservative lateral projection for declared oriented bounding boxes.
        lateral_extent = abs(math.sin(obj['yaw_rad']))*obj['length_m']/2+abs(math.cos(obj['yaw_rad']))*obj['width_m']/2
        if abs(obj['y_m']) > lateral_extent+ego['width_m']/2 or obj['x_m'] < -obj['length_m']/2:
            continue
        gap = obj['x_m']-ego['length_m']/2-obj['length_m']/2-config.stop_buffer_m
        candidate = max(0., gap/config.time_headway_s)
        if candidate < target:
            target,decision = candidate,'reference_headway'
    acceleration = max(-config.max_deceleration_mps2,min(config.max_acceleration_mps2,target-ego['speed_mps']))
    steering = config.heading_gain*angle(reference['yaw_rad']-ego['yaw_rad'])+config.steering_bias_rad
    steering = max(-dynamics.max_steering_rad,min(dynamics.max_steering_rad,steering))
    return {'acceleration_mps2': acceleration, 'steering_rad': steering,
        'target_speed_mps': target, 'decision': decision}


def advance(ego, command, dt, dynamics):
    if not 0 < dt <= 1:
        raise ValueError('Reference bicycle step must be greater than zero and at most one second')
    result = copy.deepcopy(ego)
    v2 = max(0.,min(100.,ego['speed_mps']+command['acceleration_mps2']*dt))
    speed = (ego['speed_mps']+v2)/2
    yaw_change = speed/dynamics.wheelbase_m*math.tan(command['steering_rad'])*dt
    midyaw = ego['yaw_rad']+yaw_change/2
    result['x_m'] += speed*math.cos(midyaw)*dt
    result['y_m'] += speed*math.sin(midyaw)*dt
    result['yaw_rad'] = angle(ego['yaw_rad']+yaw_change)
    result['speed_mps'] = v2
    result['source'] = 'reference planar kinematic bicycle feedback; not measured vehicle dynamics'
    return result


def _value(value, unit, status='evaluated', reason=None):
    return {'status': status, 'value': value, 'unit': unit, 'reason': reason}


def run_planning_replay(sequence_snapshot, request, *, seed=0, should_cancel=lambda:False):
    spec = ReplaySpecCreate.model_validate(request)
    frames = sequence_snapshot['frames']
    if not frames:
        raise ValueError('Replay requires at least one frozen frame')
    if spec.mode == 'planning_closed_loop' and len(frames) < 2:
        raise ValueError('Closed-loop feedback requires at least two input times')
    if spec.mode == 'planning_closed_loop' and any(frame['object_drop'] for frame in frames):
        raise ValueError('Closed loop requires complete causal object observations at every selected step')
    ticks,ego,last_acceleration,last_time,last_speed = [],copy.deepcopy(frames[0]['ego']),None,None,None
    counts = {name:{'evaluated':0,'unevaluated':0} for name in spec.observers}
    previous_hash = digest({'sequence_hash':digest(sequence_snapshot),'request':request,'seed':seed,'kernel':KERNEL_VERSION})
    for index,frame in enumerate(frames):
        if should_cancel():
            raise AdapterCancelled('Planning replay cancellation acknowledged before next reference step')
        if spec.mode != 'planning_closed_loop':
            ego = copy.deepcopy(frame['ego'])
        ego['timestamp_ns'] = frame['at_ns']
        objects = world_objects(frame)
        observed = observe_objects(ego,objects)
        command = control(ego,observed,frame['ego'],spec.controller,spec.dynamics) if spec.mode != 'original_replay' else None
        next_ego = None
        if command and index+1 < len(frames):
            dt = (int(frames[index+1]['at_ns'])-int(frame['at_ns']))/1e9
            next_ego = advance(ego,command,dt,spec.dynamics)
            next_ego['timestamp_ns'] = frames[index+1]['at_ns']
        now = int(frame['at_ns'])
        dt = (now-last_time)/1e9 if last_time is not None else None
        acceleration = (ego['speed_mps']-last_speed)/dt if dt is not None else None
        jerk = (acceleration-last_acceleration)/dt if acceleration is not None and last_acceleration is not None else None
        collided = [obj['id'] for obj in objects if _collides(ego,obj)] if objects is not None else None
        distances = [_distance(ego,obj) for obj in objects] if objects is not None else None
        ttcs = [_ttc(ego,obj) for obj in objects] if objects is not None else None
        metrics = {}
        for name in spec.observers:
            if name == 'collision':
                metrics[name] = _value(bool(collided) if collided is not None else None,'boolean',
                    'evaluated' if collided is not None else 'unevaluated', frame['object_drop'])
            elif name == 'distance':
                metrics[name] = _value(min(distances) if distances else None,'m',
                    'evaluated' if distances else 'unevaluated','no_objects' if distances == [] else frame['object_drop'])
            elif name == 'ttc':
                if ttcs is None or any(value is None for value in ttcs):
                    metrics[name] = _value(None,'s','unevaluated',frame['object_drop'] or 'object_velocity_missing')
                elif not ttcs:
                    metrics[name] = _value(None,'s','unevaluated','no_objects')
                else:
                    finite = [value for value in ttcs if math.isfinite(value)]
                    metrics[name] = _value(min(finite) if finite else None,'s','evaluated','no_constant_velocity_intersection' if not finite else None)
            elif name == 'speed':
                metrics[name] = _value(ego['speed_mps'],'m/s')
            elif name == 'acceleration':
                metrics[name] = _value(acceleration,'m/s2','evaluated' if acceleration is not None else 'unevaluated','initial_step' if acceleration is None else None)
            elif name == 'jerk':
                metrics[name] = _value(jerk,'m/s3','evaluated' if jerk is not None else 'unevaluated','insufficient_previous_steps' if jerk is None else None)
            elif name == 'trajectory_error':
                metrics[name] = _value(math.hypot(ego['x_m']-frame['ego']['x_m'],ego['y_m']-frame['ego']['y_m']),'m')
            elif name == 'goal_arrival':
                metrics[name] = _value(math.hypot(ego['x_m']-spec.goal.x_m,ego['y_m']-spec.goal.y_m)<=spec.goal.tolerance_m
                    if spec.goal else None,'boolean','evaluated' if spec.goal else 'unevaluated','goal_not_configured' if not spec.goal else None)
            elif name == 'lane_departure':
                metrics[name] = _value(None,'boolean','unevaluated','no_retained_map_lane_geometry')
            counts[name][metrics[name]['status']] += 1
        tick = {'index':index,'at_ns':frame['at_ns'],'ego':copy.deepcopy(ego),'logged_ego':frame['ego'],
            'world_objects':objects,'observation_objects':observed,'object_age_ns':frame['object_age_ns'],
            'object_drop':frame['object_drop'],'source_ego':frame['ego_source'],'source_objects':frame['object_source'],
            'source_sensors':frame['sensors'],'control':command,'next_ego':next_ego,
            'control_application':'applied_to_next_reference_state' if spec.mode == 'planning_closed_loop' and next_ego else
                'predicted_not_applied_open_loop' if command else 'not_applied_original_replay',
            'colliding_object_ids':collided,'metrics':metrics,'previous_tick_hash':previous_hash}
        previous_hash = digest(tick)
        tick['sha256'] = previous_hash
        ticks.append(tick)
        last_time,last_speed,last_acceleration = now,ego['speed_mps'],acceleration
        if spec.mode == 'planning_closed_loop' and next_ego:
            ego = next_ego
    summary = {'schema_version':'planning-replay-result/1','mode':spec.mode,'kernel_version':KERNEL_VERSION,
        'seed':seed,'randomness':'deterministic_reference_kernel_no_random_draws','frame_count':len(frames),
        'sequence_sha256':digest(sequence_snapshot),'controller':spec.controller.model_dump(mode='json'),
        'dynamics':spec.dynamics.model_dump(mode='json'),'observer_counts':counts,
        'collision_steps':sum(bool(tick['colliding_object_ids']) for tick in ticks),
        'unevaluated_object_steps':sum(tick['object_drop'] is not None for tick in ticks),
        'max_trajectory_error_m':max(math.hypot(tick['ego']['x_m']-tick['logged_ego']['x_m'],tick['ego']['y_m']-tick['logged_ego']['y_m']) for tick in ticks),
        'verification_verdict':'diagnostic_not_safety_validation','surrounding_actors':'retained_nonreactive_world_tracks',
        'camera_feedback':'not_supported_nonreactive_source_pixels','final_trace_hash':previous_hash,
        'fidelity':'authored_planar_reference' if sequence_snapshot['request']['source_type']=='authored_reference' else 'recorded_objects_reference_vehicle_feedback',
        'limitations':sequence_snapshot['limitations']+['Discrete observation collision; no continuous-time collision guarantee.',
            'TTC uses fixed-yaw constant declared velocities, not a future learned prediction.',
            'Reference controller and bicycle do not establish production driving-policy or vehicle dynamics accuracy.']}
    if sequence_snapshot['schema_version'] == 'scene-sequence/2':
        scope = sequence_snapshot.get('source_use_scope', {'use_scope':'unknown','commercial_use':None})
        summary['source_use_scope'] = {key:value for key,value in scope.items() if key != 'lineage'}
        if 'lineage' in scope:
            summary['source_lineage_sha256'] = digest(scope['lineage'])
        summary['input_geometry_scope'] = 'declared_planar_reference_footprint_not_measured_vehicle_dimensions'
    return summary,ticks


class PlanningReplayHandler:
    def __init__(self,db):
        self.db = db

    def metadata(self):
        from .experiment_runtime import ExperimentHandlerMetadata
        return ExperimentHandlerMetadata(id='reference-planning-replay',version='1',kinds=['planning_replay'],
            status='available',fidelity='planar_reference_recorded_or_authored_objects',resource_id=None,
            cancellation_supported=True,description='Original replay, open-loop reference planning and reference bicycle feedback over retained nonreactive world objects')

    def prepare(self,spec):
        from .replay_specs import get_replay_spec, replay_preflight
        if spec.comparison_kind != 'single':
            raise ValueError('Planning replay A/B requires two explicitly pinned experiments; this handler accepts a single replay spec')
        if set(spec.input) != {'replay_spec_id'}:
            raise ValueError('Planning experiment input must contain only replay_spec_id')
        if spec.model or spec.sensor:
            raise ValueError('Reference planning replay does not execute camera, learned model or sensor replacements')
        if spec.evaluator and spec.evaluator != {'id':'reference-planning-observers','version':'1'}:
            raise ValueError('Reference planning replay supports only reference-planning-observers version 1')
        with self.db.read_session() as session:
            detail = get_replay_spec(session,spec.input['replay_spec_id'])
            if detail.project_id != spec.project_id:
                raise ValueError('Replay spec belongs to another project')
            preflight = replay_preflight(session,self.db,detail)
            if not preflight['executable']:
                raise ValueError('Replay preflight blocked: '+ '; '.join(preflight['blocking_reasons']))
        return {'schema_version':'planning-replay-prepared/1','replay_spec_id':detail.id,'replay_spec_sha256':detail.sha256,
            'sequence_id':detail.request.sequence_id,'sequence_sha256':detail.request.sequence_sha256,
            'request':detail.request.model_dump(mode='json'),
            'seed':spec.seed,'adapter_id':'reference-planning-replay','adapter_version':'1','preflight':preflight}

    def execute(self,snapshot,work_dir:Path,should_cancel):
        from .experiment_runtime import ExperimentExecution,ExperimentOutputFile
        from .replay_specs import get_replay_spec,replay_preflight
        with self.db.read_session() as session:
            detail = get_replay_spec(session,snapshot['replay_spec_id'])
            if (detail.sha256 != snapshot['replay_spec_sha256'] or detail.request.sequence_id != snapshot['sequence_id']
                    or detail.request.sequence_sha256 != snapshot['sequence_sha256'] or detail.request.model_dump(mode='json') != snapshot['request']):
                raise ValueError('Pinned replay spec changed after submission')
            preflight = replay_preflight(session,self.db,detail)
            if not preflight['executable']:
                raise ValueError('Retained source preflight blocked after submission: '+ '; '.join(preflight['blocking_reasons']))
        summary,ticks = run_planning_replay(detail.sequence_snapshot,detail.request.model_dump(mode='json'),
            seed=snapshot['seed'],should_cancel=should_cancel)
        work_dir.mkdir(parents=True,exist_ok=True)
        trace = work_dir/'planning-trace.ndjson'
        with trace.open('xb') as stream:
            for tick in ticks:
                stream.write(canonical_bytes(tick)+b'\n')
        manifest = work_dir/'planning-manifest.json'
        trace_hash = __import__('hashlib').sha256()
        with trace.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):
                trace_hash.update(chunk)
        manifest.write_bytes(canonical_bytes({'summary':summary,'replay_spec':detail.model_dump(mode='json'),
            'trace':{'sha256':trace_hash.hexdigest(),'size_bytes':trace.stat().st_size,
                'encoding':'ndjson','frame_count':len(ticks)}}))
        # The normal result preview is bounded; full exact trace is a separate download.
        summary['visualization'] = {'ticks':[{'index':tick['index'],'at_ns':tick['at_ns'],'ego':tick['ego'],
            'logged_ego':tick['logged_ego'],'world_objects':tick['world_objects'][:20] if tick['world_objects'] else tick['world_objects'],
            'object_count':len(tick['world_objects']) if tick['world_objects'] is not None else None,
            'objects_preview_truncated':tick['world_objects'] is not None and len(tick['world_objects'])>20,'metrics':tick['metrics'],
            'control':tick['control']} for tick in ticks[:128]],'total_ticks':len(ticks),'preview_truncated':len(ticks)>128}
        return ExperimentExecution(summary=summary,artifacts=[ExperimentOutputFile(path=trace,name='planning-trace.ndjson',media_type='application/x-ndjson'),
            ExperimentOutputFile(path=manifest,name='planning-manifest.json',media_type='application/json')],fidelity=summary['fidelity'])
