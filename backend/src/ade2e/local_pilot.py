"""Single-operator, loopback-only pilot with a frozen UI and explicit runtimes.

This is not an Internet server, OS sandbox, signed release, or multi-user RBAC.
Tokens/logs live outside both application files and the backed-up data directory.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import fcntl
import hashlib
import http.client
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid

VERSION = "axiom-local-pilot/1"
SOURCE_PATHS = ("backend/src", "sdk/python/src", "integrations/onnx/src", "integrations/coco/src")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_bundle(root):
    root = Path(root).absolute()
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Bundle must be an existing real directory")
    path = root / "bundle.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("Missing or oversized bundle manifest")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate bundle manifest key")
            result[key] = value
        return result
    manifest = json.loads(path.read_bytes(), object_pairs_hook=unique)
    if manifest.get("schema_version") != VERSION or not isinstance(manifest.get("files"), dict):
        raise ValueError("Unsupported bundle manifest")
    files = manifest["files"]
    if not files or len(files) > 10000 or "ui/index.html" not in files:
        raise ValueError("Missing built UI or invalid file-count bound")
    actual = set()
    for base, dirs, names in os.walk(root, followlinks=False):
        for name in [*dirs, *names]:
            item = Path(base) / name
            if item.is_symlink():
                raise ValueError("Symlinks are not allowed in the frozen bundle")
        for name in names:
            item = Path(base) / name
            if not item.is_file():
                raise ValueError("Bundle contains a nonregular file")
            actual.add(item.relative_to(root).as_posix())
    if actual != set(files) | {"bundle.json"}:
        raise ValueError("Bundle contains missing or unexpected files; rebuild instead of editing it")
    for name, entry in files.items():
        if (not isinstance(name, str) or not name or "\\" in name
                or PurePosixPath(name).is_absolute() or any(p in ("", ".", "..") for p in name.split("/"))):
            raise ValueError("Unsafe bundle path")
        item = root / name
        if item.stat().st_size != entry["size_bytes"] or file_hash(item) != entry["sha256"]:
            raise ValueError("Bundle checksum differs: " + name)
    return manifest


def explicit_registries():
    # No entry-point discovery: installed CARLA/custom adapters are not loaded.
    from .adapters import Registry
    from .inference_runtime import RuntimeRegistry
    from .evaluator_runtime import EvaluatorRegistry
    from .sensor_decoders import SensorDecoderRegistry
    from .stack_runtime import StackRegistry
    from axiom_onnx import OnnxCpuRuntime
    from axiom_coco import CocoBboxEvaluator
    inference, evaluator = RuntimeRegistry(load_plugins=False), EvaluatorRegistry(load_plugins=False)
    inference.register(OnnxCpuRuntime(), replace=True)
    evaluator.register(CocoBboxEvaluator(), replace=True)
    return dict(registry=Registry(load_plugins=False), runtime_registry=inference,
                evaluator_registry=evaluator, sensor_registry=SensorDecoderRegistry(),
                stack_registry=StackRegistry(load_plugins=False))


def offline_data_check(data):
    """Inspect a private DB/WAL copy; never open the original DB with SQLite."""
    if data.is_symlink():
        raise ValueError("Data directory must not be a symlink")
    if not data.exists():
        if not data.parent.is_dir():
            raise ValueError("Create the data directory's parent first")
        return {"state": "new", "queued_supported_jobs": 0}
    if not data.is_dir():
        raise ValueError("Data path is not a directory")
    database = data / "platform.db"
    if not database.exists():
        if any(data.iterdir()):
            raise ValueError("Existing nonempty directory has no workspace database")
        return {"state": "new", "queued_supported_jobs": 0}
    from .workspace_backup import SUPPORTED_SCHEMAS, assert_offline
    from .database import SCHEMA_VERSION
    assert_offline(data, offline=True)
    with tempfile.TemporaryDirectory(prefix="axiom-pilot-check-") as temp:
        for suffix in ("", "-wal", "-shm", "-journal"):
            source = data / ("platform.db" + suffix)
            if source.is_symlink():
                raise ValueError("Symlinked SQLite files are not supported")
            if source.exists():
                shutil.copyfile(source, Path(temp) / source.name)
        with closing(sqlite3.connect(str(Path(temp) / "platform.db"))) as db:
            db.execute("PRAGMA trusted_schema=OFF")
            if db.execute("PRAGMA quick_check").fetchall() != [("ok",)] or db.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Workspace database integrity check failed")
            versions = db.execute("SELECT version FROM schema_versions").fetchall()
            if len(versions) != 1 or versions[0][0] not in SUPPORTED_SCHEMAS:
                raise ValueError("Pilot accepts schema11 through schema18 only; back up before the additive schema18 migration")
            if db.execute("SELECT COUNT(*) FROM execution_jobs WHERE status IN ('running','cancel_requested')").fetchone()[0]:
                raise ValueError("Active or interrupted ownership needs reconciliation before pilot launch")
            if db.execute("SELECT COUNT(*) FROM execution_resources WHERE state != 'idle'").fetchone()[0]:
                raise ValueError("Reserved/quarantined engine needs reconciliation; pilot does not clear it")
            pending = db.execute("SELECT kind,adapter_id,adapter_version FROM execution_jobs WHERE status='queued'").fetchall()
            regs = explicit_registries()
            allowed = {("simulation", x.id, x.version) for x in regs["registry"].list() if x.status == "available"}
            allowed |= {("perception", x.id, x.version) for x in regs["runtime_registry"].list() if x.status == "available"}
            allowed |= {("evaluation", x.id, x.version) for x in regs["evaluator_registry"].list() if x.status == "available"}
            allowed.add(("stack_replay", "recorded-stack", "1"))
            if any(row not in allowed for row in pending):
                raise ValueError("Pending job needs an adapter/version excluded from this local pilot")
            unfinished_plans = []
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='verification_attempts'").fetchone():
                unfinished_plans = db.execute("SELECT payload,payload_hash FROM verification_attempts WHERE manifest IS NULL").fetchall()
            for raw, expected_hash in unfinished_plans:
                try:
                    receipt = json.loads(raw)
                    if hashlib.sha256(canonical(receipt)).hexdigest() != expected_hash or receipt['status'] != 'running':
                        raise ValueError('Unfinished verification receipt integrity check failed')
                    for kind, key in (("perception", "runtime"), ("evaluation", "evaluator")):
                        capability = receipt['preview'][key]
                        if (kind, capability['id'], capability['version']) not in allowed:
                            raise ValueError('Unfinished verification plan needs an adapter/version excluded from this local pilot')
                except (KeyError, TypeError, json.JSONDecodeError) as error:
                    raise ValueError('Malformed unfinished verification receipt') from error
    assert_offline(data, offline=True)
    return {"state": f"existing_schema{versions[0][0]}", "queued_supported_jobs": len(pending),
            "unfinished_verification_attempts": len(unfinished_plans), "migration_on_start": versions != [(SCHEMA_VERSION,)]}


def preflight(bundle, data, state, port):
    if sys.version_info < (3, 12) or sys.platform not in ("darwin", "linux"):
        raise ValueError("Local pilot requires Python3.12+ on macOS/Linux")
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Use a nonprivileged TCP port from1024 through65535")
    paths = [Path(p).absolute() for p in (bundle, data, state)]
    for index, first in enumerate(paths):
        for second in paths[index + 1:]:
            if first.resolve().is_relative_to(second.resolve()) or second.resolve().is_relative_to(first.resolve()):
                raise ValueError("Application, data and access-state directories must be separate")
    manifest = verify_bundle(paths[0])
    for name, expected in manifest["dependencies"].items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual = "not_installed"
        if actual != expected:
            raise ValueError(f"Dependency {name}: expected {expected}, found {actual}; install requirements-pilot.txt")
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as error:
            raise ValueError(f"Port {port} is occupied; choose another port, do not stop its owner") from error
    state = paths[2]
    if state.exists() and (state.is_symlink() or not state.is_dir() or stat.S_IMODE(state.stat().st_mode) & 0o077):
        raise ValueError("Access-state directory must be private (mode0700); permissions are not changed automatically")
    if not state.exists() and not state.parent.is_dir():
        raise ValueError("Create the access-state directory's parent first")
    return {"schema_version": VERSION, "status": "ready", "bundle_manifest_sha256": file_hash(paths[0] / "bundle.json"),
            "bundle_files": len(manifest["files"]), "data": offline_data_check(paths[1]),
            "url": f"http://127.0.0.1:{port}", "auth_required": True, "node_required_at_runtime": False,
            "remote_engine_enabled": False, "scope": "Trusted single local operator; not multi-user isolation or sandbox"}


def token_file(state):
    state.mkdir(mode=0o700, exist_ok=True)
    if state.is_symlink() or stat.S_IMODE(state.stat().st_mode) & 0o077:
        raise ValueError("Token directory must be private")
    path = state / "api-token"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w") as stream:
            stream.write(secrets.token_urlsafe(32) + "\n")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077 or not 32 <= info.st_size <= 129):
            raise ValueError("Token must be an owner-only regular file (mode0600)")
        raw = stream.read(130)
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}\n?", raw):
        raise ValueError("Invalid pilot token; do not paste secrets into command arguments")
    value = raw.rstrip("\n")
    return path, value


def child_environment(bundle, data, state, port, instance, token, ready):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ADE2E_", "AXIOM_", "PYTHON"))}
    env.update(PYTHONPATH=os.pathsep.join(str(bundle / p) for p in SOURCE_PATHS),
               PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1", PYTHONNOUSERSITE="1",
               AXIOM_APP_FACTORY_ONLY="1", ADE2E_DATA_DIR=str(data), ADE2E_API_TOKEN=token,
               ADE2E_SEED_EXAMPLES="0", ADE2E_CORS_ORIGINS="",
               AXIOM_PILOT_BUNDLE=str(bundle), AXIOM_PILOT_STATE=str(state),
               AXIOM_PILOT_PORT=str(port), AXIOM_PILOT_INSTANCE=instance,
               AXIOM_PILOT_WORKER_READY=str(ready))
    return env


def create_pilot_app():
    from fastapi import Header, HTTPException
    from starlette.middleware.trustedhost import TrustedHostMiddleware
    from starlette.responses import JSONResponse
    from starlette.staticfiles import StaticFiles
    from .database import Settings
    from .main import create_app
    token = os.environ["ADE2E_API_TOKEN"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
        raise ValueError("Pilot authentication cannot be disabled")
    root = Path(os.environ["AXIOM_PILOT_BUNDLE"])
    manifest = verify_bundle(root)
    port = int(os.environ["AXIOM_PILOT_PORT"])
    origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    app = create_app(Settings(data_dir=Path(os.environ["ADE2E_DATA_DIR"]), database_url=None,
                              api_token=token, seed_examples=False, cors_origins=[]), **explicit_registries())
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"], www_redirect=False)

    @app.middleware("http")
    async def local_boundary(request, call_next):
        origin = request.headers.get("origin")
        if (origin is not None and origin not in origins) or request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "Cross-origin access is disabled in the local pilot"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        if request.url.path in ("/", "/index.html"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; "
                "worker-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; object-src 'none'")
        return response

    @app.get("/api/v1/pilot-status", tags=["system"])
    def pilot_status(authorization: str | None = Header(default=None)):
        if not secrets.compare_digest((authorization or "").encode("utf-8"), ("Bearer " + token).encode("utf-8")):
            raise HTTPException(401, "A valid bearer token is required")
        return {"schema_version": VERSION, "instance_id": os.environ["AXIOM_PILOT_INSTANCE"],
                "bundle_manifest_sha256": file_hash(root / "bundle.json"), "auth_required": True,
                "frontend": "built_static", "remote_engine_enabled": False,
                "bundle_label": manifest.get("label", "local pilot")}

    app.mount("/", StaticFiles(directory=root / "ui", html=True, follow_symlink=False), name="pilot-ui")
    return app


def worker_main():
    from .database import Database, Settings
    from . import jobs
    from .worker import run_once, worker_identity
    regs = explicit_registries()
    db = Database(Settings(data_dir=Path(os.environ["ADE2E_DATA_DIR"]), database_url=None, seed_examples=False))
    db.initialize()
    identity, stopped = worker_identity("pilot"), threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopped.set())
    jobs.touch_worker(db, identity, "idle")
    Path(os.environ["AXIOM_PILOT_WORKER_READY"]).write_bytes(canonical({"instance_id": os.environ["AXIOM_PILOT_INSTANCE"], "worker_id": identity}))
    try:
        while not stopped.is_set():
            run_once(db, worker_id=identity, **regs)
            stopped.wait(.2)
    finally:
        jobs.touch_worker(db, identity, "stopped")
        db.engine.dispose()
    return 0


def start(bundle, data, state, port):
    bundle, data, state = [Path(p).absolute() for p in (bundle, data, state)]
    report = preflight(bundle, data, state, port)
    bundle, data, state = [p.resolve() for p in (bundle, data, state)]
    data.mkdir(mode=0o700, exist_ok=True)
    lock_fd = os.open(data / ".axiom-pilot.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("Another pilot owns this data directory") from error
        token_path, token = token_file(state)
        instance = uuid.uuid4().hex
        ready = state / (instance + ".worker-ready.json")
        env = child_environment(bundle, data, state, port, instance, token, ready)
        children, logs, stopped, forced = [], [], threading.Event(), []
        old_handlers = {s: signal.signal(s, lambda *_: stopped.set()) for s in (signal.SIGINT, signal.SIGTERM)}
        code = 1
        try:
            for role in ("server", "worker"):
                log = (state / (instance + "." + role + ".log")).open("xb")
                logs.append(log)
                child = subprocess.Popen([sys.executable, "-B", "-m", "ade2e.local_pilot", "_" + role],
                    env=env, cwd=state, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                children.append(child)
                deadline = time.monotonic() + 40
                ok = False
                while time.monotonic() < deadline and child.poll() is None and not stopped.is_set():
                    try:
                        if role == "server":
                            # Never use proxy settings or follow redirects with
                            # the local secret, even if the port owner changes.
                            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=.8)
                            try:
                                connection.request("GET", "/api/v1/pilot-status", headers={"Authorization": "Bearer " + token})
                                response = connection.getresponse()
                                raw = response.read(16384)
                                ok = response.status == 200 and json.loads(raw).get("instance_id") == instance
                            finally:
                                connection.close()
                        else:
                            ok = ready.is_file() and json.loads(ready.read_bytes()).get("instance_id") == instance
                    except (OSError, ValueError, http.client.HTTPException):
                        pass
                    if ok:
                        break
                    stopped.wait(.15)
                if not ok:
                    raise RuntimeError(f"Pilot {role} did not become ready; inspect its private log")
            print(json.dumps({**report, "status": "running", "instance_id": instance,
                "token_file": str(token_path), "log_directory": str(state),
                "instructions": "Open Workspace settings and enter the token from the private file. Ctrl-C drains/stops this pilot only."}), flush=True)
            while not stopped.wait(.2) and all(child.poll() is None for child in children):
                pass
            code = 0 if stopped.is_set() else 1
        finally:
            # Signal only owned children. Worker finishes its current bounded
            # local job before exit; a forced stop is explicitly unsuccessful.
            for child in reversed(children):
                if child.poll() is None:
                    child.terminate()
            deadline = time.monotonic() + 135
            for child in children:
                try:
                    child.wait(timeout=max(.1, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    if child.poll() is None and os.getpgid(child.pid) == child.pid:
                        os.killpg(child.pid, signal.SIGKILL)
                        forced.append(child.pid)
                        child.wait(timeout=10)
            for log in logs:
                log.close()
            clean = code == 0 and not forced and all(p.returncode == 0 for p in children)
            outcome = {"schema_version": VERSION, "instance_id": instance,
                "status": "stopped" if clean else "failed",
                "children": [{"pid": p.pid, "exit_code": p.returncode} for p in children],
                "forced_stop_pids": forced, "recovery_may_be_required": not clean}
            (state / (instance + ".result.json")).write_bytes(canonical(outcome))
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)
            print(json.dumps(outcome), flush=True)
        return 0 if clean else 1


def assert_source_bound():
    """Never silently execute the editable checkout behind a staged bundle."""
    import ade2e, axiom_sdk, axiom_onnx, axiom_coco
    root = Path(os.environ["AXIOM_PILOT_BUNDLE"]).resolve()
    for module in (ade2e, axiom_sdk, axiom_onnx, axiom_coco):
        if not Path(module.__file__).resolve().is_relative_to(root):
            raise ValueError("Runtime module escaped the frozen application bundle: " + module.__name__)


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "_server":
        assert_source_bound()
        import uvicorn
        shutdown_seen = threading.Event()
        # Uvicorn restores and re-raises captured signals after graceful ASGI
        # shutdown. Our subprocess wrapper handles that forwarded signal so a
        # completed shutdown returns0 instead of looking like an OS crash.
        previous = {s: signal.signal(s, lambda *_: shutdown_seen.set()) for s in (signal.SIGTERM, signal.SIGINT)}
        try:
            app = create_pilot_app()
            if not shutdown_seen.is_set():
                uvicorn.run(app, host="127.0.0.1", port=int(os.environ["AXIOM_PILOT_PORT"]),
                            proxy_headers=False, access_log=False, timeout_graceful_shutdown=10)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        return 0
    if len(sys.argv) == 2 and sys.argv[1] == "_worker":
        assert_source_bound()
        return worker_main()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "start"))
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=5273)
    args = parser.parse_args()
    try:
        if args.command == "check":
            print(json.dumps(preflight(args.bundle, args.data_dir, args.state_dir, args.port)))
            return 0
        return start(args.bundle, args.data_dir, args.state_dir, args.port)
    except (ValueError, OSError, RuntimeError) as error:
        print(json.dumps({"status": "blocked", "error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
