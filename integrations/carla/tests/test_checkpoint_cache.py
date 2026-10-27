"""Byte-equivalent checkpoint optimization over authored native-world fixtures."""
import copy
from dataclasses import replace
import hashlib
import io
import json
import struct
from types import SimpleNamespace

import pytest
from mcap.reader import make_reader

from ade2e.schemas import Scenario
from ade2e.stack_schemas import StackPacket
from axiom_carla import durability, stack_bridge
from axiom_carla.recording import LiveMessage, live_stack_recording
from axiom_carla.runner import CarlaRunner
from axiom_carla.spool import canonical
from axiom_carla.stack_bridge import LiveEvidence, _tick
from axiom_carla.wire import encode_failure
from test_live_stack import install_live_world, live_request


@pytest.fixture(scope='module')
def saved_evidence():
    scenario = Scenario(id='checkpoint-cache-fixture', name='Authored cache fixture', category='lane_follow',
        map_name='Town01', duration_s=4, seed=17, revision=1,
        created_at='2026-09-13T00:00:00Z', updated_at='2026-09-13T00:00:00Z')
    with pytest.MonkeyPatch.context() as patch:
        install_live_world(patch)
        request = live_request(scenario)
        result = CarlaRunner().execute_with_context(request)
    payloads = {p.name: p for p in result.artifact_payloads}
    reader = make_reader(io.BytesIO(payloads['carla-stack-sources.mcap'].content))
    metadata = json.loads(next(reader.iter_metadata()).metadata['context'])
    messages = [LiveMessage(channel.id, message.sequence, message.log_time, message.data)
        for _,channel,message in reader.iter_messages(log_time_order=False)]
    return request, payloads, metadata, messages


def prefix(saved, count=8):
    request, payloads, metadata, messages = saved
    data = lambda name: json.loads(payloads[name].content)
    evidence = LiveEvidence(request, 'authored-test', 'Town01')
    evidence.base = [payloads['carla-startup.json']]
    evidence.messages = list(messages[:count*2])
    evidence.observations = copy.deepcopy(metadata['observations'][:count])
    evidence.physics = data('carla-stack-physics.json')['observations'][:count]
    evidence.vehicle_response.data = data('carla-vehicle-response.json')
    evidence.vehicle_response.data['observations'] = evidence.vehicle_response.data['observations'][:count]
    evidence.ticks = [copy.deepcopy(row) if index < count-1 else _tick(index)
        for index,row in enumerate(data('carla-stack-trace.json')['ticks'])]
    if count <= 80:
        evidence.ticks[count-1] = copy.deepcopy(data('carla-stack-trace.json')['ticks'][count-1])
        evidence.ticks[count-1].update(status='running', control_acknowledged=False,
            control_rpc_response=None, next_world_frame=None, next_time_s=None,
            next_ego=None, observed_control=None, application_status='send_outcome_unknown')
    last = evidence.observations[-1]
    raw_ref, point_ref = last['source_refs']['lidar']['raw_points'], last['points']
    evidence.raw = bytearray(payloads['carla-stack-raw-xyzi.bin'].content[:raw_ref['offset_bytes']+raw_ref['size_bytes']])
    evidence.xyz = bytearray(payloads['carla-stack-points.bin'].content[:point_ref['offset_bytes']+point_ref['size_bytes']])
    manifest = data('carla-stack-manifest.json')
    evidence.actual_lidar_attributes = manifest['actual_lidar_attributes']
    evidence.ego_actor_id = manifest['ego_actor_id']
    return evidence


class Sink:
    def checkpoint(self, payloads, phase):
        self.payloads = payloads


def old_checkpoint(evidence, phase):
    """Prior recursive-copy algorithm and uncached canonical MCAP writer."""
    frozen = copy.copy(evidence)
    frozen.ticks = copy.deepcopy(evidence.ticks)
    frozen.status, frozen.phase = 'partial', phase
    frozen.error = 'Interrupted at durable checkpoint: ' + phase
    frozen.vehicle_response = copy.copy(evidence.vehicle_response)
    frozen.vehicle_response.data = copy.deepcopy(evidence.vehicle_response.data)
    frozen._source_recording = lambda metadata: live_stack_recording(frozen.messages, metadata=metadata)
    for tick in frozen.ticks:
        if tick['status'] == 'running':
            tick.update(status='failed', error=frozen.error)
    return frozen.artifacts()


def wire(evidence, payloads, phase):
    return canonical(encode_failure('Execution interrupted; retained committed checkpoint at ' + phase,
        payloads, request=evidence.request))


def original_state(evidence):
    return canonical({'ticks': evidence.ticks, 'response': evidence.vehicle_response.data,
        'physics': evidence.physics, 'observations': evidence.observations,
        'raw': hashlib.sha256(evidence.raw).hexdigest(), 'xyz': hashlib.sha256(evidence.xyz).hexdigest()})


@pytest.mark.parametrize('count', [1, 8, 40, 81])
def test_every_projected_phase_matches_prior_bytes_and_never_mutates_inputs(saved_evidence, count):
    evidence, sink = prefix(saved_evidence, count), Sink()
    before = original_state(evidence)
    held = []
    for phase in ['before_lead_control', 'before_ego_control_send', 'ego_control_acknowledged',
                  'next_physics_before_optional_rpc', 'source_retained']:
        expected = wire(evidence, old_checkpoint(evidence, phase), phase)
        with durability.recording(sink):
            evidence.checkpoint(phase)
        actual = wire(evidence, sink.payloads, phase)
        assert actual == expected
        held.append((sink.payloads, phase, actual))
        assert original_state(evidence) == before
    # Future nested edits must not alter a checkpoint already handed to a sink.
    evidence.ticks[0]['source_refs']['ego']['message_sha256'] = 'a'*64
    evidence.vehicle_response.data['after_rest']['actors']['ego']['position_m']['x'] += 1
    evidence.observations[0]['source_refs']['ego']['log_time_ns'] = '123'
    evidence.raw[:] = b'changed'
    for payloads, phase, content in held:
        assert wire(evidence, payloads, phase) == content


def count_encodes(monkeypatch):
    calls = []
    def encode(messages, *, metadata):
        calls.append((len(messages), copy.deepcopy(metadata)))
        return live_stack_recording(messages, metadata=metadata)
    monkeypatch.setattr(stack_bridge, 'live_stack_recording', encode)
    return calls


def mcap(evidence):
    return next(p.content for p in evidence.artifacts() if p.kind == 'sensor_recording')


def test_phase_changes_reuse_only_mcap_while_checkpoint_projection_changes(saved_evidence, monkeypatch):
    evidence, sink = prefix(saved_evidence), Sink()
    calls = count_encodes(monkeypatch)
    with durability.recording(sink):
        evidence.checkpoint('before_ego_control_send')
        first = {p.name:p.content for p in sink.payloads}
        evidence.checkpoint('ego_control_acknowledged')
        second = {p.name:p.content for p in sink.payloads}
    assert len(calls) == 1
    assert first['carla-stack-sources.mcap'] == second['carla-stack-sources.mcap']
    assert first['carla-stack-trace.json'] != second['carla-stack-trace.json']
    assert first['carla-vehicle-response.json'] != second['carla-vehicle-response.json']
    assert first['carla-stack-manifest.json'] != second['carla-stack-manifest.json']


@pytest.mark.parametrize('field,value', [('channel_id', 2), ('world_frame', 100), ('timestamp_ns', 101), ('content', b'{}')])
def test_every_message_value_invalidates_cache(saved_evidence, monkeypatch, field, value):
    evidence = prefix(saved_evidence)
    calls = count_encodes(monkeypatch)
    old = mcap(evidence)
    evidence.messages[0] = replace(evidence.messages[0], **{field:value})
    new = mcap(evidence)
    assert len(calls) == 2 and new != old
    # Equality by value, not object identity, permits reuse of a fresh message.
    evidence.messages[0] = replace(evidence.messages[0])
    assert mcap(evidence) == new and len(calls) == 2
    assert list(evidence._source_recording_cache) == ['entry']


def test_metadata_status_and_version_have_distinct_exact_cache_keys(saved_evidence, monkeypatch):
    evidence = prefix(saved_evidence, 81)
    calls = count_encodes(monkeypatch)
    original = mcap(evidence)
    evidence.observations[0]['source_refs']['ego']['log_time_ns'] = '123'
    changed = mcap(evidence)
    assert changed != original and len(calls) == 2
    evidence.status = 'completed'
    complete = mcap(evidence)
    assert complete != changed and len(calls) == 3
    evidence.version = 'changed-server-version'
    assert mcap(evidence) == complete and len(calls) == 4
    monkeypatch.setattr(stack_bridge, 'ADAPTER_VERSION', 'changed-adapter-version')
    assert mcap(evidence) == complete and len(calls) == 5


def test_shallow_copy_shared_cache_cannot_return_another_snapshot_or_mutable_payload(saved_evidence, monkeypatch):
    evidence = prefix(saved_evidence)
    calls = count_encodes(monkeypatch)
    first = mcap(evidence)
    clone = copy.copy(evidence)
    clone.observations = copy.deepcopy(evidence.observations)
    clone.observations[0]['source_refs']['ego']['log_time_ns'] = '123'
    second = mcap(clone)
    assert second != first and mcap(evidence) == first and len(calls) == 3
    independent = prefix(saved_evidence)
    assert independent._source_recording_cache is not evidence._source_recording_cache
    assert mcap(independent) == first and len(calls) == 4


def test_invalid_response_after_cache_still_validates_without_mutating_original(saved_evidence):
    evidence, sink = prefix(saved_evidence), Sink()
    mcap(evidence)
    evidence.vehicle_response.data['after_rest']['actors']['ego']['position_m']['x'] = 'invalid-number'
    before = original_state(evidence)
    with durability.recording(sink), pytest.raises(ValueError):
        evidence.checkpoint('ego_control_acknowledged')
    assert original_state(evidence) == before and not hasattr(sink, 'payloads')


def test_invalid_metadata_after_cache_is_not_replaced_by_old_bytes(saved_evidence, monkeypatch):
    evidence = prefix(saved_evidence)
    calls = count_encodes(monkeypatch)
    original = mcap(evidence)
    timestamp = evidence.observations[0]['source_time_s']
    evidence.observations[0]['source_time_s'] = float('nan')
    with pytest.raises(ValueError):
        mcap(evidence)
    assert len(calls) == 1
    evidence.observations[0]['source_time_s'] = timestamp
    assert mcap(evidence) == original and len(calls) == 1


def test_cache_key_snapshots_bytes_instead_of_aliasing_mutable_message_content(saved_evidence, monkeypatch):
    evidence = prefix(saved_evidence)
    calls = count_encodes(monkeypatch)
    content = bytearray(evidence.messages[0].content)
    evidence.messages[0] = replace(evidence.messages[0], content=content)
    original = mcap(evidence)
    content.extend(b' ')
    changed = mcap(evidence)
    assert changed != original and len(calls) == 2


def test_oversized_cache_entry_is_not_retained_and_does_not_change_encoded_bytes(saved_evidence, monkeypatch):
    evidence = prefix(saved_evidence)
    metadata = {'status': 'partial', 'description': 'bounded-cache-policy-check'}
    expected = live_stack_recording(evidence.messages, metadata=metadata)
    monkeypatch.setattr(stack_bridge, 'ARTIFACT_BUDGET', 1)
    assert evidence._source_recording(metadata) == expected
    assert evidence._source_recording_cache['entry'] is None


@pytest.mark.parametrize('change', ['channel', 'count', 'bytes', 'float_frame'])
def test_invalid_source_after_cache_is_rejected(saved_evidence, change):
    evidence = prefix(saved_evidence)
    mcap(evidence)
    if change == 'channel':
        evidence.messages[0] = replace(evidence.messages[0], channel_id=3)
    elif change == 'count':
        evidence.messages = [evidence.messages[0]]*163
    elif change == 'bytes':
        evidence.messages[0] = replace(evidence.messages[0], content=b'x'*(18*1024*1024+1))
    else:
        evidence.messages[0] = replace(evidence.messages[0], world_frame=float(evidence.messages[0].world_frame))
    with pytest.raises((ValueError, TypeError, struct.error)):
        mcap(evidence)


def test_failed_retention_rolls_back_source_and_the_single_cache_entry(saved_evidence, monkeypatch):
    evidence = prefix(saved_evidence, 1)
    old_bytes = mcap(evidence)
    old_entry = evidence._source_recording_cache['entry']
    _, payloads, metadata, messages = saved_evidence
    next_source = metadata['observations'][1]
    raw_ref, point_ref = next_source['source_refs']['lidar']['raw_points'], next_source['points']
    raw = payloads['carla-stack-raw-xyzi.bin'].content[raw_ref['offset_bytes']:raw_ref['offset_bytes']+raw_ref['size_bytes']]
    points = payloads['carla-stack-points.bin'].content[point_ref['offset_bytes']:point_ref['offset_bytes']+point_ref['size_bytes']]
    snapshot = SimpleNamespace(frame=next_source['world_frame'], timestamp=SimpleNamespace(elapsed_seconds=next_source['source_time_s']))
    before = original_state(evidence)
    with monkeypatch.context() as patch:
        patch.setattr(stack_bridge, 'TOTAL_BUDGET', 1)
        with pytest.raises(ValueError, match='total artifact budget'):
            evidence.retain(snapshot, raw, next_source['lidar_timestamp_s'],
                StackPacket.model_validate(next_source['packet']), points, messages[2:4])
    assert original_state(evidence) == before and len(evidence.messages) == 2
    assert evidence._source_recording_cache['entry'] is old_entry
    assert mcap(evidence) == old_bytes
