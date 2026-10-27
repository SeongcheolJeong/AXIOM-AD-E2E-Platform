"""Isolated HIL beta server; native execution requires an explicit opt-in.

Example (source API may remain running; SQLite's backup pins a read snapshot)::

    python e2e/fixtures/hil_sim_beta_server.py .runtime/hil-beta-new \
        --source-workspace .runtime/hil-demo-20261010/workspace --prepare-only
    python e2e/fixtures/hil_sim_beta_server.py .runtime/hil-beta-new --port 8075

To enable the HIL-only queue worker in the SAME workspace, add --enable-native
--engine-env-file /absolute/private/hil-engine.json. This owner-only JSON object
must contain exactly AXIOM_CARLA_HIL_{SSH,PYTHON,SCRIPT,DIR,SPOOL,OWNER,PORT}, all
as strings. Inherited engine configuration is never used. Default startup stays
offline; configured startup does not probe or start the remote CARLA engine.

Only published experiment artifacts are copied. Attempts, staging and engine
spools are excluded. Original receipt/project IDs and artifact bytes stay exact.
Copied participant credentials are revoked only in the NEW database; provision
new participant tokens through its normal API. A fresh private bootstrap token
is saved locally and is never printed. This helper is not a general backup tool.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import asynccontextmanager, closing
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import sqlite3
import stat
import sys
import threading


ROOT = Path(__file__).resolve().parents[2]
VERSION = 'hil-beta-isolated-runtime/1'
HIL_ENGINE_KEYS = frozenset('AXIOM_CARLA_HIL_' + suffix for suffix in
                          ('SSH', 'PYTHON', 'SCRIPT', 'DIR', 'SPOOL', 'OWNER', 'PORT'))


def engine_environment(*, enable_native=False, engine_env_file=None):
    """Read only a deliberately selected, private HIL transport configuration."""
    if bool(engine_env_file) != enable_native:
        raise ValueError('--enable-native and --engine-env-file must be supplied together')
    if not enable_native:
        return {}
    path = safe_path(engine_env_file)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'r') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > 16384):
            raise ValueError('Engine configuration must be an owner-only regular JSON file, at most 16 KiB')
        def unique_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Engine configuration contains a duplicate key')
                result[key] = value
            return result
        value = json.load(stream, object_pairs_hook=unique_keys)
    if (not isinstance(value, dict) or set(value) != HIL_ENGINE_KEYS
            or any(not isinstance(item, str) or not item.strip() or '\n' in item or '\0' in item
                   for item in value.values())):
        raise ValueError('Engine configuration requires exactly the seven AXIOM_CARLA_HIL transport keys as nonempty strings')
    if not re.fullmatch(r'[0-9]{4,5}', value['AXIOM_CARLA_HIL_PORT']):
        raise ValueError('HIL engine port must be a decimal nonprivileged port')
    return value


def run_hil_once(app, worker_id):
    """Use the normal durable claim/heartbeat/publication stack for HIL only."""
    from ade2e import jobs
    from ade2e.hil_batches import advance
    from ade2e.worker import run_claim
    db = app.state.db
    jobs.expire_jobs(db)
    jobs.touch_worker(db, worker_id, 'idle')
    advance(db, app.state.experiment_registry)
    metadata = app.state.experiment_registry.get('carla-software-dut').metadata()
    capabilities = {('experiment', metadata.id, metadata.version)} if metadata.status == 'available' else set()
    claim = jobs.claim_job(db, worker_id, lease_seconds=30, capabilities=capabilities)
    if claim is not None:
        return run_claim(db, app.state.registry, claim, lease_seconds=30,
                         runtime_registry=app.state.runtime_registry,
                         sensor_registry=app.state.sensor_registry,
                         evaluator_registry=app.state.evaluator_registry,
                         stack_registry=app.state.stack_registry,
                         experiment_registry=app.state.experiment_registry)
    return None


def install_native_worker(app):
    """Drain this process's worker before the API closes lease maintenance/DB."""
    from ade2e import jobs
    from ade2e.worker import worker_identity
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with original_lifespan(application):
            identity, stopping = worker_identity('hil-beta'), threading.Event()
            jobs.touch_worker(app.state.db, identity, 'idle')
            app.state.hil_native_worker_id = identity
            def work():
                try:
                    while not stopping.is_set():
                        try:
                            run_hil_once(app, identity)
                        except Exception:
                            logging.getLogger(__name__).exception('HIL worker polling failed; no engine call is automatically retried')
                        stopping.wait(.5)
                finally:
                    jobs.touch_worker(app.state.db, identity, 'stopped')
            thread = threading.Thread(target=work, name=identity, daemon=False)
            thread.start()
            try:
                yield
            finally:
                stopping.set()
                drain = asyncio.create_task(asyncio.to_thread(thread.join))
                try:
                    await asyncio.shield(drain)
                except asyncio.CancelledError:
                    while not drain.done():
                        try:
                            await asyncio.shield(drain)
                        except asyncio.CancelledError:
                            pass
                    raise
    app.router.lifespan_context = lifespan


def safe_path(value: Path, *, directory=False):
    path = Path(value).absolute()
    if path != path.resolve() or path.is_symlink():
        raise ValueError('Symlinked or noncanonical runtime/source paths are unsupported')
    if directory and not path.is_dir():
        raise ValueError('Expected an existing directory')
    return path


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def private_file(path, content):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(content)


def copy_artifact(source, destination):
    """APFS clone when available, otherwise an independent streamed copy."""
    safe_path(source)
    if not stat.S_ISREG(source.stat().st_mode):
        raise ValueError('Published artifact must be a regular file')
    if destination.exists() or destination.is_symlink():
        raise ValueError('Artifact destination already exists')
    if sys.platform == 'darwin':
        library = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
        clone = library.clonefile
        clone.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
        clone.restype = ctypes.c_int
        if clone(os.fsencode(source), os.fsencode(destination), 0) == 0:
            destination.chmod(0o600)
            return 'apfs_clone'
        if destination.exists():
            raise ValueError('Failed clone left an unexpected destination')
    with source.open('rb') as incoming, destination.open('xb') as outgoing:
        shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
    destination.chmod(0o600)
    return 'independent_copy'


def inspect_snapshot(connection):
    connection.execute('PRAGMA trusted_schema=OFF')
    if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
        raise ValueError('Source snapshot failed SQLite quick_check')
    if connection.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('Source snapshot has broken foreign keys')
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = {'execution_jobs', 'execution_resources', 'experiments', 'experiment_artifacts', 'experiment_executions'}
    if not required <= tables:
        raise ValueError('Source must be a versioned HIL experiment workspace')
    if connection.execute("SELECT COUNT(*) FROM execution_jobs WHERE status NOT IN ('completed','failed','cancelled','interrupted')").fetchone()[0]:
        raise ValueError('Source has active or unknown-status jobs; no runtime will be served')
    if connection.execute("SELECT COUNT(*) FROM execution_resources WHERE state != 'idle'").fetchone()[0]:
        raise ValueError('Source has reserved/quarantined engine resources')
    if connection.execute("SELECT COUNT(*) FROM experiments WHERE kind != 'hil_demo'").fetchone()[0]:
        raise ValueError('This narrow snapshot helper only supports a HIL experiment workspace')
    # Refuse a partial copy of another product domain's retained file evidence.
    for table in ('artifacts', 'recordings', 'perception_model_artifacts', 'dataset_images',
                  'perception_frames', 'stack_replay_ticks', 'execution_recoveries'):
        if table in tables and connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]:
            raise ValueError(f'Unsupported retained file domain: {table}; use a full workspace backup')
    if 'verification_attempts' in tables and connection.execute('SELECT COUNT(*) FROM verification_attempts WHERE manifest IS NULL').fetchone()[0]:
        raise ValueError('Source contains an unfinished verification attempt')
    artifacts = connection.execute('''SELECT a.storage_key,a.sha256,a.size_bytes
        FROM experiment_artifacts a JOIN experiment_executions e ON e.job_id=a.job_id
        WHERE e.manifest IS NOT NULL ORDER BY a.storage_key''').fetchall()
    if len(artifacts) != connection.execute('SELECT COUNT(*) FROM experiment_artifacts').fetchone()[0]:
        raise ValueError('Source has unpublished artifact rows')
    receipts = connection.execute('SELECT job_id,manifest,manifest_hash FROM experiment_executions').fetchall()
    for identity, manifest, checksum in receipts:
        if manifest is None or hashlib.sha256(manifest).hexdigest() != checksum:
            raise ValueError(f'Unsealed or corrupt execution receipt: {identity}')
    return artifacts, {identity: checksum for identity, _, checksum in receipts}


def prepare_runtime(runtime: Path, source_workspace: Path | None = None):
    runtime = safe_path(runtime)
    if runtime.exists():
        raise ValueError('Preparation requires a NEW runtime directory; nothing is overwritten')
    source = safe_path(source_workspace, directory=True) if source_workspace else None
    if source and (runtime.is_relative_to(source) or source.is_relative_to(runtime)):
        raise ValueError('Source and destination must not contain each other')
    runtime.mkdir(mode=0o700, parents=True)
    access, workspace = runtime / 'access', runtime / 'workspace'
    access.mkdir(mode=0o700)
    workspace.mkdir(mode=0o700)
    report = dict(schema_version=VERSION, source_workspace=str(source) if source else None,
                  workspace=str(workspace), source_modified=False, copied_attempt_directories=False,
                  native_execution_enabled=False, worker_started=False, receipt_hashes={},
                  artifact_count=0, artifact_bytes=0, copy_methods={}, copied_tokens_revoked=0)
    if source:
        database = safe_path(source / 'platform.db')
        if not database.is_file():
            raise ValueError('Source platform.db is missing')
        for suffix in ('-wal', '-shm'):
            sidecar = source / ('platform.db' + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                safe_path(sidecar)
        destination = workspace / 'platform.db'
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as incoming, closing(sqlite3.connect(destination)) as output:
            incoming.execute('PRAGMA query_only=ON')
            incoming.backup(output)
            artifacts, receipts = inspect_snapshot(output)
            report['receipt_hashes'] = receipts
            published = workspace / 'experiments' / 'artifacts'
            published.mkdir(parents=True, mode=0o700)
            methods = Counter()
            for key, checksum, size in artifacts:
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,180}', key) or not re.fullmatch(r'[a-f0-9]{64}', checksum) or type(size) is not int or size < 0:
                    raise ValueError('Invalid published artifact descriptor')
                old, new = source / 'experiments' / 'artifacts' / key, published / key
                methods[copy_artifact(old, new)] += 1
                if new.stat().st_size != size or digest(new) != checksum:
                    raise ValueError(f'Published artifact differs from its receipt: {key}')
            now = datetime.now(timezone.utc).isoformat()
            cursor = output.execute('UPDATE team_project_tokens SET revoked_at=? WHERE revoked_at IS NULL', (now,))
            report['copied_tokens_revoked'] = cursor.rowcount
            output.commit()
            report.update(artifact_count=len(artifacts), artifact_bytes=sum(row[2] for row in artifacts), copy_methods=dict(methods))
        destination.chmod(0o600)
        report['snapshot_database_sha256'] = digest(destination)
    private_file(access / 'operator-token', secrets.token_urlsafe(32) + '\n')
    private_file(runtime / 'snapshot.json', json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


def create_beta_app(runtime: Path, ui: Path, *, enable_native=False, engine_env_file=None):
    engine_env = engine_environment(enable_native=enable_native, engine_env_file=engine_env_file)
    runtime, ui = safe_path(runtime, directory=True), safe_path(ui, directory=True)
    if not (runtime / 'snapshot.json').is_file():
        raise ValueError('Runtime preparation is incomplete; preserve it and use a new destination')
    token_file = safe_path(runtime / 'access' / 'operator-token')
    if not token_file.is_file() or stat.S_IMODE(token_file.stat().st_mode) & 0o077:
        raise ValueError('Operator token must be an owner-only regular file')
    if not (ui / 'index.html').is_file():
        raise ValueError('Build the frontend before starting the beta server')
    os.environ['AXIOM_APP_FACTORY_ONLY'] = '1'
    for key in list(os.environ):
        if key.startswith(('AXIOM_CARLA_', 'ADE2E_CARLA_')):
            del os.environ[key]
    os.environ.update(engine_env)
    from ade2e.database import Settings
    from ade2e.adapters import Registry
    from ade2e.experiment_runtime import ExperimentRegistry
    from ade2e.inference_runtime import RuntimeRegistry
    from ade2e.evaluator_runtime import EvaluatorRegistry
    from ade2e.stack_runtime import StackRegistry
    from ade2e.main import create_app
    from axiom_carla.hil import HilExperimentHandler
    from fastapi.staticfiles import StaticFiles
    workspace = runtime / 'workspace'
    settings = Settings(data_dir=workspace, database_url=f'sqlite:///{workspace / "platform.db"}',
                        api_token=token_file.read_text().strip(), team_mode=True,
                        seed_examples=False, cors_origins=[])
    experiments = ExperimentRegistry(load_plugins=False)
    app = create_app(settings, registry=Registry(load_plugins=False),
                     runtime_registry=RuntimeRegistry(load_plugins=False),
                     evaluator_registry=EvaluatorRegistry(load_plugins=False),
                     stack_registry=StackRegistry(load_plugins=False),
                     experiment_registry=experiments)
    handler = HilExperimentHandler(database=app.state.db)
    if enable_native:
        handler.transport.config()  # Local shape validation only; never probes the engine.
    else:
        assert handler.metadata().status == 'not_configured'
    experiments.register(handler)
    app.state.hil_native_enabled = enable_native
    app.state.hil_native_worker_id = None
    if enable_native:
        install_native_worker(app)
    app.mount('/', StaticFiles(directory=ui, html=True), name='hil-beta-ui')
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime', type=Path)
    parser.add_argument('--port', type=int, default=8075)
    parser.add_argument('--source-workspace', type=Path)
    parser.add_argument('--ui', type=Path, default=ROOT / 'frontend' / 'dist')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--enable-native', action='store_true', help='Start the HIL-only queue worker; the remote engine must already be ready')
    parser.add_argument('--engine-env-file', type=Path, help='Owner-only JSON containing exactly the seven AXIOM_CARLA_HIL transport settings')
    args = parser.parse_args()
    try:
        engine_environment(enable_native=args.enable_native, engine_env_file=args.engine_env_file)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    if args.prepare_only and args.enable_native:
        parser.error('--prepare-only cannot enable native execution')
    if not 1024 <= args.port <= 65535:
        parser.error('Use a nonprivileged loopback port')
    # Match listener semantics without replacing any existing service.
    with socket.socket() as check:
        check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            check.bind(('127.0.0.1', args.port))
        except OSError:
            parser.error('Port is occupied; choose another port without stopping its owner')
    if args.source_workspace or not args.runtime.exists():
        report = prepare_runtime(args.runtime, args.source_workspace)
        print(json.dumps(report, ensure_ascii=False), flush=True)
    if args.prepare_only:
        return
    app = create_beta_app(args.runtime, args.ui, enable_native=args.enable_native, engine_env_file=args.engine_env_file)
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
