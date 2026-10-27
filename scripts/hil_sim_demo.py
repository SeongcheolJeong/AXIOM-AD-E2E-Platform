#!/usr/bin/env python3
"""Prepare/rehearse a native CARLA software-DUT demo via public APIs.

Imports pinned native catalogs into a new project. Does not mutate the source
workspace. A resumable receipt retains failed/uncertain runs without retrying.
"""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
import time
import urllib.parse
import uuid
from carla_city_acceptance import API, definitions, digest, now, ref
from object_sim_demo import catalog, scenario_ref
from sensor_sim_demo import write

PRESETS = ('input_delay', 'input_dropout')
TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted'}


def connection(args):
    return API(args.api, args.token_file.read_text().strip() if args.token_file else None)


def presets(target, project):
    return {item['id']: item for item in target.request('/hil-sim/presets?' +
            urllib.parse.urlencode({'project_id': project}))['items']}


def prepare(args):
    target = connection(args)
    source = API(args.source_api, args.source_token_file.read_text().strip() if args.source_token_file else None)
    if args.output.exists():
        receipt = json.loads(args.output.read_text())
        if (receipt['target_api'], receipt['source_api'], receipt['source_project_id']) != (args.api, args.source_api, args.source_project):
            raise ValueError('Preparation receipt belongs to another source/target')
    else:
        maps = source.items('maps', args.source_project)
        native_map = next((item for item in maps if item['id'] == args.map_id), None)
        if native_map is None or native_map['definition'].get('generated'):
            raise ValueError('Select a registered native town map')
        native_map = catalog(source, 'maps', args.source_project, ref(native_map))
        assets, controllers = source.items('assets', args.source_project), source.items('controllers', args.source_project)
        assets = [catalog(source, 'assets', args.source_project, ref(item)) for item in assets]
        controllers = [catalog(source, 'controllers', args.source_project, ref(item)) for item in controllers]
        draft = next(item for item in definitions(native_map, assets, controllers) if item['case'] == 'lead_stop')
        if draft['status'] != 'draft':
            raise ValueError('Registered native topology does not support lead_stop: ' + str(draft))
        scenario = draft['scenario']
        scenario.update(schema_version='scenario-v2/3', name='HIL Workbench · lead stop',
                        description='Software DUT closed-loop CARLA demonstration. Simulator ground-truth objects; RGB is observation only.')
        scenario['sensors'] = [dict(id=name, actor_id='ego', kind='camera_rgb', width=640, height=360,
            period_s=.1, fov_deg=fov, pose=dict(x=x, y=0, z=z, heading_rad=0))
            for name,x,z,fov in [('front',2.8,1.7,90),('chase',-8,3,100)]]
        project = target.request('/projects', {'name': args.name})
        receipt = dict(schema_version='hil-sim-demo-preparation/1', created_at=now(), target_api=args.api,
            source_api=args.source_api, source_project_id=args.source_project, project_id=project['id'],
            authored_scenario=scenario, source_map_ref=ref(native_map), presets={}, complete=False)
        write(args.output, receipt)
    project = receipt['project_id']
    scenario = copy.deepcopy(receipt['authored_scenario'])
    for kind, references in [('maps', [scenario['map']]), ('assets', [actor['asset'] for actor in scenario['actors']]),
                             ('controllers', [actor['controller']['ref'] for actor in scenario['actors']])]:
        imported = {}
        for reference in references:
            key = digest(reference)
            if key not in imported:
                original = catalog(source, kind, receipt['source_project_id'], reference)
                imported[key] = ref(target.request('/' + kind, dict(project_id=project, id=original['id'],
                    expected_revision=0, definition=original['definition'])))
        if kind == 'maps': scenario['map'] = imported[digest(scenario['map'])]
        else:
            for actor in scenario['actors']:
                owner, key = (actor, 'asset') if kind == 'assets' else (actor['controller'], 'ref')
                owner[key] = imported[digest(owner[key])]
    saved = target.request('/scenario-v2', dict(project_id=project, scenario=scenario),
                           'hil-demo:' + project + ':scenario:' + digest(scenario))
    for identity in PRESETS:
        current = presets(target, project)[identity]
        if current['binding_revision'] is None:
            target.request('/hil-sim/presets/' + identity + '/bindings', dict(project_id=project,
                expected_revision=0, scenario_ref=scenario_ref(saved), seed=42))
        item = presets(target, project)[identity]
        if not item['ready'] or item['source']['scenario_ref'] != scenario_ref(saved):
            raise ValueError('Preset preparation differs from selected scene: ' + identity)
        receipt['presets'][identity] = {key: item[key] for key in ('binding_revision','binding_sha256','defaults','source')}
        write(args.output, receipt)
    receipt.update(complete=True, finished_at=now(), scenario_ref=scenario_ref(saved), native_execution_submitted=False,
        url=args.api.removesuffix('/api/v1') + '/#hil-sim?' + urllib.parse.urlencode({'project':project}))
    write(args.output, receipt)
    return {key:receipt[key] for key in ('complete','project_id','url')}


def rehearse(args):
    target = connection(args)
    prepared = json.loads(args.prepared.read_text())
    if not prepared.get('complete') or prepared['target_api'] != args.api:
        raise ValueError('Use a completed preparation receipt for this API')
    project = prepared['project_id']
    if args.output.exists():
        receipt = json.loads(args.output.read_text())
        if receipt['project_id'] != project or receipt['target_api'] != args.api:
            raise ValueError('Rehearsal receipt belongs to another project or API')
    else:
        receipt = {'schema_version': 'hil-sim-demo-rehearsal/1', 'id': uuid.uuid4().hex,
                   'target_api': args.api, 'project_id': project, 'started_at': now(),
                   'complete': False, 'trials': []}
        write(args.output, receipt)
    for identity in prepared['presets']:
        trial = next((value for value in receipt['trials'] if value['preset'] == identity), None)
        if trial is None:
            current = presets(target, project)[identity]
            if current['source'] != prepared['presets'][identity]['source']:
                raise ValueError('Preset source changed after preparation: ' + identity)
            preview = target.request('/hil-sim/presets/' + identity + '/preview', {
                'project_id': project, 'expected_binding_revision': current['binding_revision'],
                'parameters': current['defaults']})
            trial = {'preset': identity, 'preview_hash': preview['preview']['preview_hash'],
                     'experiment': preview['experiment'], 'parameters': preview['parameters'],
                     'changes': preview['changes'], 'binding_sha256': preview['binding_sha256']}
            receipt['trials'].append(trial)
            write(args.output, receipt)
        key = receipt['id'] + ':' + identity
        if 'experiment_id' not in trial:
            experiment = target.request('/experiments', trial['experiment'], key + ':experiment')
            if experiment['preview_hash'] != trial['preview_hash']:
                raise ValueError('Native preview changed before creation; inspect receipt and prepare explicitly')
            trial['experiment_id'] = experiment['id']
            write(args.output, receipt)
        if 'execution_id' not in trial:
            execution = target.request('/experiments/' + trial['experiment_id'] + '/executions',
                {'preview_hash': trial['preview_hash']}, key + ':execution')
            trial.update(execution_id=execution['id'], status=execution['status'])
            write(args.output, receipt)
        deadline = time.monotonic() + args.timeout
        while True:
            execution = target.request('/experiments/executions/' + trial['execution_id'])
            trial.update({key: execution.get(key) for key in
                          ('status', 'summary', 'error', 'manifest_hash', 'outcome_uncertain',
                           'fidelity', 'artifacts', 'input_hash', 'started_at', 'finished_at')})
            write(args.output, receipt)
            if execution['status'] in TERMINAL:
                break
            if time.monotonic() >= deadline:
                raise ValueError('Wait timed out; job preserved. Resume with the same receipt.')
            time.sleep(2)
        if execution['status'] != 'completed' or execution.get('outcome_uncertain'):
            raise ValueError('Native run did not complete: ' + identity + '; inspect the receipt')
        current = presets(target, project)[identity]
        original_source = prepared['presets'][identity]['source']
        expected_source = {**original_source, 'saved_execution_id': execution['id']}
        if not current['ready']:
            raise ValueError('Preset is no longer ready; completed job preserved: ' + str(current.get('reason')))
        if current['source'] == expected_source:
            # A prior binding may have committed before its response or this
            # local receipt was persisted. Recover that exact result, without
            # another native run or another binding revision.
            trial.update(bound=True, saved_binding_revision=current['binding_revision'])
            write(args.output, receipt)
        elif trial.get('bound') or current['source'] != original_source:
            raise ValueError('Preset changed during capture; completed job preserved')
        else:
            binding = target.request('/hil-sim/presets/' + identity + '/bindings', {
                'project_id': project, 'expected_revision': current['binding_revision'],
                'scenario_ref': current['source']['scenario_ref'], 'seed': current['source']['seed'],
                'saved_execution_id': execution['id']})
            trial.update(bound=True, saved_binding_revision=binding['revision'])
            write(args.output, receipt)
        print(json.dumps({'preset': identity, 'execution_id': execution['id'], 'status': execution['status']}), flush=True)
    receipt.update(complete=True, finished_at=now())
    write(args.output, receipt)
    return {'complete': True, 'native_jobs': len(receipt['trials']), 'native_captures': 2 * len(receipt['trials']),
            'output': str(args.output)}



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api', default='http://127.0.0.1:8036/api/v1')
    parser.add_argument('--token-file', type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    setup = commands.add_parser('prepare')
    setup.add_argument('--source-api', required=True)
    setup.add_argument('--source-token-file', type=Path)
    setup.add_argument('--source-project', default='default')
    setup.add_argument('--map-id', default='carla-town10hd-opt')
    setup.add_argument('--name', default='HIL Workbench · Presenter Demo')
    setup.add_argument('--output', type=Path, required=True)
    run = commands.add_parser('rehearse')
    run.add_argument('--prepared', type=Path, required=True)
    run.add_argument('--output', type=Path, required=True)
    run.add_argument('--timeout', type=float, default=900)
    args = parser.parse_args()
    print(json.dumps((prepare if args.command == 'prepare' else rehearse)(args), ensure_ascii=False))


if __name__ == '__main__':
    main()
