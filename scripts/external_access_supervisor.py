#!/usr/bin/env python3
"""Keep one temporary gateway, tunnel and awake guard alive until a fixed deadline.

This process is intended to be owned by launchd. It never starts platform APIs,
simulation services, jobs, or another supervisor, and never retries a failed child.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import time
from urllib.request import ProxyHandler, Request, build_opener


OFFICIAL_TUNNEL_SHA256 = "9a0b19f67dc7a3011bc6b972c7ce06a5fcea8784ac6bd599ffa382ea4aeb5a6e"
MAX_SECONDS = 48 * 60 * 60
PUBLIC_URL = re.compile(r"https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.trycloudflare\.com(?=$|[\s|])")


class SupervisorError(Exception):
    """A fixed, non-secret failure code suitable for the status artifact."""


def identity(pid: int) -> str:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "lstart=", "-o", "command="],
        capture_output=True, text=True, check=False, timeout=3,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def check_owned(path: Path, *, directory: bool = False, private: bool = False) -> None:
    info = path.lstat()
    if info.st_uid != os.geteuid() or stat.S_ISLNK(info.st_mode):
        raise SupervisorError("unowned_or_symlink_path")
    if directory:
        if not stat.S_ISDIR(info.st_mode) or (private and stat.S_IMODE(info.st_mode) != 0o700):
            raise SupervisorError("invalid_session_directory")
        if info.st_mode & 0o022:
            raise SupervisorError("writable_dependency")
    elif not stat.S_ISREG(info.st_mode) or (private and stat.S_IMODE(info.st_mode) != 0o600):
        raise SupervisorError("invalid_private_file")
    elif info.st_mode & 0o022:
        raise SupervisorError("writable_dependency")


def validate_paths(root: Path, session_dir: str, config_name: str, tunnel_binary: str,
                   *, now: float | None = None) -> tuple[Path, Path, Path, float]:
    root = root.resolve()
    runtime = root / ".runtime"
    check_owned(runtime, directory=True)
    base = Path(session_dir)
    if not base.is_absolute() or base.parent != runtime or base.name in ("", ".", ".."):
        raise SupervisorError("session_outside_workspace_runtime")
    check_owned(base, directory=True, private=True)
    requested_config = Path(config_name)
    config = base / "engineer-config.json"
    if config_name != "engineer-config.json" and requested_config != config:
        raise SupervisorError("unexpected_gateway_config_name")
    check_owned(config, private=True)
    if config.stat().st_size > 1024 * 1024:
        raise SupervisorError("oversized_gateway_config")
    try:
        # Never log this dictionary; it includes the browser password.
        data = json.loads(config.read_text())
    except (ValueError, UnicodeError):
        raise SupervisorError("invalid_gateway_config") from None
    deadline = data.get("expires_at") if isinstance(data, dict) else None
    instant = time.time() if now is None else now
    if (isinstance(deadline, bool) or not isinstance(deadline, (float, int))
            or not math.isfinite(deadline) or not instant < deadline <= instant + MAX_SECONDS + 5):
        raise SupervisorError("invalid_or_expired_deadline")
    if data.get("access_mode") != "project_engineer":
        raise SupervisorError("engineer_mode_required")
    binary = Path(tunnel_binary)
    if not binary.is_absolute():
        raise SupervisorError("tunnel_outside_workspace_runtime")
    try:
        relative = binary.relative_to(runtime)
    except ValueError:
        raise SupervisorError("tunnel_outside_workspace_runtime") from None
    if not relative.parts or any(part in (".", "..") for part in relative.parts):
        raise SupervisorError("tunnel_outside_workspace_runtime")
    cursor = runtime
    for part in relative.parts[:-1]:
        cursor /= part
        check_owned(cursor, directory=True)
    check_owned(binary)
    if not os.access(binary, os.X_OK):
        raise SupervisorError("tunnel_not_executable")
    digest = hashlib.sha256()
    with binary.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != OFFICIAL_TUNNEL_SHA256:
        raise SupervisorError("tunnel_hash_mismatch")
    return base, config, binary, float(deadline)


def private_write(path: Path, text: str) -> None:
    if path.exists() or path.is_symlink():
        check_owned(path, private=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def private_log(path: Path):
    if path.exists() or path.is_symlink():
        check_owned(path, private=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    return os.fdopen(descriptor, "ab", buffering=0)


def login_available(port: int) -> bool:
    # A fixed public GET proves this gateway is listening. No credential is read
    # or forwarded and environment proxy variables are not consulted.
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(Request(f"http://127.0.0.1:{port}/login", method="GET"), timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def loopback_in_use(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


class Supervisor:
    def __init__(self, root: Path, base: Path, config: Path, binary: Path,
                 deadline: float, port: int = 8787):
        self.root, self.base, self.config, self.binary = root, base, config, binary
        self.deadline, self.port = deadline, port
        self.children: dict[str, tuple[subprocess.Popen, str]] = {}
        self.logs = []
        self.stop_requested = False
        self.state = "starting"
        self.failure_code: str | None = None
        self.url: str | None = None
        self.tunnel_log_offset = 0
        self.records = {
            "expires_at": deadline,
            "supervisor": {"pid": os.getpid(), "identity": identity(os.getpid())},
        }
        if not self.records["supervisor"]["identity"]:
            raise SupervisorError("supervisor_identity_unavailable")

    def status(self) -> None:
        private_write(self.base / "processes.json", json.dumps(self.records, indent=2) + "\n")
        private_write(self.base / "supervisor-status.json", json.dumps({
            "state": self.state, "expires_at": self.deadline, "public_url": self.url,
            "failure_code": self.failure_code, "updated_at": time.time(),
            "pid": os.getpid(), "children": {name: record["pid"] for name, record in
                self.records.items() if isinstance(record, dict) and name != "supervisor"},
            "automatic_restart": False,
        }, indent=2) + "\n")

    def request_stop(self, _signum=None, _frame=None) -> None:
        self.stop_requested = True

    def spawn(self, name: str, argv: list[str]) -> None:
        log_path = self.base / f"{name}.log"
        log = private_log(log_path)
        if name == "tunnel":
            self.tunnel_log_offset = log_path.stat().st_size
        self.logs.append(log)
        child = subprocess.Popen(argv, cwd=self.root, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT, close_fds=True)
        # Register before any subsequent operation so startup failures can clean
        # this exact Popen child; a recycled PID is never signalled by identity.
        self.children[name] = (child, "")
        child_identity = identity(child.pid)
        if not child_identity:
            raise SupervisorError(f"{name}_identity_unavailable")
        self.children[name] = (child, child_identity)
        self.records[name] = {"pid": child.pid, "identity": child_identity}
        self.status()

    def check(self) -> bool:
        if self.stop_requested:
            self.state = "stopped"
            return False
        if time.time() >= self.deadline:
            self.state = "expired"
            return False
        for name, (child, _) in self.children.items():
            if child.poll() is not None:
                raise SupervisorError(f"{name}_exited")
        return True

    def wait_for_gateway(self) -> bool:
        limit = time.monotonic() + 30
        while self.check():
            if login_available(self.port):
                return True
            if time.monotonic() >= limit:
                raise SupervisorError("gateway_startup_timeout")
            time.sleep(0.25)
        return False

    def wait_for_url(self) -> bool:
        limit = time.monotonic() + 50
        while self.check():
            with (self.base / "tunnel.log").open("rb") as stream:
                stream.seek(max(self.tunnel_log_offset,
                                (self.base / "tunnel.log").stat().st_size - 65536))
                match = PUBLIC_URL.search(stream.read().decode("utf-8", errors="replace"))
            if match:
                self.url = match.group(0)
                private_write(self.base / "public-url.txt", self.url + "\n")
                return True
            if time.monotonic() >= limit:
                raise SupervisorError("tunnel_url_timeout")
            time.sleep(0.25)
        return False

    def cleanup(self) -> None:
        # Only these direct Popen children, with their recorded lstart+command,
        # may be terminated. No process groups or platform service ports.
        pending = []
        for child, recorded in reversed(list(self.children.values())):
            if child.poll() is None and recorded and identity(child.pid) == recorded:
                try:
                    child.terminate()
                    pending.append((child, recorded))
                except ProcessLookupError:
                    pass
            elif child.poll() is None and not recorded:
                # Identity retrieval failed immediately after spawn: the Popen
                # child relationship still proves ownership before it is reaped.
                try:
                    child.terminate()
                    pending.append((child, recorded))
                except ProcessLookupError:
                    pass
        end = time.monotonic() + 3
        for child, recorded in pending:
            try:
                child.wait(timeout=max(0.01, end - time.monotonic()))
            except subprocess.TimeoutExpired:
                if child.poll() is None and (not recorded or identity(child.pid) == recorded):
                    try:
                        child.kill()
                        child.wait(timeout=1)
                    except (ProcessLookupError, subprocess.TimeoutExpired):
                        pass
        for log in self.logs:
            log.close()

    def run(self) -> int:
        code = 0
        try:
            self.status()
            if not self.check():
                return code
            if loopback_in_use(self.port):
                raise SupervisorError("gateway_port_occupied")
            self.spawn("gateway", [str(self.root / ".venv/bin/python"),
                str(self.root / "scripts/external_access_gateway.py"), "--config", str(self.config),
                "--port", str(self.port)])
            if not self.wait_for_gateway():
                return code
            self.spawn("tunnel", [str(self.binary), "tunnel", "--config", "/dev/null",
                "--no-autoupdate", "--protocol", "http2", "--url", f"http://127.0.0.1:{self.port}"])
            self.spawn("awake_guard", ["/usr/bin/caffeinate", "-is", "-t",
                str(max(1, math.ceil(self.deadline - time.time())))])
            if not self.wait_for_url():
                return code
            self.state = "ready"
            self.status()
            while self.check():
                time.sleep(min(0.5, max(0.01, self.deadline - time.time())))
        except SupervisorError as exc:
            self.state, self.failure_code, code = "failed", str(exc), 1
        except Exception as exc:
            # Exception contents can contain private configuration, so only
            # the exception class is recorded and no traceback is emitted.
            self.state, self.failure_code, code = "failed", f"internal_{type(exc).__name__}", 1
        finally:
            self.cleanup()
            self.status()
        return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-dir", required=True)
    parser.add_argument("--gateway-config", default="engineer-config.json")
    parser.add_argument("--tunnel-binary", required=True)
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    os.umask(0o077)
    root = Path(__file__).resolve().parents[1]
    try:
        base, config, binary, deadline = validate_paths(
            root, args.session_dir, args.gateway_config, args.tunnel_binary)
        supervisor = Supervisor(root, base, config, binary, deadline, args.port)
    except (SupervisorError, OSError) as exc:
        # Fixed failure code only. Never print parsed config or file contents.
        print(str(exc) if isinstance(exc, SupervisorError) else f"startup_{type(exc).__name__}")
        return 1
    signal.signal(signal.SIGTERM, supervisor.request_stop)
    signal.signal(signal.SIGINT, supervisor.request_stop)
    return supervisor.run()


if __name__ == "__main__":
    raise SystemExit(main())
