import httpx
import pytest
from axiom_sdk import AxiomClient


def test_story_requests_single_or_ab_on_project_api():
    requests=[]
    def response(request):
        requests.append(request);return httpx.Response(200,json={'status':'available'})
    with AxiomClient(transport=httpx.MockTransport(response)) as client:
        client.object_sim_story('job_a',project_id='project_a')
        client.object_sim_story('job_a','job_b',project_id='project_a')
    assert all(r.method=='GET' and r.url.path=='/api/v1/object-sim/story' for r in requests)
    assert dict(requests[0].url.params)=={'project_id':'project_a','left_execution_id':'job_a'}
    assert dict(requests[1].url.params)=={'project_id':'project_a','left_execution_id':'job_a','right_execution_id':'job_b'}


@pytest.mark.parametrize('args,kwargs',[(('../job',),{}),(('job_a','../job'),{}),(('job_a',),{'project_id':'../project'})])
def test_story_invalid_identity_does_not_reach_network(args,kwargs):
    with AxiomClient(transport=httpx.MockTransport(lambda _:pytest.fail('invalid request'))) as client:
        with pytest.raises(ValueError):client.object_sim_story(*args,**kwargs)
