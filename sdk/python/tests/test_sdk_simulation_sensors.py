"""Exact configuration revision/key transmission and verified immutable export."""
import hashlib
import json

import httpx
import pytest
from axiom_sdk import AxiomClient
from axiom_sdk import cli


@pytest.mark.parametrize('revise',[False,True])
def test_write_requires_key_and_preserves_complete_body_and_retry_identity(revise):
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(201,json={'id':'cfg','revision':2 if revise else 1})
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        call=client.revise_simulation_sensor_configuration if revise else client.create_simulation_sensor_configuration
        args=('cfg',) if revise else ()
        kw={'expected_revision':1} if revise else {}
        for _ in range(2):call(*args,**kw,idempotency_key='same-key',name='Declared',note='Unknown calibration',parameters={'range':70.})
        with pytest.raises(ValueError):call(*args,**kw,idempotency_key='',name='No send')
    assert len(requests)==2 and requests[0].content==requests[1].content
    assert requests[0].headers['Idempotency-Key']=='same-key'
    assert requests[0].url.path=='/api/v1/simulation-sensor-configurations'+('/cfg/revisions' if revise else '')
    assert json.loads(requests[0].content)['parameters']=={'range':70.}


def test_export_pins_observed_revision_and_checks_original_sha_before_write(tmp_path):
    content=b'{"id":"cfg","revision":2}'
    paths=[]
    def handler(request):
        paths.append(str(request.url))
        if request.url.path.endswith('/export'):
            assert request.url.params['revision']=='2'
            return httpx.Response(200,content=content)
        return httpx.Response(200,json={'id':'cfg','revision':2,'manifest_size_bytes':len(content),'manifest_hash':hashlib.sha256(content).hexdigest(),
            'download_url':'https://evil.invalid/not-used'})
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        path=client.export_simulation_sensor_configuration('cfg',tmp_path/'saved.json')
    assert path.read_bytes()==content
    assert all('evil' not in path for path in paths)


def test_export_corruption_and_revision_mismatch_write_nothing(tmp_path):
    def handler(request):
        if request.url.path.endswith('/export'):return httpx.Response(200,content=b'corrupted')
        return httpx.Response(200,json={'id':'cfg','revision':2,'manifest_size_bytes':1,'manifest_hash':'0'*64})
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError,match='SHA'):client.export_simulation_sensor_configuration('cfg',tmp_path/'bad')
        with pytest.raises(ValueError,match='revision'):client.get_simulation_sensor_configuration('cfg',revision=1)
    assert not (tmp_path/'bad').exists()


def test_read_routes_and_filters_are_read_only():
    seen=[]
    def handler(request):
        seen.append((request.method,request.url.path,str(request.url.query)))
        return httpx.Response(200,json=[])
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        client.simulation_sensor_capabilities(adapter_id='carla')
        client.simulation_sensor_configurations()
        client.simulation_sensor_configuration_revisions('cfg')
    assert all(item[0]=='GET' for item in seen)
    assert seen[-1][1].endswith('/cfg/revisions')
    assert 'adapter_id=carla' in seen[0][2]


def test_cli_uses_explicit_key_revision_and_complete_file(monkeypatch,tmp_path,capsys):
    source=tmp_path/'definition.json';source.write_text(json.dumps({'name':'Declared','note':'Uncalibrated','parameters':{'range':70.}}))
    seen=[]
    def handler(request):seen.append(request);return httpx.Response(201,json={'id':'cfg','revision':2})
    monkeypatch.setattr(cli,'AxiomClient',lambda *a,**kw:AxiomClient(*a,transport=httpx.MockTransport(handler),**kw))
    monkeypatch.setattr('sys.argv',['axiom','revise-simulation-sensor-configuration','cfg',str(source),'--expected-revision','1','--idempotency-key','fixed'])
    cli.main()
    assert json.loads(capsys.readouterr().out)=={'id':'cfg','revision':2}
    assert seen[0].headers['Idempotency-Key']=='fixed' and json.loads(seen[0].content)['expected_revision']==1
