"""Pinned overlays and real DUT child over authored telemetry; no CARLA."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import pytest
from ade2e.hil_scene_profiles import list_scene_profiles
from axiom_carla import city, hil, hil_native
from test_hil_adapter import fake_transport, scenario, snapshot
from test_hil_dut import actor, init
from test_hil_profiles import authored_trace, pair, prepared_profiles


def profile(identity):
    return next(p for p in list_scene_profiles() if p['id'] == identity)


@pytest.mark.parametrize('identity,expected', [
    ('lead_deceleration', [(3.95, 2, 'cruise'), (4, .5, 'deceleration'), (8, .5, 'deceleration')]),
    ('lead_stop_go', [(3.95, 2, 'cruise'), (4, 0, 'stop'), (7.95, 0, 'stop'), (8, 2, 'restart')]),
])
def test_scene_input_clock_has_exact_bounded_targets(identity, expected):
    for time_s, target, phase in expected:
        decision = hil_native.lead_motion(profile(identity), time_s)
        assert (decision['target_speed_mps'], decision['phase']) == (target, phase)


@pytest.mark.parametrize('identity', ['lead_deceleration', 'lead_stop_go'])
@pytest.mark.parametrize('policy_mode', [False, True])
def test_adapter_both_sides_preserve_base_and_overlay(tmp_path, monkeypatch, identity, policy_mode):
    frozen = snapshot()
    frozen['scene_profile'] = profile(identity)
    if policy_mode:
        frozen.update(comparison_kind='same_fault_dut_ab', dut_profiles=pair())
    original = copy.deepcopy(frozen)
    calls = []
    handler = hil.HilExperimentHandler()
    handler.transport = fake_transport(calls)
    monkeypatch.setattr(hil, 'validate_trace', lambda trace, *_: trace)
    result = handler.execute(frozen, tmp_path, lambda: False)
    assert frozen == original
    for side, dispatch in zip(('baseline', 'faulted'), calls):
        assert dispatch['scenario'] == original['city_snapshot']['scenario']
        assert dispatch['hil']['scene_profile'] == original['scene_profile']
        assert result.summary['sides'][side]['scene_profile'] == original['scene_profile']
    assert result.summary['scene_profile'] == original['scene_profile']


def test_prepare_binds_exact_scene_descriptor_and_preserves_legacy(prepared_profiles):
    handler, spec, _ = prepared_profiles
    legacy = handler.prepare(spec)
    assert 'scene_profile' not in legacy
    base = copy.deepcopy(legacy['city_snapshot'])
    spec.input['scene_profile'] = profile('lead_stop_go')
    frozen = handler.prepare(spec)
    assert frozen['city_snapshot'] == base
    assert frozen['scene_profile'] == spec.input['scene_profile']
    spec.input['scene_profile']['lead_speed_schedule'][1]['at_s'] = 5
    with pytest.raises(ValueError):
        handler.prepare(spec)


@pytest.mark.parametrize('change', [
    lambda p: p.update(id='unknown'), lambda p: p.update(revision=2),
    lambda p: p.update(sha256='0' * 64), lambda p: p.update(clock='wall_clock'),
    lambda p: p['lead_speed_schedule'][1].update(target_speed_mps=.7),
    lambda p: p.update(title='changed'), lambda p: p.update(extra=True),
])
def test_bad_native_profile_rejected_before_child(tmp_path, change):
    value = profile('lead_stop_go')
    change(value)
    config = {'source_hashes': hil_native.source_hashes(), 'side': 'baseline',
        'fault': {'kind': 'drop', 'start_s': 3.5, 'duration_s': 1, 'delay_ms': 0}, 'scene_profile': value}
    with pytest.raises(ValueError):
        hil_native.HilBridge(config, scenario()['actors'][0], init()['physics'], tmp_path,
                            lead_spec=scenario()['actors'][1])
    assert not (tmp_path / 'hil-dut.stderr.log').exists()


def test_unsupported_base_lead_and_changed_helper_hash_refused(tmp_path):
    config = {'source_hashes': hil_native.source_hashes(), 'side': 'baseline',
        'fault': {'kind': 'drop', 'start_s': 3.5, 'duration_s': 1, 'delay_ms': 0},
        'scene_profile': profile('lead_stop_go')}
    lead = scenario()['actors'][1]
    lead['controller']['kind'] = 'timed_replay'
    with pytest.raises(ValueError, match='route-follow lead'):
        hil_native.HilBridge(config, scenario()['actors'][0], init()['physics'], tmp_path, lead_spec=lead)
    config['source_hashes']['hil_scene_profiles_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='sources changed'):
        hil_native.HilBridge(config, scenario()['actors'][0], init()['physics'], tmp_path)


def scene_trace(out, identity):
    """Real JSON-lines DUT, authored lead speeds; not simulated vehicle physics."""
    out.mkdir()
    value = profile(identity)
    fault = {'kind': 'delay', 'start_s': 3, 'duration_s': 4, 'delay_ms': 300}
    config = {'source_hashes': hil_native.source_hashes(), 'side': 'faulted', 'fault': fault, 'scene_profile': value}
    specs = scenario()['actors']
    bridge = hil_native.HilBridge(config, specs[0], init()['physics'], out, lead_spec=specs[1])
    frozen = {'source': {}, 'scenario': {'actors': specs}, 'hil': config}
    scene = {'source': {'hil_source_hashes': hil_native.source_hashes()}, 'hil': {'scene_profile': value},
        'actors': [{'id': 'ego', 'native_actor_id': 10}, {'id': 'lead', 'native_actor_id': 11}],
        'vehicle_physics': {key: init()['physics'] for key in ('ego', 'lead')}, 'frames': []}
    carla = SimpleNamespace(VehicleControl=lambda **v: v,
        command=SimpleNamespace(ApplyVehicleControl=lambda identity, value: (identity, value)))
    client = SimpleNamespace(apply_batch_sync=lambda commands, tick:
        [SimpleNamespace(actor_id=identity, error='') for identity, _ in commands])
    progress = steering = None
    speed = 2.
    try:
        for index in range(180):
            current = {'ego': actor('ego', index * .05), 'lead': {**actor('lead', 12 + index * .04), 'speed_mps': speed}}
            command, _ = bridge.step(index, current, 100 + index)
            motion = bridge.lead_motion(index * .05)
            physics = init()['physics']
            lead_control, lead_plan = city.city_control.feedback(current['lead'], specs[1]['route'], motion['target_speed_mps'],
                wheelbase_m=physics['wheelbase_m'], max_steer_rad=physics['max_steer_angle_rad'],
                previous=progress, previous_steering=steering, stop=motion['target_speed_mps']==0)
            progress, steering = lead_plan['progress_m'], lead_plan['steering_progress_m']
            lead_plan['hil_lead_motion'] = motion
            bridge.apply_controls(carla, client, {'ego': SimpleNamespace(id=10), 'lead': SimpleNamespace(id=11)},
                {'ego': command, 'lead': lead_control})
            # Response happens after the command, deliberately not at its boundary.
            speed = (2. if index < 85 else .5) if identity == 'lead_deceleration' else (2. if index < 85 or index >= 165 else 0.)
            next_ego = {**actor('ego', (index+1) * .05), 'control': command}
            next_lead = {**actor('lead', 12 + (index+1) * .04), 'speed_mps': speed,
                         'control': lead_control, 'plan': lead_plan}
            bridge.observed(next_ego, 101 + index, command, lead=next_lead)
            scene['frames'].append({'input_frame': 100 + index, 'frame': 101 + index,
                                    'actors': [next_ego, next_lead]})
    finally:
        bridge.dut.close()
    bridge.trace['status'] = 'completed'
    return bridge.trace, scene, frozen, fault


@pytest.mark.parametrize('identity', ['lead_deceleration', 'lead_stop_go'])
def test_real_child_trace_separates_command_and_response_clock(tmp_path, identity):
    trace, scene, frozen, fault = scene_trace(tmp_path / identity, identity)
    assert hil.validate_trace(trace, scene, frozen, 'faulted', fault) is trace
    assert 'scene_profile' not in trace['dut_initialization']
    events = {e['type']: e for e in trace['events'] if e['type'].startswith('lead_')}
    first = 'lead_deceleration_commanded' if identity == 'lead_deceleration' else 'lead_stop_commanded'
    assert events[first]['input_time_s'] == 4
    assert events[first]['time_s'] == pytest.approx(4.05)
    observed = 'lead_slowed' if identity == 'lead_deceleration' else 'lead_stopped'
    assert events[observed]['time_s'] == pytest.approx(4.30)
    assert events[observed]['provenance'] == 'native_response_speed'
    if identity == 'lead_stop_go':
        assert events['lead_restart_commanded']['time_s'] == pytest.approx(8.05)
        assert events['lead_moving_again']['time_s'] == pytest.approx(8.30)


@pytest.fixture
def retained_scene(tmp_path):
    return scene_trace(tmp_path / 'stop-go', 'lead_stop_go')


@pytest.mark.parametrize('mutate', [
    lambda t,s: t.update(scene_profile=profile('lead_deceleration')),
    lambda t,s: s['hil'].update(scene_profile=profile('lead_deceleration')),
    lambda t,s: t['frames'][85].update(lead_speed_mps=10),
    lambda t,s: t['rows'][85]['next_lead'].update(speed_mps=10),
    lambda t,s: t['frames'][80].update(lead_phase='cruise'),
    lambda t,s: t['events'].append({'type':'lead_moving_again','time_s':4.05}),
    lambda t,s: t['rows'][80].update(time_s=8),
])
def test_scene_measurement_and_event_tampering_rejected(retained_scene, mutate):
    trace, scene, frozen, fault = retained_scene
    mutate(trace, scene)
    with pytest.raises(ValueError):
        hil.validate_trace(trace, scene, frozen, 'faulted', fault)


def test_legacy_trace_has_no_scene_or_lead_signals(tmp_path):
    fault = {'kind': 'drop', 'start_s': 3.5, 'duration_s': 1, 'delay_ms': 0}
    trace, scene, frozen = authored_trace(tmp_path, 'baseline', fault)
    assert hil.validate_trace(trace, scene, frozen, 'baseline', fault) is trace
    assert 'scene_profile' not in trace
    assert not any('lead_speed_mps' in frame for frame in trace['frames'])
    trace['scene_profile'] = profile('lead_stop_go')
    with pytest.raises(ValueError, match='Legacy HIL trace'):
        hil.validate_trace(trace, scene, frozen, 'baseline', fault)


def test_acknowledged_but_wrong_lead_command_is_rejected_by_independent_replay(retained_scene):
    trace, scene, frozen, fault = retained_scene
    index = 100  # Stop interval: replace all copies of an acknowledged command.
    wrong = {'throttle': .4, 'brake': 0., 'steer': 0.}
    scene['frames'][index]['actors'][1]['control'] = copy.deepcopy(wrong)
    trace['rows'][index]['next_lead']['control'] = copy.deepcopy(wrong)
    trace['rows'][index]['command_batch'][1]['requested_control'] = copy.deepcopy(wrong)
    with pytest.raises(ValueError, match='Lead command differs'):
        hil.validate_trace(trace, scene, frozen, 'faulted', fault)


def test_native_standalone_bundle_loads_same_pure_descriptor_without_backend(tmp_path):
    # Import in an isolated interpreter, not a native launch or engine connection.
    source = Path(hil_native.__file__).parent
    for name in ('hil_native.py', 'hil_dut.py', 'hil_policy.py', 'hil_network.py', 'vecu_remote.py', 'city_native.py', 'city_control.py',
                 'city_maneuvers.py', 'reactive_planner.py'):
        shutil.copyfile(source / name, tmp_path / name)
    shutil.copyfile(hil_native.hil_scene_profiles.__file__, tmp_path / 'hil_scene_profiles.py')
    result = subprocess.run([sys.executable, '-I', '-B', '-c',
        "import sys,json;sys.path.insert(0,sys.argv[1]);import hil_native;"
        "print(json.dumps(hil_native.source_hashes(),sort_keys=True))", str(tmp_path)],
        text=True, capture_output=True, timeout=10, check=True)
    assert json.loads(result.stdout) == hil_native.source_hashes()
