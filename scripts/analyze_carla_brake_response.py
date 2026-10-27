#!/usr/bin/env python3
"""Verify and compare bounded CARLA brake diagnostics without engine access.

Only the standard library and the versioned, simulator-free protocol are used.
Input files are read only. The output directory must not exist. Integrity is
independent of diagnostic success, cleanup, stopping and driving quality.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import sys

from carla_brake_protocol import (DT, STEPS, REPORT_VERSION, POLICY, SETTLED_VERSION, canonical,
    measurement_sequence, preparation_control, settled_preparation_step, settled_preparation_gate,
    signed_heading, spatial_check, validate_intent)

VERSION = "carla-brake-offline-analysis/1"
MAX_FILE = 32 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_TRIALS = 32
MAX_EVENTS = 12_000
COMMAND_STATES = {"not_attempted", "send_outcome_unknown", "send_rejected",
                  "sent_acknowledged", "observed_next_frame"}
TIME_TOLERANCE = 1e-6
CONTROL_FLOATS = ("throttle", "brake", "steer")
CONTROL_BOOLS = ("hand_brake", "reverse", "manual_gear_shift")
LIMITATIONS = [
    "Offline consistency and content hashes do not authenticate an engine, prove physical calibration, or approve driving quality.",
    "Every retained raw observation is kept. Signed speed/acceleration use the current horizontal heading; interval dv/dt and engine acceleration are different observations.",
    "Reported control is cached simulator state. Float32 agreement is not measured actuator force, torque, brake pressure or tire-road friction.",
    "Optional RPC and telemetry fields remain unavailable/null/nonfinite as recorded; unknown values are never zero.",
    "Stopped requires five consecutive retained samples with absolute signed heading speed <=0.03 m/s. This is a diagnostic observation, not a safety verdict.",
    "A missing stop before a complete window is right-censored. A failed prefix is interrupted-censored, not a completed four-second trial.",
    "The coast/rest control recipe is an explicit diagnostic condition. Prior production response bytes choose configuration; this is not exact replication of a production controller.",
    "No smoothing, outlier removal, quality threshold relaxation, model-gain calibration or ground-truth replacement is performed.",
]


class InvalidEvidence(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InvalidEvidence(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON key: " + key)
        result[key] = value
    return result


def strict_json(data):
    try:
        return json.loads(data, object_pairs_hook=_object,
            parse_constant=lambda value: (_ for _ in ()).throw(InvalidEvidence("Nonfinite JSON: " + value)))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise InvalidEvidence("Invalid JSON: " + str(error)[:200]) from error


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def integer(value, low=0, high=2**64 - 1):
    return type(value) is int and low <= value <= high


def digest(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def f32(value):
    require(finite(value), "Nonfinite control request")
    try:
        return struct.unpack("<f", struct.pack("<f", value))[0]
    except (OverflowError, struct.error) as error:
        raise InvalidEvidence("Control request is outside float32") from error


def read_bounded(path, *, limit=MAX_FILE):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), "Missing or symlink source file: " + str(path))
    before = path.stat()
    require(before.st_size <= limit, "File exceeds bounded size: " + str(path))
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    after = path.stat()
    require(len(data) <= limit and len(data) == before.st_size == after.st_size,
            "Source file size changed while reading: " + str(path))
    require((before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns),
            "Source file changed while reading: " + str(path))
    return data


def file_info(data, path):
    return {"path": str(path), "sha256": sha(data), "size_bytes": len(data)}


def verify_chain(data, report, intent):
    require(data.endswith(b"\n"), "Missing or truncated event chain")
    events, previous = [], "0" * 64
    for line in data.splitlines():
        require(len(events) < MAX_EVENTS and len(line) <= 2 * 1024 * 1024, "Event count/line bound exceeded")
        row = strict_json(line)
        require(type(row) is dict and set(row) == {"sequence", "previous_sha256", "at", "kind", "data", "sha256"},
                "Event envelope differs")
        require(integer(row["sequence"]) and row["sequence"] == len(events), "Event sequence differs")
        require(row["previous_sha256"] == previous and digest(row["sha256"]), "Event previous hash differs")
        require(type(row["at"]) is str and type(row["kind"]) is str and type(row["data"]) is dict,
                "Invalid event types")
        require(sha(canonical({key: value for key, value in row.items() if key != "sha256"})) == row["sha256"],
                "Event content hash differs")
        events.append(row)
        previous = row["sha256"]
    chain = report.get("event_chain")
    require(type(chain) is dict and set(chain) == {"entries", "head_sha256", "sealed"}, "Report event-chain fields differ")
    require(integer(chain["entries"]) and chain["entries"] == len(events) and chain["head_sha256"] == previous,
            "Report event head/count differs")
    require(chain["sealed"] is True, "Unsealed event prefix: terminal integrity is unavailable")
    require(events and events[-1]["kind"] == "diagnostic_final", "Missing terminal diagnostic event")
    expected = {"status": report["status"], "error": report.get("error"),
                "cleanup_status": report["cleanup"]["status"],
                "reservation_retained": report["reservation_retained"],
                "report_projection_sha256": sha(canonical({key: value for key, value in report.items()
                                                           if key not in ("event_chain", "finished_at")}))}
    require(events[-1]["data"] == expected, "Terminal report projection/status differs from journal")
    starts = [row for row in events if row["kind"] == "diagnostic_started"]
    require(len(starts) == 1, "Missing/duplicate diagnostic start")
    start_expected = {key: report[key] for key in ("experiment_id", "intent_sha256", "protocol_sha256",
                                                  "sequence_sha256", "source_files")}
    require(starts[0]["data"] == start_expected, "Journal start identity differs")
    require(report["experiment_id"] == intent["experiment_id"], "Experiment identity differs")
    return events


def verify_vector(value, keys=("x", "y", "z")):
    require(type(value) is dict and set(value) == set(keys) and all(finite(value[key]) for key in keys),
            "Invalid/nonfinite raw vector")
    return value


def validate_control(value):
    require(type(value) is dict and set(value) == set(CONTROL_FLOATS + CONTROL_BOOLS + ("gear",)),
            "Control fields differ")
    require(all(finite(value[k]) for k in CONTROL_FLOATS) and
            0 <= value["throttle"] <= 1 and 0 <= value["brake"] <= 1 and -1 <= value["steer"] <= 1,
            "Invalid control values")
    require(all(type(value[k]) is bool for k in CONTROL_BOOLS) and integer(value["gear"], -1, 20),
            "Invalid control flags/gear")


def optional_rpc(value, frame, *, control=False):
    """Return availability without inventing values for absent optional RPCs."""
    if value is None:
        return "unavailable"
    require(type(value) is dict and value.get("status") in ("available", "unavailable"), "Invalid optional RPC envelope")
    for key in ("before_world_frame", "after_world_frame"):
        require(value.get(key) is None or integer(value[key]), "Invalid optional RPC frame")
    if value["status"] == "available":
        require(value.get("before_world_frame") == value.get("after_world_frame") == frame,
                "Available optional RPC crosses its source frame")
        require(value.get("error") is None, "Available RPC has an error")
        if control:
            validate_control(value.get("data"))
    elif control:
        require(value.get("data") is None, "Unavailable cached-control RPC must not expose valid data")
    return value["status"]


def observation_rows(rows, phase, actor_id=None):
    require(type(rows) is list and len(rows) <= {"measurement": 81, "preparation": 302, "rest": 101, "inventory_refresh": 1}[phase],
            "Observation count exceeds the pinned phase bound")
    previous = None
    for index, row in enumerate(rows):
        require(type(row) is dict and row.get("index") == index and type(row.get("index")) is int and row.get("phase") == phase,
                "Observation index/phase differs")
        require(integer(row.get("world_frame")) and finite(row.get("source_time_s")) and finite(row.get("delta_seconds")),
                "Invalid source frame/time")
        require(abs(row["delta_seconds"] - DT) <= TIME_TOLERANCE, "Source delta differs from 20 Hz protocol")
        if previous:
            require(row["world_frame"] == previous["world_frame"] + 1, "Observation source frame gap")
            require(abs(row["source_time_s"] - previous["source_time_s"] - DT) <= TIME_TOLERANCE,
                    "Observation source time gap")
        ego = row.get("ego")
        require(type(ego) is dict and integer(ego.get("actor_id"), 1, 2**32 - 1), "Missing ego actor identity")
        if actor_id is None:
            actor_id = ego["actor_id"]
        require(ego["actor_id"] == actor_id and ego.get("type_id") == POLICY["vehicle_blueprint"] and
                type(ego.get("role_name")) is str and bool(ego["role_name"]), "Ego actor identity changed")
        for key in ("position_m", "forward_vector", "velocity_mps", "acceleration_mps2", "angular_velocity_deg_s"):
            verify_vector(ego.get(key))
        verify_vector(ego.get("rotation_deg"), ("pitch", "yaw", "roll"))
        transform = ego.get("snapshot_transform", {})
        require(transform.get("position_m") == ego["position_m"] and transform.get("rotation_deg") == ego["rotation_deg"],
                "Raw actor transform/position/rotation differ")
        projected = signed_heading(ego["velocity_mps"], ego["rotation_deg"]["yaw"])
        require(finite(row.get("signed_heading_speed_mps")) and abs(row["signed_heading_speed_mps"] - projected) <= 1e-9,
                "Stored heading speed differs from raw vector")
        optional_rpc(row.get("reported_control"), row["world_frame"], control=True)
        optional_rpc(row.get("telemetry"), row["world_frame"])
        previous = row
    return actor_id


def verify_commands(commands, observations, protocol, actor_id, *, complete=True):
    require(type(commands) is list and len(commands) == STEPS, "The full 80-command denominator is missing")
    sequence = measurement_sequence(protocol)
    attempted_ended = False
    for index, (command, expected) in enumerate(zip(commands, sequence)):
        require(type(command) is dict, "Invalid command row")
        require(canonical({k: command.get(k) for k in ("step", "time_s", "control")}) == canonical(expected),
                "Command differs from the immutable planned sequence")
        state = command.get("application_status")
        require(state in COMMAND_STATES, "Unknown command application status")
        source = observations[index] if index < len(observations) else None
        following = observations[index + 1] if index + 1 < len(observations) else None
        if state == "not_attempted":
            attempted_ended = True
            require(all(command.get(k) is None for k in ("world_frame", "next_world_frame", "next_observation_index",
                "rpc_before_world_frame", "rpc_after_world_frame", "rpc_response")), "Unattempted command carries execution evidence")
            require(following is None, "Unattempted command has a following observation")
            continue
        require(not attempted_ended and source is not None, "Command attempted beyond a retained source prefix")
        require(command.get("world_frame") == source["world_frame"], "Command source frame differs")
        for key in ("rpc_before_world_frame", "rpc_after_world_frame"):
            require(command.get(key) is None or integer(command[key]), "Invalid command RPC frame")
        if state in ("sent_acknowledged", "observed_next_frame"):
            if complete or state == "observed_next_frame":
                require(command.get("rpc_before_world_frame") == command.get("rpc_after_world_frame") == source["world_frame"],
                        "Acknowledged control RPC crosses the source frame")
            require(command.get("rpc_response") == {"actor_id": actor_id, "error": None}, "Control ACK actor identity/error differs")
        if state == "observed_next_frame":
            require(following is not None and command.get("next_world_frame") == following["world_frame"] and
                    command.get("next_observation_index") == index + 1, "ACK/next-observation relationship differs")
        else:
            attempted_ended = True
            require(command.get("next_world_frame") is None and command.get("next_observation_index") is None,
                    "Unobserved command claims a following frame")
            # A raw snapshot may be retained just before a failing optional RPC;
            # the final command transition may not have happened. Keep that row.
            require(state == "sent_acknowledged" or following is None,
                    "Rejected/unknown command unexpectedly has a following observation")
    return dict(Counter(command["application_status"] for command in commands))


def verify_transition(command, before, after, actor_id):
    require(command.get("application_status") == "observed_next_frame" and
            command.get("world_frame") == command.get("rpc_before_world_frame") == command.get("rpc_after_world_frame") == before["world_frame"] and
            command.get("next_world_frame") == after["world_frame"] == before["world_frame"] + 1 and
            command.get("next_observation_index") == after["index"] and
            command.get("rpc_response") == {"actor_id": actor_id, "error": None},
            "Preparation command ACK/frame transition differs")
    require(control_comparison(command["control"], after.get("reported_control"))["status"] == "matched",
            "Collected preparation cached control is unavailable/mismatched")


def verify_completed_preparation(report, intent, actor_id):
    """Recompute the actual rest/preparation protocol, not the success labels."""
    rest, prep = report["rest"], report["preparation"]
    rows, trace = rest.get("observations", []), rest.get("trace", [])
    require(rest.get("status") == "passed" and 8 <= len(rows) == len(trace) <= 100, "Collected rest denominator/status differs")
    policy = {"version": "carla-startup-stability/1", "min_ticks": 8, "max_ticks": 100,
        "consecutive_stable_samples": 5, "max_linear_speed_mps": .03, "max_vertical_speed_mps": .05,
        "max_angular_speed_deg_s": 1., "max_position_step_m": .005, "max_rotation_step_deg": .05,
        "max_acceleration_mps2": .1, "max_wall_time_s": 60., "max_tick_rpc_time_s": 10., "clock_tolerance_s": 1e-6}
    require(canonical(rest.get("policy")) == canonical(policy), "Rest stability policy differs")
    streak = 0
    previous = None
    for index, (sample, row) in enumerate(zip(trace, rows)):
        require(sample.get("tick") == index + 1 and type(sample.get("tick")) is int and
                sample.get("world_frame") == row["world_frame"] and sample.get("source_time_s") == row["source_time_s"],
                "Rest trace/sample frame differs")
        actor, ego = sample.get("actor", {}), row["ego"]
        require(actor.get("actor_id") == actor_id and actor.get("invalid_fields") == [], "Rest actor validity differs")
        for key in ("position_m", "rotation_deg", "velocity_mps", "angular_velocity_deg_s", "acceleration_mps2"):
            require(actor.get(key) == ego[key], "Rest actor raw vectors differ")
        displacement = math.dist(tuple(ego["position_m"].values()), tuple(previous["position_m"].values())) if previous else None
        rotation_step = max(abs((ego["rotation_deg"][key] - previous["rotation_deg"][key] + 180) % 360 - 180)
                            for key in ("pitch", "yaw", "roll")) if previous else None
        derived = {"linear_speed_mps": math.hypot(*ego["velocity_mps"].values()),
            "absolute_vertical_speed_mps": abs(ego["velocity_mps"]["z"]),
            "angular_speed_deg_s": math.hypot(*ego["angular_velocity_deg_s"].values()),
            "acceleration_norm_mps2": math.hypot(*ego["acceleration_mps2"].values()),
            "position_step_m": displacement, "rotation_step_deg": rotation_step}
        criteria = {"linear_speed": derived["linear_speed_mps"] <= .03, "vertical_speed": derived["absolute_vertical_speed_mps"] <= .05,
            "angular_speed": derived["angular_speed_deg_s"] <= 1., "acceleration": derived["acceleration_norm_mps2"] <= .1,
            "position_step": displacement is not None and displacement <= .005,
            "rotation_step": rotation_step is not None and rotation_step <= .05}
        stable = all(criteria.values())
        require(actor.get("derived") == derived and actor.get("criteria") == criteria and actor.get("stable") is stable,
                "Rest stability does not follow actual raw observations")
        streak = streak + 1 if stable else 0
        require(sample.get("stable_streak") == streak, "Rest stable streak differs")
        require(control_comparison(POLICY["rest_control"], row.get("reported_control"))["status"] == "matched",
                "Collected rest cached control is unavailable/mismatched")
        previous = ego
    require(streak >= 5 and rest.get("stable_streak") == streak, "Collected rest lacks five stable samples")
    initial = rest.get("initial_control", {})
    require(canonical(initial.get("control")) == canonical(POLICY["rest_control"]) and
            initial.get("step") is None and initial.get("time_s") is None, "Initial rest control differs")
    refresh = report.get("inventory_refresh_observations", [])
    verify_transition(initial, {"world_frame": initial.get("world_frame")}, refresh[0] if refresh else rows[0], actor_id)
    p = prep.get("observations", [])
    commands = prep.get("commands", [])
    require(prep.get("mode") == intent["protocol"]["initialization"] and prep.get("status") == "passed",
            "Preparation mode/status differs from selected protocol")
    require(p and len(p) == len(commands) + 1, "Preparation observations/commands denominator differs")
    require(all(p[0][key] == rows[-1][key] for key in ("world_frame", "source_time_s", "delta_seconds", "ego")),
            "Rest/preparation snapshot boundary differs")
    if prep["mode"] == "velocity_injection":
        require(len(commands) == 1 and len(p) == 2, "Injection requires exactly one command and two preparation observations")
        require(commands[0].get("step") is None and commands[0].get("time_s") is None and
                canonical(commands[0].get("control")) == canonical(POLICY["rest_control"]), "Injection preparation control differs")
        request = prep.get("velocity_request")
        expected = {"actor_id": actor_id, "after_world_frame": p[0]["world_frame"], "requested_speed_mps": 2.,
            "velocity_mps": {key: p[0]["ego"]["forward_vector"][key] * 2. for key in ("x", "y", "z")},
            "operation": "actor.set_target_velocity", "ack_status": "unknown", "returned_without_ack": True}
        require(request == expected, "Injection request/vector/unknown-ACK semantics differ")
        require(1.9 <= p[-1]["signed_heading_speed_mps"] <= 2.1, "Injection did not enter the actual speed window")
    elif intent["protocol"]["schema_version"] == SETTLED_VERSION:
        require(prep.get("velocity_request") is None and 20 <= len(commands) <= 300,
                "Settled preparation has injection or invalid count")
    else:
        require(prep.get("velocity_request") is None and 5 <= len(commands) <= 300, "Throttle preparation has injection or invalid count")
        streak = 0
        for index, command in enumerate(commands):
            expected = preparation_control(intent["protocol"], p[index]["signed_heading_speed_mps"])
            require(command.get("step") == index and type(command.get("step")) is int and command.get("time_s") == index * DT and
                    canonical(command.get("control")) == canonical(expected), "Throttle preparation formula/sequence differs")
            streak = streak + 1 if 1.9 <= p[index + 1]["signed_heading_speed_mps"] <= 2.1 else 0
            if index < len(commands) - 1:
                require(streak < 5, "Throttle preparation continued after its declared stopping gate")
        require(streak >= 5 and prep.get("stable_streak") == streak, "Throttle preparation lacks five final speed-window samples")
    for index, command in enumerate(commands):
        verify_transition(command, p[index], p[index + 1], actor_id)
    require(1.9 <= report["measurement"]["observations"][0]["signed_heading_speed_mps"] <= 2.1,
            "Measurement did not start in the actual speed window")


def verify_settled_preparation(report, intent, actor_id):
    """Replay PI proposals and successful state commits without executing source code.

    The final failed proposal/observation can exist without a committed state or
    gate. It remains a failed prefix, never a successful entry.
    """
    if intent["protocol"]["schema_version"] != SETTLED_VERSION:
        return None
    prep = report["preparation"]
    require(all(key in prep for key in ("pi_steps", "pi_state", "entry_trace", "entry_gate")),
            "Settled preparation explicit state/gate fields missing")
    observations, commands = prep.get("observations", []), prep.get("commands", [])
    traces, entries = prep.get("pi_steps", []), prep.get("entry_trace", [])
    require(prep.get("mode") == "throttle" and prep.get("velocity_request") is None,
            "Settled preparation must use physical throttle without injection")
    require(type(traces) is list and type(entries) is list and len(traces) == len(commands) <= 300,
            "Settled PI proposal/command denominator differs")
    require(len(commands) <= len(observations) <= len(commands) + 1,
            "Settled preparation observation prefix differs")
    state, applied, ended = None, 0, False
    for i, command in enumerate(commands):
        require(not ended, "Settled preparation continued after an unresolved command")
        source = observations[i]
        expected = settled_preparation_step(intent["protocol"], source, state)
        require(canonical(traces[i]) == canonical(expected["trace"]), "Settled PI state/trace differs from raw source")
        require(command.get("step") == i and type(command.get("step")) is int and command.get("time_s") == i * DT and
                canonical(command.get("control")) == canonical(expected["control"]), "Settled PI command differs")
        status = command.get("application_status")
        require(status in COMMAND_STATES, "Unknown settled preparation command state")
        if status != "not_attempted":
            require(command.get("world_frame") == source["world_frame"], "Settled command source differs")
        if status in ("sent_acknowledged", "observed_next_frame"):
            require(command.get("rpc_response") == {"actor_id": actor_id, "error": None}, "Settled ACK actor differs")
        if status == "observed_next_frame":
            require(i + 1 < len(observations), "Settled command lacks its next observation")
            verify_transition(command, source, observations[i + 1], actor_id)
            state, applied = expected["state"], applied + 1
        else:
            ended = True
            require(status == "sent_acknowledged" or len(observations) == i + 1,
                    "Unresolved settled command unexpectedly has a following observation")
            require(command.get("next_world_frame") is None and command.get("next_observation_index") is None,
                    "Unresolved settled command claims its next frame")
    require(canonical(prep.get("pi_state")) == canonical(state), "Settled PI committed state differs")
    require(max(0, applied) <= len(entries) <= (applied + 1 if observations else 0),
            "Settled entry-gate prefix denominator differs")
    crossing = None
    for i, entry in enumerate(entries):
        expected = settled_preparation_gate(intent["protocol"], observations[:i + 1], intent["spawn_transform"])
        require(canonical(entry) == canonical(expected), "Settled entry gate differs from original raw prefix")
        if expected["plane_reached"]:
            require(crossing is None and i == len(entries) - 1 and len(commands) <= i,
                    "Settled preparation continued after the first entry-plane crossing")
            crossing = i
    require(canonical(prep.get("entry_gate")) == canonical(entries[-1] if entries else None),
            "Stored final settled gate differs")
    if prep.get("status") == "passed" or report["measurement"]["observations"] or report["status"] == "collected":
        require(len(entries) == len(observations) == applied + 1 and entries[-1]["admitted"] is True and
                entries[-1]["crossed_plane_now"] is True, "Settled preparation was not admitted on its first plane crossing")
    measurement = report["measurement"]
    if measurement["observations"]:
        initial = measurement["observations"][0]
        sent = any(c["application_status"] != "not_attempted" for c in measurement["commands"])
        if measurement.get("entry_gate") is not None:
            expected = settled_preparation_gate(intent["protocol"], [*observations[:-1], initial], intent["spawn_transform"])
            require(canonical(measurement["entry_gate"]) == canonical(expected) and
                    measurement.get("entry_observation_frame") == initial["world_frame"] == observations[-1]["world_frame"],
                    "Settled fresh measurement entry gate/frame differs")
            require(control_comparison(commands[-1]["control"], initial.get("reported_control"))["status"] == "matched",
                    "Settled F0 differs from the preceding preparation control")
            require(expected["admitted"] or (report["status"] == "failed" and not sent),
                    "Settled measurement actuated after rejected fresh entry gate")
        else:
            require(report["status"] == "failed" and not sent, "Settled measurement entry evidence missing before actuation")
    return {"status": prep.get("status"), "pi_proposals": len(traces), "committed_pi_steps": applied,
            "pi_state": deepcopy(state), "entry_trace": deepcopy(entries),
            "entry_gate": deepcopy(prep.get("entry_gate")), "first_crossing_index": crossing,
            "measurement_entry_gate": deepcopy(measurement.get("entry_gate")),
            "interpretation": "Observed recipe gate, not steady state, calibrated force, or hidden-state equality."}


def control_comparison(requested, envelope):
    if not envelope or envelope.get("status") != "available":
        return {"status": "unavailable", "fields": {}, "requested_gear": requested["gear"], "reported_gear": None}
    actual = envelope["data"]
    fields = {key: {"requested": requested[key], "requested_float32": f32(requested[key]),
                    "reported": actual[key], "matches": actual[key] == f32(requested[key])} for key in CONTROL_FLOATS}
    fields.update({key: {"requested": requested[key], "reported": actual[key],
                        "matches": actual[key] == requested[key]} for key in CONTROL_BOOLS})
    # Automatic gear selection is an observed result, not a float/force match.
    return {"status": "matched" if all(field["matches"] for field in fields.values()) else "mismatch",
            "fields": fields, "requested_gear": requested["gear"], "reported_gear": actual["gear"],
            "gear_policy": "automatic_request_and_report_separate"}


def summarize_stop(samples, complete, start_index=0):
    candidates = [sample for sample in samples if sample["index"] >= start_index]
    streak = 0
    for sample in candidates:
        streak = streak + 1 if abs(sample["signed_speed_mps"]) <= POLICY["stop_absolute_speed_mps"] else 0
        if streak >= POLICY["stop_consecutive_samples"]:
            onset = candidates[candidates.index(sample) - streak + 1]
            return {"status": "stopped_observed", "first_low_speed_index": onset["index"],
                    "confirmed_index": sample["index"], "onset_time_s": onset["time_s"],
                    "confirmation_time_s": sample["time_s"], "consecutive_samples": streak,
                    "threshold_absolute_speed_mps": POLICY["stop_absolute_speed_mps"]}
    return {"status": "not_started" if not candidates else "right_censored" if complete else "interrupted_censored",
            "last_observed_index": candidates[-1]["index"] if candidates else None,
            "last_observed_time_s": candidates[-1]["time_s"] if candidates else None,
            "consecutive_low_speed_samples_at_end": streak,
            "threshold_absolute_speed_mps": POLICY["stop_absolute_speed_mps"]}


def optional_wheel_omega(row, index):
    telemetry = row.get("telemetry") or {}
    if (telemetry.get("status") != "available" or telemetry.get("before_world_frame") != row["world_frame"] or
            telemetry.get("after_world_frame") != row["world_frame"]):
        return None
    wheels = [wheel for wheel in telemetry.get("wheels", []) if wheel.get("index") == index]
    if len(wheels) != 1:
        return None
    field = wheels[0].get("fields", {}).get("omega") or {}
    return field["value"] if field.get("status") == "available" and finite(field.get("value")) and field.get("reason") is None else None


def derive_samples(rows, origin_time, commands=None, *, spawn_height=None):
    result = []
    for i, row in enumerate(rows):
        ego = row["ego"]
        yaw = ego["rotation_deg"]["yaw"]
        speed = signed_heading(ego["velocity_mps"], yaw)
        interval = None if not i else (speed - result[-1]["signed_speed_mps"]) / (row["source_time_s"] - rows[i - 1]["source_time_s"])
        incoming = commands[i - 1] if commands is not None and i > 0 else None
        outgoing = commands[i] if commands is not None and i < len(commands) else None
        result.append({"index": row["index"], "phase": row["phase"], "world_frame": row["world_frame"],
            "source_time_s": row["source_time_s"], "time_s": row["source_time_s"] - origin_time,
            "signed_speed_mps": speed, "vertical_speed_mps": ego["velocity_mps"]["z"],
            "world_height_m": ego["position_m"]["z"],
            "height_from_requested_spawn_m": ego["position_m"]["z"] - spawn_height if spawn_height is not None else None,
            "linear_speed_mps": math.hypot(*(ego["velocity_mps"][k] for k in ("x", "y", "z"))),
            "engine_heading_acceleration_mps2": signed_heading(ego["acceleration_mps2"], yaw),
            "engine_vertical_acceleration_mps2": ego["acceleration_mps2"]["z"],
            "interval_dv_dt_mps2": interval,
            "interval_source_frames": [rows[i - 1]["world_frame"], row["world_frame"]] if i else None,
            "outgoing_requested_throttle": outgoing["control"]["throttle"] if outgoing else None,
            "outgoing_attempted_throttle": outgoing["control"]["throttle"] if outgoing and outgoing["application_status"] != "not_attempted" else None,
            "reported_throttle": row["reported_control"]["data"]["throttle"] if (row.get("reported_control") or {}).get("status") == "available" else None,
            **{f"raw_wheel_{wheel}_omega": optional_wheel_omega(row, wheel) for wheel in range(4)},
            "outgoing_requested_brake": outgoing["control"]["brake"] if outgoing else None,
            "outgoing_attempted_brake": outgoing["control"]["brake"] if outgoing and outgoing["application_status"] != "not_attempted" else None,
            "outgoing_application_status": outgoing["application_status"] if outgoing else None,
            "incoming_command_step": incoming["step"] if incoming else None,
            "cached_control_match": control_comparison(incoming["control"], row.get("reported_control")) if incoming else None,
            "reported_brake": row["reported_control"]["data"]["brake"] if (row.get("reported_control") or {}).get("status") == "available" else None,
            "telemetry": deepcopy(row.get("telemetry")), "reported_control": deepcopy(row.get("reported_control")),
            "bounds": deepcopy(row.get("bounds"))})
    return result


def analyze_report(report, intent):
    require(report.get("schema_version") == REPORT_VERSION, "Unsupported diagnostic report version")
    require(report.get("status") in ("collected", "failed"), "Nonterminal diagnostic status")
    require(type(report.get("reservation_retained")) is bool, "Missing reservation state")
    require(type(report.get("cleanup")) is dict and report["cleanup"].get("status") in ("confirmed", "uncertain", "not_started"),
            "Invalid cleanup status")
    if report["status"] == "collected":
        require(report.get("error") is None and report["cleanup"]["status"] == "confirmed", "Collected report has failure/unknown cleanup")
    for key in ("protocol_sha256", "sequence_sha256", "source_files"):
        require(report.get(key) == intent[key], "Report/intent " + key + " differs")
    require(report.get("experiment_id") == intent["experiment_id"] and report.get("store_id") == intent["expected_store_id"],
            "Report experiment/store identity differs")
    for key in ("engine_build", "adapter_version", "source_evidence", "requested_weather"):
        require(report.get(key) == intent[key], "Report/intent " + key + " differs")
    require(report.get("diagnostic_id") == "diagnostic_brake_" + intent["experiment_id"].replace("-", ""),
            "Diagnostic identity differs")
    require(report.get("coordinate_convention") == "carla_left_handed_world" and
            report.get("angular_velocity_basis") == "official_carla_0.9.16_degrees_per_second_installed_build_separate",
            "Native coordinate/angular-velocity declaration differs")
    measurement = report.get("measurement")
    require(type(measurement) is dict and measurement.get("expected_commands") == STEPS and
            measurement.get("expected_observations") == STEPS + 1, "Pinned measurement denominator differs")
    observations = measurement.get("observations")
    spawn = report.get("spawn", {}).get("ego", {}).get("returned_identity")
    actor_id = spawn.get("actor_id") if type(spawn) is dict else None
    if observations:
        require(integer(actor_id, 1, 2**32 - 1), "Measured ego lacks a returned spawn identity")
    actor_id = observation_rows(observations, "measurement", actor_id)
    if spawn:
        for row in observations:
            require({key: row["ego"][key] for key in ("actor_id", "type_id", "role_name")} == spawn,
                    "Observation ego identity differs from returned spawn")
    complete = report["status"] == "collected"
    counts = verify_commands(measurement.get("commands"), observations, intent["protocol"], actor_id, complete=complete)
    window_retained = len(observations) == 81
    all_intervals_observed = window_retained and counts == {"observed_next_frame": 80}
    if complete:
        require(len(observations) == 81 and counts == {"observed_next_frame": 80}, "Collected report lacks all 80 commands/81 observations")
        require(report.get("preparation", {}).get("status") == "passed", "Collected report lacks successful preparation")
        require(report.get("original") == intent["expected_world"], "Collected original world differs from intent")
        require(not report.get("collision_callbacks"), "Collected report contains a contact callback")
    preparation = report.get("preparation", {})
    prepared_rows = preparation.get("observations", [])
    rest = report.get("rest", {})
    retained = observations or prepared_rows or rest.get("observations", [])
    origin = observations[0]["source_time_s"] if observations else retained[-1]["source_time_s"] if retained else 0.
    spawn_height = intent["spawn_transform"]["position_m"]["z"]
    samples = derive_samples(observations, origin, measurement["commands"], spawn_height=spawn_height)
    observation_rows(prepared_rows, "preparation", actor_id)
    pre_samples = derive_samples(prepared_rows, origin, preparation.get("commands", []), spawn_height=spawn_height)
    # Rest schemas may contain stability samples instead of full body vectors;
    # these remain exact raw report evidence, never silently treated as measured.
    rest_rows = rest.get("trace", [])
    rest_samples = []
    rest_observations = rest.get("observations", [])
    refresh_observations = report.get("inventory_refresh_observations", [])
    require(type(report.get("inventory_refresh_ticks", 0)) is int and report.get("inventory_refresh_ticks", 0) in (0, 1) and
            len(refresh_observations) == report.get("inventory_refresh_ticks", 0), "Inventory refresh denominator differs")
    observation_rows(refresh_observations, "inventory_refresh", actor_id)
    refresh_samples = derive_samples(refresh_observations, origin, spawn_height=spawn_height)
    if rest_observations:
        observation_rows(rest_observations, "rest", actor_id)
        rest_samples = derive_samples(rest_observations, origin, spawn_height=spawn_height)
    for row in observations + prepared_rows + rest_observations + refresh_observations:
        if spawn:
            require({key: row["ego"][key] for key in ("actor_id", "type_id", "role_name")} == spawn,
                    "Retained phase actor identity differs from returned spawn")
        ego = row["ego"]
        require(row.get("bounds") == spatial_check(ego["position_m"], ego["rotation_deg"], ego["velocity_mps"], intent["spawn_transform"],
                                                  phase=row["phase"], protocol=intent["protocol"]),
                "Stored bounds differ from actual raw observation")
        if complete:
            require(row["bounds"]["status"] == "within_bounds", "Collected observation violates declared spatial/speed guards")
    if observations:
        require(prepared_rows, "Measurement lacks a retained preparation boundary")
        # Preparation's last row and measurement zero observe the same frame,
        # with fresh optional RPC readbacks. Their coherent physics must match.
        first, last = observations[0], prepared_rows[-1]
        require(all(first[k] == last[k] for k in ("world_frame", "source_time_s", "delta_seconds", "ego")),
                "Preparation/measurement boundary differs")
    settled = verify_settled_preparation(report, intent, actor_id)
    config = report.get("physics_configuration") or {}
    physics_match = (config.get("status") == "available" and config.get("friction_status") == "matched" and
        canonical({key: config.get(key) for key in ("scalars", "wheels")}) == canonical(intent["expected_physics"]))
    if complete:
        require(physics_match, "Collected post-rest physics differs from pinned configuration")
        shutdown = report.get("callback_shutdown") or {}
        require(shutdown.get("accepting") is False and shutdown.get("active") == 0 and shutdown.get("drained") is True and
                integer(shutdown.get("started")) and shutdown.get("started") == shutdown.get("finished"),
                "Collected callback shutdown is not drained")
        verify_completed_preparation(report, intent, actor_id)
        require(all(sample["cached_control_match"]["status"] == "matched" for sample in samples[1:]),
                "Collected measurement cached control is unavailable/mismatched")
        if intent["protocol"]["measurement"] == "pulse":
            start = intent["protocol"]["policy"]["pulse_start_step"]
            require(1.9 <= observations[start]["signed_heading_speed_mps"] <= 2.1,
                    f"Pulse began outside the actual speed window at step {start}")
    protocol = intent["protocol"]
    start = protocol["policy"]["pulse_start_step"]
    release = start + protocol["policy"]["pulse_steps"]
    pulse = {"requested": protocol["measurement"] == "pulse", "brake_level": protocol["brake_level"],
             "command_steps": list(range(start, release)) if protocol["measurement"] == "pulse" else [],
             "planned_start_s": start * DT if protocol["measurement"] == "pulse" else None,
             "planned_end_s": release * DT if protocol["measurement"] == "pulse" else None,
             "release_command_step": release if protocol["measurement"] == "pulse" else None}
    if pulse["requested"]:
        pulse["command_states"] = {str(i): measurement["commands"][i]["application_status"] for i in range(start, release + 1)}
        pulse["observed_response_indices"] = [sample["index"] for sample in samples if start < sample["index"] <= release]
        pulse["release_response_indices"] = [sample["index"] for sample in samples if sample["index"] > release]
    return {"diagnostic_status": report["status"], "cleanup": deepcopy(report["cleanup"]),
            "error": deepcopy(report.get("error")), "protocol": deepcopy(protocol),
            "counts": {"expected_commands": 80, "expected_observations": 81,
                       "observations": len(samples), "command_states": counts},
            "measurement_complete": all_intervals_observed, "measurement_window_retained": window_retained, "samples": samples,
            "preparation_samples": pre_samples, "rest_samples": rest_samples,
            "inventory_refresh_samples": refresh_samples,
            "rest_raw_samples": deepcopy(rest_rows), "rest_status": report.get("rest", {}).get("status"),
            "preparation_status": preparation.get("status"), "pulse": pulse,
            "settled_preparation": settled,
            "post_rest_physics_match": physics_match,
            "command_coherence_findings": [{"step": c["step"], "reason": "acknowledged_rpc_frame_mismatch_retained_failure"}
                for c in measurement["commands"] if c["application_status"] == "sent_acknowledged" and
                not (c["world_frame"] == c["rpc_before_world_frame"] == c["rpc_after_world_frame"])],
            "measurement_time_origin_source": "measurement_frame_zero" if observations else "last_retained_preparation_or_rest_frame",
            "stop": summarize_stop(samples, window_retained, start if pulse["requested"] else 0),
            "release_stop": summarize_stop(samples, window_retained, release + 1) if pulse["requested"] else None,
            "speed_window_observations": {
                "declared_window_mps": deepcopy(protocol["policy"]["speed_window_mps"]),
                "measurement_start_mps": samples[0]["signed_speed_mps"] if samples else None,
                "pulse_entry_step": start,
                "pulse_entry_speed_mps": samples[start]["signed_speed_mps"] if len(samples) > start else None,
                "pulse_entry_within_window": 1.9 <= samples[start]["signed_speed_mps"] <= 2.1 if len(samples) > start else None,
                "step_5_mps": samples[5]["signed_speed_mps"] if len(samples) > 5 else None,
                "step_5_within_window": 1.9 <= samples[5]["signed_speed_mps"] <= 2.1 if len(samples) > 5 else None,
                "interpretation": "Raw source-speed observations; a coast trace does not establish an executed pulse condition."},
            "cached_control_counts": dict(Counter(sample["cached_control_match"]["status"] for sample in samples if sample["cached_control_match"])),
            "observed_limits": {"min_signed_speed_mps": min((s["signed_speed_mps"] for s in samples), default=None),
                "max_abs_interval_dv_dt_mps2": max((abs(s["interval_dv_dt_mps2"]) for s in samples if s["interval_dv_dt_mps2"] is not None), default=None)},
            "collision_callbacks": deepcopy(report.get("collision_callbacks", []))}


def load_trial(directory, source_dir, source_evidence):
    """Return analysis and exact source bytes; never imports captured scripts."""
    directory = Path(directory)
    sources = {}
    for name in ("source-intent.json", "report.json", "events.jsonl"):
        sources[name] = read_bounded(directory / name)
    intent = strict_json(sources["source-intent.json"])
    try:
        validate_intent(intent)
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        raise InvalidEvidence("Invalid intent: " + str(error)) from error
    report = strict_json(sources["report.json"])
    require(type(report) is dict and report.get("intent_sha256") == sha(canonical(intent)), "Original intent hash differs")
    candidates = source_dir if isinstance(source_dir, (list, tuple)) else [source_dir]
    selected_bundle = None
    for candidate in candidates:
        try:
            bundle = {name: read_bounded(Path(candidate) / name, limit=2 * 1024 * 1024) for name in intent["source_files"]}
            if all(sha(bundle[name]) == expected for name, expected in intent["source_files"].items()):
                selected_bundle = candidate
                break
        except (InvalidEvidence, OSError):
            continue
    require(selected_bundle is not None, "No complete captured source bundle matches this trial's exact hashes")
    for name, data in bundle.items():
        sources["source-" + name] = data
    raw_source = read_bounded(source_evidence)
    require(sha(raw_source) == intent["source_evidence"]["sha256"], "Original configuration source evidence hash differs")
    sources["source-evidence.json"] = raw_source
    original = strict_json(raw_source)
    require(original.get("schema_version") == "carla-vehicle-response/1" and original.get("status") == "completed" and
            original.get("worker_adapter_version") == intent["adapter_version"] and original.get("server_version") == intent["engine_build"],
            "Original response schema/status/engine identity differs")
    original_physics = original.get("physics_configuration", {}).get("ego", {})
    require(original_physics.get("status") == "available" and original_physics.get("friction_status") == "matched",
            "Original response physics readback unavailable")
    require(canonical({key: original_physics.get(key) for key in ("scalars", "wheels")}) == canonical(intent["expected_physics"]),
            "Pinned physics differs from its original response source")
    events = verify_chain(sources["events.jsonl"], report, intent)
    result = analyze_report(report, intent)
    result.update(experiment_id=intent["experiment_id"], diagnostic_id=report.get("diagnostic_id"),
        integrity_status="verified", event_count=len(events), event_head_sha256=report["event_chain"]["head_sha256"],
        source_descriptor=deepcopy(intent["source_evidence"]),
        installed_module_hashes=deepcopy(report.get("installed_module_hashes", {})),
        physics_configuration=deepcopy(report.get("physics_configuration")),
        source_bundle_directory=str(Path(selected_bundle).resolve()),
        pinned_conditions={"engine_build": intent["engine_build"], "adapter_version": intent["adapter_version"],
            "physics_sha256": sha(canonical(intent["expected_physics"])),
            "spawn_sha256": sha(canonical(intent["spawn_transform"])),
            "weather_sha256": sha(canonical(intent["requested_weather"])),
            "protocol_version": intent["protocol"]["schema_version"]},
        source_scope="Captured source hashes checked against intent; no captured Python code executed.")
    result["pair_evidence"] = pair_evidence(report, intent)
    receipt_path = directory / "reservation-release.json"
    if receipt_path.exists():
        raw = read_bounded(receipt_path, limit=64 * 1024)
        receipt = strict_json(raw)
        require(type(receipt) is dict and set(receipt) == {"schema_version", "experiment_id", "diagnostic_id", "store_id", "report_sha256", "status", "released_at"} and
                receipt.get("schema_version") == "carla-brake-reservation-release/1" and receipt.get("status") == "released" and
                receipt.get("experiment_id") == intent["experiment_id"] and receipt.get("diagnostic_id") == report["diagnostic_id"] and
                receipt.get("store_id") == intent["expected_store_id"] and
                receipt.get("report_sha256") == sha(sources["report.json"]), "Reservation-release receipt identity/hash differs")
        require(report.get("release_eligible") is True and report["cleanup"]["status"] == "confirmed" and
                report["reservation_retained"] is True, "Release receipt conflicts with sealed report eligibility")
        sources["reservation-release.json"] = raw
        result["reservation_release"] = {"status": "release_receipt_verified", "receipt": receipt,
            "scope": "Recorded release receipt; no current remote reservation/world observation by this analyzer."}
    else:
        result["reservation_release"] = {"status": "not_confirmed", "report_reservation_retained": report["reservation_retained"]}
    result["source_files"] = [file_info(data, name) for name, data in sources.items()]
    return result, sources


def pair_evidence(report, intent):
    if intent["protocol"]["schema_version"] != SETTLED_VERSION:
        return None
    observations = report["measurement"]["observations"]
    prep = report["preparation"]
    controls = [deepcopy(c["control"]) for c in prep.get("commands", [])]
    source_gears = [(row.get("reported_control") or {}).get("data", {}).get("gear")
                    if (row.get("reported_control") or {}).get("data") else None
                    for row in prep.get("observations", [])[:-1]]
    return {"conditions": {"installed_module_hashes": deepcopy(report.get("installed_module_hashes")), "protocol": {k: deepcopy(v) for k, v in intent["protocol"].items()
                    if k not in ("brake_level", "measurement", "repeat_index")},
                **{k: deepcopy(intent[k]) for k in ("engine_build", "adapter_version", "expected_physics",
                    "spawn_transform", "requested_weather", "expected_world", "source_files")}},
            "preparation_controls": controls, "preparation_source_gears": source_gears,
            "preparation_control_history_sha256": sha(canonical(controls)),
            "entry_observation": deepcopy(observations[0]) if observations else None}


def raw_rotating_entry(row):
    envelope = row.get("telemetry") or {}
    require(envelope.get("status") == "available" and
            envelope.get("before_world_frame") == row["world_frame"] == envelope.get("after_world_frame"),
            "Entry raw rotating state unavailable/incoherent")
    def value(field):
        require(field.get("status") == "available" and field.get("reason") is None and finite(field.get("value")),
                "Entry raw rotating field unavailable")
        return field["value"]
    wheels = envelope.get("wheels", [])
    require(len(wheels) == 4 and all(type(w.get("index")) is int and w["index"] == i for i, w in enumerate(wheels)),
            "Entry raw wheel identity/order differs")
    return [value(envelope["scalars"]["engine_rpm"]), *(value(w["fields"]["omega"]) for w in wheels)]


def conditional_pair(candidate, baseline):
    """Report every assessed mismatch; never retime, interpolate or fit a gain."""
    reasons, differences = [], {}
    def check(condition, reason):
        if not condition:
            reasons.append(reason)
    for label, trial in (("candidate", candidate), ("baseline", baseline)):
        check(trial.get("integrity_status") == "verified", label + ": evidence integrity is not verified")
        check(trial.get("protocol", {}).get("schema_version") == SETTLED_VERSION, label + ": requires fresh protocol3 evidence")
        check(trial.get("diagnostic_status") == "collected" and trial.get("measurement_complete") is True,
              label + ": requires a collected complete 80/81 trial")
        check(((trial.get("settled_preparation") or {}).get("entry_gate") or {}).get("admitted") is True,
              label + ": observed preparation gate was not admitted")
        check(((trial.get("settled_preparation") or {}).get("measurement_entry_gate") or {}).get("admitted") is True,
              label + ": fresh measurement entry gate was not admitted")
    check(candidate.get("experiment_id") != baseline.get("experiment_id"), "Candidate and baseline are the same experiment")
    check(baseline.get("protocol", {}).get("brake_level") == 0., "Baseline must have exactly zero brake")
    left, right = candidate.get("pair_evidence"), baseline.get("pair_evidence")
    if left and right:
        for label, evidence in (("candidate", left), ("baseline", right)):
            hashes = evidence["conditions"].get("installed_module_hashes")
            check(type(hashes) is dict and bool(hashes) and all(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes.values()),
                  label + ": installed module identity is unavailable")
        for key in sorted(set(left["conditions"]) | set(right["conditions"])):
            check(canonical(left["conditions"].get(key)) == canonical(right["conditions"].get(key)),
                  "Pinned condition differs: " + key)
        policy = baseline["protocol"]["policy"]
        a, b = left.get("entry_observation"), right.get("entry_observation")
        ca, cb = left["preparation_controls"], right["preparation_controls"]
        differences["preparation"] = {"candidate_command_count": len(ca), "baseline_command_count": len(cb),
            "candidate_control_history_sha256": left["preparation_control_history_sha256"],
            "baseline_control_history_sha256": right["preparation_control_history_sha256"],
            "source_gear_history_equal": left["preparation_source_gears"] == right["preparation_source_gears"]}
        check(len(ca) == len(cb), "Preparation command counts differ; no resampling permitted")
        history_diffs = [abs(x["throttle"] - y["throttle"]) for x, y in zip(ca, cb)]
        other = [i for i, (x, y) in enumerate(zip(ca, cb)) if any(x[k] != y[k] for k in x if k != "throttle")]
        differences["preparation"].update(max_throttle_difference=max(history_diffs, default=None),
            other_control_mismatch_indices=other,
            throttle_mismatch_indices=[i for i, v in enumerate(history_diffs) if v > policy["pair_max_cached_throttle_difference"]])
        check(not other, "Preparation requested control/gear history differs")
        check(not differences["preparation"]["throttle_mismatch_indices"], "Preparation throttle history exceeds the declared tolerance")
        check(differences["preparation"]["source_gear_history_equal"], "Preparation cached source-gear history differs")
        if a and b:
            ea, eb = a["ego"], b["ego"]
            def distance(field):
                return math.dist([ea[field][k] for k in ("x", "y", "z")], [eb[field][k] for k in ("x", "y", "z")])
            differences["entry"] = {"position_distance_m": distance("position_m"),
                "velocity_distance_mps": distance("velocity_mps"),
                "rotation_max_wrapped_axis_difference_deg": max(abs((ea["rotation_deg"][k] - eb["rotation_deg"][k] + 180) % 360 - 180)
                        for k in ("pitch", "yaw", "roll")),
                "heading_acceleration_difference_mps2": abs(signed_heading(ea["acceleration_mps2"], ea["rotation_deg"]["yaw"]) -
                        signed_heading(eb["acceleration_mps2"], eb["rotation_deg"]["yaw"])),
                "angular_velocity_distance_deg_s": distance("angular_velocity_deg_s")}
            for field, limit in (("position_distance_m", "pair_max_position_distance_m"),
                    ("velocity_distance_mps", "pair_max_velocity_distance_mps"),
                    ("rotation_max_wrapped_axis_difference_deg", "pair_max_rotation_difference_deg"),
                    ("heading_acceleration_difference_mps2", "pair_max_heading_acceleration_difference_mps2"),
                    ("angular_velocity_distance_deg_s", "pair_max_angular_velocity_distance_deg_s")):
                check(differences["entry"][field] <= policy[limit], "Entry " + field + " exceeds tolerance")
            ua, ub = (a.get("reported_control") or {}).get("data"), (b.get("reported_control") or {}).get("data")
            if ua and ub:
                differences["entry"]["cached_control"] = {"candidate": ua, "baseline": ub,
                    "throttle_difference": abs(ua["throttle"] - ub["throttle"])}
                check(abs(ua["throttle"] - ub["throttle"]) <= policy["pair_max_cached_throttle_difference"],
                      "Entry cached throttle differs")
                check(all(ua[k] == ub[k] for k in ua if k != "throttle"), "Entry cached control/gear differs")
            else:
                reasons.append("Entry cached control unavailable")
            try:
                va, vb = raw_rotating_entry(a), raw_rotating_entry(b)
                threshold = policy["raw_state_minimum_absolute_median"]
                relative = [abs(x - y) / abs(y) if abs(y) > threshold else None for x, y in zip(va, vb)]
                differences["entry"]["raw_rotating_state"] = {"fields": ["engine_rpm", "wheel0.omega", "wheel1.omega", "wheel2.omega", "wheel3.omega"],
                    "candidate": va, "baseline": vb, "relative_difference_to_absolute_baseline": relative}
                check(all(d is not None and d <= policy["pair_max_raw_state_relative_difference"] and x * y > 0
                          for d, x, y in zip(relative, va, vb)), "Entry raw RPM/wheel state differs or is unassessable")
            except (InvalidEvidence, KeyError, TypeError) as error:
                reasons.append("Entry raw rotating state unavailable: " + str(error))
        else:
            reasons.append("Entry observation missing")
    else:
        reasons.append("Pinned protocol3 preparation/entry evidence missing")
    sa, sb = candidate.get("samples", []), baseline.get("samples", [])
    check(len(sa) == len(sb) == 81, "Aligned complete measurement denominator missing")
    bad_times = [i for i, (x, y) in enumerate(zip(sa, sb)) if x["index"] != y["index"] or abs(x["time_s"] - y["time_s"]) > TIME_TOLERANCE]
    differences["relative_time_mismatch_indices"] = bad_times
    check(not bad_times, "Relative source-time/index alignment differs; no interpolation permitted")
    values = []
    if not reasons:
        for x, y in zip(sa, sb):
            values.append({"index": x["index"], "time_s": x["time_s"], "candidate_world_frame": x["world_frame"],
                "baseline_world_frame": y["world_frame"], "candidate_speed_mps": x["signed_speed_mps"], "baseline_speed_mps": y["signed_speed_mps"],
                "additional_speed_loss_mps": (sa[0]["signed_speed_mps"] - x["signed_speed_mps"]) - (sb[0]["signed_speed_mps"] - y["signed_speed_mps"]),
                "candidate_near_stop": abs(x["signed_speed_mps"]) <= .03, "baseline_near_stop": abs(y["signed_speed_mps"]) <= .03})
    return {"candidate_experiment_id": candidate.get("experiment_id"), "baseline_experiment_id": baseline.get("experiment_id"),
        "status": "comparable" if not reasons else "not_comparable", "mismatches": reasons, "observed_differences": differences,
        "samples": values, "candidate_stop": candidate.get("stop"), "baseline_stop": baseline.get("stop"),
        "interpretation": "Conditional additional speed loss L_b(k)=[v_b(0)-v_b(k)]-[v_0(0)-v_0(k)]. Not brake gain, force or hidden-state equality. All near-stop/release/censored observations retained."}


HTML = r'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>CARLA brake response · 원본 응답 비교</title>
<style>
:root{font:15px/1.5 system-ui,sans-serif;color:#172b39;background:#eef2f4}*{box-sizing:border-box}body{margin:0}main{max-width:1400px;margin:auto;padding:28px}h1{margin:0;font-size:28px}h2{font-size:18px}.muted{color:#526577}.panel{background:white;border:1px solid #d2dce2;border-radius:10px;padding:18px;margin:18px 0}.warning{background:#fff2d9;border-left:4px solid #b87705;padding:12px}label{display:inline-flex;gap:8px;align-items:center;margin:6px 16px 6px 0}button,select,input{font:inherit}button,select{padding:6px;border:1px solid #aabac5;border-radius:5px;background:white}svg{width:100%;height:280px;display:block}.charts{min-width:0}.legend{display:flex;gap:18px;flex-wrap:wrap}code{overflow-wrap:anywhere;word-break:break-all;font-size:12px}a{color:#075d93}table{border-collapse:collapse;width:100%;font-size:12px}th,td{padding:8px;text-align:left;border-bottom:1px solid #dde5e9;vertical-align:top}th{position:sticky;top:0;background:#eff5f7}.scroll{overflow:auto;max-height:500px}.badge{display:inline-block;padding:2px 7px;margin-right:5px;border:1px solid #b5c3ce;border-radius:4px;background:#eef3f6}.bad{background:#fce5df;color:#8d2c11}.sources{overflow-wrap:anywhere}pre{white-space:pre-wrap;word-break:break-word;max-height:360px;overflow:auto;background:#f3f6f8;padding:10px}.cross{pointer-events:none}details{margin-top:12px}summary{cursor:pointer}#cursor{min-height:28px}footer{padding:12px 0 30px} @media(max-width:600px){main{padding:12px}h1{font-size:22px}.panel{padding:12px}svg{height:250px}}
</style><main><h1>CARLA brake response · 제동 응답 관찰</h1>
<p class="muted">Offline evidence comparison / 오프라인 원본 비교. Integrity, execution completion, cleanup and observed stopping are separate results.</p>
<div class="warning">진단은 주행 합격·보정된 제동 모델이 아닙니다. No filtering, smoothing, gain fitting or force inference. Cached control agreement is not measured actuator force.</div>
<section class="panel"><h2>Trials / 표시할 실행</h2><div id="trials"></div><label>Phase / 구간 <select id="phase"><option value="all">All retained samples / 모든 표본</option><option value="measurement">Measurement / 측정 80명령·81관측</option><option value="preparation">Preparation / 속도 준비</option><option value="rest">Rest / 정차 안정화</option></select></label><button id="all">Select all</button><button id="none">Hide all</button><p class="muted">Time zero is the first measurement frame when present; otherwise it is the last retained preparation/rest frame (measurement did not start). Each command applies after frame N; cached control is read at the following observation. The last observation has no outgoing command.</p></section>
<section class="panel"><div class="scroll" id="summary"></div></section>
<section class="panel charts"><h2>Signed forward speed / 부호 있는 종방향 속도 · m/s</h2><div class="legend">Solid: horizontal-heading projection of raw velocity. Dashed: raw vertical speed. All original phases retained.</div><svg id="speed" aria-label="Signed speed chart"></svg>
<h2>Height / 높이 · m</h2><div class="legend">Solid: raw world Z. Dashed: Z minus the requested spawn height. Neither is ground clearance.</div><svg id="height" aria-label="World height and spawn-relative height chart"></svg>
<h2>Acceleration / 가속도 · m/s²</h2><div class="legend">Solid: interval Δv/Δt. Dashed: engine raw acceleration projected onto horizontal heading. First interval remains unknown.</div><svg id="accel" aria-label="Acceleration chart"></svg>
<h2>Throttle command / 구동 명령 · normalized</h2><div class="legend">Solid: requested throttle after the source frame when attempted. Dashed: cached reported throttle at this observation. The cached value normally corresponds to the preceding command; it is not wheel torque.</div><svg id="throttle" aria-label="Requested and reported throttle chart"></svg>
<h2>Raw wheel omega / 바퀴 회전 API 값 · units unverified</h2><div class="legend">Same color = same trial. Wheel 0 solid; 1 short dashes; 2 long dashes; 3 dash-dot. Raw API numbers, without a claim of calibrated units or drivetrain engagement. Unavailable/nonfinite/incoherent values leave gaps. RPM remains in the original per-sample details.</div><svg id="omega" aria-label="Raw wheel omega chart"></svg>
<h2>Brake command / 제동 명령 · normalized</h2><div class="legend">Solid: requested brake where a command was attempted (ACK is separate). Dashed: cached reported brake. Unattempted requests stay in the original denominator. Readback is state, not force.</div><svg id="brake" aria-label="Requested and reported brake chart"></svg><div id="cursor" class="muted">Move over a chart to inspect exact frames.</div></section>
<section class="panel"><h2>All selected samples / 선택한 구간의 전체 표본</h2><div class="scroll" id="samples"></div></section>
<section class="panel"><h2>Observed preparation gate / 준비 진입 판정</h2><label>Trial <select id="gate-trial"></select></label><div id="gate"></div></section>
<section class="panel"><h2>Conditional additional speed loss / 조건부 추가 속도 손실</h2><p id="pair-baseline"></p><label>Candidate <select id="pair-trial"></select></label><div id="pair-status"></div><svg id="pair-chart" aria-label="Conditional additional speed loss chart"></svg><div id="pair-cursor"></div><div class="scroll" id="pair-samples"></div><p class="muted">L_b(k)=[v_b(0)−v_b(k)]−[v_0(0)−v_0(k)]. Only the explicitly selected fresh protocol3 zero-brake baseline is eligible. No time interpolation, state matching claim, force estimate or calibration; all near-stop and release samples remain.</p></section>
<section class="panel"><h2>Original evidence / 원본 근거</h2><div id="sources"></div></section>
<section class="panel"><h2>Interpretation limits / 해석 경계</h2><ul id="limits"></ul><details><summary>Exact derived analysis JSON</summary><pre id="analysis"></pre></details></section><footer><a href="analysis.json">Download analysis.json</a> · Self-contained charts; no CDN, network request or external library.</footer></main>
<script id="payload" type="application/json">__DATA__</script><script>
'use strict';const data=JSON.parse(document.getElementById('payload').textContent),colors=['#1164a3','#c45b17','#008777','#8e4ba7','#b03c56','#627322','#595fb5','#946023'];const el=id=>document.getElementById(id),txt=(v,d=5)=>v===null||v===undefined?'unknown':typeof v==='number'?Number(v.toFixed(d)).toString():String(v);const escape=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const label=t=>t.label||t.experiment_id||t.input_directory;let active=new Set(data.trials.map((t,i)=>i));
el('trials').innerHTML=data.trials.map((t,i)=>`<label style="color:${colors[i%colors.length]}"><input type="checkbox" checked data-index="${i}">${escape(label(t))}</label>`).join('');el('trials').addEventListener('change',e=>{const i=Number(e.target.dataset.index);e.target.checked?active.add(i):active.delete(i);render()});el('phase').onchange=render;for(const [id,yes]of[['all',true],['none',false]])el(id).onclick=()=>{active=new Set(yes?data.trials.map((_,i)=>i):[]);el('trials').querySelectorAll('input').forEach(x=>x.checked=yes);render()};
el('summary').innerHTML='<table><thead><tr><th>Trial</th><th>Integrity / outcome</th><th>Frames / commands</th><th>Start / declared pulse-entry speed</th><th>Cleanup / reservation</th><th>Stop observation</th></tr></thead><tbody>'+data.trials.map(t=>`<tr><td>${escape(label(t))}</td><td><span class="badge ${t.integrity_status==='verified'?'':'bad'}">${escape(t.integrity_status)}</span><span class="badge">${escape(t.diagnostic_status||'unknown')}</span>${t.verification_error?'<p>'+escape(t.verification_error)+'</p>':''}${t.error?'<p>'+escape(JSON.stringify(t.error))+'</p>':''}</td><td>${t.counts?`${t.counts.observations} / 81 observations<br>${escape(JSON.stringify(t.counts.command_states))}`:'unverified; no derived trace'}</td><td>${txt(t.speed_window_observations?.measurement_start_mps)} / ${txt(t.speed_window_observations?.pulse_entry_speed_mps)} m/s<br>entry step ${txt(t.speed_window_observations?.pulse_entry_step)} in declared window: ${txt(t.speed_window_observations?.pulse_entry_within_window)}<br>${t.pulse?.requested?'Pulse requested':'No pulse executed'}</td><td>${escape(t.cleanup?.status||'unknown')}<br>${escape(t.reservation_release?.status||'unknown')}</td><td>${escape(t.stop?.status||'unknown')}<br>${escape(t.stop?.confirmation_time_s===undefined?'':txt(t.stop.confirmation_time_s)+' s')}</td></tr>`).join('')+'</tbody></table>';
el('limits').innerHTML=data.limitations.map(x=>'<li>'+escape(x)+'</li>').join('');el('analysis').textContent=JSON.stringify(data,null,2);el('sources').innerHTML=data.trials.map(t=>`<details><summary>${escape(label(t))} · original bytes and hashes</summary>${(t.source_files||[]).map(f=>`<p><a href="${escape(f.path)}">${escape(f.name||f.path)}</a> · ${f.size_bytes} bytes<br><code>${escape(f.sha256)}</code></p>`).join('')}<p>Source/configuration readback and unavailable telemetry are preserved in report.json. Hash equality is consistency, not a signature.</p></details>`).join('');
el('gate-trial').innerHTML=data.trials.map((t,i)=>`<option value="${i}">${escape(label(t))}</option>`).join('');
function renderGate(){const t=data.trials[Number(el('gate-trial').value)],g=t?.settled_preparation;if(!g){el('gate').textContent='No protocol3 gate evidence. Legacy protocol1/2 uses its original entry contract.';return}el('gate').innerHTML=`<p>PI proposals ${g.pi_proposals}; committed transitions ${g.committed_pi_steps}. First plane crossing ${txt(g.first_crossing_index)}. <strong>${g.entry_gate?.admitted?'Observed gate admitted':'Not admitted / 미진입'}</strong></p><p>Fresh same-frame measurement gate: ${txt(g.measurement_entry_gate?.admitted)}. An observed recipe gate is not steady state or hidden-state equality.</p><details><summary>Final preparation and fresh entry gates / 최종·재관측 판정</summary><pre>${escape(JSON.stringify({preparation:g.entry_gate,measurement:g.measurement_entry_gate},null,2))}</pre></details><div class="scroll"><table><thead><tr><th>World frame</th><th>Progress m</th><th>Window frames</th><th>First crossing / admitted</th><th>All criteria / raw unknowns</th></tr></thead><tbody>${g.entry_trace.map(r=>`<tr><td>${r.world_frame}</td><td>${txt(r.progress_m)}</td><td>${escape(JSON.stringify(r.window_frames))}</td><td>${r.crossed_plane_now} / ${r.admitted}</td><td><details><summary>${escape(Object.entries(r.criteria).filter(([k,v])=>!v).map(([k])=>k).join(', ')||'All observed criteria satisfied')}</summary><pre>${escape(JSON.stringify(r,null,2))}</pre></details></td></tr>`).join('')}</tbody></table></div>`}el('gate-trial').onchange=renderGate;renderGate();
const paired=data.paired_comparison;el('pair-baseline').textContent=`Selected baseline: ${paired.baseline_experiment_id||'none'} · ${paired.status}`;el('pair-trial').innerHTML=paired.pairs.map((p,i)=>`<option value="${i}">${escape(p.candidate_experiment_id||'unverified candidate')}</option>`).join('');
function renderPair(){el('pair-cursor').textContent='';const pair=paired.pairs[Number(el('pair-trial').value)];if(!pair){el('pair-status').textContent=paired.note;el('pair-chart').innerHTML='';el('pair-samples').innerHTML='';return}el('pair-status').innerHTML=`<p class="badge ${pair.status==='comparable'?'':'bad'}">${escape(pair.status)}</p><ul>${pair.mismatches.map(x=>`<li>${escape(x)}</li>`).join('')}</ul><details><summary>Every assessed difference / 비교 조건·차이</summary><pre>${escape(JSON.stringify(pair.observed_differences,null,2))}</pre></details>`;const rows=pair.samples.map(r=>({...r,phase:'paired',world_frame:r.candidate_world_frame}));chart('pair-chart',[{trial:{label:pair.candidate_experiment_id},color:colors[0],rows}],[['additional_speed_loss_mps',false]]);el('pair-chart').onpointermove=e=>{const svg=el('pair-chart'),rect=svg.getBoundingClientRect(),w=Math.max(540,svg.clientWidth),time=((e.clientX-rect.left)*w/rect.width-78)/(w-106)*4;const r=rows.reduce((a,b)=>!a||Math.abs(b.time_s-time)<Math.abs(a.time_s-time)?b:a,null);el('pair-cursor').textContent=r?`#${r.index}, t ${txt(r.time_s)} s; candidate frame ${r.candidate_world_frame}, baseline frame ${r.baseline_world_frame}; additional loss ${txt(r.additional_speed_loss_mps)} m/s`:''};el('pair-samples').innerHTML='<table><thead><tr><th>Index / time s</th><th>Candidate / baseline frames</th><th>Candidate / baseline speed</th><th>Additional loss m/s</th><th>Near-stop flags candidate / baseline</th></tr></thead><tbody>'+rows.map(r=>`<tr><td>${r.index} / ${txt(r.time_s)}</td><td>${r.candidate_world_frame} / ${r.baseline_world_frame}</td><td>${txt(r.candidate_speed_mps)} / ${txt(r.baseline_speed_mps)}</td><td>${txt(r.additional_speed_loss_mps)}</td><td>${r.candidate_near_stop} / ${r.baseline_near_stop}</td></tr>`).join('')+'</tbody></table>'}el('pair-trial').onchange=renderPair;renderPair();window.addEventListener('resize',renderPair);
function series(){const phase=el('phase').value;return data.trials.flatMap((t,i)=>{if(!active.has(i)||t.integrity_status!=='verified')return[];let rows=phase==='measurement'?t.samples:phase==='preparation'?t.preparation_samples:phase==='rest'?t.rest_samples:[...t.inventory_refresh_samples,...t.rest_samples,...t.preparation_samples,...t.samples];return[{trial:t,index:i,rows,color:colors[i%colors.length]}]})}
function chart(id,items,fields){const svg=el(id),w=Math.max(540,svg.clientWidth),h=280,p={l:78,r:28,t:18,b:42},all=items.flatMap(x=>x.rows),xx=all.map(x=>x.time_s),yy=items.flatMap(x=>x.rows.flatMap(r=>fields.map(f=>r[f[0]]))).filter(Number.isFinite);let xmin=xx.length?Math.min(...xx):0,xmax=xx.length?Math.max(...xx):4,ymin=yy.length?Math.min(0,...yy):0,ymax=yy.length?Math.max(...yy):1;if(xmax<=xmin)xmax=xmin+1;if(ymax<=ymin)ymax=ymin+1;let pad=(ymax-ymin)*.08;ymin-=pad;ymax+=pad;const X=x=>p.l+(x-xmin)/(xmax-xmin)*(w-p.l-p.r),Y=y=>h-p.b-(y-ymin)/(ymax-ymin)*(h-p.t-p.b);let s='';for(let j=0;j<=5;j++){let v=ymin+(ymax-ymin)*j/5,y=Y(v);s+=`<line x1="${p.l}" x2="${w-p.r}" y1="${y}" y2="${y}" stroke="#e0e7ec"/><text x="${p.l-9}" y="${y+4}" text-anchor="end" fill="#526577" font-size="11">${txt(v,3)}</text>`}for(let j=0;j<=6;j++){let v=xmin+(xmax-xmin)*j/6,x=X(v);s+=`<text x="${x}" y="${h-17}" text-anchor="middle" fill="#526577" font-size="11">${txt(v,2)}</text>`}if(xmin<=0&&xmax>=0)s+=`<line x1="${X(0)}" x2="${X(0)}" y1="${p.t}" y2="${h-p.b}" stroke="#647686" stroke-dasharray="3 4"/>`;for(const item of items){for(const[field,dash]of fields){let d='',prev=null;for(const row of item.rows){const val=row[field];if(!Number.isFinite(val)){prev=null;continue}const contiguous=prev&&row.phase===prev.phase&&row.index===prev.index+1;d+=(contiguous?'L':'M')+X(row.time_s)+','+Y(val)+' ';prev=row}s+=`<path d="${d}" fill="none" stroke="${item.color}" stroke-width="1.7" ${dash?`stroke-dasharray="${typeof dash==='string'?dash:'5 4'}"`:''}/>`}}svg.setAttribute('viewBox',`0 0 ${w} ${h}`);svg.innerHTML=s;svg.onpointermove=e=>{const rect=svg.getBoundingClientRect(),x=(e.clientX-rect.left)*w/rect.width,t=xmin+(x-p.l)/(w-p.l-p.r)*(xmax-xmin);const near=items.map(item=>{let row=item.rows.reduce((a,b)=>!a||Math.abs(b.time_s-t)<Math.abs(a.time_s-t)?b:a,null);return row?`${label(item.trial)}: ${row.phase} #${row.index}, world ${row.world_frame}, t ${txt(row.time_s)} s, v ${txt(row.signed_speed_mps)}, vz ${txt(row.vertical_speed_mps)}, Z ${txt(row.world_height_m)}, ΔZ ${txt(row.height_from_requested_spawn_m)}, dv/dt ${txt(row.interval_dv_dt_mps2)}, engine a ${txt(row.engine_heading_acceleration_mps2)}, outgoing throttle ${txt(row.outgoing_requested_throttle)}, cached throttle ${txt(row.reported_throttle)}, raw omega [${[0,1,2,3].map(i=>txt(row[`raw_wheel_${i}_omega`])).join(", ")}], outgoing brake ${txt(row.outgoing_requested_brake)}, cached brake ${txt(row.reported_brake)}`:null}).filter(Boolean);el('cursor').textContent=near.join(' | ')}}
function render(){const items=series();chart('speed',items,[['signed_speed_mps',false],['vertical_speed_mps',true]]);chart('height',items,[['world_height_m',false],['height_from_requested_spawn_m',true]]);chart('accel',items,[['interval_dv_dt_mps2',false],['engine_heading_acceleration_mps2',true]]);chart('throttle',items,[['outgoing_attempted_throttle',false],['reported_throttle',true]]);chart('omega',items,[['raw_wheel_0_omega',false],['raw_wheel_1_omega','2 3'],['raw_wheel_2_omega','8 3'],['raw_wheel_3_omega','10 3 2 3']]);chart('brake',items,[['outgoing_attempted_brake',false],['reported_brake',true]]);el('samples').innerHTML='<table><thead><tr><th>Trial/phase/index</th><th>World frame</th><th>Time s</th><th>Signed v / vz</th><th>World Z / spawn ΔZ</th><th>Δv/Δt / engine a</th><th>Outgoing brake/status</th><th>Cached brake/match</th><th>Raw optional data</th></tr></thead><tbody>'+items.flatMap(item=>item.rows.map(r=>`<tr><td>${escape(label(item.trial))}<br>${r.phase} #${r.index}</td><td>${r.world_frame}</td><td>${txt(r.time_s)}</td><td>${txt(r.signed_speed_mps)} / ${txt(r.vertical_speed_mps)}</td><td>${txt(r.world_height_m)} / ${txt(r.height_from_requested_spawn_m)}</td><td>${txt(r.interval_dv_dt_mps2)} / ${txt(r.engine_heading_acceleration_mps2)}</td><td>${txt(r.outgoing_requested_brake)}<br>${escape(r.outgoing_application_status||'no outgoing command')}</td><td>${txt(r.reported_brake)}<br>${escape(r.cached_control_match?.status||'no incoming command')}</td><td><details><summary>Inspect</summary><pre>${escape(JSON.stringify({control:r.reported_control,telemetry:r.telemetry,bounds:r.bounds},null,2))}</pre></details></td></tr>`)).join('')+'</tbody></table>'}render();window.addEventListener('resize',render);
</script></html>'''


def write_exclusive(path, data):
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def build_report(trials, source_dir, source_evidence, output, *, baseline_experiment_id=None):
    output = Path(output)
    require(not output.exists(), "Output directory already exists; refusing to overwrite evidence")
    require(0 < len(trials) <= MAX_TRIALS, "One to 32 trial directories required")
    require(len({str(Path(path).resolve()) for path in trials}) == len(trials), "Duplicate trial directory")
    output.mkdir(parents=True, exist_ok=False)
    result = {"schema_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
              "analyzer_sha256": sha(Path(__file__).read_bytes()), "trials": [], "limitations": LIMITATIONS,
              "scope": "Offline original-evidence verification; no engine calls, database writes or calibrated performance verdict."}
    total = 0
    for i, directory in enumerate(trials):
        raw = {}
        try:
            item, raw = load_trial(directory, source_dir, source_evidence)
        except (InvalidEvidence, ValueError, KeyError, TypeError, OSError, IndexError) as error:
            item = {"integrity_status": "invalid_evidence", "verification_error": type(error).__name__ + ": " + str(error),
                    "source_files": []}
            # Retain bounded readable originals even when they fail validation.
            for name in ("source-intent.json", "report.json", "events.jsonl", "reservation-release.json"):
                try:
                    raw[name] = read_bounded(Path(directory) / name)
                except (InvalidEvidence, OSError):
                    pass
        item["input_directory"] = str(Path(directory).resolve())
        protocol = item.get("protocol", {})
        item["label"] = (f"{protocol.get('schema_version', 'unknown protocol')} · {protocol.get('initialization', 'invalid')} · "
                         f"{protocol.get('measurement', 'unknown')} {protocol.get('brake_level', '?')} · repeat {protocol.get('repeat_index', '?')}")
        folder = output / f"trial-{i + 1:02d}"
        folder.mkdir()
        item["source_files"] = []
        for name, data in raw.items():
            total += len(data)
            require(total <= MAX_TOTAL, "Aggregate evidence output exceeds 256 MiB bound")
            write_exclusive(folder / name, data)
            item["source_files"].append({**file_info(data, f"{folder.name}/{name}"), "name": name})
        result["trials"].append(item)
    result["counts"] = {"trials": len(trials), "verified": sum(t["integrity_status"] == "verified" for t in result["trials"]),
                        "invalid": sum(t["integrity_status"] != "verified" for t in result["trials"]),
                        "collected": sum(t.get("diagnostic_status") == "collected" for t in result["trials"]),
                        "failed_prefixes": sum(t.get("diagnostic_status") == "failed" for t in result["trials"])}
    result["status"] = "verified" if result["counts"]["invalid"] == 0 else "invalid_evidence_present"
    verified = [t for t in result["trials"] if t["integrity_status"] == "verified"]
    result["comparison"] = {"kind": "descriptive_overlays_no_model_fit_or_statistical_inference",
        "different_pinned_conditions": [key for key in (verified[0]["pinned_conditions"] if verified else [])
            if len({canonical(t["pinned_conditions"][key]) for t in verified}) > 1],
        "note": "Trial conditions and protocol versions remain explicit. Overlay is not an improvement verdict or proof of independent repeats."}
    baselines = [t for t in result["trials"] if t.get("experiment_id") == baseline_experiment_id] if baseline_experiment_id else []
    result["paired_comparison"] = {"baseline_experiment_id": baseline_experiment_id,
        "status": "not_requested" if baseline_experiment_id is None else "assessed" if len(baselines) == 1 else "baseline_missing_or_ambiguous",
        "pairs": [conditional_pair(t, baselines[0]) for t in result["trials"] if t is not baselines[0]] if len(baselines) == 1 else [],
        "note": "Select a fresh collected protocol3 zero-brake experiment explicitly; legacy coast is never substituted."}
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False).encode()
    require(len(encoded) <= MAX_FILE, "Analysis JSON exceeds 32 MiB bound")
    write_exclusive(output / "analysis.json", encoded)
    # HTML script data is escaped independently of normal text/attribute output.
    safe_data = encoded.decode().replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    write_exclusive(output / "index.html", HTML.replace("__DATA__", safe_data).encode())
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", type=Path, action="append", required=True, help="Capture directory; repeat for a comparison")
    parser.add_argument("--source-dir", type=Path, action="append", required=True,
                        help="Captured scripts; repeat for versioned bundles. Each trial must match one complete bundle exactly.")
    parser.add_argument("--source-evidence", type=Path, required=True, help="Original response JSON bytes referenced by intent source_evidence.sha256")
    parser.add_argument("--output", type=Path, required=True, help="New exclusive analysis directory")
    parser.add_argument("--baseline-experiment-id", help="Explicit fresh protocol3 zero-brake UUID for conditional paired speed-loss comparison")
    args = parser.parse_args(argv)
    try:
        result = build_report(args.trial, args.source_dir, args.source_evidence, args.output, baseline_experiment_id=args.baseline_experiment_id)
    except (InvalidEvidence, OSError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"status": result["status"], "counts": result["counts"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if result["status"] == "verified" else 2


if __name__ == "__main__":
    raise SystemExit(main())
