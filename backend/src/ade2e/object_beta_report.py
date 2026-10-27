"""Planner action records and native response geometry, kept as separate facts."""
import math
from fastapi import Request
from .object_story import StoryUnavailable, read_verified_scene
from .team_access import project_authorize


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _ego_outcome(scene, scenario):
    spec = next(actor for actor in scenario['actors'] if actor['role'] == 'ego')
    states = []
    for frame in scene['frames']:
        actors = frame.get('actors')
        matching = [actor for actor in actors if isinstance(actor, dict) and actor.get('id') == spec['id']] if isinstance(actors, list) else []
        if (len(matching) != 1 or not all(_finite(matching[0].get(key)) for key in ('x', 'y', 'z', 'speed_mps'))
                or matching[0]['speed_mps'] < 0):
            return {'status': 'unavailable', 'actor_id': spec['id'], 'reason': 'retained_ego_response_missing_or_invalid'}
        states.append(matching[0])
    destination = spec.get('destination') or spec['route'][-1]
    goal_error = math.hypot(states[-1]['x'] - destination['x'], states[-1]['y'] - destination['y'])
    final_speed = states[-1]['speed_mps']
    acceptance = scenario['acceptance']
    stopped = final_speed <= acceptance['max_final_speed_mps']
    arrived = stopped and goal_error <= acceptance['max_goal_error_m']
    events = scene.get('events')
    contact = None
    if isinstance(events, list) and all(isinstance(event, dict) and (event.get('kind') != 'native_contact'
            or isinstance(event.get('actor_ids'), list)) for event in events):
        contact = any(event.get('kind') == 'native_contact' and spec['id'] in event.get('actor_ids', []) for event in events)
    return {'status': 'arrived' if arrived else 'stopped' if stopped else 'moving', 'actor_id': spec['id'],
        'distance_travelled_m': sum(math.hypot(b['x'] - a['x'], b['y'] - a['y']) for a, b in zip(states, states[1:])),
        'goal_error_m': goal_error, 'final_speed_mps': final_speed, 'native_contact': contact,
        'arrival_thresholds': {'goal_error_m': acceptance['max_goal_error_m'], 'final_speed_mps': acceptance['max_final_speed_mps']},
        'source': 'retained_native_response_geometry', 'clock': 'response_frame_time_s',
        'start_time_s': scene['frames'][0]['time_s'], 'end_time_s': scene['frames'][-1]['time_s']}


def behavior_report(db, session, project, execution_id):
    from .object_demo import _execution
    side, snapshot = _execution(session, project, execution_id)
    execution, scenario = side['execution'], side['scenario']
    pending = execution['status'] in {'queued', 'running', 'cancel_requested'}
    result = {'schema_version': 'object-sim-behavior/1', 'project_id': project, 'execution_id': execution_id,
        'execution_status': execution['status'], 'outcome_uncertain': execution['outcome_uncertain'],
        'fidelity': execution['fidelity'], 'adapter_id': execution['adapter_id'], 'adapter_version': execution['adapter_version'],
        'scenario_ref': side['scenario_ref'], 'status': 'pending' if pending else 'unavailable',
        'reason': 'execution_not_terminal' if pending else None, 'source': None, 'actors': [],
        'ego_outcome': {'status': 'unavailable', 'reason': 'verified_native_response_required'},
        'verification_verdict': (execution.get('summary') or {}).get('verification_verdict'),
        'action_status_basis': 'retained_reactive_planner_events',
        'action_completion_meaning': 'planner_reported_completion_not_physical_validation',
        'physical_sensor_validation': False}
    for actor in scenario['actors']:
        result['actors'].append({'actor_id': actor['id'], 'role': actor['role'],
            'mode': 'authored_actions' if actor['behavior']['actions'] else 'route_only',
            'planner_arrival_time_s': None,
            'actions': [{'action_id': action['id'], 'kind': action['kind'], 'status': 'pending',
                'started_at_s': None, 'completed_at_s': None, 'events': []} for action in actor['behavior']['actions']]})
    if pending:
        return result
    try:
        scene, receipt = read_verified_scene(db, session, side, snapshot)
        frames = scene['frames']
        raw = sorted(scene['planner_events'], key=lambda event: (event['index'], event['time_s']))
        for actor in result['actors']:
            arrivals = [event['time_s'] for event in raw if event['actor_id'] == actor['actor_id']
                        and event['type'] == 'decision_changed' and event['reason'] == 'arrived']
            actor['planner_arrival_time_s'] = min(arrivals) if arrivals else None
            for action in actor['actions']:
                events = [event for event in raw if event['actor_id'] == actor['actor_id']
                    and event.get('action_id') == action['action_id']
                    and event['type'] in {'action_started', 'action_completed', 'action_timeout'}]
                started, completed = None, None
                for event in events:
                    valid_reasons = {action['kind']}
                    if event['type'] == 'action_completed' and action['kind'] == 'yield':
                        # The planner names the observed release condition for
                        # an untimed yield, while retaining its authored ID.
                        valid_reasons.update({'conflict_clear', 'yield_target_clear'})
                    if event['type'] in {'action_started', 'action_completed'} and event['reason'] not in valid_reasons:
                        raise StoryUnavailable('planner_action_kind_differs_from_authored_action')
                    if event['type'] == 'action_started':
                        if started is not None or completed is not None:
                            raise StoryUnavailable('planner_action_clock_or_sequence_invalid')
                        started = event['time_s']
                    if event['type'] in {'action_completed', 'action_timeout'}:
                        if started is None or completed is not None or event['time_s'] < started:
                            raise StoryUnavailable('planner_action_clock_or_sequence_invalid')
                        if event['type'] == 'action_completed':
                            completed = event['time_s']
                    action['events'].append({'type': event['type'], 'reason': event['reason'],
                        'time_s': event['time_s'], 'observation_time_s': frames[event['index']]['time_s'],
                        'frame_index': event['index'], 'input_frame': event['input_frame']})
                action.update(status='completed' if completed is not None else 'started' if started is not None else 'not_exercised',
                              started_at_s=started, completed_at_s=completed)
        result.update(status='available', reason=None, source=receipt, ego_outcome=_ego_outcome(scene, scenario))
    except StoryUnavailable as exc:
        # Unavailable source is never reported as not-exercised or failed driving.
        result.update(status='unavailable', reason=str(exc))
        for actor in result['actors']:
            actor['planner_arrival_time_s'] = None
            for action in actor['actions']:
                action.update(status='pending', started_at_s=None, completed_at_s=None, events=[])
    return result


def register_object_behavior_report_routes(api, db):
    @api.get('/object-sim/executions/{execution_id}/behavior', tags=['Object Sim'],
             openapi_extra={'x-axiom-min-role': 'Viewer', 'x-axiom-read-only': True})
    def report(execution_id: str, request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            return behavior_report(db, session, project_id, execution_id)
