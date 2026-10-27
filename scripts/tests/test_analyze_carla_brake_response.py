"""Authored protocol evidence, not an actual CARLA engine or vehicle test."""
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analyze_carla_brake_response as analysis
from carla_brake_protocol import (INTENT_VERSION, REPORT_VERSION, POLICY, LEGACY_VERSION, VERSION, canonical,
    make_protocol, measurement_sequence, preparation_control, signed_heading, spatial_check)


def available(value):
    return {"status": "available", "value": value, "reason": None}


def control_reply(frame, control, gear=0):
    value = deepcopy(control)
    for name in analysis.CONTROL_FLOATS:
        value[name] = analysis.f32(value[name])
    value["gear"] = gear  # Automatic gear does not have to equal requested 1.
    return {"before_world_frame": frame, "after_world_frame": frame,
            "status": "available", "error": None, "data": value}


def observation(index, phase, frame, speed, control, spawn, role, *, acceleration=0., yaw=0., protocol=None):
    angle = math.radians(yaw)
    position = {"x": 0., "y": 0., "z": .3}
    rotation = {"pitch": 0., "yaw": yaw, "roll": 0.}
    matrix = [[math.cos(angle), -math.sin(angle), 0., 0.],
              [math.sin(angle), math.cos(angle), 0., 0.], [0., 0., 1., .3], [0., 0., 0., 1.]]
    ego = {"actor_id": 101, "type_id": "vehicle.tesla.model3", "role_name": role,
        "position_m": position, "rotation_deg": rotation,
        "snapshot_transform": {"position_m": deepcopy(position), "rotation_deg": deepcopy(rotation), "matrix_native": matrix},
        "forward_vector": {"x": math.cos(angle), "y": math.sin(angle), "z": 0.},
        "velocity_mps": {"x": speed * math.cos(angle), "y": speed * math.sin(angle), "z": 0.},
        "acceleration_mps2": {"x": acceleration * math.cos(angle), "y": acceleration * math.sin(angle), "z": 0.},
        "angular_velocity_deg_s": {"x": 0., "y": 0., "z": 0.}}
    return {"index": index, "phase": phase, "world_frame": frame,
        "source_time_s": frame * .05, "delta_seconds": .05, "ego": ego,
        "signed_heading_speed_mps": signed_heading(ego["velocity_mps"], yaw),
        "bounds": spatial_check(position, rotation, ego["velocity_mps"], spawn, phase=phase, protocol=protocol),
        "reported_control": control_reply(frame, control),
        "telemetry": {"before_world_frame": frame, "after_world_frame": frame,
            "status": "available", "error": None, "source_api": "vehicle.get_telemetry_data",
            "interpretation": "raw_api_fields_units_unverified", "wheels": [],
            "scalars": {"speed": available(speed), "engine_rpm": {"status": "nonfinite", "value": None, "reason": "API returned nan"}}}}


def command(control, step, time_s, before=None, after=None):
    return {"step": step, "time_s": time_s, "control": deepcopy(control),
        "application_status": "observed_next_frame" if after else "not_attempted",
        "world_frame": before["world_frame"] if before else None,
        "next_world_frame": after["world_frame"] if after else None,
        "next_observation_index": after["index"] if after else None,
        "rpc_before_world_frame": before["world_frame"] if before else None,
        "rpc_after_world_frame": before["world_frame"] if before else None,
        "rpc_response": {"actor_id": 101, "error": None} if before else None}


def fixture(tmp_path, *, initialization="velocity_injection", measurement="coast", brake=.1, speeds=None,
            protocol_version=LEGACY_VERSION):
    source = tmp_path / "captured-scripts"
    source.mkdir(exist_ok=True)
    sources = {}
    for name in ("carla_brake_protocol.py", "diagnose_carla_brake_response.py", "diagnose_carla_self_hit.py"):
        data = ("# Authored source descriptor fixture, not executed: " + name).encode()
        (source / name).write_bytes(data)
        sources[name] = analysis.sha(data)
    physics = {"scalars": {"mass": available(1845.)}, "wheels": [
        {"index": i, "fields": {"tire_friction": available(analysis.f32(.8))}} for i in range(4)]}
    original = {"schema_version": "carla-vehicle-response/1", "status": "completed", "worker_adapter_version": "0.8.0",
        "server_version": "1ae5356", "physics_configuration": {"ego": {"status": "available", "friction_status": "matched", **physics}}}
    original_path = tmp_path / "original-response.json"
    original_path.write_bytes(canonical(original))
    protocol = make_protocol(initialization, measurement=measurement, brake_level=brake if measurement == "pulse" else 0., version=protocol_version)
    pose = {"position_m": {"x": 0., "y": 0., "z": .3}, "rotation_deg": {"pitch": 0., "yaw": 0., "roll": 0.}}
    world = {"world_id": "18446744073709551600", "map": {"name": "Town10HD_Opt", "opendrive_sha256": "a" * 64, "opendrive_size_bytes": 100},
        "actors": [{"actor_id": 1, "type_id": "traffic.traffic_light", "role_name": None}],
        "settings": {"values": {"synchronous_mode": False, "fixed_delta_seconds": None}, "invalid_fields": {}},
        "weather": {"values": {"cloudiness": 0.}, "invalid_fields": {}}}
    intent = {"schema_version": INTENT_VERSION, "experiment_id": str(uuid.uuid4()), "protocol": protocol,
        "protocol_sha256": analysis.sha(canonical(protocol)), "sequence_sha256": analysis.sha(canonical(measurement_sequence(protocol))),
        "expected_world": world, "expected_store_id": str(uuid.uuid4()), "engine_build": "1ae5356", "adapter_version": "0.8.0",
        "spawn_transform": pose, "requested_weather": deepcopy(world["weather"]), "expected_physics": physics,
        "source_files": sources, "source_evidence": {"path": "captured/original-response.json", "sha256": analysis.sha(original_path.read_bytes())}}
    role = "axiom_brake_" + intent["experiment_id"].replace("-", "") + "_ego"
    rest_rows = [observation(i, "rest", 100 + i, 0., POLICY["rest_control"], pose, role, protocol=protocol) for i in range(8)]
    rest_trace = []
    for i, row in enumerate(rest_rows):
        ego = row["ego"]
        actor = {k: deepcopy(ego[k]) for k in ("actor_id", "position_m", "rotation_deg", "velocity_mps", "angular_velocity_deg_s", "acceleration_mps2")}
        actor.update(invalid_fields=[], stable=i > 0, derived={"linear_speed_mps": 0., "absolute_vertical_speed_mps": 0.,
            "angular_speed_deg_s": 0., "acceleration_norm_mps2": 0., "position_step_m": 0. if i else None, "rotation_step_deg": 0. if i else None},
            criteria={"linear_speed": True, "vertical_speed": True, "angular_speed": True, "acceleration": True, "position_step": i > 0, "rotation_step": i > 0})
        rest_trace.append({"tick": i + 1, "world_frame": row["world_frame"], "source_time_s": row["source_time_s"], "actor": actor, "stable_streak": i})
    rest_control = command(POLICY["rest_control"], None, None)
    rest_control.update(application_status="observed_next_frame", world_frame=99, rpc_before_world_frame=99, rpc_after_world_frame=99,
                        next_world_frame=100, next_observation_index=0, rpc_response={"actor_id": 101, "error": None})
    prep_rows = [deepcopy(rest_rows[-1])]
    prep_rows[0].update(index=0, phase="preparation")
    prep_rows[0]["bounds"] = spatial_check(prep_rows[0]["ego"]["position_m"], prep_rows[0]["ego"]["rotation_deg"],
                                          prep_rows[0]["ego"]["velocity_mps"], pose, phase="preparation", protocol=protocol)
    prep_commands = []
    prep_speeds = [2.] if initialization == "velocity_injection" else [1., 1.5, 1.9, 1.91, 1.95, 1.98, 2.]
    for i, speed in enumerate(prep_speeds):
        control = POLICY["rest_control"] if initialization == "velocity_injection" else preparation_control(protocol, prep_rows[-1]["signed_heading_speed_mps"])
        after = observation(i + 1, "preparation", 108 + i, speed, control, pose, role, protocol=protocol)
        prep_commands.append(command(control, None if initialization == "velocity_injection" else i,
                                     None if initialization == "velocity_injection" else i * .05, prep_rows[-1], after))
        prep_rows.append(after)
    prep = {"status": "passed", "mode": initialization, "commands": prep_commands, "observations": prep_rows,
            "velocity_request": None, "stable_streak": 5}
    if initialization == "velocity_injection":
        prep["velocity_request"] = {"actor_id": 101, "after_world_frame": 107, "requested_speed_mps": 2.,
            "velocity_mps": {"x": 2., "y": 0., "z": 0.}, "operation": "actor.set_target_velocity", "ack_status": "unknown", "returned_without_ack": True}
    seq = measurement_sequence(protocol)
    first = deepcopy(prep_rows[-1]); first.update(index=0, phase="measurement")
    rows = [first]
    speeds = speeds or [2.] * 81
    for i in range(1, 81):
        rows.append(observation(i, "measurement", first["world_frame"] + i, speeds[i], seq[i - 1]["control"], pose, role,
                                acceleration=(speeds[i] - speeds[i - 1]) / .05, protocol=protocol))
    report = {"schema_version": REPORT_VERSION, "experiment_id": intent["experiment_id"], "diagnostic_id": "diagnostic_brake_" + intent["experiment_id"].replace("-", ""),
        "store_id": intent["expected_store_id"], "status": "collected", "error": None, "intent_sha256": analysis.sha(canonical(intent)),
        "protocol_sha256": intent["protocol_sha256"], "sequence_sha256": intent["sequence_sha256"], "source_files": sources,
        "source_evidence": intent["source_evidence"], "engine_build": "1ae5356", "adapter_version": "0.8.0", "original": world,
        "coordinate_convention": "carla_left_handed_world", "angular_velocity_basis": "official_carla_0.9.16_degrees_per_second_installed_build_separate",
        "requested_weather": intent["requested_weather"], "installed_module_hashes": {"authored": "b" * 64},
        "spawn": {"ego": {"returned_identity": {"actor_id": 101, "type_id": "vehicle.tesla.model3", "role_name": role}}},
        "physics_configuration": {"status": "available", "friction_status": "matched", **deepcopy(physics)},
        "rest": {"status": "passed", "stable_streak": 7, "trace": rest_trace, "observations": rest_rows,
            "initial_control": rest_control, "policy": {"version": "carla-startup-stability/1", "min_ticks": 8, "max_ticks": 100,
                "consecutive_stable_samples": 5, "max_linear_speed_mps": .03, "max_vertical_speed_mps": .05, "max_angular_speed_deg_s": 1.,
                "max_position_step_m": .005, "max_rotation_step_deg": .05, "max_acceleration_mps2": .1, "max_wall_time_s": 60.,
                "max_tick_rpc_time_s": 10., "clock_tolerance_s": 1e-6}}, "preparation": prep,
        "measurement": {"expected_commands": 80, "expected_observations": 81, "observations": rows,
            "commands": [command(s["control"], s["step"], s["time_s"], rows[i], rows[i + 1]) for i, s in enumerate(seq)]},
        "collision_callbacks": [], "callback_shutdown": {"accepting": False, "active": 0, "drained": True, "started": 0, "finished": 0},
        "cleanup": {"status": "confirmed", "errors": []}, "reservation_retained": True, "release_eligible": True,
        "finished_at": "2026-09-14T00:00:00Z"}
    directory = tmp_path / (initialization + "-" + measurement)
    directory.mkdir()
    seal(directory, intent, report)
    return directory, source, original_path, intent, report


def seal(directory, intent, report, *, receipt=True):
    rows, previous = [], "0" * 64
    items = [("diagnostic_started", {k: report[k] for k in ("experiment_id", "intent_sha256", "protocol_sha256", "sequence_sha256", "source_files")})]
    for row in report["measurement"]["observations"]:
        items.append(("observation_complete", row))
    items.append(("diagnostic_final", {"status": report["status"], "error": report["error"], "cleanup_status": report["cleanup"]["status"],
        "reservation_retained": report["reservation_retained"], "report_projection_sha256": analysis.sha(canonical({k: v for k, v in report.items() if k not in ("event_chain", "finished_at")}))}))
    for i, (kind, data) in enumerate(items):
        body = {"sequence": i, "previous_sha256": previous, "at": "2026-09-14T00:00:00Z", "kind": kind, "data": deepcopy(data)}
        previous = analysis.sha(canonical(body)); rows.append({**body, "sha256": previous})
    report["event_chain"] = {"entries": len(rows), "head_sha256": previous, "sealed": True}
    (directory / "source-intent.json").write_bytes(canonical(intent))
    (directory / "report.json").write_bytes(canonical(report))
    (directory / "events.jsonl").write_bytes(b"".join(canonical(row) + b"\n" for row in rows))
    path = directory / "reservation-release.json"
    if receipt:
        path.write_bytes(canonical({"schema_version": "carla-brake-reservation-release/1", "experiment_id": intent["experiment_id"],
            "diagnostic_id": report["diagnostic_id"], "store_id": intent["expected_store_id"], "report_sha256": analysis.sha(canonical(report)),
            "status": "released", "released_at": "2026-09-14T00:00:01Z"}))
    else:
        path.unlink(missing_ok=True)


def test_complete_coast_preserves_all_samples_unknown_telemetry_and_auto_gear(tmp_path):
    directory, source, original, intent, report = fixture(tmp_path)
    result, _ = analysis.load_trial(directory, source, original)
    assert result["counts"] == {"expected_commands": 80, "expected_observations": 81, "observations": 81,
                               "command_states": {"observed_next_frame": 80}}
    assert result["stop"]["status"] == "right_censored"
    assert result["samples"][0]["interval_dv_dt_mps2"] is None
    assert result["samples"][-1]["outgoing_requested_brake"] is None
    assert result["samples"][1]["cached_control_match"]["reported_gear"] == 0
    assert result["cached_control_counts"] == {"matched": 80}
    assert result["samples"][1]["telemetry"]["scalars"]["engine_rpm"]["value"] is None
    assert len(result["rest_samples"]) == 8 and len(result["preparation_samples"]) == 2
    assert result["reservation_release"]["status"] == "release_receipt_verified"


def test_throttle_preparation_uses_actual_formula_five_window_samples_and_no_injection(tmp_path):
    directory, source, original, *_ = fixture(tmp_path, initialization="throttle")
    result, _ = analysis.load_trial(directory, source, original)
    assert len(result["preparation_samples"]) == 8
    assert result["measurement_complete"] is True


def test_pulse_matches_float32_and_release_stopping_uses_following_frame(tmp_path):
    speeds = [2.] * 81
    speeds[6:15] = [1.8, 1.5, 1.2, .9, .5, .02, .01, 0., 0.]
    speeds[15:] = [0.] * 66
    directory, source, original, *_ = fixture(tmp_path, measurement="pulse", speeds=speeds)
    result, _ = analysis.load_trial(directory, source, original)
    assert result["pulse"]["command_steps"] == [5, 6, 7, 8, 9]
    assert result["pulse"]["observed_response_indices"] == [6, 7, 8, 9, 10]
    assert result["pulse"]["release_response_indices"][0] == 11
    assert result["samples"][6]["reported_brake"] == analysis.f32(.1)
    assert result["samples"][6]["cached_control_match"]["status"] == "matched"
    assert result["stop"]["confirmed_index"] == 15
    assert result["release_stop"]["first_low_speed_index"] == 11
    assert result["samples"][6]["interval_dv_dt_mps2"] == pytest.approx(-4.)


@pytest.mark.parametrize("change,match", [
    (lambda r: r["measurement"]["observations"][3].update(world_frame=999), "frame gap"),
    (lambda r: r["measurement"]["observations"][3].update(source_time_s=99.), "time gap"),
    (lambda r: r["measurement"]["commands"][1]["rpc_response"].update(actor_id=102), "ACK actor"),
    (lambda r: r["measurement"]["commands"][5]["control"].update(brake=.6), "immutable planned sequence"),
    (lambda r: r["measurement"].update(commands=r["measurement"]["commands"][:-1]), "denominator"),
    (lambda r: r["measurement"]["observations"][4]["reported_control"]["data"].update(brake=.6), "cached control"),
    (lambda r: r["preparation"].update(mode="throttle"), "mode/status"),
    (lambda r: r["preparation"]["velocity_request"].update(ack_status="acknowledged"), "unknown-ACK"),
    (lambda r: r["preparation"]["commands"][0]["control"].update(throttle=.1), "preparation control"),
    (lambda r: r["rest"].update(stable_streak=4), "five stable"),
    (lambda r: r["rest"]["trace"][-1]["actor"].update(stable=False), "stability"),
])
def test_coherently_resealed_false_protocol_claims_rejected(tmp_path, change, match):
    directory, source, original, intent, report = fixture(tmp_path)
    change(report); seal(directory, intent, report)
    with pytest.raises(analysis.InvalidEvidence, match=match):
        analysis.load_trial(directory, source, original)


def test_resealed_throttle_formula_and_early_velocity_fallback_are_rejected(tmp_path):
    directory, source, original, intent, report = fixture(tmp_path, initialization="throttle")
    report["preparation"]["commands"][2]["control"]["throttle"] = .35
    seal(directory, intent, report)
    with pytest.raises(analysis.InvalidEvidence, match="formula"):
        analysis.load_trial(directory, source, original)
    report["preparation"]["velocity_request"] = {"ack_status": "unknown"}
    seal(directory, intent, report)
    with pytest.raises(analysis.InvalidEvidence, match="injection"):
        analysis.load_trial(directory, source, original)


def test_actual_injection_speed_not_just_preparation_passed_label(tmp_path):
    directory, source, original, intent, report = fixture(tmp_path)
    for row in [report["preparation"]["observations"][-1], report["measurement"]["observations"][0]]:
        row["ego"]["velocity_mps"]["x"] = 1.
        row["signed_heading_speed_mps"] = 1.
        row["bounds"]["speed_norm_mps"] = 1.
    seal(directory, intent, report)
    with pytest.raises(analysis.InvalidEvidence, match="actual speed window"):
        analysis.load_trial(directory, source, original)


@pytest.mark.parametrize("failure", ["throttle_window", "pulse_window", "unavailable_control", "rest_short", "unsealed"])
def test_complete_status_does_not_override_missing_executed_protocol_evidence(tmp_path, failure):
    mode = "throttle" if failure == "throttle_window" else "velocity_injection"
    directory, source, original, intent, report = fixture(tmp_path, initialization=mode, measurement="pulse")
    if failure == "throttle_window":
        row = report["preparation"]["observations"][-2]
        row["ego"]["velocity_mps"]["x"] = row["signed_heading_speed_mps"] = row["bounds"]["speed_norm_mps"] = 1.8
        # Keep the last command's formula/readback consistent; only the claimed
        # five-consecutive-speed gate is false.
        c = report["preparation"]["commands"][-1]
        c["control"] = preparation_control(intent["protocol"], 1.8)
        r = report["preparation"]["observations"][-1]
        r["reported_control"] = control_reply(r["world_frame"], c["control"])
        report["measurement"]["observations"][0]["reported_control"] = deepcopy(r["reported_control"])
    elif failure == "pulse_window":
        r = report["measurement"]["observations"][5]
        r["ego"]["velocity_mps"]["x"] = r["signed_heading_speed_mps"] = r["bounds"]["speed_norm_mps"] = 1.89
    elif failure == "unavailable_control":
        report["measurement"]["observations"][9]["reported_control"] = {"before_world_frame": None, "after_world_frame": None,
            "status": "unavailable", "error": "Authored missing cached state", "data": None}
    elif failure == "rest_short":
        report["rest"]["observations"] = report["rest"]["observations"][:7]
        report["rest"]["trace"] = report["rest"]["trace"][:7]
    seal(directory, intent, report)
    if failure == "unsealed":
        report["event_chain"]["sealed"] = False
        (directory / "report.json").write_bytes(canonical(report))
    with pytest.raises(analysis.InvalidEvidence):
        analysis.load_trial(directory, source, original)


def test_failed_before_measurement_has_no_fabricated_measurement_or_stop(tmp_path):
    directory, source, original, intent, report = fixture(tmp_path)
    report.update(status="failed", error={"type": "RuntimeError", "message": "Authored preparation timeout"})
    report["measurement"]["observations"] = []
    report["measurement"]["commands"] = [command(s["control"], s["step"], s["time_s"]) for s in measurement_sequence(intent["protocol"])]
    report["preparation"]["status"] = "running"
    seal(directory, intent, report, receipt=False)
    result, _ = analysis.load_trial(directory, source, original)
    assert result["counts"]["observations"] == 0
    assert result["counts"]["command_states"] == {"not_attempted": 80}
    assert result["samples"] == [] and result["stop"]["status"] == "not_started"
    assert len(result["preparation_samples"]) == 2


def test_single_inventory_refresh_before_rest_preserves_source_boundary(tmp_path):
    directory, source, original, intent, report = fixture(tmp_path)
    r = deepcopy(report["rest"]["observations"][0])
    r.update(phase="inventory_refresh", world_frame=99, source_time_s=4.95)
    r["reported_control"] = control_reply(99, POLICY["rest_control"])
    r["telemetry"] = None
    report["inventory_refresh_ticks"] = 1
    report["inventory_refresh_observations"] = [r]
    report["rest"]["initial_control"].update(world_frame=98, rpc_before_world_frame=98, rpc_after_world_frame=98, next_world_frame=99)
    seal(directory, intent, report)
    result, _ = analysis.load_trial(directory, source, original)
    assert len(result["inventory_refresh_samples"]) == 1


def test_embedded_interactive_javascript_parses_without_external_runtime(tmp_path):
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("Optional static JavaScript parser unavailable")
    code = analysis.HTML.split("</script><script>", 1)[1].split("</script>", 1)[0]
    result = subprocess.run([node, "--check"], input=code, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_failed_prefix_keeps_80_requests_8_observations_and_unknown_next_frame(tmp_path):
    directory, source, original, intent, report = fixture(tmp_path)
    report.update(status="failed", error={"type": "TimeoutError", "message": "Authored optional RPC failure"})
    report["measurement"]["observations"] = report["measurement"]["observations"][:8]
    for i in range(7, 80):
        s = measurement_sequence(intent["protocol"])[i]
        report["measurement"]["commands"][i] = command(s["control"], i, s["time_s"])
    c = report["measurement"]["commands"][7]
    c.update(application_status="sent_acknowledged", world_frame=115, rpc_before_world_frame=115,
             rpc_after_world_frame=116, rpc_response={"actor_id": 101, "error": None})
    report["measurement"]["observations"][-1]["reported_control"] = None
    report["measurement"]["observations"][-1]["telemetry"] = None
    seal(directory, intent, report, receipt=False)
    result, _ = analysis.load_trial(directory, source, original)
    assert result["counts"]["observations"] == 8
    assert result["counts"]["command_states"] == {"observed_next_frame": 7, "sent_acknowledged": 1, "not_attempted": 72}
    assert result["stop"]["status"] == "interrupted_censored"
    assert result["reservation_release"]["status"] == "not_confirmed"
    assert result["command_coherence_findings"] == [{"step": 7, "reason": "acknowledged_rpc_frame_mismatch_retained_failure"}]
    assert result["samples"][-1]["reported_brake"] is None


@pytest.mark.parametrize("target", ["event", "projection", "intent", "script", "source", "receipt", "truncated"])
def test_original_hash_chain_projection_and_sources_detect_corruption(tmp_path, target):
    directory, source, original, intent, report = fixture(tmp_path)
    if target == "event":
        p = directory / "events.jsonl"; p.write_bytes(p.read_bytes().replace(b'diagnostic_started', b'diagnostic_tampered', 1))
    elif target == "projection":
        report["measurement"]["observations"][4]["ego"]["velocity_mps"]["x"] = 1.9
        (directory / "report.json").write_bytes(canonical(report))
    elif target == "intent":
        intent["protocol"]["repeat_index"] = 2
        (directory / "source-intent.json").write_bytes(canonical(intent))
    elif target == "script":
        (source / "diagnose_carla_brake_response.py").write_text("# changed")
    elif target == "source":
        original.write_text("{}")
    elif target == "receipt":
        p = directory / "reservation-release.json"; r = json.loads(p.read_bytes()); r["store_id"] = str(uuid.uuid4());p.write_bytes(canonical(r))
    else:
        p = directory / "events.jsonl";p.write_bytes(p.read_bytes()[:-1])
    with pytest.raises(analysis.InvalidEvidence):
        analysis.load_trial(directory, source, original)


def test_output_exclusive_self_contained_and_invalid_trial_is_not_silently_excluded(tmp_path):
    directory, source, original, *_ = fixture(tmp_path)
    broken = tmp_path / "broken";broken.mkdir()
    (broken / "report.json").write_text('{"status":"failed","status":"collected"}')
    before = (directory / "report.json").read_bytes()
    output = tmp_path / "analysis"
    result = analysis.build_report([directory, broken], source, original, output)
    assert result["counts"] == {"trials": 2, "verified": 1, "invalid": 1, "collected": 1, "failed_prefixes": 0}
    assert result["status"] == "invalid_evidence_present"
    page = (output / "index.html").read_text()
    assert '<svg id="speed"' in page and '<svg id="accel"' in page and '<svg id="brake"' in page
    assert "fetch(" not in page and '<script src=' not in page and "cdn" not in page.lower().split('<script id="payload"')[1]
    assert "onpointermove" in page and "window.addEventListener('resize'" in page
    assert "trial-01/report.json" in page and "invalid_evidence" in page
    assert before == (directory / "report.json").read_bytes() == (output / "trial-01/report.json").read_bytes()
    with pytest.raises(analysis.InvalidEvidence, match="already exists"):
        analysis.build_report([directory], source, original, output)


def test_duplicate_json_and_receipt_only_status_cannot_claim_release(tmp_path):
    directory, source, original, intent, report = fixture(tmp_path)
    receipt = directory / "reservation-release.json"
    data = json.loads(receipt.read_bytes()); data["status"] = "not_released";receipt.write_bytes(canonical(data))
    with pytest.raises(analysis.InvalidEvidence, match="receipt"):
        analysis.load_trial(directory, source, original)
    with pytest.raises(analysis.InvalidEvidence, match="Duplicate JSON"):
        analysis.strict_json(b'{"a":1,"a":2}')


def test_heading_projection_is_signed_not_speed_magnitude_and_first_difference_is_unknown():
    row = observation(0, "measurement", 10, -2., POLICY["rest_control"],
        {"position_m": {"x": 0., "y": 0., "z": .3}, "rotation_deg": {"pitch": 0., "yaw": 0., "roll": 0.}}, "authored", yaw=90.)
    samples = analysis.derive_samples([row], .5)
    assert samples[0]["signed_speed_mps"] == -2.
    assert samples[0]["linear_speed_mps"] == 2.
    assert samples[0]["interval_dv_dt_mps2"] is None


@pytest.mark.parametrize("version,expected", [(LEGACY_VERSION, "failed"), (VERSION, "within_bounds")])
def test_rest_freefall_bounds_use_exact_protocol_without_erasing_failed_prefix(tmp_path, version, expected):
    directory, source, original, intent, report = fixture(tmp_path, protocol_version=version)
    report.update(status="failed", error={"type": "RuntimeError", "message": "Authored stop after retained rest sample"})
    report["preparation"].update(status="not_started", commands=[], observations=[], velocity_request=None)
    report["measurement"]["observations"] = []
    report["measurement"]["commands"] = [command(s["control"], s["step"], s["time_s"]) for s in measurement_sequence(intent["protocol"])]
    row = report["rest"]["observations"][-1]
    row["ego"]["velocity_mps"]["z"] = -3.43
    row["ego"]["position_m"]["z"] -= .6
    row["ego"]["snapshot_transform"]["position_m"]["z"] -= .6
    row["ego"]["snapshot_transform"]["matrix_native"][2][3] -= .6
    row["bounds"] = spatial_check(row["ego"]["position_m"], row["ego"]["rotation_deg"], row["ego"]["velocity_mps"],
                                   intent["spawn_transform"], phase="rest", protocol=intent["protocol"])
    row["reported_control"] = row["telemetry"] = None
    seal(directory, intent, report)
    result, _ = analysis.load_trial(directory, source, original)
    assert result["rest_samples"][-1]["bounds"]["status"] == expected
    assert result["rest_samples"][-1]["vertical_speed_mps"] == -3.43
    assert result["rest_samples"][-1]["height_from_requested_spawn_m"] == -.6
    assert result["rest_samples"][-1]["telemetry"] is None
    assert result["diagnostic_status"] == "failed" and result["counts"]["command_states"] == {"not_attempted": 80}


@pytest.mark.parametrize("version", [LEGACY_VERSION, VERSION])
def test_completed_protocol_specific_rest_shape_and_wrong_version_projection(tmp_path, version):
    directory, source, original, intent, report = fixture(tmp_path, protocol_version=version)
    assert analysis.load_trial(directory, source, original)[0]["measurement_complete"]
    other = make_protocol("velocity_injection", version=VERSION if version == LEGACY_VERSION else LEGACY_VERSION)
    row = report["rest"]["observations"][0]
    row["bounds"] = spatial_check(row["ego"]["position_m"], row["ego"]["rotation_deg"], row["ego"]["velocity_mps"],
                                   intent["spawn_transform"], phase="rest", protocol=other)
    seal(directory, intent, report)
    with pytest.raises(analysis.InvalidEvidence, match="Stored bounds differ"):
        analysis.load_trial(directory, source, original)


def test_multiple_protocol_source_bundles_match_whole_bundles_and_never_mix_files(tmp_path):
    one, two = tmp_path / "v1", tmp_path / "v2"
    one.mkdir(); two.mkdir()
    d1, s1, original, *_ = fixture(one)
    d2, s2, _, intent, report = fixture(two, protocol_version=VERSION)
    for name in intent["source_files"]:
        (s2 / name).write_bytes(b"# Different authored v2 source, never executed\n" + name.encode())
    intent["source_files"] = {name: analysis.sha((s2 / name).read_bytes()) for name in intent["source_files"]}
    report.update(source_files=deepcopy(intent["source_files"]), intent_sha256=analysis.sha(canonical(intent)))
    seal(d2, intent, report)
    out = tmp_path / "combined"
    result = analysis.build_report([d1, d2], [s2, s1], original, out)
    assert result["counts"]["verified"] == 2
    assert [t["source_bundle_directory"] for t in result["trials"]] == [str(s1.resolve()), str(s2.resolve())]
    assert result["comparison"]["different_pinned_conditions"] == ["protocol_version"]
    assert '<svg id="height"' in (out / "index.html").read_text()
    split = [tmp_path / "split-a", tmp_path / "split-b"]
    for p in split:
        p.mkdir()
    for i, name in enumerate(intent["source_files"]):
        (split[i % 2] / name).write_bytes((s2 / name).read_bytes())
    with pytest.raises(analysis.InvalidEvidence, match="No complete captured source bundle"):
        analysis.load_trial(d2, split, original)


@pytest.mark.parametrize("initialization,version", [("velocity_injection", VERSION), ("throttle", VERSION), ("throttle", "carla-brake-protocol/3")])
def test_actual_diagnostic_serializer_with_authored_world_and_real_rest_evaluator(tmp_path, monkeypatch, initialization, version):
    # Exercise the separately implemented recorder/lifecycle. The world is
    # authored; this checks protocol interoperability, never CARLA physics.
    import importlib.util
    from types import SimpleNamespace
    from axiom_carla.startup import StartupPolicy, _actor_state
    path = Path(__file__).with_name("test_diagnose_carla_brake_response.py")
    spec = importlib.util.spec_from_file_location("brake_analyzer_native_fixture", path)
    native = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, native)
    spec.loader.exec_module(native)
    module = native.diagnostic_module.__wrapped__(monkeypatch)
    iterator = native.environment.__wrapped__(module, tmp_path, SimpleNamespace(param={"initialization": initialization, "version": version}))
    runner, world, _ = next(iterator)
    try:
        runner.native.update(StartupPolicy=StartupPolicy, _actor_state=_actor_state)
        runner.recorder.event("diagnostic_started", {key: runner.report[key] for key in
            ("experiment_id", "intent_sha256", "protocol_sha256", "sequence_sha256", "source_files")})
        if version == "carla-brake-protocol/3":
            _, _, _, snapshot, _ = native.settled_start((runner, world, module), monkeypatch)
        else:
            snapshot = runner.preflight()
            snapshot = runner.rest(snapshot)
        snapshot = runner.prepare(snapshot)
        runner.measure(snapshot)
        runner.cleanup()
        assert runner.finalize() == 0
        report = analysis.strict_json((runner.output / "report.json").read_bytes())
        analysis.verify_chain((runner.output / "events.jsonl").read_bytes(), report, runner.intent)
        result = analysis.analyze_report(report, runner.intent)
        assert result["measurement_complete"] and len(result["samples"]) == 81
        assert result["counts"]["command_states"] == {"observed_next_frame": 80}
        assert result["rest_status"] == "passed" and len(result["rest_samples"]) == 8
    finally:
        iterator.close()


def test_coast_step_five_outside_pulse_window_is_observed_without_claiming_pulse_execution(tmp_path):
    speeds = [2.] * 81
    speeds[5:] = [1.75] * 76
    directory, source, original, *_ = fixture(tmp_path, speeds=speeds)
    result, _ = analysis.load_trial(directory, source, original)
    assert result["measurement_complete"] and result["pulse"]["requested"] is False
    assert result["speed_window_observations"]["step_5_mps"] == 1.75
    assert result["speed_window_observations"]["step_5_within_window"] is False


def test_failure_after_retaining_whole_window_does_not_turn_it_into_early_censoring(tmp_path):
    directory, source, original, intent, report = fixture(tmp_path)
    report.update(status="failed", error={"type": "RuntimeError", "message": "Authored cleanup timeout"}, release_eligible=False)
    report["cleanup"].update(status="uncertain", errors=["Authored actor destroy not confirmed"])
    seal(directory, intent, report, receipt=False)
    result, _ = analysis.load_trial(directory, source, original)
    assert result["diagnostic_status"] == "failed"
    assert result["measurement_complete"] and result["measurement_window_retained"]
    assert result["stop"]["status"] == "right_censored"
    assert result["cleanup"]["status"] == "uncertain"


def settled_fixture(tmp_path, *, brake=0., speeds=None):
    """Prescribed state/position samples, not a calibrated plant simulation."""
    from carla_brake_protocol import SETTLED_VERSION, settled_preparation_step, settled_preparation_gate
    directory, source, original, intent, report = fixture(tmp_path, initialization="throttle", protocol_version=VERSION)
    protocol = make_protocol("throttle", measurement="pulse" if brake else "coast", brake_level=brake, version=SETTLED_VERSION)
    intent.update(protocol=protocol, protocol_sha256=analysis.sha(canonical(protocol)),
                  sequence_sha256=analysis.sha(canonical(measurement_sequence(protocol))))
    report.update(protocol_sha256=intent["protocol_sha256"], sequence_sha256=intent["sequence_sha256"],
                  intent_sha256=analysis.sha(canonical(intent)))
    spawn, role = intent["spawn_transform"], report["spawn"]["ego"]["returned_identity"]["role_name"]
    def set_position(row, x):
        row["ego"]["position_m"]["x"] = x
        row["ego"]["snapshot_transform"]["position_m"]["x"] = x
        row["ego"]["snapshot_transform"]["matrix_native"][0][3] = x
        row["bounds"] = spatial_check(row["ego"]["position_m"], row["ego"]["rotation_deg"], row["ego"]["velocity_mps"],
                                      spawn, phase=row["phase"], protocol=protocol)
    def raw_state(row):
        row["reported_control"]["data"]["gear"] = 1
        row["telemetry"]["scalars"]["engine_rpm"] = available(500.)
        row["telemetry"]["wheels"] = [{"index": i, "fields": {"omega": available(6.)}} for i in range(4)]
    for rest_row in report["rest"]["observations"]: set_position(rest_row, 0.)
    first = deepcopy(report["rest"]["observations"][-1]); first.update(phase="preparation", index=0)
    set_position(first, 0.)
    prep = {"mode": "throttle", "status": "passed", "velocity_request": None, "commands": [], "observations": [first],
            "pi_steps": [], "pi_state": None, "entry_trace": [settled_preparation_gate(protocol, [first], spawn)]}
    for i in range(120):
        proposed = settled_preparation_step(protocol, prep["observations"][-1], prep["pi_state"])
        row = observation(i + 1, "preparation", first["world_frame"] + i + 1, 2., proposed["control"], spawn, role, protocol=protocol)
        set_position(row, (i + 1) / 10); raw_state(row)
        prep["commands"].append(command(proposed["control"], i, i * .05, prep["observations"][-1], row))
        prep["observations"].append(row); prep["pi_steps"].append(proposed["trace"]); prep["pi_state"] = proposed["state"]
        prep["entry_trace"].append(settled_preparation_gate(protocol, prep["observations"], spawn))
    prep["entry_gate"] = deepcopy(prep["entry_trace"][-1]); assert prep["entry_gate"]["admitted"]
    report["preparation"] = prep
    initial = deepcopy(prep["observations"][-1]); initial.update(index=0, phase="measurement")
    seq, rows = measurement_sequence(protocol), [initial]
    speeds = speeds or [2.] * 81
    for i in range(1, 81):
        row = observation(i, "measurement", initial["world_frame"] + i, speeds[i], seq[i - 1]["control"], spawn, role,
                          acceleration=(speeds[i] - speeds[i - 1]) / .05, protocol=protocol)
        set_position(row, 12. + i * .1); raw_state(row); rows.append(row)
    report["measurement"].update(observations=rows,
        commands=[command(s["control"], s["step"], s["time_s"], rows[i], rows[i + 1]) for i, s in enumerate(seq)],
        entry_observation_frame=initial["world_frame"], entry_gate=settled_preparation_gate(protocol, [*prep["observations"][:-1], initial], spawn))
    seal(directory, intent, report)
    return directory, source, original, intent, report


def test_settled_entry_recomputed_and_pulse_immediately_follows_f0(tmp_path):
    directory, source, original, _, report = settled_fixture(tmp_path, brake=.2)
    result, _ = analysis.load_trial(directory, source, original)
    assert result["settled_preparation"]["first_crossing_index"] == 120
    assert result["settled_preparation"]["committed_pi_steps"] == 120
    assert result["settled_preparation"]["entry_gate"]["window_duration_s"] == pytest.approx(1.)
    assert result["pulse"]["command_steps"] == [0, 1, 2, 3, 4]
    assert result["pulse"]["observed_response_indices"] == [1, 2, 3, 4, 5]
    assert result["pulse"]["release_response_indices"][0] == 6
    assert result["speed_window_observations"]["pulse_entry_step"] == 0
    assert report["measurement"]["commands"][0]["world_frame"] == report["preparation"]["observations"][-1]["world_frame"]


@pytest.mark.parametrize("mutation", ["pi_trace", "pi_control", "pi_state", "gate", "missing_gate", "early_crossing", "entry_frame"])
def test_settled_forged_passed_evidence_is_rejected_even_when_resealed(tmp_path, mutation):
    directory, source, original, intent, report = settled_fixture(tmp_path)
    prep = report["preparation"]
    if mutation == "pi_trace": prep["pi_steps"][3]["state_after"]["integral"] += .01
    if mutation == "pi_control": prep["commands"][3]["control"]["throttle"] += .01
    if mutation == "pi_state": prep["pi_state"]["integral"] += .01
    if mutation == "gate": prep["entry_trace"][-1]["criteria"]["speed_window"] = False
    if mutation == "missing_gate": prep["entry_trace"].pop()
    if mutation == "entry_frame": report["measurement"]["entry_observation_frame"] += 1
    if mutation == "early_crossing":
        row = prep["observations"][50]
        row["ego"]["position_m"]["x"] = row["ego"]["snapshot_transform"]["position_m"]["x"] = 12.
        row["ego"]["snapshot_transform"]["matrix_native"][0][3] = 12.
        row["bounds"] = spatial_check(row["ego"]["position_m"], row["ego"]["rotation_deg"], row["ego"]["velocity_mps"],
                                      intent["spawn_transform"], phase="preparation", protocol=intent["protocol"])
        from carla_brake_protocol import settled_preparation_gate
        prep["entry_trace"] = [settled_preparation_gate(intent["protocol"], prep["observations"][:i+1], intent["spawn_transform"])
                               for i in range(len(prep["observations"]))]
        prep["entry_gate"] = prep["entry_trace"][-1]
    seal(directory, intent, report)
    with pytest.raises(analysis.InvalidEvidence, match="Settled|Stored final settled"):
        analysis.load_trial(directory, source, original)


def test_failed_pi_proposal_is_retained_without_advancing_committed_state(tmp_path):
    directory, source, original, intent, report = settled_fixture(tmp_path)
    prep = report["preparation"]
    prep.update(status="running", commands=prep["commands"][:4], observations=prep["observations"][:4],
                pi_steps=prep["pi_steps"][:4], pi_state=deepcopy(prep["pi_steps"][2]["state_after"]), entry_trace=prep["entry_trace"][:4])
    prep["entry_gate"] = deepcopy(prep["entry_trace"][-1])
    prep["commands"][-1].update(application_status="send_outcome_unknown", next_world_frame=None, next_observation_index=None, rpc_response=None)
    report.update(status="failed", error={"message": "Authored failed RPC"})
    report["measurement"] = {"expected_commands": 80, "expected_observations": 81, "observations": [],
        "commands": [command(s["control"], s["step"], s["time_s"]) for s in measurement_sequence(intent["protocol"])]}
    seal(directory, intent, report)
    result, _ = analysis.load_trial(directory, source, original)
    assert result["settled_preparation"]["pi_proposals"] == 4
    assert result["settled_preparation"]["committed_pi_steps"] == 3
    assert result["counts"]["command_states"] == {"not_attempted": 80}
    prep["pi_state"] = deepcopy(prep["pi_steps"][-1]["state_after"])
    seal(directory, intent, report)
    with pytest.raises(analysis.InvalidEvidence, match="committed state"):
        analysis.load_trial(directory, source, original)


def test_fresh_entry_raw_state_failure_retains_f0_without_actuation(tmp_path):
    from carla_brake_protocol import settled_preparation_gate
    directory, source, original, intent, report = settled_fixture(tmp_path)
    initial = report["measurement"]["observations"][0]
    initial["telemetry"] = None
    report["measurement"].update(observations=[initial], commands=[command(s["control"], s["step"], s["time_s"])
        for s in measurement_sequence(intent["protocol"])], entry_gate=settled_preparation_gate(intent["protocol"],
        [*report["preparation"]["observations"][:-1], initial], intent["spawn_transform"]))
    report.update(status="failed", error={"message": "Fresh raw state unavailable"})
    seal(directory, intent, report)
    result, _ = analysis.load_trial(directory, source, original)
    assert result["settled_preparation"]["measurement_entry_gate"]["admitted"] is False
    assert result["samples"][0]["telemetry"] is None
    report["measurement"]["commands"][0].update(application_status="send_outcome_unknown", world_frame=initial["world_frame"],
        rpc_before_world_frame=initial["world_frame"])
    seal(directory, intent, report)
    with pytest.raises(analysis.InvalidEvidence, match="actuated after rejected"):
        analysis.load_trial(directory, source, original)


def paired_fixtures(tmp_path):
    roots = [tmp_path / "base", tmp_path / "pulse"]
    for root in roots: root.mkdir()
    base = settled_fixture(roots[0])
    pulse = settled_fixture(roots[1], brake=.2, speeds=[2., 1.8, 1.4, .9, .3] + [0.] * 76)
    return base, pulse


def test_conditional_pair_is_explicit_full_denominator_and_preserves_near_stop(tmp_path):
    base, pulse = paired_fixtures(tmp_path)
    baseline, _ = analysis.load_trial(*base[:3]); candidate, _ = analysis.load_trial(*pulse[:3])
    pair = analysis.conditional_pair(candidate, baseline)
    assert pair["status"] == "comparable", pair["mismatches"]
    assert len(pair["samples"]) == 81 and pair["samples"][0]["additional_speed_loss_mps"] == 0.
    assert pair["samples"][5]["additional_speed_loss_mps"] == 2.
    assert all(row["candidate_near_stop"] for row in pair["samples"][5:])
    assert pair["baseline_stop"]["status"] == "right_censored"
    output = tmp_path / "study"
    result = analysis.build_report([base[0], pulse[0]], base[1], base[2], output, baseline_experiment_id=baseline["experiment_id"])
    assert result["paired_comparison"]["pairs"][0]["status"] == "comparable"
    assert 'Conditional additional speed loss' in (output / "index.html").read_text()
    assert analysis.build_report([base[0]], base[1], base[2], tmp_path / "unselected")["paired_comparison"]["status"] == "not_requested"


@pytest.mark.parametrize("mutation,reason", [("position", "position_distance"), ("speed", "velocity_distance"),
    ("rotation", "rotation_max"), ("acceleration", "heading_acceleration"), ("angular", "angular_velocity"),
    ("rpm", "raw RPM"), ("omega", "raw RPM"), ("entry_gear", "cached control/gear"),
    ("history", "throttle history"), ("history_count", "command counts"), ("history_gear", "source-gear"),
    ("time", "alignment"), ("seed", "Pinned condition"), ("module", "Pinned condition")])
def test_conditional_pair_checks_each_predeclared_boundary(tmp_path, mutation, reason):
    base, pulse = paired_fixtures(tmp_path)
    baseline, _ = analysis.load_trial(*base[:3]); candidate, _ = analysis.load_trial(*pulse[:3])
    evidence = candidate["pair_evidence"]; row = evidence["entry_observation"]
    if mutation == "position": row["ego"]["position_m"]["x"] += .02
    if mutation == "speed": row["ego"]["velocity_mps"]["x"] += .006
    if mutation == "rotation": row["ego"]["rotation_deg"]["yaw"] += .06
    if mutation == "acceleration": row["ego"]["acceleration_mps2"]["x"] += .03
    if mutation == "angular": row["ego"]["angular_velocity_deg_s"]["x"] += .06
    if mutation == "rpm": row["telemetry"]["scalars"]["engine_rpm"]["value"] *= 1.021
    if mutation == "omega": row["telemetry"]["wheels"][2]["fields"]["omega"]["value"] *= 1.021
    if mutation == "entry_gear": row["reported_control"]["data"]["gear"] = 2
    if mutation == "history": evidence["preparation_controls"][2]["throttle"] += .0006
    if mutation == "history_count": evidence["preparation_controls"].pop()
    if mutation == "history_gear": evidence["preparation_source_gears"][2] = 2
    if mutation == "time": candidate["samples"][40]["time_s"] += .001
    if mutation == "seed": evidence["conditions"]["protocol"]["seed"] += 1
    if mutation == "module": evidence["conditions"]["installed_module_hashes"] = {"changed": "a" * 64}
    pair = analysis.conditional_pair(candidate, baseline)
    assert pair["status"] == "not_comparable" and not pair["samples"]
    assert any(reason in value for value in pair["mismatches"]), pair["mismatches"]


def test_conditional_pair_reports_all_mismatches_and_rejects_legacy_baseline(tmp_path):
    base, pulse = paired_fixtures(tmp_path)
    baseline, _ = analysis.load_trial(*base[:3]); candidate, _ = analysis.load_trial(*pulse[:3])
    baseline["protocol"]["schema_version"] = VERSION
    baseline["protocol"]["brake_level"] = .2
    baseline["pair_evidence"]["preparation_controls"].pop()
    candidate["pair_evidence"]["entry_observation"]["ego"]["position_m"]["x"] += 1.
    pair = analysis.conditional_pair(candidate, baseline)
    assert len(pair["mismatches"]) >= 4 and not pair["samples"]


def test_missing_v3_explicit_state_is_not_an_inferred_unknown(tmp_path):
    directory, source, original, intent, report = settled_fixture(tmp_path)
    del report["preparation"]["pi_state"]
    seal(directory, intent, report)
    with pytest.raises(analysis.InvalidEvidence, match="explicit state/gate"):
        analysis.load_trial(directory, source, original)


def test_missing_explicit_baseline_does_not_fallback_to_first_coast(tmp_path):
    directory, source, original, *_ = settled_fixture(tmp_path)
    result = analysis.build_report([directory], source, original, tmp_path / "missing-baseline",
                                   baseline_experiment_id=str(uuid.uuid4()))
    assert result["paired_comparison"]["status"] == "baseline_missing_or_ambiguous"
    assert result["paired_comparison"]["pairs"] == []


def test_requested_throttle_next_frame_and_raw_omega_unknowns_are_not_filled(tmp_path):
    _, _, _, _, report = settled_fixture(tmp_path)
    rows = deepcopy(report["preparation"]["observations"][:3])
    commands = deepcopy(report["preparation"]["commands"][:3])
    commands[2]["application_status"] = "not_attempted"
    rows[0]["telemetry"] = None
    rows[1]["telemetry"]["wheels"][0]["fields"]["omega"] = available(-3.)
    rows[1]["telemetry"]["wheels"][1]["fields"]["omega"] = {"status": "nonfinite", "value": None, "reason": "nan"}
    rows[1]["telemetry"]["wheels"][2]["fields"]["omega"] = available(0.)
    rows[2]["telemetry"]["after_world_frame"] += 1
    result = analysis.derive_samples(rows, rows[0]["source_time_s"], commands)
    assert result[0]["outgoing_attempted_throttle"] == commands[0]["control"]["throttle"]
    assert result[1]["reported_throttle"] == analysis.f32(result[0]["outgoing_attempted_throttle"])
    assert result[2]["outgoing_attempted_throttle"] is None
    assert result[2]["outgoing_requested_throttle"] == commands[2]["control"]["throttle"]
    assert result[0]["raw_wheel_0_omega"] is None
    assert [result[1][f"raw_wheel_{i}_omega"] for i in range(4)] == [-3., None, 0., 6.]
    assert all(result[2][f"raw_wheel_{i}_omega"] is None for i in range(4))
    assert result[1]["telemetry"] == rows[1]["telemetry"]
    last = analysis.derive_samples(rows, 0., commands[:2])[-1]
    assert last["outgoing_requested_throttle"] is None
    assert '<svg id="throttle"' in analysis.HTML and '<svg id="omega"' in analysis.HTML


def test_pair_candidate_change_clears_prior_hover_in_executed_javascript(tmp_path):
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed for the offline JavaScript interaction contract")
    base, pulse = paired_fixtures(tmp_path)
    other_root = tmp_path / "other-seed"; other_root.mkdir()
    other = settled_fixture(other_root, brake=.2)
    other[3]["protocol"]["seed"] += 1
    other[3]["protocol_sha256"] = analysis.sha(canonical(other[3]["protocol"]))
    other[4].update(protocol_sha256=other[3]["protocol_sha256"], intent_sha256=analysis.sha(canonical(other[3])))
    seal(other[0], other[3], other[4])
    result = analysis.build_report([base[0], pulse[0], other[0]], base[1], base[2], tmp_path / "page",
                                  baseline_experiment_id=base[3]["experiment_id"])
    assert [p["status"] for p in result["paired_comparison"]["pairs"]] == ["comparable", "not_comparable"]
    # A minimal DOM executes the shipped handlers; this does not assert browser
    # layout or use a native CARLA trial. Root owns separate visual browser QA.
    script = analysis.HTML.split("</script><script>")[1].split("</script>")[0]
    harness = '''const assert=require('node:assert/strict');const nodes={};
    const document={getElementById(id){return nodes[id]??=({value:'',textContent:'',innerHTML:'',clientWidth:600,
      addEventListener(){},querySelectorAll(){return []},setAttribute(){},getBoundingClientRect(){return {left:0,width:600}}})}};
    const window={addEventListener(){}};document.getElementById('payload').textContent=JSON.stringify(REPORT);
    '''.replace("REPORT", json.dumps(result))
    check = '''
    document.getElementById('pair-chart').onpointermove({clientX:200});
    assert.match(document.getElementById('pair-cursor').textContent,/additional loss/);
    document.getElementById('pair-trial').value='1';document.getElementById('pair-trial').onchange();
    assert.equal(document.getElementById('pair-cursor').textContent,'');
    assert.match(document.getElementById('pair-status').innerHTML,/not_comparable/);
    assert.match(document.getElementById('pair-status').innerHTML,/Pinned condition differs/);
    assert.doesNotMatch(document.getElementById('pair-chart').innerHTML,/<path d="[^"]+/);
    document.getElementById('pair-trial').value='0';document.getElementById('pair-trial').onchange();
    assert.equal(document.getElementById('pair-cursor').textContent,'');
    assert.match(document.getElementById('pair-chart').innerHTML,/<path d="[^"]+/);
    '''
    completed = subprocess.run([node], input=harness + script + check, text=True, capture_output=True, timeout=20)
    assert completed.returncode == 0, completed.stderr
