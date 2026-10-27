"""Consecutive-prefix, stateful kernel/2 shared by MCAP replay and native CARLA."""
from __future__ import annotations
import copy
import hashlib
import math
import struct
import time
from .stack_common_schemas import MAX_STACK_POINTS, StackPacket, StackControl, StackComponentMetadata
from .stack_v2_schemas import (StackDefinitionV2, StackOutputV2, CorridorObservationV2,
    MotionEnvelopeV2, StackStateV2)
from .stack_v2_components import (CorridorParametersV2, HeadwayParametersV2, ControlParametersV2,
    grid_observation, initial_state, StoppingHeadwayV2)


def _validated(model,value):
    return model.model_validate(value.model_dump(mode='python') if hasattr(value,'model_dump') else value)


def _validate_control_limits(control, decision, parameters):
    """The saved recipe limits apply equally to plugins and retained evidence."""
    if control.throttle > parameters.max_throttle:
        raise ValueError('Controller exceeded pinned max_throttle')
    if abs(control.steer) > parameters.max_steer:
        raise ValueError('Controller exceeded pinned max_steer')
    if decision.mode == 'controlled_stop' and control.throttle > 0:
        raise ValueError('Controlled stop cannot apply positive throttle')


class StackSessionV2:
    """State lives only in this session and every output includes both snapshots.

    Kernel2 roles share the versioned grid, motion-envelope and response parameter
    contracts. A plugin may stop more conservatively; it may not remove hard input
    or full-stop envelope checks. Exceptions poison the session: restart/replay
    from the original prefix, never resume an unobserved intermediate state.
    """
    def __init__(self,definition,registry=None):
        from .stack_runtime import StackRegistry
        self.definition=_validated(StackDefinitionV2,definition).model_copy(deep=True)
        self.registry=registry or StackRegistry()
        self.components=self.registry.validate_definition(self.definition)
        self._resolved=[]
        for role,params_type in zip(('perception','policy','controller'),(CorridorParametersV2,HeadwayParametersV2,ControlParametersV2)):
            selection=getattr(self.definition,role)
            component=self.registry.get(selection.id,selection.version)
            params=component.validate_parameters(copy.deepcopy(selection.parameters))
            # These common kernel2 safety parameters remain mandatory even when
            # an installed component provides a more conservative policy.
            normalized=params_type.model_validate(params).model_dump(mode='json')
            self._resolved.append((component,normalized))
        self._state=initial_state();self._failed=False

    @property
    def state(self):return self._state.model_copy(deep=True)

    def _assert_metadata(self):
        from .stack_runtime import StackExecutionError
        for (component,_),pinned in zip(self._resolved,self.components):
            if _validated(StackComponentMetadata,component.metadata())!=pinned:
                raise StackExecutionError('Component metadata changed after kernel2 session creation')

    def step(self,packet,points_bytes=None):
        from .stack_runtime import StackExecutionError
        if self._failed:raise StackExecutionError('Kernel2 session failed; replay from its original prefix')
        try:return self._step(packet,points_bytes)
        except Exception:
            self._failed=True
            raise

    def _step(self,packet,points_bytes):
        from .stack_runtime import StackInputError,StackExecutionError
        start=time.monotonic();packet=_validated(StackPacket,packet).model_copy(deep=True)
        before=self._state.model_copy(deep=True);at=int(packet.at_ns)
        expected=0 if before.last_sequence is None else before.last_sequence+1
        if packet.sequence!=expected or before.last_at_ns is not None and at<=int(before.last_at_ns):
            raise StackInputError('Kernel2 requires sequence0 then every consecutive sequence and increasing time')
        dt=None if before.last_at_ns is None else (at-int(before.last_at_ns))/1e9
        self._assert_metadata()
        perception,policy,controller=self._resolved
        p=CorridorParametersV2.model_validate(perception[1]);c=ControlParametersV2.model_validate(controller[1])
        reasons=list(packet.unavailable_reasons)
        if p.ground_reference_z_m is None:reasons.append('ground_reference_unknown')
        if packet.ego is None:reasons.append('ego_unavailable')
        else:
            age=at-int(packet.ego.timestamp_ns)
            if age<0:reasons.append('ego_from_future')
            elif age>round(p.max_ego_age_ms*1e6):reasons.append('ego_stale')
            if packet.ego.frame_id!=self.definition.reference_frame:reasons.append('ego_frame_mismatch')
            if max(abs(packet.ego.pitch_rad),abs(packet.ego.roll_rad))>p.max_abs_pitch_roll_rad:
                reasons.append('flat_road_attitude_limit_exceeded')
        points=();consumed_hash=None
        if packet.lidar is None:reasons.append('lidar_unavailable')
        else:
            lidar=packet.lidar;age=at-int(lidar.timestamp_ns)
            if age<0:reasons.append('lidar_from_future')
            elif age>round(p.max_lidar_age_ms*1e6):reasons.append('lidar_stale')
            if lidar.frame_id!=self.definition.reference_frame:reasons.append('lidar_frame_mismatch')
            if not lidar.complete_frame:reasons.append('lidar_incomplete_or_decimated')
            if packet.ego and abs(int(packet.ego.timestamp_ns)-int(lidar.timestamp_ns))>round(p.max_input_skew_ms*1e6):
                reasons.append('input_time_skew_exceeded')
            if type(points_bytes) is not bytes:reasons.append('lidar_buffer_unavailable')
            elif len(points_bytes)!=lidar.size_bytes or len(points_bytes)>MAX_STACK_POINTS*12:reasons.append('lidar_buffer_size_mismatch')
            elif hashlib.sha256(points_bytes).hexdigest()!=lidar.points_sha256:reasons.append('lidar_buffer_hash_mismatch')
            else:
                consumed_hash=lidar.points_sha256;points=tuple(struct.iter_unpack('<fff',points_bytes))
                if any(not math.isfinite(v) or abs(v)>1e6 for point in points for v in point):
                    reasons.append('lidar_nonfinite_or_out_of_range');points=()
                if not points:reasons.append('lidar_no_valid_returns')
        reasons=list(dict.fromkeys(reasons))[:30]
        try:
            expected_observation=grid_observation(points,p,reasons)
            if reasons:observation=expected_observation
            else:
                observation=_validated(CorridorObservationV2,perception[0].observe(packet.model_copy(deep=True),points,copy.deepcopy(perception[1])))
                self._assert_metadata()
                # Grid/height-band semantics name exact retained samples. An
                # internally coherent but fabricated prefix cannot authorize motion.
                if observation!=expected_observation:raise ValueError('Grid observation differs from retained source geometry')
            guard=StoppingHeadwayV2().plan(packet,observation,policy[1],state=before,dt_s=dt)
            decision=_validated(MotionEnvelopeV2,policy[0].plan(packet.model_copy(deep=True),observation.model_copy(deep=True),
                copy.deepcopy(policy[1]),state=before.model_copy(deep=True),dt_s=dt))
            self._assert_metadata()
            if c.assumed_full_brake_deceleration_mps2 is None:
                decision=_validated(MotionEnvelopeV2,{**guard.model_dump(mode='python'),
                    'mode':'unknown_stop','target_speed_mps':0.,'target_acceleration_mps2':0.,
                    'target_jerk_mps3':0.,'force_brake':True,
                    'reasons':['brake_response_assumption_unknown',*guard.reasons]})
            elif guard.force_brake:
                decision=guard
            else:
                # A stricter full stop is allowed. A moving plugin cannot change
                # model-distance, timing, or reset a stop plan to evade its envelope.
                keys=('qualified_prefix_m','positive_surface_distance_m','limiting_horizon_m','reserve_m',
                    'available_stopping_distance_m','current_stopping_distance_m','planned_stopping_distance_m',
                    'applied_dt_s','observed_speed_acceleration_mps2','allowed_positive_acceleration_mps2')
                if any(getattr(decision,k)!=getattr(guard,k) for k in keys):raise ValueError('Policy changed the core stopping safety envelope')
                if not decision.force_brake:
                    if decision.mode!=guard.mode or decision.stop_plan!=guard.stop_plan:
                        raise ValueError('Policy changed latched model timing/mode')
                    if decision.target_speed_mps>guard.target_speed_mps+1e-9:raise ValueError('Policy exceeded bounded target speed')
            control=_validated(StackControl,controller[0].control(packet.model_copy(deep=True),decision.model_copy(deep=True),copy.deepcopy(controller[1])))
            self._assert_metadata()
            if decision.force_brake:control=StackControl(throttle=0.,brake=1.,steer=0.)
            _validate_control_limits(control, decision, c)
            mode='ready' if decision.mode=='pilot_cruise' else decision.mode
            latch_reason=before.latch_reason if before.latched else decision.reasons[0] if mode!='ready' else None
            after=StackStateV2(mode=mode,latched=mode!='ready',latch_reason=latch_reason,
                stop_plan=decision.stop_plan,last_sequence=packet.sequence,last_at_ns=packet.at_ns,
                reset_policy='new_session_only',previous_speed_mps=packet.ego.speed_mps if packet.ego else None,
                previous_target_acceleration_mps2=decision.target_acceleration_mps2)
            output=StackOutputV2(kernel_version='stack-kernel/2',sequence=packet.sequence,at_ns=packet.at_ns,
                components=[v.model_copy(deep=True) for v in self.components],input_points_sha256=consumed_hash,
                observation=observation,decision=decision,control=control,state_before=before,state_after=after,
                warnings=reasons,elapsed_ms=(time.monotonic()-start)*1000)
        except Exception as exc:
            raise StackExecutionError(f'Kernel2 component contract failed: {exc}') from exc
        self._state=after.model_copy(deep=True)
        return output


def validate_v2_source_evidence(definition,packet,points_bytes,output):
    """Pure retained-evidence cross-check; does not load or execute plugins.

    Internal grid/plan/state checks always run through StackOutputV2. For valid
    inputs, exactly recompute named ground-hit-grid/1 geometry from the retained
    consumed bytes and pinned perception parameters. Caller separately checks
    previous state continuity and outer archive/source correlation.
    """
    from .stack_v2_components import stopping_distance_bound
    definition=_validated(StackDefinitionV2,definition)
    output=_validated(StackOutputV2,output)
    c=ControlParametersV2.model_validate(definition.controller.parameters)
    _validate_control_limits(output.control, output.decision, c)
    if packet is None:
        if output.observation.input_valid or output.decision.mode not in ('unknown_stop','emergency_stop','hold') or not output.decision.force_brake:
            raise ValueError('Missing input packet cannot have valid or moving evidence')
        return output
    packet=_validated(StackPacket,packet)
    if (packet.sequence,packet.at_ns)!=(output.sequence,output.at_ns):
        raise ValueError('Output changed packet sequence/time')
    if output.state_after.previous_speed_mps!=(packet.ego.speed_mps if packet.ego else None):
        raise ValueError('State measured speed differs from packet')
    if output.input_points_sha256 is not None:
        if type(points_bytes) is not bytes or hashlib.sha256(points_bytes).hexdigest()!=output.input_points_sha256:
            raise ValueError('Consumed buffer hash differs from output')
    p=CorridorParametersV2.model_validate(definition.perception.parameters)
    h=HeadwayParametersV2.model_validate(definition.policy.parameters)
    if output.observation.input_valid:
        if packet.ego is None or packet.lidar is None or packet.unavailable_reasons or type(points_bytes) is not bytes:
            raise ValueError('Valid observation lacks complete input')
        lidar=packet.lidar;at=int(packet.at_ns)
        if not lidar.complete_frame or len(points_bytes)!=lidar.size_bytes or output.input_points_sha256!=lidar.points_sha256:
            raise ValueError('Valid observation changed full source identity')
        for sensor,limit in ((packet.ego,p.max_ego_age_ms),(lidar,p.max_lidar_age_ms)):
            if sensor.frame_id!=definition.reference_frame or not 0<=at-int(sensor.timestamp_ns)<=round(limit*1e6):
                raise ValueError('Valid observation has stale/future/wrong-frame source')
        if abs(int(packet.ego.timestamp_ns)-int(lidar.timestamp_ns))>round(p.max_input_skew_ms*1e6) or max(abs(packet.ego.pitch_rad),abs(packet.ego.roll_rad))>p.max_abs_pitch_roll_rad:
            raise ValueError('Valid observation exceeds skew or flat-road attitude')
        points=tuple(struct.iter_unpack('<fff',points_bytes))
        if not points or any(not math.isfinite(v) or abs(v)>1e6 for point in points for v in point):
            raise ValueError('Valid observation has empty/nonfinite/out-of-range points')
        expected=grid_observation(points,p)
        if output.observation!=expected:raise ValueError('Retained grid/positive surface differs from actual consumed points')
    # Pinned safety assumptions cannot be replaced by a self-consistent forged
    # lower required distance. Zero-speed fallback still preserves its model.
    speed=packet.ego.speed_mps if packet.ego else 0.
    for actual,expected in ((output.decision.current_stopping_distance_m,stopping_distance_bound(speed,h)),
        (output.decision.planned_stopping_distance_m,stopping_distance_bound(max(speed,h.cruise_speed_mps),h)),
        (output.decision.reserve_m,h.reserve_m),
        (output.decision.allowed_positive_acceleration_mps2,h.allowed_positive_acceleration_mps2)):
        if actual is None or not math.isclose(actual,expected,rel_tol=1e-10,abs_tol=1e-9):
            raise ValueError('Output stopping envelope differs from pinned model parameters')
    guard=StoppingHeadwayV2().plan(packet,output.observation,definition.policy.parameters,
        state=output.state_before,dt_s=output.decision.applied_dt_s)
    if guard.force_brake and not output.decision.force_brake:
        raise ValueError('Output bypassed a hard model/ODD/timing/plan-expiry stop')
    if not output.decision.force_brake:
        if output.decision.mode!=guard.mode or output.decision.stop_plan!=guard.stop_plan or output.decision.target_speed_mps>guard.target_speed_mps+1e-9:
            raise ValueError('Output changed core guarded plan/mode/target')
    if c.assumed_full_brake_deceleration_mps2 is None and (output.decision.mode!='unknown_stop' or not output.decision.force_brake):
        raise ValueError('Undeclared brake response cannot authorize motion')
    return output
