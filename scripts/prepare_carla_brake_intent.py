#!/usr/bin/env python3
"""Author one immutable native diagnostic intent; never connect to CARLA."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import uuid

from carla_brake_protocol import (BRAKE_LEVELS, INTENT_VERSION, VERSION, EXECUTABLE_VERSIONS, canonical,
    exact_keys, make_protocol, measurement_sequence, require, sha, validate_intent)


def read_json(path, maximum=8*1024*1024):
    require(path.is_file() and not path.is_symlink(), 'Expected a regular evidence file')
    raw = path.read_bytes()
    require(len(raw) <= maximum, 'Evidence file exceeds its byte budget')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'Duplicate JSON key')
            result[key] = value
        return result
    def invalid(value):
        raise ValueError('Nonfinite JSON token: ' + value)
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid), raw


def prepare(args):
    environment, _ = read_json(args.environment, maximum=128*1024)
    exact_keys(environment, ('expected_world', 'expected_store_id', 'engine_build',
        'adapter_version', 'spawn_transform', 'requested_weather'), 'Environment')
    response, original = read_json(args.source_evidence)
    require(response.get('schema_version') == 'carla-vehicle-response/1' and
            response.get('status') == 'completed', 'A completed original vehicle response is required')
    require(response.get('worker_adapter_version') == environment['adapter_version'] and
            response.get('server_version') == environment['engine_build'], 'Original engine/adapter differs')
    physics = response['physics_configuration']['ego']
    require(physics['status'] == 'available' and physics['friction_status'] == 'matched', 'Original physics readback unavailable')
    protocol = make_protocol(args.initialization, measurement=args.measurement,
        brake_level=args.brake_level, repeat_index=args.repeat_index, seed=args.seed,
        version=getattr(args, 'protocol_version', VERSION))
    sources = {}
    for name in ('carla_brake_protocol.py', 'diagnose_carla_brake_response.py', 'diagnose_carla_self_hit.py'):
        source = args.source_dir/name
        require(source.is_file() and not source.is_symlink(), 'Missing captured source: ' + name)
        require(source.stat().st_size <= 256*1024, 'Diagnostic source exceeds byte budget')
        sources[name] = sha(source.read_bytes())
    intent = validate_intent({'schema_version': INTENT_VERSION,
        'experiment_id': str(uuid.uuid4()), 'protocol': protocol,
        'protocol_sha256': sha(canonical(protocol)),
        'sequence_sha256': sha(canonical(measurement_sequence(protocol))), **environment,
        'expected_physics': {key: physics[key] for key in ('scalars', 'wheels')},
        'source_files': sources,
        'source_evidence': {'path': str(args.source_evidence.resolve()), 'sha256': sha(original)}})
    content = canonical(intent)
    with args.output.open('xb') as stream:
        os.chmod(args.output, 0o600)
        stream.write(content); stream.flush(); os.fsync(stream.fileno())
    return {'path': str(args.output), 'experiment_id': intent['experiment_id'],
        'intent_sha256': sha(content), 'protocol_sha256': intent['protocol_sha256'],
        'sequence_sha256': intent['sequence_sha256'], 'engine_actions': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', type=Path, required=True)
    parser.add_argument('--source-evidence', type=Path, required=True)
    parser.add_argument('--source-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--initialization', choices=('velocity_injection', 'throttle'), required=True)
    parser.add_argument('--protocol-version', choices=EXECUTABLE_VERSIONS, default=VERSION,
                        help='Select protocol3 explicitly for observed road-entry preparation; protocol2 authoring is retained')
    parser.add_argument('--measurement', choices=('coast', 'pulse'), default='coast')
    parser.add_argument('--brake-level', type=float, choices=BRAKE_LEVELS, default=0.)
    parser.add_argument('--repeat-index', type=int, default=1)
    parser.add_argument('--seed', type=int, default=42)
    print(json.dumps(prepare(parser.parse_args()), sort_keys=True))


if __name__ == '__main__':
    main()
