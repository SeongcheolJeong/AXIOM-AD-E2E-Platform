"""Bounded geometric pilot components; no surrounding actor ground truth."""
from __future__ import annotations

import math
from typing import Annotated

from pydantic import Field, model_validator

from .schemas import StrictModel
from .stack_schemas import (CorridorObservation, StackComponentMetadata, StackControl,
                            StackDecision, StackPacket)

Number = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class CorridorParameters(StrictModel):
    corridor_half_width_m: Number = Field(default=1.2, gt=0, le=10)
    ego_front_x_m: Number = Field(default=2.5, ge=0, le=10)
    lookahead_m: Number = Field(default=30.0, gt=0, le=200)
    ground_reference_z_m: Number | None = Field(default=None, ge=-10, le=10)
    ground_reference_source: str = Field(default="", max_length=1000)
    ground_tolerance_m: Number = Field(default=0.18, gt=0, le=0.5)
    min_obstacle_height_m: Number = Field(default=0.2, gt=0, le=2)
    max_obstacle_height_m: Number = Field(default=2.5, gt=0, le=10)
    min_forward_points: int = Field(default=8, ge=1, le=20000, strict=True)
    min_ground_points: int = Field(default=6, ge=1, le=20000, strict=True)
    ground_bins: int = Field(default=3, ge=1, le=20, strict=True)
    max_lidar_age_ms: Number = Field(default=100.0, ge=0, le=1000)
    max_ego_age_ms: Number = Field(default=100.0, ge=0, le=1000)
    max_input_skew_ms: Number = Field(default=50.0, ge=0, le=1000)
    max_abs_pitch_roll_rad: Number = Field(default=0.08, gt=0, le=0.3)

    @model_validator(mode="after")
    def consistent_geometry(self):
        if self.ground_reference_z_m is not None and not self.ground_reference_source.strip():
            raise ValueError("A declared ground reference needs a nonblank ground_reference_source")
        if self.lookahead_m <= self.ego_front_x_m:
            raise ValueError("Lookahead must extend beyond the ego front")
        if self.min_obstacle_height_m <= self.ground_tolerance_m:
            raise ValueError("Obstacle height must be above the ground tolerance band")
        if self.max_obstacle_height_m <= self.min_obstacle_height_m:
            raise ValueError("Maximum obstacle height must exceed minimum height")
        return self


class HeadwayParameters(StrictModel):
    cruise_speed_mps: Number = Field(default=5.0, ge=0, le=20)
    minimum_gap_m: Number = Field(default=2.0, ge=0, le=20)
    time_headway_s: Number = Field(default=1.5, ge=0, le=10)
    response_time_s: Number = Field(default=0.1, ge=0, le=5)
    assumed_deceleration_mps2: Number = Field(default=2.0, gt=0, le=10)
    allow_no_obstacle_observed_motion: bool = Field(default=False, strict=True)


class ControlParameters(StrictModel):
    throttle_gain: Number = Field(default=0.2, ge=0, le=5)
    brake_gain: Number = Field(default=0.3, gt=0, le=5)
    steering_gain: Number = Field(default=1.0, ge=0, le=10)
    max_throttle: Number = Field(default=0.5, ge=0, le=1)
    max_steer: Number = Field(default=0.5, ge=0, le=1)


class _Component:
    parameter_type: type[StrictModel]
    component_id: str
    title: str
    role: str
    limitations: list[str]

    def metadata(self):
        return StackComponentMetadata(
            id=self.component_id, name=self.title, version="1", role=self.role,
            parameter_schema=self.parameter_type.model_json_schema(),
            default_parameters=self.parameter_type().model_dump(mode="json"),
            implementation={"package": "ade2e", "algorithm": self.component_id + "/1"},
            limitations=self.limitations,
        )

    def validate_parameters(self, parameters):
        return self.parameter_type.model_validate(parameters).model_dump(mode="json")


class LidarCorridor(_Component):
    component_id = "lidar-corridor"
    title = "LiDAR corridor geometric observation"
    role = "perception"
    parameter_type = CorridorParameters
    limitations = [
        "Point geometry only: no object class, actor identity, velocity, free-space or TTC estimate.",
        "Ground reference is an authored flat-road assumption; mount height does not establish ground height.",
        "Ground counts and longitudinal bins qualify sample coverage only, not absence of obstacles.",
        "Sparse low obstacles, overhangs, occlusion and reflectivity can defeat this experimental detector.",
    ]

    def observe(self, packet: StackPacket, points: tuple[tuple[float, float, float], ...], parameters: dict):
        p = CorridorParameters.model_validate(parameters)
        if p.ground_reference_z_m is None:
            return CorridorObservation(status="unknown", input_points=len(points), reasons=["ground_reference_unknown"])
        # Keep very close surfaces, including those inside the bumper plane. A
        # front-plane cutoff would turn an approaching hit into no observation.
        # Self returns are not silently inferred or masked by this component.
        forward = [point for point in points if 0 <= point[0] <= p.lookahead_m
                   and abs(point[1]) <= p.corridor_half_width_m]
        ground = [point for point in forward if point[0] >= p.ego_front_x_m
                  and abs(point[2] - p.ground_reference_z_m) <= p.ground_tolerance_m]
        obstacles = [point for point in forward if p.min_obstacle_height_m <= point[2] - p.ground_reference_z_m <= p.max_obstacle_height_m]
        span = p.lookahead_m - p.ego_front_x_m
        bins = {min(p.ground_bins - 1, int((point[0] - p.ego_front_x_m) / span * p.ground_bins)) for point in ground}
        qualified = len(forward) >= p.min_forward_points and len(ground) >= p.min_ground_points and len(bins) == p.ground_bins
        reasons = [] if qualified else ["insufficient_forward_ground_coverage"]
        nearest = min(obstacles, key=lambda point: (point[0], point[1], point[2])) if obstacles else None
        return CorridorObservation(
            status="obstacle_observed" if nearest else "no_obstacle_observed" if qualified else "unknown",
            distance_m=max(0.0, nearest[0] - p.ego_front_x_m) if nearest else None,
            nearest_point_xyz_m=nearest, input_points=len(points), forward_points=len(forward),
            obstacle_points=len(obstacles), ground_points=len(ground), ground_bins_observed=len(bins),
            supported_distance_m=max(point[0] - p.ego_front_x_m for point in ground) if ground else None,
            coverage_qualified=qualified, reasons=reasons,
        )


class StoppingHeadway(_Component):
    component_id = "stopping-headway"
    title = "Stationary-obstacle stopping and headway policy"
    role = "policy"
    parameter_type = HeadwayParameters
    limitations = [
        "Assumes a stationary observed surface and declared braking response; not calibrated stopping performance.",
        "Ego speed and route heading are supplied telemetry; surrounding actor state is not an input.",
        "Motion after no obstacle observation requires an explicit pilot assumption; this is not free-space certification.",
    ]

    @staticmethod
    def _stopping_distance(p, speed):
        return p.minimum_gap_m + max(speed * p.time_headway_s,
            speed * p.response_time_s + speed * speed / (2 * p.assumed_deceleration_mps2))

    def _bounded_motion(self, p, observation, speed, target, mode, reason):
        required = self._stopping_distance(p, max(speed, target))
        if observation.supported_distance_m is None or required > observation.supported_distance_m:
            return StackDecision(mode="unknown_stop", target_speed_mps=0.0, stopping_distance_m=required,
                force_brake=True, reasons=["insufficient_observed_stopping_horizon"])
        return StackDecision(mode=mode, target_speed_mps=target, stopping_distance_m=required,
                             force_brake=False, reasons=[reason])

    def plan(self, packet: StackPacket, observation: CorridorObservation, parameters: dict):
        p = HeadwayParameters.model_validate(parameters)
        speed = packet.ego.speed_mps if packet.ego else 0.0
        stopping = self._stopping_distance(p, speed)
        if observation.status == "unknown" or not observation.coverage_qualified or packet.ego is None:
            return StackDecision(mode="unknown_stop", target_speed_mps=0.0, stopping_distance_m=stopping,
                                 force_brake=True, reasons=["unknown_or_insufficient_observation"])
        if observation.status == "no_obstacle_observed":
            allowed = p.allow_no_obstacle_observed_motion
            if allowed:
                return self._bounded_motion(p, observation, speed, p.cruise_speed_mps, "pilot_cruise",
                    "explicit_pilot_no_obstacle_observed_motion_assumption")
            return StackDecision(mode="hold", target_speed_mps=0.0, stopping_distance_m=stopping,
                force_brake=True, reasons=["no_obstacle_observed_motion_not_authorized"])
        if observation.distance_m is None:
            return StackDecision(mode="unknown_stop", target_speed_mps=0.0, stopping_distance_m=stopping,
                                 force_brake=True, reasons=["missing_observed_distance"])
        if observation.distance_m <= stopping:
            return StackDecision(mode="obstacle_stop", target_speed_mps=0.0, stopping_distance_m=stopping,
                                 force_brake=True, reasons=["observed_surface_inside_stopping_headway"])
        remaining = max(0.0, observation.distance_m - p.minimum_gap_m)
        # Solve v*t + v²/(2*a) <= remaining without subtractive cancellation.
        a, response = p.assumed_deceleration_mps2, p.response_time_s
        root = math.sqrt((a * response) ** 2 + 2 * a * remaining)
        target = 2 * a * remaining / (root + a * response) if root + a * response else 0.0
        if p.time_headway_s:
            target = min(target, remaining / p.time_headway_s)
        return self._bounded_motion(p, observation, speed, min(p.cruise_speed_mps, target),
                                    "approach", "stationary_surface_stopping_assumption")


class ProportionalControl(_Component):
    component_id = "proportional-control"
    title = "Proportional speed and route-heading control"
    role = "controller"
    parameter_type = ControlParameters
    limitations = ["Normalized actuator commands are not a vehicle dynamics model.",
                   "Route heading error is declared oracle/telemetry; this component does not perceive lanes.",
                   "Steer is positive left. CARLA's actuator convention must be converted at its bridge."]

    def control(self, packet: StackPacket, decision: StackDecision, parameters: dict):
        p = ControlParameters.model_validate(parameters)
        if decision.force_brake or packet.ego is None:
            return StackControl(throttle=0.0, brake=1.0, steer=0.0)
        error = decision.target_speed_mps - packet.ego.speed_mps
        steer = max(-p.max_steer, min(p.max_steer, packet.ego.route_heading_error_rad * p.steering_gain))
        return StackControl(throttle=min(p.max_throttle, max(0.0, error * p.throttle_gain)),
            brake=min(1.0, max(0.0, -error * p.brake_gain)), steer=steer)
