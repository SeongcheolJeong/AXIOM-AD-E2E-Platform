"""Offline verification of private solver-row damping interventions and A/B.

Does not call CARLA, import captured code, change metrics or omit stop samples.
Native byte changes are independently reconstructed with the pinned contract.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

from analyze_native_stop_observer import analyze as analyze_native, parse
from sticky_damping_contract import canonical, transform_rows, validate_profile


def sha(raw): return hashlib.sha256(raw).hexdigest()


def verify_interventions(path):
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('Invalid or oversized constraint record')
    head, selected, header, owner = '0' * 64, None, None, None
    calls, forward, changed, masks = 0, 0, 0, Counter()
    first_frame, last_frame = None, None
    for index, line in enumerate(path.read_bytes().splitlines()):
        wrapper = parse(line); event = wrapper['event']
        if (type(event.get('sequence')) is not int or event['sequence'] != index
                or event['previous_sha256'] != head or sha(canonical(event)) != wrapper['sha256']):
            raise ValueError('Constraint event chain mismatch')
        head = wrapper['sha256']
        kind = event['kind']
        if index == 0:
            if kind != 'intervention_loaded': raise ValueError('Missing intervention header')
            selected = validate_profile(event['profile']); header = event
            if event['profile_sha256'] != sha(canonical(selected)):
                raise ValueError('Intervention profile hash mismatch')
        elif kind == 'owned_process':
            if owner is not None: raise ValueError('Repeated intervention owner')
            owner = event
        elif kind == 'solver_rows':
            if owner is None: raise ValueError('Solver rows precede ownership verification')
            before = base64.b64decode(event['before_b64'], validate=True)
            after = base64.b64decode(event['after_b64'], validate=True)
            data = base64.b64decode(event['constant_block_b64'], validate=True)
            expected, details = transform_rows(before, data, selected)
            if expected != after or event.get('readback_matched') is not True:
                raise ValueError('Recorded mutation differs from the approved damping fields')
            if any(event.get(k) != v for k, v in details.items()):
                raise ValueError('Constraint record contradicts native row bytes')
            frame = event.get('world_frame')
            if type(frame) is not int or frame < 0 or last_frame is not None and frame < last_frame:
                raise ValueError('Invalid constraint frame sequence')
            first_frame = frame if first_frame is None else first_frame
            last_frame = frame
            calls += 1; forward += len(details['forward_wheels'])
            changed += sum(item['before'] != item['after'] for item in details['damping_fields'])
            masks[str(frame)] += len(details['forward_wheels'])
        else:
            raise ValueError('Unknown or incomplete constraint event: ' + kind)
    if not calls or owner is None or header is None:
        raise ValueError('No complete actual constraint observations')
    return {'status': 'all_recorded_row_rewrites_verified', 'header': header, 'owner': owner,
            'profile': selected, 'solver_callbacks': calls, 'forward_rows': forward,
            'rewritten_damping_fields': changed, 'outside_damping_changes': 0,
            'first_frame': first_frame, 'last_frame': last_frame,
            'forward_rows_by_frame': dict(masks), 'file_sha256': sha(path.read_bytes()),
            'event_head_sha256': head}


def trial(root, baseline):
    root = Path(root)
    native = analyze_native(root, baseline_capture=baseline)
    changes = verify_interventions(root / 'native-sticky-damping.jsonl')
    capture = root / 'observed'
    intent_raw = (capture / 'source-intent.json').read_bytes(); intent = parse(intent_raw)
    report = parse((capture / 'report.json').read_bytes())
    launch = parse((root / 'launcher-result.json').read_bytes())
    intervention = intent['native_intervention']
    if (report['intent_sha256'] != sha(json.dumps(intent, sort_keys=True, separators=(',', ':'),
            ensure_ascii=False, allow_nan=False).encode())
            or intervention['profile'] != changes['profile']
            or launch['sticky_damping_intervention'] != changes['profile']
            or launch['status'] != 'completed' or launch['remaining_owned_pids']
            or launch.get('cleanup_errors')):
        raise ValueError('Experiment profile, completion or cleanup identity mismatch')
    for name, digest in intervention['source_hashes'].items():
        path = root / name
        if path.name != name or path.is_symlink() or sha(path.read_bytes()) != digest:
            raise ValueError('Intervention implementation hash mismatch')
    if changes['owner']['pid'] != intent['owner']['pid']:
        raise ValueError('Intervention did not belong to the trial engine')
    if changes['header']['script_sha256'] != intervention['source_hashes']['native_sticky_damping_gdb.py']:
        raise ValueError('Loaded hook implementation differs')
    binding = {'base_input_hash': intent['request']['input_hash'], 'physics_profile': intent['physics_profile'],
               'intervention': intervention}
    if sha(canonical(binding)) != intent['native_candidate_hash']:
        raise ValueError('Candidate hash mismatch')
    raw = parse((capture / 'artifacts/carla-vehicle-response.json').read_bytes())['observations']
    trace = parse((capture / 'artifacts/carla-stack-trace.json').read_bytes())['ticks']
    speeds = [math.sqrt(sum(o['actors']['ego']['velocity_mps'][k] ** 2 for k in ('x', 'y', 'z'))) for o in raw]
    acc = [0.] + [(b-a)/.05 for a,b in zip(speeds,speeds[1:])]
    jerks = [(b-a)/.05 for a,b in zip(acc,acc[1:])]
    for i, (o, row) in enumerate(zip(raw, native['rows'])):
        if abs(row['acceleration_mps2'] - acc[i]) > 1e-8:
            raise ValueError('Acceleration is not the original full-sample metric')
        if i and (o['world_frame'] != raw[i-1]['world_frame'] + 1
                  or abs(o['source_time_s']-raw[i-1]['source_time_s']-.05) > 1e-6):
            raise ValueError('API time/frame sequence incomplete')
        if i < len(trace):
            command = trace[i]
            if (command['sequence'] != i or not command['control_acknowledged']
                    or command['application_status'] != 'next_frame_observed'
                    or command['source_world_frame'] != o['world_frame']
                    or command['next_world_frame'] != raw[i+1]['world_frame']):
                raise ValueError('Command/ACK/next frame identity differs')
            row['mode'] = command['output']['decision']['mode']
            row['decision_reasons'] = command['output']['decision']['reasons']
        else: row['mode'] = 'final_observation'; row['decision_reasons'] = []
    computed = {'max_deceleration_mps2': max(0., -min(acc)), 'max_jerk_mps3': max(map(abs, jerks))}
    metrics = {m['name']: m for m in native['metrics']}
    for name, value in computed.items():
        expected_threshold = 6. if name == 'max_deceleration_mps2' else 10.
        metric = metrics[name]
        if (metric['threshold'] != expected_threshold or metric['operator'] != 'lte'
                or abs(metric['value'] - value) > 1e-7 or metric['passed'] != (value <= expected_threshold)):
            raise ValueError('Metric value, threshold or verdict changed')
    mode_counts = dict(Counter(row['mode'] for row in native['rows'][:-1]))
    return {'native': native, 'intervention': changes, 'candidate_hash': intent['native_candidate_hash'],
            'request': intent['request'], 'physics_profile': intent['physics_profile'],
            'source_manifest_sha256': intent['source_manifest_sha256'], 'computed': computed,
            'mode_counts': mode_counts, 'report_sha256': sha((capture/'report.json').read_bytes()),
            'terminal_speed_mps': speeds[-1], 'all_81_samples_included': True,
            'cleanup_verified': True, 'wall_time_is_benchmark': False}


def compare(sham_root, candidate_root):
    baseline = Path(sham_root) / 'observed'
    a, b = trial(sham_root, baseline), trial(candidate_root, baseline)
    if a['intervention']['profile']['forward_damping_per_s'] != 1000. or b['intervention']['profile']['forward_damping_per_s'] != 4.:
        raise ValueError('Expected the predeclared1000 versus4 pair')
    request_a = {k:v for k,v in a['request'].items() if k != 'execution_id'}
    request_b = {k:v for k,v in b['request'].items() if k != 'execution_id'}
    if request_a != request_b or a['physics_profile'] != b['physics_profile'] or a['source_manifest_sha256'] != b['source_manifest_sha256']:
        raise ValueError('Scenario, controller, sensor, physics or source changed beyond damping')
    return {'schema_version': 'carla-private-sticky-damping-comparison/1',
            'same_authored_request_and_source': True, 'production_rollout': False,
            'real_world_calibration': False, 'new_native_executions': 2,
            'control_commands': 160, 'api_observations': 162,
            'initial_speed_delta_mps': b['native']['rows'][0]['speed_mps']-a['native']['rows'][0]['speed_mps'],
            'trials': {'sham': a, 'candidate': b},
            'limitations': ['Applies to all forward-sticky vehicle rows in the private two-vehicle scene, not ego-only.',
                'Debugger writes transient solver damping: experimental vehicle-model change, not controller improvement or production deployment.',
                'Fixed50ms all-sample metrics are unchanged. Coefficients are a hypothesis, not real-world calibration.',
                'Constraint impulse and actual brake torque are not measured; each condition is a single run.']}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sham', required=True, type=Path); p.add_argument('--candidate', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    args = p.parse_args(); result = compare(args.sham, args.candidate)
    with args.output.open('xb') as f: f.write(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False).encode())
    print(json.dumps({k: {'metrics':v['computed'], 'modes':v['mode_counts'], 'constraint_rows':v['intervention']['forward_rows'],
                         'verdict':v['native']['quality_verdict']} for k,v in result['trials'].items()}))
