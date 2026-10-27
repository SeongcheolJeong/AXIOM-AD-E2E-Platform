"""Shared API catalog and role boundaries across the new product workflows."""
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from ade2e.main import create_app
from ade2e.database import Settings
from ade2e.adapters import Registry

def test_catalog_covers_real_verification_driving_and_experiment_api_and_roles():
    with tempfile.TemporaryDirectory() as directory:
        app=create_app(Settings(data_dir=Path(directory),seed_examples=False,api_token='test-operator',team_mode=True),Registry(load_plugins=False))
        with TestClient(app) as client:
            operator={'Authorization':'Bearer test-operator'}
            catalog=client.get('/api/v1/agent/tools',headers=operator)
            assert catalog.status_code==200 and len(catalog.content)<2*1024**2
            tools=catalog.json()['tools']
            routes={(t['method'],t['path']) for t in tools}
            assert ('POST','/api/v1/driving-policies/preflight') in routes
            assert ('GET','/api/v1/verification-plans/{identifier}') in routes
            assert ('POST','/api/v1/experiments/{experiment_id}/executions') in routes
            execution=next(t for t in tools if t['path']=='/api/v1/experiments/{experiment_id}/executions')
            assert execution['required_headers']==['Idempotency-Key']
            project=client.post('/api/v1/projects',headers=operator,json={'name':'Private engineering'}).json()
            viewer=client.post(f"/api/v1/projects/{project['id']}/tokens",headers=operator,json={'user_label':'Viewer','role':'Viewer'}).json()
            headers={'Authorization':'Bearer '+viewer['token']}
            assert client.get('/api/v1/team/whoami',headers=headers).json()['permissions']['execute'] is False
            assert client.get('/api/v1/projects/default/tokens',headers=headers).status_code==404
            assert client.get('/api/v1/recordings',headers=headers).status_code==403
            assert client.get('/api/v1/experiments?project_id=default',headers=headers).status_code==404
            assert client.get(f"/api/v1/experiments?project_id={project['id']}",headers=headers).json()=={'items':[]}
            viewer_catalog=client.get('/api/v1/agent/tools',headers=headers).json()['tools']
            readonly_preview = ('POST','/api/v1/scene-sequences/preview')
            assert viewer_catalog and all(t['method']=='GET' or (t['method'],t['path'])==readonly_preview for t in viewer_catalog)
            assert readonly_preview in {(t['method'],t['path']) for t in viewer_catalog}
            assert ('POST','/api/v1/scene-sequences/freeze') not in {(t['method'],t['path']) for t in viewer_catalog}
            assert not any('/tokens' in t['path'] or t['path'].startswith('/api/v1/recordings') for t in viewer_catalog)
            assert any(t['path']=='/api/v1/experiments/executions/{job_id}/artifacts/{artifact_id}' for t in viewer_catalog)

def test_unconfigured_native_capabilities_refuse_without_creating_a_job(monkeypatch):
    monkeypatch.delenv('AXIOM_ESMINI_COMMAND',raising=False)
    with tempfile.TemporaryDirectory() as directory:
        app=create_app(Settings(data_dir=directory,seed_examples=False),Registry(load_plugins=False))
        with TestClient(app) as client:
            response=client.post('/api/v1/experiments/preview',json={'name':'Native object input','kind':'object_sim','adapter_id':'esmini-object',
                'input':{'scenario_id':'unavailable','scenario_revision':1}})
            assert response.status_code==422
            assert client.get('/api/v1/experiments/executions').json()['items']==[]
