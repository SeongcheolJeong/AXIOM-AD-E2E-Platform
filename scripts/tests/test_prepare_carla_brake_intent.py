"""Author-only tests with temporary evidence; no simulator or real trial is used."""
import builtins
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE_NAMES = ("carla_brake_protocol.py", "diagnose_carla_brake_response.py", "diagnose_carla_self_hit.py")


@pytest.fixture
def author(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    original_import = builtins.__import__

    def import_without_engine(name, *args, **kwargs):
        assert name.split(".")[0] not in {"carla", "axiom_carla", "ade2e"}, "Author must not import an engine"
        return original_import(name, *args, **kwargs)

    def forbidden_io(*args, **kwargs):
        pytest.fail("Author must not create a network connection or child process")

    monkeypatch.setattr(builtins, "__import__", import_without_engine)
    monkeypatch.setattr(socket, "socket", forbidden_io)
    monkeypatch.setattr(subprocess, "Popen", forbidden_io)
    spec = importlib.util.spec_from_file_location("tested_prepare_carla_brake_intent", ROOT / "scripts/prepare_carla_brake_intent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def inputs(tmp_path):
    available = lambda value: {"status": "available", "value": value, "reason": None}
    physics = {"status": "available", "friction_status": "matched",
               "scalars": {"mass": available(1845.0), "use_gear_autobox": available(True)},
               "wheels": [{"index": i, "fields": {"tire_friction": available(0.800000011920929)}}
                          for i in range(4)]}
    environment = {
        "expected_store_id": "a1111111-1111-4111-8111-111111111111",
        "engine_build": "1ae5356", "adapter_version": "0.8.0",
        "expected_world": {
            "world_id": "13687974690369489725",
            "map": {"name": "Carla/Maps/Town10HD_Opt", "opendrive_sha256": "a" * 64,
                    "opendrive_size_bytes": 1857682},
            "actors": [{"actor_id": 1, "type_id": "spectator", "role_name": None}],
            "settings": {"values": {"synchronous_mode": False}, "invalid_fields": {}},
            "weather": {"values": {"cloudiness": 20.0}, "invalid_fields": {}},
        },
        "spawn_transform": {"position_m": {"x": 10.0, "y": -57.0, "z": 0.9},
                            "rotation_deg": {"pitch": 0.0, "yaw": 0.0, "roll": 0.0}},
        "requested_weather": {"values": {"cloudiness": 10.0}, "invalid_fields": {}},
    }
    response = {"schema_version": "carla-vehicle-response/1", "status": "completed",
                "worker_adapter_version": "0.8.0", "server_version": "1ae5356",
                "physics_configuration": {"ego": physics}}
    args = SimpleNamespace(environment=tmp_path / "environment.json", source_evidence=tmp_path / "response.json",
                           source_dir=tmp_path / "captured-sources", output=tmp_path / "intent.json",
                           initialization="throttle", measurement="coast", brake_level=0.0, repeat_index=1, seed=42)
    args.environment.write_text(json.dumps(environment, indent=2))
    args.source_evidence.write_text(json.dumps(response, indent=2) + "\n")
    args.source_dir.mkdir()
    for i, name in enumerate(SOURCE_NAMES):
        (args.source_dir / name).write_bytes(f"# Authored source fixture {i}; never executed.\n".encode())
    return args, environment, response


def test_author_preserves_exact_original_evidence_and_hashes_captured_bytes(author, inputs):
    args, environment, response = inputs
    paths = [args.environment, args.source_evidence, *(args.source_dir / name for name in SOURCE_NAMES)]
    original = {path: path.read_bytes() for path in paths}
    result = author.prepare(args)
    written = args.output.read_bytes()
    intent = json.loads(written)
    digest = lambda raw: hashlib.sha256(raw).hexdigest()
    assert result["engine_actions"] == 0 and result["intent_sha256"] == digest(written)
    assert intent["source_evidence"] == {"path": str(args.source_evidence.resolve()),
                                          "sha256": digest(original[args.source_evidence])}
    assert intent["source_files"] == {name: digest(original[args.source_dir / name]) for name in SOURCE_NAMES}
    assert intent["expected_physics"] == {key: response["physics_configuration"]["ego"][key]
                                           for key in ("scalars", "wheels")}
    assert all(intent[key] == value for key, value in environment.items())
    assert {path: path.read_bytes() for path in paths} == original
    assert stat.S_IMODE(args.output.stat().st_mode) == 0o600


def test_author_existing_output_is_never_replaced_or_truncated(author, inputs):
    args, _, _ = inputs
    args.output.write_bytes(b"previous pinned intent bytes\x00\n")
    before = (args.output.read_bytes(), args.output.stat().st_ino, args.output.stat().st_mtime_ns)
    with pytest.raises(FileExistsError):
        author.prepare(args)
    assert (args.output.read_bytes(), args.output.stat().st_ino, args.output.stat().st_mtime_ns) == before


@pytest.mark.parametrize("path,value", [
    (("status",), "partial"), (("schema_version",), "carla-vehicle-response/2"),
    (("worker_adapter_version",), "0.7.0"), (("server_version",), "other-build"),
    (("physics_configuration", "ego", "status"), "not_observed"),
    (("physics_configuration", "ego", "friction_status"), "mismatch"),
    (("physics_configuration", "ego", "scalars", "mass", "status"), "unavailable"),
    (("physics_configuration", "ego", "scalars", "mass", "value"), float("nan")),
])
def test_author_rejects_uncompleted_wrong_engine_or_unavailable_physics_source(author, inputs, path, value):
    args, _, response = inputs
    target = response
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    args.source_evidence.write_text(json.dumps(response))
    with pytest.raises(ValueError):
        author.prepare(args)
    assert not args.output.exists()


@pytest.mark.parametrize("target", ["environment", "source_evidence", "source"])
def test_author_refuses_symlinked_sources_before_creating_intent(author, inputs, target):
    args, _, _ = inputs
    path = args.source_dir / SOURCE_NAMES[0] if target == "source" else getattr(args, target)
    original = path.with_suffix(".original")
    path.rename(original)
    path.symlink_to(original)
    with pytest.raises(ValueError):
        author.prepare(args)
    assert original.is_file() and not args.output.exists()


def test_author_missing_bundle_member_does_not_fallback_to_checkout_source(author, inputs):
    args, _, _ = inputs
    (args.source_dir / "diagnose_carla_self_hit.py").unlink()
    with pytest.raises(ValueError, match="Missing captured source"):
        author.prepare(args)
    assert not args.output.exists()


def test_author_rejects_unknown_environment_keys_and_ambiguous_json(author, inputs):
    args, environment, _ = inputs
    environment["fallback_initialization"] = "velocity_injection"
    args.environment.write_text(json.dumps(environment))
    with pytest.raises(ValueError, match="Environment"):
        author.prepare(args)
    environment.pop("fallback_initialization")
    raw = json.dumps(environment).replace('"synchronous_mode": false',
                                          '"synchronous_mode": true, "synchronous_mode": false')
    args.environment.write_text(raw)
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        author.prepare(args)
    assert not args.output.exists()


def test_repeated_authoring_has_distinct_identity_but_same_pinned_inputs(author, inputs):
    args, _, _ = inputs
    author.prepare(args)
    first = json.loads(args.output.read_bytes())
    args.output = args.output.with_name("second-intent.json")
    author.prepare(args)
    second = json.loads(args.output.read_bytes())
    assert first.pop("experiment_id") != second.pop("experiment_id")
    assert first == second


def test_explicit_v3_authoring_keeps_v2_intent_bytes_and_provenance_independent(author, inputs):
    args, _, _ = inputs
    author.prepare(args)
    original_path = args.output
    original_bytes = original_path.read_bytes()
    previous = json.loads(original_bytes)
    args.output = args.output.with_name("settled-pulse-intent.json")
    args.protocol_version = "carla-brake-protocol/3"
    args.measurement, args.brake_level = "pulse", 0.025
    result = author.prepare(args)
    current = json.loads(args.output.read_bytes())
    assert original_path.read_bytes() == original_bytes
    assert previous["protocol"]["schema_version"] == "carla-brake-protocol/2"
    assert current["protocol"]["schema_version"] == "carla-brake-protocol/3"
    assert current["protocol"]["policy"]["pulse_start_step"] == 0
    assert current["experiment_id"] != previous["experiment_id"]
    for field in ("expected_world", "expected_store_id", "engine_build", "adapter_version",
                  "spawn_transform", "requested_weather", "expected_physics", "source_evidence", "source_files"):
        assert current[field] == previous[field]
    expected = [{"step": i, "time_s": i * 0.05, "control": {
        "throttle": 0.0, "brake": 0.025 if i < 5 else 0.0, "steer": 0.0,
        "hand_brake": False, "reverse": False, "manual_gear_shift": False, "gear": 1}} for i in range(80)]
    digest = hashlib.sha256(json.dumps(expected, sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    assert current["sequence_sha256"] == result["sequence_sha256"] == digest
    assert current["sequence_sha256"] != previous["sequence_sha256"]


@pytest.mark.parametrize("version,initialization", [
    ("carla-brake-protocol/3", "velocity_injection"), ("carla-brake-protocol/4", "throttle"),
])
def test_author_rejects_unsupported_settled_mode_or_future_version_without_output(author, inputs, version, initialization):
    args, _, _ = inputs
    args.protocol_version, args.initialization = version, initialization
    with pytest.raises(ValueError):
        author.prepare(args)
    assert not args.output.exists()


@pytest.mark.parametrize("version", ["carla-brake-protocol/2", "carla-brake-protocol/3"])
def test_cli_honors_explicit_supported_protocol_version_without_engine_io(author, inputs, monkeypatch, capsys, version):
    args, _, _ = inputs
    monkeypatch.setattr(sys, "argv", ["prepare_carla_brake_intent.py",
        "--environment", str(args.environment), "--source-evidence", str(args.source_evidence),
        "--source-dir", str(args.source_dir), "--output", str(args.output),
        "--initialization", "throttle", "--protocol-version", version])
    author.main()
    result = json.loads(capsys.readouterr().out)
    assert result["engine_actions"] == 0
    assert json.loads(args.output.read_bytes())["protocol"]["schema_version"] == version


@pytest.mark.parametrize("version", ["carla-brake-protocol/1", "carla-brake-protocol/4"])
def test_cli_rejects_legacy_execution_authoring_and_future_versions(author, inputs, monkeypatch, version):
    args, _, _ = inputs
    monkeypatch.setattr(sys, "argv", ["prepare_carla_brake_intent.py",
        "--environment", str(args.environment), "--source-evidence", str(args.source_evidence),
        "--source-dir", str(args.source_dir), "--output", str(args.output),
        "--initialization", "throttle", "--protocol-version", version])
    with pytest.raises(SystemExit) as caught:
        author.main()
    assert caught.value.code == 2 and not args.output.exists()


@pytest.mark.parametrize("target,size", [("environment", 128 * 1024), ("source_evidence", 8 * 1024 * 1024),
                                        ("source", 256 * 1024)])
def test_v3_authoring_rejects_oversized_evidence_and_captured_source_before_output(author, inputs, target, size):
    args, _, _ = inputs
    args.protocol_version = "carla-brake-protocol/3"
    path = args.source_dir / SOURCE_NAMES[0] if target == "source" else getattr(args, target)
    original = path.read_bytes()
    path.write_bytes(original + b" " * (size - len(original) + 1))
    with pytest.raises(ValueError, match="budget"):
        author.prepare(args)
    assert not args.output.exists()
