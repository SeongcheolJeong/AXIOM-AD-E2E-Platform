#!/usr/bin/env python3
"""Join private native observations to API snapshots without time shifting.

This is an offline reader. It never imports captured code or calls CARLA/GDB.
Missing flags remain missing and prevent a complete native comparison.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def parse(raw):
    def invalid(value):
        raise ValueError('Nonfinite JSON token')
    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid)


def read_stream(path):
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('Unsafe or oversized native stream')
    events, samples = [], {}
    previous = '0' * 64
    for index, line in enumerate(path.read_bytes().splitlines()):
        item = parse(line)
        event = item['event']
        if (event['sequence'] != index or type(event['sequence']) is not int
                or event['previous_sha256'] != previous or sha(canonical(event)) != item['sha256']):
            raise ValueError('Native event chain mismatch')
        previous = item['sha256']
        if event['kind'] in ('observer_error', 'sample_limit'):
            raise ValueError('Native observation is incomplete: ' + str(event))
        if event['kind'] == 'vehicle_sample':
            if event.get('status') != 'observed':
                raise ValueError('Sample is not an observation')
            for key in ('world_frame', 'actor_id'):
                if type(event.get(key)) is not int or event[key] < 0:
                    raise ValueError('Missing integer frame/actor identity')
            for key in ('sticky_forward_active', 'sticky_side_active', 'suspension_limit_active'):
                values = event.get(key)
                if not isinstance(values, list) or len(values) != 4 or any(type(x) is not bool for x in values):
                    raise ValueError('Missing or invalid native flag: ' + key)
            if type(event.get('rigid_sleeping')) is not bool:
                raise ValueError('Missing sleep flag is not false')
            for key in ('wheel_speeds_native', 'corrected_wheel_speeds_native', 'low_forward_speed_timers_s'):
                values = event.get(key)
                if not isinstance(values, list) or len(values) != 4 or any(type(x) not in (int, float) or not math.isfinite(x) for x in values):
                    raise ValueError('Invalid native numeric array: ' + key)
            identity = (event['world_frame'], event['actor_id'])
            if identity in samples:
                raise ValueError('Ambiguous duplicate native frame/actor')
            samples[identity] = event
        events.append(event)
    if not events or events[0]['kind'] != 'observer_loaded':
        raise ValueError('Missing observer header')
    return events, samples


def read_clock(path):
    """Preserve every native update and summarize only exact counter groups."""
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('Unsafe or oversized clock stream')
    frames = defaultdict(list)
    head = '0' * 64
    last_frame = -1
    for index, raw in enumerate(path.read_bytes().splitlines()):
        wrapper = parse(raw)
        event = wrapper['event']
        if (type(event.get('sequence')) is not int or event['sequence'] != index
                or event['previous_sha256'] != head or sha(canonical(event)) != wrapper['sha256']):
            raise ValueError('Clock event chain mismatch')
        head = wrapper['sha256']
        if index == 0 and event['kind'] != 'clock_observer_loaded':
            raise ValueError('Missing clock observer header')
        if event['kind'] == 'observer_error':
            raise ValueError('Clock observer failed')
        if event['kind'] != 'vehicle_update':
            continue
        frame, dt = event.get('world_frame'), event.get('delta_seconds')
        if (type(frame) is not int or frame < last_frame or type(dt) not in (float, int)
                or not math.isfinite(dt) or not 0 < dt <= .1):
            raise ValueError('Invalid native time/counter')
        frames[frame].append(dt)
        last_frame = frame
    if not frames:
        raise ValueError('No actual vehicle-update calls were recorded')
    values = [dt for row in frames.values() for dt in row]
    return {'updates': len(values), 'frames': len(frames),
        'min_delta_seconds': min(values), 'max_delta_seconds': max(values),
        'updates_per_frame_histogram': dict(Counter(map(len, frames.values()))),
        'per_frame': {str(key): {'count': len(row), 'sum_delta_seconds': sum(row),
                                'deltas': row} for key, row in frames.items()},
        'sha256': sha(path.read_bytes()), 'head_sha256': head,
        'scope': 'FPhysXVehicleManager::Update invocations; does not count further vehicle/tire internal iterations.'}


def analyze(root, *, baseline_capture=None):
    root = Path(root)
    events, native = read_stream(root / 'native-stop.jsonl')
    capture = root / 'observed'
    report = parse((capture / 'report.json').read_bytes())
    result_raw = (capture / 'result.json').read_bytes()
    result = parse(result_raw)
    if report['status'] != 'completed' or not report['world_restored'] or report['result_sha256'] != sha(result_raw):
        raise ValueError('Private run is incomplete or result bytes differ')
    for item in report['artifacts']:
        raw = (capture / 'artifacts' / item['name']).read_bytes()
        if sha(raw) != item['sha256'] or len(raw) != item['size_bytes']:
            raise ValueError('Artifact mismatch')
    observations = parse((capture / 'artifacts/carla-vehicle-response.json').read_bytes())['observations']
    trace = parse((capture / 'artifacts/carla-stack-trace.json').read_bytes())['ticks']
    if len(observations) != 81 or len(trace) != 80:
        raise ValueError('Original81/80 denominator is incomplete')
    rows, missing = [], []
    for index, observation in enumerate(observations):
        ego = observation['actors']['ego']
        identity = (observation['world_frame'], ego['actor_id'])
        sample = native.get(identity)
        if sample is None:
            missing.append(identity)
        frame = result['trajectory'][index]
        rows.append({'index': index, 'time_s': frame['time_s'], 'source_time_s': observation['source_time_s'],
            'world_frame': observation['world_frame'], 'actor_id': ego['actor_id'],
            'speed_mps': frame['ego']['speed_mps'], 'acceleration_mps2': frame['acceleration_mps2'],
            'api_wheel_omega': [w['fields']['omega'].get('value') for w in ego['telemetry']['wheels']],
            'reported_brake': ego['reported_control']['data']['brake'],
            'requested_brake': trace[index]['requested_carla_control']['brake'] if index < 80 else None,
            'native': sample})
    if missing:
        raise ValueError('Exact frame/actor join failed; no offset or interpolation allowed: ' + str(missing[:4]))
    for index, row in enumerate(rows):
        speed = math.sqrt(sum(observations[index]['actors']['ego']['velocity_mps'][k] ** 2 for k in ('x', 'y', 'z')))
        if abs(row['speed_mps'] - speed) > 1e-9:
            raise ValueError('API speed differs from original XYZ')
        row['jerk_mps3'] = None if index == 0 else (row['acceleration_mps2'] - rows[index - 1]['acceleration_mps2']) / .05
        # The pinned getter at0x69d48f0 reads block+0x10 (corrected speeds),
        # not block+0x00 (raw integration speeds). Both originals stay in data.
        if any(abs(a - b) > 1e-7 for a, b in zip(row['api_wheel_omega'], row['native']['corrected_wheel_speeds_native'])):
            raise ValueError('Native wheel state does not match the exact API observation')
    peak = max(range(1, 81), key=lambda i: abs(rows[i]['jerk_mps3']))
    baseline_path = Path(baseline_capture) if baseline_capture is not None else root / 'baseline'
    baseline = parse((baseline_path / 'result.json').read_bytes())
    if len(baseline['trajectory']) != len(rows):
        raise ValueError('Baseline denominator differs')
    deltas = [abs(a['ego']['speed_mps'] - b['ego']['speed_mps']) for a, b in zip(baseline['trajectory'], result['trajectory'])]
    clock = read_clock(root / 'native-physics-clock.jsonl')
    matched_clocks = [clock['per_frame'].get(str(row['world_frame'])) for row in rows]
    if any(value is None for value in matched_clocks):
        raise ValueError('A retained API frame has no same-counter native clock group')
    if any(abs(value['sum_delta_seconds'] - .05) > 1e-6 for value in matched_clocks):
        raise ValueError('Native frame clock sum is incomplete')
    transitions = []
    previous_flags = None
    for row, actual_clock in zip(rows, matched_clocks):
        row['native_clock'] = {k: v for k, v in actual_clock.items() if k != 'deltas'}
        flags = {k: row['native'][k] for k in ('sticky_forward_active', 'sticky_side_active', 'rigid_sleeping')}
        if flags != previous_flags:
            transitions.append({'index': row['index'], 'time_s': row['time_s'],
                'world_frame': row['world_frame'], 'speed_mps': row['speed_mps'], **flags})
            previous_flags = flags
    return {'schema_version': 'carla-native-stop-analysis/1', 'status': 'exact_frame_join_verified',
        'execution_id': report['execution_id'], 'native_events': len(events), 'native_samples': len(native),
        'ego_observations': len(rows), 'unjoined_ego_observations': 0, 'peak_jerk_index': peak,
        'private_baseline_max_speed_difference_mps': max(deltas),
        'metrics': result['metrics'], 'quality_verdict': report['quality_verdict'],
        'native_clock': clock, 'flag_transitions': transitions,
        'api_native_corrected_wheel_speeds_match': True,
        'native_stream_sha256': sha((root / 'native-stop.jsonl').read_bytes()),
        'observer_header': events[0], 'rows': rows,
        'scope': 'Sampled native states at API observation frames, not substep-complete tracing, actual torque or calibrated vehicle acceptance.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = analyze(args.root)
    with args.output.open('xb') as stream:
        stream.write(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False).encode())
    print(json.dumps({k: data[k] for k in ('status', 'native_samples', 'ego_observations', 'quality_verdict', 'private_baseline_max_speed_difference_mps')}))
