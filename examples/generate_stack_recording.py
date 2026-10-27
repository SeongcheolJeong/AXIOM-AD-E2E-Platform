"""Author a bounded MCAP fixture for geometric stack decision replay.

These are constructed points and independently authored ego samples, not CARLA,
ray-cast measurements, vehicle dynamics or inferred object ground truth. Ego speed
does not change in response to a controller's output.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import struct

from mcap.writer import CompressionType, Writer

BASE_NS = 1_700_000_000_000_000_123
LIDAR_LOG_DELAY_NS = 5_000_000
EGO_LOG_DELAY_NS = 3_000_000

# Independently authored descriptions of the public layouts. Importing the
# backend decoder's constants here would conceal a fixture/decoder mismatch.
EGO_SCHEMA = {"type": "object", "properties": {
    "timestamp_ns": {"type": "string"}, "frame_id": {"type": "string"},
    "speed_mps": {"type": "number"}, "route_heading_error_rad": {"type": "number"},
    "pitch_rad": {"type": "number"}, "roll_rad": {"type": "number"}, "source": {"type": "string"}},
    "required": ["timestamp_ns", "frame_id", "speed_mps", "route_heading_error_rad", "pitch_rad", "roll_rad", "source"],
    "additionalProperties": False}
POINT_SCHEMA = {"type": "object", "properties": {
    "timestamp": {"type": "object"}, "frame_id": {"type": "string"}, "pose": {"type": "object"},
    "point_stride": {"type": "integer"}, "fields": {"type": "array"},
    "data": {"type": "string", "contentEncoding": "base64"}}}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def scene(surface_x: float | None):
    points = [(3.0 + ix * .25, iy * .25, 0.0, .2)
              for ix in range(109) for iy in range(-24, 25)]
    if surface_x is not None:
        points += [(surface_x, iy * .1, .25 + iz * .1, .8)
                   for iy in range(-8, 9) for iz in range(13)]
    # Source points are expressed at the declared roof mount, 2.2m above the
    # authored ego origin. The rig must perform the nonzero translation.
    return b"".join(struct.pack("<ffff", x, y, z - 2.2, intensity) for x, y, z, intensity in points)


def cloud(raw: bytes, at: int):
    sec, nsec = divmod(at, 1_000_000_000)
    return {"timestamp": {"sec": sec, "nsec": nsec}, "frame_id": "lidar_roof",
        "pose": {"position": {"x": 0, "y": 0, "z": 0}, "orientation": {"x": 0, "y": 0, "z": 0, "w": 1}},
        "point_stride": 16, "fields": [{"name": name, "offset": index * 4, "type": 7}
            for index, name in enumerate(("x", "y", "z", "intensity"))],
        "data": base64.b64encode(raw).decode()}


def generate(destination: Path) -> dict:
    cases = [(0, "no_past_lidar", None), (100, "far_surface", 22.0),
             (200, "headway_boundary", 12.0), (300, "empty_scan", "empty"),
             (400, "fresh_far_surface", 22.0), (650, "stale_scan", None),
             (750, "close_surface", 3.0), (850, "ground_only", "ground")]
    destination.parent.mkdir(parents=True, exist_ok=True)
    ordinal, ego_ordinals, ticks = 0, [], []
    with destination.open("xb") as stream:
        writer = Writer(stream, compression=CompressionType.ZSTD, enable_crcs=True, enable_data_crcs=True)
        writer.start(profile="json", library="axiom-authored-stack-fixture/1")
        ls = writer.register_schema("foxglove.PointCloud", "jsonschema", encoded(POINT_SCHEMA))
        es = writer.register_schema("axiom.EgoState.v1", "jsonschema", encoded(EGO_SCHEMA))
        lidar = writer.register_channel("/authored/lidar/roof", "json", ls,
            {"axes": "x-forward,y-left,z-up", "clock": "authored capture +5ms log delay"})
        ego = writer.register_channel("/authored/ego", "json", es,
            {"source": "authored independent ego telemetry", "clock": "authored capture +3ms log delay"})
        writer.add_metadata("axiom.authored.stack.fixture", {
            "source_kind": "synthetic", "source": "Original sampled surfaces and independent ego declarations; no CARLA or real measurements",
            "limitations": "No ray occlusion, scan physics, vehicle dynamics or command feedback. Ground support is deliberately authored.",
            "clock_basis": "Offsets are known by construction; they are not calibrated clock measurements.",
        })
        for index, (milliseconds, label, surface) in enumerate(cases):
            at = BASE_NS + milliseconds * 1_000_000
            selected_lidar = None
            if label == "headway_boundary":
                # Two legitimate samples have the same log timestamp. The last
                # file ordinal is the required predecessor tie winner.
                writer.add_message(lidar, at + LIDAR_LOG_DELAY_NS, encoded(cloud(scene(22.0), at)), at, index)
                ordinal += 1
            if surface is not None:
                raw = b"" if surface == "empty" else scene(None if surface == "ground" else surface)
                writer.add_message(lidar, at + LIDAR_LOG_DELAY_NS, encoded(cloud(raw, at)), at, index)
                selected_lidar = ordinal
                ordinal += 1
            state = {"timestamp_ns": str(at), "frame_id": "ego", "speed_mps": 4.0,
                "route_heading_error_rad": .08, "pitch_rad": 0.0, "roll_rad": 0.0,
                "source": "authored independent ego sample; no controller feedback"}
            writer.add_message(ego, at + EGO_LOG_DELAY_NS, encoded(state), at, index)
            ego_ordinals.append(ordinal)
            ticks.append({"index": index, "case": label, "at_ns": str(at), "ego_ordinal": ordinal,
                          "new_lidar_ordinal": selected_lidar})
            ordinal += 1
        writer.finish()
    raw = destination.read_bytes()
    return {"path": str(destination.resolve()), "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
        "source_kind": "synthetic", "channels": {"lidar": lidar, "ego": ego}, "ego_ordinals": ego_ordinals,
        "lidar_log_time_offset_ns": str(-LIDAR_LOG_DELAY_NS), "ego_log_time_offset_ns": str(-EGO_LOG_DELAY_NS),
        "ticks": ticks, "message_count": ordinal,
        "fidelity": "Actual authored MCAP point bytes and independent ego samples; open-loop protocol exercise, not CARLA or measured driving evidence."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(generate(args.destination), indent=2))
