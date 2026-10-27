"""Wire contract checks only; native execution is verified separately."""
import json
import httpx
import pytest
from axiom_sdk import AxiomClient


def test_network_methods_preserve_scope_exact_refs_and_idempotency():
    requests=[]
    def respond(request):
        requests.append(request)
        return httpx.Response(200,json={'id':'job_network'})
    with AxiomClient('http://workspace.test',token='test-token',transport=httpx.MockTransport(respond)) as sdk:
        sdk.vecu_network_catalog(project_id='project_test')
        assert requests[-1].url.path=='/api/v1/virtual-ecu-network/catalog'
        assert requests[-1].url.params['project_id']=='project_test'
        sdk.vecu_network_readiness(project_id='project_test')
        assert requests[-1].url.params['profile_id']=='actual_silkit_autoas_trampoline'
        sdk.save_vecu_network_record('networks',{'name':'Four ECUs','project_id':'project_test'},idempotency_key='net-save')
        assert requests[-1].headers['Idempotency-Key']=='net-save'
        sdk.vecu_network_record('tests','test_one',revision=4,project_id='project_test')
        assert requests[-1].url.params['revision']=='4'
        test=dict(id='test_one',revision=4,sha256='a'*64,project_id='project_test')
        sdk.preview_vecu_network_test(test)
        assert json.loads(requests[-1].content)=={k:v for k,v in test.items() if k!='id'}
        sdk.execute_vecu_network_test(test,idempotency_key='net-run')
        assert requests[-1].url.path.endswith('/tests/test_one/executions')
        assert requests[-1].headers['Idempotency-Key']=='net-run'
        sdk.vecu_network_execution('job_network',project_id='project_test')
        sdk.vecu_network_causality('job_network',project_id='project_test')
        assert requests[-1].url.path.endswith('/executions/job_network/causality') and requests[-1].url.params['project_id']=='project_test'
        sdk.cancel_vecu_network_execution('job_network',project_id='project_test')
        assert requests[-1].method=='POST' and requests[-1].url.params['project_id']=='project_test'
        assert sdk.export_vecu_network_execution('job_network',project_id='project_test')
        sdk.vecu_network_executions(project_id='project_test')
        sdk.vecu_network_records('networks',project_id='project_test')
    assert all(r.url.host=='workspace.test' and r.headers['Authorization']=='Bearer test-token' for r in requests)


def test_unsupported_collection_or_invalid_identity_never_sent():
    with AxiomClient(transport=httpx.MockTransport(lambda _:pytest.fail('unexpected request'))) as sdk:
        with pytest.raises(ValueError):sdk.vecu_network_records('commands')
        with pytest.raises(ValueError):sdk.save_vecu_network_record('shell',{},idempotency_key='bad')
        with pytest.raises(ValueError):sdk.vecu_network_execution('../untrusted')
        with pytest.raises(ValueError):sdk.vecu_network_readiness(profile_id='../../command')
