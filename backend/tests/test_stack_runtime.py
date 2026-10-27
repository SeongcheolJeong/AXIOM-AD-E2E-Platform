"""Actual point-byte causality and fail-closed modular stack contracts."""
import hashlib
import math
import struct

import pytest
from pydantic import ValidationError

from ade2e.stack_components import CorridorParameters, LidarCorridor, ProportionalControl, StoppingHeadway
from ade2e.stack_runtime import StackExecutionError, StackInputError, StackRegistry, StackSession
from ade2e.stack_schemas import (StackDefinition, StackPacket, StackEgo, StackLidar,
                               StackControl, StackDecision)


GROUND = [(x, y, 0.0) for x, y in [(3., -.5), (4., .5), (9., 0.), (13., -.5),
                                  (15., .5), (17., 0.), (22., -.5), (29., .5)]]


def definition(**policy):
    return StackDefinition(perception={"id": "lidar-corridor", "version": "1", "parameters": {
        "ground_reference_z_m": 0.0, "ground_reference_source": "Authored unit-test plane"}},
        policy={"id": "stopping-headway", "version": "1", "parameters": {"allow_no_obstacle_observed_motion": True, **policy}},
        controller={"id": "proportional-control", "version": "1", "parameters": {}})


def packet(points=GROUND, *, at=1_000_000_000, sequence=0, speed=2.0, **ego_fields):
    raw = b"".join(struct.pack("<fff", *point) for point in points)
    return StackPacket(sequence=sequence, at_ns=str(at),
        ego=StackEgo(timestamp_ns=str(at), frame_id="ego", speed_mps=speed,
                     route_heading_error_rad=0.0, pitch_rad=0.0, roll_rad=0.0,
                     source="authored_fixture", **ego_fields),
        lidar=StackLidar(timestamp_ns=str(at), frame_id="ego", point_count=len(points),
            size_bytes=len(raw), points_sha256=hashlib.sha256(raw).hexdigest(),
            source_message_sha256="a" * 64, complete_frame=True)), raw


def run(points=GROUND, **kwargs):
    p, raw = packet(points, **kwargs)
    return StackSession(definition(), StackRegistry(load_plugins=False)).step(p, raw)


def test_actual_point_bytes_change_control_without_actor_ground_truth():
    far = run(GROUND + [(25., 0., 1.)])
    near = run(GROUND + [(4., 0., 1.)])
    assert far.observation.distance_m == 22.5
    assert near.observation.distance_m == 1.5
    assert far.control.throttle > 0 and far.control.brake == 0
    assert near.control.throttle == 0 and near.control.brake == 1
    assert far.input_points_sha256 != near.input_points_sha256
    assert near.observation.obstacle_points == 1  # A thin return is not erased by a clustering minimum.


def test_ground_support_only_is_explicit_pilot_assumption_not_clear_space():
    p, raw = packet()
    result = StackSession(definition()).step(p, raw)
    assert result.observation.status == "no_obstacle_observed"
    assert result.observation.semantics == "observed_surface_distance_not_free_space_or_ttc"
    assert result.decision.mode == "pilot_cruise"
    held = StackSession(definition(allow_no_obstacle_observed_motion=False)).step(p, raw)
    assert held.decision.mode == "hold" and held.control.brake == 1


@pytest.mark.parametrize("points", [[], [(0., 0., 0.)], [(-x, y, z) for x, y, z in GROUND],
    [(3., .1, 0.)] * 20, [(x, 5., z) for x, y, z in GROUND]])
def test_empty_rear_only_or_spatially_incomplete_returns_stop(points):
    output = run(points)
    assert output.decision.mode == "unknown_stop"
    assert output.control.brake == 1 and output.control.throttle == 0


def test_obstacle_preserved_when_ground_occluded_but_motion_withheld():
    output = run([(5., 0., .8), (5., .2, .8)])
    assert output.observation.status == "obstacle_observed"
    assert output.observation.distance_m == 2.5
    assert not output.observation.coverage_qualified
    assert output.decision.mode == "unknown_stop"


def test_hit_does_not_disappear_after_entering_bumper_plane():
    output = run(GROUND + [(1.0, 0.0, .8)])
    assert output.observation.status == "obstacle_observed"
    assert output.observation.distance_m == 0
    assert output.decision.mode == "obstacle_stop" and output.control.brake == 1


@pytest.mark.parametrize("mutation,reason", [
    ("hash", "lidar_buffer_hash_mismatch"), ("size", "lidar_buffer_size_mismatch"),
    ("frame", "lidar_frame_mismatch"), ("decimation", "lidar_incomplete_or_decimated"),
    ("future", "lidar_from_future"), ("stale", "lidar_stale"),
    ("ego_stale", "ego_stale"), ("ego_future", "ego_from_future"),
    ("ego_frame", "ego_frame_mismatch"), ("tilt", "flat_road_attitude_limit_exceeded"),
    ("missing", "lidar_unavailable"), ("missing_ego", "ego_unavailable")])
def test_input_faults_are_unknown_stop_with_specific_evidence(mutation, reason):
    p, raw = packet()
    if mutation == "hash": raw = b"\x00" * len(raw)
    elif mutation == "size": raw = raw[:-1]
    elif mutation == "frame": p.lidar.frame_id = "lidar_roof"
    elif mutation == "decimation": p.lidar.complete_frame = False
    elif mutation == "future": p.lidar.timestamp_ns = str(int(p.at_ns) + 1)
    elif mutation == "stale": p.lidar.timestamp_ns = str(int(p.at_ns) - 100_000_001)
    elif mutation == "ego_stale": p.ego.timestamp_ns = str(int(p.at_ns) - 100_000_001)
    elif mutation == "ego_future": p.ego.timestamp_ns = str(int(p.at_ns) + 1)
    elif mutation == "ego_frame": p.ego.frame_id = "world"
    elif mutation == "tilt": p.ego.pitch_rad = .081
    elif mutation == "missing": p.lidar = None
    elif mutation == "missing_ego": p.ego = None
    output = StackSession(definition()).step(p, raw)
    assert reason in output.warnings
    assert output.decision.mode == "unknown_stop" and output.control.brake == 1


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), 1_000_001.])
def test_valid_hash_does_not_make_invalid_coordinate_usable(value):
    output = run(GROUND + [(value, 0., 1.)])
    assert "lidar_nonfinite_or_out_of_range" in output.warnings
    assert output.control.brake == 1


def test_age_boundary_and_exact_uint64_clock():
    p, raw = packet(at=2**64 - 1)
    p.lidar.timestamp_ns = str(int(p.at_ns) - 100_000_000)
    p.ego.timestamp_ns = p.lidar.timestamp_ns
    assert StackSession(definition()).step(p, raw).decision.mode == "pilot_cruise"
    p.lidar.timestamp_ns = str(int(p.at_ns) - 100_000_001)
    assert StackSession(definition()).step(p, raw).decision.mode == "unknown_stop"


def test_sequence_and_timestamp_cannot_rewind_or_repeat():
    p, raw = packet()
    session = StackSession(definition())
    session.step(p, raw)
    with pytest.raises(StackInputError, match="strictly increase"):
        session.step(p, raw)


def test_ground_reference_and_height_gate_are_explicit_ego_coordinates():
    points = [(x, y, z - .4) for x, y, z in GROUND] + [(5., 0., .6)]
    p, raw = packet(points)
    d = definition()
    d.perception.parameters["ground_reference_z_m"] = -.4
    result = StackSession(d).step(p, raw)
    assert result.observation.coverage_qualified and result.observation.obstacle_points == 1
    assert StackSession(definition()).step(p, raw).control.brake == 1  # Undeclared plane is not inferred from mount.


def test_stopping_distance_arithmetic_is_stationary_assumption():
    output = run(GROUND + [(25., 0., 1.)], speed=8.)
    # 2m gap + max(8*1.5, 8*.1 + 8²/(2*2)) = 18.8m.
    assert output.decision.stopping_distance_m == pytest.approx(18.8)
    assert output.decision.mode == "approach"


def test_positive_left_steering_and_actuator_saturation():
    p, raw = packet(speed=0.)
    p.ego.route_heading_error_rad = 1.
    output = StackSession(definition()).step(p, raw)
    assert output.control.steer == .5
    assert output.control.throttle == .5 and output.control.brake == 0
    p.ego.route_heading_error_rad = -1.
    assert StackSession(definition()).step(p, raw).control.steer == -.5


def test_bad_parameters_unknown_versions_and_role_confusion_fail_closed():
    registry = StackRegistry(load_plugins=False)
    for alteration in [lambda d: setattr(d.perception, "version", "missing"),
                       lambda d: setattr(d.perception, "id", "proportional-control"),
                       lambda d: d.perception.parameters.update(ground_reference_z_m=float("nan")),
                       lambda d: d.policy.parameters.update(assumed_deceleration_mps2=0)]:
        d = definition()
        alteration(d)
        with pytest.raises((StackInputError, ValidationError)):
            registry.validate_definition(d)


def test_custom_policy_cannot_override_kernel_unknown_stop():
    class UnsafePolicy(StoppingHeadway):
        def plan(self, *_):
            return StackDecision(mode="pilot_cruise", target_speed_mps=20.,
                                 stopping_distance_m=0., force_brake=False)
    class UnsafeController(ProportionalControl):
        def control(self, *_): return StackControl(throttle=1., brake=0., steer=1.)
    registry = StackRegistry(load_plugins=False)
    registry.register(UnsafePolicy(), replace=True)
    registry.register(UnsafeController(), replace=True)
    p, raw = packet([])
    result = StackSession(definition(), registry).step(p, raw)
    assert result.decision.mode == "unknown_stop"
    assert result.control == StackControl(throttle=0., brake=1., steer=0.)


def test_plugin_mutation_does_not_change_following_stage_or_frozen_parameters():
    class MutatingLidar(LidarCorridor):
        def observe(self, packet, points, parameters):
            result = super().observe(packet, points, parameters)
            packet.ego.speed_mps = 99.
            parameters["lookahead_m"] = 3.
            return result
    registry = StackRegistry(load_plugins=False)
    registry.register(MutatingLidar(), replace=True)
    session = StackSession(definition(), registry)
    for i in range(2):
        p, raw = packet(sequence=i, at=1_000_000_000 + i)
        result = session.step(p, raw)
        assert result.decision.mode == "pilot_cruise" and result.control.throttle > 0
        assert p.ego.speed_mps == 2.


def test_plugin_model_construct_cannot_bypass_output_validation():
    class BadController(ProportionalControl):
        def control(self, *_):
            return StackControl.model_construct(throttle=float("nan"), brake=0., steer=0.)
    registry = StackRegistry(load_plugins=False)
    registry.register(BadController(), replace=True)
    with pytest.raises(StackExecutionError, match="contract failed"):
        p, raw = packet()
        StackSession(definition(), registry).step(p, raw)


def test_component_version_drift_is_rejected_before_next_tick():
    class Drifting(LidarCorridor):
        drift = False
        def metadata(self):
            m = super().metadata()
            if self.drift: m.version = "2"
            return m
    component = Drifting()
    registry = StackRegistry(load_plugins=False)
    registry.register(component, replace=True)
    session = StackSession(definition(), registry)
    component.drift = True
    with pytest.raises(StackExecutionError, match="metadata changed"):
        p, raw = packet()
        session.step(p, raw)


def test_buffer_descriptor_count_and_boolean_types_are_strict():
    p, _ = packet()
    data = p.model_dump()
    data["lidar"]["point_count"] += 1
    with pytest.raises(ValidationError): StackPacket.model_validate(data)
    data = p.model_dump()
    data["ego"]["speed_mps"] = True
    with pytest.raises(ValidationError): StackPacket.model_validate(data)


def test_unknown_ground_is_default_and_explicit_value_requires_source():
    params = CorridorParameters()
    assert params.ground_reference_z_m is None
    p, raw = packet()
    d = definition()
    d.perception.parameters = {}
    output = StackSession(d).step(p, raw)
    assert "ground_reference_unknown" in output.warnings and output.control.brake == 1
    for source in ("", "  ", "\t"):
        with pytest.raises(ValidationError, match="ground_reference_source"):
            CorridorParameters(ground_reference_z_m=0.0, ground_reference_source=source)


def test_individually_fresh_samples_still_reject_excessive_skew():
    p, raw = packet()
    p.lidar.timestamp_ns = str(int(p.at_ns) - 50_000_000)
    assert StackSession(definition()).step(p, raw).decision.mode == "pilot_cruise"
    p.lidar.timestamp_ns = str(int(p.at_ns) - 50_000_001)
    output = StackSession(definition()).step(p, raw)
    assert "input_time_skew_exceeded" in output.warnings
    assert "lidar_stale" not in output.warnings and output.control.brake == 1


@pytest.mark.parametrize("current,target", [(2., 20.), (20., 5.)])
def test_stopping_horizon_checks_both_current_and_requested_speed(current, target):
    p, raw = packet(speed=current)
    output = StackSession(definition(cruise_speed_mps=target)).step(p, raw)
    assert output.observation.status == "no_obstacle_observed"
    assert output.observation.supported_distance_m == 26.5
    assert output.decision.stopping_distance_m > output.observation.supported_distance_m
    assert "insufficient_observed_stopping_horizon" in output.decision.reasons
    assert output.decision.mode == "unknown_stop" and output.control.brake == 1


def test_support_is_actual_ground_extent_and_preserves_obstacle_on_horizon_stop():
    points = [(x, y, z) for x, y, z in GROUND if x < 29.] + [(21.5, .1, 0.), (26., 0., 1.)]
    p, raw = packet(points, speed=9.)
    output = StackSession(definition()).step(p, raw)
    assert output.observation.supported_distance_m == 19.5  # max ground 22, not lookahead 30 or obstacle 26
    assert output.observation.status == "obstacle_observed"
    assert output.observation.distance_m == 23.5
    assert output.decision.mode == "unknown_stop"
    assert "insufficient_observed_stopping_horizon" in output.decision.reasons
    assert output.control.brake == 1


@pytest.mark.parametrize("stage", ["perception", "policy", "controller"])
def test_in_step_metadata_mutation_cannot_publish_old_pin(stage):
    base = {"perception": LidarCorridor, "policy": StoppingHeadway, "controller": ProportionalControl}[stage]
    method = {"perception": "observe", "policy": "plan", "controller": "control"}[stage]
    class Drifting(base):
        drift = False
        def metadata(self):
            value = super().metadata()
            if self.drift: value.version = "2"
            return value
    def call(self, *args):
        result = getattr(base, method)(self, *args)
        self.drift = True
        return result
    setattr(Drifting, method, call)
    registry = StackRegistry(load_plugins=False)
    registry.register(Drifting(), replace=True)
    p, raw = packet()
    with pytest.raises(StackExecutionError, match="metadata changed"):
        StackSession(definition(), registry).step(p, raw)
