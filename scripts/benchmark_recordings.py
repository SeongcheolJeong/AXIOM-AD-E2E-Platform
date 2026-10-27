"""Measure the local synthetic recording workload against an explicitly selected API.

This creates a recording in that API. Use an isolated test DB, not production.
Results describe this workload/environment only, not fleet-scale capacity.
"""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import statistics
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

from axiom_sdk import AxiomClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from generate_recording import BASE_NS, generate

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--url", required=True)
parser.add_argument("--duration", type=int, default=3600)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
if args.output.exists():
    raise SystemExit("Refusing to overwrite an existing benchmark report")

with TemporaryDirectory(prefix="axiom-recordings-benchmark-") as directory:
    source = Path(directory) / "synthetic.mcap"
    generate(source, duration_s=args.duration)
    with AxiomClient(args.url, token=os.getenv("ADE2E_API_TOKEN"), timeout=180) as client:
        if client.recordings():
            raise SystemExit("Benchmark requires an empty recording catalog to measure a fresh import")
        started = perf_counter()
        recording = client.import_recording(source, name=f"Synthetic {args.duration}s import benchmark",
                                            source_kind="synthetic", source="scripts/benchmark_recordings.py")
        import_s = perf_counter() - started
        telemetry = next(channel for channel in recording["channels"] if channel["topic"] == "/vehicle/telemetry")
        started = perf_counter()
        client.recording_samples(recording["id"], telemetry["id"], at_ns=BASE_NS)
        first_seek_ms = (perf_counter() - started) * 1000
        timings = []
        for index in range(50):
            timestamp = BASE_NS + (index * 73 % (args.duration + 1)) * 1_000_000_000
            started = perf_counter()
            result = client.recording_samples(recording["id"], telemetry["id"], at_ns=timestamp)
            timings.append((perf_counter() - started) * 1000)
            assert result["samples"] and int(result["samples"][0]["log_time_ns"]) <= timestamp
        client.download_recording(recording, Path(directory) / "roundtrip.mcap")
        report = {
            "scope": "Single local synthetic MCAP, sequential HTTP requests; not fleet throughput or real sensors",
            "environment": {"python": platform.python_version(), "machine": platform.machine(),
                            "os": platform.system(), "mcap": importlib.metadata.version("mcap")},
            "workload": {"duration_s": args.duration, "compression": "zstd", "message_count": recording["message_count"],
                         "channel_count": recording["channel_count"], "original_bytes": recording["size_bytes"],
                         "indexed_payload_bytes": recording["index"]["size_bytes"], "sha256": recording["sha256"]},
            "import_seconds": round(import_s, 4), "first_seek_ms": round(first_seek_ms, 3),
            "subsequent_seek_count": len(timings), "seek_median_ms": round(statistics.median(timings), 3),
            "seek_p95_ms": round(statistics.quantiles(timings, n=100, method="inclusive")[94], 3),
            "original_download_verified": True,
        }
args.output.parent.mkdir(parents=True, exist_ok=True)
with args.output.open("x") as output:
    json.dump(report, output, indent=2)
    output.write("\n")
print(json.dumps(report, indent=2))
