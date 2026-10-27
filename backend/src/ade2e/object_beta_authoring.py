"""Bounded beta edits over pinned catalogs; no simulator or engine imports."""
import copy
import math
from fastapi import HTTPException
from pydantic import ValidationError
from .object_behaviors import behavior_catalog
from .scenario_v2 import get_scenario
from .scenario_v2_schemas import ReactiveActor
from .simulation_catalog import catalog_reference, plan_route

POSE_KEYS = ('x', 'y', 'z', 'heading_rad')


def edit_error(actor_id, field, code, message):
    raise HTTPException(422, {'code': 'object_sim_edit_invalid', 'issues': [
        {'actor_id': actor_id, 'field': field, 'code': code, 'message': message}]})


def restore_source(session, project, template, ref):
    source = get_scenario(session, ref.id, ref.revision)
    if source['project_id'] != project:
        raise HTTPException(404, 'Draft scenario not found in project')
    if any(source[key] != getattr(ref, key) for key in ('configuration_hash', 'manifest_hash')):
        raise HTTPException(409, 'Draft scenario pin differs from its immutable revision')
    # This is continuation of the same template, not an implicit map/asset import.
    def structure(value):
        result = {key: child for key, child in value.items() if key not in ('name', 'description', 'actors')}
        result['actors'] = sorted((actor['id'], actor['kind'], actor['role'], actor['asset'],
            actor['controller'], actor.get('route_mode'), actor['behavior']['risk_mode']) for actor in value['actors'])
        return result
    if structure(source['scenario']) != structure(template['scenario']):
        raise HTTPException(422, 'Saved draft must retain template map, seed, assets, actor identities, controllers and risk mode')
    return source


def editable_metadata(session, project, scenario, roles, native_map):
    catalog = behavior_catalog()
    by_actor = {actor: role for role, actor in roles.items()}
    actors = []
    for actor in scenario['actors']:
        reactive = actor['controller']['kind'] == 'reactive' and actor.get('route_mode', 'native_lane_graph') == 'native_lane_graph'
        static = actor['controller']['kind'] == 'static'
        asset = catalog_reference(session, 'asset', project, actor['asset'])['definition']
        kinds = [entry['id'] for entry in catalog['behaviors'] if actor['kind'] in entry['actor_kinds']
                 and (not entry['risk'] or actor['behavior']['risk_mode'])] if reactive else []
        actors.append({'id': actor['id'], 'role': by_actor.get(actor['id'], actor['role']), 'kind': actor['kind'],
            'fields': (['pose', 'destination', 'speed_mps', 'actions'] if reactive else ['pose'] if static else []),
            'route_editable': reactive, 'speed_limits': {'min': 0, 'max': 12, 'step': .5} if reactive else None,
            'actions': {'max_count': 16, 'kinds': kinds, 'trigger_kinds': catalog['trigger_kinds'],
                'risk_mode': actor['behavior']['risk_mode'],
                'requires_target_lane': ['lane_change', 'cut_in', 'merge', 'diverge'],
                'duration_s': {'min': 0, 'max': min(120, scenario['duration_s'])},
                'speed_mps': {'min': 0, 'max': 12}},
            'asset': {'ref': actor['asset'], 'name': asset['name'], 'blueprint_id': asset['blueprint_id'],
                'dimensions': asset['dimensions'], 'bounds_source': 'catalog_metadata' if asset['dimensions'] else 'unknown',
                'native_spawn_status': 'not_checked'}})
    return {'schema_version': 'object-sim-editable/1', 'actors': actors,
        'capabilities': ['actor_speed', 'lane_snapped_start', 'lane_snapped_destination', 'existing_behavior_actions'],
        'max_snap_distance_m': 5, 'scope': 'pinned_catalog_authoring_native_spawn_and_behavior_execution_not_verified'}


def apply_actor_edits(scenario, edits, native_map, *, moved_ids, speed_roles):
    actors = {actor['id']: actor for actor in scenario['actors']}
    nodes = {node['id']: node for node in native_map['definition']['nodes']}
    for identity, model in edits.items():
        if identity not in actors:
            edit_error(identity, 'actor', 'unknown_actor', 'Actor is not part of this template')
        actor = actors[identity]
        edit = model.model_dump(mode='json', exclude_unset=True)
        reactive = actor['controller']['kind'] == 'reactive' and actor.get('route_mode', 'native_lane_graph') == 'native_lane_graph'
        allowed = {'pose', 'destination', 'speed_mps', 'actions'} if reactive else {'pose'} if actor['controller']['kind'] == 'static' else set()
        if set(edit) - allowed:
            field = sorted(set(edit) - allowed)[0]
            edit_error(identity, field, 'unsupported_edit', 'This controller does not support this quick edit')
        if 'pose' in edit and identity in moved_ids:
            edit_error(identity, 'pose', 'conflicting_edit', 'Choose one position edit for this actor')
        if 'speed_mps' in edit and identity in speed_roles:
            edit_error(identity, 'speed_mps', 'conflicting_edit', 'Choose the parameter or actor speed edit, not both')
        if 'speed_mps' in edit:
            actor['speed_mps'] = edit['speed_mps']
        if 'actions' in edit:
            actor['behavior']['actions'] = edit['actions']
        if 'pose' in edit or 'destination' in edit:
            field = 'destination' if 'destination' in edit else 'pose'
            try:
                if not reactive:
                    from .object_demo import _move_actor
                    _move_actor(actor, edit['pose'], native_map, scenario['map'])
                else:
                    result = plan_route(native_map['definition'], {'project_id': native_map['project_id'],
                        'revision': scenario['map']['revision'], 'sha256': scenario['map']['sha256'],
                        'start': edit.get('pose', actor['pose']),
                        'goal': edit.get('destination', actor.get('destination') or actor['route'][-1]),
                        'max_snap_distance_m': 5})
                    if any(nodes[node_id].get('closed') or node_id in actor['planner']['closed_node_ids'] for node_id in result['node_ids']):
                        raise HTTPException(422, 'Actor route crosses a closed lane')
                    actor['pose'] = result['route'][0]
                    actor['route'] = result['route']
                    # Native planner demands an exact node-aligned destination.
                    actor['destination'] = result['route'][-1]
            except HTTPException as exc:
                edit_error(identity, field, 'invalid_route', str(exc.detail))
        try:
            ReactiveActor.model_validate(actor)
        except ValidationError as exc:
            error = exc.errors(include_url=False, include_context=False)[0]
            edit_error(identity, '.'.join(map(str, error['loc'])) or 'actions', 'invalid_actor', error['msg'])


def check_behavior_links(scenario, native_map):
    ids = {actor['id'] for actor in scenario['actors']}
    nodes = {node['id']: node for node in native_map['definition']['nodes']}
    signal_ids = {signal['id'] for signal in native_map['definition']['signals']}
    for actor in scenario['actors']:
        for action in actor['behavior']['actions']:
            prefix = 'actions.' + action['id']
            for label, target in (('target_actor_id', action.get('target_actor_id')),
                                  ('trigger.target_actor_id', action['trigger'].get('target_actor_id'))):
                if target is not None and (target not in ids or target == actor['id']):
                    edit_error(actor['id'], prefix + '.' + label, 'invalid_target', 'Select another actor in this scenario')
            target = action.get('target_lane_node_id')
            if target is not None and (target not in nodes or nodes[target].get('closed')):
                edit_error(actor['id'], prefix + '.target_lane_node_id', 'invalid_target_lane', 'Select an open node in the pinned lane graph')
            trigger = action['trigger']
            if trigger['kind'] == 'signal' and trigger.get('signal_id') not in signal_ids:
                edit_error(actor['id'], prefix + '.trigger.signal_id', 'invalid_signal', 'Select a signal in the pinned map')
            if trigger['kind'] == 'time' and trigger['value'] > scenario['duration_s'] - scenario['timestep_s']:
                edit_error(actor['id'], prefix + '.trigger.value', 'trigger_after_run', 'Behavior starts after the last command tick')
            if action['duration_s'] > scenario['duration_s']:
                edit_error(actor['id'], prefix + '.duration_s', 'duration_exceeds_run', 'Action duration exceeds this scenario duration')


def _rectangle_overlap(a, ad, b, bd):
    # Catalog boxes are centered XY footprints; vertical origin offsets are not
    # available, so this authoring guard is not a native spawn/collision test.
    if abs(a['z'] - b['z']) >= max(ad['height_m'], bd['height_m']):
        return False
    ah, bh = a['heading_rad'], b['heading_rad']
    axes = [(math.cos(ah), math.sin(ah)), (-math.sin(ah), math.cos(ah)),
            (math.cos(bh), math.sin(bh)), (-math.sin(bh), math.cos(bh))]
    delta = (b['x'] - a['x'], b['y'] - a['y'])
    for x, y in axes:
        reach_a = abs(x * math.cos(ah) + y * math.sin(ah)) * ad['length_m'] / 2 + abs(-x * math.sin(ah) + y * math.cos(ah)) * ad['width_m'] / 2
        reach_b = abs(x * math.cos(bh) + y * math.sin(bh)) * bd['length_m'] / 2 + abs(-x * math.sin(bh) + y * math.cos(bh)) * bd['width_m'] / 2
        if abs(delta[0] * x + delta[1] * y) >= reach_a + reach_b - 1e-6:
            return False
    return True


def check_initial_overlap(session, project, scenario):
    bounds = {actor['id']: catalog_reference(session, 'asset', project, actor['asset'])['definition']['dimensions'] for actor in scenario['actors']}
    unknown = [identity for identity, box in bounds.items() if box is None]
    checked_pairs = 0
    for index, actor in enumerate(scenario['actors']):
        for other in scenario['actors'][index + 1:]:
            if bounds[actor['id']] is None or bounds[other['id']] is None:
                continue
            checked_pairs += 1
            if _rectangle_overlap(actor['pose'], bounds[actor['id']], other['pose'], bounds[other['id']]):
                edit_error(actor['id'], 'pose', 'initial_overlap', f"Catalog footprints overlap with actor {other['id']}; separate their initial positions")
    return {'status': 'partial' if unknown else 'checked', 'checked_pairs': checked_pairs,
        'unknown_bounds_actor_ids': unknown, 'basis': 'catalog_centered_xy_footprints',
        'warnings': [f"Initial overlap not checked for actors with unknown catalog bounds: {', '.join(unknown)}"] if unknown else []}
