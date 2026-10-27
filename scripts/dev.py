"""Start this workspace's API, queue worker and UI; stop them on Ctrl-C.

No daemon installation, global process cleanup or firewall modifications.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time

from connection_config import carla_environment

ROOT = Path(__file__).resolve().parents[1]


def configured_port(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer TCP port, got {raw!r}.") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{name} must be between 1 and 65535, got {port}.")
    return port


def main() -> int:
    python = ROOT / ".venv" / "bin" / "python"
    node_path_file = ROOT / ".runtime" / "node-path"
    node_paths = node_path_file.read_text().splitlines() if node_path_file.is_file() else []
    executable_path = os.pathsep.join([p for p in node_paths if Path(p).is_dir()] + [os.environ.get("PATH", "")])
    npm = shutil.which("npm", path=executable_path)
    if not python.is_file() or not npm or not (ROOT / "frontend/node_modules").exists():
        print("Run bash scripts/setup.sh first.", file=sys.stderr)
        return 1
    try:
        api_port = configured_port("AXIOM_API_PORT", 8000)
        ui_port = configured_port("AXIOM_UI_PORT", 5173)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if api_port == ui_port:
        print("AXIOM_API_PORT and AXIOM_UI_PORT must be different.", file=sys.stderr)
        return 1
    for port in (api_port, ui_port):
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                print(f"Port {port} is occupied. Stop its owner or use individual run commands in README.md.", file=sys.stderr)
                return 1
    try:
        environment = carla_environment(ROOT, {**os.environ, "PYTHONUNBUFFERED": "1", "PATH": executable_path})
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    processes = []
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, stop)
    try:
        processes.append(subprocess.Popen([str(python), "-m", "uvicorn", "ade2e.main:app", "--host", "127.0.0.1",
                                            "--port", str(api_port)], cwd=ROOT, env=environment, start_new_session=True))
        processes.append(subprocess.Popen([str(python), "-m", "ade2e.worker"], cwd=ROOT, env=environment, start_new_session=True))
        frontend_environment = {**environment, "AXIOM_API_TARGET": f"http://127.0.0.1:{api_port}"}
        processes.append(subprocess.Popen([npm, "run", "dev", "--", "--host", "127.0.0.1", "--port", str(ui_port), "--strictPort"],
                                           cwd=ROOT / "frontend", env=frontend_environment, start_new_session=True))
        print(f"\nAXIOM workspace  http://127.0.0.1:{ui_port}\nAPI reference    http://127.0.0.1:{api_port}/docs\nExecution queue worker is running. Ctrl-C stops this workspace's services.\n", flush=True)
        while not stopping and all(p.poll() is None for p in processes):
            time.sleep(0.25)
        return 0 if stopping else next((p.returncode for p in processes if p.returncode is not None), 1)
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
