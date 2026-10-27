#!/usr/bin/env python3
"""Prepare Neural Sim presenter runs using only the normal Python SDK/API.

Receipts persist intent and idempotency keys before submission. Reusing a
receipt resumes its exact jobs; failed or uncertain runs are never retried.
No credentials, native configuration or prepared remote paths are saved.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sdk/python/src'))
from axiom_sdk import AxiomClient

PRESETS = {'left': (-.3, 0, 'sequence', 0), 'right': (.3, 0, 'sequence', 0),
           'raised': (0, .2, 'sequence', 0), 'sweep': (1.5, 0, 'camera_sweep', 50)}


def now():
    return datetime.now(timezone.utc).isoformat()


def write(path, value):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Receipt must not be a symlink')
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.neural-demo-', delete=False) as stream:
        stage = Path(stream.name)
        os.chmod(stage, 0o600)
        stream.write((json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode())
        stream.flush()
        os.fsync(stream.fileno())
    stage.replace(path)


def receipt(args, intent):
    if args.output is None:
        raise ValueError('--output is required for commands that submit or bind')
    signature = hashlib.sha256(json.dumps(intent, sort_keys=True, allow_nan=False).encode()).hexdigest()
    if args.output.exists():
        value = json.loads(args.output.read_text())
        if (value.get('schema_version'), value.get('url'), value.get('project_id'), value.get('intent_sha256')) != (
                'neural-demo-preparation/1', args.url, args.project_id, signature):
            raise ValueError('Receipt belongs to another command, source, project or API; use its original arguments')
        return value
    value = dict(schema_version='neural-demo-preparation/1', operation_id=uuid.uuid4().hex,
        url=args.url, project_id=args.project_id, created_at=now(), intent_sha256=signature,
        intent=intent, runs={}, bindings={})
    write(args.output, value)
    return value


def inspect_run(client, args, run):
    value = client.experiment_execution(run['execution_id'])
    if value['project_id'] != args.project_id:
        raise ValueError('Execution belongs to another project')
    if args.wait_seconds and value['status'] not in {'completed', 'failed', 'cancelled', 'interrupted'}:
        try:
            value = client.wait_experiment(run['execution_id'], timeout=args.wait_seconds, poll_interval=2)
        except TimeoutError:
            value = client.experiment_execution(run['execution_id'])
    # Errors can include native paths/log text; retain the public status and
    # exact job ID, and inspect failure details through the authenticated UI.
    run.update({key: value.get(key) for key in ('status', 'input_hash', 'manifest_hash', 'fidelity',
        'outcome_uncertain', 'started_at', 'finished_at')})
    return value


def submit(client, args, document, key, spec):
    run = document['runs'].setdefault(key, {})
    token = document['operation_id'] + ':' + key
    if 'preview_hash' not in run:
        run['preview_hash'] = client.preview_experiment(**spec)['preview_hash']
        write(args.output, document)
    if 'experiment_id' not in run:
        saved = client.create_experiment(**spec, idempotency_key=token + ':create')
        if saved['preview_hash'] != run['preview_hash']:
            raise ValueError('Native input changed after preview; no job submitted. Inspect this receipt and create a new explicit preparation.')
        run['experiment_id'] = saved['id']
        write(args.output, document)
    if 'execution_id' not in run:
        created = client.submit_experiment(run['experiment_id'], preview_hash=run['preview_hash'], idempotency_key=token + ':execute')
        run.update(execution_id=created['id'], status=created['status'])
        write(args.output, document)
    result = inspect_run(client, args, run)
    write(args.output, document)
    return result


def bind(client, args, document, identities, saved=None):
    items = {item['id']: item for item in client.neural_sim_presets(project_id=args.project_id)['items']}
    for identity in identities:
        value = client.bind_neural_sim_preset(identity, project_id=args.project_id,
            expected_revision=items[identity]['binding_revision'] or 0,
            source_execution_id=args.source_execution_id, reconstruction_execution_id=args.reconstruction_execution_id,
            saved_execution_id=saved, seed=args.seed)
        document['bindings'][identity] = dict(revision=value['revision'], sha256=value['sha256'], saved_execution_id=saved)
        write(args.output, document)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True, help='API origin, e.g. http://127.0.0.1:8040')
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--project-id', required=True)
    parser.add_argument('--output', type=Path, help='Resumable nonsecret receipt; required except for single-job status')
    parser.add_argument('--wait-seconds', type=float, default=0, help='Bounded wait per job, 0 to submit/check only (maximum 3600)')
    commands = parser.add_subparsers(dest='command', required=True)
    source = commands.add_parser('import-source', help='Publish an operator-configured real-road source')
    source.add_argument('--source-id', required=True)
    source.add_argument('--seed', type=int, default=42)
    train = commands.add_parser('train')
    train.add_argument('--source-execution-id', required=True)
    train.add_argument('--seed', type=int, default=42)
    render = commands.add_parser('render')
    render.add_argument('--reconstruction-execution-id', required=True)
    render.add_argument('--lateral-m', type=float, default=0)
    render.add_argument('--height-m', type=float, default=0)
    render.add_argument('--mode', choices=['frame', 'sequence', 'camera_sweep'], default='sequence')
    render.add_argument('--frame-index', type=int, default=0)
    render.add_argument('--seed', type=int, default=42)
    perception = commands.add_parser('perception', help='Run a registered detector on original/baseline/novel images')
    perception.add_argument('--render-execution-id', required=True)
    perception.add_argument('--model-id', required=True)
    perception.add_argument('--model-revision', type=int, required=True)
    perception.add_argument('--frame-indices', type=int, nargs='+', default=[0, 15, 30, 45, 60])
    perception.add_argument('--score-threshold', type=float, default=.35)
    perception.add_argument('--iou-threshold', type=float, default=.45)
    perception.add_argument('--seed', type=int, default=42)
    presets = commands.add_parser('render-presets')
    presets.add_argument('--include-sweep', action='store_true', help='Also generate the frozen-frame camera motion showcase')
    binding = commands.add_parser('bind')
    for command in (presets, binding):
        command.add_argument('--source-execution-id', required=True)
        command.add_argument('--reconstruction-execution-id', required=True)
        command.add_argument('--seed', type=int, default=42)
    binding.add_argument('--preset-id', choices=['all', *PRESETS], default='all')
    binding.add_argument('--saved-execution-id', help='Completed default sequence result; requires one --preset-id')
    status = commands.add_parser('status')
    status.add_argument('--execution-id', help='Otherwise inspect the jobs in --output')
    args = parser.parse_args()
    args.url = args.url.rstrip('/').removesuffix('/api/v1')
    parsed = urlsplit(args.url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        parser.error('--url must be an HTTP API origin without credentials or query parameters')
    if not 0 <= args.wait_seconds <= 3600:
        parser.error('--wait-seconds must be within 0..3600')
    try:
        with AxiomClient(base_url=args.url, token=args.token_file.read_text().strip(), timeout=120) as client:
            if args.command == 'status':
                if args.execution_id:
                    run = dict(execution_id=args.execution_id)
                    inspect_run(client, args, run)
                    print(json.dumps(run, ensure_ascii=False))
                    return
                if args.output is None:
                    raise ValueError('Status requires --execution-id or --output')
                document = json.loads(args.output.read_text())
                if document.get('url') != args.url or document.get('project_id') != args.project_id:
                    raise ValueError('Receipt belongs to another API or project')
                for run in document['runs'].values():
                    if 'execution_id' in run:
                        inspect_run(client, args, run)
                write(args.output, document)
            else:
                intent = {key: value for key, value in vars(args).items()
                    if key not in ('url', 'token_file', 'project_id', 'output', 'wait_seconds')}
                if intent.get('include_sweep') is False:
                    intent.pop('include_sweep')  # Preserve existing three-view receipts.
                document = receipt(args, intent)
                base = dict(project_id=args.project_id, adapter_id='neural-splatfacto', adapter_version='1.0.0', seed=args.seed)
                if args.command == 'import-source':
                    spec = dict(**base, name='Neural Sim · Recorded real-road source', kind='neural_source_import',
                        input=dict(source_id=args.source_id))
                    submit(client, args, document, 'source', spec)
                elif args.command == 'train':
                    spec = dict(**base, name='Neural Sim · Scene reconstruction', kind='neural_reconstruction',
                        input=dict(source_execution_id=args.source_execution_id, rig='rig_a', camera='front'))
                    submit(client, args, document, 'train', spec)
                elif args.command == 'perception':
                    preview = client.preview_neural_perception(args.render_execution_id, project_id=args.project_id,
                        model_id=args.model_id, model_revision=args.model_revision, frame_indices=args.frame_indices,
                        score_threshold=args.score_threshold, iou_threshold=args.iou_threshold, seed=args.seed)
                    submit(client, args, document, 'perception', preview['experiment'])
                elif args.command == 'render':
                    spec = dict(**base, name='Neural Sim · Camera view', kind='neural_render', input=dict(
                        reconstruction_execution_id=args.reconstruction_execution_id, lateral_m=args.lateral_m,
                        height_m=args.height_m, mode=args.mode, frame_index=args.frame_index))
                    submit(client, args, document, 'render', spec)
                elif args.command == 'bind':
                    if args.saved_execution_id and args.preset_id == 'all':
                        raise ValueError('A saved result belongs to one explicit preset, not all presets')
                    bind(client, args, document, PRESETS if args.preset_id == 'all' else [args.preset_id], args.saved_execution_id)
                else:
                    # Queue all three explicitly requested fixed views. Only
                    # completed, certain jobs are promoted to saved bindings.
                    scene = client.experiment_execution(args.reconstruction_execution_id)
                    if (scene['project_id'] != args.project_id or scene['status'] != 'completed'
                            or scene.get('outcome_uncertain') or scene.get('experiment_kind') != 'neural_reconstruction'
                            or (scene.get('summary') or {}).get('source_execution_id') != args.source_execution_id):
                        raise ValueError('Select a completed neural reconstruction from the exact project/source before rendering presets')
                    for identity, (lateral, height, mode, frame_index) in PRESETS.items():
                        if identity == 'sweep' and not args.include_sweep:
                            continue
                        spec = dict(**base, name='Neural Sim · ' + identity, kind='neural_render', input=dict(
                            reconstruction_execution_id=args.reconstruction_execution_id, lateral_m=lateral,
                            height_m=height, mode=mode, frame_index=frame_index))
                        result = submit(client, args, document, identity, spec)
                        if result['status'] == 'completed' and not result.get('outcome_uncertain'):
                            bind(client, args, document, [identity], result['id'])
                        elif result['status'] in {'failed', 'cancelled', 'interrupted'} or result.get('outcome_uncertain'):
                            raise ValueError('Render did not complete; its exact job is retained in the receipt and was not retried')
            print(json.dumps({key: document[key] for key in ('project_id', 'runs', 'bindings')}, ensure_ascii=False))
    except Exception as error:
        # The retained receipt/job is the diagnostic source; exception logs can
        # contain operator transport details and are not copied into this file.
        parser.exit(1, f'{type(error).__name__}: {error}\n')


if __name__ == '__main__':
    main()
