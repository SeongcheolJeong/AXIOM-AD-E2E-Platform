"""Authored native-sequence tests; no actual CARLA or plant-response claims."""
from copy import deepcopy
import builtins
import importlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from test_diagnose_carla_brake_response import (
    environment, diagnostic_module, settled_start, SETTLED, World, Client, f32,
)

pytestmark = pytest.mark.parametrize('environment', [SETTLED], indirect=True)


@pytest.fixture
def isolation_environment(environment, monkeypatch, tmp_path):
    """A 225-request authored source followed by an independently owned fake world.

    Prescribed raw states are independent of throttle. They test protocol
    sequencing and provenance, not whether the PI controls a physical plant.
    """
    source, _, base = environment
    protocol = importlib.import_module('carla_throttle_isolation_protocol')
    module = importlib.import_module('diagnose_carla_throttle_isolation')
    source.report['installed_module_hashes'] = {name: 'c' * 64 for name in protocol.MODULES}
    source.recorder.event('diagnostic_started', {key: source.report[key] for key in
        ('experiment_id', 'intent_sha256', 'protocol_sha256', 'sequence_sha256', 'source_files')})
    source.output.joinpath('source-intent.json').write_bytes(base.canonical(source.intent))
    source, _, _, snapshot, _ = settled_start(environment, monkeypatch,
        speed_at=lambda index: min(1.94, index * .1), position_at=lambda index: 12.02 * index / 225)
    with pytest.raises(RuntimeError, match='entry-plane') as error:
        source.prepare(snapshot)
    source.report['error'] = {'type': 'RuntimeError', 'message': str(error.value)}
    source.cleanup()
    assert source.finalize() == 1 and source.report['cleanup']['status'] == 'confirmed'
    reference = protocol.reference_from_report(source.report, source.intent,
        {name: base.sha((source.output / name).read_bytes()) for name in protocol.CAPTURE_FILES})
    assert len(reference['controls']) == 225
    p = protocol.make_protocol(source.protocol, 'continue_pi', 225)
    intent = deepcopy(source.intent)
    intent.update(schema_version=protocol.INTENT_VERSION, experiment_id='d4444444-4444-4444-8444-444444444444',
        protocol=p, protocol_sha256=base.sha(base.canonical(p)), reference=reference,
        sequence_sha256=base.sha(base.canonical(protocol.sequence_descriptor(p, reference))),
        source_files={name: base.sha((Path(module.__file__).parent / name).read_bytes()) for name in protocol.SOURCE_FILES})
    output = tmp_path / 'isolation'
    output.mkdir()
    recorder = base.Recorder(output)
    world = World()
    runner = module.ThrottleIsolationDiagnostic(protocol.validate_intent(intent), carla=source.carla,
        client=Client(world), output=output, execution_root=source.execution_root,
        recorder=recorder, native=dict(source.native))
    runner.report['installed_module_hashes'] = deepcopy(reference['installed_module_hashes'])
    yield runner, world, module, source
    if not recorder.stream.closed:
        recorder.stream.close()


def fresh_start(env, monkeypatch, *, continuation_speed=None):
    runner, world, module, _ = env
    runner.recorder.event('diagnostic_started', {key: runner.report[key] for key in
        ('experiment_id', 'intent_sha256', 'protocol_sha256', 'sequence_sha256', 'source_files')})
    runner.output.joinpath('source-intent.json').write_bytes(module.canonical(runner.intent))
    snapshot = runner.rest(runner.preflight())
    ego = runner.returned['ego']
    world.no_throttle_response = True
    original_tick, steps, previous_speed = world.tick, [], ego.velocity.x
    def prescribed_tick(*, seconds):
        nonlocal previous_speed
        frame = original_tick(seconds=seconds)
        index = len(steps) + 1
        speed = min(1.94, index * .1)
        if index > 225 and continuation_speed is not None:
            speed = continuation_speed(index - 225)
        ego.velocity.x = speed
        ego.acceleration.x = (speed - previous_speed) / .05
        ego.transform.location.x = 12.02 * index / 225 if index <= 225 else ego.transform.location.x
        previous_speed = speed
        steps.append(index)
        return frame
    monkeypatch.setattr(world, 'tick', prescribed_tick)
    return snapshot, steps


def select_branch(runner, branch):
    protocol = importlib.import_module('carla_throttle_isolation_protocol')
    p = protocol.make_protocol(runner.reference['controller_protocol'], branch, runner.protocol['prefix_steps'])
    runner.intent.update(protocol=p, protocol_sha256=protocol.sha(protocol.canonical(p)),
        sequence_sha256=protocol.sha(protocol.canonical(protocol.sequence_descriptor(p, runner.reference))))
    runner.protocol = p
    runner.report.update(isolation_protocol=deepcopy(p), intent_sha256=protocol.sha(protocol.canonical(runner.intent)),
        protocol_sha256=runner.intent['protocol_sha256'], sequence_sha256=runner.intent['sequence_sha256'])
    runner.reservation['request_hash'] = runner.report['intent_sha256']


def select_substep_override(runner):
    protocol = importlib.import_module('carla_throttle_isolation_protocol')
    p = protocol.make_protocol(runner.reference['controller_protocol'], 'hold_throttle',
        runner.protocol['prefix_steps'], continuation_substep_s=.005)
    runner.intent.update(schema_version=protocol.intent_version(p), protocol=p, protocol_sha256=protocol.sha(protocol.canonical(p)),
        sequence_sha256=protocol.sha(protocol.canonical(protocol.sequence_descriptor(p, runner.reference))))
    runner.protocol = p
    runner.report.update(schema_version=protocol.report_version(p), isolation_protocol=deepcopy(p), intent_sha256=protocol.sha(protocol.canonical(runner.intent)),
        protocol_sha256=runner.intent['protocol_sha256'], sequence_sha256=runner.intent['sequence_sha256'])
    runner.report['continuation_physics'] = {'status': 'not_attempted', 'requested': deepcopy(p['continuation_physics']),
        'before': None, 'target': None, 'after': None, 'handoff_frame': None, 'after_frame': None,
        'before_time_s': None, 'after_time_s': None, 'api_returned_frame': None}
    runner.reservation['request_hash'] = runner.report['intent_sha256']


@pytest.mark.parametrize('branch', ['continue_pi', 'hold_throttle'])
def test_fixed_prefix_and_forty_continuations_keep_original_requests_and_same_handoff_frame(isolation_environment, monkeypatch, branch):
    runner, world, module, source = isolation_environment
    select_branch(runner, branch)
    initial, steps = fresh_start(isolation_environment, monkeypatch)
    snapshot = runner.prepare(initial)
    prep = runner.report['preparation']
    assert prep['expected_commands'] == len(prep['commands']) == len(prep['pi_steps']) == 225
    assert prep['expected_observations'] == len(prep['observations']) == 226
    assert prep['initial_gate']['admitted'] and prep['handoff_gate']['admitted']
    assert prep['pi_state']['step'] == 225
    assert [c['control'] for c in prep['commands']] == runner.reference['controls']
    assert all(s['matched'] and s['command_index'] == i for i, s in enumerate(prep['pi_steps']))
    assert len(steps) == 225 and not any(call[0] == 'velocity_injection' for call in world.calls)
    assert source.report['preparation']['entry_gate']['admitted'] is False
    frame = snapshot.frame
    runner.measure(snapshot)
    measurement = runner.report['measurement']
    assert measurement['expected_commands'] == len(measurement['commands']) == len(measurement['control_steps']) == 40
    assert measurement['expected_observations'] == len(measurement['observations']) == 41
    assert len(steps) == 265 and measurement['entry_observation_frame'] == frame
    assert measurement['observations'][0]['ego'] == prep['observations'][-1]['ego']
    assert measurement['pi_state']['step'] == (265 if branch == 'continue_pi' else 225)
    assert all(c['application_status'] == 'observed_next_frame' for c in measurement['commands'])
    for i, command in enumerate(measurement['commands']):
        assert command['world_frame'] == frame + i and command['next_world_frame'] == frame + i + 1
        assert command['control']['brake'] == command['control']['steer'] == 0.
        module.require_control_readback(command['control'], measurement['observations'][i + 1])
    if branch == 'hold_throttle':
        last = runner.reference['controls'][-1]
        assert last['throttle'] != f32(last['throttle'])
        assert all(c['control'] == last for c in measurement['commands'])
        assert all(s['state'] == prep['pi_state'] for s in measurement['control_steps'])
    else:
        assert measurement['control_steps'][0]['trace']['pi_trace']['state_before'] == prep['pi_state']
        assert any(c['control'] != runner.reference['controls'][-1] for c in measurement['commands'])
    runner.cleanup()
    assert runner.report['cleanup']['status'] == 'confirmed' and runner.finalize() == 0
    report = json.loads((runner.output / 'report.json').read_bytes())
    assert report['schema_version'] == 'carla-throttle-isolation-diagnostic/1'
    assert report['reference'] == runner.reference and report['isolation_protocol'] == runner.protocol


def test_substep_override_retains_internal_tick_as_first_acked_interval(isolation_environment, monkeypatch):
    runner, world, _, _ = isolation_environment
    select_substep_override(runner)
    initial, _ = fresh_start(isolation_environment, monkeypatch)
    snapshot = runner.prepare(initial)
    handoff_frame = snapshot.frame
    assert world.settings.max_substep_delta_time == .01
    apply = world.apply_settings
    def native_like_apply(settings):
        before = world.frame
        apply(settings)
        if settings.synchronous_mode:
            world.tick(seconds=1.)
        return before
    monkeypatch.setattr(world, 'apply_settings', native_like_apply)
    runner.measure(snapshot)
    evidence = runner.report['continuation_physics']
    assert evidence['status'] == 'applied_and_read_back'
    assert evidence['handoff_frame'] == handoff_frame
    assert evidence['after_frame'] == handoff_frame + 1
    assert evidence['before']['values']['max_substep_delta_time'] == .01
    assert evidence['after']['values']['max_substep_delta_time'] == .005
    assert evidence['after']['values']['max_substeps'] == 10
    assert runner.report['measurement']['entry_observation_frame'] == handoff_frame
    assert runner.report['measurement']['commands'][0]['world_frame'] == handoff_frame
    assert runner.report['measurement']['commands'][0]['next_world_frame'] == handoff_frame + 1
    assert world.settings.fixed_delta_seconds == .05 and world.settings.max_substep_delta_time == .005
    runner.cleanup()
    assert runner.report['cleanup']['status'] == 'confirmed'


def test_prefix_mismatch_is_retained_before_any_mismatched_request_is_sent(isolation_environment, monkeypatch):
    runner, world, _, _ = isolation_environment
    initial, ticks = fresh_start(isolation_environment, monkeypatch)
    original_observe = runner.observe
    def changed_fresh_source(snapshot, phase, rows):
        if phase == 'preparation' and len(rows) == 5:
            # Leave the slew-saturated startup command range, while staying
            # inside the diagnostic's 3 m/s spatial abort bound.
            runner.returned['ego'].velocity.x += 1.5
            snapshot = world.get_snapshot()
        return original_observe(snapshot, phase, rows)
    monkeypatch.setattr(runner, 'observe', changed_fresh_source)
    calls = len(runner.client.batch_calls)
    with pytest.raises(RuntimeError, match='float32 request'):
        runner.prepare(initial)
    prep = runner.report['preparation']
    assert len(prep['commands']) == 225 and len(prep['pi_steps']) == 6 and len(prep['observations']) == 6
    assert prep['pi_steps'][-1]['matched'] is False and prep['commands'][5]['application_status'] == 'not_attempted'
    assert prep['pi_state']['step'] == 5 and len(ticks) == 5 and len(runner.client.batch_calls) == calls + 5
    assert all(c['application_status'] == 'not_attempted' for c in prep['commands'][5:])
    assert runner.report['measurement']['observations'] == []
    assert all(c['control'] is None for c in runner.report['measurement']['commands'])


def test_different_initial_pose_stops_before_replay(isolation_environment, monkeypatch):
    runner, world, _, _ = isolation_environment
    _, ticks = fresh_start(isolation_environment, monkeypatch)
    runner.returned['ego'].transform.location.y += .02
    initial = world.get_snapshot()
    calls = len(runner.client.batch_calls)
    with pytest.raises(RuntimeError, match='initial state'):
        runner.prepare(initial)
    prep = runner.report['preparation']
    assert prep['initial_gate']['admitted'] is False and prep['pi_steps'] == [] and ticks == []
    assert len(prep['observations']) == 1 and len(runner.client.batch_calls) == calls


def test_handoff_raw_rotating_mismatch_preserves_full_replay_but_no_extension(isolation_environment, monkeypatch):
    runner, _, _, _ = isolation_environment
    initial, ticks = fresh_start(isolation_environment, monkeypatch)
    original = runner.native['_telemetry']
    def mismatch(view, frame, actor):
        data = original(view, frame, actor)
        if len(ticks) == 225:
            data['wheels'][0]['fields']['omega']['value'] *= 1.03
        return data
    runner.native['_telemetry'] = mismatch
    with pytest.raises(RuntimeError, match='prefix handoff'):
        runner.prepare(initial)
    prep = runner.report['preparation']
    assert len(prep['observations']) == 226 and prep['pi_state']['step'] == 225
    assert all(c['application_status'] == 'observed_next_frame' for c in prep['commands'])
    assert prep['handoff_gate']['admitted'] is False
    assert runner.report['measurement']['control_steps'] == []


@pytest.mark.parametrize('failure', ['ack', 'observation'])
@pytest.mark.parametrize('branch', ['continue_pi', 'hold_throttle'])
def test_extension_failure_preserves_proposal_and_prior_committed_state(isolation_environment, monkeypatch, failure, branch):
    runner, world, _, _ = isolation_environment
    select_branch(runner, branch)
    initial, ticks = fresh_start(isolation_environment, monkeypatch)
    snapshot = runner.prepare(initial)
    original_send, original_tick, sent = runner.send_control, world.tick, []
    def send(snapshot, row, **kwargs):
        sent.append(row)
        if len(sent) == 2 and failure == 'ack':
            runner.client.response_mode = 'timeout'
        return original_send(snapshot, row, **kwargs)
    def tick(*, seconds):
        frame = original_tick(seconds=seconds)
        if len(ticks) == 227 and failure == 'observation':
            runner.returned['ego'].velocity.y = 4.
        return frame
    monkeypatch.setattr(runner, 'send_control', send)
    monkeypatch.setattr(world, 'tick', tick)
    with pytest.raises((RuntimeError, TimeoutError)):
        runner.measure(snapshot)
    measurement = runner.report['measurement']
    assert len(measurement['commands']) == 40 and len(measurement['control_steps']) == len(sent) == 2
    assert measurement['pi_state'] == measurement['control_steps'][0]['state']
    assert measurement['pi_state']['step'] == (226 if branch == 'continue_pi' else 225)
    assert len(measurement['observations']) == (2 if failure == 'ack' else 3)
    assert all(c['control'] is None and c['application_status'] == 'not_attempted' for c in measurement['commands'][2:])


@pytest.mark.parametrize('change', ['frame', 'pose', 'telemetry'])
def test_fresh_f0_handoff_is_rechecked_before_extension(isolation_environment, monkeypatch, change):
    runner, world, _, _ = isolation_environment
    initial, ticks = fresh_start(isolation_environment, monkeypatch)
    snapshot = runner.prepare(initial)
    if change == 'frame':
        world.tick(seconds=1.)
        snapshot = world.get_snapshot()
    elif change == 'pose':
        runner.returned['ego'].transform.location.y += .001
        snapshot = world.get_snapshot()
    else:
        runner.native['_telemetry'] = lambda view, frame, actor: {'status': 'unavailable',
            'before_world_frame': frame, 'after_world_frame': frame, 'error': 'Authored lost raw state'}
    calls = len(runner.client.batch_calls)
    with pytest.raises(RuntimeError, match='Isolation F0|Fresh isolation F0'):
        runner.measure(snapshot)
    measurement = runner.report['measurement']
    assert len(measurement['observations']) == 1 and measurement['control_steps'] == []
    assert len(runner.client.batch_calls) == calls
    assert all(c['control'] is None for c in measurement['commands'])


@pytest.mark.parametrize('name', ['source-intent.json', 'report.json', 'events.jsonl', 'reservation-release.json'])
def test_reference_artifact_tamper_fails_before_native_import_or_output(isolation_environment, tmp_path, name):
    runner, world, module, source = isolation_environment
    reference = tmp_path / 'reference'
    reference.mkdir()
    for file in module.protocol.CAPTURE_FILES:
        raw = (source.output / file).read_bytes()
        (reference / file).write_bytes(raw + (b' ' if file == name else b''))
    with pytest.raises(RuntimeError, match='Reference artifact hash differs'):
        module.verify_reference(runner.intent, reference)
    assert world.calls == [] and runner.client.batch_calls == []


def test_reference_reconstruction_and_captured_source_bytes_are_exact(isolation_environment):
    runner, world, module, source = isolation_environment
    assert module.verify_reference(runner.intent, source.output) == runner.reference
    assert source.report['preparation']['entry_gate']['admitted'] is False
    assert world.calls == []


@pytest.mark.parametrize('failure', ['ack', 'readback'])
def test_prefix_failed_transition_does_not_commit_its_proposed_pi_state(isolation_environment, monkeypatch, failure):
    runner, _, _, _ = isolation_environment
    initial, ticks = fresh_start(isolation_environment, monkeypatch)
    original_send, original_control = runner.send_control, runner.native['_control']
    sent = []
    def send(snapshot, row, **kwargs):
        sent.append(row)
        if len(sent) == 2 and failure == 'ack':
            runner.client.response_mode = 'timeout'
        return original_send(snapshot, row, **kwargs)
    def control(view, frame, actor):
        value = original_control(view, frame, actor)
        if len(ticks) == 2 and failure == 'readback':
            value['data']['throttle'] += .001
        return value
    monkeypatch.setattr(runner, 'send_control', send)
    runner.native['_control'] = control
    with pytest.raises((RuntimeError, TimeoutError)):
        runner.prepare(initial)
    prep = runner.report['preparation']
    assert len(sent) == len(prep['pi_steps']) == 2
    assert prep['pi_state'] == prep['pi_steps'][0]['state'] and prep['pi_state']['step'] == 1
    assert prep['pi_steps'][1]['state']['step'] == 2
    assert len(prep['observations']) == (2 if failure == 'ack' else 3)
    assert all(c['application_status'] == 'not_attempted' for c in prep['commands'][2:])
    assert len(runner.report['measurement']['commands']) == 40 and runner.report['measurement']['control_steps'] == []


def test_self_consistent_artifact_rehash_cannot_bypass_original_chain_before_native_import(isolation_environment, monkeypatch, tmp_path):
    runner, world, module, source = isolation_environment
    reference = tmp_path / 'rehashed-reference'
    reference.mkdir()
    intent = deepcopy(runner.intent)
    for name in module.protocol.CAPTURE_FILES:
        raw = (source.output / name).read_bytes()
        if name == 'events.jsonl':
            events = raw.splitlines()
            first = json.loads(events[0])
            first['data']['intent_sha256'] = '0' * 64
            # Match the newly authored outer digest but keep the broken event
            # signature: direct native invocation must verify the inner chain.
            events[0] = module.canonical(first)
            raw = b'\n'.join(events) + b'\n'
        (reference / name).write_bytes(raw)
        intent['reference']['artifacts'][name] = module.sha(raw)
    path = tmp_path / 'canonical-untrusted-intent.json'
    path.write_bytes(module.canonical(module.protocol.validate_intent(intent)))
    monkeypatch.setenv('ADE2E_CARLA_EXCLUSIVE', '1')
    original_import, native_imports = builtins.__import__, []
    def deny_native(name, *args, **kwargs):
        if name.split('.')[0] in ('carla', 'axiom_carla'):
            native_imports.append(name)
            pytest.fail('Corrupt original chain reached optional native imports')
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', deny_native)
    output = tmp_path / 'forbidden-output'
    args = NS(intent=path, reference_capture=reference, runtime_dir=runner.execution_root.parent,
              output=output, host='127.0.0.1', port=2000)
    with pytest.raises((ValueError, RuntimeError), match='chain|Chain|event|Event|hash|Hash'):
        module.run(args)
    assert native_imports == [] and world.calls == [] and not output.exists()
