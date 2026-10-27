"""Opt-in generated-constraint intervention in a GDB-LAUNCHED private engine.

This script DOES write damping fields in transient solver output rows. It is
not a read-only observer. No executable file, production process, constraint
activation, velocity, pose, target, impulse limit, brake or policy is modified.
The matching sham runs this same hook with1000 and performs zero data writes.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import time

from native_stop_observer_gdb import BINARY_SHA256, validate_owner
from sticky_damping_contract import (DATA_SIZE, ROW_SIZE, active_rows, canonical,
                                     transform_rows, validate_profile)

SYMBOL = '_ZN5physx25PxVehicleConstraintShader36vehicleSuspLimitConstraintSolverPrepEPNS_14Px1DConstraintERNS_6PxVec3EjRNS_24PxConstraintInvMassScaleEPKvRKNS_11PxTransformESB_'


def install():
    import gdb

    if gdb.selected_inferior().pid or gdb.parameter('may-call-functions'):
        raise ValueError('Install before launch with inferior function calls disabled')
    root = Path(os.environ['AXIOM_NATIVE_OBSERVER_ROOT']).resolve()
    binary = Path(gdb.current_progspace().filename).resolve()
    if hashlib.sha256(binary.read_bytes()).hexdigest() != BINARY_SHA256:
        raise ValueError('Unrecognized executable')
    raw_profile = (root / 'sticky-profile.json').read_bytes()
    selected = validate_profile(json.loads(raw_profile))
    if raw_profile != canonical(selected):
        raise ValueError('Noncanonical intervention profile')
    address = int(gdb.parse_and_eval('(void*)&' + SYMBOL))
    gdb.block_for_pc(address)
    row_type = gdb.lookup_type('physx::Px1DConstraint')
    data_type = gdb.lookup_type('physx::PxVehicleConstraintShader::VehicleConstraintData')
    row_offsets = {field.name: field.bitpos // 8 for field in row_type.fields()}
    if (row_type.sizeof != ROW_SIZE or data_type.sizeof != DATA_SIZE
            or any(row_offsets.get(k) != v for k, v in
                   {'linear0': 0, 'geometricError': 12, 'velocityTarget': 28,
                    'minImpulse': 44, 'maxImpulse': 60, 'mods': 64, 'flags': 76}.items())):
        raise ValueError('Installed solver row layout differs')
    stream = (root / 'native-sticky-damping.jsonl').open('x', buffering=1)
    sequence, head, owner = 0, '0' * 64, None

    def emit(kind, payload):
        nonlocal sequence, head
        event = {'schema_version': 'carla-sticky-damping-event/1', 'sequence': sequence,
                 'previous_sha256': head, 'kind': kind, **payload}
        head = hashlib.sha256(canonical(event)).hexdigest()
        stream.write(json.dumps({'event': event, 'sha256': head}, separators=(',', ':')) + '\n')
        sequence += 1

    emit('intervention_loaded', {'profile': selected,
        'profile_sha256': hashlib.sha256(raw_profile).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'binary_sha256': BINARY_SHA256, 'symbol': SYMBOL, 'address': hex(address),
        'inferior_calls': False, 'state_writes': selected['forward_damping_per_s'] != 1000.0,
        'write_scope': 'generated_longitudinal_sticky_spring_damping_fields_only',
        'activation_target_impulse_limits_preserved': True, 'row_offsets': row_offsets})

    # The pinned shader is a leaf with one RET. Its rdi output-buffer and r8
    # constant-block registers survive unchanged. Stop once at that verified
    # instruction rather than installing a slow per-call FinishBreakpoint.
    return_address = address + 0x9bd
    disassembly = gdb.selected_inferior().architecture().disassemble(address, return_address + 1)
    if [row['addr'] for row in disassembly if row['asm'].strip().startswith('ret')] != [return_address]:
        raise ValueError('Pinned shader does not have its expected unique return')
    import re
    if any(row['asm'].strip().startswith('call') or re.search(
            r',\s*%(?:rdi|edi|di|dil|r8|r8d|r8w|r8b)\s*$', row['asm']) for row in disassembly):
        raise ValueError('Shader pointer registers are not invariant')

    class Probe(gdb.Breakpoint):
        def stop(self):
            nonlocal owner
            try:
                inferior = gdb.selected_inferior()
                if owner is None:
                    owner = validate_owner(inferior.pid, os.getpid(), root)
                    emit('owned_process', owner)
                if inferior.pid != owner['pid']:
                    raise ValueError('Private child changed')
                data = inferior.read_memory(int(gdb.parse_and_eval('$r8')), DATA_SIZE).tobytes()
                if not active_rows(data)[1]:
                    return False
                pointer = int(gdb.parse_and_eval('$rdi'))
                returned = int(gdb.parse_and_eval('$eax'))
                if not 1 <= returned <= 12:
                    raise ValueError('Invalid native solver row count')
                before = inferior.read_memory(pointer, returned * ROW_SIZE).tobytes()
                after, details = transform_rows(before, data, selected)
                for field in details['damping_fields']:
                    offset = field['byte_offset']
                    if before[offset:offset + 4] != after[offset:offset + 4]:
                        inferior.write_memory(pointer + offset, after[offset:offset + 4])
                if inferior.read_memory(pointer, len(before)).tobytes() != after:
                    raise ValueError('Transient solver row write readback mismatch')
                if sequence >= 12000:
                    raise ValueError('Intervention record bound exceeded')
                emit('solver_rows', {'world_frame': int(gdb.parse_and_eval('FCarlaEngine::FrameCounter')),
                    'thread': gdb.selected_thread().global_num, 'wall_monotonic_ns': time.monotonic_ns(), **details,
                    'constant_block_b64': base64.b64encode(data).decode(),
                    'before_b64': base64.b64encode(before).decode(),
                    'after_b64': base64.b64encode(after).decode(), 'readback_matched': True})
            except Exception as error:
                emit('intervention_error', {'error': type(error).__name__ + ': ' + str(error)[:1200]})
                return True
            return False

    Probe('*' + hex(return_address), internal=True)
    gdb.set_convenience_variable('axiom_damping_ready', gdb.Value(1))
    print('NATIVE_STICKY_DAMPING_READY', selected['forward_damping_per_s'], flush=True)


if __name__ == '__main__':
    install()
