"""Explicit terminal brake taper for an experimental kernel2 controller.

Not registered as a built-in. An isolated commissioning registry must select
this exact release. Neither the stopping plan nor its hard guards are changed.
"""
from __future__ import annotations

from .stack_v2_components import ProportionalControlV2


class TerminalBrakeControl(ProportionalControlV2):
    id = 'terminal-brake-control'
    version = '1'
    name = 'Experimental speed-proportional terminal brake cap'
    brake_per_mps = 1.0
    limitations = [
        'Algorithm/1 caps only controlled-stop brake at1.0 times observed speed in m/s; this is a candidate, not a calibrated tire-lock boundary.',
        'Reducing brake may lengthen stopping distance or miss plan expiry; unchanged kernel guards must still force full braking.',
        'No positive throttle, GT obstacle input, filtering of observations or exclusion of low-speed quality metrics.',
    ]

    def metadata(self):
        metadata = super().metadata()
        return metadata.model_copy(update={'version': self.version, 'implementation': {
            'package': 'ade2e', 'algorithm': self.id + '/' + self.version,
            'kernel': 'stack-kernel/2', 'terminal_brake_per_mps': str(self.brake_per_mps)}})

    def control(self, packet, decision, parameters):
        base = super().control(packet, decision, parameters)
        if decision.force_brake or packet.ego is None or decision.mode != 'controlled_stop':
            return base
        return base.model_copy(update={'brake': min(base.brake, self.brake_per_mps * packet.ego.speed_mps)})
