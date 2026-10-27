"""Machine-readable model authoring, inference and saved prediction comparisons."""
import json
from pathlib import Path


COMMANDS = {
    "models", "model", "model-revisions", "upload-model-artifact", "create-model", "revise-model",
    "export-model", "download-model", "perception-runtimes", "submit-perception-job", "perception-jobs",
    "perception-job", "wait-perception-job", "cancel-perception-job", "export-perception-job", "export-perception-image",
    "capture-perception-comparison", "perception-comparisons", "perception-comparison", "export-perception-comparison",
}


def register_perception_commands(sub):
    for name in ("models", "perception-runtimes", "perception-comparisons"):
        sub.add_parser(name)
    upload = sub.add_parser("upload-model-artifact")
    upload.add_argument("path")
    upload.add_argument("--name")
    create = sub.add_parser("create-model")
    create.add_argument("definition", help="Complete model definition JSON file; artifact_id must already exist")
    revise = sub.add_parser("revise-model")
    revise.add_argument("model_id")
    revise.add_argument("definition")
    revise.add_argument("--expected-revision", type=int, required=True)
    sub.add_parser("model-revisions").add_argument("model_id")
    for name in ("model", "export-model", "download-model"):
        command = sub.add_parser(name)
        command.add_argument("model_id")
        command.add_argument("--revision", type=int)
        if name != "model":
            command.add_argument("destination")
    submit = sub.add_parser("submit-perception-job")
    submit.add_argument("definition", help="Explicit model revision, recording/channel/ordinals and decoder JSON request")
    submit.add_argument("--idempotency-key", required=True, help="Reuse this key and exact body after a lost response")
    jobs = sub.add_parser("perception-jobs")
    jobs.add_argument("--status")
    jobs.add_argument("--limit", type=int, default=100)
    jobs.add_argument("--before-seq", type=int)
    for name in ("perception-job", "wait-perception-job", "cancel-perception-job", "export-perception-job"):
        command = sub.add_parser(name)
        command.add_argument("job_id")
        if name == "wait-perception-job":
            command.add_argument("--timeout", type=float, default=120)
        if name == "export-perception-job":
            command.add_argument("destination")
    image = sub.add_parser("export-perception-image")
    image.add_argument("job_id")
    image.add_argument("selection_index", type=int, help="Job selection index, not original file ordinal")
    image.add_argument("destination")
    comparison = sub.add_parser("capture-perception-comparison")
    comparison.add_argument("left_job_id")
    comparison.add_argument("right_job_id")
    comparison.add_argument("--name", required=True)
    comparison.add_argument("--note", required=True)
    comparison.add_argument("--idempotency-key", required=True)
    for name in ("perception-comparison", "export-perception-comparison"):
        command = sub.add_parser(name)
        command.add_argument("comparison_id")
        if name == "export-perception-comparison":
            command.add_argument("destination")


def _definition(path):
    path = Path(path)
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Perception request JSON exceeds 1 MiB")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Perception definition must be a JSON object")
    return value


def _written(path):
    return {"path": str(path.resolve())}


def perception_command(args, client):
    command = args.command
    if command == "upload-model-artifact":
        return client.upload_model_artifact(args.path, name=args.name)
    if command == "models": return client.models()
    if command == "model": return client.get_model(args.model_id, revision=args.revision)
    if command == "model-revisions": return client.model_revisions(args.model_id)
    if command in {"create-model", "revise-model"}:
        definition = _definition(args.definition)
        if "expected_revision" in definition:
            raise ValueError("Pass expected revision using --expected-revision")
        if command == "create-model": return client.create_model(**definition)
        return client.revise_model(args.model_id, expected_revision=args.expected_revision, **definition)
    if command == "export-model": return _written(client.export_model(args.model_id, args.destination, revision=args.revision))
    if command == "download-model":
        model = client.get_model(args.model_id, revision=args.revision)
        if model.get("id") != args.model_id or (args.revision is not None and model.get("revision") != args.revision):
            raise ValueError("Model revision identity mismatch")
        return _written(client.download_model_artifact(model["artifact"], args.destination))
    if command == "perception-runtimes": return client.perception_runtimes()
    if command == "submit-perception-job":
        definition = _definition(args.definition)
        required = {"label", "model_id", "model_revision", "recording_id", "channel_id", "ordinals", "decoder_id", "decoder_version"}
        allowed = required | {"runtime_id", "seed", "score_threshold", "iou_threshold", "max_detections"}
        if not required <= definition.keys() or definition.keys() - allowed:
            raise ValueError("Perception job JSON requires explicit model revision/messages/decoder; pass the key using --idempotency-key")
        return client.submit_perception_job(**definition, idempotency_key=args.idempotency_key)
    if command == "perception-jobs": return client.perception_jobs(status=args.status, limit=args.limit, before_seq=args.before_seq)
    if command == "perception-job": return client.get_perception_job(args.job_id)
    if command == "cancel-perception-job": return client.cancel_perception_job(args.job_id)
    if command == "wait-perception-job": return client.wait_perception_job(args.job_id, timeout=args.timeout)
    if command == "export-perception-job": return _written(client.export_perception_job(args.job_id, args.destination))
    if command == "export-perception-image": return _written(client.export_perception_image(args.job_id, args.selection_index, args.destination))
    if command == "capture-perception-comparison":
        return client.capture_perception_comparison(args.left_job_id, args.right_job_id,
            name=args.name, note=args.note, idempotency_key=args.idempotency_key)
    if command == "perception-comparisons": return client.perception_comparisons()
    if command == "perception-comparison": return client.get_perception_comparison(args.comparison_id)
    if command == "export-perception-comparison": return _written(client.export_perception_comparison(args.comparison_id, args.destination))
    raise ValueError(f"Unknown perception command: {command}")
