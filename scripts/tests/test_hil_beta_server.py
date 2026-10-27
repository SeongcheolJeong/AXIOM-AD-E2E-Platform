"""Launcher/real queue checks with authored handlers; never run CARLA or SSH."""
import importlib.util
import json
import os
from pathlib import Path
import threading

from fastapi.testclient import TestClient
import pytest


PATH = Path(__file__).resolve().parents[2] / 'e2e/fixtures/hil_sim_beta_server.py'
SPEC = importlib.util.spec_from_file_location('hil_beta_launcher', PATH)
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(os, 'environ', os.environ.copy())
    root = tmp_path.resolve()
    runtime, ui = root / 'runtime', root / 'ui'
    launcher.prepare_runtime(runtime)
    ui.mkdir()
    (ui / 'index.html').write_text('<!doctype html><title>HIL launcher fixture</title>')
    config = {key: '/authored-fixture/' + key.rsplit('_', 1)[-1].lower() for key in launcher.HIL_ENGINE_KEYS}
    config.update(AXIOM_CARLA_HIL_SSH='fixture@never-contact.invalid', AXIOM_CARLA_HIL_PORT='2100')
    environment = root / 'engine.json'
    environment.write_text(json.dumps(config))
    environment.chmod(0o600)
    from axiom_carla.hil import HilCityTransport
    def forbidden_probe(self):
        pytest.fail('Launcher must not probe the remote engine')
    monkeypatch.setattr(HilCityTransport, 'native_probe', forbidden_probe)
    return runtime, ui, environment


def test_native_configuration_requires_both_explicit_options_and_private_exact_keys(isolated):
    _, _, environment = isolated
    assert launcher.engine_environment() == {}
    for arguments in ({'enable_native': True}, {'engine_env_file': environment}):
        with pytest.raises(ValueError, match='supplied together'):
            launcher.engine_environment(**arguments)
    valid = json.loads(environment.read_text())
    assert launcher.engine_environment(enable_native=True, engine_env_file=environment) == valid
    environment.chmod(0o644)
    with pytest.raises(ValueError, match='owner-only'):
        launcher.engine_environment(enable_native=True, engine_env_file=environment)
    environment.chmod(0o600)
    for value in (dict(valid, ADE2E_DATABASE_URL='sqlite:////foreign.db'),
                  {key: item for key, item in valid.items() if key != 'AXIOM_CARLA_HIL_OWNER'},
                  dict(valid, AXIOM_CARLA_HIL_PORT=2100)):
        environment.write_text(json.dumps(value))
        with pytest.raises(ValueError, match='exactly the seven'):
            launcher.engine_environment(enable_native=True, engine_env_file=environment)


def test_offline_default_retains_auth_and_never_starts_worker(isolated, monkeypatch):
    runtime, ui, _ = isolated
    monkeypatch.setenv('AXIOM_CARLA_CITY_SSH', 'unrelated@engine.invalid')
    monkeypatch.setenv('AXIOM_CARLA_HIL_PORT', 'not-a-port')
    token = (runtime / 'access/operator-token').read_text().strip()
    app = launcher.create_beta_app(runtime, ui)
    assert app.state.hil_native_enabled is False
    handler = app.state.experiment_registry.get('carla-software-dut')
    assert handler.db is app.state.db and handler.transport.db is app.state.db
    assert handler.metadata().status == 'not_configured'
    with TestClient(app) as client:
        assert client.get('/api/v1/hil-sim/presets').status_code == 401
        client.headers['Authorization'] = 'Bearer ' + token
        assert client.get('/api/v1/hil-sim/presets').status_code == 200
        assert client.get('/api/v1/workers').json() == []
        assert app.state.hil_native_worker_id is None
        assert client.get('/').status_code == 200
    assert (runtime / 'access/operator-token').read_text().strip() == token


def test_native_mode_claims_only_hil_and_drains_normal_job_receipt(isolated, monkeypatch):
    from ade2e import jobs
    from ade2e.experiment_runtime import ExperimentExecution, ExperimentHandlerMetadata, ExperimentOutputFile
    from axiom_carla.hil import HilExperimentHandler
    runtime, ui, environment = isolated
    entered, release = threading.Event(), threading.Event()
    def prepare(self, spec):
        return {'authored_fixture_only': True, 'seed': spec.seed}
    def execute(self, snapshot, work_dir, should_cancel):
        entered.set()
        assert release.wait(5), 'Test failed to release authored execution'
        assert should_cancel() is False
        output = work_dir / 'authored-fixture.json'
        output.write_text(json.dumps(snapshot))
        return ExperimentExecution(summary={'evaluation_status': 'not_evaluated', 'authored_fixture_only': True},
            artifacts=[ExperimentOutputFile(output, output.name)], fidelity='authored_contract_test')
    monkeypatch.setattr(HilExperimentHandler, 'prepare', prepare)
    monkeypatch.setattr(HilExperimentHandler, 'execute', execute)
    app = launcher.create_beta_app(runtime, ui, enable_native=True, engine_env_file=environment)
    assert [item.id for item in app.state.experiment_registry.list()] == ['carla-software-dut']
    handler = app.state.experiment_registry.get('carla-software-dut')
    assert handler.db is app.state.db and handler.transport.db is app.state.db
    assert handler.metadata().status == 'available'
    # A queued unrelated handler must stay queued even if explicitly installed
    # by an operator. The beta worker's claim capability remains HIL-only.
    class Other:
        def metadata(self):
            return ExperimentHandlerMetadata(id='other-fixture', version='1', kinds=['object_sim'],
                status='available', fidelity='authored_contract_test')
        def prepare(self, spec):
            return prepare(self, spec)
        def execute(self, *args):
            pytest.fail('HIL worker claimed an unrelated experiment')
    app.state.experiment_registry.register(Other())
    token = (runtime / 'access/operator-token').read_text().strip()
    def submit(client, identity, kind, comparison='single'):
        document = dict(name='Authored launcher fixture', project_id='default', adapter_id=identity,
                        kind=kind, comparison_kind=comparison, input={'authored_fixture_only': True})
        created = client.post('/api/v1/experiments', json=document, headers={'Idempotency-Key': identity})
        assert created.status_code == 201, created.text
        result = client.post('/api/v1/experiments/' + created.json()['id'] + '/executions',
            json={'preview_hash': created.json()['preview_hash']}, headers={'Idempotency-Key': identity})
        assert result.status_code == 202, result.text
        return result.json()['id']
    try:
        with TestClient(app, headers={'Authorization': 'Bearer ' + token}) as client:
            other_id = submit(client, 'other-fixture', 'object_sim')
            job_id = submit(client, 'carla-software-dut', 'hil_demo', 'same_scene_fault_ab')
            assert entered.wait(5)
            assert jobs.job_get(app.state.db, job_id, kind='experiment').status == 'running'
            assert jobs.job_get(app.state.db, other_id, kind='experiment').status == 'queued'
            # Exit lifespan during a live job. Worker must publish its receipt
            # before shutdown stops maintenance and disposes the shared DB.
            timer = threading.Timer(.1, release.set)
            timer.start()
        timer.join()
    finally:
        release.set()
    completed = jobs.job_get(app.state.db, job_id, kind='experiment')
    assert completed.status == 'completed'
    assert completed.fidelity == 'authored_contract_test'
    assert completed.manifest_hash and completed.artifacts
    assert jobs.job_get(app.state.db, other_id, kind='experiment').status == 'queued'
    assert jobs.list_workers(app.state.db)[0]['state'] == 'stopped'
    assert (runtime / 'access/operator-token').read_text().strip() == token
    app.state.db.engine.dispose()


def test_native_mode_refuses_shared_presentation_port_without_network(isolated):
    runtime, ui, environment = isolated
    config = json.loads(environment.read_text())
    config['AXIOM_CARLA_HIL_PORT'] = '2000'
    environment.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='dedicated CARLA'):
        launcher.create_beta_app(runtime, ui, enable_native=True, engine_env_file=environment)
