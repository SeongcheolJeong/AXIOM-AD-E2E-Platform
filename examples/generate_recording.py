"""Generate an original synthetic MCAP for catalog/clock/decoder demonstrations.

This is authored telemetry, not a CARLA run or measured perception result.
The existing destination is never overwritten.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import struct

from mcap.writer import CompressionType, Writer

BASE_NS = 1_789_171_200_000_000_123


def generate(path: Path, *, compression: str = "zstd", duration_s: int = 30) -> dict:
    if not 2 <= duration_s <= 3600:
        raise ValueError("duration_s must be 2..3600")
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("xb") as stream:
        writer = Writer(stream, chunk_size=8192, compression={
            "none": CompressionType.NONE, "zstd": CompressionType.ZSTD, "lz4": CompressionType.LZ4,
        }[compression], enable_crcs=True, enable_data_crcs=True)
        writer.start(profile="axiom.synthetic.v1", library="AXIOM original demo generator/0.1")
        schema = writer.register_schema("axiom.demo.Telemetry", "jsonschema", json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
            "properties": {"time_ns": {"type": "string"}, "speed_mps": {"type": "number"},
                           "brake": {"type": "number"}, "position_m": {"type": "object"}},
        }).encode())
        tracks_schema = writer.register_schema("axiom.demo.AuthoredTracks", "jsonschema", json.dumps({
            "type": "object", "properties": {"tracks": {"type": "array"}, "source": {"type": "string"}},
        }).encode())
        telemetry = writer.register_channel("/vehicle/telemetry", "json", schema, metadata={
            "coordinate_frame": "local_demo_xy_m", "source_kind": "synthetic", "calibration_status": "not_supplied",
        })
        tracks = writer.register_channel("/perception/authored_tracks", "json", tracks_schema, metadata={
            "source_kind": "synthetic", "producer": "authored equations, not model inference",
        })
        opaque = writer.register_channel("/sensor/opaque_example", "axiom-demo-binary", 0, metadata={
            "purpose": "Unknown payload retention example; no sensor decoder is registered",
        })
        events = writer.register_channel("/diagnostics/events", "json", 0)
        writer.add_metadata("provenance", {"source_kind": "synthetic", "license": "Original project-authored demonstration",
                            "generator": "examples/generate_recording.py", "calibration": "not supplied",
                            "warning": "Not measured data, not CARLA, not a perception validation result"})

        # Swap two file-order messages to exercise actual timestamp indexing.
        order = list(range(duration_s * 10 + 1))
        order[10], order[11] = order[11], order[10]
        for i in order:
            time_s = i / 10
            timestamp = BASE_NS + i * 100_000_000
            speed = max(2, 12 - max(0, time_s - 8) * 0.65) + math.sin(time_s * .5) * .25
            # Integrate the same speed equation analytically, independent of file order.
            braking_s = min(max(0, time_s - 8), 10 / .65)
            cruise_s = max(0, time_s - 8 - 10 / .65)
            position = 12 * min(time_s, 8) + 12 * braking_s - .325 * braking_s ** 2 + 2 * cruise_s + .5 * (1 - math.cos(.5 * time_s))
            data = {"time_ns": str(timestamp), "speed_mps": round(speed, 4),
                    "brake": round(min(1, max(0, time_s - 8) * .08), 3),
                    "position_m": {"x": round(position, 4), "y": round(1.5 * math.sin(time_s * .2), 4)},
                    "source": "authored synthetic telemetry"}
            writer.add_message(telemetry, timestamp, json.dumps(data, separators=(",", ":")).encode(), timestamp - 3_000_000, sequence=i)
            count += 1
            if i % 5 == 0:
                payload = {"source": "authored synthetic tracks", "tracks": [{
                    "id": "actor-1", "x_m": round(position + 24 - time_s * .2, 3), "y_m": 1.5,
                    "velocity_mps": 8.0, "classification": "vehicle"}]}
                writer.add_message(tracks, timestamp + 25_000_000, json.dumps(payload).encode(), timestamp + 17_000_000, sequence=i // 5)
                count += 1
            if i % 40 == 0:
                writer.add_message(opaque, timestamp + 40_000_000, struct.pack("<Qfff", i, time_s, speed, position), timestamp + 30_000_000, sequence=i // 40)
                count += 1
            if i in (0, 80, 160, duration_s * 10):
                writer.add_message(events, timestamp, json.dumps({"event": "demo marker", "time_s": time_s}).encode(), timestamp, sequence=i)
                count += 1
        writer.finish()
    return {"path": str(path.resolve()), "message_count": count, "start_time_ns": str(BASE_NS),
            "source_kind": "synthetic", "compression": compression}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--compression", choices=["none", "zstd", "lz4"], default="zstd")
    parser.add_argument("--duration", type=int, default=30)
    args = parser.parse_args()
    print(json.dumps(generate(args.destination, compression=args.compression, duration_s=args.duration), indent=2))
