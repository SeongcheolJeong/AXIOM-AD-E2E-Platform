"""Prepare a small original nuScenes RGB + annotation recording for Log Sim.

Reads an already approved research input and existing local/SSH dataset only.
No dataset acquisition, GPU inference, original extraction, or source edits.
The output must be new; original camera timestamps and JPEG bytes are retained.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import inspect
import io
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MAX_TRANSFER_BYTES = 64 * 1024 * 1024
MAX_CAMERA_BYTES = 8 * 1024 * 1024
CAMERA_SCHEMA = {
    "title": "foxglove.CompressedImage", "type": "object",
    "properties": {"timestamp": {"type": "object"}, "frame_id": {"type": "string"},
                   "format": {"type": "string"},
                   "data": {"type": "string", "contentEncoding": "base64"}},
    "required": ["timestamp", "frame_id", "format", "data"],
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def collect_original(request):
    """Self-contained read-only helper, also streamed to the existing SSH host."""
    import base64
    import hashlib
    import json
    from pathlib import Path
    import sys
    import types

    def load(name, source):
        module = types.ModuleType(name)
        sys.modules[name] = module
        exec(compile(source, name, "exec"), module.__dict__)
        return module

    package = types.ModuleType("ade2e")
    package.__path__ = []
    sys.modules["ade2e"] = package
    load("ade2e.driving_contract", request["contract_source"])
    driving = load("log_sim_original_driving", request["driving_source"])
    converter = load("log_sim_original_converter", request["converter_source"])
    root = Path(request["data_root"])
    approved = request["approved_input"]
    if (approved["dataset"]["use_scope"] != "research_isolated"
            or approved["dataset"]["version"] != "v1.0-mini"):
        raise ValueError("An existing approved isolated mini research input is required")
    count = request["frame_count"]
    if type(count) is not int or not 3 <= count <= 16:
        raise ValueError("Select 3–16 original contiguous keyframes")
    samples = {row["token"]: row for row in json.loads((root / "v1.0-mini/sample.json").read_text())}
    current = samples[approved["frames"][0]["sample_token"]]
    for _ in range(count - 1):
        if not current["next"]:
            raise ValueError("Requested prefix exceeds the original scene")
        current = samples[current["next"]]
    manifest = driving.export_input(root, approved["dataset"]["scene_token"], current["token"],
                                    frame_count=count, rights_note=approved["dataset"]["rights_note"])
    if manifest["dataset"]["metadata_sha256"] != approved["dataset"]["metadata_sha256"]:
        raise ValueError("Existing original metadata changed from the approved research input")
    bundle = converter.collect_source_bundle(root, request["archive"], converter.canonical(manifest),
        expected_archive_sha256=request["archive_sha256"],
        converter_source_sha256=hashlib.sha256(request["converter_source"].encode()).hexdigest(),
        ego_geometry={"length_m": 4.5, "width_m": 1.9, "origin_offset_xy_m": [0., 0.],
                      "provenance": "authored_reference_footprint", "original_measured_geometry_available": False},
        compact=True)
    _, lineage = converter.convert_bundle(bundle)
    # Ordinary MCAP text fields are bounded independently of the converter.
    if len(converter.canonical(converter.compact_lineage(lineage))) > 1024 * 1024:
        raise ValueError("Original lineage exceeds the current MCAP text bound; select fewer frames")
    cameras = []
    for frame in manifest["frames"]:
        original = next(camera for camera in frame["cameras"] if camera["name"] == "CAM_FRONT")
        path = driving.bounded_file(root, original["path"])
        if path.is_symlink() or path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("Original camera image is not an accepted bounded regular file")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != original["sha256"]:
            raise ValueError("Original image changed during source preparation")
        cameras.append({**original, "sample_token": frame["sample_token"],
                        "data_base64": base64.b64encode(raw).decode()})
    return {"bundle": bundle, "cameras": cameras,
            "approved_input_sha256": request["approved_input_sha256"],
            "collector_source_sha256": request["collector_source_sha256"],
            "selected_frame_count": count}


def add_original_cameras(planning_mcap: bytes, cameras: list[dict]):
    """Copy exact planning messages/metadata and add original image messages."""
    from mcap.reader import make_reader
    from mcap.writer import CompressionType, Writer
    from PIL import Image

    reader = make_reader(io.BytesIO(planning_mcap))
    summary = reader.get_summary()
    output = io.BytesIO()
    writer = Writer(output, compression=CompressionType.ZSTD, enable_crcs=True, enable_data_crcs=True)
    writer.start(profile="axiom-log-sim-road/1", library="axiom-log-sim-road/1")
    schemas = {identity: writer.register_schema(value.name, value.encoding, value.data)
               for identity, value in summary.schemas.items()}
    channels = {identity: writer.register_channel(value.topic, value.message_encoding,
                                                  schemas.get(value.schema_id, 0), value.metadata)
                for identity, value in summary.channels.items()}
    for metadata in reader.iter_metadata():
        writer.add_metadata(metadata.name, metadata.metadata)
    image_schema = writer.register_schema("foxglove.CompressedImage", "jsonschema", canonical(CAMERA_SCHEMA))
    image_channel = writer.register_channel("/camera/CAM_FRONT/image", "json", image_schema,
        {"source": "original nuScenes CAM_FRONT JPEG", "clock": "original_camera_capture_unix_ns",
         "transport_timestamp": "unavailable_copied_capture_time", "reactive": "false"})
    messages = [(message.log_time, channels[channel.id], message.data, message.publish_time, message.sequence)
                for _, channel, message in reader.iter_messages(log_time_order=False)]
    receipts = []
    previous = -1
    for index, camera in enumerate(cameras):
        raw = base64.b64decode(camera["data_base64"], validate=True)
        if not 0 < len(raw) <= MAX_CAMERA_BYTES or sha(raw) != camera["sha256"]:
            raise ValueError("Original camera bytes differ from their retained source hash")
        at = camera["timestamp_us"] * 1000
        if type(camera["timestamp_us"]) is not int or not previous < at < 2**64:
            raise ValueError("Camera capture timestamps must retain their original increasing order")
        previous = at
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != "JPEG" or image.size != (camera["width"], camera["height"]):
                raise ValueError("Original camera shape/encoding differs")
            image.verify()
        body = {"timestamp": {"sec": at // 10**9, "nsec": at % 10**9},
                "frame_id": "CAM_FRONT_optical", "format": "jpeg", "data": camera["data_base64"]}
        messages.append((at, image_channel, canonical(body), at, index))
        receipts.append({key: value for key, value in camera.items() if key != "data_base64"})
    camera_lineage = {"schema_version": "axiom.log-sim-camera-source/1", "commercial_use": False,
                      "use_scope": "isolated_noncommercial_research", "images": receipts,
                      "capture_time_policy": "original_camera_capture_time_no_resampling",
                      "transport_time_policy": "unavailable_copied_capture_time"}
    writer.add_metadata("axiom.log_sim_camera_source", {"lineage_json": canonical(camera_lineage).decode()})
    ordinals = {identity: [] for identity in channels.values()}
    ordinals[image_channel] = []
    for ordinal, (at, channel, data, publish, sequence) in enumerate(sorted(messages, key=lambda value: (value[0], value[1]))):
        writer.add_message(channel, at, data, publish, sequence)
        ordinals[channel].append(ordinal)
    writer.finish()
    raw = output.getvalue()
    if len(raw) > 128 * 1024 * 1024:
        raise ValueError("Selected recording exceeds ordinary import bound")
    return raw, {"sha256": sha(raw), "size_bytes": len(raw), "camera_channel_id": image_channel,
                 "channel_ordinals": {str(key): value for key, value in ordinals.items()},
                 "camera_lineage": camera_lineage}


def prepare(request, output: Path, *, ssh: str | None = None):
    if output.exists():
        raise ValueError("Output must be a new directory; existing evidence is not overwritten")
    if ssh:
        if ssh.startswith("-") or any(character.isspace() for character in ssh):
            raise ValueError("Use an explicit existing SSH destination")
        program = inspect.getsource(collect_original) + "\nimport json,sys\nprint(json.dumps(collect_original(json.load(sys.stdin)),separators=(',',':')))\n"
        import shlex
        result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", ssh,
                                 "python3 -c " + shlex.quote(program)], input=canonical(request),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
        if result.returncode:
            raise RuntimeError("Read-only source collection failed: " + result.stderr.decode(errors="replace")[-1800:])
        if len(result.stdout) > MAX_TRANSFER_BYTES:
            raise ValueError("Selected source transfer exceeds its byte bound")
        collected = json.loads(result.stdout)
    else:
        collected = collect_original(request)
    return write_collected(collected, output)


def write_collected(collected: dict, output: Path):
    """Write a selected road clip from original collected rows and JPEG bytes."""
    if output.exists():
        raise ValueError("Output must be a new directory; existing evidence is not overwritten")
    from axiom_nuscenes_planning.__main__ import write_outputs
    proposal = write_outputs(collected["bundle"], output / "planning")
    raw, receipt = add_original_cameras((output / "planning/planning-log.mcap").read_bytes(), collected["cameras"])
    # Validate against the real bounded importer before handing this to the UI.
    from ade2e.mcap_ingest import parse_mcap
    with io.BytesIO() as payloads:
        parse_mcap(raw, payloads)
    (output / "road-log.mcap").write_bytes(raw)
    (output / "selected-driving-input.json").write_bytes(canonical(collected["bundle"]["original_driving_input"]))
    proposal["schema_version"] = "log-sim-road-import-proposal/1"
    proposal["mcap"] = {**proposal["mcap"], **receipt}
    proposal["sequence_input"]["ego_ordinals"] = receipt["channel_ordinals"][str(proposal["mcap"]["ego_channel_id"])]
    scene_name = collected['bundle']['original_scene']['name']
    proposal["recording"]["name"] = f"nuScenes {scene_name} · original front RGB and annotations · research"
    proposal["road_source"] = {"kind": "nuscenes", "title": f"nuScenes {scene_name} · 실도로 조사",
        "ego_channel_id": proposal["mcap"]["ego_channel_id"],
        "objects_channel_id": proposal["mcap"]["object_channel_id"], "camera_channel_id": receipt["camera_channel_id"],
        "source_scope": "isolated_noncommercial_research"}
    proposal["preparation"] = {key: collected[key] for key in
                              ("approved_input_sha256", "collector_source_sha256", "selected_frame_count")}
    (output / "import-proposal.json").write_bytes(canonical(proposal))
    return {"mcap": str(output / "road-log.mcap"), "sha256": receipt["sha256"],
            "size_bytes": len(raw), "frame_count": collected["selected_frame_count"],
            "camera_count": len(collected["cameras"]),
            "duration_s": (collected["bundle"]["frames"][-1]["sample"]["timestamp"]
                           - collected["bundle"]["frames"][0]["sample"]["timestamp"]) / 1e6,
            "proposal": str(output / "import-proposal.json")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--approved-input", type=Path, required=True)
    parser.add_argument("--frame-count", type=int, default=10,
                        help="Selected original prefix (3–16); scene-0061 first ten fit the 128-object contract")
    parser.add_argument("--ssh", help="Read existing originals over this authenticated SSH destination")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for path in (ROOT / "backend/src", ROOT / "integrations/nuscenes/src"):
        sys.path.insert(0, str(path))
    approved_raw = args.approved_input.read_bytes()
    request = {"data_root": args.data_root, "archive": args.archive, "archive_sha256": args.archive_sha256,
        "approved_input": json.loads(approved_raw), "approved_input_sha256": sha(approved_raw),
        "frame_count": args.frame_count, "collector_source_sha256": sha(Path(__file__).read_bytes()),
        "contract_source": (ROOT / "backend/src/ade2e/driving_contract.py").read_text(),
        "driving_source": (ROOT / "integrations/vad/src/axiom_vad/nuscenes_input.py").read_text(),
        "converter_source": (ROOT / "integrations/nuscenes/src/axiom_nuscenes_planning/converter.py").read_text()}
    print(json.dumps(prepare(request, args.output, ssh=args.ssh), indent=2))


if __name__ == "__main__":
    main()
