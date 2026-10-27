"""Retrieve an existing terminal remote attempt without running a simulator.

Requires a source job created with a durable remote adapter and a local queue
worker. The supplied job may be failed/interrupted even when the remote journal
has a complete result. Partial remote archives remain diagnostic evidence only.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path

from axiom_sdk import AxiomClient


def run(api, source_job_id: str, output: Path, *, note: str, idempotency_key: str):
    output.mkdir(parents=True, exist_ok=False)
    original = api.get_job(source_job_id)
    inspection = api.inspect_remote_execution(source_job_id)
    (output / 'remote-inspection.json').write_text(json.dumps(inspection, indent=2)+'\n')
    if not inspection['can_retrieve']:
        raise RuntimeError(inspection.get('reason') or 'No eligible sealed remote evidence is available')
    intent = {'source_job_id': source_job_id, 'note': note, 'idempotency_key': idempotency_key}
    (output / 'retrieval-intent.json').write_text(json.dumps(intent, indent=2)+'\n')
    queued = api.submit_execution_recovery(**intent)
    assert api.submit_execution_recovery(**intent)['id'] == queued['id']
    recovered = api.wait_execution_recovery(queued['id'], timeout=180)
    manifest = api.export_execution_recovery(recovered['id'], output / 'recovery.json')
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == recovered['manifest_hash']
    if recovered.get('archive_hash'):
        archive = api.export_remote_execution_archive(recovered['id'], output / 'remote-archive.json')
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == recovered['archive_hash']
    after = api.get_job(source_job_id)
    assert after == original, 'Original source job changed during retrieval; inspect concurrent operator actions'
    result = {'source_job_id': source_job_id, 'source_status': original['status'],
              'source_run_id': original.get('run_id'), 'recovery_id': recovered['id'],
              'recovery_status': recovered['status'], 'outcome': recovered['outcome'],
              'recovered_run_id': recovered['run_id'], 'manifest_sha256': recovered['manifest_hash'],
              'archive_sha256': recovered.get('archive_hash'),
              'scope': 'Read-only retrieval of previously executed evidence; no resimulation, no automatic engine reconciliation.'}
    if recovered['run_id']:
        value = api.get_run(recovered['run_id'])
        assert value['recovery']['source_execution_id'] == inspection['source_execution_id']
        assert value['recovery']['source_job_id'] == source_job_id
        (output / 'recovered-run.json').write_text(json.dumps(value, indent=2)+'\n')
    (output / 'summary.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--source-job-id', required=True)
    parser.add_argument('--note', required=True)
    parser.add_argument('--idempotency-key', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with AxiomClient(args.url, token=os.environ.get('ADE2E_API_TOKEN')) as api:
        print(json.dumps(run(api, args.source_job_id, args.output, note=args.note, idempotency_key=args.idempotency_key), indent=2))
