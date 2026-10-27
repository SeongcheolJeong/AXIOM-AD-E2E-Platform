"""Public build/package contract; no local compilation is performed here."""
import json
import httpx
import pytest
from axiom_sdk import AxiomClient


def test_vecu_build_and_package_ab_use_same_scoped_api():
    requests=[]
    def respond(request):
        requests.append(request)
        return httpx.Response(200,json={'id':'job_compiled'})
    with AxiomClient('http://workspace.test',token='test-token',transport=httpx.MockTransport(respond)) as sdk:
        sdk.build_vecu_package(name='C++ A',source_id='cpp-immediate',project_id='project_test',idempotency_key='build-A')
        assert requests[-1].url.path=='/api/v1/virtual-ecu/builds'
        assert json.loads(requests[-1].content)==dict(name='C++ A',source_id='cpp-immediate',project_id='project_test')
        assert requests[-1].headers['Idempotency-Key']=='build-A'
        sdk.vecu_builds(project_id='project_test')
        assert requests[-1].url.params['project_id']=='project_test'
        sdk.vecu_build('job_compiled',project_id='project_test')
        assert requests[-1].url.path=='/api/v1/virtual-ecu/builds/job_compiled'
        assert requests[-1].url.params['project_id']=='project_test'
        sdk.save_vecu_record('packages',dict(name='Built A',project_id='project_test',build_execution_id='job_compiled'),idempotency_key='register-A')
        assert json.loads(requests[-1].content)['build_execution_id']=='job_compiled'
        reference=dict(id='vecu_A',revision=1,sha256='a'*64)
        spec=dict(name='Package A/B',project_id='project_test',comparison='package_ab',package_ref=reference,
            variant_package_ref=reference|{'id':'vecu_B'},input_ref=reference|{'id':'input'},fault_enabled=False)
        sdk.save_vecu_record('tests',spec,idempotency_key='test-A-B')
        assert json.loads(requests[-1].content)==spec
    assert all(r.url.host=='workspace.test' and r.headers['Authorization']=='Bearer test-token' for r in requests)


def test_invalid_vecu_build_source_or_identity_never_sends_request():
    with AxiomClient(transport=httpx.MockTransport(lambda _:pytest.fail('unexpected request'))) as sdk:
        with pytest.raises(ValueError):sdk.build_vecu_package(name='x',source_id='unregistered',idempotency_key='bad')
        with pytest.raises(ValueError):sdk.vecu_build('../untrusted')
        with pytest.raises(ValueError):sdk.vecu_builds(project_id='../other')
