"""Machine-readable CLI for local scripts and agents."""
import argparse
import json
import os
import sys
from pathlib import Path

import httpx

from .client import AxiomClient, AxiomError
from .simulation_sensor_cli import COMMANDS as SIMULATION_SENSOR_COMMANDS, register_simulation_sensor_commands, simulation_sensor_command
from .sensor_cli import COMMANDS as SENSOR_COMMANDS, register_sensor_commands, sensor_command
from .perception_cli import COMMANDS as PERCEPTION_COMMANDS, register_perception_commands, perception_command
from .dataset_cli import COMMANDS as DATASET_COMMANDS, register_dataset_commands, dataset_command
from .stack_cli import COMMANDS as STACK_COMMANDS, register_stack_commands, stack_command
from .recovery_cli import COMMANDS as RECOVERY_COMMANDS, register_recovery_commands, recovery_command


def _control_file(path):
    if path is None:
        return None
    with Path(path).open("rb") as stream:
        content = stream.read(65537)
    if len(content) > 65536:
        raise ValueError("Control selection file exceeds 64 KiB")
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("Control selection must be a JSON object with fixed revisions")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="AXIOM autonomy workspace API")
    parser.add_argument("--url", default=os.getenv("AXIOM_URL", "http://127.0.0.1:8000"))
    sub = parser.add_subparsers(dest="command", required=True)
    register_simulation_sensor_commands(sub)
    register_sensor_commands(sub)
    register_perception_commands(sub)
    register_dataset_commands(sub)
    register_stack_commands(sub)
    register_recovery_commands(sub)
    sub.add_parser("health")
    sub.add_parser("adapters")
    sub.add_parser("simulation-sensor-profiles")
    sub.add_parser("requirements")
    sub.add_parser("workers")
    sub.add_parser("execution-resources")
    sub.add_parser("suites")
    create_suite = sub.add_parser("create-suite")
    create_suite.add_argument("definition", help="Path to a JSON suite definition")
    suite = sub.add_parser("suite")
    suite.add_argument("suite_id")
    revisions = sub.add_parser("suite-revisions")
    revisions.add_argument("suite_id")
    revise = sub.add_parser("revise-suite")
    revise.add_argument("suite_id")
    revise.add_argument("definition", help="Path to the complete new JSON suite definition")
    revise.add_argument("--expected-revision", type=int, required=True)
    campaigns = sub.add_parser("campaigns")
    campaigns.add_argument("--suite-id")
    campaigns.add_argument("--limit", type=int, default=100)
    campaigns.add_argument("--before-seq", type=int)
    campaign = sub.add_parser("submit-campaign")
    campaign.add_argument("suite_id")
    campaign.add_argument("--suite-revision", type=int, required=True)
    campaign.add_argument("--label", required=True)
    campaign.add_argument("--adapter", default="reference")
    campaign.add_argument("--control", help="Bounded JSON file selecting exact stack/rig/configuration revisions for every case")
    campaign.add_argument("--idempotency-key", required=True)
    for name in ("campaign", "cancel-campaign", "wait-campaign", "export-campaign", "compare-campaign"):
        command = sub.add_parser(name)
        command.add_argument("campaign_id")
        if name == "wait-campaign":
            command.add_argument("--timeout", type=float, default=120)
        if name == "export-campaign":
            command.add_argument("destination")
        if name == "compare-campaign":
            command.add_argument("--baseline-id", required=True)
    baselines = sub.add_parser("baselines")
    baselines.add_argument("--suite-id")
    baseline = sub.add_parser("create-baseline")
    baseline.add_argument("campaign_id")
    baseline.add_argument("--name", required=True)
    baseline.add_argument("--note", required=True)
    baseline.add_argument("--idempotency-key", required=True)
    for name in ("baseline", "export-baseline"):
        command = sub.add_parser(name)
        command.add_argument("baseline_id")
        if name == "export-baseline":
            command.add_argument("destination")
    comparisons = sub.add_parser("comparisons")
    comparisons.add_argument("--suite-id")
    comparisons.add_argument("--campaign-id")
    capture = sub.add_parser("capture-comparison")
    capture.add_argument("campaign_id")
    capture.add_argument("--baseline-id", required=True)
    capture.add_argument("--name", required=True)
    capture.add_argument("--note", required=True)
    capture.add_argument("--idempotency-key", required=True)
    for name in ("comparison", "export-comparison"):
        command = sub.add_parser(name)
        command.add_argument("comparison_id")
        if name == "export-comparison":
            command.add_argument("destination")
    jobs = sub.add_parser("jobs")
    jobs.add_argument("--status")
    jobs.add_argument("--limit", type=int, default=100)
    jobs.add_argument("--before-seq", type=int)
    submit = sub.add_parser("submit-job")
    submit.add_argument("scenario_id")
    submit.add_argument("--adapter", default="reference")
    submit.add_argument("--seed", type=int)
    submit.add_argument("--idempotency-key", required=True)
    submit.add_argument("--control", help="JSON file selecting a stack, rig and exact revisions")
    job = sub.add_parser("job")
    job.add_argument("job_id")
    cancel = sub.add_parser("cancel-job")
    cancel.add_argument("job_id")
    wait = sub.add_parser("wait-job")
    wait.add_argument("job_id")
    wait.add_argument("--timeout", type=float, default=120)
    search = sub.add_parser("scenarios")
    search.add_argument("--query", default="")
    recordings = sub.add_parser("recordings")
    recordings.add_argument("--query", default="")
    ingest = sub.add_parser("import-recording")
    ingest.add_argument("path")
    ingest.add_argument("--name")
    ingest.add_argument("--source-kind", choices=["unknown", "synthetic", "simulation", "measured"], default="unknown")
    ingest.add_argument("--source", default="")
    samples = sub.add_parser("samples")
    samples.add_argument("recording_id")
    samples.add_argument("channel_id", type=int)
    sample_mode = samples.add_mutually_exclusive_group()
    sample_mode.add_argument("--at-ns")
    sample_mode.add_argument("--from-start", action="store_true")
    sample_mode.add_argument("--after-ordinal", type=int)
    samples.add_argument("--limit", type=int, default=1)
    download = sub.add_parser("download-recording")
    download.add_argument("recording_id")
    download.add_argument("destination")
    run = sub.add_parser("run")
    run.add_argument("scenario_id")
    run.add_argument("--adapter", default="reference")
    run.add_argument("--seed", type=int)
    run.add_argument("--idempotency-key")
    run.add_argument("--control", help="JSON file selecting a stack, rig and exact revisions")
    export = sub.add_parser("export")
    export.add_argument("run_id")
    export.add_argument("destination")
    args = parser.parse_args()
    try:
        with AxiomClient(args.url, token=os.getenv("ADE2E_API_TOKEN")) as client:
            if args.command in SIMULATION_SENSOR_COMMANDS:
                result = simulation_sensor_command(args, client)
            elif args.command in SENSOR_COMMANDS:
                result = sensor_command(args, client)
            elif args.command in PERCEPTION_COMMANDS:
                result = perception_command(args, client)
            elif args.command in DATASET_COMMANDS:
                result = dataset_command(args, client)
            elif args.command in STACK_COMMANDS:
                result = stack_command(args, client)
            elif args.command in RECOVERY_COMMANDS:
                result = recovery_command(args, client)
            elif args.command in {"create-suite", "revise-suite"}:
                definition = json.loads(Path(args.definition).read_text(encoding="utf-8"))
                if not isinstance(definition, dict):
                    raise ValueError("Suite definition must be a JSON object")
                if "expected_revision" in definition:
                    raise ValueError("Pass expected revision using --expected-revision")
                result = client.create_suite(**definition) if args.command == "create-suite" else client.revise_suite(
                    args.suite_id, expected_revision=args.expected_revision, **definition)
            elif args.command == "suite":
                result = client.get_suite(args.suite_id)
            elif args.command == "suite-revisions":
                result = client.suite_revisions(args.suite_id)
            elif args.command == "submit-campaign":
                result = client.submit_campaign(args.suite_id, suite_revision=args.suite_revision,
                                                label=args.label, adapter_id=args.adapter, idempotency_key=args.idempotency_key, control=_control_file(args.control))
            elif args.command == "campaigns":
                result = client.campaigns(suite_id=args.suite_id, limit=args.limit, before_seq=args.before_seq)
            elif args.command == "campaign":
                result = client.get_campaign(args.campaign_id)
            elif args.command == "cancel-campaign":
                result = client.cancel_campaign(args.campaign_id)
            elif args.command == "wait-campaign":
                result = client.wait_campaign(args.campaign_id, timeout=args.timeout)
            elif args.command == "export-campaign":
                result = {"path": str(client.export_campaign(args.campaign_id, args.destination).resolve())}
            elif args.command == "compare-campaign":
                result = client.compare_campaign(args.campaign_id, baseline_id=args.baseline_id)
            elif args.command == "baselines":
                result = client.baselines(suite_id=args.suite_id)
            elif args.command == "baseline":
                result = client.get_baseline(args.baseline_id)
            elif args.command == "create-baseline":
                result = client.create_baseline(args.campaign_id, name=args.name, note=args.note, idempotency_key=args.idempotency_key)
            elif args.command == "export-baseline":
                result = {"path": str(client.export_baseline(args.baseline_id, args.destination).resolve())}
            elif args.command == "capture-comparison":
                result = client.capture_comparison(args.campaign_id, baseline_id=args.baseline_id,
                                                   name=args.name, note=args.note, idempotency_key=args.idempotency_key)
            elif args.command == "comparison":
                result = client.get_comparison(args.comparison_id)
            elif args.command == "comparisons":
                result = client.comparisons(campaign_id=args.campaign_id, suite_id=args.suite_id)
            elif args.command == "export-comparison":
                result = {"path": str(client.export_comparison(args.comparison_id, args.destination).resolve())}
            elif args.command == "run":
                result = client.run(args.scenario_id, adapter_id=args.adapter, seed=args.seed,
                                    idempotency_key=args.idempotency_key, control=_control_file(args.control))
            elif args.command == "submit-job":
                result = client.submit_job(args.scenario_id, adapter_id=args.adapter, seed=args.seed,
                                           idempotency_key=args.idempotency_key, control=_control_file(args.control))
            elif args.command == "jobs":
                result = client.jobs(status=args.status, limit=args.limit, before_seq=args.before_seq)
            elif args.command == "job":
                result = client.get_job(args.job_id)
            elif args.command == "cancel-job":
                result = client.cancel_job(args.job_id)
            elif args.command == "wait-job":
                result = client.wait_job(args.job_id, timeout=args.timeout)
            elif args.command == "execution-resources":
                result = client.execution_resources()
            elif args.command == "simulation-sensor-profiles":
                result = client.simulation_sensor_profiles()
            elif args.command == "export":
                result = {"path": str(client.export_run(args.run_id, args.destination).resolve())}
            elif args.command == "scenarios":
                result = client.scenarios(query=args.query)
            elif args.command == "recordings":
                result = client.recordings(query=args.query)
            elif args.command == "import-recording":
                result = client.import_recording(args.path, name=args.name, source_kind=args.source_kind, source=args.source)
            elif args.command == "samples":
                result = client.recording_samples(args.recording_id, args.channel_id, at_ns=args.at_ns, limit=args.limit,
                                                  from_start=args.from_start, after_ordinal=args.after_ordinal)
            elif args.command == "download-recording":
                result = {"path": str(client.download_recording(client.get_recording(args.recording_id), args.destination).resolve())}
            else:
                result = getattr(client, args.command)()
            print(json.dumps(result, indent=2, ensure_ascii=False))
    except (AxiomError, ValueError, OSError, httpx.HTTPError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
