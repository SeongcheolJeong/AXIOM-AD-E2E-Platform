"""CLI commands for versioned sensor rigs and exact recorded observations."""
import json
from pathlib import Path


COMMANDS = {
    "sensor-decoders", "sensor-rigs", "sensor-rig", "sensor-rig-revisions",
    "create-sensor-rig", "revise-sensor-rig", "sensor-channels", "bind-recording-sensors",
    "sensor-binding", "sensor-binding-revisions", "sensor-frame", "sensor-bundle",
    "export-sensor-preview", "import-run-recording", "recording-origin", "export-sensor-rig", "export-sensor-binding",
}


def register_sensor_commands(sub):
    sub.add_parser("sensor-decoders")
    sub.add_parser("sensor-rigs")
    for name in ("sensor-rig", "sensor-rig-revisions"):
        sub.add_parser(name).add_argument("rig_id")
    for name, identity in (("export-sensor-rig", "rig_id"), ("export-sensor-binding", "recording_id")):
        export = sub.add_parser(name)
        export.add_argument(identity)
        export.add_argument("destination")
        export.add_argument("--revision", type=int)
    create = sub.add_parser("create-sensor-rig")
    create.add_argument("definition", help="Complete rig definition JSON file")
    revise = sub.add_parser("revise-sensor-rig")
    revise.add_argument("rig_id")
    revise.add_argument("definition")
    revise.add_argument("--expected-revision", type=int, required=True)
    for name in ("sensor-channels", "sensor-binding-revisions", "recording-origin"):
        sub.add_parser(name).add_argument("recording_id")
    binding = sub.add_parser("sensor-binding")
    binding.add_argument("recording_id")
    binding.add_argument("--revision", type=int)
    bind = sub.add_parser("bind-recording-sensors")
    bind.add_argument("recording_id")
    bind.add_argument("definition", help="Complete binding JSON file with explicit rig revision and channels")
    bind.add_argument("--expected-revision", type=int, required=True, help="0 for the first binding")
    for name in ("sensor-frame", "export-sensor-preview"):
        frame = sub.add_parser(name)
        frame.add_argument("recording_id")
        frame.add_argument("channel_id", type=int)
        frame.add_argument("ordinal", type=int)
        frame.add_argument("--max-points", type=int, default=20000)
        if name == "export-sensor-preview":
            frame.add_argument("destination")
            frame.add_argument("--component", choices=("camera", "points", "intensity"), default="camera")
    bundle = sub.add_parser("sensor-bundle")
    bundle.add_argument("recording_id")
    bundle.add_argument("--at-ns", required=True)
    bundle.add_argument("--binding-revision", type=int)
    bundle.add_argument("--channel-id", type=int, action="append")
    bundle.add_argument("--max-age-ns")
    bundle.add_argument("--max-points", type=int, default=20000)
    origin = sub.add_parser("import-run-recording")
    origin.add_argument("run_id")
    origin.add_argument("artifact_id")


def sensor_command(args, client):
    command = args.command
    if command in {"create-sensor-rig", "revise-sensor-rig", "bind-recording-sensors"}:
        definition = json.loads(Path(args.definition).read_text(encoding="utf-8"))
        if not isinstance(definition, dict):
            raise ValueError("Sensor definition must be a JSON object")
        if "expected_revision" in definition:
            raise ValueError("Pass expected revision using --expected-revision")
        if command == "create-sensor-rig":
            return client.create_sensor_rig(**definition)
        if command == "revise-sensor-rig":
            return client.revise_sensor_rig(args.rig_id, expected_revision=args.expected_revision, **definition)
        return client.bind_recording_sensors(args.recording_id, expected_revision=args.expected_revision, **definition)
    if command == "sensor-decoders": return client.sensor_decoders()
    if command == "sensor-rigs": return client.sensor_rigs()
    if command == "sensor-rig": return client.get_sensor_rig(args.rig_id)
    if command == "sensor-rig-revisions": return client.sensor_rig_revisions(args.rig_id)
    if command == "sensor-channels": return client.sensor_channels(args.recording_id)
    if command == "sensor-binding": return client.sensor_binding(args.recording_id, revision=args.revision)
    if command == "sensor-binding-revisions": return client.sensor_binding_revisions(args.recording_id)
    if command == "recording-origin": return client.recording_origin(args.recording_id)
    if command == "export-sensor-rig": return {"path": str(client.export_sensor_rig(args.rig_id, args.destination, revision=args.revision).resolve())}
    if command == "export-sensor-binding": return {"path": str(client.export_sensor_binding(args.recording_id, args.destination, revision=args.revision).resolve())}
    if command == "import-run-recording": return client.import_run_recording(args.run_id, args.artifact_id)
    if command == "sensor-frame": return client.sensor_frame(args.recording_id, args.channel_id, args.ordinal, max_points=args.max_points)
    if command == "sensor-bundle":
        return client.sensor_bundle(args.recording_id, at_ns=args.at_ns, binding_revision=args.binding_revision,
                                    channel_ids=args.channel_id, max_age_ns=args.max_age_ns, max_points=args.max_points)
    if command == "export-sensor-preview":
        return {"path": str(client.export_sensor_preview(args.recording_id, args.channel_id, args.ordinal, args.destination,
                                                         component=args.component, max_points=args.max_points).resolve())}
    raise ValueError(f"Unknown sensor command: {command}")
