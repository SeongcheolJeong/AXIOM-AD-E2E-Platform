"""Gateway boundary tests using authored assets and an in-memory HTTP upstream.

These checks make no platform mutations or remote simulator calls.
"""
import json
import os
from pathlib import Path
import signal
import shutil
import socket
import subprocess
import sys
import time

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import external_access_gateway as gateway

PASSWORD = 'authored-test-password-that-is-long-enough'
ORIGIN = 'https://share.example.test'


@pytest.fixture
def dist(tmp_path):
    path = tmp_path / 'dist'
    path.mkdir()
    (path / 'index.html').write_text('<html><body>AUTHORED PRIVATE APP</body></html>')
    (path / 'favicon.svg').write_text('<svg></svg>')
    assets = path / 'assets'
    assets.mkdir()
    (assets / 'index.js').write_text('privateApp()')
    (assets / 'index.css').write_text('body{color:red}')
    (assets / 'index.js.map').write_text('privateSourceMap')
    (assets / 'secret.json').write_text('privateRuntimeFile')
    (path / 'secret.txt').write_text('privateSecret')
    return path


def config(dist, now=None):
    return gateway.GatewayConfig(PASSWORD, (now or time.time()) + 3600, dist, (
        '/api/v1/overview', '/api/v1/runs/{run_id}', '/api/v1/artifacts/{artifact_id}/download',
        '/api/v1/adapters/{adapter_id}/health', '/api/v1/jobs/{job_id}/remote-execution',
    ))


@pytest.fixture
def live(dist):
    received = []
    clock = [time.time()]

    def upstream(request):
        received.append(request)
        return httpx.Response(200, json={'authored': True}, headers={
            'set-cookie': 'upstream_secret=hidden; Path=/',
            'access-control-allow-origin': '*', 'x-internal-secret': 'hidden',
        })

    app = gateway.create_app(config(dist, clock[0]), transport=httpx.MockTransport(upstream), clock=lambda: clock[0])
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        yield client, received, clock


def login(client, password=PASSWORD, **kwargs):
    return client.post('/login', data={'password': password}, headers={'origin': ORIGIN}, **kwargs)


@pytest.mark.parametrize('path,status', [('/', 303), ('/assets/index.js', 303), ('/favicon.svg', 303),
                                         ('/api/v1/overview', 401), ('/health', 303)])
def test_private_barrier(live, path, status):
    client, received, _ = live
    response = client.get(path)
    assert response.status_code == status
    assert 'AUTHORED PRIVATE APP' not in response.text and 'privateApp' not in response.text
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['x-frame-options'] == 'DENY'
    assert not received


def test_login_session_and_static_manifest(live):
    client, received, _ = live
    page = client.get('/login')
    assert '외부 조회 전용' in page.text
    assert page.headers['referrer-policy'] == 'same-origin'
    response = login(client)
    assert response.headers['referrer-policy'] == 'same-origin'
    assert response.status_code == 303
    cookie = response.headers['set-cookie']
    for attribute in ['Secure', 'HttpOnly', 'SameSite=strict', 'Path=/']:
        assert attribute in cookie
    assert PASSWORD not in cookie
    app_page = client.get('/')
    assert 'AUTHORED PRIVATE APP' in app_page.text
    assert app_page.headers['referrer-policy'] == 'no-referrer'
    assert '외부 조회 전용 · 수정/실행 차단' in client.get('/').text
    assert client.get('/assets/index.js').text == 'privateApp()'
    assert client.get('/favicon.svg').status_code == 200
    for path in ['/secret.txt', '/assets/index.js.map', '/assets/secret.json', '/src/main.tsx', '/.runtime/token', '/@vite/client']:
        assert client.get(path).status_code == 404
    assert not received


def test_owner_static_reload_retains_session_and_route_boundary(live, dist):
    client, received, clock = live
    login_response = login(client)
    session_cookie = client.cookies.get(gateway.COOKIE)
    (dist / 'index.html').write_text('<html><body>REPLACEMENT WORKSPACE</body></html>')
    (dist / 'assets/index.js').write_text('replacementApp()')
    # Files are a snapshot until the owner deliberately requests the reload.
    assert 'AUTHORED PRIVATE APP' in client.get('/').text
    assert client.app.state.reload_static_snapshot() is True
    assert client.app.state.static_reload_generation == 1
    response = client.get('/')
    assert 'REPLACEMENT WORKSPACE' in response.text
    assert 'AUTHORED PRIVATE APP' not in response.text
    assert gateway.ACCESS_MARKER.decode() in response.text
    assert client.get('/assets/index.js').text == 'replacementApp()'
    assert client.cookies.get(gateway.COOKIE) == session_cookie
    assert not response.headers.get('set-cookie')
    assert login_response.status_code == 303
    for path in ['/api/reload', '/reload', '/assets/secret.json', '/secret.txt']:
        assert client.get(path).status_code in {403, 404}
        assert client.post(path).status_code == 403
    assert not received
    clock[0] += 3601
    assert client.app.state.reload_static_snapshot() is False
    assert client.app.state.static_reload_status == 'expired'
    assert client.app.state.static_reload_generation == 1
    assert client.get('/').status_code == 410


@pytest.mark.parametrize('damage', ['invalid_index', 'symlink_asset'])
def test_rejected_owner_reload_keeps_the_previous_complete_ui(live, dist, tmp_path, damage):
    client, received, _ = live
    login(client)
    (dist / 'assets/index.js').write_text('mustNotReplaceApp()')
    if damage == 'invalid_index':
        (dist / 'index.html').write_text('unfinished build')
    else:
        private = tmp_path / 'private.js'
        private.write_text('private file must not become public')
        (dist / 'assets/outside.js').symlink_to(private)
    assert client.app.state.reload_static_snapshot() is False
    assert client.app.state.static_reload_generation == 0
    assert client.app.state.static_reload_status == 'rejected'
    assert 'AUTHORED PRIVATE APP' in client.get('/').text
    assert client.get('/assets/index.js').text == 'privateApp()'
    assert client.get('/assets/outside.js').status_code == 404
    assert not received


@pytest.mark.skipif(not hasattr(signal, 'SIGHUP'), reason='Owner-local SIGHUP requires Unix')
def test_real_gateway_owner_signal_reloads_ui_without_restarting_or_revoking_session(dist, tmp_path):
    # An isolated child on an ephemeral loopback port, using authored assets.
    # It makes no requests to the real platform and owns no tunnel or simulator.
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    cfg_path = tmp_path / 'authored-gateway.json'
    cfg_path.write_text(json.dumps({'password': PASSWORD, 'expires_at': time.time() + 60,
        'dist': str(dist), 'read_paths': ['/api/v1/overview']}))
    cfg_path.chmod(0o600)
    script = Path(gateway.__file__).resolve()
    child = subprocess.Popen([sys.executable, str(script), '--config', str(cfg_path), '--port', str(port)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=1, follow_redirects=False,
                          trust_env=False) as client:
            deadline = time.monotonic() + 10
            while True:
                assert child.poll() is None
                try:
                    if client.get('/login').status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert time.monotonic() < deadline
                time.sleep(0.05)
            response = client.post('/login', data={'password': PASSWORD},
                                   headers={'origin': f'http://127.0.0.1:{port}'})
            assert response.status_code == 303
            # Secure cookies intentionally require HTTPS. This loopback-only
            # harness supplies the issued cookie without changing that policy.
            cookie = response.cookies.get(gateway.COOKIE)
            headers = {'cookie': f'{gateway.COOKIE}={cookie}'}
            assert 'AUTHORED PRIVATE APP' in client.get('/', headers=headers).text
            (dist / 'index.html').write_text('<html><body>OWNER SIGNAL WORKSPACE</body></html>')
            assert 'AUTHORED PRIVATE APP' in client.get('/', headers=headers).text
            os.kill(child.pid, signal.SIGHUP)
            deadline = time.monotonic() + 5
            while 'OWNER SIGNAL WORKSPACE' not in client.get('/', headers=headers).text:
                assert child.poll() is None and time.monotonic() < deadline
                time.sleep(0.05)
            assert child.poll() is None
            assert client.get('/assets/secret.json', headers=headers).status_code == 404
            assert client.post('/reload', headers=headers).status_code == 403
    finally:
        if child.poll() is None:
            child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


@pytest.mark.parametrize('path', ['/assets/%2e%2e/secret.txt', '/assets/%252e%252e/secret.txt',
                                 '/assets/index.js%00', '/assets%2findex.js', '/api//v1/overview',
                                 '/api/v1/runs/x%3fsecret=true'])
def test_encoded_or_ambiguous_paths_never_forward(live, path):
    client, received, _ = live
    login(client)
    response = client.get(path)
    assert response.status_code in {400, 404, 403, 303}
    assert not received


def test_unknown_route_and_remote_probes_fail_closed(live):
    client, received, _ = live
    login(client)
    for path in ['/api/v1/new-unknown-route', '/api/v1/adapters/carla/health', '/api/v1/jobs/abc/remote-execution']:
        response = client.get(path)
        assert response.status_code == 403
    assert not received


@pytest.mark.parametrize('method', ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE'])
def test_write_requests_never_reach_upstream(live, method):
    client, received, _ = live
    login(client)
    response = client.request(method, '/api/v1/runs/example', json={'run': 'must not execute'})
    assert response.status_code == 403
    assert '수정과 시험 실행은 차단' in response.text
    assert not received


def test_upstream_headers_and_cookie_jar_are_isolated(live):
    client, received, _ = live
    login(client)
    for _ in range(2):
        response = client.get('/api/v1/overview?limit=5', headers={
            'authorization': 'Bearer outside', 'proxy-authorization': 'Basic outside',
            'x-api-key': 'outside', 'referer': ORIGIN + '/secret', 'origin': ORIGIN,
            'range': 'bytes=0-999', 'accept': 'application/json',
        })
        assert response.status_code == 200
        assert response.json() == {'authored': True}
        assert 'set-cookie' not in response.headers
        assert 'x-internal-secret' not in response.headers
        assert 'access-control-allow-origin' not in response.headers
    assert len(received) == 2
    for outgoing in received:
        assert str(outgoing.url) == gateway.UPSTREAM + '/api/v1/overview?limit=5'
        for name in ['cookie', 'authorization', 'proxy-authorization', 'x-api-key', 'referer', 'origin']:
            assert name not in outgoing.headers
        assert outgoing.headers['accept-encoding'] == 'identity'
        assert outgoing.headers['range'] == 'bytes=0-999'
        assert outgoing.method == 'GET'


def test_expiration_revokes_every_path_and_session(live):
    client, received, clock = live
    login(client)
    clock[0] += 3601
    for path in ['/', '/login', '/assets/index.js', '/api/v1/overview']:
        assert client.get(path).status_code == 410
    assert client.post('/login', data={'password': PASSWORD}, headers={'origin': ORIGIN}).status_code == 410
    assert not received


def test_login_requires_same_origin_and_rejects_cross_site(live):
    client, received, _ = live
    for headers in [{}, {'origin': 'https://attacker.test'}, {'origin': ORIGIN, 'sec-fetch-site': 'cross-site'}]:
        assert client.post('/login', data={'password': PASSWORD}, headers=headers).status_code == 403
    assert client.get('/').status_code == 303
    assert not received


def test_rate_limit_is_global_bounded_and_recovers(live):
    client, _, clock = live
    for _ in range(8):
        assert login(client, 'wrong').status_code == 401
    assert login(client).status_code == 429
    clock[0] += 61
    assert login(client).status_code == 303


def test_logout_requires_origin_and_revokes_session(live):
    client, _, _ = live
    login(client)
    cookie = client.cookies.get(gateway.COOKIE)
    assert client.post('/logout').status_code == 403
    assert client.get('/').status_code == 200
    assert client.post('/logout', headers={'origin': ORIGIN}).status_code == 303
    client.cookies.set(gateway.COOKIE, cookie)
    assert client.get('/').status_code == 303


def test_redirect_not_followed_and_no_external_location(dist):
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(302, headers={'location': 'https://outside.test/private'})

    with TestClient(gateway.create_app(config(dist), transport=httpx.MockTransport(upstream)), base_url=ORIGIN) as client:
        login(client, follow_redirects=False)
        response = client.get('/api/v1/overview')
        assert response.status_code == 502 and 'location' not in response.headers
    assert len(calls) == 1


def test_stream_closes_and_head_has_no_body(dist):
    closed = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'first '
            yield b'second'
        async def aclose(self):
            closed.append(True)

    def upstream(request):
        return httpx.Response(200, stream=Body(), headers={'content-type': 'application/octet-stream'})

    with TestClient(gateway.create_app(config(dist), transport=httpx.MockTransport(upstream)), base_url=ORIGIN) as client:
        login(client, follow_redirects=False)
        assert client.get('/api/v1/artifacts/abc/download').content == b'first second'
        response = client.head('/api/v1/artifacts/abc/download')
        assert response.status_code == 200 and response.content == b''
    assert len(closed) == 2


def test_config_permissions_fixed_upstream_and_expiry(dist, tmp_path):
    path = tmp_path / 'config.json'
    value = {'password': PASSWORD, 'expires_at': time.time() + 3600, 'dist': str(dist),
             'read_paths': ['/api/v1/overview']}
    path.write_text(json.dumps(value))
    path.chmod(0o644)
    with pytest.raises(ValueError):
        gateway.load_config(path)
    path.chmod(0o600)
    assert gateway.load_config(path).upstream == gateway.UPSTREAM
    for key, invalid in [('upstream', 'https://outside.test'), ('expires_at', time.time() - 1),
                         ('expires_at', time.time() + 48 * 60 * 60 + 60), ('read_paths', ['/api/v1/{path:path}']),
                         ('allow_write', True)]:
        bad = dict(value, **{key: invalid})
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            gateway.load_config(path)


@pytest.mark.parametrize('offset,accepted', [
    (48 * 60 * 60, True),
    (48 * 60 * 60 + 5, True),
    (48 * 60 * 60 + 5.001, False),
    (0, False),
])
def test_config_expiration_is_bounded_to_48_hours(dist, tmp_path, monkeypatch, offset, accepted):
    now = 1800000000.0
    monkeypatch.setattr(gateway.time, 'time', lambda: now)
    path = tmp_path / 'config.json'
    value = {'password': PASSWORD, 'expires_at': now + offset, 'dist': str(dist),
             'read_paths': ['/api/v1/overview']}
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    if accepted:
        assert gateway.load_config(path).expires_at == now + offset
    else:
        with pytest.raises(ValueError, match='within the next 48 hours'):
            gateway.load_config(path)


def test_48_hour_access_keeps_12_hour_sessions_and_final_expiration(dist):
    clock = [time.time()]
    expires_at = clock[0] + 48 * 60 * 60
    cfg = gateway.GatewayConfig(PASSWORD, expires_at, dist, ('/api/v1/overview',))
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={'authored': True}))
    with TestClient(gateway.create_app(cfg, transport=transport, clock=lambda: clock[0]),
                    base_url=ORIGIN, follow_redirects=False) as client:
        response = login(client)
        assert response.status_code == 303
        assert 'Max-Age=43200' in response.headers['set-cookie']
        clock[0] += 12 * 60 * 60 - 1
        assert client.get('/api/v1/overview').status_code == 200
        clock[0] += 1
        assert client.get('/api/v1/overview').status_code == 401
        assert login(client).status_code == 303
        assert client.get('/api/v1/overview').status_code == 200
        clock[0] = expires_at - 1
        response = login(client)
        assert response.status_code == 303
        assert 'Max-Age=1' in response.headers['set-cookie']
        clock[0] = expires_at
        assert client.get('/api/v1/overview').status_code == 410
        assert login(client).status_code == 410


def test_symlink_assets_and_config_rejected(dist, tmp_path):
    outside = tmp_path / 'outside.js'
    outside.write_text('PRIVATE SOURCE')
    (dist / 'assets/escape.js').symlink_to(outside)
    with pytest.raises(ValueError):
        gateway.create_app(config(dist))
    link = tmp_path / 'config-link.json'
    link.symlink_to(outside)
    with pytest.raises((OSError, ValueError)):
        gateway.load_config(link)


def test_cross_site_get_and_get_body_do_not_forward(live):
    client, received, _ = live
    login(client)
    assert client.get('/api/v1/overview', headers={'sec-fetch-site': 'cross-site'}).status_code == 403
    assert client.request('GET', '/api/v1/overview', content=b'mutation').status_code == 400
    assert not received


def test_distributed_license_notices_remain_available_behind_auth(dist, tmp_path):
    licenses = dist / 'licenses'
    licenses.mkdir()
    (licenses / 'dm-sans-OFL.txt').write_text('AUTHORED LICENSE NOTICE')
    (licenses / 'private.json').write_text('PRIVATE JSON')
    (licenses / '.private.txt').write_text('PRIVATE HIDDEN TEXT')
    with TestClient(gateway.create_app(config(dist)), base_url=ORIGIN, follow_redirects=False) as client:
        assert client.get('/licenses/dm-sans-OFL.txt').status_code == 303
        login(client)
        response = client.get('/licenses/dm-sans-OFL.txt')
        assert response.status_code == 200 and response.text == 'AUTHORED LICENSE NOTICE'
        assert response.headers['content-type'].startswith('text/plain')
        assert response.headers['cache-control'] == 'no-store'
        assert client.get('/licenses/private.json').status_code == 404
        assert client.get('/licenses/.private.txt').status_code == 404
    outside = tmp_path / 'outside-license.txt'
    outside.write_text('PRIVATE OUTSIDE FILE')
    (licenses / 'escape.txt').symlink_to(outside)
    with pytest.raises(ValueError):
        gateway.create_app(config(dist))


def test_actual_car_visual_texture_and_notices_load_behind_auth(dist):
    source = Path(__file__).resolve().parents[2] / 'frontend/public/assets/kenney-car-kit'
    target = dist / 'assets/kenney-car-kit'
    shutil.copytree(source, target)
    (target / 'private.txt').write_text('PRIVATE TEXT')
    (target / 'private.json').write_text('{"private":true}')
    with TestClient(gateway.create_app(config(dist)), base_url=ORIGIN, follow_redirects=False) as client:
        for name in ('sedan.glb', 'Textures/colormap.png', 'License.txt', 'manifest.json'):
            assert client.get('/assets/kenney-car-kit/' + name).status_code == 303
        login(client)
        for name, media_type in (('sedan.glb', 'model/gltf-binary'), ('Textures/colormap.png', 'image/png'),
                                 ('License.txt', 'text/plain'), ('manifest.json', 'application/json')):
            response = client.get('/assets/kenney-car-kit/' + name)
            assert response.status_code == 200
            assert response.content == (source / name).read_bytes()
            assert response.headers['content-type'].startswith(media_type)
            assert response.headers['cache-control'] == 'no-store'
        assert client.get('/assets/kenney-car-kit/private.txt').status_code == 404
        assert client.get('/assets/kenney-car-kit/private.json').status_code == 404
        original = client.get('/assets/kenney-car-kit/sedan.glb').content
        (target / 'sedan.glb').write_bytes(b'not a GLB: rejected unfinished build')
        assert client.app.state.reload_static_snapshot() is False
        assert client.get('/assets/kenney-car-kit/sedan.glb').content == original


def test_oversized_glb_rejected_before_reading(dist):
    with (dist / 'assets/oversized.glb').open('wb') as handle:
        handle.truncate(32 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match='bounds'):
        gateway.create_app(config(dist))


# Team-scoped gateway checks use only authored tokens and an in-memory upstream.
VIEWER_TOKEN = 'axiom_authored_viewer_token_for_gateway_tests_only'
VIEWER_IDENTITY = {
    'mode': 'project', 'role': 'Viewer', 'project_id': 'authored-project',
    'legacy_domains_available': False,
    'permissions': {'read': True, 'execute': False, 'review': False, 'admin': False},
}


def scoped_config(dist, tmp_path, *, upstream='http://127.0.0.1:8021'):
    token = tmp_path / 'viewer.token'
    token.write_text(VIEWER_TOKEN + '\n')
    token.chmod(0o600)
    cfg = config(dist)
    return gateway.GatewayConfig(cfg.password, cfg.expires_at, cfg.dist, cfg.read_paths,
                                 upstream, token, expected_project_id=VIEWER_IDENTITY['project_id'])


def test_external_context_is_private_readonly_and_contains_no_upstream_secret(live):
    client, received, clock = live
    assert client.get(gateway.CONTEXT_PATH).status_code == 401
    login(client)
    response = client.get(gateway.CONTEXT_PATH)
    assert response.json() == {
        'schema_version': 'axiom.external-viewer-context/1', 'mode': 'external_viewer',
        'read_only': True, 'authentication': 'gateway_session', 'expires_at': clock[0] + 3600,
    }
    assert response.headers['cache-control'] == 'no-store'
    assert client.post(gateway.CONTEXT_PATH).status_code == 403
    assert client.get(gateway.CONTEXT_PATH, headers={'sec-fetch-site': 'cross-site'}).status_code == 403
    assert client.head(gateway.CONTEXT_PATH).content == b''
    page = client.get('/').content
    assert page.count(gateway.ACCESS_MARKER) == 1
    assert PASSWORD.encode() not in page and VIEWER_TOKEN.encode() not in page
    assert not received
    clock[0] += 3601
    assert client.get(gateway.CONTEXT_PATH).status_code == 410


def test_server_viewer_token_replaces_all_client_credentials(dist, tmp_path):
    cfg = scoped_config(dist, tmp_path)
    received = []

    def upstream(request):
        received.append(request)
        value = VIEWER_IDENTITY if request.url.path.endswith('/team/whoami') else {'authored': True}
        return httpx.Response(200, json=value, headers={'set-cookie': 'private_upstream_cookie=hidden'})

    with TestClient(gateway.create_app(cfg, transport=httpx.MockTransport(upstream)), base_url=ORIGIN) as client:
        assert len(received) == 1
        assert received[0].url.path == '/api/v1/team/whoami'
        login(client, follow_redirects=False)
        for _ in range(2):
            response = client.get('/api/v1/overview', headers={
                'authorization': 'Bearer attacker_supplied', 'x-api-key': 'attacker_supplied',
                'proxy-authorization': 'Basic attacker_supplied', 'referer': ORIGIN + '/secret',
            })
            assert response.status_code == 200
            assert VIEWER_TOKEN not in response.text
            assert 'set-cookie' not in response.headers
        assert VIEWER_TOKEN not in client.get(gateway.CONTEXT_PATH).text
        assert VIEWER_TOKEN not in client.get('/').text
    assert len(received) == 3
    for request in received:
        assert str(request.url).startswith(cfg.upstream + '/api/v1/')
        assert request.headers['authorization'] == 'Bearer ' + VIEWER_TOKEN
        assert request.method == 'GET'
        for name in ['cookie', 'proxy-authorization', 'referer', 'x-api-key']:
            assert name not in request.headers


@pytest.mark.parametrize('damage', [
    {'role': 'Admin'}, {'role': 'Engineer'}, {'role': 'Reviewer'}, {'mode': 'operator'},
    {'legacy_domains_available': True}, {'project_id': None},
    {'permissions': {'read': True, 'execute': True, 'review': False, 'admin': False}},
    {'permissions': {'read': True, 'execute': False, 'review': False}},
])
def test_startup_refuses_nonviewer_or_incomplete_identity(dist, tmp_path, damage):
    cfg = scoped_config(dist, tmp_path)
    seen = []

    def upstream(request):
        seen.append(request)
        return httpx.Response(200, json=dict(VIEWER_IDENTITY, **damage))

    with pytest.raises(RuntimeError, match='^Upstream Viewer role verification failed$'):
        with TestClient(gateway.create_app(cfg, transport=httpx.MockTransport(upstream)), base_url=ORIGIN):
            pytest.fail('non-Viewer gateway started')
    assert len(seen) == 1 and seen[0].url.path == '/api/v1/team/whoami'


@pytest.mark.parametrize('result', ['rejected', 'redirect', 'malformed', 'too_large', 'network_error'])
def test_identity_failures_are_closed_and_do_not_disclose_secret(dist, tmp_path, result):
    cfg = scoped_config(dist, tmp_path)
    requests = []

    def upstream(request):
        requests.append(request)
        if result == 'network_error':
            raise httpx.ConnectError(VIEWER_TOKEN)
        if result == 'redirect':
            return httpx.Response(302, headers={'location': 'https://outside.test/'})
        if result == 'rejected':
            return httpx.Response(401, text=VIEWER_TOKEN)
        if result == 'too_large':
            return httpx.Response(200, content=b' ' * (16 * 1024 + 1))
        return httpx.Response(200, text=VIEWER_TOKEN)

    with pytest.raises(RuntimeError) as error:
        with TestClient(gateway.create_app(cfg, transport=httpx.MockTransport(upstream)), base_url=ORIGIN):
            pytest.fail('unverified gateway started')
    assert str(error.value) == 'Upstream Viewer role verification failed'
    assert len(requests) == 1


@pytest.mark.parametrize('origin', [
    'http://localhost:8021', 'http://127.0.0.1:9000', 'http://127.0.0.1:8021/',
    'https://127.0.0.1:8021', 'http://127.0.0.1:8021@outside.test', 'http://carla-host.example.invalid:8021',
])
def test_upstream_origin_allowlist_is_exact(dist, tmp_path, origin):
    with pytest.raises(ValueError, match='allowed loopback'):
        gateway.create_app(scoped_config(dist, tmp_path, upstream=origin))


@pytest.mark.parametrize('origin', ['http://127.0.0.1:8020', 'http://127.0.0.1:8021', 'http://127.0.0.1:8033'])
def test_current_upstream_cannot_start_without_private_viewer_token(dist, origin):
    old = config(dist)
    cfg = gateway.GatewayConfig(old.password, old.expires_at, old.dist, old.read_paths, origin)
    with pytest.raises(ValueError, match='Viewer token file'):
        gateway.create_app(cfg)


def test_private_token_config_permissions_symlinks_and_header_injection(dist, tmp_path):
    cfg = scoped_config(dist, tmp_path)
    config_file = tmp_path / 'scoped-config.json'
    value = {'password': PASSWORD, 'expires_at': cfg.expires_at, 'dist': str(dist),
             'read_paths': list(cfg.read_paths), 'upstream': cfg.upstream,
             'upstream_token_file': str(cfg.upstream_token_file), 'expected_project_id': cfg.expected_project_id}
    config_file.write_text(json.dumps(value))
    config_file.chmod(0o600)
    loaded = gateway.load_config(config_file)
    assert loaded.upstream_token_file == cfg.upstream_token_file
    assert PASSWORD not in repr(loaded)
    cfg.upstream_token_file.chmod(0o644)
    with pytest.raises(ValueError, match='0600'):
        gateway.load_config(config_file)
    cfg.upstream_token_file.chmod(0o600)
    cfg.upstream_token_file.write_text(VIEWER_TOKEN + '\r\nX-Injected: header')
    with pytest.raises(ValueError, match='token format'):
        gateway.load_config(config_file)
    cfg.upstream_token_file.write_text(VIEWER_TOKEN)
    link = tmp_path / 'linked-token'
    link.symlink_to(cfg.upstream_token_file)
    value['upstream_token_file'] = str(link)
    config_file.write_text(json.dumps(value))
    with pytest.raises((ValueError, OSError)):
        gateway.load_config(config_file)
    value['upstream_token_file'] = 'relative/token'
    config_file.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='absolute path'):
        gateway.load_config(config_file)


@pytest.mark.parametrize('path', [
    '/api/v1/adapters/carla/capabilities', '/api/v1/adapters/carla/probe',
    '/api/v1/camera-bridge/scene-catalog', '/api/v1/camera-bridge/sources',
    '/api/v1/projects/default/tokens',
])
def test_new_native_probes_and_token_listing_denied_even_if_pinned(dist, path):
    cfg = config(dist)
    cfg = gateway.GatewayConfig(cfg.password, cfg.expires_at, cfg.dist, cfg.read_paths + (path,))
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json={})
    with TestClient(gateway.create_app(cfg, transport=httpx.MockTransport(upstream)), base_url=ORIGIN) as client:
        login(client, follow_redirects=False)
        assert client.get(path).status_code == 403
    assert not calls


def test_download_preserves_exact_identity_length_and_hash_for_get_and_head(dist):
    import hashlib
    body = b'AUTHORED MCAP payload'
    digest = hashlib.sha256(body).hexdigest()
    def upstream(request):
        return httpx.Response(200, content=body, headers={
            'content-type': 'application/octet-stream', 'content-length': str(len(body)),
            'x-content-sha256': digest, 'x-private-token': VIEWER_TOKEN,
        })
    with TestClient(gateway.create_app(config(dist), transport=httpx.MockTransport(upstream)), base_url=ORIGIN) as client:
        login(client, follow_redirects=False)
        for method in ['GET', 'HEAD']:
            response = client.request(method, '/api/v1/artifacts/abc/download')
            assert response.content == (body if method == 'GET' else b'')
            assert response.headers['content-length'] == str(len(body))
            assert response.headers['x-content-sha256'] == digest
            assert 'x-private-token' not in response.headers


def test_decoded_download_does_not_reuse_encoded_length_or_hash(dist):
    import gzip
    import hashlib
    body = b'authored uncompressed payload'
    encoded = gzip.compress(body)
    def upstream(request):
        return httpx.Response(200, content=encoded, headers={
            'content-encoding': 'gzip', 'content-length': str(len(encoded)),
            'x-content-sha256': hashlib.sha256(encoded).hexdigest(),
        })
    with TestClient(gateway.create_app(config(dist), transport=httpx.MockTransport(upstream)), base_url=ORIGIN) as client:
        login(client, follow_redirects=False)
        response = client.get('/api/v1/artifacts/abc/download')
        assert response.content == body
        assert 'content-length' not in response.headers
        assert 'x-content-sha256' not in response.headers
        assert 'content-encoding' not in response.headers


def test_upstream_error_body_is_not_exposed(dist):
    def upstream(request):
        return httpx.Response(500, text=VIEWER_TOKEN)
    with TestClient(gateway.create_app(config(dist), transport=httpx.MockTransport(upstream)), base_url=ORIGIN) as client:
        login(client, follow_redirects=False)
        response = client.get('/api/v1/overview')
        assert response.status_code == 502 and VIEWER_TOKEN not in response.text


ENGINEER_IDENTITY = dict(VIEWER_IDENTITY, role='Engineer', permissions={
    'read': True, 'execute': True, 'review': False, 'admin': False,
})
ENGINEER_WRITES = (
    {'method': 'POST', 'path': '/api/v1/experiments/preview'},
    {'method': 'POST', 'path': '/api/v1/experiments/{experiment_id}/executions'},
    {'method': 'POST', 'path': '/api/v1/experiments/executions/{job_id}/cancel'},
    {'method': 'PATCH', 'path': '/api/v1/scenario-v2/{scenario_id}'},
)


def engineer_config(dist, tmp_path):
    cfg = scoped_config(dist, tmp_path)
    return gateway.GatewayConfig(cfg.password, cfg.expires_at, cfg.dist, cfg.read_paths,
                                 cfg.upstream, cfg.upstream_token_file, 'project_engineer', ENGINEER_WRITES, cfg.expected_project_id)


@pytest.mark.parametrize('engineer', [False, True])
@pytest.mark.parametrize('bookmark,expected', [
    ('', '/#workspace?project=authored-project'),
    ('#object-sim', '/#object-sim?project=authored-project'),
    ('#object-sim?project=foreign&mode=compare&left=job_a&right=job_b&time=4.5',
     '/#object-sim?mode=compare&left=job_a&right=job_b&time=4.5&project=authored-project'),
    ('#object-sim?execution=job_a&project=foreign&project=another',
     '/#object-sim?execution=job_a&project=authored-project'),
])
def test_scoped_login_pins_project_and_preserves_local_bookmark(dist, tmp_path, engineer, bookmark, expected):
    cfg = engineer_config(dist, tmp_path) if engineer else scoped_config(dist, tmp_path)
    identity = ENGINEER_IDENTITY if engineer else VIEWER_IDENTITY
    received = []

    def upstream(request):
        received.append(request)
        return httpx.Response(200, json=identity if request.url.path.endswith('/team/whoami') else {'authored': True})

    with TestClient(gateway.create_app(cfg, transport=httpx.MockTransport(upstream)),
                    base_url=ORIGIN, follow_redirects=False) as client:
        assert client.get('/' + bookmark).headers['location'] == '/login'
        page = client.get('/login')
        assert 'name="return_hash"' in page.text and 'location.hash' in page.text
        assert VIEWER_TOKEN not in page.text and cfg.expected_project_id not in page.text
        response = client.post('/login?project=foreign&next=https://attacker.example',
                               data={'password': PASSWORD, 'return_hash': bookmark}, headers={'origin': ORIGIN})
        assert response.status_code == 303 and response.headers['location'] == expected
        assert 'AUTHORED PRIVATE APP' in client.get(expected).text
        assert len(received) == 1  # Opening the landing document cannot call a default project API.
        assert client.get('/api/v1/overview?project_id=foreign').status_code == 403
        assert len(received) == 1
        assert client.get('/api/v1/overview?project_id=authored-project').status_code == 200
        assert received[-1].url.params['project_id'] == 'authored-project'


@pytest.mark.parametrize('bookmark', [
    'https://attacker.example/#object-sim', '//attacker.example', '#//attacker.example',
    '#object-sim/../../private', '#object-sim\r\nLocation: https://attacker.example',
    '#object-sim?' + 'x' * 2048, '#object-sim?' + '&'.join('a=1' for _ in range(65)),
])
def test_login_landing_rejects_external_malformed_or_unbounded_return_targets(bookmark):
    assert gateway.login_landing('authored-project', bookmark) == '/#workspace?project=authored-project'
    assert gateway.login_landing(None, bookmark) == '/'


def test_failed_scoped_login_retains_escaped_bookmark(engineer_live):
    client, received = engineer_live
    bookmark = '#object-sim?mode=compare&note="><script>alert(1)</script>&left=job_a'
    response = client.post('/login', data={'password': 'wrong', 'return_hash': bookmark}, headers={'origin': ORIGIN})
    assert response.status_code == 401
    assert 'value="#object-sim?mode=compare&amp;note=&quot;&gt;&lt;script&gt;' in response.text
    assert '<script>alert(1)</script>' not in response.text
    assert not received


@pytest.fixture
def engineer_live(dist, tmp_path):
    received = []
    cfg = engineer_config(dist, tmp_path)
    def upstream(request):
        received.append(request)
        if request.url.path == '/api/v1/team/whoami':
            return httpx.Response(200, json=ENGINEER_IDENTITY)
        return httpx.Response(200, json={'authored': 'accepted'}, headers={'set-cookie': 'upstream_private=secret'})
    with TestClient(gateway.create_app(cfg, transport=httpx.MockTransport(upstream)),
                    base_url=ORIGIN, follow_redirects=False) as client:
        assert len(received) == 1
        received.clear()
        login(client)
        yield client, received


def test_engineer_context_marker_and_document_origin_policy(engineer_live):
    client, received = engineer_live
    value = client.get(gateway.CONTEXT_PATH).json()
    assert value['mode'] == 'external_workspace' and value['read_only'] is False
    assert value['editing_allowed'] is True and value['execution_allowed'] is True
    for path in ['/', '/index.html']:
        response = client.get(path)
        assert response.content.count(gateway.ENGINEER_MARKER) == 1
        assert gateway.ACCESS_MARKER not in response.content
        assert response.headers['referrer-policy'] == 'same-origin'
        assert '수정·실행 가능' in response.text and VIEWER_TOKEN not in response.text
    assert client.get('/assets/index.js').headers['referrer-policy'] == 'no-referrer'
    assert '수정·실행 가능' in client.get('/login').text
    assert not received


@pytest.mark.parametrize('method,path', [
    ('POST', '/api/v1/experiments/preview'),
    ('POST', '/api/v1/experiments/exp_authored/executions'),
    ('PATCH', '/api/v1/scenario-v2/scenario_authored'),
])
def test_engineer_forwards_only_pinned_json_with_server_auth_and_idempotency(engineer_live, method, path):
    client, received = engineer_live
    payload = b'{"project_id":"authored-project","name":"authored change"}'
    response = client.request(method, path, content=payload, headers={
        'origin': ORIGIN, 'sec-fetch-site': 'same-origin', 'content-type': 'application/json; charset=utf-8',
        'iDeMpOtEnCy-KeY': 'authored-key:one', 'authorization': 'Bearer browser_supplied',
        'x-api-key': 'browser_supplied', 'cookie': client.headers.get('cookie', '') +
        '; ' + gateway.COOKIE + '=' + client.cookies.get(gateway.COOKIE),
        'referer': ORIGIN + '/private',
    })
    assert response.status_code == 200 and len(received) == 1
    forwarded = received[0]
    assert forwarded.method == method and forwarded.url.path == path
    assert forwarded.content == payload
    assert forwarded.headers['authorization'] == 'Bearer ' + VIEWER_TOKEN
    assert forwarded.headers['idempotency-key'] == 'authored-key:one'
    assert forwarded.headers['content-type'] == 'application/json'
    for name in ['cookie', 'origin', 'referer', 'x-api-key']:
        assert name not in forwarded.headers
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert 'set-cookie' not in response.headers


@pytest.mark.parametrize('headers', [
    {}, {'origin': 'null'}, {'origin': 'https://attacker.test'},
    {'origin': ORIGIN, 'sec-fetch-site': 'cross-site'},
    {'origin': ORIGIN, 'sec-fetch-site': 'same-site'},
])
def test_engineer_mutations_require_strict_origin(engineer_live, headers):
    client, received = engineer_live
    response = client.post('/api/v1/experiments/preview', json={}, headers=headers)
    assert response.status_code == 403 and not received


@pytest.mark.parametrize('method,path', [
    ('DELETE', '/api/v1/experiments/preview'), ('PUT', '/api/v1/experiments/preview'),
    ('POST', '/api/v1/not-approved'), ('POST', gateway.CONTEXT_PATH),
    ('OPTIONS', '/api/v1/experiments/preview'), ('TRACE', '/api/v1/experiments/preview'),
])
def test_engineer_unpinned_methods_paths_and_context_never_forward(engineer_live, method, path):
    client, received = engineer_live
    response = client.request(method, path, json={}, headers={'origin': ORIGIN})
    assert response.status_code == 403 and not received


def test_engineer_empty_cancel_body_is_allowed_without_uploads(engineer_live):
    client, received = engineer_live
    response = client.post('/api/v1/experiments/executions/job_authored/cancel', headers={'origin': ORIGIN})
    assert response.status_code == 200
    assert len(received) == 1 and received[0].content == b''


@pytest.mark.parametrize('body,headers,status', [
    (b'{}', {'content-type': 'text/plain'}, 415),
    (b'{}', {'content-type': 'multipart/form-data'}, 415),
    (b'{}', {}, 415),
    (b'{}', {'content-type': 'application/json', 'content-encoding': 'gzip'}, 415),
    (b'not JSON', {'content-type': 'application/json'}, 400),
    (b'{"value": NaN}', {'content-type': 'application/json'}, 400),
    (b'\xff', {'content-type': 'application/json'}, 400),
    (b'{}', {'content-type': 'application/json', 'idempotency-key': ' '}, 400),
    (b'{}', {'content-type': 'application/json', 'idempotency-key': 'x' * 181}, 400),
    (b'{}', {'content-type': 'application/json', 'content-length': str(gateway.MAX_JSON_BODY + 1)}, 413),
])
def test_engineer_rejects_non_json_bad_idempotency_and_oversized_headers(engineer_live, body, headers, status):
    client, received = engineer_live
    response = client.post('/api/v1/experiments/preview', content=body, headers={'origin': ORIGIN, **headers})
    assert response.status_code == status and not received


def test_engineer_streamed_body_limit_is_enforced_without_length(engineer_live, monkeypatch):
    client, received = engineer_live
    monkeypatch.setattr(gateway, 'MAX_JSON_BODY', 32)
    def chunks():
        yield b'{"value":"'
        yield b'a' * 64
        yield b'"}'
    response = client.post('/api/v1/experiments/preview', content=chunks(), headers={
        'origin': ORIGIN, 'content-type': 'application/json',
    })
    assert response.status_code == 413 and not received
    # Rejection must release the concurrency slot; a subsequent valid request works.
    for _ in range(9):
        assert client.post('/api/v1/experiments/preview', json={}, headers={'origin': ORIGIN}).status_code == 200


@pytest.mark.parametrize('identity', [
    VIEWER_IDENTITY,
    dict(ENGINEER_IDENTITY, role='Admin'),
    dict(ENGINEER_IDENTITY, mode='operator'),
    dict(ENGINEER_IDENTITY, permissions={'read': True, 'execute': True, 'review': True, 'admin': False}),
    dict(ENGINEER_IDENTITY, permissions={'read': True, 'execute': True, 'review': False, 'admin': True}),
])
def test_engineer_startup_requires_exact_engineer_permissions(dist, tmp_path, identity):
    cfg = engineer_config(dist, tmp_path)
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json=identity)
    with pytest.raises(RuntimeError, match='^Upstream Engineer role verification failed$'):
        with TestClient(gateway.create_app(cfg, transport=httpx.MockTransport(upstream)), base_url=ORIGIN):
            pytest.fail('invalid Engineer role started')
    assert len(calls) == 1


def test_engineer_camera_reads_are_explicit_but_admin_and_probe_stay_denied(dist, tmp_path):
    cfg = engineer_config(dist, tmp_path)
    allowed = '/api/v1/camera-bridge/scene-catalog'
    denied = ('/api/v1/adapters/carla/health', '/api/v1/adapters/carla/probe',
              '/api/v1/jobs/job_authored/remote-execution', '/api/v1/projects/default/tokens',
              '/api/v1/projects/default/tokens/token_authored/revoke', '/api/v1/projects')
    pinned = cfg.write_paths + tuple({'method': 'POST', 'path': path} for path in denied)
    cfg = gateway.GatewayConfig(cfg.password, cfg.expires_at, cfg.dist, cfg.read_paths + (allowed,) + denied,
                               cfg.upstream, cfg.upstream_token_file, cfg.access_mode, pinned, cfg.expected_project_id)
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json=ENGINEER_IDENTITY if request.url.path.endswith('/team/whoami') else {})
    with TestClient(gateway.create_app(cfg, transport=httpx.MockTransport(upstream)), base_url=ORIGIN) as client:
        login(client, follow_redirects=False)
        calls.clear()
        assert client.get(allowed).status_code == 200
        assert len(calls) == 1
        assert client.get('/api/v1/camera-bridge/sources').status_code == 403
        for path in denied:
            assert client.post(path, json={}, headers={'origin': ORIGIN}).status_code == 403
        assert len(calls) == 1


def test_engineer_configuration_requires_explicit_mode_paths_and_private_token(dist, tmp_path):
    cfg = engineer_config(dist, tmp_path)
    path = tmp_path / 'engineer-config.json'
    value = {'password': PASSWORD, 'expires_at': cfg.expires_at, 'dist': str(dist),
             'read_paths': list(cfg.read_paths), 'upstream': cfg.upstream,
             'upstream_token_file': str(cfg.upstream_token_file), 'expected_project_id': cfg.expected_project_id,
             'access_mode': 'project_engineer', 'write_paths': list(ENGINEER_WRITES)}
    path.write_text(json.dumps(value)); path.chmod(0o600)
    loaded = gateway.load_config(path)
    assert loaded.access_mode == 'project_engineer' and loaded.write_paths == ENGINEER_WRITES
    for change in [
        {'access_mode': 'read_only'}, {'access_mode': 'admin'}, {'write_paths': []},
        {'write_paths': [{'method': 'GET', 'path': '/api/v1/experiments'}]},
        {'write_paths': [{'method': 'POST', 'path': 'https://outside.test'}]},
        {'write_paths': [{'method': 'POST', 'path': '/api/v1/experiments', 'unknown': True}]},
        {'upstream': gateway.UPSTREAM, 'upstream_token_file': None},
    ]:
        path.write_text(json.dumps(dict(value, **change)))
        with pytest.raises(ValueError):
            gateway.load_config(path)


@pytest.mark.parametrize('engineer', [False, True])
def test_startup_refuses_valid_role_from_wrong_project(dist, tmp_path, engineer):
    cfg = engineer_config(dist, tmp_path) if engineer else scoped_config(dist, tmp_path)
    identity = ENGINEER_IDENTITY if engineer else VIEWER_IDENTITY
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json=dict(identity, project_id='another-approved-looking-project'))
    with pytest.raises(RuntimeError, match='role verification failed'):
        with TestClient(gateway.create_app(cfg, transport=httpx.MockTransport(upstream)), base_url=ORIGIN):
            pytest.fail('gateway accepted a token for another project')
    assert len(calls) == 1 and calls[0].url.path == '/api/v1/team/whoami'


@pytest.mark.parametrize('project_id', [None, '', '../default', 'default/child', 'default project',
                                       'default?admin=true', 'p' * 65, 123])
def test_authenticated_config_requires_valid_explicit_project_pin(dist, tmp_path, project_id):
    cfg = scoped_config(dist, tmp_path)
    path = tmp_path / 'project-pin-config.json'
    value = {'password': PASSWORD, 'expires_at': cfg.expires_at, 'dist': str(dist),
             'read_paths': list(cfg.read_paths), 'upstream': cfg.upstream,
             'upstream_token_file': str(cfg.upstream_token_file), 'expected_project_id': project_id}
    path.write_text(json.dumps(value)); path.chmod(0o600)
    with pytest.raises(ValueError, match='expected_project_id'):
        gateway.load_config(path)


def test_direct_app_configuration_cannot_bypass_project_pin(dist, tmp_path):
    cfg = scoped_config(dist, tmp_path)
    missing_pin = gateway.GatewayConfig(cfg.password, cfg.expires_at, cfg.dist, cfg.read_paths,
                                        cfg.upstream, cfg.upstream_token_file)
    with pytest.raises(ValueError, match='expected_project_id'):
        gateway.create_app(missing_pin)


@pytest.fixture
def object_demo_gateway(dist, tmp_path):
    old = engineer_config(dist, tmp_path)
    cfg = gateway.GatewayConfig(old.password, old.expires_at, old.dist,
        ('/api/v1/object-sim/presets', '/api/v1/object-sim/comparison', '/api/v1/object-sim/story'),
        'http://127.0.0.1:8033', old.upstream_token_file, 'project_engineer', (
            {'method': 'POST', 'path': '/api/v1/object-sim/presets/{identity}/preview'},
            # Even a mistaken local allowlist cannot expose preset rebinding.
            {'method': 'POST', 'path': '/api/v1/object-sim/presets/{identity}/bind'},
        ), old.expected_project_id)
    received = []
    def upstream(request):
        if request.url.path == '/api/v1/team/whoami':
            return httpx.Response(200, json=ENGINEER_IDENTITY)
        received.append(request)
        return httpx.Response(200, json={'authored': True})
    with TestClient(gateway.create_app(cfg, transport=httpx.MockTransport(upstream)),
                    base_url=ORIGIN, follow_redirects=False) as client:
        login(client)
        yield client, received, cfg.expected_project_id


def test_demo_8033_scoped_reads_preview_and_no_bind(object_demo_gateway):
    client, received, project = object_demo_gateway
    assert client.get('/api/v1/object-sim/presets', params={'project_id': project}).status_code == 200
    assert client.get('/api/v1/object-sim/comparison', params={
        'project_id': project, 'left_execution_id': 'job_a', 'right_execution_id': 'job_b'}).status_code == 200
    assert client.post('/api/v1/object-sim/presets/obstacle_bypass/preview',
        json={'project_id': project, 'parameters': {'allow_lane_changes': False}}, headers={'origin': ORIGIN}).status_code == 200
    assert len(received) == 3
    assert all(str(request.url).startswith('http://127.0.0.1:8033/') for request in received)
    assert all(request.headers['authorization'] == 'Bearer ' + VIEWER_TOKEN for request in received)
    assert client.post('/api/v1/object-sim/presets/obstacle_bypass/bind',
        json={'project_id': project}, headers={'origin': ORIGIN}).status_code == 403
    assert len(received) == 3


@pytest.mark.parametrize('location', ['query', 'duplicate_query', 'body', 'null_body_project'])
def test_demo_gateway_rejects_wrong_project_before_forwarding(object_demo_gateway, location):
    client, received, project = object_demo_gateway
    if location in ('query', 'duplicate_query'):
        params = [('project_id', 'another_project')]
        if location == 'duplicate_query': params.insert(0, ('project_id', project))
        response = client.get('/api/v1/object-sim/presets', params=params)
    else:
        response = client.post('/api/v1/object-sim/presets/obstacle_bypass/preview',
            json={'project_id': None if location == 'null_body_project' else 'another_project'}, headers={'origin': ORIGIN})
    assert response.status_code == 403
    assert not received


def test_demo_story_read_allowlist_preserves_selected_project_and_get_only(object_demo_gateway):
    client, received, project = object_demo_gateway
    query = {'project_id': project, 'left_execution_id': 'job_a', 'right_execution_id': 'job_b'}
    response = client.get('/api/v1/object-sim/story', params=query)
    assert response.status_code == 200
    assert len(received) == 1
    assert received[0].method == 'GET' and received[0].url.path == '/api/v1/object-sim/story'
    assert dict(received[0].url.params) == query
    assert client.get('/api/v1/object-sim/story', params={**query, 'project_id': 'another_project'}).status_code == 403
    assert client.post('/api/v1/object-sim/story', json=query, headers={'origin': ORIGIN}).status_code == 403
    assert client.get('/api/v1/object-sim/story/private', params=query).status_code == 403
    assert len(received) == 1
