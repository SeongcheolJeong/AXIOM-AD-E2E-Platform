"""Pinned network participant launcher and independent post-run network audit.

This module does not import optional engines and never supplies live control.
The native network runner owns SIL Kit, BSW, OS and C++ child processes.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import struct
import sys
try:
    from . import hil_dut
except ImportError:
    import hil_dut

VERSION = 'network-ecu/1'
SCHEMA = 'vecu-network-runtime/1'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def validate_runtime(value):
    fields = {'kind', 'schema_version', 'profile_id', 'manifest_sha256', 'network', 'faults', 'side',
              'dut_version', 'protocol', 'runtime_sha256'}
    if (not isinstance(value, dict) or set(value) != fields
            or value.get('kind') != 'network_reference' or value.get('schema_version') != SCHEMA
            or value.get('profile_id') != 'actual_silkit_autoas_trampoline'
            or value.get('dut_version') != VERSION or value.get('protocol') != 'hil-dut/1'
            or value.get('side') not in ('baseline', 'faulted')
            or not isinstance(value.get('network'), dict) or not value['network']
            or not isinstance(value.get('faults'), list) or any(not isinstance(x, dict) for x in value['faults'])
            or any(not isinstance(value.get(k), str) or not re.fullmatch('[0-9a-f]{64}', value[k])
                   for k in ('manifest_sha256', 'runtime_sha256'))):
        raise ValueError('Unsupported network ECU runtime identity')
    raw = canonical({k: v for k, v in value.items() if k != 'runtime_sha256'})
    if len(raw) > 1024 * 1024 or hashlib.sha256(raw).hexdigest() != value['runtime_sha256']:
        raise ValueError('Network ECU runtime hash mismatch')
    return value


def _file(root, relative):
    if (not isinstance(relative, str) or not relative or '\\' in relative
            or PurePosixPath(relative).is_absolute() or '..' in PurePosixPath(relative).parts
            or str(PurePosixPath(relative)) != relative):
        raise ValueError('Network install path must be normalized and relative')
    path = root
    for part in PurePosixPath(relative).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('Network install symlink is unsupported')
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Network install file is absent or escapes installation')
    return path


def validate_install(value, root):
    """Verify operator-pinned install, also usable by a read-only hash probe."""
    if not isinstance(value, dict) or not isinstance(value.get('manifest_sha256'), str) or not re.fullmatch('[0-9a-f]{64}', value['manifest_sha256']):
        raise ValueError('Network install manifest digest is invalid')
    root = Path(root)
    path = _file(root, 'network-install.json')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != value['manifest_sha256']:
        raise ValueError('Network install manifest changed')
    manifest = json.loads(raw)
    files, binaries = manifest.get('files'), manifest.get('binaries')
    if (manifest.get('schema_version') != 'vecu-network-install/1'
            or not isinstance(files, dict) or not files or 'network_runtime.py' not in files
            or not isinstance(binaries, dict) or not binaries
            or any(not isinstance(role, str) or not isinstance(relative, str) or relative not in files
                   for role, relative in binaries.items())):
        raise ValueError('Network install manifest is incomplete')
    for relative, expected in files.items():
        if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
            raise ValueError('Network install file digest is invalid')
        if hashlib.sha256(_file(root, relative).read_bytes()).hexdigest() != expected:
            raise ValueError('Network install file changed: ' + relative)
    return manifest


def command(value, root):
    validate_runtime(value)
    validate_install(value, root)
    return [sys.executable, str(Path(root) / 'network_runtime.py')]


def verified_install(value, root):
    validate_runtime(value)
    return validate_install(value, root)


def _same(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and actual.keys() == expected.keys() and all(_same(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(_same(a, b) for a, b in zip(actual, expected))
    if type(expected) is int:
        return type(actual) is int and actual == expected
    if type(expected) is float and type(actual) in (int, float):
        return math.isfinite(actual) and math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9)
    return type(actual) is type(expected) and actual == expected


def decode_command(data_hex):
    if not isinstance(data_hex, str) or not re.fullmatch('[0-9a-f]{16}', data_hex):
        raise ValueError('CAN command requires exactly eight bytes')
    throttle, brake, steer, sequence, epoch = struct.unpack('<HHhBB', bytes.fromhex(data_hex))
    if throttle > 10000 or brake > 10000 or steer == -32768:
        raise ValueError('CAN command signal range invalid')
    return {'throttle': throttle / 10000, 'brake': brake / 10000, 'steer': steer / 32767}, sequence, epoch


def _packet(value):
    """Only the fields carried by AXO1 belong to its independent wire audit."""
    return {'sequence': value['sequence'], 'time_s': value['time_s'], 'objects': [
        {'id':obj['id'], **{k: obj.get(k, 0.) for k in ('x', 'y', 'z', 'heading_rad', 'speed_mps')}}
        for obj in value['objects']]}


class NetworkAudit:
    def __init__(self, init):
        self.init = copy.deepcopy(init)
        runtime = self.init.pop('network_runtime', None)
        can = runtime.get('network', {}).get('can', {}) if runtime else {}
        self.watchdog_ms = can.get('watchdog_ms', 100)
        self.recovery_packets = can.get('recovery_packets', 3)
        self.oracle = hil_dut.ReferenceDut(self.init)
        self.epoch = 0
        self.inputs = {}
        self.last_controller = None
        self.last_controller_sequence = -1
        self.last_command = None
        self.transmitted = set()
        self.last_time_ms = -1
        self.watchdog = True
        self.recovery_count = 0

    def check(self, request, reply):
        # Import lazily to keep launcher modules free of an import cycle.
        try:
            from .hil_policy import common_check
        except ImportError:
            from hil_policy import common_check
        common_check(request, reply)
        sequence = request['sequence']
        if type(sequence) is not int or sequence != len(self.inputs):
            raise ValueError('Network audit outer sequence is not contiguous')
        self.inputs[sequence] = copy.deepcopy(request)
        net = reply.get('network')
        if not isinstance(net, dict) or not isinstance(net.get('events'), list) or not isinstance(net.get('controller_steps'), list):
            raise ValueError('Network reply has no participant evidence')
        now = net.get('time_ms')
        if type(now) is not int or now < self.last_time_ms or not sequence * 50 <= now <= (sequence + 1) * 50:
            raise ValueError('Network virtual time differs from CARLA step')
        brake = net.get('brake')
        if not isinstance(brake, dict) or type(brake.get('epoch')) is not int or not 0 <= brake['epoch'] <= 255:
            raise ValueError('Network Brake ECU evidence is absent')
        epoch = net.get('epoch')
        if type(epoch) is not int:
            raise ValueError('Network ADAS epoch is missing')
        if epoch not in (self.epoch, self.epoch + 1):
            raise ValueError('Network ECU epoch skipped or moved backward')
        if epoch != self.epoch:
            if not any(e.get('kind') == 'reset' and e.get('epoch') == epoch for e in net['events'] if isinstance(e, dict)):
                raise ValueError('Network ECU epoch changed without reset evidence')
            self.epoch = epoch; self.oracle = hil_dut.ReferenceDut(self.init)
            self.last_controller = None; self.last_controller_sequence = -1
        os_requests = [e for e in net['events'] if isinstance(e, dict)
                       and e.get('layer') == 'os' and e.get('kind') == 'ControlTask.control_request']
        if len(os_requests) != len(net['controller_steps']):
            raise ValueError('Native controller activation count differs from OS task requests')
        for entry, activation in zip(net['controller_steps'], os_requests):
            if not isinstance(entry, dict) or set(entry) != {'request', 'reply', 'epoch'} or entry['epoch'] != self.epoch:
                raise ValueError('Network controller execution identity mismatch')
            native = entry['request']; result = entry['reply']
            actual_seq = native.get('sequence')
            if type(actual_seq) is not int or not self.last_controller_sequence < actual_seq <= sequence:
                raise ValueError('Network controller activations duplicated or reordered')
            if activation.get('tick_ms') != actual_seq * 50 or activation.get('os_epoch') != entry['epoch']:
                raise ValueError('Native controller activation differs from OS virtual tick or epoch')
            original = self.inputs[actual_seq]
            packet = native.get('object_packet', {})
            object_seq = packet.get('sequence')
            if (type(object_seq) is not int or object_seq not in self.inputs
                    or native.get('ego') != original['ego']
                    or not _same(_packet(packet), _packet(self.inputs[object_seq]['object_packet']))):
                raise ValueError('Network controller input differs from generated CARLA source')
            # The native network C++ fork permits skipped OS activations. Only
            # that sequence gate is adjusted; all independent control/state and
            # delivery/fingerprint checks execute in ReferenceDut unchanged.
            self.oracle.sequence = actual_seq - 1
            expected = self.oracle.step(native)
            if not _same(result, expected):
                raise ValueError('Network native controller differs from independent reference')
            self.last_controller = result; self.last_controller_sequence = actual_seq
        override = False
        accepted = []
        routed = set()
        tx_path = [('Com','SendSignal'), ('Com','TriggerIPDUSend'), ('PduR','Transmit'),
                   ('CanIf','Transmit'), ('CanDriver','Write'), ('CanIf','TxConfirmation'),
                   ('PduR','TxConfirmation'), ('Com','TxConfirmation')]
        rx_path = [('CanIf','RxIndication'), ('PduR','RxIndication'), ('Com','RxIndication'), ('Com','ReceiveSignal')]
        for event in net['events']:
            if not isinstance(event, dict):
                raise ValueError('Network event must be an object')
            if event.get('kind') == 'control' and event.get('participant') == 'adas':
                override = event.get('heartbeat_override')
                if type(override) is not bool:
                    raise ValueError('Missing ADAS heartbeat override evidence')
            if event.get('layer') == 'bsw' and event.get('can_id') == 0x100 and event.get('kind') in ('tx','rx'):
                expected_path = tx_path if event['kind'] == 'tx' else rx_path
                trace = event.get('trace', [])
                if (not isinstance(trace, list) or any(not isinstance(x, dict) for x in trace)
                        or [(x.get('module'),x.get('event')) for x in trace] != expected_path):
                    raise ValueError('CAN command lacks the executed BSW routing path')
                data = event.get('data')
                if not isinstance(data, list) or len(data) != 8 or any(type(v) is not int or not 0 <= v <= 255 for v in data):
                    raise ValueError('BSW command bytes are invalid')
                routed.add((event['kind'], event.get('participant'), event.get('tick_ms'), bytes(data).hex()))
            if event.get('kind') not in ('can_tx', 'can_rx') or event.get('can_id') != 0x100:
                continue
            at = event.get('tick_ms')
            if type(at) is not int or not self.last_time_ms <= at <= now:
                raise ValueError('Network CAN event lies outside acknowledged interval')
            control, cmd_seq, cmd_epoch = decode_command(event.get('data_hex'))
            if event.get('epoch') != cmd_epoch or not 0 <= cmd_epoch <= self.epoch:
                raise ValueError('CAN command belongs to another reset epoch')
            data = event['data_hex']
            route = ('tx' if event['kind'] == 'can_tx' else 'rx', event.get('participant'), at, data)
            if route not in routed:
                raise ValueError('CAN command bypasses the recorded BSW route')
            if cmd_epoch != self.epoch and not any(e.get('kind') == 'reset' and e.get('epoch') == self.epoch
                    and type(e.get('tick_ms')) is int and at < e['tick_ms'] for e in net['events'] if isinstance(e, dict)):
                raise ValueError('Old-epoch CAN command was received after ADAS reset')
            if event['kind'] == 'can_tx' and event.get('participant') == 'adas':
                if self.last_controller is None or cmd_seq != self.last_controller['sequence'] % 256 or cmd_epoch != self.epoch:
                    raise ValueError('CAN command has no native controller activation')
                expected = copy.deepcopy(self.last_controller['control'])
                if override: expected.update(throttle=0., brake=1.)
                raw = struct.pack('<HHhBB', round(expected['throttle'] * 10000), round(expected['brake'] * 10000),
                                  round(expected['steer'] * 32767), cmd_seq, cmd_epoch).hex()
                if raw != data:
                    raise ValueError('CAN transmitted command differs from native control and declared override')
                self.transmitted.add(data)
            elif event['kind'] == 'can_rx' and event.get('participant') == 'brake':
                if data not in self.transmitted:
                    raise ValueError('Brake ECU received a command with no transmitted frame')
                accepted.append((at, data, control, cmd_seq, cmd_epoch))
        for received in accepted:
            if self.last_command and received[0] - self.last_command[0] >= self.watchdog_ms:
                self.watchdog = True; self.recovery_count = 0
            if self.last_command and received[3:] == self.last_command[3:]:
                raise ValueError('Brake accepted a duplicate command as fresh')
            self.last_command = received
            if self.watchdog:
                self.recovery_count += 1
                if self.recovery_count >= self.recovery_packets:
                    self.watchdog = False; self.recovery_count = 0
        if self.last_command is None:
            expected_control = {'throttle': 0., 'brake': 1., 'steer': 0.}
            if brake.get('command_hex') is not None or brake.get('command_sequence') is not None or brake.get('last_command_ms') is not None:
                raise ValueError('Initial Brake state claims an unreceived CAN command')
            allowed_reason = {'initial', 'watchdog'}
        else:
            at, data, decoded, cmd_seq, cmd_epoch = self.last_command
            if (brake.get('command_hex') != data or brake.get('command_sequence') != cmd_seq or brake['epoch'] != cmd_epoch
                    or brake.get('last_command_ms') != at):
                raise ValueError('Brake retained command differs from received CAN frame')
            if now - at >= self.watchdog_ms:
                self.watchdog = True; self.recovery_count = 0
            expected_control = copy.deepcopy(decoded)
            if self.watchdog:
                expected_control.update(throttle=0., brake=1.)
                allowed_reason = {'watchdog'} if now - at >= self.watchdog_ms else {'recovery'}
            else:
                allowed_reason = {'command'}
        if (brake.get('reason') not in allowed_reason or not _same(brake.get('control'), expected_control)
                or not _same(reply['control'], expected_control)):
            raise ValueError('Applied vehicle control differs from decoded CAN/Brake watchdog policy')
        if (type(reply.get('object_sequence')) is not int or not -1 <= reply['object_sequence'] <= sequence
                or not hil_dut.finite(reply.get('object_age_s')) or reply['object_age_s'] < 0):
            raise ValueError('Network received-object diagnostic is invalid')
        if not math.isclose(reply['object_age_s'], request['time_s'] - reply['object_sequence'] * .05, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError('Network received-object age differs from original packet timestamp')
        self.last_time_ms = now
