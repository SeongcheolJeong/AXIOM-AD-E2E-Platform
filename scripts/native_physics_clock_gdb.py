"""Observe actual vehicle-update dt in the already owned private GDB child.

No target call or target data write. Stops briefly at the installed native
manager function entry; the original thread resumes without changing inputs.
Only updates with vehicle objects are recorded. These are observed function
invocations, not a claim about every inner tire-solver iteration.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import time


def install_clock():
    import gdb
    from native_stop_observer_gdb import validate_owner

    root = Path(os.environ['AXIOM_NATIVE_OBSERVER_ROOT']).resolve()
    owner = None
    if gdb.selected_inferior().pid:
        raise ValueError('Load clock observer before launching the private child')
    if gdb.parameter('may-call-functions'):
        raise ValueError('Inferior function calls must be disabled')
    stream = (root / 'native-physics-clock.jsonl').open('x', buffering=1)
    count, head = 0, '0' * 64

    def emit(kind, values):
        nonlocal count, head
        event = {'schema_version': 'carla-native-physics-clock/1', 'sequence': count,
                 'previous_sha256': head, 'kind': kind, **values}
        raw = json.dumps(event, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        head = hashlib.sha256(raw).hexdigest()
        stream.write(json.dumps({'event': event, 'sha256': head}, separators=(',', ':')) + '\n')
        count += 1

    emit('clock_observer_loaded', {'debugger_pid': os.getpid(), 'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
         'function': 'FPhysXVehicleManager::Update(FPhysScene_PhysX*, float)',
         'clock': 'FCarlaEngine::FrameCounter; delta is native float argument, x86_64 SysV xmm0',
         'inferior_calls': False, 'state_writes': False})

    class Clock(gdb.Breakpoint):
        def stop(self):
            nonlocal owner
            try:
                if owner is None:
                    owner = validate_owner(gdb.selected_inferior().pid, os.getpid(), root)
                    emit('owned_process', owner)
                if gdb.selected_inferior().pid != owner['pid']:
                    raise ValueError('Private inferior changed')
                manager = gdb.Value(int(gdb.parse_and_eval('$rdi'))).cast(
                    gdb.lookup_type('FPhysXVehicleManager').pointer()).dereference()
                n = int(manager['PRaycastVehicles']['ArrayNum']) + int(manager['PSweepVehicles']['ArrayNum'])
                if n == 0:
                    return False
                if count >= 20000:
                    raise ValueError('Clock capture bound exceeded')
                dt = float(gdb.parse_and_eval('$xmm0.v4_float[0]'))
                if not math.isfinite(dt) or not 0 < dt <= .1:
                    raise ValueError('Invalid native dt')
                emit('vehicle_update', {'world_frame': int(gdb.parse_and_eval('FCarlaEngine::FrameCounter')),
                     'delta_seconds': dt, 'vehicles': n, 'manager': hex(int(manager.address)),
                     'wall_monotonic_ns': time.monotonic_ns()})
            except Exception as error:
                emit('observer_error', {'error': type(error).__name__ + ': ' + str(error)[:1200]})
                return True
            return False

    symbol = '_ZN20FPhysXVehicleManager6UpdateEP16FPhysScene_PhysXf'
    address = int(gdb.parse_and_eval('(void*)&' + symbol))
    Clock('*' + hex(address), internal=True)
    gdb.set_convenience_variable('axiom_clock_ready', gdb.Value(1))
    print('NATIVE_PHYSICS_CLOCK_READY', hex(address), flush=True)


if __name__ == '__main__':
    install_clock()
