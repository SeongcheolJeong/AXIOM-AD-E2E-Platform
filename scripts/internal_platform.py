"""Run a built internal workspace on one loopback origin with its queue worker.

Select data explicitly. Older workspaces are refused; --restore-backup restores
to a NEW destination and permits migration of that restored copy only. Tokens
are private files and never printed. Only children owned by this launcher stop.
"""
from __future__ import annotations
import argparse
from contextlib import closing
import fcntl
import hashlib
import http.client
import json
import logging
import os
from pathlib import Path
import secrets
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend' / 'src'))
VERSION = 'axiom-internal-workspace/1'
ENGINE_PREFIXES = ('ADE2E_CARLA_', 'AXIOM_CARLA_', 'AXIOM_ESMINI_', 'AXIOM_CAMERA_E2E_', 'AXIOM_CAMERA_SCENE_', 'AXIOM_VAD_NATIVE_', 'AXIOM_DRIVING_INPUT_', 'AXIOM_NEURAL_')

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()

def inspect_workspace(data: Path, *, resume_queued=False):
    from ade2e.database import SCHEMA_VERSION
    from ade2e.workspace_backup import assert_offline
    if data.is_symlink():
        raise ValueError('Workspace must be a real directory')
    if not data.exists() or data.is_dir() and not any(data.iterdir()):
        return {'state': 'new', 'schema_version': SCHEMA_VERSION, 'queued_jobs': 0}
    database = data / 'platform.db'
    if not data.is_dir() or database.is_symlink() or not database.is_file():
        raise ValueError('Nonempty workspace has no regular platform.db; choose a new directory')
    assert_offline(data, offline=True)
    with tempfile.TemporaryDirectory(prefix='axiom-internal-check-') as temporary:
        for suffix in ('', '-wal', '-shm', '-journal'):
            source = data / ('platform.db' + suffix)
            if source.is_symlink():
                raise ValueError('Symlinked database files are not supported')
            if source.exists():
                import shutil
                shutil.copyfile(source, Path(temporary) / source.name)
        with closing(sqlite3.connect(Path(temporary) / 'platform.db')) as connection:
            connection.execute('PRAGMA trusted_schema=OFF')
            if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)] or connection.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('Selected workspace failed its integrity check')
            versions = connection.execute('SELECT version FROM schema_versions').fetchall()
            if versions != [(SCHEMA_VERSION,)]:
                raise ValueError('Older/unsupported workspace is not migrated in place. Select a NEW --workspace with --restore-backup to migrate a verified restored copy.')
            if connection.execute("SELECT COUNT(*) FROM execution_jobs WHERE status IN ('running','cancel_requested')").fetchone()[0] or connection.execute("SELECT COUNT(*) FROM execution_resources WHERE state != 'idle'").fetchone()[0]:
                raise ValueError('Existing execution ownership or quarantined engine requires explicit reconciliation; launcher does not clear it')
            queued = connection.execute("SELECT COUNT(*) FROM execution_jobs WHERE status='queued'").fetchone()[0]
            unfinished = connection.execute('SELECT COUNT(*) FROM verification_attempts WHERE manifest IS NULL').fetchone()[0]
            if (queued or unfinished) and not resume_queued:
                raise ValueError('Queued work is not resumed implicitly. Review it, then explicitly select --resume-queued.')
    assert_offline(data, offline=True)
    return {'state': 'existing_current_schema', 'schema_version': SCHEMA_VERSION, 'queued_jobs': queued,
            'unfinished_verification_attempts': unfinished}

def preflight(workspace, state, port=8020, ui=None, *, restore_backup=None, resume_queued=False):
    data, access, ui = Path(workspace).absolute(), Path(state).absolute(), Path(ui or ROOT / 'frontend' / 'dist').absolute()
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Use a nonprivileged loopback port from 1024 to 65535')
    if data.resolve() == (ROOT / '.data').resolve() or data.resolve().is_relative_to((ROOT / '.data').resolve()):
        raise ValueError('The operating .data workspace is excluded; select an explicit isolated workspace')
    if any(first.resolve().is_relative_to(second.resolve()) for first, second in ((data,access),(access,data),(ui,data),(data,ui),(ui,access),(access,ui))):
        raise ValueError('Data, access state and built UI must be separate directories')
    if ui.is_symlink() or not (ui / 'index.html').is_file() or (ui / 'index.html').is_symlink():
        raise ValueError('Built UI is missing. Run npm run build in frontend, then use the built directory.')
    if access.exists() and (access.is_symlink() or not access.is_dir() or access.stat().st_uid != os.getuid() or stat.S_IMODE(access.stat().st_mode) & 0o077):
        raise ValueError('Access-state directory must be private mode0700; permissions are not changed automatically')
    with socket.socket() as probe:
        try:
            # Match Uvicorn's listener semantics: an owned graceful shutdown may
            # leave accepted connections in TIME_WAIT, without a port owner.
            # SO_REUSEADDR permits that restart but still rejects a live listener.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(('127.0.0.1', port))
        except OSError as error:
            raise ValueError('Selected port is occupied; choose another port without stopping its owner') from error
    if restore_backup is not None:
        from ade2e.workspace_backup import verify
        if data.exists():
            raise ValueError('Restore requires a NEW workspace destination; existing directories are never overwritten')
        verified = verify(Path(restore_backup))
        workspace_report = {'state': 'verified_restore_then_additive_migration', 'original_schema': verified['database']['schema_version'],
                            'source_backup': str(Path(restore_backup).absolute())}
    else:
        workspace_report = inspect_workspace(data, resume_queued=resume_queued)
    return {'schema_version': VERSION, 'status': 'ready', 'workspace': str(data), 'access_state': str(access),
            'url': f'http://127.0.0.1:{port}', 'built_ui_sha256': hashlib.sha256((ui/'index.html').read_bytes()).hexdigest(),
            'data': workspace_report, 'auth_required': True, 'team_scope': 'new_project_domains_only', 'services_started': False}

def child_environment(data, access, ui, port, instance, token, *, enable_configured_engines=False):
    environment = {key:value for key,value in os.environ.items() if not key.startswith(('ADE2E_', 'AXIOM_', 'PYTHON'))}
    if enable_configured_engines:
        environment.update({key:value for key,value in os.environ.items() if key.startswith(ENGINE_PREFIXES)})
    source_paths = [ROOT/'backend/src', ROOT/'sdk/python/src', ROOT/'scripts']
    if enable_configured_engines:
        source_paths += [ROOT/'integrations'/name/'src' for name in ('carla', 'camera-e2e', 'vad', 'neural')]
    environment.update(PYTHONPATH=os.pathsep.join(map(str, source_paths)),
        PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1', PYTHONNOUSERSITE='1', AXIOM_APP_FACTORY_ONLY='1',
        ADE2E_DATA_DIR=str(data), ADE2E_API_TOKEN=token, ADE2E_TEAM_MODE='1', ADE2E_SEED_EXAMPLES='0', ADE2E_CORS_ORIGINS='',
        AXIOM_INTERNAL_UI=str(ui), AXIOM_INTERNAL_ACCESS=str(access), AXIOM_INTERNAL_PORT=str(port), AXIOM_INTERNAL_INSTANCE=instance)
    return environment

def private_json(path, value, *, exclusive=False):
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | (os.O_EXCL if exclusive else os.O_NONBLOCK)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError('Access state files must be owner-only regular files (mode0600)')
        stream.truncate(0)
        stream.write(canonical(value))

def freeze_ui(source, access, instance, expected_index_sha256):
    """Serve one immutable build per process, not a mutable development dist."""
    from ade2e.workspace_backup import inventory
    original = inventory(source)
    if (len(original) > 10_000 or sum(item['size_bytes'] for item in original.values()) > 128 * 1024 * 1024
            or any(item['size_bytes'] > 32 * 1024 * 1024 for item in original.values())):
        raise ValueError('Built UI exceeds the internal build snapshot bounds')
    if original.get('index.html', {}).get('sha256') != expected_index_sha256:
        raise ValueError('Built UI changed after preflight; rebuild and inspect before starting')
    destination = access / ('ui-' + instance)
    destination.mkdir(mode=0o700)
    for key, item in original.items():
        target = destination / key
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        checksum, size = hashlib.sha256(), 0
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'wb') as out, (source / key).open('rb') as incoming:
            for chunk in iter(lambda: incoming.read(1024 * 1024), b''):
                size += len(chunk)
                if size > item['size_bytes']:
                    raise ValueError('Built UI grew during snapshot publication')
                checksum.update(chunk)
                if out.write(chunk) != len(chunk):
                    raise OSError('Built UI snapshot returned a partial write')
        if size != item['size_bytes'] or checksum.hexdigest() != item['sha256']:
            raise ValueError('Built UI file changed during snapshot publication')
    if inventory(source) != original or inventory(destination) != original:
        raise ValueError('Built UI source or frozen copy changed during snapshot publication')
    manifest = {'schema_version':'internal-ui-snapshot/1','files':original,
        'file_count':len(original),'total_bytes':sum(item['size_bytes'] for item in original.values())}
    checksum = hashlib.sha256(canonical(manifest)).hexdigest()
    private_json(access / (instance + '.ui-manifest.json'), manifest, exclusive=True)
    return destination, checksum

def create_server_app():
    from fastapi import Header, HTTPException
    from starlette.middleware.trustedhost import TrustedHostMiddleware
    from starlette.responses import JSONResponse
    from starlette.staticfiles import StaticFiles
    from ade2e.database import Settings
    from ade2e.main import create_app
    token = os.environ['ADE2E_API_TOKEN']
    app = create_app(Settings(data_dir=Path(os.environ['ADE2E_DATA_DIR']), database_url=None, api_token=token,
                              team_mode=True, seed_examples=False, cors_origins=[]))
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1','localhost'], www_redirect=False)
    port = int(os.environ['AXIOM_INTERNAL_PORT'])
    origins = {f'http://127.0.0.1:{port}', f'http://localhost:{port}'}
    @app.middleware('http')
    async def local_boundary(request, call_next):
        if request.headers.get('origin') not in {None,*origins} or request.headers.get('sec-fetch-site')=='cross-site':
            return JSONResponse({'detail':'Cross-origin access is disabled in the internal loopback workspace'}, status_code=403)
        response=await call_next(request)
        response.headers.update({'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','X-Frame-Options':'DENY','Cache-Control':'no-store'})
        if request.url.path in {'/','/index.html'}:
            response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; worker-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
        return response
    @app.get('/api/v1/internal-platform/status')
    def status(authorization: str|None=Header(default=None)):
        if not secrets.compare_digest((authorization or '').encode(),('Bearer '+token).encode()):
            raise HTTPException(401,'A valid operator bootstrap token is required')
        from ade2e import jobs
        return {'instance_id':os.environ['AXIOM_INTERNAL_INSTANCE'],'frontend':'built_static','team_mode':True,
                'workers':jobs.list_workers(app.state.db)}
    app.mount('/',StaticFiles(directory=os.environ['AXIOM_INTERNAL_UI'],html=True,follow_symlink=False),name='internal-ui')
    return app

def worker_main():
    from ade2e.worker_supervision import configure_worker_logging,run_polling_loop
    access=Path(os.environ['AXIOM_INTERNAL_ACCESS'])
    instance=os.environ['AXIOM_INTERNAL_INSTANCE']
    configure_worker_logging(access/(instance+'.worker.log'))
    logger=logging.getLogger('ade2e.internal_worker')
    from ade2e.database import Database,Settings
    from ade2e.adapters import Registry
    from ade2e import jobs
    from ade2e.worker import run_once,worker_identity
    from ade2e.inference_runtime import RuntimeRegistry
    from ade2e.sensor_decoders import SensorDecoderRegistry
    from ade2e.evaluator_runtime import EvaluatorRegistry
    from ade2e.stack_runtime import StackRegistry
    from ade2e.experiment_defaults import default_experiment_registry
    db=None;identity=None
    try:
        try:
            db=Database(Settings());db.initialize();registry=Registry()
            runtime_registry, sensor_registry = RuntimeRegistry(), SensorDecoderRegistry()
            evaluator_registry, stack_registry = EvaluatorRegistry(), StackRegistry()
            experiment_registry = default_experiment_registry(db)
        except ImportError as error:
            logger.error('Worker startup failed: required module/dependency is unavailable (%s). '
                         'Repair this environment and restart the worker; no jobs were claimed.',error)
            return 1
        identity=worker_identity('internal');stop=threading.Event()
        for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stop.set())
        jobs.touch_worker(db,identity,'idle')
        ready=access/(instance+'.worker-ready.json')
        private_json(ready, {'instance_id':instance,'worker_id':identity}, exclusive=True)
        return run_polling_loop(
            lambda:run_once(db,registry,identity, runtime_registry=runtime_registry, sensor_registry=sensor_registry,
                            evaluator_registry=evaluator_registry, stack_registry=stack_registry,
                            experiment_registry=experiment_registry),
            stop,poll_interval=.5,logger=logger)
    finally:
        if db is not None:
            try:
                if identity is not None:jobs.touch_worker(db,identity,'stopped')
            finally:db.engine.dispose()

def _status(port, token):
    connection=http.client.HTTPConnection('127.0.0.1',port,timeout=.8)
    try:
        connection.request('GET','/api/v1/internal-platform/status',headers={'Authorization':'Bearer '+token})
        response=connection.getresponse();raw=response.read(32768)
        return json.loads(raw) if response.status==200 else None
    finally:connection.close()

def start(args):
    data,access,ui=Path(args.workspace).absolute(),Path(args.state).absolute(),Path(args.ui).absolute()
    report=preflight(data,access,args.port,ui,restore_backup=args.restore_backup,resume_queued=args.resume_queued)
    data.parent.mkdir(parents=True,exist_ok=True)
    if args.restore_backup:
        from ade2e.workspace_backup import restore
        restore(Path(args.restore_backup),data)
    data.mkdir(mode=0o700,exist_ok=True);access.parent.mkdir(parents=True,exist_ok=True)
    from ade2e.local_pilot import token_file
    lock_fd=os.open(data/'.axiom-pilot.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(lock_fd,'a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:raise ValueError('Another launcher owns this workspace') from error
        token_path,token=token_file(access);instance=uuid.uuid4().hex
        frozen_ui,ui_manifest_sha=freeze_ui(ui,access,instance,report['built_ui_sha256'])
        environment=child_environment(data,access,frozen_ui,args.port,instance,token,enable_configured_engines=args.enable_configured_engines)
        environment['AXIOM_INTERNAL_UI_MANIFEST_SHA256']=ui_manifest_sha
        children=[];logs=[];stop=threading.Event();forced=[]
        previous={sig:signal.signal(sig,lambda *_:stop.set()) for sig in (signal.SIGINT,signal.SIGTERM)}
        code=1
        try:
            for role in ('server','worker'):
                # Worker logging owns its rotating file. Keep bootstrap stderr
                # separate so this inherited descriptor cannot retain a rotated log.
                log_role='worker-bootstrap' if role=='worker' else role
                log=os.fdopen(os.open(access/(instance+'.'+log_role+'.log'),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb');logs.append(log)
                child=subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),'_'+role],cwd=ROOT,env=environment,
                                       stdout=log,stderr=subprocess.STDOUT,start_new_session=True);children.append(child)
                deadline=time.monotonic()+40;ready=False
                while time.monotonic()<deadline and child.poll() is None and not stop.is_set():
                    try:
                        if role=='server':ready=(_status(args.port,token) or {}).get('instance_id')==instance
                        else:
                            marker=access/(instance+'.worker-ready.json')
                            ready=marker.is_file() and json.loads(marker.read_bytes()).get('instance_id')==instance
                    except (OSError,ValueError,http.client.HTTPException):pass
                    if ready:break
                    stop.wait(.15)
                if not ready:raise RuntimeError(f'Internal {role} did not become ready; inspect its private log')
            running=report|{'status':'running','services_started':True,'instance_id':instance,'token_file':str(token_path),
                'built_ui_snapshot':str(frozen_ui),'built_ui_manifest_sha256':ui_manifest_sha,
                'configured_engines_enabled':args.enable_configured_engines,
                'worker_log':str(access/(instance+'.worker.log')),
                'worker_bootstrap_log':str(access/(instance+'.worker-bootstrap.log')),
                'instructions':'Open Workspace settings and enter the token from the private token file. Ctrl-C stops only this launcher’s children.'}
            private_json(access/'access.json', running)
            print(json.dumps(running,ensure_ascii=False),flush=True)
            if args.smoke:stop.set()
            while not stop.wait(.25) and all(child.poll() is None for child in children):pass
            code=0 if stop.is_set() else 1
        finally:
            # Drain the worker while API lease maintenance remains alive.
            for child in reversed(children):
                if child.poll() is None:child.terminate()
                deadline=time.monotonic()+660
                while child.poll() is None and time.monotonic()<deadline:
                    try:child.wait(timeout=.2)
                    except subprocess.TimeoutExpired:pass
                if child.poll() is None and os.getpgid(child.pid)==child.pid:
                    os.killpg(child.pid,signal.SIGKILL);forced.append(child.pid);child.wait(timeout=5)
            for log in logs:log.close()
            # Uvicorn restores and re-raises SIGTERM after lifespan drainage.
            graceful = all(child.returncode in ((0, -signal.SIGTERM) if index == 0 else (0,))
                           for index, child in enumerate(children))
            if not graceful:code=1
            outcome={'schema_version':VERSION,'instance_id':instance,'status':'stopped' if code==0 and not forced else 'failed',
                     'children':[{'pid':child.pid,'exit_code':child.returncode} for child in children],
                     'forced_stop_pids':forced,'recovery_may_be_required':bool(forced) or code!=0}
            private_json(access/(instance+'.result.json'), outcome, exclusive=True)
            for sig,handler in previous.items():signal.signal(sig,handler)
        return code if not forced else 1

def main():
    if len(sys.argv)==2 and sys.argv[1]=='_server':
        import uvicorn
        uvicorn.run(create_server_app(),host='127.0.0.1',port=int(os.environ['AXIOM_INTERNAL_PORT']),log_level='warning');return 0
    if len(sys.argv)==2 and sys.argv[1]=='_worker':return worker_main()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True);parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--port',type=int,default=8020);parser.add_argument('--ui',type=Path,default=ROOT/'frontend/dist')
    parser.add_argument('--check',action='store_true');parser.add_argument('--smoke',action='store_true')
    parser.add_argument('--resume-queued',action='store_true');parser.add_argument('--enable-configured-engines',action='store_true')
    parser.add_argument('--restore-backup',type=Path,help='Verify and restore a cold backup to a NEW workspace, then migrate only that restored copy')
    args=parser.parse_args()
    try:
        if args.check:
            print(json.dumps(preflight(args.workspace,args.state,args.port,args.ui,restore_backup=args.restore_backup,resume_queued=args.resume_queued),ensure_ascii=False));return 0
        return start(args)
    except (OSError,ValueError,RuntimeError,sqlite3.Error,subprocess.SubprocessError) as error:
        print(json.dumps({'status':'failed','error':str(error)},ensure_ascii=False),file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
