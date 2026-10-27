"""Local pilot packaging/security contracts; no CARLA or external service calls."""
from pathlib import Path
import hashlib
import json
import os
import socket
import stat

import pytest
from fastapi.testclient import TestClient

from ade2e import local_pilot as pilot
from ade2e.database import Database, Settings, SCHEMA_VERSION


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "bundle"
    (root / "ui/assets").mkdir(parents=True)
    (root / "ui/index.html").write_text('<!doctype html><html><body>Authored static UI fixture</body></html>')
    (root / "ui/assets/main.js").write_text('console.log("static fixture")')
    files = {p.relative_to(root).as_posix(): {"sha256": pilot.file_hash(p), "size_bytes": p.stat().st_size}
             for p in root.rglob('*') if p.is_file()}
    (root / "bundle.json").write_bytes(pilot.canonical({"schema_version": pilot.VERSION, "files": files,
        "dependencies": {}, "label": "authored test fixture"}))
    return root


def test_manifest_verification_and_static_content_identity(bundle):
    assert len(pilot.verify_bundle(bundle)["files"]) == 2


@pytest.mark.parametrize("change", ["bytes", "missing", "unexpected", "symlink", "traversal"])
def test_modified_or_unsafe_bundle_is_refused(bundle, tmp_path, change):
    if change == "bytes": (bundle / "ui/assets/main.js").write_text("mutated")
    if change == "missing": (bundle / "ui/assets/main.js").unlink()
    if change == "unexpected": (bundle / ".env").write_text("not for packaging")
    if change == "symlink":
        (bundle / "ui/assets/main.js").unlink()
        (bundle / "ui/assets/main.js").symlink_to(tmp_path / "outside.js")
    if change == "traversal":
        value = json.loads((bundle / "bundle.json").read_bytes())
        value["files"]["../outside"] = {"sha256": "0" * 64, "size_bytes": 0}
        (bundle / "bundle.json").write_bytes(pilot.canonical(value))
    with pytest.raises(ValueError): pilot.verify_bundle(bundle)


def test_new_workspace_check_does_not_create_data_or_credentials(bundle, tmp_path):
    data, state = tmp_path / 'data', tmp_path / 'access'
    result = pilot.preflight(bundle, data, state, 59631)
    assert result['status'] == 'ready' and result['auth_required'] is True
    assert not result['node_required_at_runtime'] and not result['remote_engine_enabled']
    assert not data.exists() and not state.exists()


def test_existing_offline_database_check_preserves_bytes(bundle, tmp_path):
    data = tmp_path / "data"
    db = Database(Settings(data_dir=data, seed_examples=False)); db.initialize(); db.engine.dispose()
    original = {p.name: pilot.file_hash(p) for p in data.glob('platform.db*')}
    result = pilot.offline_data_check(data)
    assert result['state'] == f'existing_schema{SCHEMA_VERSION}'
    assert result['migration_on_start'] is False
    assert {p.name: pilot.file_hash(p) for p in data.glob('platform.db*')} == original


@pytest.mark.parametrize('port', [0, 80, 65536, True])
def test_invalid_port(bundle, tmp_path, port):
    with pytest.raises(ValueError, match='port'):
        pilot.preflight(bundle, tmp_path/'data', tmp_path/'state', port)


def test_occupied_port_is_not_reclaimed(bundle, tmp_path):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); sock.listen()
        with pytest.raises(ValueError, match='occupied'):
            pilot.preflight(bundle, tmp_path/'data', tmp_path/'state', sock.getsockname()[1])
        assert sock.fileno() >= 0


def test_application_data_and_secrets_cannot_overlap(bundle, tmp_path):
    with pytest.raises(ValueError, match='separate'):
        pilot.preflight(bundle, bundle/'data', tmp_path/'access', 59631)
    with pytest.raises(ValueError, match='separate'):
        pilot.preflight(bundle, tmp_path/'data', tmp_path/'data/access', 59631)


def test_token_is_private_reused_and_never_returned_in_preflight(bundle, tmp_path):
    state = tmp_path / 'access'
    path, token = pilot.token_file(state)
    assert pilot.token_file(state) == (path, token)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    result = pilot.preflight(bundle, tmp_path/'data', state, 59631)
    assert token not in json.dumps(result)


@pytest.mark.parametrize('change', ['shared', 'symlink', 'invalid', 'oversized', 'fifo'])
def test_insecure_token_is_not_silently_repaired(tmp_path, change):
    state = tmp_path/'access'; path, _ = pilot.token_file(state)
    if change == 'shared': path.chmod(0o644)
    elif change == 'symlink':
        other=tmp_path/'elsewhere';other.write_text('sensitive');path.unlink();path.symlink_to(other)
    elif change == 'oversized': path.write_text('a'*43+'\n'+' '*200)
    elif change == 'fifo': path.unlink();os.mkfifo(path,0o600)
    else: path.write_text('short')
    with pytest.raises((ValueError, OSError)): pilot.token_file(state)


def test_inherited_remote_config_and_pythonpath_are_not_forwarded(bundle, tmp_path, monkeypatch):
    monkeypatch.setenv('ADE2E_CARLA_WORKER_URL','https://must-not-connect.invalid')
    monkeypatch.setenv('ADE2E_DATABASE_URL','postgres://must-not-connect.invalid')
    monkeypatch.setenv('PYTHONPATH','/another/project')
    env=pilot.child_environment(bundle,tmp_path/'data',tmp_path/'state',5273,'instance','x'*43,tmp_path/'ready')
    assert 'ADE2E_CARLA_WORKER_URL' not in env and 'ADE2E_DATABASE_URL' not in env
    assert '/another/project' not in env['PYTHONPATH']
    assert env['ADE2E_SEED_EXAMPLES']=='0' and env['AXIOM_APP_FACTORY_ONLY']=='1'


def test_explicit_registries_do_not_discover_plugins(monkeypatch):
    from ade2e import adapters, inference_runtime, evaluator_runtime, stack_runtime
    for module in (adapters,inference_runtime,evaluator_runtime,stack_runtime):
        monkeypatch.setattr(module,'entry_points',lambda **_: pytest.fail('No plugin discovery permitted'))
    regs=pilot.explicit_registries()
    assert regs['registry'].get('carla').metadata().status=='not_configured'
    assert regs['runtime_registry'].get('onnx-cpu') is not None


@pytest.fixture
def pilot_client(bundle, tmp_path, monkeypatch):
    for key,value in {'AXIOM_PILOT_BUNDLE':str(bundle),'ADE2E_DATA_DIR':str(tmp_path/'data'),
        'ADE2E_API_TOKEN':'test_'+'a'*40,'AXIOM_PILOT_PORT':'59631','AXIOM_PILOT_INSTANCE':'test-instance'}.items():
        monkeypatch.setenv(key,value)
    with TestClient(pilot.create_pilot_app(),base_url='http://127.0.0.1:59631') as client:
        yield client


def test_single_origin_static_and_authenticated_api(pilot_client):
    client=pilot_client
    assert client.get('/').status_code==200 and 'Authored static UI' in client.get('/').text
    assert client.get('/api/v1/overview').status_code==401
    assert client.get('/api/v1/pilot-status').status_code==401
    assert client.get('/api/v1/overview',headers={'Authorization':'Bearer wrong'}).status_code==401
    headers={'Authorization':'Bearer test_'+'a'*40}
    assert client.get('/api/v1/overview',headers=headers).status_code==200
    assert client.get('/api/v1/scenarios',headers=headers).json()==[]
    assert client.get('/api/v1/pilot-status',headers=headers).json()['frontend']=='built_static'
    assert client.get('/api/v1/not-a-real-route',headers=headers).status_code==404
    assert client.get('/backend/src/ade2e/main.py').status_code==404
    assert client.get('/.env').status_code==404


@pytest.mark.parametrize('headers,code', [({'Host':'attacker.example'},400),
    ({'Origin':'https://outside.invalid'},403),({'Origin':'null'},403),
    ({'Sec-Fetch-Site':'cross-site'},403)])
def test_host_and_browser_origin_boundary(pilot_client,headers,code):
    assert pilot_client.get('/',headers=headers).status_code==code


def test_static_security_headers_and_local_origin(pilot_client):
    response=pilot_client.get('/',headers={'Origin':'http://127.0.0.1:59631'})
    assert response.status_code==200
    assert response.headers['X-Frame-Options']=='DENY'
    assert response.headers['Cache-Control']=='no-store'
    assert "script-src 'self'" in response.headers['Content-Security-Policy']


def test_nonascii_bearer_is_an_auth_error_not_a_server_error(pilot_client):
    assert pilot_client.get('/api/v1/pilot-status',headers=[(b'authorization', b'Bearer \xff')]).status_code==401


def test_dependency_mismatch_prevents_any_workspace_write(bundle,tmp_path):
    manifest=json.loads((bundle/'bundle.json').read_bytes())
    manifest['dependencies']={'axiom-absent-runtime-fixture':'1.0'}
    (bundle/'bundle.json').write_bytes(pilot.canonical(manifest))
    with pytest.raises(ValueError,match='not_installed'):
        pilot.preflight(bundle,tmp_path/'data',tmp_path/'access',59631)
    assert not (tmp_path/'data').exists()


def test_server_wrapper_handles_post_shutdown_forwarded_signal(monkeypatch):
    import signal, sys, uvicorn
    original = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(sys,'argv',['local-pilot','_server'])
    monkeypatch.setattr(pilot,'assert_source_bound',lambda:None)
    monkeypatch.setattr(pilot,'create_pilot_app',lambda:object())
    monkeypatch.setenv('AXIOM_PILOT_PORT','59631')
    def completed_shutdown(*_args,**_kwargs):
        signal.raise_signal(signal.SIGTERM)
    monkeypatch.setattr(uvicorn,'run',completed_shutdown)
    assert pilot.main()==0
    assert signal.getsignal(signal.SIGTERM)==original
