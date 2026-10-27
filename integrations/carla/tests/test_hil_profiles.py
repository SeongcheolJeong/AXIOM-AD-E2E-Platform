"""Frozen DUT policies and real child protocol; authored observations, no CARLA."""
import copy
import os
from types import SimpleNamespace

import pytest

from ade2e.database import Database, Settings
from ade2e.simulation_catalog_models import SimulationCatalogRevisionRow as Row
from axiom_carla import city, hil, hil_dut, hil_native
from test_hil_adapter import fake_transport, snapshot
from test_hil_dut import actor, init, message


def seal(value):
    content = {key: item for key, item in value.items() if key != 'sha256'}
    return {**copy.deepcopy(content), 'sha256': city.sha(city.canonical(content))}


def profile(age=250, recovery=3, *, identity='reference', revision=1, project='default'):
    return seal(dict(schema_version='hil-dut-profile/1', project_id=project, id=identity,
        revision=revision, name='DUT 기준 정책', note='Authored test fixture',
        parameters=dict(max_object_age_ms=age, recovery_fresh_steps=recovery),
        created_at='2026-10-11T12:00:00+09:00', author=dict(user_label='test-engineer', role='Engineer')))


def pair():
    return {'baseline': profile(), 'faulted': profile(500, 8, identity='tolerant')}


@pytest.mark.parametrize('age,recovery', [(50, 1), (250, 3), (500, 8), (1000, 10)])
def test_profile_boundaries_and_utf8_canonical_hash(age, recovery):
    value = profile(age, recovery)
    parsed = hil_dut.validate_dut_profile(value)
    assert parsed == value and parsed is not value
    parsed['parameters']['max_object_age_ms'] = 999
    assert value['parameters']['max_object_age_ms'] == age


@pytest.mark.parametrize('field,value', [
    ('max_object_age_ms', value) for value in (0, 49, 75, 1050, True, 250., float('nan'), float('inf'))
] + [('recovery_fresh_steps', value) for value in (0, 11, True, 3., float('nan'), float('inf'))])
def test_profile_policy_is_strict_and_bounded(field, value):
    record = profile()
    record['parameters'][field] = value
    with pytest.raises(ValueError, match='integer age'):
        hil_dut.validate_dut_profile(record)


def test_profile_manifest_identity_and_content_tampering_rejected():
    value = profile()
    for key, changed in [('name', 'renamed'), ('project_id', 'other'), ('revision', 2), ('sha256', '0' * 64)]:
        corrupt = {**value, key: changed}
        with pytest.raises(ValueError, match='hash mismatch'):
            hil_dut.validate_dut_profile(corrupt)
    for corrupt in (None, {**value, 'revision': True}, {**value, 'extra': 1}):
        with pytest.raises(ValueError, match='identity'):
            hil_dut.validate_dut_profile(corrupt)


def test_explicit_default_policy_keeps_legacy_step_results_and_handshake_shape(tmp_path):
    legacy = hil_native.DutProcess(init(), tmp_path / 'legacy.log')
    explicit = hil_native.DutProcess({**init(), 'dut_profile': profile()}, tmp_path / 'profile.log')
    try:
        assert 'dut_profile' not in legacy.initial
        assert explicit.initial['dut_profile'] == profile()
        assert explicit.initial['pid'] != os.getpid()
        frozen = message(0)['object_packet']
        for index in range(12):
            request = message(index, frozen if index < 8 else None)
            old, _ = legacy.exchange(request)
            new, _ = explicit.exchange(request)
            assert old == new
        assert old['state'] == 'tracking'
    finally:
        legacy.close()
        explicit.close()


def test_process_rejects_wrong_profile_acknowledgement_and_reaps_child(tmp_path, monkeypatch):
    original = hil_native.DutProcess.exchange
    children = []
    def altered(self, request):
        reply, latency = original(self, request)
        if request['op'] == 'init':
            children.append(self.process)
            reply['dut_profile'] = profile(500, 8)
        return reply, latency
    monkeypatch.setattr(hil_native.DutProcess, 'exchange', altered)
    with pytest.raises(ValueError, match='initialization profile mismatch'):
        hil_native.DutProcess({**init(), 'dut_profile': profile()}, tmp_path / 'mismatch.log')
    assert len(children) == 1 and children[0].poll() is not None


def authored_trace(out, side, fault, selected=None, steps=115):
    out.mkdir(parents=True, exist_ok=True)
    config = {'side': side, 'fault': fault, 'source_hashes': hil_native.source_hashes()}
    if selected is not None:
        config.update(comparison_kind='same_fault_dut_ab', dut_profile=selected)
    spec = {'id': 'ego', 'role': 'ego', 'route': init()['route'], 'speed_mps': 3.}
    bridge = hil_native.HilBridge(config, spec, init()['physics'], out)
    frozen = {'source': {}, 'scenario': {'actors': [spec]}, 'hil': config}
    scene = {'source': {'hil_source_hashes': hil_native.source_hashes()}, 'frames': [],
        'actors': [{'id': 'ego', 'native_actor_id': 10}, {'id': 'lead', 'native_actor_id': 11}],
        'vehicle_physics': {'ego': init()['physics']}}
    carla = SimpleNamespace(VehicleControl=lambda **value: value,
        command=SimpleNamespace(ApplyVehicleControl=lambda identity, value: (identity, value)))
    client = SimpleNamespace(apply_batch_sync=lambda commands, tick:
        [SimpleNamespace(actor_id=identity, error='') for identity, _ in commands])
    try:
        for index in range(steps):
            current = {'ego': actor('ego', index * .1), 'lead': actor('lead', 12 + index * .1)}
            command, _ = bridge.step(index, current, 100 + index)
            lead_command = {'throttle': .2, 'brake': 0., 'steer': 0.}
            bridge.apply_controls(carla, client,
                {'ego': SimpleNamespace(id=10), 'lead': SimpleNamespace(id=11)},
                {'ego': command, 'lead': lead_command})
            observed = {**actor('ego', (index + 1) * .1), 'control': command}
            bridge.observed(observed, 101 + index, command)
            scene['frames'].append({'input_frame': 100 + index, 'frame': 101 + index,
                'actors': [observed, {**current['lead'], 'control': lead_command}]})
    finally:
        bridge.dut.close()
    bridge.trace['status'] = 'completed'
    assert bridge.dut.process.returncode == 0
    return bridge.trace, scene, frozen


@pytest.mark.parametrize('kind', ['delay', 'drop'])
def test_real_children_compare_threshold_and_recovery_on_identical_fault_schedule(tmp_path, kind):
    fault = {'kind': kind, 'start_s': 2, 'duration_s': 3 if kind == 'delay' else 1,
        'delay_ms': 400 if kind == 'delay' else 0}
    traces = {}
    for side, selected in pair().items():
        trace, scene, frozen = authored_trace(tmp_path / side, side, fault, selected)
        assert hil.validate_trace(trace, scene, frozen, side, fault) is trace
        assert trace['dut_profile'] == trace['dut_initialization']['dut_profile'] == selected
        assert trace['max_object_age_s'] == selected['parameters']['max_object_age_ms'] / 1000
        assert trace['recovery_fresh_steps'] == selected['parameters']['recovery_fresh_steps']
        assert trace['fault'] == fault and trace['comparison_kind'] == 'same_fault_dut_ab'
        traces[side] = trace
    assert [row['request']['object_packet'] for row in traces['baseline']['rows']] == [
        row['request']['object_packet'] for row in traces['faulted']['rows']]
    # Equality above is specific to authored observations, not a closed-loop physics guarantee.
    def transition(side, event):
        return [row['time_s'] for row in traces[side]['events'] if row['type'] == event]
    if kind == 'delay':
        # Delivery never rolls back object sequence when the delay starts. The
        # last fresh packet ages first, crossing250ms at input2.25/response2.30.
        assert transition('baseline', 'dut_degraded') == pytest.approx([2.30])
        assert transition('baseline', 'dut_tracking') == pytest.approx([.05, 5.15])
        assert transition('faulted', 'dut_degraded') == []
        assert transition('faulted', 'dut_tracking') == pytest.approx([.05])
    else:
        assert transition('baseline', 'dut_degraded') == pytest.approx([2.30])
        assert transition('faulted', 'dut_degraded') == pytest.approx([2.55])
        assert transition('baseline', 'dut_tracking') == pytest.approx([.05, 3.15])
        assert transition('faulted', 'dut_tracking') == pytest.approx([.05, 3.40])


@pytest.fixture
def profile_trace(tmp_path):
    fault = {'kind': 'delay', 'start_s': 2, 'duration_s': 3, 'delay_ms': 400}
    return (*authored_trace(tmp_path, 'baseline', fault, profile()), fault)


@pytest.mark.parametrize('mutate', [
    lambda t: t.update(dut_profile=profile(500, 8)),
    lambda t: t['dut_initialization'].update(dut_profile=profile(500, 8)),
    lambda t: t.update(max_object_age_s=.5),
    lambda t: t.update(recovery_fresh_steps=8),
    lambda t: t.update(comparison_kind='same_scene_fault_ab'),
    lambda t: t.update(fault=None),
    lambda t: t['rows'][50]['reply'].update(state='tracking'),
])
def test_trace_replay_rejects_profile_mode_fault_and_response_tampering(profile_trace, mutate):
    trace, scene, frozen, fault = profile_trace
    mutate(trace)
    with pytest.raises(ValueError):
        hil.validate_trace(trace, scene, frozen, 'baseline', fault)


def test_legacy_trace_keeps_absent_profile_fields_and_normal_baseline(tmp_path):
    fault = {'kind': 'delay', 'start_s': 2, 'duration_s': 3, 'delay_ms': 400}
    trace, scene, frozen = authored_trace(tmp_path, 'baseline', fault)
    assert hil.validate_trace(trace, scene, frozen, 'baseline', fault) is trace
    assert 'comparison_kind' not in trace and 'dut_profile' not in trace
    assert 'dut_profile' not in trace['dut_initialization'] and trace['fault'] is None
    assert trace['max_object_age_s'] == .25 and trace['recovery_fresh_steps'] == 3


@pytest.fixture
def prepared_profiles(tmp_path, monkeypatch):
    db = Database(Settings(data_dir=tmp_path / 'db', seed_examples=False))
    db.initialize()
    profiles = pair()
    with db.session.begin() as session:
        for value in profiles.values():
            session.add(Row(project_id=value['project_id'], kind='hil-dut-profile', id=value['id'],
                revision=value['revision'], manifest=city.canonical({k: v for k, v in value.items() if k != 'sha256'}),
                manifest_hash=value['sha256']))
    handler = hil.HilExperimentHandler(database=db)
    city_input = snapshot()['city_snapshot']
    city_input.update(source={'scenario_id': 'scenario', 'scenario_revision': 1},
        workload={'ticks': 360, 'camera_bytes': 1000})
    monkeypatch.setattr(hil, 'get_scenario', lambda *args:
        {'project_id': 'default', 'id': 'scenario', 'revision': 1, 'configuration_hash': 'a' * 64})
    monkeypatch.setattr(handler.transport, 'prepare', lambda spec: copy.deepcopy(city_input))
    spec = SimpleNamespace(kind='hil_demo', comparison_kind='same_fault_dut_ab', model=None, sensor=None,
        evaluator=None, project_id='default', seed=42,
        input={'scenario_id': 'scenario', 'scenario_revision': 1, 'fault': snapshot()['fault'], 'dut_profiles': profiles})
    yield handler, spec, db
    db.engine.dispose()


def test_prepare_resolves_exact_catalog_revision_and_freezes_full_records(prepared_profiles):
    handler, spec, db = prepared_profiles
    frozen = handler.prepare(spec)
    assert frozen['dut_profiles'] == spec.input['dut_profiles']
    assert frozen['comparison_kind'] == 'same_fault_dut_ab'
    # A later catalog revision cannot silently change the already selected revision.
    newer = profile(1000, 10, revision=2)
    with db.session.begin() as session:
        session.add(Row(project_id='default', kind='hil-dut-profile', id=newer['id'], revision=2,
            manifest=city.canonical({k: v for k, v in newer.items() if k != 'sha256'}), manifest_hash=newer['sha256']))
    assert handler.prepare(spec) == frozen
    spec.input['dut_profiles']['baseline']['name'] = 'local mutation'
    assert frozen['dut_profiles']['baseline']['name'] == 'DUT 기준 정책'


@pytest.mark.parametrize('mutation', ['content', 'project', 'revision', 'hash', 'row_corrupt', 'missing_side'])
def test_prepare_blocks_forged_generic_experiment_profile(prepared_profiles, mutation):
    handler, spec, db = prepared_profiles
    value = spec.input['dut_profiles']['baseline']
    if mutation == 'content':
        spec.input['dut_profiles']['baseline'] = profile(500, 8)
    elif mutation == 'project':
        spec.input['dut_profiles']['baseline'] = profile(project='other')
    elif mutation == 'revision':
        spec.input['dut_profiles']['baseline'] = profile(revision=2)
    elif mutation == 'hash':
        value['sha256'] = '0' * 64
    elif mutation == 'missing_side':
        del spec.input['dut_profiles']['faulted']
    else:
        with db.session.begin() as session:
            row = session.get(Row, ('default', 'hil-dut-profile', 'reference', 1))
            row.manifest = b'{}'
    with pytest.raises(ValueError):
        handler.prepare(spec)


def test_prepare_legacy_does_not_add_optional_profile_fields(prepared_profiles):
    handler, spec, _ = prepared_profiles
    spec.comparison_kind = 'same_scene_fault_ab'
    with pytest.raises(ValueError):
        handler.prepare(spec)
    del spec.input['dut_profiles']
    frozen = handler.prepare(spec)
    assert 'dut_profiles' not in frozen and frozen['comparison_kind'] == 'same_scene_fault_ab'


def test_execute_keeps_same_fault_and_distinct_frozen_profiles_without_catalog_reads(tmp_path, monkeypatch):
    handler = hil.HilExperimentHandler()
    calls = []
    handler.transport = fake_transport(calls)
    monkeypatch.setattr(hil, 'validate_trace', lambda trace, *_: trace)
    frozen = snapshot()
    frozen.update(comparison_kind='same_fault_dut_ab', dut_profiles=pair())
    original = copy.deepcopy(frozen)
    result = handler.execute(frozen, tmp_path, lambda: False)
    assert frozen == original
    assert result.summary['comparison_kind'] == 'same_fault_dut_ab'
    assert result.summary['dut_profiles'] == pair()
    for call in calls:
        side = call['hil']['side']
        assert call['hil']['fault'] == frozen['fault']
        assert call['hil']['comparison_kind'] == 'same_fault_dut_ab'
        assert call['hil']['dut_profile'] == result.summary['sides'][side]['dut_profile'] == pair()[side]
    assert {item.name for item in result.artifacts} >= {'baseline-hil-trace.json', 'faulted-hil-trace.json'}
