"""Explicitly bound demo presets, bounded edits and immutable execution comparison.

No engine is imported here. Preview never starts an execution; normal scenario and
experiment APIs own saving, native preflight, submission and retained evidence.
"""
import copy
import hashlib
import json
import math
import re
from fastapi import HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from . import experiment_jobs
from .experiment_models import ExperimentExecutionRow
from .job_models import JobRow
from .object_demo_schemas import DemoBinding, DemoPreview
from .scenario_v2 import get_scenario
from .scenario_v2_schemas import parse_scenario
from .scenario_v2_xml import canonical_bytes, digest
from .simulation_catalog import catalog_reference, map_lanes, plan_route, validate_city_catalogs
from .simulation_catalog_models import SimulationCatalogRevisionRow as Row
from .team_access import project_authorize

KIND = 'object-demo-preset'
PRESETS = {
    'obstacle_bypass': {'title': '장애물 회피', 'description': '장애물 위치와 차로 변경 설정을 바꾸고 우회·복귀를 비교합니다.',
        'keys': ['ego_speed_mps', 'obstacle_offset_m', 'allow_lane_changes'], 'roles': {'ego', 'obstacle'}},
    'gap_release': {'title': '양보 후 진입', 'description': '옆 차로 차량의 속도·간격을 바꾸고 대기 후 진입을 비교합니다.',
        'keys': ['ego_speed_mps', 'rear_speed_mps', 'rear_gap_m', 'allow_lane_changes'], 'roles': {'ego', 'rear'}},
    'junction_eight': {'title': '8대 교차로', 'description': '다중 차량의 양보와 교차로 통과를 함께 조사합니다.',
        'keys': [], 'roles': {'ego'}},
    'urban_city': {'title': '도심 주행', 'description': '실제 CARLA 도시 맵의 도로와 건물 배경에서 자차 주행을 재생합니다.',
        'keys': ['ego_speed_mps'], 'roles': {'ego'}},
    'urban_right_turn': {'title': '도심 우회전 · 앞차 반응',
        'description': '앞차의 정지 시간을 바꾸고, 같은 자차 제어로 감속·간격 확보·우회전 결과를 비교합니다.',
        'keys': ['lead_wait_s'], 'roles': {'ego', 'lead'}},
    'lead_stop_start': {'title': '앞차 정지·재출발',
        'description': '앞차의 속도·정지 시작과 유지 시간을 바꾸고 자차 반응을 조사합니다.',
        'keys': ['ego_speed_mps', 'lead_speed_mps', 'lead_wait_s', 'lead_start_s'], 'roles': {'ego', 'lead'}},
    'cut_in': {'title': '옆 차로 끼어들기',
        'description': '상대 차량의 속도·진입 시작 조건과 목표 차로를 바꾸고 실제 진입과 자차 반응을 구분해 확인합니다.',
        'keys': ['ego_speed_mps', 'intruder_speed_mps', 'cut_in_start_s'], 'roles': {'ego', 'intruder'}},
    'urban_left_turn': {'title': '도심 좌회전 · 교차 차량',
        'description': '자차와 교차 차량의 속도·진입 경로를 바꾸고 양보·통과 결과를 조사합니다.',
        'keys': ['ego_speed_mps', 'crossing_speed_mps'], 'roles': {'ego', 'crossing'}},
}
PARAMETERS = {
    'ego_speed_mps': {'type': 'number', 'label': '자차 목표 속도 (m/s)', 'min': 0, 'max': 12, 'step': .5},
    'obstacle_offset_m': {'type': 'number', 'label': '장애물 위치 이동 (m)', 'min': -15, 'max': 15, 'step': 1},
    'rear_speed_mps': {'type': 'number', 'label': '옆 차로 차량 속도 (m/s)', 'min': 0, 'max': 12, 'step': .5},
    'rear_gap_m': {'type': 'number', 'label': '옆 차로 차량 초기 종방향 간격 (m)', 'min': 5, 'max': 60, 'step': 1},
    'allow_lane_changes': {'type': 'boolean', 'label': '차로 변경 허용'},
    'lead_wait_s': {'type': 'number', 'label': '앞차 정지 유지 시간 (초, 0은 계속 진행)', 'min': 0, 'max': 6, 'step': 1},
    'lead_speed_mps': {'type': 'number', 'label': '앞차 목표 속도 (m/s)', 'min': 0, 'max': 12, 'step': .5},
    'intruder_speed_mps': {'type': 'number', 'label': '진입 차량 목표 속도 (m/s)', 'min': 0, 'max': 12, 'step': .5},
    'crossing_speed_mps': {'type': 'number', 'label': '교차 차량 목표 속도 (m/s)', 'min': 0, 'max': 12, 'step': .5},
    'lead_start_s': {'type': 'number', 'label': '앞차 정지 시작 (초)', 'min': 0, 'max': 30, 'step': .5},
    'cut_in_start_s': {'type': 'number', 'label': '끼어들기 시작 (초)', 'min': 0, 'max': 30, 'step': .5},
}
POSE_KEYS = ('x', 'y', 'z', 'heading_rad')


def _preset(identity):
    if identity not in PRESETS:
        raise HTTPException(404, 'Object Sim preset not found')
    return PRESETS[identity]


def _binding(session, project, identity):
    revision = session.scalar(select(func.max(Row.revision)).where(Row.project_id == project, Row.kind == KIND, Row.id == identity))
    row = session.get(Row, (project, KIND, identity, revision)) if revision else None
    if row is None:
        return None
    try:
        if hashlib.sha256(row.manifest).hexdigest() != row.manifest_hash:
            raise ValueError('hash')
        value = json.loads(row.manifest)
        if (value['project_id'], value['id'], value['revision'], value['kind']) != (project, identity, revision, KIND):
            raise ValueError('identity')
        DemoBinding.model_validate({**value['definition'], 'project_id': project})
        return {**value, 'sha256': row.manifest_hash}
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(409, 'Object Sim preset binding failed its integrity check') from exc


def _bound_scenario(session, project, identity, definition):
    ref = definition['scenario_ref']
    source = get_scenario(session, ref['id'], ref['revision'])
    if source['project_id'] != project:
        raise HTTPException(404, 'Preset scenario not found in project')
    if any(source[key] != ref[key] for key in ('configuration_hash', 'manifest_hash')):
        raise HTTPException(409, 'Preset scenario pin differs from immutable revision')
    scenario = source['scenario']
    if scenario['schema_version'] != 'scenario-v2/3':
        raise HTTPException(422, 'Demo presets require scenario-v2/3')
    roles = definition['actor_roles']; actors = {a['id']: a for a in scenario['actors']}
    if not _preset(identity)['roles'].issubset(roles) or len(set(roles.values())) != len(roles):
        raise HTTPException(422, 'Preset requires distinct explicit actor role bindings')
    if any(actor not in actors for actor in roles.values()) or actors[roles['ego']]['role'] != 'ego':
        raise HTTPException(422, 'Preset actor role does not match the pinned scenario')
    if actors[roles['ego']]['controller']['kind'] != 'reactive':
        raise HTTPException(422, 'Preset ego requires the reactive controller')
    if 'obstacle' in roles and actors[roles['obstacle']]['controller']['kind'] != 'static':
        raise HTTPException(422, 'Preset obstacle must use the static controller')
    if 'rear' in roles and actors[roles['rear']]['controller']['kind'] != 'reactive':
        raise HTTPException(422, 'Preset rear vehicle requires the reactive controller')
    if identity == 'junction_eight' and len(actors) != 8:
        raise HTTPException(422, 'The 8-car junction preset requires exactly eight actors')
    checked = parse_scenario(scenario)
    validation = validate_city_catalogs(session, project, checked)
    native_map = catalog_reference(session, 'map', project, scenario['map'])
    if identity in {'urban_city', 'urban_right_turn', 'urban_left_turn'}:
        # Catalog provenance is checked here; execution preflight additionally
        # compares the installed town's original OpenDRIVE with this exact pin.
        map_definition = native_map['definition']
        town_path = r'(?:/Game/)?Carla/Maps/(?:Town\d+(?:HD)?/)?Town\d+(?:HD)?(?:_Opt)?'
        if (map_definition.get('generated') is not None or map_definition.get('engine') != 'carla'
                or re.fullmatch(town_path, map_definition['native_name']) is None
                or map_definition['rights'].get('source_url', '').rstrip('/') != 'https://github.com/carla-simulator/carla'):
            raise HTTPException(422, 'Urban preset requires a native CARLA Town catalog with CARLA source provenance; generated roads are unsupported')
    if identity in {'urban_right_turn', 'lead_stop_start'}:
        lead = actors[roles['lead']]
        if lead['kind'] != 'vehicle' or lead['controller']['kind'] != 'reactive':
            raise HTTPException(422, 'Urban interaction requires a reactive lead vehicle')
        actions = lead['behavior']['actions']
        if (len(actions) != 1 or actions[0]['id'] != 'lead_wait' or actions[0]['kind'] != 'wait'
                or actions[0]['trigger']['kind'] != 'time' or actions[0]['duration_s'] <= 0
                or actions[0]['timeout_s'] < PARAMETERS['lead_wait_s']['max']):
            raise HTTPException(422, 'Urban interaction requires one pinned lead_wait time action with a supported timeout')
        if actors[roles['ego']]['behavior']['actions']:
            raise HTTPException(422, 'Urban interaction ego must react without authored stop or wait actions')
    if identity == 'cut_in':
        intruder = actors[roles['intruder']]
        actions = intruder['behavior']['actions']
        if (intruder['kind'] != 'vehicle' or intruder['controller']['kind'] != 'reactive'
                or not intruder['behavior']['risk_mode'] or len(actions) != 1
                or actions[0]['kind'] != 'cut_in' or actions[0]['trigger']['kind'] != 'time'):
            raise HTTPException(422, 'Cut-in template requires one time-triggered cut_in on a reactive vehicle with explicit risk mode')
    if identity == 'urban_left_turn':
        crossing = actors[roles['crossing']]
        if crossing['kind'] != 'vehicle' or crossing['controller']['kind'] != 'reactive':
            raise HTTPException(422, 'Left-turn interaction requires a reactive crossing vehicle')
    return source, native_map, validation['warnings']


def _defaults(identity, scenario, roles):
    actors = {a['id']: a for a in scenario['actors']}; ego = actors[roles['ego']]
    values = {'ego_speed_mps': ego['speed_mps'], 'obstacle_offset_m': 0.,
              'allow_lane_changes': ego['planner']['allow_lane_changes']}
    if 'rear' in roles:
        rear = actors[roles['rear']]; h = ego['pose']['heading_rad']
        values.update(rear_speed_mps=rear['speed_mps'], rear_gap_m=round(
            (ego['pose']['x'] - rear['pose']['x']) * math.cos(h) + (ego['pose']['y'] - rear['pose']['y']) * math.sin(h), 4))
    for role in ('lead', 'intruder', 'crossing'):
        if role in roles:
            values[role + '_speed_mps'] = actors[roles[role]]['speed_mps']
    if identity in {'urban_right_turn', 'lead_stop_start'}:
        waits = [a for a in actors[roles['lead']]['behavior']['actions'] if a['id'] == 'lead_wait']
        values['lead_wait_s'] = waits[0]['duration_s'] if waits else 0.
        values['lead_start_s'] = waits[0]['trigger']['value'] if waits and waits[0]['trigger']['kind'] == 'time' else 0.
    if identity == 'cut_in':
        actions = actors[roles['intruder']]['behavior']['actions']
        values['cut_in_start_s'] = actions[0]['trigger']['value'] if actions else 0.
    return {key: values[key] for key in _preset(identity)['keys']}


def _parameters(identity, defaults, supplied):
    if set(supplied) - set(_preset(identity)['keys']):
        raise HTTPException(422, 'Unsupported parameter for this preset')
    resolved = {**defaults, **supplied}
    for key, value in resolved.items():
        spec = PARAMETERS[key]
        if spec['type'] == 'boolean':
            valid = type(value) is bool
        else:
            valid = type(value) in (float, int) and math.isfinite(value) and spec['min'] <= value <= spec['max']
        if not valid:
            raise HTTPException(422, f'Preset parameter {key} is outside its supported type or limits')
    return resolved


def _execution(session, project, identity):
    subject = session.get(ExperimentExecutionRow, identity)
    if subject is None or subject.project_id != project:
        raise HTTPException(404, 'Object Sim execution not found in project')
    row = session.scalar(select(JobRow).where(JobRow.id == identity))
    if row is None:
        raise HTTPException(409, 'Object Sim execution job is missing')
    try:
        detail = experiment_jobs.detail(session, row).model_dump(mode='json')
        snapshot = row.prepared['input']; spec = row.prepared['spec']
        if spec['kind'] != 'object_sim' or snapshot.get('schema_version') != 'carla-city-input/1':
            raise HTTPException(422, 'Comparison requires native city Object Sim inputs')
        scenario = parse_scenario(snapshot['scenario']).model_dump(mode='json')
        source = snapshot['source']
        ref = {'id': source['scenario_id'], 'revision': source['scenario_revision'],
               'configuration_hash': source['configuration_hash'], 'manifest_hash': source['scenario_manifest_hash']}
        original = get_scenario(session, ref['id'], ref['revision'])
        if original['project_id'] != project or original['scenario'] != scenario or any(original[k] != ref[k] for k in ('configuration_hash', 'manifest_hash')):
            raise ValueError('frozen scenario')
        if scenario['seed'] != spec['seed'] or source['seed'] != spec['seed'] or source['map_sha256'] != scenario['map']['sha256']:
            raise ValueError('snapshot source')
        return {'execution': detail, 'scenario': scenario, 'scenario_ref': ref, 'metrics': detail['summary']}, snapshot
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(409, 'Object Sim execution input failed its integrity check') from exc


def _validate_saved(session, project, definition, preset_id=None):
    identity = definition.get('saved_execution_id')
    if not identity:
        if definition.get('saved_comparison_execution_id'):
            raise HTTPException(422, 'A saved comparison requires a saved baseline execution')
        return
    saved, _ = _execution(session, project, identity)
    if saved['scenario_ref'] != definition['scenario_ref'] or saved['execution']['status'] != 'completed':
        raise HTTPException(422, 'Saved execution must be a completed execution of the pinned scenario')
    comparison_id = definition.get('saved_comparison_execution_id')
    if comparison_id:
        compared = _comparison(session, project, comparison_id, identity)
        if (comparison_id == identity or not compared['compatibility']['compatible']
                or any(side['execution']['status'] != 'completed' or side['execution']['outcome_uncertain']
                       for side in (compared['left'], compared['right']))):
            raise HTTPException(422, 'Saved comparison requires distinct completed compatible executions')
        if preset_id == 'urban_right_turn':
            expected = copy.deepcopy(compared['right']['scenario'])
            lead = next(actor for actor in expected['actors'] if actor['id'] == definition['actor_roles']['lead'])
            lead['behavior']['actions'] = []
            strip_labels = lambda scenario: {key: value for key, value in scenario.items() if key not in ('name', 'description')}
            if strip_labels(compared['left']['scenario']) != strip_labels(expected):
                raise HTTPException(422, 'Urban saved comparison must differ only by removing the lead wait action')


def _item(session, project, identity):
    preset = _preset(identity)
    result = {'id': identity, 'version': 1, 'title': preset['title'], 'description': preset['description'],
              'ready': False, 'status': 'unbound', 'reason': '이 프로젝트에 데모 시나리오가 아직 고정되지 않았습니다.',
              'parameters': [], 'defaults': {}, 'binding_revision': None, 'scenario_ref': None, 'saved_execution_id': None,
              'saved_comparison_execution_id': None,
              'beta_template': identity in {'lead_stop_start', 'cut_in', 'urban_left_turn', 'urban_right_turn'},
              'observations_scope': 'user_observation_separate_from_execution_verdict'}
    binding = _binding(session, project, identity)
    if binding is None:
        return result
    definition = binding['definition']
    result.update(binding_revision=binding['revision'], scenario_ref=definition['scenario_ref'], saved_execution_id=definition.get('saved_execution_id'),
                  saved_comparison_execution_id=definition.get('saved_comparison_execution_id'))
    try:
        source, native_map, _ = _bound_scenario(session, project, identity, definition)
        defaults = _parameters(identity, _defaults(identity, source['scenario'], definition['actor_roles']), {})
        _validate_saved(session, project, definition, identity)
        from .object_beta_authoring import editable_metadata
        result.update(ready=True, status='ready', reason=None, defaults=defaults,
            editable=editable_metadata(session, project, source['scenario'], definition['actor_roles'], native_map),
            parameters=[{'key': key, **PARAMETERS[key], 'default': value} for key, value in defaults.items()])
    except (HTTPException, ValueError, KeyError) as exc:
        result.update(status='invalid', reason=str(exc.detail if isinstance(exc, HTTPException) else exc))
    return result


def _move_actor(actor, requested, native_map, map_ref):
    definition = native_map['definition']
    if actor['controller']['kind'] == 'static':
        ranked = sorted((math.dist([requested[k] for k in ('x', 'y', 'z')], [node[k] for k in ('x', 'y', 'z')]), node['id'], node) for node in definition['nodes'])
        distance, _, node = ranked[0]
        if distance > 5 or node.get('closed'):
            raise HTTPException(422, 'Dragged obstacle must be within 5 m of an open lane')
        actor['pose'] = {k: node[k] for k in POSE_KEYS}
        return
    if actor['controller']['kind'] != 'reactive' or actor.get('route_mode', 'native_lane_graph') != 'native_lane_graph':
        raise HTTPException(422, 'Quick position editing supports reactive lane routes and static actors only')
    destination = actor.get('destination') or actor['route'][-1]
    result = plan_route(definition, {'project_id': native_map['project_id'], 'revision': map_ref['revision'], 'sha256': map_ref['sha256'],
        'start': requested, 'goal': destination, 'max_snap_distance_m': 5})
    nodes = {node['id']: node for node in definition['nodes']}
    if any(nodes[identity].get('closed') for identity in result['node_ids']):
        raise HTTPException(422, 'Dragged actor route crosses a closed lane')
    actor['pose'] = result['route'][0]; actor['route'] = result['route']; actor['destination'] = destination


def _preview(session, identity, body):
    binding = _binding(session, body.project_id, identity)
    if binding is None:
        raise HTTPException(409, 'Preset is not bound in this project')
    definition = binding['definition']
    source, native_map, warnings = _bound_scenario(session, body.project_id, identity, definition)
    from .object_beta_authoring import editable_metadata, edit_error, apply_actor_edits, check_initial_overlap, check_behavior_links, restore_source
    if body.source_scenario_ref is not None:
        source = restore_source(session, body.project_id, source, body.source_scenario_ref)
    scenario = copy.deepcopy(source['scenario']); roles = definition['actor_roles']
    defaults = _defaults(identity, scenario, roles)
    values = _parameters(identity, defaults, body.parameters)
    actors = {a['id']: a for a in scenario['actors']}; ego = actors[roles['ego']]
    for role, key in ((role, role + '_speed_mps') for role in ('ego', 'rear', 'lead', 'intruder', 'crossing')):
        if key in values and values[key] != defaults[key]:
            actor = actors[roles[role]]; actor['speed_mps'] = values[key]
            for action in actor['behavior']['actions']:
                if action.get('speed_mps') is not None:
                    action['speed_mps'] = values[key]
    if 'allow_lane_changes' in values:
        ego['planner']['allow_lane_changes'] = values['allow_lane_changes']
    if 'lead_wait_s' in values and {'lead_wait_s', 'lead_start_s'} & set(body.parameters):
        lead = actors[roles['lead']]
        pinned_lead = next(actor for actor in get_scenario(session, definition['scenario_ref']['id'], definition['scenario_ref']['revision'])['scenario']['actors'] if actor['id'] == roles['lead'])
        if values['lead_wait_s'] == 0:
            lead['behavior']['actions'] = []
        else:
            if not lead['behavior']['actions']:
                lead['behavior']['actions'] = copy.deepcopy(pinned_lead['behavior']['actions'])
            lead['behavior']['actions'][0]['duration_s'] = values['lead_wait_s']
            if 'lead_start_s' in values:
                lead['behavior']['actions'][0]['trigger'] = {'kind': 'time', 'value': values['lead_start_s'], 'operator': 'gte'}
    if 'cut_in_start_s' in body.parameters:
        actor = actors[roles['intruder']]
        if actor['behavior']['actions']:
            actor['behavior']['actions'][0]['trigger'] = {'kind': 'time', 'value': values['cut_in_start_s'], 'operator': 'gte'}
    moves = {}
    if values.get('obstacle_offset_m', 0):
        actor = actors[roles['obstacle']]; pose = copy.deepcopy(actor['pose']); offset = values['obstacle_offset_m']
        pose['x'] += offset * math.cos(pose['heading_rad']); pose['y'] += offset * math.sin(pose['heading_rad']); moves[actor['id']] = pose
    if 'rear_gap_m' in values and values['rear_gap_m'] != defaults['rear_gap_m']:
        actor = actors[roles['rear']]; pose = copy.deepcopy(actor['pose']); offset = defaults['rear_gap_m'] - values['rear_gap_m']
        pose['x'] += offset * math.cos(ego['pose']['heading_rad']); pose['y'] += offset * math.sin(ego['pose']['heading_rad']); moves[actor['id']] = pose
    for identity_, pose in body.actor_poses.items():
        if identity_ not in actors:
            edit_error(identity_, 'pose', 'unknown_actor', 'Dragged actor is not part of the preset')
        if identity_ in moves:
            raise HTTPException(422, 'Use either the position parameter or a dragged pose for an actor, not both')
        moves[identity_] = pose.model_dump(mode='json')
    for identity_, pose in moves.items():
        try:
            _move_actor(actors[identity_], pose, native_map, scenario['map'])
        except HTTPException as exc:
            edit_error(identity_, 'pose', 'invalid_route', str(exc.detail))
    apply_actor_edits(scenario, body.actor_edits, native_map, moved_ids=set(moves),
                      speed_roles={roles[role] for role in roles if role + '_speed_mps' in body.parameters
                                   and body.parameters[role + '_speed_mps'] != defaults[role + '_speed_mps']})
    check_behavior_links(scenario, native_map)
    scenario = parse_scenario(scenario).model_dump(mode='json')
    validate_city_catalogs(session, body.project_id, parse_scenario(scenario))
    overlap = check_initial_overlap(session, body.project_id, scenario)
    warnings = [*warnings, *overlap['warnings']]
    return {'schema_version': 'object-sim-preview/1', 'preset_id': identity, 'project_id': body.project_id,
        'binding_revision': binding['revision'], 'scenario_ref': definition['scenario_ref'], 'scenario': scenario,
        'parameters': values, 'map': {**native_map, 'lanes': map_lanes(native_map['definition'])}, 'actor_roles': roles, 'saved_execution_id': definition.get('saved_execution_id'),
        'warnings': warnings, 'execution_status': 'not_executed', 'native_preflight_required': True,
        'source_scenario_ref': {key: source[key] for key in ('id', 'revision', 'configuration_hash', 'manifest_hash')},
        'editable': editable_metadata(session, body.project_id, scenario, roles, native_map),
        'validation': {'valid': True, 'checks': ['catalog', 'lane_route', 'behavior_links', 'initial_overlap'],
            'initial_overlap': overlap, 'native_spawn_status': 'not_checked'}}


def _diff(left, right, path=''):
    if left == right:
        return []
    if isinstance(left, dict) and isinstance(right, dict):
        return [change for key in sorted(set(left) | set(right)) for change in _diff(left.get(key), right.get(key), path + '/' + key)]
    if isinstance(left, list) and isinstance(right, list):
        if all(isinstance(item, dict) and 'id' in item for item in left + right):
            return _diff({item['id']: item for item in left}, {item['id']: item for item in right}, path)
        if len(left) == len(right):
            return [change for index, (a, b) in enumerate(zip(left, right)) for change in _diff(a, b, path + '/' + str(index))]
    return [{'path': path, 'left': left, 'right': right}]


def _numbers(value, path=''):
    if isinstance(value, dict):
        return {key: scalar for name, child in value.items() for key, scalar in _numbers(child, path + '/' + name).items()}
    if isinstance(value, list):
        # Native city summaries identify actor metrics explicitly. Align on that
        # identity even when two inputs serialize their actor arrays differently.
        identities = [child.get('actor_id') for child in value if isinstance(child, dict)]
        if len(identities) == len(value) and all(isinstance(identity, str) for identity in identities) and len(set(identities)) == len(value):
            return {key: scalar for child in value for key, scalar in _numbers(child, path + '/' + child['actor_id']).items()}
        return {key: scalar for index, child in enumerate(value) for key, scalar in _numbers(child, path + '/' + str(index)).items()}
    if type(value) is bool or type(value) in (int, float) and math.isfinite(value):
        return {path: value}
    return {}


def _comparison(session, project, left_id, right_id):
    left, a = _execution(session, project, left_id); right, b = _execution(session, project, right_id)
    reasons = []
    def check(label, x, y, required=True):
        if required and (x is None or y is None): reasons.append(label + ': provenance missing')
        elif x != y: reasons.append(label + ': values differ')
    check('map revision', left['scenario']['map'], right['scenario']['map'])
    check('seed', left['scenario']['seed'], right['scenario']['seed'])
    check('adapter', (left['execution']['adapter_id'], left['execution']['adapter_version']), (right['execution']['adapter_id'], right['execution']['adapter_version']))
    for key in ('server_version', 'adapter_script_sha256', 'native_script_sha256', 'control_script_sha256', 'maneuver_script_sha256', 'planner_script_sha256'):
        check(key, a.get(key), b.get(key), required=key != 'planner_script_sha256' or any(x['controller']['kind'] == 'reactive' for x in left['scenario']['actors'] + right['scenario']['actors']))
    check('execution fidelity', a.get('execution_fidelity'), b.get('execution_fidelity'))
    # Structural changes need a new study; quick comparison varies pose, speed,
    # route and planner settings while retaining the same objects and controller.
    for key in ('coordinate_system', 'duration_s', 'timestep_s', 'environment', 'sensors', 'acceptance', 'signals'):
        check(key, left['scenario'].get(key), right['scenario'].get(key), required=False)
    structure = lambda s: sorted((x['id'], x['kind'], x['role'], json.dumps(x['asset'], sort_keys=True), json.dumps(x['controller'], sort_keys=True)) for x in s['actors'])
    check('actor identities/assets/controllers', structure(left['scenario']), structure(right['scenario']))
    changes = _diff({k:v for k,v in left['scenario'].items() if k not in ('name','description')},
                    {k:v for k,v in right['scenario'].items() if k not in ('name','description')})
    lm = _numbers((left['metrics'] or {}).get('metrics', {})); rm = _numbers((right['metrics'] or {}).get('metrics', {}))
    complete = not reasons and all(side['execution']['status'] == 'completed' and not side['execution']['outcome_uncertain'] for side in (left, right))
    metrics = [{'path': path, 'left': lm.get(path), 'right': rm.get(path), 'delta':
        rm[path] - lm[path] if complete and type(lm.get(path)) in (int,float) and type(rm.get(path)) in (int,float) else None} for path in sorted(set(lm) | set(rm))]
    return {'schema_version': 'object-sim-comparison/1', 'project_id': project, 'left': left, 'right': right,
            'compatibility': {'compatible': not reasons, 'reasons': reasons}, 'changes': changes, 'metrics': metrics,
            'evaluation_status': 'existing_execution_verdicts_only'}


def register_object_demo_routes(api, db):
    from .object_story import register_object_story_routes
    register_object_story_routes(api, db)
    from .object_beta_observations import register_object_observation_routes
    register_object_observation_routes(api, db)
    from .object_beta_report import register_object_behavior_report_routes
    register_object_behavior_report_routes(api, db)

    @api.get('/object-sim/presets', tags=['Object Sim'])
    def presets(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            return {'schema_version': 'object-sim-presets/1', 'project_id': project_id,
                    'items': [_item(session, project_id, identity) for identity in PRESETS],
                    'ready_scope': 'pinned_authoring_inputs_only_native_preflight_required'}

    @api.post('/object-sim/presets/{identity}/bind', tags=['Object Sim'], openapi_extra={'x-axiom-min-role': 'Engineer'})
    def bind(identity: str, body: DemoBinding, request: Request):
        _preset(identity); project_authorize(request, db, body.project_id, 'Engineer')
        definition = body.model_dump(mode='json', exclude={'project_id', 'expected_revision'})
        try:
            with db.session() as session:
                source, _, _ = _bound_scenario(session, body.project_id, identity, definition)
                _parameters(identity, _defaults(identity, source['scenario'], definition['actor_roles']), {})
                _validate_saved(session, body.project_id, definition, identity)
                previous = _binding(session, body.project_id, identity); revision = previous['revision'] if previous else 0
                previous_definition = (DemoBinding.model_validate({**previous['definition'], 'project_id': body.project_id})
                    .model_dump(mode='json', exclude={'project_id', 'expected_revision'})) if previous else None
                if previous and definition == previous_definition and body.expected_revision in (revision, revision - 1):
                    return previous
                if revision != body.expected_revision:
                    raise HTTPException(409, 'Preset binding changed; reload before binding')
                value = {'schema_version': 'object-sim-preset-binding/1', 'kind': KIND, 'project_id': body.project_id,
                         'id': identity, 'revision': revision + 1, 'definition': definition}
                raw = canonical_bytes(value)
                session.add(Row(project_id=body.project_id, kind=KIND, id=identity, revision=revision + 1,
                                manifest=raw, manifest_hash=hashlib.sha256(raw).hexdigest()))
                session.flush(); result = _binding(session, body.project_id, identity); session.commit(); return result
        except IntegrityError as exc:
            raise HTTPException(409, 'Concurrent preset binding; reload and retry') from exc

    @api.post('/object-sim/presets/{identity}/preview', tags=['Object Sim'],
              openapi_extra={'x-axiom-min-role': 'Viewer', 'x-axiom-read-only': True})
    def preview(identity: str, body: DemoPreview, request: Request):
        _preset(identity); project_authorize(request, db, body.project_id)
        with db.read_session() as session:
            return _preview(session, identity, body)

    @api.get('/object-sim/comparison', tags=['Object Sim'])
    def comparison(request: Request, left_execution_id: str, right_execution_id: str, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            return _comparison(session, project_id, left_execution_id, right_execution_id)
