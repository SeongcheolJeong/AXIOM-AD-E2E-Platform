"""Explicit dataset authoring and saved detection-evaluation CLI commands."""
import json
from pathlib import Path

COMMANDS = {"capture-dataset-image", "dataset-image", "export-dataset-image", "datasets", "dataset",
            "dataset-revisions", "create-dataset", "revise-dataset", "export-dataset", "detection-evaluators",
            "submit-detection-evaluation", "detection-evaluations", "detection-evaluation",
            "wait-detection-evaluation", "cancel-detection-evaluation", "export-detection-evaluation"}


def register_dataset_commands(sub):
    for name in ("datasets", "detection-evaluators"):
        sub.add_parser(name)
    capture = sub.add_parser("capture-dataset-image")
    capture.add_argument("recording_id")
    capture.add_argument("channel_id", type=int)
    capture.add_argument("ordinal", type=int)
    capture.add_argument("--decoder-id", required=True)
    capture.add_argument("--decoder-version", required=True)
    for name in ("dataset-image", "export-dataset-image"):
        command = sub.add_parser(name)
        command.add_argument("image_id")
        if name.startswith("export-"):
            command.add_argument("destination")
    create = sub.add_parser("create-dataset")
    create.add_argument("definition", help="Complete dataset JSON; review declarations remain explicit")
    revise = sub.add_parser("revise-dataset")
    revise.add_argument("dataset_id")
    revise.add_argument("definition")
    revise.add_argument("--expected-revision", type=int, required=True)
    sub.add_parser("dataset-revisions").add_argument("dataset_id")
    for name in ("dataset", "export-dataset"):
        command = sub.add_parser(name)
        command.add_argument("dataset_id")
        command.add_argument("--revision", type=int)
        if name.startswith("export-"):
            command.add_argument("destination")
    submit = sub.add_parser("submit-detection-evaluation")
    submit.add_argument("definition", help="Explicit dataset revision/split, source job IDs and class mapping JSON")
    submit.add_argument("--idempotency-key", required=True, help="Reuse this exact key and body after a lost response")
    listing = sub.add_parser("detection-evaluations")
    listing.add_argument("--status")
    listing.add_argument("--limit", type=int, default=100)
    listing.add_argument("--before-seq", type=int)
    for name in ("detection-evaluation", "wait-detection-evaluation", "cancel-detection-evaluation", "export-detection-evaluation"):
        command = sub.add_parser(name)
        command.add_argument("evaluation_id")
        if name.startswith("wait-"):
            command.add_argument("--timeout", type=float, default=180)
        if name.startswith("export-"):
            command.add_argument("destination")


def _definition(path):
    with Path(path).open("rb") as stream:
        data = stream.read(16 * 1024 * 1024 + 1)
    if len(data) > 16 * 1024 * 1024:
        raise ValueError("Dataset request JSON exceeds 16 MiB")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Dataset request must be a JSON object")
    return value


def _written(path):
    return {"path": str(path.resolve())}


def dataset_command(args, client):
    command = args.command
    if command == "capture-dataset-image":
        return client.capture_dataset_image(args.recording_id, args.channel_id, args.ordinal,
            decoder_id=args.decoder_id, decoder_version=args.decoder_version)
    if command == "dataset-image": return client.get_dataset_image(args.image_id)
    if command == "export-dataset-image": return _written(client.export_dataset_image(args.image_id, args.destination))
    if command == "datasets": return client.datasets()
    if command == "dataset": return client.get_dataset(args.dataset_id, revision=args.revision)
    if command == "dataset-revisions": return client.dataset_revisions(args.dataset_id)
    if command == "export-dataset": return _written(client.export_dataset(args.dataset_id, args.destination, revision=args.revision))
    if command in {"create-dataset", "revise-dataset"}:
        definition = _definition(args.definition)
        if "expected_revision" in definition:
            raise ValueError("Pass expected revision using --expected-revision")
        if command == "create-dataset": return client.create_dataset(**definition)
        return client.revise_dataset(args.dataset_id, expected_revision=args.expected_revision, **definition)
    if command == "detection-evaluators": return client.detection_evaluators()
    if command == "submit-detection-evaluation":
        definition = _definition(args.definition)
        required = {"name", "note", "dataset_id", "dataset_revision", "split", "inference_job_ids", "class_mapping"}
        if not required <= definition.keys() or definition.keys() - required - {"evaluator_id", "score_threshold", "diagnostic_iou"}:
            raise ValueError("Evaluation JSON needs dataset revision/split, source jobs and mapping; pass key using --idempotency-key")
        return client.submit_detection_evaluation(**definition, idempotency_key=args.idempotency_key)
    if command == "detection-evaluations": return client.detection_evaluations(status=args.status, limit=args.limit, before_seq=args.before_seq)
    if command == "detection-evaluation": return client.get_detection_evaluation(args.evaluation_id)
    if command == "wait-detection-evaluation": return client.wait_detection_evaluation(args.evaluation_id, timeout=args.timeout)
    if command == "cancel-detection-evaluation": return client.cancel_detection_evaluation(args.evaluation_id)
    if command == "export-detection-evaluation": return _written(client.export_detection_evaluation(args.evaluation_id, args.destination))
    raise ValueError(f"Unknown dataset command: {command}")
