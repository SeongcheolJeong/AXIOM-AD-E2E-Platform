"""Authored fake-world lifecycle counterexamples, never CARLA physics evidence."""
import builtins
from copy import deepcopy
from dataclasses import dataclass
import importlib.util
import hashlib
import json
import math
from pathlib import Path
import struct
from types import SimpleNamespace as NS

import pytest


ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ("synchronous_mode", "fixed_delta_seconds", "substepping", "max_substep_delta_time", "max_substeps")
WEATHER = ("cloudiness",)


def xyz(x=0.0, y=0.0, z=0.0):
    return NS(x=x, y=y, z=z)


def f32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


class Transform:
    def __init__(self, location=None, rotation=None):
        self.location = location or xyz()
        self.rotation = rotation or NS(pitch=0.0, yaw=0.0, roll=0.0)

    def get_forward_vector(self):
        yaw = math.radians(self.rotation.yaw)
        return xyz(math.cos(yaw), math.sin(yaw))

    def get_matrix(self):
        return [[1.0, 0.0, 0.0, self.location.x], [0.0, 1.0, 0.0, self.location.y],
                [0.0, 0.0, 1.0, self.location.z], [0.0, 0.0, 0.0, 1.0]]

    def get_inverse_matrix(self):
        result = self.get_matrix()
        for i in range(3):
            result[i][3] *= -1.0
        return result


class Blueprint:
    def __init__(self, name):
        self.id, self.attributes = name, {}

    def has_attribute(self, name):
        return name == "role_name"

    def set_attribute(self, name, value):
        self.attributes[name] = value


class Actor:
    def __init__(self, world, actor_id, type_id, role=None, parent=None, transform=None):
        self.world, self.id, self.type_id = world, actor_id, type_id
        self.attributes = {} if role is None else {"role_name": role}
        self.parent, self.transform = parent, transform or Transform()
        self.velocity, self.acceleration = xyz(), xyz()
        self.control = NS(throttle=0.0, brake=0.0, steer=0.0, hand_brake=False,
                          reverse=False, manual_gear_shift=False, gear=1)
        self.physics = NS(wheels=[NS(tire_friction=3.5) for _ in range(4)])
        self.stop_error = self.destroy_error = self.destroy_false = False
        self.destroyed = False
        world.actors[actor_id] = self

    def get_world(self):
        return self.world

    def get_transform(self):
        return deepcopy(self.transform)

    def get_velocity(self):
        return deepcopy(self.velocity)

    def get_acceleration(self):
        return deepcopy(self.acceleration)

    def get_angular_velocity(self):
        return xyz()

    def get_control(self):
        return deepcopy(self.control)

    def get_physics_control(self):
        return deepcopy(self.physics)

    def apply_physics_control(self, value):
        self.world.calls.append(("physics", self.id))
        self.physics = deepcopy(value)

    def set_target_velocity(self, value):
        self.world.calls.append(("velocity_injection", self.id))
        self.velocity = deepcopy(value)

    def listen(self, callback):
        self.callback = callback
        self.world.calls.append(("listen", self.id))

    def stop(self):
        self.world.calls.append(("stop", self.id))
        if self.stop_error:
            raise TimeoutError("Authored stop timeout")

    def destroy(self):
        self.world.calls.append(("destroy", self.id))
        if self.destroy_error:
            raise TimeoutError("Authored destroy timeout")
        if self.destroy_false:
            return False
        self.destroyed = True
        self.world.actors.pop(self.id, None)
        return True


class Snapshot:
    def __init__(self, world):
        self.frame = world.frame
        self.timestamp = NS(elapsed_seconds=world.frame * 0.05, delta_seconds=0.05)
        # Sensor/vehicle handles are not snapshot ownership evidence. Freeze only state.
        self.states = {key: NS(get_transform=lambda a=deepcopy(actor.transform): deepcopy(a),
                              get_velocity=lambda v=deepcopy(actor.velocity): deepcopy(v),
                              get_acceleration=lambda a=deepcopy(actor.acceleration): deepcopy(a),
                              get_angular_velocity=lambda: xyz()) for key, actor in world.actors.items()}

    def find(self, actor_id):
        return self.states.get(actor_id)


class World:
    def __init__(self):
        self.id, self.frame = 10000000000000000001, 100
        self.calls, self.actors = [], {}
        self.settings = NS(synchronous_mode=False, fixed_delta_seconds=None, substepping=True,
                           max_substep_delta_time=0.01, max_substeps=10)
        self.weather = NS(cloudiness=20.0)
        self.original = Actor(self, 1, "spectator")
        self.hide_owned = self.no_throttle_response = False
        self.tick_jump = 1

    def get_map(self):
        return NS(name="Carla/Maps/Town10HD_Opt", to_opendrive=lambda: "authored fake OpenDRIVE")

    def get_actors(self):
        return [actor for actor in self.actors.values() if not self.hide_owned or actor.id == 1]

    def get_settings(self):
        return deepcopy(self.settings)

    def get_weather(self):
        return deepcopy(self.weather)

    def apply_settings(self, value):
        self.calls.append(("settings", value.synchronous_mode))
        self.settings = deepcopy(value)

    def set_weather(self, value):
        self.calls.append(("weather", value.cloudiness))
        self.weather = deepcopy(value)

    def get_blueprint_library(self):
        return NS(find=Blueprint)

    def spawn_actor(self, blueprint, transform, **kwargs):
        actor_id = max(self.actors) + 1
        self.calls.append(("spawn", actor_id))
        return Actor(self, actor_id, blueprint.id, blueprint.attributes["role_name"],
                     kwargs.get("attach_to"), deepcopy(transform))

    def get_snapshot(self):
        if not self.settings.synchronous_mode:
            self.frame += 1  # Represents a fresh asynchronous server snapshot after restoration.
        return Snapshot(self)

    def tick(self, *, seconds):
        assert self.settings.synchronous_mode and 0 < seconds <= 5.0
        self.calls.append(("tick", self.frame))
        self.frame += self.tick_jump
        for actor in self.actors.values():
            if actor.type_id.startswith("vehicle."):
                if not self.no_throttle_response:
                    actor.velocity.x += actor.control.throttle * 0.2
                actor.transform.location.x += actor.velocity.x * 0.05
        return self.frame


class Client:
    def __init__(self, world):
        self.world, self.batch_calls = world, []
        self.response_mode = "ack"

    def set_timeout(self, value):
        assert 0 < value <= 5.0

    def get_world(self):
        return self.world

    def get_server_version(self):
        return "1ae5356"

    get_client_version = get_server_version

    def apply_batch_sync(self, commands, do_tick):
        assert do_tick is False and len(commands) == 1
        self.batch_calls.append((deepcopy(commands), do_tick))
        actor_id, value = commands[0]
        self.world.actors[actor_id].control = deepcopy(value)
        for name in ("throttle", "brake", "steer"):
            setattr(self.world.actors[actor_id].control, name, f32(getattr(value, name)))
        if self.response_mode == "timeout":
            raise TimeoutError("Authored lost ACK after applying control")
        if self.response_mode == "empty":
            return []
        if self.response_mode == "jump":
            self.world.frame += 1
        response_id = actor_id + 100 if self.response_mode == "wrong_actor" else actor_id
        return [NS(actor_id=response_id, has_error=lambda: self.response_mode == "rejected", error="Authored rejection")]


@dataclass
class StartupPolicy:
    min_ticks: int
    max_ticks: int
    consecutive_stable_samples: int
    max_linear_speed_mps: float
    max_acceleration_mps2: float
    max_wall_time_s: float = 60.0


@pytest.fixture
def diagnostic_module(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("tested_carla_brake_runner", ROOT / "scripts/diagnose_carla_brake_response.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def environment(diagnostic_module, tmp_path, request):
    module = diagnostic_module
    import carla_brake_protocol as protocol
    world, output = World(), tmp_path / "trial"
    output.mkdir()
    execution_root = tmp_path / "executions"
    execution_root.mkdir()
    store_id = "a1111111-1111-4111-8111-111111111111"
    (execution_root / "store-id").write_text(store_id + "\n")
    fields = lambda value, names: {"values": {key: getattr(value, key) for key in names}, "invalid_fields": {}}
    available = lambda value: {"status": "available", "value": value, "reason": None}
    pinned_physics = {"scalars": {"mass": available(1845.0), "use_gear_autobox": available(True)},
                      "wheels": [{"index": i, "fields": {"tire_friction": available(0.8)}} for i in range(4)]}
    selection = getattr(request, "param", {})
    selected = protocol.make_protocol(selection.get("initialization", "velocity_injection"),
        measurement=selection.get("measurement", "coast"), brake_level=selection.get("brake_level", 0.0),
        version=selection.get("version", protocol.VERSION))
    road = world.get_map()
    intent = {"schema_version": protocol.INTENT_VERSION, "experiment_id": "b2222222-2222-4222-8222-222222222222",
              "protocol": selected, "protocol_sha256": module.sha(module.canonical(selected)),
              "sequence_sha256": module.sha(module.canonical(protocol.measurement_sequence(selected))),
              "expected_store_id": store_id, "engine_build": "1ae5356", "adapter_version": "0.8.0",
              "expected_world": {"world_id": str(world.id), "map": {"name": road.name,
                  "opendrive_sha256": module.sha(road.to_opendrive().encode()), "opendrive_size_bytes": len(road.to_opendrive())},
                  "actors": module.identities(world.actors.values()), "settings": fields(world.settings, SETTINGS),
                  "weather": fields(world.weather, WEATHER)},
              "spawn_transform": {"position_m": {"x": 0.0, "y": 0.0, "z": 0.0},
                                  "rotation_deg": {"pitch": 0.0, "yaw": 0.0, "roll": 0.0}},
              "requested_weather": {"values": {"cloudiness": 10.0}, "invalid_fields": {}},
              "expected_physics": pinned_physics,
              "source_files": {name: "a" * 64 for name in ("carla_brake_protocol.py", "diagnose_carla_brake_response.py", "diagnose_carla_self_hit.py")},
              "source_evidence": {"path": "authored-fixture.json", "sha256": "b" * 64}}

    def reported_control(view, frame, actor):
        before = view.get_snapshot().frame
        data = vars(actor.get_control())
        after = view.get_snapshot().frame
        return {"before_world_frame": before, "after_world_frame": after,
                "status": "available", "error": None, "data": data}

    native = {"fields": fields, "SETTINGS": SETTINGS, "WEATHER": WEATHER, "StartupPolicy": StartupPolicy,
              "_actor_state": lambda *args: {"stable": True, "invalid_fields": []},
              "_control": reported_control,
              "_telemetry": lambda view, frame, actor: {"before_world_frame": frame, "after_world_frame": frame,
                                                        "status": "unavailable", "error": "Authored optional telemetry absence", "data": None},
              "_physics": lambda *args: {"status": "available", "friction_status": "matched", **deepcopy(pinned_physics)}}
    carla = NS(Transform=Transform, Location=xyz, Rotation=lambda **kwargs: NS(**kwargs),
               Vector3D=xyz, WeatherParameters=lambda: NS(cloudiness=0.0),
               AttachmentType=NS(Rigid="rigid"), VehicleControl=lambda **kwargs: NS(**kwargs),
               command=NS(ApplyVehicleControl=lambda actor_id, control: (actor_id, control)))
    recorder = module.Recorder(output)
    runner = module.BrakeDiagnostic(protocol.validate_intent(intent), carla=carla, client=Client(world),
        output=output, execution_root=execution_root, recorder=recorder, native=native)
    yield runner, world, module
    if not recorder.stream.closed:
        recorder.stream.close()


def initialized(environment):
    runner, world, module = environment
    snapshot = runner.preflight()
    return runner, world, module, snapshot


def test_fake_world_complete_prefix_is_eighty_ack_intervals_eighty_one_observations(environment):
    runner, world, _, snapshot = initialized(environment)
    snapshot = runner.rest(snapshot)
    snapshot = runner.prepare(snapshot)
    runner.measure(snapshot)
    measurement = runner.report["measurement"]
    assert len(measurement["commands"]) == 80 and len(measurement["observations"]) == 81
    assert all(row["application_status"] == "observed_next_frame" for row in measurement["commands"])
    for index, command in enumerate(measurement["commands"]):
        assert command["world_frame"] == measurement["observations"][index]["world_frame"]
        assert command["next_world_frame"] == measurement["observations"][index + 1]["world_frame"]
    assert runner.report["preparation"]["velocity_request"]["ack_status"] == "unknown"
    assert not ({"pi_steps", "pi_state", "entry_trace", "entry_gate"} & runner.report["preparation"].keys())
    assert not ({"entry_gate", "entry_observation_frame"} & measurement.keys())
    runner.cleanup()
    assert runner.report["cleanup"]["status"] == "confirmed"
    assert runner.quarantine.exists(), "Cleanup must not release before durable final report"
    assert runner.finalize() == 0
    assert not runner.quarantine.exists() and (runner.output / "reservation-release.json").is_file()
    assert world.get_actors() == [world.original] and world.settings.synchronous_mode is False
    previous = "0" * 64
    for index, raw in enumerate((runner.output / "events.jsonl").read_bytes().splitlines()):
        event = json.loads(raw)
        digest = event.pop("sha256")
        expected = hashlib.sha256(json.dumps(event, sort_keys=True, separators=(",", ":"),
                                            ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        assert event["sequence"] == index and event["previous_sha256"] == previous and digest == expected
        previous = digest
    assert event["kind"] == "diagnostic_final"
    assert runner.report["event_chain"]["head_sha256"] == previous


@pytest.mark.parametrize("mode", ["timeout", "empty", "wrong_actor", "rejected", "jump"])
def test_failed_ack_never_becomes_a_completed_interval_or_sends_another_command(environment, mode):
    runner, _, _, snapshot = initialized(environment)
    row = runner.report["measurement"]["commands"][0]
    before = len(runner.client.batch_calls)
    runner.client.response_mode = mode
    with pytest.raises((RuntimeError, TimeoutError)):
        runner.send_control(snapshot, row)
    assert len(runner.client.batch_calls) == before + 1
    assert row["application_status"] != "observed_next_frame"
    if mode == "timeout":
        assert row["application_status"] == "send_outcome_unknown"
    assert row["next_world_frame"] is None


@pytest.mark.parametrize("change", ["episode", "foreign", "role"])
def test_changed_world_or_actor_identity_blocks_before_control_rpc(environment, change):
    runner, world, _, snapshot = initialized(environment)
    before = len(runner.client.batch_calls)
    if change == "episode":
        world.id += 1
    elif change == "foreign":
        Actor(world, 99, "vehicle.tesla.model3", "foreign")
    else:
        runner.returned["ego"].attributes["role_name"] = "foreign"
    with pytest.raises(RuntimeError):
        runner.send_control(snapshot, runner.report["measurement"]["commands"][0])
    assert len(runner.client.batch_calls) == before


def test_frame_gap_is_recorded_and_rejected_without_fabricating_observation(environment):
    runner, world, _, snapshot = initialized(environment)
    world.tick_jump = 2
    with pytest.raises(RuntimeError, match="exactly one"):
        runner.tick(snapshot, "authored_gap")
    assert runner.report["measurement"]["observations"] == []
    events = [json.loads(line) for line in (runner.output / "events.jsonl").read_bytes().splitlines()]
    assert events[-1]["kind"] == "tick_observed" and events[-1]["data"]["world_frame"] == snapshot.frame + 2


def test_failed_spatial_observation_is_retained_before_abort(environment):
    runner, world, _, _ = initialized(environment)
    runner.returned["ego"].transform.location.y = 1.01
    with pytest.raises(RuntimeError, match="Spatial"):
        runner.observe(world.get_snapshot(), "measurement", runner.report["measurement"]["observations"])
    retained = runner.report["measurement"]["observations"]
    assert len(retained) == 1 and retained[0]["bounds"]["failures"] == ["lateral"]


def test_stale_inventory_still_stops_and_destroys_retained_sensor_before_parent(environment):
    runner, world, _, _ = initialized(environment)
    sensor, ego = runner.returned["collision"], runner.returned["ego"]
    world.hide_owned = True
    runner.cleanup()
    assert runner.report["cleanup"]["status"] == "confirmed", runner.report["cleanup"]
    assert world.calls.index(("stop", sensor.id)) < world.calls.index(("destroy", sensor.id)) < world.calls.index(("destroy", ego.id))


@pytest.mark.parametrize("failure", ["stop", "destroy_false", "destroy_error"])
def test_sensor_failure_is_never_hidden_by_stale_empty_inventory(environment, failure):
    runner, world, _, _ = initialized(environment)
    sensor, ego = runner.returned["collision"], runner.returned["ego"]
    world.hide_owned = True
    setattr(sensor, "stop_error" if failure == "stop" else failure, True)
    runner.cleanup()
    assert ("stop", sensor.id) in world.calls and ("destroy", sensor.id) in world.calls
    assert runner.report["cleanup"]["status"] == "uncertain"
    assert runner.quarantine.is_file()
    if failure != "stop":
        assert ("destroy", ego.id) not in world.calls and world.settings.synchronous_mode is True
    else:
        assert ("destroy", ego.id) in world.calls


@pytest.mark.parametrize("conflict", ["episode", "nonce", "parent", "visible_id", "reservation"])
def test_cleanup_preserves_foreign_identity_and_uncertain_reservation(environment, conflict):
    runner, world, _, _ = initialized(environment)
    sensor = runner.returned["collision"]
    if conflict == "episode":
        world.id += 1
    elif conflict == "nonce":
        sensor.attributes["role_name"] = "foreign"
    elif conflict == "parent":
        sensor.parent = world.original
    elif conflict == "visible_id":
        Actor(world, sensor.id, sensor.type_id, "different-owner", runner.returned["ego"])
    else:
        runner.quarantine.write_bytes(b'{"foreign":"reservation"}')
    marker = runner.quarantine.read_bytes()
    runner.cleanup()
    assert runner.report["cleanup"]["status"] == "uncertain"
    assert ("destroy", sensor.id) not in world.calls
    assert runner.quarantine.read_bytes() == marker
    assert world.settings.synchronous_mode is True


def test_active_callback_retains_parent_journal_and_reservation(environment, monkeypatch):
    runner, world, _, _ = initialized(environment)
    assert runner.callback_gate.begin()
    monkeypatch.setattr(runner.callback_gate, "drain", lambda timeout: False)
    runner.cleanup()
    assert runner.report["cleanup"]["status"] == "uncertain"
    assert not runner.callbacks_drained and runner.report["callback_shutdown"]["active"] == 1
    assert ("destroy", runner.returned["ego"].id) not in world.calls
    assert runner.finalize() == 1
    assert runner.recorder.stream.closed is False and runner.quarantine.is_file()
    assert runner.report["event_chain"]["sealed"] is False
    runner.callback_gate.end()


def test_cleanup_journal_failure_still_attempts_owned_destruction_but_retains_reservation(environment, monkeypatch):
    runner, world, _, _ = initialized(environment)
    def fail(*args, **kwargs):
        raise OSError("Authored journal failure")
    monkeypatch.setattr(runner.recorder, "event", fail)
    runner.cleanup()
    assert all(("destroy", actor.id) in world.calls for actor in runner.returned.values())
    assert runner.report["cleanup"]["status"] == "uncertain" and runner.quarantine.is_file()


def test_failed_final_report_write_cannot_release_reservation(environment, monkeypatch):
    runner, _, module, _ = initialized(environment)
    runner.cleanup()
    original_write = module.exclusive_write
    def fail_report(path, content):
        if path.name == "report.json":
            raise OSError("Authored report durability failure")
        return original_write(path, content)
    monkeypatch.setattr(module, "exclusive_write", fail_report)
    with pytest.raises(OSError):
        runner.finalize()
    assert runner.reservation_owned and runner.quarantine.is_file()
    assert not (runner.output / "reservation-release.json").exists()


@pytest.mark.parametrize("failure", ["unavailable", "wrong_float", "wrong_bool", "numeric_bool", "missing", "crossed_frame"])
def test_control_readback_failure_keeps_observed_prefix_but_not_a_completed_interval(environment, failure):
    runner, _, _, snapshot = initialized(environment)
    command = runner.report["measurement"]["commands"][0]
    runner.send_control(snapshot, command)
    snapshot = runner.tick(snapshot, "readback_counterexample")
    original = runner.native["_control"]
    def changed(*args):
        value = original(*args)
        if failure == "unavailable":
            value.update(status="unavailable", data=None)
        elif failure == "wrong_float":
            value["data"]["brake"] = 0.01
        elif failure == "wrong_bool":
            value["data"]["hand_brake"] = True
        elif failure == "numeric_bool":
            value["data"]["reverse"] = 0
        elif failure == "missing":
            value["data"].pop("steer")
        else:
            value["after_world_frame"] += 1
        return value
    runner.native["_control"] = changed
    rows = runner.report["measurement"]["observations"]
    with pytest.raises(RuntimeError):
        observation = runner.observe(snapshot, "measurement", rows)
        runner.link_transition(command, observation)
    assert len(rows) == 1 and rows[0]["world_frame"] == snapshot.frame
    assert command["application_status"] == "sent_acknowledged" and command["next_world_frame"] is None


def test_control_readback_requires_native_float32_exactness_but_preserves_automatic_gear(diagnostic_module):
    control = {"throttle": 0.2, "brake": 0.025, "steer": 0.0,
               "hand_brake": False, "reverse": False, "manual_gear_shift": False, "gear": 1}
    data = {**control, "throttle": f32(0.2), "brake": f32(0.025), "gear": 3}
    observation = {"world_frame": 42, "reported_control": {"status": "available",
        "before_world_frame": 42, "after_world_frame": 42, "data": data}}
    diagnostic_module.require_control_readback(control, observation)
    assert observation["reported_control"]["data"]["gear"] == 3
    observation["reported_control"]["data"]["brake"] = 0.025
    with pytest.raises(RuntimeError, match="brake"):
        diagnostic_module.require_control_readback(control, observation)


@pytest.mark.parametrize("environment", [{"initialization": "throttle"}], indirect=True)
def test_throttle_exhaustion_preserves_three_hundred_attempts_and_never_injects_velocity(environment):
    runner, world, _, snapshot = initialized(environment)
    world.no_throttle_response = True
    with pytest.raises(RuntimeError, match="no velocity fallback"):
        runner.prepare(snapshot)
    preparation = runner.report["preparation"]
    assert len(preparation["commands"]) == 300 and len(preparation["observations"]) == 301
    assert preparation["status"] != "passed" and preparation["velocity_request"] is None
    assert not any(call[0] == "velocity_injection" for call in world.calls)
    assert all(row["control"]["brake"] == 0.0 for row in preparation["commands"])
    assert all(row["application_status"] == "not_attempted" for row in runner.report["measurement"]["commands"])


def test_measurement_speed_failure_does_not_issue_any_measurement_command(environment):
    runner, _, _, snapshot = initialized(environment)
    before = len(runner.client.batch_calls)
    with pytest.raises(RuntimeError, match="initial speed"):
        runner.measure(snapshot)
    assert len(runner.client.batch_calls) == before
    assert len(runner.report["measurement"]["observations"]) == 1


@pytest.mark.parametrize("environment", [{"measurement": "pulse", "brake_level": 0.2}], indirect=True)
def test_pulse_entry_outside_speed_window_keeps_five_coast_intervals_without_corrective_control(environment, monkeypatch):
    runner, world, _, snapshot = initialized(environment)
    snapshot = runner.prepare(snapshot)
    original_tick = world.tick
    count = 0
    def falling_speed(*, seconds):
        nonlocal count
        frame = original_tick(seconds=seconds)
        count += 1
        if count == 5:
            runner.returned["ego"].velocity.x = 1.8
        return frame
    monkeypatch.setattr(world, "tick", falling_speed)
    before = len(runner.client.batch_calls)
    with pytest.raises(RuntimeError, match="Pulse entry"):
        runner.measure(snapshot)
    assert len(runner.client.batch_calls) == before + 5
    measurement = runner.report["measurement"]
    assert len(measurement["observations"]) == 6
    assert measurement["observations"][-1]["signed_heading_speed_mps"] == 1.8
    assert all(row["application_status"] == "observed_next_frame" for row in measurement["commands"][:5])
    assert measurement["commands"][5]["application_status"] == "not_attempted"


def test_failed_release_receipt_reinstates_exact_reservation(environment, monkeypatch):
    runner, _, module, _ = initialized(environment)
    runner.cleanup()
    marker = runner.quarantine.read_bytes()
    original_write = module.exclusive_write
    def fail_receipt(path, content):
        if path.name == "reservation-release.json":
            raise OSError("Authored receipt fsync failure")
        return original_write(path, content)
    monkeypatch.setattr(module, "exclusive_write", fail_receipt)
    with pytest.raises(OSError):
        runner.finalize()
    assert runner.reservation_owned and runner.quarantine.read_bytes() == marker


def test_failed_unlink_directory_fsync_reinstates_reservation(environment, monkeypatch):
    runner, _, module, _ = initialized(environment)
    runner.cleanup()
    marker = runner.quarantine.read_bytes()
    original_fsync = module.fsync_dir
    def fail_after_unlink(path):
        if path == runner.execution_root and not runner.quarantine.exists():
            raise OSError("Authored directory fsync failure after unlink")
        return original_fsync(path)
    monkeypatch.setattr(module, "fsync_dir", fail_after_unlink)
    with pytest.raises(OSError):
        runner.finalize()
    assert runner.reservation_owned and runner.quarantine.read_bytes() == marker


def test_recorder_fsync_failure_is_sticky_and_cannot_append_a_duplicate_sequence(environment, monkeypatch):
    runner, world, module, _ = initialized(environment)
    recorder = runner.recorder
    original_fsync = module.os.fsync
    attempts = 0
    def fail_journal_once(descriptor):
        nonlocal attempts
        if descriptor == recorder.stream.fileno() and attempts == 0:
            attempts += 1
            raise OSError("Authored journal fsync failed after write")
        return original_fsync(descriptor)
    monkeypatch.setattr(module.os, "fsync", fail_journal_once)
    with pytest.raises(OSError):
        recorder.event("authored_failure", {"preserve": "ambiguous durability"})
    saved = (runner.output / "events.jsonl").read_bytes()
    with pytest.raises(RuntimeError):
        recorder.event("must_not_append_after_failure", {})
    assert (runner.output / "events.jsonl").read_bytes() == saved and recorder.failed is True
    runner.cleanup()
    assert all(("destroy", actor.id) in world.calls for actor in runner.returned.values())
    assert runner.finalize() == 1
    assert runner.quarantine.exists() and runner.report["event_chain"]["sealed"] is False


def test_identity_is_rechecked_between_sensor_stop_and_destroy(environment, monkeypatch):
    runner, world, _, _ = initialized(environment)
    sensor = runner.returned["collision"]
    original_stop = sensor.stop
    def changed_episode_after_stop():
        original_stop()
        world.id += 1
    monkeypatch.setattr(sensor, "stop", changed_episode_after_stop)
    runner.cleanup()
    assert ("stop", sensor.id) in world.calls and ("destroy", sensor.id) not in world.calls
    assert ("destroy", runner.returned["ego"].id) not in world.calls
    assert runner.report["cleanup"]["status"] == "uncertain" and runner.quarantine.exists()


def test_late_callback_after_gate_stop_does_not_touch_journal_or_create_collision_row(environment):
    runner, _, _, _ = initialized(environment)
    runner.cleanup()
    before = (runner.output / "events.jsonl").read_bytes()
    runner.collision_callback(object())  # Any attempt to read this object would fail.
    assert runner.report["collision_callbacks"] == []
    assert runner.callback_errors.empty()
    assert (runner.output / "events.jsonl").read_bytes() == before
    assert runner.callback_gate.snapshot()["rejected_after_stop"] == 1


def test_failed_rpc_after_returned_spawn_still_retains_owned_handle_for_cleanup(environment, monkeypatch):
    runner, world, module = environment
    original_event = runner.recorder.event
    def fail_after_spawn(kind, data):
        if kind == "action_returned" and data.get("operation") == "world.spawn_actor":
            runner.recorder.failed = True
            raise OSError("Authored journal failure after actor returned")
        return original_event(kind, data)
    monkeypatch.setattr(runner.recorder, "event", fail_after_spawn)
    with pytest.raises(OSError):
        runner.preflight()
    assert list(runner.returned) == ["ego"]
    assert runner.expected["ego"]["returned_actor_id"] == runner.returned["ego"].id
    runner.cleanup()
    assert ("destroy", runner.returned["ego"].id) in world.calls
    assert runner.report["cleanup"]["status"] == "uncertain" and runner.quarantine.exists()


@pytest.mark.parametrize("phase", ["rest", "inventory_refresh", "preparation", "measurement"])
def test_runner_propagates_phase_and_keeps_rest_fall_separate_from_measurement_speed(environment, phase):
    runner, world, _, _ = initialized(environment)
    runner.returned["ego"].velocity.z = -3.429999351501465
    runner.returned["ego"].transform.location.z = -0.6174003481864929
    row = runner.raw_observation(world.get_snapshot(), phase, 0)
    assert row["bounds"]["status"] == ("within_bounds" if phase in ("rest", "inventory_refresh") else "failed")
    if phase in ("preparation", "measurement"):
        assert row["bounds"]["failures"] == ["speed"]


def test_fake_rest_fall_can_settle_without_weakening_five_sample_convergence(environment, monkeypatch):
    runner, world, _, snapshot = initialized(environment)
    ego = runner.returned["ego"]
    original_tick, ticks = world.tick, []
    def fall_then_settle(*, seconds):
        frame = original_tick(seconds=seconds)
        step = len(ticks) + 1
        # Authored vertical settling fixture, explicitly not measured CARLA dynamics.
        ego.velocity.z = -3.43 if step <= 8 else 0.0
        ego.acceleration.z = -9.8 if step <= 8 else 0.0
        ego.transform.location.z = -0.6 if step <= 8 else -0.9
        ticks.append(step)
        return frame
    def stability(snapshot, actor_id, role, previous, policy):
        sample = snapshot.find(actor_id)
        stable = (math.hypot(*vars(sample.get_velocity()).values()) <= policy.max_linear_speed_mps
                  and math.hypot(*vars(sample.get_acceleration()).values()) <= policy.max_acceleration_mps2)
        return {"stable": stable, "invalid_fields": []}
    monkeypatch.setattr(world, "tick", fall_then_settle)
    runner.native["_actor_state"] = stability
    runner.rest(snapshot)
    assert len(ticks) == 13
    assert [r["stable_streak"] for r in runner.report["rest"]["trace"]] == [0] * 8 + [1, 2, 3, 4, 5]
    assert runner.report["rest"]["policy"]["max_linear_speed_mps"] == 0.03
    assert runner.report["rest"]["policy"]["max_acceleration_mps2"] == 0.1
    assert runner.report["rest"]["status"] == "passed"


def test_new_runner_rejects_legacy_execution_before_optional_import_or_dispatch(environment, monkeypatch, tmp_path):
    runner, world, module = environment
    import carla_brake_protocol as protocol
    value = deepcopy(runner.intent)
    value["protocol"] = protocol.make_protocol("velocity_injection", version="carla-brake-protocol/1")
    value["protocol_sha256"] = module.sha(module.canonical(value["protocol"]))
    source = tmp_path / "legacy-intent.json"
    source.write_bytes(module.canonical(value))
    imports = []
    original_import = builtins.__import__
    def deny_optional(name, *args, **kwargs):
        if name.split(".")[0] in ("carla", "axiom_carla"):
            imports.append(name)
            pytest.fail("Legacy rejection must happen before optional imports")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", deny_optional)
    args = NS(host="127.0.0.1", port=2000, intent=source,
              runtime_dir=runner.execution_root.parent, output=tmp_path / "forbidden-new-trial")
    with pytest.raises(RuntimeError, match="protocol2|protocol/2|protocol version|Protocol2|Protocol 2"):
        module.run(args)
    assert imports == [] and not args.output.exists()
    assert world.calls == [] and runner.client.batch_calls == []


SETTLED = {"version": "carla-brake-protocol/3", "initialization": "throttle"}


def settled_start(environment, monkeypatch, *, speed_at=None, position_at=None):
    """Prescribed raw states exercise sequencing, not a plant/controller fit.

    The fake speed reaches 2 m/s after 20 ticks and stays there regardless of
    the PI request. Actual raw-state and controller admission remain unproven.
    """
    runner, world, module, snapshot = initialized(environment)
    snapshot = runner.rest(snapshot)
    ego = runner.returned["ego"]
    speed_at = speed_at or (lambda index: min(2.0, index * 0.1))
    original_tick, steps = world.tick, []
    position = ego.transform.location.x
    previous_speed = ego.velocity.x
    world.no_throttle_response = True

    def prescribed_tick(*, seconds):
        nonlocal position, previous_speed
        frame = original_tick(seconds=seconds)
        index = len(steps) + 1
        speed = speed_at(index)
        position = position_at(index) if position_at else position + speed * 0.05
        ego.velocity.x = speed
        ego.acceleration.x = (speed - previous_speed) / 0.05
        ego.transform.location.x = position
        previous_speed = speed
        steps.append(index)
        return frame

    def telemetry(view, frame, actor):
        available = lambda value: {"status": "available", "value": value, "reason": None}
        return {"status": "available", "before_world_frame": frame, "after_world_frame": frame,
                "scalars": {"engine_rpm": available(max(1.0, actor.velocity.x * 200.0))},
                "wheels": [{"index": i, "fields": {"omega": available(max(0.1, actor.velocity.x * 3.0))}}
                           for i in range(4)]}

    monkeypatch.setattr(world, "tick", prescribed_tick)
    runner.native["_telemetry"] = telemetry
    return runner, world, module, snapshot, steps


@pytest.mark.parametrize("environment", [{**SETTLED, "measurement": "pulse", "brake_level": 0.2}], indirect=True)
def test_settled_same_frame_entry_immediate_pulse_and_release_have_full_ack_chain(environment, monkeypatch):
    runner, world, module, initial, ticks = settled_start(environment, monkeypatch)
    snapshot = runner.prepare(initial)
    prep = runner.report["preparation"]
    assert prep["status"] == "passed" and prep["entry_gate"]["admitted"]
    assert prep["entry_gate"]["first_crossing_index"] == len(prep["commands"])
    assert len(prep["entry_trace"]) == len(prep["observations"]) == len(prep["commands"]) + 1
    assert len(prep["pi_steps"]) == prep["pi_state"]["step"] == len(prep["commands"])
    for index, trace in enumerate(prep["pi_steps"]):
        assert trace["source_world_frame"] == prep["observations"][index]["world_frame"]
        assert trace["state_after"]["step"] == index + 1
        if index:
            assert trace["state_before"] == prep["pi_steps"][index - 1]["state_after"]
    assert all(not row["plane_reached"] for row in prep["entry_trace"][:-1])
    assert not any(call[0] == "velocity_injection" for call in world.calls)
    frame, prepared_ticks = snapshot.frame, len(ticks)
    runner.measure(snapshot)
    measurement = runner.report["measurement"]
    assert len(ticks) == prepared_ticks + 80  # No extra coast transition at admission.
    assert measurement["observations"][0]["ego"] == prep["observations"][-1]["ego"]
    assert measurement["entry_observation_frame"] == snapshot.frame == frame
    assert measurement["entry_gate"]["admitted"]
    assert [row["control"]["brake"] for row in measurement["commands"]] == [0.2] * 5 + [0.0] * 75
    assert len(measurement["observations"]) == 81
    for index, command in enumerate(measurement["commands"]):
        assert command["world_frame"] == frame + index
        assert command["next_world_frame"] == frame + index + 1
        assert command["application_status"] == "observed_next_frame"
        module.require_control_readback(command["control"], measurement["observations"][index + 1])
    assert measurement["observations"][1]["reported_control"]["data"]["brake"] == f32(0.2)
    assert measurement["observations"][6]["reported_control"]["data"]["brake"] == 0.0
    events = [json.loads(row) for row in (runner.output / "events.jsonl").read_bytes().splitlines()]
    pi_events = [row for row in events if row["kind"] == "preparation_pi_step"]
    assert [row["data"]["command_index"] for row in pi_events] == list(range(len(prep["commands"])))
    assert next(row["sequence"] for row in events if row["kind"] == "measurement_entry") < next(
        row["sequence"] for row in events if row["kind"] == "control_intent" and row["data"]["world_frame"] == frame)
    runner.cleanup()
    assert runner.report["cleanup"]["status"] == "confirmed" and runner.finalize() == 0


@pytest.mark.parametrize("environment", [SETTLED], indirect=True)
@pytest.mark.parametrize("failure", ["timeout", "wrong_actor", "observation", "readback"])
def test_settled_failed_transition_retains_proposed_trace_without_committing_pi_state(environment, monkeypatch, failure):
    runner, world, _, initial, ticks = settled_start(environment, monkeypatch)
    original_send, original_control = runner.send_control, runner.native["_control"]
    sent = []
    def send(snapshot, row, **kwargs):
        sent.append(row)
        if len(sent) == 2 and failure in ("timeout", "wrong_actor"):
            runner.client.response_mode = failure
        original_send(snapshot, row, **kwargs)
    def control(view, frame, actor):
        result = original_control(view, frame, actor)
        if len(ticks) == 2 and failure == "readback":
            result["data"]["brake"] = 0.1
        return result
    monkeypatch.setattr(runner, "send_control", send)
    runner.native["_control"] = control
    if failure == "observation":
        original_tick = world.tick
        def bad_observation(*, seconds):
            frame = original_tick(seconds=seconds)
            if len(ticks) == 2:
                runner.returned["ego"].velocity.y = 4.0
            return frame
        monkeypatch.setattr(world, "tick", bad_observation)
    with pytest.raises((RuntimeError, TimeoutError)):
        runner.prepare(initial)
    prep = runner.report["preparation"]
    assert len(prep["pi_steps"]) == len(prep["commands"]) == len(sent) == 2
    assert prep["pi_state"] == prep["pi_steps"][0]["state_after"]
    assert prep["pi_steps"][1]["state_before"] == prep["pi_state"]
    assert prep["pi_steps"][1]["state_after"]["step"] == 2
    assert len(prep["entry_trace"]) == 2 and not prep["entry_gate"]["plane_reached"]
    assert len(prep["observations"]) == (2 if failure in ("timeout", "wrong_actor") else 3)
    assert all(row["application_status"] == "not_attempted" for row in runner.report["measurement"]["commands"])
    assert not any(call[0] == "velocity_injection" for call in world.calls)


@pytest.mark.parametrize("environment", [SETTLED], indirect=True)
@pytest.mark.parametrize("case", ["already_reached", "too_early", "overshoot", "no_plane", "unavailable_raw", "one_unstable_row"])
def test_settled_first_crossing_and_continuous_gate_fail_without_measurement_or_later_recovery(environment, monkeypatch, case):
    if case == "too_early":
        position_at = lambda n: n * 1.21
    elif case == "overshoot":
        position_at = lambda n: n * 0.1 if n < 120 else 12.2
    elif case == "no_plane":
        position_at = lambda n: n * 0.001
    else:
        position_at = None
    speed_at = (lambda n: 1.94 if n == 125 else min(2.0, n * 0.1)) if case == "one_unstable_row" else None
    runner, world, _, snapshot, ticks = settled_start(environment, monkeypatch, speed_at=speed_at, position_at=position_at)
    if case == "already_reached":
        runner.returned["ego"].transform.location.x = 12.0
        snapshot = world.get_snapshot()
    if case == "unavailable_raw":
        runner.native["_telemetry"] = lambda view, frame, actor: {"status": "unavailable", "before_world_frame": frame,
            "after_world_frame": frame, "error": "Authored RPM unavailable"}
    with pytest.raises(RuntimeError, match="entry plane|entry-plane"):
        runner.prepare(snapshot)
    prep = runner.report["preparation"]
    assert prep["status"] != "passed" and not prep["entry_gate"]["admitted"]
    assert len(runner.report["measurement"]["observations"]) == 0
    assert len(runner.report["measurement"]["commands"]) == 80
    assert all(row["application_status"] == "not_attempted" for row in runner.report["measurement"]["commands"])
    if case == "no_plane":
        assert len(ticks) == 300 and len(prep["observations"]) == len(prep["entry_trace"]) == 301
        assert prep["pi_state"]["step"] == 300
    else:
        assert prep["entry_gate"]["plane_reached"]
        assert len(ticks) == prep["entry_gate"]["first_crossing_index"]
        assert not any(gate["plane_reached"] for gate in prep["entry_trace"][:-1])
    if case == "unavailable_raw":
        assert prep["entry_gate"]["raw_state_assessment"] == "unassessable"
    if case == "one_unstable_row":
        assert not prep["entry_gate"]["criteria"]["speed_window"]


@pytest.mark.parametrize("environment", [SETTLED], indirect=True)
@pytest.mark.parametrize("change", ["new_frame", "pose", "control", "telemetry", "stored_gate"])
def test_settled_measurement_rechecks_raw_frame_and_fresh_admission_before_first_command(environment, monkeypatch, change):
    runner, world, _, initial, _ = settled_start(environment, monkeypatch)
    snapshot = runner.prepare(initial)
    prep = runner.report["preparation"]
    if change == "new_frame":
        world.tick(seconds=1.0)
        snapshot = world.get_snapshot()
    elif change == "pose":
        runner.returned["ego"].transform.location.x += 0.001
        snapshot = world.get_snapshot()
    elif change == "control":
        runner.returned["ego"].control.throttle += 0.001
    elif change == "telemetry":
        runner.native["_telemetry"] = lambda view, frame, actor: {"status": "unavailable",
            "before_world_frame": frame, "after_world_frame": frame, "error": "Authored lost raw RPM"}
    else:
        prep["entry_gate"]["progress_m"] = 12.09
    calls = len(runner.client.batch_calls)
    with pytest.raises(RuntimeError, match="Measurement|readback|entry gate"):
        runner.measure(snapshot)
    measurement = runner.report["measurement"]
    assert len(measurement["observations"]) == 1 and len(runner.client.batch_calls) == calls
    assert all(row["application_status"] == "not_attempted" for row in measurement["commands"])
    if change == "telemetry":
        assert measurement["entry_gate"]["raw_state_assessment"] == "unassessable"
        assert not measurement["entry_gate"]["admitted"]


@pytest.mark.parametrize("environment", [SETTLED], indirect=True)
def test_settled_fresh_same_frame_telemetry_is_preserved_and_reassessed_not_copied(environment, monkeypatch):
    runner, _, _, initial, _ = settled_start(environment, monkeypatch)
    snapshot = runner.prepare(initial)
    original = runner.native["_telemetry"]
    def slightly_changed(view, frame, actor):
        value = original(view, frame, actor)
        value["scalars"]["engine_rpm"]["value"] += 0.1
        return value
    runner.native["_telemetry"] = slightly_changed
    runner.measure(snapshot)
    prep, measurement = runner.report["preparation"], runner.report["measurement"]
    assert prep["observations"][-1]["telemetry"] != measurement["observations"][0]["telemetry"]
    assert prep["entry_gate"]["raw_state_relative_spans"][0] == 0.0
    assert 0.0 < measurement["entry_gate"]["raw_state_relative_spans"][0] < 0.02
    assert measurement["entry_gate"]["admitted"]


def test_historical_five_descending_window_speeds_do_not_establish_v3_settled_admission(diagnostic_module):
    """Saved v2 throttle-1 raw speeds are a counterexample, never a v3 trial."""
    import carla_brake_protocol as protocol
    # Five raw signed speeds from the preserved v2 throttle-1 report. The other
    # 16 states and road-entry positions are authored, not historical evidence.
    speeds = [2.0] * 16 + [2.0904068422864786, 2.055849973533092, 2.0204240689866664,
                          1.9803671214145402, 1.9488173072622246]
    assert all(protocol.speed_in_window(speed) for speed in speeds[-5:])
    rows = []
    for index, speed in enumerate(speeds):
        frame = 100 + index
        rows.append({"world_frame": frame, "source_time_s": frame * 0.05,
            "ego": {"rotation_deg": {"yaw": 0.0}, "velocity_mps": {"x": speed, "y": 0., "z": 0.},
                "acceleration_mps2": {"x": 0., "y": 0., "z": 0.}, "angular_velocity_deg_s": {"x": 0., "y": 0., "z": 0.},
                "position_m": {"x": 10.05 + index * 0.1, "y": 0., "z": 0.}},
            "reported_control": {"status": "available", "before_world_frame": frame, "after_world_frame": frame,
                "data": {"gear": 1, "throttle": 0.1}}})
    gate = protocol.settled_preparation_gate(protocol.make_protocol("throttle", version=protocol.SETTLED_VERSION), rows,
        {"position_m": {"x": 0., "y": 0., "z": 0.}, "rotation_deg": {"yaw": 0.}})
    assert gate["crossed_plane_now"] and not gate["admitted"]
    assert not gate["criteria"]["speed_window"] and not gate["criteria"]["interval_acceleration"]
    assert gate["raw_state_assessment"] == "unassessable"


@pytest.mark.parametrize("environment", [SETTLED], indirect=True)
def test_settled_first_ack_failure_has_no_committed_pi_state_or_entry_advance(environment, monkeypatch):
    runner, _, _, snapshot, ticks = settled_start(environment, monkeypatch)
    runner.client.response_mode = "timeout"
    with pytest.raises(TimeoutError):
        runner.prepare(snapshot)
    prep = runner.report["preparation"]
    assert len(prep["commands"]) == len(prep["pi_steps"]) == 1
    assert prep["commands"][0]["application_status"] == "send_outcome_unknown"
    assert prep["pi_state"] is None and prep["pi_steps"][0]["state_after"]["step"] == 1
    assert len(prep["observations"]) == len(prep["entry_trace"]) == 1 and ticks == []
    assert all(row["application_status"] == "not_attempted" for row in runner.report["measurement"]["commands"])


@pytest.mark.parametrize("environment", [SETTLED], indirect=True)
def test_contact_received_after_observation_blocks_next_control_batch(environment, monkeypatch):
    runner, world, module, initial, ticks = settled_start(environment, monkeypatch)
    snapshot = runner.prepare(initial)
    # observe() completed its own collision check before this delayed callback.
    observed = runner.observe(snapshot, "measurement", runner.report["measurement"]["observations"])
    assert observed["world_frame"] == snapshot.frame and runner.report["collision_callbacks"] == []
    runner.collision_callback(NS(frame=snapshot.frame, timestamp=snapshot.timestamp.elapsed_seconds,
                                 other_actor=world.original, normal_impulse=xyz(1.0)))
    assert len(runner.report["collision_callbacks"]) == 1 and runner.callback_errors.empty()
    command = runner.report["measurement"]["commands"][0]
    calls, frame, steps = len(runner.client.batch_calls), world.frame, len(ticks)
    with pytest.raises(RuntimeError, match="Observed contact"):
        runner.send_control(snapshot, command)
    assert len(runner.client.batch_calls) == calls and world.frame == frame and len(ticks) == steps
    assert command["application_status"] == "not_attempted" and command["rpc_response"] is None
    assert command["world_frame"] is None and command["next_world_frame"] is None
    events = [json.loads(row) for row in (runner.output / "events.jsonl").read_bytes().splitlines()]
    contact_index = next(i for i, row in enumerate(events) if row["kind"] == "collision_callback")
    assert not any(row["kind"] == "control_intent" for row in events[contact_index + 1:])
    runner.cleanup()
    assert runner.report["cleanup"]["status"] == "confirmed"
    assert runner.finalize() == 1 and not runner.quarantine.exists()
    assert runner.report["status"] == "failed" and runner.report["collision_callbacks"]
