"""Build-specific private-engine experiment, never a production calibration.

Only an already-active longitudinal sticky row's acceleration-spring damping
may change. Activation, target, impulse limits, lateral and suspension rows,
controller inputs and measured outputs must remain byte-for-byte untouched.
Offsets are checked against the installed binary's DWARF by the GDB loader.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct

VERSION = 'carla-private-sticky-damping/1'
ROW_SIZE = 80
DATA_SIZE = 348
DAMPING_OFFSET = 68
ORIGINAL_DAMPING = 1000.0


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def profile(damping):
    if type(damping) not in (int, float) or damping not in (4.0, 1000.0):
        raise ValueError('Only predeclared damping4 candidate or damping1000 sham is supported')
    return {'schema_version': VERSION, 'forward_damping_per_s': float(damping),
            'scope': 'all_vehicle_forward_sticky_rows_in_owned_private_engine',
            'activation_policy': 'unchanged_native', 'lateral_and_suspension': 'unchanged',
            'target_and_impulse_limits': 'unchanged', 'production_rollout': False}


def validate_profile(value):
    if type(value) is not dict or canonical(value) != canonical(profile(value.get('forward_damping_per_s'))):
        raise ValueError('Private sticky profile does not match the exact contract')
    return value


def active_rows(data):
    if type(data) is not bytes or len(data) != DATA_SIZE:
        raise ValueError('Expected the pinned348-byte constraint data')
    groups = []
    for offset in (112, 228, 344):
        flags = list(data[offset:offset + 4])
        if any(value not in (0, 1) for value in flags):
            raise ValueError('Native active flags are not booleans')
        groups.append([i for i, flag in enumerate(flags) if flag])
    return groups


def transform_rows(rows: bytes, data: bytes, selected: dict):
    """Pure, independently testable row transformation; performs no I/O."""
    validate_profile(selected)
    suspension, forward, lateral = active_rows(data)
    expected_count = len(suspension) + len(forward) + len(lateral)
    if type(rows) is not bytes or len(rows) != expected_count * ROW_SIZE:
        raise ValueError('Returned row count differs from native active flags')
    rewritten = bytearray(rows)
    changes = []
    for ordinal, wheel in enumerate(forward, start=len(suspension)):
        offset = ordinal * ROW_SIZE
        floats = struct.unpack_from('<18f', rows, offset)
        flags = struct.unpack_from('<H', rows, offset + 76)[0]
        target = struct.unpack_from('<f', data, 212 + wheel * 4)[0]
        direction = struct.unpack_from('<3f', data, 164 + wheel * 12)
        if (not all(math.isfinite(x) for x in floats)
                or flags != 3 or floats[16] != 0.0 or floats[17] != ORIGINAL_DAMPING
                or floats[3] != 0.0 or floats[7] != target or floats[:3] != direction
                or not floats[11] < -1e30 or not floats[15] > 1e30):
            raise ValueError('Native forward row semantics differ; refuse to rewrite')
        value = selected['forward_damping_per_s']
        struct.pack_into('<f', rewritten, offset + DAMPING_OFFSET, value)
        changes.append({'row': ordinal, 'wheel': wheel, 'byte_offset': offset + DAMPING_OFFSET,
                        'before': ORIGINAL_DAMPING, 'after': value})
    allowed = {byte for item in changes for byte in range(item['byte_offset'], item['byte_offset'] + 4)}
    if any(a != b and index not in allowed for index, (a, b) in enumerate(zip(rows, rewritten))):
        raise ValueError('Unexpected mutation outside the damping field')
    return bytes(rewritten), {'row_count': expected_count, 'suspension_wheels': suspension,
                             'forward_wheels': forward, 'lateral_wheels': lateral,
                             'damping_fields': changes,
                             'unrelated_bytes_unchanged': True}
