"""Authoring freezes executable parameters, independently of advertised defaults."""
import json

from ade2e.stack_components import ControlParameters, ProportionalControl
from ade2e.stack_runtime import StackSession
from test_stack_runtime import packet


def request(controller_id='proportional-control'):
    return {'name': 'Normalized stack', 'note': 'Explicit authoring fixture', 'definition': {
        'reference_frame': 'ego',
        'perception': {'id': 'lidar-corridor', 'version': '1', 'parameters': {
            'ground_reference_z_m': 0.0, 'ground_reference_source': 'Authored fixture plane'}},
        'policy': {'id': 'stopping-headway', 'version': '1', 'parameters': {'allow_no_obstacle_observed_motion': True}},
        'controller': {'id': controller_id, 'version': '1', 'parameters': {}}}}


def test_builtin_default_parameters_and_null_ground_are_frozen(client):
    body = request()
    body['definition']['perception']['parameters'] = {}
    response = client.post('/api/v1/stacks', json=body)
    assert response.status_code == 201, response.text
    saved = response.json()
    for role in ('perception', 'policy', 'controller'):
        params = saved['definition'][role]['parameters']
        assert params  # All complete defaults are present in the definition itself.
        metadata = next(item for item in saved['component_metadata'] if item['role'] == role)
        expected = metadata['default_parameters'] | body['definition'][role]['parameters']
        assert params == expected
    assert saved['definition']['perception']['parameters']['ground_reference_z_m'] is None
    exported = client.get(saved['download_url'])
    assert exported.status_code == 200
    assert json.loads(exported.content)['definition'] == saved['definition']
    assert body['definition']['perception']['parameters'] == {}  # No mutation of the authoring request.


class HiddenDefaultController(ProportionalControl):
    component_id = 'plugin-hidden-default'
    hidden_default = .4

    def validate_parameters(self, parameters):
        # The advertised default remains .2; actual omitted-field behavior differs.
        return ControlParameters.model_validate({'throttle_gain': self.hidden_default, **parameters}).model_dump(mode='json')


def test_custom_validator_default_drift_cannot_change_old_revision_execution(client):
    registry = client.app.state.stack_registry
    component = HiddenDefaultController()
    registry.register(component)
    body = request(component.component_id)
    response = client.post('/api/v1/stacks', json=body)
    assert response.status_code == 201, response.text
    first = response.json()
    assert first['definition']['controller']['parameters']['throttle_gain'] == .4
    assert first['component_metadata'][2]['default_parameters']['throttle_gain'] == .2
    before = client.get(first['download_url']).content
    p, raw = packet(speed=4.)
    assert StackSession(first['definition'], registry).step(p, raw).control.throttle == .4

    component.hidden_default = .05  # Same advertised metadata, changed omitted-field default.
    assert StackSession(first['definition'], registry).step(p, raw).control.throttle == .4
    revised = client.post(f"/api/v1/stacks/{first['id']}/revisions", json={**body, 'expected_revision': 1})
    assert revised.status_code == 201, revised.text
    second = revised.json()
    assert second['definition']['controller']['parameters']['throttle_gain'] == .05
    assert StackSession(second['definition'], registry).step(p, raw).control.throttle == .05
    assert first['manifest_hash'] != second['manifest_hash']
    assert client.get(first['download_url']).content == before

    # Archived reads/export do not consult today's registry or normalize again.
    del registry._components[(component.component_id, component.metadata().version)]
    assert client.get(f"/api/v1/stacks/{first['id']}?revision=1").status_code == 200
    assert client.get(first['download_url']).content == before


def test_unstable_normalizer_is_rejected_before_any_revision_write(client):
    class Unstable(ProportionalControl):
        component_id = 'unstable-normalizer'
        def validate_parameters(self, parameters):
            normalized = super().validate_parameters(parameters)
            normalized['throttle_gain'] += .01
            return normalized
    client.app.state.stack_registry.register(Unstable())
    response = client.post('/api/v1/stacks', json=request('unstable-normalizer'))
    assert response.status_code == 422, response.text
    assert 'normalization' in response.json()['detail']
    assert client.get('/api/v1/stacks').json() == []


def test_metadata_change_during_normalization_cannot_create_snapshot(client):
    class Drifting(ProportionalControl):
        component_id = 'drifting-authoring'
        drift = False
        def metadata(self):
            value = super().metadata()
            if self.drift: value.version = '2'
            return value
        def validate_parameters(self, parameters):
            self.drift = True
            return super().validate_parameters(parameters)
    client.app.state.stack_registry.register(Drifting())
    response = client.post('/api/v1/stacks', json=request('drifting-authoring'))
    assert response.status_code == 422, response.text
    assert client.get('/api/v1/stacks').json() == []


def test_components_openapi_and_response_have_concrete_model(client):
    document = client.get('/openapi.json').json()
    schema = document['paths']['/api/v1/stack-components']['get']['responses']['200']['content']['application/json']['schema']
    assert schema['type'] == 'array'
    assert schema['items']['$ref'] == '#/components/schemas/StackComponentMetadata'
    response = client.get('/api/v1/stack-components')
    assert response.status_code == 200
    assert {item['role'] for item in response.json()} == {'perception', 'policy', 'controller'}
