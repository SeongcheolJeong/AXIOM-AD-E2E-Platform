"""Read native stop state in a GDB-LAUNCHED private CARLA process only.

Load with GDB's ``source`` BEFORE ``run``. This is a version-pinned observer,
not a production hook. No attach, inferior function calls, parameter writes,
binary-file patch or change to the brake controller is performed. Software
breakpoints pause the private process: timing/performance is not a benchmark.

Three offsets come from the installed binary's preserved disassembly, not a
generic PhysX ABI: vehicle->WheelsDynData 0x70, vehicle->rigid actor 0xa0,
NpRigidDynamic::isSleeping reads a uint32 at0x178. Other layouts use DWARF.
The public PhysX header is NOT substituted for the installed type layouts.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import struct
import time

BINARY_SHA256 = 'd9433afa4f5961f1419399d61477ae52f83c54d4f657bd871f73739c8f11e958'
BUILD_ID = 'a489e7257fbe49a8'
PORT = 2300
MAX_SAMPLES = 600


def validate_owner(pid: int, debugger_pid: int, root: Path) -> dict:
    """Reject another process, production port, or an unpinned executable."""
    proc = Path('/proc') / str(pid)
    args = proc.joinpath('cmdline').read_bytes().split(b'\0')
    stat = proc.joinpath('stat').read_text().rsplit(')', 1)[1].split()
    if int(stat[1]) != debugger_pid:
        raise ValueError('Observer must own the direct child, never attach')
    if (b'-carla-rpc-port=2300' not in args or b'-carla-rpc-port=2000' in args
            or ('-userdir=' + str(root / 'home')).encode() not in args):
        raise ValueError('Private endpoint/user directory identity differs')
    executable = proc.joinpath('exe').resolve()
    if hashlib.sha256(executable.read_bytes()).hexdigest() != BINARY_SHA256:
        raise ValueError('Unrecognized native build; offsets cannot be used')
    return {'pid': pid, 'parent_pid': debugger_pid, 'start_ticks': stat[19],
            'executable': str(executable), 'binary_sha256': BINARY_SHA256,
            'build_id': BUILD_ID, 'rpc_port': PORT, 'user_directory': str(root / 'home')}


def install():
    import gdb

    if gdb.selected_inferior().pid:
        raise ValueError('Load before launching a private inferior; attaching is prohibited')
    root = Path(os.environ['AXIOM_NATIVE_OBSERVER_ROOT']).resolve()
    if not root.is_dir() or root.is_symlink():
        raise ValueError('Pre-created private observer directory required')
    binary = Path(gdb.current_progspace().filename).resolve()
    if hashlib.sha256(binary.read_bytes()).hexdigest() != BINARY_SHA256:
        raise ValueError('Binary pin mismatch')
    gdb.execute('set may-call-functions off')
    gdb.execute('set print thread-events off')
    gdb.execute('set startup-with-shell off')

    # GDB may leave third-party DWARF units lazy even when minimal executable
    # symbols are known. Resolve function blocks before querying their types.
    for symbol in ('_ZNK5physx22PxVehicleWheelsDynData21getWheelRotationSpeedEj',
                   '_ZN5physx25PxVehicleConstraintShader17getBinaryMetaDataERNS_14PxOutputStreamE'):
        address = int(gdb.parse_and_eval('(void*)&' + symbol))
        gdb.block_for_pc(address)
    wheels_type = gdb.lookup_type('physx::PxVehicleWheelsDynData')
    constraint_type = gdb.lookup_type('physx::PxVehicleConstraintShader::VehicleConstraintData')
    if wheels_type.sizeof != 48 or constraint_type.sizeof != 348:
        raise ValueError('Installed DWARF layouts differ')
    constraints = {f.name: f.bitpos // 8 for f in constraint_type.fields()}
    if constraints != {'mSuspLimitData': 0, 'mStickyTireForwardData': 116, 'mStickyTireSideData': 232}:
        raise ValueError('Native constraint fields differ')

    stream = (root / 'native-stop.jsonl').open('x', encoding='utf-8', buffering=1)
    count, head, owner = 0, '0' * 64, None

    def emit(kind, payload):
        nonlocal count, head
        row = {'schema_version': 'carla-native-stop-observation/1', 'sequence': count,
               'kind': kind, 'wall_monotonic_ns': time.monotonic_ns(),
               'previous_sha256': head, **payload}
        encoded = json.dumps(row, sort_keys=True, separators=(',', ':'), allow_nan=False)
        head = hashlib.sha256(encoded.encode()).hexdigest()
        stream.write(json.dumps({'event': row, 'sha256': head}, separators=(',', ':')) + '\n')
        count += 1

    def read_number(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('Nonfinite native value')
        return number

    def array(value, n=4, boolean=False):
        return [bool(value[i]) if boolean else read_number(value[i]) for i in range(n)]

    emit('observer_loaded', {'binary_sha256': BINARY_SHA256, 'build_id': BUILD_ID,
         'observer_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
         'debugger_version': gdb.VERSION, 'debugger_pid': os.getpid(), 'rpc_port': PORT,
         'layout': constraints, 'inferior_calls': False, 'state_writes': False,
         'clock': 'FCarlaEngine::FrameCounter at vehicle telemetry RPC entry; not each physics substep'})

    class Probe(gdb.Breakpoint):
        def stop(self):
            nonlocal owner
            if count >= MAX_SAMPLES:
                emit('sample_limit', {'status': 'incomplete'})
                return True
            try:
                inferior = gdb.selected_inferior()
                if owner is None:
                    owner = validate_owner(inferior.pid, os.getpid(), root)
                    emit('owned_process', owner)
                if inferior.pid != owner['pid']:
                    raise ValueError('Inferior ownership changed')
                # Exact function entry / x86_64 SysV this argument. The mangled
                # symbol and executable bytes are pinned; no virtual call occurs.
                view = gdb.Value(int(gdb.parse_and_eval('$rdi'))).cast(
                    gdb.lookup_type('FVehicleActor').pointer()).dereference()
                actor = view['TheActor'].cast(gdb.lookup_type('ACarlaWheeledVehicle').pointer()).dereference()
                movement = actor['VehicleMovement'].dereference()
                vehicle = int(movement['PVehicle'])
                dynamics = gdb.Value(vehicle + 0x70).cast(wheels_type.pointer()).dereference()
                if int(dynamics['mNbActiveWheels']) != 4 or int(dynamics['mNbWheels4']) != 1:
                    raise ValueError('Only the pinned four-wheel layout is supported')
                block = dynamics['mWheels4DynData'][0]
                shader = int(block['mVehicleConstraints'])
                # prepareData() in this binary returns this+8. The fields below
                # are typed from its own DWARF, including the348-byte variant.
                data = gdb.Value(shader + 8).cast(constraint_type.pointer()).dereference()
                rigid = struct.unpack('<Q', inferior.read_memory(vehicle + 0xa0, 8).tobytes())[0]
                sleeping_raw = struct.unpack('<I', inferior.read_memory(rigid + 0x178, 4).tobytes())[0]
                row = {'status': 'observed', 'world_frame': int(gdb.parse_and_eval('FCarlaEngine::FrameCounter')),
                    'actor_id': int(view['Id']), 'native_vehicle': hex(vehicle), 'native_rigid_actor': hex(rigid),
                    'wheel_speeds_native': array(block['mWheelSpeeds']),
                    'corrected_wheel_speeds_native': array(block['mCorrectedWheelSpeeds']),
                    'low_forward_speed_timers_s': array(block['mTireLowForwardSpeedTimers']),
                    'low_side_speed_timers_s': array(block['mTireLowSideSpeedTimers']),
                    'sticky_forward_active': array(data['mStickyTireForwardData']['mActiveFlags'], boolean=True),
                    'sticky_side_active': array(data['mStickyTireSideData']['mActiveFlags'], boolean=True),
                    'sticky_forward_target_native': array(data['mStickyTireForwardData']['mTargetSpeeds']),
                    'suspension_limit_active': array(data['mSuspLimitData']['mActiveFlags'], boolean=True),
                    'rigid_sleeping': sleeping_raw != 0, 'rigid_sleeping_raw': sleeping_raw,
                    'movement': {name: read_number(movement[name]) for name in
                        ('RawThrottleInput', 'RawBrakeInput', 'ThrottleInput', 'BrakeInput',
                         'HandbrakeInput', 'IdleBrakeInput', 'StopThreshold')},
                    'brake_torque': {'status': 'not_observed', 'reason': 'No validated applied-torque field in this probe'},
                    'units': 'Native wheel speeds and targets retained; API correspondence checked separately'}
                emit('vehicle_sample', row)
            except Exception as error:
                emit('observer_error', {'status': 'incomplete', 'error': type(error).__name__ + ': ' + str(error)[:1200]})
                return True
            return False

    symbol = '_ZN13FVehicleActor23GetVehicleTelemetryDataER21FVehicleTelemetryData'
    address = int(gdb.parse_and_eval('(void*)&' + symbol))
    Probe('*' + hex(address), internal=True)
    gdb.set_convenience_variable('axiom_stop_ready', gdb.Value(1))
    print('NATIVE_STOP_OBSERVER_READY', hex(address), str(root), flush=True)


if __name__ == '__main__':
    install()
