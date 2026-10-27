#!/usr/bin/env python3
"""Author bounded Korea-map trials from qualified native lanes via public APIs.

Reads an operator-captured CARLA catalog for a Prius asset. Does not submit
execution, change engine state, infer survey accuracy or register a second map.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

from carla_city_acceptance import API, FROZEN, choose_paths, digest, length, pose, ref


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api', required=True)
    parser.add_argument('--project', default='default')
    parser.add_argument('--token-file', type=Path)
    parser.add_argument('--map-id', required=True)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Preserve existing authoring receipt; select a new output path')
    token = args.token_file.read_text().strip() if args.token_file else os.environ.get('ADE2E_API_TOKEN')
    api = API(args.api, token)
    native_map = api.request(f'/maps/{args.map_id}?project_id={args.project}')
    definition = native_map['definition']
    if not definition.get('external'):
        raise ValueError('Pilot requires a source-pinned externally qualified map')
    catalog = json.loads(args.catalog.read_bytes())
    if catalog['map']['opendrive']['sha256'] != definition['opendrive']['sha256']:
        raise ValueError('Captured catalog does not describe the qualified source map')
    vehicle = next(a for a in catalog['assets'] if a['blueprint_id'] == 'vehicle.toyota.prius')

    def register(kind, identity, value):
        current = next((item for item in api.items(kind, args.project) if item['id'] == identity), None)
        if current:
            if current['definition'] != value:
                raise ValueError(f'Existing {kind}/{identity} differs; preserve it')
            return current
        return api.request('/' + kind, {'project_id': args.project, 'id': identity,
            'expected_revision': 0, 'definition': value})

    asset = register('assets', 'vehicle-toyota-prius', vehicle)
    controller = register('controllers', 'korea-pilot-route-follow', {
        'resource_kind': 'controller', 'name': 'Korea pilot · native lane feedback',
        'adapter_id': 'carla-city-object', 'adapter_version': 'carla-city-object/1.0.0',
        'mode': 'route_follow', 'status': 'available',
        'image_digest': None,
        'description': 'Native route feedback. Availability is not a successful driving result.',
        'capabilities': ['end_stop', 'native_trace', 'control_math:axiom-city-pure-pursuit/2',
                         'rear_axle_steering', 'preserved_origin_metrics'],
        'rights': {'license': 'Workspace implementation; see repository licensing',
                   'source_url': 'local:axiom-city-control', 'redistribution': 'unknown',
                   'notes': 'Code rights do not cover engine, assets or map data.'}})
    paths = choose_paths(definition)
    result = {'schema_version': 'korea-map-pilot-authoring/1', 'project_id': args.project,
              'map_ref': ref(native_map), 'asset_ref': ref(asset), 'controller_ref': ref(controller),
              'scope': 'authored_trials_not_execution_or_physical_validation', 'trials': []}
    for kind, speed in [('straight', 3.), ('left_turn', 3.), ('right_turn', 3.), ('left_turn', 2.)]:
        route = paths.get(kind)
        if route is None:
            result['trials'].append({'kind': kind, 'speed_mps': speed, 'status': 'unsupported',
                'reason': 'No native directed route meets the frozen geometry bounds'})
            continue
        scenario = {'schema_version': 'scenario-v2/2',
            'name': f'세종 OSM · {kind} · {speed:g} m/s',
            'description': 'OSM-derived procedural road; authored trial on native directed lane edges. '
                           'No surveyed geometry, signal timing or sensor accuracy claim.',
            'seed': 42, 'map': ref(native_map), 'duration_s': math.ceil(length(route) / speed + 8),
            'timestep_s': .05,
            'actors': [{'id': 'ego', 'kind': 'vehicle', 'role': 'ego', 'asset': ref(asset),
                'pose': pose(route[0]), 'route': [pose(node) for node in route], 'speed_mps': speed,
                'controller': {'kind': 'route_follow', 'ref': ref(controller),
                               'end_behavior': 'stop', 'obey_signals': True}}],
            'sensors': [{'id': 'front', 'actor_id': 'ego', 'width': 640, 'height': 360, 'period_s': .2}],
            'acceptance': dict(FROZEN)}
        validation = api.request('/scenario-v2/yaml/validate',
            {'project_id': args.project, 'yaml': json.dumps(scenario, ensure_ascii=False)})
        saved = api.request('/scenario-v2/yaml/import',
            {'project_id': args.project, 'yaml': validation['canonical_yaml']},
            'korea-pilot-author:' + digest(scenario))
        result['trials'].append({'kind': kind, 'speed_mps': speed, 'status': 'authored',
            'route_length_m': length(route), 'native_node_ids': [n['id'] for n in route],
            'scenario': saved, 'configuration_hash': validation['configuration_hash']})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'output': str(args.output), 'trials': [
        {'kind': t['kind'], 'speed_mps': t['speed_mps'], 'status': t['status'],
         'scenario_id': t.get('scenario', {}).get('id')} for t in result['trials']]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
