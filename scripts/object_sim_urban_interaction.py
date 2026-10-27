#!/usr/bin/env python3
"""Author and serially run an explicitly pinned Town10 right-turn interaction.

A lead continues; B changes only its authored lead_wait action. The ego keeps the
same route, speed, planner and empty action list. Normal APIs own all execution;
this helper never connects directly to CARLA or replaces another preset.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import time
import urllib.parse
import uuid
from carla_city_acceptance import API, digest, now, pose, ref, write

VERSION = 'object-sim-urban-interaction/1'
PRESET = 'urban_right_turn'
MAP_ID = 'carla-town10hd-opt'
SOURCE_REF = {'id': 'scenario2_dc94454c99694bfa989bc2d2b7a30fa4', 'revision': 1,
    'configuration_hash': '2f4f72efeb44e431275a91dc4baff54e3dd44bb002e21cac64555e9389563bf8',
    'manifest_hash': 'f74ad5d0f98efbbe5062268506b3de23800218bbe5a2c5db4c8bdd262cff9659'}
TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted', 'rejected'}
CAMERA_RAW_LIMIT = 2 * 1024**3 - 128 * 1024**2


def distance(a, b):
    return math.dist([a[k] for k in ('x', 'y', 'z')], [b[k] for k in ('x', 'y', 'z')])


def scenario_ref(value):
    return {k: value[k] for k in ('id', 'revision', 'configuration_hash', 'manifest_hash')}


def route_nodes(definition, original):
    """Resolve coincident native junction nodes by original directed edges."""
    nodes = {n['id']: n for n in definition['nodes']}
    paths = {}
    for index, point in enumerate(original):
        candidates = sorted((n for n in nodes.values() if distance(n, point) < 1e-5
            and abs(math.atan2(math.sin(n['heading_rad']-point['heading_rad']),
                              math.cos(n['heading_rad']-point['heading_rad']))) < 1e-6), key=lambda n: n['id'])
        following = {}
        for node in candidates:
            possible = [[node['id']]] if index == 0 else [path + [node['id']] for end, path in paths.items()
                if node['id'] in nodes[end]['next_ids'] and node['id'] not in path]
            if possible:
                following[node['id']] = min(possible)
        paths = following
        if not paths:
            raise ValueError('Source route must preserve every original native graph edge and pose')
    if not paths or len(original) < 2:
        raise ValueError('Original native route is incomplete')
    return [nodes[identity] for identity in min(paths.values())]


def camera_budget(scenario):
    counts = {s['id']: math.ceil(round(scenario['duration_s']/scenario['timestep_s']) /
        (s['period_s']/scenario['timestep_s'])) for s in scenario['sensors']}
    raw = sum(counts[s['id']] * s['width'] * s['height'] * 4 for s in scenario['sensors'])
    if raw > CAMERA_RAW_LIMIT:
        raise ValueError('Complete camera recording exceeds the native raw evidence budget')
    return {'frames_by_sensor': counts, 'frame_count': sum(counts.values()),
        'raw_bytes': raw, 'raw_limit_bytes': CAMERA_RAW_LIMIT}


def scenario_pair(native_map, vehicle, source, *, lead_wait_s=6, duration_s=24, camera_free=False):
    if (type(lead_wait_s) not in (int, float) or not math.isfinite(lead_wait_s) or not 0 < lead_wait_s <= 6
            or type(duration_s) not in (int, float) or not math.isfinite(duration_s) or not 18 <= duration_s <= 30):
        raise ValueError('Use lead_wait_s in (0, 6] and a bounded 18..30 second episode')
    definition = native_map['definition']
    if (definition.get('generated') is not None or definition.get('engine') != 'carla'
            or definition['native_name'] != 'Carla/Maps/Town10HD_Opt'):
        raise ValueError('The interaction requires the original native Town10HD_Opt catalog')
    source_ego = next(a for a in source['scenario']['actors'] if a['role'] == 'ego')
    original = route_nodes(definition, source_ego['route'])
    by_id = {n['id']: n for n in definition['nodes']}
    extended = original[:]; extension = 0.
    while extension < 20:
        successors = [by_id[i] for i in extended[-1]['next_ids'] if i in by_id and i not in {n['id'] for n in extended}]
        if len(successors) != 1:
            raise ValueError('Lead goal extension requires one unambiguous original native successor')
        extension += distance(extended[-1], successors[0]); extended.append(successors[0])
        if len(extended) > len(original) + 100:
            raise ValueError('Lead route extension exceeds its bounded native search')
    spacing = 0.; lead_start = None
    for index, (a, b) in enumerate(zip(original, original[1:]), 1):
        spacing += distance(a, b)
        if spacing >= 12 - .001:
            lead_start = index; break
    if lead_start is None or len(original) - lead_start < 3:
        raise ValueError('Native turn route cannot provide the initial 12 m lead spacing')
    def actor(identity, nodes):
        path = [pose(n) for n in nodes]
        return {'id': identity, 'role': 'ego' if identity == 'ego' else 'other', 'kind': 'vehicle',
            'asset': ref(vehicle), 'pose': path[0], 'route': path, 'destination': path[-1], 'speed_mps': 4,
            'controller': {'kind': 'reactive', 'end_behavior': 'stop', 'obey_signals': True},
            'planner': {'schema_version': 'object-planner/1', 'headway_s': 2, 'allow_lane_changes': False},
            'behavior': {'schema_version': 'object-behavior/1', 'risk_mode': False, 'actions': []}}
    sensors = [] if camera_free else [{'id': identity, 'actor_id': 'ego', 'kind': 'camera_rgb',
        'pose': {'x': x, 'y': 0, 'z': z, 'heading_rad': 0}, 'width': 960, 'height': 540,
        'fov_deg': 90, 'period_s': period} for identity, x, z, period in [('front', 2.8, 1.7, .05), ('chase', -8, 3, .1)]]
    scenario = {'schema_version': 'scenario-v2/3', 'name': '도심 우회전 · 앞차 정지와 재출발',
        'description': 'Pinned native Town10 right turn. The lead alone receives an authored wait in B; ego uses identical observed-state reactive control in A/B. Native map lights are fixed green to isolate the lead interaction. Original CARLA RGB, not camera-input driving or physical validation.',
        'seed': 42, 'map': ref(native_map), 'duration_s': duration_s, 'timestep_s': .05,
        'actors': [actor('ego', original), actor('lead', extended[lead_start:])], 'sensors': sensors,
        'signals': [{'id': s['id'], 'initial_state': 'green', 'changes': []}
            for s in sorted(definition.get('signals', []), key=lambda s: s['id']) if s['kind'] == 'traffic_light'],
        'acceptance': {'max_lateral_error_p95_m': .75, 'max_goal_error_m': 2, 'max_final_speed_mps': .2,
            'require_goal': True, 'require_no_contacts': True}}
    a = copy.deepcopy(scenario)
    scenario['actors'][1]['behavior']['actions'] = [{'id': 'lead_wait', 'kind': 'wait',
        'trigger': {'kind': 'time', 'value': 2, 'operator': 'gte'}, 'duration_s': lead_wait_s,
        'timeout_s': 14, 'on_timeout': 'fail'}]
    provenance = {'source_route_node_ids': [n['id'] for n in original],
        'ego_route_node_ids': [n['id'] for n in original], 'lead_route_node_ids': [n['id'] for n in extended[lead_start:]],
        'initial_center_spacing_m': spacing, 'lead_extension_beyond_ego_goal_m': extension,
        'lead_wait_s': lead_wait_s, 'wait_trigger_s': 2, 'camera_free': camera_free,
        'signal_policy': 'all_registered_native_traffic_lights_fixed_green_to_isolate_lead_response'}
    return {'A': a, 'B': scenario}, provenance, camera_budget(scenario)


def read_receipt(args, api, project):
    receipt = json.loads(args.output.read_text())
    if receipt['schema_version'] != VERSION or receipt['project_id'] != project or receipt['target_api'] != api.url:
        raise ValueError('Receipt belongs to another project, API or recipe')
    if set(receipt['scenarios']) != {'A', 'B'} or any(digest(value) != receipt['configuration_hashes'].get(side)
            for side, value in receipt['scenarios'].items()):
        raise ValueError('Authored scenarios differ from their pinned content hashes')
    trials = receipt['trials']
    if len({t['side'] for t in trials}) != len(trials) or any(t['side'] not in {'A', 'B'} for t in trials):
        raise ValueError('Receipt has ambiguous trial identities')
    if any(t['scenario_ref']['configuration_hash'] != receipt['configuration_hashes'][t['side']] for t in trials):
        raise ValueError('Trial scenario differs from its authored pin')
    return receipt


def author(args, api, project):
    catalog = json.loads(args.catalog.read_text()); suite = json.loads(args.source_suite.read_text())
    matches = [c for c in suite['cases'] if c.get('saved_scenario') == SOURCE_REF
        and c['case'] == 'right_turn' and c.get('map', {}).get('id') == MAP_ID]
    if len(matches) != 1 or digest(matches[0]['scenario']) != SOURCE_REF['configuration_hash']:
        raise ValueError('Source suite must contain the exact pinned original Town10 right-turn scenario')
    source = matches[0]
    originals = [a for a in catalog['assets'] if a.get('blueprint_id') == 'vehicle.toyota.prius']
    if len(originals) != 1:
        raise ValueError('Original native Prius asset must be unambiguous')
    input_hash = digest({'map': catalog['map'], 'vehicle': originals[0], 'source': source,
        'lead_wait_s': args.lead_wait_s, 'duration_s': args.duration_s, 'camera_free': args.camera_free,
        'recipe_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    if args.output.exists():
        receipt = read_receipt(args, api, project)
        if receipt['authoring_input_sha256'] != input_hash:
            raise ValueError('Receipt pins different authoring inputs; choose a new explicit receipt')
        return receipt
    def native_catalog(kind, identity, definition):
        existing = [item for item in api.items(kind, project) if item['id'] == identity]
        if existing:
            query = urllib.parse.urlencode({'project_id': project, 'revision': existing[0]['revision']})
            value = api.request('/' + kind + '/' + identity + '?' + query)
            if digest(value['definition']) != digest(definition):
                raise ValueError('Existing native catalog differs from captured source: ' + identity)
            return value
        return api.request('/' + kind, {'project_id': project, 'id': identity, 'expected_revision': 0, 'definition': definition})
    native_map = native_catalog('maps', MAP_ID, catalog['map'])
    vehicle = native_catalog('assets', 'vehicle-toyota-prius', originals[0])
    scenarios, recipe, budget = scenario_pair(native_map, vehicle, source, lead_wait_s=args.lead_wait_s,
        duration_s=args.duration_s, camera_free=args.camera_free)
    checked = {side: api.request('/scenario-v2/yaml/validate', {'project_id': project, 'yaml': json.dumps(value)})
        for side, value in scenarios.items()}
    receipt = {'schema_version': VERSION, 'id': uuid.uuid4().hex, 'project_id': project, 'target_api': api.url,
        'created_at': now(), 'authoring_input_sha256': input_hash, 'source_scenario_ref': SOURCE_REF,
        'catalog_file_sha256': hashlib.sha256(args.catalog.read_bytes()).hexdigest(),
        'source_suite_file_sha256': hashlib.sha256(args.source_suite.read_bytes()).hexdigest(),
        'native_map_ref': ref(native_map), 'vehicle_ref': ref(vehicle), 'recipe': recipe, 'camera_budget': budget,
        'scenarios': {side: value['scenario'] for side, value in checked.items()},
        'configuration_hashes': {side: value['configuration_hash'] for side, value in checked.items()},
        'trials': [], 'complete': False, 'published': False}
    write(args.output, receipt); return receipt


def checked_job(api, project, trial):
    job = api.request('/experiments/executions/' + trial['execution_id'])
    if job['project_id'] != project or job['id'] != trial['execution_id']:
        raise ValueError('Execution does not belong to the selected project and trial')
    return job


def run(args, api, project):
    receipt = read_receipt(args, api, project)
    for side in ('A', 'B') if args.side == 'all' else (args.side,):
        trial = next((t for t in receipt['trials'] if t['side'] == side), None)
        if trial is None:
            scenario = receipt['scenarios'][side]; key = receipt['id'] + ':' + side
            saved = api.request('/scenario-v2', {'project_id': project, 'scenario': scenario}, key + ':scenario')
            if saved['project_id'] != project or saved['configuration_hash'] != receipt['configuration_hashes'][side]:
                raise ValueError('Saved scenario differs from selected project and authored configuration')
            experiment = api.request('/experiments', {'project_id': project, 'name': scenario['name'] + ' · ' + side,
                'kind': 'object_sim', 'adapter_id': 'carla-city-object', 'adapter_version': 'carla-city-object/1.0.0',
                'comparison_kind': 'single', 'seed': scenario['seed'], 'input': {'scenario_id': saved['id'],
                    'scenario_revision': saved['revision'], 'configuration_hash': saved['configuration_hash']}}, key + ':experiment')
            job = api.request('/experiments/' + experiment['id'] + '/executions', {'preview_hash': experiment['preview_hash']}, key + ':execution')
            trial = {'side': side, 'execution_id': job['id'], 'scenario_ref': scenario_ref(saved)}
            receipt['trials'].append(trial); write(args.output, receipt)
        deadline = time.monotonic() + args.timeout
        while True:
            job = checked_job(api, project, trial)
            trial.update({k: job.get(k) for k in ('status', 'summary', 'error', 'manifest_hash', 'outcome_uncertain')})
            write(args.output, receipt)
            if job['status'] in TERMINAL: break
            if time.monotonic() >= deadline:
                raise ValueError('Wait expired; same receipt resumes the retained job without resubmission')
            time.sleep(2)
        if job['status'] != 'completed' or job.get('outcome_uncertain') or not job.get('manifest_hash'):
            raise ValueError('Execution did not complete with a sealed certain outcome; inspect the retained trial')
        print(json.dumps({'side': side, 'execution_id': job['id'], 'status': job['status'],
            'verdict': job['summary'].get('verification_verdict'), 'camera_frames': job['summary'].get('camera_frame_count')}), flush=True)
    receipt['complete'] = len(receipt['trials']) == 2 and all(t.get('status') == 'completed'
        and not t.get('outcome_uncertain') and t.get('manifest_hash') for t in receipt['trials'])
    if receipt['complete']: receipt['finished_at'] = now()
    write(args.output, receipt); return receipt


def publish(args, api, project):
    receipt = read_receipt(args, api, project)
    if not receipt['complete'] or receipt['recipe']['camera_free']:
        raise ValueError('Only the completed retained-camera A/B pair can be published')
    if receipt['published']: return receipt
    trials = {t['side']: t for t in receipt['trials']}
    if set(trials) != {'A', 'B'}: raise ValueError('Both explicit A/B trials are required')
    for trial in trials.values():
        job = checked_job(api, project, trial)
        if job['status'] != 'completed' or job.get('outcome_uncertain') or job['manifest_hash'] != trial['manifest_hash']:
            raise ValueError('Stored execution no longer matches the sealed trial')
    comparison = api.request('/object-sim/comparison?' + urllib.parse.urlencode({'project_id': project,
        'left_execution_id': trials['A']['execution_id'], 'right_execution_id': trials['B']['execution_id']}))
    if not comparison['compatibility']['compatible']:
        raise ValueError('A/B execution provenance is incompatible; do not publish')
    for side, name in [('A', 'left'), ('B', 'right')]:
        if comparison[name]['scenario_ref'] != trials[side]['scenario_ref']:
            raise ValueError('Comparison scenario differs from the recorded trial')
    preset = next(p for p in api.request('/object-sim/presets?' + urllib.parse.urlencode({'project_id': project}))['items'] if p['id'] == PRESET)
    expected = {'scenario_ref': trials['B']['scenario_ref'], 'saved_execution_id': trials['B']['execution_id'],
        'saved_comparison_execution_id': trials['A']['execution_id']}
    if preset['binding_revision'] is not None:
        if not preset['ready'] or any(preset.get(k) != v for k, v in expected.items()):
            raise ValueError('Existing right-turn preset preserved; use an explicit reviewed revision')
        revision = preset['binding_revision']
    else:
        bound = api.request('/object-sim/presets/' + PRESET + '/bind', {'project_id': project,
            'expected_revision': 0, 'actor_roles': {'ego': 'ego', 'lead': 'lead'}, **expected})
        revision = bound['revision']
    receipt.update(published=True, binding_revision=revision, published_at=now())
    write(args.output, receipt); return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['author', 'run', 'publish'])
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--catalog', type=Path)
    parser.add_argument('--source-suite', type=Path)
    parser.add_argument('--lead-wait-s', type=float, default=6)
    parser.add_argument('--duration-s', type=float, default=24)
    parser.add_argument('--camera-free', action='store_true')
    parser.add_argument('--side', choices=['A', 'B', 'all'], default='all')
    parser.add_argument('--timeout', type=float, default=900)
    args = parser.parse_args()
    if args.action == 'author' and (not args.catalog or not args.source_suite):
        parser.error('author requires --catalog and --source-suite')
    prepared = json.loads(args.prepared.read_text())
    api = API(prepared['target_api'], args.token_file.read_text().strip())
    receipt = globals()[args.action](args, api, prepared['project_id'])
    print(json.dumps({'output': str(args.output), 'complete': receipt['complete'], 'published': receipt['published']}))


if __name__ == '__main__':
    main()
