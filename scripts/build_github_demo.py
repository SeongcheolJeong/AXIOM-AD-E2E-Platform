#!/usr/bin/env python3
"""Build, verify and copy small, original reference workspaces for GitHub.

No operational database, network service, optional simulator or private dataset
is read. Builds use the normal API and CPU reference implementations. Stored
IDs/timestamps are real build receipts; deterministic inputs and outputs do not
imply byte-identical SQLite files across builds.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

SAMPLES = ("object-reference", "log-open-loop", "log-feedback")
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "examples" / "github-demo"
SCOPE = "Original authored synthetic CPU reference; no CARLA, measured road data, sensor validation or safety certification."


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


@contextmanager
def reference_client(directory: Path):
    # Importing the API must not construct its module-level default workspace.
    os.environ["AXIOM_APP_FACTORY_ONLY"] = "1"
    from fastapi.testclient import TestClient
    from ade2e.adapters import Registry
    from ade2e.database import Settings
    from ade2e.evaluator_runtime import EvaluatorRegistry
    from ade2e.experiment_runtime import ExperimentRegistry
    from ade2e.inference_runtime import RuntimeRegistry
    from ade2e.main import create_app
    from ade2e.planning_replay import PlanningReplayHandler
    from ade2e.stack_runtime import StackRegistry

    registry = ExperimentRegistry(load_plugins=False)
    app = create_app(
        Settings(data_dir=directory, database_url=f"sqlite:///{directory.resolve() / 'platform.db'}",
                 seed_examples=False, api_token=None, team_mode=False),
        Registry(load_plugins=False), runtime_registry=RuntimeRegistry(load_plugins=False),
        evaluator_registry=EvaluatorRegistry(load_plugins=False),
        stack_registry=StackRegistry(load_plugins=False), experiment_registry=registry,
    )
    registry.register(PlanningReplayHandler(app.state.db))
    with TestClient(app) as client:
        yield client


def request(client, method, route, status=200, **kwargs):
    response = client.request(method, "/api/v1" + route, **kwargs)
    if response.status_code != status:
        raise ValueError(f"{method} {route}: {response.status_code}: {response.text[:2000]}")
    return response.json()


def object_sample(client):
    runs = []
    definitions = [
        dict(name="Synthetic · generous gap · expected pass", category="cut_in", parameters={
            "ego_speed_mps": 18, "target_speed_mps": 16, "initial_gap_m": 55,
            "trigger_time_s": 2, "friction": .85, "response_delay_s": .25}),
        dict(name="Synthetic · late cut-in · expected fail", category="cut_in", parameters={
            "ego_speed_mps": 24, "target_speed_mps": 8, "initial_gap_m": 16,
            "trigger_time_s": .5, "friction": .35, "response_delay_s": 1.6}),
    ]
    for index, definition in enumerate(definitions):
        scenario = request(client, "POST", "/scenarios", 201, json={
            **definition, "description": SCOPE, "tags": ["github-demo", "synthetic", "reference"],
            "seed": 42, "duration_s": 8,
        })
        run = request(client, "POST", "/runs", 201, json={"scenario_id": scenario["id"], "adapter_id": "reference"},
                      headers={"Idempotency-Key": f"github-demo-object-{index}"})
        runs.append({"id": run["id"], "scenario_id": scenario["id"], "scenario_revision": 1,
                     "verdict": run["verdict"], "output_hash": run["provenance"]["output_hash"],
                     "frame_count": len(run["trajectory"]), "ui": f"#runs?run={run['id']}"})
    if [run["verdict"] for run in runs] != ["passed", "failed"]:
        raise ValueError("Reference pass/fail example no longer matches its fixed expectations")
    request(client, "POST", "/requirements", 201, json={
        "name": "Illustrative cut-in TTC threshold", "description": SCOPE,
        "metric_name": "min_ttc_s", "operator": "gte", "threshold": 1,
        "scenario_ids": [run["scenario_id"] for run in runs],
    })
    return {"title": "Object reference · retained pass and fail", "runs": runs,
            "executions": [], "ui": [run["ui"] for run in runs] + ["#validation", "#scenarios"]}


def authored_mcap():
    """41 exact original authored object/ego observations, no camera frames."""
    from mcap.writer import Writer, CompressionType
    from ade2e.scene_sequences import EGO_SCHEMA, EGO_SCHEMA_NAME, OBJECT_SCHEMA, OBJECT_SCHEMA_NAME
    content = io.BytesIO()
    writer = Writer(content, compression=CompressionType.ZSTD, enable_crcs=True, enable_data_crcs=True)
    writer.start(library="axiom-github-demo/1")
    ego_schema = writer.register_schema(EGO_SCHEMA_NAME, "jsonschema", json.dumps(EGO_SCHEMA, sort_keys=True).encode())
    object_schema = writer.register_schema(OBJECT_SCHEMA_NAME, "jsonschema", json.dumps(OBJECT_SCHEMA, sort_keys=True).encode())
    ego_channel = writer.register_channel("/authored/ego", "json", ego_schema)
    object_channel = writer.register_channel("/authored/objects", "json", object_schema)
    ordinals = []
    for i in range(41):
        at = 1_000_000_000 + i * 100_000_000
        ego = dict(timestamp_ns=str(at), frame_id="map", x_m=i * .6, y_m=0., yaw_rad=0.,
                   speed_mps=6., length_m=4., width_m=2., source="original-authored-synthetic")
        objects = dict(timestamp_ns=str(at), frame_id="map", coordinate_frame="world",
                       velocity_semantics="absolute_ground_velocity", source="original-authored-synthetic",
                       objects=[dict(id="lead", x_m=22., y_m=0., yaw_rad=0., length_m=4., width_m=2., vx_mps=0., vy_mps=0.)])
        writer.add_message(object_channel, at, json.dumps(objects, sort_keys=True).encode(), at)
        writer.add_message(ego_channel, at, json.dumps(ego, sort_keys=True).encode(), at)
        ordinals.append(2 * i + 1)
    writer.finish()
    return content.getvalue(), ego_channel, object_channel, ordinals


def log_sample(client, sample):
    from ade2e.worker import run_once
    content, ego_channel, object_channel, ordinals = authored_mcap()
    recording = request(client, "POST", "/recordings?name=Original-authored-synthetic-planning-log&source_kind=synthetic", 201,
                        content=content, headers={"Content-Type": "application/octet-stream"})
    sequence = request(client, "POST", "/scene-sequences", 201, json={
        "name": "Synthetic stationary lead · 41 observations", "project_id": "default",
        "source_type": "recording", "world_frame": "map", "clock_domain": "authored-100ms-clock",
        "note": SCOPE, "recording_id": recording["id"], "ego_channel_id": ego_channel,
        "object_channel_id": object_channel, "ego_ordinals": ordinals,
    })
    case = request(client, "POST", "/cases", 201, json={
        "title": "Synthetic approach · investigate the last second", "note": SCOPE,
        "source": {"kind": "recording", "recording_id": recording["id"], "channel_id": ego_channel,
                   "start_time_ns": "4000000000", "end_time_ns": "5000000000"},
    }, headers={"Idempotency-Key": "github-demo-case"})
    modes = [("original_replay", 5.), ("open_loop", 5.)] if sample == "log-open-loop" else [
        ("planning_closed_loop", .8), ("planning_closed_loop", 6.)]
    executions = []
    for index, (mode, max_deceleration) in enumerate(modes):
        label = mode if sample == "log-open-loop" else f"feedback · max deceleration {max_deceleration:g} m/s²"
        spec = request(client, "POST", "/replay-specs", 201, json={
            "name": f"Synthetic · {label}", "project_id": "default", "sequence_id": sequence["id"],
            "sequence_sha256": sequence["sha256"], "mode": mode,
            "controller": {"cruise_speed_mps": 6., "max_deceleration_mps2": max_deceleration},
            "observers": ["collision", "distance", "ttc", "speed", "acceleration", "jerk",
                          "trajectory_error", "lane_departure", "goal_arrival"],
            "note": SCOPE + " Lane and goal observers deliberately remain unevaluated without lane/goal evidence.",
        })
        preflight = request(client, "POST", f"/replay-specs/{spec['id']}/preflight")
        if not preflight["executable"]:
            raise ValueError(f"Authored replay did not pass its input preflight: {preflight}")
        body = {"name": f"Synthetic · {label}", "kind": "planning_replay", "adapter_id": "reference-planning-replay",
                "seed": 42, "input": {"replay_spec_id": spec["id"]}}
        preview = request(client, "POST", "/experiments/preview", json=body)
        experiment = request(client, "POST", "/experiments", 201, json=body,
                             headers={"Idempotency-Key": f"github-demo-experiment-{index}"})
        if preview["preview_hash"] != experiment["preview_hash"]:
            raise ValueError("Experiment input changed between preview and save")
        job = request(client, "POST", f"/experiments/{experiment['id']}/executions", 202,
                      json={"preview_hash": experiment["preview_hash"]},
                      headers={"Idempotency-Key": f"github-demo-execute-{index}"})
        app = client.app
        if not run_once(app.state.db, app.state.registry, "github-demo-reference-builder", job_id=job["id"],
                        experiment_registry=app.state.experiment_registry):
            raise ValueError("Reference worker did not claim the submitted job")
        execution = request(client, "GET", f"/experiments/executions/{job['id']}")
        if execution["status"] != "completed" or execution["outcome_uncertain"]:
            raise ValueError(f"Reference execution did not complete: {execution}")
        executions.append({"id": execution["id"], "experiment_id": experiment["id"], "replay_spec_id": spec["id"],
                           "mode": mode, "frame_count": sequence["frame_count"],
                           "max_deceleration_mps2": max_deceleration,
                           "manifest_hash": execution["manifest_hash"],
                           "ui": f"#viewers?project=default&execution={execution['id']}"})
    return {"title": "Log · retained original and open-loop" if sample == "log-open-loop" else "Log · reference planning feedback",
            "recording_id": recording["id"], "recording_sha256": sha256(content), "sequence_id": sequence["id"],
            "case_id": case["id"], "runs": [], "executions": executions,
            "ui": [execution["ui"] for execution in executions] + ["#replay-specs", "#catalog", "#cases"]}


def compact_database(directory: Path):
    with sqlite3.connect(directory / "platform.db") as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("VACUUM")
    for directory_to_remove in sorted((p for p in directory.rglob("*") if p.is_dir()), reverse=True):
        if not any(directory_to_remove.iterdir()):
            directory_to_remove.rmdir()


def file_inventory(directory: Path):
    result = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symlinks are not portable fixtures: {path.name}")
        if path.is_file():
            data = path.read_bytes()
            if any(marker in data for marker in (b"/Users/", b"/home/", b"/private/", b"Bearer ")):
                raise ValueError(f"Private path or authorization marker found in fixture: {path.name}")
            result.append({"path": path.relative_to(directory).as_posix(), "bytes": len(data), "sha256": sha256(data)})
    return result


def build(output: Path):
    output = output.resolve()
    if output.exists():
        raise ValueError("Build destination already exists; choose a new directory. Existing examples are never overwritten.")
    output.mkdir(parents=True)
    result = {"schema_version": "axiom-github-demo/1", "scope": SCOPE, "seed": 42,
              "determinism": "Authored inputs and CPU numerical outputs repeat; IDs, build timestamps and SQLite bytes may differ.",
              "builder_sha256": sha256(Path(__file__).read_bytes()),
              "source_files": [{"path": str(path.relative_to(ROOT)), "sha256": sha256(path.read_bytes())}
                               for path in [ROOT / "backend/src/ade2e" / name for name in (
                                   "reference.py", "planning_replay.py", "scene_sequences.py", "replay_specs.py",
                                   "database.py", "service.py", "experiment_jobs.py")]],
              "samples": {}}
    try:
        for sample in SAMPLES:
            directory = output / sample
            directory.mkdir()
            with reference_client(directory) as client:
                data = object_sample(client) if sample == "object-reference" else log_sample(client, sample)
            compact_database(directory)
            data.update(scope=SCOPE, database="platform.db", files=file_inventory(directory))
            data["total_bytes"] = sum(item["bytes"] for item in data["files"])
            result["samples"][sample] = data
        write_json(output / "manifest.json", result)
    except Exception:
        # A failed new build is retained for diagnosis. Never remove its parent.
        write_json(output / "BUILD_FAILED.json", {"message": "Build incomplete. Do not load this directory."})
        raise
    return result


def check_inventory(source: Path):
    source = source.resolve()
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "axiom-github-demo/1" or set(manifest["samples"]) != set(SAMPLES):
        raise ValueError("Unsupported demo manifest")
    for sample, entry in manifest["samples"].items():
        directory = source / sample
        if directory.is_symlink() or file_inventory(directory) != entry["files"]:
            raise ValueError(f"Fixture file inventory or SHA-256 differs: {sample}")
        with sqlite3.connect((directory / "platform.db").as_uri() + "?mode=ro", uri=True) as connection:
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError(f"SQLite integrity check failed: {sample}")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError(f"SQLite foreign key check failed: {sample}")
            if connection.execute("SELECT count(*) FROM execution_jobs WHERE status NOT IN ('completed','failed','cancelled','interrupted')").fetchone()[0]:
                raise ValueError("Packaged demo must not contain active jobs")
            # No authentication material is needed for the local single-user demo.
            token_tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%token%'")]
            for table in token_tables:
                quoted_table = table.replace('"', '""')
                if connection.execute(f'SELECT count(*) FROM "{quoted_table}"').fetchone()[0]:
                    raise ValueError("Packaged demo must not contain authentication tokens")
    return manifest


def _verify_download(client, artifact):
    response = client.get(artifact["download_url"])
    if response.status_code != 200 or sha256(response.content) != artifact["sha256"]:
        raise ValueError("Retained artifact download hash differs")


def verify(source: Path):
    manifest = check_inventory(source)
    checks = []
    for sample, entry in manifest["samples"].items():
        # API startup/inspection may update metadata: only open a disposable copy.
        with tempfile.TemporaryDirectory(prefix="axiom-demo-verify-") as temporary:
            directory = Path(temporary) / "relocated"
            shutil.copytree(source / sample, directory)
            with reference_client(directory) as client:
                request(client, "GET", "/overview")
                for run in entry["runs"]:
                    detail = request(client, "GET", f"/runs/{run['id']}")
                    if detail["verdict"] != run["verdict"] or detail["provenance"]["output_hash"] != run["output_hash"]:
                        raise ValueError("Retained reference result differs")
                    for artifact in detail["artifacts"]:
                        _verify_download(client, artifact)
                for execution in entry["executions"]:
                    detail = request(client, "GET", f"/experiments/executions/{execution['id']}")
                    if detail["status"] != "completed" or detail["manifest_hash"] != execution["manifest_hash"]:
                        raise ValueError("Retained replay execution differs")
                    if detail["summary"]["verification_verdict"] != "diagnostic_not_safety_validation":
                        raise ValueError("Replay evidence must keep its diagnostic boundary")
                    for artifact in detail["artifacts"]:
                        _verify_download(client, artifact)
                    _verify_download(client, {"download_url": detail["download_url"], "sha256": detail["manifest_hash"]})
                    preflight = request(client, "POST", f"/replay-specs/{execution['replay_spec_id']}/preflight")
                    if not preflight["executable"]:
                        raise ValueError("Relocated replay inputs are not executable")
                if entry.get("recording_id"):
                    request(client, "GET", f"/recordings/{entry['recording_id']}")
                    case = request(client, "GET", f"/cases/{entry['case_id']}")
                    _verify_download(client, {"download_url": case["download_url"], "sha256": case["manifest_hash"]})
            checks.append({"sample": sample, "status": "pass", "files": len(entry["files"]),
                           "bytes": entry["total_bytes"], "runs": len(entry["runs"]), "executions": len(entry["executions"])})
    return {"status": "pass", "scope": "Relocated SQLite, normal API readback and artifact SHA-256 checks. Browser review is separate.", "checks": checks}


def load(source: Path, sample: str, data_dir: Path):
    manifest = check_inventory(source)
    destination = data_dir.resolve()
    if sample not in SAMPLES:
        raise ValueError("Unknown sample")
    if destination.exists():
        raise ValueError("Load destination must not exist; existing workspaces are never replaced.")
    if destination.is_relative_to(source.resolve()):
        raise ValueError("Choose a working data directory outside the fixture source")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source / sample, destination)
    write_json(destination / "github-demo.json", {"sample": sample, "scope": SCOPE, "ui": manifest["samples"][sample]["ui"]})
    return {"sample": sample, "data_dir": str(destination), "ui": manifest["samples"][sample]["ui"],
            "next": "Set ADE2E_DATA_DIR to this copied directory and ADE2E_SEED_EXAMPLES=0, then run python scripts/dev.py. Clear ADE2E_DATABASE_URL if previously configured."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    building = commands.add_parser("build", help="Execute original CPU examples into a new directory")
    building.add_argument("--output", type=Path, required=True)
    verifying = commands.add_parser("verify", help="Hash-check originals, then inspect disposable relocated copies")
    verifying.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    loading = commands.add_parser("load", help="Copy one verified fixture into a new working data directory")
    loading.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    loading.add_argument("--sample", choices=SAMPLES, required=True)
    loading.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = build(args.output) if args.command == "build" else verify(args.source) if args.command == "verify" else load(args.source, args.sample, args.data_dir)
    except (ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
