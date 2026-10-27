"""Candidate plumbing over authored worlds, never actual vehicle performance."""
import copy
import json

import pytest

from ade2e.adapters import AdapterExecutionError, AdapterInputError
from ade2e.simulation_evidence import validate_simulation_artifacts
from axiom_carla.physics_candidate import CandidateEvidence, profile
from axiom_carla.runner import CarlaRunner
from axiom_carla.version import ADAPTER_VERSION
from test_configured_sensor_live import configured_request, install_configured_world


@pytest.mark.parametrize('step', [.01, .005, .001, .0005])
def test_applied_before_rest_and_every_measured_frame(monkeypatch, carla_scenario, step):
    world = install_configured_world(monkeypatch)
    request = configured_request(carla_scenario)
    original = copy.deepcopy(world.settings)
    result = CarlaRunner().execute_physics_candidate(request, profile(step))
    validate_simulation_artifacts(request, result.artifact_payloads, completed=True,
        trajectory=result.trajectory, adapter_version=ADAPTER_VERSION)
    values = {p.name: p.content for p in result.artifact_payloads}
    evidence = json.loads(values['carla-physics-candidate.json'])
    assert len(evidence['readbacks']) == 83
    assert evidence['readbacks'][0]['phase'] == 'before_actor_spawn'
    assert evidence['readbacks'][1]['phase'] == 'after_rest_before_target_velocity'
    assert all(r['matched'] and r['settings']['values']['max_substep_delta_time'] == step for r in evidence['readbacks'])
    assert evidence['candidate_input_hash'] != request.input_hash
    assert len(result.trajectory) == 81
    assert world.settings == original
    assert 'STANDALONE physics candidate' in result.model_fidelity


def test_default_execution_has_no_candidate_artifact(monkeypatch, carla_scenario):
    install_configured_world(monkeypatch)
    result = CarlaRunner().execute_with_context(configured_request(carla_scenario))
    assert all(p.name != 'carla-physics-candidate.json' for p in result.artifact_payloads)
    assert 'STANDALONE physics candidate' not in result.model_fidelity


@pytest.mark.parametrize('value', [True, 0, .0025, float('nan'), '.005'])
def test_unsupported_substeps_rejected(value):
    with pytest.raises(AdapterInputError):
        profile(value)


@pytest.mark.parametrize('field,value', [('max_substeps', 9), ('max_substeps', True),
    ('fixed_delta_seconds', .1), ('substepping', False), ('synchronous_mode', 1), ('extra', 'ignored')])
def test_invalid_profile_fails_before_connect(monkeypatch, carla_scenario, field, value):
    request = configured_request(carla_scenario)
    value_profile = profile(.005); value_profile[field] = value
    monkeypatch.setattr(CarlaRunner, '_connect', lambda _: pytest.fail('must not contact the engine'))
    with pytest.raises(AdapterInputError):
        CarlaRunner().execute_physics_candidate(request, value_profile)


def test_profile_changes_candidate_identity_not_base_request(carla_scenario):
    request = configured_request(carla_scenario)
    a, b = (CandidateEvidence(profile(step), request, .05) for step in (.01, .005))
    assert a.data['base_input_hash'] == b.data['base_input_hash']
    assert a.data['candidate_input_hash'] != b.data['candidate_input_hash']


def test_precision_profile_is_explicit_without_changing_v1_defaults():
    assert profile(.005) == {'schema_version': 'carla-world-physics-candidate/1',
        'synchronous_mode': True, 'fixed_delta_seconds': .05, 'substepping': True,
        'max_substep_delta_time': .005, 'max_substeps': 10}
    for step, count in ((.001, 50), (.0005, 100)):
        value = profile(step)
        assert value['schema_version'] == 'carla-world-physics-candidate/2'
        assert value['max_substeps'] == count
        assert value['fixed_delta_seconds'] <= step * count


def test_readback_mismatch_retained_before_any_vehicle_spawn(monkeypatch, carla_scenario):
    world = install_configured_world(monkeypatch)
    original = world.apply_settings
    def faulty(settings):
        original(settings)
        if settings.synchronous_mode:
            world.settings.max_substeps = 9
    world.apply_settings = faulty
    with pytest.raises(AdapterExecutionError) as failure:
        CarlaRunner().execute_physics_candidate(configured_request(carla_scenario), profile(.005))
    payload = next(p for p in failure.value.artifact_payloads if p.name == 'carla-physics-candidate.json')
    evidence = json.loads(payload.content)
    assert evidence['status'] == 'readback_failed'
    assert not evidence['readbacks'][0]['matched']
    assert not world.vehicles
