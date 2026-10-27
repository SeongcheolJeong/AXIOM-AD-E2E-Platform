import json,httpx,pytest
from axiom_sdk import AxiomClient

def test_hil_contract_scope_revision_idempotency():
    requests=[]
    def respond(request):requests.append(request);return httpx.Response(200,json={'items':[]})
    with AxiomClient('http://workspace.test',token='test',transport=httpx.MockTransport(respond)) as sdk:
        sdk.hil_bridge_profiles(project_id='p')
        sdk.hil_bridge_readiness(project_id='p')
        sdk.save_hil_binding({'name':'two ecus','project_id':'p'},idempotency_key='save')
        assert requests[-1].headers['Idempotency-Key']=='save'
        binding=dict(id='b',revision=3,sha256='a'*64,project_id='p')
        sdk.preview_hil_binding(binding)
        assert json.loads(requests[-1].content)=={k:binding[k] for k in ('project_id','revision','sha256')}
        sdk.execute_hil_binding(binding,idempotency_key='exec')
        sdk.hil_binding('b',revision=3,project_id='p')
        sdk.hil_bindings(project_id='p');sdk.hil_bridge_executions(project_id='p')
        sdk.hil_bridge_execution('job_b',project_id='p');sdk.hil_bridge_causality('job_b',project_id='p')
        sdk.cancel_hil_bridge_execution('job_b',project_id='p');sdk.export_hil_bridge_execution('job_b',project_id='p')
    assert all(r.url.path.startswith('/api/v1/hil-bridge/') for r in requests)
    assert requests[-1].url.params['project_id']=='p'

def test_public_api_never_accepts_path_as_identity():
    with AxiomClient(transport=httpx.MockTransport(lambda _:pytest.fail('unexpected request'))) as sdk:
        with pytest.raises(ValueError):sdk.hil_binding('../driver.py',revision=1)


def test_multiple_model_contract_preserves_each_source_and_schedule():
    sent=[]
    def respond(r):sent.append(r);return httpx.Response(201,json={'id':'binding'})
    with AxiomClient(transport=httpx.MockTransport(respond)) as sdk:
        source=dict(id='s',revision=1,sha256='a'*64,project_id='p')
        members=[dict(pair=dict(id=n,revision=1,sha256='b'*64,project_id='p'),runs={s:{'id':f'job_{n}_{s}'} for s in ('baseline','faulted')},start_ms=3000+i*50) for i,n in enumerate(('power','fpga'))]
        sdk.save_hil_model_set(source,members,idempotency_key='dual')
        body=json.loads(sent[-1].content)
        assert 'model_coupling' not in body and len(body['model_couplings'])==2
        assert [c['start_ms'] for c in body['model_couplings']]==[3000,3050]
        assert sent[-1].headers['Idempotency-Key']=='dual'
        with pytest.raises(ValueError):sdk.save_hil_model_set(source,members[:1],idempotency_key='invalid')
