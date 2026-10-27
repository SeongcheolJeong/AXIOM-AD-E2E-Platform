"""Pure diagnostic contract checks; these are not native CARLA plant evidence."""
from copy import deepcopy
import hashlib
import importlib.util
import json
import math
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "tested_carla_brake_protocol", ROOT / "scripts/carla_brake_protocol.py"
)
protocol = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(protocol)


def independent_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def expected_control(brake=0.0, throttle=0.0):
    return {"throttle": throttle, "brake": brake, "steer": 0.0,
            "hand_brake": False, "reverse": False, "manual_gear_shift": False, "gear": 1}


@pytest.fixture
def intent():
    selected = protocol.make_protocol("throttle")
    available = lambda value: {"status": "available", "value": value, "reason": None}
    return {
        "schema_version": "carla-brake-intent/1",
        "experiment_id": "b2222222-2222-4222-8222-222222222222",
        "protocol": selected,
        "protocol_sha256": independent_hash(selected),
        "sequence_sha256": independent_hash([
            {"step": i, "time_s": i * 0.05, "control": expected_control()} for i in range(80)
        ]),
        "expected_store_id": "a1111111-1111-4111-8111-111111111111",
        "engine_build": "1ae5356", "adapter_version": "0.8.0",
        "expected_world": {
            "world_id": "13687974690369489725",
            "map": {"name": "Carla/Maps/Town10HD_Opt", "opendrive_sha256": "a" * 64,
                    "opendrive_size_bytes": 1857682},
            "actors": [{"actor_id": 1, "type_id": "traffic.traffic_light", "role_name": None},
                       {"actor_id": 2, "type_id": "spectator", "role_name": "spectator"}],
            "settings": {"values": {"synchronous_mode": False, "fixed_delta_seconds": None},
                         "invalid_fields": {}},
            "weather": {"values": {"cloudiness": 20.0}, "invalid_fields": {}},
        },
        "spawn_transform": {"position_m": {"x": 10.0, "y": -57.0, "z": 0.9},
                            "rotation_deg": {"pitch": 0.0, "yaw": -0.0234375, "roll": 0.0}},
        "requested_weather": {"values": {"cloudiness": 10.0}, "invalid_fields": {}},
        "expected_physics": {
            "scalars": {"mass": available(1845.0), "use_gear_autobox": available(True)},
            "wheels": [{"index": i, "fields": {"tire_friction": available(0.800000011920929)}}
                       for i in range(4)],
        },
        "source_files": {name: "b" * 64 for name in (
            "carla_brake_protocol.py", "diagnose_carla_brake_response.py", "diagnose_carla_self_hit.py")},
        "source_evidence": {"path": "authored-fixture/post-campaign-world.json", "sha256": "c" * 64},
    }


@pytest.mark.parametrize("initialization", ["throttle", "velocity_injection"])
@pytest.mark.parametrize("brake", [0.0, 0.025, 0.05, 0.1, 0.2, 0.35, 0.6, 1.0])
def test_pulse_has_exact_eighty_commands_and_five_pulse_steps(initialization, brake):
    selected = protocol.make_protocol(initialization, measurement="pulse", brake_level=brake)
    rows = protocol.measurement_sequence(selected)
    expected = [{"step": i, "time_s": i * 0.05,
                 "control": expected_control(brake if 5 <= i < 10 else 0.0)} for i in range(80)]
    assert rows == expected
    assert independent_hash(rows) == protocol.sha(protocol.canonical(rows))
    assert [row["step"] for row in rows[5:10]] == [5, 6, 7, 8, 9]
    assert rows[4]["time_s"] == 0.2 and rows[10]["time_s"] == 0.5
    assert rows[-1]["time_s"] == 3.95


@pytest.mark.parametrize("initialization", ["throttle", "velocity_injection"])
def test_coast_never_applies_throttle_brake_or_handbrake(initialization):
    selected = protocol.make_protocol(initialization)
    assert len(protocol.measurement_sequence(selected)) == 80
    assert all(row["control"] == expected_control() for row in protocol.measurement_sequence(selected))
    assert selected["policy"]["rest_control"] == expected_control()
    with pytest.raises(ValueError, match="Coast"):
        protocol.make_protocol(initialization, brake_level=0.025)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True, False, "0.2", None])
def test_brake_input_does_not_coerce_invalid_numbers(value):
    with pytest.raises(ValueError):
        protocol.make_protocol("throttle", measurement="pulse", brake_level=value)
    selected = protocol.make_protocol("throttle", measurement="pulse")
    selected["brake_level"] = value
    with pytest.raises(ValueError):
        protocol.validate_protocol(selected)


@pytest.mark.parametrize("field,value", [
    ("schema_version", "carla-brake-protocol/4"), ("initialization", "auto"),
    ("measurement", "continuous"), ("brake_level", -0.025), ("brake_level", 0.3),
    ("brake_level", 1.01), ("repeat_index", True), ("repeat_index", 0),
    ("repeat_index", 101), ("repeat_index", 1.0), ("seed", False),
    ("seed", -1), ("seed", 2**32), ("seed", 42.0),
])
def test_protocol_rejects_unsupported_or_untyped_inputs(field, value):
    selected = protocol.make_protocol("throttle", measurement="pulse")
    selected[field] = value
    with pytest.raises(ValueError):
        protocol.validate_protocol(selected)


def test_unknown_or_missing_protocol_fields_are_not_silently_dropped():
    selected = protocol.make_protocol("throttle")
    for key in selected:
        candidate = deepcopy(selected)
        del candidate[key]
        with pytest.raises(ValueError):
            protocol.validate_protocol(candidate)
    selected["fallback_initialization"] = "velocity_injection"
    with pytest.raises(ValueError):
        protocol.validate_protocol(selected)


@pytest.mark.parametrize("path,value", [
    (("dt_s",), 0.1), (("measurement_steps",), 81), (("pulse_steps",), 6),
    (("rest_speed_mps",), 0.1), (("rest_control", "brake"), 1.0),
    (("rest_control", "hand_brake"), True), (("collision_observer",), 1),
    (("lidar_sensor",), 0), (("speed_window_mps",), [1.8, 2.2]),
    (("preparation_brake",), False), (("main_wall_seconds",), float("nan")),
])
def test_policy_is_pinned_including_nested_controls_and_numeric_types(path, value):
    selected = protocol.make_protocol("throttle")
    target = selected["policy"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        protocol.validate_protocol(selected)


def test_returned_policy_and_sequences_do_not_alias_the_source_or_each_other():
    baseline = protocol.canonical(protocol.POLICY)
    selected = protocol.make_protocol("throttle")
    validated = protocol.validate_protocol(selected)
    validated["policy"]["rest_control"]["brake"] = 1.0
    validated["policy"]["speed_window_mps"][0] = 0.0
    assert protocol.canonical(protocol.POLICY) == baseline
    assert selected == protocol.make_protocol("throttle")
    rows = protocol.measurement_sequence(selected)
    rows[0]["control"]["brake"] = 1.0
    assert rows[1]["control"] == expected_control()
    assert protocol.measurement_sequence(selected)[0]["control"] == expected_control()
    for change in (lambda p: p.pop("target_speed_mps"), lambda p: p.update(fallback=True)):
        invalid = deepcopy(selected)
        change(invalid["policy"])
        with pytest.raises(ValueError):
            protocol.validate_protocol(invalid)


@pytest.mark.parametrize("speed,throttle", [(-2.0, 0.35), (0.0, 0.35), (1.5, 0.2),
                                           (1.9, 0.04), (2.0, 0.0), (2.5, 0.0)])
def test_throttle_preparation_is_bounded_and_never_brakes(speed, throttle):
    value = protocol.preparation_control(protocol.make_protocol("throttle"), speed)
    assert value == pytest.approx(expected_control(throttle=throttle))


def test_injection_never_falls_back_to_throttle_preparation():
    selected = protocol.make_protocol("velocity_injection")
    with pytest.raises(ValueError, match="not selected"):
        protocol.preparation_control(selected, 0.0)


@pytest.mark.parametrize("speed", [True, False, "2", None, math.nan, math.inf, -math.inf])
def test_preparation_and_speed_gate_reject_nonfinite_or_coerced_speed(speed):
    with pytest.raises(ValueError):
        protocol.preparation_control(protocol.make_protocol("throttle"), speed)
    with pytest.raises(ValueError):
        protocol.speed_in_window(speed)


@pytest.mark.parametrize("speed,accepted", [(1.9, True), (2.1, True), (1.899999, False),
                                          (2.100001, False), (-2.0, False), (0.0, False)])
def test_speed_gate_is_signed_and_inclusive(speed, accepted):
    assert protocol.speed_in_window(speed) is accepted


def test_signed_heading_uses_native_yaw_without_norm_or_absolute_value():
    assert protocol.signed_heading({"x": 2.0, "y": 0.0, "z": 5.0}, 0.0) == 2.0
    assert protocol.signed_heading({"x": -2.0, "y": 0.0, "z": 0.0}, 0.0) == -2.0
    assert protocol.signed_heading({"x": 0.0, "y": 2.0, "z": 0.0}, 90.0) == pytest.approx(2.0)
    assert protocol.signed_heading({"x": 0.0, "y": -2.0, "z": 0.0}, 90.0) == pytest.approx(-2.0)
    assert protocol.signed_heading({"x": 2.0, "y": 0.0, "z": 0.0}, 180.0) == pytest.approx(-2.0)
    for bad in ({"x": 1.0, "y": 0.0}, {"x": True, "y": 0.0, "z": 0.0},
                {"x": 1.0, "y": 0.0, "z": math.nan}, {"x": 1.0, "y": 0.0, "z": 0.0, "frame": 1}):
        with pytest.raises(ValueError):
            protocol.signed_heading(bad, 0.0)


def test_spatial_bounds_keep_signed_lateral_wrap_heading_and_all_failure_reasons():
    spawn = {"position_m": {"x": 10.0, "y": 20.0, "z": 1.0},
             "rotation_deg": {"pitch": 0.0, "yaw": 90.0, "roll": 0.0}}
    row = protocol.spatial_check({"x": 11.0, "y": 20.0, "z": 1.0},
                                 {"pitch": 0.0, "yaw": 95.0, "roll": 0.0},
                                 {"x": 0.0, "y": -3.0, "z": 0.0}, spawn)
    assert row["status"] == "within_bounds"
    assert row["lateral_distance_m"] == -1.0 and row["heading_error_deg"] == 5.0
    assert row["speed_norm_mps"] == 3.0
    failed = protocol.spatial_check({"x": 12.0, "y": 50.0, "z": 1.0},
                                    {"pitch": 0.0, "yaw": 96.0, "roll": 0.0},
                                    {"x": 0.0, "y": 0.0, "z": 3.01}, spawn)
    assert failed["failures"] == ["distance", "lateral", "heading", "speed"]
    spawn["rotation_deg"]["yaw"] = 179.0
    wrapped = protocol.spatial_check(spawn["position_m"],
                                     {"pitch": 0.0, "yaw": -179.0, "roll": 0.0},
                                     {"x": 0.0, "y": 0.0, "z": 0.0}, spawn)
    assert wrapped["heading_error_deg"] == 2.0 and wrapped["status"] == "within_bounds"


def test_intent_preserves_uint64_world_identity_and_returns_independent_copy(intent):
    before = deepcopy(intent)
    accepted = protocol.validate_intent(intent)
    assert accepted == before and accepted["expected_world"]["world_id"] == "13687974690369489725"
    accepted["protocol"]["policy"]["rest_control"]["brake"] = 1.0
    accepted["expected_world"]["actors"].clear()
    assert intent == before


def test_parse_intent_uses_bytes_with_exact_budget_and_retains_decimal_identity(intent):
    raw = json.dumps(intent).encode()
    assert protocol.parse_intent(raw) == intent
    assert protocol.parse_intent(raw + b" " * (128 * 1024 - len(raw))) == intent
    with pytest.raises(ValueError, match="byte budget"):
        protocol.parse_intent(raw + b" " * (128 * 1024 - len(raw) + 1))
    for nonbytes in (raw.decode(), bytearray(raw), memoryview(raw), None):
        with pytest.raises(ValueError, match="byte budget"):
            protocol.parse_intent(nonbytes)


@pytest.mark.parametrize("original,replacement", [
    ('"schema_version":"carla-brake-intent/1"',
     '"schema_version":"carla-brake-intent/1","schema_version":"carla-brake-intent/1"'),
    ('"brake_level":0.0', '"brake_level":1.0,"brake_level":0.0'),
    ('"synchronous_mode":false', '"synchronous_mode":true,"synchronous_mode":false'),
    ('"cloudiness":20.0', '"cloudiness":20.0,"cloudiness":20.0'),
])
def test_parse_intent_rejects_duplicate_keys_even_when_last_value_would_validate(intent, original, replacement):
    raw = protocol.canonical(intent).decode()
    assert original in raw
    raw = raw.replace(original, replacement, 1).encode()
    # A default decoder would discard the evidence of the conflicting first value.
    assert protocol.validate_intent(json.loads(raw)) == intent
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        protocol.parse_intent(raw)


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_parse_intent_rejects_nonstandard_nonfinite_json_tokens(intent, token):
    raw = protocol.canonical(intent).replace(b'"cloudiness":20.0',
                                            ('"cloudiness":' + token).encode(), 1)
    with pytest.raises(ValueError, match="Nonfinite JSON token"):
        protocol.parse_intent(raw)


@pytest.mark.parametrize("raw", [b"", b"{", b"null", b"[]", b"{} {}"])
def test_parse_intent_rejects_truncated_wrong_type_and_trailing_documents(raw):
    with pytest.raises(ValueError):
        protocol.parse_intent(raw)


@pytest.mark.parametrize("path,value", [
    (("schema_version",), "carla-brake-intent/2"), (("adapter_version",), "0.7.0"),
    (("engine_build",), "unknown"), (("protocol_sha256",), "0" * 64),
    (("sequence_sha256",), "0" * 64), (("expected_world", "world_id"), 13687974690369489725),
    (("expected_world", "map", "name"), "Carla/Maps/Town01"),
    (("expected_world", "map", "opendrive_sha256"), "A" * 64),
    (("expected_world", "map", "opendrive_size_bytes"), True),
    (("expected_world", "settings", "values", "synchronous_mode"), True),
    (("expected_world", "settings", "values", "synchronous_mode"), 0),
    (("requested_weather", "values", "cloudiness"), math.nan),
    (("requested_weather", "invalid_fields"), {"cloudiness": "not observed"}),
    (("spawn_transform", "position_m", "z"), True),
    (("spawn_transform", "position_m", "z"), math.inf),
    (("source_files", "carla_brake_protocol.py"), "b" * 63),
    (("source_files", "diagnose_carla_brake_response.py"), "B" * 64),
    (("source_evidence", "sha256"), "not-a-hash"), (("source_evidence", "path"), ""),
    (("expected_physics", "scalars", "mass", "value"), math.nan),
    (("expected_physics", "scalars", "mass", "status"), "not_observed"),
])
def test_intent_rejects_unsupported_source_version_hash_or_invalid_observation(intent, path, value):
    item = intent
    for key in path[:-1]:
        item = item[key]
    item[path[-1]] = value
    with pytest.raises(ValueError):
        protocol.validate_intent(intent)


def test_protocol_changes_require_both_protocol_and_sequence_hashes(intent):
    intent["protocol"]["measurement"] = "pulse"
    intent["protocol"]["brake_level"] = 0.2
    with pytest.raises(ValueError, match="Protocol hash"):
        protocol.validate_intent(intent)
    intent["protocol_sha256"] = independent_hash(intent["protocol"])
    with pytest.raises(ValueError, match="sequence hash"):
        protocol.validate_intent(intent)
    intent["sequence_sha256"] = independent_hash([
        {"step": i, "time_s": i * 0.05, "control": expected_control(0.2 if 5 <= i < 10 else 0.0)}
        for i in range(80)
    ])
    assert protocol.validate_intent(intent) == intent


def test_intent_exact_source_set_and_top_level_fields(intent):
    for key in intent:
        candidate = deepcopy(intent)
        candidate.pop(key)
        with pytest.raises(ValueError):
            protocol.validate_intent(candidate)
    for source in intent["source_files"]:
        candidate = deepcopy(intent)
        candidate["source_files"].pop(source)
        with pytest.raises(ValueError):
            protocol.validate_intent(candidate)
    candidate = deepcopy(intent)
    candidate["source_files"]["unreviewed.py"] = "d" * 64
    with pytest.raises(ValueError):
        protocol.validate_intent(candidate)
    intent["fallback"] = "old_runner"
    with pytest.raises(ValueError):
        protocol.validate_intent(intent)


@pytest.mark.parametrize("actors", [
    [], [{"actor_id": 1, "type_id": "vehicle.tesla.model3", "role_name": "foreign"}],
    [{"actor_id": 1, "type_id": "sensor.other.collision", "role_name": None}],
    [{"actor_id": 1, "type_id": "walker.pedestrian.0001", "role_name": None}],
    [{"actor_id": True, "type_id": "spectator", "role_name": None}],
    [{"actor_id": 2, "type_id": "spectator", "role_name": None},
     {"actor_id": 1, "type_id": "spectator", "role_name": None}],
    [{"actor_id": 1, "type_id": "spectator", "role_name": None}] * 2,
])
def test_intent_refuses_foreign_dynamic_actors_or_ambiguous_inventory(intent, actors):
    intent["expected_world"]["actors"] = actors
    with pytest.raises(ValueError):
        protocol.validate_intent(intent)


def test_legacy_protocol_policy_and_sequence_retain_archived_v1_hashes(intent):
    # Fixed from the original v1 injection-1 source intent, not recomputed expectations.
    legacy = protocol.make_protocol("velocity_injection", version="carla-brake-protocol/1")
    assert independent_hash(legacy) == "f6d35af8ad5bd9d83109f35b276589d5f28157065e72ed0178c776553e2e2d9c"
    assert independent_hash(legacy["policy"]) == "727090a70c63c8ff27592f132fef3d72b64296e31c127b83188f829dd450e1c3"
    assert independent_hash(protocol.measurement_sequence(legacy)) == "7431dc72529092b2d2e33b902acabf0d14ada10a19e282c4143d010200830633"
    intent["protocol"] = legacy
    intent["protocol_sha256"] = independent_hash(legacy)
    original_bytes = protocol.canonical(intent)
    assert protocol.canonical(protocol.parse_intent(original_bytes)) == original_bytes


def test_v2_changes_only_four_rest_abort_limits_not_measurement_or_convergence():
    legacy = protocol.make_protocol("throttle", version="carla-brake-protocol/1")
    current = protocol.make_protocol("throttle")
    assert current["schema_version"] == "carla-brake-protocol/2"
    added = {"maximum_rest_horizontal_speed_mps": 0.3, "maximum_rest_vertical_speed_mps": 4.5,
             "maximum_rest_horizontal_displacement_m": 0.5, "maximum_rest_vertical_displacement_m": 1.25}
    assert current["policy"] == {**legacy["policy"], **added}
    assert {k: v for k, v in current.items() if k not in ("schema_version", "policy")} == {
        k: v for k, v in legacy.items() if k not in ("schema_version", "policy")}
    assert protocol.measurement_sequence(current) == protocol.measurement_sequence(legacy)
    for speed in (-0.1, 0.0, 1.9, 2.0, 2.1):
        assert protocol.preparation_control(current, speed) == protocol.preparation_control(legacy, speed)
    current["schema_version"] = "carla-brake-protocol/1"
    with pytest.raises(ValueError, match="policy"):
        protocol.validate_protocol(current)


@pytest.mark.parametrize("phase", ["rest", "inventory_refresh", "preparation", "measurement"])
def test_archived_vertical_fall_is_allowed_only_by_explicit_v2_rest_policy(phase):
    position = {"x": 10.912545204162598, "y": -57.40138626098633, "z": 0.28259962797164917}
    rotation = {"pitch": 0.0, "yaw": -0.0234375, "roll": 0.0}
    spawn = {"position_m": {**position, "z": 0.8999999761581421}, "rotation_deg": rotation}
    velocity = {"x": 0.0, "y": 0.0, "z": -3.429999351501465}
    legacy = protocol.spatial_check(position, rotation, velocity, spawn, phase=phase,
        protocol=protocol.make_protocol("velocity_injection", version="carla-brake-protocol/1"))
    current = protocol.spatial_check(position, rotation, velocity, spawn, phase=phase,
        protocol=protocol.make_protocol("velocity_injection"))
    assert legacy["failures"] == ["speed"] and legacy["speed_norm_mps"] == abs(velocity["z"])
    assert current["status"] == ("within_bounds" if phase in ("rest", "inventory_refresh") else "failed")
    if phase in ("rest", "inventory_refresh"):
        assert current["bounds_policy"] == "rest_settling/2"
        assert current["horizontal_speed_mps"] == 0.0 and current["vertical_speed_mps"] == velocity["z"]
    else:
        assert current == legacy


@pytest.mark.parametrize("field,value,reason", [
    ("vx", 0.300001, "rest_horizontal_speed"), ("vz", -4.500001, "rest_vertical_speed"),
    ("vz", 4.500001, "rest_vertical_speed"), ("x", 0.500001, "rest_horizontal_displacement"),
    ("z", -1.250001, "rest_vertical_displacement"), ("z", 1.250001, "rest_vertical_displacement"),
])
def test_rest_policy_rejects_each_escape_direction_and_accepts_exact_boundary(field, value, reason):
    position, velocity = {"x": 0.0, "y": 0.0, "z": 0.0}, {"x": 0.0, "y": 0.0, "z": 0.0}
    rotation = {"pitch": 0.0, "yaw": 0.0, "roll": 0.0}
    spawn = {"position_m": deepcopy(position), "rotation_deg": rotation}
    target, key = (velocity, field[1:]) if field.startswith("v") else (position, field)
    target[key] = value
    row = protocol.spatial_check(position, rotation, velocity, spawn, phase="rest",
                                 protocol=protocol.make_protocol("throttle"))
    assert row["status"] == "failed" and row["failures"] == [reason]
    target[key] = math.copysign({"vx": 0.3, "vz": 4.5, "x": 0.5, "z": 1.25}[field], value)
    assert protocol.spatial_check(position, rotation, velocity, spawn, phase="rest",
                                   protocol=protocol.make_protocol("throttle"))["status"] == "within_bounds"


def test_unknown_phase_cannot_select_rest_exemption():
    with pytest.raises(ValueError, match="Unknown diagnostic phase"):
        protocol.spatial_check({"x": 0.0, "y": 0.0, "z": 0.0},
            {"pitch": 0.0, "yaw": 0.0, "roll": 0.0}, {"x": 0.0, "y": 0.0, "z": -3.43},
            {"position_m": {"x": 0.0, "y": 0.0, "z": 0.0},
             "rotation_deg": {"pitch": 0.0, "yaw": 0.0, "roll": 0.0}},
            phase="restish", protocol=protocol.make_protocol("throttle"))


def settled_rows(count=21, *, yaw=0.0):
    """Authored steady observations; no simulated or calibrated plant implied."""
    origin = {"x": 10.0, "y": -57.0, "z": 0.9}
    spawn = {"position_m": origin, "rotation_deg": {"pitch": 0.0, "yaw": yaw, "roll": 0.0}}
    radians = math.radians(yaw)
    available = lambda value: {"status": "available", "value": value, "reason": None}
    rows = []
    for i in range(count):
        frame = 100 + i
        distance = 12.05 - 0.1 * (count - i - 1)
        rows.append({
            "world_frame": frame, "source_time_s": i * 0.05,
            "ego": {
                "position_m": {"x": origin["x"] + distance * math.cos(radians),
                               "y": origin["y"] + distance * math.sin(radians), "z": 0.002},
                "rotation_deg": {"pitch": 0.0, "yaw": yaw, "roll": 0.0},
                "velocity_mps": {"x": 2.0 * math.cos(radians), "y": 2.0 * math.sin(radians), "z": 0.0},
                "acceleration_mps2": {"x": 0.0, "y": 0.0, "z": 0.0},
                "angular_velocity_deg_s": {"x": 0.0, "y": 0.0, "z": 0.0},
            },
            "reported_control": {"status": "available", "before_world_frame": frame,
                                 "after_world_frame": frame, "data": expected_control(throttle=0.04)},
            "telemetry": {"status": "available", "before_world_frame": frame,
                          "after_world_frame": frame, "scalars": {"engine_rpm": available(400.0)},
                          "wheels": [{"index": j, "fields": {"omega": available(5.0)}} for j in range(4)]},
        })
    return rows, spawn


def settled_protocol(**kwargs):
    return protocol.make_protocol("throttle", version="carla-brake-protocol/3", **kwargs)


def test_v2_archived_hashes_and_default_are_unchanged_by_protocol3():
    selected = protocol.make_protocol("velocity_injection")
    assert selected["schema_version"] == "carla-brake-protocol/2"
    assert independent_hash(selected) == "ee0f2226bfff6c7cf8eaf2213e14f9b0df8b46418e8666098f2e315578759163"
    assert independent_hash(selected["policy"]) == "f6bda8e2106e6a54f257e4cdf4d029b8358c6ca85cb0fc648c08368c572e5769"
    assert independent_hash(protocol.measurement_sequence(selected)) == "7431dc72529092b2d2e33b902acabf0d14ada10a19e282c4143d010200830633"
    for version in ("carla-brake-protocol/1", "carla-brake-protocol/2"):
        previous = protocol.make_protocol("throttle", version=version)
        assert protocol.preparation_control(previous, 1.5) == expected_control(throttle=0.2)


@pytest.mark.parametrize("brake", [0.0, 0.025, 0.05, 0.1, 0.2, 0.35, 0.6, 1.0])
def test_v3_pulse_starts_at_zero_and_has_exact_five_intervals_then_seventy_five_release(brake):
    selected = settled_protocol(measurement="pulse", brake_level=brake)
    expected = [{"step": i, "time_s": i * 0.05,
                 "control": expected_control(brake if i < 5 else 0.0)} for i in range(80)]
    assert protocol.measurement_sequence(selected) == expected
    assert selected["policy"]["pulse_start_step"] == 0
    assert all(row["control"] == expected_control() for row in protocol.measurement_sequence(settled_protocol()))


def test_v3_refuses_injection_or_stateless_preparation_instead_of_falling_back():
    with pytest.raises(ValueError, match="physical throttle"):
        protocol.make_protocol("velocity_injection", version="carla-brake-protocol/3")
    with pytest.raises(ValueError, match="explicit PI state"):
        protocol.preparation_control(settled_protocol(), 2.0)
    rows, _ = settled_rows()
    for legacy in ("carla-brake-protocol/1", "carla-brake-protocol/2"):
        with pytest.raises(ValueError, match="protocol3"):
            protocol.settled_preparation_step(protocol.make_protocol("throttle", version=legacy), rows[0])


def test_admission_requires_twenty_intervals_not_twenty_snapshots():
    rows, spawn = settled_rows(20)
    missing = protocol.settled_preparation_gate(settled_protocol(), rows, spawn)
    assert missing["crossed_plane_now"] and not missing["criteria"]["complete_20_intervals"]
    assert not missing["admitted"] and missing["window_duration_s"] == pytest.approx(0.95)
    rows, spawn = settled_rows(21)
    complete = protocol.settled_preparation_gate(settled_protocol(), rows, spawn)
    assert complete["admitted"] and all(complete["criteria"].values())
    assert complete["window_frames"] == list(range(100, 121))
    assert complete["window_duration_s"] == 1.0


def test_actual_v2_descending_speed_window_does_not_establish_settled_admission():
    rows, spawn = settled_rows()
    # Values from actual throttle-1 preparation indices33..37, some rounded to 9 decimals.
    speeds = [2.0904068422864786, 2.055849974, 2.020424069, 1.980367121, 1.9488173072622246]
    accelerations = [-0.4871368830169368, -0.691137375, -0.708518091, -0.8011389514425207, -0.6309962830463107]
    assert all(1.9 <= speed <= 2.1 for speed in speeds)  # v2 speed-only streak passed.
    for row, speed, acceleration in zip(rows[-5:], speeds, accelerations):
        row["ego"]["velocity_mps"]["x"] = speed
        row["ego"]["acceleration_mps2"]["x"] = acceleration
    result = protocol.settled_preparation_gate(settled_protocol(), rows, spawn)
    assert not result["admitted"]
    assert not result["criteria"]["interval_acceleration"]
    assert not result["criteria"]["engine_heading_acceleration"]


@pytest.mark.parametrize("field,value,criterion", [
    ("speed", 2.051, "speed_window"), ("speed_jump", 2.01, "interval_acceleration"),
    ("acceleration", 0.100001, "engine_heading_acceleration"),
    ("vertical", 0.010001, "vertical_speed"), ("angular", 1.000001, "angular_speed"),
    ("gear", 0, "same_positive_cached_gear"), ("gear", 2, "same_positive_cached_gear"),
    ("throttle", 0.042001, "cached_throttle_span"),
])
def test_each_settling_criterion_blocks_admission_without_trusting_cached_labels(field, value, criterion):
    rows, spawn = settled_rows()
    row = rows[10]
    row.update(signed_heading_speed_mps=2.0, stable=True, admitted=True)
    if field == "speed":
        for item in rows:
            item["ego"]["velocity_mps"]["x"] = value
    elif field == "speed_jump":
        row["ego"]["velocity_mps"]["x"] = value
    elif field == "acceleration":
        row["ego"]["acceleration_mps2"]["x"] = value
    elif field == "vertical":
        row["ego"]["velocity_mps"]["z"] = value
    elif field == "angular":
        row["ego"]["angular_velocity_deg_s"]["z"] = value
    else:
        row["reported_control"]["data"][field] = value
    result = protocol.settled_preparation_gate(settled_protocol(), rows, spawn)
    assert not result["criteria"][criterion] and not result["admitted"]


def test_engine_acceleration_at_window_origin_is_not_a_twenty_first_interval():
    rows, spawn = settled_rows()
    rows[0]["ego"]["acceleration_mps2"]["x"] = 39.15893133
    assert protocol.settled_preparation_gate(settled_protocol(), rows, spawn)["admitted"]
    rows[1]["ego"]["acceleration_mps2"]["x"] = 39.15893133
    assert not protocol.settled_preparation_gate(settled_protocol(), rows, spawn)["admitted"]


@pytest.mark.parametrize("failure", ["missing", "unavailable", "frame", "rpm_nan", "rpm_bool", "rpm_zero",
                                     "wheel_nan", "wheel_zero", "wheel_denominator", "wheel_sign",
                                     "wheel_order", "wheel_count"])
def test_raw_rotating_state_unavailable_or_unusable_is_unassessable_not_stable(failure):
    rows, spawn = settled_rows()
    row = rows[10]
    telemetry = row["telemetry"]
    if failure == "missing":
        row.pop("telemetry")
    elif failure == "unavailable":
        telemetry["scalars"]["engine_rpm"] = {"status": "unavailable", "value": None, "reason": "not observed"}
    elif failure == "frame":
        telemetry["after_world_frame"] += 1
    elif failure.startswith("rpm_"):
        telemetry["scalars"]["engine_rpm"]["value"] = {"rpm_nan": math.nan, "rpm_bool": True, "rpm_zero": 0.0}[failure]
    elif failure == "wheel_count":
        telemetry["wheels"].pop()
    elif failure == "wheel_order":
        telemetry["wheels"][0]["index"] = 1
    elif failure == "wheel_denominator":
        for item in rows:
            item["telemetry"]["wheels"][0]["fields"]["omega"]["value"] = 1e-6
    else:
        telemetry["wheels"][0]["fields"]["omega"]["value"] = {
            "wheel_nan": math.nan, "wheel_zero": 0.0, "wheel_sign": -5.0}[failure]
    result = protocol.settled_preparation_gate(settled_protocol(), rows, spawn)
    assert not result["admitted"] and result["raw_state_assessment"] == "unassessable"
    assert result["raw_state_relative_spans"] is None and result["raw_state_error"]


def test_raw_relative_span_uses_absolute_median_per_field_and_preserves_signed_wheel_state():
    rows, spawn = settled_rows()
    for row in rows:
        row["telemetry"]["wheels"][0]["fields"]["omega"]["value"] = -5.0
    rows[-1]["telemetry"]["scalars"]["engine_rpm"]["value"] = 408.0
    result = protocol.settled_preparation_gate(settled_protocol(), rows, spawn)
    assert result["admitted"] and result["raw_state_relative_spans"] == [0.02, 0.0, 0.0, 0.0, 0.0]
    rows[-1]["telemetry"]["scalars"]["engine_rpm"]["value"] = 408.0001
    result = protocol.settled_preparation_gate(settled_protocol(), rows, spawn)
    assert result["raw_state_assessment"] == "available" and not result["criteria"]["raw_rotating_state"]
    assert not result["admitted"]


def test_first_crossing_is_projected_on_spawn_heading_not_current_heading_or_world_x():
    rows, spawn = settled_rows(yaw=90.0)
    result = protocol.settled_preparation_gate(settled_protocol(), rows, spawn)
    assert result["admitted"] and result["progress_m"] == pytest.approx(12.05)
    assert result["previous_progress_m"] == pytest.approx(11.95)
    assert rows[-1]["ego"]["position_m"]["x"] == pytest.approx(spawn["position_m"]["x"])
    for row in rows:
        row["ego"]["rotation_deg"]["yaw"] = 0.0
        row["ego"]["velocity_mps"].update(x=2.0, y=0.0)
    assert protocol.settled_preparation_gate(settled_protocol(), rows, spawn)["progress_m"] == pytest.approx(12.05)


def test_missed_first_crossing_cannot_be_retried_after_stability_improves():
    rows, spawn = settled_rows()
    rows[0]["ego"]["position_m"]["x"] = spawn["position_m"]["x"] + 12.01
    result = protocol.settled_preparation_gate(settled_protocol(), rows, spawn)
    assert result["observed_admission_stable"] and result["plane_reached"]
    assert result["first_crossing_index"] == 0 and not result["crossed_plane_now"] and not result["admitted"]
    rows, spawn = settled_rows(22)
    rows[-2]["ego"]["position_m"]["x"] = spawn["position_m"]["x"] + 12.01
    result = protocol.settled_preparation_gate(settled_protocol(), rows, spawn)
    assert result["first_crossing_index"] == 20 and not result["admitted"]


def test_plane_overshoot_is_bounded_without_inventing_a_target_position():
    rows, spawn = settled_rows()
    rows[-1]["ego"]["position_m"]["x"] = spawn["position_m"]["x"] + 12.110001
    original = deepcopy(rows)
    result = protocol.settled_preparation_gate(settled_protocol(), rows, spawn)
    assert result["observed_admission_stable"] and result["crossed_plane_now"] and not result["admitted"]
    assert result["overshoot_m"] > 0.11 and rows == original


@pytest.mark.parametrize("failure", ["source_time", "frame", "control_frame", "control_missing", "control_bool", "gear_bool"])
def test_settling_source_discontinuity_or_missing_control_fails_closed(failure):
    rows, spawn = settled_rows()
    row = rows[10]
    if failure == "source_time":
        row["source_time_s"] += 0.002
    elif failure == "frame":
        row["world_frame"] += 1
    elif failure == "control_frame":
        row["reported_control"]["after_world_frame"] += 1
    elif failure == "control_missing":
        row.pop("reported_control")
    else:
        row["reported_control"]["data"]["gear" if failure == "gear_bool" else "throttle"] = True
    with pytest.raises(ValueError):
        protocol.settled_preparation_gate(settled_protocol(), rows, spawn)


def test_pi_initial_command_uses_slew_horizon_without_integrating_fictitious_elapsed_time():
    rows, _ = settled_rows()
    row = rows[0]
    row["ego"]["velocity_mps"]["x"] = 0.0
    row["reported_control"]["data"].update(gear=0, throttle=0.0)
    output = protocol.settled_preparation_step(settled_protocol(), row)
    assert output["control"] == expected_control(throttle=pytest.approx(0.005))
    assert output["state"]["integral"] == 0.0
    assert output["trace"]["observed_interval_s"] is None and output["trace"]["slew_interval_s"] == 0.05
    assert output["trace"]["integrator_enabled"] is False


def test_pi_gear_zero_freezes_integral_across_consecutive_sources_despite_large_speed_error():
    rows, _ = settled_rows(40)
    state = None
    for row in rows:
        row["ego"]["velocity_mps"]["x"] = 0.0
        row["reported_control"]["data"]["gear"] = 0
        result = protocol.settled_preparation_step(settled_protocol(), row, state)
        assert result["state"]["integral"] == 0.0 and not result["trace"]["integrator_enabled"]
        state = result["state"]
    assert result["control"]["throttle"] == pytest.approx(0.16)


def test_pi_integrates_actual_source_delta_and_returns_independent_trace_state():
    rows, _ = settled_rows()
    row = rows[1]
    row["source_time_s"] = 0.0500004
    row["ego"]["velocity_mps"]["x"] = 1.99
    state = {"step": 1, "integral": 0.04, "previous_throttle": 0.04,
             "previous_source_time_s": 0.0, "previous_world_frame": 100}
    original = deepcopy(state)
    result = protocol.settled_preparation_step(settled_protocol(), row, state)
    expected_integral = 0.04 + 0.05 * (2.0 - 1.99) * 0.0500004
    assert result["state"]["integral"] == pytest.approx(expected_integral, abs=1e-15)
    assert result["control"]["throttle"] == pytest.approx(0.08 * (2.0 - 1.99) + expected_integral, abs=1e-15)
    assert result["trace"]["observed_interval_s"] == 0.0500004
    assert not result["trace"]["saturated_in_error_direction"] and state == original
    result["trace"]["state_before"]["integral"] = 0.1
    assert state == original


@pytest.mark.parametrize("old_u,old_i,speed,expected_u", [
    (0.35, 0.34, 1.0, 0.35), (0.0, 0.02, 3.0, 0.0),
    (0.05, 0.04, 0.0, 0.055), (0.05, 0.04, 3.0, 0.045),
])
def test_pi_antiwindup_accounts_for_both_amplitude_and_slew_saturation(old_u, old_i, speed, expected_u):
    rows, _ = settled_rows()
    rows[1]["ego"]["velocity_mps"]["x"] = speed
    state = {"step": 1, "integral": old_i, "previous_throttle": old_u,
             "previous_source_time_s": 0.0, "previous_world_frame": 100}
    result = protocol.settled_preparation_step(settled_protocol(), rows[1], state)
    assert result["trace"]["integrator_enabled"] and result["trace"]["saturated_in_error_direction"]
    assert result["state"]["integral"] == old_i and result["control"]["throttle"] == pytest.approx(expected_u)


@pytest.mark.parametrize("field,value", [("integral", 0.01), ("previous_throttle", 0.01),
                                        ("previous_source_time_s", 0.0), ("previous_world_frame", 100)])
def test_pi_nonzero_initial_state_cannot_start_a_fresh_trial(field, value):
    rows, _ = settled_rows()
    state = {"step": 0, "integral": 0.0, "previous_throttle": 0.0,
             "previous_source_time_s": None, "previous_world_frame": None}
    state[field] = value
    with pytest.raises(ValueError, match="Nonzero initial"):
        protocol.settled_preparation_step(settled_protocol(), rows[0], state)


@pytest.mark.parametrize("field,value", [("step", True), ("step", 300), ("integral", math.nan),
                                        ("integral", False), ("previous_throttle", 0.350001),
                                        ("previous_world_frame", 99), ("previous_source_time_s", -0.002)])
def test_pi_state_type_bounds_and_source_continuity_cannot_be_bypassed(field, value):
    rows, _ = settled_rows()
    state = {"step": 1, "integral": 0.04, "previous_throttle": 0.04,
             "previous_source_time_s": 0.0, "previous_world_frame": 100}
    state[field] = value
    with pytest.raises(ValueError):
        protocol.settled_preparation_step(settled_protocol(), rows[1], state)
