"""Opt-in causal probe, not a production controller or quality improvement.

Retain terminal-brake-control/1 above 0.1m/s, then release its brake in
controlled_stop only. Plan expiry and every force-brake guard still win.
This asks whether brake application is necessary for the low-speed snap.
The threshold is above the previously observed 0.074m/s transition, not
a fitted comfort limit. Never register this probe by default.
"""
from __future__ import annotations

from .terminal_brake import TerminalBrakeControl


class TerminalBrakeReleaseProbe(TerminalBrakeControl):
    id = 'terminal-brake-release-probe'
    version = '1'
    name = 'Diagnostic brake release below 0.1mps'
    release_below_mps = 0.1
    limitations = [
        'Causal intervention only: brake release can miss the original stop deadline.',
        'Unknown input, emergency and hold always retain full braking; no positive throttle.',
        'Same terminal cap above0.1m/s; no velocity/position override or discarded samples.',
    ]

    def metadata(self):
        metadata = super().metadata()
        return metadata.model_copy(update={'implementation': {
            **metadata.implementation,
            'release_below_mps': str(self.release_below_mps),
            'purpose': 'diagnostic_not_production',
        }})

    def control(self, packet, decision, parameters):
        base = super().control(packet, decision, parameters)
        if (not decision.force_brake and packet.ego is not None
                and decision.mode == 'controlled_stop'
                and packet.ego.speed_mps < self.release_below_mps):
            return base.model_copy(update={'brake': 0.0})
        return base


class TerminalBrakeSingleReleaseProbe(TerminalBrakeReleaseProbe):
    """One predeclared 50ms release at source sequence37; never auto-tuned.

    Sequence37 is the first below0.1m/s observation in the retained cap/1
    baseline. This is a fixed intervention in that experiment, not a
    reusable vehicle controller. A changed entry is explicitly rejected.
    """
    version = '2'
    name = 'Diagnostic single-interval terminal brake release'
    release_sequence = 37
    limitations = [
        'One predeclared sequence37 intervention anchored to the retained close-lead baseline.',
        'Not a deployable time-indexed vehicle controller and not a general stopping solution.',
        'Hard stops take precedence; no positive throttle or velocity/position override.',
    ]

    def metadata(self):
        metadata = super().metadata()
        return metadata.model_copy(update={'implementation': {
            **metadata.implementation, 'release_sequence': str(self.release_sequence),
            'release_intervals': '1',
        }})

    def control(self, packet, decision, parameters):
        base = TerminalBrakeControl.control(self, packet, decision, parameters)
        if decision.force_brake or packet.ego is None or decision.mode != 'controlled_stop':
            return base
        if packet.sequence == self.release_sequence:
            if not .08 < packet.ego.speed_mps < self.release_below_mps:
                raise ValueError('Single-release probe entry differs from the predeclared0.08..0.1mps range')
            return base.model_copy(update={'brake': 0.0})
        return base
