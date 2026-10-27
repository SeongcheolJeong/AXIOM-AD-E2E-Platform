"""Private participant provisioning and password-only gateway contracts."""
import json
from pathlib import Path
import stat
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import object_beta_access as beta
import external_access_gateway as gateway


@pytest.fixture
def provisioning(tmp_path):
    dist = tmp_path / 'dist'
    dist.mkdir()
    (dist / 'index.html').write_text('<html><body>Beta authoring</body></html>')
    requested = []
    projects, tokens = [], {}

    def api(request):
        requested.append(request)
        path = request.url.path
        if path.endswith('/team/whoami'):
            return httpx.Response(200, json={'mode': 'operator'})
        if path.endswith('/experiments/capabilities'):
            return httpx.Response(200, json={'handlers': [{'kinds': ['object_sim'], 'resource_id': 'carla-city-2160'}]})
        if path == '/api/v1/projects':
            projects.append(f'project_authored{len(projects)}')
            return httpx.Response(201, json={'id': projects[-1]})
        if path.endswith('/object-beta-policy'):
            assert json.loads(request.content)['resource_id'] == 'carla-city-2160'
            return httpx.Response(200, json={'enabled': True, 'active_limit': 1, 'queue_limit': 2})
        if path.endswith('/tokens'):
            project = path.split('/')[4]
            token = 'axiom_authored_token_for_' + project
            tokens[project] = token
            return httpx.Response(201, json={'id': 'token_' + project, 'token': token})
        return httpx.Response(404)

    with httpx.Client(base_url='http://127.0.0.1:8063', transport=httpx.MockTransport(api)) as client:
        yield tmp_path, dist, client, requested, projects, tokens


def test_distinct_private_credentials_and_no_start_or_send(provisioning):
    root, dist, client, requests, projects, tokens = provisioning
    result = beta.prepare(client, root / 'invites', upstream='http://127.0.0.1:8063', dist=dist,
                          participants=['tester-a', 'tester-b'], resource_id='carla-city-2160')
    assert len(set(projects)) == 2 and len(set(tokens.values())) == 2
    assert result['service_started'] is result['invitations_sent'] is False
    passwords = []
    for member in result['participants']:
        config_path = Path(member['gateway_config'])
        config = gateway.load_config(config_path)
        assert config.access_mode == 'project_engineer' and config.expected_project_id == member['project_id']
        assert config.upstream_token_file.read_text().strip() == tokens[member['project_id']]
        passwords.append(config.password)
        assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(config_path.parent.stat().st_mode) == 0o700
        assert config.password not in json.dumps(result)
        assert tokens[member['project_id']] not in json.dumps(result)
    assert passwords[0] != passwords[1]
    assert all('/tokens/' not in item['path'] and not item['path'].endswith('object-beta-policy') for item in beta.WRITE_PATHS)


def test_existing_seeded_project_reused_and_bad_resource_stops_before_mutation(provisioning):
    root, dist, client, requests, projects, tokens = provisioning
    beta.prepare(client, root / 'existing', upstream='http://127.0.0.1:8063', dist=dist,
                 participants=['tester'], resource_id='carla-city-2160', existing_projects={'tester': 'project_seeded'})
    assert projects == [] and set(tokens) == {'project_seeded'}
    before = len([r for r in requests if r.method == 'POST'])
    with pytest.raises(ValueError, match='resource_id'):
        beta.prepare(client, root / 'bad-resource', upstream='http://127.0.0.1:8063', dist=dist,
                     participants=['tester'], resource_id='carla-city-2190')
    assert not (root / 'bad-resource').exists()
    assert len([r for r in requests if r.method == 'POST']) == before


def test_password_only_gateway_uses_assigned_project_and_hides_bearer(provisioning):
    root, dist, client, _, _, tokens = provisioning
    result = beta.prepare(client, root / 'access', upstream='http://127.0.0.1:8063', dist=dist,
                          participants=['tester'], resource_id='carla-city-2160')
    member = result['participants'][0]
    config = gateway.load_config(Path(member['gateway_config']))
    seen = []

    def upstream(request):
        seen.append(request)
        assert request.headers['authorization'] == 'Bearer ' + tokens[member['project_id']]
        if request.url.path.endswith('/whoami'):
            return httpx.Response(200, json={'mode': 'project', 'role': 'Engineer',
                'project_id': member['project_id'], 'legacy_domains_available': False,
                'permissions': {'read': True, 'execute': True, 'review': False, 'admin': False},
                'principal_id': 'opaque-identity'})
        if request.url.path.endswith('/experiments') and request.method == 'POST':
            return httpx.Response(429, json={'detail': '대기 작업을 취소해 주세요.'}, headers={'Retry-After': '10'})
        return httpx.Response(200, json={'items': []})

    origin = 'https://assigned-beta.example.test'
    with TestClient(gateway.create_app(config, transport=httpx.MockTransport(upstream)),
                    base_url=origin, follow_redirects=False) as browser:
        assert browser.get('/api/v1/projects').status_code == 401
        login = browser.post('/login', data={'password': config.password}, headers={'origin': origin})
        assert login.status_code == 303
        assert browser.get('/api/v1/projects').status_code == 200
        assert browser.get('/api/v1/team/whoami').json()['principal_id'] == 'opaque-identity'
        behavior_path = '/api/v1/object-sim/executions/job_authored/behavior'
        assert browser.get(behavior_path + '?project_id=' + member['project_id']).status_code == 200
        assert browser.get(behavior_path + '?project_id=project_other').status_code == 403
        assert browser.post(behavior_path, json={}, headers={'origin': origin}).status_code == 403
        quota = browser.post('/api/v1/experiments', json={'project_id': member['project_id']}, headers={'origin': origin})
        assert quota.status_code == 429 and quota.headers['retry-after'] == '10'
        assert quota.json()['detail'] == '대기 작업을 취소해 주세요.'
        assert browser.get('/api/v1/object-sim/presets?project_id=project_other').status_code == 403
        assert browser.post('/api/v1/projects/' + member['project_id'] + '/object-beta-policy',
                            json={'enabled': False}, headers={'origin': origin}).status_code == 403
        assert tokens[member['project_id']] not in browser.get('/').text
        assert 'Bearer' not in login.text and 'Secure' in login.headers['set-cookie']


def test_provisioned_saved_comparison_story_is_scoped_read_only(provisioning):
    root, dist, client, _, _, tokens = provisioning
    prepared = beta.prepare(client, root / 'story-access', upstream='http://127.0.0.1:8063', dist=dist,
                            participants=['tester'], resource_id='carla-city-2160')
    member = prepared['participants'][0]
    config = gateway.load_config(Path(member['gateway_config']))
    path = '/api/v1/object-sim/story'
    assert config.read_paths.count(path) == 1
    seen = []

    def upstream(request):
        assert request.headers['authorization'] == 'Bearer ' + tokens[member['project_id']]
        if request.url.path.endswith('/whoami'):
            return httpx.Response(200, json={'mode': 'project', 'role': 'Engineer',
                'project_id': member['project_id'], 'legacy_domains_available': False,
                'permissions': {'read': True, 'execute': True, 'review': False, 'admin': False}})
        seen.append(request)
        return httpx.Response(200, json={'status': 'available', 'segments': []})

    origin = 'https://assigned-beta.example.test'
    query = {'project_id': member['project_id'], 'left_execution_id': 'job_authored_stopped',
             'right_execution_id': 'job_authored_bypass'}
    with TestClient(gateway.create_app(config, transport=httpx.MockTransport(upstream)),
                    base_url=origin, follow_redirects=False) as browser:
        assert browser.get(path, params=query).status_code == 401
        assert browser.post('/login', data={'password': config.password}, headers={'origin': origin}).status_code == 303
        for endpoint in ('/api/v1/object-sim/comparison', path):
            assert browser.get(endpoint, params=query).status_code == 200
        assert len(seen) == 2
        assert seen[-1].method == 'GET' and seen[-1].url.path == path
        assert dict(seen[-1].url.params) == query
        assert browser.get(path, params={**query, 'project_id': 'project_other'}).status_code == 403
        assert browser.get(path, params=[*query.items(), ('project_id', 'project_other')]).status_code == 403
        assert browser.get(path + '/private', params=query).status_code == 403
        assert browser.post(path, json=query, headers={'origin': origin}).status_code == 403
        assert browser.get(path, params=query, headers={'sec-fetch-site': 'cross-site'}).status_code == 403
        assert len(seen) == 2


@pytest.mark.parametrize('participants', [['duplicate', 'duplicate'], ['../escape'], ['a'] * 6])
def test_invalid_participant_plan_has_no_mutations(provisioning, participants):
    root, dist, client, requests, _, _ = provisioning
    with pytest.raises(ValueError):
        beta.prepare(client, root / 'invalid', upstream='http://127.0.0.1:8063', dist=dist,
                     participants=participants, resource_id='carla-city-2160')
    assert not requests and not (root / 'invalid').exists()
