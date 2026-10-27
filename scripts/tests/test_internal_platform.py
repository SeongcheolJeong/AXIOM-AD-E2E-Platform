"""Isolated launcher checks. No existing workspace or simulator is started."""
from contextlib import closing
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import time

import pytest

PATH = Path(__file__).resolve().parents[1] / 'internal_platform.py'
SPEC = importlib.util.spec_from_file_location('axiom_internal_launcher', PATH)
launcher = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(launcher)


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def built_ui(root):
    ui = root / 'ui'
    ui.mkdir()
    (ui / 'index.html').write_text('<!doctype html><title>Isolated launcher acceptance</title>')
    return ui


def database(root):
    from ade2e.database import Database, Settings
    db = Database(Settings(data_dir=root, api_token='x' * 32, seed_examples=False))
    db.initialize()
    db.engine.dispose()
    return root / 'platform.db'


def request(port, path, token=None, **headers):
    if token:
        headers['Authorization'] = 'Bearer ' + token
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
    try:
        connection.request('GET', path, headers=headers)
        response = connection.getresponse()
        return response.status, response.read(), dict(response.getheaders())
    finally:
        connection.close()


def test_readonly_preflight_new_private_paths_and_older_workspace(tmp_path):
    ui = built_ui(tmp_path)
    data, state, port = tmp_path / 'data', tmp_path / 'access', free_port()
    checked = launcher.preflight(data, state, port, ui)
    assert checked['services_started'] is False and checked['data']['state'] == 'new'
    assert not data.exists() and not state.exists()
    database(data)
    current = launcher.preflight(data, state, port, ui)
    assert current['data']['state'] == 'existing_current_schema'
    with closing(sqlite3.connect(data / 'platform.db')) as connection:
        connection.execute('UPDATE schema_versions SET version=16')
        connection.commit()
    before = (data / 'platform.db').read_bytes()
    with pytest.raises(ValueError, match='not migrated in place'):
        launcher.preflight(data, state, port, ui)
    assert (data / 'platform.db').read_bytes() == before
    state.mkdir(mode=0o755)
    with pytest.raises(ValueError, match='private mode0700'):
        launcher.preflight(tmp_path / 'new', state, port, ui)
    state.chmod(0o700)
    unsafe = state / 'access.json'
    unsafe.write_bytes(b'preserved private-state candidate')
    unsafe.chmod(0o644)
    with pytest.raises(ValueError, match='owner-only regular'):
        launcher.private_json(unsafe, {'do_not_overwrite': True})
    assert unsafe.read_bytes() == b'preserved private-state candidate'


def test_configured_engines_are_explicit_and_paths_cannot_override_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('ADE2E_DATABASE_URL', 'sqlite:////should/not/open.db')
    monkeypatch.setenv('ADE2E_API_TOKEN', 'inherited-do-not-use')
    monkeypatch.setenv('PYTHONPATH', '/unreviewed/python/source')
    monkeypatch.setenv('AXIOM_ESMINI_COMMAND', '["/operator/native-adapter"]')
    monkeypatch.setenv('AXIOM_CAMERA_E2E_TOKEN', 'operator-secret-not-printed')
    monkeypatch.setenv('AXIOM_NEURAL_SSH_TARGET', 'operator-gpu')
    monkeypatch.setenv('AXIOM_OTHER_UNRELATED', 'not-forwarded')
    arguments = (tmp_path / 'data', tmp_path / 'access', tmp_path / 'ui', 8020, 'instance', 'private-bootstrap-token')
    implicit = launcher.child_environment(*arguments)
    assert 'ADE2E_DATABASE_URL' not in implicit and 'AXIOM_ESMINI_COMMAND' not in implicit
    assert 'AXIOM_NEURAL_SSH_TARGET' not in implicit
    assert implicit['ADE2E_API_TOKEN'] == 'private-bootstrap-token' and implicit['ADE2E_TEAM_MODE'] == '1'
    assert '/unreviewed/python/source' not in implicit['PYTHONPATH']
    explicit = launcher.child_environment(*arguments, enable_configured_engines=True)
    assert explicit['AXIOM_ESMINI_COMMAND'] == '["/operator/native-adapter"]'
    assert explicit['AXIOM_CAMERA_E2E_TOKEN'] == 'operator-secret-not-printed'
    assert explicit['AXIOM_NEURAL_SSH_TARGET'] == 'operator-gpu'
    assert 'AXIOM_OTHER_UNRELATED' not in explicit and 'ADE2E_DATABASE_URL' not in explicit
    assert str(launcher.ROOT / 'integrations/camera-e2e/src') in explicit['PYTHONPATH']
    assert str(launcher.ROOT / 'integrations/neural/src') in explicit['PYTHONPATH']


def test_real_restart_probe_allows_closed_connections_but_refuses_live_owner(tmp_path):
    ui = built_ui(tmp_path)
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    listener.listen(1)
    client = socket.socket()
    accepted = None
    try:
        with pytest.raises(ValueError, match='port is occupied'):
            launcher.preflight(tmp_path / 'data', tmp_path / 'access', port, ui)
        client.connect(('127.0.0.1', port))
        accepted, _ = listener.accept()
        accepted.sendall(b'owned connection')
        assert client.recv(64) == b'owned connection'
        # The server actively closes first, leaving a closed connection on the
        # chosen server port. Reusing it must not require killing another owner.
        accepted.shutdown(socket.SHUT_WR)
        assert client.recv(64) == b''
        accepted.close()
        accepted = None
        listener.close()
        client.close()
        report = launcher.preflight(tmp_path / 'data', tmp_path / 'access', port, ui)
        assert report['services_started'] is False
        assert not (tmp_path / 'data').exists() and not (tmp_path / 'access').exists()
    finally:
        if accepted is not None:
            accepted.close()
        client.close()
        listener.close()


def test_verified_restore_migrates_copy_only(tmp_path):
    from ade2e.database import Database, Settings
    from ade2e.workspace_backup import backup, restore
    ui = built_ui(tmp_path)
    source, bundle, destination = tmp_path / 'source', tmp_path / 'backup', tmp_path / 'restored'
    source_db = database(source)
    with closing(sqlite3.connect(source_db)) as connection:
        connection.execute('UPDATE schema_versions SET version=16')
        connection.commit()
    backup(source, bundle, offline=True)
    original = source_db.read_bytes()
    bundle_sha = hashlib.sha256((bundle / 'payload/platform.db').read_bytes()).hexdigest()
    report = launcher.preflight(destination, tmp_path / 'access', free_port(), ui, restore_backup=bundle)
    assert report['data']['original_schema'] == 16 and not destination.exists()
    restore(bundle, destination)
    restored = Database(Settings(data_dir=destination, seed_examples=False))
    restored.initialize()
    restored.engine.dispose()
    with closing(sqlite3.connect(destination / 'platform.db')) as connection:
        from ade2e.database import SCHEMA_VERSION
        assert connection.execute('SELECT version FROM schema_versions').fetchall() == [(SCHEMA_VERSION,)]
    assert source_db.read_bytes() == original
    assert hashlib.sha256((bundle / 'payload/platform.db').read_bytes()).hexdigest() == bundle_sha
    with pytest.raises(ValueError, match='NEW workspace destination'):
        launcher.preflight(destination, tmp_path / 'access', free_port(), ui, restore_backup=bundle)


def test_real_owned_api_worker_static_ui_private_auth_and_stop(tmp_path):
    ui, port = built_ui(tmp_path), free_port()
    data, state = tmp_path / 'data', tmp_path / 'access'
    process = subprocess.Popen([sys.executable, '-B', str(PATH), '--workspace', str(data), '--state', str(state),
                                '--ui', str(ui), '--port', str(port)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    token = None
    try:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline and process.poll() is None:
            if (state / 'access.json').is_file():
                break
            time.sleep(.1)
        assert (state / 'access.json').is_file(), 'Launcher did not become ready'
        access = json.loads((state / 'access.json').read_bytes())
        token = Path(access['token_file']).read_text().strip()
        assert access['services_started'] is True and access['configured_engines_enabled'] is False
        assert access['worker_log'] != access['worker_bootstrap_log']
        for path in (state / 'access.json', Path(access['token_file']),
                     Path(access['worker_log']), Path(access['worker_bootstrap_log'])):
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(state.stat().st_mode) == 0o700
        status, body, headers = request(port, '/')
        assert status == 200 and b'Isolated launcher acceptance' in body
        # A subsequent development build must not swap the running API's UI.
        original_ui = body
        (ui / 'index.html').write_text('<!doctype html><title>Later unaccepted build</title>')
        assert request(port, '/')[1] == original_ui
        assert Path(access['built_ui_snapshot']).is_relative_to(state)
        assert len(access['built_ui_manifest_sha256']) == 64
        assert "frame-ancestors 'none'" in headers['content-security-policy']
        assert request(port, '/api/v1/projects')[0] == 401
        assert request(port, '/api/v1/projects', token)[0] == 200
        status, body, _ = request(port, '/api/v1/internal-platform/status', token)
        assert status == 200 and json.loads(body)['instance_id'] == access['instance_id']
        assert len(json.loads(body)['workers']) == 1
        assert request(port, '/api/v1/projects', token, Origin='https://unrelated.example')[0] == 403
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=5)
            pytest.fail('Launcher failed to drain its owned idle children')
    assert process.returncode == 0, stderr
    assert token and token not in stdout and token not in stderr
    result = json.loads((state / (access['instance_id'] + '.result.json')).read_bytes())
    assert result['status'] == 'stopped' and result['forced_stop_pids'] == []
    assert result['children'][0]['exit_code'] in (0, -signal.SIGTERM)
    assert result['children'][1]['exit_code'] == 0
    with closing(sqlite3.connect(data / 'platform.db')) as connection:
        assert connection.execute('SELECT state FROM execution_workers').fetchall() == [('stopped',)]
    for child in result['children']:
        with pytest.raises(ProcessLookupError):
            os.kill(child['pid'], 0)
    assert launcher.preflight(data, state, port, ui)['data']['state'] == 'existing_current_schema'


@pytest.mark.parametrize('failure_target,ready', [
    ('ade2e.experiment_defaults.default_experiment_registry', False),
    ('ade2e.worker.run_once', True),
])
def test_worker_dependency_failure_exits_once_with_private_diagnostic(tmp_path, failure_target, ready):
    data, state = tmp_path / 'data', tmp_path / 'access'
    state.mkdir(mode=0o700)
    instance = 'missing-dependency'
    environment = launcher.child_environment(data, state, tmp_path / 'ui', free_port(), instance, 'x' * 32)
    program = f'''
from unittest.mock import patch
import internal_platform
with patch({failure_target!r}, side_effect=ModuleNotFoundError("No module named 'axiom_engineering'")) as failure:
    result = internal_platform.worker_main()
    assert failure.call_count == 1
raise SystemExit(result)
'''
    result = subprocess.run([sys.executable, '-B', '-c', program], cwd=launcher.ROOT, env=environment,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 1, result.stderr
    assert not result.stdout and not result.stderr
    diagnostic = state / (instance + '.worker.log')
    assert stat.S_IMODE(diagnostic.stat().st_mode) == 0o600
    message = diagnostic.read_text()
    assert message.count("No module named 'axiom_engineering'") == 1
    assert 'Traceback' not in message
    assert (state / (instance + '.worker-ready.json')).exists() is ready
    with closing(sqlite3.connect(data / 'platform.db')) as connection:
        workers = connection.execute('SELECT state FROM execution_workers').fetchall()
        assert workers == ([('stopped',)] if ready else [])
        assert connection.execute('SELECT COUNT(*) FROM execution_jobs').fetchone() == (0,)


def test_ui_snapshot_rejects_drift_and_symlinks_before_serving(tmp_path):
    ui = built_ui(tmp_path)
    state = tmp_path / 'private'
    state.mkdir(mode=0o700)
    expected = hashlib.sha256((ui/'index.html').read_bytes()).hexdigest()
    (ui/'index.html').write_text('Changed after preflight')
    with pytest.raises(ValueError,match='changed after preflight'):
        launcher.freeze_ui(ui,state,'drift',expected)
    assert not (state/'ui-drift').exists()
    expected = hashlib.sha256((ui/'index.html').read_bytes()).hexdigest()
    (ui/'outside.txt').symlink_to(Path(__file__))
    with pytest.raises(ValueError,match='Symlinks'):
        launcher.freeze_ui(ui,state,'symlink',expected)
    assert not (state/'ui-symlink').exists()
