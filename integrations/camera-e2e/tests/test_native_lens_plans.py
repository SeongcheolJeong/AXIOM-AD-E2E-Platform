"""Run with the isolated CameraE2E copy in PYTHONPATH; no native computation."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from camera_platform.registry import ConflictError, digest
from camera_platform import lens_sensor_raw_job, lens_sensor_quality_job
from camera_platform.lens_scene_plans import plan, submit_planned, install_routes
import hashlib

@pytest.mark.parametrize('kind',['raw','quality'])
def test_exact_native_drift_rejected_before_queue_and_accepted_hash_is_retained(monkeypatch,kind):
    source={'specification':{'schema':lens_sensor_raw_job.DENSE_COMMON_SCENE_SCHEMA if kind=='raw' else lens_sensor_quality_job.SCHEMA_V2,'scene_kind':'slanted_edge'},
            'source_identities':{'saved':'original'},'environment':{'code':'first'}}
    module=lens_sensor_raw_job if kind=='raw' else lens_sensor_quality_job
    monkeypatch.setattr(module,'prepare',lambda service,payload:deepcopy(source))
    calls=[]
    service=SimpleNamespace(registry=SimpleNamespace(submit=lambda document,key:calls.append(document) or document))
    proposal=plan(service,{'specification':{}},kind)
    source['environment']={'code':'changed'}
    with pytest.raises(ConflictError):submit_planned(service,{'specification':{},'plan_hash':proposal['plan_hash']},kind)
    assert calls==[]
    fresh=plan(service,{'specification':{}},kind)
    accepted=submit_planned(service,{'specification':{},'plan_hash':fresh['plan_hash']},kind)
    assert accepted['accepted_plan_hash']==digest(source) and len(calls)==1

def test_explicit_schema_and_payload_scope(monkeypatch):
    monkeypatch.setattr(lens_sensor_raw_job,'prepare',lambda s,p:{'specification':{'schema':lens_sensor_raw_job.RADIANCE_SCENE_SCHEMA,'scene_kind':'road'}})
    with pytest.raises(ValueError,match='slanted edge'):plan(None,{'specification':{}},'raw')
    with pytest.raises(ValueError):plan(None,{'specification':{},'unknown':1},'raw')

def test_endpoint_kind_cannot_be_overridden_by_query(monkeypatch):
    monkeypatch.setattr(lens_sensor_raw_job,'prepare',lambda s,p:{'specification':{'schema':lens_sensor_raw_job.DENSE_COMMON_SCENE_SCHEMA,'scene_kind':'slanted_edge'}})
    monkeypatch.setattr(lens_sensor_quality_job,'prepare',lambda s,p:(_ for _ in ()).throw(AssertionError('query must not switch endpoint kind')))
    app=FastAPI();install_routes(app,None)
    with TestClient(app) as client:
        response=client.post('/api/v1/lens-sensor-raw/plan?selected=quality',json={'specification':{}})
        assert response.status_code==200 and response.json()['schema']=='camera_lens_sensor_raw_plan_v1'
        assert client.post('/api/v1/lens-sensor-raw',json={}).status_code==403
        assert client.post('/api/v1/models',json={}).status_code==403
        assert client.get('/api/v1/models').status_code==403

def test_diagnostic_download_binds_only_exact_completed_job_descriptor_and_bytes(monkeypatch,tmp_path):
    from camera_platform import lens_scene_plans
    raw=b'authored native output contract';target=tmp_path/'native.bin';target.write_bytes(raw);sha=hashlib.sha256(raw).hexdigest()
    descriptor={'id':sha,'sha256':sha,'bytes':len(raw),'name':'coordinate.npz','media_type':'application/octet-stream',
        'url':f'/api/v1/lens-sensor-quality-studies/known/diagnostic-artifacts/{sha}'}
    monkeypatch.setattr(lens_scene_plans,'diagnostic_export',lambda service,identity:{'artifacts':[descriptor]})
    service=SimpleNamespace(registry=SimpleNamespace(blob_path=lambda checksum:target))
    app=FastAPI();install_routes(app,service)
    with TestClient(app) as client:
        response=client.get(descriptor['url']);assert response.status_code==200
        assert response.content==raw and hashlib.sha256(response.content).hexdigest()==sha
        assert client.get(descriptor['url'].replace('/known/','/other/')).status_code==404
        assert client.get(descriptor['url'].replace(sha,'0'*64)).status_code==404
