"""Mocked lifetime and ownership checks; no real tunnel, signal or job is used."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


SPEC = importlib.util.spec_from_file_location(
    "external_supervisor_under_test", Path(__file__).resolve().parents[1] / "external_access_supervisor.py")
supervisor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(supervisor)


@pytest.fixture
def layout(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    base = root / ".runtime/external-access-test"
    base.mkdir(parents=True, mode=0o700)
    base.chmod(0o700)
    config = base / "engineer-config.json"
    config.write_text(json.dumps({"expires_at": 1000 + 48 * 3600,
        "password": "never-log-password", "access_mode": "project_engineer"}))
    config.chmod(0o600)
    binary = root / ".runtime/official-binary/cloudflared"
    binary.parent.mkdir(mode=0o700)
    binary.write_bytes(b"unit-fixture-binary")
    binary.chmod(0o700)
    monkeypatch.setattr(supervisor, "OFFICIAL_TUNNEL_SHA256", hashlib.sha256(binary.read_bytes()).hexdigest())
    return root, base, config, binary


def validate(layout, **kwargs):
    root, base, _, binary = layout
    return supervisor.validate_paths(root, str(kwargs.pop("base", base)),
        kwargs.pop("config_name", "engineer-config.json"), str(kwargs.pop("binary", binary)), now=1000)


def test_private_owned_session_and_official_hash_accepted(layout):
    _, base, config, binary = layout
    assert validate(layout) == (base, config, binary, 1000 + 48 * 3600)
    assert validate(layout, config_name=str(config)) == (base, config, binary, 1000 + 48 * 3600)


@pytest.mark.parametrize("deadline", [1000, 999, True, "tomorrow", float("inf"), 1000 + 48 * 3600 + 6])
def test_deadline_is_fixed_finite_and_capped_at_48_hours(layout, deadline):
    _, _, config, _ = layout
    config.write_text(json.dumps({"expires_at": deadline, "access_mode": "project_engineer"}))
    with pytest.raises(supervisor.SupervisorError, match="invalid_or_expired_deadline"):
        validate(layout)


@pytest.mark.parametrize("name", ["../engineer-config.json", "/tmp/config.json", "readonly-config.json"])
def test_no_arbitrary_config_path(layout, name):
    with pytest.raises(supervisor.SupervisorError, match="unexpected_gateway_config_name"):
        validate(layout, config_name=name)


def test_session_cannot_escape_runtime_or_use_symlink(layout):
    root, base, _, _ = layout
    with pytest.raises(supervisor.SupervisorError, match="session_outside_workspace_runtime"):
        validate(layout, base=root / "outside")
    link = base.parent / "linked-session"
    link.symlink_to(base, target_is_directory=True)
    with pytest.raises(supervisor.SupervisorError, match="unowned_or_symlink_path"):
        validate(layout, base=link)


def test_world_readable_config_and_session_are_rejected(layout):
    _, base, config, _ = layout
    config.chmod(0o644)
    with pytest.raises(supervisor.SupervisorError, match="invalid_private_file"):
        validate(layout)
    config.chmod(0o600)
    base.chmod(0o755)
    with pytest.raises(supervisor.SupervisorError, match="invalid_session_directory"):
        validate(layout)


def test_tunnel_must_be_owned_pinned_binary_inside_runtime(layout):
    root, base, _, binary = layout
    with pytest.raises(supervisor.SupervisorError, match="tunnel_outside_workspace_runtime"):
        validate(layout, binary=root / "outside/cloudflared")
    link = base / "cloudflared"
    link.symlink_to(binary)
    with pytest.raises(supervisor.SupervisorError, match="unowned_or_symlink_path"):
        validate(layout, binary=link)
    binary.write_bytes(b"modified")
    with pytest.raises(supervisor.SupervisorError, match="tunnel_hash_mismatch"):
        validate(layout)


@pytest.fixture
def runner(layout, monkeypatch):
    root, base, config, binary = layout
    clock = SimpleNamespace(value=1000.0)
    def sleep(seconds):
        clock.value += seconds
    monkeypatch.setattr(supervisor, "time", SimpleNamespace(
        time=lambda: clock.value, monotonic=lambda: clock.value, sleep=sleep))
    monkeypatch.setattr(supervisor, "identity", lambda pid: f"fixed-start {pid} fixed-command")
    monkeypatch.setattr(supervisor, "login_available", lambda port: True)
    monkeypatch.setattr(supervisor, "loopback_in_use", lambda port: False)
    children = []
    def start(argv, **kwargs):
        child = Mock(pid=2000 + len(children))
        child.returncode = None
        child.poll.side_effect = lambda: child.returncode
        def terminate():
            child.returncode = -signal.SIGTERM
        child.terminate.side_effect = terminate
        child.wait.side_effect = lambda timeout: child.returncode
        child.argv = argv
        children.append(child)
        if "tunnel" in argv:
            kwargs["stdout"].write(b"Registered tunnel https://unit-workspace.trycloudflare.com\n")
        return child
    monkeypatch.setattr(supervisor.subprocess, "Popen", start)
    instance = supervisor.Supervisor(root, base, config, binary, 1002)
    return instance, children, clock


def test_expiry_stops_only_owned_children_and_releases_awake_guard(runner):
    instance, children, clock = runner
    assert instance.run() == 0
    assert clock.value >= 1002
    assert instance.state == "expired"
    assert len(children) == 3
    for child in children:
        child.terminate.assert_called_once()
    assert children[2].argv == ["/usr/bin/caffeinate", "-is", "-t", "2"]
    assert (instance.base / "public-url.txt").read_text().strip() == "https://unit-workspace.trycloudflare.com"
    record = json.loads((instance.base / "processes.json").read_text())
    assert set(record) == {"expires_at", "supervisor", "gateway", "tunnel", "awake_guard"}


def test_private_logs_and_status_never_contain_browser_password_or_api_token(runner):
    instance, children, _ = runner
    assert instance.run() == 0
    for path in instance.base.iterdir():
        if path.name == "engineer-config.json":
            continue
        assert path.stat().st_mode & 0o777 == 0o600
        assert "never-log-password" not in path.read_text()
    assert all("never-log-password" not in " ".join(child.argv) for child in children)


def test_child_failure_is_terminal_and_other_owned_children_are_cleaned(runner, monkeypatch):
    instance, children, _ = runner
    real_wait = instance.wait_for_url
    def fail_after_ready():
        result = real_wait()
        children[1].returncode = 17
        return result
    monkeypatch.setattr(instance, "wait_for_url", fail_after_ready)
    assert instance.run() == 1
    assert instance.state == "failed" and instance.failure_code == "tunnel_exited"
    assert len(children) == 3  # No automatic replacement tunnel or hidden URL.
    children[0].terminate.assert_called_once()
    children[1].terminate.assert_not_called()
    children[2].terminate.assert_called_once()
    assert json.loads((instance.base / "supervisor-status.json").read_text())["public_url"] == instance.url


def test_signal_stop_is_clean_terminal_state(runner, monkeypatch):
    instance, children, _ = runner
    original_wait = instance.wait_for_url
    def stop():
        instance.request_stop(signal.SIGTERM, None)
        return original_wait()
    monkeypatch.setattr(instance, "wait_for_url", stop)
    assert instance.run() == 0
    assert instance.state == "stopped"
    for child in children:
        child.terminate.assert_called_once()


def test_recycled_child_identity_is_never_signalled(runner, monkeypatch):
    instance, children, _ = runner
    instance.spawn("gateway", ["fixture"])
    monkeypatch.setattr(supervisor, "identity", lambda pid: "different start and command")
    instance.cleanup()
    children[0].terminate.assert_not_called()
    children[0].kill.assert_not_called()


def test_occupied_gateway_port_prevents_any_child_spawn(runner, monkeypatch):
    instance, children, _ = runner
    monkeypatch.setattr(supervisor, "loopback_in_use", lambda port: True)
    assert instance.run() == 1
    assert children == [] and instance.failure_code == "gateway_port_occupied"


def test_existing_tunnel_log_cannot_publish_a_stale_url(runner, monkeypatch):
    instance, _, _ = runner
    logfile = instance.base / "tunnel.log"
    logfile.write_text("https://old-address.trycloudflare.com\n")
    logfile.chmod(0o600)
    assert instance.run() == 0
    assert instance.url == "https://unit-workspace.trycloudflare.com"


def test_url_regex_accepts_only_complete_https_trycloudflare_host():
    assert supervisor.PUBLIC_URL.search("| https://real-one.trycloudflare.com |")
    for text in ("https://real-one.trycloudflare.com.attacker.test ",
                 "https://real-one.trycloudflare.com/path ",
                 "http://real-one.trycloudflare.com "):
        assert not supervisor.PUBLIC_URL.search(text)


def test_startup_timeout_is_bounded_and_releases_gateway(runner, monkeypatch):
    instance, children, clock = runner
    instance.deadline = 1100
    monkeypatch.setattr(supervisor, "login_available", lambda port: False)
    assert instance.run() == 1
    assert instance.failure_code == "gateway_startup_timeout"
    assert 1030 <= clock.value <= 1031
    assert len(children) == 1
    children[0].terminate.assert_called_once()
