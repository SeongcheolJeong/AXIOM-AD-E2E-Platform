"""The beta tools are discovered from the product API and keep its role boundary."""
import pytest
from fastapi.testclient import TestClient

from ade2e.adapters import Registry
from ade2e.database import Settings
from ade2e.experiment_runtime import ExperimentRegistry
from ade2e.main import create_app


@pytest.fixture
def beta_catalog_client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, seed_examples=False, api_token='catalog-operator', team_mode=True),
        Registry(load_plugins=False), experiment_registry=ExperimentRegistry([], load_plugins=False))
    with TestClient(app, headers={'Authorization': 'Bearer catalog-operator'}) as client:
        yield client


def beta_tools(client, headers=None):
    response = client.get('/api/v1/agent/tools', headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()['source'] == 'current_openapi'
    return {(tool['method'], tool['path']): tool for tool in response.json()['tools']
            if tool['path'].startswith('/api/v1/beta/')}


def test_operator_catalog_uses_beta_openapi_body_and_required_idempotency(beta_catalog_client):
    client = beta_catalog_client
    schema = client.get('/openapi.json').json()
    tools = beta_tools(client)
    paths = {path for path in schema['paths'] if path.startswith('/api/v1/beta/')}
    assert {'/api/v1/beta/readiness', '/api/v1/beta/evidence', '/api/v1/beta/tasks',
        '/api/v1/beta/feedback', '/api/v1/beta/overview'} <= paths
    expected = {(method.upper(), path) for path in paths for method in schema['paths'][path]
                if method in {'get', 'post', 'put', 'patch', 'delete'}}
    assert set(tools) == expected
    assert any(path.endswith('/progress') for _, path in tools)
    assert any(path.endswith('/triage') for _, path in tools)
    for (method, path), tool in tools.items():
        operation = schema['paths'][path][method.lower()]
        assert tool['operation_id'] == operation['operationId']
        assert tool['authorization']['mode'] == 'operator'
        assert tool['read_only'] is (method == 'GET')
        if method == 'POST':
            assert tool['required_headers'] == ['Idempotency-Key']
            assert tool['idempotency_scope'] == path
            body_schema = operation['requestBody']['content']['application/json']['schema']
            assert tool['openapi_input_schema']['properties']['body'] == body_schema
            assert 'body' in tool['openapi_input_schema']['required']
        if path.endswith('/overview') or path.endswith('/triage'):
            assert operation['x-axiom-operator-only'] is True


@pytest.mark.parametrize('role', ['Viewer', 'Engineer', 'Admin'])
def test_project_catalog_never_exposes_operator_beta_tools(beta_catalog_client, role):
    client = beta_catalog_client
    project = client.post('/api/v1/projects', json={'name': 'Private beta participant'}).json()
    token = client.post(f"/api/v1/projects/{project['id']}/tokens",
        json={'user_label': f'Participant {role}', 'role': role}).json()
    headers = {'Authorization': 'Bearer ' + token['token']}
    tools = beta_tools(client, headers)
    assert tools
    assert all(tool['authorization'] == dict(mode='project', project_id=project['id'], role=role)
        for tool in tools.values())
    assert not any(path.endswith('/overview') or path.endswith('/triage') for _, path in tools)
    assert ('GET', '/api/v1/beta/readiness') in tools
    assert ('GET', '/api/v1/beta/tasks') in tools
    assert ('GET', '/api/v1/beta/feedback') in tools
    if role == 'Viewer':
        assert all(method == 'GET' for method, _ in tools)
    else:
        assert ('POST', '/api/v1/beta/tasks') in tools
        assert ('POST', '/api/v1/beta/feedback') in tools
        assert any(method == 'POST' and path.endswith('/progress') for method, path in tools)
    # Discovery policy is not a substitute for enforcement at the actual route.
    assert client.get('/api/v1/beta/overview', headers=headers).status_code == 403
