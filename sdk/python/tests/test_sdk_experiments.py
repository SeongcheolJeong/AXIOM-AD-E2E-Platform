import hashlib
import json

import httpx
import pytest

from axiom_sdk import AxiomClient


def test_same_api_replay_and_explicit_frozen_experiment_submit():
    requests=[]
    def respond(request):
        requests.append(request)
        return httpx.Response(200,json={'id':'experiment_test'})
    with AxiomClient('http://workspace.test',token='test-token',transport=httpx.MockTransport(respond)) as client:
        client.create_scene_sequence(name='Temporal')
        client.create_replay_spec(name='Replay')
        client.preflight_replay('replay_test')
        client.preview_experiment(name='Pinned',kind='planning_replay')
        client.create_experiment(idempotency_key='create-experiment',name='Pinned')
        assert requests[-1].headers['Idempotency-Key']=='create-experiment'
        client.submit_experiment('experiment_test',preview_hash='a'*64,idempotency_key='run-experiment')
        assert json.loads(requests[-1].content)=={'preview_hash':'a'*64}
        assert requests[-1].headers['Idempotency-Key']=='run-experiment'
        client.cancel_experiment('job_test')
        assert requests[-1].url.path=='/api/v1/experiments/executions/job_test/cancel'
        client.import_driving_sequence('prepared-input.json',name='Retained',rights_note='Isolated research',research_isolated=True)
        assert json.loads(requests[-1].content)['research_isolated'] is True
        assert requests[-1].url.path=='/api/v1/driving-sequences/import'
    assert all(request.url.host=='workspace.test' and request.headers['Authorization']=='Bearer test-token' for request in requests)


def test_exact_source_grants_preflight_and_bounded_project_samples():
    requests=[]
    def respond(request):
        requests.append(request);return httpx.Response(200,json={'id':'source_test'})
    with AxiomClient('http://workspace.test',token='operator-test',transport=httpx.MockTransport(respond)) as client:
        client.grant_recording_source('project_test','source_test',recording_sha256='a'*64,idempotency_key='source-grant')
        assert requests[-1].headers['Idempotency-Key']=='source-grant'
        assert json.loads(requests[-1].content)['recording_sha256']=='a'*64
        client.project_recordings(project_id='project_test',q='approved',limit=20)
        assert requests[-1].url.params['project_id']=='project_test'
        client.project_recording_samples('source_test',2,project_id='project_test',after_ordinal=0,limit=30)
        assert requests[-1].url.params['after_ordinal']=='0' and requests[-1].url.params['limit']=='30'
        client.replay_sources('source_test',project_id='project_test')
        assert requests[-1].url.params['project_id']=='project_test'
        definition={'recording_id':'source_test','ego_ordinals':[1,3]}
        client.preview_scene_sequence(**definition)
        assert json.loads(requests[-1].content)==definition
        client.freeze_scene_sequence(definition,preview_sha256='b'*64)
        assert json.loads(requests[-1].content)=={'definition':definition,'preview_sha256':'b'*64}
        before=len(requests)
        with pytest.raises(ValueError):client.project_recording_samples('source_test',2,from_start=True,after_ordinal=0)
        with pytest.raises(ValueError):client.project_recording_samples('source_test',True,limit=20)
        with pytest.raises(ValueError):client.freeze_scene_sequence(definition,preview_sha256='invalid')
        assert len(requests)==before


def test_stream_native_download_uses_selected_id_and_checks_hash(tmp_path):
    raw=b'large native artifact bytes'
    expected=hashlib.sha256(raw).hexdigest()
    requests=[]
    def respond(request):
        requests.append(request)
        if '/artifacts/' in request.url.path:
            return httpx.Response(200,content=raw)
        return httpx.Response(200,json={'artifacts':[{'id':'artifact_test','sha256':expected,'size_bytes':len(raw),
            'download_url':'https://untrusted.example/download'}]})
    with AxiomClient('http://workspace.test',transport=httpx.MockTransport(respond)) as client:
        target=client.download_experiment_artifact('job_test','artifact_test',tmp_path/'native.bin')
        assert target.read_bytes()==raw
    assert requests[-1].url.path=='/api/v1/experiments/executions/job_test/artifacts/artifact_test'
    assert all(request.url.host=='workspace.test' for request in requests)


def test_corrupted_native_download_preserves_existing_evidence(tmp_path):
    target=tmp_path/'native.bin'
    target.write_bytes(b'preserve original evidence')
    def respond(request):
        if '/artifacts/' in request.url.path:
            return httpx.Response(200,content=b'tampered')
        return httpx.Response(200,json={'artifacts':[{'id':'artifact_test','sha256':'a'*64,'size_bytes':8}]})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValueError,match='SHA-256'):
            client.download_experiment_artifact('job_test','artifact_test',target)
    assert target.read_bytes()==b'preserve original evidence'
    assert not list(tmp_path.glob('.axiom-download-*'))


def test_zero_byte_native_log_download_is_valid_and_checked(tmp_path):
    expected = hashlib.sha256(b'').hexdigest()
    def respond(request):
        if '/artifacts/' in request.url.path:
            return httpx.Response(200, content=b'')
        return httpx.Response(200, json={'artifacts': [dict(id='artifact_log', sha256=expected, size_bytes=0)]})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        path = client.download_experiment_artifact('job_test', 'artifact_log', tmp_path / 'run.log')
    assert path.is_file() and path.read_bytes() == b''
    assert not list(tmp_path.glob('.axiom-download-*'))


def test_granted_recording_stream_download_uses_scoped_id_not_returned_url(tmp_path):
    raw = b'original MCAP bytes for SDK contract'; digest = hashlib.sha256(raw).hexdigest(); requests = []
    def respond(request):
        requests.append(request)
        if request.url.path.endswith('/download'):
            return httpx.Response(200, content=raw, headers={'X-Content-SHA256': digest})
        return httpx.Response(200, json={'id':'recording_test','sha256':digest,'size_bytes':len(raw),
            'download_url':'https://untrusted.example/original.mcap'})
    with AxiomClient('http://workspace.test',token='viewer-test',transport=httpx.MockTransport(respond)) as client:
        assert client.download_project_recording('recording_test',tmp_path/'original.mcap',project_id='project_test').read_bytes() == raw
    assert requests[-1].url.path == '/api/v1/project-recordings/recording_test/download'
    assert all(request.url.host=='workspace.test' and request.url.params['project_id']=='project_test'
               and request.headers['Authorization']=='Bearer viewer-test' for request in requests)


@pytest.mark.parametrize('bad_header', [False,True])
def test_granted_recording_stream_corruption_preserves_destination(tmp_path,bad_header):
    raw = b'original'; digest = hashlib.sha256(raw).hexdigest(); target = tmp_path/'original.mcap'
    target.write_bytes(b'preserve existing original')
    def respond(request):
        if request.url.path.endswith('/download'):
            return httpx.Response(200, content=b'changed!', headers={'X-Content-SHA256': 'a'*64 if bad_header else digest})
        return httpx.Response(200, json={'id':'recording_test','sha256':digest,'size_bytes':8})
    with AxiomClient('http://workspace.test',transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValueError,match='SHA-256'):
            client.download_project_recording('recording_test',target,project_id='project_test')
    assert target.read_bytes() == b'preserve existing original'
    assert not list(tmp_path.glob('.axiom-download-*'))


def test_invalid_id_and_hash_cannot_send_submission():
    with AxiomClient(transport=httpx.MockTransport(lambda _:pytest.fail('unexpected request'))) as client:
        with pytest.raises(ValueError):
            client.submit_experiment('../invalid',preview_hash='a'*64,idempotency_key='explicit')
        with pytest.raises(ValueError):
            client.submit_experiment('experiment_test',preview_hash='invalid',idempotency_key='explicit')
        with pytest.raises(ValueError):
            client.scene_sequence('https://untrusted.example/sequence')
        with pytest.raises(ValueError):
            client.import_driving_sequence('prepared-input.json',name='Source',rights_note='Research',research_isolated=False)

def test_typed_plan_revision_roles_and_hash_verified_assessment_export(tmp_path):
    raw=b'{"scope":"authored_observer_criteria","is_safety_validation":false}'
    expected=hashlib.sha256(raw).hexdigest()
    requests=[]
    def respond(request):
        requests.append(request)
        if request.url.path.endswith('/export'):return httpx.Response(200,content=raw)
        return httpx.Response(200,json={'id':'plan_test','evaluations':[{'id':'eval_test','manifest_hash':expected}]})
    with AxiomClient('http://workspace.test',transport=httpx.MockTransport(respond)) as client:
        client.team_identity();client.create_project_token('default',user_label='reviewer',role='Reviewer')
        assert json.loads(requests[-1].content)=={'user_label':'reviewer','role':'Reviewer'}
        client.create_experiment_verification_plan(idempotency_key='plan-create',name='Bounded criteria')
        assert requests[-1].headers['Idempotency-Key']=='plan-create'
        client.revise_experiment_verification_plan('plan_test',idempotency_key='plan-revise',change_reason='Fixed threshold')
        client.evaluate_experiment_verification_plan('plan_test',plan_revision=2,execution_ids=['job_test'],idempotency_key='capture')
        assert json.loads(requests[-1].content)=={'plan_revision':2,'execution_ids':['job_test']}
        assert client.export_experiment_verification_evaluation('plan_test','eval_test',tmp_path/'assessment.json').read_bytes()==raw
        assert requests[-1].url.path=='/api/v1/experiment-verification-plans/plan_test/evaluations/eval_test/export'
        before=len(requests)
        with pytest.raises(ValueError):client.evaluate_experiment_verification_plan('plan_test',plan_revision=2,execution_ids=['job_test','job_test'],idempotency_key='duplicate')
        with pytest.raises(ValueError):client.evaluate_experiment_verification_plan('plan_test',plan_revision=True,execution_ids=['job_test'],idempotency_key='invalid')
        assert len(requests)==before
