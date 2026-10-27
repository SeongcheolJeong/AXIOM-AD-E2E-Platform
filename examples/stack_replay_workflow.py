"""Immutable stacks -> actual MCAP bytes -> queued open-loop decision evidence.

Requires an API and execution worker using the same isolated/current workspace.
This example never connects to CARLA and never applies its commands to a vehicle.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import uuid

from axiom_sdk import AxiomClient
from generate_stack_recording import generate
from stack_workflow_components import workflow_component_selections


def run(api, output: Path):
    selections = workflow_component_selections(api.stack_components())
    output.mkdir(parents=True, exist_ok=False)
    fixture = generate(output / "authored-stack-input.mcap")
    (output / "fixture.json").write_text(json.dumps(fixture, indent=2) + "\n")
    recording = api.import_recording(output / "authored-stack-input.mcap", name="Authored stack inputs · eight independent ego ticks",
        source_kind="synthetic", source="examples/generate_stack_recording.py; constructed surfaces, no CARLA, measurements or command feedback")
    sources = api.stack_replay_sources(recording["id"])
    assert {(item["channel_id"], item["role"]) for item in sources["channels"]} == {
        (fixture["channels"]["lidar"], "lidar"), (fixture["channels"]["ego"], "ego")}
    rig = api.create_sensor_rig(name="Authored roof mount · stack fixture", reference_frame="ego",
        coordinate_convention="right_handed_z_up", source_kind="synthetic",
        source="Original fixture construction; mount height is not inferred ground height",
        note="Declared translation; no measured calibration approval.", sensors=[{
            "key": "roof", "name": "Authored roof LiDAR", "kind": "lidar", "frame_id": "lidar_roof",
            "transform": {"translation_m": [0, 0, 2.2], "rotation_xyzw": [0, 0, 0, 1]}}])
    binding = api.bind_recording_sensors(recording["id"], expected_revision=0, rig_id=rig["id"], rig_revision=rig["revision"],
        note="Clock offset known from generator construction; not a measured clock calibration.", channels=[{
            "channel_id": fixture["channels"]["lidar"], "sensor_key": "roof", "decoder_id": "foxglove-json", "decoder_version": "1",
            "max_age_ns": "100000000", "log_time_offset_ns": fixture["lidar_log_time_offset_ns"]}])
    selections["perception"]["parameters"]["ground_reference_z_m"] = 0.0
    selections["perception"]["parameters"]["ground_reference_source"] = "Authored fixture plane z=0 in ego; known from independent construction"
    selections["policy"]["parameters"].update(time_headway_s=1.5, allow_no_obstacle_observed_motion=True)
    definition = {"name": "Authored geometric stack · headway comparison", "description": "Same fixed source ticks and component implementations; only headway changes.",
        "note": "Experimental low-speed, flat-ground assumption. No-obstacle-observed motion explicitly enabled; not free-space certification.",
        "definition": {"reference_frame": "ego", **selections}}
    stack = api.create_stack(**definition)
    original = api.export_stack(stack["id"], output / "stack-r1.json", revision=1).read_bytes()
    revised = deepcopy(definition)
    revised["definition"]["policy"]["parameters"]["time_headway_s"] = 3.0
    second = api.revise_stack(stack["id"], expected_revision=1, **revised)
    assert second["revision"] == 2
    assert api.export_stack(stack["id"], output / "stack-r1-after.json", revision=1).read_bytes() == original
    api.export_stack(stack["id"], output / "stack-r2.json", revision=2)
    api.export_sensor_rig(rig["id"], output / "rig.json", revision=rig["revision"])
    api.export_sensor_binding(recording["id"], output / "binding.json", revision=binding["revision"])
    results = []
    for revision in (1, 2):
        request = dict(label=f"Authored stack decision replay · revision {revision}", stack_id=stack["id"], stack_revision=revision,
            recording_id=recording["id"], binding_revision=binding["revision"], lidar_channel_id=fixture["channels"]["lidar"],
            ego_channel_id=fixture["channels"]["ego"], ego_ordinals=fixture["ego_ordinals"],
            ego_log_time_offset_ns=fixture["ego_log_time_offset_ns"], idempotency_key="stack-example-" + uuid.uuid4().hex)
        # Save the exact intent before submission so a lost reply can be retried
        # with the same endpoint/key/body, instead of inventing a new execution.
        (output / f"submit-r{revision}.json").write_text(json.dumps(request, indent=2) + "\n")
        job = api.submit_stack_replay(**request)
        assert api.submit_stack_replay(**request)["id"] == job["id"]
        job = api.wait_stack_replay(job["id"], timeout=180)
        assert job["status"] == "completed", job
        assert job["counts"] == {"total": 8, "running": 0, "completed": 8, "failed": 0, "not_attempted": 0}
        for tick in job["ticks"]:
            assert tick["control_application"] == "not_applied_open_loop"
            if tick["points"] is not None:
                saved = api.export_stack_replay_points(job["id"], tick["index"], output / f"r{revision}-tick{tick['index']}.xyz")
                assert hashlib.sha256(saved.read_bytes()).hexdigest() == tick["output"]["input_points_sha256"]
        ticks = job["ticks"]
        for index in (0, 3, 5):
            assert ticks[index]["output"]["decision"]["mode"] == "unknown_stop"
            assert ticks[index]["output"]["control"]["brake"] == 1
        assert ticks[2]["input"]["lidar"]["ordinal"] == fixture["ticks"][2]["new_lidar_ordinal"]
        assert ticks[6]["output"]["decision"]["mode"] == "obstacle_stop"
        assert ticks[7]["output"]["observation"]["status"] == "no_obstacle_observed"
        api.export_stack_replay(job["id"], output / f"replay-r{revision}.json")
        results.append(job)
    first_tick, second_tick = (job["ticks"][2] for job in results)
    assert first_tick["input"] == second_tick["input"] and first_tick["points"]["sha256"] == second_tick["points"]["sha256"]
    assert first_tick["output"]["decision"]["mode"] == "approach" and first_tick["output"]["control"]["throttle"] > 0
    assert second_tick["output"]["decision"]["mode"] == "obstacle_stop" and second_tick["output"]["control"]["brake"] == 1
    summary = {"recording_id": recording["id"], "recording_sha256": recording["sha256"], "fixture": fixture,
        "stack_id": stack["id"], "stack_revisions": [1, 2], "rig_id": rig["id"], "binding_revision": binding["revision"],
        "replays": [{"id": job["id"], "counts": job["counts"], "manifest_hash": job["manifest_hash"],
                     "decisions": [tick["output"]["decision"]["mode"] for tick in job["ticks"]]} for job in results],
        "same_input_headway_control_delta_verified": True, "original_revision_unchanged": True,
        "unknown_cases": ["no_past_lidar", "empty_scan", "stale_scan"],
        "fidelity": "Actual queued kernel calculations and hash-verified artifacts from authored bytes; commands are not applied, no closed-loop or driving-performance claim."}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with AxiomClient(args.url, token=os.getenv("ADE2E_API_TOKEN")) as api:
        print(json.dumps(run(api, args.output), indent=2))
