"""Opt-in kernel2 brake tracking with measured acceleration feedback.

Not registered as a built-in: native commissioning did not improve the tail.
This controller never edits the stopping plan, its guard, or retained metrics.
It uses the packet-speed derivative already recorded by the shared kernel,
not CARLA wheel telemetry. The response gain remains an explicit assumption.
"""
from __future__ import annotations

import math

from .stack_common_schemas import StackControl
from .stack_v2_components import ControlParametersV2, ProportionalControlV2


class AccelerationFeedbackControl(ProportionalControlV2):
    id = 'acceleration-feedback-control'
    version = '1'
    name = 'Experimental acceleration feedback · not accepted'
    acceleration_feedback_gain = 0.5
    limitations = [
        'Experimental feedback using consecutive packet-speed differences, not measured wheel torque or a calibrated brake map.',
        'Algorithm/1 fixes dimensionless acceleration feedback gain0.5; noisy or delayed speed may worsen response.',
        'Hard stop, unknown input, plan expiry and standstill hold still command full brake through the unchanged kernel guard.',
        'No positive throttle during controlled stop; no filtering, clipping or removal of recorded kinematic metrics.',
    ]

    def metadata(self):
        metadata = super().metadata()
        return metadata.model_copy(update={'version': self.version, 'implementation': {
            'package': 'ade2e', 'algorithm': self.id + '/' + self.version,
            'kernel': 'stack-kernel/2', 'acceleration_feedback_gain': str(self.acceleration_feedback_gain)}})

    def control(self, packet, decision, parameters):
        p = ControlParametersV2.model_validate(parameters)
        base = super().control(packet, decision, parameters)
        if decision.force_brake or packet.ego is None or decision.mode != 'controlled_stop':
            return base
        gain = p.assumed_full_brake_deceleration_mps2
        observed = decision.observed_speed_acceleration_mps2
        if gain is None or observed is None:
            return base
        if not math.isfinite(observed):
            raise ValueError('Observed acceleration must be finite')
        # u = feed-forward + speed-error feedback + acceleration-error feedback.
        # Positive correction increases deceleration; excess measured braking
        # reduces the next brake request. Saturate the sum only, not its terms.
        feedforward = -decision.target_acceleration_mps2 / gain
        speed_feedback = p.brake_speed_gain * (packet.ego.speed_mps - decision.target_speed_mps)
        acceleration_feedback = self.acceleration_feedback_gain * (
            observed - decision.target_acceleration_mps2) / gain
        brake = min(1.0, max(0.0, feedforward + speed_feedback + acceleration_feedback))
        return StackControl(throttle=0.0, brake=brake, steer=base.steer)
