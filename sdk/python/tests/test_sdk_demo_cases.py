import httpx
import pytest
from axiom_sdk import AxiomClient


def test_readonly_project_gallery_contract():
    requests=[]
    payload={'schema_version':'demo-cases/1','project_id':'p','items':[], 'counts':{'ready':0}}
    def respond(request):
        requests.append(request)
        return httpx.Response(200,json=payload)
    with AxiomClient(token='test-token',transport=httpx.MockTransport(respond)) as client:
        assert client.demo_cases(project_id='p')==payload
    assert len(requests)==1 and requests[0].method=='GET'
    assert requests[0].url.path=='/api/v1/demo-cases' and requests[0].url.params['project_id']=='p'
    assert requests[0].headers['Authorization']=='Bearer test-token'


@pytest.mark.parametrize('project', ['', '../p', 'p?other=x', 42, None])
def test_bad_project_fails_before_network(project):
    with AxiomClient(transport=httpx.MockTransport(lambda _:pytest.fail('unexpected network'))) as client:
        with pytest.raises(ValueError):client.demo_cases(project_id=project)
