"""Isolated launcher ownership checks; no real service, job or signal is used."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


SPEC = importlib.util.spec_from_file_location(
    "stunt_service_under_test", Path(__file__).resolve().parents[1] / "stunt_demo_service.py")
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


@pytest.fixture
def layout(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    runtime = root / ".runtime/stunt-test"
    runtime.mkdir(parents=True)
    (runtime / "data").mkdir()
    config = {"ADE2E_DATA_DIR": str(runtime / "data"), "ADE2E_SEED_EXAMPLES": "0",
              "ADE2E_CORS_ORIGINS": "http://127.0.0.1:5350",
              "AXIOM_CARLA_STUNT_PORT": "2400"}
    (runtime / "environment.json").write_text(json.dumps(config))
    monkeypatch.setattr(launcher, "ROOT", root)
    return runtime, config


def write_config(layout, **overrides):
    runtime, config = layout
    (runtime / "environment.json").write_text(json.dumps({**config, **overrides}))


def heartbeat(runtime, worker_id, *, age=0, state="idle"):
    with closing(sqlite3.connect(runtime / "data/platform.db")) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS execution_workers "
                           "(id TEXT, state TEXT, heartbeat_at TEXT)")
        connection.execute("INSERT INTO execution_workers VALUES (?,?,?)", (
            worker_id, state, (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()))
        connection.commit()


@pytest.fixture
def fake_start(layout, monkeypatch):
    import internal_platform
    runtime, _ = layout
    inspect = Mock()
    monkeypatch.setattr(internal_platform, "inspect_workspace", inspect)
    probe = Mock()
    probe.__enter__ = Mock(return_value=probe)
    probe.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(launcher.socket, "socket", lambda: probe)
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(launcher, "time", SimpleNamespace(
        monotonic=lambda: clock.now, sleep=lambda delay: setattr(clock, "now", clock.now + delay)))
    children, calls = [], []

    def spawn(argv, **kwargs):
        child = Mock(pid=200000 + len(children))
        child.returncode = None
        child.poll.side_effect = lambda: child.returncode
        child.terminate.side_effect = lambda: setattr(child, "returncode", -15)
        child.kill.side_effect = lambda: setattr(child, "returncode", -9)
        child.wait.side_effect = lambda timeout: child.returncode
        children.append(child)
        calls.append((argv, kwargs))
        return child

    popen = Mock(side_effect=spawn)
    monkeypatch.setattr(launcher.subprocess, "Popen", popen)
    monkeypatch.setattr(launcher, "identity", lambda pid: f"fixed-start owned-command {pid}")
    opener = Mock()

    def response(*args, **kwargs):
        stream = io.BytesIO(b'{"status":"ok"}')
        stream.status = 200
        return stream

    opener.open.side_effect = response
    monkeypatch.setattr(launcher, "build_opener", lambda *args: opener)
    heartbeat(runtime, "worker_host_200001_nonce")
    return SimpleNamespace(runtime=runtime, inspect=inspect, children=children, calls=calls,
                           popen=popen, opener=opener, clock=clock)


def test_profile_keeps_exact_isolated_data_and_retained_results(layout):
    runtime, config = layout
    assert launcher.profile(runtime) == config
    with pytest.raises(ValueError, match="isolated runtime"):
        launcher.profile(runtime.parents[1] / "outside")
    write_config(layout, ADE2E_DATA_DIR=str(runtime.parent / "other-data"))
    with pytest.raises(ValueError, match="own runtime/data"):
        launcher.profile(runtime)
    write_config(layout, ADE2E_SEED_EXAMPLES="1")
    with pytest.raises(ValueError, match="seeding must be disabled"):
        launcher.profile(runtime)


@pytest.mark.parametrize("key,value", [
    ("ADE2E_DATABASE_URL", "sqlite:////unrelated.db"),
    ("PYTHONPATH", "/untrusted/source"), ("AXIOM_OTHER", "value"),
    ("AXIOM_CARLA_STUNT_PORT", 2400),
])
def test_profile_rejects_environment_escape(layout, key, value):
    write_config(layout, **{key: value})
    with pytest.raises(ValueError, match="Unexpected environment"):
        launcher.profile(layout[0])


def test_profile_rejects_data_symlink_outside_runtime(layout, tmp_path):
    runtime, _ = layout
    (runtime / "data").rmdir()
    (runtime / "data").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="own runtime/data"):
        launcher.profile(runtime)


def test_detached_launch_uses_owned_venv_private_logs_and_clean_environment(fake_start, monkeypatch):
    for key in ("ADE2E_DATABASE_URL", "AXIOM_OTHER", "PYTHONPATH", "PYTHONHOME"):
        monkeypatch.setenv(key, "must-not-inherit")
    result = launcher.start(fake_start.runtime, 8250)
    assert all(item["running"] for item in result["services"].values())
    assert result["automatic_restart"] is False
    fake_start.inspect.assert_called_once_with(fake_start.runtime / "data")
    assert len(fake_start.children) == 2
    for (argv, kwargs), role in zip(fake_start.calls, ("api", "worker")):
        assert argv[0] == str(launcher.ROOT / ".venv/bin/python")
        assert "_" + role in argv
        assert kwargs["start_new_session"] is True and kwargs["close_fds"] is True
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.STDOUT
        assert kwargs["stdout"].closed
        assert kwargs["cwd"] == launcher.ROOT
        assert kwargs["env"]["PYTHONNOUSERSITE"] == "1"
        assert not any(kwargs["env"].get(key) == "must-not-inherit" for key in
                       ("ADE2E_DATABASE_URL", "AXIOM_OTHER", "PYTHONPATH", "PYTHONHOME"))
        assert stat.S_IMODE((fake_start.runtime / (role + "-bootstrap.log")).stat().st_mode) == 0o600
    assert stat.S_IMODE((fake_start.runtime / "services.json").stat().st_mode) == 0o600
    assert not list(fake_start.runtime.glob("*.plist"))


def test_existing_owned_pair_is_idempotent_and_partial_pair_is_blocked(fake_start, monkeypatch):
    launcher.start(fake_start.runtime, 8250)
    fake_start.popen.reset_mock()
    fake_start.inspect.reset_mock()
    assert all(item["running"] for item in launcher.start(fake_start.runtime, 8250)["services"].values())
    fake_start.popen.assert_not_called()
    fake_start.inspect.assert_not_called()
    monkeypatch.setattr(launcher, "identity", lambda pid: "" if pid == 200001 else f"fixed-start owned-command {pid}")
    with pytest.raises(ValueError, match="Only one owned service"):
        launcher.start(fake_start.runtime, 8250)
    fake_start.popen.assert_not_called()
    for child in fake_start.children:
        child.terminate.assert_not_called()


def test_status_does_not_claim_reused_pid(layout, monkeypatch):
    runtime, _ = layout
    (runtime / "services.json").write_text(json.dumps({
        "api": {"pid": 200000, "identity": "previous-start same-command"},
        "worker": {"pid": 200001, "identity": "original-start worker-command"}}))
    monkeypatch.setattr(launcher, "identity", lambda pid: "new-start unrelated-command")
    assert not any(item["running"] for item in launcher.status(runtime)["services"].values())


def test_concurrent_start_is_rejected_before_spawning(fake_start, monkeypatch):
    monkeypatch.setattr(launcher.fcntl, "flock", Mock(side_effect=BlockingIOError("already locked")))
    with pytest.raises(BlockingIOError):
        launcher.start(fake_start.runtime, 8250)
    fake_start.popen.assert_not_called()


def test_second_spawn_failure_cleans_up_only_first_owned_child(fake_start):
    spawn = fake_start.popen.side_effect
    def fail_worker(argv, **kwargs):
        if "_worker" in argv:
            raise OSError("unit-test bootstrap failure")
        return spawn(argv, **kwargs)
    fake_start.popen.side_effect = fail_worker
    with pytest.raises(OSError, match="bootstrap failure"):
        launcher.start(fake_start.runtime, 8250)
    assert len(fake_start.children) == 1
    fake_start.children[0].terminate.assert_called_once()
    fake_start.children[0].wait.assert_called_once_with(timeout=10)
    assert not (fake_start.runtime / "services.json").exists()
    assert (fake_start.runtime / "api-bootstrap.log").exists()


def test_readiness_failure_cleans_both_and_retains_previous_receipt(fake_start):
    receipt = fake_start.runtime / "services.json"
    receipt.write_text("{}")
    fake_start.opener.open.side_effect = OSError("API unavailable")
    with pytest.raises(RuntimeError, match="did not become ready"):
        launcher.start(fake_start.runtime, 8250)
    assert receipt.read_text() == "{}"
    for child in fake_start.children:
        child.terminate.assert_called_once()
    assert fake_start.clock.now >= 30


@pytest.mark.parametrize("worker_id,age,state", [
    ("worker_host_2000019_nonce", 0, "idle"),
    ("worker_host_1200001_nonce", 0, "idle"),
    ("worker_host_200002_nonce", 0, "idle"),
    ("worker_host_200001_nonce", 60, "idle"),
    ("worker_host_200001_nonce", 0, "stopped"),
])
def test_readiness_requires_exact_new_worker_pid_and_fresh_idle_heartbeat(fake_start, worker_id, age, state):
    with closing(sqlite3.connect(fake_start.runtime / "data/platform.db")) as connection:
        connection.execute("DELETE FROM execution_workers")
        connection.commit()
    heartbeat(fake_start.runtime, worker_id, age=age, state=state)
    with pytest.raises(RuntimeError, match="did not become ready"):
        launcher.start(fake_start.runtime, 8250)
    for child in fake_start.children:
        child.terminate.assert_called_once()


@pytest.mark.parametrize("job_status,resource_state,unfinished", [
    ("queued", "idle", False), ("running", "idle", False),
    ("cancel_requested", "idle", False), ("completed", "quarantined", False),
    ("completed", "idle", True),
])
def test_real_preflight_rejects_implicit_work_or_uncertain_engine(layout, monkeypatch, job_status, resource_state, unfinished):
    from ade2e.database import SCHEMA_VERSION
    import ade2e.workspace_backup
    runtime, _ = layout
    with closing(sqlite3.connect(runtime / "data/platform.db")) as connection:
        connection.executescript("CREATE TABLE schema_versions (version INTEGER);"
                                 "CREATE TABLE execution_jobs (status TEXT);"
                                 "CREATE TABLE execution_resources (state TEXT);"
                                 "CREATE TABLE verification_attempts (manifest TEXT);")
        connection.execute("INSERT INTO schema_versions VALUES (?)", (SCHEMA_VERSION,))
        connection.execute("INSERT INTO execution_jobs VALUES (?)", (job_status,))
        connection.execute("INSERT INTO execution_resources VALUES (?)", (resource_state,))
        if unfinished:
            connection.execute("INSERT INTO verification_attempts VALUES (NULL)")
        connection.commit()
    # These fixture databases are closed above; no real lsof/process operation.
    monkeypatch.setattr(ade2e.workspace_backup, "assert_offline", lambda *args, **kwargs: None)
    popen = Mock(side_effect=AssertionError("must not start a process"))
    monkeypatch.setattr(launcher.subprocess, "Popen", popen)
    with pytest.raises(ValueError, match="Queued work|execution ownership"):
        launcher.start(runtime, 8250)
    popen.assert_not_called()
