"""Authored controller/guard checks, not CARLA or vehicle calibration."""
from types import SimpleNamespace as NS

import pytest

from ade2e.brake_feedback import AccelerationFeedbackControl
from ade2e.stack_runtime import StackRegistry
from ade2e.stack_v2_components import ControlParametersV2, ProportionalControlV2


def parameters():
    return ControlParametersV2(assumed_full_brake_deceleration_mps2=4.0,
        brake_response_source='Authored test gain; not native evidence').model_dump(mode='json')


def inputs(*, speed=.4, target=.03, acceleration=-1.36, mode='controlled_stop', force=False):
    return NS(ego=NS(speed_mps=speed, route_heading_error_rad=.01)), NS(
        mode=mode, force_brake=force, target_speed_mps=target,
        target_acceleration_mps2=-.35, observed_speed_acceleration_mps2=acceleration)


def test_new_release_does_not_replace_legacy_control():
    registry = StackRegistry(load_plugins=False)
    assert type(registry.get('proportional-control', '2')) is ProportionalControlV2
    with pytest.raises(KeyError):
        registry.get('acceleration-feedback-control', '1')
    registry.register(AccelerationFeedbackControl())
    assert type(registry.get('acceleration-feedback-control', '1')) is AccelerationFeedbackControl
    assert registry.get('acceleration-feedback-control', '1').metadata().implementation['kernel'] == 'stack-kernel/2'


def test_excess_deceleration_reduces_brake_without_positive_throttle():
    packet, decision = inputs()
    original = ProportionalControlV2().control(packet, decision, parameters())
    candidate = AccelerationFeedbackControl().control(packet, decision, parameters())
    assert candidate.brake == pytest.approx(original.brake + .5*(-1.36+.35)/4)
    assert 0 <= candidate.brake < original.brake and candidate.throttle == 0
    assert candidate.steer == original.steer


@pytest.mark.parametrize('mode', ['hold', 'emergency_stop', 'unknown_stop'])
def test_forced_braking_is_never_softened(mode):
    packet, decision = inputs(mode=mode, force=True)
    result = AccelerationFeedbackControl().control(packet, decision, parameters())
    assert result.brake == 1 and result.throttle == result.steer == 0


@pytest.mark.parametrize('acceleration', [None, -100., 100.])
def test_unknown_and_extreme_feedback_has_bounded_output(acceleration):
    packet, decision = inputs(acceleration=acceleration)
    result = AccelerationFeedbackControl().control(packet, decision, parameters())
    assert 0 <= result.brake <= 1 and result.throttle == 0
    if acceleration is None:
        assert result == ProportionalControlV2().control(packet, decision, parameters())


def test_cruise_is_exactly_legacy_control():
    packet, decision = inputs(mode='pilot_cruise', target=2.)
    assert AccelerationFeedbackControl().control(packet, decision, parameters()) == ProportionalControlV2().control(packet, decision, parameters())
