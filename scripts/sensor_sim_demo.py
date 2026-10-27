#!/usr/bin/env python3
"""Prepare and rehearse the Sensor Sim demo through public APIs.

Each preset submits one native job that captures both rigs. Receipts retain the
job before polling, so resuming never silently replaces a failed or active run.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
import urllib.parse
import uuid

from carla_city_acceptance import API, now

PRESETS = ('camera_fov', 'lidar_density', 'radar_range')
DRIVING_PRESETS = ('driving_camera_fov',)
TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted'}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    temporary.replace(path)


def connection(args):
    return API(args.api, args.token_file.read_text().strip() if args.token_file else None)


def presets(target, project):
    return {item['id']: item for item in target.request('/sensor-sim/presets?' +
            urllib.parse.urlencode({'project_id': project}))['items']}


def prepare(args):
    target = connection(args)
    if args.output.exists():
        receipt = json.loads(args.output.read_text())
        if receipt['target_api'] != args.api:
            raise ValueError('Preparation receipt belongs to another API')
    else:
        project = target.request('/projects', {'name': args.name})
        receipt = {'schema_version': 'sensor-sim-demo-preparation/1', 'created_at': now(),
                   'target_api': args.api, 'project_id': project['id'], 'presets': {}, 'complete': False}
        write(args.output, receipt)
    project = receipt['project_id']
    selected = DRIVING_PRESETS if args.driving else PRESETS
    for identity, current in presets(target, project).items():
        if identity not in selected:
            continue
        if current['binding_revision'] is None:
            target.request('/sensor-sim/presets/' + identity + '/bindings',
                           {'project_id': project, 'expected_revision': 0, 'seed': 42})
        item = presets(target, project)[identity]
        if not item['ready']:
            raise ValueError('Preset preparation failed: ' + str(item['reason']))
        receipt['presets'][identity] = {key: item[key] for key in
                                      ('binding_revision', 'binding_sha256', 'defaults', 'source')}
        write(args.output, receipt)
    if set(receipt['presets']) != set(selected):
        raise ValueError('The API did not expose the selected required presets')
    receipt.update(complete=True, finished_at=now(), native_execution_submitted=False,
                   url=args.api.removesuffix('/api/v1') + '/#sensor-sim?' +
                   urllib.parse.urlencode({'project': project}))
    write(args.output, receipt)
    return {key: receipt[key] for key in ('complete', 'project_id', 'url')}


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
        receipt = {'schema_version': 'sensor-sim-demo-rehearsal/1', 'id': uuid.uuid4().hex,
                   'target_api': args.api, 'project_id': project, 'started_at': now(),
                   'complete': False, 'trials': []}
        write(args.output, receipt)
    for identity in prepared['presets']:
        trial = next((value for value in receipt['trials'] if value['preset'] == identity), None)
        if trial is None:
            current = presets(target, project)[identity]
            if current['source'] != prepared['presets'][identity]['source']:
                raise ValueError('Preset source changed after preparation: ' + identity)
            preview = target.request('/sensor-sim/presets/' + identity + '/preview', {
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
        if not trial.get('bound'):
            current = presets(target, project)[identity]
            if current['source'] != prepared['presets'][identity]['source']:
                raise ValueError('Preset changed during capture; completed job preserved')
            binding = target.request('/sensor-sim/presets/' + identity + '/bindings', {
                'project_id': project, 'expected_revision': current['binding_revision'],
                'capture_spec': current['source']['capture_spec'], 'seed': current['source']['seed'],
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
    parser.add_argument('--api', default='http://127.0.0.1:8034/api/v1')
    parser.add_argument('--token-file', type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    setup = commands.add_parser('prepare')
    setup.add_argument('--driving', action='store_true', help='Prepare the 20-second driving multisensor preset')
    setup.add_argument('--name', default='Sensor Sim · Presenter Demo')
    setup.add_argument('--output', type=Path, required=True)
    run = commands.add_parser('rehearse')
    run.add_argument('--prepared', type=Path, required=True)
    run.add_argument('--output', type=Path, required=True)
    run.add_argument('--timeout', type=float, default=600)
    args = parser.parse_args()
    print(json.dumps((prepare if args.command == 'prepare' else rehearse)(args), ensure_ascii=False))


if __name__ == '__main__':
    main()
