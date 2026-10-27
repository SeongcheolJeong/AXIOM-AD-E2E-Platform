#!/usr/bin/env python3
"""Author a separate throttle-isolation intent from a verified failed capture.

This command reads evidence and captured source bytes. It never imports captured
Python, contacts an engine, or changes the original trial or source bundle.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import uuid

from analyze_carla_brake_response import load_trial, read_bounded, strict_json
from carla_brake_protocol import SETTLED_VERSION, require
from carla_throttle_isolation_protocol import (
    canonical, make_protocol, reference_from_report, sequence_descriptor, sha,
    validate_intent, intent_version,
)


INTENT_VERSION = 'carla-throttle-isolation-intent/1'
SOURCE_NAMES = ('carla_brake_protocol.py', 'diagnose_carla_brake_response.py',
    'diagnose_carla_self_hit.py', 'carla_throttle_isolation_protocol.py',
    'diagnose_carla_throttle_isolation.py')
CAPTURE_NAMES = ('source-intent.json', 'report.json', 'events.jsonl',
                 'reservation-release.json')
PREFIX_STEPS = 225


def require_reference(analysis, report, source_intent):
    """A sealed failure is not automatically a usable physical prefix."""
    require(analysis.get('integrity_status') == 'verified', 'Reference integrity not verified')
    protocol = source_intent['protocol']
    require(protocol['schema_version'] == SETTLED_VERSION and
            protocol['initialization'] == 'throttle' and protocol['measurement'] == 'coast',
            'Reference must be the failed protocol3 throttle coast preparation')
    require(report.get('status') == 'failed', 'Reference must retain its failed outcome')
    require(report.get('original') == source_intent['expected_world'], 'Reference original world differs')
    require(analysis.get('post_rest_physics_match') is True, 'Reference post-rest physics differs')
    require(report.get('collision_callbacks') == [], 'Reference contains contact callbacks')
    cleanup = report.get('cleanup') or {}
    require(cleanup.get('status') == 'confirmed' and cleanup.get('errors') == [] and
            cleanup.get('observed') == report['original'], 'Reference cleanup/world preservation not confirmed')
    shutdown = report.get('callback_shutdown') or {}
    require(shutdown.get('accepting') is False and shutdown.get('drained') is True and
            type(shutdown.get('active')) is int and shutdown['active'] == 0 and
            type(shutdown.get('started')) is int and shutdown['started'] >= 0 and
            type(shutdown.get('finished')) is int and shutdown['finished'] == shutdown['started'],
            'Reference callback shutdown not confirmed')
    require(analysis.get('reservation_release', {}).get('status') == 'release_receipt_verified',
            'Reference reservation release receipt is required')
    prep, measurement = report['preparation'], report['measurement']
    require(prep.get('mode') == 'throttle' and prep.get('velocity_request') is None,
            'Reference preparation must not inject velocity')
    require(len(prep['commands']) == PREFIX_STEPS and len(prep['observations']) == PREFIX_STEPS + 1 and
            all(command.get('application_status') == 'observed_next_frame' for command in prep['commands']),
            'Reference must preserve all 225 commands and 226 observations')
    require(all(row['bounds']['status'] == 'within_bounds' for row in prep['observations']),
            'Reference preparation exceeded its spatial bounds')
    gate = prep.get('entry_gate') or {}
    require(gate.get('admitted') is False and gate.get('crossed_plane_now') is True and
            type(gate.get('first_crossing_index')) is int and gate['first_crossing_index'] == PREFIX_STEPS,
            'Reference must end at its first rejected entry-plane crossing')
    require(measurement['observations'] == [] and len(measurement['commands']) == 80 and
            all(command.get('application_status') == 'not_attempted' for command in measurement['commands']),
            'Reference measurement must be entirely not attempted')


def prepare(args):
    analysis, originals = load_trial(args.reference_capture, args.reference_source_dir, args.source_evidence)
    report = strict_json(originals['report.json'])
    source_intent = strict_json(originals['source-intent.json'])
    require_reference(analysis, report, source_intent)
    require(all(name in originals for name in CAPTURE_NAMES), 'Complete reference capture required')
    artifact_hashes = {name: sha(originals[name]) for name in CAPTURE_NAMES}
    reference = reference_from_report(report, source_intent, artifact_hashes)
    protocol = make_protocol(source_intent['protocol'], args.branch, PREFIX_STEPS,
        continuation_substep_s=getattr(args, 'continuation_substep_s', None))
    sources = {name: sha(read_bounded(args.source_dir / name, limit=256 * 1024)) for name in SOURCE_NAMES}
    intent = deepcopy(source_intent)
    intent.update(schema_version=intent_version(protocol), experiment_id=str(uuid.uuid4()),
        protocol=protocol, protocol_sha256=sha(canonical(protocol)),
        sequence_sha256=sha(canonical(sequence_descriptor(protocol, reference))),
        source_files=sources, reference=reference,
        source_evidence={'path': str(args.source_evidence.resolve()),
                         'sha256': sha(originals['source-evidence.json'])})
    observed_world = getattr(args, 'expected_world', None)
    if observed_world is not None:
        require(protocol['schema_version'] == 'carla-throttle-isolation-protocol/2',
                'World rebasing requires explicit protocol2')
        observation = strict_json(read_bounded(observed_world, limit=256 * 1024))
        intent['expected_world'] = deepcopy(observation['state'])
    intent = validate_intent(intent)
    content = canonical(intent)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    return {'path': str(args.output), 'experiment_id': intent['experiment_id'],
        'intent_sha256': sha(content), 'protocol_sha256': intent['protocol_sha256'],
        'sequence_sha256': intent['sequence_sha256'],
        'reference_capture': str(args.reference_capture.resolve()),
        'reference_source_dir': str(args.reference_source_dir.resolve()),
        'reference_experiment_id': source_intent['experiment_id'],
        'reference_artifact_hashes': artifact_hashes, 'engine_actions': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference-capture', type=Path, required=True)
    parser.add_argument('--reference-source-dir', type=Path, required=True)
    parser.add_argument('--source-evidence', type=Path, required=True)
    parser.add_argument('--source-dir', type=Path, required=True)
    parser.add_argument('--branch', choices=('continue_pi', 'hold_throttle'), required=True)
    parser.add_argument('--continuation-substep-s', type=float, choices=(.01, .005),
        help='Version2: after matched F0 and command0 ACK, apply 0.01s sham or 0.005s treatment; preserve the internal settings tick as interval0. Requires hold_throttle.')
    parser.add_argument('--expected-world', type=Path,
        help='Explicit fresh read-only world observation; protocol2 permits only episode ID to differ from the original reference.')
    parser.add_argument('--output', type=Path, required=True)
    print(json.dumps(prepare(parser.parse_args()), sort_keys=True))


if __name__ == '__main__':
    main()
