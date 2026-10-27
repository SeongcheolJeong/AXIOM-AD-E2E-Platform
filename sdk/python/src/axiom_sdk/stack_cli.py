"""Explicit stack revisions and recorded open-loop replay commands."""
from .dataset_cli import _definition, _written

COMMANDS = {"stack-components", "stacks", "stack", "stack-revisions", "create-stack", "revise-stack",
            "export-stack", "stack-replay-sources", "submit-stack-replay", "stack-replays", "stack-replay",
            "wait-stack-replay", "cancel-stack-replay", "export-stack-replay", "export-stack-replay-points"}


def register_stack_commands(sub):
    for name in ("stack-components", "stacks"):
        sub.add_parser(name)
    sub.add_parser("create-stack").add_argument("definition", help="Complete component stack JSON")
    revise = sub.add_parser("revise-stack")
    revise.add_argument("stack_id")
    revise.add_argument("definition")
    revise.add_argument("--expected-revision", type=int, required=True)
    sub.add_parser("stack-revisions").add_argument("stack_id")
    for name in ("stack", "export-stack"):
        command = sub.add_parser(name)
        command.add_argument("stack_id")
        command.add_argument("--revision", type=int)
        if name.startswith("export-"): command.add_argument("destination")
    sub.add_parser("stack-replay-sources").add_argument("recording_id")
    submit = sub.add_parser("submit-stack-replay", help="Calculate commands; no vehicle motion is changed")
    submit.add_argument("definition", help="Pinned stack, recording binding and explicit input ordinals JSON")
    submit.add_argument("--idempotency-key", required=True)
    listing = sub.add_parser("stack-replays")
    listing.add_argument("--status")
    listing.add_argument("--limit", type=int, default=100)
    listing.add_argument("--before-seq", type=int)
    for name in ("stack-replay", "wait-stack-replay", "cancel-stack-replay", "export-stack-replay", "export-stack-replay-points"):
        command = sub.add_parser(name)
        command.add_argument("replay_id")
        if name == "wait-stack-replay": command.add_argument("--timeout", type=float, default=180)
        if name == "export-stack-replay-points": command.add_argument("tick_index", type=int)
        if name.startswith("export-"): command.add_argument("destination")


def stack_command(args, client):
    command = args.command
    if command == "stack-components": return client.stack_components()
    if command == "stacks": return client.stacks()
    if command == "stack": return client.get_stack(args.stack_id, revision=args.revision)
    if command == "stack-revisions": return client.stack_revisions(args.stack_id)
    if command == "export-stack": return _written(client.export_stack(args.stack_id, args.destination, revision=args.revision))
    if command in {"create-stack", "revise-stack"}:
        definition = _definition(args.definition)
        if "expected_revision" in definition:
            raise ValueError("Pass expected revision using --expected-revision")
        if command == "create-stack": return client.create_stack(**definition)
        return client.revise_stack(args.stack_id, expected_revision=args.expected_revision, **definition)
    if command == "stack-replay-sources": return client.stack_replay_sources(args.recording_id)
    if command == "submit-stack-replay":
        definition = _definition(args.definition)
        required = {"label", "stack_id", "stack_revision", "recording_id", "binding_revision", "lidar_channel_id", "ego_channel_id", "ego_ordinals"}
        if not required <= definition.keys() or definition.keys() - required - {"ego_decoder_id", "ego_decoder_version", "ego_log_time_offset_ns"}:
            raise ValueError("Replay JSON needs stack and binding revisions, role channels and ego ordinals; pass key using --idempotency-key")
        return client.submit_stack_replay(**definition, idempotency_key=args.idempotency_key)
    if command == "stack-replays": return client.stack_replays(status=args.status, limit=args.limit, before_seq=args.before_seq)
    if command == "stack-replay": return client.get_stack_replay(args.replay_id)
    if command == "wait-stack-replay": return client.wait_stack_replay(args.replay_id, timeout=args.timeout)
    if command == "cancel-stack-replay": return client.cancel_stack_replay(args.replay_id)
    if command == "export-stack-replay": return _written(client.export_stack_replay(args.replay_id, args.destination))
    if command == "export-stack-replay-points":
        return _written(client.export_stack_replay_points(args.replay_id, args.tick_index, args.destination))
    raise ValueError(f"Unknown stack command: {command}")
