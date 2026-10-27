"""Authored upstream checks; no user accounts, engine calls, or service starts."""
import json
from pathlib import Path
import sys
import time

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import beta_workspace_access as beta
import external_access_gateway as gateway


def test_prepare_pins_existing_engineer_and_preserves_secrets(tmp_path):
    dist = tmp_path / 'dist'; dist.mkdir()
    (dist / 'index.html').write_text('<html><body>Beta fixture</body></html>')
    token = tmp_path / 'engineer-token'
    token.write_text('axiom_fixture_credential_not_real_12345'); token.chmod(0o600)
    seen = []
    def upstream(request):
        seen.append(request)
        return httpx.Response(200, json={'mode': 'project', 'role': 'Engineer', 'project_id': 'project_a'})
    with httpx.Client(transport=httpx.MockTransport(upstream), base_url='http://127.0.0.1:8075') as client:
        receipt = beta.prepare(client, tmp_path / 'invite', upstream='http://127.0.0.1:8075', dist=dist,
            project_id='project_a', token_file=token)
        with pytest.raises(ValueError, match='exact project'):
            beta.prepare(client, tmp_path / 'wrong', upstream='http://127.0.0.1:8075', dist=dist,
                project_id='project_b', token_file=token)
    assert not (tmp_path / 'wrong').exists()
    assert all(request.method == 'GET' for request in seen)
    config = gateway.load_config(Path(receipt['gateway_config']))
    assert config.expected_project_id == 'project_a'
    assert not receipt['service_started'] and not receipt['invitations_sent']
    assert config.password not in json.dumps(receipt) and token.read_text() not in json.dumps(receipt)
    assert (tmp_path / 'invite').stat().st_mode & 0o777 == 0o700
    assert Path(receipt['gateway_config']).stat().st_mode & 0o777 == 0o600


def test_participant_routes_forward_feedback_but_block_operator_and_other_project(tmp_path):
    dist = tmp_path / 'dist'; dist.mkdir()
    (dist / 'index.html').write_text('<html><body>Beta fixture</body></html>')
    token = tmp_path / 'engineer-token'
    token.write_text('axiom_fixture_credential_not_real_12345'); token.chmod(0o600)
    reads, writes = beta.approved_paths()
    assert len(writes) <= 128
    seen = []
    def upstream(request):
        seen.append(request)
        if request.url.path.endswith('/whoami'):
            return httpx.Response(200, json={'mode': 'project', 'role': 'Engineer', 'project_id': 'project_a',
                'legacy_domains_available': False,
                'permissions': {'read': True, 'execute': True, 'review': False, 'admin': False}})
        return httpx.Response(200, json={'saved': True})
    config = gateway.GatewayConfig('private-fixture-password-long-enough', time.time() + 600, dist,
        reads, 'http://127.0.0.1:8075', token, 'project_engineer', writes, 'project_a')
    app = gateway.create_app(config, transport=httpx.MockTransport(upstream))
    with TestClient(app, base_url='https://beta.example.test', follow_redirects=False) as client:
        assert client.post('/login', data={'password': config.password},
            headers={'origin': 'https://beta.example.test'}).status_code == 303
        assert client.get('/api/v1/beta/tasks?project_id=project_a').status_code == 200
        assert client.post('/api/v1/beta/feedback', json={'project_id': 'project_a', 'message': 'Blocked'},
            headers={'origin': 'https://beta.example.test', 'Idempotency-Key': 'feedback-one'}).status_code == 200
        for path in ('/api/v1/beta/overview', '/api/v1/log-sim/beta-overview'):
            assert client.get(path).status_code == 403
        assert client.post('/api/v1/beta/feedback/item/triage', json={'project_id': 'project_a'},
            headers={'origin': 'https://beta.example.test'}).status_code == 403
        assert client.get('/api/v1/beta/tasks?project_id=project_b').status_code == 403
        assert client.post('/api/v1/beta/tasks', json={'project_id': 'project_b'},
            headers={'origin': 'https://beta.example.test'}).status_code == 403
    forwarded = [request for request in seen if request.url.path.endswith('/feedback')]
    assert len(forwarded) == 1 and forwarded[0].headers['idempotency-key'] == 'feedback-one'
    assert all(request.headers['authorization'] == 'Bearer ' + token.read_text() for request in seen)
