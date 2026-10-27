"""Replaceable pure-Python sensor/decision/control kernel shared by replay and CARLA.

No CARLA imports or surrounding actor state cross this boundary. Buffer/hash errors
produce explicit unknown-stop evidence, while broken component contracts fail execution.
"""
from __future__ import annotations

import hashlib
import copy
import json
import math
import struct
import time
from importlib.metadata import entry_points
from typing import Protocol

from .stack_components import LidarCorridor, StoppingHeadway, ProportionalControl, CorridorParameters
from .stack_schemas import (KERNEL_VERSION, MAX_STACK_POINTS, CorridorObservation,
    StackComponentMetadata, StackControl, StackDecision, StackDefinition, StackOutput, StackPacket,
    validate_stack_definition, stack_kernel_version)


class StackInputError(ValueError):
    """Unsupported stack definition or invalid sequence envelope."""


class StackExecutionError(RuntimeError):
    """A component failed or returned an invalid contract."""


class StackComponent(Protocol):
    def metadata(self) -> StackComponentMetadata: ...
    def validate_parameters(self, parameters: dict) -> dict: ...


class PerceptionComponent(StackComponent, Protocol):
    def observe(self, packet: StackPacket, points: tuple[tuple[float, float, float], ...], parameters: dict) -> CorridorObservation: ...


class PolicyComponent(StackComponent, Protocol):
    def plan(self, packet: StackPacket, observation: CorridorObservation, parameters: dict) -> StackDecision: ...


class ControllerComponent(StackComponent, Protocol):
    def control(self, packet: StackPacket, decision: StackDecision, parameters: dict) -> StackControl: ...


def _validated(model, value):
    # Pydantic normally trusts an existing model instance, including model_construct
    # or later mutable fields. Plugin output must cross a real validation boundary.
    return model.model_validate(value.model_dump(mode="python") if isinstance(value, model) else value)


class StackRegistry:
    """Keep exact component releases side by side in registration order.

    ``replace=True`` replaces only the same (ID, version). Omitting a version
    is supported only when one release of that ID is registered; there is no
    implicit latest-version selection. Persisted definitions always pin both.
    """

    def __init__(self, load_plugins=True):
        self._components: dict[tuple[str, str], StackComponent] = {}
        for component in (LidarCorridor(), StoppingHeadway(), ProportionalControl()):
            self.register(component)
        from .stack_v2_components import v2_components
        for component in v2_components():
            self.register(component)
        if load_plugins:
            for entry in entry_points(group="ade2e.stack_components"):
                factory = entry.load()
                self.register(factory() if callable(factory) else factory, replace=True)

    def register(self, component, *, replace=False):
        metadata = _validated(StackComponentMetadata, component.metadata())
        method = {"perception": "observe", "policy": "plan", "controller": "control"}[metadata.role]
        if not callable(getattr(component, "validate_parameters", None)) or not callable(getattr(component, method, None)):
            raise TypeError(f"Stack component must implement validate_parameters and {method}")
        key = (metadata.id, metadata.version)
        if key in self._components and not replace:
            raise ValueError("Component ID/version is already registered; use replace=True explicitly")
        self._components[key] = component

    @staticmethod
    def _registered_metadata(key, component):
        metadata = _validated(StackComponentMetadata, component.metadata())
        if (metadata.id, metadata.version) != key:
            raise KeyError("Registered stack component identity changed; register the exact ID/version explicitly")
        return metadata

    def list(self):
        return [self._registered_metadata(key, item) for key, item in self._components.items()]

    def get(self, component_id, version=None):
        if version is None:
            keys = [key for key in self._components if key[0] == component_id]
            if len(keys) > 1:
                raise KeyError("Multiple versions of stack component are registered; specify an exact version")
            key = keys[0] if keys else (component_id, None)
        else:
            key = (component_id, version)
        component = self._components.get(key)
        if component is None:
            raise KeyError("Stack component ID/version is unavailable")
        self._registered_metadata(key, component)
        return component

    def validate_definition(self, definition):
        definition = validate_stack_definition(definition)
        kernel = stack_kernel_version(definition)
        metadata = []
        for role in ("perception", "policy", "controller"):
            selection = getattr(definition, role)
            try:
                component = self.get(selection.id, selection.version)
            except KeyError as exc:
                raise StackInputError(str(exc)) from exc
            item = _validated(StackComponentMetadata, component.metadata())
            if item.role != role or item.status != "available":
                raise StackInputError("Component role/status does not match this stack")
            if item.implementation.get("kernel", KERNEL_VERSION) != kernel:
                raise StackInputError("Component kernel does not match the explicitly selected stack kernel")
            normalized = component.validate_parameters(copy.deepcopy(selection.parameters))
            if kernel == "stack-kernel/2":
                # Kernel2 is an explicit recipe: plugins retain the same grid,
                # stopping-envelope and control parameter contracts. Reject an
                # incompatible recipe before saving or dispatching any work.
                from .stack_v2_components import (
                    CorridorParametersV2, HeadwayParametersV2, ControlParametersV2)
                parameter_type = {"perception": CorridorParametersV2,
                    "policy": HeadwayParametersV2, "controller": ControlParametersV2}[role]
                common = parameter_type.model_validate(normalized).model_dump(mode="json")
                canonical = lambda value: json.dumps(value, sort_keys=True,
                    separators=(",", ":"), ensure_ascii=False, allow_nan=False)
                if canonical(normalized) != canonical(common):
                    raise StackInputError("Kernel2 component must return its complete normalized recipe parameters")
            metadata.append(item)
        return metadata


class StackSession:
    def __new__(cls, definition, registry: StackRegistry | None = None):
        if cls is StackSession and stack_kernel_version(definition) == "stack-kernel/2":
            from .stack_v2_runtime import StackSessionV2
            return StackSessionV2(validate_stack_definition(definition), registry or StackRegistry())
        return super().__new__(cls)

    def __init__(self, definition, registry: StackRegistry | None = None):
        self.definition = _validated(StackDefinition, definition).model_copy(deep=True)
        self.registry = registry or StackRegistry()
        self.components = self.registry.validate_definition(self.definition)
        self._resolved = []
        for role in ("perception", "policy", "controller"):
            selection = getattr(self.definition, role)
            component = self.registry.get(selection.id, selection.version)
            self._resolved.append((component, copy.deepcopy(component.validate_parameters(copy.deepcopy(selection.parameters)))))
        self._last_at = None
        self._last_sequence = None

    def _assert_metadata(self):
        for (component, _), pinned in zip(self._resolved, self.components):
            if _validated(StackComponentMetadata, component.metadata()) != pinned:
                raise StackExecutionError("Component metadata changed after session creation")

    def step(self, packet: StackPacket, points_bytes: bytes | None = None) -> StackOutput:
        started = time.monotonic()
        packet = _validated(StackPacket, packet).model_copy(deep=True)
        at = int(packet.at_ns)
        if self._last_at is not None and (at <= self._last_at or packet.sequence <= self._last_sequence):
            raise StackInputError("Stack sequence and aligned time must both strictly increase")
        self._last_at, self._last_sequence = at, packet.sequence
        self._assert_metadata()
        reasons = list(packet.unavailable_reasons)
        perception, policy, controller = self._resolved
        # Timing/ODD checks belong to the core even if a plugin observation is permissive.
        common = CorridorParameters.model_validate(perception[1]) if self.components[0].id == "lidar-corridor" else None
        if common and common.ground_reference_z_m is None:
            reasons.append("ground_reference_unknown")
        if packet.ego is not None and packet.lidar is not None:
            skew = abs(int(packet.ego.timestamp_ns) - int(packet.lidar.timestamp_ns))
            if skew > round((common.max_input_skew_ms if common else 50.0) * 1_000_000):
                reasons.append("input_time_skew_exceeded")
        if packet.ego is None:
            reasons.append("ego_unavailable")
        else:
            age = at - int(packet.ego.timestamp_ns)
            if age < 0:
                reasons.append("ego_from_future")
            elif age > round((common.max_ego_age_ms if common else 100.0) * 1_000_000):
                reasons.append("ego_stale")
            if packet.ego.frame_id != self.definition.reference_frame:
                reasons.append("ego_frame_mismatch")
            limit = common.max_abs_pitch_roll_rad if common else 0.08
            if abs(packet.ego.pitch_rad) > limit or abs(packet.ego.roll_rad) > limit:
                reasons.append("flat_road_attitude_limit_exceeded")
        points = ()
        consumed_hash = None
        if packet.lidar is None:
            reasons.append("lidar_unavailable")
        else:
            lidar = packet.lidar
            age = at - int(lidar.timestamp_ns)
            if age < 0:
                reasons.append("lidar_from_future")
            elif age > round((common.max_lidar_age_ms if common else 100.0) * 1_000_000):
                reasons.append("lidar_stale")
            if lidar.frame_id != self.definition.reference_frame:
                reasons.append("lidar_frame_mismatch")
            if not lidar.complete_frame:
                reasons.append("lidar_incomplete_or_decimated")
            if type(points_bytes) is not bytes:
                reasons.append("lidar_buffer_unavailable")
            elif len(points_bytes) != lidar.size_bytes or len(points_bytes) > MAX_STACK_POINTS * 12:
                reasons.append("lidar_buffer_size_mismatch")
            elif hashlib.sha256(points_bytes).hexdigest() != lidar.points_sha256:
                reasons.append("lidar_buffer_hash_mismatch")
            else:
                consumed_hash = lidar.points_sha256
                points = tuple(struct.iter_unpack("<fff", points_bytes))
                if any(not math.isfinite(v) or abs(v) > 1_000_000 for point in points for v in point):
                    reasons.append("lidar_nonfinite_or_out_of_range")
                    points = ()
                if not points:
                    reasons.append("lidar_no_valid_returns")
        reasons = list(dict.fromkeys(reasons))[:30]
        try:
            if reasons:
                observation = CorridorObservation(status="unknown", input_points=len(points), reasons=reasons)
            else:
                observation = _validated(CorridorObservation, perception[0].observe(packet.model_copy(deep=True), points, copy.deepcopy(perception[1])))
                self._assert_metadata()
                if observation.input_points != len(points):
                    raise ValueError("Observation point denominator differs from consumed buffer")
                if observation.status == "obstacle_observed" and (observation.distance_m is None or observation.obstacle_points == 0):
                    raise ValueError("Observed obstacle requires distance and supporting points")
                if observation.status != "obstacle_observed" and (observation.distance_m is not None or observation.obstacle_points):
                    raise ValueError("Non-obstacle observation cannot carry an obstacle distance/count")
                if max(observation.forward_points, observation.obstacle_points, observation.ground_points) > len(points):
                    raise ValueError("Observation counts exceed consumed point denominator")
                if observation.coverage_qualified and observation.supported_distance_m is None:
                    raise ValueError("Qualified coverage requires an observed support distance")
            decision = _validated(StackDecision, policy[0].plan(packet.model_copy(deep=True), observation.model_copy(deep=True), copy.deepcopy(policy[1])))
            self._assert_metadata()
            # A replaceable policy cannot silently accelerate on missing/invalid input.
            if observation.status == "unknown" or not observation.coverage_qualified:
                decision = StackDecision(mode="unknown_stop", target_speed_mps=0.0,
                    stopping_distance_m=decision.stopping_distance_m, force_brake=True,
                    reasons=list(dict.fromkeys(decision.reasons + ["kernel_unknown_stop"]))[:30])
            elif not decision.force_brake and (observation.supported_distance_m is None
                    or decision.stopping_distance_m > observation.supported_distance_m):
                decision = StackDecision(mode="unknown_stop", target_speed_mps=0.0,
                    stopping_distance_m=decision.stopping_distance_m, force_brake=True,
                    reasons=["kernel_insufficient_observed_stopping_horizon"])
            control = _validated(StackControl, controller[0].control(packet.model_copy(deep=True), decision.model_copy(deep=True), copy.deepcopy(controller[1])))
            self._assert_metadata()
            if decision.force_brake:
                control = StackControl(throttle=0.0, brake=1.0, steer=0.0)
        except Exception as exc:
            raise StackExecutionError(f"Stack component contract failed: {exc}") from exc
        return StackOutput(sequence=packet.sequence, at_ns=packet.at_ns, observation=observation,
            decision=decision, control=control, input_points_sha256=consumed_hash,
            components=[item.model_copy(deep=True) for item in self.components], warnings=reasons,
            elapsed_ms=(time.monotonic() - started) * 1000)
