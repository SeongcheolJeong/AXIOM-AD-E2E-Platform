"""Network launcher/audit contract tests. Native engine acceptance is separate."""
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys

import pytest
from axiom_carla import hil_dut, hil_network, hil_policy, hil_native


def initialization():
    return dict(op='init', schema_version='hil-dut-init/1',
        route=[dict(x=float(x), y=0., z=0.) for x in (0, 10, 20, 40, 80)],
        target_speed_mps=3., physics=dict(wheelbase_m=2.7, max_steer_angle_rad=.6))


def runtime(manifest='a' * 64):
    value = dict(schema_version='vecu-network-runtime/1', kind='network_reference',
        profile_id='actual_silkit_autoas_trampoline', manifest_sha256=manifest,
        network={'can': {'watchdog_ms': 100, 'recovery_packets': 3}}, faults=[], side='baseline',
        dut_version='network-ecu/1', protocol='hil-dut/1')
    value['runtime_sha256'] = hashlib.sha256(hil_network.canonical(value)).hexdigest()
    return value


def install(root):
    (root / 'network_runtime.py').write_text('print("registered runner")\n')
    (root / 'controller').write_bytes(b'operator registered binary')
    manifest = dict(schema_version='vecu-network-install/1', binaries={'controller': 'controller'},
        files={name: hashlib.sha256((root / name).read_bytes()).hexdigest()
               for name in ('network_runtime.py', 'controller')})
    raw = hil_network.canonical(manifest)
    (root / 'network-install.json').write_bytes(raw)
    return runtime(hashlib.sha256(raw).hexdigest())


def test_pinned_network_command_and_probe_hash_every_file(tmp_path):
    value = install(tmp_path)
    assert hil_policy.validate_runtime(value) == value
    assert hil_network.command(value, tmp_path) == [sys.executable, str(tmp_path / 'network_runtime.py')]
    assert hil_network.validate_install({'manifest_sha256': value['manifest_sha256']}, tmp_path)['binaries']
    (tmp_path / 'controller').write_bytes(b'changed')
    with pytest.raises(ValueError, match='file changed'):
        hil_network.command(value, tmp_path)


def test_install_manifest_paths_and_symlinks_are_rejected(tmp_path):
    value = install(tmp_path)
    for relative in ('../escape', '/absolute', 'nested/../controller', './controller', 'back\\slash'):
        with pytest.raises(ValueError, match='normalized and relative'):
            hil_network._file(tmp_path, relative)
    (tmp_path / 'controller').unlink()
    (tmp_path / 'controller').symlink_to(tmp_path / 'network_runtime.py')
    with pytest.raises(ValueError, match='symlink'):
        hil_network.command(value, tmp_path)


def test_network_identity_cannot_mutate_or_weakly_select_compiled_runtime():
    value = runtime()
    value['network']['can']['watchdog_ms'] = 200
    with pytest.raises(ValueError, match='hash mismatch'):
        hil_policy.validate_runtime(value)
    value = runtime(); value['kind'] = 'compiled_reference'
    with pytest.raises(ValueError, match='compiled ECU'):
        hil_policy.validate_runtime(value)
    value = runtime(); value['binary_path'] = '/unregistered'
    with pytest.raises(ValueError, match='network ECU'):
        hil_policy.validate_runtime(value)


TX = [('Com','SendSignal'), ('Com','TriggerIPDUSend'), ('PduR','Transmit'), ('CanIf','Transmit'),
      ('CanDriver','Write'), ('CanIf','TxConfirmation'), ('PduR','TxConfirmation'), ('Com','TxConfirmation')]
RX = [('CanIf','RxIndication'), ('PduR','RxIndication'), ('Com','RxIndication'), ('Com','ReceiveSignal')]


def bsw(kind, who, tick, data):
    return dict(layer='bsw', kind=kind, participant=who, tick_ms=tick, epoch=0, can_id=256,
        data=list(bytes.fromhex(data)), trace=[dict(module=m, event=e) for m,e in (TX if kind == 'tx' else RX)])


def can(kind, who, tick, data):
    return dict(layer='can', kind=kind, participant=who, tick_ms=tick, epoch=0, can_id=256, data_hex=data)


def rows(count=15, skipped=()):
    """Authored evidence fixture for audit rejection tests, not native validation."""
    result = []; oracle = hil_dut.ReferenceDut(initialization()); pending = None; command = None
    bad = True; fresh = 0
    for n in range(count):
        req = dict(op='step', schema_version='hil-dut-step/1', sequence=n, time_s=n * .05,
            ego=dict(id='ego', x=n * .04, y=.1, z=0., heading_rad=.01, speed_mps=1.5),
            object_packet=dict(sequence=n, time_s=n * .05, objects=[]), object_delivery_present=True)
        ev = []; steps = []; native_reply = {}
        if pending:
            received = pending; pending = None
            if command and received[0] - command[0] >= 100: bad = True; fresh = 0
            command = received
            ev += [bsw('rx','brake',received[0],received[1]), can('can_rx','brake',received[0],received[1])]
            if bad:
                fresh += 1
                if fresh >= 3: bad = False
        if command and n * 50 - command[0] >= 100: bad = True; fresh = 0
        if n and n not in skipped:
            native = copy.deepcopy(req)
            native['object_packet'] = dict(sequence=n-1,time_s=(n-1)*.05,objects=[])
            oracle.sequence = n-1
            native_reply = oracle.step(native)
            steps = [dict(request=native,reply=native_reply,epoch=0)]
            c = native_reply['control']
            data = struct.pack('<HHhBB',round(c['throttle']*10000),round(c['brake']*10000),round(c['steer']*32767),n%256,0).hex()
            pending = (n*50+1,data)
            ev += [dict(layer='os',kind='ControlTask.control_request',tick_ms=n*50,os_epoch=0,epoch=0),
                   dict(layer='ecu',kind='control',participant='adas',heartbeat_override=False,tick_ms=n*50,epoch=0),
                   bsw('tx','adas',n*50,data),can('can_tx','adas',n*50,data)]
        if command:
            decoded,seq,epoch = hil_network.decode_command(command[1])
            reason = 'watchdog' if n*50-command[0]>=100 else 'recovery' if bad else 'command'
            applied = dict(decoded)
            if reason != 'command': applied.update(throttle=0.,brake=1.)
            brake = dict(control=applied,reason=reason,command_hex=command[1],command_sequence=seq,last_command_ms=command[0],epoch=epoch)
        else:
            applied = dict(throttle=0.,brake=1.,steer=0.)
            brake = dict(control=applied,reason='initial',command_hex=None,command_sequence=None,last_command_ms=None,epoch=0)
        reply = dict(native_reply,schema_version='hil-dut-reply/1',op='step',sequence=n,
            object_sequence=n-1,object_age_s=.05,state='tracking' if brake['reason']=='command' else 'degraded',
            control=applied,plan={},network=dict(events=ev,controller_steps=steps,brake=brake,time_ms=n*50,epoch=0))
        result.append((req,reply))
    return result


def audit(items):
    checker = hil_network.NetworkAudit(initialization())
    for request, reply in items: checker.check(request, reply)


def test_audit_independent_control_can_quantization_watchdog_and_recovery():
    samples = rows(skipped=range(6,10))
    audit(samples)
    assert samples[8][1]['network']['brake']['reason'] == 'watchdog'
    assert samples[11][1]['network']['brake']['reason'] == 'recovery'
    assert samples[13][1]['network']['brake']['reason'] == 'command'


@pytest.mark.parametrize('change,match', [
    (lambda r:r['network']['controller_steps'][0]['reply']['control'].update(throttle=.9), 'independent reference'),
    (lambda r:r['network']['controller_steps'][0]['request']['ego'].update(x=99.), 'CARLA source'),
    (lambda r:r['network']['events'].pop(0), 'OS task requests'),
    (lambda r:r['network']['events'][2]['trace'].pop(), 'BSW routing'),
    (lambda r:r['network']['events'][3].update(data_hex='0100000000000100'), 'bypasses|transmitted command'),
    (lambda r:r.update(control=dict(throttle=1.,brake=0.,steer=0.)), 'Applied vehicle control'),
    (lambda r:r.update(object_age_s=.4), 'packet timestamp'),
])
def test_audit_rejects_bypassed_or_corrupt_evidence(change,match):
    samples = rows(2)
    change(samples[1][1])
    with pytest.raises(ValueError,match=match): audit(samples)


def test_source_pin_includes_network_auditor():
    assert hil_native.source_hashes()['hil_network_sha256'] == hashlib.sha256(Path(hil_network.__file__).read_bytes()).hexdigest()
