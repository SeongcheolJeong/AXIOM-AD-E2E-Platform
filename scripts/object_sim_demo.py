#!/usr/bin/env python3
"""Prepare and rehearse an isolated Object Sim demo through normal public APIs.

Source selections contain exact scenario revisions and hashes. Generated maps
are rebuilt from their original authored sources; assets keep their own rights.
Preparation never submits simulation. Rehearsal submits five explicit runs.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import sys
import time
import urllib.parse
import uuid

from carla_city_acceptance import API, digest, now, ref

ROOT = Path(__file__).resolve().parents[1]
PRESETS = ('obstacle_bypass', 'gap_release', 'junction_eight')
TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted'}


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    temporary.replace(path)


def api(url, token_file):
    return API(url, Path(token_file).read_text().strip() if token_file else None)


def scenario_ref(value):
    return {k: value[k] for k in ('id', 'revision', 'configuration_hash', 'manifest_hash')}


def catalog(source, kind, project, reference):
    query = urllib.parse.urlencode({'project_id': project, 'revision': reference['revision']})
    value = source.request(f'/{kind}/{reference["id"]}?{query}')
    if value['sha256'] != reference['sha256']:
        raise ValueError('Source catalog changed: ' + kind)
    return value


def prepare(args):
    source, target = api(args.source_api, args.source_token_file), api(args.api, args.token_file)
    selection = json.loads(args.selection.read_text())
    if set(selection['presets']) != set(PRESETS):
        raise ValueError('Select exactly the three explicit presets')
    receipt_path = args.output
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if receipt['selection_sha256'] != digest(selection) or receipt['target_api'] != args.api:
            raise ValueError('Receipt belongs to another selection or target; choose a new output')
    else:
        project = target.request('/projects', {'name': args.name})
        receipt = {'schema_version': 'object-sim-demo-preparation/1', 'created_at': now(),
                   'selection_sha256': digest(selection), 'target_api': args.api,
                   'project_id': project['id'], 'presets': {}, 'native_execution_submitted': False}
        write(receipt_path, receipt)
    project = receipt['project_id']
    maps, assets, controllers = {}, {}, {}
    for identity in PRESETS:
        chosen = selection['presets'][identity]
        saved = source.request('/scenario-v2/' + chosen['scenario_ref']['id'] + '?revision=' + str(chosen['scenario_ref']['revision']))
        if scenario_ref(saved) != chosen['scenario_ref'] or saved['project_id'] != selection['source_project_id']:
            raise ValueError('Selected source scenario differs from its exact pin: ' + identity)
        scenario = copy.deepcopy(saved['scenario'])
        map_key = digest(scenario['map'])
        if map_key not in maps:
            original = catalog(source, 'maps', saved['project_id'], scenario['map'])
            generated = original['definition'].get('generated')
            if generated:
                authored = catalog(source, 'object-maps', saved['project_id'], generated['source'])
                created = target.request('/object-maps', {'project_id': project, 'id': authored['id'],
                    'expected_revision': 0, 'definition': authored['definition']})
                built = target.request('/object-maps/' + created['id'] + '/build', {
                    'project_id': project, 'revision': created['revision'], 'sha256': created['sha256'],
                    'expected_map_revision': 0, 'server_version': original['definition']['server_version']})
                maps[map_key] = built['map_ref']
            else:
                maps[map_key] = ref(target.request('/maps', {'project_id': project, 'id': original['id'],
                    'expected_revision': 0, 'definition': original['definition']}))
        scenario['map'] = maps[map_key]
        for actor in scenario['actors']:
            for kind, cache, reference in [('assets', assets, actor['asset']), ('controllers', controllers, actor['controller'].get('ref'))]:
                if not reference:
                    continue
                key = digest(reference)
                if key not in cache:
                    original = catalog(source, kind, saved['project_id'], reference)
                    cache[key] = ref(target.request('/' + kind, {'project_id': project, 'id': original['id'],
                        'expected_revision': 0, 'definition': original['definition']}))
                if kind == 'assets':
                    actor['asset'] = cache[key]
                else:
                    actor['controller']['ref'] = cache[key]
        created = target.request('/scenario-v2', {'project_id': project, 'scenario': scenario},
                                 'demo-prepare:' + project + ':' + identity + ':' + digest(scenario)[:16])
        old = receipt['presets'].get(identity)
        if old:
            if old['scenario_ref'] != scenario_ref(created):
                raise ValueError('Prepared scenario differs from existing receipt')
        else:
            binding = target.request('/object-sim/presets/' + identity + '/bind', {
                'project_id': project, 'expected_revision': 0, 'scenario_ref': scenario_ref(created),
                'actor_roles': chosen['actor_roles']})
            receipt['presets'][identity] = {'scenario_ref': scenario_ref(created),
                'source_scenario_ref': scenario_ref(saved), 'actor_roles': chosen['actor_roles'],
                'binding_revision': binding['revision']}
            write(receipt_path, receipt)
    receipt.update(completed_at=now(), complete=True,
                   url=args.api.removesuffix('/api/v1') + '/#object-sim?' + urllib.parse.urlencode({'project': project}))
    write(receipt_path, receipt)
    return receipt


def rehearse(args):
    target = api(args.api, args.token_file)
    prepared = json.loads(args.prepared.read_text())
    if prepared['target_api'] != args.api or not prepared.get('complete'):
        raise ValueError('Use a completed preparation receipt for this target')
    project = prepared['project_id']
    if args.output.exists():
        result = json.loads(args.output.read_text())
        if result['project_id'] != project:
            raise ValueError('Rehearsal receipt belongs to another project')
    else:
        result = {'schema_version': 'object-sim-demo-rehearsal/1', 'id': uuid.uuid4().hex,
                  'project_id': project, 'started_at': now(), 'complete': False, 'trials': []}
        write(args.output, result)
    trials = [('obstacle_bypass', 'A', {}), ('obstacle_bypass', 'B', {'allow_lane_changes': False}),
              ('gap_release', 'A', {}), ('gap_release', 'B', {'rear_speed_mps': 5.0, 'rear_gap_m': 6.0}),
              ('junction_eight', 'A', {})]
    for identity, side, parameters in trials:
        key = identity + ':' + side
        trial = next((x for x in result['trials'] if x['key'] == key), None)
        if trial is None:
            preview = target.request('/object-sim/presets/' + identity + '/preview', {'project_id': project, 'parameters': parameters})
            saved = target.request('/scenario-v2', {'project_id': project, 'scenario': preview['scenario']},
                                   result['id'] + ':' + key + ':scenario')
            spec = {'project_id': project, 'name': identity + ' · ' + side, 'kind': 'object_sim',
                    'adapter_id': 'carla-city-object', 'adapter_version': 'carla-city-object/1.0.0',
                    'comparison_kind': 'single', 'seed': saved['scenario']['seed'], 'input': {
                        'scenario_id': saved['id'], 'scenario_revision': saved['revision'],
                        'configuration_hash': saved['configuration_hash']}}
            experiment = target.request('/experiments', spec, result['id'] + ':' + key + ':experiment')
            job = target.request('/experiments/' + experiment['id'] + '/executions',
                                 {'preview_hash': experiment['preview_hash']}, result['id'] + ':' + key + ':execution')
            trial = {'key': key, 'preset': identity, 'side': side, 'parameters': parameters,
                     'scenario_ref': scenario_ref(saved), 'execution_id': job['id'], 'status': job['status']}
            result['trials'].append(trial)
            write(args.output, result)
        deadline = time.monotonic() + args.timeout
        while True:
            job = target.request('/experiments/executions/' + trial['execution_id'])
            trial.update(status=job['status'], summary=job.get('summary'), error=job.get('error'),
                         manifest_hash=job.get('manifest_hash'), outcome_uncertain=job.get('outcome_uncertain'))
            write(args.output, result)
            if job['status'] in TERMINAL:
                break
            if time.monotonic() >= deadline:
                raise ValueError('Rehearsal wait timed out; existing job preserved. Reuse the same output to resume.')
            time.sleep(2)
        if job['status'] != 'completed' or job.get('outcome_uncertain'):
            raise ValueError('Native run did not complete: ' + key + '; inspect the saved receipt')
        print(json.dumps({'trial': key, 'execution_id': job['id'], 'status': job['status']}), flush=True)
        if side == 'A' and not trial.get('bound'):
            current = target.request('/object-sim/presets?' + urllib.parse.urlencode({'project_id': project}))
            preset = next(item for item in current['items'] if item['id'] == identity)
            if preset['scenario_ref']['configuration_hash'] != trial['scenario_ref']['configuration_hash']:
                raise ValueError('Preset configuration changed during rehearsal; existing run preserved')
            binding = target.request('/object-sim/presets/' + identity + '/bind', {
                'project_id': project, 'expected_revision': preset['binding_revision'],
                'scenario_ref': trial['scenario_ref'], 'actor_roles': prepared['presets'][identity]['actor_roles'],
                'saved_execution_id': job['id']})
            trial.update(bound=True, binding_revision=binding['revision'])
            write(args.output, result)
    result['comparisons'] = {}
    for identity in PRESETS[:2]:
        pair = [x for x in result['trials'] if x['preset'] == identity]
        query = {'project_id': project, 'left_execution_id': pair[0]['execution_id'], 'right_execution_id': pair[1]['execution_id']}
        result['comparisons'][identity] = target.request('/object-sim/comparison?' + urllib.parse.urlencode(query))
    result.update(complete=True, finished_at=now())
    write(args.output, result)
    return {'complete': True, 'trials': len(result['trials']), 'output': str(args.output)}


def film(args):
    """New immutable camera revisions; bind completed baselines only on request."""
    target = api(args.api, args.token_file)
    prepared = json.loads(args.prepared.read_text())
    if prepared['target_api'] != args.api or not prepared.get('complete'):
        raise ValueError('Use a completed preparation receipt for this target')
    sensors = json.loads(args.camera_profile.read_text())['sensors']
    project = prepared['project_id']
    if args.probe and args.publish_baselines:
        raise ValueError('A framing probe cannot publish demo baselines')
    if args.output.exists():
        result = json.loads(args.output.read_text())
        if (result['project_id'], result['camera_profile_sha256'], result['probe']) != (project, digest(sensors), args.probe):
            raise ValueError('Film receipt belongs to another project, profile or purpose')
    else:
        presets = target.request('/object-sim/presets?' + urllib.parse.urlencode({'project_id': project}))
        result = {'schema_version': 'object-sim-demo-film/1', 'id': uuid.uuid4().hex, 'project_id': project,
                  'started_at': now(), 'complete': False, 'camera_profile_sha256': digest(sensors),
                  'camera_profile': sensors, 'probe': args.probe, 'trials': [],
                  'source_bindings': {item['id']: {'revision': item['binding_revision'], 'scenario_ref': item['scenario_ref']}
                                      for item in presets['items']}}
        write(args.output, result)
    cases = [('obstacle_bypass', 'A', {}), ('obstacle_bypass', 'B', {'allow_lane_changes': False}),
             ('gap_release', 'A', {}), ('gap_release', 'B', {'rear_speed_mps': 5.0, 'rear_gap_m': 6.0}),
             ('junction_eight', 'A', {})]
    if args.probe:
        cases = cases[:1]
    for identity, side, parameters in cases:
        key = identity + ':' + side
        trial = next((item for item in result['trials'] if item['key'] == key), None)
        if trial is None:
            preview = target.request('/object-sim/presets/' + identity + '/preview', {'project_id': project, 'parameters': parameters})
            if preview['scenario_ref'] != result['source_bindings'][identity]['scenario_ref']:
                raise ValueError('Preset changed during filming; prior runs preserved')
            scenario = preview['scenario']
            scenario['sensors'] = copy.deepcopy(sensors)
            scenario['name'] += ' · ' + ('camera framing probe' if args.probe else 'dual camera')
            if args.probe:
                scenario['duration_s'] = 6.0
            saved = target.request('/scenario-v2', {'project_id': project, 'scenario': scenario}, result['id'] + ':' + key + ':scenario')
            experiment = target.request('/experiments', {'project_id': project, 'name': scenario['name'] + ' · ' + side,
                'kind': 'object_sim', 'adapter_id': 'carla-city-object', 'adapter_version': 'carla-city-object/1.0.0',
                'comparison_kind': 'single', 'seed': scenario['seed'], 'input': {'scenario_id': saved['id'],
                'scenario_revision': saved['revision'], 'configuration_hash': saved['configuration_hash']}}, result['id'] + ':' + key + ':experiment')
            job = target.request('/experiments/' + experiment['id'] + '/executions', {'preview_hash': experiment['preview_hash']}, result['id'] + ':' + key + ':execution')
            trial = {'key': key, 'preset': identity, 'side': side, 'parameters': parameters,
                     'scenario_ref': scenario_ref(saved), 'execution_id': job['id'], 'status': job['status']}
            result['trials'].append(trial); write(args.output, result)
        deadline = time.monotonic() + args.timeout
        while True:
            job = target.request('/experiments/executions/' + trial['execution_id'])
            trial.update(status=job['status'], summary=job.get('summary'), error=job.get('error'),
                         manifest_hash=job.get('manifest_hash'), outcome_uncertain=job.get('outcome_uncertain'))
            write(args.output, result)
            if job['status'] in TERMINAL:
                break
            if time.monotonic() >= deadline:
                raise ValueError('Film wait timed out; existing job preserved. Resume with the same output.')
            time.sleep(2)
        if job['status'] != 'completed' or job.get('outcome_uncertain'):
            raise ValueError('Camera filming did not complete: ' + key)
        print(json.dumps({'trial': key, 'execution_id': job['id'], 'status': job['status']}), flush=True)
    result.update(complete=True, finished_at=now()); write(args.output, result)
    if args.publish_baselines:
        for trial in result['trials']:
            if trial['side'] != 'A' or trial.get('binding_revision'):
                continue
            identity = trial['preset']
            binding = target.request('/object-sim/presets/' + identity + '/bind', {'project_id': project,
                'expected_revision': result['source_bindings'][identity]['revision'], 'scenario_ref': trial['scenario_ref'],
                'actor_roles': prepared['presets'][identity]['actor_roles'], 'saved_execution_id': trial['execution_id']})
            trial['binding_revision'] = binding['revision']; write(args.output, result)
        result['baselines_published'] = True; write(args.output, result)
    return {'complete': True, 'probe': args.probe, 'trials': len(result['trials']),
            'baselines_published': result.get('baselines_published', False), 'output': str(args.output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    serve = commands.add_parser('serve', help='Run the existing isolated launcher with a frozen UI and explicit engine configuration')
    serve.add_argument('--runtime', type=Path, required=True)
    serve.add_argument('--engine-env', type=Path, required=True)
    serve.add_argument('--port', type=int, default=8033)
    serve.add_argument('--resume-queued', action='store_true')
    for name in ('prepare', 'rehearse', 'film'):
        command = commands.add_parser(name)
        command.add_argument('--api', default='http://127.0.0.1:8033/api/v1')
        command.add_argument('--token-file', type=Path, required=True)
        command.add_argument('--output', type=Path, required=True)
        if name == 'prepare':
            command.add_argument('--source-api', required=True)
            command.add_argument('--source-token-file', type=Path)
            command.add_argument('--selection', type=Path, required=True)
            command.add_argument('--name', default='Object Sim Demo')
        else:
            command.add_argument('--prepared', type=Path, required=True)
            command.add_argument('--timeout', type=int, default=900)
            if name == 'film':
                command.add_argument('--camera-profile', type=Path, required=True)
                command.add_argument('--probe', action='store_true', help='One 6-second framing capture; never a driving acceptance run')
                command.add_argument('--publish-baselines', action='store_true', help='After all five captures complete, append preset bindings to their new A executions')
    args = parser.parse_args()
    if args.command == 'serve':
        environment = os.environ.copy()
        configured = json.loads(args.engine_env.read_text())
        if any(not key.startswith('AXIOM_') or not isinstance(value, str) for key, value in configured.items()):
            raise ValueError('Engine environment must contain AXIOM_ string entries only')
        environment.update(configured)
        environment['PYTHONPATH'] = os.pathsep.join(str(ROOT / path) for path in ('backend/src', 'scripts', 'integrations/opendrive/src'))
        command = [sys.executable, '-B', str(ROOT / 'scripts/internal_platform.py'), '--workspace',
                   str(args.runtime / 'workspace'), '--state', str(args.runtime / 'access'), '--port', str(args.port),
                   '--ui', str(ROOT / 'frontend/dist'), '--enable-configured-engines']
        if args.resume_queued:
            command.append('--resume-queued')
        os.execvpe(sys.executable, command, environment)
    else:
        operation = {'prepare': prepare, 'rehearse': rehearse, 'film': film}[args.command]
        print(json.dumps(operation(args), ensure_ascii=False))


if __name__ == '__main__':
    main()
