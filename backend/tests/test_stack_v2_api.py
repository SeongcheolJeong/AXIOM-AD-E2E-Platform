"""Explicit v2 authoring/queue boundaries and unchanged saved v1 contracts.

MCAP fixtures are authored inputs; the golden contract was saved from an earlier
actual CARLA run. These tests do not execute a simulator or validate a vehicle.
"""
from copy import deepcopy
import base64
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from ade2e import stack_schemas, stack_replays
from ade2e.job_models import JobRow
from ade2e.service import canonical_bytes, digest
from ade2e.stack_runtime import StackRegistry, StackSession
from ade2e.stack_schemas import validate_stack_output
from ade2e.stack_replay_schemas import StackReplayTick
from ade2e.stacks import validate_stack_snapshot
from test_stack_replays import context, submit, execute, definition


def v2_body():
    body = definition()
    body['name'] = 'Authored explicit kernel 2'
    body['definition']['schema_version'] = '2'
    body['definition']['kernel_version'] = 'stack-kernel/2'
    for role in ('perception', 'policy', 'controller'):
        body['definition'][role]['version'] = '2'
        body['definition'][role]['parameters'] = {}
    return body


def v2_context(client, *, body=None, **options):
    ctx = context(client, **options)
    saved = client.post('/api/v1/stacks', json=body or v2_body())
    assert saved.status_code == 201, saved.text
    request = ctx[2] | {'stack_id': saved.json()['id'], 'stack_revision': 1}
    return (ctx[0], ctx[1], request, saved.json(), ctx[4], ctx[5])


def test_saved_actual_v1_canonical_bytes_and_all_unchanged_schema_hashes():
    fixture = json.loads((Path(__file__).parent/'fixtures/stack_v1_saved_contract.json').read_text())
    stack = validate_stack_snapshot(fixture['stack'])
    output = validate_stack_output(fixture['output'])
    assert stack.model_dump(mode='json') == fixture['stack']
    assert output.model_dump(mode='json') == fixture['output']
    assert digest(stack.model_dump(mode='json')) == fixture['legacy_stack_canonical_sha256']
    assert digest(output.model_dump(mode='json')) == fixture['legacy_output_canonical_sha256']
    assert 'kernel_version' not in stack.definition.model_dump(mode='json')
    for name, expected in fixture['legacy_json_schema_sha256'].items():
        assert digest(getattr(stack_schemas, name).model_json_schema()) == expected, name
    raw = base64.b64decode(fixture['consumed_xyz_base64'], validate=True)
    assert hashlib.sha256(raw).hexdigest() == fixture['consumed_xyz_sha256']
    recalculated = StackSession(stack.definition, StackRegistry(load_plugins=False)).step(fixture['packet'], raw)
    assert recalculated.model_dump(mode='json', exclude={'elapsed_ms'}) == {
        key: value for key, value in fixture['output'].items() if key != 'elapsed_ms'}


def test_early_legacy_tick_omitted_optional_field_retains_exact_payload_and_hash():
    fixture = json.loads((Path(__file__).parent/'fixtures/stack_v1_early_tick.json').read_text())
    original = fixture['tick']
    assert 'supported_distance_m' not in original['output']['observation']
    tick = StackReplayTick.model_validate(original)
    assert tick.model_dump(mode='json') == original
    assert digest(tick.model_dump(mode='json')) == fixture['payload_hash']
    assert stack_replays._tick_hash(tick) == original['output_hash']
    assert 'supported_distance_m' not in tick.output['observation']
    # A consumer cannot mutate the source dictionary through the validated model.
    tick.output['warnings'].append('local inspection note')
    assert 'local inspection note' not in original['output']['warnings']


@pytest.mark.parametrize('changes', [
    {'schema_version': '2'}, {'kernel_version': 'stack-kernel/2'},
    {'schema_version': '2', 'kernel_version': 'stack-kernel/1'},
    {'schema_version': '1', 'kernel_version': 'stack-kernel/2'},
    {'schema_version': 2, 'kernel_version': 'stack-kernel/2'},
    {'schema_version': None}, {'schema_version': '3'},
])
def test_implicit_unknown_or_mismatched_kernel_rejected_without_authoring(client, changes):
    body = definition()
    body['definition'].update(changes)
    result = client.post('/api/v1/stacks', json=body)
    assert result.status_code == 422, result.text
    assert client.get('/api/v1/stacks').json() == []


def test_explicit_kernel_revision_normalizes_params_and_preserves_legacy(client):
    old = client.post('/api/v1/stacks', json=definition()).json()
    old_bytes = client.get(old['download_url']).content
    second = client.post('/api/v1/stacks/' + old['id'] + '/revisions', json=v2_body() | {'expected_revision': 1})
    assert second.status_code == 201, second.text
    saved = second.json()
    assert saved['schema_version'] == '1'
    assert saved['definition']['schema_version'] == '2'
    assert saved['kernel_version'] == saved['definition']['kernel_version'] == 'stack-kernel/2'
    for role in ('perception','policy','controller'):
        chosen = saved['definition'][role]
        component = client.app.state.stack_registry.get(chosen['id'], chosen['version'])
        assert chosen['parameters'] == component.validate_parameters({})
        assert component.metadata().implementation['kernel'] == 'stack-kernel/2'
    assert saved['definition']['perception']['parameters']['ground_reference_z_m'] is None
    assert client.get(old['download_url']).content == old_bytes
    assert client.get(f"/api/v1/stacks/{old['id']}?revision=1").json() == old
    exported = client.get(saved['download_url']).content
    assert hashlib.sha256(exported).hexdigest() == saved['manifest_hash']
    assert validate_stack_snapshot(saved).kernel_version == 'stack-kernel/2'
    for kernel in ('stack-kernel/1', 'stack-kernel/unknown', None):
        with pytest.raises(ValueError):
            validate_stack_snapshot(saved | {'kernel_version': kernel})
    assert client.post('/api/v1/stacks/' + old['id'] + '/revisions', json=v2_body() | {'expected_revision': 1}).status_code == 409
    for role in ('perception','policy','controller'):
        item = saved['definition'][role]
        del client.app.state.stack_registry._components[(item['id'], item['version'])]
    assert client.get(saved['download_url']).content == exported  # Archived read needs no installed plugin.


@pytest.mark.parametrize('body', [lambda: v2_body(), lambda: definition()])
def test_cross_kernel_component_selection_fails_before_snapshot(client, body):
    value = body()
    value['definition']['controller']['version'] = '1' if value['definition'].get('schema_version') == '2' else '2'
    result = client.post('/api/v1/stacks', json=value)
    assert result.status_code == 422 and 'kernel' in result.text
    assert client.get('/api/v1/stacks').json() == []


def test_schema_and_agent_api_expose_both_definition_and_output_contracts(client):
    document = client.get('/openapi.json').json()
    models = document['components']['schemas']
    assert any(name.startswith('StackDefinitionV2') for name in models)
    assert any(name.startswith('StackOutputV2') for name in models)
    ticks = [value for name,value in models.items() if name.startswith('StackReplayTick')]
    assert ticks and all('StackOutputV2' in json.dumps(value['properties']['output']) for value in ticks)
    assert any(item['implementation'].get('kernel') == 'stack-kernel/2' for item in client.get('/api/v1/stack-components').json())


def test_real_queue_v2_full_unknown_denominator_state_chain_export_and_pins(client):
    ctx = v2_context(client)
    job = submit(ctx, 'v2')
    with ctx[1].state.db.read_session() as session:
        row = session.scalar(select(JobRow).where(JobRow.id == job['id']))
        assert row.adapter_id == 'recorded-stack' and row.adapter_version == '1'
        assert row.prepared['kernel_version'] == 'stack-kernel/2'
        prepared = deepcopy(row.prepared)
    for changed in ('stack-kernel/1', 'stack-kernel/3', None):
        with pytest.raises(ValueError, match='kernel'):
            stack_replays.PreparedStackReplay.from_payload(prepared | {'kernel_version': changed})
    revised = client.post(f"/api/v1/stacks/{ctx[3]['id']}/revisions", json=v2_body() | {'expected_revision': 1, 'name': 'New unselected revision'})
    assert revised.status_code == 201, revised.text
    completed = execute(ctx, job)
    assert completed.status == 'completed', completed
    assert completed.stack_snapshot['revision'] == 1
    assert completed.counts.completed == completed.counts.total == 3
    previous = None
    for tick in completed.ticks:
        output = tick.output
        assert isinstance(output, dict) and output['kernel_version'] == 'stack-kernel/2'
        assert output['decision']['mode'] == 'unknown_stop'
        assert output['control']['throttle'] == 0 and output['control']['brake'] == 1
        assert output['observation']['coverage']['qualified_prefix_m'] is None
        assert 'ground_reference_unknown' in output['warnings']
        assert output['state_after']['last_sequence'] == tick.index
        assert output['state_after']['last_at_ns'] == tick.input.at_ns
        if previous is not None:
            assert output['state_before'] == previous
        previous = output['state_after']
        assert tick.control_application == 'not_applied_open_loop'
        raw = client.get(tick.points.download_url).content
        assert len(raw) == tick.points.size_bytes
        assert hashlib.sha256(raw).hexdigest() == output['input_points_sha256']
    original = client.get(completed.download_url).content
    assert hashlib.sha256(original).hexdigest() == completed.manifest_hash
    assert submit(ctx, 'v2')['id'] == job['id']
    assert execute(ctx, job) is None
    assert client.post(f'/api/v1/stack-replays/{job["id"]}/cancel').json()['status'] == 'completed'
    assert client.get(completed.download_url).content == original


@pytest.mark.parametrize('removed', ['kernel_version', 'state_before', 'state_after',
    'observation.status', 'observation.input_valid', 'observation.coverage.qualified_prefix_m',
    'observation.coverage.free_space_certified', 'decision.mode', 'decision.force_brake', 'state_after.latched'])
def test_v2_missing_output_evidence_cannot_be_inferred(client, removed):
    ctx = v2_context(client)
    result = execute(ctx, submit(ctx))
    output = deepcopy(result.ticks[0].output)
    target = output
    fields = removed.split('.')
    for key in fields[:-1]:
        target = target[key]
    target.pop(fields[-1])
    with pytest.raises(ValueError):
        validate_stack_output(output)


def test_undeclared_plugin_kernel_remains_legacy_only(client):
    from ade2e.stack_components import ProportionalControl
    class Undeclared(ProportionalControl):
        component_id = 'authored-legacy-plugin'
        def metadata(self):
            item = super().metadata()
            item.implementation.pop('kernel', None)
            return item
    plugin = Undeclared()
    client.app.state.stack_registry.register(plugin)
    body = definition()
    body['definition']['controller']['id'] = plugin.component_id
    assert client.post('/api/v1/stacks', json=body).status_code == 201
    v2 = v2_body()
    v2['definition']['controller'].update(id=plugin.component_id, version='1')
    result = client.post('/api/v1/stacks', json=v2)
    assert result.status_code == 422 and 'kernel' in result.text


def test_v2_component_kernel_drift_preflight_keeps_all_ticks_unattempted(client):
    ctx = v2_context(client)
    job = submit(ctx)
    component = ctx[1].state.stack_registry.get('proportional-control', '2')
    metadata = component.metadata()
    component.metadata = lambda: metadata.model_copy(update={'implementation': metadata.implementation | {'kernel': 'stack-kernel/1'}})
    result = execute(ctx, job)
    assert result.status == 'failed' and result.dispatched_at is None
    assert result.counts.not_attempted == result.counts.total == 3
    assert all(tick.output is None for tick in result.ticks)


@pytest.mark.parametrize('alteration', ['unsupported_field', 'omitted_defaults'])
def test_kernel2_plugin_must_return_the_complete_common_recipe_before_authoring(client, alteration):
    component = client.app.state.stack_registry.get('proportional-control', '2')
    validate = component.validate_parameters
    component.validate_parameters = lambda parameters: (
        validate(parameters) | {'unsupported_vehicle_interface': 'custom'}
        if alteration == 'unsupported_field' else {})
    result = client.post('/api/v1/stacks', json=v2_body())
    assert result.status_code == 422, result.text
    assert client.get('/api/v1/stacks').json() == []


def test_kernel2_plugin_recipe_drift_is_rejected_before_worker_dispatch(client):
    ctx = v2_context(client)
    job = submit(ctx)
    component = client.app.state.stack_registry.get('proportional-control', '2')
    validate = component.validate_parameters
    component.validate_parameters = lambda parameters: validate(parameters) | {'undeclared_actuator': 1.}
    result = execute(ctx, job)
    assert result.status == 'failed' and result.dispatched_at is None
    assert result.counts.not_attempted == result.counts.total == 3
    assert all(tick.packet is None and tick.output is None for tick in result.ticks)


def test_qualified_recorded_grid_then_missing_cell_latches_without_hiding_recovery(client):
    # Independent point construction covers each of the 13 longitudinal rows
    # and three lateral bands twice, including the final 0.5m row.
    ground = []
    for row in range(13):
        low, high = 2.5 + row, min(3.5 + row, 15.0)
        for y in (-.8, 0., .8):
            ground.extend((low + fraction * (high-low), y, 0.) for fraction in (.25,.75))
    missing_cell = [point for point in ground if not (point[0] < 3.5 and point[1] == 0.)]
    body = v2_body()
    body['definition']['perception']['parameters'] = {
        'ground_reference_z_m': 0., 'ground_reference_source': 'Authored ground geometry only'}
    body['definition']['policy']['parameters'] = {'allow_no_obstacle_observed_motion': True}
    body['definition']['controller']['parameters'] = {
        'assumed_full_brake_deceleration_mps2': 4., 'brake_response_source': 'Authored model assumption, not measured calibration'}
    ctx = v2_context(client, body=body, points=[ground, missing_cell, ground], ego_values={'speed_mps': 2.})
    result = execute(ctx, submit(ctx))
    assert result.status == 'completed' and result.counts.completed == result.counts.total == 3
    outputs = [tick.output for tick in result.ticks]
    assert [value['decision']['mode'] for value in outputs] == ['pilot_cruise','unknown_stop','unknown_stop']
    assert [value['observation']['coverage']['status'] for value in outputs] == ['qualified','partial','qualified']
    assert [value['observation']['coverage']['qualified_cells'] for value in outputs] == [39,38,39]
    assert [value['observation']['coverage']['qualified_prefix_m'] for value in outputs] == [12.5,0.,12.5]
    assert all(value['observation']['input_valid'] for value in outputs)
    assert all(not value['observation']['coverage']['free_space_certified'] for value in outputs)
    assert outputs[2]['state_before'] == outputs[1]['state_after']
    assert outputs[2]['state_after']['latched'] and outputs[2]['control']['brake'] == 1
    assert [tick.points.point_count for tick in result.ticks] == [78,76,78]


def test_v2_execution_exception_aborts_later_stateful_inputs(client, monkeypatch):
    from ade2e.stack_v2_runtime import StackSessionV2
    ctx = v2_context(client)
    step = StackSessionV2.step
    calls = []
    def fail_second(self, packet, *args, **kwargs):
        calls.append(packet.sequence)
        if packet.sequence == 1:
            raise RuntimeError('Authored component fault interrupts required state prefix')
        return step(self, packet, *args, **kwargs)
    monkeypatch.setattr(StackSessionV2, 'step', fail_second)
    result = execute(ctx, submit(ctx))
    assert result.status == 'failed'
    assert [tick.status for tick in result.ticks] == ['completed','failed','not_attempted']
    assert calls == [0,1]
    assert result.ticks[0].output['kernel_version'] == 'stack-kernel/2'
    assert result.ticks[1].output is None and result.ticks[2].packet is None
    assert hashlib.sha256(client.get(result.download_url).content).hexdigest() == result.manifest_hash


def test_v2_forged_state_reset_cannot_publish_after_first_tick(client, monkeypatch, caplog):
    from ade2e.stack_v2_runtime import StackSessionV2
    ctx = v2_context(client)
    step = StackSessionV2.step
    def reset(self, packet, *args, **kwargs):
        output = step(self, packet, *args, **kwargs)
        if packet.sequence == 1:
            output.state_before.mode = 'ready'
            output.state_before.latched = False
            output.state_before.latch_reason = None
            output.state_before.stop_plan = None
        return output
    monkeypatch.setattr(StackSessionV2, 'step', reset)
    result = execute(ctx, submit(ctx))
    assert result.status == 'failed'
    assert [tick.status for tick in result.ticks] == ['completed','failed','not_attempted']
    assert result.ticks[1].output is None
    assert result.error
    assert 'exact completed state prefix' in caplog.text


def test_v2_coherent_but_invented_grid_extents_do_not_publish(client, monkeypatch, caplog):
    from ade2e.stack_v2_runtime import StackSessionV2
    body = v2_body()
    body['definition']['perception']['parameters'] = {
        'ground_reference_z_m': 0., 'ground_reference_source': 'Authored test ground'}
    body['definition']['controller']['parameters'] = {
        'assumed_full_brake_deceleration_mps2': 4., 'brake_response_source': 'Uncalibrated test assumption'}
    ctx = v2_context(client, body=body)
    step = StackSessionV2.step
    def invent_extents(self, packet, *args, **kwargs):
        output = step(self, packet, *args, **kwargs)
        cell = next(cell for cell in output.observation.coverage.cells if cell.ground_hits)
        cell.point_min_xyz_m = (cell.point_min_xyz_m[0] + .01, *cell.point_min_xyz_m[1:])
        cell.point_max_xyz_m = (cell.point_max_xyz_m[0] + .01, *cell.point_max_xyz_m[1:])
        # Counts, bounds, qualification, control and the self-contained contract
        # remain coherent. Only checking the retained bytes detects the fiction.
        validate_stack_output(output)
        return output
    monkeypatch.setattr(StackSessionV2, 'step', invent_extents)
    result = execute(ctx, submit(ctx))
    assert result.status == 'failed' and result.counts.not_attempted == 2
    assert result.ticks[0].output is None
    assert not list(ctx[1].state.db.stack_replay_dir.iterdir())
    assert 'differs from actual consumed points' in caplog.text
