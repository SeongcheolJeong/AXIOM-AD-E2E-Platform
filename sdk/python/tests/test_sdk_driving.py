import httpx
from axiom_sdk import AxiomClient


def test_driving_checks_use_same_configured_api_without_dispatch():
    calls=[]
    def respond(request):
        calls.append(request)
        return httpx.Response(200,json={'status':'authored_api_contract'})
    with AxiomClient('http://selected-workspace.test',transport=httpx.MockTransport(respond)) as client:
        client.driving_policy_capabilities()
        client.preflight_driving_input({'schema_version':'driving-input/1'})
        client.inspect_driving_result({'schema_version':'driving-result/1'})
    assert [r.url.path for r in calls]==['/api/v1/driving-policies/capabilities','/api/v1/driving-policies/preflight','/api/v1/driving-policies/inspect-result']
    assert all(r.url.host=='selected-workspace.test' for r in calls)
