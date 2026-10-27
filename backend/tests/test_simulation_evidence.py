"""Adversarial evidence contract checks; authored source/physics fixtures only."""
import json
import struct
from copy import deepcopy

import pytest

from ade2e.adapters import AdapterExecutionError
from ade2e.reference import ReferenceAdapter
from ade2e.service import canonical_bytes, compute_run, PreparedRun
from ade2e.simulation_context import SimulationExecutionRequest, simulation_source_hash as sha
from ade2e.simulation_evidence import validate_simulation_artifacts
from test_simulation_context import context, submit, execute
from live_stack_fixture import make_artifacts


@pytest.fixture
def chain(context):
    job = submit(context)
    request = SimulationExecutionRequest(execution_id='run_authored',input_hash=job.input_hash,
        scenario=job.scenario_snapshot,context=job.execution_context)
    result = ReferenceAdapter().execute(request.scenario)
    return request, result.trajectory, make_artifacts(request,result.trajectory)


def validate(chain, *, partial=False, payloads=None):
    request, trajectory, original = chain
    validate_simulation_artifacts(request, original if payloads is None else payloads, completed=not partial,
        trajectory=None if partial else trajectory, adapter_version='context-fixture/1')


def mutated(payloads, kind, mutate, *, repair_manifest=True):
    payloads = deepcopy(payloads)
    artifact = next(p for p in payloads if p.kind == kind)
    value = json.loads(artifact.content)
    mutate(value)
    artifact.content = canonical_bytes(value)
    if repair_manifest and kind != 'live_stack_manifest':
        manifest = next(p for p in payloads if p.kind == 'live_stack_manifest')
        value = json.loads(manifest.content)
        value['artifacts'] = [{'name': p.name,'kind': p.kind,'size_bytes': len(p.content),'sha256': sha(p.content)} for p in payloads if p.kind != 'live_stack_manifest']
        manifest.content = canonical_bytes(value)
    return payloads


def test_complete_chain_and_valid_empty_full_point_buffer(chain):
    validate(chain)
    payloads = make_artifacts(chain[0],chain[1],raw=b'')
    assert next(p for p in payloads if p.kind == 'live_stack_points').content == b''
    validate(chain,payloads=payloads)


@pytest.mark.parametrize('count',[None,0,1,12,39])
def test_partial_prefix_preserves_entire_tick_denominator(chain,count):
    payloads = make_artifacts(chain[0],chain[1],partial=True,completed_ticks=count)
    validate(chain,partial=True,payloads=payloads)
    with pytest.raises(ValueError): validate(chain,payloads=payloads)


@pytest.mark.parametrize('kind,mutation',[
 ('live_stack_manifest',lambda x:x.update(context_hash='0'*64)),
 ('live_stack_manifest',lambda x:x.update(execution_id='run_foreign')),
 ('live_stack_manifest',lambda x:x.update(input_hash='f'*64)),
 ('live_stack_manifest',lambda x:x.update(worker_adapter_version='different')),
 ('live_stack_manifest',lambda x:x.update(expected_intervals=1)),
 ('live_stack_manifest',lambda x:x.update(acknowledged_commands=1)),
 ('live_stack_manifest',lambda x:x.update(ego_actor_id=2)),
 ('live_stack_manifest',lambda x:x['execution_context'].update(context_hash='0'*64)),
 ('live_stack_trace',lambda x:x['ticks'].pop()),
 ('live_stack_trace',lambda x:x['ticks'][0].update(status='not_attempted')),
 ('live_stack_trace',lambda x:x['ticks'][0].update(next_world_frame=888)),
 ('live_stack_trace',lambda x:x['ticks'][0].update(source_world_frame=888)),
 ('live_stack_trace',lambda x:x['ticks'][0].update(control_rpc_response=None)),
 ('live_stack_trace',lambda x:x['ticks'][0]['control_rpc_response'].update(actor_id=2)),
 ('live_stack_trace',lambda x:x['ticks'][0].update(control_acknowledged=False)),
 ('live_stack_trace',lambda x:x['ticks'][0]['requested_carla_control'].update(throttle=.9)),
 ('live_stack_trace',lambda x:x['ticks'][0]['output'].update(at_ns='1')),
 ('live_stack_trace',lambda x:x['ticks'][0]['output'].update(input_points_sha256='0'*64)),
 ('live_stack_trace',lambda x:x['ticks'][0]['output']['components'][0].update(name='other')),
 ('live_stack_trace',lambda x:x['ticks'][0]['points'].update(sha256='0'*64)),
 ('live_stack_trace',lambda x:x['ticks'][0]['source_refs']['lidar'].update(ordinal=2)),
 ('live_stack_trace',lambda x:x['ticks'][0]['packet'].update(actors=[])),
 ('physics_trace',lambda x:x['observations'][0]['frame']['ego'].update(speed_mps=80)),
 ('physics_trace',lambda x:x['observations'][1].update(world_frame=102)),
 ('physics_trace',lambda x:x['observations'][1].update(source_time_s=10.1)),
])
def test_rehashed_but_contradictory_chain_is_rejected(chain,kind,mutation):
    with pytest.raises(ValueError):
        validate(chain,payloads=mutated(chain[2],kind,mutation))


def test_matching_hashes_cannot_hide_wrong_source_to_ego_transform(chain):
    payloads = make_artifacts(chain[0],chain[1],points_override=struct.pack('<fff',999.,999.,999.))
    with pytest.raises(ValueError,match='transform'):
        validate(chain,payloads=payloads)


def test_invalid_raw_points_cannot_be_declared_consumed(chain):
    payloads = make_artifacts(chain[0],chain[1],raw=struct.pack('<ffff',float('nan'),1.,0.,.4))
    with pytest.raises(ValueError,match='invalid raw'):
        validate(chain,payloads=payloads)


@pytest.mark.parametrize('kind',['live_stack_trace','physics_trace','live_stack_manifest','sensor_recording','live_stack_raw_points','live_stack_points'])
def test_missing_required_artifact_never_publishes_completed_chain(chain,kind):
    with pytest.raises(ValueError): validate(chain,payloads=[p for p in chain[2] if p.kind != kind])


def test_parser_checks_container_even_after_artifact_hash_is_repaired(chain):
    payloads = deepcopy(chain[2])
    source = next(p for p in payloads if p.kind == 'sensor_recording')
    source.content = source.content[:-1] + b'X'
    payloads = mutated(payloads,'live_stack_trace',lambda _:None)
    with pytest.raises(ValueError,match='magic'):
        validate(chain,payloads=payloads)


def test_invalid_json_shapes_fail_as_contract_error(chain):
    payloads = deepcopy(chain[2])
    next(p for p in payloads if p.kind == 'live_stack_trace').content = b'{"ticks":[NaN]}'
    with pytest.raises(ValueError): validate(chain,payloads=payloads)


def test_core_rejects_missing_success_chain_and_keeps_failed_attempt(context):
    job = submit(context)
    adapter = context[4]
    adapter.execute_with_context = lambda request: ReferenceAdapter().execute(request.scenario)
    outcome = execute(context,job)
    run = context[0].get('/api/v1/runs/'+outcome.run_id).json()
    assert outcome.status == 'failed' and run['metrics'] == run['trajectory'] == []
    assert run['execution_context']['context_hash'] == job.execution_context.context_hash
    assert len(run['artifacts']) == 1 and run['artifacts'][0]['kind'] == 'evidence_manifest'


def test_foreign_partial_chain_is_discarded_but_failed_run_is_retained(context):
    job = submit(context)
    def fail(request):
        payloads = make_artifacts(request,ReferenceAdapter().execute(request.scenario).trajectory,partial=True,completed_ticks=1)
        payloads = mutated(payloads,'live_stack_manifest',lambda x:x.update(execution_id='other-run'))
        raise AdapterExecutionError('Authored rejected source',artifact_payloads=payloads)
    context[4].on_execute = fail
    outcome = execute(context,job)
    run = context[0].get('/api/v1/runs/'+outcome.run_id).json()
    assert outcome.status == 'failed' and len(run['artifacts']) == 1
    assert any(e['type']=='diagnostic_evidence_rejected' for e in run['events'])


def test_valid_partial_chain_is_managed_and_hash_verified(context):
    job = submit(context)
    def fail(request):
        payloads = make_artifacts(request,ReferenceAdapter().execute(request.scenario).trajectory,partial=True,completed_ticks=1)
        raise AdapterExecutionError('Authored retained prefix',artifact_payloads=payloads)
    context[4].on_execute = fail
    outcome = execute(context,job)
    run = context[0].get('/api/v1/runs/'+outcome.run_id).json()
    assert outcome.status == 'failed' and len(run['artifacts']) == 7
    for artifact in run['artifacts']:
        assert sha(context[0].get(artifact['download_url']).content) == artifact['sha256']
    assert run['metrics'] == run['trajectory'] == []


def test_numerically_equal_signed_zero_output_is_still_rejected_when_bytes_differ(chain):
    raw = struct.pack('<ffff', -0.0, +0.0, -0.0, .5)
    correct = make_artifacts(chain[0],chain[1],raw=raw)
    validate(chain,payloads=correct)
    shortcut = struct.pack('<fff', -0.0, -0.0, 2.2)
    actual = next(p for p in correct if p.kind=='live_stack_points').content[:12]
    assert struct.unpack('<fff',actual) == struct.unpack('<fff',shortcut)
    assert actual != shortcut and sha(actual) != sha(shortcut)
    # Rebuilt message references, packet hashes and manifest hashes all agree
    # with these incorrect bytes; only the exact transform relation rejects it.
    incorrect = make_artifacts(chain[0],chain[1],raw=raw,points_override=shortcut)
    with pytest.raises(ValueError,match='source-to-ego transform'):
        validate(chain,payloads=incorrect)
