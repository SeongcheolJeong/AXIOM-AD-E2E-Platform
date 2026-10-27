"""Mocked process boundaries; these tests never signal an actual process."""
import importlib.util
import json
from pathlib import Path
import signal
from unittest.mock import Mock

import pytest


SPEC = importlib.util.spec_from_file_location(
    'stop_external_access_under_test',
    Path(__file__).resolve().parents[1] / 'stop_external_access.py',
)
stopper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stopper)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / 'workspace'
    base = root / '.runtime' / 'external-access-test'
    base.mkdir(parents=True)
    (root / '.runtime/external-access-current.json').write_text(
        json.dumps({'directory': str(base)})
    )
    monkeypatch.setattr(stopper, '__file__', str(root / 'scripts/stop_external_access.py'))
    identity = Mock()
    kill = Mock()
    monkeypatch.setattr(stopper, 'identity', identity)
    monkeypatch.setattr(stopper.os, 'kill', kill)
    return root, base, identity, kill


def test_reused_pid_with_different_start_time_is_never_signalled(workspace, capsys):
    _, base, identity, kill = workspace
    command = '/python scripts/external_access_gateway.py --port 8787'
    (base / 'processes.json').write_text(json.dumps({
        'gateway': {'pid': 20202, 'identity': f'Mon Sep 14 10:00:00 2026 {command}'},
    }))
    identity.return_value = f'Mon Sep 14 10:01:00 2026 {command}'

    stopper.main()

    identity.assert_called_once_with(20202)
    kill.assert_not_called()
    assert '프로세스가 달라' in capsys.readouterr().out


def test_only_matching_gateway_and_tunnel_receive_sigterm(workspace):
    _, base, identity, kill = workspace
    records = {
        'gateway': {'pid': 20202, 'identity': 'start gateway command'},
        'tunnel': {'pid': 20203, 'identity': 'start tunnel command'},
        'backend': {'pid': 20204, 'identity': 'start backend command'},
        'carla': {'pid': 20205, 'identity': 'start CarlaUE4 command'},
    }
    (base / 'processes.json').write_text(json.dumps(records))
    identity.side_effect = lambda pid: next(
        record['identity'] for record in records.values() if record['pid'] == pid
    )

    stopper.main()

    assert [call.args for call in identity.call_args_list] == [(20202,), (20203,)]
    assert [call.args for call in kill.call_args_list] == [
        (20202, signal.SIGTERM), (20203, signal.SIGTERM),
    ]


@pytest.mark.parametrize('invalid_pid', [True, 1, 0, -2, '20202'])
def test_invalid_pid_cannot_signal_a_process_or_group(workspace, invalid_pid):
    _, base, identity, kill = workspace
    (base / 'processes.json').write_text(json.dumps({
        'gateway': {'pid': invalid_pid, 'identity': 'start gateway command'},
    }))

    with pytest.raises(SystemExit, match='Invalid process identifier'):
        stopper.main()

    identity.assert_not_called()
    kill.assert_not_called()


def test_session_pointer_outside_workspace_runtime_is_rejected(workspace):
    root, _, identity, kill = workspace
    (root / '.runtime/external-access-current.json').write_text(
        json.dumps({'directory': str(root / 'outside-session')})
    )

    with pytest.raises(SystemExit, match='Unexpected session directory'):
        stopper.main()

    identity.assert_not_called()
    kill.assert_not_called()


def test_supervisor_is_stopped_first_then_only_pinned_external_children(workspace):
    _, base, identity, kill = workspace
    records = {name: {'pid': 21000 + index, 'identity': f'start {name} command'}
        for index, name in enumerate(('supervisor', 'gateway', 'tunnel', 'awake_guard', 'backend'))}
    (base / 'processes.json').write_text(json.dumps(records))
    def current(pid):
        if pid == 21000 and kill.called:
            return ''  # Supervisor exited after the first SIGTERM.
        return next(record['identity'] for record in records.values() if record['pid'] == pid)
    identity.side_effect = current

    stopper.main()

    assert [call.args for call in kill.call_args_list] == [
        (21000, signal.SIGTERM), (21001, signal.SIGTERM),
        (21002, signal.SIGTERM), (21003, signal.SIGTERM)]
    assert (21004,) not in [call.args for call in identity.call_args_list]


def test_invalid_later_child_prevents_signalling_an_earlier_valid_supervisor(workspace):
    _, base, identity, kill = workspace
    (base / 'processes.json').write_text(json.dumps({
        'supervisor': {'pid': 21000, 'identity': 'start supervisor'},
        'awake_guard': {'pid': True, 'identity': 'start awake guard'},
    }))
    with pytest.raises(SystemExit, match='Invalid process identifier'):
        stopper.main()
    identity.assert_not_called()
    kill.assert_not_called()
