#!/usr/bin/env python3
"""Bounded static native LiDAR identity diagnostic; never executes a driving stack.

Run only on the dedicated CARLA host, under the deployed optional 0.7 environment.
The caller must copy the exact pinned intent and select a NEW output directory.
This file is a diagnostic artifact, not a production adapter or automated retry.

Example (paths are deliberately supplied by the operator):
  python diagnose-carla-self-hit-v2.py --intent /path/to/intent.json \
    --runtime-dir /home/axiom-user/workspace/axiom-carla-worker/runtime \
    --output /path/to/new-static-self-hit-diagnostic
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import struct
import sys
import tempfile
import threading
import time
import uuid

PINNED_INTENT_SHA256 = "a327c5a0ccf336dadcff8349d81dd21e2d83a18a6ffcb9e3ef520cedddb7ef5f"
PINNED_INPUT_HASH = "a90f6b62235de26b60ad407e5cb8a100130da285a598b35e851fc8a8894f0d25"
SEMANTIC_ATTRIBUTES = ("channels", "range", "points_per_second", "rotation_frequency",
                       "upper_fov", "lower_fov", "horizontal_fov", "sensor_tick")
PAIRS = 8
DT = .05
RPC_SECONDS = 5.0
MAIN_SECONDS = 90.0
CLEANUP_SECONDS = 40.0
CALLBACK_DRAIN_SECONDS = 5.0
MAX_POINTS = 20_000
MAX_CALLBACKS_PER_SENSOR = 16
MAX_RAW_BYTES = 32 * 1024 * 1024
SOURCE_COMMIT = "1ae5356"
SOURCES = {
    "semantic_layout": "https://github.com/carla-simulator/carla/blob/1ae5356/LibCarla/source/carla/sensor/data/SemanticLidarData.h",
    "semantic_actor_identity": "https://github.com/carla-simulator/carla/blob/1ae5356/Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Sensor/RayCastSemanticLidar.cpp",
    "raw_buffer": "https://github.com/carla-simulator/carla/blob/1ae5356/PythonAPI/carla/source/libcarla/SensorData.cpp",
    "parent": "https://github.com/carla-simulator/carla/blob/1ae5356/LibCarla/source/carla/client/ActorState.cpp",
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat()


def fsync_dir(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def exclusive_write(path, content):
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        require(stream.write(content) == len(content), "Incomplete diagnostic file write")
        stream.flush()
        os.fsync(stream.fileno())
    fsync_dir(path.parent)
    return {"path": path.name, "sha256": sha(content), "size_bytes": len(content)}


def vector(value):
    result = {key: float(getattr(value, key)) for key in ("x", "y", "z")}
    require(all(math.isfinite(v) for v in result.values()), "Nonfinite native vector")
    return result


def matrix(value):
    result = [[float(x) for x in row] for row in value]
    require(len(result) == 4 and all(len(row) == 4 for row in result) and
            all(math.isfinite(x) for row in result for x in row), "Invalid native transform matrix")
    return result


def pose(value):
    result = {"position_m": vector(value.location),
              "rotation_deg": {key: float(getattr(value.rotation, key)) for key in ("pitch", "yaw", "roll")},
              "matrix_native": matrix(value.get_matrix()), "inverse_matrix_native": matrix(value.get_inverse_matrix())}
    require(all(math.isfinite(v) for v in result["rotation_deg"].values()), "Nonfinite native rotation")
    return result


def multiply(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def identity(actor):
    return {"actor_id": int(actor.id), "type_id": actor.type_id,
            "role_name": dict(actor.attributes).get("role_name")}


def identities(actors):
    return sorted((identity(actor) for actor in actors), key=lambda item: item["actor_id"])


def inventory_key(items):
    return {(a["actor_id"], a["type_id"], a["role_name"]) for a in items}


def best_effort_event(recorder, errors, kind, data):
    """A journal error must not prevent already-authorized owned cleanup."""
    try:
        recorder.event(kind, data)
        return True
    except Exception as error:
        errors.append({"phase": "journal", "event": kind,
                       "error": type(error).__name__ + ": " + str(error)[:800]})
        return False


def cleanup_call(recorder, errors, guard, operation, function, data=None):
    """Keep deadline/ownership guards, but attempt cleanup despite journal I/O failure."""
    guard()
    best_effort_event(recorder, errors, "action_intent", {"operation": operation, **(data or {})})
    result = function()
    best_effort_event(recorder, errors, "action_returned", {"operation": operation,
        "return_value": result if type(result) in (str, bool, int, float) or result is None else None,
        "meaning": "API return only; explicit control responses and readbacks are separate"})
    guard()
    return result


class CallbackGate:
    """Track Python callback lifetimes; a closed gate never touches the recorder."""
    def __init__(self):
        self.condition = threading.Condition()
        self.accepting = True
        self.active = self.started = self.finished = self.rejected_after_stop = 0

    def begin(self):
        with self.condition:
            if not self.accepting:
                self.rejected_after_stop += 1
                return False
            self.active += 1
            self.started += 1
            return True

    def end(self):
        with self.condition:
            require(self.active > 0, "Callback gate lifetime mismatch")
            self.active -= 1
            self.finished += 1
            self.condition.notify_all()

    def stop_accepting(self):
        with self.condition:
            self.accepting = False

    def drain(self, timeout):
        deadline = time.monotonic() + max(0., timeout)
        with self.condition:
            while self.active:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.condition.wait(remaining)
            return True

    def snapshot(self):
        with self.condition:
            return {"accepting": self.accepting, "active": self.active, "started": self.started,
                    "finished": self.finished, "rejected_after_stop": self.rejected_after_stop}


class Recorder:
    def __init__(self, directory):
        self.directory = directory
        self.stream = (directory / "events.jsonl").open("xb")
        os.chmod(directory / "events.jsonl", 0o600)
        self.lock = threading.RLock()
        self.sequence = 0
        self.previous = "0" * 64
        self.raw_bytes = 0
        self.callbacks = Counter()

    def event(self, kind, data):
        with self.lock:
            value = {"sequence": self.sequence, "previous_sha256": self.previous,
                     "at": utc(), "kind": kind, "data": data}
            digest = sha(canonical(value))
            self.stream.write(canonical({**value, "sha256": digest}) + b"\n")
            self.stream.flush()
            os.fsync(self.stream.fileno())
            self.sequence += 1
            self.previous = digest

    def cloud(self, kind, measurement):
        with self.lock:
            self.callbacks[kind] += 1
            require(self.callbacks[kind] <= MAX_CALLBACKS_PER_SENSOR, "Callback count budget exceeded")
            content = bytes(measurement.raw_data)
            stride = 16 if kind == "regular" else 24
            require(len(content) <= MAX_POINTS * stride and len(content) % stride == 0, "Raw cloud bound/stride differs")
            require(self.raw_bytes + len(content) <= MAX_RAW_BYTES, "Raw diagnostic byte budget exceeded")
            filename = f"{kind}-{self.callbacks[kind]:02d}-frame-{measurement.frame}.bin"
            descriptor = exclusive_write(self.directory / filename, content)
            self.raw_bytes += len(content)
            # Persist bytes first. Even a subsequent layout/metadata error retains them.
            self.event("raw_callback_persisted", {"kind": kind, "world_frame": measurement.frame, **descriptor})
            channels = int(measurement.channels)
            counts = [int(measurement.get_point_count(channel)) for channel in range(channels)]
            require(channels == 64 and all(n >= 0 for n in counts) and
                    sum(counts) == len(measurement) == len(content) // stride, "Channel/full point denominator differs")
            layout = "<ffff" if kind == "regular" else "<ffffII"
            records = list(struct.iter_unpack(layout, content))
            require(all(all(math.isfinite(v) for v in record[:4]) for record in records), "Nonfinite raw LiDAR field")
            # Establish native endian/layout using public detection iteration as well.
            public_first = []
            for index, detection in enumerate(measurement):
                if index >= 3:
                    break
                p = detection.point
                item = (p.x, p.y, p.z, detection.intensity) if kind == "regular" else (
                    p.x, p.y, p.z, detection.cos_inc_angle, detection.object_idx, detection.object_tag)
                require(tuple(item) == records[index], "Raw record differs from public detection fields")
                public_first.append(list(item))
            value = {"kind": kind, "world_frame": int(measurement.frame), "timestamp_s": float(measurement.timestamp),
                     "measurement_world_transform": pose(measurement.transform), "horizontal_angle_rad": float(measurement.horizontal_angle),
                     "horizontal_angle_meaning": ("Semantic frame end phase per pinned native source, not callback arrival time" if kind == "semantic" else
                         "Regular serialized header may remain0 in pinned source; not a reliable scan-phase proof"),
                     "channels": channels, "point_counts_by_channel": counts,
                     "channel_byte_offsets": [sum(counts[:index]) * stride for index in range(channels + 1)],
                     "point_count": len(measurement), "stride_bytes": stride, "layout": layout,
                     "public_iteration_first_records": public_first, "raw": descriptor}
            require(math.isfinite(value["timestamp_s"]) and math.isfinite(value["horizontal_angle_rad"]), "Nonfinite cloud time/phase")
            if kind == "semantic":
                value["object_index_counts"] = dict(Counter(str(r[4]) for r in records))
                value["object_tag_counts"] = dict(Counter(str(r[5]) for r in records))
            self.event("cloud_callback", value)
            return value


def run(args):
    # These imports are intentionally confined to the deployed optional environment.
    from ade2e.schemas import Scenario
    from ade2e.simulation_context import PreparedSimulationContext, validate_simulation_context, simulation_input_hash
    from axiom_carla.version import ADAPTER_VERSION
    from axiom_carla.runner import CarlaRunner, _copy_transform
    from axiom_carla.startup import wait_for_stability, StartupFailure
    from axiom_carla.spool import file_lock
    from axiom_carla.native_journal import fields, SETTINGS, WEATHER
    from axiom_carla.vehicle_response import read_physics_configuration, require_physics_configuration
    import carla

    require(sys.byteorder == "little", "This raw layout diagnostic requires the verified little-endian host")
    require(ADAPTER_VERSION == "0.7.0", "Use the deployed optional adapter0.7 environment")
    require(os.getenv("ADE2E_CARLA_EXCLUSIVE") == "1", "Dedicated exclusive CARLA environment is required")
    intent_bytes = args.intent.read_bytes()
    require(len(intent_bytes) < 128 * 1024 and sha(intent_bytes) == PINNED_INTENT_SHA256, "The exact pinned intent file is required")
    intent = json.loads(intent_bytes)
    require(intent["adapter_version"] == "0.7.0" and intent["input_hash"] == PINNED_INPUT_HASH, "Pinned source execution differs")
    scenario = Scenario.model_validate(intent["scenario"])
    context = PreparedSimulationContext.model_validate(intent["context"])
    validate_simulation_context(scenario, context)
    require(simulation_input_hash(scenario, "carla", "0.7.0", context) == PINNED_INPUT_HASH, "Source scenario/context hash mismatch")
    profile = context.sensor_profile
    require(profile.id == "carla-lidar-ego-near/1" and profile.mount.translation_m == (0., 0., 2.2)
            and profile.mount.rotation_xyzw == (0., 0., 0., 1.), "Exact near profile/mount required")
    require(profile.attributes["noise_stddev"] == "0.0", "Original regular sensor noise must stay0")
    execution_root = args.runtime_dir / "executions"
    require(execution_root.is_dir() and not execution_root.is_symlink(), "Existing execution store is required")
    identity_path = execution_root / "store-id"
    require(identity_path.is_file() and not identity_path.is_symlink(), "Existing store identity is required")
    store_id = str(uuid.UUID(identity_path.read_text()))
    # All safety/argument validation precedes exclusive output creation.
    args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
    recorder = Recorder(args.output)
    exclusive_write(args.output / "source-intent.json", intent_bytes)
    nonce = uuid.uuid4().hex
    diagnostic_id = "diagnostic_self_hit_" + nonce
    reservation = {"execution_id": diagnostic_id, "request_hash": sha(canonical({"source_intent_sha256": PINNED_INTENT_SHA256,
        "diagnostic_id": diagnostic_id, "pairs": PAIRS, "semantic_attributes": SEMANTIC_ATTRIBUTES})), "store_id": store_id}
    quarantine = execution_root / "quarantine.json"
    expected, returned, sensors = {}, {}, {}
    streams = {kind: queue.Queue(maxsize=MAX_CALLBACKS_PER_SENSOR) for kind in ("regular", "semantic")}
    callback_errors = queue.Queue(maxsize=4)
    callback_gate = CallbackGate()
    callbacks_drained = False
    report = {"schema_version": "carla-static-self-hit-diagnostic/1", "diagnostic_id": diagnostic_id,
              "created_at": utc(), "status": "failed", "source_intent_sha256": PINNED_INTENT_SHA256,
              "source_input_hash": PINNED_INPUT_HASH, "adapter_version": ADAPTER_VERSION,
              "script_sha256": sha(Path(__file__).read_bytes()), "python_version": sys.version.split()[0],
              "source_commit_reference": SOURCE_COMMIT, "source_links": SOURCES,
              "limits": {"planned_frame_pairs": PAIRS, "fixed_delta_seconds": DT, "main_wall_seconds": MAIN_SECONDS,
                         "cleanup_wall_seconds": CLEANUP_SECONDS, "callback_drain_seconds": CALLBACK_DRAIN_SECONDS,
                         "maximum_inventory_refresh_ticks": 1, "rpc_seconds": RPC_SECONDS, "max_points_per_cloud": MAX_POINTS},
              "scope": "Static rest geometry/actor identity diagnostic. No driving stack, target velocity, route controller or metric verdict.",
              "limitations": ["Semantic object_idx names a registered hit actor;0 means unregistered/absent actor. object_tag is the hit component category, not actor identity.",
                  "Regular dropout changes point count/order; raw clouds must never be zipped by point index.",
                  "Pinned source updates semantic phase but may leave the separate regular horizontal_angle header at0. Header equality is not phase proof; use same-frame/pose channel-aware actual XYZ comparison offline.",
                  "sensor.parent is public serialized CARLA parent metadata, not an independent UE GetOwner readback.",
                  "A point within an actor bounding box alone does not prove its hit actor identity.",
                  "Bounding-box RPC may use a cached fallback; Python does not reveal that source path.",
                  "Callback drain tracks accepted Python callback lifetimes only; sensor unsubscribe is not proof that every native C++ callback queue joined. Actual child exitcode and finalization stderr require separate observation.",
                  "Deadlines are checked around bounded RPCs; a C++ RPC already in flight is not forcibly cancellable."],
              "pairs": [], "inventory_refresh_ticks": 0, "error": None,
              "cleanup": {"status": "not_started", "errors": []}}
    client = world = original_settings = original_weather = original = None
    changed = reservation_owned = False
    deadline = time.monotonic() + MAIN_SECONDS

    def guard():
        remaining = deadline - time.monotonic()
        require(remaining > 0, "Diagnostic wall deadline exceeded")
        if client is not None:
            client.set_timeout(min(RPC_SECONDS, remaining))
        return remaining

    def event_call(operation, function, data=None):
        guard()
        recorder.event("action_intent", {"operation": operation, **(data or {})})
        result = function()
        recorder.event("action_returned", {"operation": operation,
                       "return_value": result if type(result) in (str, bool, int, float) or result is None else None,
                       "meaning": "API return only; explicit control responses and readbacks are separate"})
        guard()
        return result

    def read(function):
        guard()
        value = function()
        guard()
        return value

    def setting_fields(value, names):
        value = fields(value, names)
        require(not value["invalid_fields"], "World fields are incomplete")
        return value

    def map_identity(current_world):
        road = read(current_world.get_map)
        content = read(road.to_opendrive).encode()
        require(len(content) <= 20 * 1024 * 1024, "Map identity exceeds diagnostic bound")
        return {"name": road.name, "opendrive_sha256": sha(content), "opendrive_size_bytes": len(content)}

    def spawn(blueprint, transform, role, parent=None):
        unique_role = "axiom_self_hit_" + nonce + "_" + role
        require(blueprint.has_attribute("role_name"), "Blueprint role_name unavailable")
        blueprint.set_attribute("role_name", unique_role)
        expected[role] = {"role_name": unique_role, "type_id": blueprint.id,
                          "parent_actor_id": parent.id if parent else None, "requested_transform": pose(transform)}
        recorder.event("spawn_identity_intent", {"role": role, **expected[role]})

        def issue():
            actor = world.spawn_actor(blueprint, transform, **({"attach_to": parent, "attachment_type": carla.AttachmentType.Rigid} if parent else {}))
            # Register ownership before any fallible evidence write/deadline check.
            if actor is not None:
                returned[role] = actor
                expected[role]["returned_actor_id"] = actor.id
            return actor
        actor = event_call("world.spawn_actor", issue, {"role": role, **expected[role]})
        require(actor is not None and identity(actor)["role_name"] == unique_role, "Spawn did not retain the intended identity")
        recorder.event("spawn_identity_returned", {"role": role, **identity(actor), "parent_actor_id": actor.parent.id if actor.parent else None})
        return actor

    def configure_sensor(blueprint_id, attributes):
        blueprint = read(world.get_blueprint_library).find(blueprint_id)
        readback = {}
        for key, text in attributes.items():
            require(blueprint.has_attribute(key), "Required sensor attribute missing: " + key)
            blueprint.set_attribute(key, text)
            attribute = blueprint.get_attribute(key)
            integer = key in ("channels", "points_per_second", "noise_seed")
            require(attribute.type == (carla.ActorAttributeType.Int if integer else carla.ActorAttributeType.Float), "Sensor attribute type differs: " + key)
            actual = attribute.as_int() if integer else attribute.as_float()
            target = int(text) if integer else struct.unpack("<f", struct.pack("<f", float(text)))[0]
            require(actual == target, "Sensor attribute readback differs: " + key)
            readback[key] = {"requested": text, "readback": actual, "type": "int" if integer else "float32"}
        recorder.event("detached_sensor_blueprint", {"blueprint_id": blueprint_id, "attributes": readback})
        return blueprint

    def callback(kind):
        def receive(measurement):
            if not callback_gate.begin():
                return
            try:
                value = recorder.cloud(kind, measurement)
                streams[kind].put_nowait(value)
            except Exception as error:
                value = {"kind": kind, "error": type(error).__name__ + ": " + str(error)[:800]}
                try:
                    callback_errors.put_nowait(value)
                except Exception:
                    pass
                try:
                    recorder.event("callback_error", value)
                except Exception:
                    pass
            finally:
                callback_gate.end()
        return receive

    def check_callbacks():
        require(callback_errors.empty(), "A sensor callback failed; inspect persisted callback_error events")

    def inventory_check(phase):
        expected_rows = sorted([*original["actors"], *identities(returned.values())], key=lambda item: item["actor_id"])
        observed = identities(read(world.get_actors))
        expected_keys, observed_keys = inventory_key(expected_rows), inventory_key(observed)
        data = {"phase": phase, "expected": expected_rows, "observed": observed,
                "missing": [item for item in expected_rows if (item["actor_id"], item["type_id"], item["role_name"]) not in observed_keys],
                "foreign": [item for item in observed if (item["actor_id"], item["type_id"], item["role_name"]) not in expected_keys]}
        recorder.event("inventory_check", data)
        return data

    def refresh_spawn_inventory():
        observed = inventory_check("after_sensor_spawn_before_listen")
        if not observed["missing"] and not observed["foreign"]:
            return
        # Only the known returned sensors may be absent from the pre-tick episode cache.
        sensor_keys = inventory_key(identities(sensors.values()))
        require(not observed["foreign"] and all((item["actor_id"], item["type_id"], item["role_name"]) in sensor_keys
                for item in observed["missing"]), "Inventory mismatch is not limited to just-spawned owned sensors")
        before = read(world.get_snapshot)
        report["inventory_refresh_ticks"] += 1
        require(report["inventory_refresh_ticks"] == 1, "Only one spawn inventory refresh tick is authorized")
        frame = event_call("world.tick.inventory_refresh", lambda: world.tick(seconds=min(RPC_SECONDS, guard())))
        after = read(world.get_snapshot)
        require(frame == after.frame == before.frame + 1 and abs(after.timestamp.delta_seconds-DT) <= 1e-6,
                "Unexpected inventory refresh snapshot progression")
        recorder.event("inventory_refresh_snapshot", {"before_world_frame": before.frame,
            "world_frame": after.frame, "source_time_s": after.timestamp.elapsed_seconds})
        observed = inventory_check("after_inventory_refresh_before_listen")
        require(not observed["missing"] and not observed["foreign"], "Inventory still differs after the single bounded refresh tick")

    def control_ack(actors):
        control = carla.VehicleControl(throttle=0., steer=0., brake=1., hand_brake=True, reverse=False)
        requested = {"throttle": 0., "steer": 0., "brake": 1., "hand_brake": True, "reverse": False}
        responses = event_call("client.apply_batch_sync.static_handbrake",
            lambda: client.apply_batch_sync([carla.command.ApplyVehicleControl(actor.id, control) for actor in actors.values()], False),
            {"actors": {role: actor.id for role, actor in actors.items()}, "requested": requested, "do_tick": False})
        actual = [{"actor_id": response.actor_id, "error": response.error or None} for response in responses]
        recorder.event("control_batch_ack", {"requested": requested, "responses": actual})
        require(len(actual) == len(actors) and all(item == {"actor_id": actor.id, "error": None} for item, actor in zip(actual, actors.values())), "Static handbrake was not acknowledged for both exact actors")

    def snapshot_evidence(snapshot, actors):
        output = {"world_frame": snapshot.frame, "source_time_s": snapshot.timestamp.elapsed_seconds,
                  "delta_seconds": snapshot.timestamp.delta_seconds, "actors": {}}
        for role, actor in actors.items():
            item = snapshot.find(actor.id)
            require(item is not None, "Coherent vehicle snapshot is missing " + role)
            output["actors"][role] = {**identity(actor), "snapshot_transform": pose(item.get_transform()),
                "velocity_mps": vector(item.get_velocity()), "angular_velocity_native": vector(item.get_angular_velocity()),
                "acceleration_mps2": vector(item.get_acceleration())}
        return output

    def bracketed_details(snapshot, actors):
        before = read(world.get_snapshot)
        require(before.frame == snapshot.frame, "World advanced before independent pose readback")
        details = {"before_world_frame": before.frame, "vehicles": {}, "sensors": {}}
        for role, actor in actors.items():
            observed = snapshot.find(actor.id)
            transform = observed.get_transform()
            box = read(lambda: actor.bounding_box)
            ctrl = read(actor.get_control)
            details["vehicles"][role] = {**identity(actor), "attributes": dict(actor.attributes),
                "snapshot_transform": pose(transform), "actor_api_transform": pose(read(actor.get_transform)),
                "bounding_box": {"center_actor_local_m": vector(box.location), "half_extents_m": vector(box.extent),
                    "rotation_actor_local_deg": {k: float(getattr(box.rotation, k)) for k in ("pitch", "yaw", "roll")},
                    "local_vertices_native_m": [vector(v) for v in box.get_local_vertices()],
                    "world_vertices_native_m": [vector(v) for v in box.get_world_vertices(transform)]},
                "reported_control": {"throttle": ctrl.throttle, "brake": ctrl.brake, "steer": ctrl.steer,
                                     "hand_brake": ctrl.hand_brake, "reverse": ctrl.reverse}}
            require(ctrl.throttle == 0 and ctrl.brake == 1 and ctrl.hand_brake and not ctrl.reverse,
                    "Static vehicle control readback differs")
        parent_inverse = pose(snapshot.find(actors["ego"].id).get_transform())["inverse_matrix_native"]
        for kind, sensor in sensors.items():
            observed = snapshot.find(sensor.id)
            api_transform = pose(read(sensor.get_transform))
            parent = read(lambda: sensor.parent)
            require(parent is not None and parent.id == actors["ego"].id, "Sensor public parent differs from intended ego")
            details["sensors"][kind] = {**identity(sensor), "attributes": dict(sensor.attributes),
                "public_parent": identity(parent), "parent_source": "CARLA serialized public parent metadata; not UE GetOwner",
                "actor_api_world_transform": api_transform,
                "actor_api_relative_to_ego_matrix": multiply(parent_inverse, api_transform["matrix_native"]),
                "snapshot_status": "observed" if observed is not None else "unavailable",
                "snapshot_world_transform": pose(observed.get_transform()) if observed is not None else None,
                "snapshot_relative_to_ego_matrix": multiply(parent_inverse, pose(observed.get_transform())["matrix_native"]) if observed is not None else None}
        after = read(world.get_snapshot)
        details["after_world_frame"] = after.frame
        require(after.frame == before.frame == snapshot.frame, "World advanced during bbox/parent/pose readback")
        return details

    def publish_reservation():
        nonlocal reservation_owned
        descriptor, name = tempfile.mkstemp(prefix=".self-hit-reservation-", dir=execution_root)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(canonical(reservation)); stream.flush(); os.fsync(stream.fileno())
            os.link(temporary, quarantine)  # Never replace another owner's marker.
            fsync_dir(execution_root)
            reservation_owned = True
        finally:
            temporary.unlink(missing_ok=True)
        recorder.event("diagnostic_reservation", reservation)

    def cleanup():
        nonlocal deadline, reservation_owned, callbacks_drained
        deadline = time.monotonic() + CLEANUP_SECONDS
        cleanup_report = report["cleanup"]
        cleanup_report["status"] = "checking"
        errors = cleanup_report["errors"]
        drain_attempted = False
        callback_gate.stop_accepting()
        if not changed:
            callbacks_drained = callback_gate.drain(CALLBACK_DRAIN_SECONDS)
            report["callback_shutdown"] = {**callback_gate.snapshot(), "drained": callbacks_drained}
            cleanup_report["status"] = "not_started" if callbacks_drained else "uncertain"
            return
        try:
            current = read(client.get_world)
            require(str(current.id) == original["world_id"], "World episode changed; no old actor may authorize deletion")
            require(map_identity(current) == original["map"], "Map changed; automatic cleanup is not authorized")
            all_actors = list(read(current.get_actors))
            require(inventory_key(original["actors"]).issubset(inventory_key(identities(all_actors))), "An original actor identity disappeared")
            owned = []
            # Spawn-returned handles, not the episode's possibly stale actor list,
            # are the primary ownership records. Verify every retained identity.
            for role, actor in returned.items():
                try:
                    item = expected[role]
                    require(actor.id == item["returned_actor_id"] and actor.type_id == item["type_id"]
                            and dict(actor.attributes).get("role_name") == item["role_name"]
                            and item["role_name"] == "axiom_self_hit_" + nonce + "_" + role,
                            "Returned actor ID/type/nonce differs")
                    # Missing cached entries cannot revoke a returned handle,
                    # but an explicitly conflicting current identity must do so.
                    visible = [value for value in all_actors if value.id == actor.id]
                    require(len(visible) <= 1, "Current actor ID is ambiguous")
                    if visible:
                        require(identity(visible[0]) == identity(actor), "Current actor ID conflicts with the retained ownership identity")
                        visible_parent = read(lambda: visible[0].parent)
                        require((visible_parent.id if visible_parent else None) == item["parent_actor_id"],
                                "Current actor parent conflicts with the retained ownership identity")
                    parent = read(lambda: actor.parent)
                    require((parent.id if parent else None) == item["parent_actor_id"], "Owned actor parent identity differs")
                    owned.append((role, actor))
                except Exception as error:
                    errors.append({"phase": "identity", "role": role, "error": str(error)[:800]})
            for role in expected.keys() - returned.keys():
                errors.append({"phase": "identity", "role": role, "error": "Spawn outcome has no returned ownership handle"})
            allowed = inventory_key(original["actors"]) | inventory_key(identities(returned.values()))
            foreign = [identity(a) for a in all_actors if (a.id, a.type_id, dict(a.attributes).get("role_name")) not in allowed]
            if foreign:
                errors.append({"phase": "inventory", "foreign_actors": foreign})
            destroyed = set()
            # Sensor removal and callback drain precede deletion of either parent.
            for role, actor in owned:
                if not actor.type_id.startswith("sensor."):
                    continue
                try:
                    cleanup_call(recorder, errors, guard, "sensor.stop", actor.stop, {"role": role, **identity(actor)})
                except Exception as error:
                    errors.append({"phase": "sensor_stop", "role": role, "error": str(error)[:800]})
                try:
                    result = cleanup_call(recorder, errors, guard, "actor.destroy", actor.destroy, {"role": role, **identity(actor)})
                    require(result is True, "Owned sensor destruction was not acknowledged")
                    destroyed.add(role)
                except Exception as error:
                    errors.append({"phase": "destroy", "role": role, "error": str(error)[:800]})
            drain_attempted = True
            callbacks_drained = callback_gate.drain(min(CALLBACK_DRAIN_SECONDS, max(0., deadline-time.monotonic())))
            report["callback_shutdown"] = {**callback_gate.snapshot(), "drained": callbacks_drained}
            require(callbacks_drained, "Active callbacks did not drain; journal must remain open")
            sensor_roles = {role for role, item in expected.items() if item["type_id"].startswith("sensor.")}
            require(sensor_roles <= destroyed, "Not all owned sensor removals are acknowledged; parents and synchronous world retained")
            for role, actor in owned:
                if actor.type_id.startswith("sensor."):
                    continue
                try:
                    result = cleanup_call(recorder, errors, guard, "actor.destroy", actor.destroy, {"role": role, **identity(actor)})
                    require(result is True, "Owned vehicle destruction was not acknowledged")
                    destroyed.add(role)
                except Exception as error:
                    errors.append({"phase": "destroy", "role": role, "error": str(error)[:800]})
            require(destroyed == set(expected), "Not all spawn outcomes have acknowledged removal")
            observed_settings = setting_fields(read(current.get_settings), SETTINGS)
            observed_weather = setting_fields(read(current.get_weather), WEATHER)
            require(observed_settings in (original["settings"], report["requested_settings"]), "Settings changed outside this diagnostic")
            require(observed_weather in (original["weather"], report["requested_weather"]), "Weather changed outside this diagnostic")
            require(not foreign, "Foreign actors prevent automatic world restoration")
            cleanup_call(recorder, errors, guard, "world.restore_weather", lambda: current.set_weather(original_weather))
            cleanup_call(recorder, errors, guard, "world.restore_settings", lambda: current.apply_settings(original_settings))
            # Restoring async is allowed only after all owned destruction ACKs.
            # Wait for a newer episode snapshot before trusting its actor list.
            previous_frame = read(current.get_snapshot).frame
            verified = read(client.get_world)
            while read(verified.get_snapshot).frame <= previous_frame:
                time.sleep(min(.01, guard()))
            final = {"world_id": str(verified.id), "map": map_identity(verified), "actors": identities(list(read(verified.get_actors))),
                     "settings": setting_fields(read(verified.get_settings), SETTINGS), "weather": setting_fields(read(verified.get_weather), WEATHER)}
            cleanup_report["observed"] = final
            require(final == original and len(final["actors"]) == 23, "Final world/settings/weather/map/original actor set differs")
            require(not any(dict(a.attributes).get("role_name", "").startswith("axiom_self_hit_" + nonce) for a in read(verified.get_actors)), "Owned diagnostic actor remains")
            require(not errors, "Cleanup recorded unresolved operations")
            cleanup_report.update(status="confirmed", original_actors=23, owned_actors_remaining=0, map_hash_preserved=True)
        except Exception as error:
            errors.append({"phase": "verification", "error": type(error).__name__ + ": " + str(error)[:1000]})
            cleanup_report["status"] = "uncertain"
        finally:
            if not drain_attempted:
                callbacks_drained = callback_gate.drain(min(CALLBACK_DRAIN_SECONDS, max(0., deadline-time.monotonic())))
            report["callback_shutdown"] = {**callback_gate.snapshot(), "drained": callbacks_drained}
            if not callbacks_drained:
                cleanup_report["status"] = "uncertain"
                errors.append({"phase": "callbacks", "error": "Callbacks remain active; event stream is not sealed or closed"})
            elif not best_effort_event(recorder, errors, "cleanup_result", cleanup_report):
                cleanup_report["status"] = "uncertain"
            # Reservation deliberately remains after any uncertainty or process death.
            if cleanup_report["status"] == "confirmed" and reservation_owned:
                if quarantine.is_file() and not quarantine.is_symlink() and quarantine.read_bytes() == canonical(reservation):
                    quarantine.unlink(); fsync_dir(execution_root); reservation_owned = False
                else:
                    cleanup_report["status"] = "uncertain"
                    errors.append({"phase": "reservation", "error": "Reservation changed; foreign marker retained"})

    try:
        # The production worker uses this same file lock. It stays held through cleanup and report flush.
        with file_lock(execution_root / "engine.lock"):
            try:
                require(not quarantine.exists() and not quarantine.is_symlink(), "Existing quarantine forbids diagnostic execution")
                client = carla.Client(args.host, args.port)
                guard()
                server_version = read(client.get_server_version)
                client_version = read(client.get_client_version)
                require(server_version == client_version == SOURCE_COMMIT, "CARLA client/server build differs from pinned source trial")
                world = read(client.get_world)
                road_map = read(world.get_map)
                require(road_map.name.split("/")[-1] == scenario.map_name, "Loaded map differs; no map reload is allowed")
                original_settings, original_weather = read(world.get_settings), read(world.get_weather)
                require(not original_settings.synchronous_mode, "Existing synchronous tick owner forbids this diagnostic")
                original_actors = list(read(world.get_actors))
                require(len(original_actors) == 23, "Original actor denominator must be23")
                require(not any(a.type_id.startswith(("vehicle.", "walker.", "sensor.")) for a in original_actors), "Foreign vehicle/walker/sensor actor present")
                original = {"world_id": str(world.id), "map": map_identity(world), "actors": identities(original_actors),
                    "settings": setting_fields(original_settings, SETTINGS), "weather": setting_fields(original_weather, WEATHER)}
                report["original"] = original
                recorder.event("world_original", original)
                transforms = read(lambda: CarlaRunner()._straight_spawn(road_map, scenario))
                report["spawn_transforms"] = {role: {"source": pose(source), "requested": pose(_copy_transform(carla, source, z_offset=.3))}
                    for role, source in zip(("ego", "lead"), transforms)}
                regular_bp = configure_sensor("sensor.lidar.ray_cast", profile.attributes)
                semantic_attrs = {key: profile.attributes[key] for key in SEMANTIC_ATTRIBUTES}
                semantic_bp = configure_sensor("sensor.lidar.ray_cast_semantic", semantic_attrs)
                report["sensor_configuration"] = {"regular": dict(profile.attributes), "semantic": semantic_attrs,
                    "semantic_noise_dropout": "Not modelled by semantic LiDAR; no unsupported noise/dropoff attributes are copied"}
                settings = read(world.get_settings)
                settings.synchronous_mode, settings.fixed_delta_seconds = True, DT
                settings.substepping, settings.max_substep_delta_time, settings.max_substeps = True, .01, 10
                weather = carla.WeatherParameters(cloudiness=10, precipitation=0, wetness=0, fog_density=0, sun_altitude_angle=65)
                report["requested_settings"] = setting_fields(settings, SETTINGS)
                report["requested_weather"] = setting_fields(weather, WEATHER)
                publish_reservation()  # Atomic worker-compatible marker before any world mutation.
                changed = True
                event_call("world.apply_settings", lambda: world.apply_settings(settings), report["requested_settings"])
                event_call("world.set_weather", lambda: world.set_weather(weather), report["requested_weather"])
                vehicles = {}
                for role, transform in zip(("ego", "lead"), transforms):
                    bp = read(world.get_blueprint_library).find("vehicle.tesla.model3")
                    vehicles[role] = spawn(bp, _copy_transform(carla, transform, z_offset=.3), role)
                # Configure physics only AFTER both same-blueprint vehicle spawns.
                for role, actor in vehicles.items():
                    physics = read(actor.get_physics_control)
                    wheels = physics.wheels
                    for wheel in wheels:
                        wheel.tire_friction = scenario.parameters.friction
                    physics.wheels = wheels
                    event_call("actor.apply_physics_control", lambda a=actor, p=physics: a.apply_physics_control(p),
                               {"role": role, "actor_id": actor.id, "tire_friction": scenario.parameters.friction})
                control_ack(vehicles)

                class StartupWorld:
                    def get_snapshot(self):
                        return read(world.get_snapshot)
                    def tick(self, **kwargs):
                        kwargs["seconds"] = min(RPC_SECONDS, guard(), kwargs.get("seconds", RPC_SECONDS))
                        return event_call("world.tick.startup", lambda: world.tick(**kwargs))
                try:
                    report["startup"] = wait_for_stability(StartupWorld(), vehicles, dt=DT,
                        on_sample=lambda sample: recorder.event("startup_sample", sample))
                except StartupFailure as error:
                    report["startup"] = error.report
                    raise
                rest = read(world.get_snapshot)
                report["physics_configuration"] = read(lambda: read_physics_configuration(rest, world, vehicles, scenario.parameters.friction))
                recorder.event("post_rest_physics_readback", report["physics_configuration"])
                require_physics_configuration(report["physics_configuration"])
                mount = carla.Transform(carla.Location(x=0., y=0., z=2.2), carla.Rotation())
                for kind, bp in (("regular", regular_bp), ("semantic", semantic_bp)):
                    sensors[kind] = spawn(bp, mount, kind, vehicles["ego"])
                refresh_spawn_inventory()
                for kind, sensor in sensors.items():
                    event_call("sensor.listen", lambda s=sensor, k=kind: s.listen(callback(k)), {"kind": kind, "actor_id": sensor.id})
                previous = read(world.get_snapshot)
                report["after_rest"] = snapshot_evidence(previous, vehicles)
                for index in range(PAIRS):
                    check_callbacks()
                    observed_inventory = inventory_check("before_capture_tick_" + str(index))
                    require(not observed_inventory["missing"] and not observed_inventory["foreign"],
                            "Foreign/missing actor appeared during static capture; stop further ticks")
                    frame = event_call("world.tick.static_capture", lambda: world.tick(seconds=min(RPC_SECONDS, guard())), {"pair_index": index})
                    snapshot = read(world.get_snapshot)
                    require(frame == snapshot.frame == previous.frame + 1 and abs(snapshot.timestamp.delta_seconds - DT) <= 1e-6 and
                            abs(snapshot.timestamp.elapsed_seconds - previous.timestamp.elapsed_seconds - DT) <= 1e-6, "Unexpected static frame/time progression")
                    pair = {"index": index, "snapshot": snapshot_evidence(snapshot, vehicles), "clouds": {}}
                    report["pairs"].append(pair)
                    for kind in ("regular", "semantic"):
                        try:
                            cloud = streams[kind].get(timeout=min(5., guard()))
                        except queue.Empty as error:
                            raise RuntimeError("Missing same-frame " + kind + " cloud") from error
                        pair["clouds"][kind] = cloud
                        require(cloud["world_frame"] == frame and abs(cloud["timestamp_s"] - snapshot.timestamp.elapsed_seconds) <= 1e-6,
                                "Late/future cloud does not belong to the exact static frame")
                    check_callbacks()
                    pair["independent_readbacks"] = bracketed_details(snapshot, vehicles)
                    a, b = pair["clouds"]["regular"], pair["clouds"]["semantic"]
                    pair["clouds_equal_measurement_pose"] = a["measurement_world_transform"] == b["measurement_world_transform"]
                    pair["clouds_equal_horizontal_phase"] = a["horizontal_angle_rad"] == b["horizontal_angle_rad"]
                    parent_inverse = pair["snapshot"]["actors"]["ego"]["snapshot_transform"]["inverse_matrix_native"]
                    for kind, cloud in pair["clouds"].items():
                        cloud["measurement_relative_to_snapshot_ego_matrix"] = multiply(parent_inverse, cloud["measurement_world_transform"]["matrix_native"])
                    recorder.event("paired_static_observation", pair)
                    require(pair["clouds_equal_measurement_pose"], "Co-located sensor measurement pose differs; no automatic correction")
                    previous = snapshot
                require(all(stream.empty() for stream in streams.values()), "Unexpected extra cloud callbacks remain")
                require(all(recorder.callbacks[kind] == PAIRS for kind in ("regular", "semantic")), "Full callback denominator differs from eight pairs")
                report["status"] = "collected"
            except Exception as error:
                report["error"] = {"type": type(error).__name__, "message": str(error)[:1400]}
                best_effort_event(recorder, report.setdefault("journal_errors", []), "diagnostic_error", report["error"])
            finally:
                cleanup()
                report["reservation_retained"] = reservation_owned
                if report["cleanup"]["status"] == "uncertain":
                    report["status"] = "failed"
                if not callbacks_drained or not best_effort_event(recorder, report.setdefault("journal_errors", []), "diagnostic_final", {"status": report["status"], "error": report["error"], "cleanup_status": report["cleanup"]["status"], "reservation_retained": reservation_owned}):
                    report["status"] = "failed"
                report["event_chain"] = {"entries": recorder.sequence, "head_sha256": recorder.previous, "sealed": callbacks_drained}
                report["raw_callbacks"] = dict(recorder.callbacks)
                report["raw_bytes"] = recorder.raw_bytes
                report["finished_at"] = utc()
                exclusive_write(args.output / "report.json", canonical(report))
    except Exception as error:
        # Lock/preflight failures still have an exclusive diagnostic evidence directory.
        if not (args.output / "report.json").exists():
            report["error"] = {"type": type(error).__name__, "message": str(error)[:1400]}
            report["reservation_retained"] = reservation_owned
            if callbacks_drained:
                best_effort_event(recorder, report.setdefault("journal_errors", []), "outer_error", report["error"])
            exclusive_write(args.output / "report.json", canonical(report))
        raise
    finally:
        if callbacks_drained:
            recorder.stream.close()
    print(json.dumps({"status": report["status"], "output": str(args.output), "pairs": len(report["pairs"]),
                      "cleanup": report["cleanup"]["status"], "reservation_retained": reservation_owned}, sort_keys=True))
    return 0 if report["status"] == "collected" and report["cleanup"]["status"] == "confirmed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intent", type=Path, required=True)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2000)
    args = parser.parse_args()
    require(1 <= args.port <= 65535, "Invalid CARLA port")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
