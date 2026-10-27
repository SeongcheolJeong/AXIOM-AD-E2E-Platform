#!/usr/bin/env python3
"""Author, run and explicitly publish a native Town10 city presentation.

Uses the normal project catalog/scenario/Experiment APIs. No engine mesh export,
direct CARLA connection, hidden retries or changes to the existing three presets.
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

VERSION = 'object-sim-urban-demo/1'
TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted', 'rejected'}


def scenario_definition(native_map, vehicle, source, *, probe=False):
    """Every route point comes from the selected native graph, not remapped XY."""
    definition = native_map['definition']
    if definition.get('generated') or definition['native_name'].split('/')[-1] != 'Town10HD_Opt':
        raise ValueError('This bounded city recipe requires the captured native Town10HD_Opt')
    original = source['scenario']['actors'][0]['route']
    nodes = definition['nodes']
    def nearest(point):
        candidates = [node for node in nodes if not node['is_junction']]
        node = min(candidates, key=lambda n: math.dist([n[k] for k in ('x', 'y', 'z')], [point[k] for k in ('x', 'y', 'z')]))
        if math.dist([node[k] for k in ('x', 'y', 'z')], [point[k] for k in ('x', 'y', 'z')]) > .05:
            raise ValueError('Source route is not present in this captured native graph')
        return node
    route = [pose(nearest(point)) for point in original]
    start_node = nearest(original[0])
    opposite = sorted((n for n in nodes if n['road_id'] == start_node['road_id'] and
        n['section_id'] == start_node['section_id'] and n['lane_id'] == -start_node['lane_id'] and not n['is_junction']), key=lambda n: n['x'])
    if len(opposite) < 20 or start_node['road_id'] != 12:
        raise ValueError('Expected native two-way boulevard route is unavailable')
    def lane_route(lo, hi):
        selected = [n for n in opposite if lo <= n['x'] <= hi]
        if len(selected) < 2 or any(b['id'] not in a['next_ids'] for a, b in zip(selected, selected[1:])):
            raise ValueError('Oncoming route must follow every native graph edge')
        return [pose(n) for n in selected]
    def actor(identity, path, speed, actions=None):
        return {'id': identity, 'role': 'ego' if identity == 'ego' else 'other', 'kind': 'vehicle',
            'asset': ref(vehicle), 'pose': path[0], 'route': path, 'destination': path[-1], 'speed_mps': speed,
            'controller': {'kind': 'reactive', 'end_behavior': 'stop', 'obey_signals': True},
            'planner': {'schema_version': 'object-planner/1', 'allow_lane_changes': False},
            'behavior': {'schema_version': 'object-behavior/1', 'risk_mode': False, 'actions': actions or []}}
    actors = [actor('ego', route, 4, [{'id': 'brief_wait', 'kind': 'wait',
        'trigger': {'kind': 'time', 'value': 3, 'operator': 'gte'}, 'duration_s': 2, 'timeout_s': 12, 'on_timeout': 'fail'}]),
        actor('oncoming_front', lane_route(-7, 26), 3), actor('oncoming_rear', lane_route(-27, 6), 3)]
    sensors = [{'id': identity, 'actor_id': 'ego', 'kind': 'camera_rgb',
        'pose': {'x': x, 'y': 0, 'z': z, 'heading_rad': 0}, 'width': 960, 'height': 540,
        'fov_deg': fov, 'period_s': .1} for identity, x, z, fov in [('front', 2.8, 1.7, 90), ('chase', -8, 3, 90)]]
    return {'schema_version': 'scenario-v2/3', 'name': '도심 주행 · Town10HD · 전방/추적',
        'description': 'Native Town10HD_Opt buildings and streets, three reactive vehicles, an authored two-second wait and destination stop. Camera images are original CARLA renders. No sensor-input policy or real-world validation claim.',
        'seed': 42, 'map': ref(native_map), 'duration_s': 6 if probe else 24, 'timestep_s': .05,
        'actors': actors, 'sensors': sensors,
        'acceptance': {'max_lateral_error_p95_m': .75, 'max_goal_error_m': 2, 'max_final_speed_mps': .2,
            'require_goal': True, 'require_no_contacts': True}}


def author(args, api, project):
    catalog = json.loads(args.catalog.read_text())
    suite = json.loads(args.source_suite.read_text())
    source = next(c for c in suite['cases'] if c.get('map', {}).get('id') == 'carla-town10hd-opt' and c['case'] == 'straight')
    input_hash = digest({'catalog': catalog['map'], 'source': source, 'probe': args.probe})
    if args.output.exists():
        receipt = json.loads(args.output.read_text())
        if receipt['project_id'] != project or receipt['authoring_input_sha256'] != input_hash:
            raise ValueError('Existing receipt pins another project or input')
        return receipt
    maps = api.items('maps', project)
    native_map = next((m for m in maps if m['id'] == 'carla-town10hd-opt'), None)
    if native_map:
        query = urllib.parse.urlencode({'project_id': project, 'revision': native_map['revision']})
        native_map = api.request('/maps/' + native_map['id'] + '?' + query)
        if digest(native_map['definition']) != digest(catalog['map']):
            raise ValueError('Existing native map definition differs; preserve it and choose an explicit revision')
    else:
        native_map = api.request('/maps', {'project_id': project, 'id': 'carla-town10hd-opt',
            'expected_revision': 0, 'definition': catalog['map']})
    vehicle = next(a for a in api.items('assets', project) if a['definition']['blueprint_id'] == 'vehicle.toyota.prius')
    scenario = scenario_definition(native_map, vehicle, source, probe=args.probe)
    checked = api.request('/scenario-v2/yaml/validate', {'project_id': project, 'yaml': json.dumps(scenario)})
    receipt = {'schema_version': VERSION, 'id': uuid.uuid4().hex, 'project_id': project, 'created_at': now(),
        'authoring_input_sha256': input_hash, 'catalog_file_sha256': hashlib.sha256(args.catalog.read_bytes()).hexdigest(),
        'source_scenario_ref': source['saved_scenario'], 'native_map_ref': ref(native_map),
        'scenario': checked['scenario'], 'configuration_hash': checked['configuration_hash'], 'probe': args.probe,
        'trials': [], 'complete': False, 'published': False}
    write(args.output, receipt)
    return receipt


def run(args, api, project):
    receipt = json.loads(args.output.read_text())
    if receipt['schema_version'] != VERSION or receipt['project_id'] != project:
        raise ValueError('Wrong urban receipt')
    if digest(receipt['scenario']) != receipt['configuration_hash']:
        raise ValueError('Authored scenario content differs from its pinned hash')
    for side, speed in [('A', 4)] if receipt['probe'] else [('A', 4), ('B', 6)]:
        trial = next((t for t in receipt['trials'] if t['side'] == side), None)
        if trial is None:
            scenario = copy.deepcopy(receipt['scenario'])
            scenario['actors'][0]['speed_mps'] = speed
            key = receipt['id'] + ':' + side
            saved = api.request('/scenario-v2', {'project_id': project, 'scenario': scenario}, key + ':scenario')
            experiment = api.request('/experiments', {'project_id': project, 'name': scenario['name'] + ' · ' + side,
                'kind': 'object_sim', 'adapter_id': 'carla-city-object', 'adapter_version': 'carla-city-object/1.0.0',
                'comparison_kind': 'single', 'seed': scenario['seed'], 'input': {'scenario_id': saved['id'],
                'scenario_revision': saved['revision'], 'configuration_hash': saved['configuration_hash']}}, key + ':experiment')
            job = api.request('/experiments/' + experiment['id'] + '/executions', {'preview_hash': experiment['preview_hash']}, key + ':execution')
            trial = {'side': side, 'speed_mps': speed, 'execution_id': job['id'],
                'scenario_ref': {k: saved[k] for k in ('id', 'revision', 'configuration_hash', 'manifest_hash')}}
            receipt['trials'].append(trial); write(args.output, receipt)
        deadline = time.monotonic() + args.timeout
        while True:
            job = api.request('/experiments/executions/' + trial['execution_id'])
            trial.update({k: job.get(k) for k in ['status', 'summary', 'error', 'manifest_hash', 'outcome_uncertain']})
            write(args.output, receipt)
            if job['status'] in TERMINAL:
                break
            if time.monotonic() >= deadline:
                raise ValueError('Wait expired; existing job retained. Run with the same receipt to resume.')
            time.sleep(2)
        if job['status'] != 'completed' or job.get('outcome_uncertain'):
            raise ValueError('Urban execution did not complete; inspect the retained receipt')
        print(json.dumps({'side': side, 'execution_id': job['id'], 'status': job['status'],
            'verdict': job['summary']['verification_verdict'], 'camera_frames': job['summary']['camera_frame_count']}), flush=True)
    receipt.update(complete=True, finished_at=now()); write(args.output, receipt)
    return receipt


def publish(args, api, project):
    receipt = json.loads(args.output.read_text())
    if receipt['probe'] or not receipt['complete'] or receipt['project_id'] != project:
        raise ValueError('Only the completed full urban recording can become a preset')
    if receipt['published']:
        return receipt
    trial = next(t for t in receipt['trials'] if t['side'] == 'A')
    job = api.request('/experiments/executions/' + trial['execution_id'])
    if job['status'] != 'completed' or job['manifest_hash'] != trial['manifest_hash']:
        raise ValueError('Stored urban result changed')
    preset = next(p for p in api.request('/object-sim/presets?project_id=' + project)['items'] if p['id'] == 'urban_city')
    if preset['binding_revision'] is not None:
        if (not preset['ready'] or preset['scenario_ref'] != trial['scenario_ref']
                or preset['saved_execution_id'] != trial['execution_id']):
            raise ValueError('Existing city preset binding preserved; review an explicit new revision')
        # Recover the write-after-bind interruption without replacing a binding
        # or appending another catalog revision.
        bound = {'revision': preset['binding_revision']}
    else:
        bound = api.request('/object-sim/presets/urban_city/bind', {'project_id': project, 'expected_revision': 0,
            'scenario_ref': trial['scenario_ref'], 'actor_roles': {'ego': 'ego'}, 'saved_execution_id': trial['execution_id']})
    receipt.update(published=True, binding_revision=bound['revision'], published_at=now()); write(args.output, receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['author', 'run', 'publish'])
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--catalog', type=Path)
    parser.add_argument('--source-suite', type=Path)
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--timeout', type=float, default=600)
    args = parser.parse_args()
    prepared = json.loads(args.prepared.read_text())
    api = API(prepared['target_api'], args.token_file.read_text().strip())
    if args.action == 'author' and (not args.catalog or not args.source_suite):
        parser.error('author requires --catalog and --source-suite')
    receipt = globals()[args.action](args, api, prepared['project_id'])
    print(json.dumps({'output': str(args.output), 'complete': receipt['complete'], 'published': receipt['published']}))


if __name__ == '__main__':
    main()
