#!/usr/bin/env python3
"""Execute ONE explicit source-bound CARLA physics candidate, outside platform DB.

Requires a dedicated local CARLA endpoint, the existing engine lock/store UUID,
a fresh pinned world, a complete source manifest and a new output directory.
No retry, server reload, production deployment or historical-result mutation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time
from datetime import datetime, timezone


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read_json(path, limit=2*1024*1024):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError('Expected a bounded ordinary input file: ' + str(path))
    raw = path.read_bytes()
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Duplicate JSON key')
            value[key] = item
        return value
    def invalid(token):
        raise ValueError('Nonfinite JSON token: ' + token)
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid), raw


def write(path, raw):
    with Path(path).open('xb') as stream:
        os.chmod(path, 0o600)
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def sync_dir(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def verify_bundle(root, expected):
    root = Path(root)
    if root.is_symlink():
        raise ValueError('Source root cannot be a symlink')
    root = root.resolve()
    manifest, raw = read_json(root/'candidate-source-manifest.json')
    if sha(raw) != expected or not 1 <= len(manifest['files']) <= 300:
        raise ValueError('Candidate source manifest mismatch')
    for name, digest in manifest['files'].items():
        source = root/name
        if source.is_symlink() or not source.resolve().is_relative_to(root) or not source.is_file():
            raise ValueError('Unsafe source path')
        if sha(source.read_bytes()) != digest:
            raise ValueError('Candidate source bytes changed: ' + name)
    relative = str(Path(__file__).resolve().relative_to(root))
    if relative not in manifest['files']:
        raise ValueError('Entry script is not source-bound')
    return manifest


def world_state(world):
    from axiom_carla.native_journal import SETTINGS, WEATHER, fields
    road = world.get_map(); raw = road.to_opendrive().encode()
    return {'world_id': str(world.id),
        'map': {'name': road.name, 'opendrive_sha256': sha(raw), 'opendrive_size_bytes': len(raw)},
        'actors': sorted([{'actor_id': a.id, 'type_id': a.type_id,
            'role_name': dict(a.attributes).get('role_name')} for a in world.get_actors()], key=lambda a: a['actor_id']),
        'settings': fields(world.get_settings(), SETTINGS), 'weather': fields(world.get_weather(), WEATHER)}


def execute(args):
    intent, raw = read_json(args.intent)
    required = {'schema_version', 'case', 'request', 'physics_profile', 'candidate_input_hash',
                'source_manifest_sha256', 'engine_build', 'expected_store_id', 'expected_world',
                'historical_run_id', 'historical_run_sha256'}
    if set(intent) != required or intent['schema_version'] != 'carla-physics-candidate-intent/1' or raw != canonical(intent):
        raise ValueError('Invalid or noncanonical candidate intent')
    manifest = verify_bundle(args.bundle_root, intent['source_manifest_sha256'])
    if os.getenv('ADE2E_CARLA_EXCLUSIVE') != '1':
        raise ValueError('Explicit exclusive CARLA environment required')
    if os.getenv('ADE2E_CARLA_HOST', '127.0.0.1') != '127.0.0.1' or os.getenv('ADE2E_CARLA_PORT', '2000') != '2000':
        raise ValueError('Only the dedicated local CARLA endpoint is supported')
    from ade2e.adapters import AdapterExecutionError
    from ade2e.simulation_context import validate_simulation_execution_request
    from ade2e.simulation_evidence import validate_simulation_artifacts
    from axiom_carla.physics_candidate import CandidateEvidence
    from axiom_carla.runner import CarlaRunner
    from axiom_carla.spool import file_lock
    from axiom_carla.version import ADAPTER_VERSION
    import ade2e, axiom_carla
    root = args.bundle_root.resolve()
    if not all(Path(m.__file__).resolve().is_relative_to(root) for m in (ade2e, axiom_carla)):
        raise ValueError('Imports are not from the verified isolated source bundle')
    request = validate_simulation_execution_request(intent['request'])
    candidate = CandidateEvidence(intent['physics_profile'], request, .05)
    if candidate.data['candidate_input_hash'] != intent['candidate_input_hash'] or ADAPTER_VERSION != '0.8.0':
        raise ValueError('Candidate identity or underlying adapter differs')
    store = args.runtime_dir/'executions'
    if store.is_symlink() or not store.is_dir() or (store/'store-id').read_text().strip() != intent['expected_store_id']:
        raise ValueError('Existing execution store identity differs')
    quarantine = store/'quarantine.json'
    args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
    write(args.output/'source-intent.json', raw)
    report = {'schema_version': 'carla-physics-candidate-result/1', 'case': intent['case'],
        'execution_id': request.execution_id, 'intent_sha256': sha(raw),
        'candidate_input_hash': intent['candidate_input_hash'], 'base_input_hash': request.input_hash,
        'source_manifest_sha256': intent['source_manifest_sha256'], 'source_files_verified': len(manifest['files']),
        'physics_profile': intent['physics_profile'], 'status': 'not_started', 'quality_verdict': 'not_evaluated',
        'started_at': datetime.now(timezone.utc).isoformat(), 'world_restored': False,
        'error': None, 'artifacts': [], 'scope': 'Standalone original-stack physics candidate; no platform Job/Run or production-default update.'}
    started = time.monotonic()
    with file_lock(store/'engine.lock'):
        if quarantine.exists() or quarantine.is_symlink():
            raise ValueError('Unresolved engine reservation; no candidate execution allowed')
        runner = CarlaRunner()
        _, client, world, version = runner._connect()
        before = world_state(world)
        if version != intent['engine_build'] or before != intent['expected_world']:
            raise ValueError('Fresh world/build differs from the pinned intent')
        report['before'] = before
        reservation = {'schema_version': 'carla-physics-candidate-reservation/1',
            'execution_id': request.execution_id, 'request_hash': sha(raw),
            'store_id': intent['expected_store_id'], 'output': str(args.output.resolve())}
        write(quarantine, canonical(reservation)); sync_dir(store)
        payloads, result = [], None
        def deadline(signum, frame):
            signal.alarm(0)
            raise RuntimeError('Bounded candidate wall deadline exceeded; preserve cleanup evidence')
        previous_handler = signal.signal(signal.SIGALRM, deadline)
        signal.alarm(240)
        try:
            report['status'] = 'running'
            result = runner.execute_physics_candidate(request, intent['physics_profile'])
            payloads = result.artifact_payloads
            validate_simulation_artifacts(request, payloads, completed=True,
                trajectory=result.trajectory, adapter_version=ADAPTER_VERSION)
            report['status'] = 'completed'
            report['quality_verdict'] = 'passed' if all(m.passed for m in result.metrics) else 'failed'
        except Exception as error:
            if isinstance(error, AdapterExecutionError):
                payloads = error.artifact_payloads
            report['status'] = 'failed'
            report['error'] = {'type': type(error).__name__, 'message': str(error)[:1800]}
        finally:
            signal.alarm(0); signal.signal(signal.SIGALRM, previous_handler)
            # Normal runner owns cleanup. This reader never deletes foreign actors
            # or forces world settings into a presumed good state.
            try:
                for _ in range(20):
                    after = world_state(client.get_world())
                    if after == before:
                        break
                    time.sleep(.1)
                report['after'] = after
                report['world_restored'] = after == before
            except Exception as error:
                report['world_check_error'] = str(error)[:1000]
            if not report['world_restored']:
                report['status'] = 'failed'; report['quality_verdict'] = 'not_evaluated'
            artifact_dir = args.output/'artifacts'; artifact_dir.mkdir()
            for payload in payloads:
                if Path(payload.name).name != payload.name:
                    raise ValueError('Unsafe artifact name')
                write(artifact_dir/payload.name, payload.content)
                report['artifacts'].append({'name': payload.name, 'kind': payload.kind,
                    'sha256': sha(payload.content), 'size_bytes': len(payload.content)})
            if result is not None:
                write(args.output/'result.json', canonical(result.model_dump(mode='json', exclude={'artifact_payloads'})))
                report['result_sha256'] = sha((args.output/'result.json').read_bytes())
                report['metrics'] = [m.model_dump(mode='json') for m in result.metrics]
                report['observations'] = len(result.trajectory)
            report['elapsed_wall_s'] = time.monotonic()-started
            report['finished_at'] = datetime.now(timezone.utc).isoformat()
            content = canonical(report); write(args.output/'report.json', content); sync_dir(args.output)
            if report['world_restored']:
                if read_json(quarantine)[0] != reservation:
                    raise ValueError('Reservation ownership changed; do not release')
                receipt = {'schema_version': 'carla-physics-candidate-release/1',
                    'execution_id': request.execution_id, 'report_sha256': sha(content),
                    'world_restored': True, 'status': 'released'}
                write(args.output/'reservation-release.json', canonical(receipt)); sync_dir(args.output)
                quarantine.unlink(); sync_dir(store)
    print(json.dumps({k: report.get(k) for k in ('case', 'execution_id', 'status', 'quality_verdict',
        'physics_profile', 'world_restored', 'elapsed_wall_s', 'metrics', 'error')}, ensure_ascii=False), flush=True)
    return 0 if report['status'] == 'completed' and report['world_restored'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('intent', 'bundle-root', 'runtime-dir', 'output'):
        parser.add_argument('--'+name, required=True, type=Path)
    return execute(parser.parse_args())


if __name__ == '__main__':
    raise SystemExit(main())
