"""Probe contract over authored inputs, not actual CARLA dynamics."""
from types import SimpleNamespace as NS

import pytest

from ade2e.stack_runtime import StackRegistry
from ade2e.terminal_brake import TerminalBrakeControl
from ade2e.terminal_release_probe import TerminalBrakeReleaseProbe
from ade2e.terminal_release_probe import TerminalBrakeSingleReleaseProbe
from test_terminal_brake import params


@pytest.mark.parametrize('speed', [0.0, .074, .099999, .1, .2, 1.])
def test_only_declared_low_speed_brake_changes(speed):
    packet = NS(ego=NS(speed_mps=speed, route_heading_error_rad=.03))
    decision = NS(force_brake=False, mode='controlled_stop', target_speed_mps=0.,
                  target_acceleration_mps2=-.65)
    baseline = TerminalBrakeControl().control(packet, decision, params())
    probe = TerminalBrakeReleaseProbe().control(packet, decision, params())
    assert probe.brake == (0. if speed < .1 else baseline.brake)
    assert probe.throttle == baseline.throttle == 0.
    assert probe.steer == baseline.steer


@pytest.mark.parametrize('mode', ['unknown_stop', 'emergency_stop', 'hold'])
def test_hard_stop_still_overrides_probe(mode):
    packet = NS(ego=NS(speed_mps=.074, route_heading_error_rad=0.))
    result = TerminalBrakeReleaseProbe().control(packet, NS(force_brake=True, mode=mode), params())
    assert result.brake == 1. and result.throttle == 0.


def test_probe_is_not_registered_or_implicitly_selected():
    registry = StackRegistry(load_plugins=False)
    with pytest.raises(KeyError):
        registry.get('terminal-brake-release-probe', '1')
    registry.register(TerminalBrakeReleaseProbe())
    assert registry.get('terminal-brake-release-probe', '1').metadata().implementation[
        'purpose'] == 'diagnostic_not_production'


@pytest.mark.parametrize('sequence', [0, 36, 37, 38, 43, 79])
def test_single_release_is_exactly_one_predeclared_command(sequence):
    packet = NS(sequence=sequence, ego=NS(speed_mps=.092, route_heading_error_rad=0.))
    decision = NS(force_brake=False, mode='controlled_stop', target_speed_mps=.077,
                  target_acceleration_mps2=-.556)
    base = TerminalBrakeControl().control(packet, decision, params())
    probe = TerminalBrakeSingleReleaseProbe().control(packet, decision, params())
    assert probe.brake == (0. if sequence == 37 else base.brake)


@pytest.mark.parametrize('speed', [.01, .08, .1, 1.])
def test_single_release_rejects_unmatched_state(speed):
    packet = NS(sequence=37, ego=NS(speed_mps=speed, route_heading_error_rad=0.))
    decision = NS(force_brake=False, mode='controlled_stop', target_speed_mps=.077,
                  target_acceleration_mps2=-.556)
    with pytest.raises(ValueError, match='entry differs'):
        TerminalBrakeSingleReleaseProbe().control(packet, decision, params())


@pytest.mark.parametrize('mode', ['unknown_stop', 'emergency_stop', 'hold'])
def test_single_release_never_bypasses_hard_stop_or_checks_entry_first(mode):
    packet = NS(sequence=37, ego=NS(speed_mps=1., route_heading_error_rad=0.))
    decision = NS(force_brake=True, mode=mode)
    result = TerminalBrakeSingleReleaseProbe().control(packet, decision, params())
    assert result.brake == 1. and result.throttle == 0.
