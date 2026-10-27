"""Offline reader checks; these tests do not launch CARLA or a debugger."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

path = Path(__file__).resolve().parents[1] / 'analyze_native_stop_observer.py'
spec = importlib.util.spec_from_file_location('native_stop_reader', path)
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


def write_stream(path, change=None):
    sample = {'kind': 'vehicle_sample', 'status': 'observed', 'actor_id': 24, 'world_frame': 100,
        'sticky_forward_active': [False, True, False, False], 'sticky_side_active': [False] * 4,
        'suspension_limit_active': [False] * 4, 'rigid_sleeping': False,
        'wheel_speeds_native': [1.] * 4, 'corrected_wheel_speeds_native': [1.] * 4,
        'low_forward_speed_timers_s': [0.] * 4}
    if change:
        change(sample)
    head = '0' * 64
    lines = []
    for i, event in enumerate([{'kind': 'observer_loaded'}, sample]):
        event.update(sequence=i, previous_sha256=head)
        head = reader.sha(reader.canonical(event))
        lines.append(json.dumps({'event': event, 'sha256': head}))
    path.write_text('\n'.join(lines) + '\n')


def test_exact_actor_frame_flags_are_preserved(tmp_path):
    path = tmp_path / 'trace.jsonl'
    write_stream(path)
    _, rows = reader.read_stream(path)
    assert rows[(100, 24)]['sticky_forward_active'] == [False, True, False, False]
    assert rows[(100, 24)]['rigid_sleeping'] is False


@pytest.mark.parametrize('change', [
    lambda s: s.pop('rigid_sleeping'), lambda s: s.update(rigid_sleeping=0),
    lambda s: s.update(sticky_forward_active=['false'] * 4),
    lambda s: s.update(sticky_forward_active=[False]),
    lambda s: s.update(actor_id=True), lambda s: s.update(world_frame=100.),
    lambda s: s.update(kind='observer_error'), lambda s: s.update(kind='sample_limit'),
])
def test_missing_or_malformed_native_data_cannot_be_validated(tmp_path, change):
    path = tmp_path / 'trace.jsonl'
    write_stream(path, change)
    with pytest.raises(ValueError):
        reader.read_stream(path)


def test_changed_event_bytes_are_rejected(tmp_path):
    path = tmp_path / 'trace.jsonl'
    write_stream(path)
    path.write_text(path.read_text().replace('"actor_id": 24', '"actor_id": 25'))
    with pytest.raises(ValueError, match='chain'):
        reader.read_stream(path)


def clock_stream(path, changes=None):
    updates = [{'kind': 'vehicle_update', 'world_frame': 100, 'delta_seconds': .001} for _ in range(50)]
    if changes:
        changes(updates)
    head, lines = '0' * 64, []
    for index, event in enumerate([{'kind': 'clock_observer_loaded'}, *updates]):
        event.update(sequence=index, previous_sha256=head)
        head = reader.sha(reader.canonical(event))
        lines.append(json.dumps({'event': event, 'sha256': head}))
    path.write_text('\n'.join(lines) + '\n')


def test_actual_clock_counts_are_independent_of_profile_echo(tmp_path):
    path = tmp_path / 'clock.jsonl'
    clock_stream(path)
    observed = reader.read_clock(path)
    assert observed['updates'] == 50
    assert observed['updates_per_frame_histogram'] == {50: 1}
    assert observed['per_frame']['100']['sum_delta_seconds'] == pytest.approx(.05)


@pytest.mark.parametrize('change', [
    lambda u: u[0].update(delta_seconds=0),
    lambda u: u[0].update(delta_seconds=True),
    lambda u: u[0].update(world_frame=True),
    lambda u: u[-1].update(world_frame=99),
    lambda u: u[0].update(kind='observer_error'),
    lambda u: u.clear(),
])
def test_invalid_clocks_are_not_interpolated_or_filled(tmp_path, change):
    path = tmp_path / 'clock.jsonl'
    clock_stream(path, change)
    with pytest.raises(ValueError):
        reader.read_clock(path)


def test_readonly_scope_has_no_inferior_call_or_attach_instruction():
    source = (path.parents[0] / 'native_stop_observer_gdb.py').read_text()
    assert "set may-call-functions off" in source
    assert "gdb.execute('attach" not in source
    assert 'write_memory' not in source
    launch = (path.parents[0] / 'launch_native_stop_observer.py').read_text()
    assert '$axiom_stop_ready' in launch and '$axiom_clock_ready' in launch


@pytest.mark.parametrize('change', ['production_port', 'foreign_parent', 'wrong_userdir', 'wrong_binary'])
def test_owner_guard_rejects_wrong_process(tmp_path, monkeypatch, change):
    import hashlib
    module_path = path.parent / 'native_stop_observer_gdb.py'
    spec = importlib.util.spec_from_file_location('owner_guard', module_path)
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    root = tmp_path / 'isolated'
    root.mkdir()
    proc = tmp_path / 'proc' / '123'
    proc.mkdir(parents=True)
    args = [b'carla', b'-carla-rpc-port=2300', ('-userdir=' + str(root / 'home')).encode()]
    if change == 'production_port':
        args[1] = b'-carla-rpc-port=2000'
    if change == 'wrong_userdir':
        args[2] = b'-userdir=/another/path'
    (proc / 'cmdline').write_bytes(b'\0'.join(args) + b'\0')
    fields = ['S', '999' if change == 'foreign_parent' else '12', *['0'] * 18]
    (proc / 'stat').write_text('123 (private carla) ' + ' '.join(fields))
    (proc / 'exe').write_bytes(b'authored binary')
    monkeypatch.setattr(owner, 'BINARY_SHA256', '0' * 64 if change == 'wrong_binary' else hashlib.sha256(b'authored binary').hexdigest())
    real_path = Path
    monkeypatch.setattr(owner, 'Path', lambda value: tmp_path / 'proc' if value == '/proc' else real_path(value))
    with pytest.raises(ValueError):
        owner.validate_owner(123, 12, root)


@pytest.mark.parametrize('restored', [True, False, None])
def test_unknown_world_restoration_never_publishes_a_quality_pass(restored, monkeypatch):
    import sys
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location('native_trial', path.parent / 'run_native_stop_observer_trial.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = {'status': 'completed', 'quality_verdict': 'passed', 'world_restored': restored}
    module.qualify_terminal_report(report)
    if restored is True:
        assert report['status'] == 'completed' and report['quality_verdict'] == 'passed'
    else:
        assert report['status'] == 'failed' and report['quality_verdict'] == 'not_evaluated'
