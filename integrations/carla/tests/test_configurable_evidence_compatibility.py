"""Authored context/2 transport and recovery counterexamples; no native engine."""
import copy
import json
import math

import httpx
import pytest
from fastapi.testclient import TestClient

from ade2e.adapters import AdapterInputError, AdapterUnavailable
from ade2e.execution_attempt import ExecutionAttemptRequest
from ade2e.schemas import SimulationResult
from ade2e.sensor_schemas import RigCreate, RigSnapshot, RigDetail
from ade2e.simulation_context import (SimulationControlSelectionV2, build_simulation_context,
    make_simulation_execution_request, simulation_input_hash)
from ade2e.simulation_evidence import validate_simulation_artifacts
from ade2e.simulation_sensor_capabilities import canonical, content_hash, simulation_sensor_capabilities
from ade2e.simulation_sensor_schemas import (CarlaLidarParameters, SensorConfigurationCreate,
    SensorConfigurationSnapshot, SensorConfigurationDetail)
from ade2e.simulation_sensor_pose import mount_row_analysis, validate_sensor_configuration_evidence
from ade2e.simulation_context import execution_correlation
from axiom_carla import durable_worker, remote_durable, wire, worker
from axiom_carla.remote import RemoteCarlaAdapter
from axiom_carla.runner import CarlaRunner
from axiom_carla.spool import Store, StoreUnavailable, sha
from test_durable_execution import proxy
from test_durable_version_compatibility import freeze_failure
from test_live_stack import live_request
from test_configured_sensor_live import install_configured_world


def configured_request(scenario):
    """Custom frame, nonzero lateral mount and rotation; no preset shortcuts."""
    original = live_request(scenario)
    rig_definition = RigCreate(name='Authored oblique mount', reference_frame='ego',
        coordinate_convention='right_handed_z_up', note='Contract fixture, not calibrated', sensors=[{
            'key': 'front_oblique', 'name': 'Oblique LiDAR', 'kind': 'lidar', 'frame_id': 'lidar_front_oblique',
            'transform': {'translation_m': [1.125, .2, 1.5],
                          'rotation_xyzw': [0, 0, math.sin(.2), math.cos(.2)]}}])
    rig_snapshot = RigSnapshot(**rig_definition.model_dump(mode='json'), id='configured-rig', revision=3,
        created_at='2026-09-14T00:00:00Z', definition_hash=content_hash(rig_definition.model_dump(mode='json')))
    raw = canonical(rig_snapshot.model_dump(mode='json'))
    rig = RigDetail(**rig_snapshot.model_dump(mode='json'), manifest_hash=sha(raw), manifest_size_bytes=len(raw),
        download_url='/api/v1/sensor-rigs/configured-rig/export?revision=3')
    definition = SensorConfigurationCreate(name='Non-preset bounded recipe', note='Authored input only',
        parameters=CarlaLidarParameters(channels=8, points_per_second=16000, range=60.5,
                                       lower_fov=-40., horizontal_fov=180.))
    snapshot = SensorConfigurationSnapshot(**definition.model_dump(mode='json'), id='configured-recipe', revision=2,
        created_at='2026-09-14T00:00:00Z', definition_hash=content_hash(definition.model_dump(mode='json')),
        capability_schema_hash=simulation_sensor_capabilities()[0].schema_hash)
    raw = canonical(snapshot.model_dump(mode='json'))
    detail = SensorConfigurationDetail(**snapshot.model_dump(mode='json'), manifest_hash=sha(raw),
        manifest_size_bytes=len(raw), download_url='/api/v1/simulation-sensor-configurations/configured-recipe/export?revision=2')
    stack = original.context.stack_snapshot
    selection = SimulationControlSelectionV2(schema_version='simulation-control-selection/2', stack_id=stack.id, stack_revision=stack.revision,
        rig_id=rig.id, rig_revision=rig.revision, lidar_sensor_key='front_oblique',
        sensor_configuration_id=detail.id, sensor_configuration_revision=detail.revision)
    context = build_simulation_context(stack, rig, selection, original.scenario.seed, sensor_configuration=detail)
    return make_simulation_execution_request(execution_id='configured-run', scenario=original.scenario,
        context=context, input_hash=simulation_input_hash(original.scenario, 'carla', '0.8.0', context))


def attempt_for(request):
    return ExecutionAttemptRequest(schema_version='execution-attempt/2', execution_id=request.execution_id,
        adapter_id='carla', adapter_version='0.8.0', scenario=request.scenario, context=request.context,
        input_hash=request.input_hash)


@pytest.fixture
def configured(carla_scenario):
    return configured_request(carla_scenario)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('ADE2E_CARLA_EXECUTION_STORE', str(tmp_path / 'store'))
    monkeypatch.delenv('ADE2E_CARLA_WORKER_TOKEN', raising=False)
    return Store(tmp_path / 'store')


def test_context2_attempt2_roundtrip_preserves_full_pinned_input(configured):
    request = attempt_for(configured)
    restored = ExecutionAttemptRequest.model_validate_json(canonical(request.model_dump(mode='json')))
    assert restored.request_hash == request.request_hash
    assert restored.simulation_request() == configured
    assert restored.simulation_request().schema_version == 'simulation-execution-request/2'
    assert restored.context.sensor_profile.sensor_frame == 'lidar_front_oblique'
    assert restored.context.sensor_profile.mount.translation_m == (1.125, .2, 1.5)
    assert request.context.sensor_configuration_snapshot.revision == 2
    assert request.context.rig_snapshot.revision == 3


@pytest.mark.parametrize('damage', ['attempt1', 'old_adapter', 'missing_context', 'legacy_context'])
def test_attempt_context_and_adapter_versions_cannot_be_mixed(configured, carla_scenario, damage):
    body = attempt_for(configured).model_dump(mode='json')
    if damage == 'attempt1': body['schema_version'] = 'execution-attempt/1'
    elif damage == 'old_adapter':
        body['adapter_version'] = '0.7.0'
        body['input_hash'] = simulation_input_hash(configured.scenario, 'carla', '0.7.0', configured.context)
    elif damage == 'missing_context':
        body['context'] = None
        body['input_hash'] = simulation_input_hash(configured.scenario, 'carla', '0.8.0')
    else:
        old = live_request(carla_scenario)
        body.update(context=old.context.model_dump(mode='json'), input_hash=old.input_hash)
    with pytest.raises(ValueError): ExecutionAttemptRequest.model_validate(body)


def test_wire3_is_selected_by_context_and_rejects_historical_envelopes(configured):
    payload = wire.initial_sensor_configuration_payload(configured)
    encoded = wire.encode_failure('Before native entry', [payload], request=configured)
    assert encoded['schema_version'] == 'carla-execution-failure/3'
    assert wire.decode_failure(encoded, request=configured) == ('Before native entry', [payload])
    changed = copy.deepcopy(encoded)
    changed['schema_version'] = 'carla-execution-failure/2'
    with pytest.raises(ValueError): wire.decode_failure(changed, request=configured)
    for old_version in ('0.5.0', '0.6.0', '0.7.0'):
        with pytest.raises(ValueError): wire.encode_failure('Invalid old dispatch', request=configured, adapter_version=old_version)
        with pytest.raises(ValueError): wire.decode_failure(encoded, request=configured, adapter_version=old_version)


def test_wire3_result_is_not_relabelled_as_context1(configured, worker_payload):
    # Codec-only envelope check; this authored result is deliberately not offered
    # to complete native evidence validation and is not a simulator result.
    result = SimulationResult.model_validate({**worker_payload, 'artifact_payloads': []})
    encoded = wire.encode_result(result, request=configured)
    assert encoded['schema_version'] == 'carla-execution-result/3'
    assert wire.decode_result(encoded, request=configured) == result
    changed = copy.deepcopy(encoded); changed['schema_version'] = 'carla-execution-result/2'
    with pytest.raises(ValueError): wire.decode_result(changed, request=configured)


def test_initial_context2_evidence_is_explicitly_unobserved_and_not_complete(configured):
    payload = wire.initial_sensor_configuration_payload(configured)
    value = json.loads(payload.content)
    assert value['spawn'] is value['actor_attributes'] is value['startup_mount'] is None
    assert value['observations'] == [] and value['detached_attributes'] == {}
    assert value['callback_drain']['drained'] is False
    validate_simulation_artifacts(configured, [payload], completed=False, adapter_version='0.8.0')
    with pytest.raises(ValueError): validate_simulation_artifacts(configured, [], completed=False, adapter_version='0.8.0')
    with pytest.raises(ValueError): validate_simulation_artifacts(configured, [payload], completed=True, adapter_version='0.8.0')


@pytest.mark.parametrize('damage', ['identity', 'recipe', 'rig_hash', 'configuration_hash', 'frame', 'false_drain'])
def test_early_readback_tamper_is_rejected_even_with_valid_artifact_bytes(configured, damage):
    payload = wire.initial_sensor_configuration_payload(configured)
    value = json.loads(payload.content)
    if damage == 'identity': value['execution_id'] = 'another-run'
    elif damage == 'recipe': value['requested']['attributes']['range'] = '61.0'
    elif damage == 'rig_hash': value['rig_manifest_hash'] = 'f'*64
    elif damage == 'configuration_hash': value['configuration_manifest_hash'] = 'f'*64
    elif damage == 'frame': value['sensor_frame'] = 'lidar_roof'
    else: value['callback_drain']['drained'] = True
    damaged = payload.model_copy(update={'content': canonical(value)})
    with pytest.raises(ValueError): validate_simulation_artifacts(configured, [damaged], completed=False, adapter_version='0.8.0')


@pytest.mark.parametrize('format', ['full_wire', 'references', 'no_checkpoint'])
def test_context2_partial_recovery_uses_original_request2_and_failure3(configured, store, monkeypatch, format):
    request = attempt_for(configured); artifact = wire.initial_sensor_configuration_payload(configured)
    with store.begin(request) as attempt:
        if format != 'no_checkpoint':
            expected = freeze_failure(attempt, request, [artifact], old_wire=format == 'full_wire', phase='before_native_entry')
        else:
            expected = wire.encode_failure('Worker owner stopped before a complete checkpoint. Native journal may retain earlier intent; cleanup is unknown.', [artifact], request=configured)
    before = attempt.journal(include_blobs=True)
    monkeypatch.setattr(CarlaRunner, 'execute_with_context', lambda *_: pytest.fail('Recovery dispatched native work'))
    with TestClient(worker.app) as client:
        calls = []; adapter = RemoteCarlaAdapter('http://worker.test', transport=proxy(client, calls=calls))
        status = adapter.inspect_execution(request)
        recovered = adapter.retrieve_execution(request, expected_store_id=store.id, expected_archive_sha256=status.archive_sha256)
        outer = json.loads(recovered.archive_bytes)
        assert outer['request_hash'] == request.request_hash and outer['worker_adapter_version'] == '0.8.0'
        assert canonical(outer['payload']) == canonical(expected)
        assert recovered.failure.artifact_payloads == [artifact]
        assert not any(path.endswith('/simulate') for path in calls)
        assert adapter.retrieve_execution(request, expected_store_id=store.id, expected_archive_sha256=status.archive_sha256).archive_bytes == recovered.archive_bytes
    assert attempt.journal(include_blobs=True) == before


def test_context2_mutation_without_initial_checkpoint_cannot_be_sealed_as_unobserved(configured, store):
    with store.begin(attempt_for(configured)) as attempt:
        attempt.event('world_mutation_intent', {'operation': 'authored contradictory native entry'})
    with pytest.raises(StoreUnavailable, match='initial evidence checkpoint'): attempt.recover_abandoned()
    with attempt.db() as db:
        assert db.execute('SELECT count(*) FROM archive').fetchone()[0] == 0
    assert attempt.meta()['state'] == 'running'


def test_worker_persists_not_observed_checkpoint_before_native_entry(configured, store, monkeypatch):
    request = attempt_for(configured)
    def native_entry(_runner, actual):
        assert actual == configured
        attempt = store.find(request.execution_id)
        assert attempt.meta()['checkpoint_phase'] == 'before_native_entry'
        assert attempt.meta()['mutations_started'] is False
        raise AdapterInputError('Authored input rejection before native work')
    monkeypatch.setattr(CarlaRunner, 'execute_with_context', native_entry)
    with TestClient(worker.app) as client:
        response = client.post('/v3/simulate', params={'store_id': store.id}, json=request.model_dump(mode='json'))
        assert response.status_code == 200
    status = store.inspect(request)
    recovered = remote_durable.decode_archive(request, status, response.content)
    assert recovered.failure.artifact_payloads == [wire.initial_sensor_configuration_payload(configured)]
    assert status.mutations_started is False and status.state == 'failed'


def test_installed_context_capability_declarations_are_exact_and_do_not_probe_native():
    expected = simulation_sensor_capabilities()
    assert CarlaRunner().execution_context_capabilities() == expected
    assert RemoteCarlaAdapter().execution_context_capabilities() == expected


@pytest.fixture
def configured_result(configured, monkeypatch):
    install_configured_world(monkeypatch)
    result = CarlaRunner().execute_with_context(configured)
    validate_simulation_artifacts(configured, result.artifact_payloads, completed=True,
        trajectory=result.trajectory, adapter_version='0.8.0')
    return result


def rehash_manifest(payloads):
    manifest = next(p for p in payloads if p.kind == 'live_stack_manifest')
    value = json.loads(manifest.content)
    value['artifacts'] = [{'name': p.name, 'kind': p.kind, 'sha256': sha(p.content),
                          'size_bytes': len(p.content)} for p in payloads if p.kind != 'live_stack_manifest']
    manifest.content = canonical(value)


@pytest.mark.parametrize('damage', ['missing', 'clock', 'ego', 'attributes', 'unobserved_pose', 'raw_geometry'])
def test_full_configured_chain_rejects_rehashed_contradictions(configured, configured_result, damage):
    payloads = copy.deepcopy(configured_result.artifact_payloads)
    artifact = next(p for p in payloads if p.kind == 'sensor_configuration')
    value = json.loads(artifact.content)
    if damage == 'missing': payloads.remove(artifact)
    elif damage in {'clock', 'ego'}:
        # These edits remain internally coherent within the readback artifact.
        # Only joining to the independent MCAP/physics/control chain rejects them.
        for row in [value['startup_mount'], *value['observations']]:
            if damage == 'clock':
                row['source_time_s'] += 100.
                if row['measurement_time_s'] is not None: row['measurement_time_s'] += 100.
            else:
                row['ego_actor_id'] += 10000; row['parent_actor_id'] += 10000
            row['analysis'] = mount_row_analysis(row, configured.context.sensor_profile.mount)
        validate_sensor_configuration_evidence(value, configured.context, execution_correlation(configured),
            completed=True, source_frames=[row['world_frame'] for row in value['observations']])
    elif damage == 'attributes': value['actor_attributes']['raw']['range'] = '61.0'
    elif damage == 'unobserved_pose': value['observations'][10]['measurement_world_matrix'] = None
    else:
        raw = next(p for p in payloads if p.kind == 'live_stack_raw_points')
        raw.content = b'\0\0\0\0' + raw.content[4:]
    artifact.content = canonical(value)
    rehash_manifest(payloads)
    with pytest.raises(ValueError, match='sensor mount time/ego' if damage in {'clock', 'ego'} else None):
        validate_simulation_artifacts(configured, payloads, completed=True,
            trajectory=configured_result.trajectory, adapter_version='0.8.0')


def test_context2_complete_archive_roundtrip_preserves_native_bytes_without_redispatch(configured, configured_result, store, monkeypatch):
    request = attempt_for(configured)
    with store.begin(request) as attempt:
        attempt.seal('completed', wire.encode_result(configured_result, request=configured),
                     execution_ms=12., cleanup_status='confirmed')
    original = attempt.archive()
    monkeypatch.setattr(CarlaRunner, 'execute_with_context', lambda *_: pytest.fail('Read dispatched an engine'))
    with TestClient(worker.app) as client:
        calls = []; adapter = RemoteCarlaAdapter('http://worker.test', transport=proxy(client, calls=calls))
        status = adapter.inspect_execution(request)
        recovered = adapter.retrieve_execution(request, expected_store_id=store.id, expected_archive_sha256=status.archive_sha256)
    assert recovered.archive_bytes == original and recovered.result == configured_result
    assert not any(path.endswith('/simulate') for path in calls)


@pytest.mark.parametrize('damage', [None, 'schema', 'input_hash', 'context_hash'])
def test_context2_preflight_preserves_exact_selection_before_dispatch(configured, store, monkeypatch, damage):
    ready = {'status': 'ready', 'version': '1ae5356', 'map': configured.scenario.map_name}
    monkeypatch.setattr(CarlaRunner, 'preflight', lambda _: ready)
    def preflight(_runner, scenario, context):
        assert scenario == configured.scenario and context == configured.context
        return ready
    monkeypatch.setattr(CarlaRunner, 'preflight_with_context', preflight)
    monkeypatch.setattr(CarlaRunner, 'execute_with_context', lambda *_: pytest.fail('Preflight executed a trial'))
    with TestClient(worker.app) as client:
        calls = []
        def handle(request):
            calls.append(request.url.path)
            response = client.request(request.method, request.url.path, content=request.content,
                                      headers={'content-type': 'application/json'})
            value = response.json()
            if request.url.path == '/v2/preflight':
                assert value['schema_version'] == 'carla-execution-preflight/2'
                if damage == 'schema': value['schema_version'] = 'carla-execution-preflight/1'
                elif damage: value[damage] = 'f'*64
            return httpx.Response(response.status_code, json=value)
        adapter = RemoteCarlaAdapter('http://worker.test', transport=httpx.MockTransport(handle))
        if damage:
            with pytest.raises(AdapterUnavailable): adapter.validate_execution_context(configured.scenario, configured.context)
        else: adapter.validate_execution_context(configured.scenario, configured.context)
    assert calls == ['/health', '/v2/preflight'] and store.find(configured.execution_id) is None


def test_api_configured_run_to_recording_origin_preserves_pinned_inputs_after_library_changes(configured, tmp_path, monkeypatch):
    from ade2e.adapters import Registry
    from ade2e.database import Settings
    from ade2e.main import create_app
    from ade2e.scenario_schemas import ScenarioCreate
    from ade2e.stack_schemas import StackCreate
    # Actual core API/DB/MCAP pipeline over an authored world. No CARLA engine.
    install_configured_world(monkeypatch)
    class AuthoredAdapter(CarlaRunner):
        def metadata(self):
            return RemoteCarlaAdapter('http://authored.invalid').metadata().model_copy(update={
                'name': 'Authored configured-world fixture',
                'description': 'Protocol test only; no simulator connection'})
    registry = Registry(load_plugins=False); registry.register(AuthoredAdapter())
    settings = Settings(data_dir=tmp_path/'api', database_url=f'sqlite:///{tmp_path}/api/platform.db',
                        api_token=None, seed_examples=False)
    with TestClient(create_app(settings, registry)) as client:
        def post(path, body, **kwargs):
            response = client.post('/api/v1' + path, json=body, **kwargs)
            assert response.status_code in {200, 201}, response.text
            return response.json()
        scenario = post('/scenarios', configured.scenario.model_dump(mode='json', include=set(ScenarioCreate.model_fields)))
        stack = post('/stacks', configured.context.stack_snapshot.model_dump(mode='json', include=set(StackCreate.model_fields)))
        rig_body = configured.context.rig_snapshot.model_dump(mode='json', include=set(RigCreate.model_fields))
        rig = post('/sensor-rigs', rig_body)
        recipe_body = configured.context.sensor_configuration_snapshot.model_dump(mode='json', include=set(SensorConfigurationCreate.model_fields))
        recipe = post('/simulation-sensor-configurations', recipe_body, headers={'Idempotency-Key': 'original-recipe'})
        control = {'schema_version': 'simulation-control-selection/2', 'mode': 'stack',
            'stack_id': stack['id'], 'stack_revision': 1, 'rig_id': rig['id'], 'rig_revision': 1,
            'lidar_sensor_key': configured.context.lidar_sensor_key,
            'sensor_configuration_id': recipe['id'], 'sensor_configuration_revision': 1}
        run = post('/runs', {'scenario_id': scenario['id'], 'adapter_id': 'carla', 'control': control})
        assert run['status'] == 'completed', run.get('error')
        source = next(a for a in run['artifacts'] if a['kind'] == 'sensor_recording')
        original = client.get(source['download_url']).content
        recording = post(f'/runs/{run["id"]}/recordings', {'artifact_id': source['id']})
        origin_url = f'/api/v1/recordings/{recording["id"]}/origin'
        origin = client.get(origin_url).json()
        assert origin['snapshot']['execution_context'] == run['execution_context']
        assert origin['snapshot']['execution_context_hash'] == run['execution_context']['context_hash']
        assert origin['snapshot']['input_hash'] == run['provenance']['input_hash']
        assert client.get(recording['download_url']).content == original
        assert sha(original) == source['sha256'] == recording['sha256']
        changed = copy.deepcopy(rig_body); changed['sensors'][0]['transform']['translation_m'][0] += 1.
        post(f'/sensor-rigs/{rig["id"]}/revisions', {**changed, 'expected_revision': 1})
        changed = copy.deepcopy(recipe_body); changed['parameters']['range'] = 99.
        post(f'/simulation-sensor-configurations/{recipe["id"]}/revisions', {**changed, 'expected_revision': 1},
             headers={'Idempotency-Key': 'revised-recipe'})
        assert client.get(origin_url).json() == origin
        assert post(f'/runs/{run["id"]}/recordings', {'artifact_id': source['id']}) == recording
        assert origin['snapshot']['execution_context']['rig_snapshot']['revision'] == 1
        assert origin['snapshot']['execution_context']['sensor_configuration_snapshot']['revision'] == 1
