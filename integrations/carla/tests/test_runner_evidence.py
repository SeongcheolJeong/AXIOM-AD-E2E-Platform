"""Deterministic fake-world regression tests; these do not exercise CARLA physics."""
import copy
import json
import struct
from types import SimpleNamespace

import pytest

from ade2e.adapters import AdapterExecutionError, AdapterUnavailable
from axiom_carla.runner import CarlaRunner


def transform(x=0, y=0, z=0):
    return SimpleNamespace(
        location=SimpleNamespace(x=x, y=y, z=z),
        rotation=SimpleNamespace(pitch=0, yaw=0, roll=0),
        get_forward_vector=lambda: SimpleNamespace(x=1, y=0, z=0),
    )


def native_transform(location=None, rotation=None):
    pose = transform(**vars(location)) if location else transform()
    if rotation is not None:
        pose.rotation = rotation
    return pose


class FakeVehicle:
    def __init__(self, pose, destroy_ok=True):
        self.id = 0
        self.pose, self.destroy_ok = pose, destroy_ok
        self.velocity = SimpleNamespace(x=0, y=0, z=0)
        self.bounding_box = SimpleNamespace(extent=SimpleNamespace(x=2.5, y=1))
        self.destroy_called = False
        self.is_alive = True
        self.physics = SimpleNamespace(wheels=[SimpleNamespace(tire_friction=3.5) for _ in range(4)])

    def get_transform(self):
        return self.pose

    def get_velocity(self):
        return self.velocity

    def get_acceleration(self):
        return SimpleNamespace(x=0, y=0, z=0)

    def get_angular_velocity(self):
        return SimpleNamespace(x=0, y=0, z=0)

    def set_target_velocity(self, value):
        self.velocity = value

    def get_physics_control(self):
        return copy.deepcopy(self.physics)

    def apply_physics_control(self, value):
        self.physics = copy.deepcopy(value)
        for wheel in self.physics.wheels:
            wheel.tire_friction = struct.unpack('<f', struct.pack('<f', wheel.tire_friction))[0]

    def destroy(self):
        self.destroy_called = True
        if self.destroy_ok:
            self.is_alive = False
        return self.destroy_ok


class FakeCollisionSensor:
    def __init__(self):
        self.callback = None
        self.stopped = self.destroyed = False

    def listen(self, callback):
        self.callback = callback

    def stop(self):
        self.stopped = True

    def destroy(self):
        self.destroyed = True
        return True


class FakeWorld:
    def __init__(self, *, late_settle_event=False, destroy_ok=True, collision_schedule=(), fail_weather_restore=False):
        self.frame = 0
        self.vehicles = []
        self.collision = FakeCollisionSensor()
        self.late_settle_event = late_settle_event
        self.destroy_ok = destroy_ok
        self.collision_schedule = collision_schedule
        self.fail_weather_restore = fail_weather_restore
        self.settings = SimpleNamespace(synchronous_mode=False, fixed_delta_seconds=None, no_rendering_mode=False)
        self.weather = "original-weather"

    def get_map(self):
        return SimpleNamespace(name="/Game/Carla/Maps/Town01")

    def get_actors(self):
        return SimpleNamespace(filter=lambda _: [])

    def get_settings(self):
        return copy.copy(self.settings)

    def apply_settings(self, settings):
        self.settings = settings

    def get_weather(self):
        return self.weather

    def set_weather(self, value):
        if self.fail_weather_restore and value == "original-weather":
            raise RuntimeError("injected weather restore failure")
        self.weather = value

    def get_blueprint_library(self):
        return SimpleNamespace(find=lambda _: SimpleNamespace(set_attribute=lambda *_: None))

    def try_spawn_actor(self, blueprint, pose):
        vehicle = FakeVehicle(pose, self.destroy_ok)
        vehicle.id = len(self.vehicles)+1
        self.vehicles.append(vehicle)
        return vehicle

    def spawn_actor(self, *_, **kwargs):
        return self.collision

    def tick(self, seconds=10):
        self.frame += 1
        for vehicle in self.vehicles:
            vehicle.pose.location.x += vehicle.velocity.x * 0.05
        # The event belongs to settling frame 4 (t=.20), but its callback arrives
        # at frame 10, after measured frame zero (world frame 9 / t=.45).
        if self.late_settle_event and self.frame == 10:
            self.collision.callback(SimpleNamespace(other_actor=SimpleNamespace(id=123), frame=4, timestamp=0.20))
        for delivery_frame, event_frame, timestamp, actor_id in self.collision_schedule:
            if self.frame == delivery_frame:
                self.collision.callback(SimpleNamespace(other_actor=SimpleNamespace(id=actor_id), frame=event_frame, timestamp=timestamp))
        return self.frame

    def get_snapshot(self):
        return SimpleNamespace(frame=self.frame, timestamp=SimpleNamespace(elapsed_seconds=self.frame * 0.05, delta_seconds=0.05),
            find=lambda actor_id: next((vehicle for vehicle in self.vehicles if vehicle.id == actor_id), None))


@pytest.fixture
def fake_world_runner(monkeypatch):
    monkeypatch.setenv("ADE2E_CARLA_CAPTURE", "0")

    def install(**kwargs):
        world = FakeWorld(**kwargs)
        carla = SimpleNamespace(
            WeatherParameters=lambda **args: SimpleNamespace(**args),
            Transform=native_transform,
            Location=lambda **args: SimpleNamespace(**args),
            Rotation=lambda **args: SimpleNamespace(**args),
            Vector3D=lambda x, y, z: SimpleNamespace(x=x, y=y, z=z),
        )
        monkeypatch.setattr(CarlaRunner, "_connect", lambda _: (carla, None, world, "unit-test-only"))
        monkeypatch.setattr(CarlaRunner, "_straight_spawn", lambda *_: (transform(), transform(x=45)))
        monkeypatch.setattr(CarlaRunner, "_control", lambda *_: None)
        return world

    return install


def test_late_settling_collision_does_not_become_measured_contact(fake_world_runner, carla_scenario):
    fake_world_runner(late_settle_event=True)
    result = CarlaRunner().execute(carla_scenario)
    assert next(metric.value for metric in result.metrics if metric.name == "collision_count") == 0
    assert not [event for event in result.events if event.type == "collision"]


def test_capture_disabled_provenance_does_not_claim_rgb_lidar_capture(fake_world_runner, carla_scenario):
    fake_world_runner()
    result = CarlaRunner().execute(carla_scenario)
    manifest = json.loads(next(a.content for a in result.artifact_payloads if a.kind == "sensor_manifest"))
    assert "disabled" in manifest["sensor_scope"].lower()
    assert manifest["captures"] == []
    assert not any(a.kind in {"camera_rgb", "lidar"} for a in result.artifact_payloads)
    assert "disabled" in result.model_fidelity.lower() or "no sensor capture" in result.model_fidelity.lower()


def test_failed_actor_destruction_is_not_reported_as_clean_success(fake_world_runner, carla_scenario):
    world = fake_world_runner(destroy_ok=False)
    with pytest.raises(AdapterExecutionError, match="cleanup|destroy|restore"):
        CarlaRunner().execute(carla_scenario)
    assert all(vehicle.destroy_called for vehicle in world.vehicles)
    assert world.settings.synchronous_mode is False
    assert world.weather == "original-weather"


def test_collision_window_includes_start_and_preserves_first_measured_contact(fake_world_runner, carla_scenario):
    fake_world_runner(collision_schedule=[
        (10, 4, 0.20, 123),  # same actor: late settling contact excluded
        (9, 9, 0.45, 123),  # exactly first measured snapshot: included
        (12, 12, 0.60, 123), # repeated contact with actor: no duplicate
    ])
    result = CarlaRunner().execute(carla_scenario)
    collisions = [event for event in result.events if event.type == "collision"]
    assert next(metric.value for metric in result.metrics if metric.name == "collision_count") == 1
    assert len(collisions) == 1
    assert collisions[0].time_s == pytest.approx(0)


def test_settings_restore_is_attempted_even_if_weather_restore_fails(fake_world_runner, carla_scenario):
    world = fake_world_runner(fail_weather_restore=True)
    with pytest.raises(AdapterExecutionError, match="cleanup|restore"):
        CarlaRunner().execute(carla_scenario)
    assert world.settings.synchronous_mode is False
    assert world.settings.fixed_delta_seconds is None


@pytest.mark.parametrize("teardown_stage", ["stop", "destroy"])
def test_final_measured_collision_received_during_teardown_is_counted(fake_world_runner, carla_scenario, teardown_stage):
    world = fake_world_runner()
    original = getattr(world.collision, teardown_stage)

    def deliver_then_teardown():
        world.collision.callback(SimpleNamespace(other_actor=SimpleNamespace(id=999), frame=world.frame, timestamp=world.frame * 0.05))
        return original()

    setattr(world.collision, teardown_stage, deliver_then_teardown)
    result = CarlaRunner().execute(carla_scenario)
    metric = next(metric for metric in result.metrics if metric.name == "collision_count")
    assert metric.value == 1 and not metric.passed
    contacts = [event for event in result.events if event.type == "collision"]
    assert len(contacts) == 1
    assert contacts[0].time_s == pytest.approx(carla_scenario.duration_s)
    manifest = json.loads(next(artifact.content for artifact in result.artifact_payloads if artifact.kind == "sensor_manifest"))
    assert any("does not certify" in limitation for limitation in manifest["limitations"])
    assert "stream completeness unverified" in result.model_fidelity


def test_rejection_after_world_execution_is_an_execution_error(fake_world_runner, carla_scenario, monkeypatch):
    from ade2e.adapters import AdapterInputError
    world = fake_world_runner()

    def road_ended(*_):
        raise AdapterInputError("Road geometry ended during the trial")

    monkeypatch.setattr(CarlaRunner, "_control", road_ended)
    with pytest.raises(AdapterExecutionError, match="execution was attempted"):
        CarlaRunner().execute(carla_scenario)
    assert all(vehicle.destroy_called for vehicle in world.vehicles)
    assert world.settings.synchronous_mode is False
