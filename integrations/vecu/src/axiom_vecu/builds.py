"""Content-pinned C++ builds on the configured native CARLA worker."""
import base64
import hashlib
import io
import json
from pathlib import Path
import shlex
import signal
import os
import subprocess
import tarfile
import time
import uuid
from sqlalchemy import select
from ade2e import experiment_jobs
from ade2e.adapters import AdapterCancelled
from ade2e.experiment_models import ExperimentExecutionRow,ExperimentArtifactRow
from ade2e.job_models import JobRow
from ade2e.experiment_runtime import ExperimentExecution,ExperimentOutputFile
from ade2e.service import canonical_bytes,digest
from axiom_carla import vecu_remote,hil_policy

SOURCES=[{'id':'cpp-immediate','name':'C++ · 즉시 전제동','policy_id':'immediate'},
         {'id':'cpp-ramp','name':'C++ · 300ms 단계 제동 / 500ms 복귀','policy_id':'ramp'}]

def command(transport,operation):
    if not transport.target or not transport.directory or not transport.python:raise ValueError('Compiled ECU Linux worker is not configured')
    remote=shlex.join([transport.python,transport.directory+'/vecu_remote.py',operation])
    return ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',transport.target,remote]

def call(transport,operation,payload=None,check=lambda:None,timeout=200):
    proc=subprocess.Popen(command(transport,operation),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    data=canonical_bytes(payload)+b'\n' if payload is not None else b''
    start=time.monotonic()
    try:
        first=True
        while True:
            check()
            try:
                out,err=proc.communicate(input=data if first else None,timeout=.2);break
            except subprocess.TimeoutExpired:first=False
            if time.monotonic()-start>timeout:raise TimeoutError('Compiled ECU worker operation timed out')
        if proc.returncode:raise ValueError('Compiled ECU worker failed: '+err.decode(errors='replace')[-12000:])
        return json.loads(out)
    finally:
        if proc.poll() is None:
            if operation=='build' and payload:
                cancel=command(transport,'cancel');cancel[-1]+=' --key '+payload['build_key']
                try:subprocess.run(cancel,capture_output=True,timeout=10)
                except (OSError,subprocess.TimeoutExpired):pass
            os.killpg(proc.pid,signal.SIGTERM)
            try:proc.wait(timeout=2)
            except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()

def source_bundle():
    root=Path(__file__).resolve().parents[2]/'native'
    files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*'))
        if p.is_file() and not any(x in ('__pycache__','.DS_Store') for x in p.parts) and p.suffix not in ('.pyc',)}
    if 'build.py' not in files:raise ValueError('Native source package is not installed')
    buffer=io.BytesIO()
    # Stable archive metadata permits source snapshot checks before dispatch.
    import gzip
    with gzip.GzipFile(fileobj=buffer,mode='wb',mtime=0) as compressed:
        with tarfile.open(fileobj=compressed,mode='w') as tar:
            for name in files:
                content=(root/name).read_bytes();info=tarfile.TarInfo(name);info.size=len(content);info.mode=0o644;tar.addfile(info,io.BytesIO(content))
    return files,buffer.getvalue()

def prepare(transport,source_id):
    if source_id not in {s['id'] for s in SOURCES}:raise ValueError('Source package is not operator registered')
    probe=call(transport,'probe',timeout=15)
    if probe['helper_sha256']!=hashlib.sha256(Path(vecu_remote.__file__).read_bytes()).hexdigest():raise ValueError('Native build helper differs')
    files,bundle=source_bundle()
    return {'schema_version':'vecu-build-input/1','source_id':source_id,'files':files,'bundle_sha256':hashlib.sha256(bundle).hexdigest(),'probe':probe}

def execute(transport,snapshot,work,should_cancel):
    work=Path(work);work.mkdir(parents=True,exist_ok=True)
    files,bundle=source_bundle()
    if files!=snapshot['files'] or hashlib.sha256(bundle).hexdigest()!=snapshot['bundle_sha256']:raise ValueError('Native sources changed after build preview')
    def check():
        if should_cancel():raise AdapterCancelled('ECU build cancelled')
    check();key=uuid.uuid4().hex
    result=call(transport,'build',{'build_key':key,'source_id':snapshot['source_id'],'bundle':base64.b64encode(bundle).decode(),
        'bundle_sha256':snapshot['bundle_sha256'],'files':files},check)
    manifest=result['manifest'];policy=next(s['policy_id'] for s in SOURCES if s['id']==snapshot['source_id'])
    if (manifest.get('files')!=files or manifest.get('source_sha256')!=digest(files)
        or manifest.get('source_id')!=snapshot['source_id'] or manifest.get('policy_id')!=policy
        or manifest.get('dut_version')!=hil_policy.POLICIES[policy] or manifest.get('protocol')!='hil-dut/1'
        or manifest.get('target_platform')!={'os':'linux','arch':'x86_64'}
        or manifest.get('compiler',{}).get('version','').split('\n')[0]!=snapshot['probe']['compiler']
        or manifest.get('build_log_sha256')!=hashlib.sha256(result['log'].encode()).hexdigest()):
        raise ValueError('Native build manifest differs from frozen source/policy/platform')
    runtime=dict(kind='compiled_reference',build_key=key,source_id=snapshot['source_id'],policy_id=policy,
        dut_version=hil_policy.POLICIES[policy],protocol='hil-dut/1',binary_sha256=manifest['binary_sha256'],
        source_sha256=manifest['source_sha256'],compiler=manifest['compiler'],target_platform=manifest['target_platform'],
        classification='reference_application',production_software=False)
    runtime['runtime_sha256']=digest(runtime)
    call(transport,'check',runtime,check,timeout=15)
    summary={'schema_version':'vecu-build-result/1','source_id':snapshot['source_id'],'runtime':runtime,'build_manifest':manifest,'verdict':'passed'}
    outputs=[]
    for name,data,mime in [('vecu-build.json',canonical_bytes(summary),'application/json'),('vecu-build.log',result['log'].encode(),'text/plain'),('vecu-build-source.tar.gz',bundle,'application/gzip')]:
        path=work/name;path.write_bytes(data);outputs.append(ExperimentOutputFile(path,name,mime))
    return ExperimentExecution(summary=summary,artifacts=outputs,fidelity='native_cpp_host_build')

def built_runtime(db,job_id,project,session=None):
    if session is None:
        with db.read_session() as current:return built_runtime(db,job_id,project,current)
    row=session.get(ExperimentExecutionRow,job_id);job=session.scalar(select(JobRow).where(JobRow.id==job_id))
    if row is None or job is None or row.project_id!=project or job.status!='completed':raise ValueError('Build must be completed in the same project')
    experiment_jobs.detail(session,job) # Validate sealed execution manifest.
    spec=job.prepared.get('spec',{}) if isinstance(job.prepared,dict) else {}
    if spec.get('adapter_id')!='host-vecu' or spec.get('kind')!='virtual_ecu' or set(spec.get('input',{}))!={'build_source_id'}:
        raise ValueError('Execution was not submitted as a registered ECU build')
    artifact=session.scalar(select(ExperimentArtifactRow).where(ExperimentArtifactRow.job_id==job_id,ExperimentArtifactRow.name=='vecu-build.json'))
    if artifact is None:raise ValueError('Execution is not a compiled ECU build')
    summary=json.loads(experiment_jobs.artifact_path(db,artifact).read_bytes())
    if summary!=row.summary or summary.get('schema_version')!='vecu-build-result/1' or summary.get('verdict')!='passed':raise ValueError('Build result identity mismatch')
    value=dict(summary['runtime']);hil_policy.validate_runtime(value)
    source_id=spec['input']['build_source_id']
    if summary.get('source_id')!=source_id or value.get('source_id')!=source_id or not any(s['id']==source_id and s['policy_id']==value['policy_id'] for s in SOURCES):
        raise ValueError('Build source/policy identity differs')
    if digest({k:v for k,v in value.items() if k!='runtime_sha256'})!=value['runtime_sha256']:raise ValueError('Compiled runtime hash differs')
    return value
