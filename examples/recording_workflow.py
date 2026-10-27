"""Generate/import/seek/download an original synthetic recording through the SDK."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from axiom_sdk import AxiomClient
from generate_recording import BASE_NS, generate

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--url", default=os.getenv("AXIOM_URL", "http://127.0.0.1:8000"))
args = parser.parse_args()
with TemporaryDirectory(prefix="axiom-recording-example-") as directory:
    root = Path(directory)
    source = root / "synthetic-telemetry.mcap"
    generate(source)
    with AxiomClient(args.url, token=os.getenv("ADE2E_API_TOKEN")) as client:
        recording = client.import_recording(source, name="Synthetic telemetry · exact clock demo",
                                            source_kind="synthetic", source="Original examples/generate_recording.py")
        repeated = client.import_recording(source, name=recording["name"], source_kind="synthetic",
                                           source="Original examples/generate_recording.py")
        assert repeated["id"] == recording["id"]
        telemetry = next(channel for channel in recording["channels"] if channel["topic"] == "/vehicle/telemetry")
        sample = client.recording_samples(recording["id"], telemetry["id"], at_ns=BASE_NS + 8_050_000_000)
        assert sample["samples"][0]["log_time_ns"] == str(BASE_NS + 8_000_000_000)
        assert sample["samples"][0]["decode_status"] == "decoded"
        messages = list(client.iter_recording_samples(recording["id"], telemetry["id"]))
        assert len(messages) == telemetry["message_count"]
        assert len({message["ordinal"] for message in messages}) == len(messages)
        assert [int(message["log_time_ns"]) for message in messages] == sorted(int(message["log_time_ns"]) for message in messages)
        downloaded = client.download_recording(recording, root / "downloaded.mcap")
        assert downloaded.read_bytes() == source.read_bytes()
        assert recording["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
        print(json.dumps({"recording_id": recording["id"], "messages": recording["message_count"],
                          "channels": recording["channel_count"], "exact_seek": sample["samples"][0],
                          "duplicate_import_preserved_id": True, "complete_channel_traversal": len(messages),
                          "original_download_verified": True}, indent=2))
