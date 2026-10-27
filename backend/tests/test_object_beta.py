"""Authored contract fixtures; no CARLA execution is represented by these tests."""
import copy
import json
import pytest
from test_object_demo import env, save, bind, execute
from test_scenario_city import pose, asset_definition, reference


def preview(client, identity='obstacle_bypass', **body):
    return client.post('/api/v1/object-sim/presets/' + identity + '/preview', json=body)


def lead_template(scenario):
    scenario = copy.deepcopy(scenario)
    lead = scenario['actors'][2]
    lead.update(id='lead', pose=pose(50), route=[pose(50), pose(90)], speed_mps=3,
        behavior={'actions': [{'id': 'lead_wait', 'kind': 'wait', 'trigger': {'kind': 'time', 'value': 2},
                               'duration_s': 4, 'timeout_s': 10}]})
    scenario['actors'] = [scenario['actors'][0], lead]
    return scenario


def bind_lead(client, scenario):
    ref = save(client, lead_template(scenario))
    response = client.post('/api/v1/object-sim/presets/lead_stop_start/bind', json={
        'scenario_ref': ref, 'actor_roles': {'ego': 'ego', 'lead': 'lead'}})
    assert response.status_code == 200, response.text
    return ref


def test_four_beta_templates_are_explicitly_unbound_until_pinned(env):
    _, client, _, _, scenario = env
    items = client.get('/api/v1/object-sim/presets').json()['items']
    assert {item['id'] for item in items if item['beta_template']} == {
        'lead_stop_start', 'cut_in', 'urban_left_turn', 'urban_right_turn'}
    assert all(item['status'] == 'unbound' for item in items)
    ref = bind_lead(client, scenario)
    value = preview(client, 'lead_stop_start', parameters={'lead_wait_s': 5, 'lead_start_s': 3, 'lead_speed_mps': 5}).json()
    assert value['scenario_ref'] == ref
    lead = value['scenario']['actors'][1]
    assert lead['speed_mps'] == 5
    assert lead['behavior']['actions'][0]['trigger']['value'] == 3
    assert lead['behavior']['actions'][0]['duration_s'] == 5
    assert value['validation']['native_spawn_status'] == 'not_checked'
    assert value['editable']['actors'][1]['route_editable']
    assert value['editable']['actors'][1]['asset']['bounds_source'] == 'unknown'


def test_actor_speed_start_and_destination_edits_use_directed_lane_graph(env):
    _, client, _, _, scenario = env
    bind(client, save(client, scenario))
    result = preview(client, actor_edits={'ego': {'speed_mps': 6, 'pose': pose(34, .2), 'destination': pose(79, .2)}})
    assert result.status_code == 200, result.text
    actor = result.json()['scenario']['actors'][0]
    assert actor['speed_mps'] == 6 and actor['pose'] == pose(35)
    assert actor['destination'] == pose(80) and actor['route'][-1] == pose(80)
    for edits, field in [({'ego': {'destination': pose(20)}}, 'destination'),
                         ({'parked': {'speed_mps': 1}}, 'speed_mps'),
                         ({'missing': {'speed_mps': 1}}, 'actor')]:
        response = preview(client, actor_edits=edits)
        assert response.status_code == 422
        assert response.json()['detail']['issues'][0]['field'] == field


def test_saved_draft_resume_retains_pin_and_rejects_changed_template_structure(env):
    _, client, _, _, scenario = env
    ref = bind_lead(client, scenario)
    first = preview(client, 'lead_stop_start', parameters={'lead_wait_s': 0}).json()
    draft = save(client, first['scenario'], 'draft')
    continued = preview(client, 'lead_stop_start', source_scenario_ref=draft, parameters={'lead_wait_s': 3})
    assert continued.status_code == 200, continued.text
    assert continued.json()['source_scenario_ref'] == draft
    assert continued.json()['scenario_ref'] == ref
    assert continued.json()['scenario']['actors'][1]['behavior']['actions'][0]['duration_s'] == 3
    wrong = copy.deepcopy(first['scenario']); wrong['seed'] += 1
    changed = save(client, wrong, 'wrong')
    assert preview(client, 'lead_stop_start', source_scenario_ref=changed).status_code == 422
    assert preview(client, 'lead_stop_start', source_scenario_ref={**draft, 'manifest_hash': '0' * 64}).status_code == 409
    # Continuing a stored non-time trigger must not silently revert it to time.
    distance = copy.deepcopy(continued.json()['scenario'])
    distance['actors'][1]['behavior']['actions'][0]['trigger'] = {
        'kind': 'distance', 'value': 10, 'target_actor_id': 'ego'}
    distance_ref = save(client, distance, 'distance')
    restored = preview(client, 'lead_stop_start', source_scenario_ref=distance_ref)
    assert restored.status_code == 200, restored.text
    assert restored.json()['scenario']['actors'][1]['behavior']['actions'][0]['trigger']['kind'] == 'distance'


def test_actions_validate_actor_targets_lane_nodes_time_and_risk(env):
    _, client, _, _, scenario = env
    bind_lead(client, scenario)
    actions = [{'id': 'lead_wait', 'kind': 'wait', 'trigger': {'kind': 'distance', 'value': 15, 'target_actor_id': 'ego'},
                'duration_s': 3, 'timeout_s': 10}]
    value = preview(client, 'lead_stop_start', actor_edits={'lead': {'actions': actions}})
    assert value.status_code == 200, value.text
    for update, field in [({'trigger': {'kind': 'distance', 'value': 1, 'target_actor_id': 'missing'}}, 'trigger.target_actor_id'),
                          ({'trigger': {'kind': 'time', 'value': 30}}, 'trigger.value'),
                          ({'target_lane_node_id': 'missing'}, 'target_lane_node_id')]:
        response = preview(client, 'lead_stop_start', actor_edits={'lead': {'actions': [{**actions[0], **update}]}})
        assert response.status_code == 422, response.text
        assert response.json()['detail']['issues'][0]['field'].endswith(field)
    cut = {'id': 'risk', 'kind': 'cut_in', 'target_lane_node_id': '0:12'}
    assert preview(client, 'lead_stop_start', actor_edits={'lead': {'actions': [cut]}}).status_code == 422


def test_cut_in_binding_requires_explicit_risk_and_existing_reactive_actor(env):
    _, client, _, _, scenario = env
    scenario = lead_template(scenario); actor = scenario['actors'][1]
    actor.update(id='intruder', pose=pose(40, 3.5), route=[pose(40, 3.5), pose(90, 3.5)],
        behavior={'risk_mode': True, 'actions': [{'id': 'cut', 'kind': 'cut_in',
            'target_lane_node_id': '0:12', 'trigger': {'kind': 'time', 'value': 2}}]})
    ref = save(client, scenario)
    response = client.post('/api/v1/object-sim/presets/cut_in/bind', json={
        'scenario_ref': ref, 'actor_roles': {'ego': 'ego', 'intruder': 'intruder'}})
    assert response.status_code == 200, response.text
    result = preview(client, 'cut_in', parameters={'cut_in_start_s': 4, 'intruder_speed_mps': 5})
    assert result.status_code == 200, result.text
    assert result.json()['scenario']['actors'][1]['behavior']['actions'][0]['trigger']['value'] == 4
    assert result.json()['editable']['actors'][1]['actions']['risk_mode'] is True


def test_left_turn_requires_native_map_and_reactive_crossing_vehicle(env):
    from test_object_demo import urban_scenario
    _, client, _, _, scenario = env
    urban = urban_scenario(client, scenario)
    crossing = copy.deepcopy(urban['actors'][0]); crossing.update(id='crossing', role='other', pose=pose(60), route=[pose(60), pose(95)])
    urban['actors'].append(crossing)
    ref = save(client, urban)
    result = client.post('/api/v1/object-sim/presets/urban_left_turn/bind', json={
        'scenario_ref': ref, 'actor_roles': {'ego': 'ego', 'crossing': 'crossing'}})
    assert result.status_code == 200, result.text
    changed = preview(client, 'urban_left_turn', parameters={'crossing_speed_mps': 6})
    assert changed.status_code == 200, changed.text
    assert changed.json()['scenario']['actors'][1]['speed_mps'] == 6
    # The catalog name is not acceptance evidence for an actual left turn.
    assert changed.json()['execution_status'] == 'not_executed'


def test_catalog_bounds_overlap_blocks_initial_pose_and_unknown_bounds_are_explicit(env):
    _, client, _, _, scenario = env
    definition = asset_definition(); definition['dimensions'] = {'length_m': 4.5, 'width_m': 1.8, 'height_m': 1.5}
    asset = client.post('/api/v1/assets', json={'id': 'known-vehicle', 'definition': definition})
    for actor in scenario['actors']:
        actor['asset'] = reference(asset.json())
    bind(client, save(client, scenario))
    assert preview(client).json()['validation']['initial_overlap']['status'] == 'checked'
    response = preview(client, actor_edits={'parked': {'pose': pose(30)}})
    assert response.status_code == 422, response.text
    assert response.json()['detail']['issues'][0]['code'] == 'initial_overlap'


def observation(client, execution_id, ref, key='observation', **fields):
    return client.post('/api/v1/object-sim/observations', headers={'Idempotency-Key': key}, json={
        'execution_id': execution_id, 'scenario_ref': ref, 'time_s': 5, 'title': 'Inspect interaction',
        'note': 'Behavior was visible but the ego remained stopped.', 'kind': 'issue',
        'behavior_exercised': 'observed', 'ego_outcome': 'stopped', **fields})


def test_observation_preserves_executed_pin_and_separate_manual_assessments(env):
    _, client, _, _, scenario = env
    ref = save(client, scenario); execution_id = execute(env, ref)
    response = observation(client, execution_id, ref)
    assert response.status_code == 201, response.text
    value = response.json()
    assert value['assessment_source'] == 'user_observation'
    assert value['verification_status'] == 'not_a_test_verdict'
    assert value['scenario_ref'] == ref and value['execution']['id'] == execution_id
    assert value['behavior_exercised'] == 'observed' and value['ego_outcome'] == 'stopped'
    assert observation(client, execution_id, ref).json() == value
    assert observation(client, execution_id, ref, note='Changed with reused key').status_code == 409
    assert observation(client, execution_id, {**ref, 'revision': 2}, key='wrong').status_code == 409
    assert observation(client, execution_id, ref, key='late', time_s=30).status_code == 422
    listed = client.get('/api/v1/object-sim/observations', params={'execution_id': execution_id}).json()
    assert listed['items'] == [value]
    assert 'request_hash' not in value and 'token' not in json.dumps(value)


def test_observation_denies_other_project_viewer_pending_and_credentials(env):
    _, client, _, _, scenario = env
    ref = save(client, scenario); execution_id = execute(env, ref, complete=False)
    assert observation(client, execution_id, ref).status_code == 422
    assert observation(client, execution_id, ref, note='Authorization: Bearer secret-not-to-save').status_code == 422
    viewer = client.post('/api/v1/projects/default/tokens', json={'role': 'Viewer', 'user_label': 'reader'}).json()['token']
    project = client.post('/api/v1/projects', json={'name': 'Other'}).json()['id']
    other = client.post(f'/api/v1/projects/{project}/tokens', json={'role': 'Engineer', 'user_label': 'other'}).json()['token']
    client.headers['Authorization'] = 'Bearer ' + viewer
    assert observation(client, execution_id, ref).status_code == 403
    client.headers['Authorization'] = 'Bearer ' + other
    assert observation(client, execution_id, ref, project_id=project).status_code == 404
    assert client.get('/api/v1/object-sim/observations', params={'project_id': project, 'execution_id': execution_id}).status_code == 404
