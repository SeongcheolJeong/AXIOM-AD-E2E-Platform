"""Run two pinned stacks on CARLA, then recalculate each from its actual MCAP.

Requires an exclusive, connected CARLA 0.4 worker plus the AXIOM API and queue
worker. This executes the simulator. It is an experimental geometric LiDAR
pilot with declared flat ground and simulator ego/map oracles, not calibration
or a passing safety demonstration. Verdicts and partial failures are preserved.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import uuid

from axiom_sdk import AxiomClient
from stack_workflow_components import workflow_component_selections


def save(directory, name, value):
    with (directory / name).open("x") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def exact_slice(data, ref):
    start, size = ref["offset_bytes"], ref["size_bytes"]
    assert type(start) is int and type(size) is int and start >= 0 and size >= 0
    assert start + size <= len(data)
    result = data[start:start+size]
    assert hashlib.sha256(result).hexdigest() == ref["sha256"]
    return result


def check_trace(run, artifacts):
    context = run["execution_context"]
    trace = json.loads((artifacts / "carla-stack-trace.json").read_text())
    manifest = json.loads((artifacts / "carla-stack-manifest.json").read_text())
    physics = json.loads((artifacts / "carla-stack-physics.json").read_text())
    xyz = (artifacts / "carla-stack-points.bin").read_bytes()
    raw = (artifacts / "carla-stack-raw-xyzi.bin").read_bytes()
    for value in (trace, manifest, physics):
        assert value["execution_id"] == run["id"]
        assert value["input_hash"] == run["provenance"]["input_hash"]
        assert value["context_hash"] == context["context_hash"]
        assert value["status"] == "completed"
    assert manifest["execution_context"] == context
    assert manifest["cleanup_errors"] == []
    assert type(manifest["ego_actor_id"]) is int and manifest["ego_actor_id"] > 0
    for descriptor in manifest["artifacts"]:
        content = (artifacts / descriptor["name"]).read_bytes()
        assert len(content) == descriptor["size_bytes"]
        assert hashlib.sha256(content).hexdigest() == descriptor["sha256"]
    n = round(run["duration_s"] / .05)
    assert len(trace["ticks"]) == manifest["acknowledged_commands"] == n
    assert manifest["next_frame_observed_commands"] == n
    assert len(physics["observations"]) == manifest["retained_source_observations"] == n+1
    for i, tick in enumerate(trace["ticks"]):
        assert tick["sequence"] == i and tick["status"] == "completed"
        assert tick["control_acknowledged"] and tick["application_status"] == "next_frame_observed"
        # apply_batch_sync(..., False) returns one successful server response.
        # An asynchronous apply_control() return would not establish this ACK.
        assert tick["control_rpc_response"] == {"actor_id": manifest["ego_actor_id"], "error": None}
        assert tick["source_world_frame"] == physics["observations"][i]["world_frame"]
        assert tick["next_world_frame"] == tick["source_world_frame"] + 1
        assert tick["next_world_frame"] == physics["observations"][i+1]["world_frame"]
        assert tick["output"]["at_ns"] == tick["packet"]["at_ns"]
        control = tick["output"]["control"]
        assert tick["requested_carla_control"]["steer"] == -control["steer"]
        for key in ("throttle", "brake", "reverse", "hand_brake"):
            assert tick["requested_carla_control"][key] == control[key]
        readback = tick["observed_control"]
        assert set(readback) == {"throttle", "brake", "steer", "reverse", "hand_brake"}
        for key, lower in (("throttle", 0), ("brake", 0), ("steer", -1)):
            assert math.isfinite(readback[key]) and lower <= readback[key] <= 1
        assert type(readback["reverse"]) is bool and type(readback["hand_brake"]) is bool
        # Preserve rather than assert float64 requested == native float32 readback;
        # neither a command ACK nor a readback establishes achieved deceleration.
        if tick["points"] is not None:
            points = exact_slice(xyz, tick["points"])
            assert len(points) == tick["points"]["point_count"] * 12
            assert tick["points"]["sha256"] == tick["output"]["input_points_sha256"]
            assert tick["points"]["sha256"] == tick["packet"]["lidar"]["points_sha256"]
        exact_slice(raw, tick["source_refs"]["lidar"]["raw_points"])
    return trace


def replay_and_compare(api, run, trace, artifacts, directory):
    artifact = next(item for item in run["artifacts"] if item["name"] == "carla-stack-sources.mcap")
    recording = api.import_run_recording(run["id"], artifact["id"])
    save(directory, "recording.json", recording)
    origin = api.recording_origin(recording["id"])
    save(directory, "origin.json", origin)
    assert origin["run_id"] == run["id"] and origin["recording_id"] == recording["id"]
    assert origin["artifact_id"] == artifact["id"]
    assert origin["snapshot"]["execution_context_hash"] == run["execution_context"]["context_hash"]
    assert origin["snapshot"]["execution_context"] == run["execution_context"]
    assert recording["sha256"] == artifact["sha256"] == origin["snapshot"]["recording_sha256"]
    context = run["execution_context"]
    first = trace["ticks"][0]["source_refs"]
    binding = api.bind_recording_sensors(recording["id"], expected_revision=0,
        rig_id=context["rig_snapshot"]["id"], rig_revision=context["rig_snapshot"]["revision"],
        note="Same CARLA episode clock and explicitly set mount; simulator declarations, not measured calibration.",
        channels=[{"channel_id": first["lidar"]["channel_id"], "sensor_key": context["lidar_sensor_key"],
                   "decoder_id": "foxglove-json", "decoder_version": "1", "max_age_ns": "50000000", "log_time_offset_ns": "0"}])
    save(directory, "binding.json", binding)
    # Inspect every retained original, including the final observation that has
    # no outgoing control command, rather than assuming the stream count.
    samples = {}
    for role in ("lidar", "ego"):
        items = list(api.iter_recording_samples(recording["id"], first[role]["channel_id"]))
        assert len(items) == len(trace["ticks"]) + 1
        samples[role] = {item["ordinal"]: item for item in items}
    for tick in trace["ticks"]:
        for role in ("lidar", "ego"):
            source = tick["source_refs"][role]
            assert samples[role][source["ordinal"]]["sha256"] == source["message_sha256"]
    request = {"label": f"Actual CARLA inputs · {run['id']} · same stack recalculation",
        "stack_id": context["stack_snapshot"]["id"], "stack_revision": context["stack_snapshot"]["revision"],
        "recording_id": recording["id"], "binding_revision": binding["revision"],
        "lidar_channel_id": first["lidar"]["channel_id"], "ego_channel_id": first["ego"]["channel_id"],
        "ego_ordinals": [tick["source_refs"]["ego"]["ordinal"] for tick in trace["ticks"]],
        "idempotency_key": "carla-stack-replay-" + uuid.uuid4().hex}
    save(directory, "replay-request.json", request)
    replay = api.submit_stack_replay(**request)
    save(directory, "replay-submitted.json", replay)
    replay = api.wait_stack_replay(replay["id"], timeout=180)
    save(directory, "replay-result.json", replay)
    assert replay["status"] == "completed", replay
    assert replay["counts"]["completed"] == len(trace["ticks"])
    comparisons = []
    xyz = (artifacts / "carla-stack-points.bin").read_bytes()
    for live, recorded in zip(trace["ticks"], replay["ticks"], strict=True):
        assert recorded["status"] == "completed"
        assert recorded["control_application"] == "not_applied_open_loop"
        assert recorded["packet"] == live["packet"]
        for role in ("lidar", "ego"):
            assert recorded["input"][role]["message_sha256"] == live["source_refs"][role]["message_sha256"]
        # Calculation timing differs. All semantic outputs must be identical.
        semantic_live = {k: v for k, v in live["output"].items() if k != "elapsed_ms"}
        semantic_replay = {k: v for k, v in recorded["output"].items() if k != "elapsed_ms"}
        assert semantic_live == semantic_replay, {"tick": live["sequence"], "live": semantic_live, "replay": semantic_replay}
        if live["points"] is not None:
            target = directory / f"replay-points-{live['sequence']:03d}.bin"
            api.export_stack_replay_points(replay["id"], live["sequence"], target)
            assert target.read_bytes() == exact_slice(xyz, live["points"])
        comparisons.append({"sequence": live["sequence"], "semantic_outputs_match": True,
            "input_points_sha256": live["output"]["input_points_sha256"], "live_application": live["application_status"],
            "replay_application": recorded["control_application"]})
    api.export_stack_replay(replay["id"], directory / "replay-manifest.json")
    save(directory, "same-source-comparison.json", comparisons)
    return {"recording_id": recording["id"], "replay_id": replay["id"], "matched_ticks": len(comparisons)}


def run(api, output):
    selections = workflow_component_selections(api.stack_components())
    output.mkdir(parents=True, exist_ok=False)
    health = api.adapter_health("carla")
    save(output, "connection.json", health)
    assert health["status"] == "ready" and health["metadata"]["worker_adapter_version"] == "0.4.0", health
    rig = api.create_sensor_rig(name="CARLA roof LiDAR · explicit live recipe", reference_frame="ego",
        coordinate_convention="right_handed_z_up", source_kind="simulation",
        source="carla-lidar-ego/1 explicitly sets native mount z=2.2m; not measured calibration",
        note="Fixed recipe declaration for live source and recorded recalculation.",
        sensors=[{"key": "roof", "name": "Ray-cast roof LiDAR", "kind": "lidar", "frame_id": "lidar_roof",
                  "transform": {"translation_m": [0, 0, 2.2], "rotation_xyzw": [0, 0, 0, 1]}}])
    save(output, "rig.json", rig)
    selections["perception"]["parameters"].update(lookahead_m=15.0, ground_reference_z_m=0.0,
        ground_reference_source="Explicit low-speed flat-road pilot assumption: plane z=0 in ego coordinates; uncalibrated, not inferred from LiDAR mount or certified road geometry.")
    selections["policy"]["parameters"].update(cruise_speed_mps=2.0, time_headway_s=1.5, allow_no_obstacle_observed_motion=True)
    definition = {"name": "CARLA live geometric stack · observed horizon comparison",
        "description": "Same geometry and controller; headway changes the required stopping horizon within the observed 15m corridor.",
        "note": "Explicit bounded pilot motion assumption; unknown/insufficient observations still stop.",
        "definition": {"reference_frame": "ego", **selections}}
    stack = api.create_stack(**definition)
    save(output, "stack-r1.json", stack)
    revised = deepcopy(definition)
    revised["definition"]["policy"]["parameters"]["time_headway_s"] = 8.0
    second = api.revise_stack(stack["id"], expected_revision=1, **revised)
    save(output, "stack-r2.json", second)
    scenario = api.create_scenario(name="CARLA live LiDAR stack · 2mps 20m gap", category="lane_follow",
        map_name=health["metadata"]["map"].split("/")[-1], duration_s=4, seed=42,
        description="Actual CARLA physics and LiDAR input; fixed stack comparison with ego/map oracle.",
        parameters={"ego_speed_mps": 2, "target_speed_mps": 2, "initial_gap_m": 20,
                    "trigger_time_s": 2, "friction": .8, "response_delay_s": .4})
    save(output, "scenario.json", scenario)
    results = []
    for revision in (1, 2):
        directory = output / f"revision-{revision}"; directory.mkdir()
        request = {"scenario_id": scenario["id"], "adapter_id": "carla",
            "idempotency_key": "carla-live-stack-" + uuid.uuid4().hex,
            "control": {"mode": "stack", "stack_id": stack["id"], "stack_revision": revision,
                        "rig_id": rig["id"], "rig_revision": rig["revision"], "lidar_sensor_key": "roof",
                        "sensor_profile_id": "carla-lidar-ego/1"}}
        save(directory, "request.json", request)
        job = api.submit_job(**request); save(directory, "submitted.json", job)
        job = api.wait_job(job["id"], timeout=240); save(directory, "job.json", job)
        if not job.get("run_id"):
            raise RuntimeError(f"Inspect saved job {job['id']}; no automatic retry")
        result = api.get_run(job["run_id"]); save(directory, "run.json", result)
        artifacts = directory / "artifacts"; artifacts.mkdir()
        for artifact in result["artifacts"]:
            api.download_artifact(artifact, artifacts / artifact["name"])
        if result["status"] != "completed":
            raise RuntimeError(f"Inspect partial diagnostic artifacts for {result['id']}; no automatic retry or resource reconciliation")
        trace = check_trace(result, artifacts)
        recalculation = replay_and_compare(api, result, trace, artifacts, directory)
        results.append({"revision": revision, "run_id": result["id"], "verdict": result["verdict"],
            "metrics": result["metrics"], "context_hash": result["execution_context"]["context_hash"],
            "decisions": {mode: sum(t["output"]["decision"]["mode"] == mode for t in trace["ticks"])
                          for mode in sorted({t["output"]["decision"]["mode"] for t in trace["ticks"]})},
            **recalculation})
        save(directory, "summary.json", results[-1])
    summary = {"scope": __doc__, "results": results}
    save(output, "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with AxiomClient(args.url, token=os.getenv("ADE2E_API_TOKEN")) as api:
        print(json.dumps(run(api, args.output), indent=2))
