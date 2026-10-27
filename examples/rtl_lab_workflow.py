"""Run the four saved FIFO presets through public SDK and the actual queued worker.

The receipt is written before each execution request, including its retry key. Use
--resume to continue that exact campaign; existing downloads are checked, not replaced.
This script starts no services.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlencode
from uuid import uuid4

from axiom_sdk import AxiomClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8120')
    parser.add_argument('--ui', default='http://127.0.0.1:5220')
    parser.add_argument('--project', default='default')
    parser.add_argument('--token', default='')
    parser.add_argument('--output', default='docs/validation/rtl-lab-20261014.json')
    parser.add_argument('--resume', action='store_true', help='Continue the saved campaign without creating replacement runs')
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists() and not args.resume:
        raise SystemExit('Output exists; preserve prior receipts and choose another --output path')
    if args.resume and not output.is_file():
        raise SystemExit('--resume requires an existing receipt file')
    output.parent.mkdir(parents=True, exist_ok=True)
    artifact_root = output.parent / (output.stem + '-artifacts')
    if artifact_root.exists() and not args.resume:
        raise SystemExit('Artifact directory exists; choose another --output path')
    client = AxiomClient(args.url, token=args.token, timeout=180)
    readiness = client.rtl_lab_readiness(project_id=args.project)
    if not readiness.get('available'):
        raise SystemExit('RTL tools are unavailable: ' + json.dumps(readiness))
    if args.resume:
        evidence = json.loads(output.read_text())
        if (evidence.get('schema_version') != 'rtl-validation/1'
                or evidence.get('api_url') != args.url or evidence.get('ui_url') != args.ui
                or evidence.get('project_id') != args.project):
            raise SystemExit('Resume API/UI/project must exactly match the saved campaign')
        stamp, campaign = evidence['created_at'], evidence['campaign_id']
    else:
        stamp = datetime.now(timezone.utc).isoformat()
        campaign = str(uuid4())
        evidence = dict(schema_version='rtl-validation/1', created_at=stamp,
                        scope='New queued SimPy and Verilator reference FIFO executions; no physical or vendor-chip validation',
                        api_url=args.url, ui_url=args.ui, project_id=args.project,
                        campaign_id=campaign, readiness=readiness, runs={})
        # Reserve the receipt path exclusively before authoring anything remotely.
        with output.open('x') as stream:
            json.dump(evidence, stream, ensure_ascii=False, indent=2)
    artifact_root.mkdir(exist_ok=args.resume)

    def save():
        staged = output.with_name(output.name + '.part')
        staged.write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
        staged.replace(output)

    if 'presets' not in evidence:
        evidence['presets'] = client.rtl_lab_templates(project_id=args.project)['items']
        save()
    presets = evidence['presets']
    wanted = ['normal', 'stall', 'repeated', 'reset']
    byid = {p['id']: p for p in presets}
    for identity in wanted:
        record = evidence['runs'].get(identity)
        if record is None:
            key = campaign + '-' + identity
            record = dict(preset=identity, definition=byid[identity], save_key=key + '-test', execution_key=key + '-run')
            evidence['runs'][identity] = record
            save()
        preset = record['definition']
        if 'test' not in record:
            record['test'] = client.save_rtl_lab_test(name=preset['name'] + ' ' + stamp,
                config=preset['config'], seed=preset['seed'], project_id=args.project, idempotency_key=record['save_key'])
            save()
        test = record['test']
        if client.rtl_lab_test(test['id'], revision=test['revision'], project_id=args.project) != test:
            raise RuntimeError('Saved test readback differs; campaign left intact')
        # Keep concrete experiment receipt before submission so uncertain outcomes
        # can be retried without changing the frozen preview or creating new work.
        if 'preview' not in record:
            record['preview'] = client.preview_rtl_lab_test(test)
            save()
        preview = record['preview']
        if 'experiment_id' not in record:
            experiment = client.create_experiment(**preview['experiment'], idempotency_key=record['execution_key'])
            record['experiment_id'] = experiment['id']
            save()
        if 'job_id' not in record:
            job = client.submit_experiment(record['experiment_id'], preview_hash=preview['preview_hash'],
                                           idempotency_key=record['execution_key'])
            record['job_id'] = job['id']
            record['execution'] = job
            save()
            print('submitted', identity, record['job_id'], flush=True)
        else:
            print('resuming', identity, record['job_id'], flush=True)
        job_id = record['job_id']
        started = time.monotonic()
        while True:
            run = client.experiment_execution(job_id)
            record['execution'] = run
            save()
            if run['status'] in ['completed', 'failed', 'cancelled', 'interrupted']:
                break
            if time.monotonic() - started > 300:
                raise RuntimeError('Still running; resume the saved job_id, do not create a replacement execution')
            time.sleep(.5)
        if run['status'] != 'completed':
            raise RuntimeError('Execution failed; preserved receipt: ' + json.dumps(run, ensure_ascii=False))
        folder = artifact_root / identity
        folder.mkdir(exist_ok=args.resume)
        verified = {a['name']: a for a in record.get('artifacts_verified', [])}
        documents = {}
        for artifact in run['artifacts']:
            name = artifact['name']
            if Path(name).name != name:
                raise RuntimeError('Unexpected nested artifact name')
            path = folder / name
            if not path.exists():
                client.download_experiment_artifact(job_id, artifact['id'], path)
            raw = path.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            if sha != artifact['sha256'] or len(raw) != artifact['size_bytes']:
                raise RuntimeError('Artifact digest or size differs: ' + name)
            verified[name] = dict(name=name, sha256=sha, size_bytes=len(raw), path=str(path))
            record['artifacts_verified'] = list(verified.values())
            if artifact['media_type'] == 'application/json':
                documents[name] = json.loads(raw)
            save()
        record['rtl_trace'] = documents['rtl-trace.json']
        record['simpy_trace'] = documents['simpy-trace.json']
        record['comparison'] = documents['comparison.json']
        record['summary'] = documents['summary.json']
        export_path = folder / 'execution-export.json'
        if not export_path.exists():
            client.export_experiment_execution(job_id, export_path)
        if hashlib.sha256(export_path.read_bytes()).hexdigest() != run['manifest_hash']:
            raise RuntimeError('Existing execution export differs; it was not overwritten')
        record['export_path'] = str(export_path)
        record['all_artifact_hashes_verified'] = True
        query = dict(project=args.project, mode='fpga', target='rtl-fifo', test=test['id'],
                     revision=test['revision'], execution=job_id, cycle=384, window=368, span=64, view='engine')
        record['url'] = args.ui.rstrip('/') + '/#hil-sim?' + urlencode(query)
        if identity != 'normal':
            record['scenario_comparison_url'] = args.ui.rstrip('/') + '/#hil-sim?' + urlencode(
                query | dict(compare=evidence['runs']['normal']['job_id'], view='scenario'))
        save()
        if record['summary']['verdict'] != 'passed' or not record['comparison']['matched']:
            raise RuntimeError('RTL validation did not pass; partial traces and verdict were preserved')
        summary = record['summary']
        print(identity, json.dumps({k: summary[k] for k in ['verdict', 'input_transfers', 'output_transfers',
              'reset_discarded', 'peak_occupancy', 'input_stall_cycles', 'cycles']}, ensure_ascii=False), flush=True)

    normal = evidence['runs']['normal']['rtl_trace']
    original_words = [r['in_data'] for r in normal['cycles'] if r['accepted_in']]
    for identity, record in evidence['runs'].items():
        trace = record['rtl_trace']
        if trace['seed'] != normal['seed'] or trace['config']['words'] != normal['config']['words']:
            raise RuntimeError('Input definitions differ across the campaign')
        if [r['in_data'] for r in trace['cycles'] if r['accepted_in']] != original_words:
            raise RuntimeError('Accepted source word sequence differs: ' + identity)
        record['matched_input_sequence_verified'] = True
    evidence['matched_input_sequence_verified'] = True
    evidence['completed_at'] = datetime.now(timezone.utc).isoformat()
    save()
    print('evidence', output, flush=True)


if __name__ == '__main__':
    main()
