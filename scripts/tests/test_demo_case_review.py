"""In-process review proxy checks; no real service, engine, or worker is started."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

SPEC = importlib.util.spec_from_file_location('demo_case_review', Path(__file__).resolve().parents[1]/'demo_case_review.py')
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)


@pytest.fixture
def prepared(tmp_path):
    from ade2e.database import Database, Settings
    from ade2e.team_models import ProjectTokenRow
    workspace=tmp_path/'workspace'
    db=Database(Settings(data_dir=workspace,seed_examples=False))
    db.initialize()
    with db.session() as session:
        session.add(ProjectTokenRow(id='viewer',project_id='default',role='Viewer',user_label='Test viewer',
            token_sha256=hashlib.sha256(b'project-viewer').hexdigest(),created_at='2026-10-10T00:00:00Z'))
        session.commit()
    db.engine.dispose()
    # Keep the fixture independent of WAL sidecar lifecycle; production retains
    # its existing WAL instead of using immutable=1, which could hide new rows.
    with sqlite3.connect(workspace/'platform.db') as connection:
        connection.execute('PRAGMA journal_mode=DELETE')
    ui=tmp_path/'ui';ui.mkdir();(ui/'index.html').write_text('<title>Review fixture</title>')
    token=tmp_path/'operator-token';token.write_text('local-operator')
    curation=tmp_path/'curation.json';curation.write_text(json.dumps({'schema_version':'demo-case-curation/1','projects':[]}))
    return workspace,ui,token,curation


def make(prepared):
    workspace,ui,token,curation=prepared
    return review.review_app(workspace,'http://127.0.0.1:8123',ui,token,curation)


def test_factory_and_catalog_are_readonly_authorized_and_do_not_start_workspace(prepared,monkeypatch):
    from ade2e.database import Database
    import sqlalchemy
    workspace=prepared[0]
    before={str(p.relative_to(workspace)):p.read_bytes() for p in workspace.rglob('*') if p.is_file()}
    paths={str(p.relative_to(workspace)) for p in workspace.rglob('*')}
    def forbidden(*args,**kwargs):pytest.fail('Review attempted filesystem/database initialization')
    monkeypatch.setattr(Database,'__init__',forbidden)
    monkeypatch.setattr(Database,'initialize',forbidden)
    monkeypatch.setattr(Path,'mkdir',forbidden)
    monkeypatch.setattr(Path,'write_bytes',forbidden)
    monkeypatch.setattr(Path,'write_text',forbidden)
    engines=[];original=sqlalchemy.create_engine
    def capture(*args,**kwargs):
        engine=original(*args,**kwargs);engines.append(engine);return engine
    monkeypatch.setattr(sqlalchemy,'create_engine',capture)
    with TestClient(make(prepared)) as client:
        assert client.get('/api/v1/demo-cases').status_code==401
        response=client.get('/api/v1/demo-cases',headers={'Authorization':'Bearer project-viewer'})
        assert response.status_code==200 and response.json()['counts']['templates']==10
        assert client.get('/api/v1/demo-cases?project_id=not-owned',headers={'Authorization':'Bearer project-viewer'}).status_code==404
        assert client.post('/api/v1/demo-cases',json={}).status_code==405
        with engines[0].connect() as connection:
            with pytest.raises(OperationalError,match='readonly'):
                connection.execute(text("UPDATE team_projects SET name='must not change'"))
    assert before=={str(p.relative_to(workspace)):p.read_bytes() for p in workspace.rglob('*') if p.is_file()}
    assert paths=={str(p.relative_to(workspace)) for p in workspace.rglob('*')}


def transport(monkeypatch,handler):
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(**kwargs,transport=httpx.MockTransport(handler)))


def test_original_upstream_owns_auth_and_writes_preserve_identity(prepared,monkeypatch):
    seen=[]
    class JsonBody(httpx.AsyncByteStream):
        def __init__(self, value):self.value=value
        async def __aiter__(self):yield json.dumps(self.value).encode()
    async def respond(request):
        seen.append((request.method,str(request.url),dict(request.headers),await request.aread()))
        if request.headers.get('authorization')!='Bearer project-viewer':
            return httpx.Response(401,stream=JsonBody({'detail':'Original auth denial'}))
        return httpx.Response(202,stream=JsonBody({'status':'queued by original fixture'}),headers={'X-Source':'original'})
    transport(monkeypatch,respond)
    with TestClient(make(prepared)) as client:
        assert client.get('/api/v1/team/whoami').status_code==401
        response=client.post('/api/v1/experiments?project_id=default',json={'name':'Explicit browser action'},
            headers={'Authorization':'Bearer project-viewer','Idempotency-Key':'same-key',
                     'Origin':'http://127.0.0.1:8053','Connection':'x-one-hop','X-One-Hop':'remove'})
        assert response.status_code==202 and response.headers['X-Source']=='original'
    assert len(seen)==2 and 'authorization' not in seen[0][2]
    method,url,headers,body=seen[1]
    assert method=='POST' and url=='http://127.0.0.1:8123/api/v1/experiments?project_id=default'
    assert headers['authorization']=='Bearer project-viewer' and headers['idempotency-key']=='same-key'
    assert headers['origin']=='http://127.0.0.1:8053' and 'x-one-hop' not in headers
    assert json.loads(body)=={'name':'Explicit browser action'}
    assert all('local-operator' not in str(record) for record in seen)


class RawChunks(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'first'
        yield b'second'


def test_raw_artifact_stream_and_hop_headers_are_preserved_correctly(prepared,monkeypatch):
    async def respond(request):
        return httpx.Response(206,stream=RawChunks(),headers={'Content-Length':'11','Content-Range':'bytes 0-10/20',
            'ETag':'sealed-hash','Connection':'x-hop-response','X-Hop-Response':'remove'})
    transport(monkeypatch,respond)
    with TestClient(make(prepared)) as client:
        response=client.get('/api/v1/experiments/executions/job/artifacts/artifact')
    assert response.status_code==206 and response.content==b'firstsecond'
    assert response.headers['Content-Range']=='bytes 0-10/20' and response.headers['ETag']=='sealed-hash'
    assert 'x-hop-response' not in response.headers


def test_timeout_before_headers_is_clear_502(prepared,monkeypatch):
    async def respond(request):raise httpx.ReadTimeout('fixture timeout',request=request)
    transport(monkeypatch,respond)
    with TestClient(make(prepared)) as client:
        response=client.get('/api/v1/team/whoami')
    assert response.status_code==502 and 'does not restart' in response.json()['detail']


def test_mid_stream_failure_is_not_a_successful_truncated_artifact(prepared,monkeypatch):
    class Interrupted(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'partial'
            raise httpx.ReadError('fixture interrupted original')
    async def respond(request):return httpx.Response(200,stream=Interrupted())
    transport(monkeypatch,respond)
    with TestClient(make(prepared)) as client:
        with pytest.raises(httpx.ReadError,match='interrupted'):
            client.get('/api/v1/experiments/executions/job/artifacts/artifact')
