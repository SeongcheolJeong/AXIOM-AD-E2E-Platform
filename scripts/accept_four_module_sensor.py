"""Opt-in native sensor acceptance through the ordinary experiment API/worker.

Use a new, isolated data directory. This never migrates the operating workspace,
restarts CARLA, reloads a map, or stops another process. Transport comes from the
optional adapter's operator environment, not request-supplied commands.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import threading
import time

from fastapi.testclient import TestClient
from ade2e.adapters import Registry
from ade2e.database import Settings
from ade2e.main import create_app
from ade2e.sensor_capture_schemas import capture_template
from ade2e.worker import run_once


def checked(response):
    if response.is_error:
        raise RuntimeError(f"API {response.status_code}: {response.text}")
    return response.json()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--live', action='store_true', required=True)
    args = parser.parse_args()
    state = args.state.absolute()
    state.mkdir(parents=True, exist_ok=True)
    data = state / 'platform'
    if data.exists():
        raise ValueError('Choose a new state directory; existing databases are never reused or migrated')
    app = create_app(Settings(data_dir=data, seed_examples=False), Registry(load_plugins=False))
    records, samples, stop = [], [], threading.Event()
    handler = app.state.experiment_registry.get('carla-streaming-capture')
    if handler is None or handler.metadata().status != 'available':
        raise ValueError('Configure the optional native capture adapter first')

    def monitor():
        while not stop.is_set():
            started = time.monotonic()
            result = subprocess.run(['ssh', '-o', 'BatchMode=yes', handler.target,
                'nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=15)
            samples.append({'monotonic_seconds': started, 'exit_code': result.returncode,
                            'gpu_memory_used_mib,total_mib,utilization_percent': result.stdout.strip()})
            stop.wait(1)

    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    try:
        with TestClient(app) as client:
            for label, duration, comparison in [('native10', 10, 'single'), ('native30', 30, 'single'),
                                                ('sensor-ab10', 10, 'same_scene_sensor_ab')]:
                rig = capture_template()
                rig['duration_seconds'] = duration
                inputs = rig
                if comparison != 'single':
                    candidate = copy.deepcopy(rig)
                    candidate['rig_revision'] = 2
                    candidate['sensors'][0]['parameters'] = {'fov': '70'}
                    inputs = {'rig_a': rig, 'rig_b': candidate}
                body = {'project_id': 'default', 'name': label, 'kind': 'sensor_capture',
                        'adapter_id': 'carla-streaming-capture', 'input': inputs,
                        'comparison_kind': comparison, 'seed': 42}
                preview = checked(client.post('/api/v1/experiments/preview', json=body))
                experiment = checked(client.post('/api/v1/experiments', json=body,
                                                 headers={'Idempotency-Key': label + '-create'}))
                submitted = checked(client.post(f"/api/v1/experiments/{experiment['id']}/executions",
                    json={'preview_hash': preview['preview_hash']}, headers={'Idempotency-Key': label + '-submit'}))
                started = time.monotonic()
                run_once(app.state.db, app.state.registry, 'sensor-native-acceptance',
                         job_id=submitted['id'], experiment_registry=app.state.experiment_registry)
                elapsed = time.monotonic() - started
                receipt = checked(client.get(f"/api/v1/experiments/executions/{submitted['id']}"))
                if receipt['status'] != 'completed':
                    (state / (label + '-failed.json')).write_text(json.dumps(receipt, indent=2))
                    raise RuntimeError(f"Native acceptance did not complete: {receipt.get('error')}")
                for artifact in receipt['artifacts']:
                    if artifact['media_type'] == 'image/png':
                        response = client.get(artifact['download_url'])
                        response.raise_for_status()
                        if hashlib.sha256(response.content).hexdigest() != artifact['sha256']:
                            raise RuntimeError('API preview download hash mismatch')
                        (state / (label + '-' + artifact['name'])).write_bytes(response.content)
                (state / (label + '-receipt.json')).write_text(json.dumps(receipt, indent=2, sort_keys=True))
                records.append({'label': label, 'duration_seconds': duration, 'comparison_kind': comparison,
                                'job_id': receipt['id'], 'status': receipt['status'],
                                'wall_seconds_worker_dispatch_transfer_seal': elapsed,
                                'manifest_hash': receipt['manifest_hash'], 'fidelity': receipt['fidelity'],
                                'summary': receipt['summary'], 'artifact_count': len(receipt['artifacts'])})
                print(json.dumps({'label': label, 'status': receipt['status'], 'worker_wall_seconds': elapsed}), flush=True)
    finally:
        stop.set()
        thread.join(timeout=20)
        (state / 'acceptance.json').write_text(json.dumps({'schema_version': 'sensor-product-acceptance/1',
            'records': records, 'gpu_samples': samples,
            'limits': ['Stationary uncalibrated native engine sensor models',
                       'Sequential sensor A/B does not freeze world clock, traffic lights, or renderer state',
                       'Worker wall time includes transport; no realtime or physical accuracy acceptance']},
            indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
