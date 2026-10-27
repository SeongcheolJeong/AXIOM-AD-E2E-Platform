"""Scene catalog/ref transport, without an engine or backend dependency."""
import json
import httpx
import pytest
from axiom_sdk import AxiomClient
from test_sdk_hil_beta import SAVE

SCENE = dict(id='lead_stop_go', revision=1, sha256='a061779a405b1a659f2f2d0eaec68d0b6fa969f9a848388f280e2dadf0b99c75')


def test_scene_catalog_save_and_preset_preview_use_exact_ref():
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'ok': True})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        client.hil_sim_scenes(project_id='project_one')
        client.save_hil_sim_test(**SAVE, scene_profile=SCENE)
        client.preview_hil_sim_preset('input_delay', expected_binding_revision=1, scene_profile=SCENE,
                                     parameters={'delay_ms': 400, 'start_s': 2, 'duration_s': 3})
        client.save_hil_sim_test(**SAVE, scene_profile=None)
        client.preview_hil_sim_preset('input_delay', expected_binding_revision=1, scene_profile=None)
    assert requests[0].url.path == '/api/v1/hil-sim/scenes' and requests[0].url.params['project_id'] == 'project_one'
    assert json.loads(requests[1].content)['scene_profile'] == json.loads(requests[2].content)['scene_profile'] == SCENE
    assert json.loads(requests[2].content)['parameters'] == {'delay_ms': 400, 'start_s': 2, 'duration_s': 3}
    assert 'scene_profile' not in json.loads(requests[3].content)
    assert 'scene_profile' not in json.loads(requests[4].content)


@pytest.mark.parametrize('reference', [{}, {**SCENE, 'id': 'lead_stop'}, {**SCENE, 'revision': 2},
    {**SCENE, 'revision': True}, {**SCENE, 'revision': 1.0}, {**SCENE, 'sha256': 'bad'},
    {**SCENE, 'schema_version': 'hil-scene-profile/1'}])
def test_bad_scene_refs_fail_before_network(reference):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Invalid ref reached network'))) as client:
        with pytest.raises(ValueError):
            client.save_hil_sim_test(**SAVE, scene_profile=reference)
        with pytest.raises(ValueError):
            client.preview_hil_sim_preset('input_delay', expected_binding_revision=1, scene_profile=reference)
