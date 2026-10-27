"""Authored SDK-transition failures; these tests do not run a CARLA engine."""
import pytest

from test_diagnose_carla_throttle_isolation import (
    environment, diagnostic_module, isolation_environment, fresh_start,
    select_substep_override, SETTLED,
)

pytestmark = pytest.mark.parametrize('environment', [SETTLED], indirect=True)


@pytest.mark.parametrize('failure', ['before_apply', 'after_apply', 'two_frames'])
def test_settings_transition_failure_preserves_ack_and_restores_only_owned_settings(
        isolation_environment, monkeypatch, failure):
    runner, world, _, _ = isolation_environment
    select_substep_override(runner)
    initial, _ = fresh_start(isolation_environment, monkeypatch)
    handoff = runner.prepare(initial)
    original_apply = world.apply_settings

    def faulty_apply(settings):
        selected = settings.synchronous_mode and settings.max_substep_delta_time == .005
        if selected and failure == 'before_apply':
            raise RuntimeError('Injected error before settings application')
        result = original_apply(settings)
        if selected:
            world.tick(seconds=1.)
            if failure == 'after_apply':
                raise RuntimeError('Injected error after settings application')
            if failure == 'two_frames':
                world.tick(seconds=1.)
        return result

    monkeypatch.setattr(world, 'apply_settings', faulty_apply)
    with pytest.raises(RuntimeError):
        runner.measure(handoff)
    measurement = runner.report['measurement']
    assert len(measurement['observations']) == 1
    assert measurement['commands'][0]['application_status'] == 'sent_acknowledged'
    assert all(c['application_status'] == 'not_attempted' for c in measurement['commands'][1:])
    assert measurement['pi_state'] == runner.report['preparation']['pi_state']
    assert runner.report['continuation_physics']['status'] == 'failed_or_uncertain'
    runner.cleanup()
    assert runner.report['cleanup']['status'] == 'confirmed'
    assert runner.report['cleanup']['observed'] == runner.intent['expected_world']
    assert runner.finalize() == 1
    assert (runner.output / 'reservation-release.json').is_file()


def test_unknown_settings_are_not_adopted_for_cleanup(isolation_environment, monkeypatch):
    runner, world, _, _ = isolation_environment
    select_substep_override(runner)
    initial, _ = fresh_start(isolation_environment, monkeypatch)
    handoff = runner.prepare(initial)
    original_apply = world.apply_settings

    def unexpected_apply(settings):
        result = original_apply(settings)
        if settings.synchronous_mode and settings.max_substep_delta_time == .005:
            world.settings.fixed_delta_seconds = .06
            world.tick(seconds=1.)
        return result

    monkeypatch.setattr(world, 'apply_settings', unexpected_apply)
    with pytest.raises(RuntimeError, match='readback'):
        runner.measure(handoff)
    runner.cleanup()
    assert runner.report['cleanup']['status'] == 'uncertain'
    assert runner.finalize() == 1
    assert not (runner.output / 'reservation-release.json').exists()
    assert runner.quarantine.is_file()
