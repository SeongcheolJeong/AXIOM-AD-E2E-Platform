"""Model/case identity and behavior checks; no real engine launched."""
import copy
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import carla_vehicle_model_validation as m


def reference():
    return {'source_manifest_sha256': '1' * 64,
            'request': {'context': {'stack_snapshot': {'definition': {'controller': {'id': 'terminal-brake-control', 'version': '1'}}}}},
            'physics_profile': {'schema_version': 'carla-world-physics-candidate/2', 'synchronous_mode': True,
                'fixed_delta_seconds': .05, 'substepping': True, 'max_substep_delta_time': .001, 'max_substeps': 50}}


def selection():
    r = reference(); raw = m.canonical(r); hashes = {name: '2' * 64 for name in m.EXECUTION_FILES}
    return m.make_selection(r, raw, hashes, 'close-lead-1p5', 4.), r, raw, hashes


def test_same_model_across_cases_different_selection():
    a, r, raw, hashes = selection()
    b = m.make_selection(r, raw, hashes, 'cruise-2p0', 4.)
    c = m.make_selection(r, raw, hashes, 'cruise-2p0', 1000.)
    assert a['model_sha256'] == b['model_sha256'] != c['model_sha256']
    assert a['selection_sha256'] != b['selection_sha256']
    assert m.validate_selection(a, r, raw, hashes) == a


@pytest.mark.parametrize('change', ['revision', 'damping', 'source', 'binary', 'criteria', 'case', 'rollout', 'extra'])
def test_tampering_rejected(change):
    a, r, raw, hashes = selection()
    if change == 'revision': a['model']['revision'] = 2
    elif change == 'damping': a['model']['native_profile']['forward_damping_per_s'] = 0
    elif change == 'source': a['model']['execution_source_hashes']['native_sticky_damping_gdb.py'] = '3' * 64
    elif change == 'binary': a['model']['binary_sha256'] = '3' * 64
    elif change == 'criteria': a['case']['criteria']['max_jerk_mps3']['threshold'] = 100
    elif change == 'case': a['case']['scenario_overrides']['ego_speed_mps'] = 9
    elif change == 'rollout': a['model']['production_rollout'] = True
    else: a['silent_override'] = True
    with pytest.raises(ValueError): m.validate_selection(a, r, raw, hashes)


def test_slope_not_faked_and_original_is_immutable():
    with pytest.raises(ValueError, match='slope'): m.case_definition('slope-5deg')
    original = {'id': 'old', 'map_name': 'Town10HD_Opt', 'duration_s': 4., 'seed': 42,
                'parameters': {'friction': .8, 'ego_speed_mps': 2.}}
    frozen = copy.deepcopy(original)
    result = m.derive_scenario(original, 'close-lead-1p5')
    assert original == frozen
    assert result['parameters']['ego_speed_mps'] == 1.5 and result['parameters']['friction'] == .8
    assert result['id'] != original['id']


@pytest.mark.parametrize('case_id', list(m.CASES))
def test_behavior_gate_does_not_call_stationary_cruise_a_success(case_id):
    case = m.case_definition(case_id)
    policy = {'cruise_speed_mps': 2., 'max_speed_tracking_error_mps': .75, 'standstill_speed_mps': .03}
    rows = [{'mode': 'hold', 'speed_mps': 0.} for _ in range(81)]
    result = m.evaluate_behavior(case, rows, policy)
    assert result['verdict'] == ('failed' if case_id.startswith('cruise') else 'passed')
    rows[20]['mode'] = 'emergency_stop'
    assert m.evaluate_behavior(case, rows, policy)['verdict'] == 'failed'
    with pytest.raises(ValueError): m.evaluate_behavior(case, rows[:-1], policy)


def test_cruise_complete_and_hold_drift():
    p = {'cruise_speed_mps': 2., 'max_speed_tracking_error_mps': .75, 'standstill_speed_mps': .03}
    rows = [{'mode': 'pilot_cruise', 'speed_mps': 2.} for _ in range(81)]
    assert m.evaluate_behavior(m.case_definition('cruise-2p0'), rows, p)['verdict'] == 'passed'
    rows = [{'mode': 'hold', 'speed_mps': 0.} for _ in range(81)]
    rows[79]['speed_mps'] = .04
    assert m.evaluate_behavior(m.case_definition('close-lead-1p5'), rows, p)['verdict'] == 'failed'


@pytest.mark.parametrize('raw', [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}'])
def test_ambiguous_or_nonfinite_manifest_rejected(tmp_path, raw):
    p = tmp_path / 'manifest.json'; p.write_bytes(raw)
    with pytest.raises(ValueError): m.read_json(p)


def test_missing_and_symlink_inputs_rejected(tmp_path):
    with pytest.raises(ValueError): m.read_json(tmp_path / 'missing')
    target = tmp_path / 'target'; target.write_text('{}')
    link = tmp_path / 'link'; link.symlink_to(target)
    with pytest.raises(ValueError): m.read_json(link)


def manifest_files(root):
    (root / 'bundle').mkdir()
    manifest_raw = m.canonical({'files': {}})
    (root / 'bundle/candidate-source-manifest.json').write_bytes(manifest_raw)
    r = reference(); r['source_manifest_sha256'] = m.digest(manifest_raw)
    raw = m.canonical(r); (root / 'historical-intent.json').write_bytes(raw)
    for name in m.EXECUTION_FILES: (root / name).write_bytes(b'# authored fixture, never executed\n')
    hashes = {name: m.digest((root / name).read_bytes()) for name in m.EXECUTION_FILES}
    selected = m.make_selection(r, raw, hashes, 'close-lead-1p5', 4.)
    (root / 'model-validation.json').write_bytes(m.canonical(selected))
    (root / 'sticky-profile.json').write_bytes(m.canonical(selected['model']['native_profile']))
    return selected


@pytest.mark.parametrize('change', [None, 'code', 'native', 'bundle', 'reference'])
def test_preflight_checks_real_files_before_engine_start(tmp_path, change):
    selected = manifest_files(tmp_path)
    if change == 'code': (tmp_path / 'native_sticky_damping_gdb.py').write_bytes(b'# changed')
    elif change == 'native': (tmp_path / 'sticky-profile.json').write_bytes(m.canonical(m.profile(1000.)))
    elif change == 'bundle': (tmp_path / 'bundle/candidate-source-manifest.json').write_bytes(b'{}')
    elif change == 'reference': (tmp_path / 'historical-intent.json').write_bytes(b'{}')
    if change:
        with pytest.raises(ValueError): m.validate_selection_files(tmp_path)
    else:
        assert m.validate_selection_files(tmp_path) == selected


def test_launcher_requires_model_and_native_selection_together(tmp_path, monkeypatch):
    import launch_native_stop_observer as launcher
    monkeypatch.setattr(launcher.subprocess, 'Popen', lambda *a, **k: pytest.fail('Engine must not launch'))
    with pytest.raises(ValueError, match='explicit native'):
        launcher.launch(tmp_path, tmp_path/'binary', model_validation=True)
    with pytest.raises(ValueError, match='manifest'):
        launcher.launch(tmp_path, tmp_path/'binary', model_validation=True, sticky_damping=True)


def test_incomplete_suite_preserves_denominator_and_unknown_outcomes(tmp_path):
    import analyze_carla_model_validation as analyzer
    a, r, raw, hashes = selection()
    cases = [{'key': case+'-'+label, 'selection': m.make_selection(r, raw, hashes, case, damping)}
             for case in m.CASES for label, damping in [('sham', 1000.), ('candidate', 4.)]]
    suite = {'cases': cases, 'unsupported_cases': []}
    encoded = m.canonical(suite)
    (tmp_path/'suite.json').write_bytes(encoded)
    (tmp_path/'suite.sha256').write_text(m.digest(encoded))
    started = tmp_path / cases[0]['key']; started.mkdir(); (started/'launch-output.log').write_text('started')
    result = analyzer.analyze(tmp_path)
    assert result['planned_executions'] == 6 and result['candidate_denominator'] == 3
    assert result['verified_executions'] == 0 and result['suite_acceptance'] == 'not_accepted'
    assert result['cases'][0]['status'] == 'started_outcome_not_recovered'
    assert result['cases'][1]['status'] == 'not_executed'
    suite['cases'].pop(); encoded = m.canonical(suite)
    (tmp_path/'suite.json').write_bytes(encoded); (tmp_path/'suite.sha256').write_text(m.digest(encoded))
    with pytest.raises(ValueError, match='denominator'): analyzer.analyze(tmp_path)
