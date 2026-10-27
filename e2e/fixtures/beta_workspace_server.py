"""Isolated common-beta browser fixture. No simulators or existing workspaces.

Uses normal APIs to create two fixture projects, one authored scenario and an
explicitly authored result through the ordinary experiment queue.
Credentials stay in an ignored owner-only directory and are never printed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime', type=Path)
    parser.add_argument('--port', type=int, default=8086)
    args = parser.parse_args()
    root = args.runtime.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    operator = secrets.token_urlsafe(32)
    os.environ.update(ADE2E_DATA_DIR=str(root / 'workspace'), ADE2E_API_TOKEN=operator,
                      ADE2E_TEAM_MODE='1', ADE2E_SEED_EXAMPLES='0', AXIOM_APP_FACTORY_ONLY='1')
    from fastapi.staticfiles import StaticFiles
    from fastapi.testclient import TestClient
    from ade2e.database import Settings
    from ade2e.experiment_runtime import ExperimentRegistry, ExperimentHandlerMetadata, ExperimentExecution
    from ade2e.scenario_v2 import get_scenario
    from ade2e.adapters import Registry
    from ade2e.worker import run_once
    from ade2e.main import create_app
    import uvicorn
    registry = ExperimentRegistry([], load_plugins=False)
    app = create_app(Settings(), experiment_registry=registry)

    class AuthoredBrowserHandler:
        """Native-shaped input solely to exercise retained evidence associations."""
        def metadata(self):
            return ExperimentHandlerMetadata(id='beta-browser-fixture', version='1', kinds=['object_sim'],
                status='available', fidelity='authored_browser_fixture', cancellation_supported=True,
                description='Authored UI fixture; no simulator, sensor or real-world measurement')

        def prepare(self, spec):
            with app.state.db.read_session() as session:
                source = get_scenario(session, spec.input['scenario_id'], spec.input['scenario_revision'])
            if source['project_id'] != spec.project_id:
                raise ValueError('Browser fixture project differs')
            return dict(schema_version='carla-city-input/1', scenario=source['scenario'],
                source=dict(scenario_id=source['id'], scenario_revision=source['revision'],
                    configuration_hash=source['configuration_hash'], scenario_manifest_hash=source['manifest_hash'],
                    map_sha256=source['scenario']['map']['sha256'], seed=spec.seed),
                execution_fidelity='authored_browser_fixture', scope='Authored fixture; no simulator executed')

        def execute(self, snapshot, work_dir, should_cancel):
            return ExperimentExecution(summary=dict(schema_version='beta-browser-fixture-result/1',
                scope='Authored browser fixture; no measured trajectory or simulator execution',
                verification_verdict='not_evaluated'), fidelity='authored_browser_fixture')

    registry.register(AuthoredBrowserHandler())
    def checked(response):
        assert response.is_success, response.text
        return response.json()
    with TestClient(app) as client:
        client.headers['Authorization'] = 'Bearer ' + operator
        participants = []
        for label in ('Browser tester A', 'Browser tester B'):
            project = checked(client.post('/api/v1/projects', json={'name': label}))
            token = checked(client.post('/api/v1/projects/' + project['id'] + '/tokens',
                                        json={'user_label': label, 'role': 'Engineer'}))
            participants.append({'project_id': project['id'], 'token': token['token']})
        project_id = participants[0]['project_id']
        rights = {'license': 'authored-test-fixture', 'redistribution': 'restricted',
                  'notes': 'Browser workflow fixture, no actual city or simulator evidence'}
        def pose(x):
            return dict(x=x, y=0, z=0, heading_rad=0)
        xodr = '<OpenDRIVE><header name="beta-browser-fixture"/></OpenDRIVE>'
        native_map = checked(client.post('/api/v1/maps', json={'project_id': project_id,
            'id': 'beta-browser-map', 'definition': {'resource_kind': 'map', 'name': 'Authored beta graph',
                'native_name': 'FixtureTown', 'server_version': 'authored-browser-fixture', 'rights': rights,
                'opendrive': {'content': xodr, 'sha256': hashlib.sha256(xodr.encode()).hexdigest()},
                'nodes': [dict(id=str(i), **pose(i * 5), road_id=1, section_id=0, lane_id=-1,
                              lane_width_m=3.5, next_ids=[str(i + 1)] if i < 20 else []) for i in range(21)],
                'spawn_points': [pose(0)]}}))
        asset = checked(client.post('/api/v1/assets', json={'project_id': project_id,
            'id': 'beta-browser-vehicle', 'definition': {'resource_kind': 'asset', 'name': 'Authored vehicle',
                'blueprint_id': 'vehicle.fixture', 'actor_kind': 'vehicle', 'rights': rights}}))
        def ref(value):
            return {key: value[key] for key in ('id', 'revision', 'sha256')}
        scenario = checked(client.post('/api/v1/scenario-v2', headers={'Idempotency-Key': 'beta-browser-source'},
            json={'project_id': project_id, 'scenario': {'schema_version': 'scenario-v2/3',
                'name': 'Authored beta browser fixture', 'duration_s': 5, 'map': ref(native_map),
                'actors': [{'id': 'ego', 'role': 'ego', 'asset': ref(asset), 'pose': pose(0),
                            'route': [pose(0), pose(50)], 'controller': {'kind': 'reactive'}, 'speed_mps': 3}]}}))
        definition = dict(project_id=project_id, name='Authored beta browser result', kind='object_sim',
            adapter_id='beta-browser-fixture', seed=scenario['scenario']['seed'],
            input=dict(scenario_id=scenario['id'], scenario_revision=scenario['revision'],
                       configuration_hash=scenario['configuration_hash']))
        experiment = checked(client.post('/api/v1/experiments', json=definition,
                                          headers={'Idempotency-Key': 'beta-browser-experiment'}))
        execution = checked(client.post('/api/v1/experiments/' + experiment['id'] + '/executions',
            json={'preview_hash': experiment['preview_hash']}, headers={'Idempotency-Key': 'beta-browser-execution'}))
        run_once(app.state.db, Registry(), 'authored-beta-browser-worker', job_id=execution['id'], experiment_registry=registry)
        completed = checked(client.get('/api/v1/experiments/executions/' + execution['id']))
        assert completed['status'] == 'completed' and completed['fidelity'] == 'authored_browser_fixture'
    receipt = {'operator': operator, 'participants': participants, 'scenario': scenario,
               'execution_id': execution['id'], 'execution_fidelity': 'authored_browser_fixture',
               'scope': 'Authored UI/API fixture; no simulator or measured results'}
    descriptor = os.open(root / 'access.json', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(receipt, stream)
    app.mount('/', StaticFiles(directory=Path(__file__).resolve().parents[2] / 'frontend/dist', html=True))
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
