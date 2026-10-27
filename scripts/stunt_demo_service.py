"""Start the isolated Stunt API/worker outside terminal sessions.

Children have separate sessions and file logs; no terminal pipes stay open.
Nothing is installed at login. Failed services are not automatically restarted
and queued jobs are not resumed. The frontend and native engine are untouched.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def profile(runtime: Path) -> dict[str, str]:
    runtime = runtime.resolve()
    if not runtime.is_relative_to(ROOT / ".runtime"):
        raise ValueError("Select an isolated runtime inside this repository's .runtime")
    config = json.loads((runtime / "environment.json").read_text())
    allowed = {"ADE2E_DATA_DIR", "ADE2E_SEED_EXAMPLES", "ADE2E_CORS_ORIGINS"}
    if not isinstance(config, dict) or any(
        not isinstance(value, str) or not (key in allowed or key.startswith("AXIOM_CARLA_STUNT_"))
        for key, value in config.items()
    ):
        raise ValueError("Unexpected environment key or non-string value in runtime profile")
    if Path(config.get("ADE2E_DATA_DIR", "")).resolve() != runtime / "data":
        raise ValueError("The Stunt profile must use its own runtime/data directory")
    if config.get("ADE2E_SEED_EXAMPLES") != "0":
        raise ValueError("Example seeding must be disabled for retained Stunt results")
    return config


def identity(pid: int) -> str:
    response = subprocess.run(["ps", "-p", str(pid), "-o", "lstart=", "-o", "command="],
                              capture_output=True, text=True, timeout=3)
    return response.stdout.strip() if response.returncode == 0 else ""


def status(runtime: Path) -> dict:
    record = runtime / "services.json"
    saved = json.loads(record.read_text()) if record.exists() else {}
    result = {"automatic_restart": False, "services": {}}
    for role in ("api", "worker"):
        item = saved.get(role, {})
        running = bool(item.get("identity") and identity(item["pid"]) == item["identity"])
        result["services"][role] = {**item, "running": running, "log": str(runtime / (role + ".log"))}
    return result


def start(runtime: Path, port: int) -> dict:
    with (runtime / "services.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return start_locked(runtime, port)


def start_locked(runtime: Path, port: int) -> dict:
    from internal_platform import inspect_workspace
    config = profile(runtime)
    current = status(runtime)["services"]
    if any(item["running"] for item in current.values()):
        if all(item["running"] for item in current.values()):
            return status(runtime)
        raise ValueError("Only one owned service is running; inspect logs and stop that owned process before starting both")
    # Reject active/queued work, uncertain engine ownership and schema migration.
    inspect_workspace(Path(config["ADE2E_DATA_DIR"]))
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", port))
    started, saved = [], {}
    try:
        for role in ("api", "worker"):
            log = runtime / (role + "-bootstrap.log")
            fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "ab") as stream:
                environment = {key: value for key, value in os.environ.items()
                               if not key.startswith(("ADE2E_", "AXIOM_", "PYTHON"))}
                environment.update(PYTHONUNBUFFERED="1", PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
                process = subprocess.Popen(
                    [str(ROOT / ".venv/bin/python"), "-B", str(Path(__file__).resolve()),
                     "_" + role, "--runtime", str(runtime), "--port", str(port)],
                    cwd=ROOT, env=environment, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                    start_new_session=True, close_fds=True,
                )
            started.append(process)
            saved[role] = {"pid": process.pid, "identity": identity(process.pid), "port": port}
            if not saved[role]["identity"]:
                raise RuntimeError(f"Could not establish {role} process identity")
        # Require API readiness and this exact worker's fresh heartbeat, not just PIDs.
        import sqlite3
        opener = build_opener(ProxyHandler({}))
        deadline = time.monotonic() + 30
        ready = False
        while time.monotonic() < deadline and all(process.poll() is None for process in started):
            try:
                with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                    api_ready = response.status == 200 and json.load(response).get("status") == "ok"
                database = Path(config["ADE2E_DATA_DIR"]) / "platform.db"
                from contextlib import closing
                with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
                    worker_ids = connection.execute(
                        "SELECT id FROM execution_workers WHERE state='idle' "
                        "AND julianday(heartbeat_at) > julianday('now', '-15 seconds')",
                    ).fetchall()
                    # worker_identity uses <prefix>_<hostname>_<pid>_<nonce>.
                    # LIKE underscores are wildcards, so substring matching can
                    # mistake another PID's heartbeat for this new process.
                    worker_ready = any(
                        len(parts := worker_id.rsplit("_", 2)) == 3
                        and parts[1] == str(saved["worker"]["pid"])
                        for (worker_id,) in worker_ids
                    )
                ready = api_ready and worker_ready
            except (OSError, ValueError, sqlite3.Error):
                ready = False
            if ready:
                break
            time.sleep(.2)
        if not ready:
            raise RuntimeError("Stunt services did not become ready; inspect api/worker logs")
        temporary = runtime / "services.json.tmp"
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600), "w") as stream:
            json.dump(saved, stream, indent=2)
        temporary.replace(runtime / "services.json")
    except Exception:
        for process in reversed(started):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        raise
    return status(runtime)


def child(runtime: Path, role: str, port: int) -> int:
    config = profile(runtime)
    # An unrelated parent shell's workspace/token cannot override this profile.
    for key in list(os.environ):
        if key.startswith(("ADE2E_", "AXIOM_")):
            del os.environ[key]
    os.environ.update(config)
    os.environ.update(PYTHONUNBUFFERED="1", PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    for source in ("backend/src", "sdk/python/src", "integrations/carla/src"):
        sys.path.insert(0, str(ROOT / source))
    if role == "api":
        import uvicorn
        from ade2e.worker_supervision import configure_worker_logging
        configure_worker_logging(runtime / "api.log")
        uvicorn.run("ade2e.main:app", host="127.0.0.1", port=port, log_config=None, access_log=False)
        return 0
    from ade2e.worker import main as worker_main
    sys.argv = [sys.argv[0], "--log-file", str(runtime / "worker.log")]
    return worker_main()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "status", "_api", "_worker"))
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8250)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Use a nonprivileged TCP port")
    runtime = args.runtime.resolve()
    try:
        if args.action.startswith("_"):
            return child(runtime, args.action[1:], args.port)
        profile(runtime)
        result = start(runtime, args.port) if args.action == "start" else status(runtime)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
