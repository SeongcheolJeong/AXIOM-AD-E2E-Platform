"""Shared API transport and content-integrity checks, not native execution."""
import hashlib
import json
import httpx
import pytest
from axiom_sdk import AxiomClient


def test_yaml_and_catalog_use_same_project_api_and_preserve_original_text():
    requests=[]
    original='# Authored comment\nname: test\n'
    def respond(request):
        requests.append(request)
        if request.url.path.endswith('/export'):
            return httpx.Response(200,text=original,headers={'X-Content-SHA256':hashlib.sha256(original.encode()).hexdigest()})
        return httpx.Response(200,json={'accepted':True})
    with AxiomClient('http://workspace.test',token='project-engineer',transport=httpx.MockTransport(respond)) as client:
        client.validate_scenario_yaml(original,project_id='project_a')
        client.import_scenario_yaml(original,project_id='project_a',idempotency_key='source-key')
        client.create_scenario_v2({'schema_version':'scenario-v2/2'},project_id='project_a',idempotency_key='create',source_yaml=original)
        client.revise_scenario_v2('scenario2_a',{},expected_revision=1,idempotency_key='revise')
        assert client.export_scenario_yaml('scenario2_a',revision=1,original=True)==original
        client.simulation_catalog('maps',project_id='project_a')
        client.simulation_catalog_revision('maps','town01',project_id='project_a',revision=2)
        client.register_simulation_catalog('controllers','control_a',{'resource_kind':'controller'},expected_revision=1,project_id='project_a')
        client.plan_city_route('town01',project_id='project_a',revision=2,sha256='a'*64,start={'x':1,'y':2},goal={'x':3,'y':4})
    assert all(r.headers['Authorization']=='Bearer project-engineer' for r in requests)
    assert requests[0].url.path=='/api/v1/scenario-v2/yaml/validate'
    assert json.loads(requests[1].content)=={'yaml':original,'project_id':'project_a'}
    assert requests[1].headers['Idempotency-Key']=='source-key'
    assert requests[4].url.params['format']=='source_yaml'
    assert requests[4].url.params['revision']=='1'
    assert requests[-1].url.path=='/api/v1/maps/town01/route'
    assert json.loads(requests[-1].content)['sha256']=='a'*64


def test_export_refuses_modified_yaml_and_bad_local_parameters_make_no_request():
    with AxiomClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,text='changed',headers={'X-Content-SHA256':'0'*64}))) as client:
        with pytest.raises(ValueError,match='hash mismatch'):
            client.export_scenario_yaml('scenario2_a')
    with AxiomClient(transport=httpx.MockTransport(lambda _:pytest.fail('Unexpected HTTP request'))) as client:
        with pytest.raises(ValueError):client.simulation_catalog('../secret')
        with pytest.raises(ValueError):client.simulation_catalog('maps',limit=0)
        with pytest.raises(ValueError):client.simulation_catalog_revision('maps','town01',revision=0)
        with pytest.raises(ValueError):client.revise_scenario_v2('scenario2_a',{},expected_revision=True,idempotency_key='bad')
        with pytest.raises(ValueError):client.export_scenario_yaml('scenario2_a',revision=-1)
        with pytest.raises(ValueError):client.plan_city_route('town01',revision=1,sha256='not-a-hash',start={},goal={})
