"""Deterministic 2D kinematic reference engine, not a sensor or vehicle twin.

Straight road, axis-aligned rectangles, a delayed object-truth braking rule,
friction-limited deceleration and 50 ms integration. Weather/time/map are
catalog metadata; only explicit friction changes road dynamics. No perception,
steering, tyre slip, impact dynamics or compliance-grade safety thresholds.
"""

import math
import random
from typing import Callable

from .adapters import AdapterCancelled, AdapterCancelledBeforeDispatch, supports_cancellation
from .schemas import Actor, Adapter, EgoState, Event, Frame, Metric, Scenario, SimulationResult

DT = 0.05
EGO_HALF_LENGTH = 2.25
EGO_HALF_WIDTH = 0.95


def compare_metric(value: float | None, operator: str, threshold: float, *, name: str = "") -> bool:
    # Null TTC explicitly means no future intersection under constant velocity.
    # Other missing/null metrics must never become passing evidence.
    if value is None:
        if name != "min_ttc_s":
            return False
        value = math.inf
    return {"gte": value >= threshold, "lte": value <= threshold, "eq": value == threshold}[operator]


def contact_time(dx: float, dy: float, rvx: float, rvy: float, half_x: float, half_y: float) -> float | None:
    """First intersection time of two axis-aligned boxes under relative velocity."""
    enter, leave = -math.inf, math.inf
    for displacement, velocity, extent in ((dx, rvx, half_x), (dy, rvy, half_y)):
        if abs(velocity) < 1e-12:
            if abs(displacement) > extent:
                return None
            continue
        first, last = sorted(((-extent - displacement) / velocity, (extent - displacement) / velocity))
        enter, leave = max(enter, first), min(leave, last)
    if leave < max(enter, 0):
        return None
    return max(0, enter)


class ReferenceAdapter:
    def metadata(self) -> Adapter:
        return Adapter(
            id="reference", name="Object Reference", version="0.1.0", kind="object_simulation", status="available",
            description="Deterministic straight-road object simulation with a delayed reactive braking controller.",
            capabilities=["cut_in", "emergency_brake", "pedestrian_crossing", "lane_follow", "2D trajectory", "collision / TTC / comfort metrics", "seeded replay"],
            limitations=["No camera, LiDAR, radar or perception model", "Kinematic rectangles, no impact or calibrated vehicle dynamics", "Weather, time of day and map name are descriptive metadata", "Illustrative thresholds; not certification evidence"],
            license="Project source; distribution license not yet selected", source_url="",
        )

    def execute(self, scenario: Scenario) -> SimulationResult:
        return self._execute(scenario, None)

    def execute_cancellable(self, scenario: Scenario, should_cancel: Callable[[], bool]) -> SimulationResult:
        if not supports_cancellation(self):
            # A legacy subclass replaced execute without opting into this hook.
            if should_cancel():
                raise AdapterCancelledBeforeDispatch("Execution cancelled before dispatch")
            return self.execute(scenario)
        return self._execute(scenario, should_cancel)

    def _execute(self, scenario: Scenario, should_cancel: Callable[[], bool] | None) -> SimulationResult:
        p = scenario.parameters
        rng = random.Random(scenario.seed)
        # Small, documented seeded variation makes repetitions reproducible.
        gap = max(0, p.initial_gap_m + rng.uniform(-0.15, 0.15))
        trigger = max(0, p.trigger_time_s + rng.uniform(-0.05, 0.05))
        pedestrian = scenario.category == "pedestrian_crossing"
        half_x = EGO_HALF_LENGTH + (0.3 if pedestrian else 2.25)
        half_y = EGO_HALF_WIDTH + (0.3 if pedestrian else 0.95)
        target_x, target_v = gap + half_x, p.target_speed_mps
        ego_x, ego_v, ego_a = 0.0, p.ego_speed_mps, 0.0
        max_brake = min(8.0, p.friction * 9.81)
        frames, events = [], []
        collided = False
        collision_count = 0
        min_ttc = math.inf
        max_deceleration, max_jerk = 0.0, 0.0
        acceleration_history: list[float] = []
        previous_relative: tuple[float, float] | None = None
        last_a = 0.0
        trigger_logged = False
        braking_logged = False
        steps = int(math.ceil(scenario.duration_s / DT))
        for step in range(steps + 1):
            if should_cancel is not None and should_cancel():
                raise AdapterCancelled("Reference execution cancelled")
            t = min(step * DT, scenario.duration_s)
            if scenario.category == "cut_in":
                fraction = max(0.0, min(1.0, (t - trigger) / 1.5))
                target_y = 3.7 * (1 - fraction)
                target_vy = -3.7 / 1.5 if 0 < fraction < 1 else 0.0
            elif pedestrian:
                target_y = -5.5 + max(0.0, t - trigger) * target_v
                target_vy = target_v if t >= trigger else 0.0
            else:
                target_y, target_vy = 0.0, 0.0
            target_vx = 0.0 if pedestrian else target_v
            dx, dy = target_x - ego_x, target_y
            ttc = contact_time(dx, dy, target_vx - ego_v, target_vy, half_x, half_y)
            touching = abs(dx) <= half_x and abs(dy) <= half_y
            if previous_relative is not None:
                pdx, pdy = previous_relative
                swept = contact_time(pdx, pdy, dx - pdx, dy - pdy, half_x, half_y)
                touching = touching or (swept is not None and swept <= 1)
            if touching and not collided:
                collision_count += 1
                events.append(Event(time_s=round(t, 4), type="collision", message="Ego and target reference bounding boxes intersected. No impact dynamics are modeled."))
                collided = True
            if ttc is not None:
                min_ttc = min(min_ttc, ttc)
            if t >= trigger and not trigger_logged and scenario.category != "lane_follow":
                events.append(Event(time_s=round(t, 4), type="trigger", message={"cut_in": "Target starts a 1.5 s lane transition.", "emergency_brake": "Lead vehicle starts friction-limited braking.", "pedestrian_crossing": "Pedestrian starts crossing."}[scenario.category]))
                trigger_logged = True

            max_deceleration = max(max_deceleration, -ego_a)
            if step:
                max_jerk = max(max_jerk, abs(ego_a - last_a) / (t - frames[-1].time_s))
            frames.append(Frame(
                time_s=round(t, 4), ego=EgoState(x=round(ego_x, 5), y=0, yaw=0, speed_mps=round(ego_v, 5)),
                actors=[Actor(id="target", type="pedestrian" if pedestrian else "vehicle", x=round(target_x, 5), y=round(target_y, 5), yaw=math.pi / 2 if pedestrian else 0, speed_mps=round(target_v, 5)),
                        Actor(id="background_oncoming", type="vehicle", x=round(180 - 12 * t, 5), y=-3.7, yaw=math.pi, speed_mps=12)],
                ttc_s=None if ttc is None else round(ttc, 5), acceleration_mps2=round(ego_a, 5),
            ))
            previous_relative, last_a = (dx, dy), ego_a
            if step == steps:
                break

            # Reactive truth-object controller. Lateral future intersection is
            # included in TTC; no detection or sensor latency is simulated.
            closing = max(0, ego_v - target_vx)
            road_gap = max(0.1, dx - half_x)
            hazard = (ttc is not None and 0 <= ttc < 4.0 and dx > -half_x)
            following = abs(dy) < half_y and dx > 0 and road_gap < 3 + 1.8 * ego_v
            if hazard or following:
                requested = -min(max_brake, max(0.8, closing * closing / (2 * max(0.2, road_gap - 2)) + 0.8))
            else:
                requested = min(1.5, max(0, (p.ego_speed_mps - ego_v) * 0.5))
            acceleration_history.append(requested)
            delayed_index = len(acceleration_history) - 1 - int(math.ceil(p.response_delay_s / DT))
            delayed = acceleration_history[delayed_index] if delayed_index >= 0 else 0.0
            dt = min(DT, scenario.duration_s - t)
            commanded = max(ego_a - 8 * dt, min(ego_a + 8 * dt, delayed))
            next_v = max(0, ego_v + commanded * dt)
            ego_a = (next_v - ego_v) / dt
            if ego_a < -0.05 and not braking_logged:
                events.append(Event(time_s=round(t + dt, 4), type="braking", message="Delayed object-truth controller applies ego braking."))
                braking_logged = True
            ego_x += (ego_v + next_v) * 0.5 * dt
            ego_v = next_v
            if not pedestrian:
                target_a = -min(7.0, p.friction * 9.81) if scenario.category == "emergency_brake" and t >= trigger else 0.0
                next_target_v = max(0, target_v + target_a * dt)
                target_x += (target_v + next_target_v) * 0.5 * dt
                target_v = next_target_v

        values = [
            ("collision_count", collision_count, "count", 0, "eq"),
            ("min_ttc_s", None if math.isinf(min_ttc) else round(min_ttc, 5), "s", 1.0, "gte"),
            ("max_deceleration_mps2", round(max_deceleration, 5), "m/s²", 6.0, "lte"),
            ("max_jerk_mps3", round(max_jerk, 5), "m/s³", 15.0, "lte"),
            ("distance_m", round(ego_x, 5), "m", 0.0, "gte"),
        ]
        metrics = [Metric(name=name, value=value, unit=unit, threshold=threshold, operator=operator, passed=compare_metric(value, operator, threshold, name=name)) for name, value, unit, threshold, operator in values]
        return SimulationResult(trajectory=frames, events=events, metrics=metrics, model_fidelity="reference object model; synthetic, uncalibrated; no sensor or perception simulation")
