"""Authored boundary checks for an opt-in experimental terminal brake cap."""
from types import SimpleNamespace as NS
import pytest

from ade2e.stack_runtime import StackRegistry
from ade2e.stack_v2_components import ControlParametersV2, ProportionalControlV2
from ade2e.terminal_brake import TerminalBrakeControl


def params():
    return ControlParametersV2(assumed_full_brake_deceleration_mps2=4.,
        brake_response_source='Authored contract test').model_dump(mode='json')


@pytest.mark.parametrize('speed', [0., .01, .1, .4, 1., 2.])
def test_cap_never_increases_braking_or_applies_throttle(speed):
    packet = NS(ego=NS(speed_mps=speed, route_heading_error_rad=.01))
    decision = NS(force_brake=False, mode='controlled_stop', target_speed_mps=0., target_acceleration_mps2=-.65)
    original = ProportionalControlV2().control(packet, decision, params())
    changed = TerminalBrakeControl().control(packet, decision, params())
    assert changed.brake == min(original.brake, speed)
    assert changed.throttle == 0 and changed.steer == original.steer


@pytest.mark.parametrize('mode', ['unknown_stop','emergency_stop','hold'])
def test_hard_stop_bypasses_tail_cap_even_at_zero_speed(mode):
    packet=NS(ego=NS(speed_mps=0., route_heading_error_rad=0.))
    decision=NS(force_brake=True, mode=mode)
    assert TerminalBrakeControl().control(packet, decision, params()).brake == 1.


def test_component_is_not_a_default_replacement():
    registry=StackRegistry(load_plugins=False)
    with pytest.raises(KeyError):registry.get('terminal-brake-control','1')
    registry.register(TerminalBrakeControl())
    assert type(registry.get('proportional-control','2')) is ProportionalControlV2
    assert registry.get('terminal-brake-control','1').metadata().implementation['kernel']=='stack-kernel/2'
